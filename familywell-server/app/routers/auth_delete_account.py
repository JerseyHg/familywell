"""
auth_delete_account.py — 账号注销接口
──────────────────────────────────────────
★ 需要合并到 auth.py 中，或在 main.py 中单独注册此 router

功能：
1. 删除用户所有健康记录 + COS 文件
2. 删除用户向量数据、聊天历史
3. 删除用药记录、营养日志、保险、提醒
4. 删除家庭成员关系（如果是管理者且为唯一成员，连家庭一起删）
5. 删除用户 profile
6. 删除用户账号

接入方式（二选一）：
  方式A: 将以下路由函数复制到 auth.py 中
  方式B: 在 main.py 中 app.include_router(delete_account_router)
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserProfile
from app.models.record import Record
from app.models.health_indicator import HealthIndicator
from app.models.nutrition import NutritionLog
from app.models.medication import Medication, MedicationTask
from app.models.insurance import Insurance
from app.models.reminder import Reminder, ReminderSetting
from app.models.embedding import RecordEmbedding, ChatHistory
from app.models.family import Family, FamilyMember
from app.utils.deps import get_current_user
from app.services.cos_service import _client as cos_client, settings as cos_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


# ────────────────────────────────────────
# ★ 账号注销
# ────────────────────────────────────────

@router.delete("/account")
async def delete_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    永久注销账号，删除该用户的所有数据。
    此操作不可逆。

    删除顺序（注意外键约束）：
    1. medication_task → medication
    2. health_indicator, nutrition_log, insurance
    3. record_embedding, chat_history
    4. reminder, reminder_setting
    5. record（先收集 file_key 用于删 COS）
    6. family_member（处理家庭关系）
    7. user_profile
    8. user
    """
    user_id = user.id
    logger.warning(f"Account deletion requested by user {user_id} ({user.username})")

    try:
        # ── 1. 收集 COS 文件 keys（在删记录之前）──
        result = await db.execute(
            select(Record.file_key).where(
                Record.user_id == user_id,
                Record.file_key.isnot(None),
            )
        )
        file_keys = [row[0] for row in result.all()]

        # ── 2. 删除用药建议 → 用药任务 → 药物 ──
        # 先删 medication_suggestion（外键引用 medication）
        try:
            from app.models.medication import MedicationSuggestion
            await db.execute(
                delete(MedicationSuggestion).where(MedicationSuggestion.user_id == user_id)
            )
        except Exception:
            pass

        med_ids_result = await db.execute(
            select(Medication.id).where(Medication.user_id == user_id)
        )
        med_ids = [row[0] for row in med_ids_result.all()]
        if med_ids:
            await db.execute(
                delete(MedicationTask).where(MedicationTask.medication_id.in_(med_ids))
            )
        await db.execute(delete(Medication).where(Medication.user_id == user_id))

        # ── 3. 删除健康指标、营养日志、保险 ──
        await db.execute(delete(HealthIndicator).where(HealthIndicator.user_id == user_id))
        await db.execute(delete(NutritionLog).where(NutritionLog.user_id == user_id))
        await db.execute(delete(Insurance).where(Insurance.user_id == user_id))

        # ── 4. 删除向量 + 聊天历史 ──
        await db.execute(delete(RecordEmbedding).where(RecordEmbedding.user_id == user_id))
        await db.execute(delete(ChatHistory).where(ChatHistory.user_id == user_id))

        # ── 5. 删除提醒 ──
        await db.execute(delete(Reminder).where(Reminder.user_id == user_id))
        await db.execute(delete(ReminderSetting).where(ReminderSetting.user_id == user_id))

        # ── 6. 删除记录 ──
        await db.execute(delete(Record).where(Record.user_id == user_id))

        # ── 7. 处理家庭关系 ──
        # 查找用户所在的家庭
        fm_result = await db.execute(
            select(FamilyMember).where(FamilyMember.user_id == user_id)
        )
        family_memberships = fm_result.scalars().all()

        for fm in family_memberships:
            if fm.role == "admin":
                # 管理者：检查是否还有其他成员
                count_result = await db.execute(
                    select(func.count()).select_from(FamilyMember).where(
                        FamilyMember.family_id == fm.family_id,
                        FamilyMember.user_id != user_id,
                    )
                )
                other_count = count_result.scalar() or 0

                if other_count == 0:
                    # 唯一成员 → 删除整个家庭
                    await db.execute(
                        delete(FamilyMember).where(FamilyMember.family_id == fm.family_id)
                    )
                    await db.execute(
                        delete(Family).where(Family.id == fm.family_id)
                    )
                else:
                    # 还有其他成员 → 把 admin 转给最早加入的成员
                    next_admin_result = await db.execute(
                        select(FamilyMember)
                        .where(
                            FamilyMember.family_id == fm.family_id,
                            FamilyMember.user_id != user_id,
                        )
                        .order_by(FamilyMember.joined_at.asc())
                        .limit(1)
                    )
                    next_admin = next_admin_result.scalar_one_or_none()
                    if next_admin:
                        next_admin.role = "admin"

                    await db.execute(
                        delete(FamilyMember).where(
                            FamilyMember.family_id == fm.family_id,
                            FamilyMember.user_id == user_id,
                        )
                    )
            else:
                # 普通成员 → 直接移除
                await db.execute(
                    delete(FamilyMember).where(
                        FamilyMember.family_id == fm.family_id,
                        FamilyMember.user_id == user_id,
                    )
                )

        # ── 9. 删除用户 profile ──
        await db.execute(delete(UserProfile).where(UserProfile.user_id == user_id))

        # ── 10. 删除用户 ──
        await db.execute(delete(User).where(User.id == user_id))

        # ── 提交事务 ──
        await db.commit()

        # ── 11. 异步删除 COS 文件（事务提交后，失败不影响注销）──
        if file_keys:
            try:
                # 腾讯云 COS 批量删除
                objects = [{"Key": key} for key in file_keys]
                cos_client.delete_objects(
                    Bucket=cos_settings.COS_BUCKET,
                    Delete={"Object": objects, "Quiet": "true"},
                )
                logger.info(f"Deleted {len(file_keys)} COS files for user {user_id}")
            except Exception as e:
                # COS 删除失败不影响注销流程
                logger.error(f"Failed to delete COS files for user {user_id}: {e}")

        logger.warning(f"Account {user_id} ({user.username}) permanently deleted")

        return {
            "message": "账号已注销，所有数据已删除",
            "deleted_records": len(file_keys),
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"Account deletion failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="注销失败，请稍后重试")
