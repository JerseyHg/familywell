"""
RAG Service
───────────
本地向量检索 → 组装上下文 → 调豆包 LLM → 生成回答

支持两种模式：
- chat()        : 同步，等全部生成完一次性返回（fallback / 首页摘要用）
- chat_stream() : 流式，SSE 逐字推送，图表先行（主力模式）
"""
import json
import logging
from datetime import datetime, date, timedelta
from typing import AsyncGenerator

from openai import AsyncOpenAI
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user import User, UserProfile
from app.models.medication import Medication, MedicationTask
from app.models.health_indicator import HealthIndicator
from app.models.reminder import Reminder
from app.models.embedding import ChatHistory
from app.services import embedding_service
from app.services import chart_service

logger = logging.getLogger(__name__)
settings = get_settings()

_client = AsyncOpenAI(
    api_key=settings.DOUBAO_API_KEY,
    base_url=settings.DOUBAO_BASE_URL,
)

SYSTEM_PROMPT = """你是 FamilyWell 的 AI 家庭健康助手。

你的职责：
1. 基于用户和家人的真实健康档案数据来回答问题
2. 当数据不足时诚实说明，不编造
3. 给出的建议需标注数据来源（如"根据2月24日的PSA检查报告"）
4. 涉及疾病诊断时提醒用户咨询医生
5. 语气温暖专业，像一位懂医学的家人

你可以：
- 解读检查指标趋势
- 汇总用药情况
- 分析营养摄入
- 提供保险到期提醒
- 对比历史报告变化
- 回答"他/她最近怎么样"这类综合问题

下面是从用户健康档案中检索到的相关信息：

{context}

{realtime_data}

请基于以上信息回答用户的问题。如果信息不足以回答，请说明需要哪些额外数据。"""


# ════════════════════════════════════════
# 1. 获取实时补充数据
# ════════════════════════════════════════

async def get_realtime_context(db: AsyncSession, user_id: int) -> str:
    """查询结构化表获取实时数据，补充向量检索不一定能覆盖的最新信息。"""
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

    if not parts:
        return ""

    return "以下是实时数据（最新状态）：\n" + "\n".join(parts)


# ════════════════════════════════════════
# 2. 获取对话历史
# ════════════════════════════════════════

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


# ════════════════════════════════════════
# 3. 共用 context 构建（同步/流式都用）
# ════════════════════════════════════════

async def prepare_context(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    question: str,
    family_user_ids: list[int] | None = None,
) -> dict:
    """构建 RAG 所需的全部上下文。同步和流式共用。"""

    # 向量检索
    retrieved = await embedding_service.search_similar(
        db=db,
        user_id=user_id,
        query=question,
        family_user_ids=family_user_ids,
    )

    # 组装 context
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

    # 实时数据
    realtime_data = await get_realtime_context(db, user_id)

    # 对话历史
    history = await get_chat_history(db, user_id, session_id)

    # 组装 messages
    system_content = SYSTEM_PROMPT.format(
        context=context,
        realtime_data=realtime_data,
    )
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    # 图表
    charts = await chart_service.generate_charts(db, user_id, question)

    return {
        "messages": messages,
        "sources": sources,
        "charts": charts,
    }


async def save_chat_history(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    question: str,
    answer: str,
    sources: list[dict],
    token_count: int | None = None,
):
    """保存一轮对话到 chat_history 表。"""
    db.add(ChatHistory(
        user_id=user_id,
        session_id=session_id,
        role="user",
        content=question,
    ))
    db.add(ChatHistory(
        user_id=user_id,
        session_id=session_id,
        role="assistant",
        content=answer,
        sources=json.dumps([s["record_id"] for s in sources if s.get("record_id")]),
        token_count=token_count,
    ))
    await db.flush()


# ════════════════════════════════════════
# 4. 同步模式（fallback）
# ════════════════════════════════════════

async def chat(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    question: str,
    family_user_ids: list[int] | None = None,
) -> dict:
    """同步 RAG：等全部生成完一次性返回。"""
    ctx = await prepare_context(db, user_id, session_id, question, family_user_ids)

    try:
        response = await _client.chat.completions.create(
            model=settings.DOUBAO_CHAT_MODEL,
            messages=ctx["messages"],
            max_tokens=1500,
            temperature=0.4,
        )
        answer = response.choices[0].message.content.strip()
        token_count = response.usage.total_tokens if response.usage else None
    except Exception as e:
        logger.error(f"RAG chat LLM call failed: {e}")
        answer = "抱歉，AI 助手暂时无法回答，请稍后再试。"
        token_count = None

    await save_chat_history(db, user_id, session_id, question, answer, ctx["sources"], token_count)

    return {
        "answer": answer,
        "charts": ctx["charts"],
        "sources": ctx["sources"],
        "session_id": session_id,
    }


# ════════════════════════════════════════
# 5. 流式模式（SSE 逐字推送）
# ════════════════════════════════════════

async def chat_stream(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    question: str,
    family_user_ids: list[int] | None = None,
) -> AsyncGenerator[str, None]:
    """
    流式 RAG：SSE 格式逐步推送。

    事件顺序：
    1. type=charts  → 图表数据（查 DB 几 ms，最先推出）
    2. type=sources → 引用来源列表
    3. type=text    → AI 回答文字（豆包流式返回，逐块推送）
    4. type=done    → 结束信号 + session_id
    """
    # ── 1. 准备上下文（向量检索 + DB 查询 + 图表生成，<200ms） ──
    ctx = await prepare_context(db, user_id, session_id, question, family_user_ids)

    # ── 2. 先推图表（用户最先看到图表） ──
    if ctx["charts"]:
        yield _sse_line({"type": "charts", "charts": ctx["charts"]})

    # ── 3. 推引用来源 ──
    if ctx["sources"]:
        yield _sse_line({"type": "sources", "sources": ctx["sources"]})

    # ── 4. 流式调豆包 LLM ──
    full_answer = ""
    token_count = None

    try:
        stream = await _client.chat.completions.create(
            model=settings.DOUBAO_CHAT_MODEL,
            messages=ctx["messages"],
            max_tokens=1500,
            temperature=0.4,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                delta = chunk.choices[0].delta.content
                full_answer += delta
                yield _sse_line({"type": "text", "content": delta})

            # 最后一个 chunk 可能带 usage
            if hasattr(chunk, "usage") and chunk.usage:
                token_count = chunk.usage.total_tokens

    except Exception as e:
        logger.error(f"RAG stream LLM failed: {e}")
        full_answer = "抱歉，AI 助手暂时无法回答，请稍后再试。"
        yield _sse_line({"type": "text", "content": full_answer})

    # ── 5. 保存对话记录 ──
    await save_chat_history(
        db, user_id, session_id, question, full_answer, ctx["sources"], token_count,
    )

    # ── 6. 推结束信号 ──
    yield _sse_line({"type": "done", "session_id": session_id})


def _sse_line(data: dict) -> str:
    """格式化为 SSE data 行。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ════════════════════════════════════════
# 6. 快捷问答（首页 AI 提示用）
# ════════════════════════════════════════

async def quick_health_summary(
    db: AsyncSession, user_id: int
) -> str:
    """为首页生成 AI 健康概要，基于最新向量数据。"""
    query = "这个人最近的健康状况总结，包括用药、指标变化、需要注意的事项"

    retrieved = await embedding_service.search_similar(
        db=db,
        user_id=user_id,
        query=query,
        top_k=5,
    )

    if not retrieved:
        return ""

    context = "\n".join([r["content_text"] for r in retrieved])
    realtime = await get_realtime_context(db, user_id)

    try:
        response = await _client.chat.completions.create(
            model=settings.DOUBAO_CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是家庭健康助手。请根据以下数据，用 2-3 句话概括此人近期健康状况和需要注意的事项。语气温暖简洁。",
                },
                {
                    "role": "user",
                    "content": f"健康数据：\n{context}\n\n{realtime}",
                },
            ],
            max_tokens=200,
            temperature=0.5,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Quick health summary failed: {e}")
        return ""
