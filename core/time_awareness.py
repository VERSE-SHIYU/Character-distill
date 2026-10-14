# -*- coding: utf-8 -*-
"""时间感知块：由「现在」与「上次对话时间」算出要注入的那段提示 —— 纯函数，无 IO。

spec：`docs/specs/arc-attitude-time-boundary.md` §B1。「要不要注入」与「注入什么文字」都只在
这里写一处；`ChatEngine` 只把事实（现在、上次对话时间、是不是首轮）递进来，不认识存储与时钟。
"""

from __future__ import annotations

from datetime import datetime

from core.clock import UserClock, describe_time_period

# 距上次对话不到这个秒数、且时段没变 → 不注入（同一时段连聊几句不必每轮重复报时间）。
INJECT_GAP_SECONDS = 600


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


def _interval_sentence(now: datetime, last_activity_at: datetime) -> str:
    """距上次对话的分档文案（原是 `chat_engine._build_time_awareness_block` 里的分档，原样搬来）。"""
    user_updated = UserClock.to_user_tz(last_activity_at)
    days_ago = (now.date() - user_updated.date()).days
    if days_ago == 0:
        seconds = (now - user_updated).total_seconds()
        if seconds < 0:
            return ""
        if seconds < INJECT_GAP_SECONDS:
            return "你们刚刚还在聊。"
        return "今天早些时候你们聊过。"
    if days_ago == 1:
        return "你们昨天聊过。"
    if days_ago <= 6:
        return "你们已经好几天没说话了。"
    return "你们已经很久没联系了。"


def should_inject(now: datetime, last_activity_at, *, first_turn: bool) -> bool:
    """要不要注入时间块：首轮、或距上次对话 ≥ `INJECT_GAP_SECONDS`、或时段变了 → 是。

    否则不注入 —— 同一时段、十来分钟内连聊几句，时间没变也没断，不必每轮重复（省 token）。
    """
    if first_turn:
        return True
    last = _as_datetime(last_activity_at)
    if last is None:
        return True
    user_updated = UserClock.to_user_tz(last)
    if (now - user_updated).total_seconds() >= INJECT_GAP_SECONDS:
        return True
    return describe_time_period(now.hour) != describe_time_period(user_updated.hour)


def build_block(now: datetime, last_activity_at, *, first_turn: bool) -> str:
    """注入块（不该注入时返回空串）：现实时间 + 间隔句 + 一句剧情时间优先的说明。

    只陈述事实，**不给行为示范** —— 怎么把时间感受体现在语气里，由角色本身与好感阶段决定，
    不由这一块替它规定（原块的「比如深夜会关心对方怎么还不睡」已删）。
    """
    if not should_inject(now, last_activity_at, first_turn=first_turn):
        return ""
    block = (f"\n\n【现实时间】现在是{describe_time_period(now.hour)}"
             f"（{now.hour:02d}:{now.minute:02d}）。\n")
    last = None if first_turn else _as_datetime(last_activity_at)
    if last is not None:
        interval = _interval_sentence(now, last)
        if interval:
            block += f"{interval}\n"
    return block + (
        "对话里已写明剧情时间（如「第二天早上」）时以剧情为准；"
        "对方问起现实时间时才按这里回答。\n"
    )
