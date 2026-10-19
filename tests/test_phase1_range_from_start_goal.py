# -*- coding: utf-8 -*-
"""阶段 1 的范围一律从全书开头算起；它的锚点不参与任何核对（10-19 起，spec `phase1-anchor-not-checked.md`）。"""
from __future__ import annotations

from core import phase_anchoring
from core.quotes import normalize

_SRC = ("开头甲甲甲甲甲甲甲甲甲甲。\n"
        "阶段一开场乙乙乙乙乙乙乙乙乙乙。\n"
        "阶段二开场丙丙丙丙丙丙丙丙丙丙。")
_A1 = "阶段一开场乙乙乙乙乙乙乙乙乙乙"
_A2 = "阶段二开场丙丙丙丙丙丙丙丙丙丙"


def _phases(a1, a2):
    return [{"label": "L1", "state": "S1", "anchor": a1},
            {"label": "L2", "state": "S2", "anchor": a2}]


def test_g1_phase1_range_starts_at_zero_even_with_anchor():
    ph = phase_anchoring.build_anchors(_phases(_A1, _A2), _SRC)
    assert ph.reason == ""
    assert ph.ranges[0][0] == 0


def test_g2_text_before_phase1_anchor_belongs_to_phase1():
    ph = phase_anchoring.build_anchors(_phases(_A1, _A2), _SRC)
    assert phase_anchoring.phase_at(ph.ranges, 0) == 1


def test_g3_phase2_still_starts_at_its_anchor():
    ph = phase_anchoring.build_anchors(_phases(_A1, _A2), _SRC)
    assert ph.ranges[1][0] == normalize(_SRC).index(normalize(_A2))


def test_g4_phase1_anchor_is_not_order_checked():
    """原 g4 断言「顺序核对仍用阶段 1 锚点」—— 10-19 起反过来：阶段 1 锚点不划区间，也不比顺序
    （祈灼重转 1989b374baea：它把第 122 章这个对的阶段 2 起点换掉了）。"""
    ph = phase_anchoring.build_anchors(_phases(_A2, _A1), _SRC)
    assert ph.reason == ""
