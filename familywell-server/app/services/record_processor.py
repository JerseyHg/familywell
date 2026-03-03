"""
app/services/record_processor.py — 记录处理管线
═══════════════════════════════════════════════════
★ PDF 双路径：文字类 PDF → 文本模型；扫描件 → 视觉模型（多页支持）
"""
import base64
import logging
import tempfile
from datetime import datetime, date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.record import Record
from app.models.health_indicator import HealthIndicator
from app.models.nutrition import NutritionLog
from app.models.medication import Medication
from app.models.insurance import Insurance
from app.models.reminder import Reminder
from app.services import ai_service, cos_service, embedding_service
from app.services.health_validator import validate_indicators_batch

logger = logging.getLogger(__name__)


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


# ════════════════════════════════════════
# ★ PDF 文字提取 & 类型检测
# ════════════════════════════════════════

def _extract_pdf_text(pdf_path: str) -> str:
    """
    尝试从 PDF 中提取文字。
    返回提取到的文本；如果是扫描件则返回空字符串或很少的文字。
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        texts = []
        for page in doc:
            texts.append(page.get_text())
        doc.close()
        return "\n".join(texts).strip()
    except Exception as e:
        logger.warning(f"PDF text extraction failed: {e}")
        return ""


def _is_text_pdf(extracted_text: str, min_chars: int = 50) -> bool:
    """判断 PDF 是文字类还是扫描件：提取到足够多的字符即为文字类。"""
    return len(extracted_text) > min_chars


# ════════════════════════════════════════
# 主处理管线
# ════════════════════════════════════════

async def process_record(record_id: int):
    """Main pipeline: download file → AI recognize → dispatch results."""
    async with async_session() as db:
        try:
            # 1. Get record
            result = await db.execute(select(Record).where(Record.id == record_id))
            record = result.scalar_one_or_none()
            if not record:
                logger.error(f"Record {record_id} not found")
                return

            record.ai_status = "processing"
            await db.commit()

            # 2. Download file from COS
            with tempfile.TemporaryDirectory() as tmp_dir:
                ext = record.file_key.rsplit(".", 1)[-1] if record.file_key else "jpg"
                local_path = Path(tmp_dir) / f"file.{ext}"
                cos_service.download_file(record.file_key, str(local_path))

                # 3. ★ 根据文件类型分流处理
                if record.file_type == "pdf":
                    ai_result = await _process_pdf(record_id, str(local_path), tmp_dir)
                else:
                    # ── 图片文件：原有逻辑不变 ──
                    with open(local_path, "rb") as f:
                        image_b64 = base64.b64encode(f.read()).decode()
                    ai_result = await ai_service.recognize_image(image_b64)

            # 6. Update record
            record.ai_raw_result = ai_result
            record.ai_processed_at = datetime.utcnow()

            category = ai_result.get("category", "other")
            record.category = category
            record.title = ai_result.get("title")
            record.hospital = ai_result.get("hospital")
            record.record_date = _parse_date(ai_result.get("date"))
            record.ai_status = "completed"

            # 7. Dispatch to sub-tables
            await _dispatch_result(db, record, ai_result)

            await db.commit()
            logger.info(f"Record {record_id} processed: category={category}")

            # 8. Generate embeddings (async, non-blocking)
            try:
                await embedding_service.embed_record(record_id)
                logger.info(f"Record {record_id} embedded successfully")
            except Exception as emb_err:
                logger.warning(f"Embedding failed for record {record_id}: {emb_err}")

            # 9. ★ 清除该用户的热点缓存（拍照上传完成，数据已变化）
            try:
                from app.services.rag_service import invalidate_user_cache
                await invalidate_user_cache(record.user_id)
            except Exception as cache_err:
                logger.warning(f"Cache invalidation failed for user {record.user_id}: {cache_err}")

        except Exception as e:
            logger.error(f"Failed to process record {record_id}: {e}")
            record.ai_status = "failed"
            record.ai_error = str(e)
            await db.commit()


# ════════════════════════════════════════
# ★ PDF 双路径处理
# ════════════════════════════════════════

async def _process_pdf(record_id: int, pdf_path: str, tmp_dir: str) -> dict:
    """
    PDF 智能分流：
    - 文字类 PDF → 提取文字 → 文本模型（更准确、更便宜、无页数限制）
    - 扫描件 PDF → 转图片 → 视觉模型（逐页识别后合并）
    """
    pdf_text = _extract_pdf_text(pdf_path)

    if _is_text_pdf(pdf_text):
        # ── 文字类 PDF：直接用文本接口 ──
        logger.info(f"Record {record_id}: text PDF detected ({len(pdf_text)} chars)")
        return await ai_service.recognize_text(pdf_text)
    else:
        # ── 扫描件 PDF：转图片走视觉模型 ──
        logger.info(f"Record {record_id}: scanned PDF detected")
        return await _process_scanned_pdf(record_id, pdf_path, tmp_dir)


async def _process_scanned_pdf(record_id: int, pdf_path: str, tmp_dir: str) -> dict:
    """扫描件 PDF：逐页转图片 → 视觉模型识别 → 合并结果。"""
    from pdf2image import convert_from_path

    images = convert_from_path(pdf_path, dpi=200)
    logger.info(f"Record {record_id}: scanned PDF has {len(images)} pages")

    if len(images) == 1:
        # 单页：直接识别
        img_path = Path(tmp_dir) / "page1.jpg"
        images[0].save(str(img_path), "JPEG")
        with open(img_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        return await ai_service.recognize_image(image_b64)

    # 多页：逐页识别后合并
    all_raw_texts = []
    ai_result = None

    for i, img in enumerate(images):
        img_path = Path(tmp_dir) / f"page{i + 1}.jpg"
        img.save(str(img_path), "JPEG")
        with open(img_path, "rb") as f:
            page_b64 = base64.b64encode(f.read()).decode()

        logger.info(f"Record {record_id}: recognizing page {i + 1}/{len(images)}")
        page_result = await ai_service.recognize_image(page_b64)

        # 第一页的结构化结果作为主结果
        if ai_result is None:
            ai_result = page_result
        else:
            # 后续页的指标、药物追加到主结果
            if page_result.get("indicators"):
                ai_result.setdefault("indicators", []).extend(
                    page_result["indicators"]
                )
            if page_result.get("medications"):
                ai_result.setdefault("medications", []).extend(
                    page_result["medications"]
                )

        all_raw_texts.append(page_result.get("raw_text", ""))

    # 合并所有页的 raw_text
    if ai_result:
        ai_result["raw_text"] = "\n\n".join(filter(None, all_raw_texts))

    return ai_result or {"category": "other", "title": "识别失败"}


# ════════════════════════════════════════
# 结果分发
# ════════════════════════════════════════

async def _dispatch_result(db: AsyncSession, record: Record, ai_result: dict):
    """Dispatch AI results to appropriate sub-tables."""
    category = ai_result.get("category", "other")
    user_id = record.user_id

    if category in ("checkup", "lab"):
        await _process_indicators(db, user_id, record, ai_result)

    elif category == "prescription":
        await _process_prescription(db, user_id, record, ai_result)

    elif category == "insurance":
        await _process_insurance(db, user_id, record, ai_result)

    elif category == "food":
        await _process_food(db, user_id, record, ai_result)

    elif category == "bp_reading":
        await _process_bp(db, user_id, record, ai_result)


async def _process_indicators(
    db: AsyncSession, user_id: int, record: Record, ai_result: dict
):
    """Extract health indicators from checkup/lab reports."""
    raw_indicators = ai_result.get("indicators", [])
    valid_indicators, warnings = validate_indicators_batch(raw_indicators)
    measured_at = record.record_date or datetime.utcnow()
    if warnings:
        logger.warning(f"Record {record.id} validation warnings: {warnings}")
        ai_result["_validation_warnings"] = warnings

    for ind in valid_indicators:
        hi = HealthIndicator(
            user_id=user_id,
            record_id=record.id,
            indicator_type=ind.get("type", "unknown"),
            value=float(ind.get("value", 0)),
            unit=ind.get("unit"),
            is_abnormal=ind.get("abnormal", False),
            reference_low=ind.get("reference_low"),
            reference_high=ind.get("reference_high"),
            measured_at=measured_at,
            source="ai_extract",
        )
        db.add(hi)

        # Create reminder for abnormal indicators
        if ind.get("abnormal"):
            reminder = Reminder(
                user_id=user_id,
                type="custom",
                title=f'{ind.get("name", "指标")}异常',
                description=f'{ind.get("name")}: {ind.get("value")} {ind.get("unit", "")}',
                priority="urgent",
                related_id=record.id,
                related_type="record",
                remind_at=datetime.utcnow(),
            )
            db.add(reminder)


async def _process_prescription(
    db: AsyncSession, user_id: int, record: Record, ai_result: dict
):
    """Extract medications from prescription."""
    medications = ai_result.get("medications", [])

    if not medications:
        return
    # 不再自动创建 Medication，改为待确认状态
    record.ai_status = "pending_confirmation"

    for med in medications:
        medication = Medication(
            user_id=user_id,
            prescription_record_id=record.id,
            name=med.get("name", "未知药品"),
            dosage=med.get("dosage"),
            frequency=med.get("frequency"),
            scheduled_times=med.get("times", ["08:00"]),
            start_date=record.record_date or date.today(),
            remaining_count=med.get("quantity"),
            is_active=True,
        )
        db.add(medication)


async def _process_insurance(
    db: AsyncSession, user_id: int, record: Record, ai_result: dict
):
    """Extract insurance info from policy document."""
    ins = Insurance(
        user_id=user_id,
        record_id=record.id,
        provider=ai_result.get("provider"),
        policy_type=ai_result.get("policy_type"),
        policy_number=ai_result.get("policy_number"),
        insured_name=ai_result.get("insured_name"),
        start_date=_parse_date(ai_result.get("start_date")),
        end_date=_parse_date(ai_result.get("end_date")),
        premium=ai_result.get("premium"),
        coverage=ai_result.get("coverage"),
        is_active=True,
    )
    db.add(ins)


async def _process_food(
    db: AsyncSession, user_id: int, record: Record, ai_result: dict
):
    """Extract nutrition data from food photo."""
    log = NutritionLog(
        user_id=user_id,
        record_id=record.id,
        meal_type=ai_result.get("meal_type"),
        food_items=ai_result.get("food_items"),
        calories=ai_result.get("calories"),
        protein_g=ai_result.get("protein_g"),
        fat_g=ai_result.get("fat_g"),
        carb_g=ai_result.get("carb_g"),
        fiber_g=ai_result.get("fiber_g"),
        sodium_mg=ai_result.get("sodium_mg"),
        logged_at=record.record_date or date.today(),
    )
    db.add(log)


async def _process_bp(
    db: AsyncSession, user_id: int, record: Record, ai_result: dict
):
    """Extract blood pressure readings."""
    measured = record.record_date or datetime.utcnow()

    if ai_result.get("systolic"):
        db.add(HealthIndicator(
            user_id=user_id, record_id=record.id,
            indicator_type="bp_systolic", value=float(ai_result["systolic"]),
            unit="mmHg", measured_at=measured, source="ai_extract",
        ))
    if ai_result.get("diastolic"):
        db.add(HealthIndicator(
            user_id=user_id, record_id=record.id,
            indicator_type="bp_diastolic", value=float(ai_result["diastolic"]),
            unit="mmHg", measured_at=measured, source="ai_extract",
        ))
    if ai_result.get("heart_rate"):
        db.add(HealthIndicator(
            user_id=user_id, record_id=record.id,
            indicator_type="heart_rate", value=float(ai_result["heart_rate"]),
            unit="bpm", measured_at=measured, source="ai_extract",
        ))
