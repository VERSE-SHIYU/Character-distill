# -*- coding: utf-8 -*-
"""蒸馏遗留④ 目标检查：经历类条目标在「故事开始前」（phase 0）时不再被当成编号非法丢掉。

现状（main d07bfff0）：`_valid_number_rows` 只收 1..n 的编号，0 一律撤回 + warning。于是
「童年、身世」这类故事开始之前就发生的关键记忆，被模型标成 phase 0 时整条丢掉 —— 顶层
`key_memories` 里永远看不到。

目标：经历类认 0 = 故事开始前，进顶层、不做位置核对、不 warning，只留一行 info 便于人工
翻看有没有误标；非经历类（做法、态度）标 0 照旧撤回。0 与阶段号并存时忽略 0、其余照常核对
（不新增泄露面）。发给模型的草稿结构一个字节都不变。

走真实代码：`card_from_draft`、`dispatch`、`format_prompt_after`。预言独立于被测代码：
句子、阶段号是本文件的字面量。

P1 只标 0 的记忆 → 顶层 key_memories，不挂任何阶段（选任一阶段都看得到）
P2 只标 0 的记忆 → 不打「阶段编号不合法」warning
P3 只标 0 的记忆 → 一行 info 日志写明「故事开始前」+ 条目文字
P4 标 0+2、摘录落在阶段 2 → 忽略 0 照常核对：挂阶段 2，不进顶层，也不 warning
P5 标 0 的做法 → 仍按编号非法撤回（只有经历类认 0）
P6 标 0 的态度 → 仍按编号非法撤回（只有经历类认 0）
P7 提示词：维度 E 与 G6 重要事项都写明「故事开始前填 0」的口径
P8 标 0 之外还带越界号（[0, 9]，共 2 个阶段）→ 越界号照旧 warning，整条仍按故事前进顶层
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_draft import card_from_draft

_SRC = "开头甲甲甲。中间乙乙乙。"
_Q1, _Q2 = "开头甲甲甲", "中间乙乙乙"
_PRE = "童年时在集市上走丢过"
_SHOWN = "被问起时排出九文大钱"


def _phased(**extra):
    d = {"name": "孔乙己", "character_arc": {"phases": [
        {"label": "一", "state": "s1", "anchor": ""},
        {"label": "二", "state": "s2", "anchor": _Q2}]}}
    d.update(extra)
    return d


def _mounted_memories(card):
    return [m for p in card.character_arc.phases
            for m in (p.overlay or {}).get("key_memories", [])]


def test_p0_fixture_sentences_are_distinct():
    assert _PRE != _SHOWN


def test_p1_memory_tagged_zero_only_lands_at_top_level():
    card = card_from_draft(_phased(key_memories=[
        {"memory": _PRE, "occurrences": [{"phase": 0, "quote": _Q1}]}]), _SRC)
    assert _PRE in card.key_memories, "只标 0 的经历类记忆没进顶层（故事里的经历要一直在场）"
    assert _PRE not in _mounted_memories(card), "故事前的记忆不该挂到某个阶段"


def test_p2_memory_tagged_zero_only_is_not_warned(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card_from_draft(_phased(key_memories=[
            {"memory": _PRE, "occurrences": [{"phase": 0, "quote": _Q1}]}]), _SRC)
    assert not any("阶段编号不合法" in r.getMessage() for r in caplog.records), \
        "故事前的经历被打成了编号非法"


def test_p3_memory_tagged_zero_only_is_logged_as_pre_story(caplog):
    with caplog.at_level(logging.INFO, logger="core.card_draft"):
        card_from_draft(_phased(key_memories=[
            {"memory": _PRE, "occurrences": [{"phase": 0, "quote": _Q1}]}]), _SRC)
    assert any("故事开始前" in r.getMessage() and _PRE in r.getMessage()
               for r in caplog.records), "没有 info 日志写明「故事开始前」+ 条目文字"


def test_p4_zero_coexisting_with_phase_two_hangs_phase_two_not_top(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card = card_from_draft(_phased(key_memories=[
            {"memory": _PRE, "occurrences": [{"phase": 0, "quote": _Q1},
                                              {"phase": 2, "quote": _Q2}]}]), _SRC)
    assert _PRE not in card.key_memories, "0 与阶段号并存时不该进顶层（不新增泄露面）"
    assert _PRE in _mounted_memories(card), "0 与阶段号并存时没按阶段号照常核对挂上"
    assert not any("阶段编号不合法" in r.getMessage() for r in caplog.records), \
        "0 与阶段号并存时忽略 0，不该 warning"


def test_p4b_zero_coexisting_still_goes_through_position_check():
    """0 与阶段号并存时其余编号照常走 `verify`：摘录落在别的阶段就改挂过去，不进顶层。"""
    card = card_from_draft(_phased(key_memories=[
        {"memory": _PRE, "occurrences": [{"phase": 0, "quote": _Q1},
                                          {"phase": 1, "quote": _Q2}]}]), _SRC)
    assert _PRE not in card.key_memories, "并存时不该进顶层"
    phase2 = card.character_arc.phases[1]
    assert _PRE in (phase2.overlay or {}).get("key_memories", []), \
        "标阶段 1、摘录落在阶段 2，应改挂到阶段 2（位置核对照常跑）"


def test_p5_behavior_tagged_zero_is_still_retracted(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card = card_from_draft(_phased(situation_behaviors=[
            {"situation": "讨酒", "behavior": _SHOWN,
             "occurrences": [{"phase": 0, "quote": _Q1}]}]), _SRC)
    top = [b.behavior for b in card.situation_behaviors]
    per = [b.behavior for p in card.character_arc.phases for b in p.behaviors]
    assert _SHOWN not in top + per, "标 0 的做法该撤回（只有经历类认 0）"
    assert any("阶段编号不合法" in r.getMessage() for r in caplog.records), \
        "标 0 的做法该打编号非法 warning"


def test_p6_attitude_tagged_zero_is_still_retracted(caplog):
    rel = {"target": "掌柜", "relation": "主顾",
           "attitudes": [{"attitude": _SHOWN, "quote": _Q1}]}
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card = card_from_draft(_phased(relationships=[rel]), _SRC)
    atts = [pa.attitude for r in card.relationships for pa in r.phase_attitudes]
    assert _SHOWN not in atts, "标 0 的态度该撤回（只有经历类认 0）"


def test_p7_prompt_states_pre_story_policy_in_e_and_g6():
    from core.distiller import format_prompt_after
    g6 = format_prompt_after("G6")
    assert ("故事开始之前就发生的经历（如童年、身世），phase 填 0；"
            "故事里发生的，用维度 L 的阶段编号。") in g6, "维度 E 没写「故事开始前填 0」"
    assert "关键记忆发生在故事开始之前的填 0；" in g6, "G6 重要事项没写「故事开始前填 0」"


def test_p8_zero_plus_out_of_range_still_warns_and_lands_at_top_level(caplog):
    """0 之外还带了越界编号（[0, 9]，共 2 个阶段）：越界号照旧 warning，整条仍按故事前进顶层。"""
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card = card_from_draft(_phased(key_memories=[
            {"memory": _PRE, "occurrences": [{"phase": 0, "quote": _Q1},
                                              {"phase": 9, "quote": _Q2}]}]), _SRC)
    assert _PRE in card.key_memories, "0 + 越界号的故事前记忆仍该进顶层"
    assert _PRE not in _mounted_memories(card), "故事前的记忆不该挂到某个阶段"
    assert any("阶段编号不合法" in r.getMessage() for r in caplog.records), \
        "0 之外的越界编号该照旧 warning"
