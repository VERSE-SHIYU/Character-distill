# -*- coding: utf-8 -*-
"""D7-a 目标检查：原文自带「……」的句子照抄成锚点，必须定位得上；模型自己标的删减照旧从严。

单开文件，不并进 `tests/test_phase_anchoring.py`（那是变异驱动的覆盖域，见
`tests/test_phase_anchoring_elided.py` 文件头的说明）。
"""
from __future__ import annotations

from core import phase_anchoring
from core import quotes
from core.quotes import locate_in_normalized, normalize, paragraph_bounds

# 段 1 普通叙述；段 2 含一句原文自带省略号、省略号后只剩 3 字的心声（谢凛羽阶段 2 锚点同形）
_SRC = ("开头甲甲甲甲甲甲甲甲甲甲，中间乙乙乙乙乙乙乙乙乙乙。\n"
        "他想了很久：我到底是讨厌她，还是……喜欢她？后来丙丙丙丙丙丙丙丙丙丙。")
_NATIVE = "我到底是讨厌她，还是……喜欢她？"


def _at(src: str, quote: str) -> list[int] | None:
    norm = normalize(src)
    return quotes.locate_anchor(src, norm, quote, paragraph_bounds(src, norm))


def test_g1_native_ellipsis_sentence_is_located():
    """D7-a：原文自带省略号、后段不足 10 字 —— 逐字档命中，位置 = 规范化坐标。"""
    raw = _SRC.index(_NATIVE)
    assert _at(_SRC, _NATIVE) == [len(normalize(_SRC[:raw]))]


def test_g2_card_with_native_ellipsis_anchor_keeps_phase_starts():
    """整卡口径：阶段 2 用这句当锚点，不再整卡跳过，阶段 2 从这句开始。"""
    phases = [{"label": "L1", "state": "S1", "anchor": ""},
              {"label": "L2", "state": "S2", "anchor": _NATIVE}]
    ph = phase_anchoring.build_anchors(phases, _SRC)
    start = len(normalize(_SRC[:_SRC.index(_NATIVE)]))
    assert ph.reason == ""
    assert ph.ranges == [(0, start), (start, len(normalize(_SRC)))]


def test_g3_model_elision_still_needs_two_long_fragments():
    """不放宽：原文里没有的「长……短」删减，照旧按切段核对（短段丢掉后不足 2 段 → None）。"""
    assert _at(_SRC, "开头甲甲甲甲甲甲甲甲甲甲……乙乙") is None


def test_g4_model_elision_with_two_long_fragments_still_passes():
    """不过严：模型标出的合规删减（两段各 ≥10 字、同段按序）照旧通过，起点取首段。"""
    q = "开头甲甲甲甲甲甲甲甲甲甲……中间乙乙乙乙乙乙乙乙乙乙"
    assert _at(_SRC, q) == [0]


def test_g5_anchor_without_ellipsis_is_unchanged():
    """不含省略号的锚点与改前同一条路（规范化整句），结果逐个相等。"""
    norm = normalize(_SRC)
    for q in ("中间乙乙乙乙乙乙乙乙乙乙", "后来丙丙丙丙丙丙丙丙丙丙", "原文里没有的一句话"):
        assert quotes.locate_anchor(_SRC, norm, q) == locate_in_normalized(norm, q)


def test_g6_marked_ellipsis_over_contiguous_text_is_not_whole_string_matched():
    """不放宽：标了省略号、原文其实连着（中间只隔标点）的锚点，不能因为规范化删掉「…」和标点
    就整串命中 —— 含省略号的锚点不走规范化整句档（spec §3 设计决定 2），照旧按切段核对：
    后段「中间」不足 10 字被丢、只剩 1 段 → None。"""
    assert _at(_SRC, "开头甲甲甲甲甲甲甲甲甲甲……中间") is None
