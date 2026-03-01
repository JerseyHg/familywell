"""
app/main.py — 应用入口
──────────────────────
[P0-1] 启动时校验 JWT_SECRET_KEY
[P0-2] 加入 Redis 速率限制中间件
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import engine, Base
from app.config import get_settings
from app.services.cron_service import run_daily_tasks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── [P0-1] 启动时触发配置加载（含 JWT 校验）──
    settings = get_settings()
    logger.info("Configuration loaded. JWT key validated.")

    # Startup
    logger.info("Starting FamilyWell API...")

    # Create tables (use alembic in production)
    async with engine.begin() as conn:
        await conn.execute(
            __import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector")
        )
        from app import models  # noqa
        await conn.run_sync(Base.metadata.create_all)

    # ── [P0-2] 初始化 Redis 连接用于速率限制 ──
    redis_client = None
    if settings.RATE_LIMIT_ENABLED:
        try:
            import redis.asyncio as aioredis
            redis_client = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=3,
            )
            await redis_client.ping()
            logger.info("Redis connected for rate limiting.")

            # 挂载到 app.state 供中间件使用
            app.state.redis = redis_client
        except Exception as e:
            logger.warning(f"Redis connection failed (rate limiting disabled): {e}")
            redis_client = None

    # Start scheduler
    scheduler.add_job(run_daily_tasks, "cron", hour=0, minute=5, id="daily_tasks")
    scheduler.start()
    logger.info("Scheduler started.")

    yield

    # Shutdown
    scheduler.shutdown()
    if redis_client:
        await redis_client.close()
    await engine.dispose()
    logger.info("FamilyWell API shutdown.")


app = FastAPI(
    title="FamilyWell API",
    description="家庭健康档案管理系统",
    version="1.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── [P0-2] 速率限制中间件 ──
settings = get_settings()
if settings.RATE_LIMIT_ENABLED:
    from app.middleware.rate_limit import RateLimitMiddleware
    # 注意：Redis client 在 lifespan 中初始化，这里先传 None，
    # 中间件内部从 request.app.state.redis 获取
    class _LazyRateLimitMiddleware(RateLimitMiddleware):
        async def dispatch(self, request, call_next):
            if not self.redis and hasattr(request.app.state, 'redis'):
                self.redis = request.app.state.redis
            return await super().dispatch(request, call_next)

    app.add_middleware(_LazyRateLimitMiddleware, settings=settings)

# Register routers
from app.routers import auth, profile, records, medications, stats, families, reminders, home, chat, search, projects  # noqa

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(records.router)
app.include_router(medications.router)
app.include_router(stats.router)
app.include_router(families.router)
app.include_router(reminders.router)
app.include_router(home.router)
app.include_router(chat.router)
app.include_router(search.router)
app.include_router(projects.router)


@app.get("/")
async def root():
    return {"message": "FamilyWell API", "version": "1.1.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
