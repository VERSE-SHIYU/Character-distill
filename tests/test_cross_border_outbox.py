# -*- coding: utf-8 -*-
"""跨境发件箱（spec cross-border-outbox）：邀请码新增 / 删除 / 已使用、用户资料，失败自动重发。

事务发件箱的标准写法：改业务数据的**同一个事务**里写一条待发送记录（内容在写入时就定好），
后台按写入顺序发送，对端确认后删掉这条记录；没确认就一直留着、下一轮再发。

对端用 httpx 自带的 MockTransport 扮演（记录收到的请求，按用例决定回 200 还是失败），
业务写入走真 PG。**为什么用真 PG**：「同一个事务」「同一个码只能被用一次」只有真库答得了。
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

import peer_client
from conftest import PG_ENV, TEST_DATABASE_URL
from storage.base import InviteCodeUnavailable
from storage.postgres_store import PostgresStore

pytestmark = PG_ENV.skipif("跨境发件箱用例")

_OUTBOX_OPS = ("invite_create", "invite_delete", "invite_used", "user_profile")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("INTER_NODE_SECRET", "test-inter-node-secret-0123456789abcdef")
    monkeypatch.setenv("PEER_NODE_URL", "https://peer-node")


@pytest.fixture
def store(outbox_store):
    return outbox_store


def _peer(monkeypatch, decide=lambda req: 200):
    """对端：记下每个请求；`decide(request)` 返回状态码或抛异常。"""
    seen: list[httpx.Request] = []

    def _handler(request):
        seen.append(request)
        result = decide(request)
        if isinstance(result, Exception):
            raise result
        return httpx.Response(result)

    monkeypatch.setattr(
        peer_client, "_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(_handler), timeout=timeout))
    return seen


def _outbox_calls(seen, path_suffix):
    return [json.loads(r.content) for r in seen if r.url.path.endswith(path_suffix)]


async def _rows(store, op=None):
    async with await store._connect() as conn:
        rows = await conn.fetch(
            "SELECT op_type, target_id, payload FROM cross_border_outbox "
            "WHERE op_type = ANY($1::text[]) ORDER BY id",
            [op] if op else list(_OUTBOX_OPS))
    return [dict(r) for r in rows]


async def _resync(store):
    from cross_border_sync import _resync_once
    await _resync_once(store)


# ── 1. 写入即入队（同一事务），发出后删掉 ─────────────────────────────

async def test_new_invite_code_is_queued_then_sent_and_removed(store, monkeypatch):
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=True)
    rows = await _rows(store, "invite_create")
    assert [r["target_id"] for r in rows] == [code]
    assert json.loads(rows[0]["payload"]) == {"code": code, "created_by": "admin1"}

    seen = _peer(monkeypatch)
    await _resync(store)
    assert _outbox_calls(seen, "/invite-code/receive") == [{"code": code, "created_by": "admin1"}]
    assert await _rows(store, "invite_create") == [], "对端确认后这一条要删掉"


async def test_peer_down_keeps_the_row_and_next_round_delivers(store, monkeypatch):
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=True)
    _peer(monkeypatch, lambda req: httpx.ConnectError("down", request=req))
    await _resync(store)
    assert len(await _rows(store, "invite_create")) == 1, "没确认就不许删"

    seen = _peer(monkeypatch)
    await _resync(store)
    assert _outbox_calls(seen, "/invite-code/receive") == [{"code": code, "created_by": "admin1"}]
    assert await _rows(store, "invite_create") == []


# ── 2. 同一个码按写入顺序发：新增没送到，删除不许先到 ─────────────────

async def test_same_code_keeps_order_when_the_first_send_fails(store, monkeypatch):
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=True)
    assert await store.delete_invite_code(code, propagate=True) is True

    def _fail_create(req):
        return 500 if req.url.path.endswith("/invite-code/receive") else 200

    seen = _peer(monkeypatch, _fail_create)
    await _resync(store)
    assert _outbox_calls(seen, "/invite-code/delete") == [], (
        "新增还没送到，删除就先送了 —— 下一轮补发的新增会把已作废的码救活")

    seen = _peer(monkeypatch)
    await _resync(store)
    order = [r.url.path.rsplit("/", 1)[-1] for r in seen if "/invite-code/" in r.url.path]
    assert order == ["receive", "delete"], order
    assert await _rows(store) == []


# ── 3. 删除、批量删已使用 ─────────────────────────────────────────────

async def test_delete_used_invites_queues_each_deleted_code(store):
    admin = await store.create_user(_uid("u"), _uid("n"), "x")
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=False)
    await store.create_user(_uid("u"), _uid("n"), "x", invite_code=code)
    n = await store.delete_used_invites(propagate=True)
    assert n >= 1
    assert code in [r["target_id"] for r in await _rows(store, "invite_delete")]
    assert admin  # 只为造一个不相干的用户


# ── 4. 注册：建用户 + 占用邀请码 + 入队，一个事务 ─────────────────────

async def test_registration_claims_the_code_and_queues_profile_and_used(store):
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=False)
    uid, name = _uid("u"), _uid("n")
    await store.create_user(uid, name, "x", home_region="cn", invite_code=code)

    assert (await store.get_invite_code(code))["used_by"] == uid
    used = await _rows(store, "invite_used")
    assert [json.loads(r["payload"]) for r in used] == [{"code": code}], (
        "隐私政策 3.2(4)：邀请码同步不含使用者身份，只同步「已使用」")
    prof = await _rows(store, "user_profile")
    assert [json.loads(r["payload"]) for r in prof if r["target_id"] == uid] == [
        {"id": uid, "username": name, "home_region": "cn", "avatar_data": ""}
    ], "隐私政策 3.2(1)：资料只同步用户名、头像、所属地域（不含昵称）"


async def test_a_code_can_be_claimed_only_once_even_concurrently(store):
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=False)
    a, b = _uid("u"), _uid("u")
    results = await asyncio.gather(
        store.create_user(a, _uid("n"), "x", invite_code=code),
        store.create_user(b, _uid("n"), "x", invite_code=code),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, InviteCodeUnavailable)]
    assert len(ok) == 1 and len(refused) == 1, results
    loser = b if ok[0]["id"] == a else a
    assert await store.get_user_by_id(loser) is None, "码没占到，用户也不许留下（同一事务回滚）"


async def test_unknown_or_used_code_refuses_registration(store):
    with pytest.raises(InviteCodeUnavailable):
        await store.create_user(_uid("u"), _uid("n"), "x", invite_code=_uid("nope"))


# ── 5. 头像更新：只留最新一版 ─────────────────────────────────────────

async def test_avatar_updates_collapse_to_the_latest(store):
    uid = _uid("u")
    await store.create_user(uid, _uid("n"), "x", home_region="sg")
    await store.update_user_avatar(uid, "data:a")
    await store.update_user_avatar(uid, "data:b")
    prof = [r for r in await _rows(store, "user_profile") if r["target_id"] == uid]
    assert len(prof) == 1 and json.loads(prof[0]["payload"])["avatar_data"] == "data:b"


# ── 6. 接收端：已使用只改一次、不回传 ─────────────────────────────────

async def test_peer_used_marks_once_and_never_echoes(store):
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=False)
    assert await store.mark_invite_used_from_peer(code) is True
    assert await store.mark_invite_used_from_peer(code) is False
    assert (await store.get_invite_code(code))["used_by"] == "peer"
    assert await _rows(store) == [], "从对端收到的变更再入队就会回传给对端"


# ── 7. 路由层：注册 / 管理后台 / 接收端 ───────────────────────────────

def _auth_app(store, operator_id=None):
    from fastapi import FastAPI

    from deps import get_storage
    from routers import admin as A
    from routers import inter_node as N
    from routers.auth import get_current_user, router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(A.router)
    app.include_router(N.router)
    app.dependency_overrides[get_storage] = lambda: store
    if operator_id:
        async def _live():
            return await store.get_user_by_id(operator_id)
        app.dependency_overrides[get_current_user] = _live
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t")


@pytest.fixture
def _no_rate_limit(monkeypatch):
    import limiter as L
    monkeypatch.setattr(L.limiter, "enabled", False)


def _register_body(username, code):
    from legal_versions import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION
    return {"username": username, "password": "abcd1234", "invite_code": code,
            "agreed_terms_version": CURRENT_TERMS_VERSION,
            "agreed_privacy_version": CURRENT_PRIVACY_VERSION}


async def test_register_route_uses_the_transactional_path(store, _no_rate_limit):
    """注册走 create_user(invite_code=...) 一条事务：码被占、资料入队、用户建好。"""
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=False)
    name = _uid("n")
    async with _client(_auth_app(store)) as c:
        r = await c.post("/api/auth/register", json=_register_body(name, code))
    assert r.status_code == 200, r.text
    uid = (await store.get_user_by_username(name))["id"]
    assert (await store.get_invite_code(code))["used_by"] == uid
    assert [r["target_id"] for r in await _rows(store, "invite_used")] == [code]
    prof = [json.loads(r["payload"]) for r in await _rows(store, "user_profile") if r["target_id"] == uid]
    assert prof and prof[0]["home_region"] and isinstance(prof[0]["home_region"], str)


async def test_register_route_maps_a_lost_race_to_400(store, _no_rate_limit, monkeypatch):
    """路由前面的检查看到码没被用，写入时却被别人抢先占了 → 400，而不是 500。"""
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=False)
    real = store.get_invite_code

    async def _stale(c):  # 模拟检查时刻码还空着
        row = await real(c)
        return {**row, "used_by": None} if row else row

    await store.create_user(_uid("u"), _uid("n"), "x", invite_code=code)
    monkeypatch.setattr(store, "get_invite_code", _stale)
    async with _client(_auth_app(store)) as c:
        r = await c.post("/api/auth/register", json=_register_body(_uid("n"), code))
    assert r.status_code == 400 and "已被使用" in r.text, r.text


async def test_admin_invite_routes_queue_and_do_not_send_inline(store, monkeypatch):
    admin = await store.create_user(_uid("u"), _uid("n"), "x")
    await store.set_user_role(admin["id"], "admin")
    seen = _peer(monkeypatch)
    async with _client(_auth_app(store, admin["id"])) as c:
        r = await c.post("/api/admin/invite/generate", json={"count": 2})
        assert r.status_code == 200, r.text
        codes = [x["code"] for x in r.json()]
        assert (await c.delete(f"/api/admin/invite/{codes[0]}")).status_code == 200
    assert seen == [], "路由不许自己发：发送只由补发循环一处做"
    ops = [(r["op_type"], r["target_id"]) for r in await _rows(store)
           if r["op_type"].startswith("invite_")]   # 建管理员本身会入一条 user_profile，不在本条断言内
    assert ops == [("invite_create", codes[0]), ("invite_create", codes[1]),
                   ("invite_delete", codes[0])], ops


@pytest.mark.parametrize("path,payload", [
    ("/api/inter-node/invite-code/receive", lambda code: {"code": code, "created_by": "peer-admin"}),
    ("/api/inter-node/invite-code/used", lambda code: {"code": code}),
    ("/api/inter-node/invite-code/delete", lambda code: {"code": code}),
])
async def test_changes_received_from_the_peer_are_not_echoed(store, monkeypatch, path, payload):
    monkeypatch.setenv("INTER_NODE_SIGN_VERSION", "1")
    code = _uid("c")
    if not path.endswith("/receive"):
        await store.create_invite_code(code, "x", propagate=False)
    req = peer_client.build_request(path.replace("/api/inter-node", "/api/inter-node"), payload(code))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_auth_app(store))) as c:
        r = await c.send(req)
    assert r.status_code == 200, r.text
    assert await _rows(store) == [], "从对端收到的变更再入队就会回传给对端"


# ── 8. 存量补发（PG 032）：上线前已有的用户资料 ─────────────────────────
#
# 发件箱是这次才落地的，上线前就存在的用户从没入过队，对端永远看不到。
# 迁移只跑一次（账本），但**账本为空时全部迁移会重放**，故正文必须幂等。

_BACKFILL = "032_outbox_backfill.sql"


async def _seed_pre_backfill(store):
    """造「存量」：本地用户 ×2（其中一人用掉一个码）、远端资料 ×1。

    种子走 store 自己的写方法，它们顺带入队（注册会入队资料与「已使用」），所以末尾清一次
    发件箱 —— 本组用例只量**迁移**放进去的东西。返回 `(u1, u2, remote_id)`。
    """
    u1, u2, remote = _uid("u"), _uid("u"), _uid("u")
    code_local = _uid("c")
    await store.create_user(u1, _uid("n"), "x", home_region="cn-shenzhen")
    await store.create_invite_code(code_local, "admin1", propagate=False)
    await store.create_user(u2, _uid("n"), "x", home_region="cn-shenzhen",
                            invite_code=code_local)
    await store.upsert_remote_user_profile(remote, _uid("n"), "sg-singapore")
    async with await store._connect() as conn:
        await conn.execute("DELETE FROM cross_border_outbox")
    return u1, u2, remote


async def _queue_existing_profile(store, user_id: str, *, avatar: str) -> None:
    """直插一条待发资料 —— 模拟「注册 / 换头像时已经入队」的那一份。

    直插而不是走 `update_user_avatar`：那条路会把 `users.avatar_data` 一起改掉，于是
    「队里的新值」与「表里的旧快照」就没有差别，`DO NOTHING` 与 `DO UPDATE` 读数相同 ——
    本组用例要验的正是这两者的差别。
    """
    async with await store._connect() as conn:
        await conn.execute(
            """INSERT INTO cross_border_outbox (op_type, target_id, payload)
               VALUES ('user_profile', $1, $2)""",
            user_id,
            json.dumps({"id": user_id, "username": "queued", "home_region": "cn-shenzhen",
                        "avatar_data": avatar}, ensure_ascii=False),
        )


async def _replay_backfill(store) -> None:
    """把 032 从账本里划掉，再用**新实例**初始化 —— 只有这一份会重跑。

    新实例是必需的：同一个 store 的 `_ensure_initialized` 会因 `_initialized` 短路，
    迁移根本不再执行。
    """
    async with await store._connect() as conn:
        await conn.execute("DELETE FROM schema_migrations WHERE filename = $1", _BACKFILL)
    restarted = PostgresStore(TEST_DATABASE_URL)
    try:
        await restarted._ensure_initialized()
    finally:
        await restarted.close()


async def test_backfill_queues_every_local_user_profile_once(store):
    """存量补发：本机每个用户入队一条资料；对端同步来的资料一条都不发，队里已有的不覆盖。

    判据取「人队条数 == 本机用户数」而不是「至少两个」：多出来的一定来自
    `remote_user_profiles`（那是从对端收的资料，发回去就是回传），少一个就是漏发。
    """
    u1, u2, remote = await _seed_pre_backfill(store)
    await _queue_existing_profile(store, u1, avatar="data:newer")

    await _replay_backfill(store)

    rows = {r["target_id"]: json.loads(r["payload"])
            for r in await _rows(store, "user_profile")}
    async with await store._connect() as conn:
        local_users = await conn.fetchval("SELECT count(*) FROM users")
    assert len(rows) == local_users, (
        f"本机 {local_users} 个用户，队里却只有 {len(rows)} 条资料 —— 漏发或多发")
    assert remote not in rows, f"对端同步来的资料被发回去了（回传）：{remote}"
    assert rows[u2]["id"] == u2 and rows[u2]["home_region"] == "cn-shenzhen", rows[u2]
    assert rows[u2]["avatar_data"] == "", rows[u2]
    assert set(rows[u2]) == {"id", "username", "home_region", "avatar_data"}, (
        f"资料带上了四个字段以外的东西（政策 3.2(1) 不含昵称、注册时间）：{rows[u2]}")
    assert rows[u1]["avatar_data"] == "data:newer", (
        f"补发拿 users 里的旧快照盖掉了队里更新的资料：{rows[u1]!r} —— "
        "所以必须是 DO NOTHING 而不是 DO UPDATE")
