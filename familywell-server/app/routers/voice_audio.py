"""
app/routers/voice_audio.py — 语音音频处理
═══════════════════════════════════════════
★ 流程：前端录音MP3 → 上传COS → 后端下载 → 火山引擎ASR转文字 → 豆包LLM分析
★ 使用火山引擎「大模型录音文件识别标准版」API（异步任务模式）
"""
import asyncio
import json
import logging
import uuid

import httpx

from app.config import get_settings
from app.services.cos_service import generate_presigned_url

logger = logging.getLogger(__name__)
settings = get_settings()

# 火山引擎 ASR 接口
ASR_SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
ASR_QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"

# 复用 httpx 连接池
_http_client = httpx.AsyncClient(timeout=60.0)


# ════════════════════════════════════════
# 1. 火山引擎 ASR 转写
# ════════════════════════════════════════

async def transcribe_audio_with_asr(audio_cos_key: str) -> str:
    """
    使用火山引擎大模型录音文件识别 API 将音频转为文字。

    流程：
    1. 生成 COS 预签名 URL（让火山引擎能下载音频）
    2. 提交 ASR 任务
    3. 轮询查询结果
    4. 返回识别文字
    """
    # 1. 生成预签名 URL（有效期 30 分钟）
    audio_url = generate_presigned_url(audio_cos_key, expires=1800)
    logger.info(f"[ASR] Audio URL generated for key: {audio_cos_key}")

    # 2. 提交任务
    task_id = str(uuid.uuid4())

    headers = {
        "X-Api-App-Key": settings.VOLC_ASR_APPID,
        "X-Api-Access-Key": settings.VOLC_ASR_TOKEN,
        "X-Api-Resource-Id": "volc.bigasr.auc",
        "X-Api-Request-Id": task_id,
        "X-Api-Sequence": "-1",
    }

    request_body = {
        "user": {
            "uid": "familywell_asr"
        },
        "audio": {
            "url": audio_url,
            "format": "mp3",
            "codec": "raw",
        },
        "request": {
            "model_name": "bigmodel",
            "model_version": "400",
            "enable_itn": True,       # 逆文本归一化（"一百" → "100"）
            "enable_punc": True,      # 自动标点
            "enable_ddc": True,       # 数字转换
            "show_utterances": True,  # 分句结果
        }
    }

    resp = await _http_client.post(
        ASR_SUBMIT_URL,
        content=json.dumps(request_body),
        headers=headers,
    )

    status_code = resp.headers.get("X-Api-Status-Code", "")
    if status_code != "20000000":
        msg = resp.headers.get("X-Api-Message", "unknown error")
        logger.error(f"[ASR] Submit failed: {status_code} - {msg}")
        raise Exception(f"ASR submit failed: {msg}")

    x_tt_logid = resp.headers.get("X-Tt-Logid", "")
    logger.info(f"[ASR] Task submitted: {task_id}, logid: {x_tt_logid}")

    # 3. 轮询查询结果（最多等 120 秒）
    query_headers = {
        "X-Api-App-Key": settings.VOLC_ASR_APPID,
        "X-Api-Access-Key": settings.VOLC_ASR_TOKEN,
        "X-Api-Resource-Id": "volc.bigasr.auc",
        "X-Api-Request-Id": task_id,
        "X-Tt-Logid": x_tt_logid,
    }

    max_wait = 120  # 最多等 120 秒
    interval = 2    # 每 2 秒查一次
    elapsed = 0

    while elapsed < max_wait:
        await asyncio.sleep(interval)
        elapsed += interval

        query_resp = await _http_client.post(
            ASR_QUERY_URL,
            content=json.dumps({}),
            headers=query_headers,
        )

        code = query_resp.headers.get("X-Api-Status-Code", "")

        if code == "20000000":
            # ★ 任务完成，提取文字
            result_json = query_resp.json()
            text = result_json.get("result", {}).get("text", "")
            logger.info(f"[ASR] Transcription done ({elapsed}s): {text[:100]}...")
            return text

        elif code in ("20000001", "20000002"):
            # 排队中/处理中，继续等
            logger.debug(f"[ASR] Waiting... ({elapsed}s, status: {code})")
            continue

        elif code == "20000003":
            # 静音音频
            logger.warning(f"[ASR] Silent audio detected for {audio_cos_key}")
            return ""

        else:
            msg = query_resp.headers.get("X-Api-Message", "unknown")
            logger.error(f"[ASR] Query failed: {code} - {msg}")
            raise Exception(f"ASR query failed: {msg}")

    # 超时
    logger.error(f"[ASR] Timeout after {max_wait}s for task {task_id}")
    raise Exception("ASR transcription timeout")


# ════════════════════════════════════════
# 2. 批量转写多个音频片段
# ════════════════════════════════════════

async def transcribe_audio_keys(audio_keys: list[str]) -> str:
    """
    转写多个音频文件，将结果拼接为一段文字。
    各段之间用逗号分隔。
    """
    if not audio_keys:
        return ""

    texts = []
    for key in audio_keys:
        try:
            text = await transcribe_audio_with_asr(key)
            if text:
                texts.append(text.strip())
        except Exception as e:
            logger.error(f"[ASR] Failed to transcribe {key}: {e}")
            # 单段失败不阻断，继续处理下一段
            continue

    return "，".join(texts)
