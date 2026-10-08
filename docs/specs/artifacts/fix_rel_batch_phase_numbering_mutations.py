"""spec `fix-rel-batch-phase-numbering.md` 的变异对账表：阶段序号口径被打坏必须红。一次性产物。

执行框架与判档不在本文件里：改文件、跑 pytest、按字节还原、基线门都用
`tests/perf/mutation_framework.py` 的 `run_oneoff`（与 `personality_inject_mutations.py` 同一处置）。

用法：仓库根目录 `.venv/Scripts/python.exe docs/specs/artifacts/fix_rel_batch_phase_numbering_mutations.py`
期望：全部 RED。退出码：0 = 全部符合预期；1 = 有存活；2 = 基线红（拒跑）。
"""
from __future__ import annotations

import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "perf"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_framework as framework  # noqa: E402

RB = ROOT / "core" / "relationship_batch.py"
TARGETS = (RB,)

UNIT = "tests/test_arc_phase_fields_unit.py"
NUMBERED = f"{UNIT}::test_rel_batch_prompt_numbers_phases_by_position"
NO_PHASE = f"{UNIT}::test_rel_batch_prompt_without_phases_marks_none_and_rule_says_zero"

_ENUM = "    named = [(i, p) for i, p in enumerate(phases, 1) if p]"
_JOIN = '    stage = "；".join(f"{i}. {p}" for i, p in named) or "（无阶段）"'
_RULE4 = '    "4. phase 填阶段序号（整数 1..n）；没有阶段时填 0。\\n"'


def _m(label: str, target: str, old: str, new: str):
    return (label, target, [("repl", RB, [(old, new)])], "RED")


MUTANTS = [
    # 题目点名的两向：去掉编号 → 红；编号从 0 起（错位一格）→ 红。
    _m("MG1 去掉阶段编号（退回「名称、名称」）", NUMBERED, _JOIN,
       '    stage = "、".join(p for _, p in named) or "（无阶段）"'),
    _m("MG2 阶段编号从 0 起（错位一格）", NUMBERED, _ENUM,
       "    named = [(i, p) for i, p in enumerate(phases, 0) if p]"),
    # 作者补的第三向：无阶段分支的口径里没有「没有阶段时填 0」时，那条断言要能分辨。
    _m("MG3 口径删掉「没有阶段时填 0」", NO_PHASE, _RULE4,
       '    "4. phase 填阶段序号（整数 1..n）。\\n"'),
]

if __name__ == "__main__":
    sys.exit(framework.run_oneoff(MUTANTS, targets=TARGETS))
