"""
app/routers/home.py — 首页聚合接口
──────────────────────────────────
★ 修复：nickname 优先使用 profile.real_name
★ 新增：返回 medication_suggestions（待确认药物建议）
★ 优化：AI tip 与 DB 查询并行执行，避免 LLM 调用阻塞首页渲染
★ 修复：所有日期使用用户本地时区，避免跨时区日期偏移
★ 新增：dashboard 仪表盘数据（健康指标/用药依从性/营养摄入/紧急提醒）
"""
import asyncio
from datetime import date, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, or_, case, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, async_session
from app.models.user import User, UserProfile
from app.models.record import Record
from app.models.medication import Medication, MedicationTask, MedicationSuggestion
from app.models.health_indicator import HealthIndicator
from app.models.nutrition import NutritionLog
from app.models.reminder import Reminder
from app.schemas.home import HomeResponse
from app.utils.deps import get_current_user
from app.utils.timezone import get_tz_offset, user_today, utc_to_user_local
from app.services import rag_service
from app.services.cron_service import ensure_user_tasks_for_date

# 指标类型的中文显示名和单位
_INDICATOR_DISPLAY = {
    "bp_systolic": ("收缩压", "mmHg"),
    "bp_diastolic": ("舒张压", "mmHg"),
    "glucose_fasting": ("空腹血糖", "mmol/L"),
    "psa": ("PSA", "ng/mL"),
    "hemoglobin": ("血红蛋白", "g/L"),
    "cholesterol_total": ("总胆固醇", "mmol/L"),
    "uric_acid": ("尿酸", "μmol/L"),
    "creatinine": ("肌酐", "μmol/L"),
    "alt": ("谷丙转氨酶", "U/L"),
    "ast": ("谷草转氨酶", "U/L"),
}

router = APIRouter(prefix="/api/home", tags=["home"])


async def _build_dashboard(
    db: AsyncSession, user_id: int, today: date,
) -> dict | None:
    """构建首页仪表盘数据：健康指标 + 用药依从性 + 营养摄入 + 紧急提醒。"""
    since_7d = today - timedelta(days=7)

    # ── 1. 健康指标：取每类指标的最新值（最多 6 个）──
    # 子查询：每类指标的最新 measured_at
    latest_subq = (
        select(
            HealthIndicator.indicator_type,
            func.max(HealthIndicator.measured_at).label("max_at"),
        )
        .where(HealthIndicator.user_id == user_id)
        .group_by(HealthIndicator.indicator_type)
        .subquery()
    )
    ind_result = await db.execute(
        select(HealthIndicator)
        .join(
            latest_subq,
            (HealthIndicator.indicator_type == latest_subq.c.indicator_type)
            & (HealthIndicator.measured_at == latest_subq.c.max_at),
        )
        .where(HealthIndicator.user_id == user_id)
        .limit(6)
    )
    indicators = ind_result.scalars().all()

    health_indicators = []
    for ind in indicators:
        display = _INDICATOR_DISPLAY.get(
            ind.indicator_type, (ind.indicator_type, ind.unit or "")
        )
        # 判断状态
        status = "normal"
        if ind.is_abnormal:
            status = "danger"
        elif ind.reference_high and float(ind.value) > float(ind.reference_high):
            status = "danger"
        elif ind.reference_low and float(ind.value) < float(ind.reference_low):
            status = "danger"
        elif ind.reference_high and float(ind.value) > float(ind.reference_high) * 0.9:
            status = "warning"

        health_indicators.append({
            "label": display[0],
            "value": str(round(float(ind.value), 1)),
            "unit": display[1],
            "status": status,
        })

    # ── 2. 用药依从性（近 7 天）──
    adh_result = await db.execute(
        select(
            func.count(MedicationTask.id).label("total"),
            func.sum(case((MedicationTask.status.in_(["done", "completed"]), 1), else_=0)).label("done"),
        )
        .where(
            MedicationTask.user_id == user_id,
            MedicationTask.scheduled_date >= since_7d,
            MedicationTask.scheduled_date <= today,
        )
    )
    adh_row = adh_result.one()
    adh_total = adh_row.total or 0
    adh_done = int(adh_row.done or 0)

    med_adherence = None
    if adh_total > 0:
        # 按药物分组
        by_med_result = await db.execute(
            select(
                Medication.name,
                Medication.dosage,
                func.count(MedicationTask.id).label("total"),
                func.sum(case((MedicationTask.status.in_(["done", "completed"]), 1), else_=0)).label("done"),
            )
            .join(MedicationTask, MedicationTask.medication_id == Medication.id)
            .where(
                MedicationTask.user_id == user_id,
                MedicationTask.scheduled_date >= since_7d,
                MedicationTask.scheduled_date <= today,
            )
            .group_by(Medication.id, Medication.name, Medication.dosage)
        )
        medications = []
        for r in by_med_result.all():
            med_total = r.total or 0
            med_done = int(r.done or 0)
            medications.append({
                "name": f"{r.name} {r.dosage or ''}".strip(),
                "done": med_done,
                "total": med_total,
                "pct": round(med_done / med_total * 100) if med_total else 0,
            })

        med_adherence = {
            "rate": round(adh_done / adh_total * 100) if adh_total else 0,
            "done": adh_done,
            "total": adh_total,
            "medications": medications,
        }

    # ── 3. 近 7 天营养摄入 ──
    nutri_result = await db.execute(
        select(
            func.sum(NutritionLog.protein_g).label("protein"),
            func.sum(NutritionLog.fat_g).label("fat"),
            func.sum(NutritionLog.carb_g).label("carb"),
            func.sum(NutritionLog.calories).label("calories"),
            func.count(distinct(NutritionLog.logged_at)).label("days"),
        )
        .where(
            NutritionLog.user_id == user_id,
            NutritionLog.logged_at >= since_7d,
        )
    )
    nutri_row = nutri_result.one()
    protein = float(nutri_row.protein or 0)
    fat = float(nutri_row.fat or 0)
    carb = float(nutri_row.carb or 0)
    total_g = protein + fat + carb

    nutrition_7d = None
    if total_g > 0:
        nutrition_7d = {
            "protein": round(protein),
            "fat": round(fat),
            "carb": round(carb),
            "total_calories": round(float(nutri_row.calories or 0)),
            "days": nutri_row.days or 0,
        }

    # ── 4. 紧急提醒（取前 5 条未解决的）──
    alerts_result = await db.execute(
        select(Reminder)
        .where(Reminder.user_id == user_id, Reminder.is_resolved == False)
        .order_by(Reminder.priority.desc(), Reminder.created_at.desc())
        .limit(5)
    )
    reminders = alerts_result.scalars().all()
    alerts = [{
        "title": r.title,
        "description": r.description or "",
        "type": r.type,
        "priority": r.priority,
    } for r in reminders]

    # 如果全部为空，返回 None（前端不显示仪表盘区域）
    if not health_indicators and not med_adherence and not nutrition_7d and not alerts:
        return None

    return {
        "health_indicators": health_indicators,
        "med_adherence": med_adherence,
        "nutrition_7d": nutrition_7d,
        "alerts": alerts,
    }


@router.get("", response_model=HomeResponse)
async def get_home_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tz_offset: int | None = Depends(get_tz_offset),
):
    """Simplified homepage data for v2 chat-centric design."""
    today = user_today(tz_offset)

    # ── AI tip 使用独立 session，与下方 DB 查询并行执行 ──
    # quick_health_summary 包含 embedding 搜索 + LLM 调用（1-3s），
    # 用独立 session 让它不阻塞其他快速 DB 查询
    async def _fetch_ai_tip() -> str | None:
        async with async_session() as tip_db:
            try:
                return await rag_service.quick_health_summary(tip_db, user.id)
            except Exception:
                return None

    ai_tip_task = asyncio.create_task(_fetch_ai_tip())

    # ── 以下 DB 查询在同一 session 上顺序执行（均为索引查询，总计 ~10-20ms）──

    # 1. Profile summary — 优先使用 real_name
    profile_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )
    profile = profile_result.scalar_one_or_none()

    display_name = user.nickname or "我"
    if profile and profile.real_name:
        display_name = profile.real_name

    profile_data = {
        "nickname": display_name,
        "age": None,
        "tags": [],
    }
    if profile:
        if profile.birthday:
            age = today.year - profile.birthday.year - (
                (today.month, today.day) < (profile.birthday.month, profile.birthday.day)
            )
            profile_data["age"] = age
        profile_data["tags"] = profile.medical_history or []

    # 2. Pending medication tasks (today, status=pending only)
    # ★ 按需生成：若 cron 未执行（服务器重启/休眠），在此处补生成当天任务
    await ensure_user_tasks_for_date(db, user.id, today)

    tasks_result = await db.execute(
        select(MedicationTask)
        .options(selectinload(MedicationTask.medication))
        .where(
            MedicationTask.user_id == user.id,
            MedicationTask.scheduled_date == today,
            MedicationTask.status == "pending",
        )
        .order_by(MedicationTask.scheduled_time)
    )
    tasks = tasks_result.scalars().all()

    pending_tasks = [{
        "id": t.id,
        "name": f"{t.medication.name} {t.medication.dosage or ''}".strip() if t.medication else "未知",
        "time": t.scheduled_time.strftime("%H:%M"),
    } for t in tasks]

    # 3. Recent activity (last 5 records)
    records_result = await db.execute(
        select(Record)
        .where(
            Record.user_id == user.id,
            or_(
                Record.ai_status == "completed",
                Record.ai_status == "processing",
                Record.ai_status == "failed",
            ),
        )
        .order_by(Record.created_at.desc())
        .limit(5)
    )
    records = records_result.scalars().all()

    recent_activity = [{
        "id": r.id,
        "category": r.category,
        "title": r.title or "处理中…",
        # ★ 返回 record_date (ISO) 供前端用本地时间格式化；
        #   同时保留 date 字段作为后备，使用用户时区转换 created_at
        "record_date": r.record_date.isoformat() if r.record_date else None,
        "date": utc_to_user_local(r.created_at, tz_offset).strftime("%m/%d") if r.created_at else "",
        "ai_status": r.ai_status,
    } for r in records]

    # 4. Unresolved alert count
    alert_result = await db.execute(
        select(func.count(Reminder.id))
        .where(Reminder.user_id == user.id, Reminder.is_resolved == False)
    )
    alert_count = alert_result.scalar() or 0

    # 5. 待确认药物建议
    suggestions_result = await db.execute(
        select(MedicationSuggestion)
        .where(
            MedicationSuggestion.user_id == user.id,
            MedicationSuggestion.status == "pending",
        )
        .order_by(MedicationSuggestion.created_at.desc())
        .limit(10)
    )
    suggestions = suggestions_result.scalars().all()

    medication_suggestions = [{
        "id": s.id,
        "name": s.name,
        "dosage": s.dosage,
        "frequency": s.frequency,
        "created_at": utc_to_user_local(s.created_at, tz_offset).strftime("%m/%d") if s.created_at else None,
    } for s in suggestions]

    # 6. Dashboard 仪表盘数据
    dashboard = await _build_dashboard(db, user.id, today)

    # ── 等待 AI tip 完成（此时 DB 查询已全部结束）──
    ai_tip = await ai_tip_task

    return HomeResponse(
        profile=profile_data,
        pending_tasks=pending_tasks,
        ai_tip=ai_tip if ai_tip else None,
        recent_activity=recent_activity,
        alert_count=alert_count,
        medication_suggestions=medication_suggestions,
        dashboard=dashboard,
    )
