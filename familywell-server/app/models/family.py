from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Family(Base):
    __tablename__ = "family"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String(100))
    invite_code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    members: Mapped[list["FamilyMember"]] = relationship(back_populates="family")


class FamilyMember(Base):
    __tablename__ = "family_member"
    __table_args__ = (
        UniqueConstraint("family_id", "user_id"),
        Index("idx_fm_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    family_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("family.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    family: Mapped["Family"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="family_memberships")
