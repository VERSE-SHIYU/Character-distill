"""091_users_role.sql：加列 + 由 is_admin 回填，且「只跑一次」的时序必须钉住。

这条迁移的难点不是 SQL，是**时序**。users 上的 is_admin 与 role 是一组拉锯：

- `013_admin.sql` 每轮启动都把 is_admin 加回来（默认 0），与 018 加回
  api_key/base_url/model 同形；sqlite_store.py 的退役列块每轮把它删掉。
- 091 登记在 BEFORE 段，好让回填读得到 is_admin（退役列块夹在 BEFORE 与 AFTER 之间）。
- 「只跑一次」不靠任何账本，靠 `_apply_migration` 的 ADD COLUMN 谓词：脚本里每个
  ADD COLUMN 的列都已存在 → **整份脚本跳过**（含尾部 UPDATE 回填）。这条谓词一旦退化成
  「逐句跳过」，被后台改过的 role 会在下次启动被回填重新覆盖。

所以本文件的正文是最后一个用例（时序锁），前面几个是它的地基。

注：`_ensure_initialized` 的守卫是**每实例**的（`self._initialized`），所以「下一次启动」
一律用新的 `SQLiteStore(db_path)` 表达，复用同一个实例只会拿到空转。
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from core import roles
from storage.sqlite_store import SQLiteStore


def _db(tmp_path, name: str = "users_role.db") -> str:
    return str(tmp_path / name)


async def _boot(db_path: str) -> SQLiteStore:
    """跑一次「启动」（新实例，等价于进程重启）。"""
    store = SQLiteStore(db_path)
    await store._ensure_initialized()
    return store


def _columns(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    finally:
        conn.close()


def _role_of(db_path: str, user_id: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


async def _legacy_db(tmp_path, is_admin_value: int) -> tuple[str, str]:
    """造一个「pre-091 形态」的库：role 列不存在，users 里是 is_admin。

    先跑一次 init 拿到当前 schema（省得手抄几十列），再把 role 列摘掉、把 is_admin
    加回来 —— 等价于 091 落地前那一版库。返回 (db_path, user_id)。
    """
    db_path = _db(tmp_path)
    store = await _boot(db_path)
    user = await store.create_user(
        f"usr_{uuid.uuid4().hex}", f"legacy_{uuid.uuid4().hex[:8]}", "x")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE users DROP COLUMN role")
        conn.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                     (is_admin_value, user["id"]))
        conn.commit()
    finally:
        conn.close()
    return db_path, user["id"]


async def test_fresh_db_has_role_column_with_check(tmp_path):
    """(E1) 新库 users.role 存在，且 CHECK 挡得住未知角色。"""
    db_path = _db(tmp_path)
    await _boot(db_path)

    assert "role" in _columns(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (id, username, username_lower, role)"
                " VALUES ('u_bad', 'bad', 'bad', 'superuser')")
    finally:
        conn.close()


async def test_backfill_promotes_legacy_admin(tmp_path):
    """(E2) is_admin=1 的旧行，回填后 role='admin'。"""
    db_path, uid = await _legacy_db(tmp_path, is_admin_value=1)

    await _boot(db_path)

    assert _role_of(db_path, uid) == roles.ADMIN


async def test_backfill_leaves_non_admin_as_user(tmp_path):
    """(E3) is_admin=0 的旧行，回填后是默认的 role='user'（不提权）。"""
    db_path, uid = await _legacy_db(tmp_path, is_admin_value=0)

    await _boot(db_path)

    assert _role_of(db_path, uid) == roles.USER


async def test_is_admin_is_retired_after_backfill(tmp_path):
    """(E4) 回填用的 is_admin 用完即撤，且重启不会再驻留下来。"""
    db_path, _ = await _legacy_db(tmp_path, is_admin_value=1)
    await _boot(db_path)
    assert "is_admin" not in _columns(db_path), "第一轮 init 后 is_admin 仍在 users 上"

    await _boot(db_path)
    assert "is_admin" not in _columns(db_path), "第二轮 init 后 is_admin 复活了"


async def test_backfilled_role_survives_is_admin_resurrection(tmp_path):
    """(E5·时序锁) 回填跑过之后，重启时 is_admin 被 013 复活成残留值，role 不得被覆盖。

    这是「091 整份跳过」这条谓词的判别力所在：光看结果，is_admin 复活成默认 0 时
    尾部 `UPDATE ... WHERE is_admin = 1` 本来就是空操作，测不出差别。所以这里故意把
    残留值造成 1 —— 一旦 ADD COLUMN 谓词退化、尾部回填重跑，这条用户会被提回 admin，
    断言立刻红；不重跑则 role 保持调用方设定值。

    顺带钉住另一半：复活出来的 is_admin 必须被退役列块再删掉（拉锯每轮都要收敛）。
    """
    db_path = _db(tmp_path, "resurrect.db")
    store = await _boot(db_path)

    user = await store.create_user(
        f"usr_{uuid.uuid4().hex}", f"keeper_{uuid.uuid4().hex[:8]}", "x")
    await store.set_user_role(user["id"], roles.GUEST)

    # 下一次启动前的一瞬：013_admin.sql 已把 is_admin 加回，且带着一条残留的 1
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
        conn.commit()
        # 阳性对照：残留值确实命中尾部回填的 WHERE 条件 —— 所以下面的 role 断言
        # 若是红的，原因只能是「回填重跑了」，不会是「条件没满足」。
        assert conn.execute("SELECT is_admin FROM users WHERE id = ?",
                            (user["id"],)).fetchone()[0] == 1
    finally:
        conn.close()

    await _boot(db_path)

    assert _role_of(db_path, user["id"]) == roles.GUEST, (
        "重启把 role 覆盖了 —— 091 的尾部回填重跑了（ADD COLUMN 谓词退化的典型症状）")
    assert "is_admin" not in _columns(db_path), "复活出来的 is_admin 没被退役列块删掉"
