# -*- coding: utf-8 -*-
"""卡片整卡写回的乐观锁（spec `docs/specs/arc-phase-unlocated.md` §13）。

编辑保存、挪动、唤醒语回写都是「读出、修改、整卡写回」。两个请求同时在途时，后写的会静默
覆盖先写的。修法分两半：出卡带 `revision`（`card_out.card_revision`，按存储原文现算，不落库），
路由先核对请求带来的 revision；存储层按读到的原文**比较后写入**，核对与写入之间又被改也拦得住。
冲突一律 409。存储那一半在 PG 上真跑见 `test_postgres_store.py::TestPgCardCompareAndSwap`。
"""
from __future__ import annotations

import json
import logging

from core.card_out import CARD_CONFLICT, card_revision, out_card
from core.fingerprint import content_fingerprint
from core.schema import CharacterCard


def _card(**kw) -> CharacterCard:
    return CharacterCard.model_validate({"name": "x", "character_arc": {
        "phases": [{"state": "s"}],
        "unlocated": {"behaviors": [{"situation": "s", "behavior": "b"}]}}, **kw})


class _Store:
    """与 PG 同契约的假存储：`update_card` 比较后写入。`meddle` 在读与写之间插一次别人的写入。"""

    def __init__(self, card: CharacterCard):
        self.rows = {"c1": {"id": "c1", "user_id": "u1", "card_json": card.model_dump_json()}}
        self.meddle = None

    def stored(self) -> dict:
        return json.loads(self.rows["c1"]["card_json"])

    async def get_card_owned(self, card_id, user_id):
        row = self.rows.get(card_id)
        if not row or row["user_id"] != user_id:
            return None
        snapshot = dict(row)
        if self.meddle:                       # 读完之后、写之前，别的请求改了这张卡
            self.meddle(self.rows[card_id])
            self.meddle = None
        return snapshot

    async def update_card(self, card_id, card_json, *, expected):
        if self.rows[card_id]["card_json"] != expected:
            return None
        self.rows[card_id]["card_json"] = json.dumps(card_json, ensure_ascii=False)
        return dict(self.rows[card_id])


def _client(store):
    from fastapi import FastAPI

    from conftest import app_lifespan, open_test_client
    from deps import get_storage
    from routers.auth import get_current_user
    from routers.distill import router

    app = FastAPI(lifespan=app_lifespan)
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": "user"}
    return open_test_client(app)


def _someone_renames(row):
    data = json.loads(row["card_json"])
    data["name"] = "别人改的"
    row["card_json"] = json.dumps(data, ensure_ascii=False)


def _patch(store, revision, name="我改的"):
    data = store.stored()
    data["name"] = name
    return _client(store).patch("/api/distill/card/c1", json={"card_json": data, "revision": revision})


def _move(store, revision):
    return _client(store).post("/api/distill/card/c1/unlocated/move", json={
        "section": "behaviors", "index": 0, "phase": 1, "revision": revision})


# ── revision：出卡现算，按存储原文 ───────────────────────────────────

def test_out_card_revision_is_the_raw_stored_text_and_follows_content():
    a, b = _card().model_dump_json(), _card(identity="变了").model_dump_json()
    assert (out_card({"card_json": a})["revision"], out_card({"card_json": b})["revision"] != card_revision(a)) == (
        card_revision(a), True)


def test_card_revision_is_the_full_fingerprint_of_the_stored_text():
    """版本 = 存储原文的**全量**内容指纹（§13.2，补充 13）。

    上一条两边都经 `card_revision`，函数内部怎么变都过得去；这里拿它之外的真值比：唯一的指纹
    实现 `content_fingerprint`，外加位宽（SHA-256 十六进制 64 位）。截短之后旧版本可能碰巧等于
    新版本，那就是一次放行的静默覆盖 —— 乐观锁挡不挡得住，全看这个碰撞面。
    """
    raw = _card().model_dump_json()
    assert (card_revision(raw), len(card_revision(raw))) == (content_fingerprint(raw), 64)


# ── 编辑保存（PATCH） ─────────────────────────────────────────────

def test_patch_with_current_revision_saves_and_returns_the_new_revision():
    store = _Store(_card())
    r = _patch(store, card_revision(store.rows["c1"]["card_json"]))
    assert (r.status_code, store.stored()["name"], r.json()["card"]["revision"]) == (
        200, "我改的", card_revision(store.rows["c1"]["card_json"]))


def test_patch_with_stale_revision_is_409_and_card_untouched():
    store = _Store(_card())
    stale = card_revision(store.rows["c1"]["card_json"])
    _someone_renames(store.rows["c1"])
    r = _patch(store, stale)
    assert (r.status_code, r.json()["detail"], store.stored()["name"]) == (409, CARD_CONFLICT, "别人改的")


def test_patch_racing_another_write_is_409_and_keeps_the_other_write():
    """核对通过之后、写入之前别人写了：存储层比较失败 → 409，不覆盖别人的写入。"""
    store = _Store(_card())
    rev = card_revision(store.rows["c1"]["card_json"])
    store.meddle = _someone_renames
    r = _patch(store, rev)
    assert (r.status_code, store.stored()["name"]) == (409, "别人改的")


# ── 挪动 ─────────────────────────────────────────────────────────

def test_move_with_stale_revision_is_409_and_card_untouched():
    store = _Store(_card())
    stale = card_revision(store.rows["c1"]["card_json"])
    _someone_renames(store.rows["c1"])
    r = _move(store, stale)
    assert (r.status_code, len(store.stored()["character_arc"]["unlocated"]["behaviors"])) == (409, 1)


def test_move_racing_another_write_is_409_and_keeps_the_other_write():
    store = _Store(_card())
    rev = card_revision(store.rows["c1"]["card_json"])
    store.meddle = _someone_renames
    r = _move(store, rev)
    assert (r.status_code, store.stored()["name"],
            len(store.stored()["character_arc"]["unlocated"]["behaviors"])) == (409, "别人改的", 1)


# ── 唤醒语回写：写进库里当前那张卡，不拿内存里的旧卡整卡覆盖 ─────────────

async def test_awakening_lands_on_the_latest_card_and_keeps_the_users_edit():
    from routers.distill import _persist_awakening

    store = _Store(_card())
    _someone_renames(store.rows["c1"])            # 存卡之后、唤醒语回写之前，用户改了名
    await _persist_awakening(store, "c1", "u1", "醒来了")
    assert (store.stored()["name"], store.stored()["awakening_message"]) == ("别人改的", "醒来了")


async def test_awakening_racing_another_write_writes_nothing_and_warns(caplog):
    from routers.distill import _persist_awakening

    store = _Store(_card())
    store.meddle = _someone_renames
    with caplog.at_level(logging.WARNING, logger="routers.distill"):
        await _persist_awakening(store, "c1", "u1", "醒来了")
    assert (store.stored()["name"], store.stored().get("awakening_message", ""),
            any("changed meanwhile" in r.getMessage() for r in caplog.records)) == ("别人改的", "", True)
