from datetime import datetime
from sqlalchemy import (
    BigInteger, String, Text, Boolean, Integer, DateTime, Enum, JSON,
    ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Reminder(Base):
    __tablename__ = "reminder"
    __table_args__ = (
        Index("idx_user_unread", "user_id", "is_read", "remind_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )

    type: Mapped[str] = mapped_column(
        Enum(
            "medication", "insurance_expiry", "checkup_due",
            "visit_upcoming", "med_low_stock", "custom",
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    priority: Mapped[str] = mapped_column(
        Enum("urgent", "normal"), default="normal"
    )

    related_id: Mapped[int | None] = mapped_column(BigInteger)
    related_type: Mapped[str | None] = mapped_column(String(50))

    remind_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReminderSetting(Base):
    __tablename__ = "reminder_setting"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), unique=True, nullable=False
    )

    med_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    med_reminder_times: Mapped[dict | None] = mapped_column(
        JSON, default=lambda: ["08:00", "19:00"]
    )

    insurance_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    insurance_remind_days: Mapped[dict | None] = mapped_column(
        JSON, default=lambda: [30, 7]
    )

    checkup_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    checkup_interval_months: Mapped[int] = mapped_column(Integer, default=12)

    visit_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    visit_remind_days: Mapped[dict | None] = mapped_column(
        JSON, default=lambda: [3, 1]
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
