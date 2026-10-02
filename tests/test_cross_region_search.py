# -*- coding: utf-8 -*-
"""跨区用户发现（spec cross-region-user-search）：搜索、对端主页、禁用视图、注销清理。

对端用户在本库只以 `remote_user_profiles` + `remote_cards` 存在（隐私政策 3.2(1)(2)）。
本文件锁的是「这两张表里的东西在搜索 / 主页 / 角色卡作者名上看得见，且只看得见这些」。

**为什么用真 PG**：搜索是 UNION + NOT EXISTS + ILIKE，排除规则（禁用、本地同 id）
只有真库答得了；SQLite 的 LIKE 天生不分大小写，测不出 PG 侧的大小写问题。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conftest import PG_ENV, TEST_DATABASE_URL
from deps import get_storage
import account_visibility as AV
from routers import inter_node as N
from routers import market as M
from routers.auth import get_current_user
from storage.postgres_store import PostgresStore

pytestmark = PG_ENV.skipif("跨区用户发现用例")

_THERE = "sg-singapore"


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import limiter as L

    monkeypatch.setattr(L.limiter, "enabled", False)
    monkeypatch.setenv("INTER_NODE_SECRET", "test-inter-node-secret-0123456789abcdef")


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


async def _local(store, name: str | None = None) -> str:
    uid = _uid("u")
    await store.create_user(uid, name or _uid("n"), "x")
    return uid


async def _remote(store, name: str, *, disabled: bool = False, uid: str | None = None) -> str:
    uid = uid or _uid("r")
    await store.upsert_remote_account(uid, name, _THERE, "data:av", is_disabled=disabled)
    return uid


async def _remote_card(store, owner: str, name: str) -> str:
    cid = _uid("rc")
    await store.upsert_remote_card(cid, _THERE, owner, name, '{"name": "x"}', "", "", "",
                                   "2026-09-01T00:00:00+00:00")
    return cid


def _users(result: dict) -> dict[str, dict]:
    return {u["id"]: u for u in result["users"]}


# ── 1. 搜索：对端用户进结果，带 is_remote ─────────────────────────────────────

async def test_search_finds_local_and_remote_users(store):
    tag = uuid.uuid4().hex[:8]
    local = await _local(store, f"alice{tag}")
    remote = await _remote(store, f"alice{tag}far")
    got = _users(await store.global_search(f"alice{tag}"))
    assert got[local]["is_remote"] is False
    assert got[remote]["is_remote"] is True
    assert got[remote]["username"] == f"alice{tag}far"


async def test_search_remote_is_case_insensitive(store):
    tag = uuid.uuid4().hex[:8]
    remote = await _remote(store, f"Bob{tag}")
    assert remote in _users(await store.global_search(f"bob{tag}"))


async def test_search_skips_disabled_remote_user(store):
    tag = uuid.uuid4().hex[:8]
    gone = await _remote(store, f"carol{tag}", disabled=True)
    live = await _remote(store, f"carolx{tag}")
    got = _users(await store.global_search(f"carol{tag}"))
    assert gone not in got
    assert _users(await store.global_search(f"carolx{tag}")).keys() == {live}


async def test_search_local_row_wins_over_mirrored_remote_row(store):
    """演示账号两地同 id：只出一条，且是本地那条。"""
    tag = uuid.uuid4().hex[:8]
    uid = await _local(store, f"demo{tag}")
    await _remote(store, f"demo{tag}", uid=uid)
    rows = [u for u in (await store.global_search(f"demo{tag}"))["users"] if u["id"] == uid]
    assert rows == [rows[0]] and rows[0]["is_remote"] is False


async def test_search_texts_is_case_insensitive(store):
    owner = await _local(store)
    tag = uuid.uuid4().hex[:8]
    tid = _uid("t")
    await store.save_text(tid, "f.txt", "c", title=f"Moby{tag}", user_id=owner)
    got = await store.global_search(f"moby{tag}", owner)
    assert [t["id"] for t in got["texts"]] == [tid]


# ── 2. 对端角色卡的作者名：所有展示对端卡的查询都取到资料 ─────────────────────

async def test_remote_card_author_resolved_everywhere(store):
    tag = uuid.uuid4().hex[:8]
    owner = await _remote(store, f"dave{tag}")
    cid = await _remote_card(store, owner, f"Card{tag}")

    def pick(rows):
        return next(r for r in rows if r["id"] == cid)

    assert pick((await store.global_search(f"card{tag}"))["cards"])["author_name"] == f"dave{tag}"
    hit = pick(await store.search_public_cards(f"Card{tag}", 1, 50))
    assert (hit["author_name"], hit["author_avatar"]) == (f"dave{tag}", "data:av")
    listed = await store.list_public_cards(1, 500, "new")
    assert pick(listed)["author_name"] == f"dave{tag}"
    detail = await store.get_market_card_detail(cid, owner)
    assert (detail["author_name"], detail["author_avatar"]) == (f"dave{tag}", "data:av")


# ── 3. 注销传播：对端资料一并删掉，之后搜不到 ─────────────────────────────────

async def test_purge_removes_remote_profile(store):
    tag = uuid.uuid4().hex[:8]
    remote = await _remote(store, f"erin{tag}")
    counts = await store.purge_remote_user_data(remote)
    assert "remote_user_profiles" in counts
    assert await store.get_remote_user_profile(remote) is None
    assert remote not in _users(await store.global_search(f"erin{tag}"))


async def test_remote_user_cards_only_that_user(store):
    a = await _remote(store, _uid("fa"))
    b = await _remote(store, _uid("fb"))
    ca = await _remote_card(store, a, "A")
    await _remote_card(store, b, "B")
    assert [c["id"] for c in await store.get_remote_user_cards(a)] == [ca]


async def test_resync_overwrites_disabled_flag_both_ways(store):
    """解封要能撤销：同一 id 再同步一次，状态跟着变回来。"""
    uid = await _remote(store, _uid("k"), disabled=True)
    await store.upsert_remote_account(uid, "k", _THERE, "", is_disabled=False)
    assert (await store.get_public_account(uid))["is_disabled"] is False
    await store.upsert_remote_account(uid, "k", _THERE, "", is_disabled=True)
    assert (await store.get_public_account(uid))["is_disabled"] is True


# ── 4. 接收端：is_disabled 落库；旧版发送方不带字段按「正常」 ────────────────

def _inter_node_app(store) -> FastAPI:
    app = FastAPI()
    app.include_router(N.router)
    app.dependency_overrides[get_storage] = lambda: store
    return app


async def _post_sync(store, body: dict):
    from inter_node_auth import create_auth_header

    async with AsyncClient(transport=ASGITransport(app=_inter_node_app(store)),
                           base_url="http://t") as c:
        return await c.post("/api/inter-node/user/sync", json=body,
                            headers=create_auth_header(body))


@pytest.mark.parametrize("sent, stored", [(True, 1), (False, 0), (None, 0)])
async def test_receiver_stores_disabled_flag(store, sent, stored):
    uid = _uid("r")
    body = {"id": uid, "username": _uid("n"), "home_region": _THERE, "avatar_data": ""}
    if sent is not None:
        body["is_disabled"] = sent
    r = await _post_sync(store, body)
    assert r.status_code == 200, r.text
    assert (await store.get_public_account(uid))["is_disabled"] is bool(stored)


# ── 5. 作者主页：对端视图 / 禁用视图 / 本地照旧 ───────────────────────────────

def _market_app(store, viewer_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(M.router)
    app.dependency_overrides[get_storage] = lambda: store

    async def _viewer():
        return await store.get_user_by_id(viewer_id)

    app.dependency_overrides[get_current_user] = _viewer
    return app


async def _author(store, viewer: str, target: str):
    async with AsyncClient(transport=ASGITransport(app=_market_app(store, viewer)),
                           base_url="http://t") as c:
        return await c.get(f"/api/market/author/{target}")


async def test_author_page_of_remote_user(store):
    viewer = await _local(store)
    remote = await _remote(store, _uid("g"))
    cid = await _remote_card(store, remote, "G")
    r = await _author(store, viewer, remote)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["view"] == "remote"
    assert data["capabilities"] == AV.page_meta(AV.AccountView.REMOTE)["capabilities"]
    assert set(data["author"]) == set(AV.IDENTITY_FIELDS[AV.AccountView.REMOTE])
    assert data["author"]["home_region"] == _THERE
    assert [c["id"] for c in data["cards"]] == [cid]


async def test_author_page_of_disabled_remote_user(store):
    viewer = await _local(store)
    remote = await _remote(store, _uid("h"), disabled=True)
    await _remote_card(store, remote, "H")
    data = (await _author(store, viewer, remote)).json()
    assert data["view"] == "disabled"
    assert set(data["author"]) == set(AV.IDENTITY_FIELDS[AV.AccountView.DISABLED])
    assert data["cards"] == []


async def test_author_page_of_disabled_local_user(store):
    viewer = await _local(store)
    target = await _local(store)
    await store.set_user_disabled(target, True)
    data = (await _author(store, viewer, target)).json()
    assert data["view"] == "disabled"
    assert set(data["author"]) == set(AV.IDENTITY_FIELDS[AV.AccountView.DISABLED])
    # 本人看自己仍是完整视图
    assert (await _author(store, target, target)).json()["view"] == "self"


async def test_author_page_of_unknown_id_is_404(store):
    viewer = await _local(store)
    assert (await _author(store, viewer, _uid("ghost"))).status_code == 404


async def test_author_page_of_local_user_carries_local_view(store):
    viewer = await _local(store)
    target = await _local(store)
    data = (await _author(store, viewer, target)).json()
    assert data["view"] == "local"
    assert data["capabilities"] == AV.page_meta(AV.AccountView.LOCAL)["capabilities"]


# ── 6. 账号目录本身（PostgresStore._PUBLIC_ACCOUNTS）──────────────────────────────────

async def test_directory_resolves_local_and_remote(store):
    local = await _local(store)
    remote = await _remote(store, _uid("m"))
    a = await store.get_public_account(local)
    b = await store.get_public_account(remote)
    assert (a["is_remote"], b["is_remote"]) == (False, True)
    assert b["home_region"] == _THERE and b["nickname"] == ""
    assert await store.get_public_account(_uid("ghost")) is None


async def test_directory_search_matches_local_nickname(store):
    tag = uuid.uuid4().hex[:8]
    uid = await _local(store)
    async with await store._connect() as conn:
        await conn.execute("UPDATE users SET nickname = $1 WHERE id = $2", f"Nick{tag}", uid)
    assert uid in {a["id"] for a in await store.search_discoverable_accounts(f"nick{tag}", 5)}


# ── 7. 规则表（account_visibility）──────────────────────────────────────────

def _acc(**kw):
    return {"id": "a", "is_disabled": False, "is_remote": False, **kw}


@pytest.mark.parametrize("acc, viewer, view", [
    (_acc(), "a", AV.AccountView.SELF),
    (_acc(is_disabled=True), "a", AV.AccountView.SELF),          # 本人优先于禁用
    (_acc(), "b", AV.AccountView.LOCAL),
    (_acc(is_remote=True), "b", AV.AccountView.REMOTE),
    (_acc(is_disabled=True), "b", AV.AccountView.DISABLED),
    (_acc(is_remote=True, is_disabled=True), "b", AV.AccountView.DISABLED),
])
def test_view_of(acc, viewer, view):
    assert AV.view_of(acc, viewer) is view


def test_capability_table():
    """规则表本身就是规格：改它要改这张断言（以及前端的渲染测试）。"""
    caps = {v: AV.page_meta(v)["capabilities"] for v in AV.AccountView}
    on = {v: {k for k, ok in c.items() if ok} for v, c in caps.items()}
    every = set(caps[AV.AccountView.LOCAL])
    assert on[AV.AccountView.LOCAL] == every
    assert on[AV.AccountView.SELF] == every - {"message", "follow"}
    assert on[AV.AccountView.REMOTE] == {"message", "cards"}
    assert on[AV.AccountView.DISABLED] == set()
