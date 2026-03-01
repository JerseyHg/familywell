"""
app/models/medication.py — 用药管理模型
──────────────────────────────────────────
★ 新增 MedicationSuggestion 表：语音识别到新药物时，先创建待确认建议，
  用户在首页确认后才创建真正的 Medication + Task。
"""
from datetime import datetime, date, time
from sqlalchemy import (
    BigInteger, String, Boolean, Integer, DateTime, Date, Time, JSON, Text,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Medication(Base):
    __tablename__ = "medication"
    __table_args__ = (Index("idx_med_user_active", "user_id", "is_active"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    prescription_record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("record.id")
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dosage: Mapped[str | None] = mapped_column(String(50))
    frequency: Mapped[str | None] = mapped_column(String(50))
    scheduled_times: Mapped[dict | None] = mapped_column(JSON)  # ["08:00", "19:00"]

    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    remaining_count: Mapped[int | None] = mapped_column(Integer)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="medications")
    tasks: Mapped[list["MedicationTask"]] = relationship(back_populates="medication")


class MedicationTask(Base):
    __tablename__ = "medication_task"
    __table_args__ = (
        UniqueConstraint("medication_id", "scheduled_date", "scheduled_time"),
        Index("idx_medtask_user_date", "user_id", "scheduled_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    medication_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("medication.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )

    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_time: Mapped[time] = mapped_column(Time, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    medication_name: Mapped[str | None] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    medication: Mapped["Medication"] = relationship(back_populates="tasks")


class MedicationSuggestion(Base):
    """
    语音识别到的新药物建议。
    用户确认后才创建真正的 Medication + Task，避免产生垃圾数据。
    """
    __tablename__ = "medication_suggestion"
    __table_args__ = (
        Index("idx_medsug_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("record.id")
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dosage: Mapped[str | None] = mapped_column(String(50))
    frequency: Mapped[str | None] = mapped_column(String(50))
    ai_raw: Mapped[dict | None] = mapped_column(JSON)
    source_text: Mapped[str | None] = mapped_column(Text)

    # pending = 待确认, confirmed = 已确认, dismissed = 已忽略
    status: Mapped[str] = mapped_column(String(20), default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    medication_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("medication.id")
    )
