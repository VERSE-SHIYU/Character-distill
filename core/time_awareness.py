# -*- coding: utf-8 -*-
"""时间感知：现实时间事实与「隔了多久」两句提示 —— 纯函数，无 IO。

spec：`docs/specs/time-awareness-fix.md`（取代 `arc-attitude-time-boundary.md` §B1 的门控设计）。
两件事各管各的，**位置也由各自的性质决定**：

- `reality_line`：现在几点 + 剧情时间优先。只是供模型查用的背景事实，**每轮都给**，由
  `ChatEngine` 放进系统提示（离生成位置远、不显眼，不易被每轮复述）。
- `interval_note`：距上次对话隔了多久。只在真隔了一段（≥ `INTERVAL_GAP_SECONDS`）才有，是这一轮
  该注意的事，由 `ChatEngine` 接在本轮用户消息末尾。时段变化不再单独触发：`reality_line` 每轮都有。

文案只在这里写一处；`ChatEngine` 只递事实（现在、上次对话时间），不认识存储。
"""

from __future__ import annotations

from datetime import datetime

from core.clock import UserClock, describe_time_period

# 距上次对话不到这个秒数 → 不提「隔了多久」（连聊几句，对话没断）。
INTERVAL_GAP_SECONDS = 600


def _as_datetime(value) -> datetime | None:
    """把会话的 `updated_at`（datetime 或 ISO 字符串）归一成 datetime；解析不了当没有。"""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _interval_sentence(now: datetime, last: datetime) -> str:
    """隔了多久的分档文案。只在隔了 ≥ `INTERVAL_GAP_SECONDS` 时调用（同日即「今天早些时候」）。"""
    days_ago = (now.date() - UserClock.to_user_tz(last).date()).days
    if days_ago <= 0:
        return "今天早些时候你们聊过。"
    if days_ago == 1:
        return "你们昨天聊过。"
    if days_ago <= 6:
        return "你们已经好几天没说话了。"
    return "你们已经很久没联系了。"


def reality_line(now: datetime) -> str:
    """现实时间事实 + 剧情时间优先（每轮都给，放系统提示）。只陈述事实，不给行为示范。"""
    return (f"\n\n【现实时间】现在是{describe_time_period(now.hour)}"
            f"（{now.hour:02d}:{now.minute:02d}）。"
            "对话里已写明剧情时间（如「第二天早上」）时以剧情为准；"
            "对方问起现实时间时才按这里回答。\n")


def interval_note(now: datetime, last_activity_at) -> str:
    """距上次对话隔了多久（接在本轮用户消息末尾）；不值得提或无从得知时返回空串。"""
    last = _as_datetime(last_activity_at)
    if last is None or (now - UserClock.to_user_tz(last)).total_seconds() < INTERVAL_GAP_SECONDS:
        return ""
    return f"\n\n{_interval_sentence(now, last)}\n"
