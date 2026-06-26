#!/usr/bin/env python3
"""SQLite → PostgreSQL 一次性数据迁移工具。

把 data/character_sim.db（旧 SQLite 库）的所有业务数据搬进 PG。
处理 data_residency 列拆分：SQLite users 里的 password_hash/api_key/base_url/model → PG user_secrets。
探测旧库 schema，兼容改造前（列在 users）和改造后（已有 user_secrets）两种格式。

用法:
  python scripts/migrate_sqlite_to_pg.py [--dry-run] [--sqlite PATH]

环境变量:
  DATABASE_URL     PG 连接字符串（默认 postgresql://postgres:postgres@localhost:5432/character_sim）
  SQLITE_PATH      SQLite 路径（默认 data/character_sim.db）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime

import asyncpg

# ── 配置 ──────────────────────────────────────────────────────────────────────

DEFAULT_SQLITE = "data/character_sim.db"
DEFAULT_PG_URL = "postgresql://postgres:postgres@localhost:5432/character_sim"

# PG 中为 IDENTITY 列的表（需要 OVERRIDING SYSTEM VALUE）
IDENTITY_TABLES = {
    "messages", "group_messages", "usage_stats",
    "geo_block_log", "user_consent", "message_reactions",
}

# SQLite users → PG users 时排除的密钥列（已拆到 user_secrets）
SECRET_COLUMNS = {"password_hash", "api_key", "base_url", "model"}

# 迁移顺序 — 按外键依赖排列
TABLE_ORDER = [
    ("users", "users", "id"),
    (None, "user_secrets", "user_id"),              # 从 SQLite users 构造
    ("texts", "texts", "id"),
    ("cards", "cards", "id"),
    ("sessions", "sessions", "id"),
    ("messages", "messages", "id"),
    ("direct_messages", "direct_messages", "id"),
    ("card_likes", "card_likes", None),              # composite PK
    ("card_comments", "card_comments", "id"),
    ("card_versions", "card_versions", "id"),
    ("user_posts", "user_posts", "id"),
    ("post_comments", "post_comments", "id"),
    ("post_likes", "post_likes", None),
    ("user_follows", "user_follows", None),
    ("group_sessions", "group_sessions", "id"),
    ("group_messages", "group_messages", "id"),
    ("group_affinity", "group_affinity", None),
    ("text_comments", "text_comments", "id"),
    ("text_comment_likes", "text_comment_likes", None),
    ("card_comment_reports", "card_comment_reports", "id"),
    ("reading_progress", "reading_progress", None),
    ("usage_stats", "usage_stats", "id"),
    ("refresh_tokens", "refresh_tokens", "token_hash"),
    ("invite_codes", "invite_codes", "id"),
    ("verification_codes", "verification_codes", "id"),
    ("announcements", "announcements", "id"),
    ("config_changelog", "config_changelog", "id"),
    ("review_log", "review_log", "id"),
    ("featured_cards", "featured_cards", "id"),
    ("message_reactions", "message_reactions", "id"),
    ("geo_block_log", "geo_block_log", "id"),
    ("user_consent", "user_consent", "id"),
    ("wechat_users", "wechat_users", "openid"),
]

# ── Schema 探测 ───────────────────────────────────────────────────────────────

def get_sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names for a SQLite table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cursor.fetchall()]


async def get_pg_columns(pg: asyncpg.Connection, table: str) -> list[str]:
    """Return column names for a PG table via information_schema."""
    rows = await pg.fetch(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name = $1
           ORDER BY ordinal_position""",
        table,
    )
    return [r["column_name"] for r in rows]


def probe_sqlite_schema(conn: sqlite3.Connection) -> bool:
    """Return True if SQLite users has password_hash (old schema, needs split)."""
    cols = get_sqlite_columns(conn, "users")
    return "password_hash" in cols


# ── 类型转换 ──────────────────────────────────────────────────────────────────

# SQLite 时间戳字符串 → PG TIMESTAMPTZ 可接受的格式
TIMESTAMP_COLS: dict[str, set[str]] = {
    "users": {"created_at", "last_login_at", "last_active_at"},
    "texts": {"created_at"},
    "cards": {"created_at"},
    "sessions": {"created_at", "updated_at"},
    "messages": {"created_at"},
    "direct_messages": {"created_at"},
    "card_likes": {"created_at"},
    "card_comments": {"created_at"},
    "card_versions": {"created_at"},
    "user_posts": {"created_at"},
    "post_comments": {"created_at"},
    "post_likes": {"created_at"},
    "user_follows": {"created_at"},
    "group_sessions": {"created_at"},
    "group_messages": {"created_at"},
    "text_comments": {"created_at"},
    "card_comment_reports": {"created_at", "resolved_at"},
    "reading_progress": {"updated_at"},
    "usage_stats": {"created_at"},
    "invite_codes": {"created_at"},
    "verification_codes": {"created_at", "expires_at"},
    "announcements": {"created_at"},
    "config_changelog": {"created_at"},
    "review_log": {"created_at"},
    "featured_cards": {"created_at"},
    "message_reactions": {"created_at"},
    "geo_block_log": {"created_at"},
    "user_consent": {"created_at"},
    "wechat_users": {"created_at"},
}


def _parse_timestamp(val: str | None) -> str | None:
    """Convert SQLite timestamp string to PG-compatible format or None."""
    if val is None or val == "":
        return None
    # SQLite 格式: "2026-05-20 06:36:56" → PG 接受
    try:
        dt = datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
        return dt.isoformat()
    except ValueError:
        return val  # 原样传递，让 PG 决定


def normalize_row(
    row: dict, table: str, pg_columns: set[str],
) -> dict:
    """Filter row dict to only PG columns and convert timestamp types."""
    result = {}
    for col, val in row.items():
        if col not in pg_columns:
            continue
        # 字符串布尔 → SMALLINT
        if isinstance(val, str) and val.lower() in ("true", "false"):
            val = 1 if val.lower() == "true" else 0
        # 时间戳转换
        ts_cols = TIMESTAMP_COLS.get(table, set())
        if col in ts_cols and isinstance(val, str):
            val = _parse_timestamp(val)
        result[col] = val
    return result


# ── 核心迁移 ──────────────────────────────────────────────────────────────────

def read_sqlite_table(conn: sqlite3.Connection, table: str) -> list[dict]:
    """Read all rows from a SQLite table as dicts."""
    cursor = conn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    return [dict(zip(cols, row)) for row in rows]


def build_insert_sql(
    table: str,
    columns: list[str],
    identity: bool = False,
    conflict_col: str | None = None,
) -> str:
    """Build INSERT ... ON CONFLICT DO NOTHING statement."""
    placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
    col_names = ", ".join(columns)
    override = " OVERRIDING SYSTEM VALUE" if identity else ""
    conflict = f"ON CONFLICT ({conflict_col}) DO NOTHING" if conflict_col else "ON CONFLICT DO NOTHING"
    return f"INSERT{override} INTO {table} ({col_names}) VALUES ({placeholders}) {conflict}"


async def migrate_table(
    pg: asyncpg.Connection,
    sqlite_rows: list[dict],
    sqlite_table: str,
    pg_table: str,
    pk_col: str | None,
    identity: bool = False,
    pg_columns: set[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Migrate one SQLite table to PG. Returns {migrated, skipped, errors}."""
    result = {"migrated": 0, "skipped": 0, "errors": 0, "error_rows": []}

    if not sqlite_rows:
        return result

    if pg_columns is None:
        pg_columns = await get_pg_columns(pg, pg_table)
        pg_columns = set(pg_columns)

    # 找出 SQLite 行与 PG 列的公共列
    common_cols = [c for c in sqlite_rows[0].keys() if c in pg_columns]

    if not common_cols:
        result["error_rows"].append(f"No common columns for {sqlite_table} → {pg_table}")
        result["errors"] = len(sqlite_rows)
        return result

    if not dry_run:
        insert_sql = build_insert_sql(
            pg_table, common_cols, identity=identity, conflict_col=pk_col,
        )

    for row in sqlite_rows:
        normalized = normalize_row(row, pg_table, pg_columns)
        values = [normalized.get(c) for c in common_cols]

        if dry_run:
            result["migrated"] += 1
            continue

        try:
            tag = await pg.execute(insert_sql, *values)
            if tag and "0" not in tag:
                result["migrated"] += 1
            else:
                result["skipped"] += 1
        except Exception as exc:
            result["errors"] += 1
            id_val = row.get(pk_col, "<unknown>") if pk_col else "<composite>"
            result["error_rows"].append(f"  [{pg_table}] {id_val}: {exc}")

    return result


# ── 用户特殊处理 ──────────────────────────────────────────────────────────────

def split_user_into_secrets(user_row: dict) -> dict:
    """Extract secret columns from a SQLite user row into user_secrets dict."""
    return {
        "user_id": user_row["id"],
        "password_hash": user_row.get("password_hash", ""),
        "api_key": user_row.get("api_key", ""),
        "base_url": user_row.get("base_url", ""),
        "model": user_row.get("model", ""),
    }


async def migrate_users(
    pg: asyncpg.Connection,
    sqlite_users: list[dict],
    dry_run: bool = False,
) -> tuple[dict, dict]:
    """Migrate users → PG users + PG user_secrets, handling column split.

    Returns (users_result, secrets_result).
    """
    pg_users_cols = set(await get_pg_columns(pg, "users"))
    pg_secrets_cols = set(await get_pg_columns(pg, "user_secrets"))

    users_result = {"migrated": 0, "skipped": 0, "errors": 0, "error_rows": []}
    secrets_result = {"migrated": 0, "skipped": 0, "errors": 0, "error_rows": []}

    if not sqlite_users:
        return users_result, secrets_result

    # PG users 列: 排除 SECRET_COLUMNS
    user_cols = [c for c in sqlite_users[0].keys()
                 if c in pg_users_cols and c not in SECRET_COLUMNS]
    # home_region 不在 SQLite 中，手动追加
    user_cols.append("home_region")

    secrets_cols = ["user_id", "password_hash", "api_key", "base_url", "model"]

    if not dry_run:
        user_insert = build_insert_sql("users", user_cols, conflict_col="id")
        secrets_insert = f"""INSERT INTO user_secrets ({", ".join(secrets_cols)})
                             VALUES (${'$, $'.join(str(i+1) for i in range(len(secrets_cols)))})
                             ON CONFLICT (user_id) DO NOTHING"""

    for row in sqlite_users:
        # users 行
        user_row = normalize_row(row, "users", pg_users_cols)
        user_row.pop("password_hash", None)
        user_row.pop("api_key", None)
        user_row.pop("base_url", None)
        user_row.pop("model", None)
        user_row["home_region"] = "cn-shenzhen"  # 默认值
        user_vals = [user_row.get(c) for c in user_cols]

        # user_secrets 行
        secret_row = split_user_into_secrets(row)
        secret_vals = [secret_row.get(c) for c in secrets_cols]

        if dry_run:
            users_result["migrated"] += 1
            secrets_result["migrated"] += 1
            continue

        try:
            tag = await pg.execute(user_insert, *user_vals)
            if tag and "0" not in tag:
                users_result["migrated"] += 1
            else:
                users_result["skipped"] += 1
        except Exception as exc:
            users_result["errors"] += 1
            users_result["error_rows"].append(f"  [users] {row['id']}: {exc}")

        try:
            tag = await pg.execute(secrets_insert, *secret_vals)
            if tag and "0" not in tag:
                secrets_result["migrated"] += 1
            else:
                secrets_result["skipped"] += 1
        except Exception as exc:
            secrets_result["errors"] += 1
            secrets_result["error_rows"].append(f"  [user_secrets] {row['id']}: {exc}")

    return users_result, secrets_result


# ── 主流程 ────────────────────────────────────────────────────────────────────

def print_header(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}")


def print_result(label: str, result: dict) -> None:
    migrated = result["migrated"]
    skipped = result["skipped"]
    errors = result["errors"]
    print(f"  {label:30s} {migrated:>5} migrated, {skipped:>5} skipped", end="")
    if errors:
        print(f", {errors} ERRORS", end="")
        for err in result.get("error_rows", []):
            print(f"\n    {err}")
    print()


async def _dry_run_sqlite_only(sqlite_conn: sqlite3.Connection) -> int:
    """Dry-run without PG: just count rows from SQLite."""
    print_header("Dry-Run（仅 SQLite 计数，无 PG 校验）")
    total = 0
    for sqlite_table, pg_table, _pk_col in TABLE_ORDER:
        if sqlite_table is None:
            continue
        rows = read_sqlite_table(sqlite_conn, sqlite_table)
        indicator = "→" if rows else " "
        print(f"  {sqlite_table:25s} {indicator} {pg_table:25s} {len(rows):>6} rows")
        total += len(rows)
    print(f"\n  总计: {total} rows across {sum(1 for t, _, _ in TABLE_ORDER if t)} tables")
    return 0


async def run_migration(
    sqlite_path: str,
    pg_url: str,
    dry_run: bool = False,
    skip_pg: bool = False,
) -> int:
    """Run the migration. Returns 0 on success, 1 on failure."""
    # ── 1. 连接 SQLite（只读） ──
    print_header(f"连接 SQLite: {sqlite_path}")
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    has_password_hash = probe_sqlite_schema(sqlite_conn)
    print(f"  SQLite schema: {'old (password_hash in users → need column split)' if has_password_hash else 'new (user_secrets exists)'}")

    # ── 1a. Dry-run 无 PG ──
    if skip_pg:
        return await _dry_run_sqlite_only(sqlite_conn)

    # ── 2. 连接 PG ──
    if dry_run:
        print_header(f"Dry-Run 连接 PG: 只读校验列兼容性")
    else:
        print_header(f"连接 PG: {pg_url}")
    pg = await asyncpg.connect(pg_url)
    print("  连接成功")

    # ── 3. 逐表迁移 ──
    totals = {"migrated": 0, "skipped": 0, "errors": 0}
    failed_tables: list[str] = []

    for sqlite_table, pg_table, pk_col in TABLE_ORDER:
        if sqlite_table is None and pg_table == "user_secrets":
            # 从 SQLite users 构造 user_secrets
            if has_password_hash:
                all_users = read_sqlite_table(sqlite_conn, "users")
            else:
                all_users = read_sqlite_table(sqlite_conn, "user_secrets")

            if dry_run:
                print(f"\n  {pg_table:30s}  → {len(all_users)} rows (from users)")
                totals["migrated"] += len(all_users)
            else:
                u_result, s_result = await migrate_users(pg, all_users, dry_run=False)
                print_result(f"  users → users", u_result)
                print_result(f"  users → {pg_table}", s_result)
                for k in totals:
                    totals[k] += u_result.get(k, 0) + s_result.get(k, 0)
                if u_result.get("errors", 0) or s_result.get("errors", 0):
                    failed_tables.append(pg_table)
            continue

        if sqlite_table is None:
            continue  # PG-only 表，跳过

        sqlite_rows = read_sqlite_table(sqlite_conn, sqlite_table)
        if not sqlite_rows:
            print(f"  {sqlite_table:30s}  0 rows")
            continue

        identity = sqlite_table in IDENTITY_TABLES or pg_table in IDENTITY_TABLES
        pg_cols = set(await get_pg_columns(pg, pg_table))

        if dry_run:
            common = [c for c in sqlite_rows[0].keys() if c in pg_cols]
            missing = set(sqlite_rows[0].keys()) - pg_cols - (SECRET_COLUMNS if sqlite_table == "users" else set())
            extra = pg_cols - set(sqlite_rows[0].keys()) - {"home_region"}
            print(f"  {sqlite_table:25s} → {pg_table:25s} {len(sqlite_rows):>6} rows"
                  f"  |  {len(common)} common cols", end="")
            if missing:
                print(f"  |  SQLite-only: {', '.join(sorted(missing))}", end="")
            if extra:
                print(f"  |  PG-only (defaulted): {', '.join(sorted(extra))}", end="")
            print()
            totals["migrated"] += len(sqlite_rows)
            continue

        # 每表独立事务
        async with pg.transaction():
            result = await migrate_table(
                pg, sqlite_rows, sqlite_table, pg_table, pk_col,
                identity=identity, pg_columns=pg_cols, dry_run=False,
            )

        print_result(f"  {sqlite_table} → {pg_table}", result)
        for k in totals:
            totals[k] += result.get(k, 0)
        if result.get("errors", 0):
            failed_tables.append(pg_table)

    # ── 4. 汇总 ──
    note = " (dry-run, no data written)" if dry_run else ""
    print_header(f"迁移汇总{note}")
    print(f"  总计: {totals['migrated']} migrated, {totals['skipped']} skipped, {totals['errors']} errors")
    if dry_run:
        print(f"  ⚠  DRY-RUN 模式 — 未写入任何数据。确认行数后去掉 --dry-run 执行真迁移。")
    if failed_tables:
        print(f"  !! 失败表: {', '.join(failed_tables)}")
        print(f"  请检查上面 ERRORS 标记的详细原因")
    elif not dry_run:
        print(f"  所有表迁移完成 ✓")

    await pg.close()
    sqlite_conn.close()
    return 1 if failed_tables else 0


async def backup_pg(pg_url: str) -> None:
    """Print a reminder to backup PG before running."""
    print_header("提示")
    print("  请先备份 PG（虽然现在是空的，习惯要有）：")
    print(f"    pg_dump {pg_url} > pg_backup_$(date +%Y%m%d_%H%M%S).sql")
    print("  或者用 Docker：")
    print("    docker exec <container> pg_dump -U postgres character_sim > backup.sql")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SQLite → PostgreSQL 一次性数据迁移",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="连接 PG 校验列兼容性，只统计不写入",
    )
    parser.add_argument(
        "--skip-pg", action="store_true",
        help="跳过 PG 连接，只统计 SQLite 行数（不校验列兼容性）",
    )
    parser.add_argument(
        "--sqlite", default=os.environ.get("SQLITE_PATH", DEFAULT_SQLITE),
        help=f"SQLite 路径（默认 {DEFAULT_SQLITE}）",
    )
    parser.add_argument(
        "--pg-url", default=os.environ.get("DATABASE_URL", DEFAULT_PG_URL),
        help="PG 连接字符串（默认 $DATABASE_URL 或 localhost）",
    )
    args = parser.parse_args()

    if not os.path.exists(args.sqlite):
        print(f"错误: SQLite 文件不存在: {args.sqlite}")
        return 1

    if args.dry_run:
        print("── DRY-RUN 模式：只统计，不写入 ──")
    if args.skip_pg:
        print("── SKIP-PG 模式：不连接 PG，仅 SQLite 行数统计 ──")

    return asyncio.run(run_migration(
        sqlite_path=args.sqlite,
        pg_url=args.pg_url,
        dry_run=args.dry_run,
        skip_pg=args.skip_pg,
    ))


if __name__ == "__main__":
    sys.exit(main())
