# -*- coding: utf-8 -*-
"""PR-4 目标检查：位置核对用上回忆区（倒叙章节）。

目标：阶段按原文位置归，默认位置 = 故事时间；回忆区里的文字不满足这一点。所以
1. 落在回忆区的证据句不作位置证据 —— 保留模型标的阶段，不被改挂到回忆区所在的阶段；
2. 证据句在回忆区内外都出现时，只用回忆区外的位置；
3. 阶段锚点落在回忆区 → 整卡跳过位置核对，原因写明回忆区；
4. 回忆区未标 → 行为与今天完全相同；
5. 两个蒸馏入口把 PR-3 算出的回忆区真的交给转卡。

替身书：第 1–3 章正文，第 4 章是日札（倒叙）。两个阶段：阶段 2 从第 2 章开始。
"""
from __future__ import annotations

import logging
import uuid

import pytest

from core.card_draft import card_from_draft
from core.retro_regions import NOT_DETECTED, ChapterRange, RetroRegions
from storage.sqlite_store import SQLiteStore
import test_distill_draft_persist as P
from limiter import limiter
from routers import distill as D          # 与 test_distill_draft_persist 同一个模块对象

_SRC = ("第1章\n开头甲甲甲甲甲。旧事丁丁丁丁丁。\n"
        "第2章\n中间乙乙乙乙乙。\n"
        "第3章\n结尾丙丙丙丙丙。\n"
        "第4章\n【日札】回忆戊戊戊戊戊。旧事丁丁丁丁丁。中间己己己己己。\n")
_RETRO = RetroRegions("marked", (ChapterRange(4, 4, "日记"),))
_IN_DIARY = "回忆戊戊戊戊戊"      # 只在第 4 章（回忆区）
_BOTH = "旧事丁丁丁丁丁"          # 第 1 章与第 4 章各一处


def _draft(*behaviors, anchor2="中间乙乙乙乙乙"):
    return {"name": "角色",
            "character_arc": {"axis": "a", "phases": [
                {"label": "L1", "state": "S1", "anchor": ""},
                {"label": "L2", "state": "S2", "anchor": anchor2}]},
            "situation_behaviors": list(behaviors)}


def _beh(situation, phase, quote):
    return {"situation": situation, "behavior": "做法",
            "occurrences": [{"phase": phase, "quote": quote}]}


def _phase_of(card, situation):
    """该做法挂在哪些阶段（1 起）；顶层记 0。"""
    dump = card.model_dump()
    at = [i for i, p in enumerate(dump["character_arc"]["phases"], 1)
          if any(b["situation"] == situation for b in p["behaviors"])]
    top = [0] if any(b["situation"] == situation for b in dump["situation_behaviors"]) else []
    return top + at


# ── 1 / 4：回忆区里的证据保留模型标注；未标时同今天（被改挂） ──────────────────────

def test_g1_quote_in_retro_keeps_the_tagged_phase():
    d = _draft(_beh("日札里的", 1, _IN_DIARY), _beh("正文里的", 2, "结尾丙丙丙丙丙"))
    assert (_phase_of(card_from_draft(d, _SRC, retro=_RETRO), "日札里的"),
            _phase_of(card_from_draft(d, _SRC, retro=NOT_DETECTED), "日札里的")) == ([1], [2])


# ── 2：回忆区内外都出现，只用回忆区外的位置 ────────────────────────────────────

def test_g2_quote_inside_and_outside_uses_only_the_outside_position():
    d = _draft(_beh("两处都有", 2, _BOTH), _beh("正文里的", 2, "结尾丙丙丙丙丙"))
    assert (_phase_of(card_from_draft(d, _SRC, retro=_RETRO), "两处都有"),
            _phase_of(card_from_draft(d, _SRC, retro=NOT_DETECTED), "两处都有")) == ([1], [2])


# ── 3：锚点落在回忆区 → 整卡跳过，原因写明回忆区 ─────────────────────────────────

def test_g3_anchor_in_retro_skips_the_card_and_names_the_region(caplog):
    d = _draft(_beh("正文里的", 2, "结尾丙丙丙丙丙"), anchor2="中间己己己己己")
    with caplog.at_level(logging.WARNING):
        card = card_from_draft(d, _SRC, retro=_RETRO)
    skips = [r.getMessage() for r in caplog.records if "整卡跳过位置检查" in r.getMessage()]
    assert (card.character_arc.has_positions(), len(skips),
            "阶段 2 锚点落在回忆区（第4–4章日记）" in (skips or [""])[0]) == (False, 1, True)


# ── 5：转卡必须拿到回忆区；两个蒸馏入口把 PR-3 的结果交给它 ─────────────────────────

def test_g5_card_from_draft_requires_retro():
    with pytest.raises(TypeError):
        card_from_draft(_draft(), _SRC)


class _Stub(P._DraftStub):
    def detect_retro_regions(self, content):
        return _RETRO


@pytest.fixture(autouse=True)
def _isolate_rate_limiter():
    """`limiter` 是进程级单例，`/run_stream` 限 16/hour、按 client IP 记；全量跑时各文件的
    请求共用「testclient」一个桶，本文件再多一次就把后面的 test_security_authz 顶成 429。
    前后各清一次（同 `test_text_upload_filename.py`）。"""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"retro_pos_{uuid.uuid4().hex}.db"))


def _spy(monkeypatch):
    seen = []
    real = D.card_from_draft

    def _wrapped(data, source_text, *, retro):
        seen.append(retro)
        return real(data, source_text, retro=retro)

    monkeypatch.setattr(D, "card_from_draft", _wrapped)
    monkeypatch.setattr(P, "_DraftStub", _Stub)
    return seen


def test_g5b_start_passes_the_resolved_regions(monkeypatch, store):
    seen = _spy(monkeypatch)
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = P._seed_text(store, uid)
    resp, _ = P._run_start(monkeypatch, store, uid, tid)
    assert (resp.status_code, seen) == (200, [_RETRO])


def test_g5c_run_stream_passes_the_resolved_regions(monkeypatch, store):
    seen = _spy(monkeypatch)
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = P._seed_text(store, uid)
    P._run_stream(monkeypatch, store, uid, tid)
    assert seen == [_RETRO]
