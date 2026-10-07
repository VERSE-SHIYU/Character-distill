# -*- coding: utf-8 -*-
"""测试辅助：把「这一轮好感要变多少」翻成评估协议的字段（`core/affinity_protocol.py`）。

旧测试写的是「模型给出目标好感」；协议改成报事件之后，用它表达同一个意图，字段名只在协议模块定义。
"""

from __future__ import annotations

from core.affinity_protocol import FIELD_DELTA, FIELD_EVENT, FIELD_INDEX, FIELD_TIER
from core.affinity_rules import TIERS


def verdict(change: int) -> dict:
    """好感 +N → 做到亲近条件；−N → 冒犯；0 → 闲聊。档位取 N 所在的那一档（N 在 1–8 之间）。"""
    if change == 0:
        return {FIELD_EVENT: "neutral"}
    size = abs(change)
    tier = next(name for name, (lo, hi) in TIERS.items() if lo <= size <= hi)
    if change > 0:
        return {FIELD_EVENT: "met_condition", FIELD_TIER: tier, FIELD_DELTA: size, FIELD_INDEX: 0}
    return {FIELD_EVENT: "offended", FIELD_TIER: tier, FIELD_DELTA: size}
