# -*- coding: utf-8 -*-
"""目标检查：给蒸馏 / 草稿重转补诊断日志（spec `docs/specs/distill-diagnostic-logs.md` v2）。

G1–G9 先全部 `xfail(strict=True)`，每个提交跑一次，最后一次去掉标记。
替身与搭 app 复用 `test_distill_draft_persist` / `test_card_redraft_goal` /
`test_phase_anchoring` / `test_phase_locate_goal` / `test_anchor_skip_diagnostics_goal`。
"""
from __future__ import annotations

import ast
import copy
import json
import logging
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.card_draft import card_from_draft
from core.distiller import text_fingerprint
from core.request_context import LLM_CALLER, Caller
from core.retro_regions import NOT_DETECTED
from core.stdout_logging import install_stdout_logging
from routers import distill as D
from storage.sqlite_store import SQLiteStore

import test_anchor_skip_diagnostics_goal as SK
import test_phase_anchoring as PA
import test_phase_locate_goal as PL
from test_card_redraft_goal import _NoRedistillStub
from test_distill_draft_persist import (
    _DraftStub,
    _draft,
    _install,
    _only_card_id,
    _run_start,
    _seed_text,
)
from test_distill_task_api import _assert_premise_resolves, _build_client, _run_async

_REPO = Path(__file__).resolve().parents[1]
_ROOT = logging.getLogger()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"diag_{uuid.uuid4().hex}.db"))

xfail = pytest.mark.xfail(strict=True, reason="诊断日志尚未实现")


@contextmanager
def _stdout_logging():
    """装真 stdout handler，退出时按原集合还原（同 `test_stdout_logging`）。"""
    before = list(_ROOT.handlers)
    try:
        install_stdout_logging()
        yield
    finally:
        _ROOT.handlers[:] = before


def _quiet_retro(monkeypatch):
    """真 distiller 有 `detect_retro_regions`；替身缺这个方法时回忆区那步会打一条带堆栈的
    warning，堆栈续行的行首自然没有任务 ID（与 G1 要断言的日志格式无关）。"""
    monkeypatch.setattr(_DraftStub, "detect_retro_regions",
                        lambda self, content: NOT_DETECTED, raising=False)


def _start_with_draft(monkeypatch, store, uid, tid, draft):
    """跑一次真 `/start`，但用给定的草稿替身（用于制造转卡异常）。"""
    distiller = _DraftStub(draft)
    llm, threads = _install(monkeypatch, store, distiller)
    _assert_premise_resolves(store, llm)
    resp = _build_client(store, uid).post(
        "/api/distill/start",
        json={"text_id": tid, "character_name": "乙", "force": False},
    )
    for t in threads:
        t.join(timeout=30)
    return resp


@xfail
def test_g1_every_line_carries_task_id(capsys, monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)
    _quiet_retro(monkeypatch)

    with _stdout_logging():
        resp, _ = _run_start(monkeypatch, store, uid, tid)
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]
        card_id = _only_card_id(store, tid, uid)

        stub = _NoRedistillStub(_draft())
        _llm, threads = _install(monkeypatch, store, stub)
        redraft = _build_client(store, uid).post(f"/api/distill/card/{card_id}/redraft")
        assert redraft.status_code == 200, redraft.text
        redraft_id = redraft.json()["task_id"]
        for t in threads:
            t.join(timeout=30)
        out = capsys.readouterr().out

    lines = out.splitlines()
    starts = [ln for ln in lines if "任务开始" in ln]
    ends = [ln for ln in lines if "任务结束" in ln]
    # 首次蒸馏一条开始、一条结束；草稿重转一条开始、一条结束。
    assert [("来源=蒸馏" in ln and "force=false resumed=false" in ln)
            for ln in starts if f"[{task_id}]" in ln] == [True]
    assert [("来源=草稿重转" in ln and "force=true resumed=false" in ln)
            for ln in starts if f"[{redraft_id}]" in ln] == [True]
    assert len([ln for ln in starts if "来源=蒸馏" in ln]) == 1
    assert len([ln for ln in ends if f"[{task_id}]" in ln]) == 1
    assert len([ln for ln in ends if f"[{redraft_id}]" in ln]) == 1

    for tid_ in (task_id, redraft_id):
        begin = next(i for i, ln in enumerate(lines) if "任务开始" in ln and f"[{tid_}]" in ln)
        finish = next(i for i, ln in enumerate(lines) if "任务结束" in ln and f"[{tid_}]" in ln)
        # 只看本 handler 的日志行（行首是 `HH:MM:SS`）；链路上残留的裸 print 不在其列。
        logs = [ln for ln in lines[begin:finish + 1] if re.match(r"\d{2}:\d{2}:\d{2} ", ln)]
        assert logs, f"任务 {tid_} 范围内应有日志行"
        bad = [ln for ln in logs if f"[{tid_}]" not in ln]
        assert bad == [], f"任务 {tid_} 范围内有无 ID 的日志行：{bad}"


@xfail
def test_g1b_resume_start_line_says_resumed(capsys, monkeypatch, store):
    """任务级门通过、复用 error 行时，开始行写 resumed=true。"""
    from test_distill_draft_persist import _DISTILL_NAME, _SRC

    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)
    _quiet_retro(monkeypatch)
    _run_async(store.create_distill_task(
        f"dt_{uuid.uuid4().hex}", uid, tid, character=_DISTILL_NAME, status="error",
        progress_pct=40, message="蒸馏失败：服务内部异常，请重试", card_id="", awakening="",
        chunk_size=3000, text_fingerprint=text_fingerprint(_SRC),
    ))

    with _stdout_logging():
        resp, _ = _run_start(monkeypatch, store, uid, tid)
        assert resp.status_code == 200, resp.text
        out = capsys.readouterr().out

    starts = [ln for ln in out.splitlines()
              if "任务开始" in ln and f"[{resp.json()['task_id']}]" in ln]
    assert [("resumed=true" in ln) for ln in starts] == [True]


@xfail
def test_g2_step_timings_in_order(capsys, monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)
    _quiet_retro(monkeypatch)

    with _stdout_logging():
        resp, _ = _run_start(monkeypatch, store, uid, tid)
        assert resp.status_code == 200, resp.text
        card_id = _only_card_id(store, tid, uid)
        out_start = capsys.readouterr().out

        stub = _NoRedistillStub(_draft())
        _llm, threads = _install(monkeypatch, store, stub)
        redraft = _build_client(store, uid).post(f"/api/distill/card/{card_id}/redraft")
        assert redraft.status_code == 200, redraft.text
        for t in threads:
            t.join(timeout=30)
        out_redraft = capsys.readouterr().out

        bad = {"name": "角色", "character_arc": "不是对象"}
        uid_bad = f"usr_{uuid.uuid4().hex[:8]}"
        tid_bad = _seed_text(store, uid_bad)
        resp_bad = _start_with_draft(monkeypatch, store, uid_bad, tid_bad, bad)
        assert resp_bad.status_code == 200, resp_bad.text
        out_bad = capsys.readouterr().out

    want = ["名单", "回忆区", "蒸馏", "重定位", "转卡", "后置", "打标签", "落卡", "待补", "苏醒台词"]
    got = re.findall(r"步骤 (\S+) 完成 耗时 (\d+\.\d+)s", out_start)
    assert [name for name, _ in got] == want
    assert all(secs for _, secs in got)
    assert "步骤 蒸馏 完成" not in out_redraft
    assert re.search(r"步骤 转卡 失败 耗时 \d+\.\d+s：ValidationError", out_bad), out_bad


@xfail
def test_g3_token_totals_by_action(capsys, monkeypatch):
    from core import task_log
    from core.utils import try_record_usage

    class _Store:
        async def record_usage(self, *_a, **_kw):
            pass

    class _LLM:
        _model = "m"
        last_usage: dict = {}

    def _discard(coro, wait=False):
        coro.close()

    monkeypatch.setattr("core.utils.submit_to_main_loop", _discard)
    usage = {"prompt_tokens": 10, "completion_tokens": 2,
             "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 7}

    token = LLM_CALLER.set(Caller(ip=None, user_id="u_log"))
    try:
        with _stdout_logging():
            with task_log.task_scope("t1", 来源="测试"):
                try_record_usage(_Store(), _LLM(), action="a", usage=usage)
                try_record_usage(_Store(), _LLM(), action="b", usage=usage)
            try_record_usage(_Store(), _LLM(), action="c", usage=usage)
            out = capsys.readouterr().out
    finally:
        LLM_CALLER.reset(token)

    end = [ln for ln in out.splitlines() if "任务结束" in ln]
    assert len(end) == 1
    assert ("调用 2 次" in end[0], "输入 20" in end[0], "输出 4" in end[0],
            "a:1/10/2" in end[0], "b:1/10/2" in end[0], "c:" in end[0]) == (
        True, True, True, True, True, False)


@xfail
def test_g4_phase_starts_logged_every_time(caplog):
    msgs = SK._messages(caplog, SK._RETRO, SK._draft("中间乙乙乙乙乙"))
    starts = [m for m in msgs if m.startswith("阶段起点（角色）阶段 ")]
    assert len(starts) == 3
    assert [m.split("」锚点「")[0] for m in starts] == [
        "阶段起点（角色）阶段 1「L1", "阶段起点（角色）阶段 2「L2",
        "阶段起点（角色）阶段 3「L3"]
    assert [(bool(re.search(r"第\d+章", m)), "规范化位置" in m,
             ("在回忆区" in m), "前文：" in m, "整卡跳过" in m) for m in starts] == [
        (True, True, True, True, False)] * 3


@xfail
def test_g5_rehang_line_names_the_chapter(caplog):
    with caplog.at_level(logging.WARNING, logger="core.phase_anchoring"):
        PA._card([PA._row([PA._occ(3, PA._Q1)], "错挂")])
    assert [r.getMessage() for r in caplog.records if "改挂" in r.getMessage()] == [
        "改挂：做法 '错挂' 标在阶段 3，摘录落在阶段 [1]（摘录在无章节标题）"]


@xfail
def test_g6_candidate_verdicts_and_reasons(caplog):
    class _WhyJudge(PL._Judge):
        def chat(self, system, messages, max_tokens=None, **kw):
            n = int(re.search(r"### 第(\d+)章", messages[-1]["content"]).group(1))
            d = json.loads(super().chat(system, messages, max_tokens=max_tokens, **kw))
            d["why"] = f"第{n}章{'是' if d['happens'] else '否'}"
            return json.dumps(d)

    judge = _WhyJudge(PL._YES)
    with caplog.at_level(logging.WARNING):
        PL._distiller(judge).locate_phase_starts(PL._draft(), PL._SRC, "他", PL._RETRO)
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("[phase_locate]")]

    cands = [ln for ln in lines if "候选" in ln]
    assert len(cands) == len(judge.asked)
    assert all(re.fullmatch(
        r"\[phase_locate\] card=他 阶段 [23] 候选 第\d+章：(是|否)；理由：第\d+章(是|否)；句子：.*",
        ln) for ln in cands)
    assert [ln.endswith("句子：") for ln in cands if "：否；" in ln] == [True] * len(
        [ln for ln in cands if "：否；" in ln])
    originals = [ln for ln in lines if "→ 第" in ln]
    assert [ln.rsplit("；理由：", 1)[-1] for ln in originals] == ["第2章是", "第4章是"]


@xfail
def test_g6b_verified_anchors_say_so(caplog):
    good = copy.deepcopy(PL._PHASES)
    good[1]["anchor"], good[2]["anchor"] = "那天他在桥边初遇阿月", "他终于决定远行求学"
    with caplog.at_level(logging.WARNING):
        PL._distiller(PL._Judge(PL._YES)).locate_phase_starts(
            PL._draft(good), PL._SRC, "他", PL._RETRO)
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("[phase_locate]")]
    assert lines == ["[phase_locate] card=他 锚点核对通过，用模型锚点"]


@xfail
def test_g7_no_print_left_on_the_chain():
    targets = {
        "web/routers/distill.py": [
            "_persist_awakening", "_distill_to_draft", "_run_distill_task", "_distill_start_impl"],
        "core/distiller.py": [
            "_collect_stream", "fill_relationships", "_chat_accounted", "_parse_json_with_retry",
            "identify_characters", "_judge_groups", "distill_incremental_stream"],
        "core/text_manager.py": ["save_distilled_card"],
        "core/utils.py": ["try_record_usage"],
    }
    bad = []
    for rel, names in targets.items():
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                            and sub.func.id == "print"):
                        bad.append(f"{rel}:{sub.lineno}:{node.name}")
    assert bad == []


@xfail
def test_g8_zero_means_story_start_is_a_warning(caplog):
    draft = {
        "name": "角色",
        "character_arc": {"axis": "a", "phases": PA._phases(2, {2: PA._Q3})},
        "decision_style": [{"value": "B", "occurrences": [{"phase": 0, "quote": PA._Q1}]}],
    }
    with caplog.at_level(logging.WARNING):
        card = card_from_draft(draft, PA._SRC, retro=NOT_DETECTED)
    assert sorted((r.levelno, r.getMessage()) for r in caplog.records
                  if r.getMessage().startswith("[card_draft]")) == [
        (logging.WARNING, "[card_draft] 全程：B")]
    assert card.decision_style == "B"


@xfail
def test_g9_scalar_collision_names_kept_and_dropped(caplog):
    draft = {
        "name": "角色",
        "character_arc": {"axis": "a", "phases": PA._phases(2, {2: PA._Q3})},
        "decision_style": [
            {"value": "B", "occurrences": [{"phase": 1, "quote": PA._Q3}]},
            {"value": "A", "occurrences": [{"phase": 2, "quote": PA._Q3}]},
        ],
    }
    with caplog.at_level(logging.WARNING):
        card = card_from_draft(draft, PA._SRC, retro=NOT_DETECTED)
    msgs = [r.getMessage() for r in caplog.records]
    assert card.character_arc.phases[1].overlay.get("decision_style") == "B"
    assert [m for m in msgs if "有多条取值" in m] == [
        "[card_draft] decision_style 的阶段 2 有多条取值，"
        "保留「B」（由阶段 1 改挂进来），丢弃「A」（原标此阶段）"]

    both = {
        "name": "角色",
        "character_arc": {"axis": "a", "phases": PA._phases(2, {2: PA._Q3})},
        "decision_style": [
            {"value": "B", "occurrences": [{"phase": 2, "quote": PA._Q3}]},
            {"value": "A", "occurrences": [{"phase": 2, "quote": PA._Q3}]},
        ],
    }
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        card_from_draft(both, PA._SRC, retro=NOT_DETECTED)
    assert [r.getMessage() for r in caplog.records if "有多条取值" in r.getMessage()] == [
        "[card_draft] decision_style 的阶段 2 有多条取值，"
        "保留「B」（原标此阶段），丢弃「A」（原标此阶段）"]
