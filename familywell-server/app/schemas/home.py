"""app/schemas/home.py — 首页聚合相关响应模型"""
from pydantic import BaseModel


class HomeResponse(BaseModel):
    profile: dict
    pending_tasks: list[dict]          # 当前待服药物（只显示 pending 的）
    ai_tip: str | None                 # AI 主动提示文案
    recent_activity: list[dict]        # 最近 2-3 条动态
    alert_count: int                   # 未解决提醒数（红点用）
    medication_suggestions: list[dict] # 待确认的药物建议
