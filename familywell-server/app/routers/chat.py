"""
Chat Router — AI 健康助手
─────────────────────────
POST /api/chat           同步模式（fallback）
POST /api/chat/stream    ★ 流式模式（SSE，主力）
GET  /api/chat/sessions  历史对话列表
GET  /api/chat/sessions/{sid}  对话详情
DELETE /api/chat/sessions/{sid} 删除对话
"""
import uuid
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.family import Family, FamilyMember
from app.models.embedding import ChatHistory
from app.utils.deps import get_current_user
from app.services import rag_service

router = APIRouter(prefix="/api/chat", tags=["Chat"])


# ─── Schemas ───

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    session_id: str | None = None
    include_family: bool = False


class ChatResponse(BaseModel):
    answer: str
    charts: list[dict] = []
    sources: list[dict]
    session_id: str


class SessionItem(BaseModel):
    session_id: str
    last_message: str
    message_count: int
    updated_at: str


# ─── Endpoints ───

@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    ★ 流式模式 — SSE 推送。

    前端通过 wx.request + enableChunkedTransfer 接收分块数据。

    SSE 事件顺序:
      data: {"type":"charts","charts":[...]}     ← 图表先到（<100ms）
      data: {"type":"sources","sources":[...]}   ← 引用来源
      data: {"type":"text","content":"根据"}      ← 文字逐块推送
      data: {"type":"text","content":"最近的"}
      ...
      data: {"type":"done","session_id":"abc"}   ← 结束
    """
    session_id = req.session_id or str(uuid.uuid4())[:16]

    family_user_ids = None
    if req.include_family:
        family_user_ids = await _get_family_user_ids(db, user.id)

    async def event_generator():
        async for sse_line in rag_service.chat_stream(
            db=db,
            user_id=user.id,
            session_id=session_id,
            question=req.question,
            family_user_ids=family_user_ids,
        ):
            yield sse_line

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # 让 Nginx 不缓冲 SSE
        },
    )


@router.post("", response_model=ChatResponse)
async def send_message(
    req: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """同步模式 — 等全部生成完一次性返回。作为流式的 fallback。"""
    session_id = req.session_id or str(uuid.uuid4())[:16]

    family_user_ids = None
    if req.include_family:
        family_user_ids = await _get_family_user_ids(db, user.id)

    result = await rag_service.chat(
        db=db,
        user_id=user.id,
        session_id=session_id,
        question=req.question,
        family_user_ids=family_user_ids,
    )

    return ChatResponse(
        answer=result["answer"],
        charts=result.get("charts", []),
        sources=result["sources"],
        session_id=result["session_id"],
    )


@router.get("/sessions")
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取用户的所有对话列表。"""
    result = await db.execute(
        select(
            ChatHistory.session_id,
            func.count(ChatHistory.id).label("count"),
            func.max(ChatHistory.created_at).label("last_at"),
        )
        .where(ChatHistory.user_id == user.id)
        .group_by(ChatHistory.session_id)
        .order_by(func.max(ChatHistory.created_at).desc())
        .limit(20)
    )
    sessions = result.fetchall()

    items = []
    for s in sessions:
        last_msg_result = await db.execute(
            select(ChatHistory.content)
            .where(ChatHistory.user_id == user.id)
            .where(ChatHistory.session_id == s.session_id)
            .where(ChatHistory.role == "user")
            .order_by(ChatHistory.created_at.desc())
            .limit(1)
        )
        last_msg = last_msg_result.scalar_one_or_none() or ""

        items.append({
            "session_id": s.session_id,
            "last_message": last_msg[:50],
            "message_count": s.count,
            "updated_at": str(s.last_at),
        })

    return items


@router.get("/sessions/{session_id}")
async def get_session_messages(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取某个对话的完整消息记录。"""
    result = await db.execute(
        select(ChatHistory)
        .where(ChatHistory.user_id == user.id)
        .where(ChatHistory.session_id == session_id)
        .order_by(ChatHistory.created_at.asc())
    )
    messages = result.scalars().all()

    return [
        {
            "role": m.role,
            "content": m.content,
            "sources": m.sources,
            "created_at": str(m.created_at),
        }
        for m in messages
    ]


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除一个对话。"""
    await db.execute(
        delete(ChatHistory)
        .where(ChatHistory.user_id == user.id)
        .where(ChatHistory.session_id == session_id)
    )
    return {"ok": True}


# ─── Helpers ───

async def _get_family_user_ids(db: AsyncSession, user_id: int) -> list[int]:
    """获取该用户所在家庭的所有成员 ID（仅限管理者）。"""
    result = await db.execute(
        select(FamilyMember)
        .where(FamilyMember.user_id == user_id)
        .where(FamilyMember.role == "admin")
    )
    membership = result.scalar_one_or_none()
    if not membership:
        return [user_id]

    members_result = await db.execute(
        select(FamilyMember.user_id)
        .where(FamilyMember.family_id == membership.family_id)
    )
    return [row[0] for row in members_result.fetchall()]
