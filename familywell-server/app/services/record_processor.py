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

logger = logging.getLogger(__name__)


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


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

                # 3. Convert PDF to images if needed
                if record.file_type == "pdf":
                    from pdf2image import convert_from_path

                    images = convert_from_path(str(local_path), first_page=1, last_page=3)
                    # Use first page for recognition
                    img_path = Path(tmp_dir) / "page1.jpg"
                    images[0].save(str(img_path), "JPEG")
                    local_path = img_path

                # 4. Encode to base64
                with open(local_path, "rb") as f:
                    image_b64 = base64.b64encode(f.read()).decode()

            # 5. Call Doubao AI
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

        except Exception as e:
            logger.error(f"Failed to process record {record_id}: {e}")
            record.ai_status = "failed"
            record.ai_error = str(e)
            await db.commit()


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
    indicators = ai_result.get("indicators", [])
    measured_at = record.record_date or datetime.utcnow()

    for ind in indicators:
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
