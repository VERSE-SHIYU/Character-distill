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
from core.fingerprint import content_fingerprint
from core.schema import (
    FORMAT_GROUPS,
    ArcAxis,
    BehaviorCore,
    BoundaryExample,
    CharacterCard,
    PhaseState,
    Relationship,
)

logger = logging.getLogger(__name__)


class DraftOccurrence(BaseModel):
    """一次出现：这个做法/记忆在某个阶段里做过，附一段该阶段里的原文摘录（10-40 字，逐字照抄）。"""
    phase: int
    quote: str = ""


class DraftBehavior(BehaviorCore):
    """情境→行为：此人遇到某类情境时的具体做法；occurrences 是它在各阶段的原文摘录。"""
    occurrences: list[DraftOccurrence] = []


class DraftMemory(BaseModel):
    """一条关键经历；occurrences 是它在各阶段的原文摘录（全程成立 → 顶层）。"""
    memory: str = ""
    occurrences: list[DraftOccurrence] = []


class DraftAttitude(BaseModel):
    """本角色在某阶段对某人的态度；quote 是这段态度成立的原文摘录。"""
    phase: int
    attitude: str = ""
    quote: str = ""


class DraftRelationship(Relationship):
    """一条关系：`attitudes` 按阶段给态度，转卡时按位置检查分发到 `phase_attitudes`。"""
    attitudes: list[DraftAttitude] = []


class DraftPhase(PhaseState):
    """弧线上的一个阶段；anchor 是标志这一阶段开始的一句原文（阶段 1 可留空）。"""
    anchor: str = ""
    boundary_examples: list[BoundaryExample] = []


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
    key_memories: list[DraftMemory] = []
    relationships: list[DraftRelationship] = []


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


def _first_quote(row) -> str:
    return row.occurrences[0].quote if row.occurrences else ""


def _row_label(item) -> str:
    """编号非法警告里点出是哪一条：做法取 `situation`，记忆取 `memory`，关系取 `target`。"""
    return (getattr(item, "situation", "") or getattr(item, "memory", "")
            or getattr(item, "target", ""))


def _valid_number_rows(items, count: int, kind: str) -> list[list[int]]:
    """规则 0：每条条目的合法阶段编号（升序、去重）。越界/空的编号被撤回并 warning 一条。

    `count == 0`（无阶段的卡）不做检查、不 warning。做法、记忆、关系态度共用这一份。
    """
    valid_rows: list[list[int]] = []
    for item in items:
        nums = [occ.phase for occ in item.occurrences]
        valid = sorted({p for p in nums if 1 <= p <= count})
        if count and (len(valid) != len(set(nums)) or not valid):
            logger.warning("[card_draft] %s的阶段编号不合法（共 %d 个阶段）%s：%s",
                           kind, count, _row_label(item), nums)
        valid_rows.append(valid)
    return valid_rows


def dispatch(final_phases: list[list[int]], count: int) -> tuple[list[int], list[list[int]]]:
    """按最终阶段把条目分发到顶层与各阶段 —— 做法与记忆共用这一份（S8 唯一分发处）。

    无阶段的卡（`count == 0`）或标注覆盖全部阶段 → 顶层；否则挂到最终阶段下；编号全部被
    撤回（`final` 空）→ 不出现（整条撤回）。返回 `(顶层序号, by_phase)`，`by_phase[p-1]`
    是挂到阶段 p 的序号。**`by_phase[` 只在这里出现**，判据 S8 靠它定位唯一分发点。
    """
    top: list[int] = []
    by_phase: list[list[int]] = [[] for _ in range(count)]
    for i, final in enumerate(final_phases):
        if count == 0 or len(final) == count:
            top.append(i)
        elif final:
            for p in final:
                by_phase[p - 1].append(i)
    return top, by_phase


def _convert_relationships(rels, phases, source_text: str, count: int,
                           name: str) -> list[dict]:
    """关系草稿 → 存卡关系：态度复用位置检查与编号过滤，写 `phase_attitudes` 与顶层 `attitude`。

    顶层 `attitude` 取它出现的最后一个（存留下来的）阶段的态度 —— k=n 与前端编辑都读它。
    没有 `attitudes` 的关系（旧卡）原样保留。
    """
    if not rels:
        return []
    # 复用 `DraftMemory` 当载体：位置检查要的只是「带 occurrences 的条目」，target 当 label。
    items = [DraftMemory(
        memory=r.target,
        occurrences=[DraftOccurrence(phase=a.phase, quote=a.quote) for a in r.attitudes],
    ) for r in rels]
    res = phase_anchoring.verify(
        items, _valid_number_rows(items, count, "态度"), phases, source_text,
        kind="态度", label=lambda x: x.memory, name=name)
    out: list[dict] = []
    for i, r in enumerate(rels):
        row = r.model_dump()
        row.pop("attitudes", None)
        if not r.attitudes:
            out.append(row)                       # 旧卡：顶层 attitude 原样
            continue
        att_map = {a.phase: a.attitude for a in r.attitudes}
        final = res.phases[i]
        row["phase_attitudes"] = [
            {"phase": p, "attitude": att_map.get(p, "")} for p in final]
        row["attitude"] = att_map[max(final or att_map)]
        out.append(row)
    return out


def card_from_draft(data: Any, source_text: str) -> CharacterCard:
    """模型输出（草稿）→ 存卡。形态不对抛 `pydantic.ValidationError`，由调用方按各自口径上屏。

    先做阶段编号合法性过滤（越界、空 → 对不上的撤回、打 warning，一条的编号全部作废就整条
    撤回，与 `core.card_quotes.retract_unverified` 对模型输出的口径同）；再让 `phase_anchoring`
    按 `source_text` 核对摘录位置，标错的阶段去掉，全部被去掉的退回原标注（兜底）。做法与记忆
    各走一遍（位置检查口径相同），结果交 `dispatch` 分发：所有阶段都成立 → 顶层，否则挂到成立
    的阶段下；没有阶段的卡，全部放顶层，不做位置检查。关系态度同样过一遍检查。最后写
    `ArcPhase.start`（整卡跳过时不写）与 `CharacterArc.source_fingerprint`。`source_text` 必填。
    """
    draft = CardDraft.model_validate(data)
    phases = draft.character_arc.phases
    count = len(phases)

    b = phase_anchoring.verify(
        draft.situation_behaviors,
        _valid_number_rows(draft.situation_behaviors, count, "做法"),
        phases, source_text, kind="做法", name=draft.name)
    m = phase_anchoring.verify(
        draft.key_memories,
        _valid_number_rows(draft.key_memories, count, "记忆"),
        phases, source_text, kind="记忆", name=draft.name,
        warn_skip=not draft.situation_behaviors)   # 卡的跳过警告由第一类有内容的条目打一次
    logger.info("[phase_anchoring] card=%s tags=%d dropped=%d ambiguous=%d "
                "unverified_quotes=%d fallback=%d skipped_card=%s memories_dropped=%d",
                draft.name, b.tags, b.dropped, b.ambiguous,
                b.unverified_quotes, b.fallback, b.skipped_card, m.dropped)

    b_top, b_slots = dispatch(b.phases, count)
    m_top, m_slots = dispatch(m.phases, count)

    card = draft.model_dump()
    card["situation_behaviors"] = [
        {"situation": draft.situation_behaviors[i].situation,
         "behavior": draft.situation_behaviors[i].behavior,
         "source_quote": _first_quote(draft.situation_behaviors[i])}
        for i in b_top
    ]
    card["key_memories"] = [draft.key_memories[i].memory for i in m_top]
    card["relationships"] = _convert_relationships(
        draft.relationships, phases, source_text, count, draft.name)

    for idx, phase in enumerate(card["character_arc"]["phases"]):
        phase["behaviors"] = [
            {"situation": draft.situation_behaviors[i].situation,
             "behavior": draft.situation_behaviors[i].behavior,
             "source_quote": b.phase_quotes[i].get(idx + 1, "")}
            for i in b_slots[idx]
        ]
        phase["memories"] = [draft.key_memories[i].memory for i in m_slots[idx]]
    if b.starts is not None:                      # 整卡跳过位置检查 → 不写起点（D7）
        for phase, start in zip(card["character_arc"]["phases"], b.starts):
            phase["start"] = start
    card["character_arc"]["source_fingerprint"] = content_fingerprint(source_text)
    return CharacterCard.model_validate(card)
