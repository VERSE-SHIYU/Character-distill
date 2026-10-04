# -*- coding: utf-8 -*-
"""arc-behaviors-draft §7 小样判定 —— 真实模型蒸出的卡，按 G1–G6 由代码判定，不靠人看。

为什么要它：代码层（分发、各入口）已由 tests/test_card_draft.py 与变异锁住；这里判的是
**模型**拿到草稿提示词后标得对不对，只能用真实蒸馏的产物来判。

输入：一张卡的 JSON 文件（落库的 card_json 原样导出），可选一份蒸馏期间的后端日志。

用法（每张卡一次）：
    python tests/perf/arc_draft_sample_check.py 孔乙己.json --expect-phases 2+ --forbid-late 争辩 --log distill.log
    python tests/perf/arc_draft_sample_check.py 阿Q.json   --expect-phases 3+
    python tests/perf/arc_draft_sample_check.py 掌柜.json  --expect-phases 0

判据（与 spec §7 同号）：
  G1 日志里「做法的阶段编号不合法」出现 0 次（给了 --log 才判）
  G2 做法条数 ≥ 3（按 situation 去重：标了几个阶段的同一条只算一次）
  G3 早期做法没漏到后面：更早阶段里含 --forbid-late 词的那几条做法，它们的 situation
     不出现在「最后阶段 ∪ 顶层」里；且更早阶段里确实有这样的做法（否则「模型没写这条」
     会被误判成通过）。按 situation 认条目而不是按词搜后面的阶段 —— 后期做法常写成
     「不再争辩」，按词搜会误报。
  G4 --expect-phases 0：没有阶段，做法全在顶层
  G5 同一处（每个阶段、顶层各算一处）没有两条相同 situation
  G6 --expect-phases N+：阶段数 ≥ N，且至少一个阶段下挂着只属于它的做法
退出码：全过 0，任一不过 1。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

WARNING = "做法的阶段编号不合法"


def _text(row: dict) -> str:
    return f"{row.get('situation', '')} {row.get('behavior', '')}"


def check(card: dict, expect: str, forbid_late: list[str], log: str | None) -> list[tuple[str, bool, str]]:
    phases = card.get("character_arc", {}).get("phases", [])
    top = card.get("situation_behaviors", [])
    buckets = [("顶层", top)] + [(f"阶段{i}", p.get("behaviors", [])) for i, p in enumerate(phases, 1)]
    out: list[tuple[str, bool, str]] = []

    if log is not None:
        n = log.count(WARNING)
        out.append(("G1", n == 0, f"warning {n} 次"))

    total = sum(len(rows) for _, rows in buckets)
    unique = len({r.get("situation") for _, rows in buckets for r in rows})
    out.append(("G2", unique >= 3, f"做法 {unique} 条（按 situation 去重）"))

    if forbid_late:
        late = {r.get("situation") for r in top + (phases[-1].get("behaviors", []) if phases else [])}
        early = [r for p in phases[:-1] for r in p.get("behaviors", [])
                 if any(w in _text(r) for w in forbid_late)]
        leaked = sorted({r.get("situation") for r in early} & late)
        out.append(("G3", bool(early) and not leaked,
                    f"更早阶段含 {forbid_late} 的做法 {len(early)} 条；漏到最后阶段∪顶层的 {leaked or '无'}"))

    dup = [name for name, rows in buckets
           if len({r.get('situation') for r in rows}) != len(rows)]
    out.append(("G5", not dup, f"重复 situation：{dup or '无'}"))

    if expect == "0":
        out.append(("G4", not phases and total == len(top), f"阶段 {len(phases)} 个，顶层 {len(top)} 条"))
    else:
        need = int(expect.rstrip("+"))
        scoped = sum(1 for p in phases if p.get("behaviors"))
        out.append(("G6", len(phases) >= need and scoped >= 1,
                     f"阶段 {len(phases)} 个（要求 ≥{need}），挂了专属做法的阶段 {scoped} 个"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("card", type=pathlib.Path)
    ap.add_argument("--expect-phases", required=True, help="0 表示无弧线；N+ 表示至少 N 个阶段")
    ap.add_argument("--forbid-late", nargs="*", default=[])
    ap.add_argument("--log", type=pathlib.Path)
    a = ap.parse_args()
    card = json.loads(a.card.read_text(encoding="utf-8"))
    log = a.log.read_text(encoding="utf-8", errors="replace") if a.log else None
    results = check(card, a.expect_phases, a.forbid_late, log)
    print(f"{card.get('name', a.card.stem)}：")
    for gid, ok, detail in results:
        print(f"  {gid} {'过' if ok else '不过'}  {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
