# -*- coding: utf-8 -*-
"""D24 ③a：转卡前把查不到的摘录换回原文原句。

逐行对应任务表的九种情况：

  1  改字的摘录被替回原文句，条目挂上阶段、不在未定位区
  2  模型返回原文里没有的句子 → 不替换
  3  模型返回原文里有、但与原摘录相似度 < 75 的句子 → 不替换
  4  摘录找不到任何 ≥ 60 分的候选 → 不发给模型、不替换
  5  摘录规范化后不足 6 字 → 不处理
  6  所有摘录都能逐字定位 → 不调模型，结果与 card_from_draft 相同
  7  模型调用抛异常 → 打 warning，照常转卡，结果与改前相同
  8  草稿里的阶段锚点查不到 → 不处理（锚点不是摘录槽位）
  9  一张卡有多条待修摘录 → 只调用模型一次

原文与草稿都是字面量，不从被测模块取期望值 —— 从源码 import 就自证自明，变异杀不掉。
"""
from __future__ import annotations

import copy
import json
import logging

from core.card_draft import card_from_draft
from core.distiller import Distiller
from core.retro_regions import NOT_DETECTED

# 每行一句，行号即阶段证据的坐标系：阶段 1 从头到第 3 行，阶段 2 从第 3 行起。
_SRC = (
    "云绮冷冷地看着她。\n"
    "她偏想做那投潭的石子，不肯回头。\n"
    "云绮扬手又是一记耳光，这次打得兰香踉跄后退。\n"
    "兰香低头道：谢陛下隆恩册封，谢太后垂爱赐宝。\n"
)

_CHANGED = "她偏要做那投潭的石子"          # 原文「她偏想做那投潭的石子」
_ORIGINAL = "她偏想做那投潭的石子"


class _FakeLLM:
    def __init__(self, reply=None, *, error=None):
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, list[dict]]] = []
        self.last_usage = None

    def chat(self, system, messages, max_tokens=None, **_kw):
        self.calls.append((system, messages))
        if self.error is not None:
            raise self.error
        return self.reply


def _draft(behaviors, *, anchor="云绮扬手又是一记耳光"):
    return {
        "name": "云绮",
        "character_arc": {"axis": "从隐忍到反击", "phases": [
            {"label": "隐忍", "state": "投潭之前", "anchor": ""},
            {"label": "反击", "state": "投潭之后", "anchor": anchor},
        ]},
        "situation_behaviors": behaviors,
    }


def _behavior(quote, *, situation="被劝回头"):
    return {"situation": situation, "behavior": "偏要做那投潭的石子",
            "occurrences": [{"phase": 1, "quote": quote}]}


def _convert(data, reply=None, *, error=None):
    """真 Distiller（LLM 打桩）→ 新入口 `draft_to_card`。"""
    llm = _FakeLLM(reply, error=error)
    card = Distiller(llm).draft_to_card(data, _SRC, retro=NOT_DETECTED)
    return card, llm


def _slots(data):
    return [occ["quote"] for b in data["situation_behaviors"] for occ in b["occurrences"]]


def _unlocated(card):
    return card.model_dump()["character_arc"]["unlocated"]["behaviors"]


# ── 1 / 2 / 3 / 9：本次要改的行为（先红）────────────────────────────────────

def test_row1_changed_quote_is_replaced_and_lands_in_its_phase():
    data = _draft([_behavior(_CHANGED)])
    card, _llm = _convert(data, json.dumps([{"id": 1, "sentence": _ORIGINAL}]))

    assert _slots(data) == [_ORIGINAL]                 # 草稿里的摘录换成原文句
    phase1 = card.model_dump()["character_arc"]["phases"][0]["behaviors"]
    assert [b["situation"] for b in phase1] == ["被劝回头"]
    assert _unlocated(card) == []


def test_row2_sentence_absent_from_source_is_rejected():
    data = _draft([_behavior(_CHANGED)])
    card, _llm = _convert(data, json.dumps([{"id": 1, "sentence": "她偏要做那投潭的石头"}]))

    assert _slots(data) == [_CHANGED]                  # 不替换
    assert len(_unlocated(card)) == 1                  # 照旧进未定位区


def test_row3_low_similarity_sentence_is_rejected():
    data = _draft([_behavior(_CHANGED)])
    # 这句在原文里（第 4 行），但与原摘录的相似度远低于 75。
    card, _llm = _convert(data, json.dumps(
        [{"id": 1, "sentence": "谢陛下隆恩册封，谢太后垂爱赐宝"}], ensure_ascii=False))

    assert _slots(data) == [_CHANGED]
    assert len(_unlocated(card)) == 1


def test_row9_one_model_call_for_all_pending_quotes():
    data = _draft([
        _behavior(_CHANGED, situation="s1"),
        _behavior("云绮扬手又是一记巴掌", situation="s2"),
        _behavior("臣女谢过陛下隆恩册封", situation="s3"),
    ])
    reply = json.dumps([
        {"id": 1, "sentence": _ORIGINAL},
        {"id": 2, "sentence": "云绮扬手又是一记耳光"},
        {"id": 3, "sentence": "谢陛下隆恩册封"},
    ], ensure_ascii=False)

    card, llm = _convert(data, reply)

    assert len(llm.calls) == 1
    assert _slots(data) == [_ORIGINAL, "云绮扬手又是一记耳光", "谢陛下隆恩册封"]
    assert _unlocated(card) == []


# ── 4–8：不变的保证 ─────────────────────────────────────────────────────────

def test_row4_no_candidate_is_not_sent_to_the_model():
    data = _draft([_behavior("银河战舰在木星轨道集结完毕")])
    card, llm = _convert(data, "[]")

    assert llm.calls == []
    assert _slots(data) == ["银河战舰在木星轨道集结完毕"]
    assert len(_unlocated(card)) == 1


def test_row5_short_quote_is_not_processed():
    data = _draft([_behavior("投潭呀")])               # 规范化后 3 字 < 6
    card, llm = _convert(data, "[]")

    assert llm.calls == []
    assert _slots(data) == ["投潭呀"]
    assert len(_unlocated(card)) == 1


def test_row6_all_quotes_locate_no_model_call_and_same_as_before():
    data = _draft([_behavior(_ORIGINAL)])
    data["relationships"] = [{
        "target": "兰香", "relation": "主仆",
        "attitudes": [{"phase": 1, "attitude": "轻慢", "quote": "兰香低头道"}],
    }]
    expected = card_from_draft(copy.deepcopy(data), _SRC, retro=NOT_DETECTED)

    card, llm = _convert(data, error=AssertionError("所有摘录都能定位时不该调模型"))

    assert llm.calls == []
    assert card.model_dump() == expected.model_dump()


def test_row7_model_failure_keeps_the_card_unchanged(caplog):
    data = _draft([_behavior(_CHANGED)])
    expected = card_from_draft(copy.deepcopy(data), _SRC, retro=NOT_DETECTED)

    with caplog.at_level(logging.WARNING):
        card, _llm = _convert(data, error=RuntimeError("boom"))

    assert card.model_dump() == expected.model_dump()
    assert any("[quote_repair]" in r.getMessage() for r in caplog.records)


def test_row8_unlocatable_phase_anchor_is_left_alone():
    data = _draft([_behavior(_ORIGINAL)], anchor="这段原文根本不存在")
    expected = card_from_draft(copy.deepcopy(data), _SRC, retro=NOT_DETECTED)

    card, llm = _convert(data, error=AssertionError("锚点不是摘录槽位，不该调模型"))

    assert data["character_arc"]["phases"][1]["anchor"] == "这段原文根本不存在"
    assert llm.calls == []
    assert card.model_dump() == expected.model_dump()
