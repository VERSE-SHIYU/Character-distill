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
from core.schema import ArcPhase, BoundaryExample, CharacterCard, Relationship, RetrievalWindow


class ArcView(NamedTuple):
    """一个阶段的视图：投影卡之外还要交给注入与检索的那几件事。"""
    k: int                                  # 归一后的阶段号（1..n；无阶段卡为 0）
    n: int                                  # 卡的阶段总数
    show_axis: bool                         # k==n（不截断）→ 注入变化轴
    boundary: bool                          # k<n（截断了）→ 注入边界说明与示范
    memories: list[str]                     # 要注入的记忆：顶层 + 阶段 1..k
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

    路由存库前的校验用它。`selectable` 为假的卡（起点不全或缺指纹）恒不可选（DA8）——
    归一（`_normalize_k`）不吃这个门：位置不全的旧卡照样投影到用户给的阶段号。
    """
    if not card.character_arc.selectable:
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
    memories = list(card.key_memories) + [
        m for p in phases[:k] for m in (p.overlay.get("key_memories") or [])
    ]
    boundary = k < n
    boundary_examples = list(phases[k - 1].boundary_examples) if boundary else []
    before: int | None = None
    if boundary and card.character_arc.has_positions():
        before = phases[k].start
    return ArcView(
        k=k, n=n, show_axis=(n > 0 and k == n), boundary=boundary,
        memories=memories, boundary_examples=boundary_examples, before=before,
        source_fingerprint=card.character_arc.source_fingerprint,
    )


def _project_relationships(
    rels: list[Relationship], k: int,
) -> list[Relationship]:
    """关系投影：取 ≤k 里最新一条的态度与口径；之后才认识的去掉；无阶段态度原样（旧卡）。"""
    out: list[Relationship] = []
    for r in rels:
        if not r.phase_attitudes:
            out.append(r.model_copy(deep=True))   # 旧卡：原样
            continue
        upto = [pa for pa in r.phase_attitudes if pa.phase <= k]
        if not upto:
            continue                               # 之后才认识 → 去掉
        latest = max(upto, key=lambda pa: pa.phase)
        r2 = r.model_copy(deep=True)
        r2.attitude = latest.attitude
        if latest.note:
            r2.note = latest.note
        out.append(r2)
    return out


def _project_custom(proj: ProjectedCard, card: CharacterCard, k: int, n: int) -> None:
    """专门投影（各自函数）：弧线截断、关系口径、情境做法原样。就地改 `proj`。"""
    if k < n:
        proj.character_arc.phases = [
            ArcPhase(label=p.label, state=p.state) for p in proj.character_arc.phases[:k]
        ]
        proj.character_arc.axis = ""
        proj.first_message = ""
    proj.relationships = _project_relationships(card.relationships, k)


def project_card(card: CharacterCard, arc_phase: int | None) -> tuple[ProjectedCard, ArcView]:
    """把卡投影到阶段 k：返回（投影后的副本，阶段视图）。原卡不改。

    除弧线 / 关系 / 开场白这些专门投影（`_project_custom`）外，全按 `REGISTRY` 通用执行 ——
    不写字段名分支（S2）。

    **列表顺序是读者依赖的契约（B3）**：状态类列表 = **阶段 k 特有在前 + 全程在后**。
    读者按 N 取前几条（`[:3]` / `[:2]`）时，阶段 k 才成立的人设排在最前，不会被顶层的
    全程条目挤掉（顶层条目 ≥N 时尤其）。经历类列表仍是顶层在前 + 1..k（读者整体使用，
    不取前 N）。
    """
    view = arc_view(card, arc_phase)
    k = view.k
    phases = card.character_arc.phases
    proj = ProjectedCard(**card.model_dump())

    for path, spec in REGISTRY.items():
        kth = phases[k - 1].overlay.get(path) if k >= 1 else None
        if spec.layer == "state":
            if spec.kind == "list":
                base = list(get_path(proj, path) or [])
                set_path(proj, path, list(kth or []) + base)
            elif kth:
                set_path(proj, path, kth)
        elif spec.layer == "experience":
            if spec.kind == "list":
                base = list(get_path(proj, path) or [])
                extra = [x for p in phases[:k] for x in (p.overlay.get(path) or [])]
                set_path(proj, path, base + extra)
            else:
                parts = [get_path(proj, path) or ""]
                parts += [p.overlay.get(path) or "" for p in phases[:k]]
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
