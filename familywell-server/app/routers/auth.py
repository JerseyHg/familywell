from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserProfile
from app.models.reminder import ReminderSetting
from app.schemas.auth import (
    RegisterRequest, LoginRequest, LoginResponse, UserResponse,
)
from app.utils.auth import hash_password, verify_password, create_access_token
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=LoginResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check unique username
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        nickname=req.nickname or req.username,
    )
    db.add(user)
    await db.flush()

    # Create empty profile
    profile = UserProfile(user_id=user.id, onboarding_completed=False)
    db.add(profile)

    # Create default reminder settings
    setting = ReminderSetting(user_id=user.id)
    db.add(setting)

    await db.flush()

    token = create_access_token(user.id)
    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
        ),
    )


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user.id)
    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return UserResponse(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
    )
