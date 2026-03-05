"""app/schemas/stats.py — 健康统计相关请求/响应模型"""
from datetime import datetime
from pydantic import BaseModel


class IndicatorDataPoint(BaseModel):
    value: float
    measured_at: datetime


class IndicatorTrendResponse(BaseModel):
    indicator_type: str
    unit: str | None
    latest: IndicatorDataPoint | None
    trend: str | None
    change_pct: float | None
    data: list[IndicatorDataPoint]


class NutritionDayData(BaseModel):
    date: str
    protein_g: float | None
    fat_g: float | None
    carb_g: float | None
    calories: float | None


class NutritionTrendResponse(BaseModel):
    avg: dict
    trend: dict
    data: list[NutritionDayData]


class MedAdherenceDayData(BaseModel):
    date: str
    rate: float


class MedAdherenceResponse(BaseModel):
    overall_rate: float
    total_tasks: int
    completed: int
    missed: int
    daily: list[MedAdherenceDayData]
    by_medication: list[dict]
