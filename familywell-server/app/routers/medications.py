from datetime import date, datetime, time as time_type
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

    # ★ 立即生成当天任务，不用等凌晨 cron
    today = date.today()
    if med.start_date <= today and (med.end_date is None or med.end_date >= today):
        await _generate_tasks_for_med(db, med, today)
        await db.flush()

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


# ─── Voice Add ───

from pydantic import BaseModel as _BaseModel

class VoiceMedRequest(_BaseModel):
    text: str  # 语音转文字后的内容


@router.post("/voice-add")
async def voice_add_medication(
    req: VoiceMedRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    通用语音记录入口。AI 自动判断内容类型并分发处理：
    - 用药 → 创建 Medication + 当天 Task
    - 饮食 → 创建 Record(food) + NutritionLog
    - 血压/指标 → 创建 HealthIndicator
    - 症状/其他 → 创建 Record + 文字记录
    """
    from app.config import get_settings
    from app.models.record import Record
    from app.models.health_indicator import HealthIndicator
    from app.models.nutrition import NutritionLog
    from openai import AsyncOpenAI
    import json

    _settings = get_settings()
    client = AsyncOpenAI(
        api_key=_settings.DOUBAO_API_KEY,
        base_url=_settings.DOUBAO_BASE_URL,
    )

    # 1. AI 分类 + 提取
    prompt = """你是一个健康记录助手。分析用户描述，判断类型并提取信息。
返回JSON（只返回JSON，不要多余文字）：

{
  "type": "medication|food|vitals|symptom",
  "summary": "一句话总结",
  "data": { ... }
}

各类型的data格式：

type=medication 时:
  "data": {"medications": [{"name":"药名","dosage":"剂量","med_type":"long_term|course|temporary","course_count":1,"days_per_course":7,"total_days":7,"times_per_day":1}]}

type=food 时:
  "data": {"meal_type":"breakfast|lunch|dinner|snack","food_items":["食物1","食物2"],"calories":估算总卡路里,"protein_g":蛋白质克,"fat_g":脂肪克,"carb_g":碳水克}

type=vitals 时(血压/体重/血糖等):
  "data": {"indicators":[{"type":"bp_systolic|bp_diastolic|heart_rate|weight|glucose_fasting|temperature","value":数值,"unit":"单位"}]}

type=symptom 时:
  "data": {"symptoms":["症状1","症状2"],"severity":"mild|moderate|severe","notes":"补充说明"}

注意：
- "吃了/喝了+食物" → type=food
- "吃了/服了+药名" → type=medication  
- "血压/体重/血糖+数值" → type=vitals
- "头疼/不舒服/拉肚子" → type=symptom
- 如果同时包含多种信息，选最主要的类型

用户描述："""

    try:
        response = await client.chat.completions.create(
            model=_settings.DOUBAO_MODEL,
            messages=[{"role": "user", "content": prompt + req.text}],
            max_tokens=1024,
            temperature=0.1,
        )

        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        result = json.loads(text)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"AI 解析失败: {str(e)}")

    record_type = result.get("type", "symptom")
    summary = result.get("summary", req.text[:50])
    data = result.get("data", {})
    today = date.today()

    # 2. 根据类型分发处理
    response_data = {"type": record_type, "summary": summary}

    if record_type == "medication":
        # ── 用药 ──
        DEFAULT_TIMES = {1: ["08:00"], 2: ["08:00", "20:00"], 3: ["08:00", "12:00", "20:00"]}
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
                    from datetime import timedelta
                    end_date = today + timedelta(days=total)
            elif med_type == "temporary":
                from datetime import timedelta
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

        response_data["medications"] = created_meds
        response_data["message"] = f"已添加 {len(created_meds)} 个药物，今日服药提醒已生成"

    elif record_type == "food":
        # ── 饮食 ──
        food_items = data.get("food_items", [])
        calories = data.get("calories")
        raw_text = f"饮食记录：{summary}。食物：{'、'.join(food_items) if food_items else '未知'}。"
        if calories:
            raw_text += f"估算热量约{calories}千卡。"
        if data.get("protein_g"):
            raw_text += f"蛋白质{data['protein_g']}g，脂肪{data.get('fat_g',0)}g，碳水{data.get('carb_g',0)}g。"

        record = Record(
            user_id=user.id, category="food", title=summary,
            record_date=today, ai_status="completed", source="voice",
            ai_raw_result={
                "category": "food",
                "title": summary,
                "raw_text": raw_text,
                "date": today.isoformat(),
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

        response_data["nutrition"] = {
            "calories": data.get("calories"),
            "food_items": data.get("food_items"),
        }
        response_data["message"] = f"饮食记录已保存：{summary}"

    elif record_type == "vitals":
        # ── 指标 ──
        indicators = data.get("indicators", [])
        raw_text = f"健康指标记录：{summary}。"
        for ind in indicators:
            raw_text += f"{ind.get('type','指标')}: {ind.get('value')}{ind.get('unit','')}。"

        record = Record(
            user_id=user.id, category="bp_reading", title=summary,
            record_date=today, ai_status="completed", source="voice",
            ai_raw_result={
                "category": "bp_reading",
                "title": summary,
                "raw_text": raw_text,
                "date": today.isoformat(),
                **data,
            },
        )
        db.add(record)
        await db.flush()

        indicators = data.get("indicators", [])
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

        response_data["indicators"] = indicators
        response_data["message"] = f"健康指标已记录：{summary}"

    else:
        # ── 症状/其他 ──
        symptoms = data.get("symptoms", [])
        raw_text = f"症状记录：{summary}。{req.text}"

        record = Record(
            user_id=user.id, category="visit", title=summary,
            record_date=today, ai_status="completed", source="voice",
            notes=req.text,
            ai_raw_result={
                "category": "visit",
                "title": summary,
                "raw_text": raw_text,
                "date": today.isoformat(),
                **data,
            },
        )
        db.add(record)

        response_data["message"] = f"已记录：{summary}"

    await db.flush()

    # 3. 为新建的 Record 生成 embedding（让 RAG 能检索到）
    if record_type != "medication":
        try:
            from app.services import embedding_service
            # record 变量在各分支中已赋值
            await db.commit()  # 先提交，embedding_service 会开新 session
            await embedding_service.embed_record(record.id)
        except Exception as emb_err:
            import logging
            logging.getLogger(__name__).warning(f"Embedding failed for voice record: {emb_err}")
    else:
        await db.commit()

    return response_data


# ─── Tasks ───

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
        .order_by(MedicationTask.scheduled_date.desc(), MedicationTask.scheduled_time)
    )
    tasks = result.scalars().all()

    grouped = {}
    for task in tasks:
        key = task.scheduled_date.isoformat()
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({
            "id": task.id,
            "medication_id": task.medication_id,
            "medication_name": task.medication.name if task.medication else None,
            "medication_dosage": task.medication.dosage if task.medication else None,
            "scheduled_date": task.scheduled_date.isoformat(),
            "scheduled_time": task.scheduled_time.strftime("%H:%M"),
            "status": task.status,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        })

    return grouped


@router.put("/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a medication task as completed."""
    result = await db.execute(
        select(MedicationTask).where(
            MedicationTask.id == task_id,
            MedicationTask.user_id == user.id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    today = date.today()
    if task.scheduled_date < today.replace(day=today.day - 1):
        raise HTTPException(status_code=400, detail="只能补打昨天的任务")

    task.status = "done"
    task.completed_at = datetime.utcnow()
    await db.flush()

    med_result = await db.execute(
        select(Medication).where(Medication.id == task.medication_id)
    )
    med = med_result.scalar_one_or_none()
    if med and med.remaining_count is not None and med.remaining_count > 0:
        med.remaining_count -= 1
        await db.flush()

    return {"status": "done", "completed_at": task.completed_at.isoformat()}
