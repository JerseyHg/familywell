"""
app/schemas/auth.py — 认证相关请求/响应模型
──────────────────────────────────────────────
[P1-2] 新增 WxLoginRequest（微信登录）
"""
from pydantic import BaseModel


class RegisterRequest(BaseModel):
    username: str
    password: str
    nickname: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


# ── [P1-2] 微信登录 ──

class WxLoginRequest(BaseModel):
    """微信小程序登录：前端调用 wx.login() 拿到 code 发给后端"""
    code: str
    nickname: str | None = None
    avatar_url: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    username: str
    nickname: str | None
    avatar_url: str | None
    is_new: bool = False  # [P1-2] 标记是否新用户（前端决定是否进引导页）

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
