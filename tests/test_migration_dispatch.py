"""迁移分发与错误处理的**形态锁**（缺陷 15）。

两条不变量，都锁「症状不可能发生」，不是「这次修好了」：

1. `storage/migrations/` 下每个 `.sql` 要么在 sqlite 的迁移次序表里，要么在执行器
   （`storage/sqlite_store.py`）声明的 `_MIGRATIONS_NOT_APPLIED` 里带理由。此前次序是 74
   个手写块，**加一个迁移文件忘了接线没有任何东西报警** —— 先例
   `079_remote_user_profiles.sql` 漏了一整个版本，SQLite 新库因此缺表。

   豁免的事实源在执行器、不在本文件（缺陷 21 的教训）：定义在测试里，读
   `sqlite_store.py` 的人看不见「079 是被有意略过的」。本测试从执行器读那一份，**不自己
   维护副本** —— 豁免只有一个源，不存在两处要同步。

   **但这条锁只管「有没有归宿」，不管「归宿是否可接受」** —— 079 当时正是躺在豁免表里、
   本锁绿着、库依然缺表。豁免理由声明的后果由
   `test_sqlite_fresh_schema.py::test_fresh_db_covers_every_table_pg_declares` 去验。
2. `_ensure_initialized` 里不得有「失败只 print、从不重抛」的 except 块。此前 74 处
   `except Exception: print` 同时干了两件坏事：真失败被吞成「初始化成功」，
   而「已建库重跑」这条正常路径每次都打一行假失败（034）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from storage import sqlite_store

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "storage" / "migrations"
SQLITE_STORE = ROOT / "storage" / "sqlite_store.py"


def test_every_migration_file_is_dispatched():
    """目录里每个 .sql 都要有归宿 —— 接线了，或明确记下为什么不接。"""
    not_applied = sqlite_store._MIGRATIONS_NOT_APPLIED
    dispatched = (
        {"001_init.sql"}
        | set(sqlite_store._MIGRATIONS_BEFORE_USER_REBUILD)
        | set(sqlite_store._MIGRATIONS_AFTER_USER_REBUILD)
    )
    on_disk = {p.name for p in MIGRATIONS_DIR.glob("*.sql")}

    missing = sorted(on_disk - dispatched - set(not_applied))
    assert not missing, (
        f"这些迁移文件躺在 storage/migrations/ 里却没人应用：{missing}。"
        "加进 sqlite_store 的次序表，或在 _MIGRATIONS_NOT_APPLIED 里写明理由。")
    unknown = sorted(dispatched - on_disk)
    assert not unknown, f"次序表指向了不存在的迁移文件：{unknown}"
    stale = sorted(set(not_applied) - on_disk)
    assert not stale, f"_MIGRATIONS_NOT_APPLIED 里的豁免指向已不存在的文件：{stale}"


def test_exemption_has_one_source_in_the_executor(monkeypatch):
    """证明豁免只有一个源：本测试读的是执行器的声明，不是自己那份副本。

    变异（即本用例）：往执行器的 `_MIGRATIONS_NOT_APPLIED` 塞一条指向不存在文件的假豁免，
    锁必须相应变红（stale 分支）。若测试另存副本，这条不会生效。
    """
    monkeypatch.setitem(sqlite_store._MIGRATIONS_NOT_APPLIED, "999_ghost.sql", "假豁免")
    with pytest.raises(AssertionError, match="已不存在的文件"):
        test_every_migration_file_is_dispatched()


def test_live_published_uniq_index_has_one_unskippable_source():
    """`cards_published_from_live_uniq` 的定义只能在一个**不会被跳过**的文件里。

    背景（实测）：`_apply_migration` 的判据是「脚本里每个 ADD COLUMN 的列都已存在 → 整份
    跳过」。这条索引原先和 088 的 ADD COLUMN 同文件，而列正是 088 加的 —— 列一旦存在
    （088 跑过之后就是），整份脚本被静默跳过，索引永远建不上；于是执行器尾部又写了第二句
    兜底。同一个仓由此出现「两份定义、两种库两种行为」，且 publish_card 的 `ON CONFLICT`
    认的就是这条索引，缺了当场报错。

    锁两件事：
      ① 定义所在文件不含 ADD COLUMN（判据对它不成立，每次 init 都 APPLY，实测连跑两次均
         APPLY）；
      ② 执行器里没有第二份可执行的索引定义（注释不算）。
    """
    name = "090_published_from_live_uniq.sql"
    text = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
    assert "cards_published_from_live_uniq" in text, f"{name} 里没有索引定义"
    stray = sqlite_store._ADD_COLUMN_RE.findall(text)
    assert not stray, (
        f"{name} 里出现了 ADD COLUMN（{stray}）—— 列一旦存在，这份文件会被整份跳过，"
        "索引随之永远建不上。加列放在 088，本文件只放索引。")

    # ② 单一来源：执行器里不得再有可执行的索引定义（注释/文档字符串里的引用不算）
    tree = ast.parse(SQLITE_STORE.read_text(encoding="utf-8"))
    ddl = [n.value for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and "CREATE UNIQUE INDEX" in n.value.upper()]
    assert not ddl, (
        f"执行器里还有 {len(ddl)} 处可执行的 CREATE UNIQUE INDEX —— "
        f"索引的定义只留在 {name}，两处定义必然出现两种库两种行为。")

    # ③ 顺序：索引必须排在回填并收敛之后（Step 3 的 089 落地后即 089 之后）
    order = list(sqlite_store._MIGRATIONS_AFTER_USER_REBUILD)
    assert order.index(name) > order.index("088_published_from.sql"), (
        "索引排到了加列之前 —— 存量库里同一草稿的多张存活副本会撞唯一索引，"
        "回填并收敛必须排在它前面。")


async def test_already_satisfied_drop_column_is_stripped(tmp_path):
    """DROP 支的判据与 ADD 支**对称**：列已不在 ⇒ 这句已生效 ⇒ 剥掉，不是再发一次裸 DROP。

    SQLite 没有 `DROP COLUMN IF EXISTS`，所以「已生效的那句」不剥掉就是一条会抛
    `no such column` 的死脚本。剥除在**同一份脚本内逐句**生效，不是「整份跳过」——
    整份跳过只在「每句 DROP 都已生效」时触发，而真实形态是**一份里有的已生效、有的没有**
    （早先的迁移删过一列，后来这份文件又把它连同新列一起写了一遍）。本用例造的就是这个形态。

    **变异 = 删掉 `_apply_migration` 里的 `_DROP_COLUMN_RE.sub(...)` 那一句 → 红**
    （`sqlite3.OperationalError: no such column: gone_col`，因为 `present` 判据算出来了
    却没用来剥）。删掉整个 `if add_cols or drop_cols:` 块也一样红。
    """
    store = sqlite_store.SQLiteStore(str(tmp_path / "drop.db"))
    mig = tmp_path / "900_drop_probe.sql"
    # `gone_col` 不在表里（已被早先的迁移删掉）—— 它这一句必须被剥；
    # `legacy_col` 还在 —— 它这一句必须真执行。两句同处一份文件。
    mig.write_text(
        "ALTER TABLE probe DROP COLUMN gone_col;\n"
        "ALTER TABLE probe DROP COLUMN legacy_col;\n",
        encoding="utf-8")
    assert sqlite_store._DROP_COLUMN_RE.findall(mig.read_text(encoding="utf-8")), \
        "探针失效：正则没认出这两句 DROP，本用例什么也没锁住"

    async with await store._connect() as conn:
        await conn.execute(
            "CREATE TABLE probe (id TEXT PRIMARY KEY, legacy_col TEXT DEFAULT '')")
        await conn.commit()
        await sqlite_store._apply_migration(conn, mig)
        cols = await sqlite_store._existing_columns(conn, "probe")
        assert "legacy_col" not in cols, (
            f"已在表里的列没被删；实际列={sorted(cols)}")
        # 再跑一次：这次两句都已生效 → 整份跳过，不得抛
        await sqlite_store._apply_migration(conn, mig)
        cols2 = await sqlite_store._existing_columns(conn, "probe")
    assert "legacy_col" not in cols2, f"第二次跑把列加了回来；实际列={sorted(cols2)}"


async def test_rename_branch_is_target_based_and_clears_the_rebuilt_stub(tmp_path):
    """RENAME 支的判据取**目标表**，且目标已在时要把源表空壳删掉。

    ADD/DROP 两支只有一张表要管，改名有两个名字，所以判据不能照抄。取「源表已不在」当
    判据在这里是错的：074 那类 `CREATE TABLE IF NOT EXISTS` 每轮都会把改名前那张表重建
    出来，真实迁移（098）跑到时源表**总是在**（空壳）—— 按源表判就会每轮重发 ALTER，撞上
    已存在的目标表（`already another table with this name`），而这正是第二次 init 的实际形态。

    **变异（实测）**：把 `_apply_migration` 的 `_rename` 里那行 `return f'DROP TABLE IF
    EXISTS ...'` 改成 `return ""`（只剥不删）→ 第三次调用后库里同时留着新表与空壳，末尾
    那条断言红。
    """
    store = sqlite_store.SQLiteStore(str(tmp_path / "rename.db"))
    mig = tmp_path / "901_rename_probe.sql"
    mig.write_text("ALTER TABLE old_probe RENAME TO new_probe;\n", encoding="utf-8")
    assert sqlite_store._RENAME_TABLE_RE.findall(mig.read_text(encoding="utf-8")), \
        "探针失效：正则没认出这句 RENAME，本用例什么也没锁住"

    async with await store._connect() as conn:
        await conn.execute("CREATE TABLE old_probe (id TEXT PRIMARY KEY)")
        await conn.commit()

        await sqlite_store._apply_migration(conn, mig)   # 目标不在 ⇒ 真改名
        assert await sqlite_store._existing_columns(conn, "new_probe"), "改名没生效"
        assert not await sqlite_store._existing_columns(conn, "old_probe"), "源表还在"

        # 造第二次 init 的实际形态：更早那句 IF NOT EXISTS 把旧名又建了出来
        await conn.execute("CREATE TABLE IF NOT EXISTS old_probe (id TEXT PRIMARY KEY)")
        await conn.commit()
        await sqlite_store._apply_migration(conn, mig)   # 目标已在 ⇒ 剥掉这句 + 删掉空壳
        assert await sqlite_store._existing_columns(conn, "new_probe"), "改名后的表被删掉了"
        assert not await sqlite_store._existing_columns(conn, "old_probe"), (
            "目标表已在时源表空壳没被清掉 —— 库里会永远多一张表，「重启过的库」与「全新库」"
            "从此不是同一个 schema")

        await sqlite_store._apply_migration(conn, mig)   # 源已不在 ⇒ 整份跳过，不得抛
        assert not await sqlite_store._existing_columns(conn, "old_probe")
        assert await sqlite_store._existing_columns(conn, "new_probe")


async def test_rename_branch_refuses_to_drop_a_nonempty_source(tmp_path):
    """目标表已在、源表**有行**时不静默 DROP，而是抛 —— 与 PG 031 同语义。

    上面那条锁的是「源表是空壳」这一支：空壳该清掉，否则库比全新库多一张表。本条锁另一支：
    源表**非空**说明改名并没有真的生效（数据还留在旧名下），此时清掉旧表 = 丢数据。PG 031
    在同样情形下 `RAISE EXCEPTION`（两表同存、旧表有 N 行、不自动删）；SQLite 此前直接
    DROP，两侧语义不一致。

    **变异（实测）**：去掉 `_drop_rebuilt_shell` 里的 `if row[0]:` 那道检查（或让它永远返回
    DROP 语句）→ `pytest.raises` 收不到异常，红。
    """
    store = sqlite_store.SQLiteStore(str(tmp_path / "rename_guard.db"))
    mig = tmp_path / "902_rename_guard.sql"
    mig.write_text("ALTER TABLE old_guard RENAME TO new_guard;\n", encoding="utf-8")
    assert sqlite_store._RENAME_TABLE_RE.findall(mig.read_text(encoding="utf-8")), \
        "探针失效：正则没认出这句 RENAME，本用例什么也没锁住"

    async with await store._connect() as conn:
        # 两表同存、旧表里真有一行：改名早已生效的形态下旧表不该有数据
        await conn.execute("CREATE TABLE new_guard (id TEXT PRIMARY KEY)")
        await conn.execute("CREATE TABLE old_guard (id TEXT PRIMARY KEY)")
        await conn.execute("INSERT INTO old_guard (id) VALUES ('keep-me')")
        await conn.commit()

        with pytest.raises(RuntimeError, match="旧表有 1 行"):
            await sqlite_store._apply_migration(conn, mig)

        # 抛了就不许动数据：旧表还在，那一行也还在
        assert await sqlite_store._existing_columns(conn, "old_guard"), (
            "抛错之后源表还是被删掉了 —— 这正是要防的静默丢数据")
        cursor = await conn.execute("SELECT count(*) FROM old_guard")
        (kept,) = await cursor.fetchall()
        assert kept[0] == 1, f"抛错后源表的行数变了：{kept[0]}"


def test_migration_runner_never_swallows_a_failure():
    """迁移执行区不得有「只 print、从不重抛」的 except 块。

    74 处同族块是靠人工逐个数出来的；这条锁让它不可能再长回来 —— 新写一个
    `except Exception: print(...)` 就红。
    """
    tree = ast.parse(SQLITE_STORE.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_ensure_initialized"),
        None,
    )
    assert fn is not None, "找不到 _ensure_initialized —— 锁的靶子没了，先修靶子"

    bad = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not any(isinstance(x, ast.Raise) for x in ast.walk(handler)):
                bad.append(handler.lineno)
    assert not bad, (
        f"_ensure_initialized 第 {sorted(bad)} 行的 except 只 print、从不重抛 —— "
        "真失败会被吞成「初始化成功」。要么删掉这个 except（用确定性前置判断代替），"
        "要么在 handler 里 raise。")
