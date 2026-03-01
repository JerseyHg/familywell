"""
app/routers/auth.py — 认证路由
──────────────────────────────
[P1-2] 新增 POST /api/auth/wx-login 微信登录接口
       流程：前端 wx.login() → code → 后端换 openid → 查找或创建用户 → 返回 JWT
"""
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.models.user import User, UserProfile
from app.models.reminder import ReminderSetting
from app.schemas.auth import (
    RegisterRequest, LoginRequest, LoginResponse, UserResponse,
    WxLoginRequest,
)
from app.utils.auth import hash_password, verify_password, create_access_token
from app.utils.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)
settings = get_settings()


# ── 原有接口（不变）──

@router.post("/register", response_model=LoginResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
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

    profile = UserProfile(user_id=user.id, onboarding_completed=False)
    db.add(profile)

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
    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
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


# ────────────────────────────────────────
# [P1-2] 微信登录
# ────────────────────────────────────────

@router.post("/wx-login", response_model=LoginResponse)
async def wx_login(req: WxLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    微信小程序登录流程：
    1. 前端调用 wx.login() 获取 code
    2. 后端用 code + appid + secret 调用微信 jscode2session 换取 openid
    3. 查找已有用户（by openid）或自动注册新用户
    4. 返回 JWT token
    """
    if not settings.WECHAT_APPID or not settings.WECHAT_SECRET:
        raise HTTPException(
            status_code=501,
            detail="微信登录未配置，请在 .env 中设置 WECHAT_APPID 和 WECHAT_SECRET",
        )

    # ── Step 1: 用 code 换 openid ──
    openid = await _code_to_openid(req.code)

    # ── Step 2: 查找或创建用户 ──
    result = await db.execute(select(User).where(User.openid == openid))
    user = result.scalar_one_or_none()

    is_new = False

    if user:
        # 老用户：更新昵称/头像（如果前端传了）
        if req.nickname and req.nickname != user.nickname:
            user.nickname = req.nickname
        if req.avatar_url and req.avatar_url != user.avatar_url:
            user.avatar_url = req.avatar_url
    else:
        # 新用户：自动注册
        is_new = True
        user = User(
            username=f"wx_{openid[:16]}",  # 用 openid 前缀作为 username
            openid=openid,
            nickname=req.nickname or "微信用户",
            avatar_url=req.avatar_url,
            password_hash=None,  # 微信登录用户无密码
        )
        db.add(user)
        await db.flush()

        # 创建空 profile
        profile = UserProfile(user_id=user.id, onboarding_completed=False)
        db.add(profile)

        # 创建默认提醒设置
        setting = ReminderSetting(user_id=user.id)
        db.add(setting)

        await db.flush()
        logger.info(f"New user registered via WeChat: {user.id} (openid={openid[:8]}...)")

    token = create_access_token(user.id)

    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
            is_new=is_new,
        ),
    )


async def _code_to_openid(code: str) -> str:
    """调用微信 jscode2session 接口换取 openid"""
    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": settings.WECHAT_APPID,
        "secret": settings.WECHAT_SECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

        if "openid" not in data:
            errcode = data.get("errcode", "unknown")
            errmsg = data.get("errmsg", "")
            logger.error(f"WeChat jscode2session failed: {errcode} {errmsg}")
            raise HTTPException(
                status_code=401,
                detail=f"微信登录失败：{errmsg or '无效的 code'}",
            )

        return data["openid"]

    except httpx.HTTPError as e:
        logger.error(f"WeChat API request failed: {e}")
        raise HTTPException(status_code=502, detail="微信服务器通信失败")
