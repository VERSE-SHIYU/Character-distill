# -*- coding: utf-8 -*-
"""好感规则表的单元测试（spec `docs/specs/personality-inject.md` §2 的 R2–R18）。直接测纯函数。"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.affinity_rules import (
    DEFAULT_WARMING, RelationState, apply_event, relational_tier, warming_conditions,
)
from core.card_layers import REGISTRY
from core.schema import PsycheProfile

OWN = ["条件甲", "条件乙"]


def psy(baseline=50, own=True, **kw):
    return PsycheProfile(affinity_baseline=baseline, warming_conditions=OWN if own else [], **kw)


def step(aff, event, tier="small", delta=1, *, state=None, psyche=None, idx=0):
    return apply_event(aff, state or RelationState(), psyche or psy(), event=event, tier=tier,
                       delta=delta, met_index=idx)


WARM = RelationState(nonneg_streak=2, met_count=3)      # 大档可生效、亲近门槛已过


@pytest.mark.parametrize("tier,delta,expect", [
    ("small", 0, 1), ("small", 1, 1), ("small", 2, 2), ("small", 9, 2),
    ("medium", 1, 3), ("medium", 4, 4), ("medium", 9, 5),
    ("large", 1, 6), ("large", 7, 7), ("large", 40, 8),
])
def test_r2_number_is_clipped_into_its_tier(tier, delta, expect):
    assert step(40, "met_condition", tier, delta, state=WARM)[0] == 40 + expect
    assert step(40, "offended", tier, delta)[0] == 40 - expect


def test_r2_sign_comes_from_the_event_not_the_model():
    assert step(40, "met_condition", "medium", -4, state=WARM)[0] == 44
    assert step(40, "offended", "medium", -4)[0] == 36


@pytest.mark.parametrize("aff", [10, 50, 90])
def test_r3_neutral_never_moves(aff):
    assert step(aff, "neutral", None, None, state=WARM)[0] == aff


def test_r4_friendly_adds_only_below_baseline_and_stops_at_it():
    assert step(47, "friendly", "large", 8)[0] == 49            # 一律按小档
    assert step(49, "friendly", "small", 2)[0] == 50            # 加到基线为止
    assert step(50, "friendly", "small", 2)[0] == 50            # 基线及以上不加
    assert step(60, "friendly", "small", 2)[0] == 60


def test_r11_gate_wins_over_a_baseline_above_the_close_threshold():
    assert step(71, "friendly", "small", 2, psyche=psy(baseline=80))[0] == 72


@pytest.mark.parametrize("idx", [2, -1, None, "0", True, 1.0])
def test_r5_bad_condition_index_counts_as_friendly(idx):
    new, state, warns = step(60, "met_condition", "medium", 4, idx=idx)
    assert (new, state.met_count, state.last_event) == (60, 0, "friendly") and warns


def test_r5_valid_index_counts_also_for_default_conditions():
    assert step(40, "met_condition", "small", 2, idx=1)[1].met_count == 1
    assert step(40, "met_condition", "small", 2, idx=1, psyche=psy(own=False))[1].met_count == 1
    assert step(40, "met_condition", "small", 2, idx=2, psyche=psy(own=False))[1].met_count == 0


@pytest.mark.parametrize("streak,expect", [(0, 5), (1, 5), (2, 8), (5, 8)])
def test_r6_large_needs_two_non_negative_turns(streak, expect):
    assert step(20, "met_condition", "large", 8, state=RelationState(nonneg_streak=streak))[0] == 20 + expect


def test_r6_streak_counts_neutral_and_resets_on_offence():
    s = RelationState()
    for event in ("neutral", "friendly"):
        _, s, _ = step(20, event, "small", 1, state=s)
    assert s.nonneg_streak == 2
    assert step(20, "offended", "small", 1, state=s)[1].nonneg_streak == 0


def test_r7_r8_r9_offence_repair_and_clearing():
    a, s, _ = step(50, "offended", "medium", 4)
    assert (a, s.pre_offence) == (46, 50)
    a, s, _ = step(a, "trigger", "small", 1, state=s)
    assert (a, s.pre_offence) == (45, 50), "连续冒犯只记第一次的值"
    a, s, _ = step(a, "repair", "large", 8, state=s)
    assert (a, s.pre_offence) == (50, None), "大档修复按中档算，到冒犯前的值为止并清空"
    assert step(50, "repair", "medium", 4, state=s)[0] == 50, "没有待修复的冒犯：按 friendly，基线上不加"


def test_r9_any_non_negative_event_reaching_the_old_value_clears_it():
    s = RelationState(pre_offence=40)
    assert step(39, "friendly", "small", 2, state=s)[1].pre_offence is None       # 基线 50 以下，客气补回
    assert step(38, "friendly", "small", 1, state=s)[1].pre_offence == 40         # 还没回到
    assert step(38, "met_condition", "medium", 5, state=s)[1].pre_offence is None


@pytest.mark.parametrize("met_before,expect", [(0, 72), (1, 72), (2, 75), (3, 75)])
def test_r10_third_condition_opens_the_close_tier(met_before, expect):
    assert step(70, "met_condition", "medium", 5, state=RelationState(met_count=met_before))[0] == expect


def test_r10_does_not_apply_once_already_close():
    assert step(74, "met_condition", "medium", 5)[0] == 79


def test_r10_repair_returns_to_the_pre_offence_value_even_across_the_gate():
    s = RelationState(pre_offence=82)                 # 起点就在亲近档的关系，被冒犯后掉到 73 以下
    assert step(70, "repair", "medium", 5, state=s) == (75, RelationState("repair", 0, 1, 82), [])
    assert step(80, "repair", "medium", 5, state=s) == (82, RelationState("repair", 0, 1, None), [])


def test_r8_large_repair_counts_as_medium():
    assert step(40, "repair", "large", 8, state=RelationState(pre_offence=60))[0] == 45


def test_r7_trigger_alone_records_the_pre_offence_value():
    assert step(50, "trigger", "small", 1)[1].pre_offence == 50


def test_affinity_stays_within_0_and_100():
    assert step(3, "offended", "large", 8)[0] == 0
    assert step(97, "met_condition", "large", 8, state=WARM)[0] == 100


@pytest.mark.parametrize("kw", [
    dict(event="praise", tier="small", delta=1), dict(event=None, tier="small", delta=1),
    dict(event="friendly", tier="huge", delta=1), dict(event="friendly", tier="small", delta="2"),
    dict(event="offended", tier="small", delta=1.5), dict(event="offended", tier="small", delta=True),
])
def test_r16_invalid_input_raises_so_the_caller_keeps_everything(kw):
    with pytest.raises(ValueError):
        apply_event(40, RelationState(), psy(), met_index=0, **kw)


def test_r17_state_round_trips_and_old_saves_get_defaults():
    s = RelationState("offended", 2, 0, 55)
    assert RelationState.from_dict(s.to_dict()) == s
    for junk in (None, "x", [], {}, {"last_event": "praise", "met_count": "2", "pre_offence": "55"},
                 {"last_event": ["offended"]}, {"last_event": {"a": 1}, "met_count": [3]}):
        assert RelationState.from_dict(junk) == RelationState()


def test_r18_default_condition_cards_keep_the_old_per_turn_ceiling():
    assert step(20, "met_condition", "large", 8, state=WARM, psyche=psy(own=False))[0] == 25
    assert step(20, "met_condition", "large", 8, state=WARM, psyche=psy(own=True))[0] == 28
    assert step(20, "offended", "large", 8, psyche=psy(own=False))[0] == 12


def test_r12_code_ignores_grudge_and_volatility():
    a = step(40, "repair", "small", 2, state=RelationState(pre_offence=60), psyche=psy(grudge_inertia="记仇", volatility="剧烈"))
    b = step(40, "repair", "small", 2, state=RelationState(pre_offence=60), psyche=psy(grudge_inertia="大度", volatility="平稳"))
    assert a == b


def test_r13_default_conditions_only_when_the_card_has_none():
    assert warming_conditions(psy()) == OWN
    assert warming_conditions(psy(own=False)) == list(DEFAULT_WARMING) and len(DEFAULT_WARMING) == 2


@pytest.mark.parametrize("aff,last,expect", [
    (80, "offended", "conflict"), (10, "trigger", "conflict"), (80, "neutral", "close"),
    (73, "", "close"), (72, "friendly", "normal"), (10, "repair", "normal"),
])
def test_r14_tier_selection(aff, last, expect):
    assert relational_tier(aff, RelationState(last_event=last)) == expect


def test_registry_has_the_five_new_psyche_fields():
    got = {k: (v.layer, v.kind) for k, v in REGISTRY.items() if k.startswith("psyche.") and
           k.split(".")[1] in ("warming_conditions", "relational_modes", "agreeableness_facets")}
    assert got == {
        "psyche.warming_conditions": ("state", "list"),
        "psyche.relational_modes.close": ("state", "scalar"),
        "psyche.relational_modes.normal": ("state", "scalar"),
        "psyche.relational_modes.conflict": ("state", "scalar"),
        "psyche.agreeableness_facets": ("stable", "list"),
    }
