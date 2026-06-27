#!/usr/bin/env python3
"""存量 psyche 回填：对 DB 中 psyche 为默认值的卡，仅回填 psyche 字段。

用法：
  python scripts/backfill_psyche.py                    # dry-run 模式（默认）
  python scripts/backfill_psyche.py --no-dry-run       # 实际回填

幂等：已有非默认 psyche 的卡跳过。
安全：只写 card_json 中的 psyche 字段，绝不触碰其他字段。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import sqlite3
from pathlib import Path


# ── 工具函数 ──────────────────────────────────────────────

def _load_env() -> None:
    """手动加载项目根 .env。"""
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


def _is_psyche_default(psyche: dict | None) -> bool:
    """psyche 为 None，或 triggers/soft_spots 空且 baseline∈[48,52] 视为默认。"""
    if psyche is None:
        return True
    t = psyche.get("triggers")
    s = psyche.get("soft_spots")
    bl = psyche.get("affinity_baseline", 50)
    triggers_empty = not t or (isinstance(t, list) and len(t) == 0)
    soft_spots_empty = not s or (isinstance(s, list) and len(s) == 0)
    baseline_default = isinstance(bl, (int, float)) and 48 <= int(round(bl)) <= 52
    return triggers_empty and soft_spots_empty and baseline_default


def _parse_card_json(raw: str | dict | None) -> dict | None:
    """解析 card_json，支持 str 和 dict 两种格式。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Psyche-only 蒸馏 prompt ───────────────────────────────

PSYCHE_SYSTEM_PROMPT = (
    "你是一个角色心理画像专家。基于角色分析数据，"
    "提取该角色的心理画像（psyche profile），只返回 JSON，不要任何其他内容。"
)


def _build_psyche_prompt(card_data: dict) -> str:
    """从卡片已有分析字段构建 psyche-only 蒸馏 prompt。"""
    lines: list[str] = []
    lines.append(f"角色名: {card_data.get('name', '未知')}")

    identity = card_data.get("identity", "")
    if identity:
        lines.append(f"身份: {identity}")

    traits = card_data.get("personality_traits", [])
    if traits:
        lines.append("\n性格特质:")
        for t in traits:
            lines.append(f"  - {t}")

    patterns = card_data.get("emotional_patterns", [])
    if patterns:
        lines.append("\n情感模式:")
        for p in patterns:
            lines.append(f"  - {p}")

    values = card_data.get("values", [])
    if values:
        lines.append("\n价值观:")
        for v in values:
            lines.append(f"  - {v}")

    tensions = card_data.get("inner_tensions", [])
    if tensions:
        lines.append("\n内在矛盾:")
        for t in tensions:
            lines.append(f"  - {t}")

    background = card_data.get("background", "")
    if background:
        lines.append(f"\n背景: {background[:600]}")

    relationships = card_data.get("relationships", [])
    if relationships and isinstance(relationships, list):
        lines.append("\n人际关系（本角色视角）:")
        for r in relationships[:5]:
            if isinstance(r, dict):
                target = r.get("target", "?")
                attitude = r.get("attitude", "")
                lines.append(f"  - 对{target}: {attitude}")

    decision = card_data.get("decision_style", "")
    if decision:
        lines.append(f"\n决策风格: {decision[:200]}")

    lines.append(
        """

基于以上角色分析，提取该角色的心理画像（psyche profile）。
只返回以下 JSON，不要任何其他内容：
{
  "openness": <1-5整数>,
  "conscientiousness": <1-5整数>,
  "extraversion": <1-5整数>,
  "agreeableness": <1-5整数>,
  "neuroticism": <1-5整数>,
  "affinity_baseline": <0-100整数>,
  "volatility": "平稳|适中|剧烈",
  "grudge_inertia": "大度|一般|记仇",
  "triggers": ["一句话雷点（含原文依据）", ...],
  "soft_spots": ["一句话软肋（含原文依据）", ...]
}

注意：
- 大五人格各 1-5 分（1 极低 5 极高），必须符合真实人格分布的一致性
  （高神经质+低宜人性是典型组合；高宜人性+高神经质需有原文证据支撑）
- 高神经质偏 volatility="剧烈"、grudge_inertia="记仇"
- 低宜人性偏 grudge_inertia="记仇"
- affinity_baseline：高冷/谨慎者 25-40，热情/外向者 55-70，多数人 45-55
- triggers（1-3 条）：从内在矛盾、冲突、情感模式提取
- soft_spots（1-3 条）：从人际关系、情感模式、价值观提取
- 确保 triggers/soft_spots 至少有 1 条，不要空数组"""
    )
    return "\n".join(lines)


# ── LLM 调用 ───────────────────────────────────────────────

def _call_llm_for_psyche(api_key: str, prompt: str) -> dict | None:
    """调用 DeepSeek 生成 psyche 字段。"""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1", timeout=120)
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": PSYCHE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        text = resp.choices[0].message.content.strip()
    except Exception as exc:
        print(f"    LLM call error: {exc}")
        return None

    # 提取 JSON
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not json_match:
        print(f"    No JSON found in LLM response: {text[:200]}")
        return None
    try:
        result = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        print(f"    JSON parse error: {exc}")
        return None

    # 校验必需字段
    required = ["openness", "conscientiousness", "extraversion",
                 "agreeableness", "neuroticism", "affinity_baseline",
                 "volatility", "grudge_inertia"]
    missing = [k for k in required if k not in result]
    if missing:
        print(f"    Missing required fields: {missing}")
        return None

    # 确保 triggers 和 soft_spots 是数组
    for arr_field in ("triggers", "soft_spots"):
        if arr_field not in result or not isinstance(result[arr_field], list):
            result[arr_field] = []
    return result


# ── SQLite ─────────────────────────────────────────────────

def _query_sqlite(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, card_json FROM cards WHERE deleted_at IS NULL"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _update_sqlite(db_path: str, card_id: str, card_json: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE cards SET card_json = ? WHERE id = ?", (card_json, card_id))
        conn.commit()
        return True
    except Exception as exc:
        print(f"    DB update failed: {exc}")
        return False
    finally:
        conn.close()


# ── Postgres ───────────────────────────────────────────────

async def _query_postgres(dsn: str) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, name, card_json FROM cards WHERE deleted_at IS NULL"
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _update_postgres(dsn: str, card_id: str, card_json: str) -> bool:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE cards SET card_json = $1 WHERE id = $2",
            card_json, card_id,
        )
        return True
    except Exception as exc:
        print(f"    DB update failed: {exc}")
        return False
    finally:
        await conn.close()


# ── 主流程 ─────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="存量 psyche 回填：对 DB 中 psyche 为默认值的卡仅回填 psyche 字段"
    )
    parser.add_argument(
        "--no-dry-run", action="store_true",
        help="实际执行回填（默认 dry-run 只预览）"
    )
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="跳过 LLM 调用，只分析哪些卡需要回填（配合 dry-run 使用）"
    )
    args = parser.parse_args()
    dry_run = not args.no_dry_run

    _load_env()

    backend = os.getenv("STORAGE_BACKEND", "").strip()
    if not backend:
        print("ERROR: STORAGE_BACKEND 未设置")
        sys.exit(1)

    # ── 连接 DB ──────────────────────────────────────────
    dsn: str | None = None
    db_path: str | None = None
    if backend == "sqlite":
        db_path = os.getenv("DB_PATH", "data/charsim.db").strip()
        print(f"[连接 SQLite: {db_path}]")
        rows = _query_sqlite(db_path)
        update_fn = lambda cid, cj: _update_sqlite(db_path, cid, cj)
    elif backend == "postgres":
        dsn = os.getenv("DATABASE_URL", "").strip()
        if not dsn:
            print("ERROR: STORAGE_BACKEND=postgres 但 DATABASE_URL 未设置")
            sys.exit(1)
        safe_dsn = dsn.split("@")[-1] if "@" in dsn else dsn
        print(f"[连接 Postgres: {safe_dsn}]")
        rows = asyncio.run(_query_postgres(dsn))
        update_fn = lambda cid, cj: asyncio.run(_update_postgres(dsn, cid, cj))
    else:
        print(f"ERROR: 不支持的 STORAGE_BACKEND={backend}")
        sys.exit(1)

    print(f"  查询到 {len(rows)} 张卡片\n")

    # ── 筛选需要回填的卡 ────────────────────────────────
    to_backfill: list[dict] = []
    already_ok: list[dict] = []
    parse_errors = 0

    for r in rows:
        raw = r.get("card_json")
        card_data = _parse_card_json(raw)
        if card_data is None:
            parse_errors += 1
            continue
        psyche = card_data.get("psyche") if isinstance(card_data, dict) else None
        if _is_psyche_default(psyche):
            to_backfill.append(r)
        else:
            already_ok.append(r)

    print(f"  已有非默认 psyche: {len(already_ok)}")
    print(f"  需要回填:          {len(to_backfill)}")
    if parse_errors:
        print(f"  解析失败:          {parse_errors}")

    if not to_backfill:
        print("\n所有卡片均已包含非默认 psyche，无需回填。")
        return

    # ── 列出待回填卡片 ──────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  待回填卡片清单:")
    for r in to_backfill:
        raw = r.get("card_json")
        card_data = _parse_card_json(raw)
        name = card_data.get("name", r.get("name", "?")) if card_data else r.get("name", "?")
        print(f"    [{r['id'][:12]}] {name}")

    if dry_run:
        print(f"\n  [Dry-run] 共 {len(to_backfill)} 张卡需要回填。")
        print(f"  执行实际回填请加 --no-dry-run 参数。")
        return

    if args.skip_llm:
        print(f"\n  --skip-llm 模式：仅分析，不调用 LLM。")
        return

    # ── 执行回填 ─────────────────────────────────────────
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY 未设置")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"  开始回填 {len(to_backfill)} 张卡片...\n")

    success = 0
    failed = 0

    for idx, r in enumerate(to_backfill, 1):
        card_id = r["id"]
        raw = r.get("card_json")
        card_data = _parse_card_json(raw)
        if card_data is None:
            print(f"  [{idx}/{len(to_backfill)}] {card_id[:12]} 解析失败，跳过")
            failed += 1
            continue

        name = card_data.get("name", r.get("name", "?"))
        print(f"  [{idx}/{len(to_backfill)}] {name}...", end=" ", flush=True)

        try:
            prompt = _build_psyche_prompt(card_data)
            psyche = _call_llm_for_psyche(api_key, prompt)
            if psyche is None:
                print("FAILED (LLM)")
                failed += 1
                continue

            # 仅更新 psyche 字段
            card_data["psyche"] = psyche
            updated_json = json.dumps(card_data, ensure_ascii=False)

            ok = update_fn(card_id, updated_json)
            if ok:
                print(f"OK  (baseline={psyche.get('affinity_baseline')}, "
                      f"volatility={psyche.get('volatility')}, "
                      f"triggers={len(psyche.get('triggers', []))}, "
                      f"soft_spots={len(psyche.get('soft_spots', []))})")
                success += 1
            else:
                print("FAILED (DB)")
                failed += 1
        except Exception as exc:
            print(f"ERROR: {exc}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"  回填完成: success={success}, failed={failed}")


if __name__ == "__main__":
    main()
