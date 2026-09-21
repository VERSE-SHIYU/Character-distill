# -*- coding: utf-8 -*-
"""「索引/触发器各两份定义」的成因探针，及「索引先于回填」的启动失败复现。

台账（AGENTS.md）引用本脚本的读数，故入库。跑法（仓根）：
    .venv/Scripts/python.exe scripts/probe_published_index_sources.py

A 段 —— `_apply_migration` 的「列都在即整份跳过」会不会吞掉尾部 DDL：
  A1 全新库：决策日志 + init 后索引/触发器
  A2 「列已存在」的库（跑过 088 的库）：索引由谁建（迁移文件还是执行器尾部）
  A3 成因正面复现：把 [ADD COLUMN + CREATE INDEX] 放同一个文件，第二次跑会不会丢索引
  A4 只含 CREATE INDEX、无 ADD COLUMN 的文件：会不会被跳过（判「拆成独立文件」是否成立）
  A5 重建表对索引 / 触发器各做什么（FK 缺席 → 与生产同路径）
  A6 端到端：全新库连跑两次 init，索引/触发器仍在，且索引的创建者是 090

B 段 —— 顺序（索引在前 vs 回填在前 vs 回填并收敛）：
  B1 dirty 库（同一草稿两张存活副本）上先建索引再回填
  B2 同库先回填再建索引
  B3 回填 → 收敛 → 建索引

**A 段的读数是历史快照**：它测的是「两份定义」那个已不存在的形态（重建前后对照仍可复跑，
但 A1/A2 的决策日志描述的是旧代码）。B 段与实现无关，任何时候可复跑。
只写临时库，不碰 data/charsim.db。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storage.sqlite_store as S
from storage.sqlite_store import SQLiteStore

INDEX = "cards_published_from_live_uniq"
TRIGGER = "trg_cards_clear_published_from"
MIG = ROOT / "storage" / "migrations"


def index_sql() -> str:
    """索引的定义只从迁移文件取 —— 探针不另存一份（否则它自己就是第二个来源）。"""
    text = (MIG / "090_published_from_live_uniq.sql").read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("--"))


# audit_published_from_prebackfill.sql 里 ① 的谓词，逐字搬来当候选回填规则
BACKFILL = """
UPDATE cards SET published_from = forked_from
 WHERE visibility = 'public' AND deleted_at IS NULL AND published_from IS NULL
   AND forked_from <> '' AND user_id IS NOT NULL
   AND user_id = (SELECT d.user_id FROM cards d WHERE d.id = cards.forked_from)
"""


def q(db: str, sql: str, args=()) -> list:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def has(db: str, kind: str, name: str) -> bool:
    return q(db, "SELECT COUNT(*) FROM sqlite_master WHERE type=? AND name=?", (kind, name))[0][0] > 0


def has_index(db: str) -> bool:
    return has(db, "index", INDEX)


def has_trigger(db: str) -> bool:
    return has(db, "trigger", TRIGGER)


def try_exec(db: str, sql: str) -> str:
    conn = sqlite3.connect(db)
    try:
        conn.execute(sql)
        conn.commit()
        return "OK"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()


async def apply_file(db: str, path: pathlib.Path) -> str:
    """走真正的 `_apply_migration`（含跳过判据），不是裸 executescript。"""
    store = SQLiteStore(db)
    async with await store._connect() as conn:
        try:
            await S._apply_migration(conn, path)
            return "OK"
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"


async def init(db: str) -> None:
    await SQLiteStore(db)._ensure_initialized()


async def decision_for(db: str, needle: str) -> list[str]:
    """打 `_apply_migration` 的决策：跑了哪些、跳了哪些。"""
    log: list[str] = []
    orig = S._apply_migration

    async def spy(conn, path):
        sql = path.read_text(encoding="utf-8")
        add_cols = list(S._ADD_COLUMN_RE.finditer(sql))
        if add_cols:
            present: dict[str, set[str]] = {}
            for m in add_cols:
                present.setdefault(m.group("table"), set())
            for t in present:
                present[t] = await S._existing_columns(conn, t)
            if all(m.group("column") in present[m.group("table")] for m in add_cols):
                log.append(f"SKIP  {path.name}")
                return
        log.append(f"APPLY {path.name}")
        await orig(conn, path)

    S._apply_migration = spy
    try:
        await init(db)
    finally:
        S._apply_migration = orig
    return [line for line in log if needle in line]


async def strip_composite_fk(db: str) -> None:
    """把复合外键去掉（只留 UNIQUE），造出「FK 缺席」形态 —— 与生产里走重建的前提一致。

    不走 `_rebuild_cards_published_from`（它把现状 FK 逐行复制，复合那条会被拆成两条单列
    FK，反而制造 `foreign key mismatch`）—— 这里只保留不属于复合外键的 FK 行。
    """
    store = SQLiteStore(db)
    async with await store._connect() as conn:
        info = await (await conn.execute("PRAGMA table_info(cards)")).fetchall()
        fks = await (await conn.execute("PRAGMA foreign_key_list(cards)")).fetchall()
        keep = [r for r in fks
                if not (r[2] == "cards" and r[3] in ("published_from", "user_id"))]
        cols = S._cards_rebuild_columns(info, keep, ('UNIQUE ("id", "user_id")',))
        names = [r[1] for r in info]
        collist = ", ".join(f'"{c}"' for c in names)
        async with S._fk_disabled(conn):
            await conn.executescript(f"""
                CREATE TABLE cards_mig ({",\n    ".join(cols)});
                INSERT INTO cards_mig ({collist}) SELECT {collist} FROM cards;
                DROP TABLE cards;
                ALTER TABLE cards_mig RENAME TO cards;
            """)
            await conn.commit()
        await conn.execute(index_sql())
        await conn.execute(S._CARDS_CLEAR_PUBLISHED_FROM_TRIGGER)
        await conn.commit()


async def rebuild_directly(db: str) -> None:
    store = SQLiteStore(db)
    async with await store._connect() as conn:
        await S._rebuild_cards_published_from(conn)


async def make_dirty(db: str) -> None:
    """同一草稿 + 两张存活副本，且无唯一索引 —— 老语义（每次发布新建一行）的存量形态。"""
    conn = sqlite3.connect(db)
    try:
        conn.execute(f"DROP INDEX IF EXISTS {INDEX}")
        conn.execute("""INSERT INTO cards (id, text_id, name, card_json, created_at, user_id,
                                             visibility, forked_from, likes)
                        VALUES ('draft1', 't1', 'A', '{}', '2026-01-01', 'owner',
                                'private', '', 0)""")
        for cid in ("copy1", "copy2"):
            conn.execute("""INSERT INTO cards (id, text_id, name, card_json, created_at, user_id,
                                                 visibility, forked_from, likes)
                            VALUES (?, 't1', 'A', '{}', '2026-01-02', 'owner',
                                    'public', 'draft1', 0)""", (cid,))
        conn.commit()
    finally:
        conn.close()


def live_copies(db: str) -> list:
    return q(db, "SELECT id, published_from FROM cards "
                 "WHERE published_from IS NOT NULL AND deleted_at IS NULL ORDER BY id")


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="probe_pubidx_")
    print(f"tmp={tmp}\n=== A 成因 ===")

    fresh = os.path.join(tmp, "a1.db")
    log = await decision_for(fresh, "published_from")
    print("A1 全新库 决策=%s → index=%s trigger=%s" % (log, has_index(fresh), has_trigger(fresh)))

    a2 = os.path.join(tmp, "a2.db")
    await init(a2)
    conn = sqlite3.connect(a2)
    conn.execute(f"DROP INDEX IF EXISTS {INDEX}")
    conn.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}")
    conn.commit()
    conn.close()
    log = await decision_for(a2, "published_from")
    print("A2 列已存在 决策=%s → index=%s trigger=%s   ← 索引由 090 建，不是被跳过的那份"
          % (log, has_index(a2), has_trigger(a2)))

    # A3 成因正面复现：同一个文件里 [ADD COLUMN + CREATE INDEX]，第二次跑会不会丢索引
    a3 = os.path.join(tmp, "a3.db")
    await init(a3)
    combo = pathlib.Path(tmp) / "probe_combo.sql"
    combo.write_text(
        "ALTER TABLE cards ADD COLUMN probe_col TEXT DEFAULT NULL;\n"
        "CREATE INDEX IF NOT EXISTS probe_tail_idx ON cards(likes);\n", encoding="utf-8")
    r1 = await apply_file(a3, combo)
    first = has(a3, "index", "probe_tail_idx")
    conn = sqlite3.connect(a3)
    conn.execute("DROP INDEX probe_tail_idx")
    conn.commit()
    conn.close()
    r2 = await apply_file(a3, combo)
    print("A3 [ADD COLUMN+CREATE INDEX] 同文件：首次=%s 建了索引=%s；列已在、再跑=%s 索引=%s"
          % (r1, first, r2, has(a3, "index", "probe_tail_idx")))

    a4 = os.path.join(tmp, "a4.db")
    await init(a4)
    only_index = pathlib.Path(tmp) / "probe_only_index.sql"
    only_index.write_text("CREATE INDEX IF NOT EXISTS probe_only_idx ON cards(likes);\n",
                          encoding="utf-8")
    reps = []
    for _ in range(2):
        store = SQLiteStore(a4)
        async with await store._connect() as c:
            try:
                await S._apply_migration(c, only_index)
                reps.append("OK")
            except Exception as exc:
                reps.append(f"{type(exc).__name__}: {exc}")
    print("A4 只含 CREATE INDEX 的文件连跑两次 = %s" % reps)

    a5 = os.path.join(tmp, "a5.db")
    await init(a5)
    await strip_composite_fk(a5)
    before = (has_index(a5), has_trigger(a5))
    await rebuild_directly(a5)
    print("A5 重建前 (index,trigger)=%s → 重建后 %s   ← 索引重放=保住；触发器不重放=丢"
          % (before, (has_index(a5), has_trigger(a5))))

    a6 = os.path.join(tmp, "a6.db")
    await init(a6)
    log2 = await decision_for(a6, "published_from")
    print("A6 全新库连跑两次 init：第二次决策=%s → index=%s trigger=%s"
          % (log2, has_index(a6), has_trigger(a6)))

    print("\n=== B 顺序 ===")
    b1 = os.path.join(tmp, "b1.db")
    await init(b1)
    await make_dirty(b1)
    r_idx = try_exec(b1, index_sql())
    r_bf = try_exec(b1, BACKFILL)
    print("B1 索引在前：建索引=%s；回填=%s" % (r_idx, r_bf))

    b2 = os.path.join(tmp, "b2.db")
    await init(b2)
    await make_dirty(b2)
    r_bf = try_exec(b2, BACKFILL)
    r_idx = try_exec(b2, index_sql())
    print("B2 回填在前：回填=%s；建索引=%s" % (r_bf, r_idx))

    b3 = os.path.join(tmp, "b3.db")
    await init(b3)
    await make_dirty(b3)
    conv = """
    UPDATE cards SET deleted_at = '2026-01-03'
     WHERE published_from IS NOT NULL AND deleted_at IS NULL
       AND id NOT IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY published_from
                                    ORDER BY rowid DESC) rn FROM cards
                                    WHERE published_from IS NOT NULL AND deleted_at IS NULL)
                      WHERE rn = 1)
    """
    r_bf = try_exec(b3, BACKFILL)
    r_conv = try_exec(b3, conv)
    r_idx = try_exec(b3, index_sql())
    print("B3 回填→收敛→建索引：回填=%s；收敛=%s；建索引=%s；存活副本=%s"
          % (r_bf, r_conv, r_idx, live_copies(b3)))

    print("\n（临时库留在 %s，查看完可整目录删）" % tmp)


if __name__ == "__main__":
    asyncio.run(main())
