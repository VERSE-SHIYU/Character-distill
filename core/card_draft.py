"""模型输出契约：蒸馏时模型写的「草稿」，以及草稿 → 存卡的唯一出口。

为什么不让模型直接写存卡结构：存卡里做法分两处放（阶段下 = 只在那个阶段成立，顶层 =
从头到尾都成立），要模型判断「从头到尾都成立」是一道概括题，它会两边都写。草稿里每条
做法只写一次，并给每个阶段标一段**该阶段里**的原文摘录（照原文回答的事实题）；这条摘录的
位置由 `core.phase_anchoring` 按原文核对，标错的阶段去掉，再按最终阶段分发：所有阶段都
成立的放顶层，否则挂到成立的阶段下。状态/经历类字段同样每条只写一次（`DraftTimed`），
形态由 `core.card_layers.REGISTRY` 统一派生（`_derive_draft_model`），不逐字段手写。

两处唯一出处：
- `draft_schema`：发给模型的 JSON 结构只从这里取 —— 提示词让模型写草稿，附的结构也必须
  是草稿，两者说的是同一件事。
- `card_from_draft`：模型输出变成 `CharacterCard` 只经这里。蒸馏的流式路径一律交出草稿
  JSON，由消费方（`web/routers/distill.py`）调这一处；同步路径同样调这一处。
"""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from core import phase_anchoring
from core.card_layers import REGISTRY, get_path, set_path
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


class DraftTimed(BaseModel):
    """状态/经历类字段在草稿里的形态：一条取值 + 它在**哪些阶段**成立的原文摘录。

    每条取值只写一次，由 `card_from_draft` 按登记表的类别分发：覆盖全部阶段 → 卡片顶层，
    否则挂到标了的阶段（经历类只挂最早阶段）。形态由登记表统一派生（`_derive_draft_model`），
    不逐字段手写。
    """
    value: str = ""
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
    """一条关系：`attitudes` 按阶段给态度，转卡时按位置检查分发到 `phase_attitudes`。

    `relation` 在草稿里可以有默认值：主调用（维度 F）只出对象名单（只有 target），
    关系详情由 `_relationships_batched` 按批补齐后再转卡。
    """
    relation: str = ""
    attitudes: list[DraftAttitude] = []


class DraftPhase(PhaseState):
    """弧线上的一个阶段；anchor 是标志这一阶段开始的一句原文（阶段 1 可留空）。"""
    anchor: str = ""
    boundary_examples: list[BoundaryExample] = []


class DraftArc(ArcAxis):
    """角色弧线：一条变化轴 + 按故事顺序排列的阶段；做法不写在阶段下，写在 situation_behaviors。"""
    phases: list[DraftPhase] = []


# 做法与记忆各自的草稿类已经带 occurrences（`DraftBehavior` / `DraftMemory`），不参与派生。
_DEDICATED = {"situation_behaviors", "key_memories"}

# 由 `list[DraftTimed]` 统一转换的状态/经历类字段（分发规则按各自的 layer 走）。
_GENERIC_PATHS = [(p, s) for p, s in REGISTRY.items()
                  if s.layer in ("state", "experience") and p not in _DEDICATED]


def _derive_draft_model(base: type[BaseModel], prefix: str = "", *,
                        name: str | None = None) -> type[BaseModel]:
    """按登记表派生草稿形态：state / experience 叶子换成 `list[DraftTimed]`，嵌套子模型递归。

    只有真改了字段的层才新建模型（没改的原样返回）—— `$defs` 里只多出必要的几个。
    """
    overrides: dict[str, Any] = {}
    for fname, f in base.model_fields.items():
        path = f"{prefix}{fname}"
        spec = REGISTRY.get(path)
        if spec is not None and spec.layer in ("state", "experience") and path not in _DEDICATED:
            overrides[fname] = (list[DraftTimed], Field(default_factory=list))
            continue
        ann = f.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel) and ann is not BaseModel:
            nested = _derive_draft_model(ann, f"{path}.")
            if nested is not ann:
                overrides[fname] = (nested, Field(default_factory=nested))
    if not overrides:
        return base
    return create_model(name or f"Draft{base.__name__}", __base__=base,
                        __module__=base.__module__, __doc__=base.__doc__,
                        **overrides)


# docstring 会进发给模型的 JSON 结构，只写给模型看的话；与存卡的差别见模块说明。
class CardDraftBase(CharacterCard):
    """角色卡——蒸馏引擎的唯一输出格式"""
    # 标题沿用存卡的名字：它会进发给模型的 JSON 结构，模型看到的仍是「角色卡」。
    model_config = ConfigDict(title="CharacterCard")

    character_arc: DraftArc = DraftArc()
    situation_behaviors: list[DraftBehavior] = []
    key_memories: list[DraftMemory] = []
    relationships: list[DraftRelationship] = []


# 状态/经历类字段的草稿形态由登记表派生（§4.2），不逐字段手写。
CardDraft = _derive_draft_model(CardDraftBase, name="CardDraft")


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
    """编号非法警告里点出是哪一条：做法取 `situation`，记忆取 `memory`，关系取 `target`，
    状态/经历类字段的取值取 `value`。"""
    return (getattr(item, "situation", "") or getattr(item, "memory", "")
            or getattr(item, "target", "") or getattr(item, "value", ""))


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


def _layer_phases(final_phases: list[list[int]], count: int, layer: str) -> list[list[int]]:
    """按类别把每条条目的最终阶段折成「要挂的阶段」：``experience`` 只留最早阶段。

    ①把同一条经历挂到每个标了的阶段，投影 1..k 时就重复了（C14）—— 只挂最早阶段即不重复。
    覆盖全部阶段的那条原样保留：顶层判据 `len(final) == count` 还要用它。
    """
    if layer == "experience":
        return [f if (count and len(f) == count) else f[:1] for f in final_phases]
    return final_phases


def dispatch(final_phases: list[list[int]], count: int, *,
             layer: str = "state") -> tuple[list[int], list[list[int]]]:
    """按最终阶段把条目分发到顶层与各阶段 —— 分发只此一处（S6）。

    `layer` 决定「挂到哪些阶段」：``state``（默认，做法走这条）→ 每个最终阶段都挂；
    ``experience``（记忆）→ 只挂最早阶段（`_layer_phases`）。共同规则：无阶段的卡
    （`count == 0`）或标注覆盖全部阶段 → 顶层；编号全部被撤回（`final` 空）→ 整条不出现。
    返回 `(顶层序号, by_phase)`，`by_phase[p-1]` 是挂到阶段 p 的序号，**`by_phase[` 只在这里出现**。
    """
    final_phases = _layer_phases(final_phases, count, layer)
    top: list[int] = []
    by_phase: list[list[int]] = [[] for _ in range(count)]
    for i, final in enumerate(final_phases):
        if count == 0 or len(final) == count:
            top.append(i)
        elif final:
            for p in final:
                by_phase[p - 1].append(i)
    return top, by_phase


def dispatch_single(final_phases: list[list[int]], count: int, *,
                    layer: str, label: str) -> list[int | None]:
    """单值状态字段的分发：每条条目只落**一个**阶段（同阶段多条 → 保留第一条 + warning）。

    返回与条目同序的阶段号；`None` = 不落阶段（覆盖全部 → 顶层；无合法编号 → 丢弃）。
    与 `dispatch` 同源（`_layer_phases` + 「覆盖全部 → 顶层」），只是单值字段一个阶段存不下两条。
    """
    out: list[int | None] = []
    taken: set[int] = set()
    for final in _layer_phases(final_phases, count, layer):
        if count and len(final) == count:
            out.append(None)                      # 覆盖全部 → 顶层
            continue
        one = final[:1]
        if not one:
            out.append(None)
            continue
        p = one[0]
        if p in taken:
            logger.warning("[card_draft] %s 的阶段 %d 有多条取值，保留第一条", label, p)
            out.append(None)
            continue
        taken.add(p)
        out.append(p)
    return out


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
    各走一遍（位置检查口径相同），结果交 `dispatch` 分发：做法按状态类挂到每个成立阶段，记忆按
    经历类只挂最早阶段；所有阶段都成立 → 顶层，没有阶段的卡全部放顶层、不做位置检查。关系态度
    同样过一遍检查。最后写
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
    m_top, m_slots = dispatch(m.phases, count, layer="experience")

    card = draft.model_dump()
    overlays: dict[int, dict[str, Any]] = {}
    for path, spec in _GENERIC_PATHS:
        rows = get_path(draft, path)
        res = phase_anchoring.verify(
            rows, _valid_number_rows(rows, count, path), phases, source_text,
            kind=path, label=lambda x: x.value, name=draft.name, warn_skip=False)
        if spec.kind == "list":
            top, slots = dispatch(res.phases, count, layer=spec.layer)
            set_path(card, path, [rows[i].value for i in top])
            for idx, slot in enumerate(slots):
                vals = [rows[i].value for i in slot]
                if vals:
                    overlays.setdefault(idx, {})[path] = vals
        else:
            placed = dispatch_single(res.phases, count, layer=spec.layer, label=path)
            top_val = rows[0].value if (count == 0 and rows) else next(
                (rows[i].value for i, p in enumerate(placed)
                 if p is None and res.phases[i]), "")
            set_path(card, path, top_val)
            for i, p in enumerate(placed):
                if p is not None:
                    overlays.setdefault(p - 1, {})[path] = rows[i].value

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
        memories = [draft.key_memories[i].memory for i in m_slots[idx]]
        if memories:
            overlays.setdefault(idx, {})["key_memories"] = memories
        if idx in overlays:
            phase["overlay"] = overlays[idx]
    if b.starts is not None:                      # 整卡跳过位置检查 → 不写起点（D7）
        for phase, start in zip(card["character_arc"]["phases"], b.starts):
            phase["start"] = start
    card["character_arc"]["source_fingerprint"] = content_fingerprint(source_text)
    return CharacterCard.model_validate(card)
