# -*- coding: utf-8 -*-
"""帖子 / 卡片评论点赞（spec-comment-likes）：迁移的级联、共用的切换函数、两条新路由。

**为什么单开一个文件**：三种评论（text / post / card）共用同一套存取代码，本份的判据
就是「同一件事在三种评论上逐一同形」。参数化放在一个文件里，漏掉某一种是一个显眼的
缺口；分散成三个文件，漏一种只是一个安静的空缺。

**为什么用真 PG**：级联是 `ON DELETE CASCADE` 的事，只有真库答得了；SQLite 侧不测
（AGENTS.md：存储改动只保证 PG）。

Run: pytest tests/test_comment_likes.py -v
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conftest import PG_ENV, TEST_DATABASE_URL
from deps import get_storage
from routers.auth import get_current_user, get_optional_user
from routers.market import router as market_router
from routers.text import router as text_router
from storage.postgres_store import PostgresStore

_pg = PG_ENV.skipif("PG 评论点赞用例")


#: 三种评论各自的表名 —— 测试直接照着断言，不从被测代码里取（那会让「选错表」的
#: 变异在两边同时生效，判据跟着一起歪）。
_COMMENT_TABLE = {"text": "text_comments", "post": "post_comments", "card": "card_comments"}
_LIKE_TABLE = {
    "text": "text_comment_likes",
    "post": "post_comment_likes",
    "card": "card_comment_likes",
}


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def _store() -> PostgresStore:
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    return store


async def _seed_comment(store: PostgresStore, kind: str) -> tuple[str, str]:
    """造一条真实存在的评论，返回 `(comment_id, author_id)`。"""
    username = _uid("n")
    user = await store.create_user(_uid("u"), username, "x")
    if kind == "text":
        text = await store.save_text(_uid("t"), "f.txt", "正文", user_id=user["id"])
        comment = await store.add_text_comment(text["id"], user["id"], username, "评论")
    elif kind == "card":
        text = await store.save_text(_uid("t"), "f.txt", "正文", user_id=user["id"])
        card = await store.save_card(_uid("c"), text["id"], "卡", "{}", user["id"])
        comment = await store.add_comment(card["id"], user["id"], username, "评论")
    else:
        post = await store.add_post(user["id"], "帖子", "public")
        comment = await store.add_post_comment(post["id"], user["id"], username, "评论")
    return comment["id"], user["id"]


async def _like_rows(table: str, comment_id: str) -> int:
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        return await conn.fetchval(
            f"SELECT COUNT(*) FROM {table} WHERE comment_id = $1", comment_id)
    finally:
        await conn.close()


# ── 1. 迁移：删评论时它的赞跟着消失（外键级联）────────────────────────────────
#
# 计数存在评论行上，评论没了计数就无所谓；但**赞行留着**是孤儿 —— 评论 id 一旦被复用
# （`add_comment` 用的是 uuid4 前缀，理论上会），新评论一出现就带着前世的赞。
# 这一条只测迁移，所以赞行走裸 SQL 插入，不依赖切换函数。


@pytest.mark.parametrize("kind", ["post", "card"])
async def test_deleting_a_comment_takes_its_likes_with_it(kind: str):
    store = await _store()
    try:
        comment_id, user_id = await _seed_comment(store, kind)
    finally:
        await store.close()

    table = _LIKE_TABLE[kind]
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(
            f"INSERT INTO {table} (comment_id, user_id) VALUES ($1, $2)", comment_id, user_id)
        assert await _like_rows(table, comment_id) == 1, "赞行没插进去，下面的级联断言会空转"

        await conn.execute(f"DELETE FROM {_COMMENT_TABLE[kind]} WHERE id = $1", comment_id)
        assert await _like_rows(table, comment_id) == 0, (
            f"{kind} 评论删了，它的赞还留着 —— 迁移里缺 ON DELETE CASCADE")
    finally:
        await conn.close()


# ── 2. 三种评论共用一套切换代码 ──────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["text", "post", "card"])
async def test_toggling_a_like_flips_it_and_moves_the_counter(kind: str):
    """点一次 +1、`liked=True`；再点一次回到原值、`liked=False`。"""
    store = await _store()
    try:
        comment_id, user_id = await _seed_comment(store, kind)
        assert await store.toggle_comment_like(kind, comment_id, user_id) == {
            "liked": True, "likes": 1}
        assert await store.toggle_comment_like(kind, comment_id, user_id) == {
            "liked": False, "likes": 0}
    finally:
        await store.close()


@pytest.mark.parametrize("kind", ["text", "post", "card"])
async def test_liking_a_comment_that_is_gone_returns_none_and_writes_nothing(kind: str):
    """评论不存在 → `None`，且**一行赞都不写**。

    旧 `toggle_text_comment_like` 只看赞行、不看评论，于是点一条不存在的评论会插进一行
    孤儿记录并返回 0 —— 「回 404」与「什么都没写」是同一件事的两面，所以钉在一起。
    """
    store = await _store()
    try:
        username = _uid("n")
        user = await store.create_user(_uid("u"), username, "x")
        missing = _uid("gone")
        assert await store.toggle_comment_like(kind, missing, user["id"]) is None
    finally:
        await store.close()
    assert await _like_rows(_LIKE_TABLE[kind], missing) == 0, "评论不存在，却写了赞行"


@pytest.mark.parametrize("kind", ["text", "post", "card"])
async def test_liked_ids_come_from_the_table_for_that_kind(kind: str):
    """`get_liked_comment_ids` 按类型选表：写死一张表会把另两种评论一律读成「没赞过」。"""
    store = await _store()
    try:
        liked_id, user_id = await _seed_comment(store, kind)
        other_id, _ = await _seed_comment(store, kind)
        await store.toggle_comment_like(kind, liked_id, user_id)
        assert await store.get_liked_comment_ids(kind, [liked_id, other_id], user_id) == {liked_id}
    finally:
        await store.close()


# ── 3. 路由层：不存在的评论回 404，列表带 liked_by_me ────────────────────────
#
# 存储层返回 `None` 只是「回 404」的一半：路由把 `None` 翻译成 404 是另一半。
# 列表的 `liked_by_me` 则是路由层的账 —— 存储只答「这批 id 里哪些赞过」。

_LIKE_ROUTE = {
    "text": "/api/text/comments/{}/like",
    "post": "/api/market/post/comments/{}/like",
    "card": "/api/market/comments/{}/like",
}
_LIST_ROUTE = {
    "post": "/api/market/post/{}/comments",
    "card": "/api/market/{}/comments",
}


async def _seed_two_comments(store: PostgresStore, kind: str) -> tuple[str, str, str, str]:
    """同一父级下两条评论（不同作者），返回 `(parent_id, 我赞的那条, 另一条, 我的 id)`。"""
    username = _uid("n")
    user = await store.create_user(_uid("u"), username, "x")
    other_name = _uid("n")
    other = await store.create_user(_uid("u"), other_name, "x")
    if kind == "post":
        parent = await store.add_post(user["id"], "帖子", "public")
        liked = await store.add_post_comment(parent["id"], user["id"], username, "评论")
        second = await store.add_post_comment(parent["id"], other["id"], other_name, "评论")
    else:
        text = await store.save_text(_uid("t"), "f.txt", "正文", user_id=user["id"])
        card = await store.save_card(_uid("c"), text["id"], "卡", "{}", user["id"])
        # 新卡默认 private，匿名访客看不到私卡评论。要测「游客看得见计数」，卡得是公开的。
        await store.update_card_visibility(card["id"], "public")
        liked = await store.add_comment(card["id"], user["id"], username, "评论")
        second = await store.add_comment(card["id"], other["id"], other_name, "评论")
        parent = card
    return parent["id"], liked["id"], second["id"], user["id"]


def _app(store: PostgresStore, user: dict | None):
    """只装被测的两条 router —— 判据是这两条路由的行为，不是整个 app 的装配。

    用 `AsyncClient` 而不是 `TestClient`：asyncpg 的连接池绑定创建它的 loop，测试的
    临时 loop 里建池、再让 `TestClient` 的 portal loop 去用会炸
    （`another operation is in progress`）。同一个 `async def` 里造数据 + 发请求，
    存储和 app 就共用同一个 loop。
    """
    app = FastAPI()
    app.include_router(text_router)
    app.include_router(market_router)
    app.dependency_overrides[get_storage] = lambda: store
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_optional_user] = lambda: user
    return app


async def _request(method: str, app: FastAPI, path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.request(method, path)


@_pg
@pytest.mark.parametrize("kind", ["text", "post", "card"])
async def test_liking_a_missing_comment_over_http_returns_404(kind: str):
    store = await _store()
    try:
        user = await store.create_user(_uid("u"), _uid("n"), "x")
        resp = await _request("POST", _app(store, user), _LIKE_ROUTE[kind].format(_uid("gone")))
        assert resp.status_code == 404
    finally:
        await store.close()


@_pg
@pytest.mark.parametrize("kind", ["post", "card"])
async def test_comment_list_marks_liked_by_me_and_carries_the_count(kind: str):
    """「我赞的那条为真、另一条为假」——写死成 `False` 会让前半条当场变红。"""
    store = await _store()
    try:
        parent_id, liked_id, second_id, user_id = await _seed_two_comments(store, kind)
        await store.toggle_comment_like(kind, liked_id, user_id)
        me = {"id": user_id, "username": "n", "role": "user"}
        resp = await _request("GET", _app(store, me), _LIST_ROUTE[kind].format(parent_id))
        by_id = {c["id"]: c for c in resp.json()["comments"]}
        assert by_id[liked_id]["liked_by_me"] is True
        assert by_id[second_id]["liked_by_me"] is False
        assert by_id[liked_id]["likes"] == 1
    finally:
        await store.close()


@_pg
async def test_card_comment_list_is_never_liked_for_anonymous():
    """游客看得到计数、看不到按钮：`liked_by_me` 一律 `False`，哪怕这条评论真被赞过。"""
    store = await _store()
    try:
        card_id, liked_id, _, user_id = await _seed_two_comments(store, "card")
        await store.toggle_comment_like("card", liked_id, user_id)
        resp = await _request("GET", _app(store, None), _LIST_ROUTE["card"].format(card_id))
        by_id = {c["id"]: c for c in resp.json()["comments"]}
        assert by_id[liked_id]["liked_by_me"] is False
        assert by_id[liked_id]["likes"] == 1
    finally:
        await store.close()
