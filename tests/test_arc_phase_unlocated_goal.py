# -*- coding: utf-8 -*-
"""目标检查（spec `arc-phase-unlocated.md` §1）—— 检的是「达成目标」，不是「符合 spec」。

目标：**一条条目挂在哪个阶段，必须有原文位置作证据；没有证据的不进 prompt，也不丢。**
用公版《孔乙己》（`tests/fixtures/kongyiji.txt`）+ 一份混着对标、错标、编造摘录的草稿，
走真实的 `card_from_draft`，再用**独立于被测代码的预言**（`str.find` 定阶段范围、逐段找摘录
位置）核对四条不变量：

- G1 证据：挂在阶段 p 的每一条，草稿里至少有一段它的摘录落在阶段 p；
- G2 守恒：草稿里每条有合法标注的条目，在卡上（顶层 / 某阶段 / 未定位区）至少出现一次；
- G3 不进 prompt：任一阶段的投影卡（所有拼 prompt 的入口只收投影卡，DA18）里没有未定位条目；
- G4 同口径：卡上每个文本叶子（含 overlay、未定位区）注入守卫都能清掉。

**预言的边界（如实写明，不假装覆盖）**：
- 摘录与原文的规范化共用被测代码的 `core.quotes.normalize` —— 规范化口径错了，预言跟着错；
  它守的是「定位」，不是「规范化」；
- 预言不模拟 `MAX_OCCURRENCES` 截断与规则 b（摘录多处出现时取哪一处），故 G1 只是**必要
  条件**：挂对了的一定过，过了的不保证是规则要的那一个阶段；
- 夹具含同一关系两条态度标同一阶段、摘录各落一个阶段的情形（「孩子们」，审计发现 1）——
  按关系聚合证据的实现会把 A 挂到 B 的阶段，G1 当场红。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.arc_view import project_card
from core.card_draft import card_from_draft
from core.card_layers import get_path, overlay_leaves
from core.moderation.card_guard import neutralize_one
from core.moderation.card_text import iter_texts
from core.quotes import normalize

_TEXT = (Path(__file__).resolve().parent / "fixtures" / "kongyiji.txt").read_text(encoding="utf-8")
_ANCHOR2 = "大约是中秋前的两三天"
_FAKE = "这句话原文里根本没有"


def _occ(p, q):
    return {"phase": p, "quote": q}


# 文本值一律唯一，方便从卡上反查草稿条目。
_DRAFT = {
    "name": "孔乙己",
    "character_arc": {"axis": "从争辩到不辩", "phases": [
        {"label": "穷酸要面子", "state": "s1", "anchor": ""},
        {"label": "断腿之后", "state": "s2", "anchor": _ANCHOR2}]},
    "situation_behaviors": [
        {"situation": "B对标", "behavior": "涨红脸争辩", "occurrences": [_occ(1, "窃书不能算偷")]},
        {"situation": "B错标", "behavior": "满口之乎者也", "occurrences": [_occ(2, "多乎哉？不多也")]},
        {"situation": "B阶段2", "behavior": "只说不要取笑", "occurrences": [_occ(2, "不要取笑")]},
        {"situation": "B全程", "behavior": "排钱付账",
         "occurrences": [_occ(1, "排出九文大钱"), _occ(2, "下回还清罢")]},
        {"situation": "B编造", "behavior": "无中生有", "occurrences": [_occ(1, _FAKE)]},
    ],
    "key_memories": [
        {"memory": "M错标", "occurrences": [_occ(2, "茴字")]},
        {"memory": "M编造", "occurrences": [_occ(1, _FAKE)]},
    ],
    "personality_traits": [
        {"value": "T错标", "occurrences": [_occ(2, "涨红了脸")]},
        {"value": "T编造", "occurrences": [_occ(1, _FAKE)]},
    ],
    "speaking_style": {"catchphrases": [
        {"value": "多乎哉", "occurrences": [_occ(1, "多乎哉？不多也")]}]},
    "relationships": [
        {"target": "掌柜", "relation": "债主", "attitudes": [
            {"phase": 1, "attitude": "A1掌柜", "quote": "排出九文大钱"},
            {"phase": 2, "attitude": "A2掌柜", "quote": "孔乙己长久没有来了"}]},
        {"target": "孩子们", "relation": "邻居孩子", "attitudes": [
            {"phase": 1, "attitude": "A撞车外", "quote": "不要取笑"},
            {"phase": 1, "attitude": "A撞车内", "quote": "茴字"}]},
        {"target": "酒客", "relation": "看客", "attitudes": [
            {"phase": 1, "attitude": "A编造", "quote": _FAKE}]},
    ],
}

_CARD = card_from_draft(json.loads(json.dumps(_DRAFT)), _TEXT)
_NORM = normalize(_TEXT)
_START2 = _NORM.find(normalize(_ANCHOR2))


def _landing(quote: str) -> set[int]:
    """独立预言：摘录（规范化后）在原文里每一处出现所在的阶段。"""
    q = normalize(quote)
    return {1 if m.start() < _START2 else 2 for m in re.finditer(re.escape(q), _NORM)} if q else set()


def _quotes_of(text: str) -> set[int]:
    """反查草稿：文本值是 `text` 的那条条目，所有摘录的落点阶段并集。"""
    for b in _DRAFT["situation_behaviors"]:
        if text in (b["situation"],):
            return set().union(*(_landing(o["quote"]) for o in b["occurrences"]))
    for m in _DRAFT["key_memories"]:
        if m["memory"] == text:
            return set().union(*(_landing(o["quote"]) for o in m["occurrences"]))
    for row in _DRAFT["personality_traits"] + _DRAFT["speaking_style"]["catchphrases"]:
        if row["value"] == text:
            return set().union(*(_landing(o["quote"]) for o in row["occurrences"]))
    for r in _DRAFT["relationships"]:
        for a in r["attitudes"]:
            if a["attitude"] == text:
                return _landing(a["quote"])
    raise KeyError(text)


def _placements():
    """卡上每个挂在阶段下的条目：(阶段号, 文本)。"""
    out = []
    for p, ph in enumerate(_CARD.character_arc.phases, 1):
        out += [(p, b.situation) for b in ph.behaviors]
        for _path, val in overlay_leaves(ph.overlay):
            out += [(p, v) for v in (val if isinstance(val, list) else [val])]
    for r in _CARD.relationships:
        out += [(pa.phase, pa.attitude) for pa in r.phase_attitudes if pa.attitude]
    return out


def test_g1_every_phase_placement_has_positional_evidence():
    """挂在阶段 p 的条目，有摘录落在 p；唯一例外是没有任何证据的经历类（挂最后阶段）。"""
    bad = [(p, t) for p, t in _placements()
           if p not in _quotes_of(t) and not (_quotes_of(t) == set() and p == 2)]
    assert bad == []


def test_g2_nothing_with_a_valid_tag_is_lost():
    dump = json.dumps(_CARD.model_dump(), ensure_ascii=False)
    texts = ([b["situation"] for b in _DRAFT["situation_behaviors"]]
             + [m["memory"] for m in _DRAFT["key_memories"]]
             + [t["value"] for t in _DRAFT["personality_traits"]]
             + [c["value"] for c in _DRAFT["speaking_style"]["catchphrases"]]
             + [a["attitude"] for r in _DRAFT["relationships"] for a in r["attitudes"]])
    assert [t for t in texts if t not in dump] == []


def test_g3_no_projection_carries_unlocated_items():
    loose = _CARD.character_arc.unlocated
    texts = ([b.situation for b in loose.behaviors] + [a.attitude for a in loose.attitudes]
             + [v for _p, vs in overlay_leaves(loose.overlay) for v in vs])
    dumps = [json.dumps(project_card(_CARD, k)[0].model_dump(), ensure_ascii=False) for k in (1, 2)]
    assert (sorted(texts), [t for t in texts for d in dumps if t in d]) == (
        ["A编造", "B编造", "T编造"], [])


def test_g4_guard_reaches_every_text_leaf():
    dump = _CARD.model_dump()
    paths = [p for p, _ in iter_texts(dump)]
    assert [p for p in paths if not neutralize_one(json.loads(json.dumps(dump)), p)] == []


def test_g0_fixture_really_has_a_same_phase_collision():
    """夹具自检：「孩子们」两条态度标同一阶段、摘录落点各不相同 —— 否则发现 1 那条 G1 空转。"""
    atts = next(r for r in _DRAFT["relationships"] if r["target"] == "孩子们")["attitudes"]
    assert ([a["phase"] for a in atts], [_landing(a["quote"]) for a in atts]) == ([1, 1], [{2}, {1}])


def test_g0_fixture_really_exercises_rehang_and_unlocated():
    """夹具自检：确有改挂（B错标→1、M错标→1、T错标→1）与挂最后（M编造→2），否则 G1–G3 空转。"""
    ph = _CARD.character_arc.phases
    assert ([b.situation for b in ph[0].behaviors], get_path(ph[0].overlay, "key_memories"),
            get_path(ph[0].overlay, "personality_traits"), get_path(ph[1].overlay, "key_memories")) == (
        ["B对标", "B错标"], ["M错标"], ["T错标"], ["M编造"])
