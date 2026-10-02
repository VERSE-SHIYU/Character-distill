# -*- coding: utf-8 -*-
"""跨区 fork（spec cross-region-fork）：公开作品目录、fork 对端卡、对端卡详情、卡能力表。

对端卡在本库只以 `remote_cards` 存在（隐私政策 3.2(2)，含完整 `card_json`）。fork 读本库
副本，不依赖对端在线；产物是 fork 者自己的私密卡，原作删除 / 作者注销都不影响它。
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import card_visibility as CV
from conftest import PG_ENV, TEST_DATABASE_URL
from deps import get_storage
from routers import card as C
from routers import market as M
from routers.auth import get_current_user, get_optional_user
from storage.postgres_store import PostgresStore

pytestmark = PG_ENV.skipif("跨区 fork 用例")

_THERE = "sg-singapore"
_CARD = json.dumps({"name": "Far", "persona": "x"})


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import limiter as L

    monkeypatch.setattr(L.limiter, "enabled", False)


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


async def _user(store) -> str:
    uid = _uid("u")
    await store.create_user(uid, _uid("n"), "x")
    return uid


async def _remote_card(store, *, owner: str | None = None, name: str = "Far") -> tuple[str, str]:
    owner = owner or _uid("r")
    await store.upsert_remote_account(owner, _uid("far"), _THERE, "", is_disabled=False)
    cid = _uid("rc")
    await store.upsert_remote_card(cid, _THERE, owner, name, _CARD, "data:img", "desc", "tag",
                                   "2026-09-01T00:00:00+00:00")
    return cid, owner


async def _local_card(store, owner: str, *, public: bool) -> str:
    cid = uuid.uuid4().hex[:12]
    async with await store._connect() as conn:
        await conn.execute(
            "INSERT INTO cards (id, name, card_json, user_id, visibility) VALUES ($1, $2, $3, $4, $5)",
            cid, "Near", json.dumps({"name": "Near"}), owner, "public" if public else "private")
    return cid


# ── 1. 公开作品目录 ───────────────────────────────────────────────────────────

async def test_directory_resolves_local_public_and_remote(store):
    owner = await _user(store)
    local = await _local_card(store, owner, public=True)
    remote, _ = await _remote_card(store)
    a = await store.get_public_card(local)
    b = await store.get_public_card(remote)
    assert (a["is_remote"], b["is_remote"]) == (False, True)
    assert b["origin_region"] == _THERE and b["text_id"] is None
    assert b["card_json"] == _CARD and b["avatar_data"] == "data:img"


async def test_directory_hides_private_and_deleted_local_cards(store):
    owner = await _user(store)
    private = await _local_card(store, owner, public=False)
    gone = await _local_card(store, owner, public=True)
    async with await store._connect() as conn:
        await conn.execute("UPDATE cards SET deleted_at = NOW() WHERE id = $1", gone)
    assert await store.get_public_card(private) is None
    assert await store.get_public_card(gone) is None
    assert await store.get_public_card(_uid("ghost")) is None


# ── 2. fork 对端卡 ────────────────────────────────────────────────────────────

async def test_fork_remote_card_makes_a_private_local_copy(store):
    me = await _user(store)
    remote, _ = await _remote_card(store)
    new = await store.fork_card(remote, uuid.uuid4().hex[:12], me, "")
    assert new is not None
    assert (new["user_id"], new["forked_from"], new["visibility"]) == (me, remote, "private")
    assert new["card_json"] == _CARD and new["text_id"] in (None, "")


async def test_fork_remote_card_twice_returns_the_same_copy(store):
    me = await _user(store)
    remote, _ = await _remote_card(store)
    a = await store.fork_card(remote, uuid.uuid4().hex[:12], me, "")
    b = await store.fork_card(remote, uuid.uuid4().hex[:12], me, "")
    assert a["id"] == b["id"]


async def test_fork_remote_card_with_inherited_text_link_is_unlinked(store):
    """`new_text_id=None` 表示沿用原卡关联 —— 对端卡的文本不在本库，沿用即不关联。"""
    me = await _user(store)
    remote, _ = await _remote_card(store)
    new = await store.fork_card(remote, uuid.uuid4().hex[:12], me, None)
    assert new is not None and not new["text_id"]


async def test_fork_survives_peer_purge(store):
    """删除规则 a：fork 出来的卡归 fork 者，原作者注销不连带删除。"""
    me = await _user(store)
    remote, owner = await _remote_card(store)
    new = await store.fork_card(remote, uuid.uuid4().hex[:12], me, "")
    await store.purge_remote_user_data(owner)
    assert await store.get_public_card(remote) is None
    assert (await store.get_card_owned(new["id"], me))["forked_from"] == remote


async def test_fork_local_card_unchanged(store):
    author = await _user(store)
    me = await _user(store)
    pub = await _local_card(store, author, public=True)
    priv = await _local_card(store, author, public=False)
    assert (await store.fork_card(pub, uuid.uuid4().hex[:12], me, ""))["forked_from"] == pub
    assert await store.fork_card(priv, uuid.uuid4().hex[:12], me, "") is None


# ── 3. 卡详情：对端卡可看；他人私密卡与不存在同判 ────────────────────────────

async def test_card_detail_of_remote_card(store):
    me = await _user(store)
    remote, owner = await _remote_card(store)
    d = await store.get_card_detail(remote, me)
    assert d["is_remote"] is True and d["is_market_card"] is True
    assert d["author_name"] == (await store.get_public_account(owner))["username"]
    assert (d["likes"], d["comment_count"], d["liked_by_me"]) == (0, 0, False)


async def test_card_detail_hides_someone_elses_private_card(store):
    author = await _user(store)
    other = await _user(store)
    priv = await _local_card(store, author, public=False)
    assert await store.get_card_detail(priv, other) is None
    assert (await store.get_card_detail(priv, author))["is_remote"] is False


async def test_market_detail_of_remote_card_shares_the_shape(store):
    me = await _user(store)
    remote, _ = await _remote_card(store)
    a = await store.get_card_detail(remote, me)
    b = await store.get_market_card_detail(remote, me)
    assert a == b


# ── 4. 路由：能力随详情下发 ───────────────────────────────────────────────────

def _app(store, viewer_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(C.router)
    app.include_router(M.router)
    app.dependency_overrides[get_storage] = lambda: store

    async def _viewer():
        return await store.get_user_by_id(viewer_id)

    app.dependency_overrides[get_current_user] = _viewer
    app.dependency_overrides[get_optional_user] = _viewer
    return app


async def _get(store, viewer: str, url: str):
    async with AsyncClient(transport=ASGITransport(app=_app(store, viewer)), base_url="http://t") as c:
        return await c.get(url)


@pytest.mark.parametrize("url", ["/api/cards/{id}/detail", "/api/market/card/{id}"])
async def test_detail_routes_carry_card_capabilities(store, url):
    me = await _user(store)
    author = await _user(store)
    remote, _ = await _remote_card(store)
    local = await _local_card(store, author, public=True)
    r = (await _get(store, me, url.format(id=remote))).json()
    l = (await _get(store, me, url.format(id=local))).json()
    assert (r["view"], l["view"]) == ("remote", "local")
    assert r["capabilities"] == CV.page_meta({"is_remote": True})["capabilities"]
    assert l["capabilities"] == CV.page_meta({"is_remote": False})["capabilities"]


async def test_fork_route_on_remote_card(store):
    me = await _user(store)
    remote, _ = await _remote_card(store)
    async with AsyncClient(transport=ASGITransport(app=_app(store, me)), base_url="http://t") as c:
        r = await c.post(f"/api/market/{remote}/fork", json={"text_id": ""})
    assert r.status_code == 200, r.text
    assert r.json()["card"]["forked_from"] == remote


# ── 5. 规则表 ─────────────────────────────────────────────────────────────────

def test_card_capability_table():
    """规则表本身就是规格：改它要改这张断言（以及前端的渲染测试）。"""
    on = {v: {k for k, ok in CV.page_meta({"is_remote": v is CV.CardView.REMOTE})["capabilities"].items() if ok}
          for v in CV.CardView}
    assert on[CV.CardView.LOCAL] == {"fork", "like", "comment", "history", "report", "moderate"}
    assert on[CV.CardView.REMOTE] == {"fork"}
