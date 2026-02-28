from datetime import datetime
from pydantic import BaseModel


# ─── Reminder ───
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


# ─── Stats ───
class IndicatorDataPoint(BaseModel):
    value: float
    measured_at: datetime


class IndicatorTrendResponse(BaseModel):
    indicator_type: str
    unit: str | None
    latest: IndicatorDataPoint | None
    trend: str | None
    change_pct: float | None
    data: list[IndicatorDataPoint]


class NutritionDayData(BaseModel):
    date: str
    protein_g: float | None
    fat_g: float | None
    carb_g: float | None
    calories: float | None


class NutritionTrendResponse(BaseModel):
    avg: dict
    trend: dict
    data: list[NutritionDayData]


class MedAdherenceDayData(BaseModel):
    date: str
    rate: float


class MedAdherenceResponse(BaseModel):
    overall_rate: float
    total_tasks: int
    completed: int
    missed: int
    daily: list[MedAdherenceDayData]
    by_medication: list[dict]


# ─── Home (v2 simplified) ───
class HomeResponse(BaseModel):
    profile: dict
    pending_tasks: list[dict]     # 当前待服药物（只显示 pending 的）
    ai_tip: str | None            # AI 主动提示文案
    recent_activity: list[dict]   # 最近 2-3 条动态
    alert_count: int              # 未解决提醒数（红点用）
