# -*- coding: utf-8 -*-
"""D25 第 3 步之一目标检查：全局排队落库 + 批量入口。

Q1–Q11 先以 ``xfail(strict=True)`` 落红，实现后摘标记转绿；Q12–Q13 是对照（现状即绿）。
API 夹具照 ``test_distill_task_api._install_bg``；线程池打桩为一个 worker，用 Event 卡住
第一个任务，第二个任务因此排在池里 —— 这就是「排队」这件事本身。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_distill_task_api as A
from test_distill_task_api import store, user_id  # noqa: F401  复用同一套夹具
from core import concurrency as C
from core.card_draft import card_from_draft
from core.distiller import Distiller, IdentifyResult
from core.request_context import Caller, LLM_CALLER, current_user_id
from deps import get_storage
from routers import distill as D
from routers.auth import get_current_user

pytestmark = pytest.mark.usefixtures("embedding_key_configured")

xfail = pytest.mark.xfail(strict=True, reason="D25 queue/batch 尚未实现")

_POOLS: list[ThreadPoolExecutor] = []


@pytest.fixture(autouse=True)
def _clean_queue_state():
    yield
    with D._task_lock:
        D._tasks.clear()
        D._user_slots.clear()
    while _POOLS:
        _POOLS.pop().shutdown(wait=False, cancel_futures=True)


class _TM:
    """只够落卡那一段：Task 5 的 save_distilled_card 返回值被读 card_id。"""

    async def save_distilled_card(self, text_id, card, user_id, *, embedding_key="",
                                  embedding_region="", draft, character):
        return {"card_id": f"card_{character}"}


class _QueueProbeDistiller:
    """可控蒸馏器：进流时记一次调用/身份，然后卡在 Event 上让队列可观察。"""

    # 与 test_distill_task_api._CardStubDistiller.CARD 同形的最小草稿。
    CARD = {"name": "乙", "first_message": "",
            "dialogue_examples": [{"value": "示例", "occurrences": []}]}

    def __init__(self):
        self._events: dict[str, tuple[threading.Event, threading.Event]] = {}
        self._lock = threading.Lock()
        self.calls: dict[str, int] = {}
        self.seen_user: dict[str, str | None] = {}

    def _pair(self, name: str) -> tuple[threading.Event, threading.Event]:
        pair = self._events.get(name)
        if pair is None:
            pair = self._events[name] = (threading.Event(), threading.Event())
        return pair

    def wait_started(self, name: str, timeout: float = 30) -> bool:
        return self._pair(name)[0].wait(timeout)

    def release_character(self, name: str) -> None:
        self._pair(name)[1].set()

    def release_all(self) -> None:
        for name in ("甲", "乙", "丙", "丁"):
            self.release_character(name)

    def effective_chunk_size(self, text_type="story"):
        return 3000

    def identify_characters(self, content):
        return [{"name": n, "aliases": []} for n in ("甲", "乙", "丙", "丁")]

    def identify_roster(self, content):
        return IdentifyResult(self.identify_characters(content), trace=None)

    def distill_incremental_stream(self, text, character_name, *, aliases=None,
                                   text_type="story", on_chunk_done=None,
                                   resume_candidates=None, retro=None):
        started, release = self._pair(character_name)
        with self._lock:
            self.calls[character_name] = self.calls.get(character_name, 0) + 1
            self.seen_user[character_name] = current_user_id()
        started.set()
        release.wait(timeout=30)
        yield {"status": "analyzing", "current": 1, "total": 1}
        yield json.dumps(self.CARD, ensure_ascii=False)

    def draft_to_card(self, data, source_text, *, retro):
        return card_from_draft(data, source_text, retro=retro)

    def finalize_card(self, card, content, name, aliases=(), roster=()):
        return card

    def _auto_tag(self, card_dict):
        return []

    @property
    def llm(self):
        return None


def _install_queue_bg(monkeypatch, store, distiller, *, workers=1):
    """把 `/start` 与 `/batch` 的真线程池装进测试：替身只压出网边界与落库投递。"""
    llm = A._install_user_llm(monkeypatch)
    monkeypatch.setattr("deps.get_distiller", lambda llm=None: distiller)
    monkeypatch.setattr("deps.get_text_manager", lambda llm=None: _TM())
    monkeypatch.setattr(D, "get_storage", lambda: store)
    monkeypatch.setattr(D, "submit_to_main_loop", lambda coro, timeout=10: asyncio.run(coro))
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="test-distill")
    monkeypatch.setattr(D, "_DISTILL_POOL", pool, raising=False)
    _POOLS.append(pool)

    futures = A._Futures()
    real_submit = C.ctx_submit

    def _spy_submit(p, fn, *args, **kwargs):
        fut = real_submit(p, fn, *args, **kwargs)
        futures.append(fut)
        return fut

    monkeypatch.setattr(C, "ctx_submit", _spy_submit)
    return llm, futures


def _text(store, uid, body="角色说的话"):
    tid = f"txt_{uuid.uuid4().hex}"
    A._run_async(store.save_text(tid, "src.txt", body, user_id=uid))
    return tid


def _seed_roster(store, tid, uid, names=("甲", "乙", "丙", "丁")):
    A._run_async(store.save_characters(
        tid, [{"name": n, "aliases": []} for n in names],
        version=Distiller.IDENTIFY_VERSION,
    ))


def _row(store, task_id):
    return A._run_async(store.get_distill_task_unscoped(task_id))


def _wait_status(store, task_id, wanted, timeout=20):
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        row = _row(store, task_id)
        if row is not None and row["status"] == wanted:
            return row
        time.sleep(0.02)
    pytest.fail(f"任务 {task_id} 未到达 {wanted!r}，最后是 {row}")


def _identity_client(store, uid):
    """请求线程里立起 LLM_CALLER —— 生产里由 AuthMiddleware 做同一件事。"""
    app = FastAPI()
    app.include_router(D.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "t", "role": "user",
    }

    @app.middleware("http")
    async def _identity(request, call_next):
        token = LLM_CALLER.set(Caller(ip=None, user_id=uid))
        try:
            return await call_next(request)
        finally:
            LLM_CALLER.reset(token)

    return TestClient(app)


def _start(client, tid, name, **extra):
    return client.post("/api/distill/start",
                       json={"text_id": tid, "character_name": name, "force": False, **extra})


# ── Q1–Q5：全局排队 ────────────────────────────────────────────────────────


@xfail
def test_q1_second_user_waits_as_queued(store, user_id, monkeypatch):
    uid_a = f"usr_{uuid.uuid4().hex[:8]}"
    tid_a, tid_b = _text(store, uid_a), _text(store, user_id)
    distiller = _QueueProbeDistiller()
    _llm, futures = _install_queue_bg(monkeypatch, store, distiller, workers=1)
    client_a, client_b = A._build_client(store, uid_a), A._build_client(store, user_id)
    try:
        assert _start(client_a, tid_a, "甲").status_code == 200
        assert distiller.wait_started("甲"), "第一个任务没占住唯一 worker"

        rb = _start(client_b, tid_b, "乙")
        assert rb.status_code == 200, rb.text
        assert _row(store, rb.json()["task_id"])["status"] == "queued"

        body = client_b.get(f"/api/distill/task/{rb.json()['task_id']}").json()
        assert body["done"] is False
        assert body["actions"] == ["cancel"]
    finally:
        distiller.release_all()


@xfail
def test_q2_queued_task_runs_after_slot_frees(store, user_id, monkeypatch):
    uid_a = f"usr_{uuid.uuid4().hex[:8]}"
    tid_a, tid_b = _text(store, uid_a), _text(store, user_id)
    distiller = _QueueProbeDistiller()
    _llm, futures = _install_queue_bg(monkeypatch, store, distiller, workers=1)
    client_a, client_b = A._build_client(store, uid_a), A._build_client(store, user_id)
    try:
        assert _start(client_a, tid_a, "甲").status_code == 200
        assert distiller.wait_started("甲")
        rb = _start(client_b, tid_b, "乙")
        assert rb.status_code == 200, rb.text
        task_b = rb.json()["task_id"]
        assert _row(store, task_b)["status"] == "queued"

        distiller.release_character("甲")
        futures[0].result(timeout=30)
        assert _wait_status(store, task_b, "running")["status"] == "running"

        distiller.release_character("乙")
        futures[1].result(timeout=30)
        assert _row(store, task_b)["status"] == "done"
    finally:
        distiller.release_all()


@xfail
def test_q3_cancel_while_queued_never_runs_and_frees_slot(store, user_id, monkeypatch):
    uid_a = f"usr_{uuid.uuid4().hex[:8]}"
    tid_a, tid_b = _text(store, uid_a), _text(store, user_id)
    distiller = _QueueProbeDistiller()
    _llm, futures = _install_queue_bg(monkeypatch, store, distiller, workers=1)
    client_a, client_b = A._build_client(store, uid_a), A._build_client(store, user_id)
    try:
        assert _start(client_a, tid_a, "甲").status_code == 200
        assert distiller.wait_started("甲")
        rb = _start(client_b, tid_b, "乙")
        task_b = rb.json()["task_id"]
        assert _row(store, task_b)["status"] == "queued"

        assert client_b.delete(f"/api/distill/task/{task_b}").status_code == 200
        distiller.release_character("甲")
        futures[0].result(timeout=30)
        futures[1].result(timeout=30)

        assert distiller.calls.get("乙", 0) == 0
        assert _row(store, task_b)["status"] == "error"
        assert _start(client_b, tid_b, "乙").status_code == 200, "排队期取消后用户槽没释放"
    finally:
        distiller.release_all()


@xfail
def test_q4_text_deleted_while_queued_never_runs(store, user_id, monkeypatch):
    uid_a = f"usr_{uuid.uuid4().hex[:8]}"
    tid_a, tid_b = _text(store, uid_a), _text(store, user_id)
    distiller = _QueueProbeDistiller()
    _llm, futures = _install_queue_bg(monkeypatch, store, distiller, workers=1)
    client_a, client_b = A._build_client(store, uid_a), A._build_client(store, user_id)
    try:
        assert _start(client_a, tid_a, "甲").status_code == 200
        assert distiller.wait_started("甲")
        rb = _start(client_b, tid_b, "乙")
        assert _row(store, rb.json()["task_id"])["status"] == "queued"

        A._run_async(D.cancel_distill_tasks_by_text_id(tid_b))
        distiller.release_character("甲")
        futures[0].result(timeout=30)
        futures[1].result(timeout=30)

        assert distiller.calls.get("乙", 0) == 0
    finally:
        distiller.release_all()


@xfail
def test_q5_queued_task_keeps_submit_time_identity(store, user_id, monkeypatch):
    uid_a = f"usr_{uuid.uuid4().hex[:8]}"
    tid_a, tid_b = _text(store, uid_a), _text(store, user_id)
    distiller = _QueueProbeDistiller()
    _llm, futures = _install_queue_bg(monkeypatch, store, distiller, workers=1)
    client_a = A._build_client(store, uid_a)
    client_b = _identity_client(store, user_id)
    try:
        assert _start(client_a, tid_a, "甲").status_code == 200
        assert distiller.wait_started("甲")
        rb = _start(client_b, tid_b, "乙")
        assert rb.status_code == 200, rb.text
        assert _row(store, rb.json()["task_id"])["status"] == "queued", "B 没有真的在排队"

        distiller.release_character("甲")
        futures[0].result(timeout=30)
        assert distiller.wait_started("乙")
        assert distiller.seen_user["乙"] == user_id, \
            "排队后才开跑的任务丢了提交时的请求身份"
    finally:
        distiller.release_all()


# ── Q6–Q7：契约 ────────────────────────────────────────────────────────────


@xfail
def test_q6_no_busy_timeout_message_left():
    source = Path(D.__file__).read_text(encoding="utf-8")
    assert "服务器繁忙" not in source


@xfail
def test_q7_queued_affordances():
    assert D._task_affordances("queued") == (False, ["cancel"], 3000)


# ── Q9–Q11：批量入口 ───────────────────────────────────────────────────────


@xfail
def test_q9_batch_starts_each_name(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    _seed_roster(store, tid, user_id)
    distiller = _QueueProbeDistiller()
    _llm, futures = _install_queue_bg(monkeypatch, store, distiller, workers=3)
    client = A._build_client(store, user_id)

    resp = client.post("/api/distill/batch",
                       json={"text_id": tid, "names": ["甲", "乙"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [t["name"] for t in body["tasks"]] == ["甲", "乙"]
    assert len({t["task_id"] for t in body["tasks"]}) == 2
    assert body["errors"] == []
    for fut in futures:
        fut.result(timeout=30)


@xfail
def test_q10_batch_capacity_rejection_starts_nothing(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    _seed_roster(store, tid, user_id)
    A._seed_distill_row(store, f"dt_{uuid.uuid4().hex}", user_id, status="running", text_id=tid)
    A._seed_distill_row(store, f"dt_{uuid.uuid4().hex}", user_id, status="running", text_id=tid)
    distiller = _QueueProbeDistiller()
    _llm, futures = _install_queue_bg(monkeypatch, store, distiller, workers=3)
    client = A._build_client(store, user_id)

    resp = client.post("/api/distill/batch",
                       json={"text_id": tid, "names": ["丙", "丁"]})
    assert resp.status_code == 429, resp.text
    assert "已有蒸馏任务在运行" in resp.json()["detail"]
    assert futures == []
    with D._task_lock:
        slots = D._user_slots.get(user_id, set())
        assert (tid, "丙") not in slots and (tid, "丁") not in slots


@xfail
def test_q11_batch_rejects_name_outside_roster(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    _seed_roster(store, tid, user_id)
    distiller = _QueueProbeDistiller()
    _llm, futures = _install_queue_bg(monkeypatch, store, distiller, workers=3)
    client = A._build_client(store, user_id)

    resp = client.post("/api/distill/batch",
                       json={"text_id": tid, "names": ["戊"]})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "只能蒸馏识别名单里的角色"
    assert futures == []


# ── Q12–Q13：对照（#236 的每用户上限与同目标门不变）──────────────────────


def test_q12_fourth_serial_start_still_rejected(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    for name in ("甲", "乙", "丙"):
        resp, _ = A._capture_start(monkeypatch, store, user_id, tid, character=name)
        assert resp.status_code == 200, resp.text

    fourth, _ = A._capture_start(monkeypatch, store, user_id, tid, character="丁")
    assert fourth.status_code == 429, fourth.text
    assert "最多 3 个" in fourth.json()["detail"]


def test_q13_same_target_second_start_still_rejected(store, user_id, monkeypatch):
    tid = _text(store, user_id)
    first, _ = A._capture_start(monkeypatch, store, user_id, tid, character="甲")
    assert first.status_code == 200, first.text

    second, _ = A._capture_start(monkeypatch, store, user_id, tid, character="甲")
    assert second.status_code == 429, second.text
    assert "这个角色正在蒸馏中" in second.json()["detail"]
