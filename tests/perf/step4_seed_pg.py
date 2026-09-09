# -*- coding: utf-8 -*-
"""②④ Step 4 rig：把 dev sqlite 的最小 fixture 行拷进 rig PostgreSQL。

rig 是全空的 fresh PG（数据目录/凭据/端口全部独立，见 data/eval_scratch/s4/）。
本脚本只拷「打通 chat(agent_mode) 与 distill 冒烟」所需的闭包，不是全量迁移：

  users(testadmin, 保留原 id) → texts(3d394865332c) → cards(fb975334594d)
  → sessions(75806c950ffc) → messages(该会话 1 行)

原则（用户要求）：
  - 按 PG information_schema 列清单插入，不照搬 sqlite 列。两后端 schema 由
    tests/test_schema_parity.py 保证列名一致，但类型不同 → 用 PG data_type 做显式转换。
  - password_hash 从 sqlite user_secrets 原样拷（argon2id，salt 内嵌，可跨库校验）。
  - api_config 由 store.update_user_api_config 指向 rig mock
    (host.docker.internal:MOCK_PORT)，与容器内 app 同一可达性。
  - 幂等可复跑：已存在的行跳过。

前置：app 容器已 up 且 /api/health 通过（schema 已由 app 迁移）。脚本自身的
store.create_user 也会触发迁移（幂等），故直接插入前会再确认表存在。

用法：
  python tests/perf/step4_seed_pg.py --sqlite data/character_sim.db \
      --s4env data/eval_scratch/s4/.env.s4 [--mock-base http://host.docker.internal:60950/v1]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 固定 fixture（dev sqlite 里 testadmin 的阿棠聊天图；copy 保留原 id → 复压复用 e2e 常量）
TESTADMIN = "testadmin"
FIXTURES = {
    "texts": ["3d394865332c"],           # 阿棠卡片来源文本
    "cards": ["fb975334594d"],           # 阿棠
    "sessions": ["75806c950ffc"],        # testadmin 上已有会话
    # messages: 按 session_id 拉取（id 是 PG IDENTITY，不拷贝）
}


def _utf8_print(*a) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(*a, flush=True)


# ── 读取 .env.s4 / 源 sqlite ─────────────────────────────
def parse_kv(path: Path) -> dict:
    d = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def sqlite_cols(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def read_sqlite_row(conn, table: str, where: str, params) -> dict | None:
    cols = sqlite_cols(conn, table)
    r = conn.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchone()
    if r is None:
        return None
    return {c: v for c, v in zip(cols, r)}


# ── PG 侧 introspection + 转换 ────────────────────────────
async def pg_columns(pool, table: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT column_name, data_type, is_nullable, is_identity, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """, table)
    return [dict(r) for r in rows]


def _convert(pg_type: str, value):
    """sqlite 值按 PG data_type 转换。None 原样透传（可空列留 NULL）。"""
    if value is None:
        return None
    s = value if isinstance(value, str) else str(value)
    base = pg_type.lower()
    if base.startswith("bool"):
        if isinstance(value, bool):
            return value
        return value not in ("", "0", "false", "False", "f", 0)
    if "timestamp" in base or base == "date":
        if not s.strip():
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if base == "date":
            return dt.date() if dt.tzinfo else dt.date()
        # timestamptz：naive 视为 UTC；timestamp(无 tz) 则去 tz
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if base == "timestamp without time zone":
            return dt.replace(tzinfo=None)
        return dt
    if "json" in base:
        return json.loads(s) if s.strip() else None
    if "int" in base or base == "bigint" or base == "smallint":
        return int(s) if s.strip() else None
    if "numeric" in base or "decimal" in base or base == "real" or base == "double":
        if base in ("real", "double"):
            return float(s) if s.strip() else None
        return int(s) if s.strip() and s.replace(".", "", 1).lstrip("-").isdigit() else (
            float(s) if s.strip() else None)
    return value  # text/varchar/uuid 等原样


async def copy_rows(pool, table: str, rows: list[dict]) -> tuple[int, int]:
    """按 PG 列清单插入 rows；跳过 IDENTITY 列；返回 (inserted, skipped)。"""
    cols = await pg_columns(pool, table)
    if not cols:
        raise RuntimeError(f"PG 表 {table} 不存在 —— app 未完成迁移？")
    col_names = [c["column_name"] for c in cols]
    insert_names = [c["column_name"] for c in cols if c["is_identity"] != "YES"]
    insert_types = {c["column_name"]: c["data_type"] for c in cols}
    # id 列保留（非 identity）；其余按交集
    n_ins = n_skip = 0
    for row in rows:
        pk_vals = row.get("id")
        if table == "messages":
            pk_vals = None  # identity，无显式 id
        exist = None
        if pk_vals is not None:
            exist = await pool.fetchval(f"SELECT 1 FROM {table} WHERE id = $1", pk_vals)
        elif table == "messages":
            exist = await pool.fetchval(
                "SELECT 1 FROM messages WHERE session_id = $1 AND content = $2",
                row.get("session_id"), row.get("content"))
        if exist:
            n_skip += 1
            continue
        vals = []
        for cn in insert_names:
            if cn not in row:
                vals.append(None)
            else:
                vals.append(_convert(insert_types[cn], row[cn]))
        placeholders = ", ".join(f"${i+1}" for i in range(len(insert_names)))
        names = ", ".join(insert_names)
        await pool.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})", *vals)
        n_ins += 1
    return n_ins, n_skip


# ── seed ─────────────────────────────────────────────────
async def seed(args) -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    s4 = parse_kv(Path(args.s4env))
    dsn = f"postgresql://{s4['POSTGRES_USER']}:{s4['POSTGRES_PASSWORD']}@127.0.0.1:5435/{s4['POSTGRES_DB']}"
    mock_base = args.mock_base or f"http://host.docker.internal:{s4['MOCK_PORT']}/v1"

    # app 必须先 up（schema 由 app 迁移）；health 门禁防并发迁移竞态
    import urllib.request
    try:
        with urllib.request.urlopen(f"{args.app_base}/api/health", timeout=3) as r:
            assert r.status == 200, r.status
    except Exception as exc:
        raise SystemExit(f"app 未就绪（{args.app_base}/api/health 不可达）：{exc}")

    # 载入 repo .env（FERNET/JWT 供 api_config 加密与 app 同源）
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ["STORAGE_BACKEND"] = "postgres"
    os.environ["DATABASE_URL"] = dsn

    import asyncpg
    import sqlite3

    sqlite_path = ROOT / args.sqlite
    if not sqlite_path.exists():
        raise SystemExit(f"sqlite 源不存在: {sqlite_path}")

    cs = sqlite3.connect(str(sqlite_path))
    cs.row_factory = sqlite3.Row
    _utf8_print(f"[seed] app_ok={args.app_base}  mock_base={mock_base}")

    # 1) testadmin 用户 + api_config
    from storage import get_store
    store = get_store()
    ex = await store.get_user_by_username(TESTADMIN)
    if ex:
        uid = ex["id"]
        _utf8_print(f"[seed] user {TESTADMIN} exists id={uid}")
    else:
        us = read_sqlite_row(cs, "user_secrets", "user_id = (SELECT id FROM users WHERE username = ?)", (TESTADMIN,))
        hashv = us["password_hash"] if us else None
        src_user = read_sqlite_row(cs, "users", "username = ?", (TESTADMIN,))
        uid = src_user["id"]
        if not hashv:
            # users 表里可能留了旧 hash（user_secrets 为空时 fallback）
            hashv = src_user.get("password_hash")
        await store.create_user(uid, TESTADMIN, hashv, email=src_user.get("email") or "",
                                home_region=src_user.get("home_region") or "sg")
        _utf8_print(f"[seed] user {TESTADMIN} created id={uid}")
    await store.update_user_api_config(uid, api_key="sk-e2e-mock", base_url=mock_base,
                                       model="deepseek-v4-pro", embedding_region="cn")
    _utf8_print(f"[seed] testadmin api_config -> {mock_base}")

    # 2) 直插 texts/cards/sessions + messages(会话行)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        msgs = [dict(r) for r in cs.execute(
            "SELECT * FROM messages WHERE session_id = ?", ("75806c950ffc",))]
        plan = [
            ("texts", [read_sqlite_row(cs, "texts", "id = ?", ("3d394865332c",))]),
            ("cards", [read_sqlite_row(cs, "cards", "id = ?", ("fb975334594d",))]),
            ("sessions", [read_sqlite_row(cs, "sessions", "id = ?", ("75806c950ffc",))]),
            ("messages", msgs),
        ]
        for table, rows in plan:
            rows = [r for r in rows if r is not None]
            if not rows:
                _utf8_print(f"[seed] skip {table}: sqlite 无源行")
                continue
            ins, sk = await copy_rows(pool, table, rows)
            _utf8_print(f"[seed] {table}: +{ins} (skip {sk})")
        # 3) 冒烟前结构核对：期望 6 张表全在
        missing = []
        for t in ("users", "user_secrets", "texts", "cards", "sessions", "messages"):
            if not await pg_columns(pool, t):
                missing.append(t)
        if missing:
            raise SystemExit(f"[seed] FAIL: PG 缺表 {missing}")
    finally:
        await pool.close()
    _utf8_print("[seed] done")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="data/character_sim.db")
    ap.add_argument("--s4env", default="data/eval_scratch/s4/.env.s4")
    ap.add_argument("--app-base", default="http://127.0.0.1:7862")
    ap.add_argument("--mock-base", default="")
    args = ap.parse_args()
    asyncio.run(seed(args))


if __name__ == "__main__":
    main()
