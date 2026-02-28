from datetime import datetime, date
from sqlalchemy import (
    BigInteger, String, Text, DateTime, Date, JSON,
    ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Record(Base):
    __tablename__ = "record"
    __table_args__ = (
        Index("idx_user_category", "user_id", "category"),
        Index("idx_user_created", "user_id", "created_at"),
        Index("idx_ai_status", "ai_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )

    # Classification
    category: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="other",
    )

    # Basic info
    title: Mapped[str | None] = mapped_column(String(200))
    hospital: Mapped[str | None] = mapped_column(String(200))
    record_date: Mapped[date | None] = mapped_column(Date)

    # File
    file_key: Mapped[str | None] = mapped_column(String(500))
    file_type: Mapped[str] = mapped_column(
        String(10), default="image"
    )
    thumbnail_key: Mapped[str | None] = mapped_column(String(500))

    # AI processing
    ai_status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
    )
    ai_raw_result: Mapped[dict | None] = mapped_column(JSON)
    ai_error: Mapped[str | None] = mapped_column(Text)
    ai_processed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Meta
    source: Mapped[str] = mapped_column(
        String(20), default="camera"
    )
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="records")
    health_indicators: Mapped[list["HealthIndicator"]] = relationship(
        back_populates="record"
    )
    nutrition_logs: Mapped[list["NutritionLog"]] = relationship(
        back_populates="record"
    )
