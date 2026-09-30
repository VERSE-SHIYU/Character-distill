# -*- coding: utf-8 -*-
"""人类 gold (gold_final.json, reconciled) vs judge (judge_labels.jsonl) 打分。

报告（用户口径）：
- per-dim κ 分开报（d1/d2/d4），不取平均；d3 本语料不可评，单独声明。
- overall = CRPO strict-win（某侧至少在 ≥1 维胜且无任何维落败才算该侧赢，否则平局），
  由各维推导，κ 同时报平局率。
- 每维附带：原始一致率、双标平局率、不一致明细（哪些 pair、判向）。
κ 类别 = {A, tie, B}，Cohen's kappa 名义版。
"""
import argparse
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(HERE, "gold_final.json")
DEFAULT_LABELS = os.path.join(HERE, "judge_labels.jsonl")
GKEY = {"d1": "d1_persona", "d2": "d2_style", "d4": "d4_emotion"}   # gold_final.json 的字段名
DIMS = ["d1", "d2", "d4"]          # d3 excluded（语料不可评）
DIM_LABEL = {"d1": "d1 人设符合", "d2": "d2 腔调稳定", "d4": "d4 情感连贯"}
CATS = ["A", "tie", "B"]


def cohen_kappa(h, j):
    n = len(h)
    if n == 0:
        return float("nan")
    # expected agreement under marginal independence
    ph = {c: sum(x == c for x in h) / n for c in CATS}
    pj = {c: sum(x == c for x in j) / n for c in CATS}
    pe = sum(ph[c] * pj[c] for c in CATS)
    po = sum(1 for a, b in zip(h, j) if a == b) / n
    if pe == 1:
        return float("nan")
    return (po - pe) / (1 - pe)


def crpo(votes):
    na = sum(1 for v in votes if v == "A")
    nb = sum(1 for v in votes if v == "B")
    if na > 0 and nb == 0:
        return "A"
    if nb > 0 and na == 0:
        return "B"
    return "tie"


def main(labels):
    gold = {g["pair_id"]: g for g in json.load(open(GOLD, encoding="utf-8"))}
    judge = {r["pair_id"]: r for r in map(json.loads, open(labels, encoding="utf-8"))}
    pids = sorted(set(gold) & set(judge))
    print(f"aligned pairs: {len(pids)}  (human {len(gold)} / judge {len(judge)})\n")
    print(f"judge provider/model: {judge[pids[0]].get('provider')}/{judge[pids[0]].get('model')} "
          f"(from {os.path.basename(labels)})")

    gold_crpo = {p: crpo([gold[p][GKEY[d]] for d in DIMS]) for p in pids}
    judge_crpo = {p: crpo([judge[p][d] for d in DIMS]) for p in pids}

    # per-dim kappa + detail
    for d in DIMS:
        h = [gold[p][GKEY[d]] for p in pids]
        j = [judge[p][d] for p in pids]
        k = cohen_kappa(h, j)
        po = sum(1 for a, b in zip(h, j) if a == b) / len(h)
        ht = h.count("tie") / len(h)
        jt = j.count("tie") / len(j)
        mis = [f"{p}:人{gold[p][GKEY[d]]}/机{judge[p][d]}" for p in pids if gold[p][GKEY[d]] != judge[p][d]]
        print(f"\n[{DIM_LABEL[d]}]  κ={k:.3f}  一致率={po:.2f}  "
              f"平局率 人{ht:.2f}/机{jt:.2f}")
        print("   不一致:", " ".join(mis) if mis else "(无)")

    # CRPO overall
    ho = [gold_crpo[p] for p in pids]
    jo = [judge_crpo[p] for p in pids]
    k = cohen_kappa(ho, jo)
    po = sum(1 for a, b in zip(ho, jo) if a == b) / len(ho)
    ht = Counter(ho)["tie"] / len(ho)
    jt = Counter(jo)["tie"] / len(jo)
    print(f"\n[overall CRPO]  κ={k:.3f}  一致率={po:.2f}  平局率 人{ht:.2f}/机{jt:.2f}")
    print("   human overall:", dict(Counter(ho)))
    print("   judge overall:", dict(Counter(jo)))
    mis = [f"{p}:人{ho[i]}/机{jo[i]}" for i, p in enumerate(pids) if ho[i] != jo[i]]
    print("   不一致:", " ".join(mis) if mis else "(无)")

    # d3 note
    d3_ties = all(g["d3_knowledge"] == "tie" for g in gold.values())
    print(f"\n[d3] 全 tie={d3_ties} → 本语料不可评，不计入 κ（需专门越界探测样本）。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=DEFAULT_LABELS, help="judge 标签 jsonl 路径")
    args = ap.parse_args()
    if not os.path.exists(args.labels):
        sys.exit(f"缺 {args.labels} —— 先跑 judge_run.py 生成 judge 标签。")
    main(args.labels)
