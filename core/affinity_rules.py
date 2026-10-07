"""好感规则表：一轮评估发生了哪类事，好感和关系状态怎么变。

纯函数，无 IO，不认识 prompt、落库、引擎。评估模型只报「事件、档位、档内整数」
（协议见 `core/affinity_protocol.py`），数值怎么变全在这里，别处不再写一份。
出处：`docs/specs/personality-inject.md` §2（R1–R18）。

结构：先把事件规整（R5、R8 的降级）→ 算出这类事该变多少（R2–R7）→ 取所有上限里最低的
一条（R4、R8、R10、R18）→ 更新状态。门槛（R10）只管第一次进亲近档：到过的关系不再受它限制。加一条上限规则 = 在 `_CEILINGS` 里加一个函数。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable

CLOSE_FROM = 73         # 亲近档下界：只此一处，`affinity_service.AFFINITY_STAGES` 引用它
MET_REQUIRED = 3        # 跨进亲近档前要累计的 `met_condition` 次数（R10）
LARGE_NEEDS_STREAK = 2  # 正向大档要求此前连续几轮非负（R6）
LEGACY_UP_MAX = 5       # 用默认亲近条件的卡，单轮最多涨这么多（R18）

TIERS: dict[str, tuple[int, int]] = {"small": (1, 2), "medium": (3, 5), "large": (6, 8)}   # R2
MAX_DROP = max(hi for _, hi in TIERS.values())   # 单轮最多能掉多少（疏远检测的「急降」用它）
POSITIVE = frozenset({"met_condition", "friendly", "repair"})
NEGATIVE = frozenset({"offended", "trigger"})
EVENTS = POSITIVE | NEGATIVE | {"neutral"}

# 卡上没有亲近条件时用的两条（R13）。只此一处。
DEFAULT_WARMING = (
    "对方向你说了自己的心事（感受，不是寒暄）",
    "对方认真回应了你说的事（听懂、认可、在乎）",
)


def warming_conditions(psyche) -> list[str]:
    """这张卡生效的亲近条件：卡上有就用卡上的，没有才用默认两条。"""
    return list(psyche.warming_conditions or DEFAULT_WARMING)


@dataclass(frozen=True)
class RelationState:
    """好感数字之外、规则表要记的状态（一个存档一份）。"""
    last_event: str = ""            # 上一轮生效的事件类别
    met_count: int = 0              # 本存档累计 `met_condition` 次数
    nonneg_streak: int = 0          # 连续非负轮数
    pre_offence: int | None = None  # 冒犯前的值；非空 = 有待修复的冒犯
    reached_close: bool = False     # 到过亲近档（含起点就在亲近档）；到过就不再受门槛限制（R10）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "RelationState":
        """从落库的 dict 还原；缺键、类型不对的键取默认值（旧存档没有这份状态）。"""
        if not isinstance(data, dict):
            return cls()
        base = cls()
        pick = lambda key, ok: data[key] if ok(data.get(key)) else getattr(base, key)
        is_int = lambda v: type(v) is int
        is_event = lambda v: isinstance(v, str) and v in EVENTS
        is_bool = lambda v: type(v) is bool
        return cls(pick("last_event", is_event), pick("met_count", is_int),
                   pick("nonneg_streak", is_int), pick("pre_offence", is_int),
                   pick("reached_close", is_bool))


def relational_tier(affinity: int, state: RelationState) -> str:
    """三档关系做法取哪一档（R14）：上一轮冲突 → conflict（优先）；≥73 → close；其余 normal。"""
    if state.last_event in NEGATIVE:
        return "conflict"
    return "close" if affinity >= CLOSE_FROM else "normal"


# ── 第一步：规整事件 ────────────────────────────────────────────────────────────

def _validate(event, tier, delta) -> None:
    if event not in EVENTS:
        raise ValueError(f"未知的 affinity_event：{event!r}")
    if event != "neutral" and (tier not in TIERS or type(delta) is not int):
        raise ValueError(f"档位或整数不合法：tier={tier!r} delta={delta!r}")


def _settle(event, tier, state: RelationState, psyche, met_index) -> tuple[str, str, list[str]]:
    """把模型报的事件规整成实际生效的（事件，档位）。"""
    warnings: list[str] = []
    if event == "met_condition":
        n = len(warming_conditions(psyche))
        if type(met_index) is not int or not 0 <= met_index < n:          # R5
            warnings.append(f"met_condition_index={met_index!r} 不在 0..{n - 1}，按 friendly 算")
            event = "friendly"
    if event == "repair" and state.pre_offence is None:                   # R8：没有待修复的冒犯
        event = "friendly"
    if event == "friendly":                                               # R4：一律小档
        tier = "small"
    elif event == "repair" and tier == "large":                           # R8：大档只留给 met_condition
        tier = "medium"
    elif event == "met_condition" and tier == "large" and state.nonneg_streak < LARGE_NEEDS_STREAK:
        tier = "medium"                                                   # R6
    return event, tier, warnings


def _change(event: str, tier: str, delta) -> int:
    """这类事按档该变多少（带符号）；模型给的整数只取大小，截到该档区间（R2）。"""
    if event == "neutral":
        return 0                                                          # R3
    lo, hi = TIERS[tier]
    size = max(lo, min(hi, abs(delta)))
    return -size if event in NEGATIVE else size


# ── 第二步：上限。每个函数返回「这一轮好感最高能到多少」，不适用就返回 None ──────────

Ceiling = Callable[[int, str, RelationState, object], "int | None"]


def _baseline_line(affinity, event, state, psyche):          # R4：客气最多到基线
    return max(affinity, psyche.affinity_baseline) if event == "friendly" else None


def _repair_limit(affinity, event, state, psyche):           # R8：修复最多回到冒犯前
    return state.pre_offence if event == "repair" else None


def _legacy_step(affinity, event, state, psyche):            # R18：默认条件卡保留改造前的单轮上限
    return None if psyche.warming_conditions else affinity + LEGACY_UP_MAX


def _close_gate(affinity, event, state, psyche):             # R10：第一次进亲近档要挣够次数
    blocked = affinity < CLOSE_FROM and not state.reached_close and state.met_count < MET_REQUIRED
    return CLOSE_FROM - 1 if blocked else None


_CEILINGS: tuple[Ceiling, ...] = (_baseline_line, _repair_limit, _legacy_step, _close_gate)


# ── 入口 ───────────────────────────────────────────────────────────────────────

def apply_event(affinity: int, state: RelationState, psyche, *, event, tier, delta,
                met_index=None) -> tuple[int, RelationState, list[str]]:
    """一轮评估 → （新好感，新状态，warning 列表）。输入不合法抛 ValueError，调用方保持原值（R16）。"""
    _validate(event, tier, delta)
    event, tier, warnings = _settle(event, tier, state, psyche, met_index)

    negative = event in NEGATIVE
    state = replace(
        state,
        reached_close=state.reached_close or affinity >= CLOSE_FROM,   # R10：回合开始好感 ≥73 即记为到过；跨进那轮的下轮开头补记
        last_event=event,
        met_count=state.met_count + (event == "met_condition"),
        nonneg_streak=0 if negative else state.nonneg_streak + 1,
        pre_offence=affinity if negative and state.pre_offence is None else state.pre_offence,  # R7
    )
    new = affinity + _change(event, tier, delta)
    if new > affinity:
        bounds = [b for b in (c(affinity, event, state, psyche) for c in _CEILINGS) if b is not None]
        new = max(affinity, min([new, *bounds]))
    new = max(0, min(100, new))
    if state.pre_offence is not None and not negative and new >= state.pre_offence:
        state = replace(state, pre_offence=None)                          # R9：补回来了，冒犯了结
    return new, state, warnings
