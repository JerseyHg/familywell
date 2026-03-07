"""
app/routers/home.py — 首页聚合接口
──────────────────────────────────
★ 修复：nickname 优先使用 profile.real_name
★ 新增：返回 medication_suggestions（待确认药物建议）
★ 优化：AI tip 与 DB 查询并行执行，避免 LLM 调用阻塞首页渲染
★ 修复：所有日期使用用户本地时区，避免跨时区日期偏移
"""
import asyncio
from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, async_session
from app.models.user import User, UserProfile
from app.models.record import Record
from app.models.medication import MedicationTask, MedicationSuggestion
from app.models.reminder import Reminder
from app.schemas.home import HomeResponse
from app.utils.deps import get_current_user
from app.utils.timezone import get_tz_offset, user_today, utc_to_user_local
from app.services import rag_service

router = APIRouter(prefix="/api/home", tags=["home"])


@router.get("", response_model=HomeResponse)
async def get_home_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tz_offset: int | None = Depends(get_tz_offset),
):
    """Simplified homepage data for v2 chat-centric design."""
    today = user_today(tz_offset)

    # ── AI tip 使用独立 session，与下方 DB 查询并行执行 ──
    # quick_health_summary 包含 embedding 搜索 + LLM 调用（1-3s），
    # 用独立 session 让它不阻塞其他快速 DB 查询
    async def _fetch_ai_tip() -> str | None:
        async with async_session() as tip_db:
            try:
                return await rag_service.quick_health_summary(tip_db, user.id)
            except Exception:
                return None

    ai_tip_task = asyncio.create_task(_fetch_ai_tip())

    # ── 以下 DB 查询在同一 session 上顺序执行（均为索引查询，总计 ~10-20ms）──

    # 1. Profile summary — 优先使用 real_name
    profile_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )
    profile = profile_result.scalar_one_or_none()

    display_name = user.nickname or "我"
    if profile and profile.real_name:
        display_name = profile.real_name

    profile_data = {
        "nickname": display_name,
        "age": None,
        "tags": [],
    }
    if profile:
        if profile.birthday:
            age = today.year - profile.birthday.year - (
                (today.month, today.day) < (profile.birthday.month, profile.birthday.day)
            )
            profile_data["age"] = age
        profile_data["tags"] = profile.medical_history or []

    # 2. Pending medication tasks (today, status=pending only)
    tasks_result = await db.execute(
        select(MedicationTask)
        .options(selectinload(MedicationTask.medication))
        .where(
            MedicationTask.user_id == user.id,
            MedicationTask.scheduled_date == today,
            MedicationTask.status == "pending",
        )
        .order_by(MedicationTask.scheduled_time)
    )
    tasks = tasks_result.scalars().all()

    pending_tasks = [{
        "id": t.id,
        "name": f"{t.medication.name} {t.medication.dosage or ''}".strip() if t.medication else "未知",
        "time": t.scheduled_time.strftime("%H:%M"),
    } for t in tasks]

    # 3. Recent activity (last 5 records)
    records_result = await db.execute(
        select(Record)
        .where(
            Record.user_id == user.id,
            or_(
                Record.ai_status == "completed",
                Record.ai_status == "processing",
                Record.ai_status == "failed",
            ),
        )
        .order_by(Record.created_at.desc())
        .limit(5)
    )
    records = records_result.scalars().all()

    recent_activity = [{
        "id": r.id,
        "category": r.category,
        "title": r.title or "处理中…",
        # ★ 返回 record_date (ISO) 供前端用本地时间格式化；
        #   同时保留 date 字段作为后备，使用用户时区转换 created_at
        "record_date": r.record_date.isoformat() if r.record_date else None,
        "date": utc_to_user_local(r.created_at, tz_offset).strftime("%m/%d") if r.created_at else "",
        "ai_status": r.ai_status,
    } for r in records]

    # 4. Unresolved alert count
    alert_result = await db.execute(
        select(func.count(Reminder.id))
        .where(Reminder.user_id == user.id, Reminder.is_resolved == False)
    )
    alert_count = alert_result.scalar() or 0

    # 5. 待确认药物建议
    suggestions_result = await db.execute(
        select(MedicationSuggestion)
        .where(
            MedicationSuggestion.user_id == user.id,
            MedicationSuggestion.status == "pending",
        )
        .order_by(MedicationSuggestion.created_at.desc())
        .limit(10)
    )
    suggestions = suggestions_result.scalars().all()

    medication_suggestions = [{
        "id": s.id,
        "name": s.name,
        "dosage": s.dosage,
        "frequency": s.frequency,
        "created_at": utc_to_user_local(s.created_at, tz_offset).strftime("%m/%d") if s.created_at else None,
    } for s in suggestions]

    # ── 等待 AI tip 完成（此时 DB 查询已全部结束）──
    ai_tip = await ai_tip_task

    return HomeResponse(
        profile=profile_data,
        pending_tasks=pending_tasks,
        ai_tip=ai_tip if ai_tip else None,
        recent_activity=recent_activity,
        alert_count=alert_count,
        medication_suggestions=medication_suggestions,
    )
