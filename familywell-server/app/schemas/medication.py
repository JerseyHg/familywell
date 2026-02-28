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


class MedicationUpdate(BaseModel):
    name: str | None = None
    dosage: str | None = None
    frequency: str | None = None
    scheduled_times: list[str] | None = None
    end_date: date | None = None
    remaining_count: int | None = None
    is_active: bool | None = None


class MedicationResponse(BaseModel):
    id: int
    name: str
    dosage: str | None
    frequency: str | None
    scheduled_times: list | None
    start_date: date | None
    end_date: date | None
    remaining_count: int | None
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
