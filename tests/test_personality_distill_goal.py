# -*- coding: utf-8 -*-
"""③ 段 2 目标检查：蒸馏能产出性格注入要用的字段，并按阶段、经核对落到卡上。

目标：段 1 让聊天读得懂动机之外的这些字段（三档做法、亲近条件、宜人性分面），段 3 会读
动机；但只有蒸馏真的产出它们、并且摘录经过核对、按阶段挂对位置，聊天才用得上。段 2 管的
就是这一段：「模型被要求写什么」→「草稿」→「存卡」→「投影 / 聊天 prompt」。

走真实代码：`format_prompt_after`（发给模型的分组提示词）、`card_from_draft`（草稿 → 存卡的
唯一出口）、`retract_unverified`（引文核对）、`project_card`（投影）、`ChatEngine`（聊天
prompt）。只换 LLM，桩只记下 system prompt。

预言独立于被测代码：句子、键名、阶段、摘录都是本文件里的字面量；不 import 被测模块的常量。

D1 每个会进卡的字段（登记表里 stable / state / experience，且归某个蒸馏组）都写进了该组的
   JSON 模板 —— 模板漏了的字段，模型不会产出（模板末尾要求「不要添加自定义字段」）。
D2 新字段的写法要求写进了对应维度：G2 有动机，G4 有三档做法、亲近条件、宜人性三分面。
D3 动机按阶段分发：全部阶段成立 → 顶层；只在阶段 2 成立 → 只在阶段 2；投影后阶段 2 的在前。
D4 三档做法按阶段挂（三层路径），一路到聊天 prompt：选阶段 k 只出阶段 k 的那一句。
   **main 上就是绿的**（段 1 已登记、草稿派生与分发是通用的）—— 这是回归守卫，不是证据。
D5 引文核对覆盖新字段：查不到的引文去掉引号；分面摘录查不到只清空 `quote`，行为保留。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.arc_view import project_card
from core.card_draft import card_from_draft
from core.card_layers import REGISTRY
from core.card_quotes import retract_unverified
from core.chat_engine import ChatEngine
from core.distiller import format_prompt_after
from core.schema import FORMAT_GROUPS, POST_FORMAT_FIELDS

# 三段原文，各有独特 token：阶段 1 = 开头一段，阶段 2 从「中间」那句起（锚点）。
_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q1, _Q2, _Q3 = "开头甲甲甲", "中间乙乙乙", "结尾丙丙丙"
_FAKE = "从来没写过的一句话"          # 原文里没有

_TEMPLATE_MARK = "JSON 模板"          # 分组提示词里 JSON 模板段的引导词


def _occ(*phases):
    return [{"phase": p, "quote": {1: _Q1, 2: _Q2}[p]} for p in phases]


def _timed(value, *phases):
    return {"value": value, "occurrences": _occ(*phases)}


# 两个阶段的草稿；各字段的取值两两不同，互不为子串（D0 自检）。
_M_ALL = "想要被人敬重；为此不惜硬撑体面"
_M_P2 = "想要保住最后一点脸面；为此不惜撒谎"
_CLOSE_P1, _CLOSE_P2 = "对熟人絮叨之乎者也", "对熟人只低头不语"
_NORMAL, _CONFLICT = "对生人端着读书人的架子", "被顶撞就涨红了脸争辩"
_WARM_P2 = "对方不提他偷书的事"
_FACET_BEHAVIOR = "对弱者会分一颗茴香豆"


def _draft(**over):
    d = {
        "name": "孔乙己",
        "character_arc": {"axis": "从死要面子到不再分辩", "phases": [
            {"label": "死要面子", "state": "断腿之前", "anchor": ""},
            {"label": "不再分辩", "state": "断腿之后", "anchor": _Q2},
        ]},
        "motives": [_timed(_M_ALL, 1, 2), _timed(_M_P2, 2)],
        "psyche": {
            "agreeableness": 2,
            "warming_conditions": [_timed(_WARM_P2, 2)],
            "relational_modes": {
                "close": [_timed(_CLOSE_P1, 1), _timed(_CLOSE_P2, 2)],
                "normal": [_timed(_NORMAL, 1, 2)],
                "conflict": [_timed(_CONFLICT, 1, 2)],
            },
            "agreeableness_facets": [
                {"facet": "同情", "level": "中", "behavior": _FACET_BEHAVIOR, "quote": _Q1},
            ],
        },
    }
    d.update(over)
    return d


def _card(draft=None):
    card = card_from_draft(draft or _draft(), _SRC)
    card, _ = retract_unverified(card, _SRC)
    return card


def test_d0_fixture_sentences_are_distinct():
    lines = [_M_ALL, _M_P2, _CLOSE_P1, _CLOSE_P2, _NORMAL, _CONFLICT, _WARM_P2, _FACET_BEHAVIOR]
    assert len(set(lines)) == len(lines)
    assert not any(a != b and a in b for a in lines for b in lines), "夹具句子互为子串，D3/D4 的「不得出现」空转"


# ── D1 模板覆盖：会进卡的字段，模型都被要求写 ─────────────────────────────

def _group_of(top: str) -> str | None:
    return next((g for g, fields in FORMAT_GROUPS.items() if top in fields), None)


def _distilled_leaves():
    """登记表里由蒸馏产出的叶子：stable / state / experience，且顶层字段归某个蒸馏组。"""
    out = []
    for path, spec in REGISTRY.items():
        top = path.split(".")[0]
        if spec.layer in ("stable", "state", "experience") and top not in POST_FORMAT_FIELDS:
            out.append((path, _group_of(top)))
    return out


def test_d1_every_distilled_field_is_in_its_group_template():
    leaves = _distilled_leaves()
    assert ("psyche.warming_conditions", "G4") in leaves, "登记表里没有亲近条件 —— D1 空转"
    missing = []
    for path, group in leaves:
        assert group is not None, f"{path} 不归任何蒸馏组，也不在 POST_FORMAT_FIELDS"
        template = format_prompt_after(group).split(_TEMPLATE_MARK, 1)[1]
        if f'"{path.split(".")[-1]}"' not in template:
            missing.append(f"{group}:{path}")
    assert not missing, f"这些字段进卡，但分组 JSON 模板里没有，模型不会产出：{missing}"


def test_d1_motives_is_a_distilled_field_of_g2():
    assert ("motives", "G2") in _distilled_leaves(), "动机没有登记为 G2 的蒸馏字段"


# ── D2 维度说明：模型被告知怎么写 ─────────────────────────────────────────

def _dims(group: str) -> str:
    """分组提示词里「分析维度」到「输出要求」之间的那段（不含 JSON 模板）。"""
    text = format_prompt_after(group)
    return text.split("## 分析维度", 1)[1].split("## 输出要求", 1)[0]


def test_d2_g2_asks_for_motives_without_means():
    dims = _dims("G2")
    assert "动机" in dims, "G2 没有动机这一维度"
    assert "手段" in dims, "动机维度没说「不写手段（手段在情境→做法）」"
    assert "如实" in dims and "美化" in dims, "动机维度没要求负面动机照原文如实写、不美化"


def test_d2_g4_asks_for_relational_modes_warming_and_facets():
    dims = _dims("G4")
    for word in ("亲近条件", "对亲近的人", "对平常的人", "起冲突时", "同情", "谦恭", "信任"):
        assert word in dims, f"G4 维度说明里没有「{word}」"
    # 分面是稳定类：引用稳定类写法（只写一次、不按阶段拆）；不写具体剧情
    assert "这类字段全程不变" in dims.split("同情", 1)[1], "分面没有引用稳定类写法"
    assert "剧情" in dims.split("同情", 1)[1], "分面没说「只写概括性的行为，不写具体剧情事件」"
    # 分面是 list[对象]，登记表不展开它（D1 管不到元素的键）：模板里要写出元素的四个键
    template = format_prompt_after("G4").split(_TEMPLATE_MARK, 1)[1]
    facets = template.split('"agreeableness_facets"', 1)[-1]
    for key in ("facet", "level", "behavior", "quote"):
        assert f'"{key}"' in facets, f"分面模板缺元素键 {key}"


# ── D3 动机按阶段分发、投影 ─────────────────────────────────────────────

def test_d3_motives_dispatched_by_phase_and_projected():
    card = _card()
    assert card.motives == [_M_ALL], "全部阶段都成立的动机应在顶层，且只有它"
    assert card.character_arc.phases[1].overlay["motives"] == [_M_P2]
    assert "motives" not in card.character_arc.phases[0].overlay
    p1, _ = project_card(card, 1)
    p2, _ = project_card(card, 2)
    assert p1.motives == [_M_ALL], "阶段 1 不该看到只在阶段 2 成立的动机"
    assert p2.motives == [_M_P2, _M_ALL], "阶段 2：阶段特有的在前，全程的在后"


# ── D4 三档做法按阶段一路到聊天 prompt（回归守卫）─────────────────────────

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


def _first_prompt(card, arc_phase: int, affinity: int) -> str:
    llm = _RecLLM()
    eng = ChatEngine(llm=llm, rag=None, card=card, card_id="c", storage=None,
                     session_id="", is_new_session=False, arc_phase=arc_phase)
    eng.load_affinity({"affinity": affinity, "trust": 30, "mood": "平静", "guard": 70,
                       "reason": "", "inner_voice": ""}, initialized=True)
    eng.chat("你好")
    return llm.prompts[0]


@pytest.mark.parametrize("k, mine, other", [(1, _CLOSE_P1, _CLOSE_P2), (2, _CLOSE_P2, _CLOSE_P1)])
def test_d4_relational_mode_of_the_chosen_phase_reaches_the_chat_prompt(k, mine, other):
    sp = _first_prompt(_card(), arc_phase=k, affinity=80)
    assert mine in sp, f"阶段 {k}、亲近档：缺这一阶段对亲近的人的做法"
    assert other not in sp, f"阶段 {k}：出现了别的阶段的做法"
    assert _FACET_BEHAVIOR in sp, "分面行为没进人格块"


# ── D5 引文核对覆盖新字段 ─────────────────────────────────────────────

def test_d5_facet_quote_is_verbatim_checked_behavior_kept():
    good = _card()
    assert good.psyche.agreeableness_facets[0].quote == _Q1, "查得到的分面摘录被误删"
    draft = _draft()
    draft["psyche"]["agreeableness_facets"][0]["quote"] = _FAKE
    bad = _card(draft)
    facet = bad.psyche.agreeableness_facets[0]
    assert facet.quote == "", "查不到的分面摘录没被清空"
    assert facet.behavior == _FACET_BEHAVIOR, "清空摘录时把行为也删了（Q2：分面非空即生效）"


def _with_fake_quote(text: str) -> str:
    return f"{text}（「{_FAKE}」）"


def _quoted_ok(text: str) -> str:
    return f"{text}（「{_Q2}」）"


def test_d5_unverified_quotes_in_new_state_fields_are_unquoted():
    draft = _draft()
    draft["motives"] = [_timed(_with_fake_quote(_M_ALL), 1, 2), _timed(_quoted_ok(_M_P2), 2)]
    draft["psyche"]["warming_conditions"] = [_timed(_with_fake_quote(_WARM_P2), 2)]
    draft["psyche"]["relational_modes"]["close"] = [_timed(_with_fake_quote(_CLOSE_P1), 1),
                                                     _timed(_with_fake_quote(_CLOSE_P2), 2)]
    card = _card(draft)
    dump = json.dumps(card.model_dump(), ensure_ascii=False)
    assert f"「{_FAKE}」" not in dump, "新字段里查不到的引文还带着引号（顶层或阶段 overlay）"
    assert _FAKE in dump, "去引号时把文字也删了（应只去引号）"
    assert f"「{_Q2}」" in dump, "查得到的引文被误去引号"
