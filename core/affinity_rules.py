"""好感规则表（③b-1）：评估模型只报「发生了哪类事、哪一档、档内的整数」，好感怎么变由这里定。

纯函数，无 IO、不读卡片以外的东西。出处见 `docs/specs/personality-3a-design.md` v6 §5：
档位区间照 EIBench（arXiv 2606.15532 附录 G，关系刻度同为 0–100）；进亲近档要累计 3 次
「做到亲近条件」（2609.00982 的三步有序门槛；PatientAct 2608.12750）。
"""

from __future__ import annotations

from dataclasses import dataclass

CLOSE_FROM = 73        # 亲近档下界：只此一处，`affinity_service.AFFINITY_STAGES` 引用它
MET_REQUIRED = 3       # 跨进亲近档前要累计的 `met_condition` 次数
LARGE_NEEDS_STREAK = 2  # 正向大档要求此前连续几轮非负

TIERS: dict[str, tuple[int, int]] = {"small": (1, 2), "medium": (3, 5), "large": (6, 8)}
POSITIVE = frozenset({"met_condition", "friendly", "repair"})
NEGATIVE = frozenset({"offended", "trigger"})
EVENTS = POSITIVE | NEGATIVE | {"neutral"}

# 卡上没有亲近条件时用的两条（亲密过程模型：表露 + 被回应）。只此一处。
DEFAULT_WARMING = (
    "对方向你说了自己的心事（感受，不是寒暄）",
    "对方认真回应了你说的事（听懂、认可、在乎）",
)


def warming_conditions(psyche) -> list[str]:
    """这张卡生效的亲近条件：卡上有就用卡上的，没有才用默认两条。"""
    return list(getattr(psyche, "warming_conditions", None) or DEFAULT_WARMING)


@dataclass(frozen=True)
class RelationState:
    """好感数字之外、规则表要记的状态（一个存档一份）。"""
    last_event: str = ""           # 上一轮生效的事件类别
    met_count: int = 0             # 本存档累计 `met_condition` 次数
    nonneg_streak: int = 0         # 连续非负轮数
    pre_offence: int | None = None  # 冒犯前的值；非空 = 有待修复的冒犯


def relational_tier(affinity: int, state: RelationState) -> str:
    """三档关系做法取哪一档：上一轮冲突 → conflict（优先）；≥73 → close；其余 normal。"""
    if state.last_event in NEGATIVE:
        return "conflict"
    return "close" if affinity >= CLOSE_FROM else "normal"


def _magnitude(tier: str, delta: int) -> int:
    lo, hi = TIERS[tier]
    return max(lo, min(hi, abs(delta)))


def apply_event(affinity: int, state: RelationState, psyche, *, event, tier, delta,
                met_index=None) -> tuple[int, RelationState, list[str]]:
    """一轮评估 → （新好感，新状态，warning 列表）。输入不合法抛 ValueError，调用方保持原值。"""
    if event not in EVENTS:
        raise ValueError(f"未知的 affinity_event：{event!r}")
    if event != "neutral" and (tier not in TIERS or type(delta) is not int):
        raise ValueError(f"档位或整数不合法：tier={tier!r} delta={delta!r}")

    warnings: list[str] = []
    if event == "met_condition":
        n = len(warming_conditions(psyche))
        if type(met_index) is not int or not 0 <= met_index < n:
            warnings.append(f"met_condition_index={met_index!r} 不在 0..{n - 1}，按 friendly 算")
            event = "friendly"
    if event == "repair" and state.pre_offence is None:
        event = "friendly"                      # 没有待修复的冒犯

    new, met, pre = affinity, state.met_count, state.pre_offence
    if event == "friendly":
        if affinity < psyche.affinity_baseline:  # 基线这条线：客气最多加到基线
            new = min(psyche.affinity_baseline, affinity + _magnitude("small", delta))
    elif event == "met_condition":
        if tier == "large" and state.nonneg_streak < LARGE_NEEDS_STREAK:
            tier = "medium"
        new, met = affinity + _magnitude(tier, delta), met + 1
    elif event == "repair":
        new = min(pre, affinity + _magnitude("medium" if tier == "large" else tier, delta))
    elif event in NEGATIVE:
        pre = affinity if pre is None else pre
        new = affinity - _magnitude(tier, delta)

    if affinity < CLOSE_FROM and met < MET_REQUIRED:   # 没挣够次数，不能跨进亲近档
        new = min(new, CLOSE_FROM - 1)
    new = max(0, min(100, new))
    if pre is not None and event not in NEGATIVE and new >= pre:
        pre = None                                     # 补回来了，冒犯了结
    streak = 0 if event in NEGATIVE else state.nonneg_streak + 1
    return new, RelationState(event, met, streak, pre), warnings
