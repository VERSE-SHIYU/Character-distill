# -*- coding: utf-8 -*-
"""缺陷 75 的规格：删除文本的三个副作用必须在**鉴权通过之后**发生。

三个副作用：
  1. 取消在途**上传**任务 —— `routers.text._upload_tasks`（纯内存）；
  2. 取消在途**蒸馏**任务 —— `routers.distill._tasks`（内存）**与** `distill_tasks` 行的 status；
  3. 断开卡片 —— `UPDATE cards SET text_id`（持久写，且 SQL 谓词里没有 user_id）。

修前顺序是「副作用 → 鉴权」，于是**任何已登录用户**对**他人**的 text 发
`DELETE /api/text/{id}?keep_cards=true` 都能在拿到 404 之前把对方的卡摘掉、把对方的
在途任务掐掉 —— 跨用户越权写。本文件钉住修后顺序：鉴权不过 = 三样一件没动。

**零副作用必须配正控**：只断言「非属主时什么都没变」，一个「什么都不做」的实现也全绿。
故每个非属主用例都有对应的属主用例，断言同样的三样**都变了**。

口径与全仓一致：非属主与不存在同判 **404**（防 ID 枚举）。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_storage
from routers import distill as distill_module
from routers import text as text_module
from routers.auth import get_current_user
from routers.distill import router as distill_router
from routers.text import router as text_router
from storage.sqlite_store import SQLiteStore

UPLOAD_TASK = "upload_task_1"
DISTILL_TASK = "distill_task_1"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"side_effects_{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_a():
    return f"user_a_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def user_b():
    return f"user_b_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _clean_task_globals():
    """两个任务表都是模块级进程全局 —— 不清会跨用例互相污染。"""
    def clear():
        with text_module._upload_task_lock:
            text_module._upload_tasks.clear()
        with distill_module._task_lock:
            distill_module._tasks.clear()

    clear()
    yield
    clear()


def _client(store, user_id, monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(text_router)
    app.include_router(distill_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "username": "testuser", "is_admin": False,
    }
    # `cancel_distill_tasks_by_text_id` 直接调 `deps.get_storage()`（**不经 Depends**），
    # `dependency_overrides` 拦不住它 —— 不重定向就会去开本机 `data/character_sim.db`。
    # 用例的通过条件不得是本机恰好有什么库（缺陷 46 同型）。
    monkeypatch.setattr(distill_module, "get_storage", lambda: store)
    return TestClient(app)


@pytest.fixture
def client_a(store, user_a, monkeypatch):
    return _client(store, user_a, monkeypatch)


@pytest.fixture
def client_b(store, user_b, monkeypatch):
    return _client(store, user_b, monkeypatch)


# ── Seed / observe ────────────────────────────────────────────────────────────

def _seed(store, uid) -> tuple[str, str]:
    """造一条 text + 一张卡 + 一个在途上传任务 + 一个在途蒸馏任务（内存 + 库各一份）。"""
    tid = f"txt_{uuid.uuid4().hex}"
    cid = f"card_{uuid.uuid4().hex}"
    _run(store.save_text(tid, "src.txt", "内容", user_id=uid))
    _run(store.save_card(cid, tid, "张三", '{"name": "张三"}', user_id=uid))

    with text_module._upload_task_lock:
        text_module._upload_tasks[UPLOAD_TASK] = {
            "status": "parsing", "progress_pct": 5, "message": "解析文件中…",
            "text_id": tid, "user_id": uid,
        }
    with distill_module._task_lock:
        distill_module._tasks[DISTILL_TASK] = {
            "status": "running", "progress_pct": 30, "user_id": uid,
            "text_id": tid, "character": "张三", "message": "蒸馏中…",
        }
    _run(store.create_distill_task(DISTILL_TASK, uid, tid, "张三", status="running"))
    return tid, cid


def _card_text_id(store, cid) -> str:
    return _run(store.get_card_unscoped(cid))["text_id"]


def _upload_status() -> str:
    with text_module._upload_task_lock:
        return text_module._upload_tasks[UPLOAD_TASK]["status"]


def _distill_status_mem() -> str:
    with distill_module._task_lock:
        return distill_module._tasks[DISTILL_TASK]["status"]


def _distill_status_db(store) -> str:
    return _run(store.get_distill_task_unscoped(DISTILL_TASK))["status"]


def _assert_untouched(store, cid, tid):
    """三样副作用一件没动。"""
    assert _card_text_id(store, cid) == tid, "卡片被摘了（跨用户越权写）"
    assert _upload_status() == "parsing", "上传任务被掐了"
    assert _distill_status_mem() == "running", "蒸馏任务（内存）被掐了"
    row = _run(store.get_distill_task_unscoped(DISTILL_TASK))
    assert row is not None and row["status"] == "running", "蒸馏行被置 error 了或被删了"


def _assert_cancelled_and_detached(store, cid, *, distill_row_deleted: bool = False):
    """三样副作用都发生了。

    永久删除路径的 `distill_tasks` 行随后会被 `hard_delete_text` 清掉，DB 那半**事后
    不可观测** —— 那里只断言行已消失，取消的证据由**内存** `_tasks` 承担（它才是给
    后台线程的停止信号）。硬把 DB 状态也断言成 error 会把一个永远读不到的状态写进用例。
    """
    assert _card_text_id(store, cid) == "", "卡片没被断开"
    assert _upload_status() == "error", "上传任务没被取消"
    assert _distill_status_mem() == "error", "蒸馏任务（内存）没被取消"
    row = _run(store.get_distill_task_unscoped(DISTILL_TASK))
    if distill_row_deleted:
        assert row is None, "永久删除后蒸馏行该被清掉"
    else:
        assert row is not None and row["status"] == "error", "蒸馏行没被置 error"


# ═══════════════════════════════════════════════════════════════════════════════
# 非属主：404，且零副作用
# ═══════════════════════════════════════════════════════════════════════════════

class TestNonOwnerHasNoSideEffects:
    def test_soft_delete_keep_cards(self, store, user_a, client_b):
        tid, cid = _seed(store, user_a)
        r = client_b.delete(f"/api/text/{tid}", params={"keep_cards": True})
        assert r.status_code == 404, f"期望 404，实得 {r.status_code}"
        _assert_untouched(store, cid, tid)

    def test_permanent_delete_keep_cards(self, store, user_a, client_b):
        tid, cid = _seed(store, user_a)
        r = client_b.delete(f"/api/text/{tid}/permanent", params={"keep_cards": True})
        assert r.status_code == 404, f"期望 404，实得 {r.status_code}"
        _assert_untouched(store, cid, tid)

    def test_soft_delete_without_keep_cards_still_cancels_nothing(
            self, store, user_a, client_b):
        """取消任务那一半**不看 keep_cards** —— 不带这个参数照样是越权。"""
        tid, cid = _seed(store, user_a)
        r = client_b.delete(f"/api/text/{tid}")
        assert r.status_code == 404, f"期望 404，实得 {r.status_code}"
        _assert_untouched(store, cid, tid)

    def test_permanent_delete_without_keep_cards_still_cancels_nothing(
            self, store, user_a, client_b):
        tid, cid = _seed(store, user_a)
        r = client_b.delete(f"/api/text/{tid}/permanent")
        assert r.status_code == 404, f"期望 404，实得 {r.status_code}"
        _assert_untouched(store, cid, tid)

    def test_unknown_text_id_404_and_no_crash(self, store, client_b):
        r = client_b.delete(f"/api/text/txt_{uuid.uuid4().hex}", params={"keep_cards": True})
        assert r.status_code == 404, f"期望 404，实得 {r.status_code}"


# ═══════════════════════════════════════════════════════════════════════════════
# 属主正控：三样副作用都发生（否则「零副作用」可以靠什么都不做假绿）
# ═══════════════════════════════════════════════════════════════════════════════

class TestOwnerPositiveControl:
    def test_soft_delete_keep_cards(self, store, user_a, client_a):
        tid, cid = _seed(store, user_a)
        r = client_a.delete(f"/api/text/{tid}", params={"keep_cards": True})
        assert r.status_code == 200, r.text
        _assert_cancelled_and_detached(store, cid)
        assert _run(store.get_text_unscoped(tid))["deleted_at"], "文本没进回收站"

    def test_permanent_delete_keep_cards(self, store, user_a, client_a):
        tid, cid = _seed(store, user_a)
        _run(store.delete_text(tid))
        r = client_a.delete(f"/api/text/{tid}/permanent", params={"keep_cards": True})
        assert r.status_code == 200, r.text
        _assert_cancelled_and_detached(store, cid, distill_row_deleted=True)
        assert _run(store.get_text_unscoped(tid)) is None, "文本没被永久删除"

    def test_soft_delete_without_keep_cards_keeps_card_attached(
            self, store, user_a, client_a):
        """不带 keep_cards 时卡片**不该**被断开 —— 否则正控只证明了「总会断开」。"""
        tid, cid = _seed(store, user_a)
        r = client_a.delete(f"/api/text/{tid}")
        assert r.status_code == 200, r.text
        assert _card_text_id(store, cid) == tid, "没要求 keep_cards，卡片却被断开了"
        assert _upload_status() == "error"
        assert _distill_status_mem() == "error"
        assert _distill_status_db(store) == "error"


# ═══════════════════════════════════════════════════════════════════════════════
# admin：跨属主删除是**放行**的，副作用照常发生（与非属主的分界就在这里）
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminIsAuthorized:
    def test_admin_soft_delete_foreign_text_cancels_and_detaches(
            self, store, user_a, monkeypatch):
        tid, cid = _seed(store, user_a)
        app = FastAPI()
        app.include_router(text_router)
        app.include_router(distill_router)
        app.dependency_overrides[get_storage] = lambda: store
        app.dependency_overrides[get_current_user] = lambda: {
            "id": f"admin_{uuid.uuid4().hex[:8]}", "username": "admin", "is_admin": True,
        }
        monkeypatch.setattr(distill_module, "get_storage", lambda: store)

        r = TestClient(app).delete(f"/api/text/{tid}", params={"keep_cards": True})
        assert r.status_code == 200, r.text
        _assert_cancelled_and_detached(store, cid)
