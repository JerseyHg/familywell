from datetime import datetime
from pydantic import BaseModel


class FamilyCreate(BaseModel):
    name: str | None = None


class FamilyJoin(BaseModel):
    invite_code: str


class FamilyResponse(BaseModel):
    id: int
    name: str | None
    invite_code: str
    created_at: datetime

    class Config:
        from_attributes = True


class FamilyMemberResponse(BaseModel):
    user_id: int
    nickname: str | None
    role: str
    joined_at: datetime


class FamilyOverviewMember(BaseModel):
    user_id: int
    nickname: str | None
    age: int | None
    tags: list[str]
    meds_count: int
    insurance_count: int
    last_checkup: str | None
    urgent_alerts: int
    ai_summary: str | None
    key_indicator: dict | None


class FamilyOverviewResponse(BaseModel):
    summary: dict
    members: list[FamilyOverviewMember]
