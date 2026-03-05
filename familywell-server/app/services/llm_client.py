"""
app/services/llm_client.py — 统一 LLM 客户端
══════════════════════════════════════════════
全项目唯一的 AsyncOpenAI 实例入口。
所有 service 文件通过 get_llm_client() 获取客户端，
不再各自 AsyncOpenAI(...) 重复实例化。

使用方式：
    from app.services.llm_client import get_llm_client
    client = get_llm_client()
    await client.chat.completions.create(...)
"""
from openai import AsyncOpenAI
from app.config import get_settings

_client: AsyncOpenAI | None = None


def get_llm_client() -> AsyncOpenAI:
    """
    返回全局单例 AsyncOpenAI 客户端。
    首次调用时惰性初始化，之后复用同一实例（复用连接池）。
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(
            api_key=settings.DOUBAO_API_KEY,
            base_url=settings.DOUBAO_BASE_URL,
        )
    return _client
