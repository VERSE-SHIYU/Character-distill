"""G：/api/admin/tasks 改读 DB + 出参白名单（spec 验收）。

1. 重启后可见：内存空、DB 有 interrupted 行 → admin_tasks 返回它 —— 本项存在的理由。
2. 白名单：响应不含 user_id / _db 等内部字段（逐个显式断言，不靠键数量）。
3. 字段集与 _task_response 输出逐字段一致（不另起一套字段集）。
4. 非管理员 → 403（权限不足，非归属拒绝，不得翻 404）。
另：running 行的 mem 只细化 stage/message，不覆盖 status/progress_pct/存在性；
信封 {tasks, total, truncated} 的截断必须显式上报，不静默裁。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_storage
from routers import admin as A
from routers import distill as D
from routers.auth import get_current_user
from storage.sqlite_store import SQLiteStore


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _db_path(tmp_path) -> str:
    return str(tmp_path / f"test_{uuid.uuid4().hex}.db")


@pytest.fixture(autouse=True)
def _clean_tasks():
    """每条用例后清空模块级内存表，避免跨用例污染。"""
    yield
    with D._task_lock:
        D._tasks.clear()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(_db_path(tmp_path))


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex[:8]}"


def _build_client(store, *, is_admin=True, uid="admin1"):
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "u", "is_admin": is_admin,
    }
    return TestClient(app)


def _seed(store, user_id, *, status="running", character="甲", text_id="txt1",
          message="m", pct=0):
    task_id = f"dt_{uuid.uuid4().hex}"
    _run_async(store.create_distill_task(
        task_id, user_id, text_id, character, status=status,
        progress_pct=pct, message=message, card_id="", awakening="",
    ))
    return task_id


# ── 1. 重启后可见（本项存在的理由；改回读内存必红）──────────────────────────

class TestVisibleAfterRestart:
    def test_interrupted_row_visible_when_memory_empty(self, store, user_id):
        """内存空 = 模拟进程重启；DB 里 boot reconcile 置的 interrupted 行必须可见。"""
        task_id = _seed(store, user_id, status="running", character="角色A", message="蒸馏中")
        _run_async(store.mark_interrupted_distills())   # 走真实的 boot reconcile 路径

        with D._task_lock:
            assert D._tasks == {}            # 显式保证内存空 —— 读内存的实现必返回 []

        resp = _build_client(store).get("/api/admin/tasks")
        assert resp.status_code == 200
        by_id = {t["task_id"]: t for t in resp.json()["tasks"]}
        assert task_id in by_id, "重启后被置 interrupted 的行对管理员不可见"
        row = by_id[task_id]
        assert row["status"] == "interrupted"
        assert row["done"] is True
        assert row["actions"] == ["resume"]
        assert row["character"] == "角色A"

    def test_memory_only_task_not_invented(self, store, user_id):
        """反向：DB 无行 → 不返回（admin 是 DB 真相视图，不是内存镜像）。"""
        with D._task_lock:
            D._tasks["dt_ghost"] = {"status": "analyzing", "progress_pct": 40,
                                    "message": "分析中", "user_id": user_id}
        resp = _build_client(store).get("/api/admin/tasks")
        assert resp.status_code == 200
        assert resp.json()["tasks"] == []


# ── 2. 白名单：内部字段逐个显式断言 ────────────────────────────────────────

class TestOutputWhitelist:
    def test_internal_fields_absent(self, store, user_id):
        """DB 行含 user_id 等内部列，响应里逐个别断言不存在。"""
        _seed(store, user_id, status="done", pct=100)
        body = _build_client(store).get("/api/admin/tasks").json()
        assert len(body["tasks"]) == 1
        item = body["tasks"][0]
        for internal in ("user_id", "_db", "chunk_size", "overlap",
                         "text_fingerprint", "created_at", "updated_at"):
            assert internal not in item, f"内部字段泄漏: {internal}"

    def test_spread_of_row_would_leak(self, store, user_id):
        """对照：直接把 DB 行 spread 出去确实会带 user_id —— 证明上面那条有判别力。"""
        task_id = _seed(store, user_id, status="done", pct=100)
        row = _run_async(store.get_distill_task_unscoped(task_id))
        assert "user_id" in row                      # 行里本就有
        assert "user_id" not in D._task_response(row)  # 白名单把它挡在外面


# ── 3. 字段集与 _task_response 一致 ────────────────────────────────────────

class TestFieldSetMatchesContract:
    def test_item_equals_task_response_of_db_row(self, store, user_id):
        """每一项必须逐字段等于 _task_response(DB 行) —— 不另起字段集。"""
        task_id = _seed(store, user_id, status="error", pct=37, message="蒸馏失败: x")
        row = _run_async(store.get_distill_task_unscoped(task_id))
        expected = D._task_response(row)
        item = _build_client(store).get("/api/admin/tasks").json()["tasks"][0]
        assert item == expected
        assert set(item) == {
            "task_id", "status", "done", "actions", "poll_after_ms", "progress_pct",
            "message", "character", "text_id", "card_id", "awakening", "stage",
        }

    def test_running_row_gets_mem_stage_but_db_truth_wins(self, store, user_id):
        """mem 只细化 stage/message；status/progress_pct 仍以 DB 为准。"""
        task_id = _seed(store, user_id, status="running", pct=5, message="DB 粗消息")
        with D._task_lock:
            D._tasks[task_id] = {
                "status": "analyzing", "message": "分析第 3/12 片",
                "progress_pct": 999,          # 内存谎报 —— 不得覆盖 DB
                "user_id": "someone-else",
            }
        item = _build_client(store).get("/api/admin/tasks").json()["tasks"][0]
        assert item["stage"] == "analyzing"
        assert item["message"] == "分析第 3/12 片"
        assert item["status"] == "running"       # DB 真相
        assert item["progress_pct"] == 5         # 内存 999 不得覆盖


# ── 3b. 截断必须显式上报（静默截断与「失败必须可见」相悖）───────────────────

class _SmallCapStore(SQLiteStore):
    """上限压到 2 的 store —— 不必造 201 行就能验截断上报。"""

    async def list_distill_tasks(self, limit: int = 2) -> list[dict]:
        return await super().list_distill_tasks(limit=limit)


class TestEnvelope:
    def test_total_and_not_truncated_when_under_cap(self, store, user_id):
        _seed(store, user_id, status="done")
        _seed(store, user_id, status="error")
        body = _build_client(store).get("/api/admin/tasks").json()
        assert body["total"] == 2
        assert len(body["tasks"]) == 2
        assert body["truncated"] is False

    def test_truncated_true_and_total_is_whole_table(self, user_id, tmp_path):
        """被裁过必须显式上报，且 total 是全表数、不被上限污染。"""
        capped = _SmallCapStore(_db_path(tmp_path))
        for _ in range(3):
            _seed(capped, user_id, status="done")
        body = _build_client(capped).get("/api/admin/tasks").json()
        assert len(body["tasks"]) == 2        # 上限生效
        assert body["total"] == 3             # total 未被上限污染
        assert body["truncated"] is True


# ── 4. 非管理员 → 403 ──────────────────────────────────────────────────────

class TestAuthz:
    def test_non_admin_gets_403_not_404(self, store):
        resp = _build_client(store, is_admin=False).get("/api/admin/tasks")
        assert resp.status_code == 403
        assert resp.status_code != 404
