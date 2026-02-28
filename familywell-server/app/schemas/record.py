from datetime import date, datetime
from pydantic import BaseModel


class UploadUrlRequest(BaseModel):
    file_name: str
    content_type: str = "image/jpeg"


class UploadUrlResponse(BaseModel):
    upload_url: str
    file_key: str


class RecordCreate(BaseModel):
    file_key: str
    file_type: str = "image"
    source: str = "camera"
    notes: str | None = None
    project_id: int | None = None


class RecordStatusResponse(BaseModel):
    id: int
    ai_status: str
    category: str | None = None
    title: str | None = None


class RecordResponse(BaseModel):
    id: int
    category: str
    title: str | None
    hospital: str | None
    record_date: date | None
    file_key: str | None
    file_type: str
    ai_status: str
    source: str
    notes: str | None
    project_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class RecordListResponse(BaseModel):
    total: int
    items: list[RecordResponse]
