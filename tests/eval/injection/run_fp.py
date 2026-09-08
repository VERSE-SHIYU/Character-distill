# -*- coding: utf-8 -*-
"""Step-3 误伤率测试：把 2.1 字段级判官跑在本地库全部干净角色卡上。

判定口径：
- 干净卡 = cards.deleted_at IS NULL 且 name NOT LIKE 'inj-%'（注入污染卡本来就该拦，
  混入会把误伤率算低）。以运行时实际数量为准。
- 一张卡被判「误伤」= 判官对它至少 flag 了一个字段（真阳性为 0，故全部视为 FP）。
- 产出：总体误伤率 + 字段级分布 + 每条误判的字段值/理由。
作用域声明在报告中写：仅在现有 N 张卡的语体分布上测得，非普适误伤率。

用法:  python tests/eval/injection/run_fp.py [--db data/character_sim.db] [--out data/eval_scratch/fp_result.json]
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
for p in (REPO, os.path.join(REPO, "web")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.moderation.card_guard import judge_card, leaf_texts  # noqa: E402


def load_cards(db_path: str):
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, name, card_json FROM cards WHERE deleted_at IS NULL"
        ).fetchall()
    finally:
        con.close()
    out = []
    for cid, name, cj in rows:
        if str(name).startswith("inj-"):
            continue
        try:
            if isinstance(cj, str):
                data = json.loads(cj)
            elif isinstance(cj, dict):
                data = cj
            else:
                continue
        except Exception:
            continue
        out.append({"id": cid, "name": name, "card": data})
    return out


def value_of(card, path):
    for p, t in leaf_texts(card):
        if p == path:
            return t
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(REPO, "data", "character_sim.db"))
    ap.add_argument("--out", default=os.path.join(REPO, "data", "eval_scratch", "fp_result.json"))
    ap.add_argument("--commit", default="")
    args = ap.parse_args()

    from adapters.llm_adapter import LLMAdapter
    llm = LLMAdapter()

    cards = load_cards(args.db)
    per_card = []
    total_fp_cards = 0
    for c in cards:
        verdict = judge_card(c["card"], llm)
        flagged = []
        for f in verdict.flagged:
            flagged.append(
                {"path": f["path"], "reason": f.get("reason", ""), "value": value_of(c["card"], f["path"])[:200]}
            )
        fp = bool(flagged) and not verdict.error
        if fp:
            total_fp_cards += 1
        per_card.append(
            {"id": c["id"], "name": c["name"], "error": verdict.error,
             "error_msg": verdict.error_msg, "fp": fp, "flagged": flagged}
        )
        status = "FP" if fp else ("ERR" if verdict.error else "ok")
        print(f"[fp] {c['name']:12s} {c['id'][:8]} {status} flags={len(flagged)}")

    n = len(cards)
    result = {
        "commit": args.commit,
        "n_cards": n,
        "n_fp_cards": total_fp_cards,
        "fp_rate": f"{total_fp_cards / n:.1%}" if n else "0",
        "per_card": per_card,
    }
    # field-level distribution
    field_counts = {}
    for c in per_card:
        if not c["fp"]:
            continue
        for f in c["flagged"]:
            field_counts[f["path"]] = field_counts.get(f["path"], 0) + 1
    result["field_distribution"] = dict(sorted(field_counts.items(), key=lambda kv: -kv[1]))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print(f"\n=== FP summary: {total_fp_cards}/{n} = {result['fp_rate']}")
    print("field distribution:", json.dumps(result["field_distribution"], ensure_ascii=False))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
