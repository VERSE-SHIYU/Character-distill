#!/usr/bin/env python3
"""一次性诊断：统计已蒸馏卡片的 psyche 字段分布。

只读不写，判断"通用回退"问题是否真实存在。
输出一份文本报告供人工判断。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path


# ── 加载 .env ──────────────────────────────────────────────

def _load_env() -> None:
    """手动加载项目根 .env，不依赖 python-dotenv。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


# ── DB 连接 ────────────────────────────────────────────────

def _query_sqlite(db_path: str) -> list[dict]:
    """同步 SQLite 查询。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, name, card_json FROM cards WHERE deleted_at IS NULL"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


async def _query_postgres(dsn: str) -> list[dict]:
    """异步 Postgres 查询。"""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, name, card_json FROM cards WHERE deleted_at IS NULL"
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


# ── 解析 psyche ────────────────────────────────────────────

def _parse_psyche(card_json_raw: str | dict | None) -> dict | None:
    """从 card_json 提取 psyche 字段。"""
    if card_json_raw is None:
        return None
    if isinstance(card_json_raw, dict):
        data = card_json_raw
    else:
        try:
            data = json.loads(card_json_raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    psyche = data.get("psyche")
    return psyche if isinstance(psyche, dict) else None


# ── 统计逻辑 ──────────────────────────────────────────────

def _analyze(rows: list[dict]) -> dict:
    total = len(rows)

    triggers_empty = 0
    soft_spots_empty = 0
    baselines: list[int] = []
    volatility_counter: Counter[str] = Counter()
    grudge_counter: Counter[str] = Counter()
    agreeableness_counter: Counter[int] = Counter()
    all_default = 0
    no_psyche = 0

    for r in rows:
        psyche = _parse_psyche(r.get("card_json"))
        if psyche is None:
            no_psyche += 1
            continue

        # triggers
        t = psyche.get("triggers")
        if not t or (isinstance(t, list) and len(t) == 0):
            triggers_empty += 1

        # soft_spots
        s = psyche.get("soft_spots")
        if not s or (isinstance(s, list) and len(s) == 0):
            soft_spots_empty += 1

        # affinity_baseline
        bl = psyche.get("affinity_baseline", 50)
        if isinstance(bl, (int, float)):
            baselines.append(int(round(bl)))

        # volatility
        volatility_counter[str(psyche.get("volatility", "适中"))] += 1

        # grudge_inertia
        grudge_counter[str(psyche.get("grudge_inertia", "一般"))] += 1

        # agreeableness
        ag = psyche.get("agreeableness", 3)
        agreeableness_counter[int(ag) if isinstance(ag, (int, float)) else 3] += 1

        # 同时命中 triggers空 + soft_spots空 + baseline∈[48,52]
        t_def = not t or (isinstance(t, list) and len(t) == 0)
        s_def = not s or (isinstance(s, list) and len(s) == 0)
        bl_def = isinstance(bl, (int, float)) and 48 <= int(round(bl)) <= 52
        if t_def and s_def and bl_def:
            all_default += 1

    baseline_dist = Counter(baselines)

    return {
        "total": total,
        "triggers_empty": triggers_empty,
        "triggers_empty_pct": triggers_empty / total * 100,
        "soft_spots_empty": soft_spots_empty,
        "soft_spots_empty_pct": soft_spots_empty / total * 100,
        "baselines": baselines,
        "baseline_dist": baseline_dist,
        "baseline_default_count": sum(1 for v in baselines if 48 <= v <= 52),
        "baseline_default_pct": sum(1 for v in baselines if 48 <= v <= 52) / len(baselines) * 100
        if baselines
        else 0,
        "volatility_counter": volatility_counter,
        "volatility_default_count": volatility_counter.get("适中", 0),
        "volatility_default_pct": volatility_counter.get("适中", 0) / total * 100,
        "grudge_counter": grudge_counter,
        "grudge_default_count": grudge_counter.get("一般", 0),
        "grudge_default_pct": grudge_counter.get("一般", 0) / total * 100,
        "agreeableness_counter": agreeableness_counter,
        "agreeableness_default_count": agreeableness_counter.get(3, 0),
        "agreeableness_default_pct": agreeableness_counter.get(3, 0) / total * 100,
        "all_default_count": all_default,
        "all_default_pct": all_default / total * 100,
        "no_psyche": no_psyche,
    }


# ── 报告输出 ──────────────────────────────────────────────

def _print_report(s: dict) -> None:
    print("=" * 62)
    print("  Psyche 字段分布诊断报告")
    print("=" * 62)
    print(f"  总卡片数:              {s['total']}")
    if s["total"] == 0:
        print("\n  (无卡片数据)")
        return

    print(f"  无 psyche 字段:        {s['no_psyche']}")
    print()

    # 1
    print(f"  [1] triggers 为空:     {s['triggers_empty']:>4}/{s['total']}  ({s['triggers_empty_pct']:5.1f}%)")

    # 2
    print(f"  [2] soft_spots 为空:   {s['soft_spots_empty']:>4}/{s['total']}  ({s['soft_spots_empty_pct']:5.1f}%)")

    # 3
    n_bl = len(s["baselines"])
    print(f"  [3] affinity_baseline:")
    print(f"       疑似默认 [48,52]: {s['baseline_default_count']:>4}/{n_bl}  ({s['baseline_default_pct']:5.1f}%)")
    print(f"       分布直方图:")
    if s["baseline_dist"]:
        max_cnt = max(s["baseline_dist"].values())
        bar_max = 40
        for v in sorted(s["baseline_dist"]):
            cnt = s["baseline_dist"][v]
            bar_len = max(1, round(cnt / max_cnt * bar_max)) if max_cnt else 1
            bar = "█" * bar_len
            marker = "  ← default range" if 48 <= v <= 52 else ""
            print(f"         {v:>4}: {bar} ({cnt}){marker}")

    # 4
    print(f"  [4] volatility:")
    print(f"       默认'适中':       {s['volatility_default_count']:>4}/{s['total']}  ({s['volatility_default_pct']:5.1f}%)")
    for k, cnt in sorted(s["volatility_counter"].items()):
        print(f"         {k}: {cnt}")

    # 5
    print(f"  [5] grudge_inertia:")
    print(f"       默认'一般':       {s['grudge_default_count']:>4}/{s['total']}  ({s['grudge_default_pct']:5.1f}%)")
    for k, cnt in sorted(s["grudge_counter"].items()):
        print(f"         {k}: {cnt}")

    # 6
    print(f"  [6] agreeableness:")
    print(f"       默认 3:           {s['agreeableness_default_count']:>4}/{s['total']}  ({s['agreeableness_default_pct']:5.1f}%)")
    for k, cnt in sorted(s["agreeableness_counter"].items()):
        print(f"         {k}: {cnt}")

    # 7
    print(f"  [7] triggers空 + soft_spots空 + baseline∈[48,52]:")
    print(f"       {s['all_default_count']:>4}/{s['total']}  ({s['all_default_pct']:5.1f}%)  ← 完全落回通用档")

    print("=" * 62)


# ── 入口 ──────────────────────────────────────────────────

def main() -> None:
    _load_env()

    backend = os.getenv("STORAGE_BACKEND", "").strip()
    if not backend:
        print("ERROR: STORAGE_BACKEND 未设置")
        sys.exit(1)

    if backend == "sqlite":
        db_path = os.getenv("DB_PATH", "data/charsim.db").strip()
        print(f"[连接 SQLite: {db_path}]\n")
        rows = _query_sqlite(db_path)
    elif backend == "postgres":
        dsn = os.getenv("DATABASE_URL", "").strip()
        if not dsn:
            print("ERROR: STORAGE_BACKEND=postgres 但 DATABASE_URL 未设置")
            sys.exit(1)
        safe_dsn = dsn.split("@")[-1] if "@" in dsn else dsn
        print(f"[连接 Postgres: {safe_dsn}]\n")
        rows = asyncio.run(_query_postgres(dsn))
    else:
        print(f"ERROR: 不支持的 STORAGE_BACKEND={backend}")
        sys.exit(1)

    print(f"  查询到 {len(rows)} 张卡片\n")
    stats = _analyze(rows)
    _print_report(stats)


if __name__ == "__main__":
    main()
