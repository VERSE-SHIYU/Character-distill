# -*- coding: utf-8 -*-
"""阶段未验证的处理 + overlay 与卡片同形 —— spec `docs/specs/arc-phase-unlocated.md` §4。

本文件是变异驱动 `tests/perf/arc_phase_unlocated_mutations.py` 的覆盖域。约定同
`test_phase_anchoring.py`：**每条用例只留一条 assert**（多件事并成一条元组/结构相等）。
原文夹具与 `test_phase_anchoring.py` 同形：三段各有独特 token，阶段范围精确可算。
"""
from __future__ import annotations

import json
import logging

import pydash
import pytest

from core.arc_view import project_card
from core.card_draft import card_from_draft
from core.card_layers import REGISTRY, get_path
from core.card_quotes import retract_unverified
from core.export import to_tavern_json
from core.moderation.card_guard import neutralize_one
from core.moderation.card_text import iter_texts
from core.schema import ArcPhase, CharacterCard

_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q1, _Q2, _Q3 = "开头甲甲甲", "中间乙乙乙", "结尾丙丙丙"
_A3 = {2: _Q2, 3: _Q3}
_NOWHERE = "查无此句"

# 登记表里 7 个带点的 state / experience 路径（overlay 曾把它们当键名存）
DOTTED = [p for p, s in REGISTRY.items() if "." in p and s.layer in ("state", "experience")]


def _phases(n, anchors):
    return [{"label": f"L{i}", "state": f"S{i}", "anchor": anchors.get(i, "")}
            for i in range(1, n + 1)]


def _occ(phase, quote):
    return {"phase": phase, "quote": quote}


def _timed(value, *occ):
    return {"value": value, "occurrences": list(occ)}


def _draft(n=3, anchors=_A3, **fields):
    d = {"name": "角色", "character_arc": {"axis": "a", "phases": _phases(n, anchors)}}
    for path, val in fields.items():
        pydash.set_(d, path.replace("__", "."), val)
    return d


def _card(n=3, anchors=_A3, src=_SRC, **fields):
    return card_from_draft(_draft(n, anchors, **fields), src)


def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


def _sample(path, value="样本"):
    return [value] if REGISTRY[path].kind == "list" else value


# ── N：overlay 与卡片同形 ─────────────────────────────────────────────

@pytest.mark.parametrize("path", DOTTED)
def test_n1_legacy_flat_key_is_nested_on_load(path):
    """#116 之后的旧卡把路径当键名存 → 加载时转成嵌套；读得到、键里不再有点。"""
    card = CharacterCard.model_validate({"name": "x", "character_arc": {"phases": [
        {"state": "s", "overlay": {path: _sample(path)}}]}})
    ov = card.character_arc.phases[0].overlay
    assert (get_path(ov, path), any("." in k for k in _keys(ov))) == (_sample(path), False)


def test_n2_nested_value_wins_over_flat_duplicate():
    ov = {"speaking_style": {"catchphrases": ["嵌套"]}, "speaking_style.catchphrases": ["扁平"]}
    assert ArcPhase(state="s", overlay=ov).overlay == {"speaking_style": {"catchphrases": ["嵌套"]}}


def test_n3_overlay_rejects_unregistered_nested_leaf():
    with pytest.raises(ValueError, match="未登记"):
        ArcPhase(state="s", overlay={"speaking_style": {"nope": ["x"]}})


def test_n4_overlay_rejects_wrong_kind_on_nested_leaf():
    with pytest.raises(ValueError, match="应为列表"):
        ArcPhase(state="s", overlay={"speaking_style": {"catchphrases": "不是列表"}})


def test_n4b_unlocated_overlay_uses_the_same_validator_and_rejects_dotted_key():
    """未定位区 overlay 与阶段 overlay 同一个校验器：键名带点直接拒（不靠解析器猜）。"""
    with pytest.raises(ValueError, match="不许含"):
        CharacterCard.model_validate({"name": "x", "character_arc": {"unlocated": {
            "overlay": {"speaking_style.catchphrases": ["哼"]}}}})



def test_n4c_unlocated_overlay_rejects_experience_path():
    """补充 15：未定位区只收 state 路径 —— 校验器第二个调用点的 `layers` 参数（阶段一侧是 N3）。

    经历类进了未定位区就挪不出（`move_unlocated` 只收 state）、也不进引文核对。
    """
    with pytest.raises(ValueError, match="未登记"):
        CharacterCard.model_validate({"name": "x", "character_arc": {"unlocated": {
            "overlay": {"key_memories": ["某事"]}}}})

def _full_card():
    """每个 state / experience 路径都有阶段 2 特有的取值和一条没有证据的取值，外加做法与关系。

    没有证据的取值按类别打标记：状态类 →「未定位-」（只该出现在未定位区），经历类 →
    「挂最后-」（挂最后阶段，阶段 3 的 prompt 里该有、更早的阶段不该有）。
    """
    fields = {}
    for path, spec in REGISTRY.items():
        if spec.layer not in ("state", "experience") or path == "key_memories":
            continue
        mark = "未定位" if spec.layer == "state" else "挂最后"
        fields[path.replace(".", "__")] = [_timed(f"{path}-阶段2", _occ(2, _Q2)),
                                          _timed(f"{mark}-{path}", _occ(1, _NOWHERE))]
    fields["key_memories"] = [{"memory": "挂最后-key_memories", "occurrences": [_occ(1, _NOWHERE)]}]
    fields["situation_behaviors"] = [
        {"situation": "未定位-做法", "behavior": "b", "occurrences": [_occ(1, _NOWHERE)]}]
    fields["relationships"] = [{"target": "甲", "relation": "友", "attitudes": [
        {"phase": 1, "attitude": "未定位-态度", "quote": _NOWHERE}]}]
    return _card(**fields)


def test_n5_no_dotted_key_anywhere_in_card_or_projection():
    """根因锁：卡片与 overlay（含未定位区、各阶段投影）里任何一层的键名都不含「.」。"""
    card = _full_card()
    dumps = [card.model_dump()] + [project_card(card, k)[0].model_dump() for k in (1, 2, 3)]
    assert [k for d in dumps for k in _keys(d) if "." in k] == []


def test_n6_card_from_draft_writes_dotted_path_nested():
    card = _card(speaking_style__catchphrases=[_timed("哼", _occ(2, _Q2))])
    assert card.character_arc.phases[1].overlay == {"speaking_style": {"catchphrases": ["哼"]}}


# ── G：引文核对与注入守卫在 overlay 上与顶层同口径 ─────────────────────

def _dotted_card(path, value, *, top: bool):
    data = {"name": "x", "character_arc": {"phases": [{"state": "s", "overlay": {}}]}}
    if top:
        pydash.set_(data, path, value)
    else:
        pydash.set_(data["character_arc"]["phases"][0]["overlay"], path, value)
    return CharacterCard.model_validate(data)


@pytest.mark.parametrize("path", DOTTED)
def test_g1_guard_neutralizes_each_dotted_overlay_leaf(path):
    card = _dotted_card(path, _sample(path), top=False).model_dump()
    leaf = f"character_arc.phases[0].overlay.{path}" + ("[0]" if REGISTRY[path].kind == "list" else "")
    assert neutralize_one(card, leaf) is True


@pytest.mark.parametrize("path", DOTTED)
def test_g2_quote_check_in_overlay_matches_top_level(path):
    """同一个编造值放顶层与放阶段 overlay，核对结果一致（该核的都核到，不该核的都不动）。"""
    value = _sample(path, "他说「编造的话」") if path != "speaking_style.catchphrases" else ["编造口癖"]
    top, _ = retract_unverified(_dotted_card(path, value, top=True), "原文只有别的。")
    ovl, _ = retract_unverified(_dotted_card(path, value, top=False), "原文只有别的。")
    assert get_path(ovl.character_arc.phases[0].overlay, path) == get_path(top, path)


def test_g3_fabricated_catchphrase_in_phase_overlay_is_retracted():
    """验收复现：编造的口癖在阶段 overlay 里要被撤回（改前留在卡上）。"""
    card, _ = retract_unverified(
        _dotted_card("speaking_style.catchphrases", ["编造口癖"], top=False), "原文只有别的。")
    assert get_path(card.character_arc.phases[0].overlay, "speaking_style.catchphrases") == []


# ── U：没有位置证据的条目按类别处置 ───────────────────────────────────

def test_u1_state_behavior_without_evidence_goes_to_unlocated():
    card = _card(situation_behaviors=[
        {"situation": "s", "behavior": "b", "occurrences": [_occ(3, _NOWHERE)]}])
    assert ([b.situation for b in card.character_arc.unlocated.behaviors],
            card.situation_behaviors, [p.behaviors for p in card.character_arc.phases]) == (
        ["s"], [], [[], [], []])


def test_u2_state_list_value_without_evidence_goes_to_unlocated_overlay():
    card = _card(personality_traits=[_timed("多疑", _occ(2, _NOWHERE))])
    assert (card.character_arc.unlocated.overlay, card.personality_traits,
            [p.overlay for p in card.character_arc.phases]) == (
        {"personality_traits": ["多疑"]}, [], [{}, {}, {}])


def test_u3_state_scalar_dotted_without_evidence_is_kept_as_list_nested():
    card = _card(speaking_style__tone=[_timed("冷", _occ(1, _NOWHERE)),
                                       _timed("热", _occ(2, _NOWHERE))])
    assert card.character_arc.unlocated.overlay == {"speaking_style": {"tone": ["冷", "热"]}}


def test_u4_experience_memory_without_evidence_hangs_on_last_phase():
    card = _card(key_memories=[{"memory": "某事", "occurrences": [_occ(1, _NOWHERE)]}])
    assert ([get_path(p.overlay, "key_memories") for p in card.character_arc.phases],
            card.key_memories) == ([None, None, ["某事"]], [])


def test_u5_experience_scalar_dotted_without_evidence_hangs_on_last_phase():
    card = _card(cognitive__knowledge_scope=[_timed("只识几个字", _occ(1, _NOWHERE))])
    assert [p.overlay for p in card.character_arc.phases] == [
        {}, {}, {"cognitive": {"knowledge_scope": "只识几个字"}}]


def test_u6_single_phase_card_experience_goes_top_state_goes_unlocated():
    card = _card(n=1, anchors={}, key_memories=[{"memory": "m", "occurrences": [_occ(1, _NOWHERE)]}],
                 personality_traits=[_timed("t", _occ(1, _NOWHERE))])
    assert (card.key_memories, card.character_arc.unlocated.overlay) == (
        ["m"], {"personality_traits": ["t"]})


def test_u10_card_without_phases_skips_the_check_and_puts_state_on_top():
    """无阶段的卡不做位置检查（规则 5）：状态类也进顶层，不进未定位区（审计发现 3）。"""
    card = card_from_draft({"name": "角色", "personality_traits": [_timed("多疑", _occ(1, _NOWHERE))],
                            "situation_behaviors": [{"situation": "s", "behavior": "b",
                                                     "occurrences": [_occ(1, _NOWHERE)]}]}, _SRC)
    loose = card.character_arc.unlocated
    assert (card.personality_traits, [b.situation for b in card.situation_behaviors],
            loose.overlay, loose.behaviors) == (["多疑"], ["s"], {}, [])


def test_u7_skipped_card_keeps_model_tags_for_items_without_evidence():
    """整卡跳过（锚点查不到，规则 2）原样保留：标注照挂，不进未定位区。"""
    card = _card(anchors={2: _Q2, 3: "锚点查无此句"}, personality_traits=[
        _timed("多疑", _occ(2, _NOWHERE))])
    assert (card.character_arc.unlocated.overlay,
            get_path(card.character_arc.phases[1].overlay, "personality_traits")) == ({}, ["多疑"])


def test_u9_experience_rehung_to_several_landings_hangs_only_the_earliest():
    """并集之后套类别规则：经历类只挂并集里最早的阶段（状态类挂全部，见 phase_anchoring U19）。"""
    src = "甲乙丙丁戊己甲乙"                               # 「甲乙」@0（阶段1）、@6（阶段3）
    card = _card(src=src, anchors={2: "丙丁", 3: "戊己"},
                 key_memories=[{"memory": "旧事", "occurrences": [_occ(2, "甲乙")]}])
    assert [get_path(p.overlay, "key_memories") for p in card.character_arc.phases] == [
        ["旧事"], None, None]


def test_u8_monitor_counts_every_category(caplog):
    """rehung / unlocated / to_last 汇总做法、字段、记忆、关系四类。"""
    with caplog.at_level(logging.INFO, logger="core.card_draft"):
        _card(situation_behaviors=[{"situation": "s", "behavior": "b",
                                    "occurrences": [_occ(3, _Q1)]}],          # 做法改挂 1
              values=[_timed("v", _occ(1, _NOWHERE))],                         # 字段未定位 1
              key_memories=[{"memory": "m", "occurrences": [_occ(1, _NOWHERE)]}],  # 挂最后 1
              relationships=[{"target": "甲", "relation": "友", "attitudes": [
                  {"phase": 2, "attitude": "a", "quote": _Q3}]}])              # 态度改挂 1
    line = [r.getMessage() for r in caplog.records if "[phase_anchoring]" in r.getMessage()][0]
    assert line.split(" rehung=")[1] == (
        "2 unlocated=1 to_last=1 skipped_card=False memories_dropped=1 "
        "kinds=做法:1/0/0,字段:0/1/0,记忆:0/0/1,关系:1/0/0")


# ── R：关系态度逐条分发 ──────────────────────────────────────────────

def _rel(*attitudes):
    return [{"target": "甲", "relation": "友", "attitudes": list(attitudes)}]


def _att(phase, attitude, quote, note=""):
    return {"phase": phase, "attitude": attitude, "note": note, "quote": quote}


def _pa(card):
    return [(pa.phase, pa.attitude) for pa in card.relationships[0].phase_attitudes]


def test_r1_attitude_is_rehung_to_where_its_quote_lands():
    card = _card(relationships=_rel(_att(3, "疏远", _Q2)))
    assert (_pa(card), card.relationships[0].attitude) == ([(2, "疏远")], "疏远")


def test_r2_collision_original_tag_wins_loser_goes_to_unlocated_with_note():
    card = _card(relationships=_rel(_att(3, "改挂来的", _Q1, note="口径乙"),
                                    _att(1, "原本在1", _Q1)))
    assert (_pa(card), [(a.attitude, a.note, a.phase)
                        for a in card.character_arc.unlocated.attitudes]) == (
        [(1, "原本在1")], [("改挂来的", "口径乙", 3)])


def test_r3_attitude_that_wins_elsewhere_is_not_unlocated():
    src = "甲乙丙丁戊己甲乙"                               # 「甲乙」@0（阶段1）、@6（阶段3）
    card = _card(src=src, anchors={2: "丙丁", 3: "戊己"}, relationships=_rel(
        _att(2, "两处落点", "甲乙"), _att(1, "原本在1", "甲乙")))
    assert (_pa(card), card.character_arc.unlocated.attitudes) == (
        [(1, "原本在1"), (3, "两处落点")], [])


def test_r7_same_phase_tags_each_attitude_uses_only_its_own_evidence():
    """两条态度都标阶段 1、摘录各落一个阶段：各自挂到自己的落点，不共用证据（审计发现 1）。"""
    card = _card(relationships=_rel(_att(1, "证据在2", _Q2), _att(1, "证据在1", _Q1)))
    assert (_pa(card), card.character_arc.unlocated.attitudes) == (
        [(1, "证据在1"), (2, "证据在2")], [])


def test_r4_relationship_without_any_located_attitude_hangs_last_with_empty_attitude():
    card = _card(relationships=_rel(_att(1, "无证据", _NOWHERE)))
    assert (_pa(card), card.relationships[0].attitude,
            [a.attitude for a in card.character_arc.unlocated.attitudes]) == (
        [(3, "")], "", ["无证据"])


def test_r5_empty_attitude_relationship_absent_before_last_and_rendered_without_colon():
    from core.context_engine import ContextEngine

    card = _card(relationships=_rel(_att(1, "无证据", _NOWHERE)))
    early = project_card(card, 2)[0].relationships
    ext = ContextEngine.__new__(ContextEngine)
    ext.card = project_card(card, 3)[0]
    assert (early, "- 甲（友）\n" in ext._build_card_ext() + "\n",
            "- 甲（友）：" in ext._build_card_ext()) == ([], True, False)


def test_r6_card_without_phases_keeps_latest_attitude():
    card = card_from_draft({"name": "角色", "relationships": _rel(
        _att(1, "早", _Q1), _att(2, "晚", _Q2))}, _SRC)
    assert (card.relationships[0].attitude, card.relationships[0].phase_attitudes) == ("晚", [])


# ── P：未定位区不进 prompt、不进导出 ──────────────────────────────────

def test_p1_projection_never_carries_unlocated():
    card = _full_card()
    assert [project_card(card, k)[0].character_arc.unlocated.model_dump() for k in (1, 2, 3)] == [
        {"behaviors": [], "overlay": {}, "attitudes": []}] * 3


def test_p2_prompt_never_contains_unlocated_text_but_has_last_phase_experience():
    """未定位区不进任何阶段的 prompt；挂最后阶段的经历只在阶段 3 出现（正控）。"""
    from core.context_engine import ContextEngine

    card = _full_card()
    texts = []
    for k in (1, 2, 3):
        ce = ContextEngine.__new__(ContextEngine)
        ce.card, ce.arc_view = project_card(card, k)
        texts.append(ce._build_card_core() + ce._build_card_ext())
    assert ([("未定位" in t) for t in texts], [("挂最后-key_memories" in t) for t in texts]) == (
        [False, False, False], [False, False, True])


def test_p3_export_never_contains_unlocated_text():
    assert "未定位" not in json.dumps(to_tavern_json(_full_card()), ensure_ascii=False)


# ── Q：未定位区照样进引文核对与审核遍历 ───────────────────────────────

def _loose_card(**unlocated):
    return CharacterCard.model_validate({"name": "x", "character_arc": {
        "phases": [{"state": "s"}], "unlocated": unlocated}})


def test_q1_quote_in_unlocated_behavior_is_stripped():
    card, _ = retract_unverified(_loose_card(behaviors=[
        {"situation": "s", "behavior": "他说「编造的话」"}]), "原文只有别的。")
    assert card.character_arc.unlocated.behaviors[0].behavior == "他说编造的话"


def test_q2_unverifiable_source_quote_in_unlocated_behavior_is_cleared():
    card, _ = retract_unverified(_loose_card(behaviors=[
        {"situation": "s", "behavior": "b", "source_quote": "编造摘录"}]), "原文只有别的。")
    assert card.character_arc.unlocated.behaviors[0].source_quote == ""


def test_q3_fabricated_catchphrase_in_unlocated_overlay_is_removed():
    card, _ = retract_unverified(_loose_card(overlay={"speaking_style": {
        "catchphrases": ["编造口癖"]}}), "原文只有别的。")
    assert card.character_arc.unlocated.overlay == {"speaking_style": {"catchphrases": []}}


def test_q4_quote_in_unlocated_attitude_is_stripped():
    card, _ = retract_unverified(_loose_card(attitudes=[
        {"target": "甲", "attitude": "常说「编造的话」"}]), "原文只有别的。")
    assert card.character_arc.unlocated.attitudes[0].attitude == "常说编造的话"


def test_q5_moderation_walker_sees_unlocated_leaves():
    card = _loose_card(behaviors=[{"situation": "s", "behavior": "b"}],
                       overlay={"psyche": {"soft_spots": ["软"]}},
                       attitudes=[{"target": "甲", "attitude": "a"}]).model_dump()
    assert {p for p, _ in iter_texts(card) if "unlocated" in p} >= {
        "character_arc.unlocated.behaviors[0].behavior",
        "character_arc.unlocated.overlay.psyche.soft_spots[0]",
        "character_arc.unlocated.attitudes[0].attitude"}


def test_q6_guard_neutralizes_unlocated_overlay_leaf():
    card = _loose_card(overlay={"psyche": {"soft_spots": ["注入"]}}).model_dump()
    assert neutralize_one(card, "character_arc.unlocated.overlay.psyche.soft_spots[0]") is True


