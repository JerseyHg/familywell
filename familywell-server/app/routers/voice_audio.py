"""
★ 新增文件：app/routers/voice_audio.py
──────────────────────────────────────────
语音音频直接分析路由，替代原有的文字转换流程。

两个新端点：
1. POST /api/medications/voice-add-audio
   - 接收 audio_keys（COS 文件 key 列表）
   - 下载音频 → 转 base64 → 豆包 LLM 分析 → 结构化提取
   - 复用原有的多类型拆分逻辑

2. POST /api/chat/stream-voice
   - 接收 audio_keys
   - 下载音频 → 转 base64 → 先用 LLM 转文字 → RAG 问答流式返回
"""
import base64
import json
import logging
import os
import tempfile
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class VoiceAudioRequest(BaseModel):
    """语音音频分析请求"""
    audio_keys: list[str]  # COS 文件 key 列表


async def download_and_encode_audio(audio_key: str) -> str:
    """从 COS 下载音频文件并转为 base64"""
    from app.services import cos_service

    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cos_service.download_file(audio_key, tmp_path)
        with open(tmp_path, 'rb') as f:
            audio_bytes = f.read()
        return base64.b64encode(audio_bytes).decode('utf-8')
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def transcribe_audio_with_llm(audio_base64: str) -> str:
    """
    使用豆包 LLM 将音频转为文字。
    豆包 Seed 2.0 支持多模态输入（包括音频）。
    """
    from openai import AsyncOpenAI
    from app.config import get_settings

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.DOUBAO_API_KEY,
        base_url=settings.DOUBAO_BASE_URL,
    )

    try:
        response = await client.chat.completions.create(
            model=settings.DOUBAO_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请仔细听以下音频内容，将其完整转录为文字。只输出转录的文字内容，不要添加任何解释、标点修正说明或额外信息。"
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_base64,
                                "format": "mp3"
                            }
                        }
                    ],
                }
            ],
            max_tokens=2000,
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Audio transcription failed: {e}")
        # 如果多模态音频不支持，尝试提示用户
        raise Exception(f"语音转文字失败: {str(e)}")


async def transcribe_audio_keys(audio_keys: list[str]) -> str:
    """下载并转录所有音频片段，拼接为完整文本"""
    texts = []
    for key in audio_keys:
        audio_b64 = await download_and_encode_audio(key)
        text = await transcribe_audio_with_llm(audio_b64)
        if text:
            texts.append(text)

    return '，'.join(texts)
