from datetime import datetime, date
from sqlalchemy import (
    BigInteger, String, Text, Boolean, DateTime, Date,
    ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Project(Base):
    """项目表 —— 用户自定义的健康记录文件夹（如化疗周期、年度体检）"""
    __tablename__ = "project"
    __table_args__ = (
        Index("idx_project_user", "user_id"),
        Index("idx_project_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(10), default="📁")

    # 时间范围（用于半自动归档）
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    # 状态：active=进行中, archived=已结束
    status: Mapped[str] = mapped_column(String(20), default="active")

    # 预设模板类型（可选）
    template: Mapped[str | None] = mapped_column(
        String(30)
        # 'chemo_cycle'   — 化疗周期
        # 'annual_checkup'— 年度体检
        # 'pregnancy'     — 孕期
        # 'hospitalization'— 住院
        # 'weight_loss'   — 减重
        # 'rehab'         — 康复
        # 'custom'        — 自定义
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    records: Mapped[list["Record"]] = relationship(back_populates="project")
