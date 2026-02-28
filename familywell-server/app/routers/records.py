import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.record import Record
from app.schemas.record import (
    UploadUrlRequest, UploadUrlResponse,
    RecordCreate, RecordStatusResponse,
    RecordResponse, RecordListResponse,
)
from app.services import cos_service
from app.services.record_processor import process_record
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/records", tags=["records"])


@router.post("/upload-url", response_model=UploadUrlResponse)
async def get_upload_url(
    req: UploadUrlRequest,
    user: User = Depends(get_current_user),
):
    """Get a presigned URL for direct upload to COS."""
    file_key = cos_service.generate_file_key(user.id, req.file_name)
    upload_url = cos_service.get_presigned_upload_url(file_key, req.content_type)
    return UploadUrlResponse(upload_url=upload_url, file_key=file_key)


@router.post("", response_model=RecordStatusResponse)
async def create_record(
    req: RecordCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a record after file is uploaded to COS. Starts async AI processing."""
    record = Record(
        user_id=user.id,
        file_key=req.file_key,
        file_type=req.file_type,
        source=req.source,
        notes=req.notes,
        project_id=req.project_id,
        category="other",  # Will be updated by AI
        ai_status="pending",
    )
    db.add(record)
    await db.flush()

    # Start async AI processing
    background_tasks.add_task(process_record, record.id)

    return RecordStatusResponse(
        id=record.id,
        ai_status="pending",
    )


@router.get("/{record_id}/status", response_model=RecordStatusResponse)
async def get_record_status(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Poll AI recognition status."""
    result = await db.execute(
        select(Record).where(Record.id == record_id, Record.user_id == user.id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    return RecordStatusResponse(
        id=record.id,
        ai_status=record.ai_status,
        category=record.category if record.ai_status == "completed" else None,
        title=record.title if record.ai_status == "completed" else None,
    )


@router.get("", response_model=RecordListResponse)
async def list_records(
    category: str | None = Query(None),
    project_id: int | None = Query(None, description="按项目筛选"),
    unassigned: bool = Query(False, description="只看未归项目的记录"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List records with optional category / project filter."""
    query = select(Record).where(Record.user_id == user.id)
    count_query = select(func.count(Record.id)).where(Record.user_id == user.id)

    if category:
        query = query.where(Record.category == category)
        count_query = count_query.where(Record.category == category)

    if project_id is not None:
        query = query.where(Record.project_id == project_id)
        count_query = count_query.where(Record.project_id == project_id)
    elif unassigned:
        query = query.where(Record.project_id.is_(None))
        count_query = count_query.where(Record.project_id.is_(None))

    # Total count
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Paginated results
    query = query.order_by(Record.created_at.desc()).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    records = result.scalars().all()

    return RecordListResponse(
        total=total,
        items=[RecordResponse.model_validate(r) for r in records],
    )


@router.get("/{record_id}", response_model=RecordResponse)
async def get_record(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get record detail."""
    result = await db.execute(
        select(Record).where(Record.id == record_id, Record.user_id == user.id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    return RecordResponse.model_validate(record)
