"""spec `state-inertia.md` 的变异对账表：状态类「沿用最近一次」被打破必须红。一次性产物。

执行框架与判档不在本文件里：改文件、跑 pytest、按字节还原、基线门都用
`tests/perf/mutation_framework.py` 的 `run_oneoff`（与 `personality_inject_mutations.py`、
`fix_rel_batch_phase_numbering_mutations.py` 同一处置）。

靶子 = 目标检查 `tests/test_state_inertia_goal.py` + `tests/test_arc_view.py`
（spec §6 的 N1–N7；N1/N5/N7 各多钉一条，见 spec §6 表第二列）。

用法：worktree 根目录 `<主仓>/.venv/Scripts/python.exe docs/specs/artifacts/state_inertia_mutations.py`
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

ARC = ROOT / "core" / "arc_view.py"

GOAL = "tests/test_state_inertia_goal.py"
I1 = f"{GOAL}::test_i1_last_phase_inherits_earlier_values"
I2 = f"{GOAL}::test_i2_own_value_replaces_earlier_not_merged"
I3 = f"{GOAL}::test_i3_never_reads_later_phases"
I4 = f"{GOAL}::test_i4_lifelong_entries_stay_after_phase_entries"
I5 = f"{GOAL}::test_i5_default_phase_chat_uses_the_cards_own_relational_mode"
I6 = f"{GOAL}::test_i6_scalar_value_with_evidence_at_k_beats_inherited"
U18 = "tests/test_arc_view.py::test_u18_dialogues_phase_k_first_then_top"

# 变异点（`core/arc_view.py` 里的字面量；每个 `old` 只此一处出现）。
_INH_LIST = "list(inherited(per_phase, k) or [])"          # 状态类列表：沿用最近一次
_INH_SCALAR = "kth = inherited(per_phase, k)"              # 状态类单值：沿用最近一次
_SCALAR_GUARD = "elif own or not get_path(proj, path):"    # 单值的「k 上有证据优先」门
_INH_HEAD = "for v in reversed(values[:max(k, 0)]):"       # inherited 的「≤k」上界


def _m(label: str, target: str, pairs):
    return (label, target, [("repl", ARC, pairs)], "RED")


MUTANTS = [
    # N1 退回只看阶段 k（列表、单值两处都用自己那格，不沿用）。红 I1、I5。
    _m("N1 退回只看阶段 k · I1", I1,
       [(_INH_LIST, "list(own or [])"), (_INH_SCALAR, "kth = own")]),
    _m("N1 退回只看阶段 k · I5", I5,
       [(_INH_LIST, "list(own or [])"), (_INH_SCALAR, "kth = own")]),
    # N2 列表拼成 ≤k 全部并集（不再整格替换）。红 I2。
    _m("N2 列表并集（不整格替换） · I2", I2,
       [(_INH_LIST, "[x for p in per_phase[:k] for x in (p or [])]")]),
    # N3 inherited 扫全部阶段（看不该看的 k 之后）。红 I3。
    _m("N3 沿用扫全部阶段 · I3", I3,
       [(_INH_HEAD, "for v in reversed(values):")]),
    # N4 单值去掉「k 上有证据优先」门（沿用盖过全程）。红 I6。
    _m("N4 沿用盖过全程 · I6", I6,
       [(_SCALAR_GUARD, "else:")]),
    # N5 列表顺序反过来（全程在前）。红 I4 与 arc_view 的顺序契约 u18。
    _m("N5 全程放前面 · I4", I4,
       [(_INH_LIST + " + base", "base + " + _INH_LIST)]),
    _m("N5 全程放前面 · u18", U18,
       [(_INH_LIST + " + base", "base + " + _INH_LIST)]),
    # N6 列表沿用 k-1（忽略自己那格）。红 I2。
    _m("N6 沿用忽略自己那格 · I2", I2,
       [(_INH_LIST, "list(inherited(per_phase, k - 1) or [])")]),
    # N7 单值不沿用（只有自己那格有值才写）。红 I1、I5。
    _m("N7 单值不沿用 · I1", I1,
       [(_SCALAR_GUARD, "elif own:")]),
    _m("N7 单值不沿用 · I5", I5,
       [(_SCALAR_GUARD, "elif own:")]),
]

if __name__ == "__main__":
    sys.exit(framework.run_oneoff(MUTANTS, targets=(ARC,)))
