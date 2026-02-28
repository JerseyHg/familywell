from datetime import date, datetime
from pydantic import BaseModel


# ─── Request ───

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = "📁"
    start_date: date | None = None
    end_date: date | None = None
    template: str | None = "custom"


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None


class RecordAssign(BaseModel):
    """把记录归入 / 移出项目"""
    record_ids: list[int]


# ─── Response ───

class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str | None
    icon: str | None
    start_date: date | None
    end_date: date | None
    status: str
    template: str | None
    record_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    total: int
    items: list[ProjectResponse]
