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

from conftest import PG_ENV, TEST_DATABASE_URL
from storage.postgres_store import PostgresStore

_pg = PG_ENV.skipif("PG 评论点赞用例")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def _store() -> PostgresStore:
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    return store


async def _seed_comment(store: PostgresStore, kind: str) -> tuple[str, str]:
    """造一条真实存在的评论，返回 `(comment_id, author_id)`。"""
    user = await store.create_user(_uid("u"), _uid("n"), "x")
    if kind == "card":
        text = await store.save_text(_uid("t"), "f.txt", "正文", user_id=user["id"])
        card = await store.save_card(_uid("c"), text["id"], "卡", "{}", user["id"])
        comment = await store.add_comment(card["id"], user["id"], user["username"], "评论")
    else:
        post = await store.add_post(user["id"], "帖子", "public")
        comment = await store.add_post_comment(post["id"], user["id"], user["username"], "评论")
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


@pytest.mark.parametrize("kind,table", [("post", "post_comment_likes"), ("card", "card_comment_likes")])
async def test_deleting_a_comment_takes_its_likes_with_it(kind: str, table: str):
    store = await _store()
    try:
        comment_id, user_id = await _seed_comment(store, kind)
    finally:
        await store.close()

    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(
            f"INSERT INTO {table} (comment_id, user_id) VALUES ($1, $2)", comment_id, user_id)
        assert await _like_rows(table, comment_id) == 1, "赞行没插进去，下面的级联断言会空转"

        await conn.execute(f"DELETE FROM {kind}_comments WHERE id = $1", comment_id)
        assert await _like_rows(table, comment_id) == 0, (
            f"{kind} 评论删了，它的赞还留着 —— 迁移里缺 ON DELETE CASCADE")
    finally:
        await conn.close()
