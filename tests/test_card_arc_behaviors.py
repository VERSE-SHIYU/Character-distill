# -*- coding: utf-8 -*-
"""角色弧线（变化轴 + 阶段）与情境→行为条目：卡片数据层的契约。

spec：docs/specs/arc-behaviors-s1.md、docs/specs/arc-behaviors-draft.md。做法挂在哪里就是它的
适用范围：阶段下的只在那个阶段成立，卡片顶层的从头到尾都成立。
"""
from core.card_quotes import retract_unverified
from core.distiller import DISTILL_PROMPT_AFTER_NAME, format_prompt_after
from core.schema import CharacterCard


# ── 形态与旧卡兼容 ────────────────────────────────────────────────────

def test_legacy_string_list_arc_becomes_unlabeled_phases():
    card = CharacterCard.model_validate({"name": "x", "character_arc": ["起初冷漠", "学会信任"]})
    assert card.character_arc.axis == ""
    assert [(p.label, p.state) for p in card.character_arc.phases] == [
        ("", "起初冷漠"), ("", "学会信任")]


def test_object_arc_keeps_axis_labels_and_phase_behaviors():
    arc = {"axis": "从桀骜到担当", "phases": [{
        "label": "桀骜不服", "state": "大闹天宫前后，动辄动手",
        "behaviors": [{"situation": "被人轻视", "behavior": "当场动手", "source_quote": ""}],
    }]}
    card = CharacterCard.model_validate({"name": "x", "character_arc": arc})
    assert card.character_arc.model_dump() == {
        "axis": "从桀骜到担当", "source_fingerprint": "",
        "phases": [{
            "label": "桀骜不服", "state": "大闹天宫前后，动辄动手",
            "behaviors": [{"situation": "被人轻视", "behavior": "当场动手", "source_quote": ""}],
            "overlay": {}, "start": None, "boundary_examples": [],
        }],
    }


def test_card_without_new_fields_gets_empty_defaults():
    card = CharacterCard.model_validate({"name": "x"})
    assert card.character_arc.model_dump() == {
        "axis": "", "phases": [], "source_fingerprint": ""}
    assert card.situation_behaviors == []


def test_dump_roundtrip_is_stable_and_canonicalizes_legacy_arc():
    """编辑保存存的是 `model_validate(...).model_dump()`：旧形态存回即新形态，再读不变。"""
    legacy = {"name": "x", "character_arc": ["a"],
              "situation_behaviors": [{"situation": "s", "behavior": "b"}]}
    once = CharacterCard.model_validate(legacy).model_dump()
    assert once["character_arc"] == {
        "axis": "", "source_fingerprint": "",
        "phases": [{"label": "", "state": "a", "behaviors": [], "overlay": {},
                    "start": None, "boundary_examples": []}],
    }
    assert once["situation_behaviors"] == [{"situation": "s", "behavior": "b", "source_quote": ""}]
    assert CharacterCard.model_validate(once).model_dump() == once


# ── 提示词片段归属 ────────────────────────────────────────────────────

def test_g6_prompt_carries_arc_and_behaviors_together():
    """做法标的是阶段编号：弧线与情境→行为必须在同一组（同一次调用）里产出。

    模板是草稿形态：阶段下没有做法（做法各带 phases 那一半由 test_card_draft 守）。
    """
    g6, g4 = format_prompt_after("G6"), format_prompt_after("G4")
    assert "L. 角色弧线" in g6 and "O. 情境→行为" in g6
    assert '"axis"' in g6 and '"phases"' in g6 and '"situation_behaviors"' in g6
    assert '"behaviors"' not in g6   # 做法的阶段编号见 test_card_draft 的 G6 模板用例
    assert "character_arc 是【对象】" in g6 and "situation_behaviors / key_memories 的每个元素是【对象】" in g6
    assert "L. 角色弧线" not in g4 and "O. 情境→行为" not in g4
    assert '"character_arc"' not in g4 and '"situation_behaviors"' not in g4


def test_full_prompt_carries_both():
    """一次读完路径用完整提示词：两块都得在。"""
    assert "L. 角色弧线" in DISTILL_PROMPT_AFTER_NAME and '"axis"' in DISTILL_PROMPT_AFTER_NAME
    assert "O. 情境→行为" in DISTILL_PROMPT_AFTER_NAME
    assert '"situation_behaviors"' in DISTILL_PROMPT_AFTER_NAME


# ── 落卡前核对 ────────────────────────────────────────────────────────

SOURCE = "刘姥姥笑道：“你老慢慢说，我听着呢。”说着便把手里的茶放下了。众人都笑起来。"


def _card(**behavior):
    row = {"situation": "被人取笑", "behavior": "跟着自嘲", "source_quote": ""}
    row.update(behavior)
    return CharacterCard.model_validate({"name": "刘姥姥", "situation_behaviors": [row]})


def test_source_quote_found_in_text_is_kept():
    new, retracted = retract_unverified(_card(source_quote="说着便把手里的茶放下了"), SOURCE)
    assert new.situation_behaviors[0].source_quote == "说着便把手里的茶放下了"
    assert retracted == []


def test_source_quote_not_in_text_is_blanked_and_entry_kept():
    new, retracted = retract_unverified(_card(source_quote="她拍着大腿哈哈大笑"), SOURCE)
    assert len(new.situation_behaviors) == 1
    assert new.situation_behaviors[0].behavior == "跟着自嘲"
    assert new.situation_behaviors[0].source_quote == ""
    assert {"field": "situation_behaviors[0].source_quote", "quote": "她拍着大腿哈哈大笑"} in retracted


def test_empty_source_quote_is_not_a_retraction():
    _, retracted = retract_unverified(_card(), SOURCE)
    assert retracted == []


def test_behavior_quote_marks_stripped_when_not_in_text():
    new, _ = retract_unverified(_card(behavior="说“我老婆子没见过世面”来自嘲"), SOURCE)
    assert new.situation_behaviors[0].behavior == "说我老婆子没见过世面来自嘲"


def test_several_unverified_catchphrases_are_all_removed():
    """口癖并入同一张 `VERBATIM_FIELDS`：连续几条对不上，都要删，且不误删对得上的。"""
    card = CharacterCard.model_validate({
        "name": "刘姥姥",
        "speaking_style": {"catchphrases": ["无事忙", "阿弥陀佛保佑", "你老慢慢说"]},
    })
    new, retracted = retract_unverified(card, SOURCE)
    assert new.speaking_style.catchphrases == ["你老慢慢说"]
    assert {r["quote"] for r in retracted} == {"无事忙", "阿弥陀佛保佑"}
    assert {r["field"] for r in retracted} == {"speaking_style.catchphrases"}


def test_phase_behaviors_go_through_the_same_checks():
    """阶段下的做法与顶层的走同一套核对：摘录查不到清空，引号里查不到的去引号。"""
    card = CharacterCard.model_validate({"name": "刘姥姥", "character_arc": {"phases": [{
        "label": "初来乍到", "state": "头一回进府",
        "behaviors": [{"situation": "被人取笑", "behavior": "说“我老婆子没见过世面”来自嘲",
                       "source_quote": "她拍着大腿哈哈大笑"}],
    }]}})
    new, _ = retract_unverified(card, SOURCE)
    row = new.character_arc.phases[0].behaviors[0]
    assert row.source_quote == ""
    assert row.behavior == "说我老婆子没见过世面来自嘲"

