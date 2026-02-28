from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.reminder import Reminder, ReminderSetting
from app.schemas.common import ReminderResponse, ReminderSettingUpdate, ReminderSettingResponse
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


@router.get("", response_model=list[ReminderResponse])
async def list_reminders(
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Reminder).where(Reminder.user_id == user.id)
    if unread_only:
        query = query.where(Reminder.is_read == False)
    query = query.order_by(Reminder.created_at.desc()).offset((page - 1) * size).limit(size)

    result = await db.execute(query)
    return [ReminderResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/urgent", response_model=list[ReminderResponse])
async def get_urgent_reminders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Reminder)
        .where(
            Reminder.user_id == user.id,
            Reminder.is_resolved == False,
            Reminder.priority == "urgent",
        )
        .order_by(Reminder.created_at.desc())
        .limit(10)
    )
    return [ReminderResponse.model_validate(r) for r in result.scalars().all()]


@router.put("/{reminder_id}/read")
async def mark_read(
    reminder_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Reminder).where(Reminder.id == reminder_id, Reminder.user_id == user.id)
    )
    reminder = result.scalar_one_or_none()
    if not reminder:
        raise HTTPException(status_code=404, detail="提醒不存在")
    reminder.is_read = True
    return {"message": "已读"}


@router.get("/settings", response_model=ReminderSettingResponse)
async def get_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ReminderSetting).where(ReminderSetting.user_id == user.id)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        setting = ReminderSetting(user_id=user.id)
        db.add(setting)
        await db.flush()
    return ReminderSettingResponse.model_validate(setting)


@router.put("/settings", response_model=ReminderSettingResponse)
async def update_settings(
    req: ReminderSettingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ReminderSetting).where(ReminderSetting.user_id == user.id)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        setting = ReminderSetting(user_id=user.id)
        db.add(setting)

    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(setting, key, value)
    await db.flush()
    return ReminderSettingResponse.model_validate(setting)
