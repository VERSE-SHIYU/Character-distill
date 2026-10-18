# -*- coding: utf-8 -*-
"""目标检查：模型写的阶段锚点核对不通过时，由代码在原文里重新找起点（spec `docs/specs/phase-locate.md`）。

目标：
1. 锚点核对不通过（例：落在回忆区）→ 逐章判断找到起点，卡上写出阶段起点；
2. 每个阶段取「答是」的最早一章，且晚于上一阶段的起点；
3. 回忆区里的章不进候选（模型根本不会被问到）；
4. 任一阶段找不到起点 → 草稿不动，行为同今天（整卡跳过位置核对）；
5. 锚点本来就核对得过 → 一次判断都不调；
6. 锚点句在书里更早处也出现 → 向后拼接，直到首次出现就在起点章；
7. 每个阶段打一行监测（落在哪章、答「是」的章）；
8. 两个蒸馏入口都在转卡前调它，并把回忆区交给它。

替身书：第 1 章开头；第 2 章初遇；第 3 章过渡（只提起）；第 4 章远行；第 5 章过渡；第 6 章是日札（回忆区，
复述初遇与远行）。判断由替身按「章号 ∈ 预设集合」作答。
"""
from __future__ import annotations

import copy
import json
import logging
import re
import uuid

import pytest

from core.card_draft import card_from_draft
from core.retro_regions import ChapterRange, RetroRegions
from storage.sqlite_store import SQLiteStore
import test_distill_draft_persist as P
from limiter import limiter
from routers import distill as D

_TODO = pytest.mark.xfail(strict=True, reason="阶段起点重定位：尚未实现")

_SRC = ("第1章\n他在家里读书。日子平平淡淡。\n"
        "第2章\n那天他在桥边初遇阿月。他从此心动不已。\n"
        "第3章\n他常想起桥边的阿月。书院来信催他。\n"
        "第4章\n他终于决定远行求学。行囊收拾好了。\n"
        "第5章\n路上风雨兼程。他想念家乡。\n"
        "第6章\n【日札】那年在桥边初遇阿月，从此心动。后来我决定远行求学。\n")
_RETRO = RetroRegions("marked", (ChapterRange(6, 6, "日记"),))
_PHASES = [{"label": "平淡读书", "state": "在家读书", "anchor": ""},
           {"label": "初遇心动", "state": "在桥边初遇阿月之后心动", "anchor": "那年在桥边初遇阿月，从此心动"},
           {"label": "远行求学", "state": "决定远行求学之后", "anchor": "后来我决定远行求学"}]


def _draft(phases=None):
    return {"name": "他", "character_arc": {"axis": "a", "phases": copy.deepcopy(phases or _PHASES)},
            "situation_behaviors": [{"situation": "s", "behavior": "b",
                                     "occurrences": [{"phase": 3, "quote": "路上风雨兼程"}]}]}


class _Judge:
    """替身模型：按（阶段 label，章号）作答；记下被问到的每一章。"""

    def __init__(self, yes: dict[str, dict[int, str]]):
        self.yes = yes            # label → {章号: 关键词（取含它的那句）}
        self.asked: list[tuple[str, int]] = []
        self.last_usage = None
        self.model = "m"

    def chat(self, system, messages, max_tokens=None, **kw):
        user = messages[-1]["content"]
        n = int(re.search(r"### 第(\d+)章", user).group(1))
        label = re.search(r"角色弧线的一个阶段：(.+?)：", user).group(1)
        self.asked.append((label, n))
        kw_ = self.yes.get(label, {}).get(n)
        if kw_ is None:
            return json.dumps({"happens": False, "sent": "", "why": "否"})
        sents = re.findall(r"S(\d+)：(.*)", user)
        sid = next(i for i, s in sents if kw_ in s)
        return json.dumps({"happens": True, "sent": f"S{sid}", "why": "是"})


def _distiller(judge):
    from core.distiller import Distiller
    return Distiller(llm=judge, config_path=None)


def _starts(data, src=_SRC):
    card = card_from_draft(data, src, retro=_RETRO)
    return [p.start for p in card.character_arc.phases]


def _chapter_of(pos_norm, src=_SRC):
    """规范化位置 → 章号（测试里自算，不借被测代码）。"""
    from core.quotes import normalize
    heads = [(int(m.group(1)), len(normalize(src[:m.start()]))) for m in re.finditer(r"(?m)^第(\d+)章", src)]
    return max(n for n, p in heads if p <= pos_norm)


_YES = {"初遇心动": {2: "初遇阿月", 3: "桥边的阿月"}, "远行求学": {4: "决定远行求学", 5: "风雨兼程"}}


@_TODO
def test_g1_bad_anchors_get_relocated_and_the_card_gets_starts():
    data = _draft()
    _distiller(_Judge(_YES)).locate_phase_starts(data, _SRC, "他", _RETRO)
    starts = _starts(data)
    assert ([_chapter_of(s) for s in starts[1:]], data["character_arc"]["phases"][1]["anchor"]) == (
        [2, 4], "那天他在桥边初遇阿月。")


@_TODO
def test_g2_earliest_yes_after_the_previous_phase_wins():
    yes = {"初遇心动": {3: "桥边的阿月", 4: "决定远行求学"}, "远行求学": {2: "初遇阿月", 4: "决定远行求学"}}
    data = _draft()
    _distiller(_Judge(yes)).locate_phase_starts(data, _SRC, "他", _RETRO)
    assert [_chapter_of(s) for s in _starts(data)[1:]] == [3, 4]


@_TODO
def test_g3_retro_chapters_are_never_asked():
    judge = _Judge(_YES)
    _distiller(judge).locate_phase_starts(_draft(), _SRC, "他", _RETRO)
    assert (6 in {n for _, n in judge.asked}, len(judge.asked) > 0) == (False, True)


@_TODO
def test_g4_a_phase_without_any_yes_leaves_the_draft_untouched(caplog):
    data = _draft()
    with caplog.at_level(logging.WARNING):
        _distiller(_Judge({"初遇心动": {2: "初遇阿月"}})).locate_phase_starts(data, _SRC, "他", _RETRO)
    assert (data, any("[phase_locate]" in r.getMessage() and "找不到起点" in r.getMessage()
                      for r in caplog.records)) == (_draft(), True)


@_TODO
def test_g5_valid_anchors_make_no_calls():
    good = copy.deepcopy(_PHASES)
    good[1]["anchor"], good[2]["anchor"] = "那天他在桥边初遇阿月", "他终于决定远行求学"
    judge = _Judge(_YES)
    data = _draft(good)
    _distiller(judge).locate_phase_starts(data, _SRC, "他", _RETRO)
    assert (judge.asked, data) == ([], _draft(good))


@_TODO
def test_g6_repeated_sentence_is_extended_until_its_first_occurrence_is_in_the_chapter():
    src = _SRC.replace("第4章\n他终于决定远行求学。", "第4章\n路上风雨兼程。他终于决定远行求学。")
    src = src.replace("第5章\n路上风雨兼程。", "第5章\n他想念家乡。路上风雨兼程。远方的书院在望。")
    data = _draft()
    _distiller(_Judge({"初遇心动": {2: "初遇阿月"}, "远行求学": {5: "风雨兼程"}})).locate_phase_starts(
        data, src, "他", _RETRO)
    anchor = data["character_arc"]["phases"][2]["anchor"]
    assert (anchor, _chapter_of(_starts(data, src)[2], src)) == ("路上风雨兼程。远方的书院在望。", 5)


@_TODO
def test_g7_one_monitoring_line_per_phase(caplog):
    with caplog.at_level(logging.WARNING):
        _distiller(_Judge(_YES)).locate_phase_starts(_draft(), _SRC, "他", _RETRO)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[phase_locate]")]
    assert lines == [
        "[phase_locate] card=他 阶段 2「初遇心动」→ 第2章，答是的章 [2, 3]；锚点「那天他在桥边初遇阿月。」",
        "[phase_locate] card=他 阶段 3「远行求学」→ 第4章，答是的章 [4, 5]；锚点「他终于决定远行求学。」"]


# ── 8：两个蒸馏入口都在转卡前调它，并把回忆区交给它 ──────────────────────────────

class _Stub(P._DraftStub):
    def detect_retro_regions(self, content):
        return _RETRO

    def locate_phase_starts(self, draft, text, character_name, retro):
        _ORDER.append(("locate", retro))
        draft["_located"] = True


_ORDER: list = []


@pytest.fixture(autouse=True)
def _isolate_rate_limiter():
    """同 `test_retro_position_check_goal.py`：`/run_stream` 限流按进程共用一个桶。"""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"phase_locate_{uuid.uuid4().hex}.db"))


def _spy(monkeypatch):
    _ORDER.clear()
    real = D.card_from_draft

    def _wrapped(data, source_text, *, retro):
        _ORDER.append(("card", data.get("_located", False)))
        data.pop("_located", None)
        return real(data, source_text, retro=retro)

    monkeypatch.setattr(D, "card_from_draft", _wrapped)
    monkeypatch.setattr(P, "_DraftStub", _Stub)


@_TODO
def test_g8a_start_locates_before_converting(monkeypatch, store):
    _spy(monkeypatch)
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    resp, _ = P._run_start(monkeypatch, store, uid, P._seed_text(store, uid))
    assert (resp.status_code, _ORDER) == (200, [("locate", _RETRO), ("card", True)])


@_TODO
def test_g8b_run_stream_locates_before_converting(monkeypatch, store):
    _spy(monkeypatch)
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    P._run_stream(monkeypatch, store, uid, P._seed_text(store, uid))
    assert _ORDER == [("locate", _RETRO), ("card", True)]
