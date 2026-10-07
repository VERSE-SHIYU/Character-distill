# -*- coding: utf-8 -*-
"""评估协议的单元测试：提示词里的数字和名字都来自规则表，读回答只取协议字段。"""

from __future__ import annotations

import json

from affinity_verdict import verdict
from core import affinity_protocol as protocol
from core.affinity_rules import DEFAULT_WARMING, EVENTS, TIERS
from core.schema import PsycheProfile


def test_rules_text_is_generated_from_the_rule_table():
    text = protocol.render_rules(PsycheProfile(warming_conditions=["条件甲", "条件乙"]))
    for event in EVENTS:
        assert f"{event}=" in text
    for name, (lo, hi) in TIERS.items():
        assert f"{name}（{lo}–{hi}）" in text
    assert "  0. 条件甲\n  1. 条件乙\n" in text
    assert not any(c in text for c in DEFAULT_WARMING)


def test_rules_text_falls_back_to_the_default_conditions():
    text = protocol.render_rules(PsycheProfile())
    assert all(c in text for c in DEFAULT_WARMING)


def test_json_fields_and_reader_use_the_same_names():
    fields = protocol.render_json_fields()
    reply = verdict(+4)
    for key in reply:
        assert f'"{key}"' in fields
    assert protocol.read_verdict(json.loads(json.dumps(reply))) == {
        "event": "met_condition", "tier": "medium", "delta": 4, "met_index": 0}
    assert protocol.read_verdict({}) == {"event": None, "tier": None, "delta": None, "met_index": None}
