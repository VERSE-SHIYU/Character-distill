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

import copy
import logging
from collections.abc import Callable
from typing import Annotated, Any, NamedTuple

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    create_model,
    model_validator,
)

from core import phase_anchoring
from core.card_layers import REGISTRY, get_path, set_path
from core.fingerprint import content_fingerprint
from core.retro_regions import RetroRegions
from core.schema import (
    FORMAT_GROUPS,
    ArcAxis,
    BehaviorCore,
    BoundaryExample,
    CharacterCard,
    PhaseState,
    Relationship,
    placeholder_phase_attitudes,
    top_attitude,
)

logger = logging.getLogger(__name__)


def _phase_number(value: Any) -> int:
    """阶段号：能转成整数的照转（``"2"`` → 2）；转不成、或小数非整，打一条 warning 写明原值，按 0。

    0 是「无阶段」哨兵：有阶段的卡里由规则 0（`_valid_number_rows`）当不合法撤回（状态类、经历类
    除外 —— 那里的 0 分别是「全程」「故事开始前」，进顶层）。这样模型写坏的阶段号只连累那一条，
    不再让 pydantic 在整卡校验处把整张卡炸掉。**只此一处**，调用点不写兜底。
    """
    if not isinstance(value, bool):                       # bool 是 int 子类，但阶段号写布尔是坏值
        try:
            n = int(value)
        except (TypeError, ValueError):
            pass
        else:
            if not isinstance(value, float) or value.is_integer():
                return n
    logger.warning("[card_draft] 阶段编号不是整数，按 0 处理：%r", value)
    return 0


# 阶段号的类型只在这里定义；字段本身与「缺省、发给模型的形态」收在 `PhaseTagged` 一处。
PhaseNumber = Annotated[int, BeforeValidator(_phase_number)]


def _phase_stays_required(schema: dict, cls) -> None:
    """发给模型的结构不因默认值而变：phase 仍列在 required、不带 default。"""
    schema.get("properties", {}).get("phase", {}).pop("default", None)
    required = schema.setdefault("required", [])
    if "phase" not in required:
        required.insert(0, "phase")


class PhaseTagged(BaseModel):
    """带阶段号的草稿条目：阶段号的全部规则只在这一处（类型、缺省、发给模型的形态）。

    缺 `phase` 键按 0 处理，交给规则 0（`_valid_number_rows`）撤回那一条（状态类、经历类除外 ——
    那里的 0 分别是「全程」「故事开始前」）；`phase` 仍列在发给模型的结构里、仍是必填，模型看到
    的契约不变。
    """
    model_config = ConfigDict(json_schema_extra=_phase_stays_required)
    phase: PhaseNumber = 0

    @model_validator(mode="before")
    @classmethod
    def _warn_missing_phase(cls, data: Any) -> Any:
        if isinstance(data, dict) and "phase" not in data:
            logger.warning("[card_draft] 缺阶段编号，按 0 处理：%r", data)
        return data


class DraftOccurrence(PhaseTagged):
    """一次出现：这个做法/记忆在某个阶段里做过，附一段该阶段里的原文摘录（10-40 字，逐字照抄）。"""
    quote: str = ""


class DraftBehavior(BehaviorCore):
    """情境→行为：此人遇到某类情境时的具体做法；occurrences 是它在各阶段的原文摘录。"""
    occurrences: list[DraftOccurrence] = []


class DraftTimed(BaseModel):
    """状态/经历类字段在草稿里的形态：一条取值 + 它在**哪些阶段**成立的原文摘录。

    每条取值只写一次，由 `card_from_draft` 按登记表的类别分发：覆盖全部阶段、或只标了 0
    （状态类「全程」/ 经历类「故事开始前」）→ 卡片顶层，否则挂到标了的阶段（经历类只挂最早
    阶段）。形态由登记表统一派生（`_derive_draft_model`），不逐字段手写。
    """
    value: str = ""
    occurrences: list[DraftOccurrence] = []


class DraftMemory(BaseModel):
    """一条关键经历；occurrences 是它在各阶段的原文摘录（全程成立 → 顶层）。"""
    memory: str = ""
    occurrences: list[DraftOccurrence] = []


class DraftAttitude(PhaseTagged):
    """本角色在某阶段对某人的态度；quote 是这段态度成立的原文摘录。

    `note` 是这一阶段的单向口径（喂给聊天模型的固定立场），随阶段变；空则回落关系顶层 note。
    """
    attitude: str = ""
    note: str = ""
    quote: str = ""


class DraftRelationship(Relationship):
    """一条关系：`attitudes` 按阶段给态度，转卡时按位置检查分发到 `phase_attitudes`。

    `relation` 在草稿里可以有默认值：主调用（维度 F）只出对象名单（只有 target），
    关系详情由 `_relationships_batched` 按批补齐后再转卡。
    """
    relation: str = ""
    attitudes: list[DraftAttitude] = []


class DraftPhase(PhaseState):
    """弧线上的一个阶段；起点是 anchor（一句原文）或 start_chapter（章号）其一，发给模型的结构
    只露其中一个（`draft_schema` 的 ``start_by``），读草稿时按 `phase_anchoring.start_of` 判断。"""
    anchor: str = ""
    start_chapter: int | None = None
    boundary_examples: list[BoundaryExample] = []


# 每种起点写法发给模型的阶段说明与起点字段（**只此一处**；另一种写法的字段从结构里拿掉）。
# 锚点那条是改前的原句（逐字不变）；章号那条照探针第六版（spec phase-start-chapter §2 J5）。
_START_SCHEMA: dict[phase_anchoring.StartBy, tuple[str, dict[str, Any] | None]] = {
    phase_anchoring.StartBy.ANCHOR: (
        "弧线上的一个阶段；anchor 是标志这一阶段开始的一句原文（阶段 1 可留空）。", None),
    phase_anchoring.StartBy.CHAPTER: (
        "弧线上的一个阶段；start_chapter 是这一阶段开始的章号（阶段 1 填 0）。",
        {"default": 0, "title": "Start Chapter", "type": "integer"}),
}


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


def _phase_schema(defs: dict[str, Any], start_by: phase_anchoring.StartBy) -> None:
    """就地把 ``$defs.DraftPhase`` 改成 `start_by` 这一种起点写法（`_START_SCHEMA`）。"""
    phase = defs["DraftPhase"]
    description, field = _START_SCHEMA[start_by]
    phase["description"] = description
    for other in phase_anchoring.StartBy:
        if other is not start_by:
            phase["properties"].pop(other.value, None)
    if field is not None:
        phase["properties"][start_by.value] = field


def draft_schema(group: str | None = None, *,
                 start_by: phase_anchoring.StartBy = phase_anchoring.StartBy.ANCHOR) -> dict[str, Any]:
    """发给模型的 JSON 结构。``group=None`` → 整张；取 `FORMAT_GROUPS` 的键 → 该组子集。

    不给分组调用整张卡的 schema：那会让每一组都以为要输出全部字段。``$defs`` 整体带上，
    不按引用裁剪 —— 多带的定义只是几行噪声，裁错一条就是 ``$ref`` 解析不了的硬伤。
    ``start_by``：阶段起点要哪种写法（默认锚点）；结构里只露那一种。
    """
    full = copy.deepcopy(CardDraft.model_json_schema())
    _phase_schema(full["$defs"], start_by)
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


class ValidRows(NamedTuple):
    """规则 0 的结果：每条的合法编号 + 「只标了 0」的条目序号（状态类全程 / 经历类故事开始前）。"""

    valid: list[list[int]]
    zero_only: list[int]


#: 认 0 的类别 → 0 的含义。加一种认 0 的类别只改这一行；做法、态度（``custom``）不在这里。
_ZERO_MEANINGS = {"experience": "故事开始前", "state": "全程"}


def _valid_number_rows(items, count: int, kind: str, *, layer: str) -> ValidRows:
    """规则 0：每条条目的合法阶段编号（升序、去重）。越界/空的编号被撤回并 warning 一条。

    `count == 0`（无阶段的卡）不做检查、不 warning。状态类、经历类、做法、记忆、关系态度共用
    这一份；认不认 0 看 `layer`，调用方传登记表的类别，不在这里写字段名。

    ``layer`` 在 `_ZERO_MEANINGS` 里（状态类 / 经历类）时 0 表示「一直成立」（全程 / 故事开始前），
    三种情形：只标了 0（或 0 与越界号）→ 记 ``zero_only``，合法编号给空（`verify` 走整条撤回那一支、
    不做位置核对）并打一行 info 便于人工翻看误标；0 与阶段号并存 → 忽略 0、其余编号照常核对（模型
    给了故事内阶段就有位置证据可用）；0 之外的越界号照旧 warning。其他类别（做法、关系态度）标 0
    照旧按坏编号撤回。
    """
    valid_rows: list[list[int]] = []
    zero_only: list[int] = []
    for i, item in enumerate(items):
        nums = [occ.phase for occ in item.occurrences]
        pre = (layer in _ZERO_MEANINGS and count and 0 in nums
               and not any(1 <= p <= count for p in nums))  # 只标了 0（或 0 + 越界号）→ 顶层
        if layer in _ZERO_MEANINGS and count:
            nums = [p for p in nums if p]                   # 认 0 的类别，0 不算坏编号
        valid = sorted({p for p in nums if 1 <= p <= count})
        if count and (len(valid) != len(set(nums)) or (not valid and not pre)):
            logger.warning("[card_draft] %s的阶段编号不合法（共 %d 个阶段）%s：%s",
                           kind, count, _row_label(item), nums)
        if pre:
            zero_only.append(i)
            logger.warning("[card_draft] %s：%s", _ZERO_MEANINGS[layer], _row_label(item))
        valid_rows.append(valid)
    return ValidRows(valid_rows, zero_only)


def experience_fallback(count: int) -> list[int]:
    """经历类没有位置证据 → 挂最后阶段（时间不明放最后，永不泄露后续剧情；spec §3.2）。

    `dispatch`（记忆、字段）与关系（一条态度都不剩的关系）共用这一处。
    """
    return [count]


def _layer_phases(final_phases: list[list[int]], count: int, layer: str) -> list[list[int]]:
    """按类别把每条条目的最终阶段折成「要挂的阶段」：``experience`` 只留最早阶段。

    ①把同一条经历挂到每个标了的阶段，投影 1..k 时就重复了（C14）—— 只挂最早阶段即不重复。
    覆盖全部阶段的那条原样保留：顶层判据 `len(final) == count` 还要用它。
    """
    if layer == "experience":
        return [f if (count and len(f) == count) else f[:1] for f in final_phases]
    return final_phases


def dispatch(final_phases: list[list[int]], count: int, *,
             layer: str = "state", kind: str = "list",
             label: str = "", unlocated: list[int] = (),
             zero_only: list[int] = (),
             describe: Callable[[int, int], str] | None = None,
             tagged: Callable[[int], set[int]] | None = None,
             ) -> tuple[list[int], list[list[int]], list[int]]:
    """按最终阶段把条目分发到顶层、各阶段与未定位区 —— 分发只此一处（S6 / B1）。

    `layer` 决定「挂到哪些阶段」：``state``（默认，做法走这条）→ 每个最终阶段都挂；
    ``experience``（记忆）→ 只挂最早阶段（`_layer_phases`）。共同规则：无阶段的卡
    （`count == 0`）或标注覆盖全部阶段 → 顶层；编号全部被撤回（`final` 空）→ 整条不出现。

    `unlocated`（`phase_anchoring.verify` 给的序号：有合法标注、却没有一段摘录能定位）按类别
    处置（spec §3.2）：``experience`` → 挂最后阶段（时间不明放最后，永不泄露后续剧情）；
    ``state`` → 不进任何阶段，序号原样交回第三项，由调用方放进未定位区。

    `zero_only`（状态类「全程」/ 经历类「故事开始前」的序号）的最终阶段置为全部阶段，由顶层
    判据 `len(final) == count` 进顶层。这条路径上不做位置核对（一直成立的底色多半摘录不在
    阶段 1，故事前的经历在原文里多半是后文回忆，位置都定不了时间）。

    `kind` 决定「一个格子放几条」（登记表说了算）：``list`` → 全放；``scalar`` → 顶层与
    每个阶段都只留第一条，其余丢弃 + warning（单值字段一个格子存不下两条）。

    返回 `(顶层序号, by_phase, 未定位序号)`，`by_phase[p-1]` 是挂到阶段 p 的序号，
    **`by_phase[` 只在这里出现**。
    """
    final_phases = [list(f) for f in final_phases]
    for i in zero_only:                          # 只标了 0 → 覆盖全部阶段（顶层判据见下）
        final_phases[i] = list(range(1, count + 1))
    loose: list[int] = []
    for i in unlocated:
        if layer == "experience":
            final_phases[i] = experience_fallback(count)
        else:
            loose.append(i)
    final_phases = _layer_phases(final_phases, count, layer)
    top: list[int] = []
    by_phase: list[list[int]] = [[] for _ in range(count)]
    for i, final in enumerate(final_phases):
        if count == 0 or len(final) == count:
            top.append(i)
        elif final:
            for p in final:
                by_phase[p - 1].append(i)
    if kind == "scalar":
        if tagged is None:
            raise ValueError(f"单值字段 {label or '(未命名)'} 分发时必须给 tagged（模型原标），"
                             "否则撞车会静默退回「排前面优先」")
        top = _cap_slot(top, label, "顶层", 0, describe, tagged)
        by_phase = [_cap_slot(slot, label, f"阶段 {p}", p, describe, tagged)
                    for p, slot in enumerate(by_phase, 1)]
    return top, by_phase, loose


def slot_winner(cands: list[int], phase: int,
                tagged: Callable[[int], set[int]]) -> int:
    """「一格留谁」只在这里判定：原标此阶段（顶层为 0）的那条优先，没有则取第一条。

    `tagged(i)` 返回第 i 条**模型原标**的阶段集合；不用位置核对结果（整卡跳过时它为空）。
    """
    return next((i for i in cands if phase in tagged(i)), cands[0])


def _cap_slot(slot: list[int], label: str, where: str, phase: int,
              describe: Callable[[int, int], str] | None,
              tagged: Callable[[int], set[int]]) -> list[int]:
    """单值字段的一个格子只留一条，其余丢弃 + warning（顶层格子同此规则）。

    `describe` 为 None 时文案与旧版逐字一致；给了就写明保留的是哪条、丢弃的是哪条及其来源。
    """
    if not slot:
        return []
    winner = slot_winner(slot, phase, tagged)
    for i in slot:
        if i == winner:
            continue
        if describe is None:
            logger.warning("[card_draft] %s 的%s有多条取值，保留第一条", label, where)
        else:
            logger.warning("[card_draft] %s 的%s 有多条取值，保留%s，丢弃%s",
                           label, where, describe(winner, phase), describe(i, phase))
    return [winner]


def _convert_relationships(rels, anchors, count: int, name: str) -> tuple[list[dict], list[dict], int, int]:
    """关系草稿 → 存卡关系：**每条态度单独**过位置检查与 `dispatch`，再按关系收回（spec §3.3）。

    态度只核对、不挪阶段（`Placement.KEEP_TAGGED`）：摘录查到（不超 `MAX_OCCURRENCES`）即认
    所标阶段，摘录查不到 → 进未定位区。每条态度各自是一个检查条目 —— 按关系聚合的话，同一阶段
    的几条态度会共用位置证据，A 的摘录会把 B 也带走（审计发现 1）。同一阶段挂上多条时，**原本
    就标在这个阶段**的那条优先，其余按标注顺序取第一条（撞车落败打一条 WARNING）；一个阶段都没
    赢下的态度进未定位区，`note` 与原标阶段跟着它走。一条态度都不剩的关系按经历类挂最后阶段、
    态度留空（`placeholder_phase_attitudes`）。顶层 `attitude` 取 `top_attitude`。没有
    `attitudes` 的关系（旧卡）原样保留。
    返回 `(关系, 未定位态度, 改挂条数, 挂最后阶段条数)`。
    """
    if not rels:
        return [], [], 0, 0
    flat = [(i, a) for i, r in enumerate(rels) for a in r.attitudes]
    # 复用 `DraftMemory` 当载体：位置检查要的只是「带 occurrences 的条目」，target 当 label。
    items = [DraftMemory(memory=rels[i].target,
                         occurrences=[DraftOccurrence(phase=a.phase, quote=a.quote)])
             for i, a in flat]
    # 态度只核对、不挪阶段：态度常在后文被回忆复述，摘录位置不等于态度成立时间，
    # 摘录查到（不超 MAX_OCCURRENCES）即算命中所标阶段（spec A1 / 调查文档第三之二节）。
    res = phase_anchoring.verify(
        items, _valid_number_rows(items, count, "态度",
                                  layer=REGISTRY["relationships"].layer).valid, anchors,
        kind="态度", label=lambda x: x.memory, name=name,
        placement=phase_anchoring.Placement.KEEP_TAGGED)
    # 未定位的态度（`dispatch` 第三项）不在任何格子里，下面自然一个阶段都赢不下 → 进未定位区
    top, slots, _ = dispatch(res.phases, count, unlocated=res.unlocated)
    out: list[dict] = []
    loose: list[dict] = []
    to_last = 0
    for i, r in enumerate(rels):
        row = r.model_dump()
        row.pop("attitudes", None)
        if not r.attitudes:
            out.append(row)                       # 旧卡：顶层 attitude 原样
            continue
        mine = [j for j, (ri, _) in enumerate(flat) if ri == i]
        if count == 0:                            # 无阶段的卡：不分阶段，取最后一条态度
            row["phase_attitudes"] = []
            row["attitude"] = r.attitudes[-1].attitude
            out.append(row)
            continue
        winners: dict[int, int] = {}              # 阶段 → 该阶段采用的态度（flat 序号）
        for t in range(1, count + 1):
            cands = [j for j in mine if j in top or j in slots[t - 1]]
            if cands:
                winners[t] = slot_winner(cands, t, lambda j: {flat[j][1].phase})
                for j in cands:                   # 同阶段撞车：只采用一条，其余落败
                    if j != winners[t]:
                        logger.warning("撞车落败：态度 %r 标在阶段 %d，阶段 %d 采用了另一条",
                                       r.target, flat[j][1].phase, t)
        won = set(winners.values())
        for j in mine:
            if j not in won:                      # 没有位置证据，或撞车全输 → 未定位区
                a = flat[j][1]
                loose.append({"target": r.target, "attitude": a.attitude, "note": a.note,
                              "phase": a.phase})
        if winners:
            row["phase_attitudes"] = [
                {"phase": t, "attitude": flat[j][1].attitude, "note": flat[j][1].note}
                for t, j in sorted(winners.items())]
        else:                                     # 一条都不剩 → 经历类：挂最后阶段、态度留空
            to_last += 1
            row["phase_attitudes"] = placeholder_phase_attitudes(experience_fallback(count)[0])
        row["attitude"] = top_attitude(row["phase_attitudes"])
        out.append(row)
    return out, loose, res.rehung, to_last


def card_from_draft(data: Any, source_text: str, *, retro: RetroRegions) -> CharacterCard:
    """模型输出（草稿）→ 存卡。形态不对抛 `pydantic.ValidationError`，由调用方按各自口径上屏。

    先做阶段编号合法性过滤（越界、空 → 对不上的撤回、打 warning，一条的编号全部作废就整条
    撤回，与 `core.card_quotes.retract_unverified` 对模型输出的口径同）；再让 `phase_anchoring`
    按 `source_text` 核对摘录位置，摘录落在哪个阶段就挂哪个阶段（改挂）；一段都定不了位的，
    状态类进未定位区、经历类挂最后阶段（spec `arc-phase-unlocated.md` §3）。做法与记忆
    各走一遍（位置检查口径相同），结果交 `dispatch` 分发：做法按状态类挂到每个成立阶段，记忆按
    经历类只挂最早阶段；所有阶段都成立 → 顶层，没有阶段的卡全部放顶层、不做位置检查。关系态度
    同样过一遍检查。最后写
    `ArcPhase.start`（整卡跳过时不写）与 `CharacterArc.source_fingerprint`。`source_text` 必填。

    `retro` 必填：这本书的回忆区（倒叙章节，`core.retro_regions`）。回忆区里的位置不作位置证据、
    锚点落进回忆区整卡跳过（`phase_anchoring`）。不识别回忆区的入口显式传 `NOT_DETECTED`，
    行为同没有回忆区 —— 必填是为了让生产入口不会悄悄漏传。
    """
    draft = CardDraft.model_validate(data)
    phases = draft.character_arc.phases
    count = len(phases)
    anchors = phase_anchoring.build_anchors(                       # 整本原文只归一化这一次
        phases, source_text, retro=retro.spans(source_text) if phases else ())
    phase_anchoring.log_phase_starts(phases, source_text, anchors, draft.name)   # 每张卡只在这里打一次

    b = phase_anchoring.verify(
        draft.situation_behaviors,
        _valid_number_rows(draft.situation_behaviors, count, "做法",
                           layer=REGISTRY["situation_behaviors"].layer).valid,
        anchors, kind="做法", name=draft.name)
    m_rows = _valid_number_rows(draft.key_memories, count, "记忆",
                                layer=REGISTRY["key_memories"].layer)
    m = phase_anchoring.verify(
        draft.key_memories, m_rows.valid,
        anchors, kind="记忆", name=draft.name)
    b_top, b_slots, b_loose = dispatch(b.phases, count, unlocated=b.unlocated)
    m_top, m_slots, _ = dispatch(m.phases, count, layer="experience",
                                 unlocated=m.unlocated, zero_only=m_rows.zero_only)
    # 监测按四类分别计（改挂 / 进未定位区 / 挂最后阶段），日志行里再给汇总（spec §3.7）
    kinds = {"做法": [b.rehung, len(b_loose), 0], "字段": [0, 0, 0],
             "记忆": [m.rehung, 0, len(m.unlocated)], "关系": [0, 0, 0]}

    card = draft.model_dump()
    overlays: dict[int, dict[str, Any]] = {}
    loose_overlay: dict[str, Any] = {}
    for path, spec in _GENERIC_PATHS:
        rows = get_path(draft, path)
        vr = _valid_number_rows(rows, count, path, layer=spec.layer)
        res = phase_anchoring.verify(
            rows, vr.valid, anchors,
            kind=path, label=lambda x: x.value, name=draft.name)
        # 列表与单值走同一个循环：`kind` 在 `dispatch` 里决定格子容量（scalar 每格只留第一条），
        # 这里只按 `kind` 决定取值形态（list → 列表，scalar → 单值）。
        def describe(i: int, phase: int) -> str:
            """单值撞车时说明这条取值从哪来：原标此阶段 / 由某阶段改挂进来 / 全程（顶层）。"""
            value = f"「{rows[i].value}」"
            if phase == 0:
                return f"{value}（全程）"
            targets = res.tag_targets[i]
            if phase in targets.get(phase, []):
                return f"{value}（原标此阶段）"
            source = next((q for q, landed in targets.items() if phase in landed), None)
            return f"{value}（{'由阶段 %d 改挂进来' % source if source is not None else '原标此阶段'}）"

        top, slots, loose = dispatch(res.phases, count, layer=spec.layer, kind=spec.kind,
                                     label=path, unlocated=res.unlocated, zero_only=vr.zero_only,
                                     describe=describe,
                                     tagged=lambda i: {o.phase for o in rows[i].occurrences})
        f = kinds["字段"]
        f[0] += res.rehung
        f[1] += len(loose)
        f[2] += len(res.unlocated) - len(loose)
        top_vals = [rows[i].value for i in top]
        set_path(card, path,
                 top_vals if spec.kind == "list" else (top_vals[0] if top_vals else ""))
        for idx, slot in enumerate(slots):
            vals = [rows[i].value for i in slot]
            if not vals:
                continue
            # overlay 与卡片同形：按登记表路径嵌套写入，不把路径当键名（spec §3.5）
            set_path(overlays.setdefault(idx, {}), path,
                     vals if spec.kind == "list" else vals[0])
        if loose:                                 # 未定位区一律存列表（单值字段也可能有多条）
            set_path(loose_overlay, path, [rows[i].value for i in loose])

    card["situation_behaviors"] = [
        {"situation": draft.situation_behaviors[i].situation,
         "behavior": draft.situation_behaviors[i].behavior,
         "source_quote": _first_quote(draft.situation_behaviors[i])}
        for i in b_top
    ]
    card["key_memories"] = [draft.key_memories[i].memory for i in m_top]
    card["relationships"], loose_attitudes, r_rehung, r_to_last = _convert_relationships(
        draft.relationships, anchors, count, draft.name)
    kinds["关系"] = [r_rehung, len(loose_attitudes), r_to_last]
    rehung, unlocated, to_last = (sum(v[i] for v in kinds.values()) for i in range(3))
    card["character_arc"]["unlocated"] = {
        "behaviors": [
            {"situation": draft.situation_behaviors[i].situation,
             "behavior": draft.situation_behaviors[i].behavior,
             "source_quote": _first_quote(draft.situation_behaviors[i])}
            for i in b_loose],
        "overlay": loose_overlay,
        "attitudes": loose_attitudes,
    }
    logger.warning("[phase_anchoring] card=%s tags=%d dropped=%d ambiguous=%d "
                   "unverified_quotes=%d rehung=%d unlocated=%d to_last=%d "
                   "skipped_card=%s memories_dropped=%d kinds=%s retro=%s",
                draft.name, b.tags, b.dropped, b.ambiguous, b.unverified_quotes,
                rehung, unlocated, to_last, b.skipped_card, m.dropped,
                ",".join(f"{k}:{r}/{u}/{t}" for k, (r, u, t) in kinds.items()), retro.summary())

    for idx, phase in enumerate(card["character_arc"]["phases"]):
        phase["behaviors"] = [
            {"situation": draft.situation_behaviors[i].situation,
             "behavior": draft.situation_behaviors[i].behavior,
             "source_quote": b.phase_quotes[i].get(idx + 1, "")}
            for i in b_slots[idx]
        ]
        memories = [draft.key_memories[i].memory for i in m_slots[idx]]
        if memories:
            set_path(overlays.setdefault(idx, {}), "key_memories", memories)
        if idx in overlays:
            phase["overlay"] = overlays[idx]
    if b.starts is not None:                      # 整卡跳过位置检查 → 不写起点（D7）
        for phase, start in zip(card["character_arc"]["phases"], b.starts):
            phase["start"] = start
    card["character_arc"]["source_fingerprint"] = content_fingerprint(source_text)
    return CharacterCard.model_validate(card)
