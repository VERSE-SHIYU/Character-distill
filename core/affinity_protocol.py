"""好感评估协议：问评估模型要什么、怎么读它的回答。

只管「线上格式」：字段名、事件说明、档位说明都在这里，提示词里的数字从规则表的常量生成，
不手写第二份。规则本身在 `core/affinity_rules.py`。
"""

from __future__ import annotations

from core.affinity_rules import EVENTS, TIERS, warming_conditions

FIELD_EVENT, FIELD_TIER, FIELD_DELTA, FIELD_INDEX = (
    "affinity_event", "affinity_tier", "affinity_delta", "met_condition_index")

# 事件类别 → 给评估模型看的说明。键必须正好是规则表的 EVENTS（有单测守）。
EVENT_HELP: dict[str, str] = {
    "met_condition": "做到了下面某一条亲近条件",
    "friendly": "一般友好",
    "neutral": "闲聊（敷衍和「嗯」「哦」这类纯应答也算闲聊，不算一般友好）",
    "offended": "冒犯",
    "trigger": "触到雷点",
    "repair": "道歉、解释或补偿",
}
TIER_HELP: dict[str, str] = {
    "small": "只是点到为止",
    "medium": "切实回应了或明确搞砸了",
    "large": "真正改变了你对 ta 的态度，大幅正向通常要前面几轮一致",
}
assert set(EVENT_HELP) == set(EVENTS) and set(TIER_HELP) == set(TIERS)


def render_rules(psyche) -> str:
    """评估 prompt 里讲「怎么判事件和档位」的一段。"""
    events = "；".join(f"{name}={text}" for name, text in EVENT_HELP.items())
    tiers = "；".join(f"{name}（{lo}–{hi}）{TIER_HELP[name]}" for name, (lo, hi) in TIERS.items())
    conditions = "".join(f"  {i}. {c}\n" for i, c in enumerate(warming_conditions(psyche)))
    return (
        f"好感事件判定规则（用于 {FIELD_EVENT} / {FIELD_TIER} / {FIELD_DELTA}）：\n"
        f"- 只判断对方这一轮做了哪类事，不要自己给好感数值：\n  {events}\n"
        f"- 亲近条件（met_condition 时在 {FIELD_INDEX} 填满足的是第几条，从 0 数）：\n{conditions}"
        f"- 必须先选档，再在该档内给整数：{tiers}\n"
        "- 不要太快接受道歉：一句好话不该带来大幅回升\n\n"
    )


def render_json_fields() -> str:
    """评估 prompt 的 JSON 段里，好感相关的几行。"""
    return (
        f'  "{FIELD_EVENT}": "{"|".join(EVENT_HELP)}",\n'
        f'  "{FIELD_TIER}": "{"|".join(TIERS)}",\n'
        f'  "{FIELD_DELTA}": 该档区间内的整数,\n'
        f'  "{FIELD_INDEX}": 整数或null,\n'
    )


def read_verdict(data: dict) -> dict:
    """从评估模型的 JSON 里取出规则表要的四样（`apply_event` 的关键字参数）。"""
    return {"event": data.get(FIELD_EVENT), "tier": data.get(FIELD_TIER),
            "delta": data.get(FIELD_DELTA), "met_index": data.get(FIELD_INDEX)}
