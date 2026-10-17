# -*- coding: utf-8 -*-
"""阶段 1 的范围一律从全书开头算起（它的锚点只参与顺序核对）。"""
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


def test_g4_order_check_still_uses_phase1_anchor():
    ph = phase_anchoring.build_anchors(_phases(_A2, _A1), _SRC)
    assert "不严格递增" in ph.reason
