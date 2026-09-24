"""`storage/pg_identity_sync.py` 的锁 —— 需要真 PG。

**为什么是真库用例**：这条对齐读的是 `pg_catalog` 与表里的 `max(id)`，判据是「`nextval`
之后**真的**拿到了 max+1」。用假连接替掉 asyncpg，锁掉的只是「SQL 字符串长什么样」，
而那正是缺陷 25「签名的代理代替 SQL 事实」那一谱系的假绿。

**场景全部自建**（随机名的探测表 + `OVERRIDING SYSTEM VALUE` 灌入），不依赖库里已有数据
的分布 —— 拿生产读数当判据的话，换个库锁就失灵了。

PG 连不上时显式 skip，理由见 `tests/test_postgres_store.py` 模块头（缺陷 21 同型）。
"""

from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import PG_ENV  # noqa: E402

from storage.pg_identity_sync import align_identity_sequences  # noqa: E402
from storage.postgres_store import PostgresStore  # noqa: E402


def _dsn() -> str:
    return os.environ["DATABASE_URL"]


_pg = PG_ENV.skipif("身份序列对齐用例")


@pytest.fixture
async def conn():
    c = await asyncpg.connect(_dsn())
    try:
        yield c
    finally:
        await c.close()


@asynccontextmanager
async def _identity_table(conn: asyncpg.Connection, id_column_ddl: str = "id"):
    """建一张随机名的 identity 表，用完删掉。

    随机名就是「不维护表清单」的探针：这个名字在任何源码里都不存在。
    """
    name = f"_pgid_probe_{uuid.uuid4().hex[:10]}"
    await conn.execute(
        f'CREATE TABLE "{name}" ({id_column_ddl} bigint GENERATED ALWAYS AS IDENTITY '
        f"PRIMARY KEY, v text)"
    )
    try:
        yield name
    finally:
        await conn.execute(f'DROP TABLE IF EXISTS "{name}"')


async def _seq_state(conn: asyncpg.Connection, table: str) -> tuple[int, bool]:
    """序列的 (last_value, is_called)。探测表名是小写安全标识符，无需引号。"""
    row = await conn.fetchrow(f'SELECT last_value, is_called FROM "{table}_id_seq"')
    return row["last_value"], row["is_called"]


def _moved_for(moved: list[tuple[str, str, int, int]], table: str) -> list[tuple[str, str, int, int]]:
    return [m for m in moved if m[0] == table]


@_pg
async def test_lagging_sequence_is_moved_so_next_insert_lands_after_max(conn):
    """显式 id 灌入 + 序列落后 → 对齐后不带 id 的 INSERT 拿到 max+1。"""
    async with _identity_table(conn) as t:
        await conn.execute(
            f'INSERT INTO "{t}" (id, v) OVERRIDING SYSTEM VALUE'
            f" VALUES (1, 'a'), (2, 'b'), (3, 'c')"
        )
        # 落后态本身是个前置断言：OVERRIDING 不推进序列，它会停在起点。
        assert await _seq_state(conn, t) == (1, False)

        moved = await align_identity_sequences(conn)

        assert _moved_for(moved, t) == [(t, "id", 1, 4)]
        assert await conn.fetchval(f'INSERT INTO "{t}" (v) VALUES (\'d\') RETURNING id') == 4


@_pg
async def test_leading_sequence_is_not_rolled_back(conn):
    """序列领先于 max(id) → 不回退（把 500 拉回 4 会让 id 复用，比落后更危险）。"""
    async with _identity_table(conn) as t:
        await conn.execute(
            f'INSERT INTO "{t}" (id, v) OVERRIDING SYSTEM VALUE'
            f" VALUES (1, 'a'), (2, 'b'), (3, 'c')"
        )
        await conn.execute(f'SELECT setval(\'{t}_id_seq\', 500, true)')

        moved = await align_identity_sequences(conn)

        assert _moved_for(moved, t) == []
        assert await _seq_state(conn, t) == (500, True)
        assert await conn.fetchval(f'INSERT INTO "{t}" (v) VALUES (\'d\') RETURNING id') == 501


@_pg
async def test_empty_table_sequence_is_left_alone(conn):
    """空表不动 —— `max(id)` 是 NULL 时那个序列位置没有任何信息量。

    判别力：若把 NULL 当成 0 处理，这里会 setval(...,0,true) → 读回 (0, True)。
    """
    async with _identity_table(conn) as t:
        await conn.execute(f'SELECT setval(\'{t}_id_seq\', 42, true)')

        moved = await align_identity_sequences(conn)

        assert _moved_for(moved, t) == []
        assert await _seq_state(conn, t) == (42, True)


@_pg
async def test_alignment_is_idempotent(conn):
    """连跑两次结果相同：第二次一个也不动。"""
    async with _identity_table(conn) as t:
        await conn.execute(
            f'INSERT INTO "{t}" (id, v) OVERRIDING SYSTEM VALUE'
            f" VALUES (1, 'a'), (2, 'b'), (3, 'c')"
        )

        first = await align_identity_sequences(conn)
        state_after_first = await _seq_state(conn, t)
        second = await align_identity_sequences(conn)

        assert _moved_for(first, t) == [(t, "id", 1, 4)]
        assert _moved_for(second, t) == []
        assert await _seq_state(conn, t) == state_after_first


@_pg
async def test_new_identity_table_is_aligned_without_being_registered(conn):
    """新建的 identity 表不改代码即被对齐 —— 列是现查 catalog 得来的，没有登记表。

    顺带锁住标识符引号那条路：列名带大写时，不引号就会拼出 `max(Id)` 并由 PG 折叠成
    `id` —— 探测表里没有 `id` 列，那会直接报错（而不是静默改错列）。
    """
    async with _identity_table(conn) as t, _identity_table(conn, id_column_ddl='"MixedCaseId"') as t2:
        await conn.execute(
            f'INSERT INTO "{t}" (id, v) OVERRIDING SYSTEM VALUE VALUES (5, \'a\'), (6, \'b\')'
        )
        await conn.execute(
            f'INSERT INTO "{t2}" ("MixedCaseId", v) OVERRIDING SYSTEM VALUE'
            f" VALUES (1, 'a'), (2, 'b')"
        )

        moved = await align_identity_sequences(conn)

        assert _moved_for(moved, t) == [(t, "id", 1, 7)]
        assert _moved_for(moved, t2) == [(t2, "MixedCaseId", 1, 3)]
        assert await conn.fetchval(f'INSERT INTO "{t}" (v) VALUES (\'c\') RETURNING id') == 7


@_pg
async def test_store_startup_runs_the_alignment(conn):
    """接线锁：真正会生效的是**启动时**那次调用，不是这个函数本身。

    只在测试里直接调 `align_identity_sequences` 的话，把 `_ensure_initialized` 里那行删掉
    也全绿 —— 那锁住的是函数存在，不是缺陷被修。这里造好落后态，再让一个**新的 store
    对象**走它自己的启动路径（建池 → 跑迁移 → 对齐），对齐必须自己发生。
    """
    async with _identity_table(conn) as t:
        await conn.execute(
            f'INSERT INTO "{t}" (id, v) OVERRIDING SYSTEM VALUE VALUES (1, \'a\'), (2, \'b\')'
        )

        store = PostgresStore(_dsn())
        try:
            await store._ensure_initialized()
        finally:
            await store.close()

        assert await conn.fetchval(f'INSERT INTO "{t}" (v) VALUES (\'c\') RETURNING id') == 3
