# -*- coding: utf-8 -*-
"""阶段视图 —— 把整卡投影到用户选的那个阶段（纯计算，无 IO）。

**阶段相关的取舍只在这里写一次**：阶段号归一、要注入哪些阶段、检索上界、关系取哪一版
态度、边界示范取谁。注入（`context_engine`）、检索（`_scene_items`）、开场白（`distill`）、
存卡调度（`text_manager`）都读本模块的产出，不各自再判一遍。

投影只在 `ChatEngine.__init__`（和开场白生成处）做一次：`self.card` 即投影后的卡，
下游的关系消费方、卡片扩展层、群聊都天然读到阶段 k 的版本，不必改。
"""

from __future__ import annotations

from typing import NamedTuple

from core.card_layers import REGISTRY, get_path, set_path
from core.schema import (ArcPhase, BoundaryExample, CharacterCard, Relationship,
                         RetrievalWindow, UnlocatedItems)


class ArcView(NamedTuple):
    """一个阶段的视图：投影卡之外还要交给注入与检索的那几件事。"""
    k: int                                  # 归一后的阶段号（1..n；无阶段卡为 0）
    n: int                                  # 卡的阶段总数
    show_axis: bool                         # k==n（不截断）→ 注入变化轴
    boundary: bool                          # k<n（截断了）→ 注入边界说明与示范
    boundary_examples: list[BoundaryExample]  # 阶段 k 的边界示范（k<n 才有）
    before: int | None                      # 检索上界（规范化坐标）；不截断或缺位置时 None
    source_fingerprint: str = ""            # 起点所依据的正文指纹

    @property
    def window(self) -> RetrievalWindow | None:
        """检索时间窗；不截断时 None。检索方只认这一个值，不另取上界与指纹。"""
        if self.before is None:
            return None
        return RetrievalWindow(self.before, self.source_fingerprint)


class ProjectedCard(CharacterCard):
    """已投影到某阶段的卡 —— **只能由 `project_card` 构造**（DA18）。

    拼角色扮演 prompt 的入口（`ChatEngine` 的注入、`ContextEngine`、开场白、苏醒台词、
    市场 @ 回复）只收这个类型，把「拿原卡拼 prompt」在类型上堵死。
    """


def require_projected(card) -> ProjectedCard:
    """拼角色扮演 prompt 的入口只收投影卡 —— 收原卡在类型上直接堵死（DA18）。

    仅类型检查，不复制：`project_card` 已经给的是副本，这里再拷一次只会多一份没人读的卡。
    """
    if not isinstance(card, ProjectedCard):
        raise TypeError(
            f"{type(card).__name__} 不是 ProjectedCard —— 拼角色扮演 prompt 只收 "
            f"core.arc_view.project_card 的产物")
    return card


def _phase_in_range(card: CharacterCard, arc_phase: int | None) -> int | None:
    """**阶段号的范围规则只在这里**：1..n 原样返回，None / 越界 → None（审计 A3）。"""
    n = len(card.character_arc.phases)
    if arc_phase is None or not 1 <= arc_phase <= n:
        return None
    return int(arc_phase)


def valid_phase(card: CharacterCard, arc_phase: int | None) -> int | None:
    """用户选的阶段号是否有效：1..n 原样返回；None / 越界 / 卡不可选阶段 → None。

    路由存库前的校验用它。不可选阶段的卡（起点不全或缺指纹，`has_positions` 为假）恒不可选
    （DA8）—— 归一（`_normalize_k`）不吃这个门：位置不全的旧卡照样投影到用户给的阶段号。
    """
    if not card.character_arc.has_positions():
        return None
    return _phase_in_range(card, arc_phase)


def _normalize_k(card: CharacterCard, arc_phase: int | None) -> int:
    """阶段号归一：有效 → 原样；否则 → n（最后阶段）；无阶段卡 → 0。"""
    k = _phase_in_range(card, arc_phase)
    return len(card.character_arc.phases) if k is None else k


def arc_view(card: CharacterCard, arc_phase: int | None) -> ArcView:
    """算出阶段视图（不复制卡）。"""
    phases = card.character_arc.phases
    n = len(phases)
    k = _normalize_k(card, arc_phase)
    boundary = k < n
    boundary_examples = list(phases[k - 1].boundary_examples) if boundary else []
    before: int | None = None
    if boundary and card.character_arc.has_positions():
        before = phases[k].start
    return ArcView(
        k=k, n=n, show_axis=(n > 0 and k == n), boundary=boundary,
        boundary_examples=boundary_examples, before=before,
        source_fingerprint=card.character_arc.source_fingerprint,
    )


def phase_header(index: int, label: str = "") -> str:
    """阶段表头「阶段 i·label」（无 label 则「阶段 i」）—— 表头写法只此一处（B6）。"""
    label = (label or "").strip()
    return f"阶段 {index}·{label}" if label else f"阶段 {index}"


def card_outline(card: CharacterCard, layers: tuple[str, ...] = ("state", "experience")):
    """整卡的两层视图：``(全程, 逐阶段)`` —— 导出与展示共用的唯一字段名来源（B5）。

    - **全程** = 原卡顶层的 state / experience 字段；
    - **逐阶段** = 第 i 个阶段的 ``overlay``（每个阶段一份）。

    两层都按登记表遍历，一行是 ``(路径, 中文名, 文本)``：字段名一律取 ``FieldSpec.label``
    （不在此拼字段名），列表「；」连接、单值原样，无值跳过。默认只取 state / experience 两类
    —— 它们是会随阶段变的人设；stable 全程不变、custom 各自渲染、none 不进 prompt。
    要整组列出某类字段（如导出的说话风格，含 stable 的用词水平、禁忌用词）时，用 `layers`
    放宽；阶段 overlay 里只会有 state / experience 路径，放宽不会多出阶段内容。
    """
    def rows(read) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for path, spec in REGISTRY.items():
            if spec.layer not in layers:
                continue
            value = read(path)
            if spec.kind == "list":
                text = "；".join(str(x) for x in (value or []))
            else:
                text = str(value or "").strip()
            if text:
                out.append((path, spec.label, text))
        return out

    lifelong = rows(lambda path: get_path(card, path))
    per_phase = [rows(lambda path, o=p.overlay: get_path(o, path))
                 for p in card.character_arc.phases]
    return lifelong, per_phase


def inherited(values: list, k: int):
    """沿用最近一次：阶段 1..k 里最近一个有值的那一格；都没有 → None。永远不看 k 之后。

    状态「一直成立，直到被改变」—— 阶段 k 自己那格没有值，就是这一格还没被改写，沿用 1..k 里
    最近的一次（`core/arc_view.py` 的状态类投影与关系口径共用这一份，不留第二处写法）。
    """
    for v in reversed(values[:max(k, 0)]):
        if v:
            return v
    return None


def _project_relationships(
    rels: list[Relationship], k: int,
) -> list[Relationship]:
    """关系投影：只留阶段 ≤k 的态度；之后才认识的去掉；无阶段态度原样（旧卡）。

    - 态度取 ≤k 里最新的一条；口径取 ≤k 里最近一条**非空**的口径。都只看 ≤k，**不回落**到
      关系顶层的 note。生成规则第 7 条要求顶层写「两人最初的关系」，但那是对模型的要求，
      模型未必照做；投影只认按阶段标好的条目，不赌顶层那句没写成后期立场。
    - 投影卡里的 `phase_attitudes` 也截到 ≤k：投影卡只装阶段 k 能看到的东西（R2），
      读者以后读这个字段也不会读到后期。
    """
    out: list[Relationship] = []
    for r in rels:
        if not r.phase_attitudes:
            out.append(r.model_copy(deep=True))   # 旧卡：原样
            continue
        upto = sorted((pa for pa in r.phase_attitudes if pa.phase <= k),
                      key=lambda pa: pa.phase)
        if not upto:
            continue                               # 之后才认识 → 去掉
        r2 = r.model_copy(deep=True)
        r2.phase_attitudes = [pa.model_copy() for pa in upto]
        r2.attitude = upto[-1].attitude
        r2.note = inherited([pa.note for pa in upto], len(upto)) or ""
        out.append(r2)
    return out


def _project_custom(proj: ProjectedCard, card: CharacterCard, k: int, n: int) -> None:
    """专门投影（各自函数）：弧线截断、关系口径、情境做法。就地改 `proj`。

    阶段表一律只留 1..k 的 label / state（k=n 也一样）：各阶段的 overlay 已经按登记表并进
    顶层字段，留在阶段里就是同一份内容的第二个来源，k=n 时还会带着前几个阶段的状态（R2）。

    情境做法是状态类：投影卡的 `situation_behaviors` = **阶段 k 的做法在前 + 全程做法在后**
    （同 B3 的顺序契约）；其他阶段的做法、未定位区的做法不进投影。阶段下的做法随阶段表一起
    被丢掉，这里是它们唯一的出口。
    """
    own = [b.model_copy(deep=True) for b in card.character_arc.phases[k - 1].behaviors] if k >= 1 else []
    proj.situation_behaviors = own + list(proj.situation_behaviors)
    proj.character_arc.phases = [
        ArcPhase(label=p.label, state=p.state) for p in proj.character_arc.phases[:k]
    ]
    if k < n:
        proj.character_arc.axis = ""
        proj.first_message = ""
    proj.relationships = _project_relationships(card.relationships, k)
    proj.character_arc.unlocated = UnlocatedItems()   # 未定位区不进 prompt：投影卡不带它


def project_card(card: CharacterCard, arc_phase: int | None) -> tuple[ProjectedCard, ArcView]:
    """把卡投影到阶段 k：返回（投影后的副本，阶段视图）。原卡不改。

    除弧线 / 关系 / 开场白这些专门投影（`_project_custom`）外，全按 `REGISTRY` 通用执行 ——
    不写字段名分支（S2）。

    **列表顺序是读者依赖的契约（B3）**：状态类列表 = **阶段 k（或沿用最近一次）在前 + 全程在后**。
    读者按 N 取前几条（`[:3]` / `[:2]`）时，阶段 k 才成立的人设排在最前，不会被顶层的
    全程条目挤掉（顶层条目 ≥N 时尤其）。经历类列表仍是顶层在前 + 1..k（读者整体使用，
    不取前 N）。
    """
    view = arc_view(card, arc_phase)
    k = view.k
    phases = card.character_arc.phases
    proj = ProjectedCard(**card.model_dump())

    for path, spec in REGISTRY.items():
        if spec.layer == "state":
            per_phase = [get_path(p.overlay, path) for p in phases]
            own = per_phase[k - 1] if k >= 1 else None
            if spec.kind == "list":
                base = list(get_path(proj, path) or [])
                set_path(proj, path, list(inherited(per_phase, k) or []) + base)
            elif own or not get_path(proj, path):
                kth = inherited(per_phase, k)
                if kth:
                    set_path(proj, path, kth)
        elif spec.layer == "experience":
            if spec.kind == "list":
                base = list(get_path(proj, path) or [])
                extra = [x for p in phases[:k] for x in (get_path(p.overlay, path) or [])]
                set_path(proj, path, base + extra)
            else:
                parts = [get_path(proj, path) or ""]
                parts += [get_path(p.overlay, path) or "" for p in phases[:k]]
                set_path(proj, path, "；".join(x for x in parts if x))

    _project_custom(proj, card, k, view.n)
    return proj, view


def phase_of(card: CharacterCard, pos: int) -> int:
    """规范化位置 `pos` 落在哪个阶段（1 起）；落在起点上归**后**一阶段。"""
    phases = card.character_arc.phases
    n = len(phases)
    if n == 0:
        return 0
    for i in range(n - 1, -1, -1):
        s = phases[i].start
        if s is not None and s <= pos:
            return i + 1
    return 1
