"""
Embedding Service
─────────────────
- 调用豆包 embedding-vision API 生成向量
- 将 AI 识别结果转为自然语言文本 → embedding → 存入 record_embedding
- 支持按用户检索 top-K 相似片段
"""
import json
import logging
from datetime import datetime

import httpx
from sqlalchemy import select, text, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session
from app.models.record import Record
from app.models.embedding import RecordEmbedding

logger = logging.getLogger(__name__)
settings = get_settings()

# httpx 异步客户端（复用连接池）
_http_client = httpx.AsyncClient(timeout=30.0)


# ════════════════════════════════════════
# 1. 生成 Embedding 向量
# ════════════════════════════════════════

async def generate_embedding(text_input: str) -> list[float]:
    """
    调用豆包 embedding-vision multimodal API，返回 2048 维向量。

    接口格式：POST /embeddings/multimodal
    请求体：{"model": "...", "input": [{"type": "text", "text": "..."}]}
    响应体：{"data": {"embedding": [...]}}
    """
    url = f"{settings.DOUBAO_BASE_URL}/embeddings/multimodal"

    try:
        resp = await _http_client.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.DOUBAO_API_KEY}",
            },
            json={
                "model": settings.DOUBAO_EMBEDDING_MODEL,
                "input": [{"type": "text", "text": text_input}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"]["embedding"]
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        raise


# ════════════════════════════════════════
# 2. AI 识别结果 → 自然语言文本
# ════════════════════════════════════════

def ai_result_to_texts(record: Record) -> list[dict]:
    """
    把一条 record 的 AI 识别结果转化为多条可 embedding 的文本片段。
    每条返回 { content_type, content_text, category, source_date }
    """
    result = record.ai_raw_result
    if not result:
        return []

    fragments = []
    category = result.get("category", "other")
    title = result.get("title", "")
    hospital = result.get("hospital", "")
    date_str = result.get("date", str(record.record_date or ""))

    # ── 整体摘要 ──
    summary_parts = [f"记录类型: {category}"]
    if title:
        summary_parts.append(f"标题: {title}")
    if hospital:
        summary_parts.append(f"医院: {hospital}")
    if date_str:
        summary_parts.append(f"日期: {date_str}")

    if category in ("checkup", "lab"):
        indicators = result.get("indicators", [])
        for ind in indicators:
            name = ind.get("name", "")
            val = ind.get("value", "")
            unit = ind.get("unit", "")
            abnormal = "异常" if ind.get("abnormal") else "正常"
            ref = ""
            if ind.get("reference_low") is not None and ind.get("reference_high") is not None:
                ref = f"，参考范围 {ind['reference_low']}-{ind['reference_high']} {unit}"

            ind_text = f"{name}: {val} {unit}（{abnormal}{ref}）"
            summary_parts.append(ind_text)

            # 每条指标也单独 embedding，方便精确检索
            fragments.append({
                "content_type": "indicator",
                "content_text": f"{date_str} 检查结果 — {name}: {val} {unit}，{abnormal}。来自{hospital or '未知医院'}的{title or '检查报告'}。{ref}",
                "category": category,
                "source_date": date_str,
            })

    elif category == "prescription":
        meds = result.get("medications", [])
        doctor = result.get("doctor", "")
        if doctor:
            summary_parts.append(f"开方医生: {doctor}")
        for med in meds:
            name = med.get("name", "")
            dosage = med.get("dosage", "")
            freq = med.get("frequency", "")
            qty = med.get("quantity", "")
            med_text = f"处方药物 — {name} {dosage}，{freq}，开具{qty}{'片/粒' if qty else ''}"
            summary_parts.append(med_text)

            fragments.append({
                "content_type": "medication",
                "content_text": f"{date_str} {hospital or ''}处方: {name} {dosage}，用法 {freq}。{f'开具 {qty} 片/粒。' if qty else ''}{f'医生: {doctor}。' if doctor else ''}",
                "category": "prescription",
                "source_date": date_str,
            })

    elif category == "insurance":
        for key, label in [
            ("provider", "保险公司"), ("policy_type", "险种"),
            ("insured_name", "被保人"), ("start_date", "开始日期"),
            ("end_date", "到期日期"), ("premium", "年保费"),
            ("coverage", "保额"),
        ]:
            if result.get(key):
                summary_parts.append(f"{label}: {result[key]}")

        fragments.append({
            "content_type": "insurance",
            "content_text": f"保险信息 — {result.get('provider', '')} {result.get('policy_type', '')}，被保人 {result.get('insured_name', '')}，有效期 {result.get('start_date', '')} 至 {result.get('end_date', '')}，年保费 {result.get('premium', '')} 元，保额 {result.get('coverage', '')} 元。",
            "category": "insurance",
            "source_date": date_str,
        })

    elif category == "food":
        items = result.get("food_items", [])
        item_str = "、".join([f"{i.get('name', '')}{i.get('amount', '')}" for i in items]) if items else ""
        meal = result.get("meal_type", "")
        cal = result.get("calories", "")
        summary_parts.append(f"餐别: {meal}")
        if item_str:
            summary_parts.append(f"食物: {item_str}")
        summary_parts.append(
            f"热量 {cal}kcal，蛋白质 {result.get('protein_g', '')}g，脂肪 {result.get('fat_g', '')}g，碳水 {result.get('carb_g', '')}g"
        )

    elif category == "bp_reading":
        sys_val = result.get("systolic", "")
        dia_val = result.get("diastolic", "")
        hr = result.get("heart_rate", "")
        summary_parts.append(f"血压: {sys_val}/{dia_val} mmHg")
        if hr:
            summary_parts.append(f"心率: {hr} bpm")

    # ── 整体摘要作为一条 embedding ──
    summary_text = "\n".join(summary_parts)
    fragments.insert(0, {
        "content_type": "record_summary",
        "content_text": summary_text,
        "category": category,
        "source_date": date_str,
    })

    return fragments


# ════════════════════════════════════════
# 3. 对一条 record 执行完整 embedding 流程
# ════════════════════════════════════════

async def embed_record(record_id: int):
    """下载 record → 转文本 → embedding → 存入 record_embedding。"""
    async with async_session() as db:
        try:
            result = await db.execute(select(Record).where(Record.id == record_id))
            record = result.scalar_one_or_none()
            if not record or not record.ai_raw_result:
                return

            # 先删除该 record 的旧 embedding（支持重新处理）
            await db.execute(
                delete(RecordEmbedding).where(RecordEmbedding.record_id == record_id)
            )

            # 转文本片段
            fragments = ai_result_to_texts(record)
            if not fragments:
                return

            # 批量 embedding（逐条调用，豆包 embedding 接口很快）
            for frag in fragments:
                vec = await generate_embedding(frag["content_text"])
                emb = RecordEmbedding(
                    record_id=record.id,
                    user_id=record.user_id,
                    content_type=frag["content_type"],
                    content_text=frag["content_text"],
                    embedding=vec,
                    category=frag.get("category"),
                    source_date=frag.get("source_date"),
                )
                db.add(emb)

            await db.commit()
            logger.info(f"Record {record_id}: embedded {len(fragments)} fragments")

        except Exception as e:
            logger.error(f"Embedding failed for record {record_id}: {e}")
            await db.rollback()


# ════════════════════════════════════════
# 4. 向量检索（语义搜索核心）
# ════════════════════════════════════════

async def search_similar(
    db: AsyncSession,
    user_id: int,
    query: str,
    top_k: int = None,
    content_types: list[str] | None = None,
    family_user_ids: list[int] | None = None,
) -> list[dict]:
    """
    语义检索：query → embedding → pgvector cosine 相似度 → top-K 结果。

    Args:
        user_id: 当前用户 ID
        query: 用户的问题 / 搜索词
        top_k: 返回条数
        content_types: 过滤类型 ['record_summary', 'indicator', ...]
        family_user_ids: 管理者可查看全家数据，传入家人的 user_id 列表

    Returns:
        [{ content_text, content_type, category, source_date, record_id, score }]
    """
    if top_k is None:
        top_k = settings.RAG_TOP_K

    # 生成 query embedding
    query_vec = await generate_embedding(query)

    # 构建 SQL（pgvector 的 <=> 是 cosine distance，越小越相似）
    # score = 1 - distance 转成相似度
    target_ids = family_user_ids if family_user_ids else [user_id]
    id_list = ",".join(str(i) for i in target_ids)

    type_filter = ""
    if content_types:
        types_str = ",".join(f"'{t}'" for t in content_types)
        type_filter = f"AND content_type IN ({types_str})"

    sql = text(f"""
        SELECT
            id, record_id, user_id, content_type, content_text,
            category, source_date,
            1 - (embedding <=> :vec) AS score
        FROM record_embedding
        WHERE user_id IN ({id_list})
        {type_filter}
        ORDER BY embedding <=> :vec
        LIMIT :top_k
    """)

    result = await db.execute(sql, {
        "vec": str(query_vec),
        "top_k": top_k,
    })
    rows = result.fetchall()

    return [
        {
            "id": row.id,
            "record_id": row.record_id,
            "user_id": row.user_id,
            "content_type": row.content_type,
            "content_text": row.content_text,
            "category": row.category,
            "source_date": row.source_date,
            "score": round(float(row.score), 4),
        }
        for row in rows
        if float(row.score) >= settings.RAG_SCORE_THRESHOLD
    ]
