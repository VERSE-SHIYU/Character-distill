"""模型输出契约：蒸馏时模型写的「草稿」，以及草稿 → 存卡的唯一出口。

为什么不让模型直接写存卡结构：存卡里做法分两处放（阶段下 = 只在那个阶段成立，顶层 =
从头到尾都成立），要模型判断「从头到尾都成立」是一道概括题，它会两边都写。草稿里每条
做法只写一次，并给每个阶段标一段**该阶段里**的原文摘录（照原文回答的事实题）；这条摘录的
位置由 `core.phase_anchoring` 按原文核对，标错的阶段去掉，再按最终阶段分发：所有阶段都
成立的放顶层，否则挂到成立的阶段下。

两处唯一出处：
- `draft_schema`：发给模型的 JSON 结构只从这里取 —— 提示词让模型写草稿，附的结构也必须
  是草稿，两者说的是同一件事。
- `card_from_draft`：模型输出变成 `CharacterCard` 只经这里。蒸馏的流式路径一律交出草稿
  JSON，由消费方（`web/routers/distill.py`）调这一处；同步路径同样调这一处。
"""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from core import phase_anchoring
from core.schema import FORMAT_GROUPS, ArcAxis, BehaviorCore, CharacterCard, PhaseState

logger = logging.getLogger(__name__)


class DraftOccurrence(BaseModel):
    """一次出现：这个做法在某个阶段里做过，附一段该阶段里的原文摘录（10-40 字，逐字照抄）。"""
    phase: int
    quote: str = ""


class DraftBehavior(BehaviorCore):
    """情境→行为：此人遇到某类情境时的具体做法；occurrences 是它在各阶段的原文摘录。"""
    occurrences: list[DraftOccurrence] = []


class DraftPhase(PhaseState):
    """弧线上的一个阶段；anchor 是标志这一阶段开始的一句原文（阶段 1 可留空）。"""
    anchor: str = ""


class DraftArc(ArcAxis):
    """角色弧线：一条变化轴 + 按故事顺序排列的阶段；做法不写在阶段下，写在 situation_behaviors。"""
    phases: list[DraftPhase] = []


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


def _first_quote(row: DraftBehavior) -> str:
    return row.occurrences[0].quote if row.occurrences else ""


def card_from_draft(data: Any, source_text: str) -> CharacterCard:
    """模型输出（草稿）→ 存卡。形态不对抛 `pydantic.ValidationError`，由调用方按各自口径上屏。

    先做阶段编号合法性过滤（越界、空 → 对不上的撤回、打 warning，一条做法的编号全部作废就
    整条撤回，与 `core.card_quotes.retract_unverified` 对模型输出的口径同）—— 现有行为不变；
    再让 `phase_anchoring` 按 `source_text` 核对摘录位置，标错的阶段去掉，全部被去掉的退回原
    标注（兜底）。分发同原规则：所有阶段都成立 → 顶层，否则挂到成立的阶段下。没有阶段的卡，
    所有做法放顶层，不做位置检查。`source_text` 必填 —— 位置检查没有原文就无从谈起。
    """
    draft = CardDraft.model_validate(data)
    count = len(draft.character_arc.phases)

    valid_rows: list[list[int]] = []
    for row in draft.situation_behaviors:
        nums = [occ.phase for occ in row.occurrences]
        valid = sorted({p for p in nums if 1 <= p <= count})
        if count and (len(valid) != len(set(nums)) or not valid):
            logger.warning("[card_draft] 做法的阶段编号不合法（共 %d 个阶段）%s：%s",
                           count, nums, row.situation)
        valid_rows.append(valid)

    result = phase_anchoring.verify(draft, source_text, valid_rows)
    logger.info("[phase_anchoring] card=%s tags=%d dropped=%d ambiguous=%d "
                "unverified_quotes=%d fallback=%d skipped_card=%s",
                draft.name, result.tags, result.dropped, result.ambiguous,
                result.unverified_quotes, result.fallback, result.skipped_card)

    general: list[dict] = []
    by_phase: list[list[dict]] = [[] for _ in range(count)]

    for i, row in enumerate(draft.situation_behaviors):
        base = {"situation": row.situation, "behavior": row.behavior}
        if count == 0:
            general.append({**base, "source_quote": _first_quote(row)})
            continue
        final = result.phases[i]
        if not final:
            continue                             # 编号全部作废 → 整条撤回
        if len(final) == count:
            general.append({**base, "source_quote": _first_quote(row)})
        else:
            for p in final:
                by_phase[p - 1].append(
                    {**base, "source_quote": result.phase_quotes[i].get(p, "")})

    card = draft.model_dump()
    card["situation_behaviors"] = general
    for phase, behaviors in zip(card["character_arc"]["phases"], by_phase):
        phase["behaviors"] = behaviors
    return CharacterCard.model_validate(card)
