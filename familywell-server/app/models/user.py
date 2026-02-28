from datetime import datetime, date
from sqlalchemy import (
    BigInteger, String, Boolean, DateTime, Date, DECIMAL, JSON,
    ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    profile: Mapped["UserProfile"] = relationship(back_populates="user", uselist=False)
    records: Mapped[list["Record"]] = relationship(back_populates="user")
    medications: Mapped[list["Medication"]] = relationship(back_populates="user")
    family_memberships: Mapped[list["FamilyMember"]] = relationship(back_populates="user")


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), unique=True, nullable=False
    )
    real_name: Mapped[str | None] = mapped_column(String(50))
    gender: Mapped[str | None] = mapped_column(String(10))
    birthday: Mapped[date | None] = mapped_column(Date)
    blood_type: Mapped[str | None] = mapped_column(
        String(10)
    )
    height_cm: Mapped[float | None] = mapped_column(DECIMAL(5, 1))
    weight_kg: Mapped[float | None] = mapped_column(DECIMAL(5, 1))
    allergies: Mapped[dict | None] = mapped_column(JSON)
    medical_history: Mapped[dict | None] = mapped_column(JSON)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(50))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20))
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="profile")
