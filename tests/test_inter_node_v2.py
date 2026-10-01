# -*- coding: utf-8 -*-
"""节点间签名 v2（RFC 9421，spec inter-node-v2）：发送端 → 接收端的整条链路。

「对端」就是同一个 app 的 `/api/inter-node/*` 路由：请求由 `peer_client.build_request`
构造并签名（与生产同一个出口），经 ASGITransport 直接送进 app，不开端口、不出网。
改签名、改地址、改请求体这些「攻击」都在请求构造好之后动手，等价于链路上的篡改。

**为什么用真 PG**：防重放靠 nonce 表的主键冲突，只有真库答得了。
"""
from __future__ import annotations

import datetime
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from http_message_signatures import HTTPMessageSigner, algorithms

import inter_node_auth as A
import peer_client
from conftest import PG_ENV, TEST_DATABASE_URL
from core.fingerprint import key_fingerprint
from deps import get_storage
from routers import inter_node as N
from storage.postgres_store import PostgresStore

pytestmark = PG_ENV.skipif("节点间签名 v2 用例")

SECRET = "test-inter-node-secret-0123456789abcdef"
PREV = "previous-inter-node-secret-0123456789ab"
READ_PATH = "/api/inter-node/admin/users"          # 只读接口：验签过了就回列表


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("INTER_NODE_SECRET", SECRET)
    monkeypatch.delenv("INTER_NODE_SECRET_PREV", raising=False)
    monkeypatch.setenv("PEER_NODE_URL", "https://peer-node")
    monkeypatch.setenv("INTER_NODE_SIGN_VERSION", "2")
    monkeypatch.delenv("INTER_NODE_ACCEPT_V1", raising=False)
    monkeypatch.setenv("INTER_NODE_SELF_HOST", "peer-node")   # 接收端 = peer-node 这台


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


def _app(store) -> FastAPI:
    app = FastAPI()
    app.include_router(N.router)
    app.dependency_overrides[get_storage] = lambda: store
    return app


async def _send(store, request: httpx.Request) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(store))) as c:
        return await c.send(request)


def _resend(req: httpx.Request, *, url=None, content=None, headers=None) -> httpx.Request:
    """同一份签名头，换地址 / 请求体 / 头 —— 模拟链路上的篡改或挪用。"""
    h = dict(req.headers)
    h.update(headers or {})
    h.pop("content-length", None)
    return httpx.Request(req.method, url or req.url, content=req.content if content is None else content,
                         headers=h)


def _signed_at(path: str, payload: dict, *, created: int, secret: str = SECRET,
               components=A.REQUIRED_COMPONENTS) -> httpx.Request:
    """手工按指定 created / 密钥 / 覆盖清单签一个请求（生产出口不开这些口子）。"""
    body = json.dumps(payload).encode()
    req = httpx.Request("POST", f"https://peer-node{path}", content=body,
                        headers={"Content-Type": "application/json"})
    req.headers["Content-Digest"] = A.content_digest(body)
    kid = key_fingerprint(secret)
    HTTPMessageSigner(signature_algorithm=algorithms.HMAC_SHA256,
                      key_resolver=A._KeyResolver({kid: secret.encode()})).sign(
        req, key_id=kid, covered_component_ids=components, nonce=uuid.uuid4().hex,
        created=datetime.datetime.fromtimestamp(created), include_alg=True)
    return req


def _now() -> int:
    return int(datetime.datetime.now().timestamp())


# ── 1. 正路与发送端开关 ─────────────────────────────────────────────────

async def test_v2_signed_request_is_accepted(store):
    r = await _send(store, peer_client.build_request(READ_PATH, {"request": "admin_users"}))
    assert r.status_code == 200, r.text


async def test_v2_write_carries_non_ascii_body_intact(store):
    """摘要算在原始字节上：中文正文原样落库。"""
    uid = f"u_{uuid.uuid4().hex[:8]}"
    msg = {"id": f"m_{uuid.uuid4().hex[:8]}", "sender_id": uid, "receiver_id": uid,
           "content": "你好，跨境", "created_at": "2026-09-30 00:00:00"}
    r = await _send(store, peer_client.build_request("/api/inter-node/dm/receive", msg))
    assert r.status_code == 200, r.text
    assert r.json()["message"]["content"] == "你好，跨境"


def test_sender_version_switch(monkeypatch):
    monkeypatch.setenv("INTER_NODE_SIGN_VERSION", "1")
    v1 = peer_client.build_request(READ_PATH, {"a": 1})
    assert v1.headers["Authorization"].startswith("HMAC-SHA256")
    assert "Signature-Input" not in v1.headers
    monkeypatch.setenv("INTER_NODE_SIGN_VERSION", "2")
    v2 = peer_client.build_request(READ_PATH, {"a": 1})
    assert "Signature-Input" in v2.headers and "Authorization" not in v2.headers


def test_sender_refuses_plain_http(monkeypatch):
    monkeypatch.setenv("PEER_NODE_URL", "http://peer-node")
    with pytest.raises(peer_client.PeerConfigError):
        peer_client.build_request(READ_PATH, {"a": 1})


# ── 2. 挪用与篡改 ─────────────────────────────────────────────────────

async def test_path_swap_is_rejected(store):
    """为 /invite-code/delete 签的请求，改发到 /invite-code/receive。"""
    req = peer_client.build_request("/api/inter-node/invite-code/delete", {"code": "c"})
    r = await _send(store, _resend(req, url="https://peer-node/api/inter-node/invite-code/receive"))
    assert r.status_code == 401, r.text


async def test_authority_swap_is_rejected(store, monkeypatch):
    """发给 peer-node 的请求被原样送到另一台（两台共用一把密钥），Host 头仍写 peer-node。

    接收端按本节点配置的对外域名算 @authority，所以拒；若信了请求自带的 Host 就会放行。
    """
    req = peer_client.build_request(READ_PATH, {"request": "admin_users"})
    monkeypatch.setenv("INTER_NODE_SELF_HOST", "other-node")      # 这次收到请求的是另一台
    r = await _send(store, _resend(req))
    assert r.status_code == 401, r.text


async def test_v2_refused_when_self_host_unset(store, monkeypatch):
    monkeypatch.delenv("INTER_NODE_SELF_HOST")
    r = await _send(store, peer_client.build_request(READ_PATH, {"request": "admin_users"}))
    assert r.status_code == 401 and "INTER_NODE_SELF_HOST" in r.text, r.text


async def test_body_swap_with_stale_digest_is_rejected(store):
    req = peer_client.build_request(READ_PATH, {"request": "admin_users"})
    r = await _send(store, _resend(req, content=b'{"request":"other"}'))
    assert r.status_code == 401 and "Content-Digest" in r.text, r.text


async def test_body_swap_with_recomputed_digest_is_rejected(store):
    body = b'{"request":"other"}'
    req = peer_client.build_request(READ_PATH, {"request": "admin_users"})
    r = await _send(store, _resend(req, content=body,
                                   headers={"Content-Digest": A.content_digest(body)}))
    assert r.status_code == 401, r.text


async def test_replay_is_rejected(store):
    req = peer_client.build_request(READ_PATH, {"request": "admin_users"})
    assert (await _send(store, _resend(req))).status_code == 200
    r = await _send(store, _resend(req))
    assert r.status_code == 401 and "重放" in r.text, r.text


async def test_missing_required_component_is_rejected(store):
    req = _signed_at(READ_PATH, {"request": "admin_users"}, created=_now(),
                     components=("content-digest",))
    r = await _send(store, req)
    assert r.status_code == 401 and "必需组件" in r.text, r.text


# ── 3. 时间窗：按实测时钟差按比例钉住 ─────────────────────────────────
#
# 2026-09-30 实测两台读数差 8 秒。容忍必须明显大于它（库缺省 5 秒会误拒），又不能
# 大到让截获的请求长期可用：钉住「领先 20 秒放行、领先 40 秒拒绝、60 秒前签的拒绝」。

@pytest.mark.parametrize("offset,expected", [(20, 200), (-20, 200), (40, 401), (-60, 401)])
async def test_time_window(store, offset, expected):
    req = _signed_at(READ_PATH, {"request": "admin_users"}, created=_now() + offset)
    r = await _send(store, req)
    assert r.status_code == expected, (offset, r.text)


# ── 4. 密钥轮换 ────────────────────────────────────────────────────────

async def test_unknown_key_is_rejected(store):
    req = _signed_at(READ_PATH, {"request": "admin_users"}, created=_now(), secret=PREV)
    assert (await _send(store, req)).status_code == 401


async def test_previous_key_is_accepted_during_rotation(store, monkeypatch):
    monkeypatch.setenv("INTER_NODE_SECRET_PREV", PREV)
    req = _signed_at(READ_PATH, {"request": "admin_users"}, created=_now(), secret=PREV)
    r = await _send(store, req)
    assert r.status_code == 200, r.text


# ── 5. v1 双收与停用 ───────────────────────────────────────────────────

async def test_v1_still_accepted_by_default(store, monkeypatch):
    monkeypatch.setenv("INTER_NODE_SIGN_VERSION", "1")
    r = await _send(store, peer_client.build_request(READ_PATH, {"request": "admin_users"}))
    assert r.status_code == 200, r.text


async def test_v1_rejected_once_retired(store, monkeypatch):
    monkeypatch.setenv("INTER_NODE_SIGN_VERSION", "1")
    monkeypatch.setenv("INTER_NODE_ACCEPT_V1", "0")
    r = await _send(store, peer_client.build_request(READ_PATH, {"request": "admin_users"}))
    assert r.status_code == 401 and "v1" in r.text, r.text


# ── 6. 每个接收端都过验签（路由表现取，不手写名单） ────────────────────

async def test_every_inter_node_route_requires_a_signature(store):
    paths = sorted(r.path for r in N.router.routes
                   if isinstance(r, APIRoute) and "POST" in r.methods)
    assert len(paths) >= 10, paths
    open_ = []
    for p in paths:
        req = httpx.Request("POST", f"https://peer-node{p}", json={"id": "x", "code": "x",
                                                                    "target_id": "x"})
        r = await _send(store, req)
        if r.status_code != 401:
            open_.append((p, r.status_code))
    assert open_ == [], f"不带签名也进得去：{open_}"


# ── 7. nonce 表自清 ────────────────────────────────────────────────────

async def test_claim_prunes_rows_older_than_the_window(store):
    old = f"old_{uuid.uuid4().hex}"
    async with await store._connect() as conn:
        await conn.execute(
            "INSERT INTO inter_node_nonces (nonce, created_at) "
            "VALUES ($1, CURRENT_TIMESTAMP - interval '10 minutes')", old)
    assert await store.claim_inter_node_nonce(uuid.uuid4().hex, keep_seconds=120) is True
    async with await store._connect() as conn:
        left = await conn.fetchval("SELECT COUNT(*) FROM inter_node_nonces WHERE nonce = $1", old)
    assert left == 0
    assert A.NONCE_KEEP_SECONDS >= (A.MAX_AGE + A.CLOCK_SKEW).total_seconds(), (
        "nonce 保留时长短于有效期 + 时钟偏差：窗口内的重放会因为登记被清掉而放过")
