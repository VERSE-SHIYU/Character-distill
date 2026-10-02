# -*- coding: utf-8 -*-
"""用户资料走跨境 outbox 同步给对端（spec profile-outbox）。

注册 / 改头像在业务写入的同一事务里入队一行 `user_profile`；补发循环 `_resync_once`
发送时读最新资料、只发本节点地区的用户，对端 200 后按「id + 发出时的版本戳」删行。
迁移 033 把存量用户各入队一条（注册同步缺陷期间漏发的人由此补上）。

对端用 httpx 自带的 MockTransport 扮演；业务写入走真 PG。
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from conftest import PG_ENV
from cross_border_sync import _resync_once
from deps import get_storage
from routers.auth import router as auth_router
from storage.base import USER_PROFILE_OP

pytestmark = PG_ENV.skipif("资料 outbox 用例")

_HERE = "cn-shenzhen"
_THERE = "sg-singapore"
_SYNC_PATH = "/api/inter-node/user/sync"
_MIGRATION = Path(__file__).resolve().parents[1] / "storage/migrations_pg/033_backfill_profile_sync.sql"


def _uid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


@pytest.fixture
def store(outbox_store):
    return outbox_store


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import limiter as L

    monkeypatch.setattr(L.limiter, "enabled", False)
    monkeypatch.setenv("INTER_NODE_SECRET", "test-inter-node-secret-0123456789abcdef")
    monkeypatch.setenv("PEER_NODE_URL", "https://peer-node")
    monkeypatch.setenv("NODE_REGION", _HERE)


class Peer:
    """扮演对端：记录收到的资料；`status` 决定回什么；`on_request` 模拟发送途中的并发写。"""

    def __init__(self, monkeypatch, status: int = 200, on_request=None):
        self.sent: list[dict] = []
        self.status = status
        self._on_request = on_request
        real_client = httpx.AsyncClient
        peer = self

        async def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == _SYNC_PATH:
                peer.sent.append(json.loads(req.content))
                if peer._on_request is not None:
                    hook, peer._on_request = peer._on_request, None
                    await hook()
            return httpx.Response(peer.status)

        def _client(*_a, **kw):
            return real_client(transport=httpx.MockTransport(handler), timeout=kw.get("timeout"))

        monkeypatch.setattr(httpx, "AsyncClient", _client)

    def for_user(self, user_id: str) -> list[dict]:
        return [b for b in self.sent if b.get("id") == user_id]


async def _profile_rows(store, user_id: str) -> list[dict]:
    async with await store._connect() as conn:
        rows = await conn.fetch(
            "SELECT id, payload FROM cross_border_outbox WHERE op_type = $1 AND target_id = $2",
            USER_PROFILE_OP, user_id)
    return [dict(r) for r in rows]


async def _new_user(store, region: str = _HERE) -> str:
    uid = _uid("u")
    await store.create_user(uid, _uid("n"), "hash", home_region=region)
    return uid


# ── 注册：同一事务入队，循环发出字符串地区 ─────────────────────────────────

async def test_registration_queues_and_the_loop_sends_the_profile(store, monkeypatch, caplog):
    """注册后资料进 outbox；下一轮补发把它发给对端，地区是字符串、行随之删除。"""
    from legal_versions import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_storage] = lambda: store
    code = _uid("c")
    await store.create_invite_code(code, "admin1", propagate=False)
    name = _uid("n")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t") as c:
        r = await c.post("/api/auth/register", json={
            "username": name, "password": "abcd1234", "invite_code": code,
            "agreed_terms_version": CURRENT_TERMS_VERSION,
            "agreed_privacy_version": CURRENT_PRIVACY_VERSION,
        })
    assert r.status_code == 200, r.text
    uid = (await store.get_user_by_username(name))["id"]
    assert len(await _profile_rows(store, uid)) == 1, "注册没有把资料入队"

    peer = Peer(monkeypatch)
    caplog.set_level(logging.INFO)
    await _resync_once(store)

    got = peer.for_user(uid)
    assert len(got) == 1, f"资料没发出去；日志：{[x.getMessage() for x in caplog.records]}"
    # 字段集就是隐私政策 3.2(1) 的清单（含账号状态）；多一个、少一个都要先改政策。
    assert got[0] == {"id": uid, "username": name, "home_region": _HERE, "avatar_data": "",
                      "is_disabled": False}
    assert await _profile_rows(store, uid) == [], "对端已确认，这一行必须删掉"


async def test_failed_create_user_leaves_no_profile_row(store):
    """建用户失败（用户名重复）整笔回滚：不能留下指向不存在用户的待发行。"""
    name = _uid("n")
    await store.create_user(_uid("u"), name, "hash", home_region=_HERE)
    loser = _uid("u")
    with pytest.raises(ValueError):
        await store.create_user(loser, name, "hash", home_region=_HERE)
    assert await _profile_rows(store, loser) == [], "建用户失败却留下了待发行"


# ── 改头像：换版本戳；发送途中又改，新资料不丢 ──────────────────────────────

async def test_avatar_change_requeues_and_unknown_user_does_not(store):
    uid = await _new_user(store)
    [before] = await _profile_rows(store, uid)
    await store.update_user_avatar(uid, "data:new")
    [after] = await _profile_rows(store, uid)
    assert after["payload"] != before["payload"], "资料变了却没换版本戳"

    ghost = _uid("u")
    await store.update_user_avatar(ghost, "data:x")
    assert await _profile_rows(store, ghost) == [], "不存在的用户不该入队"


async def test_change_during_send_is_sent_next_round(store, monkeypatch):
    """发送途中头像又改了：这一轮确认后不许删行，下一轮发出新头像。"""
    uid = await _new_user(store)

    async def change_avatar():
        await store.update_user_avatar(uid, "data:v2")

    peer = Peer(monkeypatch, on_request=change_avatar)
    await _resync_once(store)
    assert [b["avatar_data"] for b in peer.for_user(uid)] == [""]
    assert len(await _profile_rows(store, uid)) == 1, "发送途中的新资料被这一轮的确认一起删掉了"

    await _resync_once(store)
    assert [b["avatar_data"] for b in peer.for_user(uid)] == ["", "data:v2"], "对端没收到最新头像"
    assert await _profile_rows(store, uid) == [], "按发出时的版本戳删不掉已确认的行"


# ── 发送方的判断：失败留行、非本区与已删用户丢行 ────────────────────────────

async def test_peer_rejection_keeps_the_row_and_logs_it(store, monkeypatch, caplog):
    uid = await _new_user(store)
    Peer(monkeypatch, status=500)
    caplog.set_level(logging.ERROR)
    await _resync_once(store)
    assert len(await _profile_rows(store, uid)) == 1, "对端没确认，这一行不能丢"
    out = "\n".join(x.getMessage() for x in caplog.records)
    assert uid in out and "500" in out, f"失败日志缺用户或状态码：{out!r}"


async def test_user_not_homed_here_is_dropped_unsent(store, monkeypatch):
    """镜像自对端的账号（如演示账号）：本节点不对外宣告，行直接结束。"""
    uid = await _new_user(store, region=_THERE)
    peer = Peer(monkeypatch)
    await _resync_once(store)
    assert peer.for_user(uid) == [], "非本区用户被本节点宣告出去了"
    assert await _profile_rows(store, uid) == []


async def test_deleted_user_is_dropped_unsent(store, monkeypatch):
    uid = await _new_user(store)
    async with await store._connect() as conn:
        await conn.execute("DELETE FROM user_secrets WHERE user_id = $1", uid)
        await conn.execute("DELETE FROM users WHERE id = $1", uid)
    peer = Peer(monkeypatch)
    await _resync_once(store)
    assert peer.for_user(uid) == []
    assert await _profile_rows(store, uid) == [], "用户已不存在，这一行永远发不出去，必须结束"


async def test_acked_row_with_null_payload_is_removed(store, monkeypatch):
    """payload 列可空：确认后按「发出时的 payload」删行，NULL 也要删得掉。"""
    async with await store._connect() as conn:
        await conn.execute(
            "INSERT INTO cross_border_outbox (op_type, target_id, payload) VALUES ('card_delete', $1, NULL)",
            _uid("card"))
    Peer(monkeypatch)
    await _resync_once(store)
    async with await store._connect() as conn:
        left = await conn.fetchval("SELECT count(*) FROM cross_border_outbox")
    assert left == 0, "确认过的 NULL payload 行没删掉"


# ── 迁移 033：存量补发 ─────────────────────────────────────────────────────

async def test_backfill_migration_sends_every_local_user_once(store, monkeypatch):
    """存量用户（入队之前就建好的）经 033 入队，一轮全部发出；非本区的不发。"""
    local = [await _new_user(store) for _ in range(3)]
    mirror = await _new_user(store, region=_THERE)
    async with await store._connect() as conn:
        await conn.execute("DELETE FROM cross_border_outbox")   # 模拟缺陷期：从没入过队
        await conn.execute(_MIGRATION.read_text(encoding="utf-8"))
        # 共用测试库里还有别的用例建的用户：只留本用例的行，一轮的上限（100）才测得准
        await conn.execute(
            "DELETE FROM cross_border_outbox WHERE NOT (target_id = ANY($1::text[]))",
            local + [mirror])
    for uid in local + [mirror]:
        assert len(await _profile_rows(store, uid)) == 1, f"{uid} 没被 033 入队"

    peer = Peer(monkeypatch)
    await _resync_once(store)
    for uid in local:
        assert len(peer.for_user(uid)) == 1, f"{uid} 没补发出去"
        assert await _profile_rows(store, uid) == []
    assert peer.for_user(mirror) == []


async def test_backfill_migration_keeps_a_pending_row_as_is(store):
    uid = await _new_user(store)
    [before] = await _profile_rows(store, uid)
    async with await store._connect() as conn:
        await conn.execute(_MIGRATION.read_text(encoding="utf-8"))
    assert await _profile_rows(store, uid) == [before], "033 改动了已有的待发行"


async def test_signing_failure_keeps_the_row_and_the_loop_alive(store, monkeypatch, caplog):
    """签名抛错：这一行留到下一轮、记错误日志；`_resync_once` 不许抛出（否则补发任务整个停掉）。"""
    import peer_client

    def boom(_body):
        raise TypeError("cannot sign")

    uid = await _new_user(store)
    # 打在 `peer_client` 的名字上：它 `from inter_node_auth import create_auth_header`，
    # 改 `inter_node_auth` 模块属性碰不到它（签名改用 v1 时才走这一支，缺省即 v1）
    monkeypatch.setattr(peer_client, "create_auth_header", boom)
    caplog.set_level(logging.ERROR)
    try:
        await _resync_once(store)
    except Exception as exc:
        raise AssertionError(f"签名失败把补发循环带停了：{exc!r}") from exc
    assert len(await _profile_rows(store, uid)) == 1
    assert any("cannot sign" in x.getMessage() for x in caplog.records)


# ── 账号状态随资料发出（spec cross-region-user-search，方案 A）─────────────────

_MIGRATION_034 = (Path(__file__).resolve().parents[1]
                  / "storage/migrations_pg/034_remote_profile_disabled.sql")


async def test_disable_and_enable_requeue_and_send_the_status(store, monkeypatch):
    uid = await _new_user(store)
    peer = Peer(monkeypatch)
    await _resync_once(store)                        # 注册那一行先发掉
    await store.set_user_disabled(uid, True)
    assert len(await _profile_rows(store, uid)) == 1, "禁用没有入队"
    await _resync_once(store)
    await store.set_user_disabled(uid, False)
    await _resync_once(store)
    assert [b["is_disabled"] for b in peer.for_user(uid)] == [False, True, False]


async def test_disable_unknown_user_queues_nothing(store):
    ghost = _uid("u")
    with pytest.raises(ValueError):
        await store.set_user_disabled(ghost, True)
    assert await _profile_rows(store, ghost) == [], "不存在的用户不该入队"


async def test_ban_requeues_the_profile(store):
    uid = await _new_user(store)
    async with await store._connect() as conn:
        await conn.execute("DELETE FROM cross_border_outbox")
    await store.ban_user_and_contents(uid, "admin1")
    assert len(await _profile_rows(store, uid)) == 1, "封禁没有入队"


async def test_034_backfill_queues_disabled_users_only(store):
    off = await _new_user(store)
    on = await _new_user(store)
    async with await store._connect() as conn:
        await conn.execute("UPDATE users SET is_disabled = 1 WHERE id = $1", off)
        await conn.execute("DELETE FROM cross_border_outbox")   # 模拟存量：从没入过队
        await conn.execute(_MIGRATION_034.read_text(encoding="utf-8"))
    assert len(await _profile_rows(store, off)) == 1, "已禁用用户没被 034 入队"
    assert await _profile_rows(store, on) == [], "034 不该碰正常用户"
