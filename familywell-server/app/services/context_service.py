"""
app/services/context_service.py — RAG 上下文组装
══════════════════════════════════════════════════
★ 扩展 get_realtime_context：
  - 补充用户基础档案（过敏、病史、血型）
  - 补充保险到期信息
  - 补充近期就诊记录摘要（最近2条 visit）

★ 新增 _detect_content_types()：
  - 基于关键词映射意图 → content_types
  - prepare_context 传给 search_similar，避免 Top-K 被不相关类型占槽
  - 零额外 LLM 调用，纯关键词匹配，确定性强
"""
import logging
from datetime import datetime, date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication import Medication, MedicationTask
from app.models.health_indicator import HealthIndicator
from app.models.reminder import Reminder
from app.models.embedding import ChatHistory
from app.models.nutrition import NutritionLog
from app.models.user import UserProfile
from app.models.insurance import Insurance
from app.models.record import Record
from app.services import embedding_service
from app.services import chart_service

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
# System Prompt
# ══════════════════════════════════════════════════

SYSTEM_PROMPT = """你是 FamilyWell 的 AI 家庭健康助手，名字叫"小康"。

你的核心职责：
1. 优先基于用户的真实健康档案数据来回答问题
2. 引用数据时标注来源（如"根据2月24日的同济医院MR报告"）
3. 涉及疾病诊断时提醒用户咨询医生
4. 语气温暖专业，像一位懂医学的家人

回答策略（按优先级）：
A. 如果档案中有相关数据 → 基于数据详细回答，标注来源
B. 如果档案数据不足但问题涉及健康/医学 → 先说明档案中有什么，然后**结合你的医学知识给出通用解读和建议**，明确标注哪些是通用知识、哪些是个人数据
C. 如果问题完全无关健康 → 用温暖友好的语气简短回应，自然引导回健康话题

你可以做的事：
- 解读检查报告（MR、CT、血检、尿检等）的具体内容和含义
- 解释医学术语和指标的临床意义
- 分析指标趋势和异常值
- 汇总用药情况、分析营养摄入
- 提供健康知识科普（疾病预防、养生、运动、饮食等）
- 回答"他/她最近怎么样"这类综合问题

重要原则：
- 不要因为档案数据不全就完全拒绝回答。如果用户问"MR报告怎么看"，即使档案中只有部分信息，你也应该解读已有内容，并补充通用的MR报告阅读指南
- 回答要具体、有用，避免泛泛而谈或反复让用户"补充上传"
- 如果确实需要更多数据，在回答完已有信息后，再简短提一句可以补充什么

下面是从用户健康档案中检索到的相关信息：

{context}

{realtime_data}

请基于以上信息回答用户的问题。"""


# ══════════════════════════════════════════════════
# ★ 意图识别 → content_types 过滤
# ══════════════════════════════════════════════════

# 意图关键词表
_INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("indicator", [
        "血压", "血糖", "血脂", "胆固醇", "甘油三酯", "体重", "BMI", "心率", "脉搏",
        "血红蛋白", "白细胞", "血小板", "肌酐", "尿酸", "转氨酶", "PSA", "指标",
        "化验", "体检", "检验", "异常", "偏高", "偏低", "正常范围", "参考值",
    ]),
    ("medication", [
        "药", "用药", "吃药", "服药", "处方", "药物", "药名", "剂量", "频率",
        "漏服", "副作用", "药效", "停药", "换药", "配药", "开药",
    ]),
    ("food", [
        "饮食", "吃了", "喝了", "早餐", "午餐", "晚餐", "热量", "卡路里",
        "营养", "蛋白质", "碳水", "脂肪", "食物",
    ]),
    ("insurance", [
        "保险", "保单", "续保", "保费", "理赔", "保额", "到期", "投保",
    ]),
    ("visit", [
        "就诊", "看病", "门诊", "住院", "出院", "手术", "病历", "主诉",
        "症状", "诊断", "病情", "治疗", "医嘱", "复诊",
    ]),
    ("report", [
        "报告", "MR", "CT", "超声", "B超", "X光", "心电图", "检查", "影像",
        "所见", "结论",
    ]),
    ("profile", [
        "过敏", "病史", "血型", "档案", "既往",
    ]),
]

# 意图 → content_types 映射（宁宽勿窄）
_INTENT_TO_CONTENT_TYPES: dict[str, list[str]] = {
    "indicator":  ["indicator", "record_summary", "findings", "diagnosis", "raw_text"],
    "medication": ["medication", "record_summary", "recommendations", "diagnosis"],
    "food":       ["record_summary"],
    "insurance":  ["insurance", "record_summary"],
    "visit":      ["chief_complaint", "present_illness", "findings", "diagnosis",
                   "recommendations", "record_summary", "raw_text"],
    "report":     ["findings", "diagnosis", "recommendations", "raw_text",
                   "record_summary", "chief_complaint"],
    "profile":    ["profile", "record_summary"],
}

# 始终追加 profile，确保 LLM 始终看到用户基础信息
_ALWAYS_INCLUDE = ["profile"]


def _detect_content_types(question: str) -> list[str] | None:
    """
    关键词匹配意图 → 返回应检索的 content_types 列表。
    - 多意图并存时合并去重
    - 未匹配任何意图 → 返回 None（全量检索）
    - profile 始终追加
    """
    matched_intents: set[str] = set()

    for intent, keywords in _INTENT_KEYWORDS:
        for kw in keywords:
            if kw in question:
                matched_intents.add(intent)
                break

    if not matched_intents:
        logger.debug("No intent matched, using full retrieval")
        return None

    combined: list[str] = []
    for intent in matched_intents:
        for ct in _INTENT_TO_CONTENT_TYPES.get(intent, []):
            if ct not in combined:
                combined.append(ct)

    for ct in _ALWAYS_INCLUDE:
        if ct not in combined:
            combined.append(ct)

    logger.debug(f"Intent: {matched_intents} → content_types: {combined}")
    return combined


# ══════════════════════════════════════════════════
# 1. 实时补充数据
# ══════════════════════════════════════════════════

async def get_realtime_context(db: AsyncSession, user_id: int) -> str:
    """
    查询结构化表获取实时数据，补充向量检索不一定能覆盖的最新信息。
    """
    parts = []
    today = date.today()

    # ── 个人基础档案 ──
    profile_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile:
        profile_parts = []
        if profile.real_name:
            profile_parts.append(f"姓名：{profile.real_name}")
        if profile.gender:
            profile_parts.append(f"性别：{'男' if profile.gender == 'male' else '女'}")
        if profile.birthday:
            age = today.year - profile.birthday.year
            profile_parts.append(f"年龄：约{age}岁")
        if profile.blood_type:
            profile_parts.append(f"血型：{profile.blood_type}型")
        if profile.height_cm and profile.weight_kg:
            bmi = round(float(profile.weight_kg) / (float(profile.height_cm) / 100) ** 2, 1)
            profile_parts.append(f"身高体重：{profile.height_cm}cm / {profile.weight_kg}kg（BMI {bmi}）")
        allergies = profile.allergies or []
        if isinstance(allergies, list) and allergies:
            profile_parts.append(f"⚠️ 过敏史：{', '.join(str(a) for a in allergies)}")
        else:
            profile_parts.append("过敏史：无已知过敏")
        history = profile.medical_history or []
        if isinstance(history, list) and history:
            profile_parts.append(f"既往病史：{', '.join(str(h) for h in history)}")
        if profile_parts:
            parts.append("【个人基础档案】" + "；".join(profile_parts))

    # ── 今日用药任务 ──
    tasks_result = await db.execute(
        select(MedicationTask)
        .where(MedicationTask.user_id == user_id)
        .where(MedicationTask.scheduled_date == today)
    )
    tasks = tasks_result.scalars().all()
    if tasks:
        done = [t for t in tasks if t.status == "done"]
        pending = [t for t in tasks if t.status == "pending"]
        missed = [t for t in tasks if t.status == "missed"]
        parts.append(
            f"【今日用药】已完成 {len(done)}/{len(tasks)} 次"
            + (f"，待完成: {', '.join(t.medication_name or '药物' for t in pending)}" if pending else "")
            + (f"，漏服: {', '.join(t.medication_name or '药物' for t in missed)}" if missed else "")
        )

    # ── 近 30 天异常指标 ──
    thirty_ago = datetime.utcnow() - timedelta(days=30)
    abnormal_result = await db.execute(
        select(HealthIndicator)
        .where(HealthIndicator.user_id == user_id)
        .where(HealthIndicator.is_abnormal == True)
        .where(HealthIndicator.measured_at >= thirty_ago)
        .order_by(HealthIndicator.measured_at.desc())
        .limit(10)
    )
    abnormals = abnormal_result.scalars().all()
    if abnormals:
        items = [f"{a.indicator_type}: {a.value} {a.unit or ''}" for a in abnormals]
        parts.append(f"【近30天异常指标】{'; '.join(items)}")

    # ── 紧急提醒 ──
    reminders_result = await db.execute(
        select(Reminder)
        .where(Reminder.user_id == user_id)
        .where(Reminder.priority == "urgent")
        .where(Reminder.is_resolved == False)
        .limit(5)
    )
    reminders = reminders_result.scalars().all()
    if reminders:
        items = [f"{r.title}: {r.description or ''}" for r in reminders]
        parts.append(f"【紧急提醒】{'; '.join(items)}")

    # ── 在用药物 ──
    meds_result = await db.execute(
        select(Medication)
        .where(Medication.user_id == user_id)
        .where(Medication.is_active == True)
    )
    meds = meds_result.scalars().all()
    if meds:
        items = [f"{m.name} {m.dosage or ''} {m.frequency or ''}" for m in meds]
        parts.append(f"【当前用药】{'; '.join(items)}")

    # ── 今日饮食 ──
    nutrition_result = await db.execute(
        select(NutritionLog)
        .where(NutritionLog.user_id == user_id)
        .where(NutritionLog.logged_at == today)
    )
    meals = nutrition_result.scalars().all()
    if meals:
        meal_items = []
        for m in meals:
            if m.food_items:
                items_str = "、".join(
                    item.get("name", str(item)) if isinstance(item, dict) else str(item)
                    for item in m.food_items
                )
            else:
                items_str = "未知食物"
            cal_str = f"，约{m.calories}千卡" if m.calories else ""
            meal_items.append(f"{m.meal_type or ''}：{items_str}{cal_str}")
        parts.append(f"【今日饮食】{'；'.join(meal_items)}")

    # ── 保险到期信息 ──
    insurance_result = await db.execute(
        select(Insurance)
        .where(Insurance.user_id == user_id)
        .where(Insurance.is_active == True)
        .where(Insurance.end_date != None)
        .order_by(Insurance.end_date.asc())
    )
    insurances = insurance_result.scalars().all()
    if insurances:
        ins_items = []
        for ins in insurances:
            days_left = (ins.end_date - today).days
            if days_left < 0:
                status = "已过期"
            elif days_left <= 30:
                status = f"⚠️ 还剩{days_left}天到期"
            else:
                status = f"有效至{ins.end_date}"
            label = f"{ins.provider or ''} {ins.policy_type or '保险'}".strip()
            ins_items.append(f"{label}（{status}）")
        parts.append(f"【保险信息】{'; '.join(ins_items)}")

    # ── 近期就诊记录（最近2条） ──
    recent_records_result = await db.execute(
        select(Record)
        .where(Record.user_id == user_id)
        .where(Record.category.in_(["visit", "checkup", "lab"]))
        .where(Record.ai_status == "completed")
        .order_by(Record.record_date.desc())
        .limit(2)
    )
    recent_records = recent_records_result.scalars().all()
    if recent_records:
        rec_items = []
        for r in recent_records:
            date_str = r.record_date.strftime("%Y-%m-%d") if r.record_date else "日期未知"
            hospital_str = f" @ {r.hospital}" if r.hospital else ""
            diagnosis = ""
            if r.ai_raw_result and isinstance(r.ai_raw_result, dict):
                diagnosis = r.ai_raw_result.get("diagnosis") or ""
                if len(diagnosis) > 50:
                    diagnosis = diagnosis[:50] + "…"
            diag_str = f"，诊断：{diagnosis}" if diagnosis else ""
            rec_items.append(f"{date_str}{hospital_str} {r.title or r.category}{diag_str}")
        parts.append(f"【近期就诊记录】{'; '.join(rec_items)}")

    if not parts:
        return ""

    return "以下是实时数据（最新状态）：\n" + "\n".join(parts)


# ══════════════════════════════════════════════════
# 2. 对话历史
# ══════════════════════════════════════════════════

async def get_chat_history(
    db: AsyncSession, user_id: int, session_id: str, limit: int = 10
) -> list[dict]:
    """获取最近 N 轮对话作为上下文。"""
    result = await db.execute(
        select(ChatHistory)
        .where(ChatHistory.user_id == user_id)
        .where(ChatHistory.session_id == session_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


# ══════════════════════════════════════════════════
# 3. 完整上下文组装（同步/流式共用）
# ══════════════════════════════════════════════════

async def prepare_context(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    question: str,
    family_user_ids: list[int] | None = None,
) -> dict:
    """
    构建 RAG 所需的全部上下文。chat() 和 chat_stream() 共用。

    流程：
    1. ★ 意图识别 → content_types 过滤
    2. 向量检索（embedding_service.search_similar）
    3. 查询实时数据（get_realtime_context）
    4. 获取对话历史（get_chat_history）
    5. 格式化 SYSTEM_PROMPT + messages
    6. 生成图表数据（chart_service.generate_charts）
    """

    # ── ★ 意图识别 ──
    content_types = _detect_content_types(question)

    # ── 向量检索 ──
    retrieved = await embedding_service.search_similar(
        db=db,
        user_id=user_id,
        query=question,
        family_user_ids=family_user_ids,
        content_types=content_types,  # ★ 精准过滤
    )

    context_parts = []
    sources = []
    for i, chunk in enumerate(retrieved):
        context_parts.append(
            f"[来源{i+1}] ({chunk['content_type']}, {chunk['source_date'] or '日期未知'}, "
            f"相关度 {chunk['score']})\n{chunk['content_text']}"
        )
        sources.append({
            "record_id": chunk["record_id"],
            "content_type": chunk["content_type"],
            "category": chunk.get("category"),
            "score": chunk["score"],
        })

    context = "\n\n".join(context_parts) if context_parts else "（未检索到相关健康档案数据）"

    # ── 实时数据 ──
    realtime_data = await get_realtime_context(db, user_id)

    # ── 对话历史 ──
    history = await get_chat_history(db, user_id, session_id)

    # ── 组装 messages ──
    system_content = SYSTEM_PROMPT.format(
        context=context,
        realtime_data=realtime_data,
    )
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    # ── 图表 ──
    charts = await chart_service.generate_charts(db, user_id, question)

    return {
        "messages": messages,
        "sources": sources,
        "charts": charts,
    }
