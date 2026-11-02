# -*- coding: utf-8 -*-
"""`core.card_relationships.dedupe_relationship_targets`：同一 target 的关系条目只留第一条；对不上名单主名的记 WARNING、不改名。

刘姥姥验收实测：一张卡里「贾母」出现两条（`relation` 分别是「老亲家、贾府老祖宗」与
「老亲家」）—— 同一个人被拆成两条，注入时同一段关系被说两遍。§10 C4 的关系判据就是
「不许有重复 target」，这一层让产品自己保证它，而不是留给验收去发现。

**不合并文字。** 两条的 relation/attitude/note 措辞不同，谁去谁留没有依据；合并只会编出
第三条既非此也非彼的表述。留**第一条**是可复算的规则（顺序由模型给的原文决定）。

**变异。** ① 去掉去重 → T1 的「只剩第一条」红；② 改成按整条 dict 去重（relation 不同就
都留）→ T1 红；③ 去掉 `logger.warning` → T3 红。
"""
from __future__ import annotations

import logging

from core.card_relationships import dedupe_relationship_targets
from core.schema import CharacterCard

CARD = {
    "name": "刘姥姥",
    "relationships": [
        {"target": "贾母", "relation": "老亲家、贾府老祖宗", "attitude": "敬着"},
        {"target": "贾母", "relation": "老亲家", "attitude": "也敬着"},
        {"target": "凤姐", "relation": "远房亲戚"},
    ],
}


def _dedupe(d: dict = CARD):
    card = CharacterCard.model_validate(d)
    return card, dedupe_relationship_targets(card, roster=[])


def test_a_repeated_target_keeps_only_the_first_entry():
    """同一 target 的两条只留第一条（整条丢弃，不合并文字），丢弃清单报出 target 与被丢的 relation。

    变异：去掉去重、或改成按整条 dict 去重（relation 不同就都留）→ 第一条断言红。
    """
    card, (new, dropped) = _dedupe()

    assert [(r.target, r.relation) for r in new.relationships] == [
        ("贾母", "老亲家、贾府老祖宗"), ("凤姐", "远房亲戚")]
    assert dropped == [{"field": "relationships", "target": "贾母", "relation": "老亲家"}]
    assert len(card.relationships) == 3, "原卡不就地改"


def test_distinct_targets_are_left_alone():
    """target 各不相同 → 一个都不丢，也不记日志（去重只对同 target 生效）。"""
    _, (new, dropped) = _dedupe({"name": "刘姥姥", "relationships": [
        {"target": "贾母", "relation": "老亲家"},
        {"target": "凤姐", "relation": "远房亲戚"},
    ]})

    assert [r.target for r in new.relationships] == ["贾母", "凤姐"]
    assert dropped == []


def test_each_dropped_entry_logs_one_warning_line(caplog):
    """每丢一条记一行 `logger.warning`，带字段、target 和被丢的 relation（供 `docker logs` 追）。

    变异：去掉 warning → 行数断言红；不报被丢的 relation → 内容断言红。
    """
    with caplog.at_level(logging.WARNING, logger="core.card_relationships"):
        _, (_, dropped) = _dedupe({"name": "刘姥姥", "relationships": [
            {"target": "贾母", "relation": "第一条"},
            {"target": "贾母", "relation": "第二条"},
            {"target": "贾母", "relation": "第三条"},
        ]})

    assert [d["relation"] for d in dropped] == ["第二条", "第三条"]
    msgs = [r.getMessage() for r in caplog.records
            if r.name == "core.card_relationships"]
    assert len(msgs) == 2, msgs
    assert "relationships" in msgs[0] and "贾母" in msgs[0] and "第二条" in msgs[0]
    assert "第三条" in msgs[1]


# ── D24 ①a：名单只用于观测，不按别名换名 ─────────────────────────────

ROSTER = [
    {"name": "云绮", "aliases": ["绮绮"]},
    {"name": "柳若芙", "aliases": ["慕容昭瑜"]},
    {"name": "祈灼", "aliases": ["公子"]},
]


def _card(relationships):
    return CharacterCard.model_validate({"name": "云绮", "relationships": list(relationships)})


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == "core.card_relationships"]


def test_alias_target_is_kept_as_written_and_warned(caplog):
    """别名写法不换成主名（别名表离开原文不能认人），两条都留；别名那条记 WARNING。"""
    card = _card([
        {"target": "柳若芙", "relation": "A"},
        {"target": "慕容昭瑜", "relation": "B"},
    ])

    with caplog.at_level(logging.WARNING, logger="core.card_relationships"):
        new, dropped = dedupe_relationship_targets(card, ROSTER)

    assert [(r.target, r.relation) for r in new.relationships] == [
        ("柳若芙", "A"), ("慕容昭瑜", "B")]
    assert dropped == []
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "慕容昭瑜" in warnings[0], warnings


def test_title_alias_is_not_mapped_to_its_roster_owner(caplog):
    """「公子」挂在祈灼名下，也不能把写「公子」的关系改成祈灼、更不能因此丢掉真的祈灼那条。"""
    card = _card([
        {"target": "公子", "relation": "X"},
        {"target": "祈灼", "relation": "Y"},
    ])

    with caplog.at_level(logging.WARNING, logger="core.card_relationships"):
        new, dropped = dedupe_relationship_targets(card, ROSTER)

    assert [(r.target, r.relation) for r in new.relationships] == [
        ("公子", "X"), ("祈灼", "Y")]
    assert dropped == []
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "公子" in warnings[0], warnings


def test_target_outside_roster_is_kept_and_warned(caplog):
    """名单里没有的对象原样保留，并记一行 WARNING。"""
    card = _card([{"target": "某人", "relation": "C"}])

    with caplog.at_level(logging.WARNING, logger="core.card_relationships"):
        new, dropped = dedupe_relationship_targets(card, ROSTER)

    assert [(r.target, r.relation) for r in new.relationships] == [("某人", "C")]
    assert dropped == []
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "某人" in warnings[0]


def test_primary_names_log_nothing(caplog):
    """对象都是名单主名：卡不变、不记任何 WARNING。"""
    card = _card([{"target": "柳若芙", "relation": "A"}, {"target": "祈灼", "relation": "Y"}])

    with caplog.at_level(logging.WARNING, logger="core.card_relationships"):
        new, dropped = dedupe_relationship_targets(card, ROSTER)

    assert new == card and dropped == []
    assert _warnings(caplog) == []


def test_finalize_card_hands_the_roster_to_dedupe(caplog):
    """产卡收尾（三条通道共用的 `finalize_card`）把名单交给去重：名单外的对象在日志里看得到。"""
    from core.distiller import Distiller

    card = CharacterCard.model_validate(
        {"name": "云绮", "relationships": [{"target": "某人", "relation": "C"}]})

    with caplog.at_level(logging.WARNING, logger="core.card_relationships"):
        out = Distiller(llm=None, config_path=None).finalize_card(
            card, "正文", "云绮", [], [{"name": "云绮", "aliases": []}])

    assert [r.target for r in out.relationships] == ["某人"]
    assert any("某人" in m for m in _warnings(caplog)), _warnings(caplog)
