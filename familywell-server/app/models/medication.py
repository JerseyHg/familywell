from datetime import datetime, date, time
from sqlalchemy import (
    BigInteger, String, Boolean, Integer, DateTime, Date, Time, JSON,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Medication(Base):
    __tablename__ = "medication"
    __table_args__ = (Index("idx_user_active", "user_id", "is_active"),)

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
        Index("idx_user_date", "user_id", "scheduled_date"),
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

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    medication: Mapped["Medication"] = relationship(back_populates="tasks")
