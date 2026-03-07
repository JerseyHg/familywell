"""
app/utils/timezone.py — 时区工具
─────────────────────────────────
根据前端传入的 X-Timezone-Offset header 计算用户本地日期/时间。

JS getTimezoneOffset() 返回值的含义：
  UTC+8 → -480   (比 UTC 快 480 分钟)
  UTC-5 → 300    (比 UTC 慢 300 分钟)
所以：用户本地时间 = UTC + (-offset) 分钟
"""
from datetime import date, datetime, timedelta

from fastapi import Header


def user_today(tz_offset: int | None) -> date:
    """根据前端传入的时区偏移量计算用户本地的"今天"。"""
    if tz_offset is None:
        return date.today()
    utc_now = datetime.utcnow()
    user_now = utc_now + timedelta(minutes=-tz_offset)
    return user_now.date()


def utc_to_user_local(dt: datetime, tz_offset: int | None) -> datetime:
    """将 UTC datetime 转换为用户本地时间（用于显示）。"""
    if tz_offset is None or dt is None:
        return dt
    return dt + timedelta(minutes=-tz_offset)


def get_tz_offset(
    x_timezone_offset: int | None = Header(None, alias="X-Timezone-Offset"),
) -> int | None:
    """FastAPI 依赖：从请求 header 中提取时区偏移量。"""
    return x_timezone_offset
