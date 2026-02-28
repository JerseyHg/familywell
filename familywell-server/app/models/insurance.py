from datetime import datetime, date
from sqlalchemy import (
    BigInteger, String, Boolean, DateTime, Date, DECIMAL, ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Insurance(Base):
    __tablename__ = "insurance"
    __table_args__ = (
        Index("idx_ins_user_active", "user_id", "is_active"),
        Index("idx_end_date", "end_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("record.id")
    )

    provider: Mapped[str | None] = mapped_column(String(100))
    policy_type: Mapped[str | None] = mapped_column(String(100))
    policy_number: Mapped[str | None] = mapped_column(String(100))

    insured_name: Mapped[str | None] = mapped_column(String(50))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    premium: Mapped[float | None] = mapped_column(DECIMAL(10, 2))
    coverage: Mapped[float | None] = mapped_column(DECIMAL(15, 2))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
