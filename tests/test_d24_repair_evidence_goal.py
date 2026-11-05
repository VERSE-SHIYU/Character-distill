# -*- coding: utf-8 -*-
"""D24-B 目标检查：修摘录、核对口头禅时，没成的每一条都留一条 WARNING 写明原因。

逐行对应提示词 Q1–Q10 / C1–C7。新行为（Q1–Q7、C1–C5）先以 `xfail(strict=True)`
验证为红；对照行（Q8–Q10、C6、C7）改前就该绿。

原文与期望值都是字面量，不从被测模块取。
"""
from __future__ import annotations

import copy
import json
import logging
from fractions import Fraction

import core.distiller as distiller_module
from core.card_draft import card_from_draft
from core.distiller import Distiller
from core.retro_regions import NOT_DETECTED
from core.schema import CharacterCard

# ── 修摘录：夹具与假模型（写法照 tests/test_d24_quote_repair_goal.py）────────

_SRC = (
    "云绮冷冷地看着她。\n"
    "她偏想做那投潭的石子，不肯回头。\n"
    "云绮扬手又是一记耳光，这次打得兰香踉跄后退。\n"
    "兰香低头道：谢陛下隆恩册封，谢太后垂爱赐宝。\n"
)
_CHANGED = "她偏要做那投潭的石子"            # 原文「她偏想做那投潭的石子」
_CHANGED_2 = "云绮扬手又是一记巴掌"          # 原文「云绮扬手又是一记耳光」
_ORIGINAL = "她偏想做那投潭的石子"
_LOW_SIMILARITY = "谢陛下隆恩册封，谢太后垂爱赐宝"   # 在原文里，与 _CHANGED 相似度 0.0


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
    llm = _FakeLLM(reply, error=error)
    card = Distiller(llm).draft_to_card(data, _SRC, retro=NOT_DETECTED)
    return card, llm


def _quote_warnings(caplog):
    return [r for r in caplog.records
            if r.levelno == logging.WARNING and "[quote_repair]" in r.getMessage()]


def _same_card_as_direct_conversion(card, data):
    """Q10：只多日志，不改输出 —— 卡 == 直接 `card_from_draft`。"""
    expected = card_from_draft(copy.deepcopy(data), _SRC, retro=NOT_DETECTED)
    assert card.model_dump() == expected.model_dump()


# ── Q1–Q7：新行为（先红）──────────────────────────────────────────────────

def test_q1_too_short_quote_logs_reason(caplog):
    data = _draft([_behavior("投潭呀")])                    # 规范化后 3 字 < 6
    with caplog.at_level(logging.WARNING):
        card, llm = _convert(data, "[]")

    assert llm.calls == []
    rows = _quote_warnings(caplog)
    assert len(rows) == 1, [r.getMessage() for r in caplog.records]
    assert "投潭呀" in rows[0].getMessage()
    assert "太短" in rows[0].getMessage()
    _same_card_as_direct_conversion(card, data)


def test_q2_no_candidate_logs_reason(caplog):
    data = _draft([_behavior("银河战舰在木星轨道集结完毕")])
    with caplog.at_level(logging.WARNING):
        card, llm = _convert(data, "[]")

    assert llm.calls == []
    rows = _quote_warnings(caplog)
    assert len(rows) == 1, [r.getMessage() for r in caplog.records]
    assert "无 ≥60 分候选" in rows[0].getMessage()
    _same_card_as_direct_conversion(card, data)


def test_q3_missing_row_logs_model_omitted(caplog):
    data = _draft([
        _behavior(_CHANGED, situation="s1"),
        _behavior(_CHANGED_2, situation="s2"),
    ])
    reply = json.dumps([{"id": 1, "sentence": _ORIGINAL}], ensure_ascii=False)
    with caplog.at_level(logging.WARNING):
        card, llm = _convert(data, reply)

    assert len(llm.calls) == 1
    rows = [r.getMessage() for r in _quote_warnings(caplog)]
    omitted = [m for m in rows if "模型漏回" in m]
    assert len(omitted) == 1, rows
    assert _CHANGED_2 in omitted[0]
    raw = [m for m in rows if "模型原始回复" in m]
    assert len(raw) == 1, "一批里有没修成的，只记一条模型原始回复"
    assert reply in raw[0]
    _same_card_as_direct_conversion(card, data)


def test_q4_null_sentence_logs_empty_reply(caplog):
    data = _draft([_behavior(_CHANGED)])
    reply = json.dumps([{"id": 1, "sentence": None}])
    with caplog.at_level(logging.WARNING):
        card, _llm = _convert(data, reply)

    rows = [r.getMessage() for r in _quote_warnings(caplog)]
    assert len([m for m in rows if "模型回空" in m]) == 1, rows
    raw = [m for m in rows if "模型原始回复" in m]
    assert len(raw) == 1 and reply in raw[0]
    _same_card_as_direct_conversion(card, data)


def test_q5_sentence_absent_from_source_logs_reason(caplog):
    data = _draft([_behavior(_CHANGED)])
    reply = json.dumps([{"id": 1, "sentence": "她偏要做那投潭的石头"}],
                       ensure_ascii=False)
    with caplog.at_level(logging.WARNING):
        card, _llm = _convert(data, reply)

    rows = [r.getMessage() for r in _quote_warnings(caplog)]
    assert len([m for m in rows if "原文查不到" in m]) == 1, rows
    _same_card_as_direct_conversion(card, data)


def test_q6_low_similarity_logs_reason_and_score(caplog):
    data = _draft([_behavior(_CHANGED)])
    reply = json.dumps([{"id": 1, "sentence": _LOW_SIMILARITY}], ensure_ascii=False)
    with caplog.at_level(logging.WARNING):
        card, _llm = _convert(data, reply)

    rows = [r.getMessage() for r in _quote_warnings(caplog)]
    assert len([m for m in rows if "相似度 0.0 < 75" in m]) == 1, rows
    _same_card_as_direct_conversion(card, data)


def test_q7_one_raw_reply_warning_even_with_multiple_failures(caplog):
    data = _draft([
        _behavior(_CHANGED, situation="s1"),
        _behavior(_CHANGED_2, situation="s2"),
    ])
    reply = json.dumps([{"id": 1, "sentence": None}, {"id": 2, "sentence": None}])
    with caplog.at_level(logging.WARNING):
        _card, _llm = _convert(data, reply)

    rows = [r.getMessage() for r in _quote_warnings(caplog)]
    assert len([m for m in rows if "模型回空" in m]) == 2, rows
    raw = [m for m in rows if "模型原始回复" in m]
    assert len(raw) == 1, "多条没修成也只记一条模型原始回复"
    assert reply in raw[0]


# ── Q8–Q10：对照（改前就绿）────────────────────────────────────────────────

def test_q8_all_repaired_logs_only_the_success_line(caplog):
    data = _draft([_behavior(_CHANGED)])
    reply = json.dumps([{"id": 1, "sentence": _ORIGINAL}], ensure_ascii=False)
    with caplog.at_level(logging.WARNING):
        card, _llm = _convert(data, reply)

    rows = [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING]
    assert len([m for m in rows if "修摘录：" in m]) == 1
    assert not [m for m in rows if "模型原始回复" in m]
    assert not [m for m in rows if "未修成" in m]
    _same_card_as_direct_conversion(card, data)


def test_q9_all_located_logs_nothing(caplog):
    data = _draft([_behavior(_ORIGINAL)])
    with caplog.at_level(logging.WARNING):
        card, llm = _convert(data, error=AssertionError("不该调模型"))

    assert llm.calls == []
    rows = [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING]
    assert not [m for m in rows if "[quote_repair]" in m or "修摘录" in m]
    _same_card_as_direct_conversion(card, data)


# Q10 的断言已写进 Q1–Q9 每条：卡与直接 `card_from_draft` 逐字相同。


# ── 口头禅：夹具与假模型（写法照 tests/test_d24_catchphrase_goal.py）────────

_NAME = "云绮"
_ROSTER = [{"name": _NAME, "aliases": []}, {"name": "掌柜", "aliases": []}]

# 4 章；本人名字每章都出现（出场 4 章）。「酒」在第 1、2 章的台词里（2/4 ≥ 1/3），
# 「不多」只在第 1 章（1/4 < 1/3）。
_CONTENT = (
    "第1章\n"
    "云绮看着窗外……\n"
    "云绮道：“酒。”\n"
    "云绮道：“不多。”\n"
    "第2章\n"
    "云绮走了进来。\n"
    "云绮道：“酒。”\n"
    "第3章\n"
    "云绮笑了笑。\n"
    "第4章\n"
    "云绮离开了。\n"
)
_CONTENT_NO_CHAPTERS = (
    "云绮看着窗外……\n"
    "云绮道：“酒。”\n"
    "云绮走了进来。\n"
    "云绮道：“酒。”\n"
    "云绮道：“不多。”\n"
)
# 5 章都出场；「酒」只在第 1 章（1/5）。
_CONTENT_FIVE_CHAPTERS = (
    "第1章\n"
    "云绮道：“酒。”\n"
    "第2章\n"
    "云绮走了进来。\n"
    "第3章\n"
    "云绮笑了笑。\n"
    "第4章\n"
    "云绮离开了。\n"
    "第5章\n"
    "云绮看着窗外。\n"
)
# 3 章都出场；「酒」只在第 1 章（恰好 1/3）。
_CONTENT_THREE_CHAPTERS = (
    "第1章\n"
    "云绮道：“酒。”\n"
    "第2章\n"
    "云绮走了进来。\n"
    "第3章\n"
    "云绮笑了笑。\n"
)


class _VerifyLLM:
    """假模型：核对调用按顺序回一组说话人；挑对话示例的格子一律填 0。"""

    model = "fake-d24"
    last_usage = None

    def __init__(self, batches):
        self._batches = [list(b) for b in batches]
        self.calls: list[str] = []

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        self.calls.append(function["name"])
        if function["name"] == "verify_catchphrase":
            speakers = self._batches.pop(0) if self._batches else []
            return {f"who{i}": s for i, s in enumerate(speakers, 1)}
        return {k: (0 if k.startswith("pick") else "无法判断")
                for k in function["parameters"]["properties"]}

    @property
    def verify_calls(self) -> int:
        return self.calls.count("verify_catchphrase")


def _card(catchphrases) -> CharacterCard:
    return CharacterCard.model_validate(
        {"name": _NAME, "speaking_style": {"catchphrases": list(catchphrases)}})


def _finalize(card, content, llm):
    return Distiller(llm=llm, config_path=None).finalize_card(
        card, content, _NAME, [], _ROSTER)


def _catchphrase_warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "口头禅「" in r.getMessage()]


# ── C1–C5：新行为（先红）──────────────────────────────────────────────────

def test_c1_unknown_chapters_logs_reason_for_each(caplog):
    llm = _VerifyLLM([])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["酒", "不多"]), _CONTENT_NO_CHAPTERS, llm)

    assert out.speaking_style.catchphrases == []
    assert llm.verify_calls == 0
    rows = _catchphrase_warnings(caplog)
    assert len(rows) == 2, rows
    for msg in rows:
        assert "已删" in msg
        assert "章节" not in msg, "原因里不能出现「章节」，见 test_d24_chapters_goal"
    assert not [m for m in rows if "第N章" not in m]


def test_c2_too_few_chapters_logs_both_counts(caplog):
    llm = _VerifyLLM([])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["不多"]), _CONTENT, llm)

    assert out.speaking_style.catchphrases == []
    assert llm.verify_calls == 0
    rows = _catchphrase_warnings(caplog)
    assert len(rows) == 1, rows
    assert "含该词的章 1 / 出场章 4 < 1/3" in rows[0]


def test_c3_said_by_roster_other_logs_the_speaker(caplog):
    llm = _VerifyLLM([["掌柜", "掌柜"]])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["酒"]), _CONTENT, llm)

    assert out.speaking_style.catchphrases == []
    assert llm.verify_calls == 1
    rows = _catchphrase_warnings(caplog)
    assert len(rows) == 1, rows
    assert "模型判为「掌柜」说" in rows[0]


def test_c4_said_by_outsider_logs_the_outsider(caplog):
    llm = _VerifyLLM([["名单外的人", _NAME]])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["酒"]), _CONTENT, llm)

    assert out.speaking_style.catchphrases == []
    rows = _catchphrase_warnings(caplog)
    assert len(rows) == 1, rows
    assert "模型判为「名单外的人」说" in rows[0]


def test_c5_too_few_subject_chapters_logs_both_counts(caplog):
    llm = _VerifyLLM([[_NAME, "无法判断"]])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["酒"]), _CONTENT, llm)

    assert out.speaking_style.catchphrases == []
    assert llm.verify_calls == 1
    rows = _catchphrase_warnings(caplog)
    assert len(rows) == 1, rows
    assert "本人说的章 1 / 出场章 4 < 1/3" in rows[0]


# ── C6–C7：对照（改前就绿）────────────────────────────────────────────────

def test_c6_all_pass_logs_nothing_and_keeps_the_card(caplog):
    llm = _VerifyLLM([[_NAME, _NAME]])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["酒"]), _CONTENT, llm)

    assert out.model_dump() == _card(["酒"]).model_dump()
    assert not [r for r in caplog.records if "口头禅" in r.getMessage()]


def test_c7_dropped_set_is_unchanged(caplog):
    llm = _VerifyLLM([[_NAME, _NAME]])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["酒", "不多"]), _CONTENT, llm)

    assert out.speaking_style.catchphrases == ["酒"]
    assert set(["酒", "不多"]) - set(out.speaking_style.catchphrases) == {"不多"}


def test_c8_threshold_in_reason_follows_the_constant(caplog, monkeypatch):
    """C8：原因里的门槛跟着常量走 —— 改成 1/4，日志显示 < 1/4（不是 1/3）。"""
    monkeypatch.setattr(distiller_module, "CATCHPHRASE_MIN_CHAPTER_SHARE",
                        Fraction(1, 4))
    llm = _VerifyLLM([])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["酒"]), _CONTENT_FIVE_CHAPTERS, llm)

    assert out.speaking_style.catchphrases == []
    assert llm.verify_calls == 0
    rows = _catchphrase_warnings(caplog)
    assert len(rows) == 1, rows
    assert "含该词的章 1 / 出场章 5 < 1/4" in rows[0]


def test_c9_exact_third_is_kept_and_not_logged(caplog):
    """C9：比值恰好 1/3 不算「低于」—— 保留，且没有删除日志。"""
    llm = _VerifyLLM([[_NAME]])
    with caplog.at_level(logging.WARNING):
        out = _finalize(_card(["酒"]), _CONTENT_THREE_CHAPTERS, llm)

    assert out.speaking_style.catchphrases == ["酒"]
    assert llm.verify_calls == 1
    assert not [m for m in _catchphrase_warnings(caplog) if "酒" in m]
