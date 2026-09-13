"""迁移接线覆盖审计（缺陷 21 排查工具）—— 三个差集一次算清，不靠肉眼。

**为什么需要它**：`15e5f3e0` 加了 `079_remote_user_profiles.sql` 并用它改了三处 SQL，
却从没把它接进 SQLite 执行器 —— 单看那次 commit 的 diff 一切正常，漏的是**另一个文件**
（执行器）里少了一行。这类「缺席被当成完成」在改动现场不可见，只有把两个集合求差才看得见。

三个差集（各自回答一个不同的问题）：

- **A 文件存在但从不执行**：`storage/migrations/*.sql` − 执行清单 − 显式豁免
- **B 清单写了但文件没了**：执行清单 − `storage/migrations/*.sql`（执行器里 `if exists`
  静默跳过，只有集合差能发现）
- **C 双 store 单侧有**：按去掉编号前缀的**主题**比对 `migrations/` 与 `migrations_pg/`
  （两侧编号不同源：sqlite 造到 084，PG 从 001 重新编号且 001 是单体 bootstrap），
  再对 sqlite 独有项做「是否已被 PG bootstrap 覆盖」的对象级核对

**它不是锁**（本轮只查不修）：把差集打出来给人看。要做成形态锁见报告 §三。

用法（无参数、无副作用、不连任何库）：
    python tests/perf/migration_coverage_audit.py
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storage import sqlite_store  # noqa: E402

SQLITE_DIR = ROOT / "storage" / "migrations"
PG_DIR = ROOT / "storage" / "migrations_pg"

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(?P<table>\w+)[`\"\]]?\s*\(",
    re.IGNORECASE)
_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+[`\"\[]?(?P<table>\w+)[`\"\]]?\s+ADD\s+COLUMN\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(?P<column>\w+)", re.IGNORECASE)
_COMMENT_RE = re.compile(r"--[^\n]*")
# 表体里以这些词开头的项不是列定义，是表级约束
_CONSTRAINT_KEYWORDS = {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "KEY", "EXCLUDE"}


def _table_body(sql: str, open_paren: int) -> str:
    """从 `(` 处按括号配平取出 CREATE TABLE 的体 —— 列类型里还有括号（NUMERIC(10,2)）。"""
    depth = 0
    for i in range(open_paren, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_paren + 1:i]
    return ""


def _split_top_level(body: str) -> list[str]:
    """按**顶层**逗号切分表体 —— 别切碎 `NUMERIC(10,2)` 或 `CHECK(x IN (1,2))`。"""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return parts


def _inline_columns(sql: str) -> set[tuple[str, str]]:
    """`CREATE TABLE t (a TEXT, b INT)` 里的内联列 —— PG 的 001_init 全是这种写法。

    少了这一步，「PG 有没有这列」只能看到 ALTER 加的列，于是把 bootstrap 里已有的列
    全报成缺失（实测 45 条假阳性）。**先确认核对器看得到它要核对的东西**。
    """
    out: set[tuple[str, str]] = set()
    for m in _CREATE_TABLE_RE.finditer(sql):
        body = _table_body(sql, m.end() - 1)
        for item in _split_top_level(body):
            toks = re.findall(r"[`\"\[]?(\w+)", item)
            if not toks or toks[0].upper() in _CONSTRAINT_KEYWORDS:
                continue
            out.add((m.group("table"), toks[0]))
    return out


def _topics(names: set[str]) -> dict[str, str]:
    """`079_remote_user_profiles.sql` → `remote_user_profiles` → 文件名。"""
    out = {}
    for n in names:
        out[re.sub(r"^\d+_", "", n[:-4])] = n
    return out


def _objects_from_sql(raw: str) -> tuple[set[str], set[tuple[str, str]]]:
    """一段 SQL 里建了哪些表、有哪些 (表, 列) —— 内联定义与 ALTER 加的都要算。"""
    sql = _COMMENT_RE.sub("", raw)
    tables = {m.group("table") for m in _CREATE_TABLE_RE.finditer(sql)}
    cols = _inline_columns(sql) | {m.group("table", "column") for m in _ADD_COLUMN_RE.finditer(sql)}
    return tables, cols


def _objects(path: Path) -> tuple[set[str], set[tuple[str, str]]]:
    return _objects_from_sql(path.read_text(encoding="utf-8"))


def _self_check() -> None:
    """负控 + 正控：核对器必须真看得见它要核对的东西。

    否则「无缺口」只是它瞎了。本脚本第一版就栽在这里：只从 ALTER 取列、看不见 PG
    bootstrap 的内联列定义，把 45 条已存在的列全报成缺失；反过来若提取器返回空集，
    「全覆盖」同样假成立。**先确认探针真的在探，再采信它的结论**（AGENTS.md §四）。
    """
    fake = "CREATE TABLE zz_probe (a TEXT, b NUMERIC(10,2));\nALTER TABLE texts ADD COLUMN zz_col TEXT;"
    tables, cols = _objects_from_sql(fake)
    assert "zz_probe" in tables, f"提取器漏掉 CREATE TABLE：{tables}"
    assert {("zz_probe", "a"), ("zz_probe", "b")} <= cols, f"漏掉内联列（含带括号的类型）：{cols}"
    assert ("texts", "zz_col") in cols, f"漏掉 ALTER 加的列：{cols}"
    # 正控：真 bootstrap 上必须看得见已知列，否则 C 差集恒空
    boot_tables, boot_cols = _objects(PG_DIR / "001_init.sql")
    assert "texts" in boot_tables, f"PG bootstrap 提取不到 texts：{sorted(boot_tables)[:5]}"
    assert ("users", "username") in boot_cols, "PG bootstrap 提取不到 users.username（内联列）"


def _exemptions() -> dict[str, str]:
    """从**执行器**读豁免声明 `_MIGRATIONS_NOT_APPLIED`。

    豁免只有一个源（缺陷 21 的收口）：声明住在执行器模块里，与次序元组同一处，读
    `sqlite_store.py` 的人一眼看到「哪个迁移被有意略过、为什么」。本脚本与
    `tests/test_migration_dispatch.py` 都从这里读，不存在第二份要同步的副本。
    """
    return dict(sqlite_store._MIGRATIONS_NOT_APPLIED)


def main() -> None:
    _self_check()
    sqlite_on_disk = {p.name for p in SQLITE_DIR.glob("*.sql")}
    pg_on_disk = {p.name for p in PG_DIR.glob("*.sql")}
    dispatched = ({"001_init.sql"}
                  | set(sqlite_store._MIGRATIONS_BEFORE_USER_REBUILD)
                  | set(sqlite_store._MIGRATIONS_AFTER_USER_REBUILD))
    exempt = _exemptions()

    print(f"sqlite 目录 {len(sqlite_on_disk)} 个 / 执行清单 {len(dispatched)} 个 "
          f"(含 001_init) / 显式豁免 {len(exempt)} 个")
    print(f"pg   目录 {len(pg_on_disk)} 个 / 执行方式 = glob(*.sql)，目录即清单\n")

    # ── A：文件存在但从不执行 ──────────────────────────────
    gap_a = sorted(sqlite_on_disk - dispatched - set(exempt))
    print(f"A. 文件存在、既不在清单也不在豁免（{len(gap_a)}）：")
    print(f"   {gap_a or '（空）'}")
    if exempt:
        print(f"   豁免项（有意不执行，仍需确认理由是否仍成立）：")
        for name in sorted(exempt):
            stale = "  ⚠ 文件已不存在" if name not in sqlite_on_disk else ""
            print(f"     {name}{stale}")
            print(f"       {exempt[name][:100]}")

    # ── B：清单写了但文件没了 ──────────────────────────────
    gap_b = sorted(dispatched - sqlite_on_disk)
    print(f"\nB. 清单指向不存在的文件（{len(gap_b)}）：{gap_b or '（空）'}")
    print(f"   执行器 `if _path.exists()` 会静默跳过这类名字")

    # ── C：双 store 单侧有 ────────────────────────────────
    st, pt = _topics(sqlite_on_disk), _topics(pg_on_disk)
    only_sqlite = sorted(set(st) - set(pt))
    only_pg = sorted(set(pt) - set(st))
    print(f"\nC. 主题比对：sqlite 独有 {len(only_sqlite)} / PG 独有 {len(only_pg)}")
    print(f"   PG 独有（{only_pg or '（空）'}）—— 001_init 是 bootstrap，其余应在 sqlite 侧有同主题")

    # sqlite 独有项：逐个查它建的对象在 PG 侧（含 001_init bootstrap）有没有
    pg_boot_tables, pg_boot_cols = _objects(PG_DIR / "001_init.sql")
    for p in sorted(PG_DIR.glob("*.sql")):
        t, c = _objects(p)
        pg_boot_tables |= t
        pg_boot_cols |= c

    uncovered = []
    for topic in only_sqlite:
        t, c = _objects(SQLITE_DIR / st[topic])
        miss_t = sorted(t - pg_boot_tables)
        miss_c = sorted(f"{a}.{b}" for a, b in c - pg_boot_cols)
        if miss_t or miss_c:
            uncovered.append((st[topic], miss_t, miss_c))

    print(f"\n   sqlite 独有主题中，对象在 PG 侧找不到的（{len(uncovered)}）：")
    if not uncovered:
        print("     （空）—— 全部落在 PG 001_init.sql 的 bootstrap 里")
    for name, mt, mc in uncovered:
        print(f"     {name}")
        if mt:
            print(f"       PG 无此表：{mt}")
        if mc:
            print(f"       PG 无此列：{mc}")


if __name__ == "__main__":
    main()
