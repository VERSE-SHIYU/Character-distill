# -*- coding: utf-8 -*-
"""E1 第 1 段目标检查：状态类字段里「一直成立」的那条标 phase 0 → 卡片顶层（全程）。

问题（调查报告 E1 行）：状态类字段要求每个阶段各给一段该阶段里的原文摘录，从头到尾都成立时
标全部阶段。阶段 1 很短的书里，底色条目找不到落在阶段 1 的原文，被改挂到后面的阶段或被整条
去掉 —— 凑不齐「全部阶段」就进不了顶层，选哪个阶段都看不到。

目标：状态类与经历类共用规则 0 —— 只标 0 = 一直成立，进卡片顶层、不做位置核对；0 与阶段号
并存时忽略 0（同经历类 P4）。情境→做法、关系态度标 0 照旧撤回。

走真实代码：`card_from_draft` → `project_card` / `format_prompt_after`。预言用本文件的字面量。

Z1 性格只标 0、摘录落在阶段 3 → 进顶层，不挂阶段格，阶段 1 投影看得到
Z2 同 Z1、摘录查不到 → 同 Z1（0 不做位置核对）
Z3 只标 0 → 不打「阶段编号不合法」warning，打一行 info「全程：<取值>」
Z4 后来才有的「唤她小乖」标 2、3 → 阶段 2、3 有，阶段 1 投影没有（回归守卫）
Z5 单值语气「冷淡」标 0、「温软」标 3 → 阶段 1、2 投影冷淡，阶段 3 温软
Z6 口癖（原话类）只标 0 → 进顶层
Z7 状态类标 0 + 2、摘录在阶段 2 → 忽略 0、挂阶段 2，不进顶层，不 warning
Z8 提示词：G2、G3 与口癖段都带 `_BASELINE_ZERO_RULE` 整句，旧句消失
"""

from __future__ import annotations

import logging
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.arc_view import project_card
from core.card_draft import card_from_draft
from core.retro_regions import NOT_DETECTED
from core.card_layers import get_path

_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q = {1: "开头甲甲甲", 2: "中间乙乙乙", 3: "结尾丙丙丙"}

_BASELINE = "沉默寡言，心里有数"
_MISSING_QUOTE = "这句摘录原文里没有"
_LATER = "唤她小乖"
_COLD = "冷淡"
_WARM = "温软"

_BASELINE_ZERO_SENTENCE = (
    "从头到尾一直如此的（此人的底色），只写一条，phase 填 0；"
    "故事里因某人某事才出现或改变的（如对某人的称呼），不填 0，标出它确实成立的阶段。"
)


def _timed(value, *phases, quote=None):
    return {"value": value,
            "occurrences": [{"phase": p,
                             "quote": quote if quote is not None else _Q[p]}
                            for p in phases]}


def _draft(**extra):
    d = {"name": "祈灼",
         "character_arc": {"phases": [
             {"label": "一", "state": "s1", "anchor": ""},
             {"label": "二", "state": "s2", "anchor": _Q[2]},
             {"label": "三", "state": "s3", "anchor": _Q[3]},
         ]}}
    d.update(extra)
    return d


def _mounted(card, path):
    return [get_path(p.overlay or {}, path) for p in card.character_arc.phases]


def _all_mounted(card, path):
    out = []
    for value in _mounted(card, path):
        if isinstance(value, list):
            out.extend(value)
        elif value:
            out.append(value)
    return out


def test_z1_state_zero_only_lands_at_top_and_is_visible_from_phase_one():
    card = card_from_draft(_draft(personality_traits=[
        _timed(_BASELINE, 0, quote=_Q[3])]), _SRC, retro=NOT_DETECTED)
    assert card.personality_traits == [_BASELINE], "只标 0 的性格没进顶层"
    assert _BASELINE not in _all_mounted(card, "personality_traits"), \
        "一直成立的底色不该挂到某个阶段格"
    proj, _ = project_card(card, 1)
    assert _BASELINE in proj.personality_traits, "阶段 1 投影看不到一直成立的底色"


def test_z2_zero_skips_position_check():
    card = card_from_draft(_draft(personality_traits=[
        _timed(_BASELINE, 0, quote=_MISSING_QUOTE)]), _SRC, retro=NOT_DETECTED)
    assert card.personality_traits == [_BASELINE], "0 不做位置核对，查不到摘录也该进顶层"
    assert _BASELINE not in _all_mounted(card, "personality_traits")
    proj, _ = project_card(card, 1)
    assert _BASELINE in proj.personality_traits


def test_z3_zero_only_is_not_warned_and_logs_the_value(caplog):
    with caplog.at_level(logging.INFO, logger="core.card_draft"):
        card_from_draft(_draft(personality_traits=[
            _timed(_BASELINE, 0, quote=_Q[1])]), _SRC, retro=NOT_DETECTED)
    assert not any("阶段编号不合法" in r.getMessage() for r in caplog.records), \
        "只标 0 的状态类条目被打成了编号非法"
    assert any("全程：" in r.getMessage() and _BASELINE in r.getMessage()
               for r in caplog.records), "没有 info 日志写明「全程：」+ 取值"


def test_z4_later_entry_does_not_leak_into_phase_one():
    card = card_from_draft(_draft(speaking_style={
        "catchphrases": [_timed(_LATER, 2, 3)]}), _SRC, retro=NOT_DETECTED)
    mounted = _mounted(card, "speaking_style.catchphrases")
    assert _LATER in mounted[1] and _LATER in mounted[2], "标 2、3 的口癖该挂阶段 2、3"
    assert _LATER not in (mounted[0] or []), "标 2、3 的口癖不该挂阶段 1"
    proj, _ = project_card(card, 1)
    assert _LATER not in proj.speaking_style.catchphrases, "后来的口癖泄露到了阶段 1"


def test_z5_zero_baseline_scalar_applies_to_earlier_phases():
    card = card_from_draft(_draft(speaking_style={
        "tone": [_timed(_COLD, 0, quote=_Q[1]), _timed(_WARM, 3)]}), _SRC, retro=NOT_DETECTED)
    assert card.speaking_style.tone == _COLD, "0 的基线语气没进顶层"
    assert [project_card(card, k)[0].speaking_style.tone for k in (1, 2, 3)] == \
        [_COLD, _COLD, _WARM], "单值基线/阶段值的投影不符合预期"


def test_z6_zero_only_verbatim_field_lands_at_top():
    card = card_from_draft(_draft(speaking_style={
        "catchphrases": [_timed("哼", 0, quote=_Q[1])]}), _SRC, retro=NOT_DETECTED)
    assert card.speaking_style.catchphrases == ["哼"], "只标 0 的口癖没进顶层"
    assert "哼" not in _all_mounted(card, "speaking_style.catchphrases")


def test_z7_zero_coexisting_with_phase_is_ignored(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card = card_from_draft(_draft(personality_traits=[{
            "value": _BASELINE,
            "occurrences": [{"phase": 0, "quote": _Q[1]},
                            {"phase": 2, "quote": _Q[2]}]}]), _SRC, retro=NOT_DETECTED)
    assert _BASELINE not in card.personality_traits, "0 与阶段号并存时不该进顶层"
    assert _BASELINE in _mounted(card, "personality_traits")[1], \
        "0 与阶段号并存时应忽略 0、照常挂到阶段 2"
    assert not any("阶段编号不合法" in r.getMessage() for r in caplog.records), \
        "0 与阶段号并存时忽略 0，不该 warning"


def test_z8_prompt_carries_the_baseline_zero_rule():
    from core.distiller import format_prompt_after

    g2 = format_prompt_after("G2")
    g3 = format_prompt_after("G3")
    full = format_prompt_after()

    # 维度 B 那一段守 `_PHASE_DESCRIPTION_RULE`；截到下一个维度标号为止，别让别处的同句兜底。
    b_match = re.search(r"B\. 核心性格.*?(?=\n[A-Z]\. )", g2, re.S)
    assert b_match, "提示词里找不到维度 B 那一段"
    assert _BASELINE_ZERO_SENTENCE in b_match.group(0), \
        "维度 B 那一段没带「一直如此写 0」的整句（描述类规则）"

    # 「重要：」那一段守 `_FORMAT_IMPORTANCE`；只看以对象字段说明开头的那一行。
    importance_lines = [line for line in g2.splitlines()
                        if line.startswith('- 写成 [{"value": …')]
    assert importance_lines, "提示词里找不到「重要：」里的对象字段那一行"
    assert _BASELINE_ZERO_SENTENCE in importance_lines[0], \
        "「重要：」那一段没带「一直如此写 0」的整句"

    # 口癖那段守 `_PHASE_VERBATIM_RULE`。
    catchphrase_part = g3.split("口癖按阶段给，", 1)[1].split("用词水平是稳定项", 1)[0]
    assert _BASELINE_ZERO_SENTENCE in catchphrase_part, "口癖那段没带「一直如此写 0」的整句"

    assert "从头到尾都成立时标全部阶段" not in full, "旧句「标全部阶段」还在"
    assert "全阶段都成立时把每个阶段都标上" not in full, "旧句「把每个阶段都标上」还在"
