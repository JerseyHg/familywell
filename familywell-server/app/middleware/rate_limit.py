"""
app/middleware/rate_limit.py — Redis 速率限制中间件
──────────────────────────────────────────────────────
[P0-2] 基于 Redis 滑动窗口的 API 速率限制

策略：
- 普通接口：60 次/分钟
- 认证接口（登录/注册）：10 次/分钟（防暴力破解）
- AI 接口（识别/问答）：20 次/分钟（控制成本）
- 按 IP + 路径分组限流
"""
import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# 路径 → 限流等级映射
_AUTH_PATHS = {"/api/auth/login", "/api/auth/register", "/api/auth/wx-login"}
_AI_PATHS = {"/api/records", "/api/chat/stream", "/api/medications/voice-add", "/api/profile/voice-parse"}


def _get_client_ip(request: Request) -> str:
    """从 X-Forwarded-For 或 X-Real-IP 获取真实 IP。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else "unknown"


def _get_rate_limit(path: str, settings) -> int:
    """根据路径返回对应的每分钟限制数。"""
    if path in _AUTH_PATHS:
        return settings.RATE_LIMIT_AUTH
    for ai_path in _AI_PATHS:
        if path.startswith(ai_path):
            return settings.RATE_LIMIT_AI
    return settings.RATE_LIMIT_DEFAULT


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis 滑动窗口速率限制。
    使用 sorted set，score 为时间戳，窗口 60 秒。
    """

    def __init__(self, app, redis_client=None, settings=None):
        super().__init__(app)
        self.redis = redis_client
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        # 跳过健康检查和静态路径
        path = request.url.path
        if path in ("/health", "/", "/docs", "/openapi.json"):
            return await call_next(request)

        # 跳过 OPTIONS 预检
        if request.method == "OPTIONS":
            return await call_next(request)

        # 未启用或 Redis 不可用 → 放行
        if not self.settings or not self.settings.RATE_LIMIT_ENABLED or not self.redis:
            return await call_next(request)

        try:
            client_ip = _get_client_ip(request)
            limit = _get_rate_limit(path, self.settings)
            window = 60  # 60 秒窗口

            # Redis sorted set key: rl:{ip}:{path_group}
            path_group = "auth" if path in _AUTH_PATHS else "ai" if any(path.startswith(p) for p in _AI_PATHS) else "default"
            key = f"rl:{client_ip}:{path_group}"

            now = time.time()
            window_start = now - window

            pipe = self.redis.pipeline()
            # 清理过期记录
            pipe.zremrangebyscore(key, 0, window_start)
            # 统计窗口内请求数
            pipe.zcard(key)
            # 添加当前请求
            pipe.zadd(key, {f"{now}": now})
            # 设置 key 过期（防止垃圾堆积）
            pipe.expire(key, window + 10)
            results = await pipe.execute()

            current_count = results[1]

            if current_count >= limit:
                logger.warning(f"Rate limit exceeded: {client_ip} on {path_group} ({current_count}/{limit})")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "请求过于频繁，请稍后再试",
                        "retry_after": window,
                    },
                    headers={
                        "Retry-After": str(window),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )

            response = await call_next(request)
            # 在 response header 中标注限流信息
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(max(0, limit - current_count - 1))
            return response

        except Exception as e:
            # Redis 挂了不影响业务，只记日志
            logger.warning(f"Rate limit check failed (passing through): {e}")
            return await call_next(request)
