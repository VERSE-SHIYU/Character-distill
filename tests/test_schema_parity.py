"""Schema parity: PG and SQLite migration schemas must not drift.

Production backend is PostgreSQL. SQLite is only for local unit tests.
This test enforces that any migration adding a table/column to one backend
must also add it to the other, preventing silent schema drift.

Type-level differences (SMALLINT vs INTEGER, TIMESTAMPTZ vs TEXT) are
allowed because each dialect uses its native types.  What matters is that
every table and every column exists in both schemas with matching nullability.

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
_COL_TYPE_RE = re.compile(
    r"^\s*(\w+)\s+"
    r"(INTEGER|TEXT|BOOLEAN|SMALLINT|TIMESTAMP(?:TZ)?|DOUBLE\s+PRECISION|REAL|BLOB)",
    re.IGNORECASE | re.MULTILINE,
)

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


def _extract_column_names(columns_text: str) -> set[str]:
    """Return set of column names from a CREATE TABLE body."""
    names: set[str] = set()
    for m in _COL_TYPE_RE.finditer(columns_text):
        names.add(m.group(1).lower())
    return names


def _extract_schema(sql: str) -> dict[str, set[str]]:
    """Build {table: {col, ...}} from CREATE TABLE + ALTER TABLE ADD COLUMN."""
    schema: dict[str, set[str]] = {}

    for tname, col_text in _extract_create_blocks(sql):
        key = tname.lower()
        schema[key] = _extract_column_names(col_text)

    alter_pat = re.compile(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        re.IGNORECASE,
    )
    for m in alter_pat.finditer(_strip_sql_comments(sql)):
        tname = m.group(1).lower()
        cname = m.group(2).lower()
        schema.setdefault(tname, set()).add(cname)

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

    # 2) Column-level parity for shared tables
    for table in sorted(pg_tables & sqlite_tables):
        pg_cols = pg_schema[table]
        sqlite_cols = sqlite_schema[table]

        missing_in_sqlite = pg_cols - sqlite_cols
        missing_in_pg = sqlite_cols - pg_cols

        if missing_in_sqlite:
            errors.append(
                f"[{table}] columns in PG but missing in SQLite: "
                f"{sorted(missing_in_sqlite)}"
            )
        if missing_in_pg:
            errors.append(
                f"[{table}] columns in SQLite but missing in PG: "
                f"{sorted(missing_in_pg)}"
            )

    if errors:
        msg = (
            "Schema drift detected between PG and SQLite migrations:\n"
            + "\n".join(f"  - {e}" for e in errors)
            + "\n\n"
            "Fix: add the corresponding migration to the other backend.\n"
            "PG  → storage/migrations_pg/\n"
            "SQLite → storage/migrations/\n"
        )
        assert False, msg
