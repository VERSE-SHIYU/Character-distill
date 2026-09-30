# -*- coding: utf-8 -*-
"""注册后把用户资料同步给对端（spec fix-register-profile-sync；发送改走跨境发件箱后仍锁同一条线上缺陷）。

缺陷：注册路由把函数 `node_region`（而不是它的返回值）当所属地域传给了
`forward_user_profile_to_peer`，签名序列化请求体时抛 `TypeError`，被路由的 try/except
吞成一行错误日志 —— 资料一次都没发出去。发件箱之后注册只入队，由补发循环发送，
本测试走「注册 → 跑一轮补发」这条真实路径，断言对端收到、地域是字符串。

对端用 httpx 自带的 MockTransport 扮演，记录收到的请求；业务写入走真 PG。
"""
from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest
from fastapi import FastAPI

from conftest import PG_ENV
from core.node import node_region
from deps import get_storage
from routers.auth import router as auth_router

pytestmark = PG_ENV.skipif("注册资料同步用例")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def store(outbox_store):
    return outbox_store


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import limiter as L

    monkeypatch.setattr(L.limiter, "enabled", False)
    monkeypatch.setenv("INTER_NODE_SECRET", "test-inter-node-secret-0123456789abcdef")
    monkeypatch.setenv("PEER_NODE_URL", "https://peer-node")


async def test_registration_delivers_the_profile_with_a_string_region(store, monkeypatch, caplog):
    from legal_versions import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_storage] = lambda: store
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t")

    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def _peer(*_a, **kw):  # 转发代码在调用时才取 httpx.AsyncClient：换成记录请求的对端
        return real_client(transport=httpx.MockTransport(
            lambda req: (seen.append(req), httpx.Response(200))[1]), timeout=kw.get("timeout"))

    monkeypatch.setattr(httpx, "AsyncClient", _peer)

    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=False)
    name = _uid("n")
    caplog.set_level(logging.ERROR)
    async with client:
        r = await client.post("/api/auth/register", json={
            "username": name, "password": "abcd1234", "invite_code": code,
            "agreed_terms_version": CURRENT_TERMS_VERSION,
            "agreed_privacy_version": CURRENT_PRIVACY_VERSION,
        })
    assert r.status_code == 200, r.text

    from cross_border_sync import _resync_once
    await _resync_once(store)

    sent = [json.loads(q.content) for q in seen if q.url.path == "/api/inter-node/user/sync"]
    assert len(sent) == 1, (
        f"资料没发出去；错误日志：{[x.getMessage() for x in caplog.records]}")
    assert sent[0]["username"] == name
    assert sent[0]["home_region"] == node_region(), sent[0]
    assert not [x for x in caplog.records if "Forward user profile" in x.getMessage()]
