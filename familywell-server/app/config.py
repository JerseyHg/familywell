from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://familywell:familywell123@postgres:5432/familywell"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 10080  # 7 days

    # 腾讯云 COS
    COS_SECRET_ID: str = ""
    COS_SECRET_KEY: str = ""
    COS_REGION: str = "ap-beijing"
    COS_BUCKET: str = ""

    # 豆包 API
    DOUBAO_API_KEY: str = ""
    DOUBAO_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"
    DOUBAO_MODEL: str = "doubao-seed-2.0-lite"

    # 豆包 Embedding
    DOUBAO_EMBEDDING_MODEL: str = "doubao-embedding-large"
    EMBEDDING_DIMENSION: int = 2048

    # 豆包 Chat (RAG 问答用，可与 DOUBAO_MODEL 同一个或用更强的)
    DOUBAO_CHAT_MODEL: str = "doubao-seed-2.0-lite"

    # RAG 参数
    RAG_TOP_K: int = 8
    RAG_SCORE_THRESHOLD: float = 0.3

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
