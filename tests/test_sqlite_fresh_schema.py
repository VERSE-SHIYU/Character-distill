"""真建库、真跑迁移：全新的 SQLite 库必须跑完所有迁移、列必须齐。

为什么需要这个文件：`tests/test_schema_parity.py` 是正则扫 `.sql` 文件文本，
「写了」就算「跑成了」，不看真库。于是 067_embedding_config.sql 的
`ADD COLUMN IF NOT EXISTS`（PG 语法，SQLite 不支持）在 SQLite 上从未生效过——
executescript 解析期即抛 `near "EXISTS": syntax error`，被「猜错误串」式的
except 漏掉，新库永远缺 users.embedding_key / embedding_region，
`get_user_api_config` 的 `SELECT u.embedding_key` 直接抛 OperationalError。

本文件补上那个盲区：建真库 → 真跑 `_ensure_initialized` → 真读 PRAGMA。
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path

import aiosqlite

from storage.sqlite_store import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
PG_DIR = ROOT / "storage" / "migrations_pg"

EMBEDDING_COLS = ("embedding_key", "embedding_region")

# texts 上**必须一直不存在**的退役列（缺陷 87）：056 已空操作，094 只在老库上真跑一次。
# 判据是「重启后列集不变 **且** 重启时 094 没执行」——见
# test_retired_texts_columns_stay_retired_after_restart。
RETIRED_TEXT_COLS = ("content_resolved", "coref_resolved")

# SQLite 自建、不出现在任何迁移文件里的内部表
_SQLITE_INTERNAL_TABLES = frozenset({"sqlite_sequence"})
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(?P<table>\w+)", re.IGNORECASE)
_COMMENT_RE = re.compile(r"--[^\n]*")


def _pg_declared_tables() -> set[str]:
    """`migrations_pg/` 声明的全部表名（去注释后扫 CREATE TABLE）。"""
    names: set[str] = set()
    for p in sorted(PG_DIR.glob("*.sql")):
        sql = _COMMENT_RE.sub("", p.read_text(encoding="utf-8"))
        names |= {m.group("table") for m in _CREATE_TABLE_RE.finditer(sql)}
    return names


def _tables_in(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


async def _init(store: SQLiteStore, capsys) -> str:
    """跑一次懒初始化，返回本次建库/迁移打到 stdout 的全部输出。"""
    await store._ensure_initialized()
    return capsys.readouterr().out


def _columns(db_path: str, table: str = "users") -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _fresh_store(tmp_path) -> tuple[SQLiteStore, str]:
    db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
    return SQLiteStore(db_path), db_path


class TestFreshSqliteSchema:
    async def test_fresh_db_has_embedding_columns(self, tmp_path, capsys):
        """(a) 全新库 init 后 users 表含 embedding_key / embedding_region。"""
        store, db_path = _fresh_store(tmp_path)
        await _init(store, capsys)
        cols = _columns(db_path)
        for col in EMBEDDING_COLS:
            assert col in cols, f"新库 users 缺 {col}；实际列={sorted(cols)}"

    async def test_get_user_api_config_ok_on_fresh_db(self, tmp_path):
        """(b) 新库上 get_user_api_config 的 SELECT u.embedding_key 不再抛。"""
        store, _ = _fresh_store(tmp_path)
        cfg = await store.get_user_api_config(f"usr_{uuid.uuid4().hex}")
        assert cfg["embedding_region"] == "cn"

    async def test_second_init_adds_nothing_and_stays_silent(self, tmp_path, capsys):
        """(c) 同一个库连跑两次 init：列齐，且**整库零失败输出**。

        这条才验得到 PRAGMA 前置：只跑一次的话，裸 ALTER 在新库上本来也成功。
        第二次跑时列已存在，确定性写法根本不发那条 ALTER（PRAGMA 已跳过）。

        全强度断言（缺陷 15 收口）：此前第二次 init 另有一处**既有**噪音 ——
        034_post_enhancements 的 except 连 duplicate column 都不吞、直接 print，于是
        「同库两次 init 无失败输出」只能退到「不含 067 的失败」。034 改确定性执行后
        这层退让不再必要，恢复整库口径。

        **users 列集必须两次相等**（缺陷 26 收口）：本用例名里写着 "adds nothing"，
        此前却只断言 embedding 两列 + 无失败输出 —— 名字承诺的比断言的多，于是
        「第二次 init 把 api_key/base_url/model 加回来、驻留成 25 列」这条路一直没被看见。
        「重启过的库」才是生产实际运行的那个 schema（见 `TestPgFreshSchemaClosure::
        test_restarted_sqlite_matches_fresh_pg`），所以这条断言是本用例的正文。
        """
        db_path = str(tmp_path / "twice.db")
        out1 = await _init(SQLiteStore(db_path), capsys)
        cols_after_first = _columns(db_path)
        out2 = await _init(SQLiteStore(db_path), capsys)
        assert "failed" not in out1, f"首次 init 打了失败行:\n{out1}"
        assert "failed" not in out2, f"第二次 init 打了失败行:\n{out2}"
        cols = _columns(db_path)
        for col in EMBEDDING_COLS:
            assert col in cols, f"两次 init 后 users 缺 {col}；实际列={sorted(cols)}"
        added = sorted(cols - cols_after_first)
        removed = sorted(cols_after_first - cols)
        assert cols == cols_after_first, (
            f"第二次 init 改动了 users 列集 —— 「全新库」与「重启过的库」不是同一个 schema。"
            f"新加={added} 少了={removed}；两次列集={sorted(cols_after_first)} / {sorted(cols)}")

    async def test_fresh_init_prints_no_failure(self, tmp_path, capsys):
        """(d) 兜整类盲区：建库期间 stdout 不得出现任何失败行 —— **两次 init 都算**。

        一处断言覆盖全部迁移文件——将来任何迁移在 SQLite 上跑失败立刻红，
        不必等到有人用到那张表。断言 "failed" 而非仅 "migration failed"：
        后者漏掉 `Migration <file> failed: ...` 这类文件名插在中间的格式，
        而 "failed" 是它的超集。
        成功路径本就不打印含 failed 的行（init 的 print 只在异常处理里，且都已删），
        故超集不会误报。

        第二次 init 必须覆盖：只跑一次的话，「已建库重跑」这条正常路径根本不会经过
        `ADD COLUMN` 的失败分支 —— 034 假失败正是只在第二次出现。
        """
        db_path = str(tmp_path / "twice.db")
        out1 = await _init(SQLiteStore(db_path), capsys)
        out2 = await _init(SQLiteStore(db_path), capsys)
        assert "failed" not in out1, f"新建 sqlite 库的输出里出现失败行:\n{out1}"
        assert "failed" not in out2, f"第二次 init 的输出里出现失败行:\n{out2}"

    async def test_partial_state_fills_only_missing_column(self, tmp_path, capsys):
        """(e) 只缺一列时只补那一列——PRAGMA 前置与「猜错误串」的真正分界。

        造一个「embedding_key 已有、embedding_region 缺」的库（模拟上一版迁移留下的
        半成品）。确定性写法只 ALTER 缺的那列；而 executescript 是遇错即中断整个脚本，
        第一条 ALTER 撞 duplicate 就会让第二条永不执行 → embedding_region 永远补不上。
        所以这条用例是 (c) 补不到的判别力：(c) 在「executor 改回裸 ALTER + except」下
        仍绿（那条 except 吞的正是它自己引发的 duplicate）。
        """
        db_path = str(tmp_path / "partial.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                embedding_key TEXT DEFAULT ''
            )"""
        )
        conn.commit()
        conn.close()

        await _init(SQLiteStore(db_path), capsys)
        cols = _columns(db_path)
        for col in EMBEDDING_COLS:
            assert col in cols, f"半成品库 init 后缺 {col}；实际列={sorted(cols)}"

    async def test_retired_texts_columns_stay_retired_after_restart(self, tmp_path, capsys, monkeypatch):
        """(f) texts 的退役列（缺陷 87）：新库没有，**重启后仍然没有**，且**重启不重写这张表**。

        056 现在是空操作（两列已由 094 退役；无迁移账本时每轮都会执行它，故不再 `ADD`）。
        于是：新库从没建过这两列 ⇒ 094 的每句 DROP 都已生效 ⇒ **整份跳过**；老库（列还在）
        ⇒ 094 真正执行一次 DROP，之后再跑同样整份跳过。

        故「二次 init 后列集不变」与「二次 init 时 094 整份跳过」是同一件事的两面，本用例
        两条都断言：前者是结果，后者是机制。**必须两条都锁** —— 只比列集会漏掉「每轮把列
        加回来又删掉」：那种形态下列集照样相等，但 `texts` 存的是全文，每次 init 都要把整张
        表重写一遍（`DROP COLUMN` 在 SQLite 里是「建新表 + 拷数据 + 换名」）。

        **变异（实测）**：把 056 恢复成两条 `ADD COLUMN` → 每轮先把列加回来 ⇒ 094 每轮都得
        真发一次 DROP ⇒ 「整份跳过」断言红，而列集断言仍绿（094 又删回去了）。这正是加这条
        断言的判别力所在。

        ⚠ 与 `tests/test_migration_dispatch.py::test_already_satisfied_drop_column_is_stripped`
        分工：那条锁执行器 DROP 支自身的剥除逻辑，本条锁端到端「重启态下 094 不再执行」。
        """
        db_path = str(tmp_path / "retired.db")
        await _init(SQLiteStore(db_path), capsys)
        first = _columns(db_path, "texts")

        ran: list[str] = []
        real_script = aiosqlite.Connection.executescript

        async def _spy(conn, sql):
            ran.append(sql)
            return await real_script(conn, sql)

        monkeypatch.setattr(aiosqlite.Connection, "executescript", _spy)
        await _init(SQLiteStore(db_path), capsys)
        monkeypatch.undo()
        second = _columns(db_path, "texts")
        for cols, when in ((first, "首次 init 后"), (second, "第二次 init 后")):
            for col in RETIRED_TEXT_COLS:
                assert col not in cols, f"{when} texts 仍有退役列 {col}；实际列={sorted(cols)}"
        assert first == second, (
            f"第二次 init 改动了 texts 列集 —— 「全新库」与「重启过的库」不是同一个 schema。"
            f"新加={sorted(second - first)} 少了={sorted(first - second)}")

        # SQLite 侧只有 094 是 `.sql` 里的 DROP COLUMN（其余退役走 Python 表重建），
        # 故「有 DROP COLUMN 发给 SQLite」即「094 没被整份跳过」。
        executed = [s for s in ran if "DROP COLUMN" in s.upper()]
        assert not executed, (
            f"重启时 094 把 DROP COLUMN 真的执行了（{len(executed)} 次）—— texts 的整张表"
            f"因此被重写；每一次启动都白付这个代价。首条脚本片段={executed[0][:120]!r}")


class TestExemptionClosedLoop:
    """豁免出口的闭环（缺陷 21）。

    锁的是**症状本身**，不是「文件有没有登记」这个代理指标 —— 后者可被合法豁免绕过。
    实测反例（2026-09-13）：`079_remote_user_profiles.sql` 从没接进 SQLite 执行器，
    而 `test_migration_dispatch` 一直绿着，因为它躺在 `_NOT_APPLIED` 里带理由豁免。
    **三把锁各自尽职、全部绿，库仍然缺表**：

    - `test_migration_dispatch` 只管「有没有归宿」，不管归宿是否可接受；
    - `test_schema_parity` 是正则扫 `.sql` 文本，079 文件在盘上就认为两侧对称；
    - `TestFreshSqliteSchema` 只断言 init 无失败输出与 embedding 两列 —— 没有任何东西
      试图建那张表，所以不会失败。

    所以豁免机制本身成了新的静默通道：写下理由即永久放行，而理由里声明的后果
    （「新库缺表、代码在用」）没有任何东西去验。本类补上那一半。

    真源选 `migrations_pg/`：PG 执行器是 `sorted(glob('*.sql'))`（`postgres_store.py`），
    **目录即清单、没有豁免出口** —— 故「PG 目录里声明的表」就是「应该存在的表」的可靠定义。

    **变异判别力（实测）**：把 079 从次序元组移回执行器的豁免表
    `_MIGRATIONS_NOT_APPLIED` → `test_fresh_db_covers_every_table_pg_declares` 红，而
    `test_every_migration_file_is_dispatched` **仍绿**。旧锁抓不到的那一类，正是本锁抓的。

    **镜像（缺陷 23）**：本锁管的是**测试 / 本地后端**（SQLite 新库）。**生产后端**（真 PG 库）
    那一面在 `tests/test_postgres_store.py::TestPgFreshSchemaClosure` —— 同一真源的反向断言
    （真 PG 库 ⊇ `migrations_pg/` 声明表 ∪ `postgres_store.py` 引用表）。两条合起来才是完整
    闭环；分成两条是因为一条只需 SQLite（无 PG 也能跑），一条必须有真 PG。另有 text 层的
    `tests/test_schema_parity.py` 断言两目录的表 / 列集合相等 —— 三层各管各的。

    **列级**在 `TestPgFreshSchemaClosure::test_fresh_sqlite_and_fresh_pg_have_the_same_columns`，
    但它**故意不用上面这个「真库 ⊇ 文本声明」的形状**：`DROP TABLE` 0 处而 `DROP COLUMN`
    9 处（2026-09-23 现跑现数），本仓真的删过列（PG 声明式 DROP + SQLite 的裸 DROP 与
    Python 表重建），而文本提取器只认 CREATE TABLE + ADD COLUMN、DROP 看不见 → 套用本
    形状当场红，且只能靠豁免清单救，而豁免即永久放行。列级改比「另一侧真库」。
    **别把两处统一成同一个形状**，理由是写在代码里的。
    """

    async def test_fresh_db_covers_every_table_pg_declares(self, tmp_path, capsys):
        store, db_path = _fresh_store(tmp_path)
        await _init(store, capsys)
        missing = sorted(_pg_declared_tables() - _tables_in(db_path) - _SQLITE_INTERNAL_TABLES)
        assert not missing, (
            f"这些表 `migrations_pg/` 声明了、SQLite 新库却没有：{missing}。"
            "两个 store 的 schema 漂移了 —— 要么把对应迁移接进 `storage/sqlite_store.py` "
            "的次序元组（并写清位次理由），要么在该文件的 `_MIGRATIONS_NOT_APPLIED` 里"
            "显式豁免**并说明该表为何 SQLite 侧不需要**。")

