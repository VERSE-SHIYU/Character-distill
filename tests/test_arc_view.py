# -*- coding: utf-8 -*-
"""U1–U4、U17–U19b：`core.arc_view` 的纯计算契约（无 IO）。

阶段相关的取舍只应在这个模块里写一次；这些用例把它钉住：
阶段号归一、投影（弧线/记忆/台词/关系/边界示范/开场白）、检索上界。
"""

import pytest

from core import arc_view as av
from core.schema import CharacterCard


def _card(
    *,
    n: int = 3,
    starts=(0, 100, 200),
    fp: str = "FP",
    memories=("m1", "m2", "m3"),
    dialogues=(("d1",), ("d2",), ("d3",)),
    boundaries=((("问1", "答1"),), (("问2", "答2"),), (("问3", "答3"),)),
    attitudes=((1, "早"), (2, "中"), (3, "晚")),
    top_memories=("top",),
    top_dialogues=("dt",),
    axis: str = "从冷到热",
    relationships=None,
    first_message: str = "开场白",
) -> CharacterCard:
    phases = []
    for i in range(n):
        phase = {
            "label": f"L{i + 1}",
            "state": f"S{i + 1}",
            "behaviors": [],
            "memories": [memories[i]] if i < len(memories) else [],
            "dialogue_examples": list(dialogues[i]) if i < len(dialogues) else [],
            "boundary_examples": [
                {"ask": a, "reply": r} for a, r in (boundaries[i] if i < len(boundaries) else ())
            ],
        }
        if i < len(starts) and starts[i] is not None:
            phase["start"] = starts[i]
        phases.append(phase)
    arc = {"axis": axis, "phases": phases, "source_fingerprint": fp}
    if relationships is None:
        relationships = []
    return CharacterCard.model_validate({
        "name": "甲",
        "key_memories": list(top_memories),
        "dialogue_examples": list(top_dialogues),
        "first_message": first_message,
        "character_arc": arc,
        "relationships": relationships,
    })


# ── U1 阶段号归一 ──────────────────────────────────────────────
@pytest.mark.parametrize("arg,expected", [
    (None, 3),   # 旧存档 / 没选 → 最后阶段
    (0, 3),      # 越界（下界）
    (1, 1),      # 阶段 1 是合法的
    (3, 3),      # n
    (4, 3),      # 越界（上界）→ 最后阶段
])
def test_u1_phase_number_normalizes(arg, expected):
    view = av.arc_view(_card(), arg)
    assert view.k == expected
    assert view.n == 3


def test_u1_no_phases_card_is_k0():
    card = CharacterCard.model_validate({"name": "甲"})
    view = av.arc_view(card, 2)
    assert view.k == 0 and view.n == 0


# ── U2 k<n 隐藏之后阶段与轴、给边界说明；k=n 相反 ────────────────
def test_u2_mid_phase_hides_later():
    card, view = av.project_card(_card(), 2)
    assert [p.label for p in card.character_arc.phases] == ["L1", "L2"]
    assert card.character_arc.axis == ""
    assert view.boundary is True
    assert view.show_axis is False


def test_u2_last_phase_keeps_axis_no_boundary():
    card, view = av.project_card(_card(), 3)
    assert [p.label for p in card.character_arc.phases] == ["L1", "L2", "L3"]
    assert card.character_arc.axis == "从冷到热"
    assert view.boundary is False
    assert view.show_axis is True


# ── U3 记忆 = 顶层 + 阶段 1..k ─────────────────────────────────
def test_u3_memories_top_plus_upto_k():
    card, view = av.project_card(_card(), 2)
    assert card.key_memories == ["top", "m1", "m2"]
    assert "m3" not in card.key_memories
    assert view.memories == ["top", "m1", "m2"]


def test_u3_memories_at_last_include_all():
    card, _ = av.project_card(_card(), 3)
    assert card.key_memories == ["top", "m1", "m2", "m3"]


# ── U4 检索上界 ────────────────────────────────────────────────
def test_u4_before_is_next_phase_start():
    view = av.arc_view(_card(), 1)
    assert view.before == 100          # 阶段 2 的起点


def test_u4_missing_start_gives_none():
    view = av.arc_view(_card(starts=(0, None, 200)), 1)
    assert view.before is None


def test_u4_non_increasing_gives_none():
    view = av.arc_view(_card(starts=(0, 200, 100)), 1)
    assert view.before is None


def test_u4_empty_fingerprint_gives_none():
    view = av.arc_view(_card(fp=""), 1)
    assert view.before is None


def test_u4_last_phase_gives_none():
    view = av.arc_view(_card(), 3)
    assert view.before is None


def test_has_positions_true_and_false():
    # 判定已挪进 `CharacterArc.has_positions`（本段 S7）；卡级语义不变。
    assert _card().character_arc.has_positions() is True
    assert _card(starts=(0, None, 200)).character_arc.has_positions() is False
    assert _card(starts=(0, 200, 100)).character_arc.has_positions() is False
    assert _card(fp="").character_arc.has_positions() is False


# ── U17 关系投影 ───────────────────────────────────────────────
def _rels():
    return [
        # 从头就在：态度随阶段变化。顶层值（"顶层"）刻意与阶段 3（"晚"）不同，
        # 好让 M29「k=n 误用阶段态度」打出红。
        {"target": "乙", "relation": "友", "attitude": "顶层",
         "phase_attitudes": [{"phase": 1, "attitude": "早"}, {"phase": 2, "attitude": "中"},
                             {"phase": 3, "attitude": "晚"}]},
        # 之后才认识：earliest phase = 3 > k
        {"target": "丙", "relation": "敌", "attitude": "恨",
         "phase_attitudes": [{"phase": 3, "attitude": "恨"}]},
        # 旧卡：没有 phase_attitudes
        {"target": "丁", "relation": "亲", "attitude": "疼爱"},
    ]


def test_u17_mid_phase_takes_latest_upto_k():
    card, _ = av.project_card(_card(relationships=_rels()), 2)
    by = {r.target: r for r in card.relationships}
    assert by["乙"].attitude == "中"       # ≤2 的最新
    assert "丙" not in by                   # 之后才认识 → 去掉
    assert by["丁"].attitude == "疼爱"      # 旧卡原样


def test_u17_last_phase_takes_phase_attitude():
    # 本段 DA5 改了①：口径一律取 ≤k 最新一条，k=n 也取阶段 n 的（不再回落顶层）。
    card, _ = av.project_card(_card(relationships=_rels()), 3)
    by = {r.target: r for r in card.relationships}
    assert by["乙"].attitude == "晚"
    assert "丙" in by


def test_u17_no_phase_attitudes_kept_at_mid_phase():
    rels = [{"target": "丁", "relation": "亲", "attitude": "疼爱"}]
    card, _ = av.project_card(_card(relationships=rels), 1)
    assert card.relationships[0].attitude == "疼爱"


# ── U18 台词投影 + phase_of 边界 ───────────────────────────────
def test_u18_dialogues_phase_k_first_then_top():
    # 本段 DA15 把对白示例归状态类：阶段 k 特有在前 + 顶层（不再累加 1..k；B3 顺序契约）。
    card, _ = av.project_card(_card(), 2)
    assert card.dialogue_examples == ["d2", "dt"]


def test_u18_phase_of_boundary_is_later_phase():
    card = _card(starts=(0, 100, 200))
    assert av.phase_of(card, 99) == 1
    assert av.phase_of(card, 100) == 2   # 落在起点 → 归后一阶段
    assert av.phase_of(card, 200) == 3
    assert av.phase_of(card, 300) == 3


# ── U19 边界示范 ───────────────────────────────────────────────
def test_u19_mid_phase_only_own_boundary_examples():
    card, view = av.project_card(_card(), 2)
    asks = [b.ask for b in view.boundary_examples]
    assert asks == ["问2"]                 # 只有阶段 2 的
    assert view.boundary is True


def test_u19_last_phase_no_boundary_examples():
    _, view = av.project_card(_card(), 3)
    assert view.boundary_examples == []
    assert view.boundary is False


def test_u19_old_card_without_examples_has_none():
    _, view = av.project_card(_card(boundaries=((), (), ())), 2)
    assert view.boundary_examples == []


# ── U19b 开场白 ────────────────────────────────────────────────
def test_u19b_mid_phase_clears_first_message():
    card, _ = av.project_card(_card(), 2)
    assert card.first_message == ""


def test_u19b_last_phase_keeps_first_message():
    card, _ = av.project_card(_card(), 3)
    assert card.first_message == "开场白"
