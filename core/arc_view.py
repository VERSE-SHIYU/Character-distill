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


def valid_phase(card: CharacterCard, arc_phase: int | None) -> int | None:
    """用户选的阶段号是否有效：1..n 原样返回；None / 越界 / 卡无阶段 → None。

    **阶段号的范围规则只在这里**：路由存库前的校验与 `_normalize_k` 的归一都用它（审计 A3）。
    """
    n = len(card.character_arc.phases)
    if arc_phase is None or not 1 <= arc_phase <= n:
        return None
    return int(arc_phase)


def _normalize_k(card: CharacterCard, arc_phase: int | None) -> int:
    """阶段号归一：有效 → 原样；否则 → n（最后阶段）；无阶段卡 → 0。"""
    k = valid_phase(card, arc_phase)
    return len(card.character_arc.phases) if k is None else k


def has_positions(card: CharacterCard) -> bool:
    """阶段起点是否可用于按位置截断：齐全、严格递增、且有正文指纹。

    存卡调度（是否补位置）与检索上界（是否启用过滤）**共用这一个判定**。
    """
    phases = card.character_arc.phases
    if not phases or not card.character_arc.source_fingerprint:
        return False
    starts = [p.start for p in phases]
    if any(s is None for s in starts):
        return False
    return all(starts[i] < starts[i + 1] for i in range(len(starts) - 1))


def arc_view(card: CharacterCard, arc_phase: int | None) -> ArcView:
    """算出阶段视图（不复制卡）。"""
    phases = card.character_arc.phases
    n = len(phases)
    k = _normalize_k(card, arc_phase)
    memories = list(card.key_memories) + [m for p in phases[:k] for m in p.memories]
    boundary = k < n
    boundary_examples = list(phases[k - 1].boundary_examples) if boundary else []
    before: int | None = None
    if boundary and has_positions(card):
        before = phases[k].start
    return ArcView(
        k=k, n=n, show_axis=(n > 0 and k == n), boundary=boundary,
        memories=memories, boundary_examples=boundary_examples, before=before,
        source_fingerprint=card.character_arc.source_fingerprint,
    )


def _project_relationships(
    rels: list[Relationship], k: int, n: int,
) -> list[Relationship]:
    """关系投影：k<n 取 ≤k 里最新态度、之后才认识的去掉；k=n 一律用顶层态度。"""
    if k == n:
        return [r.model_copy(deep=True) for r in rels]
    out: list[Relationship] = []
    for r in rels:
        if not r.phase_attitudes:
            out.append(r.model_copy(deep=True))   # 旧卡：原样
            continue
        upto = [pa for pa in r.phase_attitudes if pa.phase <= k]
        if not upto:
            continue                               # 之后才认识 → 去掉
        r2 = r.model_copy(deep=True)
        r2.attitude = max(upto, key=lambda pa: pa.phase).attitude
        out.append(r2)
    return out


def project_card(card: CharacterCard, arc_phase: int | None) -> tuple[CharacterCard, ArcView]:
    """把卡投影到阶段 k：返回（投影后的副本，阶段视图）。原卡不改。"""
    view = arc_view(card, arc_phase)
    k, n = view.k, view.n
    proj = card.model_copy(deep=True)
    if k < n:
        proj.character_arc.phases = [
            ArcPhase(label=p.label, state=p.state) for p in proj.character_arc.phases[:k]
        ]
        proj.character_arc.axis = ""
        proj.first_message = ""
    proj.key_memories = list(view.memories)
    proj.dialogue_examples = list(card.dialogue_examples) + [
        d for p in card.character_arc.phases[:k] for d in p.dialogue_examples
    ]
    proj.relationships = _project_relationships(card.relationships, k, n)
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
