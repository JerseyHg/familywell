from datetime import date, datetime
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

    # Only allow completing today or yesterday's tasks
    today = date.today()
    if task.scheduled_date < today.replace(day=today.day - 1):
        raise HTTPException(status_code=400, detail="只能补打昨天的任务")

    task.status = "done"
    task.completed_at = datetime.utcnow()
    await db.flush()

    # Decrease remaining count
    med_result = await db.execute(
        select(Medication).where(Medication.id == task.medication_id)
    )
    med = med_result.scalar_one_or_none()
    if med and med.remaining_count is not None and med.remaining_count > 0:
        med.remaining_count -= 1
        await db.flush()

    return {"status": "done", "completed_at": task.completed_at.isoformat()}
