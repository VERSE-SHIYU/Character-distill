"""card_from_draft 耗时实测（spec `arc-phase-fields` §10「效率 #1」的对账数据）。

一次 `card_from_draft` 里位置核对要建几次「规范化原文 + 各阶段区间」这个上下文：修复前
**每个字段各建一次**（每个 state/experience 字段 + 做法 + 记忆 + 关系各一次 `normalize`
整本原文），修复后只在开头建一次。本脚本给出两侧可复现的数字。

用法：在仓库根目录
    .venv/Scripts/python.exe docs/specs/artifacts/arc_phase_fields_perf.py

样本卡 + 原文取自仓内 `tests/fixtures/kongyiji.txt`（公版）；脚本**不内嵌原文**，锚点与
摘录都从原文切片现取（切片坐标即阶段区间，保证位置检查照常命中、走的不是兜底路径）。
段落规模按 spec §2.3（24–29 条 / 卡）。
"""
from __future__ import annotations

import pathlib
import statistics
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.card_draft import card_from_draft  # noqa: E402
from core.card_layers import REGISTRY  # noqa: E402
from core.quotes import normalize  # noqa: E402

SOURCE = (ROOT / "tests" / "fixtures" / "kongyiji.txt").read_text(encoding="utf-8")
N_PHASES = 6
REPEATS = 30


def _sample_draft() -> dict:
    norm = normalize(SOURCE)
    step = len(norm) // N_PHASES
    # 阶段坐标即切片坐标：锚点取每段开头 8 字，摘录取段内 6 字 —— 都在该阶段区间里。
    anchors = [""] + [norm[i * step:i * step + 8] for i in range(1, N_PHASES)]
    quotes = {p: norm[(p - 1) * step + 20:(p - 1) * step + 26] for p in range(1, N_PHASES + 1)}
    all_phases = [{"phase": p, "quote": quotes[p]} for p in range(1, N_PHASES + 1)]

    draft: dict = {
        "name": "掌柜",
        "character_arc": {"axis": "从冷漠到怜悯",
                          "phases": [{"label": f"P{p}", "state": f"状态{p}",
                                      "anchor": anchors[p - 1]} for p in range(1, N_PHASES + 1)]},
        "situation_behaviors": [
            {"situation": f"情境{i}", "behavior": f"做法{i}", "occurrences": all_phases}
            for i in range(6)],
        "key_memories": [
            {"memory": f"记忆{i}", "occurrences": all_phases} for i in range(4)],
        "relationships": [
            {"target": f"对象{i}", "relation": "熟人",
             "attitudes": [{"phase": p, "attitude": f"态度{i}", "quote": quotes[p]}
                           for p in range(1, N_PHASES + 1)]} for i in range(3)],
    }
    # 每个 state/experience 字段两条：一条全程成立、一条只在阶段 3 —— 规模对齐 §2.3。
    for path, spec in REGISTRY.items():
        if spec.layer not in ("state", "experience") or path in ("key_memories",):
            continue
        value = {"state": ["全程都这样", "阶段三才这样"],
                 "experience": ["这辈子的经历", "后来的事"]}[spec.layer]
        entry = [{"value": value[0], "occurrences": all_phases},
                 {"value": value[1], "occurrences": [{"phase": 3, "quote": quotes[3]}]}]
        _set(draft, path, entry)
    return draft


def _set(root: dict, path: str, value) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        root = root.setdefault(p, {})
    root[parts[-1]] = value


def _time_calls(draft: dict) -> list[float]:
    spans = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        card_from_draft(draft, SOURCE)
        spans.append((time.perf_counter() - t0) * 1000.0)
    return spans


def main() -> int:
    draft = _sample_draft()
    card = card_from_draft(draft, SOURCE)                     # 先跑一次，确认样本有效
    phases = len(card.character_arc.phases)

    # 位置核对的上下文：一个卡里被建了几次 —— 计数口径与新增判别器（U20）相同。
    import core.phase_anchoring as pa
    calls = {"n": 0}
    real = pa.normalize

    def counting(s):
        calls["n"] += 1
        return real(s)

    pa.normalize = counting
    try:
        card_from_draft(draft, SOURCE)
    finally:
        pa.normalize = real

    spans = _time_calls(draft)
    print(f"原文 {len(SOURCE)} 字（规范化后 {len(normalize(SOURCE))} 字），阶段 {phases} 个，"
          f"每轮 {REPEATS} 次 card_from_draft")
    print(f"每次 card_from_draft 里 normalize(整本原文) 的调用次数：{calls['n']}")
    print(f"耗时 ms：最小 {min(spans):.2f}  中位 {statistics.median(spans):.2f}  "
          f"均值 {statistics.mean(spans):.2f}  最大 {max(spans):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
