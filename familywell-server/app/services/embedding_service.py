"""
Embedding Service
─────────────────
- 调用豆包 embedding-vision API 生成向量
- 将 AI 识别结果转为自然语言文本 → embedding → 存入 record_embedding
- 支持按用户检索 top-K 相似片段

✅ 新增：
- raw_text 全文 OCR 分段 embedding（每段 ~500 字）
- findings / diagnosis / recommendations 单独 embedding
- visit 类型（MR报告、出院小结等）完整文本存储
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
from app.models.user import UserProfile

logger = logging.getLogger(__name__)
settings = get_settings()

# httpx 异步客户端（复用连接池）
_http_client = httpx.AsyncClient(timeout=30.0)

# raw_text 分段大小（字符数），太长的文本分段 embedding 效果更好
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


# ════════════════════════════════════════
# 1. 生成 Embedding 向量
# ════════════════════════════════════════

async def generate_embedding(text_input: str) -> list[float]:
    """
    调用豆包 embedding-vision multimodal API，返回 2048 维向量。
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

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    将长文本切分成多个片段，每段 ~chunk_size 字符，相邻段有 overlap 字符重叠。
    尽量在句号、换行处切分，避免截断句子。
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            # 尝试在句号、换行、分号处切分
            best_break = -1
            for sep in ['\n', '。', '；', '，', '.', ';']:
                pos = text.rfind(sep, start + chunk_size // 2, end)
                if pos > best_break:
                    best_break = pos

            if best_break > start:
                end = best_break + 1  # 包含分隔符

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap if end < len(text) else end

    return chunks


def ai_result_to_texts(record: Record) -> list[dict]:
    """
    把一条 record 的 AI 识别结果转化为多条可 embedding 的文本片段。
    每条返回 { content_type, content_text, category, source_date }

    ✅ 语音记录走快速路径：直接用 raw_text 做 embedding
    ✅ 拍照上传走完整解析：结构化字段 + raw_text 分段
    """
    result = record.ai_raw_result
    if not result:
        return []
    if isinstance(result, str):
        import json
        result = json.loads(result)

    category = result.get("category", "other")
    title = result.get("title", "")
    date_str = result.get("date", str(record.record_date or ""))
    raw_text = result.get("raw_text") or ""

    # ════════════════════════════════════
    # ★ 快速路径：语音记录直接用 raw_text
    # 语音记录的 raw_text 已经是完整自然语言，不需要拼字段
    # ════════════════════════════════════
    if raw_text and getattr(record, "source", None) == "voice":
        prefix = f"【{date_str} · {title}】" if date_str or title else ""
        full_text = prefix + raw_text
        chunks = _chunk_text(full_text)
        return [
            {
                "content_type": "record_summary",
                "content_text": chunk,
                "category": category,
                "source_date": date_str,
            }
            for chunk in chunks
        ]

    # ════════════════════════════════════
    # 拍照上传的完整解析路径
    # ════════════════════════════════════

    fragments = []
    hospital = result.get("hospital", "")

    # ── 文档前缀 ──
    prefix = ""
    prefix_parts = []
    if date_str:
        prefix_parts.append(date_str)
    if hospital:
        prefix_parts.append(hospital)
    if title:
        prefix_parts.append(title)
    if prefix_parts:
        prefix = "【" + " · ".join(prefix_parts) + "】"

    # ── 整体摘要 ──
    summary_parts = [f"记录类型: {category}"]
    if title:
        summary_parts.append(f"标题: {title}")
    if hospital:
        summary_parts.append(f"医院: {hospital}")
    if date_str:
        summary_parts.append(f"日期: {date_str}")

    # ── 提取 findings / diagnosis / recommendations ──
    findings = result.get("findings") or ""
    diagnosis = result.get("diagnosis") or ""
    recommendations = result.get("recommendations") or ""
    chief_complaint = result.get("chief_complaint") or ""
    present_illness = result.get("present_illness") or ""
    past_history = result.get("past_history") or ""
    physical_exam = result.get("physical_exam") or ""
    department = result.get("department") or ""
    doctor = result.get("doctor") or ""

    if department:
        summary_parts.append(f"科室: {department}")
    if doctor:
        summary_parts.append(f"医生: {doctor}")

    # 医学文本字段单独 embedding
    medical_fields = [
        ("chief_complaint", "主诉", chief_complaint),
        ("present_illness", "现病史", present_illness),
        ("past_history", "既往史", past_history),
        ("physical_exam", "体格检查", physical_exam),
        ("findings", "检查所见", findings),
        ("diagnosis", "诊断结论", diagnosis),
        ("recommendations", "建议/医嘱", recommendations),
    ]
    for field_key, field_label, field_text in medical_fields:
        if field_text and len(field_text) > 5:
            summary_parts.append(f"{field_label}: {field_text[:200]}")
            fragments.append({
                "content_type": field_key,
                "content_text": f"{prefix} {field_label}：{field_text}",
                "category": category,
                "source_date": date_str,
            })

    # ── 分类处理 ──
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

            fragments.append({
                "content_type": "indicator",
                "content_text": f"{prefix} 检查结果 — {name}: {val} {unit}，{abnormal}。{ref}",
                "category": category,
                "source_date": date_str,
            })

    elif category == "prescription":
        meds = result.get("medications", [])
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
                "content_text": f"{prefix} 处方: {name} {dosage}，用法 {freq}。{f'开具 {qty} 片/粒。' if qty else ''}{f'医生: {doctor}。' if doctor else ''}",
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
            "content_text": f"{prefix} 保险信息 — {result.get('provider', '')} {result.get('policy_type', '')}，被保人 {result.get('insured_name', '')}，有效期 {result.get('start_date', '')} 至 {result.get('end_date', '')}，年保费 {result.get('premium', '')} 元，保额 {result.get('coverage', '')} 元。",
            "category": "insurance",
            "source_date": date_str,
        })

    elif category == "food":
        items = result.get("food_items", [])
        item_str = "、".join([
            i if isinstance(i, str) else f"{i.get('name', '')}{i.get('amount', '')}"
            for i in items
        ]) if items else ""
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

    # ── raw_text 全文分段 embedding（拍照上传的 OCR 全文） ──
    if raw_text and len(raw_text) > 20:
        chunks = _chunk_text(raw_text)
        for i, chunk in enumerate(chunks):
            fragments.append({
                "content_type": "raw_text",
                "content_text": f"{prefix} 原文（{i+1}/{len(chunks)}）：{chunk}",
                "category": category,
                "source_date": date_str,
            })
        logger.info(
            f"Record raw_text: {len(raw_text)} chars → {len(chunks)} chunks"
        )

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
    """
    if top_k is None:
        top_k = settings.RAG_TOP_K

    # 生成 query embedding
    query_vec = await generate_embedding(query)

    # 构建 SQL
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

# ════════════════════════════════════════
# 5. User Profile Embedding
# ════════════════════════════════════════

def _profile_to_text(profile: "UserProfile") -> str | None:
    """
    将 UserProfile 的关键字段拼成一段自然语言文本，用于 embedding。
    只包含对健康咨询有意义的字段。
    返回 None 表示 profile 内容不足，跳过 embedding。
    """
    parts = []

    if profile.real_name:
        parts.append(f"姓名：{profile.real_name}")
    if profile.gender:
        gender_label = "男" if profile.gender == "male" else "女"
        parts.append(f"性别：{gender_label}")
    if profile.birthday:
        from datetime import date
        age = date.today().year - profile.birthday.year
        parts.append(f"出生日期：{profile.birthday}（约{age}岁）")
    if profile.blood_type:
        parts.append(f"血型：{profile.blood_type}型")
    if profile.height_cm:
        parts.append(f"身高：{profile.height_cm}cm")
    if profile.weight_kg:
        parts.append(f"体重：{profile.weight_kg}kg")
        if profile.height_cm:
            bmi = round(float(profile.weight_kg) / (float(profile.height_cm) / 100) ** 2, 1)
            parts.append(f"BMI：{bmi}")

    # 过敏史——最重要的字段，拼接时强调
    allergies = profile.allergies or []
    if isinstance(allergies, list) and allergies:
        parts.append(f"过敏史：{', '.join(str(a) for a in allergies)}")
    else:
        parts.append("过敏史：无已知过敏")

    # 既往病史
    history = profile.medical_history or []
    if isinstance(history, list) and history:
        parts.append(f"既往病史：{', '.join(str(h) for h in history)}")
    else:
        parts.append("既往病史：无")

    if profile.emergency_contact_name:
        parts.append(f"紧急联系人：{profile.emergency_contact_name}（{profile.emergency_contact_phone or '无电话'}）")

    if len(parts) < 2:
        return None  # 信息太少，不值得 embed

    return "【个人健康档案】\n" + "\n".join(parts)


async def embed_user_profile(user_id: int) -> bool:
    """
    将用户 profile 的关键字段生成 embedding，存入 record_embedding。
    - record_id = None（不关联具体 record）
    - content_type = 'profile'
    - 每次调用先删旧 embedding，再写新的（幂等）

    在以下时机调用：
    1. onboarding 完成时（profile.onboarding_completed 变为 True）
    2. 用户更新 profile 时（PUT /api/profile）

    返回 True 表示成功，False 表示 profile 内容不足或失败。
    """
    async with async_session() as db:
        try:
            from app.models.user import UserProfile
            result = await db.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                logger.warning(f"embed_user_profile: no profile for user {user_id}")
                return False

            text = _profile_to_text(profile)
            if not text:
                logger.info(f"embed_user_profile: profile too sparse for user {user_id}, skipped")
                return False

            # 先删除旧的 profile embedding
            await db.execute(
                delete(RecordEmbedding).where(
                    RecordEmbedding.user_id == user_id,
                    RecordEmbedding.content_type == "profile",
                )
            )

            # 生成新 embedding
            vec = await generate_embedding(text)
            emb = RecordEmbedding(
                record_id=None,        # profile 不关联具体 record
                user_id=user_id,
                content_type="profile",
                content_text=text,
                embedding=vec,
                category="profile",
                source_date=None,
            )
            db.add(emb)
            await db.commit()

            logger.info(f"embed_user_profile: user {user_id} profile embedded ({len(text)} chars)")
            return True

        except Exception as e:
            logger.error(f"embed_user_profile failed for user {user_id}: {e}")
            await db.rollback()
            return False