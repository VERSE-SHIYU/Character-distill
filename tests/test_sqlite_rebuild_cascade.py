"""两处表重建（users / cards）共用同一个 FK 关闭件 —— 关不住就按 CASCADE 丢子表行。

**为什么要锁**：FK 打开时 `DROP TABLE` 会先做一次隐式 DELETE FROM，于是
`refresh_tokens` 与 `user_secrets`（后者存的是加密凭据）按 ON DELETE CASCADE 被清空，
`DROP TABLE cards` 同理清掉 8 张子表里的 6 张。`PRAGMA defer_foreign_keys = ON`
**挡不住** —— 它推迟的是「约束违例」的检查，而 CASCADE 是 FK **动作**，该发照发。

**为什么必须共用一个件**：原先两处各写一遍，users 那条只用了 defer（= 没关），
只在 SQLite < 3.35 才进得去，真跑会丢数据。本文件两条用例就是那个共用件的
两个判别面 —— 少一条，共用件就退化成「只服务一处」。

**构造方式**：users 那条按住 `sqlite3.sqlite_version_info` 把回落分支逼出来（本机
3.49 默认走原生 DROP COLUMN，进不去）；cards 那条直接调那段重建函数（本机没有
`text_id NOT NULL` 的老库可造）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import aiosqlite

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage.sqlite_store import (  # noqa: E402
    SQLiteStore,
    _rebuild_cards_nullable_text_id,
)

USER_ID = "u-cascade-probe"


def _conn(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path)


def _count(path: str, table: str) -> int:
    c = _conn(path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


def _cols(path: str, table: str) -> list[str]:
    c = _conn(path)
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    finally:
        c.close()


async def test_users_rebuild_keeps_cascade_children(tmp_path, monkeypatch):
    """users 重建走回落分支时，refresh_tokens / user_secrets 的行数必须不变。"""
    db = str(tmp_path / "users.db")
    await SQLiteStore(db)._ensure_initialized()

    c = _conn(db)
    try:
        c.execute("INSERT INTO users (id, username) VALUES (?, ?)", (USER_ID, "cascade"))
        c.execute("INSERT INTO user_secrets (user_id, password_hash) VALUES (?, ?)",
                  (USER_ID, "hash"))
        for i in range(3):
            c.execute("INSERT INTO refresh_tokens (token_hash, user_id, expires_at) "
                      "VALUES (?, ?, ?)", (f"tok{i}", USER_ID, "2099-01-01"))
        c.commit()
    finally:
        c.close()

    tables = ("refresh_tokens", "user_secrets")
    before = {t: _count(db, t) for t in tables}
    assert all(n > 0 for n in before.values()), "种子没落库，用例测不到东西"

    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))
    await SQLiteStore(db)._ensure_initialized()

    assert [c for c in ("api_key", "base_url", "model") if c in _cols(db, "users")] == [], \
        "回落分支没跑成（018 加回的列还在），本用例没测到重建那一步"
    assert {t: _count(db, t) for t in tables} == before


async def test_cards_rebuild_keeps_cascade_children(tmp_path):
    """cards 重建时，引用它的子表（此处以 sessions 为代表）行数必须不变。"""
    db = str(tmp_path / "cards.db")
    await SQLiteStore(db)._ensure_initialized()

    c = _conn(db)
    try:
        c.execute("INSERT INTO cards (id, text_id, name, card_json) VALUES (?, ?, ?, ?)",
                  ("card-cascade", None, "n", "{}"))
        for i in range(2):
            c.execute("INSERT INTO sessions (id, card_id) VALUES (?, ?)",
                      (f"s{i}", "card-cascade"))
        c.commit()
    finally:
        c.close()

    before = _count(db, "sessions")
    assert before == 2

    async with aiosqlite.connect(db) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await _rebuild_cards_nullable_text_id(conn)

    assert _count(db, "sessions") == before
