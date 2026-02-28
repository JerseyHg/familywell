"""
Chart Service — 从结构化表查数据，生成前端可渲染的图表 JSON
──────────────────────────────────────────────────────────────

支持的图表类型：
- bar_stack:    堆叠柱状图（营养摄入）
- dual_line:    双折线图（血压趋势）
- line:         单折线图（PSA 等指标趋势）
- adherence:    用药依从性（环形进度 + 柱状图 + 药物进度条）
- alerts:       待处理事项列表
- indicators:   关键指标卡片
"""
import logging
from datetime import date, timedelta
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health_indicator import HealthIndicator
from app.models.nutrition import NutritionLog
from app.models.medication import Medication, MedicationTask
from app.models.insurance import Insurance
from app.models.reminder import Reminder
from app.models.record import Record

logger = logging.getLogger(__name__)


# ────────────────────────────────────────
# 意图识别：根据用户问题判断需要什么图表
# ────────────────────────────────────────

CHART_INTENTS = [
    {
        "keywords": ["饮食", "营养", "吃", "食物", "蛋白质", "碳水", "脂肪", "热量", "卡路里"],
        "handler": "nutrition",
    },
    {
        "keywords": ["血压", "收缩压", "舒张压", "高压", "低压"],
        "handler": "blood_pressure",
    },
    {
        "keywords": ["药", "用药", "吃药", "服药", "打卡", "漏服", "依从"],
        "handler": "medication_adherence",
    },
    {
        "keywords": ["psa", "PSA", "前列腺"],
        "handler": "indicator_psa",
    },
    {
        "keywords": ["血糖", "空腹血糖", "糖化"],
        "handler": "indicator_glucose",
    },
    {
        "keywords": ["保险", "保单", "到期", "续保"],
        "handler": "insurance",
    },
    {
        "keywords": ["最近怎么样", "身体状况", "健康状况", "整体", "综合", "总结"],
        "handler": "overview",
    },
    {
        "keywords": ["提醒", "注意", "待办", "待处理"],
        "handler": "alerts",
    },
]


def detect_chart_intent(question: str) -> list[str]:
    """根据用户提问检测需要生成哪些图表。"""
    question_lower = question.lower()
    handlers = []
    for intent in CHART_INTENTS:
        if any(kw in question_lower for kw in intent["keywords"]):
            handlers.append(intent["handler"])
    return handlers


# ────────────────────────────────────────
# 各类图表数据生成器
# ────────────────────────────────────────

async def generate_charts(
    db: AsyncSession, user_id: int, question: str, days: int = 7
) -> list[dict]:
    """根据问题意图生成图表数据。"""
    handlers = detect_chart_intent(question)
    charts = []

    for handler in handlers:
        try:
            if handler == "nutrition":
                chart = await _chart_nutrition(db, user_id, days)
                if chart:
                    charts.append(chart)
            elif handler == "blood_pressure":
                chart = await _chart_bp(db, user_id, days)
                if chart:
                    charts.append(chart)
            elif handler == "medication_adherence":
                chart = await _chart_med_adherence(db, user_id, days)
                if chart:
                    charts.append(chart)
            elif handler == "indicator_psa":
                chart = await _chart_indicator(db, user_id, "psa", "PSA", "ng/mL", 365)
                if chart:
                    charts.append(chart)
            elif handler == "indicator_glucose":
                chart = await _chart_indicator(db, user_id, "glucose_fasting", "空腹血糖", "mmol/L", 180)
                if chart:
                    charts.append(chart)
            elif handler == "insurance":
                chart = await _chart_insurance(db, user_id)
                if chart:
                    charts.append(chart)
            elif handler == "overview":
                # 综合概览：返回多张图表
                for sub in ["blood_pressure", "medication_adherence", "alerts"]:
                    sub_chart = None
                    if sub == "blood_pressure":
                        sub_chart = await _chart_bp(db, user_id, 7)
                    elif sub == "medication_adherence":
                        sub_chart = await _chart_med_adherence(db, user_id, 7)
                    elif sub == "alerts":
                        sub_chart = await _chart_alerts(db, user_id)
                    if sub_chart:
                        charts.append(sub_chart)
            elif handler == "alerts":
                chart = await _chart_alerts(db, user_id)
                if chart:
                    charts.append(chart)
        except Exception as e:
            logger.warning(f"Chart generation failed for {handler}: {e}")

    return charts


async def _chart_nutrition(db: AsyncSession, user_id: int, days: int) -> dict | None:
    """营养摄入堆叠柱状图。"""
    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(NutritionLog)
        .where(NutritionLog.user_id == user_id, NutritionLog.logged_at >= since)
        .order_by(NutritionLog.logged_at)
    )
    logs = result.scalars().all()
    if not logs:
        return None

    # 按天聚合
    daily = {}
    for log in logs:
        d = str(log.logged_at)
        if d not in daily:
            daily[d] = {"protein": 0, "fat": 0, "carb": 0, "calories": 0, "meals": 0}
        daily[d]["protein"] += log.protein_g or 0
        daily[d]["fat"] += log.fat_g or 0
        daily[d]["carb"] += log.carb_g or 0
        daily[d]["calories"] += log.calories or 0
        daily[d]["meals"] += 1

    data = []
    for d, v in sorted(daily.items()):
        data.append({
            "label": d[5:],  # MM-DD
            "蛋白质": round(v["protein"]),
            "脂肪": round(v["fat"]),
            "碳水": round(v["carb"]),
        })

    total_days = len(daily)
    avg_protein = round(sum(v["protein"] for v in daily.values()) / total_days) if total_days else 0
    avg_fat = round(sum(v["fat"] for v in daily.values()) / total_days) if total_days else 0
    avg_carb = round(sum(v["carb"] for v in daily.values()) / total_days) if total_days else 0
    total_meals = sum(v["meals"] for v in daily.values())

    return {
        "type": "bar_stack",
        "title": f"近{days}天营养摄入 (g/天)",
        "icon": "🍽️",
        "data": data,
        "keys": ["蛋白质", "脂肪", "碳水"],
        "colors": ["#2D8B6F", "#F5A623", "#E85D3A99"],
        "summary": {
            "avg_protein": avg_protein,
            "avg_fat": avg_fat,
            "avg_carb": avg_carb,
            "total_meals": total_meals,
            "days": total_days,
        },
    }


async def _chart_bp(db: AsyncSession, user_id: int, days: int) -> dict | None:
    """血压趋势双折线图。"""
    since = date.today() - timedelta(days=days)

    sys_result = await db.execute(
        select(HealthIndicator)
        .where(
            HealthIndicator.user_id == user_id,
            HealthIndicator.indicator_type == "bp_systolic",
            HealthIndicator.measured_at >= since,
        )
        .order_by(HealthIndicator.measured_at)
    )
    sys_data = sys_result.scalars().all()

    dia_result = await db.execute(
        select(HealthIndicator)
        .where(
            HealthIndicator.user_id == user_id,
            HealthIndicator.indicator_type == "bp_diastolic",
            HealthIndicator.measured_at >= since,
        )
        .order_by(HealthIndicator.measured_at)
    )
    dia_data = dia_result.scalars().all()

    if not sys_data:
        return None

    # 按天合并
    bp_map = {}
    for s in sys_data:
        d = str(s.measured_at)[:10]
        bp_map.setdefault(d, {})["收缩压"] = round(s.value)
    for d_item in dia_data:
        d = str(d_item.measured_at)[:10]
        bp_map.setdefault(d, {})["舒张压"] = round(d_item.value)

    data = [{"label": d[5:], **v} for d, v in sorted(bp_map.items())]

    latest_sys = sys_data[-1].value if sys_data else 0
    latest_dia = dia_data[-1].value if dia_data else 0

    return {
        "type": "dual_line",
        "title": f"近{days}天血压趋势 (mmHg)",
        "icon": "❤️",
        "data": data,
        "key1": "收缩压",
        "key2": "舒张压",
        "color1": "#E85D3A",
        "color2": "#3B7DD8",
        "summary": {
            "latest": f"{round(latest_sys)}/{round(latest_dia)}",
            "count": len(data),
        },
    }


async def _chart_med_adherence(db: AsyncSession, user_id: int, days: int) -> dict | None:
    """用药依从性图表。"""
    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(MedicationTask)
        .where(MedicationTask.user_id == user_id, MedicationTask.scheduled_date >= since)
        .order_by(MedicationTask.scheduled_date)
    )
    tasks = result.scalars().all()
    if not tasks:
        return None

    # 总体统计
    total = len(tasks)
    done = sum(1 for t in tasks if t.status == "done")
    missed = sum(1 for t in tasks if t.status == "missed")
    rate = round(done / total * 100) if total else 0

    # 按天统计
    daily = {}
    for t in tasks:
        d = str(t.scheduled_date)
        if d not in daily:
            daily[d] = {"done": 0, "total": 0}
        daily[d]["total"] += 1
        if t.status == "done":
            daily[d]["done"] += 1

    daily_data = []
    for d, v in sorted(daily.items()):
        daily_data.append({
            "label": d[5:],
            "rate": round(v["done"] / v["total"] * 100) if v["total"] else 0,
        })

    # 按药物统计
    meds_result = await db.execute(
        select(Medication)
        .where(Medication.user_id == user_id, Medication.is_active == True)
    )
    meds = meds_result.scalars().all()
    med_stats = []
    for med in meds:
        med_tasks = [t for t in tasks if t.medication_id == med.id]
        med_done = sum(1 for t in med_tasks if t.status == "done")
        med_total = len(med_tasks)
        med_stats.append({
            "name": f"{med.name} {med.dosage or ''}".strip(),
            "done": med_done,
            "total": med_total,
        })

    return {
        "type": "adherence",
        "title": "用药依从性",
        "icon": "💊",
        "data": daily_data,
        "summary": {
            "rate": rate,
            "done": done,
            "total": total,
            "missed": missed,
        },
        "medications": med_stats,
    }


async def _chart_indicator(
    db: AsyncSession, user_id: int,
    indicator_type: str, display_name: str, unit: str,
    days: int,
) -> dict | None:
    """单指标趋势折线图。"""
    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(HealthIndicator)
        .where(
            HealthIndicator.user_id == user_id,
            HealthIndicator.indicator_type == indicator_type,
            HealthIndicator.measured_at >= since,
        )
        .order_by(HealthIndicator.measured_at)
    )
    indicators = result.scalars().all()
    if not indicators:
        return None

    data = [{"label": str(ind.measured_at)[:7], "value": round(ind.value, 2)} for ind in indicators]
    latest = indicators[-1].value
    first = indicators[0].value
    change = round((latest - first) / first * 100) if first else 0

    return {
        "type": "line",
        "title": f"{display_name}趋势",
        "icon": "📈",
        "data": data,
        "unit": unit,
        "summary": {
            "latest": round(latest, 2),
            "change_pct": change,
            "count": len(data),
        },
    }


async def _chart_insurance(db: AsyncSession, user_id: int) -> dict | None:
    """保险概览。"""
    result = await db.execute(
        select(Insurance)
        .where(Insurance.user_id == user_id, Insurance.is_active == True)
    )
    insurances = result.scalars().all()
    if not insurances:
        return None

    items = []
    for ins in insurances:
        days_left = (ins.end_date - date.today()).days if ins.end_date else None
        items.append({
            "provider": ins.provider or "未知",
            "policy_type": ins.policy_type or "保险",
            "end_date": str(ins.end_date) if ins.end_date else None,
            "days_left": days_left,
            "premium": float(ins.premium) if ins.premium else None,
            "urgent": days_left is not None and days_left <= 30,
        })

    return {
        "type": "insurance",
        "title": "保险概览",
        "icon": "🛡️",
        "data": items,
    }


async def _chart_alerts(db: AsyncSession, user_id: int) -> dict | None:
    """待处理事项。"""
    result = await db.execute(
        select(Reminder)
        .where(Reminder.user_id == user_id, Reminder.is_resolved == False)
        .order_by(Reminder.priority.desc(), Reminder.created_at.desc())
        .limit(5)
    )
    reminders = result.scalars().all()
    if not reminders:
        return None

    items = [{
        "title": r.title,
        "description": r.description or "",
        "type": r.type,
        "priority": r.priority,
    } for r in reminders]

    return {
        "type": "alerts",
        "title": "待处理事项",
        "icon": "⚠️",
        "data": items,
    }
