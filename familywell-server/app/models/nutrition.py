from datetime import datetime, date
from sqlalchemy import BigInteger, DateTime, Date, DECIMAL, String, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class NutritionLog(Base):
    __tablename__ = "nutrition_log"
    __table_args__ = (Index("idx_user_date", "user_id", "logged_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("record.id")
    )

    meal_type: Mapped[str | None] = mapped_column(
        String(20)
    )
    food_items: Mapped[dict | None] = mapped_column(JSON)

    calories: Mapped[float | None] = mapped_column(DECIMAL(7, 1))
    protein_g: Mapped[float | None] = mapped_column(DECIMAL(6, 1))
    fat_g: Mapped[float | None] = mapped_column(DECIMAL(6, 1))
    carb_g: Mapped[float | None] = mapped_column(DECIMAL(6, 1))
    fiber_g: Mapped[float | None] = mapped_column(DECIMAL(6, 1))
    sodium_mg: Mapped[float | None] = mapped_column(DECIMAL(7, 1))

    logged_at: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    record: Mapped["Record"] = relationship(back_populates="nutrition_logs")
