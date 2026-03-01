"""
app/routers/medications.py — 用药管理 + 语音记录
──────────────────────────────────────────────────
★ voice-add 改为多类型拆分：一段话里的饮食/用药/指标/症状各自保存
"""
from datetime import date, datetime, time as time_type, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.medication import Medication, MedicationTask
from app.schemas.medication import (
    MedicationCreate, MedicationUpdate, MedicationResponse, TaskResponse,
)
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/medications", tags=["medications"])


async def _generate_tasks_for_med(db: AsyncSession, med: Medication, target_date: date):
    """Generate tasks for a single medication on a given date."""
    scheduled_times = med.scheduled_times or ["08:00"]
    count = 0
    for t_str in scheduled_times:
        h, m = t_str.split(":")
        scheduled_time = time_type(int(h), int(m))

        existing = await db.execute(
            select(MedicationTask).where(
                MedicationTask.medication_id == med.id,
                MedicationTask.scheduled_date == target_date,
                MedicationTask.scheduled_time == scheduled_time,
            )
        )
        if existing.scalar_one_or_none() is None:
            task = MedicationTask(
                medication_id=med.id,
                user_id=med.user_id,
                scheduled_date=target_date,
                scheduled_time=scheduled_time,
                status="pending",
            )
            db.add(task)
            count += 1
    return count


@router.get("", response_model=list[MedicationResponse])
async def list_medications(
    active_only: bool = Query(True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Medication).where(Medication.user_id == user.id)
    if active_only:
        query = query.where(Medication.is_active == True)
    query = query.order_by(Medication.created_at.desc())

    result = await db.execute(query)
    return [MedicationResponse.model_validate(m) for m in result.scalars().all()]


@router.post("", response_model=MedicationResponse)
async def create_medication(
    req: MedicationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    med = Medication(
        user_id=user.id,
        name=req.name,
        dosage=req.dosage,
        frequency=req.frequency,
        scheduled_times=req.scheduled_times or ["08:00"],
        start_date=req.start_date or date.today(),
        end_date=req.end_date,
        remaining_count=req.remaining_count,
        is_active=True,
    )
    db.add(med)
    await db.flush()

    today = date.today()
    if med.start_date <= today and (med.end_date is None or med.end_date >= today):
        await _generate_tasks_for_med(db, med, today)
        await db.flush()

    # ★ 新增药物，清缓存
    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception:
        pass

    return MedicationResponse.model_validate(med)


@router.put("/{med_id}", response_model=MedicationResponse)
async def update_medication(
    med_id: int,
    req: MedicationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Medication).where(Medication.id == med_id, Medication.user_id == user.id)
    )
    med = result.scalar_one_or_none()
    if not med:
        raise HTTPException(status_code=404, detail="药物不存在")

    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(med, key, value)
    await db.flush()
    return MedicationResponse.model_validate(med)


# ══════════════════════════════════════════════════
# Voice Add — 多类型拆分
# ══════════════════════════════════════════════════

from pydantic import BaseModel as _BaseModel
import json
import logging

logger = logging.getLogger(__name__)


class VoiceMedRequest(_BaseModel):
    text: str


# ★ 新 Prompt：返回 items 数组，支持一段话拆分成多条记录
VOICE_MULTI_PROMPT = """你是一个健康记录助手。分析用户描述，将其中包含的所有健康信息拆分提取。
一段话中可能同时包含多种类型的信息（比如饮食+用药+指标），请全部拆分出来。

返回JSON（只返回JSON，不要多余文字）：

{
  "items": [
    {
      "type": "medication|food|vitals|symptom",
      "summary": "这一条的简短总结",
      "data": { ... }
    }
  ]
}

各类型的 data 格式：

type=medication:
  {"medications": [{"name":"药名","dosage":"剂量","med_type":"long_term|course|temporary","course_count":1,"days_per_course":7,"total_days":7,"times_per_day":1}]}

type=food:
  {"meal_type":"breakfast|lunch|dinner|snack","food_items":["食物1","食物2"],"calories":估算总卡路里,"protein_g":蛋白质克,"fat_g":脂肪克,"carb_g":碳水克}

type=vitals (血压/体重/血糖等):
  {"indicators":[{"type":"bp_systolic|bp_diastolic|heart_rate|weight|glucose_fasting|temperature","value":数值,"unit":"单位"}]}

type=symptom:
  {"symptoms":["症状1","症状2"],"severity":"mild|moderate|severe","notes":"补充说明"}

关键规则：
- "吃了/喝了+食物" → type=food
- "吃了/服了+药名" → type=medication
- "血压/体重/血糖+数值" → type=vitals
- "头疼/不舒服/拉肚子" → type=symptom
- ★ 如果同时包含多种信息，拆分成多个 item，不要合并！
- 例如 "中午吃了米饭和鱼，然后把降压药吃了" → items 里应有 food + medication 两条
- 如果只有一种类型，items 里也只有一个元素
- meal_type 优先以用户描述为准（"早上/中午/晚上/下午茶"等），只有用户完全没提到餐次时才根据当前时间推断：6-10点 breakfast，10-14点 lunch，14-17点 snack，17-21点 dinner，其余 snack
- 日期优先以用户描述为准（"今天中午" → 今天，"昨天晚上" → 昨天），没提到时默认当前日期
- summary 里带上餐次，如"午餐：米饭、红烧鱼"

用户描述："""


@router.post("/voice-add")
async def voice_add_medication(
    req: VoiceMedRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    通用语音记录入口。★ 支持多类型拆分：
    一段话中的饮食/用药/指标/症状分别保存到各自的表和 embedding 中。
    """
    from app.config import get_settings
    from app.models.record import Record
    from app.models.health_indicator import HealthIndicator
    from app.models.nutrition import NutritionLog
    from openai import AsyncOpenAI

    _settings = get_settings()
    client = AsyncOpenAI(
        api_key=_settings.DOUBAO_API_KEY,
        base_url=_settings.DOUBAO_BASE_URL,
    )

    # ── 1. AI 多类型拆分 ──
    # ★ 把当前时间告诉 AI，用于推断 meal_type 和日期
    now = datetime.now()
    time_context = f"\n\n（当前时间：{now.strftime('%Y-%m-%d %H:%M')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}）\n用户描述："

    try:
        response = await client.chat.completions.create(
            model=_settings.DOUBAO_MODEL,
            messages=[{"role": "user", "content": VOICE_MULTI_PROMPT.rstrip('：') + time_context + req.text}],
            max_tokens=1024,
            temperature=0.1,
        )

        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        ai_result = json.loads(text)
    except Exception as e:
        logger.error(f"AI parse failed: {e}")
        raise HTTPException(status_code=422, detail=f"AI 解析失败: {str(e)}")

    items = ai_result.get("items", [])
    if not items:
        # 兜底：如果 AI 没返回 items，当作旧格式处理
        if ai_result.get("type"):
            items = [ai_result]
        else:
            raise HTTPException(status_code=422, detail="AI 未能识别出有效内容")

    today = date.today()
    response_items = []
    record_ids_to_embed = []

    # ── 2. 逐条处理每个 item ──
    for item in items:
        item_type = item.get("type", "symptom")
        summary = item.get("summary", req.text[:30])
        data = item.get("data", {})

        try:
            if item_type == "medication":
                result_item = await _process_medication(db, user, data, summary, today)
            elif item_type == "food":
                result_item, record_id = await _process_food(db, user, data, summary, today)
                if record_id:
                    record_ids_to_embed.append(record_id)
            elif item_type == "vitals":
                result_item, record_id = await _process_vitals(db, user, data, summary, today)
                if record_id:
                    record_ids_to_embed.append(record_id)
            else:
                result_item, record_id = await _process_symptom(db, user, data, summary, today, req.text)
                if record_id:
                    record_ids_to_embed.append(record_id)

            result_item["type"] = item_type
            result_item["summary"] = summary
            response_items.append(result_item)

        except Exception as e:
            logger.error(f"Failed to process {item_type}: {e}")
            response_items.append({
                "type": item_type,
                "summary": summary,
                "error": str(e),
            })

    await db.flush()

    # ── 3. 为新建的 Record 生成 embedding ──
    if record_ids_to_embed:
        try:
            from app.services import embedding_service
            await db.commit()
            for rid in record_ids_to_embed:
                try:
                    await embedding_service.embed_record(rid)
                except Exception as emb_err:
                    logger.warning(f"Embedding failed for record {rid}: {emb_err}")
        except Exception as e:
            logger.warning(f"Embedding import failed: {e}")
    else:
        await db.commit()

    # ── 4. ★ 清除该用户的热点缓存（数据已变化，旧缓存不再准确）──
    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception as e:
        logger.warning(f"Cache clear failed (non-fatal): {e}")

    return {
        "items": response_items,
        "total": len(response_items),
        # 向后兼容旧前端：取第一个 item 的 type/summary
        "type": response_items[0]["type"] if response_items else "unknown",
        "summary": response_items[0]["summary"] if response_items else req.text[:30],
    }


# ══════════════════════════════════════════════════
# 各类型的处理函数（从原来的 if/elif 抽取出来）
# ══════════════════════════════════════════════════

DEFAULT_TIMES = {1: ["08:00"], 2: ["08:00", "20:00"], 3: ["08:00", "12:00", "20:00"]}


async def _process_medication(
    db: AsyncSession, user: User, data: dict, summary: str, today: date
) -> dict:
    """处理用药类型 → 创建 Medication + 当天 Task"""
    created_meds = []

    for m in data.get("medications", []):
        name = m.get("name", "").strip()
        if not name:
            continue

        med_type = m.get("med_type", "long_term")
        times_per_day = int(m.get("times_per_day", 1))
        scheduled_times = DEFAULT_TIMES.get(times_per_day, ["08:00"])

        end_date = None
        if med_type == "course":
            total = int(m.get("course_count", 1)) * int(m.get("days_per_course", 7))
            if total > 0:
                end_date = today + timedelta(days=total)
        elif med_type == "temporary":
            end_date = today + timedelta(days=int(m.get("total_days", 7)))

        med = Medication(
            user_id=user.id, name=name, dosage=m.get("dosage"),
            frequency=f"每天{times_per_day}次", scheduled_times=scheduled_times,
            start_date=today, end_date=end_date, is_active=True,
        )
        db.add(med)
        await db.flush()
        await _generate_tasks_for_med(db, med, today)
        created_meds.append({"name": name, "dosage": m.get("dosage")})

    return {
        "medications": created_meds,
        "message": f"已添加 {len(created_meds)} 个药物，今日服药提醒已生成",
    }


async def _process_food(
    db: AsyncSession, user: User, data: dict, summary: str, today: date
) -> tuple[dict, int | None]:
    """处理饮食类型 → 创建 Record(food) + NutritionLog"""
    from app.models.record import Record
    from app.models.nutrition import NutritionLog

    food_items = data.get("food_items", [])
    calories = data.get("calories")
    raw_text = f"饮食记录：{summary}。食物：{'、'.join(food_items) if food_items else '未知'}。"
    if calories:
        raw_text += f"估算热量约{calories}千卡。"
    if data.get("protein_g"):
        raw_text += f"蛋白质{data['protein_g']}g，脂肪{data.get('fat_g', 0)}g，碳水{data.get('carb_g', 0)}g。"

    record = Record(
        user_id=user.id, category="food", title=summary,
        record_date=today, ai_status="completed", source="voice",
        ai_raw_result={
            "category": "food", "title": summary,
            "raw_text": raw_text, "date": today.isoformat(),
            **data,
        },
    )
    db.add(record)
    await db.flush()

    log = NutritionLog(
        user_id=user.id, record_id=record.id,
        meal_type=data.get("meal_type"),
        food_items=data.get("food_items"),
        calories=data.get("calories"),
        protein_g=data.get("protein_g"),
        fat_g=data.get("fat_g"),
        carb_g=data.get("carb_g"),
        logged_at=today,
    )
    db.add(log)

    return {
        "nutrition": {"calories": calories, "food_items": food_items},
        "message": f"饮食记录已保存：{summary}",
    }, record.id


async def _process_vitals(
    db: AsyncSession, user: User, data: dict, summary: str, today: date
) -> tuple[dict, int | None]:
    """处理指标类型 → 创建 Record + HealthIndicator"""
    from app.models.record import Record
    from app.models.health_indicator import HealthIndicator

    indicators = data.get("indicators", [])
    raw_text = f"健康指标记录：{summary}。"
    for ind in indicators:
        raw_text += f"{ind.get('type', '指标')}: {ind.get('value', '')}{ind.get('unit', '')}。"

    record = Record(
        user_id=user.id, category="bp_reading", title=summary,
        record_date=today, ai_status="completed", source="voice",
        ai_raw_result={
            "category": "bp_reading", "title": summary,
            "raw_text": raw_text, "date": today.isoformat(),
            **data,
        },
    )
    db.add(record)
    await db.flush()

    for ind in indicators:
        hi = HealthIndicator(
            user_id=user.id, record_id=record.id,
            indicator_type=ind.get("type", "unknown"),
            value=float(ind.get("value", 0)),
            unit=ind.get("unit"),
            measured_at=datetime.utcnow(),
            source="voice",
        )
        db.add(hi)

    return {
        "indicators": indicators,
        "message": f"健康指标已记录：{summary}",
    }, record.id


async def _process_symptom(
    db: AsyncSession, user: User, data: dict, summary: str, today: date, original_text: str
) -> tuple[dict, int | None]:
    """处理症状/其他类型 → 创建 Record"""
    from app.models.record import Record

    raw_text = f"症状记录：{summary}。{original_text}"

    record = Record(
        user_id=user.id, category="visit", title=summary,
        record_date=today, ai_status="completed", source="voice",
        notes=original_text,
        ai_raw_result={
            "category": "visit", "title": summary,
            "raw_text": raw_text, "date": today.isoformat(),
            **data,
        },
    )
    db.add(record)
    await db.flush()

    return {
        "message": f"已记录：{summary}",
    }, record.id


# ══════════════════════════════════════════════════
# Tasks
# ══════════════════════════════════════════════════

@router.get("/tasks", response_model=dict)
async def list_tasks(
    start_date: date = Query(default_factory=date.today),
    end_date: date | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get medication tasks grouped by date."""
    if end_date is None:
        end_date = start_date

    result = await db.execute(
        select(MedicationTask)
        .options(selectinload(MedicationTask.medication))
        .where(
            MedicationTask.user_id == user.id,
            MedicationTask.scheduled_date >= start_date,
            MedicationTask.scheduled_date <= end_date,
        )
        .order_by(MedicationTask.scheduled_date, MedicationTask.scheduled_time)
    )
    tasks = result.scalars().all()

    grouped: dict = {}
    for t in tasks:
        d = t.scheduled_date.isoformat()
        if d not in grouped:
            grouped[d] = []
        grouped[d].append({
            "id": t.id,
            "medication_id": t.medication_id,
            "name": f"{t.medication.name} {t.medication.dosage or ''}".strip() if t.medication else "未知",
            "time": t.scheduled_time.strftime("%H:%M"),
            "status": t.status,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        })

    return {"dates": grouped}


@router.put("/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MedicationTask).where(
            MedicationTask.id == task_id,
            MedicationTask.user_id == user.id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    task.status = "completed"
    task.completed_at = datetime.utcnow()
    await db.flush()

    # 如果有剩余量，递减
    med_result = await db.execute(
        select(Medication).where(Medication.id == task.medication_id)
    )
    med = med_result.scalar_one_or_none()
    if med and med.remaining_count is not None and med.remaining_count > 0:
        med.remaining_count -= 1

    await db.commit()

    # ★ 用药状态变化，清缓存
    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception:
        pass

    return {"status": "completed", "completed_at": task.completed_at.isoformat()}

