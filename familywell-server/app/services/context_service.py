"""
app/services/context_service.py — RAG 上下文组装
──────────────────────────────────────────────────────
★ 从 rag_service.py 中拆出的上下文构建层。

职责：
- 查询结构化表获取实时数据（用药、指标、饮食、提醒）
- 获取对话历史
- 向量检索 + 实时数据 + 历史 + 图表 → 组装完整 RAG messages

被 rag_service.py 的 chat() / chat_stream() 调用。
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
# 1. 实时补充数据
# ══════════════════════════════════════════════════

async def get_realtime_context(db: AsyncSession, user_id: int) -> str:
    """
    查询结构化表获取实时数据，补充向量检索不一定能覆盖的最新信息。

    包括：今日用药任务、近 30 天异常指标、紧急提醒、在用药物、今日饮食。
    """
    parts = []

    # ── 今日用药任务 ──
    today = date.today()
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

    # ── 最近异常指标（30天内） ──
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
            items_str = "、".join(m.food_items) if m.food_items else "未知食物"
            cal_str = f"，约{m.calories}千卡" if m.calories else ""
            meal_items.append(f"{m.meal_type or ''}：{items_str}{cal_str}")
        parts.append(f"【今日饮食】{'；'.join(meal_items)}")

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
    1. 向量检索（embedding_service.search_similar）
    2. 查询实时数据（get_realtime_context）
    3. 获取对话历史（get_chat_history）
    4. 格式化 SYSTEM_PROMPT + messages
    5. 生成图表数据（chart_service.generate_charts）

    返回：
    {
        "messages": [...],   # LLM 的完整 messages 参数
        "sources": [...],    # 引用来源列表
        "charts": [...],     # 图表数据
    }
    """

    # ── 向量检索 ──
    retrieved = await embedding_service.search_similar(
        db=db,
        user_id=user_id,
        query=question,
        family_user_ids=family_user_ids,
    )

    # 组装 context 文本
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
