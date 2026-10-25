# -*- coding: utf-8 -*-
"""回忆区（PR-3：只算、不用）的目标检查。

目标：一本书的回忆区（日记 / 书信 / 番外 / 整章回忆）能被标出来、按书存一份、同书再蒸
直接复用；标不了时不挡蒸馏。检查 6–7 走真 `/start`、`/run_stream`（真 SQLiteStore，只把
模型换成替身），证明两个蒸馏入口真的把回忆区算出并落库。
"""
from __future__ import annotations

import json
import uuid

import pytest

from core.chapters import chapter_openings, chapter_starts
from core.retro_cache import resolve_retro_regions
from core.retro_regions import RETRO_VERSION, ChapterRange, RetroRegions, detect, instruction, parse
from storage.sqlite_store import SQLiteStore
import test_distill_draft_persist as P
from test_distill_task_api import _run_async

pytestmark = pytest.mark.usefixtures("embedding_key_configured")

# 谢凛羽原文 3335472a438b（第 1–509 章）的标准答案，Claude 逐章核过：第 491–504 章日札（123 篇，
# 记的是正文里早已写过的日子）、第 508–509 章番外（云钺童年）是倒叙；第 490 章「正文完」，
# 第 505–507 章是结局之后的番外（第 507 章末「番外：误认为有孕篇（完）」），时间接在正文之后，不是倒叙。
# 原文不入库，这里用同章数的替身书检验解析口径；真模型实测记在 docs/specs/retro-regions-detect.md §7。
_TRUTH_RETRO = set(range(491, 505)) | {508, 509}


def _head(n: int) -> str:
    return f"这是第{n}章开头独有的一句话。"


def _tail(n: int) -> str:
    return f"这是第{n}章结尾独有的一句话。"


_BOOK = "".join(f"第{n}章\n　　{_head(n)}\n　　中间的正文。\n　　{_tail(n)}\n" for n in range(1, 510))
_BOOK_CHAPTERS = chapter_starts(_BOOK)


def _reply(*ranges) -> str:
    return json.dumps({"ranges": [{"from": a, "to": b, "kind": k, "why": "理由"} for a, b, k in ranges]},
                      ensure_ascii=False)


_TRUTH_REPLY = _reply((491, 504, "日记"), (508, 509, "番外"))


def _covered(regions: RetroRegions) -> set[int]:
    return {n for r in regions.ranges for n in range(r.first, r.last + 1)}


# ── 检查 1：章节标题只认行首的「第N章」，认不准就不猜 ──────────────────────────

def test_g1_chapter_starts_reads_line_start_headings():
    src = "第1章\n他说起第3章里的事。\n第2章\n正文"
    assert chapter_starts(src) == [(1, 0), (2, src.index("第2章"))]


@pytest.mark.parametrize("src", ["没有标题的正文", "第1章\n甲\n第1章\n乙", "第2章\n甲\n第1章\n乙"])
def test_g1b_unusable_headings_give_none(src):
    assert chapter_starts(src) is None


# ── 检查 2：章号从代码列的目录里选；解析结果与标准答案逐章相等（不漏、也不多） ────

def test_g2_reply_parses_to_exactly_the_retro_chapters():
    regions = parse(_TRUTH_REPLY, _BOOK_CHAPTERS)
    assert regions.status == "marked"
    assert _covered(regions) == _TRUTH_RETRO


def test_g2b_catalogue_lists_every_chapter_with_its_opening():
    text = instruction(_BOOK, _BOOK_CHAPTERS)
    lines = text.split("### 目录\n", 1)[1].split("\n")
    assert lines == [f"第{n}章：{_head(n)} 中间的正文。 {_tail(n)}"[:len(f"第{n}章：") + 40]
                     for n in range(1, 510)]


def test_g2c_openings_skip_filler_lines():
    src = "第1章\n　　……\n　　——\n　　【日札八月十五】\n　　今日是中秋。\n第2章\n正文"
    assert chapter_openings(src, chapter_starts(src)) == [(1, "【日札八月十五】 今日是中秋。"), (2, "正文")]


# ── 检查 3：章号不在目录里、首尾颠倒、缺章号、回复不是 JSON → 整本「未标」且不缓存 ────

@pytest.mark.parametrize("reply", [
    _reply((491, 510, "日记")),                          # 末章不在目录里
    _reply((504, 491, "日记")),                          # 首尾颠倒
    _reply((491, 504, "日记"), (0, 509, "番外")),         # 一个范围坏了，整本不用
    '{"ranges": [{"kind": "日记", "start_quote": "今日是中秋。"}]}',  # 没有章号
    '{"ranges": "491-504"}',
    "我觉得没有",
])
def test_g3_bad_range_or_malformed_reply_is_unavailable_and_not_final(reply):
    regions = parse(reply, _BOOK_CHAPTERS)
    assert regions.status == "unavailable" and regions.final is False


# ── 检查 4：没有章节标题就不问模型 ────────────────────────────────────────────

def test_g4_no_headings_skips_the_model():
    calls = []
    regions = detect("没有标题的正文", prefix="P", call=lambda s, u: calls.append(s) or "")
    assert calls == [] and regions.status == "unavailable" and regions.final is True


def test_g4b_detect_sends_book_prefix_first():
    seen = {}

    def _call(system, user):
        seen["system"] = system
        return '{"ranges": []}'

    detect("第1章\n甲", prefix="以下是完整的文本内容：\n\n第1章\n甲", call=_call)
    assert seen["system"].startswith("以下是完整的文本内容：\n\n第1章\n甲\n\n## 本步：标出倒叙")
    assert seen["system"].endswith("### 目录\n第1章：甲")


def test_g5_json_round_trip():
    regions = parse(_TRUTH_REPLY, _BOOK_CHAPTERS)
    assert RetroRegions.from_json(regions.to_json()) == regions


# ── 检查 6：按书缓存：第二次不再调模型；版本不符、非属主、偶发失败都不复用 ─────────

class _Detector:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def detect_retro_regions(self, content):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"retro_{uuid.uuid4().hex}.db"))


def _seed(store, uid):
    tid = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", "第1章\n甲", user_id=uid))
    return tid


_MARKED = RetroRegions("marked", (ChapterRange(1, 1, "日记"),))


def test_g6_second_resolve_hits_the_cache(store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed(store, uid)
    d = _Detector(_MARKED)
    assert _run_async(resolve_retro_regions(store, d, tid, uid, "x")) == _MARKED
    assert _run_async(resolve_retro_regions(store, d, tid, uid, "x")) == _MARKED
    assert d.calls == 1


def test_g6b_version_mismatch_recomputes(store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed(store, uid)
    _run_async(store.save_retro_regions(tid, _MARKED.to_json(), version=RETRO_VERSION - 1))
    d = _Detector(_MARKED)
    _run_async(resolve_retro_regions(store, d, tid, uid, "x"))
    assert d.calls == 1


def test_g6c_other_user_does_not_read_the_cache(store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed(store, uid)
    _run_async(store.save_retro_regions(tid, _MARKED.to_json(), version=RETRO_VERSION))
    assert _run_async(store.get_retro_regions_owned(tid, f"usr_{uuid.uuid4().hex}",
                                                    version=RETRO_VERSION)) == ""


def test_g6d_transient_result_is_not_cached(store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed(store, uid)
    d = _Detector(RetroRegions.unavailable("回复格式不对", final=False))
    _run_async(resolve_retro_regions(store, d, tid, uid, "x"))
    _run_async(resolve_retro_regions(store, d, tid, uid, "x"))
    assert d.calls == 2


def test_g6e_detector_failure_does_not_raise(store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed(store, uid)
    regions = _run_async(resolve_retro_regions(store, _Detector(RuntimeError("网络断了")), tid, uid, "x"))
    assert regions.status == "unavailable" and regions.final is False


# ── 检查 7：两个蒸馏入口真的算出并落库 ────────────────────────────────────────

class _RetroDraftStub(P._DraftStub):
    def __init__(self, draft):
        super().__init__(draft)
        self.retro_calls = 0

    def detect_retro_regions(self, content):
        self.retro_calls += 1
        return _MARKED


def _stub_factory(monkeypatch):
    made = []

    def _make(draft):
        s = _RetroDraftStub(draft)
        made.append(s)
        return s

    monkeypatch.setattr(P, "_DraftStub", _make)
    return made


def _stored(store, tid, uid):
    return _run_async(store.get_retro_regions_owned(tid, uid, version=RETRO_VERSION))


def test_g7_start_stores_retro_regions(monkeypatch, store):
    made = _stub_factory(monkeypatch)
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = P._seed_text(store, uid)
    resp, _ = P._run_start(monkeypatch, store, uid, tid)
    assert resp.status_code == 200, resp.text
    assert made[0].retro_calls == 1
    assert RetroRegions.from_json(_stored(store, tid, uid)) == _MARKED
    P._only_card_id(store, tid, uid)          # 卡照常落库


def test_g7b_run_stream_stores_retro_regions(monkeypatch, store):
    made = _stub_factory(monkeypatch)
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = P._seed_text(store, uid)
    P._run_stream(monkeypatch, store, uid, tid)
    assert made[0].retro_calls == 1
    assert RetroRegions.from_json(_stored(store, tid, uid)) == _MARKED
    P._only_card_id(store, tid, uid)


def test_g7c_failing_detection_does_not_block_the_card(monkeypatch, store):
    """回忆区标不了（替身没有这个方法，等同调用失败），卡照常落库。"""
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = P._seed_text(store, uid)
    resp, _ = P._run_start(monkeypatch, store, uid, tid)
    assert resp.status_code == 200, resp.text
    P._only_card_id(store, tid, uid)
    assert _stored(store, tid, uid) == ""
