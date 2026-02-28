"""
Search Router — 语义搜索
────────────────────────
GET /api/search?q=...   语义搜索健康记录
"""
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.utils.deps import get_current_user
from app.services import embedding_service

router = APIRouter(prefix="/api/search", tags=["Search"])


class SearchResult(BaseModel):
    record_id: int | None
    content_type: str
    content_text: str
    category: str | None
    source_date: str | None
    score: float


@router.get("", response_model=list[SearchResult])
async def semantic_search(
    q: str = Query(..., min_length=1, max_length=200, description="搜索词"),
    top_k: int = Query(10, ge=1, le=30),
    content_type: str | None = Query(None, description="过滤类型: record_summary|indicator|medication|insurance"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    语义搜索健康记录。

    比传统关键词搜索更智能：
    - "肝功能" 能匹配到标题为"血液生化检验报告"的记录
    - "吃的什么药" 能匹配到处方和用药记录
    - "血压高不高" 能匹配到血压测量数据
    """
    content_types = [content_type] if content_type else None

    results = await embedding_service.search_similar(
        db=db,
        user_id=user.id,
        query=q,
        top_k=top_k,
        content_types=content_types,
    )

    return [
        SearchResult(
            record_id=r["record_id"],
            content_type=r["content_type"],
            content_text=r["content_text"],
            category=r.get("category"),
            source_date=r.get("source_date"),
            score=r["score"],
        )
        for r in results
    ]
