"""Schema parity: PG and SQLite 的迁移文本不得漂移 —— **只比本锁读得准的两件事**。

Production backend is PostgreSQL. SQLite is only for local unit tests.

1. **表集合** —— 来自 `CREATE TABLE` 文本。两目录都不含 `DROP TABLE`（现跑现数 0 处），
   故「文件里写了」= 「库里会有」，可靠。
2. **共有列的可空性** —— 只在两边都出现过的列上比；可空性就写在列定义那一行里。

**列集合故意不比**（2026-09-23 收窄，缺陷 87 的收尾）。文本提取器只认 `CREATE TABLE` +
`ALTER … ADD COLUMN`，**看不见 `DROP COLUMN`、也看不见 Python 侧的表重建/删列**，而两个
后端的退役手段并不同构：`users` 的 5 列（`password_hash` / `api_key` / `base_url` /
`model` / `is_admin`）PG 走 `.sql` 的 `DROP COLUMN`，SQLite 走 `_USERS_RETIRED_COLUMNS`
的 Python 重建。于是任何「一边用文本退役、另一边用 Python 退役」的列，在本锁眼里都成了
假漂移 —— 实测：把提取器改成 DROP 感知只会把红点从 `texts` 平移到 `users`（5 列），
永远修不干净。

列级权威判据在**真库比真库**：`tests/test_postgres_store.py::TestPgFreshSchemaClosure::
test_fresh_sqlite_and_fresh_pg_have_the_same_columns`（表级同族：
`tests/test_sqlite_fresh_schema.py::TestExemptionClosedLoop::test_fresh_db_covers_every_table_pg_declares`）。
CI 起真 PG 且置 `REQUIRE_PG_TESTS=1`（`.github/workflows/build.yml`），那条闭环每次 CI
都被强制执行 —— 而本文件不需要 PG 就能跑，是它的前置粗筛。

Type-level differences (SMALLINT vs INTEGER, TIMESTAMPTZ vs TEXT) are
allowed because each dialect uses its native types.

Usage:
    pytest tests/test_schema_parity.py -v
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PG_DIR = PROJECT_ROOT / "storage" / "migrations_pg"
SQLITE_DIR = PROJECT_ROOT / "storage" / "migrations"

# Column names or SQL keywords that appear at the start of a line in
# CREATE TABLE blocks but are NOT column definitions.
_SKIP_WORDS = frozenset({
    "constraint", "primary", "foreign", "unique", "check", "index",
    "create", "--", "",
})

# Type keywords that signal a real column definition vs a constraint clause.
# 第 3 组把该列的其余修饰（NOT NULL / DEFAULT … / PRIMARY KEY …）一并截下来 —— 可空性
# 就在里面。截到行尾即止（这些 .sql 一律一列一行）。
_COL_TYPE_RE = re.compile(
    r"^\s*(\w+)\s+"
    r"(INTEGER|TEXT|BOOLEAN|SMALLINT|BIGINT|SERIAL|BIGSERIAL|TIMESTAMP(?:TZ)?|DOUBLE\s+PRECISION|REAL|BLOB)"
    r"([^\n]*)",
    re.IGNORECASE | re.MULTILINE,
)

_NOT_NULL_RE = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)

# 已核实、**不修**的可空性分叉 —— {("表","列"): 理由}。本锁只登记，不改任何一方：
# 两个迁移文件都是历史文件，改任一侧都会让「全新库」与「已建库」变成两个 schema
# （缺陷 26 的教训），真正的对齐要靠新迁移，属独立议题。
#
# **豁免不是放行**：下面会反向核对每条豁免是否仍然成立，一旦某条不再分叉即报红 ——
# 免得这份名单比它描述的事实活得久（缺陷 21 / 079 的教训：豁免即永久放行）。
_NULLABILITY_EXEMPT: dict[tuple[str, str], str] = {
    ("dm_reactions", "created_at"):
        "SQLite 069 写 `NOT NULL DEFAULT (datetime('now'))`，PG 004 写 `DEFAULT "
        "CURRENT_TIMESTAMP`（可空）。两侧都有默认值，写入路径不显式给 NULL，暂无实际分叉；"
        "已报告，未修（改历史迁移文件会让新库与已建库分叉）。",
}

# ── helpers ──────────────────────────────────────────────────────


def _read_sql_files(directory: Path) -> str:
    """Concatenate all .sql files sorted by name."""
    parts = []
    for p in sorted(directory.glob("*.sql")):
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _extract_create_blocks(sql: str):
    """Yield (table_name, columns_text) for each CREATE TABLE statement."""
    sql = _strip_sql_comments(sql)
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
        re.IGNORECASE,
    )
    for m in pattern.finditer(sql):
        tname = m.group(1)
        start = m.end()
        depth = 1
        pos = start
        while depth > 0 and pos < len(sql):
            if sql[pos] == "(":
                depth += 1
            elif sql[pos] == ")":
                depth -= 1
            pos += 1
        if depth == 0:
            yield tname, sql[start : pos - 1]


def _extract_columns(columns_text: str) -> dict[str, bool]:
    """{列名: 是否显式 NOT NULL}。

    **只认显式声明，不推算**：`PRIMARY KEY` 的隐式非空两方言口径不同（PG 隐式 NOT NULL，
    SQLite 的 legacy quirk 是允许 NULL），按它推算只会造出假差异。本锁管的是「迁移文件里
    写的可空性一致」，实际建库的隐式行为由 tests/test_sqlite_fresh_schema.py 那类锁管。
    """
    out: dict[str, bool] = {}
    for m in _COL_TYPE_RE.finditer(columns_text):
        out[m.group(1).lower()] = bool(_NOT_NULL_RE.search(m.group(3)))
    return out


def _extract_schema(sql: str) -> dict[str, dict[str, bool]]:
    """Build {table: {col: not_null}} from CREATE TABLE + ALTER TABLE ADD COLUMN."""
    schema: dict[str, dict[str, bool]] = {}

    for tname, col_text in _extract_create_blocks(sql):
        schema[tname.lower()] = _extract_columns(col_text)

    alter_pat = re.compile(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)([^;\n]*)",
        re.IGNORECASE,
    )
    for m in alter_pat.finditer(_strip_sql_comments(sql)):
        not_null = bool(_NOT_NULL_RE.search(m.group(3) or ""))
        schema.setdefault(m.group(1).lower(), {})[m.group(2).lower()] = not_null

    return schema


# ── the test ─────────────────────────────────────────────────────


def test_schema_parity():
    pg_schema = _extract_schema(_read_sql_files(PG_DIR))
    sqlite_schema = _extract_schema(_read_sql_files(SQLITE_DIR))

    pg_tables = set(pg_schema)
    sqlite_tables = set(sqlite_schema)

    errors: list[str] = []

    # 1) Table-level parity
    only_pg = pg_tables - sqlite_tables
    only_sqlite = sqlite_tables - pg_tables
    if only_pg:
        errors.append(f"Tables in PG but missing in SQLite: {sorted(only_pg)}")
    if only_sqlite:
        errors.append(f"Tables in SQLite but missing in PG: {sorted(only_sqlite)}")

    # 2) 共有列的可空性。**列名集合故意不比** —— 文本提取器看不见 DROP COLUMN、也看不见
    #    Python 侧退役，比出来的是假漂移；列级权威判据在真库闭环（见模块 docstring）。
    for table in sorted(pg_tables & sqlite_tables):
        pg_cols = pg_schema[table]
        sqlite_cols = sqlite_schema[table]

        # Nullability parity —— 模块 docstring 一直声称校验这一条，但此前只比了列名集合，
        # 可空性在 `_extract_column_names` 返回 set[str] 的那一刻就被丢掉了，无从比较。
        # 缺陷 78 的 `cards.text_id`（SQLite NOT NULL / PG 可空）正是从这道缝里漏过去的。
        for col in sorted(set(pg_cols) & set(sqlite_cols)):
            if pg_cols[col] == sqlite_cols[col]:
                continue
            if (table, col) in _NULLABILITY_EXEMPT:
                continue
            pg_decl = "NOT NULL" if pg_cols[col] else "可空"
            sq_decl = "NOT NULL" if sqlite_cols[col] else "可空"
            errors.append(
                f"[{table}.{col}] 可空性不一致：PG {pg_decl} / SQLite {sq_decl}"
            )

    # 3) 豁免名单不得腐烂：每条豁免都必须仍然对得上一个**真实存在且真的分叉**的列。
    for (table, col), reason in sorted(_NULLABILITY_EXEMPT.items()):
        if table not in pg_tables or table not in sqlite_tables:
            errors.append(
                f"豁免条目 [{table}.{col}] 指向的表已不在两侧 schema 里，理由（{reason[:20]}…）"
                "的前提没了 —— 删掉这条豁免"
            )
            continue
        if col not in pg_schema[table] or col not in sqlite_schema[table]:
            errors.append(
                f"豁免条目 [{table}.{col}] 指向的列已不在两侧 schema 里 —— 删掉这条豁免"
            )
        elif pg_schema[table][col] == sqlite_schema[table][col]:
            errors.append(
                f"豁免条目 [{table}.{col}] 已不再分叉（两侧可空性一致）—— 删掉这条豁免"
            )

    if errors:
        msg = (
            "Schema drift detected between PG and SQLite migrations:\n"
            + "\n".join(f"  - {e}" for e in errors)
            + "\n\n"
            "Fix: add the corresponding migration to the other backend.\n"
            "PG  → storage/migrations_pg/\n"
            "SQLite → storage/migrations/\n"
            "\n"
            "（本锁只判表集合与共有列的可空性；**列集合不在此判** —— 见模块 docstring，\n"
            " 列级权威判据是 test_postgres_store.py::TestPgFreshSchemaClosure 的真库闭环。）\n"
        )
        assert False, msg
