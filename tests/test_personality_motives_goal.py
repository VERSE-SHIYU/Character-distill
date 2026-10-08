# -*- coding: utf-8 -*-
"""③ 段 3 目标检查：动机进聊天 prompt（「## 想要什么」）。

目标：洗白主要发生在动机上（设计稿 §4a，依据 2601.04716）。段 2 让蒸馏产出了按阶段的动机，
段 3 让它真的进到聊天模型看到的 system prompt：选了阶段 k，模型看到的是「阶段 k 的那个人想要
什么」，看不到别的阶段才有的动机，也看不到没定位的动机。

走真实代码：`card_from_draft` → `ChatEngine` 发出的 system prompt。覆盖三条构建 prompt 的入口：
一对一聊天（`chat`）、群聊（`group_session` 调的 `_compose_context`）、主动消息
（`chat_engine.py` 主动消息那里直接调的 `ContextEngine.build`）。只换 LLM，桩只记下 system prompt。

预言独立于被测代码：句子、标题、阶段都是本文件里的字面量。

M1 选阶段 k：「## 想要什么」出现，每条一行；阶段 k 的在前、全程的在后；别的阶段才有的不出现。
M2 位置：在「## 行为模式」「## 遇事的做法」之后，「## 语言风格」之前。
M3 群聊（`_compose_context`）、主动消息（`ContextEngine.build`）两个入口同样带它。
M4 没有动机 → 整块不出现（main 上就绿：回归守卫）。
M5 只进了未定位区的动机不进 prompt（main 上就绿：回归守卫）。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_draft import card_from_draft
from core.chat_engine import ChatEngine

_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q1, _Q2 = "开头甲甲甲", "中间乙乙乙"
_NOWHERE = "原文里根本没有的一句"

_TITLE = "## 想要什么"
_M_ALL = "想要被人敬重；为此不惜硬撑体面"
_M_P1 = "想要考上秀才；为此不惜省下酒钱买书"
_M_P2 = "想要保住最后一点脸面；为此不惜撒谎"
_M_LOOSE = "想要一件没有出处的东西；为此不惜一切"
_BEHAVIOR = "排出九文大钱"


def _timed(value, *phases, quote=None):
    return {"value": value,
            "occurrences": [{"phase": p, "quote": quote or {1: _Q1, 2: _Q2}[p]} for p in phases]}


def _card(motives=None, behaviors=True):
    draft = {
        "name": "孔乙己",
        "identity": "落魄读书人",
        "character_arc": {"axis": "从死要面子到不再分辩", "phases": [
            {"label": "死要面子", "state": "断腿之前", "anchor": ""},
            {"label": "不再分辩", "state": "断腿之后", "anchor": _Q2},
        ]},
        "motives": [_timed(_M_ALL, 1, 2), _timed(_M_P1, 1), _timed(_M_P2, 2)] if motives is None else motives,
        "situation_behaviors": [{"situation": "讨酒", "behavior": _BEHAVIOR,
                                 "occurrences": [{"phase": 1, "quote": _Q1}, {"phase": 2, "quote": _Q2}]}]
        if behaviors else [],
    }
    return card_from_draft(draft, _SRC)


class _RecLLM:
    model = "stub"

    def __init__(self):
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.prompts: list[str] = []

    def preflight(self):
        return None

    def chat(self, system_prompt, messages, **_kw):
        if system_prompt.startswith("你是精确的JSON输出器"):
            return json.dumps({"affinity_event": "neutral", "affinity_tier": "small",
                               "affinity_delta": 1}, ensure_ascii=False)
        self.prompts.append(system_prompt)
        return "回复"


def _engine(card, k):
    llm = _RecLLM()
    eng = ChatEngine(llm=llm, rag=None, card=card, card_id="c", storage=None,
                     session_id="", is_new_session=False, arc_phase=k)
    eng.load_affinity({"affinity": 40, "trust": 30, "mood": "平静", "guard": 70,
                       "reason": "", "inner_voice": ""}, initialized=True)
    return eng, llm


def _chat_prompt(card, k) -> str:
    eng, llm = _engine(card, k)
    eng.chat("你好")
    return llm.prompts[0]


def _section(prompt: str) -> str:
    assert _TITLE in prompt, "prompt 里没有「## 想要什么」"
    return prompt.split(_TITLE, 1)[1].split("\n## ", 1)[0]


def test_m0_fixture_sentences_are_distinct():
    lines = [_M_ALL, _M_P1, _M_P2, _M_LOOSE, _BEHAVIOR]
    assert len(set(lines)) == len(lines)
    assert not any(a != b and a in b for a in lines for b in lines)


@pytest.mark.parametrize("k, mine, other", [(1, _M_P1, _M_P2), (2, _M_P2, _M_P1)])
def test_m1_motives_of_the_chosen_phase_reach_the_chat_prompt(k, mine, other):
    sec = _section(_chat_prompt(_card(), k))
    assert f"- {mine}\n" in sec and f"- {_M_ALL}\n" in sec, f"阶段 {k}：缺本阶段或全程的动机（每条一行）"
    assert sec.index(mine) < sec.index(_M_ALL), "阶段特有的动机应排在全程的前面"
    assert other not in sec, f"阶段 {k}：出现了别的阶段才有的动机"


def test_m2_section_sits_after_behaviors_before_speaking_style():
    sp = _chat_prompt(_card(), 2)
    assert sp.index("## 行为模式") < sp.index("## 遇事的做法") < sp.index(_TITLE) < sp.index("## 语言风格")
    sp = _chat_prompt(_card(behaviors=False), 2)
    assert "## 遇事的做法" not in sp
    assert sp.index("## 行为模式") < sp.index(_TITLE) < sp.index("## 语言风格")


def test_m3_group_and_proactive_entries_carry_it():
    eng, _ = _engine(_card(), 2)
    group = eng._compose_context("")                                   # group_session.py 的入口
    proactive = eng._ctx_engine.build("", eng.user_role, current_mood="平静")   # 主动消息的入口
    for name, prompt in (("群聊", group), ("主动消息", proactive)):
        sec = _section(prompt)
        assert _M_P2 in sec and _M_ALL in sec and _M_P1 not in sec, f"{name}入口的动机不对"


def test_m4_no_motives_no_section():
    assert _TITLE not in _chat_prompt(_card(motives=[]), 2)


def test_m5_unlocated_motive_stays_out_of_the_prompt():
    card = _card(motives=[_timed(_M_LOOSE, 2, quote=_NOWHERE)])
    assert card.motives == [] and _M_LOOSE in json.dumps(
        card.character_arc.unlocated.overlay, ensure_ascii=False), "夹具前提不成立：动机没进未定位区"
    assert _M_LOOSE not in _chat_prompt(card, 2)
