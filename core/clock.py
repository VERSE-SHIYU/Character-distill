"""用户本地时间的唯一抽象来源。业务层只用本模块，禁止裸 datetime.now()。"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.nonfatal import nonfatal_sync

DEFAULT_TZ = "Asia/Shanghai"  # 时区缺失/非法时的回退

# 请求入口（`web/server.py` 的 AuthMiddleware）定出来的「本次请求的时区」。
# 空串 = 没人定（进程外调用、后台任务、单元测试）→ `UserClock` 回退 DEFAULT_TZ。
# 放在本模块是因为它是「用户本地时间的唯一抽象来源」，读它的人只该是 `UserClock`。
_current_timezone: ContextVar[str] = ContextVar("current_timezone", default="")


def set_current_timezone(tz: str) -> None:
    """把请求入口定出来的时区设进上下文（空串 = 没定出来）。"""
    _current_timezone.set(tz)


def _safe_zone(tz: str | None) -> ZoneInfo:
    """用哪个时区 —— **必有答案**，解析不了就兜底 `DEFAULT_TZ`。

    兜底不是「正常情况」：走到这里说明上下文或库里存了一个非法的 IANA 时区名，
    而这个用户看到的**所有**时间会整体偏移。结果仍然可用（所以是 WARNING 而非
    ERROR），但必须留痕，否则「某个人时间一直差几小时」无从查起。

    不用 `is_valid_timezone` 先探一遍再解析：那是解析两次，且引入 TOCTOU 式的
    「探的时候好、用的时候坏」。这里就用一次 try 表达「解析不了就兜底」。
    """
    if tz:
        with nonfatal_sync("clock", "resolve user timezone", level=logging.WARNING):
            return ZoneInfo(tz)
    return ZoneInfo(DEFAULT_TZ)


def is_valid_timezone(tz: str | None) -> bool:
    """`tz` 是不是一个能被解析的 IANA 时区名（空串 / 非法名都算否）。

    与 `_safe_zone` 问的不是同一件事：那个问「用哪个时区」（必有答案，兜底 DEFAULT_TZ），
    这个问「这个名字算不算数」（有真假）。请求入口要的是后者 —— 头里是个垃圾值时
    既不能用它，更不能把它写进用户资料。
    """
    if not tz:
        return False
    try:
        ZoneInfo(tz)
        return True
    except Exception:
        return False


class UserClock:
    """面向用户/角色感知的现实时间唯一抽象来源。业务层只用本类，禁止裸 datetime.now()。"""

    @staticmethod
    def now(tz: str | None = None) -> datetime:
        return datetime.now(_safe_zone(tz or _current_timezone.get()))  # 始终返回 aware datetime

    @staticmethod
    def to_user_tz(dt: datetime, tz: str | None = None) -> datetime:
        """把任意 datetime 归一到用户时区；naive 视为 UTC。

        `tz` 不给（或给空）时用请求入口设的当前时区 —— 业务代码一律不传，传了就是
        在绕开「请求入口统一确定」这条规则。
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_safe_zone(tz or _current_timezone.get()))


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
