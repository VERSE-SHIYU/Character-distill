"""F2 目标检查：每次起蒸馏，任务行记下「是否重新蒸馏」和「从哪个入口发起」（spec docs/specs/distill-entry-audit.md §一）。

E1 新任务：/start 带 force、entry → distill_tasks 行里 force、entry 与请求一致。
E2 续跑（复用出错行）：行里的 force、entry 改成这次请求的值。
E3 草稿重转：行里 entry='redraft'、force=True。
E4 entry 只收约定的几种：未知值 422；不传时存空串（脚本、旧前端）。
E5 任务开始那行日志带 entry=…。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid

import pytest

import test_distill_task_api as A
from test_distill_task_api import store, user_id, _clean_tasks  # noqa: F401  复用同一套夹具
from core.distiller import text_fingerprint

pytestmark = pytest.mark.usefixtures("embedding_key_configured")

ENTRIES = ("character_page", "workbench_card", "workbench_task", "task_bar", "redraft")


def _row(store, task_id):
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT force, entry FROM distill_tasks WHERE task_id = ?", (task_id,)).fetchone()
    return (bool(r["force"]), r["entry"]) if r else None


def _start(monkeypatch, store, uid, tid, body):
    A._assert_premise_resolves(store, A._install_user_llm(monkeypatch))
    monkeypatch.setattr("deps.get_distiller", lambda llm=None: A._ResumeDistillerStub(3000))
    started: list = []
    monkeypatch.setattr("core.concurrency.ctx_thread",
                        lambda *a, **k: started.append(k) or threading.Thread())
    resp = A._build_client(store, uid).post("/api/distill/start", json={"text_id": tid, **body})
    return resp, started


def _text(store, uid, body="正文" * 20):
    tid = f"txt_{uuid.uuid4().hex}"
    A._run_async(store.save_text(tid, "src.txt", body, user_id=uid))
    return tid


@pytest.mark.parametrize("entry,force", [("character_page", True), ("workbench_card", True),
                                         ("character_page", False)])
def test_e1_new_task_records_force_and_entry(store, user_id, monkeypatch, entry, force):
    tid = _text(store, user_id)
    resp, _ = _start(monkeypatch, store, user_id, tid,
                     {"character_name": "甲", "force": force, "entry": entry})
    assert resp.status_code == 200, resp.text
    assert _row(store, resp.json()["task_id"]) == (force, entry)


def test_e2_resume_row_restamped_with_this_request(store, user_id, monkeypatch):
    body = "角色说的话" * 20
    tid = _text(store, user_id, body)
    task_id = f"dt_{uuid.uuid4().hex}"
    A._seed_checkpoint_row(store, user_id, tid, task_id=task_id, chunk_size=3000,
                           fp=text_fingerprint(body), status="error", message="x")
    # 旧行先记成「重新蒸馏、工作台卡片详情」，才看得出这次请求有没有把两列都改写
    A._run_async(store.update_distill_task(task_id, force=True, entry="workbench_card"))
    assert _row(store, task_id) == (True, "workbench_card")
    resp, _ = _start(monkeypatch, store, user_id, tid,
                     {"character_name": "甲", "force": False, "entry": "task_bar"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["task_id"] == task_id          # 前提：确实复用了出错行
    assert _row(store, task_id) == (False, "task_bar")


def test_e3_redraft_records_redraft_entry(store, user_id, monkeypatch):
    import json
    tid = _text(store, user_id)
    cid = f"card_{uuid.uuid4().hex[:12]}"
    A._run_async(store.save_card(cid, tid, "甲", json.dumps({"name": "甲"}), user_id=user_id))
    A._run_async(store.save_card_draft(cid, json.dumps({"character": "甲", "draft": {"name": "甲"}})))
    A._assert_premise_resolves(store, A._install_user_llm(monkeypatch))
    monkeypatch.setattr("deps.get_distiller", lambda llm=None: A._ResumeDistillerStub(3000))
    monkeypatch.setattr("core.concurrency.ctx_thread", lambda *a, **k: threading.Thread())
    resp = A._build_client(store, user_id).post(f"/api/distill/card/{cid}/redraft")
    assert resp.status_code == 200, resp.text
    assert _row(store, resp.json()["task_id"]) == (True, "redraft")


def test_e4_entry_is_a_closed_set(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    bad, _ = _start(monkeypatch, store, user_id, tid, {"character_name": "甲", "entry": "somewhere"})
    assert bad.status_code == 422
    tid2 = _text(store, user_id)
    none_given, _ = _start(monkeypatch, store, user_id, tid2, {"character_name": "乙"})
    assert none_given.status_code == 200, none_given.text
    assert _row(store, none_given.json()["task_id"]) == (False, "")


def test_e5_task_start_log_carries_entry(store, user_id, monkeypatch, caplog):
    import routers.distill as D
    tid = _text(store, user_id)
    resp, started = _start(monkeypatch, store, user_id, tid,
                           {"character_name": "甲", "force": True, "entry": "workbench_card"})
    assert resp.status_code == 200, resp.text
    kw = started[0]
    assert kw.get("kwargs", {}).get("entry") == "workbench_card", kw   # 入口随线程参数传下去
    with caplog.at_level(logging.WARNING):
        try:
            D._run_distill_task(resp.json()["task_id"], tid, "甲", True, user_id, "正文", "story",
                                None, **kw["kwargs"])
        except AssertionError:
            pass       # llm=None 撞函数体开头的硬门（distill.py「硬门而不是注释」）；开始行由装饰器在进入函数体之前打
    starts = [r.getMessage() for r in caplog.records if r.getMessage().startswith("任务开始")]
    assert starts and "entry=workbench_card" in starts[0], starts
