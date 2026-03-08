"""
app/models/user.py — 用户模型
──────────────────────────────
[P1-2] User 表新增 openid 字段（可选，用于微信登录）
       password_hash 改为可选（微信登录的用户可能没有密码）
"""
from datetime import datetime, date
from sqlalchemy import (
    BigInteger, SmallInteger, String, Boolean, DateTime, Date, DECIMAL, JSON,
    ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # [P1-2] 微信用户可能无密码

    # ── [P1-2] 微信 openid ──
    openid: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )

    nickname: Mapped[str | None] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(String(500))

    # ★ 用户最近一次请求的时区偏移（JS getTimezoneOffset 值，如 UTC+8 → -480）
    tz_offset: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

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
    blood_type: Mapped[str | None] = mapped_column(String(10))
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
