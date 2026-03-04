"""
app/routers/voice_input.py — 语音/文字录入统一入口
──────────────────────────────────────────────────────
★ 从 medications.py 中抽出的通用录入路由。
  支持 6 种类型拆分：medication / food / vitals / symptom / insurance / memo

端点：
  POST /api/voice/add        文字录入（多类型自动拆分）
  POST /api/voice/add-audio  音频录入（先 ASR 转文字，再走同一套分析逻辑）
"""
import json
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI

from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.models.record import Record
from app.models.health_indicator import HealthIndicator
from app.models.nutrition import NutritionLog
from app.models.insurance import Insurance
from app.models.medication import Medication, MedicationTask, MedicationSuggestion
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/voice", tags=["voice_input"])
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════

class VoiceTextRequest(BaseModel):
    text: str


class VoiceAudioRequest(BaseModel):
    """语音音频分析请求"""
    audio_keys: list[str]


# ══════════════════════════════════════════════════
# Prompt（支持 6 种类型拆分）
# ══════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════
# 共享内部函数：LLM 分析文本 → items 列表
# ══════════════════════════════════════════════════

async def _analyze_text_to_items(text: str) -> list[dict]:
    """
    调用 LLM 将文本拆分为结构化 items 列表。
    voice-add 和 voice-add-audio 共用此逻辑（消除重复）。
    """
    _settings = get_settings()
    client = AsyncOpenAI(
        api_key=_settings.DOUBAO_API_KEY,
        base_url=_settings.DOUBAO_BASE_URL,
    )

    resp = await client.chat.completions.create(
        model=_settings.DOUBAO_MODEL,
        messages=[{"role": "user", "content": VOICE_MULTI_PROMPT + text}],
        max_tokens=2000,
        temperature=0.2,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    parsed = json.loads(raw.strip())

    items = parsed.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="未能识别有效内容")

    return items


async def _dispatch_items(
    db: AsyncSession,
    user: User,
    items: list[dict],
    original_text: str,
) -> dict:
    """
    遍历 items 列表，分发到各 _process_* 函数，统一处理 embedding 和缓存。
    voice-add 和 voice-add-audio 共用此逻辑（消除重复）。
    """
    today = date.today()
    response_items = []
    record_ids_to_embed = []

    for item in items:
        item_type = item.get("type", "memo")
        summary = item.get("summary", original_text[:30])
        data = item.get("data", {})

        try:
            if item_type == "medication":
                result_item, record_id = await _process_medication(
                    db, user, data, summary, today, original_text
                )
            elif item_type == "food":
                result_item, record_id = await _process_food(db, user, data, summary, today)
            elif item_type == "vitals":
                result_item, record_id = await _process_vitals(db, user, data, summary, today)
            elif item_type == "insurance":
                result_item, record_id = await _process_insurance_voice(db, user, data, summary, today)
            elif item_type == "symptom":
                result_item, record_id = await _process_symptom(db, user, data, summary, today, original_text)
            else:
                result_item, record_id = await _process_memo(db, user, data, summary, today, original_text)

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

    # ── 生成 embedding ──
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
            await db.commit()
    else:
        await db.commit()

    # ── 清除热点缓存 ──
    try:
        from app.services.rag_service import invalidate_user_cache
        await invalidate_user_cache(user.id)
    except Exception as e:
        logger.warning(f"Cache clear failed (non-fatal): {e}")

    return {
        "items": response_items,
        "total": len(response_items),
        "type": response_items[0]["type"] if response_items else "unknown",
        "summary": response_items[0]["summary"] if response_items else original_text[:30],
    }


# ══════════════════════════════════════════════════
# 端点
# ══════════════════════════════════════════════════

@router.post("/add")
async def voice_add(
    req: VoiceTextRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    通用语音记录入口。★ 支持多类型拆分：
    一段话中的饮食/用药/指标/症状分别保存到各自的表和 embedding 中。
    ★ 用药新逻辑：已有药物自动打卡，新药物创建 Suggestion 待确认。
    """
    try:
        items = await _analyze_text_to_items(req.text)
    except json.JSONDecodeError as e:
        logger.error(f"Voice add LLM parse failed: {e}")
        raise HTTPException(status_code=500, detail="AI 分析失败，请重试")

    result = await _dispatch_items(db, user, items, req.text)
    return result


@router.post("/add-audio")
async def voice_add_audio(
    req: VoiceAudioRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    ★ 音频录入端点：接收音频文件 → ASR 转文字 → 多类型拆分。
    """
    from app.routers.voice_audio import transcribe_audio_keys

    if not req.audio_keys:
        raise HTTPException(status_code=400, detail="请提供至少一个音频文件")

    try:
        full_text = await transcribe_audio_keys(req.audio_keys)
        if not full_text.strip():
            raise HTTPException(status_code=400, detail="未能识别语音内容，请重试")
        logger.info(f"Audio transcribed for user={user.id}: {full_text[:100]}...")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio transcription failed for user={user.id}: {e}")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {str(e)}")

    try:
        items = await _analyze_text_to_items(full_text)
    except json.JSONDecodeError as e:
        logger.error(f"Voice audio LLM parse failed: {e}")
        raise HTTPException(status_code=500, detail="AI 分析失败，请重试")

    result = await _dispatch_items(db, user, items, full_text)
    result["text"] = full_text
    return result


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
                logger.info(f"Auto-checked medication '{name}' for user {user.id}")
            else:
                auto_checked.append(f"{name}（今日已无待打卡）")
        else:
            # ── ④ 新药物 → 创建 Suggestion ──
            suggestion = MedicationSuggestion(
                user_id=user.id,
                record_id=record.id,
                name=name,
                dosage=m.get("dosage"),
                frequency=m.get("frequency"),
                ai_raw=m,
                source_text=original_text,
                status="pending",
            )
            db.add(suggestion)
            new_suggestions.append(name)

    parts = []
    if auto_checked:
        parts.append(f"已打卡：{'、'.join(auto_checked)}")
    if new_suggestions:
        parts.append(f"发现新药物：{'、'.join(new_suggestions)}，请在首页确认")

    return {
        "auto_checked": auto_checked,
        "new_suggestions": new_suggestions,
        "message": "；".join(parts) if parts else f"已记录：{summary}",
    }, record.id


async def _process_food(
    db: AsyncSession, user: User, data: dict, summary: str, today: date
) -> tuple[dict, int | None]:
    """处理饮食类型 → 创建 Record + NutritionLog"""
    food_items = data.get("food_items", [])
    calories = data.get("calories")

    raw_text = f"饮食记录：{summary}。"
    if food_items:
        raw_text += f"食物：{'、'.join(str(i) for i in food_items)}。"
    if calories:
        raw_text += f"约{calories}千卡。"

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
    premium = data.get("premium")
    provider = data.get("provider")
    policy_type = data.get("policy_type")
    notes = data.get("notes", "")

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


# ══════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════

def _parse_voice_date(d: str | None) -> date | None:
    """解析语音中的日期字符串，支持 YYYY-MM-DD 格式"""
    if not d:
        return None
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None
