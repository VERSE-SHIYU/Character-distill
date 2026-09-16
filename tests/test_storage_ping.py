"""`StorageBase.ping()` 的契约、两个后端的实现、以及它的判别力（缺陷 41）。

**为什么要有这个探针**：就绪端点在 PG 凭据错的时候必须红。此前的三层探针在那种场合
全是绿的 —— `pg_isready` 只握手不认证、启动期的库异常被 print 掉、`/api/health` 压根
不碰库。三者共用一个非红信号，于是「库不可用」与「一切正常」分不出来（§四）。

**为什么 ping 要执行语句而不是只取连接**：池可能交回一条已失效的连接，而 acquire 路径
本身完全正常。`test_sqlite_ping_raises_when_locked_out_mid_flight` 就是这句话的现场 ——
连接取得到，语句跑不动。

**为什么 SQLite 侧读 `sqlite_master` 而不是 `SELECT 1`**：不带 FROM 的 SELECT 不读文件、
不取锁、不开读事务，**结构上不可能失败**，于是「取到了连接」与「库答得上话」合一 ——
探针回到缺陷 41 那个全绿形态。B-1b 就是这条的判别面。

**变异（B-1/B-1b/B-2，驱动在 `tests/perf/ping_mutations.py`）**：
  - B-1 让 SQLite 的 ping 只取连接、不执行语句 → `test_..._locked_out_mid_flight` 红；
  - B-1b 把语句退回 `SELECT 1` → 同上（Linux 红；Windows 见那条用例的 docstring）；
  - B-2 摘掉 `StorageBase.ping` 上的 `@abstractmethod` → 契约锁那两条红。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import PG_ENV

from storage.base import StorageBase
from storage.postgres_store import PostgresStore
from storage.sqlite_store import SQLiteStore


def _dsn() -> str:
    return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/charsim_test")


_pg = PG_ENV.skipif(
    "StorageBase.ping 的 PG 用例",
    # 原文如此（复制粘贴残留 PostgresStore）：本轮是纯重构，逐字保留，单独修。
    disabled_label="PostgresStore 用例",
)


# ── 契约锁 ────────────────────────────────────────────────────────────────────

def _stub_subclass(*omit: str) -> type:
    """造一个 StorageBase 子类：除 `omit` 外的抽象方法全部给个空实现。

    `omit` 里没有的名字都会进 `__abstractmethods__`，所以「只漏 ping」这一件事要能被
    单独表达出来 —— 这正是契约锁的前提。若把整个类写死成一个字符串常量的空子类，
    它会因为漏掉其它 90 个抽象方法而恒抛 TypeError，锁就瞎了（红的成因不可辨，§四）。
    """
    ns: dict = {}
    for name in sorted(StorageBase.__abstractmethods__ - set(omit)):
        async def _stub(self, *args, **kwargs):
            return None
        _stub.__name__ = name
        ns[name] = _stub
    return type("_StubStore", (StorageBase,), ns)


def test_storage_base_subclass_without_ping_cannot_be_instantiated():
    """漏实现 ping 的 StorageBase 子类必须**在实例化时**就炸，而不是运行到才炸。

    这是契约锁：`@abstractmethod` 一旦被摘掉，漏实现就变成静默的 —— 实例照常建出来，
    直到就绪端点调用它才 AttributeError，而且那时已经没有任何东西拦在部署路径上。
    """
    cls = _stub_subclass("ping")
    with pytest.raises(TypeError) as excinfo:
        cls()
    assert "ping" in str(excinfo.value), (
        f"实例化确实抛了 TypeError，但点名的不是 ping：{excinfo.value}")


def test_contract_lock_has_teeth():
    """负控：把 ping 也实现掉，同一套桩子必须能建出实例。

    没有这一条，上面那条可能只是因为「桩子造得不对、恒抛」而绿 —— 那种绿与契约生效的
    绿长得一样。
    """
    cls = _stub_subclass()
    assert "ping" not in cls.__abstractmethods__
    instance = cls()
    assert isinstance(instance, StorageBase)
    assert callable(getattr(instance, "ping"))


# ── SQLite：真 ping ───────────────────────────────────────────────────────────

async def test_sqlite_ping_succeeds_on_usable_database(tmp_path: Path):
    """正常库上 ping 静默返回（不返回值、不抛异常）。"""
    store = SQLiteStore(str(tmp_path / "ok.db"))
    assert await store.ping() is None


async def test_sqlite_ping_raises_when_db_path_is_unusable(tmp_path: Path):
    """库文件路径不可用时 ping 必须抛异常，不能静默。

    构造：路径的上一层是个普通文件 —— `_ensure_initialized` 建目录就失败。
    """
    blocker = tmp_path / "afile"
    blocker.write_text("not a directory", encoding="utf-8")
    store = SQLiteStore(str(blocker / "x.db"))
    with pytest.raises(OSError):
        await store.ping()


async def test_sqlite_ping_raises_when_locked_out_mid_flight(tmp_path: Path):
    """**连接取得到、语句跑不了** —— 缺陷 41 那句话的现场。

    另一个连接持 `BEGIN EXCLUSIVE` 时删档模式（rollback journal）下的读拿不到共享锁，
    于是 `_connect()` 成功（`journal_mode` 那句 PRAGMA 允许在锁下跳过，日志留一条 warning），
    而 ping 的那句读语句超时抛 `OperationalError: database is locked`。

    **这条是 B-1 与 B-1b 的判别面**：B-1 把 ping 改成「只取连接、不执行语句」、B-1b 把
    语句退回 `SELECT 1`，两种情况下面每一句都还成立，唯独异常不再抛出。没有它，
    「ping 真的读到了库」只是实现细节，没有任何用例在管。

    **平台差异（实测，别按经验读这条的绿）**：判别力只在 Linux/sqlite 3.46（生产镜像那套）
    成立 —— 那边的 `SELECT 1` 不开读事务，锁下照样过。本机 Windows 的 sqlite 3.49 则连
    `SELECT 1` / `SELECT 1+1` / `PRAGMA schema_version` 都在锁下抛 `database is locked`
    （同一条连接上只有 `busy_timeout` / `foreign_keys` 这两个锁无关的 PRAGMA 能过），
    于是**任何**语句都会让这条用例变绿，B-1b 在 Windows 上是假绿。根因是版本差异不是平台：
    生产跑的就是 Linux 那套，所以以 Linux 的结论为准，Windows 的绿不构成证据。

    代价：SQLite 的 busy_timeout 是 5s，两处等待叠加约 15s。这是生产语义的一部分
    （`_connect()` 里写死的），不为跑得快去动它。

    **前提自检**：上面那段平台差异不靠人记得住 —— 用例自己先验一次。判定不成立时
    `pytest.skip`（写清版本号），不装作绿。
    """
    db = tmp_path / "locked.db"
    store = SQLiteStore(str(db))
    await store._ensure_initialized()        # 已初始化 = 生产上那个跑着的 store

    holder = sqlite3.connect(str(db), isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")

        # `SELECT 1` 不读任何表，本不该需要读锁。它在同一条排他锁下也抛 locked 时，
        # 说明本环境的引擎对**任何**语句都判锁 —— 那么 B-1b（把语句退回 `SELECT 1`）
        # 照样会红，红源却与本用例的命题无关。判据不成立时报「不适用」，
        # 因为假绿与真绿长得一样，而两者要防的是同一件事。
        probe = sqlite3.connect(str(db), isolation_level=None, timeout=0)
        try:
            probe.execute("SELECT 1")
        except sqlite3.OperationalError:
            pytest.skip(
                f"本环境 sqlite {sqlite3.sqlite_version} 在排他锁下连 `SELECT 1` 都抛 "
                "database is locked ——「读文件的语句」与「不读文件的语句」在此不可区分，"
                "判别场景在本环境不成立，B-1b 须在 Linux 验证")
        finally:
            probe.close()

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await store.ping()
    finally:
        holder.rollback()
        holder.close()


# ── PG：真 ping ───────────────────────────────────────────────────────────────

@_pg
async def test_pg_ping_succeeds_on_reachable_database():
    """真 PG 上 ping 静默返回；顺带证明它带起了池与迁移（就绪含 schema 就绪）。"""
    store = PostgresStore(_dsn())
    try:
        assert await store.ping() is None
    finally:
        await store.close()
