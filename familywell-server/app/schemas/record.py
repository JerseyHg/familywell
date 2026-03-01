"""
app/schemas/record.py — 记录相关请求/响应模型
──────────────────────────────────────────────
[P1-1] 新增 RecordDetailResponse（含 ai_raw_result）
       新增 RecordUpdate（编辑字段）
[P1-3] 新增 PrescriptionConfirmRequest
"""
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


# ────────────────────────────────────────
# [P1-1] 记录详情（含 AI 原始结果）
# ────────────────────────────────────────

class RecordDetailResponse(BaseModel):
    """记录详情，包含 AI 识别原始结果，供详情页展示和编辑"""
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
    updated_at: datetime | None = None

    # AI 原始结果（JSON）— 前端用于展示结构化识别内容
    ai_raw_result: dict | None = None

    # 关联数据（可选，按需返回）
    image_url: str | None = None  # COS 预签名 URL

    class Config:
        from_attributes = True


# ────────────────────────────────────────
# [P1-1] 记录编辑
# ────────────────────────────────────────

class RecordUpdate(BaseModel):
    """用户手动编辑 AI 识别结果"""
    title: str | None = None
    hospital: str | None = None
    record_date: date | None = None
    category: str | None = None
    notes: str | None = None
    project_id: int | None = None

    # 允许用户修正 AI 提取的结构化数据
    ai_raw_result: dict | None = None


# ────────────────────────────────────────
# [P1-3] 处方确认
# ────────────────────────────────────────

class PrescriptionMedConfirm(BaseModel):
    """单个待确认的药物"""
    name: str
    dosage: str | None = None
    frequency: str | None = None
    times: list[str] | None = None
    confirmed: bool = True  # False = 用户取消此药


class PrescriptionConfirmRequest(BaseModel):
    """用户确认处方中的药物列表"""
    medications: list[PrescriptionMedConfirm]
