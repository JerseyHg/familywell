"""
app/config.py — 环境变量配置
──────────────────────────────
[P0-1] 新增 JWT 密钥启动校验：默认值不允许在生产环境使用
[P1-2] 新增微信小程序 APPID / SECRET 配置
"""
import secrets
import logging
from pydantic_settings import BaseSettings
from functools import lru_cache

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://familywell:familywell123@postgres:5432/familywell"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # JWT ── [P0-1] 默认值改为空字符串，启动时校验
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 10080  # 7 days

    # 腾讯云 COS
    COS_SECRET_ID: str = ""
    COS_SECRET_KEY: str = ""
    COS_REGION: str = "ap-shanghai"
    COS_BUCKET: str = ""
    COS_ACCELERATE_DOMAIN: str = ""

    # 豆包 API
    DOUBAO_API_KEY: str = ""
    DOUBAO_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"
    DOUBAO_MODEL: str = "doubao-seed-2.0-lite"

    # 豆包 Embedding
    DOUBAO_EMBEDDING_MODEL: str = "doubao-embedding-large"
    EMBEDDING_DIMENSION: int = 2048

    # 豆包 Chat (RAG 问答用)
    DOUBAO_CHAT_MODEL: str = "doubao-seed-2.0-lite"

    # RAG 参数
    RAG_TOP_K: int = 8
    RAG_SCORE_THRESHOLD: float = 0.3

    # ── [P1-2] 微信登录 ──
    WECHAT_APPID: str = ""
    WECHAT_SECRET: str = ""

    # ── [P0-2] 速率限制 ──
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: int = 60        # 每分钟请求数（普通接口）
    RATE_LIMIT_AUTH: int = 10           # 每分钟请求数（登录/注册）
    RATE_LIMIT_AI: int = 20            # 每分钟请求数（AI 相关接口）

    # 火山引擎语音识别 ASR
    VOLC_ASR_APPID: str = ""       # 火山引擎语音应用 AppID
    VOLC_ASR_TOKEN: str = ""       # 火山引擎语音应用 Access Token

    class Config:
        env_file = ".env"


# ── [P0-1] 不安全默认值黑名单 ──
_INSECURE_SECRETS = {
    "", "change-me-in-production", "secret", "your-secret-key",
    "jwt-secret", "changeme", "test",
}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()

    # ── [P0-1] JWT 密钥安全校验 ──
    if settings.JWT_SECRET_KEY in _INSECURE_SECRETS:
        generated = secrets.token_urlsafe(32)
        logger.warning(
            "⚠️  JWT_SECRET_KEY 未设置或使用了不安全的默认值！\n"
            f"   已自动生成临时密钥（仅本次运行有效，重启后 token 全部失效）。\n"
            f"   请在 .env 中设置: JWT_SECRET_KEY={generated}"
        )
        # 直接修改 settings 对象（绕过 frozen）
        object.__setattr__(settings, 'JWT_SECRET_KEY', generated)

    return settings
