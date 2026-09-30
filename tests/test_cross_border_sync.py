"""Tests for cross_border_sync forwarding functions.

Tests cover both forward_dm_to_peer and forward_card_to_peer.
Uses monkeypatch for env vars and unittest.mock for httpx so no real
network calls are made.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import PG_ENV

# 这三条要真 PG：断言的是**表里那一行**还在不在，而 `get_pending_delete_propagations`
# 只看得见 `synced = 0` 的行 —— 用它当仪器的话，「行被删掉」与「行被标成 synced=1」
# 读数完全一样，正好放走本条要抓的那个变异。
_pg = PG_ENV.skipif("跨境删除 outbox 用例")


@pytest.fixture(autouse=True)
def _ensure_no_env_leak(monkeypatch):
    """Isolate env vars per test."""
    monkeypatch.delenv("PEER_NODE_URL", raising=False)
    monkeypatch.setenv("INTER_NODE_SECRET", "test-inter-node-secret-0123456789abcdef")


def _peer(monkeypatch, *, status=200, error=None):
    """把 `peer_client` 的出站请求改走 httpx 自带的 MockTransport，返回收到的请求列表。

    发送出口只有 `peer_client.post_to_peer` 一处（其下 `_client` 是唯一建客户端处），
    所以打桩打在这里，不去 patch 全局 `httpx.AsyncClient`。
    """
    import httpx

    import peer_client

    seen: list[httpx.Request] = []

    def _handler(request):
        seen.append(request)
        if error is not None:
            raise error
        return httpx.Response(status)

    monkeypatch.setattr(
        peer_client, "_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(_handler), timeout=timeout),
    )
    return seen


def _json(request) -> dict:
    return json.loads(request.content)


@pytest.mark.parametrize("status_code,expected_marked", [
    (200, True),
    (401, False),
    (500, False),
])
async def test_forward_dm_to_peer(monkeypatch, status_code, expected_marked):
    """forward_dm_to_peer returns True only on 200, and caller marks synced."""
    from cross_border_sync import forward_dm_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "https://sg-node:7860")
    seen = _peer(monkeypatch, status=status_code)

    msg = {
        "id": "test123",
        "sender_id": "user_a",
        "receiver_id": "user_b",
        "content": "hello",
        "created_at": "2026-06-26 08:00:00",
    }
    ok = await forward_dm_to_peer(msg, MagicMock())

    assert ok is expected_marked
    assert len(seen) == 1
    assert seen[0].url.path == "/api/inter-node/dm/receive"
    assert _json(seen[0]) == msg
    assert seen[0].headers["Authorization"].startswith("HMAC-SHA256")


async def test_forward_dm_to_peer_no_peer_url():
    """No PEER_NODE_URL => no-op, returns False."""
    from cross_border_sync import forward_dm_to_peer

    ok = await forward_dm_to_peer({"id": "x"}, MagicMock())
    assert ok is False


async def test_forward_dm_to_peer_empty_peer_url(monkeypatch):
    """Empty PEER_NODE_URL => no-op, returns False."""
    from cross_border_sync import forward_dm_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "")
    ok = await forward_dm_to_peer({"id": "x"}, MagicMock())
    assert ok is False


async def test_forward_dm_to_peer_connection_error(monkeypatch):
    """Connection error => returns False (message not lost)."""
    import httpx

    from cross_border_sync import forward_dm_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "https://sg-node:7860")
    _peer(monkeypatch, error=httpx.ConnectError("down"))
    msg = {
        "id": "err123",
        "sender_id": "user_a",
        "receiver_id": "user_b",
        "content": "hello",
        "created_at": "2026-06-26 08:00:00",
    }
    ok = await forward_dm_to_peer(msg, MagicMock())
    assert ok is False


# ── forward_card_to_peer tests ──────────────────────────────────────────

CARD_FIXTURE = {
    "id": "card001",
    "user_id": "author_x",
    "name": "Test Card",
    "card_json": '{"title":"hello"}',
    "avatar_data": "data:image/png;base64,abc",
    "visibility": "public",
    "market_description": "A test card",
    "market_tags": "fantasy",
    "created_at": "2026-06-25 12:00:00",
}


@pytest.mark.parametrize("status_code,expected_marked", [
    (200, True),
    (401, False),
    (500, False),
])
async def test_forward_card_to_peer(monkeypatch, status_code, expected_marked):
    """forward_card_to_peer returns True only on 200."""
    from cross_border_sync import forward_card_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "https://sg-node:7860")
    seen = _peer(monkeypatch, status=status_code)

    storage = MagicMock()
    storage.get_user_by_id = AsyncMock(return_value={"home_region": "sg"})
    ok = await forward_card_to_peer(CARD_FIXTURE, storage)

    assert ok is expected_marked
    assert len(seen) == 1
    assert seen[0].url.path == "/api/inter-node/card/receive"
    body = _json(seen[0])
    # Payload must have all expected fields; text_id removed, origin_region added
    assert body["id"] == CARD_FIXTURE["id"]
    assert body["user_id"] == CARD_FIXTURE["user_id"]
    assert body["origin_region"] == "sg"
    assert body["name"] == CARD_FIXTURE["name"]
    assert body["card_json"] == CARD_FIXTURE["card_json"]
    assert body["visibility"] == CARD_FIXTURE["visibility"]
    assert "text_id" not in body
    assert seen[0].headers["Authorization"].startswith("HMAC-SHA256")


async def test_forward_card_to_peer_no_peer_url():
    """No PEER_NODE_URL => no-op, returns False."""
    from cross_border_sync import forward_card_to_peer

    ok = await forward_card_to_peer(CARD_FIXTURE, MagicMock())
    assert ok is False


async def test_forward_card_to_peer_empty_peer_url(monkeypatch):
    """Empty PEER_NODE_URL => no-op, returns False."""
    from cross_border_sync import forward_card_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "")
    ok = await forward_card_to_peer(CARD_FIXTURE, MagicMock())
    assert ok is False


async def test_forward_card_to_peer_connection_error(monkeypatch):
    """Connection error => returns False (card not lost)."""
    import httpx

    from cross_border_sync import forward_card_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "https://sg-node:7860")
    _peer(monkeypatch, error=httpx.ConnectError("down"))
    storage = MagicMock()
    storage.get_user_by_id = AsyncMock(return_value=None)
    ok = await forward_card_to_peer(CARD_FIXTURE, storage)
    assert ok is False


# ── forward_outbox_to_peer tests ────────────────────────────────────────

async def test_forward_outbox_to_peer_logs_status_on_non_200(monkeypatch, caplog):
    """对端回非 200 时，日志里必须留下 op_type / target_id / 状态码。

    只返回 False 而不留痕的话，线上「删不掉、也传不出去」这件事在日志里完全不可见 ——
    运维只能看到对端数据没被删，查不出是哪一条、卡在哪个状态码上。

    断言走 `caplog` 而不是 stdout（spec-119）：这条要求的意义正是「面板上看得见」，
    而容器 stdout 到不了面板。
    """
    from cross_border_sync import forward_outbox_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "https://sg-node:7860")
    caplog.set_level(logging.ERROR)
    _peer(monkeypatch, status=503)

    ok = await forward_outbox_to_peer("card_delete", "card-xyz", "")

    assert ok is False
    out = "\n".join(r.getMessage() for r in caplog.records)
    assert "card_delete" in out, f"日志里没有 op_type：{out!r}"
    assert "card-xyz" in out, f"日志里没有 target_id：{out!r}"
    assert "503" in out, f"日志里没有状态码：{out!r}"


async def test_forward_outbox_to_peer_logs_exception(monkeypatch, caplog):
    """连不上对端时，同样要留下 op_type / target_id（异常本身另附）。"""
    from cross_border_sync import forward_outbox_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "https://sg-node:7860")
    caplog.set_level(logging.ERROR)
    _peer(monkeypatch, error=RuntimeError("boom-unreachable"))

    ok = await forward_outbox_to_peer("user_purge", "usr-abc", "")

    assert ok is False
    out = "\n".join(r.getMessage() for r in caplog.records)
    assert "user_purge" in out, f"日志里没有 op_type：{out!r}"
    assert "usr-abc" in out, f"日志里没有 target_id：{out!r}"
    assert "boom-unreachable" in out, f"日志里没有异常信息：{out!r}"


# ── _resync_once：一轮补发的边界 ─────────────────────────────────────────

@pytest.fixture
def pg_store(outbox_store):
    return outbox_store


async def _outbox_row(store, row_id: int):
    """按 id 直读那一行（不经过 `get_pending_delete_propagations`，见文件头说明）。"""
    async with await store._connect() as conn:
        return await conn.fetchrow(
            "SELECT id, synced FROM cross_border_delete_outbox WHERE id = $1", row_id)


async def _seed_one(store, op_type: str = "card_delete", target: str = "card-1") -> int:
    await store.enqueue_delete_propagation(op_type, target)
    pending = await store.get_pending_delete_propagations()
    assert len(pending) == 1, f"种子没落库，用例测不到东西：{pending}"
    return pending[0]["id"]


@_pg
async def test_delete_resync_removes_row_after_ack(pg_store, monkeypatch):
    """对端确认后，这一行从表里消失 —— 不是标成 synced=1 后永久驻留。"""
    from cross_border_sync import _resync_once

    monkeypatch.setenv("PEER_NODE_URL", "http://sg-node:7860")
    row_id = await _seed_one(pg_store)

    with patch("cross_border_sync.forward_outbox_to_peer", AsyncMock(return_value=True)):
        await _resync_once(pg_store)

    assert await _outbox_row(pg_store, row_id) is None, (
        "对端已确认，这一行必须被删除；还在表里就说明只是被标了 synced=1，"
        "而 synced=1 的行全仓没有任何读者 —— 表会一直涨")


@_pg
async def test_delete_resync_keeps_row_without_ack(pg_store, monkeypatch):
    """对端没确认时，这一行必须原样留在待办里（synced 仍为 0）。"""
    from cross_border_sync import _resync_once

    monkeypatch.setenv("PEER_NODE_URL", "http://sg-node:7860")
    row_id = await _seed_one(pg_store, "user_purge", "usr-1")

    with patch("cross_border_sync.forward_outbox_to_peer", AsyncMock(return_value=False)):
        await _resync_once(pg_store)

    row = await _outbox_row(pg_store, row_id)
    assert row is not None, "对端没确认，这一行不能丢 —— 丢了这次删除就永远传不出去"
    assert row["synced"] == 0


@_pg
async def test_delete_resync_survives_card_query_failure(pg_store, monkeypatch):
    """卡片查询抛异常时，删除补发照常执行 —— 两件事互不相关。"""
    from cross_border_sync import _resync_once

    monkeypatch.setenv("PEER_NODE_URL", "http://sg-node:7860")
    row_id = await _seed_one(pg_store, "dm_retract", "dm-1")

    with patch.object(pg_store, "get_unsynced_cross_border_cards_unscoped",
                      AsyncMock(side_effect=RuntimeError("card query boom"))), \
            patch("cross_border_sync.forward_outbox_to_peer", AsyncMock(return_value=True)):
        await _resync_once(pg_store)

    assert await _outbox_row(pg_store, row_id) is None, (
        "卡片查询失败把删除补发一起带走了 —— 删除那段被嵌在卡片的 else 里，"
        "卡片查不出来时整段不执行")


# ── restore_card：恢复时的跨境撤销 ──────────────────────────────────────

_CARD = "card-81b-restore"


@pytest.fixture
async def pg_card(pg_store):
    """一张公开、已同步的卡；用例结束后删掉，免得下次跑带着上一轮的残骸。"""
    async with await pg_store._connect() as conn:
        await conn.execute(
            """INSERT INTO cards (id, name, card_json, user_id, visibility, cross_border_synced)
               VALUES ($1, 'restore-target', '{}', 'user-81b', 'public', 1)
               ON CONFLICT (id) DO UPDATE
                   SET deleted_at = NULL, cross_border_synced = 1, visibility = 'public'""",
            _CARD,
        )
    yield _CARD
    async with await pg_store._connect() as conn:
        await conn.execute("DELETE FROM cards WHERE id = $1", _CARD)
        await conn.execute(
            "DELETE FROM cross_border_delete_outbox WHERE target_id = $1", _CARD)


async def _queued_card_deletes(store) -> list[dict]:
    """直读 outbox 里这张卡的 `card_delete` 行 —— 不经 pending 视图，见文件头说明。"""
    async with await store._connect() as conn:
        rows = await conn.fetch(
            """SELECT id, synced FROM cross_border_delete_outbox
               WHERE op_type = 'card_delete' AND target_id = $1""",
            _CARD,
        )
    return [dict(r) for r in rows]


@_pg
async def test_restore_card_drops_queued_delete(pg_card, pg_store):
    """恢复一张还在排队等删除的公开卡，那条删除必须被撤销。

    不撤的话，60 秒后的那一轮补发会照发不误 —— 对端把用户刚刚恢复的卡删掉，
    而且对端的删除是硬删，用户看不到任何回退的迹象。
    """
    await pg_store.delete_card(_CARD)
    assert len(await _queued_card_deletes(pg_store)) == 1, "种子没入队，用例测不到东西"

    await pg_store.restore_card(_CARD)

    assert await _queued_card_deletes(pg_store) == [], (
        "恢复后那条 card_delete 还在 outbox 里 —— 下一轮补发会把刚恢复的卡删到对端去")
    pending = await pg_store.get_pending_delete_propagations()
    assert not any(r["target_id"] == _CARD for r in pending), (
        f"待发队列里还有这张卡的删除：{pending}")


@_pg
async def test_restore_card_requeues_card_for_forwarding(pg_card, pg_store):
    """恢复一张 already-synced 的公开卡后，它必须重新进入待转发队列。

    同步标记不归零的话，补发查询（只挑 `cross_border_synced = 0`）永远选不中它 ——
    对端那边的副本被删掉之后就再也回不来了。
    """
    assert _CARD not in {c["id"] for c in
                         await pg_store.get_unsynced_cross_border_cards_unscoped()}, (
        "种子必须是已同步的（cross_border_synced = 1），否则本条测的不是恢复的功劳")

    await pg_store.delete_card(_CARD)
    await pg_store.restore_card(_CARD)

    unsynced = {c["id"] for c in await pg_store.get_unsynced_cross_border_cards_unscoped()}
    assert _CARD in unsynced, (
        "恢复后的公开卡没回到待转发队列 —— cross_border_synced 还停在 1，"
        "对端的副本删了就永远建不回来")


@_pg
async def test_delete_requeues_after_ack_and_restore(pg_card, pg_store):
    """同一目标传播出去、又恢复过之后，再次删除仍要能入队。

    outbox 的唯一键是 `(op_type, target_id)`，所以「已确认」必须真的把行删掉；
    只是标成 `synced = 1` 的话，槽位仍被占着，第二次删除的 INSERT 会被
    `ON CONFLICT DO NOTHING` 吃掉 —— 这一次删除永远发不出去。
    """
    await pg_store.delete_card(_CARD)
    rows = await _queued_card_deletes(pg_store)
    assert len(rows) == 1, "种子没入队，用例测不到东西"

    await pg_store.remove_delete_propagation(rows[0]["id"])
    assert await _queued_card_deletes(pg_store) == [], (
        "对端已确认，这一行必须真的从表里消失 —— 留着（哪怕标了 synced = 1）"
        "就还占着 (op_type, target_id) 这个唯一键，同一张卡再也删不掉")

    await pg_store.restore_card(_CARD)
    await pg_store.delete_card(_CARD)

    assert len(await _queued_card_deletes(pg_store)) == 1, (
        "恢复后再删除，这条删除没能重新入队 —— 对端会一直留着这张已删的卡")
