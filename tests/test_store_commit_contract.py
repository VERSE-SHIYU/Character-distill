# -*- coding: utf-8 -*-
"""提交契约的语义层（缺陷 24，第八次同族显形）—— 与 `tests/test_store_connect_lock.py` 互补。

形态锁（`test_store_connect_lock.py`）锁「连接有没有绕过出口」；本文件锁「出口的承诺是不是
真的兑现」。两层各管各的，缺一不可 —— 仓内成文见 AGENTS.md §四「形态锁与语义用例是两层
防线」。只锁形态的话，把 `_ConnectionContext.__aexit__` 的 commit 删掉锁照样绿。

**被锁的命题**：`async with await self._connect() as conn:` 作用域退出即提交。

  - 无异常退出 → 落盘，且**跨连接**可见（不是同连接内的可见性假象）
  - 因异常退出 → 整个作用域原子回滚，不留半写状态

口径说明：跨连接读回用**同步 sqlite3** 新开连接（aiosqlite 的 `:memory:` 每次 `_connect`
都是新库，故一律用临时文件库；同步 sqlite3 读已提交数据没有额外依赖，且与
`tests/test_sqlite_fresh_schema.py` 同做法）。
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path

import aiosqlite
import pytest

from storage.sqlite_store import SQLiteStore, _ConnectionContext

_INJECTED = "__INJECTED_WRITE_FAILURE__"
_WRITE_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def _fresh_store(tmp_path: Path) -> tuple[SQLiteStore, str]:
    db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
    return SQLiteStore(db_path), db_path


def _row_counts(db_path: str) -> dict[str, int]:
    """每个表当前行数快照 —— 用于断言「全回滚、无半写」。"""
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")]
        return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in sorted(tables)}
    finally:
        conn.close()


def _count(db_path: str, sql: str, *args) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


class _InstrumentedConn:
    """包装真连接：数写语句；`fail_at` 给定时第 fail_at 条**写语句**抛错。

    只数写语句、不数 SELECT —— 否则「第 n 次 execute」随方法里的读语句漂移，注入点就不再是
    「已写了 k 条之后」。commit / rollback / close 经 `__getattr__` 原样转发给真连接，
    所以被测的仍是真正的 `_ConnectionContext.__aexit__`。
    """

    def __init__(self, conn, fail_at: int | None = None) -> None:
        self._conn = conn
        self._fail_at = fail_at
        self.writes = 0

    async def execute(self, sql, *a, **kw):
        if _WRITE_RE.search(sql):
            self.writes += 1
            if self._fail_at is not None and self.writes == self._fail_at:
                raise RuntimeError(_INJECTED)
        return await self._conn.execute(sql, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _instrument(store: SQLiteStore, monkeypatch, fail_at: int | None = None) -> list[_InstrumentedConn]:
    """把 store 的 `_connect` 包上 `_InstrumentedConn`；返回每次包装的列表（读 `[-1].writes`）。

    被测方法都只开一个作用域，所以 `[-1]` 就是那个连接；用列表而非单值是为了不依赖
    「只开一次」这个前提 —— 真开两次也不会静默取错。
    """
    orig = SQLiteStore._connect
    box: list[_InstrumentedConn] = []

    async def _patched():
        ctx = await orig(store)
        ctx.conn = _InstrumentedConn(ctx.conn, fail_at)
        box.append(ctx.conn)
        return ctx

    monkeypatch.setattr(store, "_connect", _patched)
    return box


def _chain_text(exc: BaseException) -> str:
    parts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        parts.append(f"{type(exc).__name__}: {exc}")
        exc = exc.__cause__ or exc.__context__
    return " | ".join(parts)


async def _seed_user(store: SQLiteStore, uid: str) -> None:
    await store.create_user(uid, f"u_{uid}", "hash")


class TestContextManagerContract:
    """机制本身：`_ConnectionContext` 成功即提交、异常即回滚。**变异的目标就是这里。**"""

    async def test_clean_exit_commits(self, tmp_path: Path):
        """无异常退出 → 写入落盘，且新开连接读得到。"""
        store, db_path = _fresh_store(tmp_path)
        await store._ensure_initialized()
        ctx = _ConnectionContext(await aiosqlite.connect(db_path))
        async with ctx as conn:
            await conn.execute(
                "INSERT INTO texts (id, filename, content, char_count) VALUES ('t1','f','c',1)")
        assert _count(db_path, "SELECT count(*) FROM texts WHERE id='t1'") == 1, (
            "作用域正常退出后写入没落盘 —— _ConnectionContext.__aexit__ 的 commit 没了？")

    async def test_exception_rolls_back(self, tmp_path: Path):
        """异常退出 → 整个作用域回滚（close 的隐式回滚之外，rollback 是显式的）。"""
        store, db_path = _fresh_store(tmp_path)
        await store._ensure_initialized()
        ctx = _ConnectionContext(await aiosqlite.connect(db_path))
        with pytest.raises(RuntimeError, match="boom"):
            async with ctx as conn:
                await conn.execute(
                    "INSERT INTO texts (id, filename, content, char_count) VALUES ('t2','f','c',1)")
                raise RuntimeError("boom")
        assert _count(db_path, "SELECT count(*) FROM texts WHERE id='t2'") == 0, (
            "异常路径没有回滚 —— 半写状态被留下了")


class TestOffendersPersistAcrossConnections:
    """缺陷现场的两个方法：写入后**新开连接**必须读得到（同连接读会掩盖问题）。"""

    async def test_add_post_comment(self, tmp_path: Path):
        store, db_path = _fresh_store(tmp_path)
        await store._ensure_initialized()
        await _seed_user(store, "u1")
        out = await store.add_post_comment("p1", "u1", "u1", "hello")
        assert out["id"]
        assert _count(db_path, "SELECT count(*) FROM post_comments WHERE id=?", out["id"]) == 1, (
            "add_post_comment 返回了 dict 但没落盘 —— 前端会显示、刷新即消失（缺陷 24 原现场）")

    async def test_cleanup_empty_cards(self, tmp_path: Path):
        store, db_path = _fresh_store(tmp_path)
        await store._ensure_initialized()
        await store.save_text("t1", "f.txt", "content", user_id="u1")
        await store.save_card("c1", "t1", "hero", "{}", user_id="u1")
        affected = await store.cleanup_empty_cards("t1", "u1")
        assert affected == 1, f"应命中 1 张空卡，实际 {affected}"
        assert _count(db_path, "SELECT count(*) FROM cards WHERE id='c1' AND deleted_at IS NOT NULL") == 1, (
            "cleanup_empty_cards 返回了真实 rowcount 却没落盘（缺陷 24 同形第二处）")


class TestMultiWriteAtomicityPreserved:
    """方案 B 不削弱既有事务边界：27 个多步写方法里最长的两个，中途失败必须全回滚。

    **注入点取「最后一条写语句」而不是写死的第 3 条** —— 写死会有静默空转：若前两条写语句
    碰的表在夹具里是空的（`delete_user` 先删 messages / sessions，而夹具里 0 行），那么
    「第 3 条炸掉」时**没有任何已落盘的行变化**，断言 `before == after` 恒成立，用例看着绿、
    其实什么都没验。取最后一条写语句则保证「前面所有写都已执行」，而阶段一已证明这些写
    真的会改行 → 注入点必然落在「已改过行」之后。
    """

    async def _seed(self, store: SQLiteStore) -> None:
        await _seed_user(store, "u1")
        await store.save_text("t1", "f.txt", "content", user_id="u1")
        await store.save_card("c1", "t1", "hero", '{"a":1}', user_id="u1")

    @staticmethod
    async def _invoke(store: SQLiteStore, method: str):
        return await (store.delete_user("u1") if method == "delete_user"
                      else store.hard_delete_text("t1"))

    @pytest.mark.parametrize("method", ["delete_user", "hard_delete_text"])
    async def test_mid_failure_rolls_back_everything(self, tmp_path, monkeypatch, method):
        # ---- 阶段一：数总写语句数，并证明这个夹具真的让方法改动了行（探针自效性）----
        store_a, db_a = _fresh_store(tmp_path)
        await self._seed(store_a)
        before_a = _row_counts(db_a)
        box = _instrument(store_a, monkeypatch)
        await self._invoke(store_a, method)
        total_writes = box[-1].writes
        assert total_writes >= 2, f"{method} 只发出 {total_writes} 条写语句，夹具失去意义"
        assert _row_counts(db_a) != before_a, (
            f"夹具没让 {method} 改动任何行 —— 下面的断言会恒绿（探针空转）")

        # ---- 阶段二：在最后一条写语句处注入失败 → 必须全回滚、无半写 ----
        store_b, db_b = _fresh_store(tmp_path)
        await self._seed(store_b)
        before_b = _row_counts(db_b)
        box_b = _instrument(store_b, monkeypatch, fail_at=total_writes)
        with pytest.raises(Exception) as ei:
            await self._invoke(store_b, method)
        assert _INJECTED in _chain_text(ei.value), (
            f"注入没生效（异常链里没有哨兵）：{_chain_text(ei.value)} —— 本用例没验到东西")
        assert box_b[-1].writes == total_writes, (
            f"注入点落空：期望在第 {total_writes} 条写语句炸，实际只发出 {box_b[-1].writes} 条")

        assert _row_counts(db_b) == before_b, (
            f"{method} 中途失败后留下了半写状态 —— 多步写的原子性被破坏了。\n"
            f"  前={before_b}\n  后={_row_counts(db_b)}")


class TestReadOnlyScopesUnaffected:
    """只读作用域上 commit/rollback 是 no-op —— 208 个读方法语义与开销都不该变。"""

    async def test_read_only_scope_exit_is_clean(self, tmp_path: Path):
        store, db_path = _fresh_store(tmp_path)
        await store._ensure_initialized()
        ctx = _ConnectionContext(await aiosqlite.connect(db_path))
        async with ctx as conn:            # 只读：没有事务可提交
            cursor = await conn.execute("SELECT count(*) FROM texts")
            assert (await cursor.fetchone())[0] == 0

    async def test_read_methods_still_work(self, tmp_path: Path):
        store, _ = _fresh_store(tmp_path)
        assert await store.get_conversations("usr_nobody") == []
        assert await store.get_remote_user_profile("usr_nobody") is None
