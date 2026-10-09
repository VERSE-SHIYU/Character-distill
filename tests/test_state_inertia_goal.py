# -*- coding: utf-8 -*-
"""③ 验收后续 目标检查：状态类字段按「沿用最近一次」投影到阶段 k。

问题（演示卡重蒸验收 B 调查，2026-10-09）：状态类字段只挂在有原文证据的阶段；聊天默认取
最后阶段，最后阶段没有证据就是空 —— 5 张卡上三档关系做法 / 亲近条件 / 动机为空的格子，
全部是这一条路（值在较早阶段的 overlay 里）。三档做法要三句全非空才生效，于是多数卡上不生效。

Shiyu 2026-10-09 定：状态类字段改为**沿用最近一次** —— 阶段 k 没有值，就用 1..k 里最近
一个有值的阶段；阶段 k 自己有值就用自己的（整格替换，不与早期合并）；永远不看 k 之后。
与关系态度的投影（`_project_relationships`：≤k 最新）同一条规则。

走真实代码：`card_from_draft` → `project_card` / `ChatEngine` 发出的 system prompt。
预言独立于被测代码：句子、阶段号都是本文件里的字面量。

I1 最后阶段没有证据的三档 / 亲近条件，沿用早期阶段的值；三档齐全。
I2 阶段自己有值就整格替换，不与早期合并（列表不并集）。
I3 不看 k 之后：阶段 1 看不到只在阶段 2、3 才有的值（main 上就绿：回归守卫）。
I4 全程（顶层）条目照旧在阶段条目之后。
I5 聊天默认阶段（最后阶段）的 system prompt 用上了这张卡自己的三档做法。
I6 单值字段：阶段 k 上有证据的值（自己这一格或全程）优先，沿用只补空缺
   （main 上就绿：回归守卫）。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.arc_view import project_card
from core.card_draft import card_from_draft
from core.chat_engine import ChatEngine

_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q = {1: "开头甲甲甲", 2: "中间乙乙乙", 3: "结尾丙丙丙"}

_CLOSE_P1 = "对熟人絮叨之乎者也"
_NORMAL_P1 = "对生人端着读书人的架子"
_CONFLICT_P2 = "被顶撞就涨红了脸争辩"
_WARM_P1 = "对方给他温一碗酒"
_M_P1 = "想要考上秀才；为此不惜省下酒钱买书"
_M_P3 = "想要保住最后一点脸面；为此不惜撒谎"
_M_ALL = "想要被人敬重；为此不惜硬撑体面"
_CLOSE_ALL = "对熟人拱手作揖"


def _timed(value, *phases):
    return {"value": value, "occurrences": [{"phase": p, "quote": _Q[p]} for p in phases]}


def _card():
    draft = {
        "name": "孔乙己",
        "identity": "落魄读书人",
        "character_arc": {"axis": "从死要面子到不再分辩", "phases": [
            {"label": "死要面子", "state": "断腿之前", "anchor": ""},
            {"label": "强撑", "state": "被打之后", "anchor": _Q[2]},
            {"label": "不再分辩", "state": "最后一次来", "anchor": _Q[3]},
        ]},
        "motives": [_timed(_M_P1, 1), _timed(_M_P3, 3), _timed(_M_ALL, 1, 2, 3)],
        "psyche": {
            "warming_conditions": [_timed(_WARM_P1, 1)],
            "relational_modes": {
                "close": [_timed(_CLOSE_P1, 1)],
                "normal": [_timed(_NORMAL_P1, 1)],
                "conflict": [_timed(_CONFLICT_P2, 2)],
            },
        },
    }
    return card_from_draft(draft, _SRC)


def test_i0_fixture_is_as_described():
    lines = [_CLOSE_P1, _NORMAL_P1, _CONFLICT_P2, _WARM_P1, _M_P1, _M_P3, _M_ALL, _CLOSE_ALL]
    assert len(set(lines)) == len(lines)
    assert not any(a != b and a in b for a in lines for b in lines)
    card = _card()
    assert len(card.character_arc.phases) == 3
    last = card.character_arc.phases[2].overlay
    assert "psyche" not in last or not last["psyche"].get("relational_modes"), \
        "夹具前提不成立：最后阶段本身就有三档"
    assert card.motives == [_M_ALL], "夹具前提不成立：全程动机没进顶层"


@pytest.mark.parametrize("k", [3, None])          # None = 聊天默认（最后阶段）
def test_i1_last_phase_inherits_earlier_values(k):
    proj, _ = project_card(_card(), k)
    rm = proj.psyche.relational_modes
    assert (rm.close, rm.normal, rm.conflict) == (_CLOSE_P1, _NORMAL_P1, _CONFLICT_P2)
    assert rm.complete
    assert proj.psyche.warming_conditions == [_WARM_P1]


def test_i2_own_value_replaces_earlier_not_merged():
    proj3, _ = project_card(_card(), 3)
    assert _M_P3 in proj3.motives and _M_P1 not in proj3.motives, "阶段 3 有自己的动机，不该并入阶段 1 的"
    proj2, _ = project_card(_card(), 2)
    assert _M_P1 in proj2.motives and _M_P3 not in proj2.motives, "阶段 2 没有动机，应沿用阶段 1"


def test_i3_never_reads_later_phases():
    proj1, _ = project_card(_card(), 1)
    assert proj1.psyche.relational_modes.conflict == "", "阶段 1 看到了阶段 2 才有的值"
    assert not proj1.psyche.relational_modes.complete
    assert _M_P3 not in proj1.motives


def test_i4_lifelong_entries_stay_after_phase_entries():
    proj, _ = project_card(_card(), 2)
    assert proj.motives == [_M_P1, _M_ALL]


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


def test_i5_default_phase_chat_uses_the_cards_own_relational_mode():
    llm = _RecLLM()
    eng = ChatEngine(llm=llm, rag=None, card=_card(), card_id="c", storage=None,
                     session_id="", is_new_session=False, arc_phase=None)
    eng.load_affinity({"affinity": 40, "trust": 30, "mood": "平静", "guard": 70,
                       "reason": "", "inner_voice": ""}, initialized=True)
    eng.chat("你好")
    assert _NORMAL_P1 in llm.prompts[0], "默认阶段的聊天没用上这张卡自己的「对平常的人」"


def test_i6_scalar_value_with_evidence_at_k_beats_inherited():
    card = card_from_draft(_draft_for_i6([_timed(_CLOSE_P1, 1), _timed(_CLOSE_ALL, 1, 2, 3)]), _SRC)
    assert card.psyche.relational_modes.close == _CLOSE_ALL, "夹具前提不成立：全程值没进顶层"
    proj1, _ = project_card(card, 1)
    proj3, _ = project_card(card, 3)
    assert proj1.psyche.relational_modes.close == _CLOSE_P1, "阶段 1 自己的值应优先于全程"
    assert proj3.psyche.relational_modes.close == _CLOSE_ALL, "阶段 3 有全程值（有证据），不该被阶段 1 的值顶掉"


def _draft_for_i6(close):
    return {
        "name": "孔乙己",
        "identity": "落魄读书人",
        "character_arc": {"axis": "从死要面子到不再分辩", "phases": [
            {"label": "死要面子", "state": "断腿之前", "anchor": ""},
            {"label": "强撑", "state": "被打之后", "anchor": _Q[2]},
            {"label": "不再分辩", "state": "最后一次来", "anchor": _Q[3]},
        ]},
        "psyche": {"relational_modes": {"close": close}},
    }
