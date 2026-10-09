# -*- coding: utf-8 -*-
"""蒸馏遗留④ 目标检查：模型漏写阶段号，只撤回那一条，不让整张卡失败。

现状（main b9b8cd2）：`DraftOccurrence.phase` / `DraftAttitude.phase` 是必填字段，草稿里漏写
`phase` 键时 pydantic 抛 ValidationError，整张卡在 95% 处失败（#126 已处理「写了但不是数字」，
没处理「没写」）。

目标：阶段号的全部规则收在一处（一个共用基类）——漏写按 0 处理并打一条 warning，交给
规则 0（`_valid_number_rows`）撤回那一条；无阶段的卡里 0 本来就合法，照常进顶层。发给模型的
草稿结构**一个字节都不变**（phase 仍是必填、没有默认值）。

走真实代码：`card_from_draft`、`draft_schema`。预言独立于被测代码：句子、阶段号是本文件的字面量。

K1 有阶段的卡：做法 occurrence 漏写 phase → 整卡不抛错；这一条不进任何阶段也不进顶层；同卡另一条照常
K2 有阶段的卡：关系 attitude 漏写 phase → 整卡不抛错；同一关系的另一条态度照常
K3 漏写时有一条 warning，写明是「缺阶段编号」
K4 无阶段的卡：漏写 phase → 按 0（无阶段）照常进顶层
K5 发给模型的结构：两处 phase 仍在 required 里、没有 default（main 上就绿：回归守卫）
K6 结构：phase 字段只在一个地方声明，两个草稿类都继承它
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_draft import card_from_draft, draft_schema

_SRC = "开头甲甲甲。中间乙乙乙。"
_Q1, _Q2 = "开头甲甲甲", "中间乙乙乙"
_LOST, _KEPT = "讨酒时漏了阶段号的做法", "讨酒时排出九文大钱"


def _phased(**extra):
    d = {"name": "孔乙己", "character_arc": {"phases": [
        {"label": "一", "state": "s1", "anchor": ""},
        {"label": "二", "state": "s2", "anchor": _Q2}]}}
    d.update(extra)
    return d


def _behaviors(lost_occ):
    return [
        {"situation": "讨酒", "behavior": _LOST, "occurrences": [lost_occ]},
        {"situation": "被问", "behavior": _KEPT, "occurrences": [{"phase": 1, "quote": _Q1}]},
    ]


def _all_behaviors(card):
    top = [b.behavior for b in card.situation_behaviors]
    per = [b.behavior for p in card.character_arc.phases for b in p.behaviors]
    loose = [b.behavior for b in card.character_arc.unlocated.behaviors]
    return top, per, loose


def test_k0_fixture_sentences_are_distinct():
    assert _LOST != _KEPT and _LOST not in _KEPT and _KEPT not in _LOST


def test_k1_occurrence_without_phase_drops_only_that_row():
    card = card_from_draft(_phased(situation_behaviors=_behaviors({"quote": _Q1})), _SRC)
    top, per, loose = _all_behaviors(card)
    assert _LOST not in top + per + loose, "漏写阶段号的那一条不该落到任何地方"
    assert _KEPT in per, "同卡另一条没照常落阶段"


def test_k2_attitude_without_phase_drops_only_that_attitude():
    rel = {"target": "掌柜", "relation": "主顾", "attitudes": [
        {"attitude": "漏号的态度", "quote": _Q1},
        {"phase": 2, "attitude": "第二阶段的态度", "quote": _Q2}]}
    card = card_from_draft(_phased(relationships=[rel]), _SRC)
    atts = [pa.attitude for r in card.relationships for pa in r.phase_attitudes]
    assert "第二阶段的态度" in atts, "同一关系的另一条态度没照常落阶段"
    assert "漏号的态度" not in atts


def test_k3_missing_phase_is_warned(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card_from_draft(_phased(situation_behaviors=_behaviors({"quote": _Q1})), _SRC)
    assert any("缺阶段编号" in r.getMessage() for r in caplog.records), "漏写阶段号没有 warning"


def test_k4_unphased_card_keeps_row_at_top():
    draft = {"name": "孔乙己", "situation_behaviors": [
        {"situation": "讨酒", "behavior": _LOST, "occurrences": [{"quote": _Q1}]}]}
    card = card_from_draft(draft, _SRC)
    assert _LOST in [b.behavior for b in card.situation_behaviors], "无阶段的卡，漏号应按 0 进顶层"


@pytest.mark.parametrize("cls", ["DraftOccurrence", "DraftAttitude"])
def test_k5_schema_sent_to_model_still_requires_phase(cls):
    defs = draft_schema()["$defs"]
    assert cls in defs, f"$defs 里没有 {cls}"
    # 与 main（b9b8cd2）上发给模型的形态逐字相同：phase 必填、只有 title 与 type
    assert defs[cls].get("required") == ["phase"], f"{cls} 的 required 变了：{defs[cls].get('required')}"
    assert defs[cls]["properties"]["phase"] == {"title": "Phase", "type": "integer"}, \
        f"{cls}.phase 的结构变了：{defs[cls]['properties']['phase']}"


def test_k6_phase_declared_once_and_shared():
    from core import card_draft as cd
    owners = [n for n, c in vars(cd).items()
              if isinstance(c, type) and "phase" in getattr(c, "__annotations__", {})]
    assert len(owners) == 1, f"phase 字段应只在一个类里声明，现在：{owners}"
    base = getattr(cd, owners[0])
    assert issubclass(cd.DraftOccurrence, base) and issubclass(cd.DraftAttitude, base)
