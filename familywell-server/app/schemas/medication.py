from datetime import date, datetime, time
from pydantic import BaseModel


class MedicationCreate(BaseModel):
    name: str
    dosage: str | None = None
    frequency: str | None = None
    scheduled_times: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    remaining_count: int | None = None
    interval_days: int | None = 1   # ★ 每几天一次


class MedicationUpdate(BaseModel):
    name: str | None = None
    dosage: str | None = None
    frequency: str | None = None
    scheduled_times: list[str] | None = None
    end_date: date | None = None
    remaining_count: int | None = None
    is_active: bool | None = None
    interval_days: int | None = None   # ★


class MedicationResponse(BaseModel):
    id: int
    name: str
    dosage: str | None
    frequency: str | None
    scheduled_times: list | None
    start_date: date | None
    end_date: date | None
    remaining_count: int | None
    interval_days: int | None = 1      # ★
    is_active: bool

    class Config:
        from_attributes = True


class TaskResponse(BaseModel):
    id: int
    medication_id: int
    medication_name: str | None = None
    scheduled_date: date
    scheduled_time: time
    status: str
    completed_at: datetime | None

    class Config:
        from_attributes = True


# ── Suggestion Confirm ──
class SuggestionConfirmRequest(BaseModel):
    """用户确认药物建议时提交的补充信息。"""
    times_per_day: int | None = 1
    med_type: str | None = "long_term"   # long_term | course | temporary
    total_days: int | None = None        # 疗程/临时用药的总天数
    dosage: str | None = None            # 用户可补充/修改剂量
    interval_days: int | None = 1        # ★ 每几天一次，1=每天，2=隔天，3=每3天…
