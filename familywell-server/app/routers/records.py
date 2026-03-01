"""
app/routers/records.py — 健康记录路由
──────────────────────────────────────
[P1-1] GET  /{id} → RecordDetailResponse（含 ai_raw_result + 图片URL）
       PUT  /{id} → 编辑记录
[P1-3] POST /{id}/confirm-prescription → 确认处方药物

原有接口保持不变：upload-url, create, status, list
"""
import asyncio
import logging
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.record import Record
from app.models.medication import Medication
from app.schemas.record import (
    UploadUrlRequest, UploadUrlResponse,
    RecordCreate, RecordStatusResponse,
    RecordResponse, RecordListResponse,
    RecordDetailResponse, RecordUpdate,
    PrescriptionConfirmRequest,
)
from app.services import cos_service
from app.services.record_processor import process_record
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/records", tags=["records"])
logger = logging.getLogger(__name__)


# ── 原有接口（不变）──

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
        category="other",
        ai_status="pending",
    )
    db.add(record)
    await db.flush()
    background_tasks.add_task(process_record, record.id)
    return RecordStatusResponse(id=record.id, ai_status="pending")


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
        category=record.category if record.ai_status in ("completed", "pending_confirmation") else None,
        title=record.title if record.ai_status in ("completed", "pending_confirmation") else None,
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

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(Record.created_at.desc()).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    records = result.scalars().all()

    return RecordListResponse(
        total=total,
        items=[RecordResponse.model_validate(r) for r in records],
    )


# ────────────────────────────────────────
# [P1-1] 记录详情（含 AI 原始结果 + 图片URL）
# ────────────────────────────────────────

@router.get("/{record_id}", response_model=RecordDetailResponse)
async def get_record_detail(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    获取记录详情，包含：
    - AI 识别的原始 JSON 结果（ai_raw_result）
    - COS 图片预签名 URL（有效期 1 小时）
    """
    result = await db.execute(
        select(Record).where(Record.id == record_id, Record.user_id == user.id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 生成图片预签名 URL
    image_url = None
    if record.file_key:
        try:
            image_url = cos_service.get_file_url(record.file_key)
        except Exception as e:
            logger.warning(f"Failed to generate presigned URL for {record.file_key}: {e}")

    return RecordDetailResponse(
        id=record.id,
        category=record.category,
        title=record.title,
        hospital=record.hospital,
        record_date=record.record_date,
        file_key=record.file_key,
        file_type=record.file_type,
        ai_status=record.ai_status,
        source=record.source,
        notes=record.notes,
        project_id=record.project_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        ai_raw_result=record.ai_raw_result,
        image_url=image_url,
    )


# ────────────────────────────────────────
# [P1-1] 编辑记录
# ────────────────────────────────────────

@router.put("/{record_id}", response_model=RecordDetailResponse)
async def update_record(
    record_id: int,
    req: RecordUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    编辑记录的基本信息和 AI 识别结果。
    修改 ai_raw_result 后会触发重新生成 embedding。
    """
    result = await db.execute(
        select(Record).where(Record.id == record_id, Record.user_id == user.id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 更新基本字段
    update_data = req.model_dump(exclude_unset=True)
    need_re_embed = False

    for key, value in update_data.items():
        if key == "ai_raw_result" and value is not None:
            # 合并更新 AI 原始结果（而非全量替换）
            existing = record.ai_raw_result or {}
            existing.update(value)
            record.ai_raw_result = existing
            need_re_embed = True
        else:
            setattr(record, key, value)

    await db.flush()

    # 如果修改了 AI 结果，重新生成 embedding
    if need_re_embed:
        try:
            from app.services import embedding_service
            await db.commit()
            await embedding_service.embed_record(record.id)
            logger.info(f"Record {record_id} re-embedded after edit")
        except Exception as e:
            logger.warning(f"Re-embedding failed for record {record_id}: {e}")

    # 返回详情
    image_url = None
    if record.file_key:
        try:
            image_url = cos_service.get_file_url(record.file_key)
        except Exception:
            pass

    return RecordDetailResponse(
        id=record.id,
        category=record.category,
        title=record.title,
        hospital=record.hospital,
        record_date=record.record_date,
        file_key=record.file_key,
        file_type=record.file_type,
        ai_status=record.ai_status,
        source=record.source,
        notes=record.notes,
        project_id=record.project_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        ai_raw_result=record.ai_raw_result,
        image_url=image_url,
    )


# ────────────────────────────────────────
# [P1-3] 处方确认
# ────────────────────────────────────────

@router.post("/{record_id}/confirm-prescription")
async def confirm_prescription(
    record_id: int,
    req: PrescriptionConfirmRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    用户确认处方中的药物。
    只有 ai_status="pending_confirmation" 且 category="prescription" 的记录可以确认。
    """
    result = await db.execute(
        select(Record).where(
            Record.id == record_id,
            Record.user_id == user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    if record.ai_status != "pending_confirmation":
        raise HTTPException(status_code=400, detail="该记录不需要确认")

    if record.category != "prescription":
        raise HTTPException(status_code=400, detail="该记录不是处方类型")

    # 创建用户确认的药物
    created = []
    for med_confirm in req.medications:
        if not med_confirm.confirmed:
            continue

        medication = Medication(
            user_id=user.id,
            prescription_record_id=record.id,
            name=med_confirm.name,
            dosage=med_confirm.dosage,
            frequency=med_confirm.frequency,
            scheduled_times=med_confirm.times or ["08:00"],
            start_date=record.record_date or date.today(),
            is_active=True,
        )
        db.add(medication)
        created.append(med_confirm.name)

    # 更新记录状态
    record.ai_status = "completed"
    await db.flush()

    # 生成当天用药任务
    if created:
        from app.routers.medications import _generate_tasks_for_med
        today = date.today()
        meds_result = await db.execute(
            select(Medication).where(
                Medication.prescription_record_id == record_id,
                Medication.user_id == user.id,
                Medication.is_active == True,
            )
        )
        for med in meds_result.scalars().all():
            if med.start_date <= today and (med.end_date is None or med.end_date >= today):
                await _generate_tasks_for_med(db, med, today)

    await db.flush()

    return {
        "ok": True,
        "created_medications": created,
        "message": f"已确认 {len(created)} 个药物" if created else "未添加药物",
    }
