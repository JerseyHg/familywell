"""
app/services/rag_service.py — RAG 问答 + 缓存
──────────────────────────────────────────────────────
★ 重构后：上下文组装逻辑已迁移至 context_service.py。
  本文件仅保留：
  - Redis 热点缓存层
  - LLM 调用（chat / chat_stream）
  - 对话历史持久化（save_chat_history）
  - 首页摘要（quick_health_summary）
"""
import json
import hashlib
import logging
from datetime import datetime, date, timedelta
from typing import AsyncGenerator

import redis.asyncio as aioredis
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.embedding import ChatHistory
from app.services import embedding_service
from app.services.context_service import prepare_context  # ★ 从 context_service 导入

logger = logging.getLogger(__name__)
settings = get_settings()

_client = AsyncOpenAI(
    api_key=settings.DOUBAO_API_KEY,
    base_url=settings.DOUBAO_BASE_URL,
)


# ══════════════════════════════════════════════════
# Redis 热点缓存
# ══════════════════════════════════════════════════

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """获取 Redis 连接（懒初始化）。"""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


# 热点问题缓存 TTL（秒）：数据每天都在变化，缓存 2 小时
HOT_CACHE_TTL = 2 * 60 * 60

# ✅ 模板问题列表（和前端 homePrompts 对应）
HOT_QUESTIONS = {
    "过去7天饮食情况",
    "这周药吃齐了吗",
    "血压最近趋势怎样",
    "最近身体怎么样",
    "PSA 变化趋势",
    "保险什么时候到期",
    "有什么需要注意的",
    "下次该做什么检查",
}


def _cache_key(user_id: int, question: str) -> str:
    """生成 Redis 缓存 key。"""
    q_hash = hashlib.md5(question.encode()).hexdigest()[:8]
    return f"fw:chat:{user_id}:{q_hash}"


async def get_cached_answer(user_id: int, question: str) -> dict | None:
    """尝试从 Redis 读取缓存的回答。"""
    try:
        r = await get_redis()
        data = await r.get(_cache_key(user_id, question))
        if data:
            logger.info(f"Cache HIT for user={user_id} q='{question}'")
            return json.loads(data)
    except Exception as e:
        logger.warning(f"Redis get failed: {e}")
    return None


async def set_cached_answer(user_id: int, question: str, answer: dict):
    """将回答写入 Redis 缓存。"""
    try:
        r = await get_redis()
        await r.setex(
            _cache_key(user_id, question),
            HOT_CACHE_TTL,
            json.dumps(answer, ensure_ascii=False),
        )
        logger.info(f"Cache SET for user={user_id} q='{question}' TTL={HOT_CACHE_TTL}s")
    except Exception as e:
        logger.warning(f"Redis set failed: {e}")


async def invalidate_user_cache(user_id: int):
    """
    清除指定用户的所有热点问题缓存。

    任何数据写入后都应调用此函数，包括：
    - 语音录入（voice_input.py）
    - 拍照上传识别完成（record_processor.py）
    - 编辑记录（records.py PUT）
    - 确认处方（records.py confirm-prescription）
    - 用药打卡（medications.py complete_task）
    - 手动创建药物（medications.py create）
    """
    try:
        r = await get_redis()
        keys_to_delete = [_cache_key(user_id, q) for q in HOT_QUESTIONS]
        if keys_to_delete:
            deleted = await r.delete(*keys_to_delete)
            logger.info(f"Cache invalidated for user {user_id}: {deleted} keys cleared")
    except Exception as e:
        logger.warning(f"Cache invalidation failed for user {user_id} (non-fatal): {e}")


# ══════════════════════════════════════════════════
# 对话历史持久化
# ══════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════
# 同步模式（fallback）
# ══════════════════════════════════════════════════

async def chat(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    question: str,
    family_user_ids: list[int] | None = None,
) -> dict:
    """同步 RAG：等全部生成完一次性返回。"""

    # ✅ 热点缓存：检查 Redis
    if question in HOT_QUESTIONS:
        cached = await get_cached_answer(user_id, question)
        if cached:
            await save_chat_history(db, user_id, session_id, question, cached["answer"], cached.get("sources", []))
            cached["session_id"] = session_id
            return cached

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

    result = {
        "answer": answer,
        "charts": ctx["charts"],
        "sources": ctx["sources"],
        "session_id": session_id,
    }

    # ✅ 热点缓存：写入 Redis
    if question in HOT_QUESTIONS:
        await set_cached_answer(user_id, question, {
            "answer": answer,
            "charts": ctx["charts"],
            "sources": ctx["sources"],
        })

    return result


# ══════════════════════════════════════════════════
# 流式模式（SSE 逐字推送）
# ══════════════════════════════════════════════════

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

    # ✅ 热点缓存：命中时以"伪流式"推送
    if question in HOT_QUESTIONS:
        cached = await get_cached_answer(user_id, question)
        if cached:
            logger.info(f"Streaming from cache for user={user_id}")

            if cached.get("charts"):
                yield _sse_line({"type": "charts", "charts": cached["charts"]})
            if cached.get("sources"):
                yield _sse_line({"type": "sources", "sources": cached["sources"]})

            yield _sse_line({"type": "text", "content": cached["answer"]})

            await save_chat_history(
                db, user_id, session_id, question,
                cached["answer"], cached.get("sources", []),
            )

            yield _sse_line({"type": "done", "session_id": session_id})
            return

    # ── 1. 准备上下文 ──
    ctx = await prepare_context(db, user_id, session_id, question, family_user_ids)

    # ── 2. 先推图表 ──
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

    # ✅ 热点缓存：写入 Redis
    if question in HOT_QUESTIONS and full_answer:
        await set_cached_answer(user_id, question, {
            "answer": full_answer,
            "charts": ctx["charts"],
            "sources": [s for s in ctx["sources"]],
        })

    # ── 6. 推结束信号 ──
    yield _sse_line({"type": "done", "session_id": session_id})


def _sse_line(data: dict) -> str:
    """格式化为 SSE data 行。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ══════════════════════════════════════════════════
# 首页健康摘要
# ══════════════════════════════════════════════════

async def quick_health_summary(
    db: AsyncSession, user_id: int
) -> str:
    """为首页生成温馨健康小贴士，简短积极。"""
    query = "这个人最近的生活习惯、饮食、用药情况"

    retrieved = await embedding_service.search_similar(
        db=db,
        user_id=user_id,
        query=query,
        top_k=3,
    )

    if not retrieved:
        return ""

    context = "\n".join([r["content_text"] for r in retrieved])

    try:
        response = await _client.chat.completions.create(
            model=settings.DOUBAO_CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个温暖的家庭健康助手。请根据用户近期的健康数据，生成一条简短的健康小贴士。\n\n"
                        "要求：\n"
                        "- 最多2句话，不超过50个字\n"
                        "- 语气温暖阳光，像朋友的关心\n"
                        "- 给出具体可行的小建议（饮食、运动、作息、情绪等）\n"
                        "- 绝对不要提及具体疾病名称、手术、肿瘤、异常指标等负面信息\n"
                        "- 绝对不要让用户感到焦虑或不安\n"
                        "- 可以结合季节、天气、时间段给建议\n\n"
                        "好的示例：\n"
                        "- 今天记得多喝水哦，适当散步15分钟，心情会更好 ☀️\n"
                        "- 最近蛋白质摄入不错，继续保持均衡饮食 💪\n"
                        "- 按时吃药的习惯很棒，别忘了今天也要好好休息 🌙"
                    ),
                },
                {
                    "role": "user",
                    "content": f"用户近期数据：\n{context}",
                },
            ],
            max_tokens=100,
            temperature=0.8,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Quick health summary failed: {e}")
        return ""
