"""
app/routers/medications.py — 用药管理 + 语音记录
──────────────────────────────────────────────────
★ voice-add 改为：
  1. 一定创建 Record (category=medication_log) → 归档可见
  2. 已有药物 → 自动打卡
  3. 新药物 → 创建 MedicationSuggestion（待确认），不直接创建 Medication
★ 新增 confirm-suggestion / dismiss-suggestion 端点
"""
from datetime import date, datetime, time as time_type, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.medication import Medication, MedicationTask, MedicationSuggestion
from app.schemas.medication import (
    MedicationCreate, MedicationUpdate, MedicationResponse, TaskResponse,
    SuggestionConfirmRequest,
)
from app.utils.deps import get_current_user

from app.routers.voice_audio import transcribe_audio_keys

router = APIRouter(prefix="/api/medications", tags=["medications"])


async def _generate_tasks_for_med(db: AsyncSession, med: Medication, target_date: date):
    """Generate tasks for a single medication on a given date.
    ★ Respects interval_days: if interval > 1, only generate on matching days.
    """
    # ★ 检查是否是该药物的服药日
    interval = med.interval_days or 1
    if interval > 1 and med.start_date:
        days_since_start = (target_date - med.start_date).days
        if days_since_start < 0 or days_since_start % interval != 0:
            return 0  # 今天不是服药日

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
                medication_name=med.name,
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
# Medication Suggestions — 确认 / 忽略
# ══════════════════════════════════════════════════

DEFAULT_TIMES = {1: ["08:00"], 2: ["08:00", "20:00"], 3: ["08:00", "12:00", "20:00"]}


@router.get("/suggestions")
async def list_suggestions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取所有待确认的药物建议。"""
    result = await db.execute(
        select(MedicationSuggestion)
        .where(
            MedicationSuggestion.user_id == user.id,
            MedicationSuggestion.status == "pending",
        )
        .order_by(MedicationSuggestion.created_at.desc())
    )
    suggestions = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "dosage": s.dosage,
            "frequency": s.frequency,
            "source_text": s.source_text,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in suggestions
    ]


@router.post("/suggestions/{suggestion_id}/confirm")
async def confirm_suggestion(
    suggestion_id: int,
    req: SuggestionConfirmRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    用户确认药物建议 → 创建 Medication + 当天 Task。
    """
    result = await db.execute(
        select(MedicationSuggestion).where(
            MedicationSuggestion.id == suggestion_id,
            MedicationSuggestion.user_id == user.id,
            MedicationSuggestion.status == "pending",
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="建议不存在或已处理")

    times_per_day = req.times_per_day or 1
    interval = req.interval_days or 1
    scheduled_times = DEFAULT_TIMES.get(times_per_day, ["08:00"])

    # ★ 生成频率描述
    if interval == 1:
        freq_text = f"每天{times_per_day}次"
    elif interval == 2:
        freq_text = f"隔天{times_per_day}次"
    else:
        freq_text = f"每{interval}天{times_per_day}次"

    today = date.today()
    end_date = None
    if req.med_type == "course" and req.total_days:
        end_date = today + timedelta(days=req.total_days)
    elif req.med_type == "temporary":
        end_date = today + timedelta(days=req.total_days or 7)

    med = Medication(
        user_id=user.id,
        name=suggestion.name,
        dosage=req.dosage or suggestion.dosage,
        frequency=freq_text,
        scheduled_times=scheduled_times,
        start_date=today,
        end_date=end_date,
        interval_days=interval,
        is_active=True,
    )
    db.add(med)
    await db.flush()

    await _generate_tasks_for_med(db, med, today)

    suggestion.status = "confirmed"
    suggestion.confirmed_at = datetime.utcnow()
    suggestion.medication_id = med.id

    await db.commit()

    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception:
        pass

    return {
        "message": f"已添加药物「{suggestion.name}」，今日服药提醒已生成",
        "medication_id": med.id,
    }


@router.post("/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(
    suggestion_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户忽略药物建议。"""
    result = await db.execute(
        select(MedicationSuggestion).where(
            MedicationSuggestion.id == suggestion_id,
            MedicationSuggestion.user_id == user.id,
            MedicationSuggestion.status == "pending",
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=404, detail="建议不存在或已处理")

    suggestion.status = "dismissed"
    await db.commit()

    return {"message": f"已忽略「{suggestion.name}」"}


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
      "type": "medication|food|vitals|symptom|insurance|memo",
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

type=insurance (保险缴费/续保/投保等):
  {"provider":"保险公司（如有）","policy_type":"险种（如有）","premium":费用金额,"start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","notes":"其他信息"}

type=memo (通用备忘/提醒/日常记录):
  {"content":"完整记录内容","notes":"补充说明"}

关键规则：
- "吃了/喝了+食物" → type=food
- "吃了/服了+药名" → type=medication
- "血压/体重/血糖+数值" → type=vitals
- "头疼/不舒服/拉肚子" → type=symptom
- "保险/保费/续保/投保/缴费" → type=insurance
- 不属于以上任何类型的健康相关记录 → type=memo
- ★ 如果同时包含多种信息，拆分成多个 item，不要合并！
- 例如 "中午吃了米饭和鱼，然后把降压药吃了" → items 里应有 food + medication 两条
- 如果只有一种类型，items 里也只有一个元素
- meal_type 优先以用户描述为准（"早上/中午/晚上/下午茶"等），只有用户完全没提到餐次时才根据当前时间推断：6-10点 breakfast，10-14点 lunch，14-17点 snack，17-21点 dinner，其余 snack
- 日期优先以用户描述为准（"今天中午" → 今天，"昨天晚上" → 昨天），没提到时默认当前日期
- summary 里带上餐次，如"午餐：米饭、红烧鱼"
- 金额数字用阿拉伯数字表示，如"一万块" → 10000

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
    ★ 用药新逻辑：已有药物自动打卡，新药物创建 Suggestion 待确认。
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
        if ai_result.get("type"):
            items = [ai_result]
        else:
            raise HTTPException(status_code=422, detail="AI 未能识别出有效内容")

    today = date.today()
    response_items = []
    record_ids_to_embed = []

    # ── 2. 逐条处理每个 item ──
    for item in items:
        item_type = item.get("type", "memo")
        summary = item.get("summary", req.text[:30])
        data = item.get("data", {})

        try:
            if item_type == "medication":
                result_item, record_id = await _process_medication(
                    db, user, data, summary, today, req.text
                )
                if record_id:
                    record_ids_to_embed.append(record_id)
            elif item_type == "food":
                result_item, record_id = await _process_food(db, user, data, summary, today)
                if record_id:
                    record_ids_to_embed.append(record_id)
            elif item_type == "vitals":
                result_item, record_id = await _process_vitals(db, user, data, summary, today)
                if record_id:
                    record_ids_to_embed.append(record_id)
            elif item_type == "insurance":
                result_item, record_id = await _process_insurance_voice(db, user, data, summary, today)
                if record_id:
                    record_ids_to_embed.append(record_id)
            elif item_type == "symptom":
                result_item, record_id = await _process_symptom(db, user, data, summary, today, req.text)
                if record_id:
                    record_ids_to_embed.append(record_id)
            else:
                # memo / 其他未知类型 → 通用记录
                result_item, record_id = await _process_memo(db, user, data, summary, today, req.text)
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
        "type": response_items[0]["type"] if response_items else "unknown",
        "summary": response_items[0]["summary"] if response_items else req.text[:30],
    }


# ══════════════════════════════════════════════════
# 各类型的处理函数
# ══════════════════════════════════════════════════


async def _process_medication(
    db: AsyncSession, user: User, data: dict, summary: str, today: date,
    original_text: str = "",
) -> tuple[dict, int | None]:
    """
    ★ 新逻辑：
    1. 一定创建 Record(category=medication_log) → 归档可见
    2. 已有药物 → 自动打卡（找 pending task → done）
    3. 新药物 → 创建 MedicationSuggestion（待用户确认）
    """
    from app.models.record import Record

    medications = data.get("medications", [])
    med_names = [m.get("name", "").strip() for m in medications if m.get("name", "").strip()]

    # ── ① 创建 Record（归档可见） ──
    raw_text = f"服药记录：{summary}。药物：{'、'.join(med_names) if med_names else '未知'}。"
    record = Record(
        user_id=user.id,
        category="medication_log",
        title=summary,
        record_date=today,
        ai_status="completed",
        source="voice",
        ai_raw_result={
            "category": "medication_log",
            "title": summary,
            "raw_text": raw_text,
            "date": today.isoformat(),
            **data,
        },
    )
    db.add(record)
    await db.flush()

    auto_checked = []
    new_suggestions = []

    for m in medications:
        name = m.get("name", "").strip()
        if not name:
            continue

        # ── ② 查找已有药物（模糊匹配名称） ──
        existing_result = await db.execute(
            select(Medication).where(
                Medication.user_id == user.id,
                Medication.is_active == True,
                func.lower(Medication.name) == func.lower(name),
            )
        )
        existing_med = existing_result.scalar_one_or_none()

        if existing_med:
            # ── ③ 已有药物 → 自动打卡 ──
            task_result = await db.execute(
                select(MedicationTask).where(
                    MedicationTask.medication_id == existing_med.id,
                    MedicationTask.scheduled_date == today,
                    MedicationTask.status == "pending",
                )
                .order_by(MedicationTask.scheduled_time)
                .limit(1)
            )
            task = task_result.scalar_one_or_none()

            if task:
                task.status = "done"
                task.completed_at = datetime.utcnow()
                auto_checked.append(name)
                logger.info(f"Auto-checked task {task.id} for med '{name}'")
            else:
                # 没有 pending task（可能已经打过卡了），只记录
                auto_checked.append(f"{name}（今日已完成）")
        else:
            # ── ④ 新药物 → 创建 Suggestion ──
            suggestion = MedicationSuggestion(
                user_id=user.id,
                record_id=record.id,
                name=name,
                dosage=m.get("dosage"),
                frequency=f"每天{m.get('times_per_day', 1)}次" if m.get("times_per_day") else None,
                ai_raw=m,
                source_text=original_text[:200] if original_text else None,
                status="pending",
            )
            db.add(suggestion)
            new_suggestions.append(name)
            logger.info(f"Created suggestion for new med '{name}'")

    # 构建返回消息
    parts = []
    if auto_checked:
        parts.append(f"已自动打卡：{'、'.join(auto_checked)}")
    if new_suggestions:
        parts.append(f"发现新药物：{'、'.join(new_suggestions)}，请在首页确认")
    message = "；".join(parts) if parts else f"已记录：{summary}"

    return {
        "auto_checked": auto_checked,
        "new_suggestions": new_suggestions,
        "message": message,
    }, record.id


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
            user_id=user.id,
            record_id=record.id,
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


async def _process_insurance_voice(
    db: AsyncSession, user: User, data: dict, summary: str, today: date
) -> tuple[dict, int | None]:
    """处理保险类型 → 创建 Record(insurance) + Insurance 记录"""
    from app.models.record import Record
    from app.models.insurance import Insurance

    premium = data.get("premium")
    provider = data.get("provider")
    policy_type = data.get("policy_type")
    notes = data.get("notes", "")

    # 解析日期
    start_date = _parse_voice_date(data.get("start_date"))
    end_date = _parse_voice_date(data.get("end_date"))

    raw_text = f"保险记录：{summary}。"
    if premium:
        raw_text += f"费用{premium}元。"
    if provider:
        raw_text += f"保险公司：{provider}。"
    if notes:
        raw_text += notes

    record = Record(
        user_id=user.id, category="insurance", title=summary,
        record_date=today, ai_status="completed", source="voice",
        notes=notes,
        ai_raw_result={
            "category": "insurance", "title": summary,
            "raw_text": raw_text, "date": today.isoformat(),
            "provider": provider, "policy_type": policy_type,
            "premium": premium,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
    )
    db.add(record)
    await db.flush()

    # 写入 Insurance 表
    ins = Insurance(
        user_id=user.id,
        record_id=record.id,
        provider=provider,
        policy_type=policy_type,
        premium=float(premium) if premium else None,
        start_date=start_date,
        end_date=end_date,
        is_active=True,
    )
    db.add(ins)
    await db.flush()

    price_str = f"，费用 {premium} 元" if premium else ""
    return {
        "message": f"🛡️ 已记录保险：{summary}{price_str}",
    }, record.id


async def _process_memo(
    db: AsyncSession, user: User, data: dict, summary: str, today: date, original_text: str
) -> tuple[dict, int | None]:
    """处理通用备忘/记事 → 创建 Record(other)"""
    from app.models.record import Record

    content = data.get("content", original_text)

    record = Record(
        user_id=user.id, category="other", title=summary,
        record_date=today, ai_status="completed", source="voice",
        notes=content,
        ai_raw_result={
            "category": "other", "title": summary,
            "raw_text": content, "date": today.isoformat(),
        },
    )
    db.add(record)
    await db.flush()

    return {
        "message": f"📝 已记录：{summary}",
    }, record.id


def _parse_voice_date(d: str | None) -> date | None:
    """解析语音中的日期字符串，支持 YYYY-MM-DD 格式"""
    if not d:
        return None
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


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


class VoiceAudioRequest(_BaseModel):
    """语音音频分析请求"""
    audio_keys: list[str]


@router.post("/voice-add-audio")
async def voice_add_audio(
    req: VoiceAudioRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    ★ 新端点：直接接收音频文件进行分析。
    流程：
    1. 从 COS 下载音频 → base64
    2. 调用 LLM 转录为文字（利用多模态能力）
    3. 复用原有的 voice-add 多类型拆分逻辑
    """
    from app.routers.voice_audio import transcribe_audio_keys

    if not req.audio_keys:
        raise HTTPException(status_code=400, detail="请提供至少一个音频文件")

    try:
        # ★ Step 1: 下载音频并用 LLM 转文字
        full_text = await transcribe_audio_keys(req.audio_keys)

        if not full_text.strip():
            raise HTTPException(status_code=400, detail="未能识别语音内容，请重试")

        logger.info(f"Audio transcribed for user={user.id}: {full_text[:100]}...")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio transcription failed for user={user.id}: {e}")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {str(e)}")

    # ★ Step 2: 复用原有的文字分析逻辑
    # 以下代码与 voice_add_medication 中的逻辑完全一致

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

    # 调用 LLM 分析文本
    try:
        resp = await client.chat.completions.create(
            model=_settings.DOUBAO_MODEL,
            messages=[{"role": "user", "content": VOICE_MULTI_PROMPT + full_text}],
            max_tokens=2000,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        parsed = json.loads(raw.strip())
    except Exception as e:
        logger.error(f"Voice audio LLM parse failed: {e}")
        raise HTTPException(status_code=500, detail="AI 分析失败，请重试")

    items = parsed.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="未能识别有效内容")

    # ★ Step 3: 创建 Record（归档可见）
    from datetime import date as date_type
    today = date_type.today()

    record = Record(
        user_id=user.id,
        file_key=req.audio_keys[0] if req.audio_keys else None,
        file_type="audio",
        source="voice",
        category="medication_log",
        ai_status="completed",
        ai_raw_result={
            "category": "medication_log",
            "title": f"语音记录 - {today.strftime('%m/%d')}",
            "raw_text": full_text,
            "items": items,
        },
        record_date=today,
        title=f"语音记录 - {today.strftime('%m/%d')}",
    )
    db.add(record)
    await db.flush()

    # ★ Step 4: 按类型分发处理（复用原有逻辑）
    result_items = []

    for item in items:
        item_type = item.get("type", "memo")
        data = item.get("data", {})
        summary = item.get("summary", "")

        try:
            if item_type == "medication":
                meds = data.get("medications", [])
                for med_info in meds:
                    med_name = med_info.get("name", "").strip()
                    if not med_name:
                        continue

                    # 查找已有药物
                    existing = await db.execute(
                        select(Medication).where(
                            Medication.user_id == user.id,
                            Medication.name == med_name,
                            Medication.is_active == True,
                        )
                    )
                    existing_med = existing.scalar_one_or_none()

                    if existing_med:
                        # 自动打卡
                        from datetime import datetime, time as time_type
                        now = datetime.now()
                        task_result = await db.execute(
                            select(MedicationTask).where(
                                MedicationTask.medication_id == existing_med.id,
                                MedicationTask.scheduled_date == today,
                                MedicationTask.completed == False,
                            )
                        )
                        uncompleted = task_result.scalars().first()
                        if uncompleted:
                            uncompleted.completed = True
                            uncompleted.completed_at = now
                            summary = f"✅ {med_name} 已打卡"
                        else:
                            summary = f"💊 {med_name} 今日已全部服用"
                    else:
                        # 创建药物建议
                        sug = MedicationSuggestion(
                            user_id=user.id,
                            record_id=record.id,
                            name=med_name,
                            dosage=med_info.get("dosage"),
                            med_type=med_info.get("med_type", "long_term"),
                            times_per_day=med_info.get("times_per_day", 1),
                            total_days=med_info.get("total_days"),
                            reason=f"语音记录提取",
                        )
                        db.add(sug)
                        summary = f"💊 新药物「{med_name}」待确认"

                result_items.append({"type": "medication", "summary": summary})

            elif item_type == "food":
                from app.models.nutrition import NutritionLog
                log = NutritionLog(
                    user_id=user.id,
                    record_id=record.id,
                    meal_type=data.get("meal_type", "snack"),
                    food_items=data.get("food_items", []),
                    calories=data.get("calories"),
                    protein_g=data.get("protein_g"),
                    fat_g=data.get("fat_g"),
                    carb_g=data.get("carb_g"),
                    log_date=today,
                )
                db.add(log)
                result_items.append({"type": "food", "summary": summary or "饮食已记录"})

            elif item_type == "vitals":
                indicators = data.get("indicators", [])
                for ind in indicators:
                    hi = HealthIndicator(
                        user_id=user.id,
                        record_id=record.id,
                        indicator_type=ind.get("type", "other"),
                        value=ind.get("value"),
                        unit=ind.get("unit", ""),
                        logged_at=today,
                    )
                    db.add(hi)
                result_items.append({"type": "vitals", "summary": summary or "指标已记录"})

            else:
                result_items.append({"type": item_type, "summary": summary or "已记录"})

        except Exception as e:
            logger.error(f"Voice audio item processing error: {e}")
            result_items.append({"type": item_type, "summary": f"处理失败: {str(e)}"})

    await db.commit()

    # 清除缓存
    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception:
        pass

    # 生成 Embedding
    try:
        from app.services.embedding_service import generate_record_embeddings
        from app.database import async_session_factory
        async with async_session_factory() as embed_db:
            await generate_record_embeddings(embed_db, record.id)
    except Exception as e:
        logger.warning(f"Embedding generation failed: {e}")

    return {"items": result_items, "text": full_text}