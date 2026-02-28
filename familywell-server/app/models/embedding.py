from datetime import datetime
from sqlalchemy import (
    BigInteger, String, Text, DateTime, Integer,
    ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from app.database import Base
from app.config import get_settings

settings = get_settings()


class RecordEmbedding(Base):
    """向量存储表 —— 每条记录生成 1~N 条 embedding"""
    __tablename__ = "record_embedding"
    __table_args__ = (
        Index("idx_emb_user", "user_id"),
        Index("idx_emb_record", "record_id"),
        Index("idx_emb_type", "content_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("record.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )

    # 来源类型
    content_type: Mapped[str] = mapped_column(
        String(30), nullable=False
        # 'record_summary'  — AI识别结果摘要
        # 'indicator'       — 单条指标
        # 'medication'      — 用药信息
        # 'insurance'       — 保险信息
        # 'profile'         — 个人档案
        # 'manual'          — 手动补充的笔记
    )

    # 被 embedding 的原文（检索命中后直接返回做 context）
    content_text: Mapped[str] = mapped_column(Text, nullable=False)

    # 向量
    embedding = mapped_column(Vector(settings.EMBEDDING_DIMENSION), nullable=False)

    # 元数据
    category: Mapped[str | None] = mapped_column(String(30))
    source_date: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatHistory(Base):
    """对话历史表 —— 支持多轮对话"""
    __tablename__ = "chat_history"
    __table_args__ = (
        Index("idx_chat_user_session", "user_id", "session_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[str | None] = mapped_column(Text)  # JSON: 引用的 record_id 列表
    token_count: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
