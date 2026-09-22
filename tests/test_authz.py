# -*- coding: utf-8 -*-
"""core/authz.py::fetch_for_actor 的四个分支。

判据不只看返回值，还要看 **unscoped 有没有被调用** —— 越权的形态是「无身份原语被触发」，
返回值恰好相同（都拿到那条记录）也改变不了越权事实。所以用调用记录当探针。
"""
from __future__ import annotations

import asyncio

import pytest

from core.authz import fetch_for_actor

OWNER = {"id": "u_owner", "role": "user"}
ADMIN = {"id": "u_admin", "role": "admin"}
# 缺 role 键的调用方（如内部构造的 user dict）—— 必须按「不是管理员」处理。
BARE = {"id": "u_bare"}

RECORD = {"id": "e1", "user_id": "u_owner"}


class _Source:
    """记录被调用的实参，返回预设结果。"""

    def __init__(self, result):
        self.result = result
        self.calls: list[tuple] = []

    async def __call__(self, *args):
        self.calls.append(args)
        return self.result


@pytest.fixture
def owned_miss():
    return _Source(None)


@pytest.fixture
def owned_hit():
    return _Source(dict(RECORD))


@pytest.fixture
def unscoped():
    return _Source(dict(RECORD))


def _run(coro):
    return asyncio.run(coro)


def test_owned_hit_returns_record_without_touching_unscoped(owned_hit, unscoped):
    out = _run(fetch_for_actor(owned_hit, unscoped, "e1", OWNER, allow_admin=False))
    assert out == RECORD
    assert owned_hit.calls == [("e1", "u_owner")]
    assert unscoped.calls == [], "属主命中时不得再走无身份原语"


def test_owned_miss_without_allow_admin_returns_none(owned_miss, unscoped):
    out = _run(fetch_for_actor(owned_miss, unscoped, "e1", ADMIN, allow_admin=False))
    assert out is None
    assert unscoped.calls == [], "allow_admin=False 时即便调用方是管理员也不得跨属主"


def test_owned_miss_non_admin_returns_none(owned_miss, unscoped):
    out = _run(fetch_for_actor(owned_miss, unscoped, "e1", OWNER, allow_admin=True))
    assert out is None
    assert unscoped.calls == []


@pytest.mark.parametrize("user", [ADMIN, BARE], ids=["admin", "no_role_key"])
def test_owned_miss_admin_falls_through_to_unscoped(owned_miss, unscoped, user):
    """管理员落 unscoped；缺 role 键的按非管理员处理，仍不落。"""
    out = _run(fetch_for_actor(owned_miss, unscoped, "e1", user, allow_admin=True))
    if user is ADMIN:
        assert out == RECORD
        assert unscoped.calls == [("e1",)]
    else:
        assert out is None
        assert unscoped.calls == []


def test_unscoped_miss_still_returns_none(owned_miss, unscoped):
    unscoped.result = None
    out = _run(fetch_for_actor(owned_miss, unscoped, "e1", ADMIN, allow_admin=True))
    assert out is None
    assert unscoped.calls == [("e1",)]
