"""024_users_role.sql / 025_retire_is_admin.sql：在真 PG 上验收加列、回填、删列。

SQLite 侧同一语义由 `tests/test_users_role_migration.py` 钉着，**两侧不能互相当证据**：
实现机制不同 —— SQLite 靠 `_apply_migration` 的 ADD COLUMN 谓词整份跳过，删列在
sqlite_store.py 的退役列块里（因为 013 每轮把 is_admin 加回来）；PG 没有账本，靠 024 的
DO 块现查 information_schema，删列由 025 的 `DROP COLUMN IF EXISTS` 承担。

每个用例自建一个一次性数据库（跑完即 DROP），不碰 DATABASE_URL 指向的共享库 ——
本文件会 DROP users.role 来造旧形态，在共享库上做会波及同会话的其它 PG 用例。
"""

from __future__ import annotations

import os
import re
import uuid

import asyncpg

from conftest import PG_ENV
from core import roles
from storage.postgres_store import PostgresStore

_pg = PG_ENV.skipif("PG 角色迁移用例")


def _base_dsn() -> str:
    return os.environ["DATABASE_URL"]


def _with_db(dsn: str, name: str) -> str:
    return re.sub(r"/[^/]*$", f"/{name}", dsn)


class _ThrowawayDb:
    """自建/自毁的一次性库；换 DSN 的最后一段库名。"""

    def __init__(self) -> None:
        self.name = f"cdrole_{uuid.uuid4().hex[:8]}"

    async def __aenter__(self) -> str:
        conn = await asyncpg.connect(_base_dsn())
        try:
            await conn.execute(f'CREATE DATABASE "{self.name}"')
        finally:
            await conn.close()
        return _with_db(_base_dsn(), self.name)

    async def __aexit__(self, *exc) -> None:
        conn = await asyncpg.connect(_base_dsn())
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
        finally:
            await conn.close()


async def _columns(dsn: str) -> set[str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='users'")
        return {r["column_name"] for r in rows}
    finally:
        await conn.close()


async def _legacy_shape(dsn: str) -> str:
    """把已迁移的库改回「pre-024 形态」：无 role，有 is_admin，且该用户 is_admin=true。

    返回用户 id。等价于 024 落地前那一版生产库。
    """
    store = PostgresStore(dsn)
    await store._ensure_initialized()
    user = await store.create_user(
        f"usr_{uuid.uuid4().hex}", f"legacy_{uuid.uuid4().hex[:8]}", "x")
    await store.close()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("ALTER TABLE users DROP COLUMN role")
        # 001_init.sql 里 is_admin 是 SMALLINT（不是 BOOLEAN）：回填写的是 `= 1`，
        # 照 013_admin.sql 那行的 SQLite 声明（BOOLEAN）造会造出一个更严苛的库，
        # 让 024 在真 PG 上以 `boolean = integer` 报错——那不是生产形态。
        await conn.execute("ALTER TABLE users ADD COLUMN is_admin SMALLINT DEFAULT 0")
        await conn.execute("UPDATE users SET is_admin = 1 WHERE id = $1", user["id"])
    finally:
        await conn.close()
    return user["id"]


@_pg
async def test_pg_fresh_db_gets_role_and_loses_is_admin():
    """(P1) 全新库跑完迁移：role 在，is_admin 不在。"""
    async with _ThrowawayDb() as dsn:
        store = PostgresStore(dsn)
        await store._ensure_initialized()
        await store.close()

        cols = await _columns(dsn)
        assert "role" in cols, f"新库 users 缺 role；实际列={sorted(cols)}"
        assert "is_admin" not in cols, "025 没把 is_admin 删掉"


@_pg
async def test_pg_backfill_promotes_legacy_admin_then_drops_column():
    """(P2) 旧形态库里 is_admin=true 的行被回填成 admin，且 is_admin 随即被 025 删掉。"""
    async with _ThrowawayDb() as dsn:
        uid = await _legacy_shape(dsn)

        store = PostgresStore(dsn)
        await store._ensure_initialized()
        await store.close()

        conn = await asyncpg.connect(dsn)
        try:
            role = await conn.fetchval("SELECT role FROM users WHERE id = $1", uid)
        finally:
            await conn.close()

        assert role == roles.ADMIN, f"回填没把 is_admin=true 提成 admin，实际 {role!r}"
        assert "is_admin" not in await _columns(dsn), "025 没把 is_admin 删掉"


@_pg
async def test_pg_post_backfill_role_survives_restart():
    """(P3·时序锁) 回填跑过之后，重启不得再回填——被改过的 role 要在重启后原样保留。

    判别力所在：PG 的谓词是「role 列是否存在」。若 DO 块退化成无条件执行，重启时
    `UPDATE ... WHERE is_admin = 1` 会因为 025 已把列删掉而**直接报错**（初始化整体失败），
    或（列还在的退化形态下）把这条降级过的用户提回 admin。两种症状本条都拦得住。
    """
    async with _ThrowawayDb() as dsn:
        uid = await _legacy_shape(dsn)

        store = PostgresStore(dsn)
        await store._ensure_initialized()
        await store.set_user_role(uid, roles.GUEST)
        await store.close()

        # 第二次启动：全量重放 migrations_pg/*
        store = PostgresStore(dsn)
        await store._ensure_initialized()
        await store.close()

        conn = await asyncpg.connect(dsn)
        try:
            role = await conn.fetchval("SELECT role FROM users WHERE id = $1", uid)
        finally:
            await conn.close()
        assert role == roles.GUEST, f"重启把 role 覆盖成 {role!r} —— 024 的回填重跑了"
