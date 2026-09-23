"""两处启动期断言：SQLite 最低版本门 + cards 重建不丢子表行。

**为什么锁版本门**：`ALTER TABLE ... DROP COLUMN` 要 3.35 才有，低于它的库此前走
「原表重建」回落分支 —— 那段 `CREATE TABLE users_mig` 不幂等，中途失败一次就在库里
留下 `users_mig`，下次启动必报 `table users_mig already exists`，应用起不来（缺陷 82）。
整条回落分支已删：低于 3.35 直接明确报错，不做任何降级。

**为什么锁 cards 重建**：FK 打开时 `DROP TABLE` 会先做一次隐式 DELETE FROM，于是引用
cards 的子表（这里以 `sessions` 为代表）按 ON DELETE CASCADE 被清空。`PRAGMA
defer_foreign_keys = ON` **挡不住** —— 它推迟的是「约束违例」的检查，而 CASCADE 是
FK **动作**，该发照发。故重建必须走 `_fk_disabled`。

**构造方式**：版本门那条按住 `sqlite3.sqlite_version_info`（本机 3.49，真降级做不到）；
cards 那条直接调那段重建函数（本机没有 `text_id NOT NULL` 的老库可造）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import aiosqlite
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage.sqlite_store import (  # noqa: E402
    SQLiteStore,
    _rebuild_cards_nullable_text_id,
)

def _conn(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path)


def _count(path: str, table: str) -> int:
    c = _conn(path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


async def test_sqlite_below_minimum_version_fails_loudly(tmp_path, monkeypatch):
    """SQLite < 3.35 时初始化必须明确报错，错误信息点名所需版本。"""
    db = str(tmp_path / "too_old.db")
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))

    with pytest.raises(RuntimeError) as excinfo:
        await SQLiteStore(db)._ensure_initialized()

    assert "3.35" in str(excinfo.value)


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
