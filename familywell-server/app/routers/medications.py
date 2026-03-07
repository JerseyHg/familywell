"""
app/routers/medications.py — 用药管理
──────────────────────────────────────────────────
★ 重构后：仅保留药物 CRUD / 任务打卡 / Suggestion 确认。
  语音录入已迁移至 voice_input.py。
★ 修复：所有日期使用用户本地时区
"""
from datetime import date, datetime, time as time_type, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.medication import Medication, MedicationTask, MedicationSuggestion
from app.schemas.medication import (
    MedicationCreate, MedicationUpdate, MedicationResponse, TaskResponse,
    SuggestionConfirmRequest,
)
from app.utils.deps import get_current_user
from app.utils.timezone import get_tz_offset, user_today

router = APIRouter(prefix="/api/medications", tags=["medications"])


# ══════════════════════════════════════════════════
# 内部工具函数
# ══════════════════════════════════════════════════

async def _generate_tasks_for_med(db: AsyncSession, med: Medication, target_date: date):
    """Generate tasks for a single medication on a given date.
    ★ Respects interval_days: if interval > 1, only generate on matching days.
    """
    # ★ 检查是否是该药物的服药日
    interval = med.interval_days or 1
    if interval > 1 and med.start_date:
        days_since_start = (target_date - med.start_date).days
        if days_since_start < 0 or days_since_start % interval != 0:
            return 0  # 今天不是服药日

    scheduled_times = med.scheduled_times or ["08:00"]
    count = 0
    for t_str in scheduled_times:
        h, m = t_str.split(":")
        scheduled_time = time_type(int(h), int(m))

        existing = await db.execute(
            select(MedicationTask).where(
                MedicationTask.medication_id == med.id,
                MedicationTask.scheduled_date == target_date,
                MedicationTask.scheduled_time == scheduled_time,
            )
        )
        if existing.scalar_one_or_none() is None:
            task = MedicationTask(
                medication_id=med.id,
                user_id=med.user_id,
                scheduled_date=target_date,
                scheduled_time=scheduled_time,
                status="pending",
                medication_name=med.name,
            )
            db.add(task)
            count += 1
    return count


# ══════════════════════════════════════════════════
# 药物 CRUD
# ══════════════════════════════════════════════════

@router.get("", response_model=list[MedicationResponse])
async def list_medications(
    active_only: bool = Query(True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Medication).where(Medication.user_id == user.id)
    if active_only:
        query = query.where(Medication.is_active == True)
    query = query.order_by(Medication.created_at.desc())

    result = await db.execute(query)
    return [MedicationResponse.model_validate(m) for m in result.scalars().all()]


@router.post("", response_model=MedicationResponse)
async def create_medication(
    req: MedicationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tz_offset: int | None = Depends(get_tz_offset),
):
    today = user_today(tz_offset)
    med = Medication(
        user_id=user.id,
        name=req.name,
        dosage=req.dosage,
        frequency=req.frequency,
        scheduled_times=req.scheduled_times or ["08:00"],
        start_date=req.start_date or today,
        end_date=req.end_date,
        remaining_count=req.remaining_count,
        is_active=True,
    )
    db.add(med)
    await db.flush()
    if med.start_date <= today and (med.end_date is None or med.end_date >= today):
        await _generate_tasks_for_med(db, med, today)
        await db.flush()

    # ★ 新增药物，清缓存
    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception:
        pass

    return MedicationResponse.model_validate(med)


@router.put("/{med_id}", response_model=MedicationResponse)
async def update_medication(
    med_id: int,
    req: MedicationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Medication).where(Medication.id == med_id, Medication.user_id == user.id)
    )
    med = result.scalar_one_or_none()
    if not med:
        raise HTTPException(status_code=404, detail="药物不存在")

    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(med, key, value)
    await db.flush()
    return MedicationResponse.model_validate(med)


# ══════════════════════════════════════════════════
# Medication Suggestions — 确认 / 忽略
# ══════════════════════════════════════════════════

DEFAULT_TIMES = {1: ["08:00"], 2: ["08:00", "20:00"], 3: ["08:00", "12:00", "20:00"]}


@router.get("/suggestions")
async def list_suggestions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取所有待确认的药物建议。"""
    result = await db.execute(
        select(MedicationSuggestion)
        .where(
            MedicationSuggestion.user_id == user.id,
            MedicationSuggestion.status == "pending",
        )
        .order_by(MedicationSuggestion.created_at.desc())
    )
    suggestions = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "dosage": s.dosage,
            "frequency": s.frequency,
            "source_text": s.source_text,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in suggestions
    ]


@router.post("/suggestions/{suggestion_id}/confirm")
async def confirm_suggestion(
    suggestion_id: int,
    req: SuggestionConfirmRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tz_offset: int | None = Depends(get_tz_offset),
):
    """
    用户确认药物建议 → 创建 Medication + 当天 Task。
    """
    result = await db.execute(
        select(MedicationSuggestion).where(
            MedicationSuggestion.id == suggestion_id,
            MedicationSuggestion.user_id == user.id,
            MedicationSuggestion.status == "pending",
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="建议不存在或已处理")

    times_per_day = req.times_per_day or 1
    interval = req.interval_days or 1
    scheduled_times = DEFAULT_TIMES.get(times_per_day, ["08:00"])

    # ★ 生成频率描述
    if interval == 1:
        freq_text = f"每天{times_per_day}次"
    elif interval == 2:
        freq_text = f"隔天{times_per_day}次"
    else:
        freq_text = f"每{interval}天{times_per_day}次"

    today = user_today(tz_offset)
    end_date = None
    if req.med_type == "course" and req.total_days:
        end_date = today + timedelta(days=req.total_days)
    elif req.med_type == "temporary":
        end_date = today + timedelta(days=req.total_days or 7)

    med = Medication(
        user_id=user.id,
        name=suggestion.name,
        dosage=req.dosage or suggestion.dosage,
        frequency=freq_text,
        scheduled_times=scheduled_times,
        start_date=today,
        end_date=end_date,
        interval_days=interval,
        is_active=True,
    )
    db.add(med)
    await db.flush()

    await _generate_tasks_for_med(db, med, today)

    suggestion.status = "confirmed"
    suggestion.confirmed_at = datetime.utcnow()
    suggestion.medication_id = med.id

    await db.commit()

    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception:
        pass

    return {
        "message": f"已添加药物「{suggestion.name}」，今日服药提醒已生成",
        "medication_id": med.id,
    }


@router.post("/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(
    suggestion_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户忽略药物建议。"""
    result = await db.execute(
        select(MedicationSuggestion).where(
            MedicationSuggestion.id == suggestion_id,
            MedicationSuggestion.user_id == user.id,
            MedicationSuggestion.status == "pending",
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="建议不存在或已处理")

    suggestion.status = "dismissed"
    await db.commit()

    return {"message": f"已忽略「{suggestion.name}」"}


# ══════════════════════════════════════════════════
# 用药任务
# ══════════════════════════════════════════════════

@router.get("/tasks")
async def list_tasks(
    start_date: date | None = Query(None),
    end_date: date | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tz_offset: int | None = Depends(get_tz_offset),
):
    if start_date is None:
        start_date = user_today(tz_offset)
    if end_date is None:
        end_date = start_date

    result = await db.execute(
        select(MedicationTask)
        .options(selectinload(MedicationTask.medication))
        .where(
            MedicationTask.user_id == user.id,
            MedicationTask.scheduled_date >= start_date,
            MedicationTask.scheduled_date <= end_date,
        )
        .order_by(MedicationTask.scheduled_date, MedicationTask.scheduled_time)
    )
    tasks = result.scalars().all()

    grouped: dict = {}
    for t in tasks:
        d = t.scheduled_date.isoformat()
        if d not in grouped:
            grouped[d] = []
        grouped[d].append({
            "id": t.id,
            "medication_id": t.medication_id,
            "name": f"{t.medication.name} {t.medication.dosage or ''}".strip() if t.medication else "未知",
            "time": t.scheduled_time.strftime("%H:%M"),
            "status": t.status,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        })

    return {"dates": grouped}


@router.put("/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MedicationTask).where(
            MedicationTask.id == task_id,
            MedicationTask.user_id == user.id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    task.status = "done"  # ★ 修复：与仪表盘/统计的计数逻辑一致（均使用 "done"）
    task.completed_at = datetime.utcnow()
    await db.flush()

    # 如果有剩余量，递减
    med_result = await db.execute(
        select(Medication).where(Medication.id == task.medication_id)
    )
    med = med_result.scalar_one_or_none()
    if med and med.remaining_count is not None and med.remaining_count > 0:
        med.remaining_count -= 1

    await db.commit()

    # ★ 用药状态变化，清缓存
    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception:
        pass

    return {"status": "done", "completed_at": task.completed_at.isoformat()}
