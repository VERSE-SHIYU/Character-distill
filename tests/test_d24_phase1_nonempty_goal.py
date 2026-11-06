# -*- coding: utf-8 -*-
"""D24 目标检查：阶段 1 不许为空 —— 正文开始前的经历只算背景；按章号定起点时，
空阶段 1 让弧线组重做一次，仍为空则记 WARNING 照常往下走。

G1–G7 先以 ``xfail(strict=True)`` 落红；C1–C2 是对照（改前就绿）。
替身照 ``test_phase_start_chapter_goal.py`` 的 ``_group_llm`` / ``_run`` 写：
同一入口 ``distill_incremental_stream``、同一套组回包、同一个关系替身。差别只有：
G6 每次调用按顺序取下一份弧线（要测重做）；``calls`` 记成列表（要数 G6 次数）。
"""
from __future__ import annotations

import json
import logging
import re

import pytest

import core.distiller as D
from core import phase_anchoring
from core.card_draft import CardDraft, draft_schema
from core.distiller import PHASE_DEPENDENT_GROUPS, Distiller
from core.schema import FORMAT_GROUPS

_NAME = "阿灼"
_ALIAS = "小灼"
_USAGE = {"prompt_tokens": 1, "completion_tokens": 1}
_REL_MARKER = "只产出这几个人物与主角的关系"

_FAR_1 = "远处的盐场上，晒盐人正把盐耙推过白茫茫的盐田。"
_CH2 = "城南米铺的掌柜一早就在柜台后面拨着算盘，嘴里念着米价。"
_ALIAS_LINE = "小灼推开窗，说他要进宫去。"
_SRC = "\n".join([
    "第1章 楼上",
    "阿灼独自坐在楼上抚琴，不见来客。",
    *[f"楼下的伙计搬来第{i}坛酒。" for i in range(1, 9)],
    _FAR_1,
    "第2章 米铺",
    _CH2,
    "第3章 入宫",
    _ALIAS_LINE,
    "第4章 日札",
    "日札：那年在桥边初遇她，阿灼从此心动。",
    "第5章 退路",
    "阿灼对她说，他愿意做她的退路。",
])
# 第 1、2 章都没有他，第 3 章才出场。
_SRC_LATE = "\n".join([
    "第1章 空",
    _CH2,
    "第2章 空",
    _FAR_1,
    "第3章 出场",
    "阿灼推开窗，说他要进宫去。",
])
_CHAPTERLESS = "阿灼独自坐在楼上抚琴。\n后来阿灼进了宫。\n" + _FAR_1

# 第一次：阶段 2 从第 1 章开始（= 此人在摘录里的首章）→ 阶段 1 为空。
_ARC_EMPTY = {"axis": "从骄纵到收敛", "phases": [
    {"label": "骄纵跋扈", "state": "首次出场时的做派", "start_chapter": 0, "boundary_examples": []},
    {"label": "初入宫", "state": "刚入宫时", "start_chapter": 1, "boundary_examples": []},
    {"label": "转折", "state": "第3章之后", "start_chapter": 3, "boundary_examples": []},
]}
# 重做后：阶段 1 从首次出场开始，阶段 2 落在第 3 章。
_ARC_OK = {"axis": "从骄纵到收敛", "phases": [
    {"label": "骄纵跋扈", "state": "首次出场时的做派", "start_chapter": 0, "boundary_examples": []},
    {"label": "转折", "state": "第3章之后", "start_chapter": 3, "boundary_examples": []},
    {"label": "后半", "state": "第5章之后", "start_chapter": 5, "boundary_examples": []},
]}
_ANCHOR_ARC = {"axis": "从拒人到入世", "phases": [
    {"label": "拒人", "state": "独坐楼上之时", "anchor": "", "boundary_examples": []},
    {"label": "入世", "state": "进宫之后", "anchor": "后来阿灼进了宫。", "boundary_examples": []},
]}


def _phases(*starts):
    return [{"label": f"阶段{i}", "state": "", "start_chapter": s}
            for i, s in enumerate(starts, 1)]


def _payload_for(group: str, arc: dict) -> dict:
    base = CardDraft(name=_NAME).model_dump()
    if group == "G6":
        base["character_arc"] = arc
    if group == "G7":
        base["situation_behaviors"] = []
        base["key_memories"] = []
    if group == "G5":
        base["relationships"] = []
    return {k: base[k] for k in FORMAT_GROUPS[group]}


def _group_llm(arcs: list[dict]):
    """按组回包；G6 每次调用按顺序取下一份弧线，用完后停在最后一份。"""
    calls: list[dict] = []
    state = {"i": 0}

    class _Client:
        async def close(self):
            pass

    class _LLM:
        last_usage = None
        model = "stub"

        def _make_async_client(self):
            return _Client()

        async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
            return ("片段分析", _USAGE)

        def chat_stream_long(self, system, messages, max_tokens=None, **kw):
            if _REL_MARKER in system:
                yield "[]"
                return _USAGE
            group = re.search(r"CharacterCard\[(G\d)\]", system).group(1)
            if group == "G6":
                arc = arcs[min(state["i"], len(arcs) - 1)]
                state["i"] += 1
            else:
                arc = arcs[-1]
            calls.append({"group": group, "system": system, "messages": messages})
            yield json.dumps(_payload_for(group, arc), ensure_ascii=False)
            return _USAGE

    return _LLM(), calls


def _run(text: str, arcs: list[dict]):
    llm, calls = _group_llm(arcs)
    frames = list(Distiller(llm=llm, config_path=None).distill_incremental_stream(
        text, _NAME, aliases=[_ALIAS], text_type="story"))
    errors = [f for f in frames if isinstance(f, dict) and "error" in f]
    assert not errors, errors
    drafts = [json.loads(f) for f in frames if isinstance(f, str)]
    assert len(drafts) == 1, frames
    return calls, drafts[0]


@pytest.fixture(autouse=True)
def _no_usage_rows(monkeypatch):
    monkeypatch.setattr(D, "try_record_usage", lambda *a, **kw: None)


def _g6_calls(calls):
    return [c for c in calls if c["group"] == "G6"]


def _retry_warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
            and "阶段 1 在正文里一章都没有" in r.getMessage()]


# ── G1 / G2：判定函数 ────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True)
def test_g1_empty_phase1_is_detected_on_real_starts():
    from core.phase_anchoring import phase1_has_no_chapter

    assert phase1_has_no_chapter(_phases(0, 1, 100, 480), 1) is True   # 11-05 云绮
    assert phase1_has_no_chapter(_phases(0, 5, 9), 5) is True          # 首章晚出场


@pytest.mark.xfail(strict=True)
def test_g2_non_empty_or_undecidable_is_not_flagged():
    from core.phase_anchoring import phase1_has_no_chapter

    assert phase1_has_no_chapter(_phases(0, 100, 320, 483), 1) is False   # 11-01
    assert phase1_has_no_chapter(_phases(0, 13, 469), 1) is False         # 11-03
    assert phase1_has_no_chapter(_phases(2, 11, 99), 1) is False          # 魏无羡
    assert phase1_has_no_chapter(_phases(0), 1) is False                  # 只有 1 个阶段
    anchor_only = [{"label": "一", "state": "", "start_chapter": 0},
                   {"label": "二", "state": "", "anchor": "某句原文"}]
    assert phase1_has_no_chapter(anchor_only, 1) is False                 # 阶段 2 只有锚点
    assert phase1_has_no_chapter(_phases(0, 1, 100, 480), None) is False  # 非摘录 / 按锚点


# ── G3：摘录首章 ────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True)
def test_g3_first_chapter_is_the_first_marker_in_the_excerpt():
    from core.phase_excerpt import build_excerpt, first_chapter

    assert first_chapter(build_excerpt(_SRC, [_NAME, _ALIAS])) == 1
    assert first_chapter(build_excerpt(_SRC_LATE, [_NAME])) == 3
    assert first_chapter("没有章节标记的摘录") is None


# ── G4 / G5：空阶段 1 让弧线组重做一次 ──────────────────────────────────

@pytest.mark.xfail(strict=True)
def test_g4_empty_phase1_retries_the_arc_group_once(caplog):
    with caplog.at_level(logging.WARNING, logger="core.distiller"):
        calls, draft = _run(_SRC, [_ARC_EMPTY, _ARC_OK])

    g6 = _g6_calls(calls)
    assert len(g6) == 2, f"弧线组不是恰两次：{len(g6)}"
    second = g6[1]["messages"][0]["content"]
    assert "阶段 1 在正文里一章都没有" in second
    assert "第1章" in second
    assert g6[1]["system"] == g6[0]["system"], "重做时 system 变了（缓存会失效）"

    dependent = [c for c in calls if c["group"] in PHASE_DEPENDENT_GROUPS]
    assert dependent
    for call in dependent:
        assert "起始章：第3章" in call["system"], call["group"]
        assert "起始章：第5章" in call["system"], call["group"]
        assert "起始章：第1章" not in call["system"], f"{call['group']} 用了第一次的阶段列表"

    warnings = _retry_warnings(caplog)
    assert warnings and "骄纵跋扈" in warnings[0], warnings

    labels = [p["label"] for p in draft["character_arc"]["phases"]]
    starts = [p.get("start_chapter") for p in draft["character_arc"]["phases"]]
    assert labels == ["骄纵跋扈", "转折", "后半"], labels
    assert starts == [0, 3, 5], starts


@pytest.mark.xfail(strict=True)
def test_g5_still_empty_after_retry_warns_and_continues(caplog):
    with caplog.at_level(logging.WARNING, logger="core.distiller"):
        calls, draft = _run(_SRC, [_ARC_EMPTY, _ARC_EMPTY])

    assert len(_g6_calls(calls)) == 2, "重做只做一次"
    warnings = _retry_warnings(caplog)
    assert len(warnings) == 2, warnings
    assert "仍为空" in warnings[1], warnings
    assert [p["label"] for p in draft["character_arc"]["phases"]] == ["骄纵跋扈", "初入宫", "转折"]


# ── G6 / G7：提示词与结构 ───────────────────────────────────────────────

@pytest.mark.xfail(strict=True)
def test_g6_arc_dimension_puts_phase1_inside_the_narrative():
    from core.distiller import _PRE_STORY

    for start_by in (phase_anchoring.StartBy.ANCHOR, phase_anchoring.StartBy.CHAPTER):
        text = D._arc_dimension(start_by)
        assert "从故事开始到结束" not in text
        assert "按故事顺序排列" not in text
        assert "阶段 1 从此人在正文里首次出场开始" in text
        assert _PRE_STORY in text


@pytest.mark.xfail(strict=True)
def test_g7_draft_schema_uses_narrative_order_wording():
    blob = json.dumps(draft_schema("G6"), ensure_ascii=False)
    assert "按故事顺序排列" not in blob
    assert "按正文叙述顺序排列" in blob


# ── C1 / C2：对照（改前就绿）────────────────────────────────────────────

def test_c1_normal_arc_calls_the_arc_group_once(caplog):
    with caplog.at_level(logging.WARNING, logger="core.distiller"):
        calls, _ = _run(_SRC, [_ARC_OK])
    assert len(_g6_calls(calls)) == 1
    assert not _retry_warnings(caplog)


def test_c2_chapterless_book_calls_the_arc_group_once():
    calls, _ = _run(_CHAPTERLESS, [_ANCHOR_ARC])
    assert len(_g6_calls(calls)) == 1
