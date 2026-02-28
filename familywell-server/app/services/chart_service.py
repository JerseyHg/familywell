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
- insurance:    保险概览

意图识别策略：
- 主力：调用豆包 LLM 理解用户自然语言意图（准确率高）
- 兜底：关键词匹配（LLM 调用失败时降级使用）
"""
import json
import logging
from datetime import date, timedelta

from openai import AsyncOpenAI
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.health_indicator import HealthIndicator
from app.models.nutrition import NutritionLog
from app.models.medication import Medication, MedicationTask
from app.models.insurance import Insurance
from app.models.reminder import Reminder
from app.models.record import Record

logger = logging.getLogger(__name__)
settings = get_settings()

# 复用一个 AsyncOpenAI 客户端
_client = AsyncOpenAI(
    api_key=settings.DOUBAO_API_KEY,
    base_url=settings.DOUBAO_BASE_URL,
)

# 所有合法的图表 handler 名称（用于校验 LLM 返回值）
VALID_HANDLERS = {
    "nutrition",
    "blood_pressure",
    "medication_adherence",
    "indicator_psa",
    "indicator_glucose",
    "insurance",
    "overview",
    "alerts",
}


# ════════════════════════════════════════
# 意图识别 — LLM 模式（主力）
# ════════════════════════════════════════

INTENT_PROMPT = """你是一个意图分类器。根据用户的健康相关提问，判断需要展示哪些图表。

可选的图表类型（只能从以下列表中选）：
- nutrition：饮食、营养摄入、吃了什么、蛋白质/碳水/脂肪/热量
- blood_pressure：血压、高压、低压、收缩压、舒张压
- medication_adherence：用药、吃药、服药、漏服、药物依从性、药吃齐了吗
- indicator_psa：PSA、前列腺相关指标
- indicator_glucose：血糖、空腹血糖、糖化血红蛋白
- insurance：保险、保单、到期、续保
- overview：最近怎么样、身体状况、整体健康、综合总结
- alerts：提醒、待办、待处理事项

规则：
1. 只返回一个 JSON 数组，包含匹配的图表类型字符串
2. 如果用户问题不需要任何图表（比如闲聊、问医学知识、问诊断建议），返回空数组 []
3. 一个问题可以匹配多个图表类型，例如"血压和吃药情况"→ ["blood_pressure", "medication_adherence"]
4. 注意区分"吃药"和"吃饭"——"吃药/药吃齐了吗/服药"是 medication_adherence，"吃了什么/饮食/营养"是 nutrition
5. 只返回 JSON，不要任何多余文字、不要 markdown 代码块

用户提问：{question}"""


async def detect_chart_intent_llm(question: str) -> list[str]:
    """
    用 LLM 判断用户提问需要哪些图表。

    返回: handler 名称列表，如 ["medication_adherence"]
    异常: 任何错误都会抛出，由调用方降级到关键词模式
    """
    response = await _client.chat.completions.create(
        model=settings.DOUBAO_CHAT_MODEL,
        messages=[
            {"role": "user", "content": INTENT_PROMPT.format(question=question)},
        ],
        max_tokens=100,
        temperature=0,
    )

    text = response.choices[0].message.content.strip()

    # 清理可能的 markdown 代码块包裹
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    handlers = json.loads(text)

    # 校验：只保留合法的 handler 名称
    if not isinstance(handlers, list):
        logger.warning(f"LLM intent returned non-list: {handlers}")
        raise ValueError(f"LLM returned non-list: {handlers}")

    valid = [h for h in handlers if h in VALID_HANDLERS]
    if len(valid) != len(handlers):
        logger.warning(
            f"LLM intent had invalid handlers, dropped: "
            f"{[h for h in handlers if h not in VALID_HANDLERS]}"
        )

    logger.info(f"LLM intent for '{question}': {valid}")
    return valid


# ════════════════════════════════════════
# 意图识别 — 关键词模式（兜底）
# ════════════════════════════════════════

CHART_INTENTS_KEYWORDS = [
    {
        "keywords": ["饮食", "营养", "食物", "蛋白质", "碳水", "脂肪",
                     "热量", "卡路里", "吃了什么", "吃了啥", "吃的怎么样",
                     "今天吃", "昨天吃", "最近吃"],
        "handler": "nutrition",
    },
    {
        "keywords": ["血压", "收缩压", "舒张压", "高压", "低压"],
        "handler": "blood_pressure",
    },
    {
        "keywords": ["药", "用药", "吃药", "服药", "打卡", "漏服",
                     "依从", "吃齐", "药吃"],
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


def detect_chart_intent_keyword(question: str) -> list[str]:
    """关键词兜底：当 LLM 调用失败时使用。"""
    question_lower = question.lower()
    handlers = []
    for intent in CHART_INTENTS_KEYWORDS:
        if any(kw in question_lower for kw in intent["keywords"]):
            handlers.append(intent["handler"])

    # 互斥规则：同时命中 nutrition + medication 时，含"药"优先 medication
    if "medication_adherence" in handlers and "nutrition" in handlers:
        med_keywords = ["药", "用药", "吃药", "服药", "漏服", "依从", "吃齐", "药吃"]
        if any(kw in question_lower for kw in med_keywords):
            handlers.remove("nutrition")

    logger.info(f"Keyword fallback intent for '{question}': {handlers}")
    return handlers


# ════════════════════════════════════════
# 统一入口：先 LLM，失败则降级关键词
# ════════════════════════════════════════

async def detect_chart_intent(question: str) -> list[str]:
    """
    检测用户提问需要哪些图表。

    策略：LLM 优先 → 关键词兜底。
    LLM 调用约增加 200-500ms，但图表在 SSE 文字之前推送，
    用户几乎感知不到这段额外延迟。
    """
    try:
        return await detect_chart_intent_llm(question)
    except Exception:
        logger.info("Falling back to keyword intent detection")
        return detect_chart_intent_keyword(question)


# ════════════════════════════════════════
# 图表数据生成
# ════════════════════════════════════════

async def generate_charts(
    db: AsyncSession, user_id: int, question: str, days: int = 7
) -> list[dict]:
    """根据问题意图生成图表数据。"""
    handlers = await detect_chart_intent(question)
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


# ════════════════════════════════════════
# 各类图表数据生成器
# ════════════════════════════════════════

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
            "label": d[5:],
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

    total = len(tasks)
    done = sum(1 for t in tasks if t.status == "done")
    missed = sum(1 for t in tasks if t.status == "missed")
    rate = round(done / total * 100) if total else 0

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
