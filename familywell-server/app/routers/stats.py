from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.health_indicator import HealthIndicator
from app.models.nutrition import NutritionLog
from app.models.medication import MedicationTask, Medication
from app.schemas.stats import (
    IndicatorTrendResponse, IndicatorDataPoint,
    NutritionTrendResponse, NutritionDayData,
    MedAdherenceResponse, MedAdherenceDayData,
)
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])

PERIOD_DAYS = {"7d": 7, "30d": 30, "3m": 90, "6m": 180, "1y": 365}


def _get_start_date(period: str) -> date:
    days = PERIOD_DAYS.get(period, 30)
    return date.today() - timedelta(days=days)


@router.get("/indicators", response_model=IndicatorTrendResponse)
async def get_indicator_trend(
    type: str = Query(..., description="e.g. psa, bp_systolic, blood_glucose_fasting"),
    period: str = Query("6m"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    start = _get_start_date(period)
    result = await db.execute(
        select(HealthIndicator)
        .where(
            HealthIndicator.user_id == user.id,
            HealthIndicator.indicator_type == type,
            HealthIndicator.measured_at >= start,
        )
        .order_by(HealthIndicator.measured_at.asc())
    )
    indicators = result.scalars().all()

    data = [
        IndicatorDataPoint(value=float(i.value), measured_at=i.measured_at)
        for i in indicators
    ]

    latest = data[-1] if data else None
    first = data[0] if data else None
    change_pct = None
    trend = None
    if latest and first and first.value != 0:
        change_pct = round((latest.value - first.value) / first.value * 100, 1)
        trend = "decreasing" if change_pct < -5 else "increasing" if change_pct > 5 else "stable"

    unit = indicators[0].unit if indicators else None

    return IndicatorTrendResponse(
        indicator_type=type,
        unit=unit,
        latest=latest,
        trend=trend,
        change_pct=change_pct,
        data=data,
    )


@router.get("/nutrition", response_model=NutritionTrendResponse)
async def get_nutrition_trend(
    period: str = Query("7d"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    start = _get_start_date(period)
    result = await db.execute(
        select(
            NutritionLog.logged_at,
            func.sum(NutritionLog.protein_g).label("protein"),
            func.sum(NutritionLog.fat_g).label("fat"),
            func.sum(NutritionLog.carb_g).label("carb"),
            func.sum(NutritionLog.calories).label("cal"),
        )
        .where(NutritionLog.user_id == user.id, NutritionLog.logged_at >= start)
        .group_by(NutritionLog.logged_at)
        .order_by(NutritionLog.logged_at.asc())
    )
    rows = result.all()

    data = [
        NutritionDayData(
            date=r.logged_at.isoformat(),
            protein_g=float(r.protein) if r.protein else None,
            fat_g=float(r.fat) if r.fat else None,
            carb_g=float(r.carb) if r.carb else None,
            calories=float(r.cal) if r.cal else None,
        )
        for r in rows
    ]

    n = len(data) or 1
    avg_p = sum(d.protein_g or 0 for d in data) / n
    avg_f = sum(d.fat_g or 0 for d in data) / n
    avg_c = sum(d.carb_g or 0 for d in data) / n
    avg_cal = sum(d.calories or 0 for d in data) / n

    return NutritionTrendResponse(
        avg={"protein_g": round(avg_p, 1), "fat_g": round(avg_f, 1),
             "carb_g": round(avg_c, 1), "calories": round(avg_cal, 1)},
        trend={},
        data=data,
    )


@router.get("/bp")
async def get_bp_trend(
    period: str = Query("30d"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get blood pressure trend (systolic + diastolic paired)."""
    start = _get_start_date(period)

    sys_result = await db.execute(
        select(HealthIndicator)
        .where(
            HealthIndicator.user_id == user.id,
            HealthIndicator.indicator_type == "bp_systolic",
            HealthIndicator.measured_at >= start,
        )
        .order_by(HealthIndicator.measured_at.asc())
    )
    dia_result = await db.execute(
        select(HealthIndicator)
        .where(
            HealthIndicator.user_id == user.id,
            HealthIndicator.indicator_type == "bp_diastolic",
            HealthIndicator.measured_at >= start,
        )
        .order_by(HealthIndicator.measured_at.asc())
    )

    sys_data = {i.measured_at.date(): float(i.value) for i in sys_result.scalars()}
    dia_data = {i.measured_at.date(): float(i.value) for i in dia_result.scalars()}

    all_dates = sorted(set(sys_data.keys()) | set(dia_data.keys()))
    data = [
        {
            "date": d.isoformat(),
            "systolic": sys_data.get(d),
            "diastolic": dia_data.get(d),
        }
        for d in all_dates
    ]

    return {"data": data, "count": len(data)}


@router.get("/medication-adherence", response_model=MedAdherenceResponse)
async def get_medication_adherence(
    period: str = Query("7d"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    start = _get_start_date(period)

    daily_result = await db.execute(
        select(
            MedicationTask.scheduled_date,
            func.count(MedicationTask.id).label("total"),
            func.sum(case((MedicationTask.status == "done", 1), else_=0)).label("done"),
        )
        .where(
            MedicationTask.user_id == user.id,
            MedicationTask.scheduled_date >= start,
        )
        .group_by(MedicationTask.scheduled_date)
        .order_by(MedicationTask.scheduled_date.asc())
    )
    daily_rows = daily_result.all()

    daily = [
        MedAdherenceDayData(
            date=r.scheduled_date.isoformat(),
            rate=round(r.done / r.total * 100, 1) if r.total > 0 else 0,
        )
        for r in daily_rows
    ]

    total_tasks = sum(r.total for r in daily_rows)
    completed = sum(r.done for r in daily_rows)
    missed = total_tasks - completed
    overall_rate = round(completed / total_tasks * 100, 1) if total_tasks > 0 else 0

    by_med_result = await db.execute(
        select(
            Medication.name,
            func.count(MedicationTask.id).label("total"),
            func.sum(case((MedicationTask.status == "done", 1), else_=0)).label("done"),
        )
        .join(MedicationTask, MedicationTask.medication_id == Medication.id)
        .where(
            MedicationTask.user_id == user.id,
            MedicationTask.scheduled_date >= start,
        )
        .group_by(Medication.id, Medication.name)
    )
    by_medication = [
        {"name": r.name, "total": r.total, "completed": r.done}
        for r in by_med_result.all()
    ]

    return MedAdherenceResponse(
        overall_rate=overall_rate,
        total_tasks=total_tasks,
        completed=completed,
        missed=missed,
        daily=daily,
        by_medication=by_medication,
    )
