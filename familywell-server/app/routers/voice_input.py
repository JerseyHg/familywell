"""
app/routers/voice_input.py — 语音/文字录入路由
══════════════════════════════════════════════════
业务逻辑已全部迁移至 app/services/voice_service.py。
本文件只负责：入参校验、认证依赖、调用 service。

端点：
  POST /api/voice/add        文字录入（多类型自动拆分）
  POST /api/voice/add-audio  音频录入（ASR 转文字后走同一套逻辑）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.utils.deps import get_current_user
from app.services.voice_service import analyze_text_to_items, dispatch_items

router = APIRouter(prefix="/api/voice", tags=["voice_input"])
logger = logging.getLogger(__name__)


# ── Schemas ──

class VoiceTextRequest(BaseModel):
    text: str


class VoiceAudioRequest(BaseModel):
    audio_keys: list[str]


# ── 端点 ──

@router.post("/add")
async def voice_add(
    req: VoiceTextRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    文字录入入口。支持多类型拆分：
    饮食 / 用药 / 指标 / 症状 / 保险 / 备忘 分别保存到各自表。
    用药新逻辑：已有药物自动打卡，新药创建 Suggestion 待确认。
    """
    try:
        items = await analyze_text_to_items(req.text)
    except Exception as e:
        logger.error(f"Voice add parse failed: {e}")
        raise HTTPException(status_code=500, detail="AI 分析失败，请重试")

    return await dispatch_items(db, user, items, req.text)


@router.post("/add-audio")
async def voice_add_audio(
    req: VoiceAudioRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    音频录入入口：接收音频文件 key → ASR 转文字 → 多类型拆分。
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
        items = await analyze_text_to_items(full_text)
    except Exception as e:
        logger.error(f"Voice audio parse failed: {e}")
        raise HTTPException(status_code=500, detail="AI 分析失败，请重试")

    result = await dispatch_items(db, user, items, full_text)
    result["text"] = full_text
    return result
