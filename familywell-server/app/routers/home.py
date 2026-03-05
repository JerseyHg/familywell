"""
app/routers/home.py — 首页聚合接口
──────────────────────────────────
★ 修复：nickname 优先使用 profile.real_name
★ 新增：返回 medication_suggestions（待确认药物建议）
"""
from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User, UserProfile
from app.models.record import Record
from app.models.medication import MedicationTask, MedicationSuggestion
from app.models.reminder import Reminder
from app.schemas.home import HomeResponse
from app.utils.deps import get_current_user
from app.services import rag_service

router = APIRouter(prefix="/api/home", tags=["home"])


@router.get("", response_model=HomeResponse)
async def get_home_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Simplified homepage data for v2 chat-centric design."""
    today = date.today()

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

    # 3. AI proactive tip
    try:
        ai_tip = await rag_service.quick_health_summary(db, user.id)
    except Exception:
        ai_tip = None

    # 4. Recent activity (last 5 records)
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
        "date": r.created_at.strftime("%m/%d"),
        "ai_status": r.ai_status,
    } for r in records]

    # 5. Unresolved alert count
    alert_result = await db.execute(
        select(func.count(Reminder.id))
        .where(Reminder.user_id == user.id, Reminder.is_resolved == False)
    )
    alert_count = alert_result.scalar() or 0

    # 6. 待确认药物建议
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
        "created_at": s.created_at.strftime("%m/%d") if s.created_at else None,
    } for s in suggestions]

    return HomeResponse(
        profile=profile_data,
        pending_tasks=pending_tasks,
        ai_tip=ai_tip if ai_tip else None,
        recent_activity=recent_activity,
        alert_count=alert_count,
        medication_suggestions=medication_suggestions,
    )
