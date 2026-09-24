# -*- coding: utf-8 -*-
"""会话条目只有一份定义：`core.text_manager.new_session_entry`。

条目是一个**到处被按字段索引**的 dict（`session["lock"]` / `session["outbox"]` /
`session["retract_state"]` …）。每多一个「谁用到谁 `setdefault`」的字段，就多一种
「建会话那条路没带上」的漏法：缺 `lock` 是 `KeyError`（本轮直接崩），缺 `outbox`
是补写队列不跟着会话走。字段清单锁在**工厂**上，不锁在任何调用点。

变异：从 `new_session_entry` 删掉 `lock`（或 `retract_state`）→ 本条红。
"""

from __future__ import annotations

import asyncio

from core.text_manager import new_session_entry
from routers.chat import RETRACT_COOLDOWN_TURNS


def test_entry_carries_the_fields_callers_index_into():
    entry = new_session_entry(object(), None, "u1")

    assert entry["user_id"] == "u1"
    assert isinstance(entry["lock"], asyncio.Lock), "缺 lock：`async with session['lock']` 当场 KeyError"
    assert entry["outbox"].has_pending is False, "缺 outbox：补写队列不跟着会话走"


def test_a_brand_new_session_is_not_inside_the_retract_cooldown():
    """`retract_state` 的初值有语义：新建的会话必须**不在**冷却里（否则第一次撤回必被挡）。

    断言的是这条语义，不是那个字面量元组 —— 照抄初值的话，初值改成什么都照样绿。
    """
    rs = new_session_entry(object(), None, "u1")["retract_state"]

    assert rs["retract_count"] == 0
    assert rs["turn_index"] - rs["last_retract_turn"] >= RETRACT_COOLDOWN_TURNS


def test_each_entry_gets_its_own_lock_outbox_and_retract_state():
    """两个会话不许共用同一把锁 / 同一个队列 / 同一份撤回计数 —— 共享了就是互相挡、互相改。"""
    a = new_session_entry(object(), None, "u1")
    b = new_session_entry(object(), None, "u1")

    assert a["lock"] is not b["lock"]
    assert a["outbox"] is not b["outbox"]
    assert a["retract_state"] is not b["retract_state"]
