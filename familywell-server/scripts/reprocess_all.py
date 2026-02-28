"""
重新处理所有已有记录 — 一次性脚本
────────────────────────────────
用途：部署新的 ai_service.py（含 raw_text 全文提取）后，
     对历史记录重新调 AI 识别 + 重新 embedding。

运行方式：
    docker compose exec api python -m scripts.reprocess_all

或者：
    docker compose exec api python scripts/reprocess_all.py
"""
import asyncio
import base64
import tempfile
import logging
from pathlib import Path

from sqlalchemy import select
from app.database import async_session
from app.models.record import Record
from app.services import ai_service, cos_service, embedding_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def reprocess_record(record_id: int):
    """对单条记录重新 AI 识别 + embedding。"""
    async with async_session() as db:
        try:
            result = await db.execute(select(Record).where(Record.id == record_id))
            record = result.scalar_one_or_none()
            if not record or not record.file_key:
                logger.warning(f"Record {record_id}: skipped (no file)")
                return

            # 1. 下载文件
            with tempfile.TemporaryDirectory() as tmp_dir:
                ext = record.file_key.rsplit(".", 1)[-1] if record.file_key else "jpg"
                local_path = Path(tmp_dir) / f"file.{ext}"
                cos_service.download_file(record.file_key, str(local_path))

                # PDF → image
                if record.file_type == "pdf":
                    from pdf2image import convert_from_path
                    images = convert_from_path(str(local_path), first_page=1, last_page=3)
                    img_path = Path(tmp_dir) / "page1.jpg"
                    images[0].save(str(img_path), "JPEG")
                    local_path = img_path

                with open(local_path, "rb") as f:
                    image_b64 = base64.b64encode(f.read()).decode()

            # 2. 重新调 AI 识别（新 prompt 会提取 raw_text）
            ai_result = await ai_service.recognize_image(image_b64)

            # 3. 更新 record
            record.ai_raw_result = ai_result
            record.category = ai_result.get("category", record.category)
            record.title = ai_result.get("title", record.title)
            record.hospital = ai_result.get("hospital", record.hospital)
            await db.commit()

            # 4. 重新 embedding
            await embedding_service.embed_record(record_id)

            raw_len = len(ai_result.get("raw_text", ""))
            logger.info(
                f"✅ Record {record_id}: category={ai_result.get('category')}, "
                f"raw_text={raw_len} chars"
            )

        except Exception as e:
            logger.error(f"❌ Record {record_id} failed: {e}")


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(Record.id)
            .where(Record.ai_status == "completed")
            .order_by(Record.id)
        )
        record_ids = [row[0] for row in result.fetchall()]

    logger.info(f"Found {len(record_ids)} records to reprocess")

    for i, rid in enumerate(record_ids):
        logger.info(f"Processing [{i+1}/{len(record_ids)}] record_id={rid}")
        await reprocess_record(rid)
        # 稍微 sleep 避免打爆豆包 API rate limit
        await asyncio.sleep(1)

    logger.info("🎉 All done!")


if __name__ == "__main__":
    asyncio.run(main())
