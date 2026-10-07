# -*- coding: utf-8 -*-
"""未定位区的条目挪进某个阶段 —— 纯计算，无 IO（spec `arc-phase-unlocated.md` §3.6）。

未定位区装的是摘录定不了位的状态类条目（做法、字段值、关系态度），只展示、不进 prompt。
用户在卡片上把一条挪进阶段 k，就是给了依据：条目从未定位区移出、写进阶段 k。

**字段是列表还是单值只认登记表**（`core.card_layers.REGISTRY` 的 `kind`），前端不另存一份。
单值字段（决策方式、语气……）挪进已有值的阶段时**交换**：原值退回未定位区，不静默丢弃（D4）。
关系态度同理：阶段 k 已有态度就交换；挪进来之前先去掉「态度留空」的占位（它是「一条态度都
没定位」时挂在最后阶段的，§3.3）—— 不去掉的话，投影取 ≤k 最新一条时会被这条空态度盖住。
"""
from __future__ import annotations

import pydash

from core.card_layers import REGISTRY, get_path, set_path
from core.schema import CharacterCard, is_placeholder_attitude, top_attitude

SECTIONS = ("behaviors", "overlay", "attitudes")


class UnknownPhase(ValueError):
    """目标阶段号不在 1..n —— 与其他参数错分开，路由给它单独的文案（spec 补充 17）。"""


def _take(items: list, index: int, what: str):
    if not 0 <= index < len(items):
        raise ValueError(f"未定位区没有这一条{what}：{index}")
    return items.pop(index)


def move_unlocated(card: CharacterCard, *, section: str, index: int, phase: int,
                   path: str = "") -> CharacterCard:
    """把未定位区 `section` 的第 `index` 条挪进阶段 `phase`（1 起），返回新卡（原卡不改）。

    `section="overlay"` 时 `path` 是登记表里的 state 路径，`index` 是该路径列表里的序号。
    序号漂移（另一个标签页先挪过、编辑过）不在这里判：路由先按卡的版本核对（§13），卡变过
    即 409，传进来的卡就是调用方看到的那一版，序号必然对得上。
    参数不合法抛 `ValueError`（路由转 400）；其中阶段号越界抛它的子类 `UnknownPhase`。
    """
    if section not in SECTIONS:
        raise ValueError(f"未知的未定位分区：{section}")
    n = len(card.character_arc.phases)
    if not 1 <= phase <= n:
        raise UnknownPhase(f"阶段号越界：{phase}（共 {n} 个阶段）")
    data = card.model_dump()
    loose = data["character_arc"]["unlocated"]
    target = data["character_arc"]["phases"][phase - 1]

    if section == "behaviors":
        target["behaviors"].append(_take(loose["behaviors"], index, "做法"))
    elif section == "overlay":
        spec = REGISTRY.get(path)
        if spec is None or spec.layer != "state":
            raise ValueError(f"不是可挪动的状态字段：{path}")
        vals = list(get_path(loose["overlay"], path) or [])
        value = _take(vals, index, "字段值")
        current = get_path(target["overlay"], path)
        if spec.kind == "list":
            set_path(target["overlay"], path, list(current or []) + [value])
        else:
            if current:                              # 交换：原值退回未定位区（D4）
                vals.append(current)
            set_path(target["overlay"], path, value)
        if vals:
            set_path(loose["overlay"], path, vals)
        else:
            pydash.unset(loose["overlay"], path)
    else:
        item = _take(loose["attitudes"], index, "态度")
        rel = next((r for r in data["relationships"] if r["target"] == item["target"]), None)
        if rel is None:
            raise ValueError(f"关系已不存在：{item['target']}")
        kept = [pa for pa in rel["phase_attitudes"] if not is_placeholder_attitude(pa)]
        same = next((pa for pa in kept if pa["phase"] == phase), None)
        if same is not None:                         # 交换：原态度退回未定位区（D4）
            loose["attitudes"].append({"target": rel["target"], "attitude": same["attitude"],
                                       "note": same["note"], "phase": phase})
            kept.remove(same)
        kept.append({"phase": phase, "attitude": item["attitude"], "note": item["note"]})
        rel["phase_attitudes"] = sorted(kept, key=lambda pa: pa["phase"])
        rel["attitude"] = top_attitude(rel["phase_attitudes"])
    return CharacterCard.model_validate(data)
