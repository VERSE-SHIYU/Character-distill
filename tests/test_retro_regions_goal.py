# -*- coding: utf-8 -*-
"""回忆区（PR-3：只算、不用）的目标检查。

目标：一本书的回忆区（日记 / 书信 / 番外 / 整章回忆）能被标出来、按书存一份、同书再蒸
直接复用；标不了时不挡蒸馏。检查 6–7 走真 `/start`、`/run_stream`（真 SQLiteStore，只把
模型换成替身），证明两个蒸馏入口真的把回忆区算出并落库。
"""
from __future__ import annotations

import uuid

import pytest

from core.chapters import chapter_starts
from core.retro_cache import resolve_retro_regions
from core.retro_regions import RETRO_VERSION, ChapterRange, RetroRegions, detect, parse
from storage.sqlite_store import SQLiteStore
import test_distill_draft_persist as P
from test_distill_task_api import _run_async

# 2026-10-17 小重放的两次真实回复（deepseek-flash，谢凛羽原文 3335472a438b，第 1–509 章）
_REAL_REPLY_1 = ('{"ranges": [{"from": 491, "to": 502, "kind": "日记", "why": "日札"}, '
                 '{"from": 503, "to": 505, "kind": "日记", "why": "日札"}, '
                 '{"from": 506, "to": 507, "kind": "番外", "why": "番外"}, '
                 '{"from": 508, "to": 509, "kind": "番外", "why": "番外"}]}')
_REAL_REPLY_2 = ('{"ranges": [{"from": 491, "to": 498, "kind": "日记", "why": "日札"}, '
                 '{"from": 499, "to": 502, "kind": "日记", "why": "日札"}, '
                 '{"from": 503, "to": 505, "kind": "日记", "why": "日札"}, '
                 '{"from": 508, "to": 509, "kind": "番外", "why": "番外"}]}')
_TRUTH_RETRO = set(range(491, 505)) | {508, 509}
_CHAPTERS_509 = [(n, n * 100) for n in range(1, 510)]


def _covered(regions: RetroRegions) -> set[int]:
    return {n for r in regions.ranges for n in range(r.first, r.last + 1)}


# ── 检查 1：章节标题只认行首的「第N章」，认不准就不猜 ──────────────────────────

def test_g1_chapter_starts_reads_line_start_headings():
    src = "第1章\n他说起第3章里的事。\n第2章\n正文"
    assert chapter_starts(src) == [(1, 0), (2, src.index("第2章"))]


@pytest.mark.parametrize("src", ["没有标题的正文", "第1章\n甲\n第1章\n乙", "第2章\n甲\n第1章\n乙"])
def test_g1b_unusable_headings_give_none(src):
    assert chapter_starts(src) is None


# ── 检查 2：真实回复解析后，日札与番外一章不漏 ─────────────────────────────────

@pytest.mark.parametrize("reply", [_REAL_REPLY_1, _REAL_REPLY_2])
def test_g2_real_replies_cover_every_retro_chapter(reply):
    regions = parse(reply, _CHAPTERS_509)
    assert regions.status == "marked"
    assert _TRUTH_RETRO <= _covered(regions)


# ── 检查 3：对不上原文的范围丢掉；回复不是 JSON 不缓存 ─────────────────────────

def test_g3_ranges_outside_the_book_or_reversed_are_dropped():
    reply = ('{"ranges": [{"from": 3, "to": 2, "kind": "日记"}, {"from": 8, "to": 9, "kind": "番外"},'
             ' {"from": 1, "to": 2, "kind": "日记"}]}')
    regions = parse(reply, [(1, 0), (2, 10), (3, 20)])
    assert regions.ranges == (ChapterRange(1, 2, "日记"),)


def test_g3b_malformed_reply_is_not_final():
    regions = parse("我觉得没有", [(1, 0)])
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
    assert seen["system"].startswith("以下是完整的文本内容：\n\n第1章\n甲\n\n## 本步：标出不是")


def test_g5_json_round_trip():
    regions = parse(_REAL_REPLY_2, _CHAPTERS_509)
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
