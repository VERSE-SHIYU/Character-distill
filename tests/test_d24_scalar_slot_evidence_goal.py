# -*- coding: utf-8 -*-
"""目标检查（D24）：单值字段每格按证据留对的那一条，落选进未定位区。

规则（本文件唯一权威）：
- 留谁：三档，高档胜，同档取行序第一条。
  ① 原文在本阶段的现世位置（回忆区外）；
  ② 模型标了本阶段、但没有现世位置证据（原文只在回忆区，或整卡跳过位置检查）；
  ③ 从别的阶段按位置挪进来的。
- 落选去哪：不挪去别的阶段。已在别的格子赢下 → 不处理；否则状态类、有阶段的卡 →
  进未定位区（备份、不进聊天）；其余（无阶段的卡、经历类 `cognitive.knowledge_scope`）→
  丢弃并打 WARNING 写明原因。

不变量：单值字段的每一条取值转卡后**要么**在顶层或某阶段，**要么**在未定位区，
**要么**有一条写明原因的「丢弃」WARNING；没有任何取值被放进它既没被标注、也没有位置证据的阶段。

甲 = 无回忆区（PA._SRC）；乙 = 有回忆区（第 4 章「日札」是回忆区，落在阶段 3 范围内）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import phase_anchoring
from core.card_draft import DraftBehavior, card_from_draft, dispatch
from core.card_layers import get_path, set_path
from core.retro_regions import NOT_DETECTED, ChapterRange, RetroRegions

import test_phase_anchoring as PA

_XFAIL = pytest.mark.xfail(strict=True, reason="D24 目标检查：实现前应红")

# 甲：无回忆区，PA._phases(3, {2: PA._Q2, 3: PA._Q3})
_JIA_ANCHORS = {2: PA._Q2, 3: PA._Q3}

# 乙：第 4 章是回忆区，三阶段锚点
_YI_SRC = ("第1章\n开头甲甲甲甲甲。旧事丁丁丁丁丁。\n"
           "第2章\n中间乙乙乙乙乙。\n"
           "第3章\n结尾丙丙丙丙丙。\n"
           "第4章\n【日札】回忆戊戊戊戊戊。旧事丁丁丁丁丁。中间己己己己己。\n")
_YI_RETRO = RetroRegions("marked", (ChapterRange(4, 4, "日记"),))
_YI_ANCHORS = {1: "开头甲甲甲甲甲", 2: "中间乙乙乙乙乙", 3: "结尾丙丙丙丙丙"}


def _v(value, phase, quote):
    return {"value": value, "occurrences": [{"phase": phase, "quote": quote}]}


def _card(values, *, field="decision_style", src=PA._SRC, retro=NOT_DETECTED,
          anchors=None, n=3):
    draft = {"name": "角色",
             "character_arc": {"axis": "a",
                               "phases": PA._phases(n, _JIA_ANCHORS if anchors is None else anchors)}}
    set_path(draft, field, values)
    return card_from_draft(draft, src, retro=retro)


def _collision_logs(caplog):
    return [r.getMessage() for r in caplog.records if "有多条取值" in r.getMessage()]


def _phase_value(card, path, index):
    return get_path(card.character_arc.phases[index].overlay, path)


def _loose_value(card, path):
    return get_path(card.character_arc.unlocated.overlay, path)


# ── E1 挪进来的落选 ────────────────────────────────────────────────────
@_XFAIL
def test_e1_rehung_loser_goes_to_unlocated(caplog):
    """B 标阶段 3、原文在阶段 2：A（原标本阶段）赢，B（改挂进来）进未定位区。"""
    with caplog.at_level(logging.WARNING):
        card = _card([_v("A", 2, PA._Q2), _v("B", 3, PA._Q2)])
    assert (_phase_value(card, "decision_style", 1),
            _phase_value(card, "decision_style", 2),
            _loose_value(card, "decision_style"),
            _collision_logs(caplog)) == (
        "A", None, ["B"],
        ["[card_draft] decision_style 的阶段 2 有多条取值，保留「A」（原标此阶段），"
         "「B」（由阶段 3 改挂进来）进未定位区"])


# ── E2 现世胜回忆区 ────────────────────────────────────────────────────
@_XFAIL
def test_e2_present_day_beats_retro_only(caplog):
    """两条都标阶段 3：A 的摘录只在回忆区，B 有阶段 3 现世位置 → B 赢，A 进未定位区。"""
    with caplog.at_level(logging.WARNING):
        card = _card([_v("A", 3, "回忆戊戊戊戊戊"), _v("B", 3, "结尾丙丙丙丙丙")],
                     src=_YI_SRC, retro=_YI_RETRO, anchors=_YI_ANCHORS)
    assert (_phase_value(card, "decision_style", 2),
            _loose_value(card, "decision_style"),
            _collision_logs(caplog)) == (
        "B", ["A"],
        ["[card_draft] decision_style 的阶段 3 有多条取值，保留「B」（原标此阶段），"
         "「A」（原标此阶段，原文只在回忆区）进未定位区"])


# ── E3 同档取第一条（对照：改前就绿）──────────────────────────────────
def test_e3_same_tier_keeps_the_first():
    card = _card([_v("A", 2, PA._Q2), _v("B", 2, PA._Q2)])
    assert _phase_value(card, "decision_style", 1) == "A"


@_XFAIL
def test_e3b_same_tier_loser_goes_to_unlocated():
    card = _card([_v("A", 2, PA._Q2), _v("B", 2, PA._Q2)])
    assert _loose_value(card, "decision_style") == ["B"]


# ── E4 已在别处赢（对照）──────────────────────────────────────────────
def _won_elsewhere_card():
    return _card([
        _v("A", 2, PA._Q2),
        {"value": "B", "occurrences": [{"phase": 1, "quote": PA._Q1},
                                       {"phase": 2, "quote": PA._Q2}]},
    ])


def test_e4_loser_that_won_another_phase_is_not_unlocated():
    card = _won_elsewhere_card()
    assert (_phase_value(card, "decision_style", 0),
            _phase_value(card, "decision_style", 1),
            _loose_value(card, "decision_style")) == ("B", "A", None)


@_XFAIL
def test_e4b_loser_that_won_elsewhere_is_named_in_log(caplog):
    with caplog.at_level(logging.WARNING):
        _won_elsewhere_card()
    assert _collision_logs(caplog) == [
        "[card_draft] decision_style 的阶段 2 有多条取值，保留「A」（原标此阶段），"
        "「B」（原标此阶段）已在阶段 1 保留"]


# ── E5 经历类（对照）──────────────────────────────────────────────────
def test_e5_experience_scalar_loser_is_not_unlocated():
    card = _card([_v("A", 2, PA._Q2), _v("B", 2, PA._Q2)],
                 field="cognitive.knowledge_scope")
    assert (_phase_value(card, "cognitive.knowledge_scope", 1),
            _loose_value(card, "cognitive.knowledge_scope")) == ("A", None)


@_XFAIL
def test_e5b_experience_scalar_loser_is_dropped_with_reason(caplog):
    with caplog.at_level(logging.WARNING):
        _card([_v("A", 2, PA._Q2), _v("B", 2, PA._Q2)],
              field="cognitive.knowledge_scope")
    assert _collision_logs(caplog) == [
        "[card_draft] cognitive.knowledge_scope 的阶段 2 有多条取值，保留「A」（原标此阶段），"
        "丢弃「B」（原标此阶段）（经历类不进未定位区）"]


# ── E6 无阶段的卡（对照）──────────────────────────────────────────────
def test_e6_card_without_phases_drops_the_loser():
    assert dispatch([[1, 2], [1, 2]], 0, kind="scalar",
                    tagged=lambda i: {0}) == ([0], [], [])


# ── E7 整卡跳过 ────────────────────────────────────────────────────────
@_XFAIL
def test_e7_skipped_card_loser_goes_to_unlocated():
    card = _card([_v("A", 3, PA._Q1), _v("B", 3, PA._Q1)],
                 anchors={2: "锚点查无此句", 3: PA._Q3})
    assert (_phase_value(card, "decision_style", 2),
            _loose_value(card, "decision_style")) == ("A", ["B"])


# ── E8 监测行 ──────────────────────────────────────────────────────────
@_XFAIL
def test_e8_monitoring_counts_rehung_unlocated_to_last(caplog):
    with caplog.at_level(logging.WARNING):
        _card([_v("A", 2, PA._Q2), _v("B", 3, PA._Q2)])
    line = [r.getMessage() for r in caplog.records if "[phase_anchoring]" in r.getMessage()][0]
    assert "字段:1/1/0" in line


# ── E9 事实层 ──────────────────────────────────────────────────────────
@_XFAIL
def test_e9_present_phases_records_only_present_day_evidence():
    anchors = phase_anchoring.build_anchors(
        PA._phases(3, _YI_ANCHORS), _YI_SRC, retro=_YI_RETRO.spans(_YI_SRC))
    items = [
        DraftBehavior(situation="s", behavior="b",
                      occurrences=[{"phase": 3, "quote": "回忆戊戊戊戊戊"}]),
        DraftBehavior(situation="s", behavior="b",
                      occurrences=[{"phase": 3, "quote": "结尾丙丙丙丙丙"}]),
    ]
    res = phase_anchoring.verify(items, [[3], [3]], anchors)
    assert (res.present_phases[0], res.present_phases[1]) == (set(), {3})


# ── E10 关系态度不变（对照）────────────────────────────────────────────
def test_e10_relationship_attitude_winner_is_unchanged():
    """关系态度不传新判据：同阶段撞车仍是行序第一条，即使排在前面的摘录只在回忆区。"""
    card = card_from_draft({
        "name": "角色",
        "character_arc": {"axis": "a", "phases": PA._phases(3, _YI_ANCHORS)},
        "relationships": [{"target": "甲", "relation": "友", "attitudes": [
            {"phase": 2, "attitude": "A", "quote": "回忆戊戊戊戊戊"},
            {"phase": 2, "attitude": "B", "quote": "中间乙乙乙乙乙"}]}],
    }, _YI_SRC, retro=_YI_RETRO)
    assert [(pa.phase, pa.attitude) for pa in card.relationships[0].phase_attitudes] == [(2, "A")]
