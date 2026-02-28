from datetime import date, datetime, time as time_type
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.medication import Medication, MedicationTask
from app.schemas.medication import (
    MedicationCreate, MedicationUpdate, MedicationResponse, TaskResponse,
)
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/medications", tags=["medications"])


async def _generate_tasks_for_med(db: AsyncSession, med: Medication, target_date: date):
    """Generate tasks for a single medication on a given date."""
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
            )
            db.add(task)
            count += 1
    return count


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
):
    med = Medication(
        user_id=user.id,
        name=req.name,
        dosage=req.dosage,
        frequency=req.frequency,
        scheduled_times=req.scheduled_times or ["08:00"],
        start_date=req.start_date or date.today(),
        end_date=req.end_date,
        remaining_count=req.remaining_count,
        is_active=True,
    )
    db.add(med)
    await db.flush()

    # ★ 立即生成当天任务，不用等凌晨 cron
    today = date.today()
    if med.start_date <= today and (med.end_date is None or med.end_date >= today):
        await _generate_tasks_for_med(db, med, today)
        await db.flush()

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


# ─── Voice Add ───

from pydantic import BaseModel as _BaseModel

class VoiceMedRequest(_BaseModel):
    text: str  # 语音转文字后的内容


@router.post("/voice-add")
async def voice_add_medication(
    req: VoiceMedRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    接收语音转文字内容，用 AI 解析出药物信息，自动创建 Medication + 当天 Task。
    
    示例输入: "我得了灰指甲，医生开了伊曲康唑200mg，3个疗程每个疗程7天，每天吃2次"
    """
    from app.services import ai_service
    import json

    # 1. AI 解析语音内容
    prompt = """从以下用户描述中提取用药信息，返回JSON数组（可能有多个药）：
[{
  "name": "药品名称",
  "dosage": "剂量如 200mg/片",
  "med_type": "long_term|course|temporary",
  "course_count": 疗程数(仅course类型),
  "days_per_course": 每疗程天数(仅course类型),
  "total_days": 总天数(仅temporary类型),
  "times_per_day": 每天几次(默认1),
  "disease": "疾病名称(可选)"
}]
注意：
- 如果说"长期吃"、"一直吃"、"终身服用"，med_type 为 long_term
- 如果提到"疗程"，med_type 为 course
- 如果说"吃几天"、"感冒药"，med_type 为 temporary
- 只返回JSON，不要多余文字

用户描述："""

    try:
        from openai import AsyncOpenAI
        from app.config import get_settings

        _settings = get_settings()
        client = AsyncOpenAI(
            api_key=_settings.DOUBAO_API_KEY,
            base_url=_settings.DOUBAO_BASE_URL,
        )
        response = await client.chat.completions.create(
            model=_settings.DOUBAO_MODEL,
            messages=[{"role": "user", "content": prompt + req.text}],
            max_tokens=1024,
            temperature=0.1,
        )

        text = response.choices[0].message.content.strip()
        # 清理 markdown
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        meds_data = json.loads(text)
        if not isinstance(meds_data, list):
            meds_data = [meds_data]

    except Exception as e:
        raise HTTPException(status_code=422, detail=f"AI 解析失败: {str(e)}")

    # 2. 创建 Medication + Task
    DEFAULT_TIMES = {
        1: ["08:00"],
        2: ["08:00", "20:00"],
        3: ["08:00", "12:00", "20:00"],
    }

    today = date.today()
    created = []

    for m in meds_data:
        name = m.get("name", "").strip()
        if not name:
            continue

        med_type = m.get("med_type", "long_term")
        times_per_day = int(m.get("times_per_day", 1))
        scheduled_times = DEFAULT_TIMES.get(times_per_day, ["08:00"])

        # 计算 end_date
        end_date = None
        if med_type == "course":
            course_count = int(m.get("course_count", 1))
            days_per_course = int(m.get("days_per_course", 7))
            total_days = course_count * days_per_course
            if total_days > 0:
                from datetime import timedelta
                end_date = today + timedelta(days=total_days)
        elif med_type == "temporary":
            total_days = int(m.get("total_days", 7))
            from datetime import timedelta
            end_date = today + timedelta(days=total_days)

        med = Medication(
            user_id=user.id,
            name=name,
            dosage=m.get("dosage"),
            frequency=f"每天{times_per_day}次",
            scheduled_times=scheduled_times,
            start_date=today,
            end_date=end_date,
            is_active=True,
        )
        db.add(med)
        await db.flush()

        # 立即生成当天任务
        await _generate_tasks_for_med(db, med, today)

        created.append({
            "id": med.id,
            "name": med.name,
            "dosage": med.dosage,
            "med_type": med_type,
            "end_date": end_date.isoformat() if end_date else None,
            "times_per_day": times_per_day,
        })

    await db.flush()

    return {
        "message": f"已添加 {len(created)} 个药物",
        "medications": created,
    }


# ─── Tasks ───

@router.get("/tasks", response_model=dict)
async def list_tasks(
    start_date: date = Query(default_factory=date.today),
    end_date: date | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get medication tasks grouped by date."""
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
        .order_by(MedicationTask.scheduled_date.desc(), MedicationTask.scheduled_time)
    )
    tasks = result.scalars().all()

    grouped = {}
    for task in tasks:
        key = task.scheduled_date.isoformat()
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({
            "id": task.id,
            "medication_id": task.medication_id,
            "medication_name": task.medication.name if task.medication else None,
            "medication_dosage": task.medication.dosage if task.medication else None,
            "scheduled_date": task.scheduled_date.isoformat(),
            "scheduled_time": task.scheduled_time.strftime("%H:%M"),
            "status": task.status,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        })

    return grouped


@router.put("/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a medication task as completed."""
    result = await db.execute(
        select(MedicationTask).where(
            MedicationTask.id == task_id,
            MedicationTask.user_id == user.id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    today = date.today()
    if task.scheduled_date < today.replace(day=today.day - 1):
        raise HTTPException(status_code=400, detail="只能补打昨天的任务")

    task.status = "done"
    task.completed_at = datetime.utcnow()
    await db.flush()

    med_result = await db.execute(
        select(Medication).where(Medication.id == task.medication_id)
    )
    med = med_result.scalar_one_or_none()
    if med and med.remaining_count is not None and med.remaining_count > 0:
        med.remaining_count -= 1
        await db.flush()

    return {"status": "done", "completed_at": task.completed_at.isoformat()}
