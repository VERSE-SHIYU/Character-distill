# -*- coding: utf-8 -*-
"""目标检查：阶段 1 的锚点不划区间，就不参与任何核对（spec `docs/specs/phase1-anchor-not-checked.md`）。

起因（祈灼草稿重转 10-19，task 1989b374baea）：阶段 1 锚点取自第 383 章的章内回忆（716034），
阶段 2 锚点「因为，我想当你的底牌。」在第 122 章（224224）、本来是对的；两者比顺序判不过 →
重新定位把阶段 2..n 全部重找 → 第 122 章被换成第 335 章。阶段 1 一律从 0 起（#176），它的锚点
在重新定位里也被当成不可信而置空（spec `phase-locate.md`），只有触发重新定位的核对还在用它。

目标：
1. 阶段 1 锚点比阶段 2 还靠后时，不跳过整卡，阶段 2 从它自己的锚点起；
3. 阶段 1 锚点查不到、或落在回忆区，都不影响核对；
4. 阶段 2..n 之间的顺序照旧核对。
（原目标 2「不触发重新定位」随重新定位一起删掉，2026-10-25，spec `phase-start-chapter.md`。）
"""
from __future__ import annotations

import copy

from core import phase_anchoring
from core.quotes import normalize
from core.retro_regions import ChapterRange, RetroRegions

# 替身书：第 1 章开头；第 2 章初遇；第 3 章过渡；第 4 章远行；第 5 章过渡；第 6 章是日札（回忆区）。
_SRC = ("第1章\n他在家里读书。日子平平淡淡。\n"
        "第2章\n那天他在桥边初遇阿月。他从此心动不已。\n"
        "第3章\n他常想起桥边的阿月。书院来信催他。\n"
        "第4章\n他终于决定远行求学。行囊收拾好了。\n"
        "第5章\n路上风雨兼程。他想念家乡。\n"
        "第6章\n【日札】那年在桥边初遇阿月，从此心动。后来我决定远行求学。\n")
_RETRO = RetroRegions("marked", (ChapterRange(6, 6, "日记"),))
_PHASES = [{"label": "平淡读书", "state": "在家读书", "anchor": ""},
           {"label": "初遇心动", "state": "在桥边初遇阿月之后心动", "anchor": "那年在桥边初遇阿月，从此心动"},
           {"label": "远行求学", "state": "决定远行求学之后", "anchor": "后来我决定远行求学"}]

# 阶段 2、3 用书里当下发生的句子（第 2、4 章）；阶段 1 用更靠后的第 5 章句子 —— 祈灼 10-18 的形状。
_GOOD = copy.deepcopy(_PHASES)
_GOOD[0]["anchor"] = "路上风雨兼程"
_GOOD[1]["anchor"] = "那天他在桥边初遇阿月"
_GOOD[2]["anchor"] = "他终于决定远行求学"


def _build(phases, retro=()):
    return phase_anchoring.build_anchors(phases, _SRC, retro=retro)


def test_g1_late_phase1_anchor_does_not_skip_the_card():
    ph = _build(_GOOD)
    assert (ph.reason, ph.ranges[1][0]) == ("", normalize(_SRC).index(normalize("那天他在桥边初遇阿月")))


def test_g3_unfindable_or_retro_phase1_anchor_is_ignored():
    missing = copy.deepcopy(_GOOD)
    missing[0]["anchor"] = "查无此句"
    retro = copy.deepcopy(_GOOD)
    retro[0]["anchor"] = "那年在桥边初遇阿月，从此心动"      # 第 6 章日札（回忆区）
    assert (_build(missing).reason, _build(retro, _RETRO.spans(_SRC)).reason) == ("", "")


def test_g4_order_among_phases_2_to_n_is_still_checked():
    rev = copy.deepcopy(_GOOD)
    rev[1]["anchor"], rev[2]["anchor"] = rev[2]["anchor"], rev[1]["anchor"]
    assert "不严格递增" in _build(rev).reason


def test_g5_phase_list_for_downstream_calls_omits_phase1_anchor():
    """依赖组与关系分批收到的阶段列表里，阶段 1 不带模型写的锚点原句（同一条规则，
    审计自查 10-19 发现：distiller._phase_list_note 曾把第 383 章回忆句当阶段 1 起始原句给下游）。"""
    from core.distiller import _phase_list_note
    note = _phase_list_note(_GOOD)
    assert ("路上风雨兼程" in note, "那天他在桥边初遇阿月" in note) == (False, True)
