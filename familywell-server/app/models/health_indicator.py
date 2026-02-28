from datetime import datetime
from sqlalchemy import (
    BigInteger, String, Boolean, DateTime, DECIMAL,
    ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class HealthIndicator(Base):
    __tablename__ = "health_indicator"
    __table_args__ = (
        Index("idx_user_type_time", "user_id", "indicator_type", "measured_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("record.id")
    )

    indicator_type: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(DECIMAL(10, 2), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))

    is_abnormal: Mapped[bool] = mapped_column(Boolean, default=False)
    reference_low: Mapped[float | None] = mapped_column(DECIMAL(10, 2))
    reference_high: Mapped[float | None] = mapped_column(DECIMAL(10, 2))

    measured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), default="ai_extract"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    record: Mapped["Record"] = relationship(back_populates="health_indicators")
