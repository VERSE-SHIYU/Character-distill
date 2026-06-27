"""用户本地时间的唯一抽象来源。业务层只用本模块，禁止裸 datetime.now()。"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Shanghai"  # 时区缺失/非法时的回退


def _safe_zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz) if tz else ZoneInfo(DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


class UserClock:
    """面向用户/角色感知的现实时间唯一抽象来源。业务层只用本类，禁止裸 datetime.now()。"""

    @staticmethod
    def now(tz: str | None = None) -> datetime:
        return datetime.now(_safe_zone(tz))  # 始终返回 aware datetime

    @staticmethod
    def to_user_tz(dt: datetime, tz: str | None) -> datetime:
        """把任意 datetime 归一到用户时区；naive 视为 UTC。"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_safe_zone(tz))


def describe_time_period(hour: int) -> str:
    """将小时（0-23）映射为中文时段名。"""
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "夜晚"
    return "深夜"  # 23 <= hour or hour < 5
