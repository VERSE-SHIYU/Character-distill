# -*- coding: utf-8 -*-
"""识别失败 / 无目标角色：三个通道（bg 任务 / SSE / HTTP）各自的形态。

缺陷 86 的形态：失败被当成「空名单」。`chars = []` 有两处宽捕获，把上游故障
（网络 / DB / 额度）与「这本书真的没有具名角色」渲染成同一句话 —— 用户看到
「没识别到角色」，运维什么都收不到，两类结果不可辨。

本文件锁三件事：

  1. **bg 任务不吞识别失败** —— 识别抛异常，任务行是失败文案，**不是**「未识别到任何角色」
  2. **SSE 不吞识别失败** —— 同上，看错误帧
  3. **判据只有一处** —— 空名单时三个通道给出**同一句**「未识别到任何角色」

第 3 条的期望文案是**字面量**（不从源码 import）：文案定义在
``core/character_roster.py`` 一处（`NoTargetCharacter.user_message`），
变异把那一处改掉 → 三个通道的断言同时变红。

变异对象一览：
  - 1/2：把 `chars = []` 宽捕获加回去（bg 那条改回 `except Exception: chars = []`，
        SSE 那条在 `_event_gen` 里改回）→ 任务行 / 错误帧变回「未识别到任何角色」
  - 3：  把 `_NO_TARGET_MESSAGES` 里的中文改一个字 → 三通道断言同时红
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
import server
from core.distiller import DistillError, Distiller
from core.text_manager import TextManager
from deps import get_storage
from routers import distill as D
from routers.auth import get_current_user
from storage.sqlite_store import SQLiteStore

# 三通道共用的期望文案。改动被锁的是「定义文案的那一处」（character_roster），
# 所以这里必须是复制过来的字面量 —— 从源码 import 就自证自明，变异杀不掉。
EMPTY_ROSTER_TEXT = "未识别到任何角色"
FAILURE_TEXT = "识别失败：上游接口限流，请稍后重试"


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _RaisingDistiller:
    """识别直接抛 —— 模拟上游故障（网络 / 额度 / DB）。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def identify_characters(self, content):
        raise self._exc


class _EmptyRosterDistiller:
    """识别**成功**，但名单是空的 —— 这本书真的没有具名角色。"""

    def identify_characters(self, content):
        return []


class _OpenSemaphore:
    def acquire(self, timeout=0):
        return True

    def release(self):
        pass


class _TM:
    """只保证 `/run` 与 `/run_stream` 的 503 门过得去；本文件不考蒸馏结果。"""

    async def get_or_distill(self, *a, **kw):
        raise AssertionError("无目标角色时不该走到蒸馏")


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _clean_tasks():
    yield
    with D._task_lock:
        D._tasks.clear()
        D._user_slots.clear()


def _seed_text(store, uid, body="角色说的话"):
    tid = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", body, user_id=uid))
    return tid


def _build_client(store, uid, monkeypatch, *, distiller, tm=None):
    app = FastAPI()
    app.include_router(D.router)
    # legacy 两条（`POST /api/identify` / `POST /api/distill`）也挂上：它们的依赖是
    # 函数体里 late-import 的，上面的 monkeypatch 一样管用。
    app.include_router(D.legacy_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "testuser", "is_admin": False,
    }
    app.dependency_overrides[D.get_sessions] = lambda: {}
    server.register_domain_error_handlers(app)   # 与生产装配同一处

    async def _llm(*a, **kw):
        return object()

    # 必须走 monkeypatch：裸赋值 `deps.get_user_llm = ...` 会泄漏到同进程后续用例
    # （test_llm_access_gate 的 L1–L4 就是靠 deps.get_user_llm 取实例的，被污染即红）。
    monkeypatch.setattr(deps, "get_user_llm", _llm)
    monkeypatch.setattr(deps, "get_distiller", lambda *a, **kw: distiller)
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: tm or _TM())
    return TestClient(app, raise_server_exceptions=False)


def _run_bg(store, user_id, text_id, distiller, monkeypatch):
    """驱动真 bg 任务的失败收口，返回落库后的任务行。

    直接调 `_run_distill_task`（改动就在它里面），不经 HTTP：本文件不考路由与槽位，
    那两条各自的用例在 test_distill_task_api.py。
    """
    task_id = f"dt_{uuid.uuid4().hex}"
    _run_async(store.create_distill_task(
        task_id, user_id, text_id, character="", status="running", progress_pct=0,
        message="进行中", card_id="", awakening="",
    ))
    with D._task_lock:
        D._tasks[task_id] = {
            "status": "running", "progress_pct": 0, "user_id": user_id,
            "text_id": text_id, "character": "", "message": "",
            "card_id": "", "awakening": "", "_db": None,
        }

    monkeypatch.setattr(D, "get_storage", lambda: store)
    monkeypatch.setattr(D, "submit_to_main_loop", lambda coro, timeout=10: asyncio.run(coro))
    monkeypatch.setattr(D, "_DISTILL_SEMAPHORE", _OpenSemaphore())
    monkeypatch.setattr("deps.get_distiller", lambda llm=None: distiller)

    D._run_distill_task(
        task_id, text_id, "", False, user_id, "角色说的话", "story", object(),
    )
    return _run_async(store.get_distill_task_unscoped(task_id))


def _sse_errors(text: str) -> list[dict]:
    frames = [json.loads(l[len("data: "):]) for l in text.splitlines() if l.startswith("data: ")]
    return [f for f in frames if "error" in f]


# ── 1. bg 任务不吞识别失败 ────────────────────────────────────────────────


class TestBgTaskDoesNotSwallowIdentifyFailure:
    def test_identify_failure_is_the_task_message(self, store, user_id, monkeypatch):
        """识别抛异常 → 任务行是那条失败文案，不是「未识别到任何角色」。

        变异对象 = 把宽捕获加回 bg：`except Exception: chars = []` → 空名单 →
        `target_character_name` 抛 NoTargetCharacter → 任务行变成「未识别到任何角色」，本用例红。
        """
        tid = _seed_text(store, user_id)
        distiller = _RaisingDistiller(DistillError(FAILURE_TEXT, "API 429；9/12 个分片失败"))

        row = _run_bg(store, user_id, tid, distiller, monkeypatch)

        assert row["status"] == "error"
        assert row["message"] == FAILURE_TEXT
        assert row["message"] != EMPTY_ROSTER_TEXT, "上游故障被渲染成了「没有角色」"
        assert "429" not in row["message"], "运维口径漏上屏"


# ── 2. SSE 不吞识别失败 ──────────────────────────────────────────────────


class TestStreamDoesNotSwallowIdentifyFailure:
    def test_identify_failure_is_the_error_frame(self, store, user_id, monkeypatch):
        """识别抛异常 → SSE 错误帧是那条失败文案，不是「未识别到任何角色」。

        变异对象 = 把宽捕获加回 `_event_gen`：帧里变成「未识别到任何角色」，本用例红。
        """
        tid = _seed_text(store, user_id)
        client = _build_client(
            store, user_id, monkeypatch,
            distiller=_RaisingDistiller(DistillError(FAILURE_TEXT, "API 429")),
        )

        r = client.post("/api/distill/run_stream", json={"text_id": tid})

        assert r.status_code == 200
        errs = _sse_errors(r.text)
        assert len(errs) == 1, f"期望恰好一帧 error，实际 {errs}"
        assert errs[0]["error"] == FAILURE_TEXT
        assert errs[0]["error"] != EMPTY_ROSTER_TEXT, "上游故障被渲染成了「没有角色」"


# ── 3. 判据只有一处：三通道同一句话 ───────────────────────────────────────


class TestNoTargetCharacterHasASingleSource:
    """空名单时三通道给出同一句「未识别到任何角色」。

    三份判据（HTTP 的 `_first_character_name`、bg 与 SSE 各自手抄的两段）原先各写
    一遍文案。判据下沉到 `core/character_roster.target_character_name` 后，文案只
    定义在 `NoTargetCharacter.user_message` 一处 —— 本组三个通道断言同一句字面量。

    变异对象 = 改 `_NO_TARGET_MESSAGES` 里的中文：三处断言**同时**变红。
    """

    def test_bg_task_says_it(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)

        row = _run_bg(store, user_id, tid, _EmptyRosterDistiller(), monkeypatch)

        assert row["status"] == "error"
        assert row["message"] == EMPTY_ROSTER_TEXT

    def test_sse_frame_says_it(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        client = _build_client(
            store, user_id, monkeypatch, distiller=_EmptyRosterDistiller())

        r = client.post("/api/distill/run_stream", json={"text_id": tid})

        errs = _sse_errors(r.text)
        assert len(errs) == 1, f"期望恰好一帧 error，实际 {errs}"
        assert errs[0]["error"] == EMPTY_ROSTER_TEXT

    def test_http_says_it(self, store, user_id, monkeypatch):
        """HTTP：不点名时不走识别失败，而是「挑不出角色」→ 400 + 同一句文案。

        码 400 由 `web/server.py::_domain_error_status` 按 MRO 给（NoTargetCharacter
        是 DistillError 的子类），路由不再自己配码。
        """
        tid = _seed_text(store, user_id)
        client = _build_client(
            store, user_id, monkeypatch, distiller=_EmptyRosterDistiller())

        r = client.post("/api/distill/run", json={"text_id": tid})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == EMPTY_ROSTER_TEXT


# ── 4. 识别族 HTTP 路由不吞 DistillError（同一缺陷的 HTTP 侧收尾） ────────

# `_identify_single_call` 两次解析均失败时抛的那个 DistillError 的上屏文案。
# 同上面的口径：复制字面量，不从源码 import（import 就自证自明，变异杀不掉）。
PARSE_FAIL_TEXT = "识别失败：模型返回的角色名单无法解析，请重试"


class _NonJSONLLM:
    """两次都回非 JSON —— `_identify_single_call` 的首次与重试解析都会失败。

    刻意用**真 `Distiller`** 而不是桩：本组判的正是「识别层抛出的 `DistillError`
    能不能穿过路由层到达统一出口」，桩掉识别层就把被测的那一段换掉了。
    """

    model = "fake-model-identify-parse-fail"

    def __init__(self) -> None:
        self.last_usage = None
        self.calls = 0

    def chat(self, system, messages):
        self.calls += 1
        return "这不是一个 JSON 数组"


class _StubTextManager:
    """只让 legacy `/api/distill` 走过 `upload_text` 那一跳 —— 本文件不考上传。"""

    async def upload_text(self, filename, content, **kw):
        return {"text_id": f"txt_{uuid.uuid4().hex}"}


def _failing_distiller() -> Distiller:
    return Distiller(llm=_NonJSONLLM(), config_path=None)


class TestIdentifyFamilyRoutesKeepDistillError:
    """三条识别族 HTTP 路由不再就地包 `HTTPException(500, "操作失败，请稍后重试")`。

    原先那条宽捕获拦下 `DistillError`：单分片解析失败从 400 变 500，用户也看不到
    「名单无法解析」这个真实原因。删掉捕获后，异常冒泡到 `web/server.py` 的
    `_domain_error_handler`（400 + `user_message`）—— 同文件的 `/identify`（带
    text_id）本来就是这个形状。

    变异对象 = 在任一处加回 `except Exception: raise HTTPException(500, ...)`
    → 该用例状态码 500、detail 变「操作失败，请稍后重试」，红。
    """

    def test_legacy_identify_parse_failure_is_400(self, store, user_id, monkeypatch):
        """`POST /api/identify`（`_do_identify`）→ 400 + 真实原因。"""
        client = _build_client(
            store, user_id, monkeypatch, distiller=_failing_distiller())

        r = client.post("/api/identify", json={"text": f"甲说了一句话。{uuid.uuid4().hex}"})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == PARSE_FAIL_TEXT

    def test_legacy_distill_auto_identify_failure_is_400(self, store, user_id, monkeypatch):
        """`POST /api/distill` 不点名 → `_resolve_character_name` → 400 + 真实原因。"""
        client = _build_client(
            store, user_id, monkeypatch,
            distiller=_failing_distiller(), tm=_StubTextManager())

        r = client.post(
            "/api/distill",
            json={"text": f"甲说了一句话。{uuid.uuid4().hex}", "character_name": ""})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == PARSE_FAIL_TEXT

    def test_reindex_identify_failure_is_400(self, store, user_id, monkeypatch):
        """`POST /api/distill/reindex/{id}` 的名单段 → 400 + 真实原因。"""
        tid = _seed_text(store, user_id, body=f"角色说的话{uuid.uuid4().hex}")
        client = _build_client(
            store, user_id, monkeypatch, distiller=_failing_distiller())

        r = client.post(f"/api/distill/reindex/{tid}")

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == PARSE_FAIL_TEXT


# ── 5. 点名 `/run` 的别名解析不吞 DistillError（TextManager 那处宽捕获） ───


class TestNamedRunKeepsIdentifyFailure:
    """点名蒸馏走 `TextManager.get_or_distill`：卡片未命中时要先取别名。

    那段取别名原先自带 `except Exception: print(...)`，识别失败被**降级成「没有别名」**
    —— 用户看到蒸馏照常成功、只是别名缺失，故障无声。删掉宽捕获后 `DistillError` 冒泡到
    路由的 `except DistillError: raise`（`/run` 与 legacy `/api/distill` 各一处），
    由 `web/server.py` 统一出口给出 400 + `user_message`。

    变异对象 = 把宽捕获加回 `core/text_manager.py` 的别名段（`except Exception: print(...)`）
    → 识别失败被吞，代码继续往下走（桩上没有 `distill_incremental` → AttributeError）→
    500「操作失败，请稍后重试」→ 本用例的 400 断言红。
    """

    def test_named_run_identify_failure_is_400(self, store, user_id, monkeypatch):
        tid = _seed_text(store, user_id)
        distiller = _RaisingDistiller(DistillError(FAILURE_TEXT, "API 429"))
        tm = TextManager(lambda: store, distiller, object(), {}, memory_manager=None)
        client = _build_client(
            store, user_id, monkeypatch, distiller=distiller, tm=tm)

        r = client.post(
            "/api/distill/run", json={"text_id": tid, "character_name": "甲"})

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == FAILURE_TEXT
        assert r.json()["detail"] != EMPTY_ROSTER_TEXT, "上游故障被渲染成了「没有角色」"
