"""模型输出契约：蒸馏时模型写的「草稿」，以及草稿 → 存卡的唯一出口。

为什么不让模型直接写存卡结构：存卡里做法分两处放（阶段下 = 只在那个阶段成立，顶层 =
从头到尾都成立），要模型判断「从头到尾都成立」是一道概括题，它会两边都写。草稿里每条
做法只写一次，并标出它出现在哪几个阶段（照原文回答的事实题）；归到哪里由
`card_from_draft` 按标注算：所有阶段都标了的放顶层，否则挂到标了的阶段下。

两处唯一出处：
- `draft_schema`：发给模型的 JSON 结构只从这里取 —— 提示词让模型写草稿，附的结构也必须
  是草稿，两者说的是同一件事。
- `card_from_draft`：模型输出变成 `CharacterCard` 只经这里。蒸馏的流式路径一律交出草稿
  JSON，由消费方（`web/routers/distill.py`）调这一处；同步路径同样调这一处。
"""

import logging
from typing import Any

from pydantic import ConfigDict

from core.schema import FORMAT_GROUPS, ArcAxis, CharacterCard, PhaseState, SituationBehavior

logger = logging.getLogger(__name__)


class DraftBehavior(SituationBehavior):
    """情境→行为：此人遇到某类情境时的具体做法；phases 是这个做法出现过的阶段编号（从 1 开始）。"""
    phases: list[int] = []


class DraftArc(ArcAxis):
    """角色弧线：一条变化轴 + 按故事顺序排列的阶段；做法不写在阶段下，写在 situation_behaviors。"""
    phases: list[PhaseState] = []


# docstring 会进发给模型的 JSON 结构，只写给模型看的话；与存卡的差别见模块说明。
class CardDraft(CharacterCard):
    """角色卡——蒸馏引擎的唯一输出格式"""
    # 标题沿用存卡的名字：它会进发给模型的 JSON 结构，模型看到的仍是「角色卡」。
    model_config = ConfigDict(title="CharacterCard")

    character_arc: DraftArc = DraftArc()
    situation_behaviors: list[DraftBehavior] = []


def draft_schema(group: str | None = None) -> dict[str, Any]:
    """发给模型的 JSON 结构。``group=None`` → 整张；取 `FORMAT_GROUPS` 的键 → 该组子集。

    不给分组调用整张卡的 schema：那会让每一组都以为要输出全部字段。``$defs`` 整体带上，
    不按引用裁剪 —— 多带的定义只是几行噪声，裁错一条就是 ``$ref`` 解析不了的硬伤。
    """
    full = CardDraft.model_json_schema()
    if group is None:
        return full
    fields = FORMAT_GROUPS[group]
    return {
        "title": f"CharacterCard[{group}]",
        "type": "object",
        "properties": {k: v for k, v in full["properties"].items() if k in fields},
        "required": [k for k in full.get("required", []) if k in fields],
        "$defs": full.get("$defs", {}),
    }


def card_from_draft(data: Any) -> CharacterCard:
    """模型输出（草稿）→ 存卡。形态不对抛 `pydantic.ValidationError`，由调用方按各自口径上屏。

    阶段编号不合法（越界、空）的处理同 `core.card_quotes.retract_unverified` 对模型输出的
    口径：对不上的撤回、打 warning，卡照常落；一条做法的编号全部作废就整条撤回。
    没有阶段的卡，所有做法放顶层，`phases` 不看。
    """
    draft = CardDraft.model_validate(data)
    count = len(draft.character_arc.phases)
    general: list[dict] = []
    by_phase: list[list[dict]] = [[] for _ in range(count)]

    for row in draft.situation_behaviors:
        # 带着 phases 也无妨：存卡的 SituationBehavior 不收这一项，校验时丢掉。
        behavior = row.model_dump()
        if count == 0:
            general.append(behavior)
            continue
        valid = sorted({p for p in row.phases if 1 <= p <= count})
        if len(valid) != len(set(row.phases)) or not valid:
            logger.warning("[card_draft] 做法的阶段编号不合法（共 %d 个阶段）%s：%s",
                           count, row.phases, row.situation)
        if not valid:
            continue
        if len(valid) == count:
            general.append(behavior)
        else:
            for p in valid:
                by_phase[p - 1].append(behavior)

    card = draft.model_dump()
    card["situation_behaviors"] = general
    for phase, behaviors in zip(card["character_arc"]["phases"], by_phase):
        phase["behaviors"] = behaviors
    return CharacterCard.model_validate(card)
