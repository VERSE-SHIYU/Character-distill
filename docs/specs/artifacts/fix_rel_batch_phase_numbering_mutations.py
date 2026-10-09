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
DRAFT = ROOT / "core" / "card_draft.py"
TARGETS = (RB, DRAFT)

UNIT = "tests/test_arc_phase_fields_unit.py"
NUMBERED = f"{UNIT}::test_rel_batch_prompt_numbers_phases_by_position"
NO_PHASE = f"{UNIT}::test_rel_batch_prompt_without_phases_marks_none_and_rule_says_zero"
RULE4 = f"{UNIT}::test_rel_batch_rule4_pins_one_based_range"
BAD_OCC = f"{UNIT}::test_bad_phase_number_on_occurrence_retracts_row_not_whole_card"
SCHEMA = f"{UNIT}::test_draft_schema_phase_stays_integer"
BOOL_BAD = f"{UNIT}::test_phase_number_bool_is_bad_value_retracts"
FRAC_BAD = f"{UNIT}::test_phase_number_fraction_is_bad_value_retracts"

_ENUM = "    named = [(i, p) for i, p in enumerate(phases, 1) if p]"
_JOIN = '    stage = "；".join(f"{i}. {p}" for i, p in named) or "（无阶段）"'
_RULE4 = '    "4. phase 填阶段序号（整数 1..n）；没有阶段时填 0。\\n"'
# `_phase_number` 的兜底两行；MC1/MC2 只动这两行的形状。
_FALLBACK = '    logger.warning("[card_draft] 阶段编号不是整数，按 0 处理：%r", value)\n    return 0'


def _m(label: str, target: str, path: pathlib.Path, old: str, new: str):
    return (label, target, [("repl", path, [(old, new)])], "RED")


MUTANTS = [
    # 题目点名的两向：去掉编号 → 红；编号从 0 起（错位一格）→ 红。
    _m("MG1 去掉阶段编号（退回「名称、名称」）", NUMBERED, RB, _JOIN,
       '    stage = "、".join(p for _, p in named) or "（无阶段）"'),
    _m("MG2 阶段编号从 0 起（错位一格）", NUMBERED, RB, _ENUM,
       "    named = [(i, p) for i, p in enumerate(phases, 0) if p]"),
    # 作者补的第三向：无阶段分支的口径里没有「没有阶段时填 0」时，那条断言要能分辨。
    _m("MG3 口径删掉「没有阶段时填 0」", NO_PHASE, RB, _RULE4,
       '    "4. phase 填阶段序号（整数 1..n）。\\n"'),
    # 补充（审计后）：规则第 4 条的「1..n」本身要被钉住，改成 0..n-1 必须红。
    _m("MG4 规则第 4 条写成「整数 0..n-1」", RULE4, RB, _RULE4,
       '    "4. phase 填阶段序号（整数 0..n-1）；没有阶段时填 0。\\n"'),
    # 补充（审计后）：`card_draft` 的阶段号类型兜底。坏值必须落到规则 0，不许炸整卡。
    _m("MC1 去掉兜底（非数字重抛）", BAD_OCC, DRAFT, _FALLBACK, "    return int(value)"),
    _m("MC2 兜底返回 1 而不是 0（坏值被误挂阶段 1）", BAD_OCC, DRAFT, _FALLBACK,
       '    logger.warning("[card_draft] 阶段编号不是整数，按 0 处理：%r", value)\n    return 1'),
    _m("MC3 只 DraftAttitude 用共用类型、DraftOccurrence 没用", BAD_OCC, DRAFT,
       '    phase: PhaseNumber\n    quote: str = ""', '    phase: int\n    quote: str = ""'),
    _m("MC4 draft_schema 里 phase 变 string", SCHEMA, DRAFT,
       '    phase: PhaseNumber\n    attitude: str = ""', '    phase: str\n    attitude: str = ""'),
    # 二次审计（补充）：布尔 / 非整数小数两条坏值方向，之前没有判别器（删各自的判断测试仍全绿）。
    _m("MB1 去掉布尔判断（True 被当成整数 1）", BOOL_BAD, DRAFT,
       "    if not isinstance(value, bool):", "    if True:  # 变异：布尔当整数"),
    _m("MB2 去掉 is_integer 判断（2.5 被截成 2）", FRAC_BAD, DRAFT,
       "            if not isinstance(value, float) or value.is_integer():",
       "            if True:  # 变异：小数截断"),
]

if __name__ == "__main__":
    sys.exit(framework.run_oneoff(MUTANTS, targets=TARGETS))
