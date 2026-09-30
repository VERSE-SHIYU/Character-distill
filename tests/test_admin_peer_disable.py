# -*- coding: utf-8 -*-
"""跨节点禁用 / 启用（spec admin-peer-disable）：发起端路由 → 签名 → 对端接收端 → 共用函数。

测试里「对端」就是同一个 app 的接收端路由：发起端经 `peer_client._client` 发出的请求，
被换成直连本 app 的 ASGITransport（不开端口、不出网）；对端不可达用 httpx 自带的
MockTransport 造。两端共用同一个测试 PG —— 用户 id 各不相同，不串。

**为什么用真 PG**：禁用是否真的写进库、找不到用户是否真的是「0 行」，只有真库答得了。
"""
from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

import peer_client
from conftest import PG_ENV, TEST_DATABASE_URL
from core import roles
from deps import get_storage
from inter_node_auth import create_auth_header
from routers import admin as A
from routers import inter_node as N
from routers.auth import get_current_user
from storage.postgres_store import PostgresStore

pytestmark = PG_ENV.skipif("跨节点禁用用例")

_RECEIVER = "/api/inter-node/admin/user-disabled"


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("INTER_NODE_SECRET", "test-inter-node-secret-0123456789abcdef")
    monkeypatch.setenv("PEER_NODE_URL", "http://peer-node")


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


async def _user(store, role=roles.USER) -> str:
    uid = _uid("u")
    await store.create_user(uid, _uid("n"), "x")
    await store.set_user_role(uid, role)
    return uid


def _app(store, operator_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(A.router)
    app.include_router(N.router)
    app.dependency_overrides[get_storage] = lambda: store

    async def _live():
        return await store.get_user_by_id(operator_id)

    app.dependency_overrides[get_current_user] = _live
    return app


def _route_peer_to(monkeypatch, transport: httpx.AsyncBaseTransport) -> None:
    """发起端发往对端的请求改走 `transport`（生产代码不留任何测试钩子）。"""
    monkeypatch.setattr(
        peer_client, "_client",
        lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout,
                                          base_url="http://peer-node"),
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _disabled(store, uid) -> bool:
    return bool((await store.get_user_by_id(uid) or {}).get("is_disabled"))


# ── 1. 正路：发起端禁用 / 启用对端用户，对端库里生效 ─────────────────────

async def test_peer_disable_then_enable(store, monkeypatch):
    admin = await _user(store, roles.ADMIN)
    target = await _user(store)
    app = _app(store, admin)
    _route_peer_to(monkeypatch, ASGITransport(app=app))
    async with _client(app) as c:
        r = await c.post(f"/api/admin/peer/users/{target}/disable")
        assert r.status_code == 200, r.text
        assert await _disabled(store, target)
        r = await c.post(f"/api/admin/peer/users/{target}/enable")
        assert r.status_code == 200, r.text
        assert not await _disabled(store, target)


# ── 2. 对端的拒绝原样转回：404 / 400（不能禁用自己）────────────────────

async def test_peer_unknown_user_is_relayed_as_404(store, monkeypatch):
    admin = await _user(store, roles.ADMIN)
    app = _app(store, admin)
    _route_peer_to(monkeypatch, ASGITransport(app=app))
    async with _client(app) as c:
        r = await c.post(f"/api/admin/peer/users/{_uid('ghost')}/disable")
    assert r.status_code == 404, r.text


async def test_peer_self_disable_is_relayed_as_400(store, monkeypatch):
    """两台管理员整号复制、id 相同：从一台禁用对端同 id 的账号 = 把自己锁在对端外面。"""
    admin = await _user(store, roles.ADMIN)
    app = _app(store, admin)
    _route_peer_to(monkeypatch, ASGITransport(app=app))
    async with _client(app) as c:
        r = await c.post(f"/api/admin/peer/users/{admin}/disable")
    assert r.status_code == 400, r.text
    assert not await _disabled(store, admin)


# ── 3. 对端不可达 / 签名被拒 → 502，不假装成功 ─────────────────────────

@pytest.mark.parametrize("peer", ["unreachable", "401"])
async def test_peer_failure_is_502(store, monkeypatch, peer):
    admin = await _user(store, roles.ADMIN)
    target = await _user(store)

    def _handler(request):
        if peer == "unreachable":
            raise httpx.ConnectError("peer down", request=request)
        return httpx.Response(401, json={"detail": "Unauthorized"})

    _route_peer_to(monkeypatch, httpx.MockTransport(_handler))
    async with _client(_app(store, admin)) as c:
        r = await c.post(f"/api/admin/peer/users/{target}/disable")
    assert r.status_code == 502, r.text
    assert not await _disabled(store, target), "对端没确认，本节点同 id 的账号也不能被动到"


async def test_no_peer_configured_is_503(store, monkeypatch):
    monkeypatch.delenv("PEER_NODE_URL")
    admin = await _user(store, roles.ADMIN)
    target = await _user(store)
    async with _client(_app(store, admin)) as c:
        r = await c.post(f"/api/admin/peer/users/{target}/disable")
    assert r.status_code == 503, r.text


# ── 4. 接收端：签名、操作名、字段 ────────────────────────────────────────

def _body(target, operator, *, op="admin_set_user_disabled", disabled=True) -> dict:
    return {"op": op, "subject_user_id": target, "operator_id": operator,
            "disabled": disabled, "request_id": uuid.uuid4().hex}


async def _post_signed(c, path, body, *, sign_body=None):
    headers = create_auth_header(sign_body if sign_body is not None else body)
    return await c.post(path, content=json.dumps(body), headers={
        **headers, "Content-Type": "application/json"})


async def test_receiver_rejects_bad_signature(store):
    target = await _user(store)
    body = _body(target, "someone")
    async with _client(_app(store, "nobody")) as c:
        r = await _post_signed(c, _RECEIVER, body, sign_body={**body, "disabled": False})
    assert r.status_code == 401
    assert not await _disabled(store, target)


async def test_receiver_rejects_other_op(store):
    """签名对、操作名不对 → 400：别的接口签出来的请求体搬不过来。"""
    target = await _user(store)
    async with _client(_app(store, "nobody")) as c:
        r = await _post_signed(c, _RECEIVER, _body(target, "someone", op="user_purge"))
    assert r.status_code == 400, r.text
    assert not await _disabled(store, target)


async def test_signed_disable_body_is_refused_by_every_other_write_receiver(store):
    """反方向：把一份签好名的禁用请求体原样搬到其它接收端，没有一个接受。

    接收端集合从路由表现取（不手写名单）。`/admin/users` 是只读列表，按设计对任何
    签名请求都回列表，不在「写」的集合里。
    """
    target = await _user(store)
    body = _body(target, "someone")
    paths = sorted(
        r.path for r in N.router.routes
        if isinstance(r, APIRoute) and "POST" in r.methods
        and r.path not in (_RECEIVER, "/api/inter-node/admin/users")
    )
    assert len(paths) >= 8, f"接收端集合塌了：{paths}"
    async with _client(_app(store, "nobody")) as c:
        accepted = []
        for p in paths:
            r = await _post_signed(c, p, body)
            if r.status_code < 400:
                accepted.append((p, r.status_code))
    assert accepted == [], f"禁用请求体被别的接收端接受了：{accepted}"


async def test_receiver_logs_who_did_what(store, caplog):
    operator = _uid("op")
    target = await _user(store)
    body = _body(target, operator)
    caplog.set_level(logging.INFO)
    async with _client(_app(store, "nobody")) as c:
        r = await _post_signed(c, _RECEIVER, body)
    assert r.status_code == 200, r.text
    lines = [rec.getMessage() for rec in caplog.records if rec.name.endswith("inter_node")]
    assert any(body["request_id"] in m and operator in m and target in m for m in lines), lines


# ── 5. 联邦用户列表改走 `post_to_peer` 后行为不变（原先无任何测试）─────────

async def test_federated_list_reads_peer_users_via_signed_request(store, monkeypatch):
    admin = await _user(store, roles.ADMIN)
    app = _app(store, admin)
    _route_peer_to(monkeypatch, ASGITransport(app=app))
    async with _client(app) as c:
        r = await c.get("/api/admin/users/federated")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["peer_unreachable"] is False
    assert data["peer"] and all(u["node_region"] == "peer" for u in data["peer"])
    assert admin in {u["id"] for u in data["peer"]}, "对端（同库）应当列出这位管理员"


async def test_federated_list_degrades_when_peer_is_down(store, monkeypatch):
    admin = await _user(store, roles.ADMIN)

    def _down(request):
        raise httpx.ConnectError("peer down", request=request)

    _route_peer_to(monkeypatch, httpx.MockTransport(_down))
    async with _client(_app(store, admin)) as c:
        r = await c.get("/api/admin/users/federated")
    assert r.status_code == 200, r.text
    assert r.json()["peer_unreachable"] is True and r.json()["peer"] == []
