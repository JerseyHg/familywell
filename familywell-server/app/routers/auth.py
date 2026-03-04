"""
app/routers/auth.py — 认证路由
──────────────────────────────
★ 重构后：合并了 auth_delete_account.py 的注销端点。
  auth_delete_account.py 可删除。

端点：
  POST   /api/auth/register    账号注册
  POST   /api/auth/login       账号登录
  GET    /api/auth/me           获取当前用户信息
  POST   /api/auth/wx-login     微信小程序登录
  DELETE /api/auth/account      永久注销账号 ← 从 auth_delete_account.py 合并
"""
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.models.user import User, UserProfile
from app.models.record import Record
from app.models.health_indicator import HealthIndicator
from app.models.nutrition import NutritionLog
from app.models.medication import Medication, MedicationTask, MedicationSuggestion
from app.models.insurance import Insurance
from app.models.reminder import Reminder, ReminderSetting
from app.models.embedding import RecordEmbedding, ChatHistory
from app.models.family import Family, FamilyMember
from app.schemas.auth import (
    RegisterRequest, LoginRequest, LoginResponse, UserResponse,
    WxLoginRequest,
)
from app.utils.auth import hash_password, verify_password, create_access_token
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)
settings = get_settings()


# ══════════════════════════════════════════════════
# 注册 / 登录 / 个人信息
# ══════════════════════════════════════════════════

@router.post("/register", response_model=LoginResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        nickname=req.nickname or req.username,
    )
    db.add(user)
    await db.flush()

    profile = UserProfile(user_id=user.id, onboarding_completed=False)
    db.add(profile)

    setting = ReminderSetting(user_id=user.id)
    db.add(setting)

    await db.flush()

    token = create_access_token(user.id)
    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
        ),
    )


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user.id)
    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return UserResponse(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
    )


# ══════════════════════════════════════════════════
# [P1-2] 微信登录
# ══════════════════════════════════════════════════

@router.post("/wx-login", response_model=LoginResponse)
async def wx_login(req: WxLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    微信小程序登录流程：
    1. 前端调用 wx.login() 获取 code
    2. 后端用 code + appid + secret 调用微信 jscode2session 换取 openid
    3. 查找已有用户（by openid）或自动注册新用户
    4. 返回 JWT token
    """
    if not settings.WECHAT_APPID or not settings.WECHAT_SECRET:
        raise HTTPException(
            status_code=501,
            detail="微信登录未配置，请在 .env 中设置 WECHAT_APPID 和 WECHAT_SECRET",
        )

    # ── Step 1: 用 code 换 openid ──
    openid = await _code_to_openid(req.code)

    # ── Step 2: 查找或创建用户 ──
    result = await db.execute(select(User).where(User.openid == openid))
    user = result.scalar_one_or_none()

    is_new = False

    if user:
        # 老用户：更新昵称/头像（如果前端传了）
        if req.nickname and req.nickname != user.nickname:
            user.nickname = req.nickname
        if req.avatar_url and req.avatar_url != user.avatar_url:
            user.avatar_url = req.avatar_url
    else:
        # 新用户：自动注册
        is_new = True
        user = User(
            username=f"wx_{openid[:16]}",  # 用 openid 前缀作为 username
            openid=openid,
            nickname=req.nickname or "微信用户",
            avatar_url=req.avatar_url,
            password_hash=None,  # 微信登录用户无密码
        )
        db.add(user)
        await db.flush()

        # 创建空 profile
        profile = UserProfile(user_id=user.id, onboarding_completed=False)
        db.add(profile)

        # 创建默认提醒设置
        setting = ReminderSetting(user_id=user.id)
        db.add(setting)

        await db.flush()
        logger.info(f"New user registered via WeChat: {user.id} (openid={openid[:8]}...)")

    token = create_access_token(user.id)

    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
            is_new=is_new,
        ),
    )


async def _code_to_openid(code: str) -> str:
    """调用微信 jscode2session 接口换取 openid"""
    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": settings.WECHAT_APPID,
        "secret": settings.WECHAT_SECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

        if "openid" not in data:
            errcode = data.get("errcode", "unknown")
            errmsg = data.get("errmsg", "")
            logger.error(f"WeChat jscode2session failed: {errcode} {errmsg}")
            raise HTTPException(
                status_code=401,
                detail=f"微信登录失败：{errmsg or '无效的 code'}",
            )

        return data["openid"]

    except httpx.HTTPError as e:
        logger.error(f"WeChat API request failed: {e}")
        raise HTTPException(status_code=502, detail="微信服务器通信失败")


# ══════════════════════════════════════════════════
# 账号注销（从 auth_delete_account.py 合并）
# ══════════════════════════════════════════════════

@router.delete("/account")
async def delete_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    永久注销账号，删除该用户的所有数据。
    此操作不可逆。

    删除顺序（注意外键约束）：
    1. medication_suggestion → medication_task → medication
    2. health_indicator, nutrition_log, insurance
    3. record_embedding, chat_history
    4. reminder, reminder_setting
    5. record（先收集 file_key 用于删 COS）
    6. project
    7. family_member（处理家庭关系）
    8. user_profile
    9. user
    """
    from app.services.cos_service import _client as cos_client, settings as cos_settings

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
        await db.execute(
            delete(MedicationSuggestion).where(MedicationSuggestion.user_id == user_id)
        )

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

        # ── 6.5 删除项目 ──
        from app.models.project import Project
        await db.execute(delete(Project).where(Project.user_id == user_id))

        # ── 7. 处理家庭关系 ──
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

        # ── 8. 删除用户 profile ──
        await db.execute(delete(UserProfile).where(UserProfile.user_id == user_id))

        # ── 9. 删除用户 ──
        await db.execute(delete(User).where(User.id == user_id))

        # ── 提交事务 ──
        await db.commit()

        # ── 10. 异步删除 COS 文件（事务提交后，失败不影响注销）──
        if file_keys:
            try:
                objects = [{"Key": key} for key in file_keys]
                cos_client.delete_objects(
                    Bucket=cos_settings.COS_BUCKET,
                    Delete={"Object": objects, "Quiet": "true"},
                )
                logger.info(f"Deleted {len(file_keys)} COS files for user {user_id}")
            except Exception as e:
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
