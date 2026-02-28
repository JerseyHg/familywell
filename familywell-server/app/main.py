import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import engine, Base
from app.services.cron_service import run_daily_tasks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting FamilyWell API...")

    # Create tables (use alembic in production)
    async with engine.begin() as conn:
        # Enable pgvector extension
        await conn.execute(
            __import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector")
        )
        # Import all models so they register with Base
        import app.models  # noqa
        await conn.run_sync(Base.metadata.create_all)

    # Start scheduler
    scheduler.add_job(run_daily_tasks, "cron", hour=0, minute=5, id="daily_tasks")
    scheduler.start()
    logger.info("Scheduler started.")

    yield

    # Shutdown
    scheduler.shutdown()
    await engine.dispose()
    logger.info("FamilyWell API shutdown.")


app = FastAPI(
    title="FamilyWell API",
    description="家庭健康档案管理系统",
    version="1.0.0",
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
    return {"message": "FamilyWell API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
