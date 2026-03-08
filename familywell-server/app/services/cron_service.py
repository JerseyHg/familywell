import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select, update, func, and_, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.medication import Medication, MedicationTask
from app.models.insurance import Insurance
from app.models.record import Record
from app.models.reminder import Reminder, ReminderSetting
from app.models.user import User

logger = logging.getLogger(__name__)

# ★ 默认时区偏移（UTC+8 = -480），用于未记录时区的用户
_DEFAULT_TZ_OFFSET = -480


def _user_local_today(tz_offset: int | None) -> date:
    """根据用户存储的时区偏移量计算其本地"今天"。
    tz_offset 是 JS getTimezoneOffset() 的值：UTC+8 → -480, UTC-5 → 300
    未知时区的用户默认按 UTC+8 处理。
    """
    offset = tz_offset if tz_offset is not None else _DEFAULT_TZ_OFFSET
    utc_now = datetime.utcnow()
    user_now = utc_now + timedelta(minutes=-offset)
    return user_now.date()


async def run_daily_tasks():
    """Run all daily scheduled tasks. Called at 00:05 every day."""
    logger.info("Starting daily cron tasks...")
    async with async_session() as db:
        # ★ 修复历史数据：将旧的 "completed" 状态统一为 "done"
        await db.execute(
            update(MedicationTask)
            .where(MedicationTask.status == "completed")
            .values(status="done")
        )
        await generate_medication_tasks(db)
        await mark_missed_tasks(db)
        await check_insurance_expiry(db)
        await check_checkup_due(db)
        await check_low_stock(db)
        await db.commit()
    logger.info("Daily cron tasks completed.")


async def _generate_tasks_for_user(
    db: AsyncSession, user_id: int, target_date: date,
) -> int:
    """为指定用户在指定日期生成用药任务（检查 interval_days / start_date）。"""
    result = await db.execute(
        select(Medication).where(
            Medication.user_id == user_id,
            Medication.is_active == True,
            (Medication.end_date == None) | (Medication.end_date >= target_date),
            (Medication.start_date == None) | (Medication.start_date <= target_date),
        )
    )
    medications = result.scalars().all()

    count = 0
    for med in medications:
        # ★ 检查 interval_days：隔 N 天服药一次
        interval = med.interval_days or 1
        if interval > 1 and med.start_date:
            days_since_start = (target_date - med.start_date).days
            if days_since_start < 0 or days_since_start % interval != 0:
                continue

        scheduled_times = med.scheduled_times or ["08:00"]
        for t_str in scheduled_times:
            h, m = t_str.split(":")
            scheduled_time = time(int(h), int(m))

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


async def ensure_user_tasks_for_date(
    db: AsyncSession, user_id: int, target_date: date,
) -> None:
    """按需生成当天用药任务。
    _generate_tasks_for_user 内部已做去重（唯一约束），可安全重复调用。
    """
    count = await _generate_tasks_for_user(db, user_id, target_date)
    if count > 0:
        await db.flush()
        logger.info(f"On-demand generated {count} tasks for user {user_id} on {target_date}")


async def generate_medication_tasks(db: AsyncSession):
    """Generate today's medication tasks for all active medications.
    ★ 使用每个用户存储的 tz_offset 计算其本地"今天"。
    """
    # 查询所有有活跃药物的用户及其时区
    result = await db.execute(
        select(User.id, User.tz_offset)
        .where(
            User.id.in_(
                select(distinct(Medication.user_id)).where(Medication.is_active == True)
            )
        )
    )
    users = result.all()

    total = 0
    for uid, tz_offset in users:
        today = _user_local_today(tz_offset)
        count = await _generate_tasks_for_user(db, uid, today)
        total += count

    logger.info(f"Generated {total} medication tasks for {len(users)} users")


async def mark_missed_tasks(db: AsyncSession):
    """Mark tasks from before yesterday as missed.
    ★ 使用每个用户时区的"昨天"，避免过早标记未到期任务。
    对于不同时区的用户，安全地取 2 天前作为全局截止日——
    确保不会误标当天/昨天的任务为 missed。
    """
    # 安全截止日：UTC 今天 - 2 天，对任何时区都已经是"昨天或更早"
    safe_cutoff = (datetime.utcnow() - timedelta(days=2)).date()

    result = await db.execute(
        update(MedicationTask)
        .where(
            MedicationTask.scheduled_date <= safe_cutoff,
            MedicationTask.status == "pending",
        )
        .values(status="missed")
    )
    logger.info(f"Marked {result.rowcount} tasks as missed (cutoff={safe_cutoff})")


async def check_insurance_expiry(db: AsyncSession):
    """Create reminders for insurance policies expiring soon."""
    result = await db.execute(
        select(Insurance).where(
            Insurance.is_active == True,
            Insurance.end_date != None,
        )
    )
    insurances = result.scalars().all()

    for ins in insurances:
        # 查询该用户的时区
        user_result = await db.execute(
            select(User.tz_offset).where(User.id == ins.user_id)
        )
        tz_offset = user_result.scalar_one_or_none()
        today = _user_local_today(tz_offset)

        days_left = (ins.end_date - today).days
        if days_left < 0:
            continue

        # Get user's reminder settings
        setting_result = await db.execute(
            select(ReminderSetting).where(ReminderSetting.user_id == ins.user_id)
        )
        setting = setting_result.scalar_one_or_none()
        remind_days = (setting.insurance_remind_days if setting else [30, 7]) or [30, 7]

        if days_left in remind_days:
            # Check if reminder already exists for today
            existing = await db.execute(
                select(Reminder).where(
                    Reminder.user_id == ins.user_id,
                    Reminder.type == "insurance_expiry",
                    Reminder.related_id == ins.id,
                    func.date(Reminder.created_at) == today,
                )
            )
            if existing.scalar_one_or_none() is None:
                reminder = Reminder(
                    user_id=ins.user_id,
                    type="insurance_expiry",
                    title=f"{ins.policy_type or '保险'}将于{ins.end_date}到期",
                    description=f"还剩 {days_left} 天，请尽快续保",
                    priority="urgent" if days_left <= 7 else "normal",
                    related_id=ins.id,
                    related_type="insurance",
                    remind_at=datetime.utcnow(),
                )
                db.add(reminder)


async def check_checkup_due(db: AsyncSession):
    """Remind users who haven't had a checkup in over N months."""
    result = await db.execute(select(User))
    users = result.scalars().all()

    for user in users:
        # Get reminder setting
        setting_result = await db.execute(
            select(ReminderSetting).where(ReminderSetting.user_id == user.id)
        )
        setting = setting_result.scalar_one_or_none()
        if setting and not setting.checkup_reminder_enabled:
            continue
        interval = (setting.checkup_interval_months if setting else 12) or 12

        # Find last checkup record
        last_checkup = await db.execute(
            select(Record)
            .where(Record.user_id == user.id, Record.category == "checkup")
            .order_by(Record.record_date.desc())
            .limit(1)
        )
        last = last_checkup.scalar_one_or_none()

        today = _user_local_today(user.tz_offset)
        threshold = today - timedelta(days=interval * 30)
        if last is None or (last.record_date and last.record_date < threshold):
            # Check if already reminded this month
            existing = await db.execute(
                select(Reminder).where(
                    Reminder.user_id == user.id,
                    Reminder.type == "checkup_due",
                    func.month(Reminder.created_at) == today.month,
                    func.year(Reminder.created_at) == today.year,
                )
            )
            if existing.scalar_one_or_none() is None:
                reminder = Reminder(
                    user_id=user.id,
                    type="checkup_due",
                    title="该做体检了",
                    description=f"已超过 {interval} 个月未体检，建议安排年度体检",
                    priority="normal",
                    remind_at=datetime.utcnow(),
                )
                db.add(reminder)


async def check_low_stock(db: AsyncSession):
    """Remind users when medication stock is running low."""
    result = await db.execute(
        select(Medication).where(
            Medication.is_active == True,
            Medication.remaining_count != None,
            Medication.remaining_count < 14,
            Medication.remaining_count > 0,
        )
    )
    medications = result.scalars().all()

    for med in medications:
        # 查询用户时区
        user_result = await db.execute(
            select(User.tz_offset).where(User.id == med.user_id)
        )
        tz_offset = user_result.scalar_one_or_none()
        today = _user_local_today(tz_offset)

        existing = await db.execute(
            select(Reminder).where(
                Reminder.user_id == med.user_id,
                Reminder.type == "med_low_stock",
                Reminder.related_id == med.id,
                func.date(Reminder.created_at) == today,
            )
        )
        if existing.scalar_one_or_none() is None:
            reminder = Reminder(
                user_id=med.user_id,
                type="med_low_stock",
                title=f"{med.name}余量不足",
                description=f"预计还能吃 {med.remaining_count} 天，建议提前配药",
                priority="urgent" if med.remaining_count < 7 else "normal",
                related_id=med.id,
                related_type="medication",
                remind_at=datetime.utcnow(),
            )
            db.add(reminder)
