# -*- coding: utf-8 -*-
"""`core.card_relationships.dedupe_relationship_targets`：关系对象按名单归一后，同一 target 只留第一条。

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
from core.unlocated import move_unlocated

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


# ── 目标检查：关系对象按识别名单归一（D24 ①a） ─────────────────────────

ROSTER = [
    {"name": "云绮", "aliases": ["绮绮"]},
    {"name": "柳若芙", "aliases": ["慕容昭瑜"]},
    {"name": "甲", "aliases": ["阿三"]},
    {"name": "乙", "aliases": ["阿三"]},
]


def _card(relationships, attitudes=()):
    return CharacterCard.model_validate({
        "name": "云绮",
        "relationships": list(relationships),
        "character_arc": {
            "phases": [{"state": "s1"}, {"state": "s2"}],
            "unlocated": {"attitudes": list(attitudes)},
        },
    })


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == "core.card_relationships"]


def test_alias_and_primary_collapse_to_one_relationship():
    """同一人的两个写法（主名 + 名单里的别名）按名单归一 → 只留第一条。"""
    card = _card([
        {"target": "柳若芙", "relation": "A"},
        {"target": "慕容昭瑜", "relation": "B"},
    ])

    new, dropped = dedupe_relationship_targets(card, ROSTER)

    assert [(r.target, r.relation) for r in new.relationships] == [("柳若芙", "A")]
    assert len(dropped) == 1
    assert dropped[0]["original_target"] == "慕容昭瑜"


def test_alias_target_is_rewritten_to_primary_name():
    """只有别名写法的一条：target 换成名单里的主名，relation 不变。"""
    card = _card([{"target": "慕容昭瑜", "relation": "B"}])

    new, dropped = dedupe_relationship_targets(card, ROSTER)

    assert ([(r.target, r.relation) for r in new.relationships], dropped) == (
        [("柳若芙", "B")], [])


def test_target_outside_roster_is_kept_and_warned(caplog):
    """名单里没有的对象原样保留，并记一行「不在名单」的 WARNING。"""
    card = _card([{"target": "某人", "relation": "C"}])

    with caplog.at_level(logging.WARNING, logger="core.card_relationships"):
        new, dropped = dedupe_relationship_targets(card, ROSTER)

    assert [(r.target, r.relation) for r in new.relationships] == [("某人", "C")]
    assert dropped == []
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "某人" in warnings[0]


def test_unlocated_attitude_follows_the_merged_target():
    """未定位态度与关系用同一套映射换名，挪回时能按主名找到留下的关系。"""
    card = _card([
        {"target": "柳若芙", "relation": "A"},
        {"target": "慕容昭瑜", "relation": "B"},
    ], attitudes=[{"target": "慕容昭瑜", "attitude": "X"}])

    new, _ = dedupe_relationship_targets(card, ROSTER)
    assert [a.target for a in new.character_arc.unlocated.attitudes] == ["柳若芙"]

    moved = move_unlocated(new, section="attitudes", index=0, phase=1)
    assert [pa.attitude for pa in moved.relationships[0].phase_attitudes] == ["X"]


def test_alias_shared_by_two_people_is_not_canonicalized(caplog):
    """「阿三」同时是甲和乙的别名 → 不能判定归属，原样保留并记 WARNING。"""
    card = _card([{"target": "阿三", "relation": "F"}])

    with caplog.at_level(logging.WARNING, logger="core.card_relationships"):
        new, dropped = dedupe_relationship_targets(card, ROSTER)

    assert [(r.target, r.relation) for r in new.relationships] == [("阿三", "F")]
    assert dropped == []
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "阿三" in warnings[0]
