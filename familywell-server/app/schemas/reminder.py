"""app/schemas/reminder.py — 提醒相关请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel


class ReminderResponse(BaseModel):
    id: int
    type: str
    title: str
    description: str | None
    priority: str
    remind_at: datetime | None
    is_read: bool
    is_resolved: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ReminderSettingUpdate(BaseModel):
    med_reminder_enabled: bool | None = None
    med_reminder_times: list[str] | None = None
    insurance_reminder_enabled: bool | None = None
    insurance_remind_days: list[int] | None = None
    checkup_reminder_enabled: bool | None = None
    checkup_interval_months: int | None = None
    visit_reminder_enabled: bool | None = None
    visit_remind_days: list[int] | None = None


class ReminderSettingResponse(BaseModel):
    med_reminder_enabled: bool
    med_reminder_times: list | None
    insurance_reminder_enabled: bool
    insurance_remind_days: list | None
    checkup_reminder_enabled: bool
    checkup_interval_months: int
    visit_reminder_enabled: bool
    visit_remind_days: list | None

    class Config:
        from_attributes = True
