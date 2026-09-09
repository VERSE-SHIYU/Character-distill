"""步骤2.1 DB-truth 收尾的 red-first 测试（spec §6.1）。

A  _set_task 记账后置：落库失败 → _db 不提前、条目不 pop；恢复后同状态重试能补发。
A2 终态收口确认写：终态落库失败 → finally 补一次；补写仍失败 → 打独立日志不 pop 不抛；
    补写成功 → pop。
B  distill_task_params 归位读 DB（无内存回退）：内存空 + DB 行 → 200 从 DB 取参；
    他人任务 → 403；不存在 → 404。
C  distill_start 落库失败 → 拒绝启动（503），不开后台线程。
D  distill_task_status 只读覆盖：DB running + 内存活跃 → 覆盖 message / 加 stage；
    其余字段与存在性永远以 DB 为准；DB 非 running → 不覆盖。

改前跑：A/A2/D 的内存覆盖断言应红；B/C 对旧（纯内存）实现应红。
"""

from __future__ import annotations

import asyncio
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_storage
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
    """每条用例后清空模块级内存任务表，避免跨用例污染。"""
    yield
    with D._task_lock:
        D._tasks.clear()


# ── 单元测试（A / A2）：monkeypatch 模块级 get_storage + run_on_main_loop ─────

class _FakeStore:
    """save_distill_task 可控失败；成功时记录完整快照。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.attempts = 0
        self.fail = False

    async def save_distill_task(self, task_id, user_id, text_id, character="",
                                status="queued", progress_pct=0, message="",
                                card_id="", awakening=""):
        self.attempts += 1
        if self.fail:
            raise RuntimeError("db down")
        self.calls.append({
            "task_id": task_id, "user_id": user_id, "text_id": text_id,
            "character": character, "status": status, "progress_pct": progress_pct,
            "message": message, "card_id": card_id, "awakening": awakening,
        })
        return self.calls[-1]


def _install(monkeypatch, store):
    """让 distill 的落库路径打到假 store：同步 await，不碰真 DB / 真 loop。"""
    monkeypatch.setattr(D, "get_storage", lambda: store)

    def _sync_run(coro, timeout=10):
        return asyncio.run(coro)

    monkeypatch.setattr(D, "run_on_main_loop", _sync_run)


def _seed_task(task_id, status, pct, db):
    with D._task_lock:
        D._tasks[task_id] = {
            "status": status, "progress_pct": pct, "user_id": "usr1",
            "text_id": "txt1", "character": "甲", "message": "m",
            "card_id": "", "awakening": "", "_db": db,
        }


class TestASetTaskAccounting:
    def test_db_failure_keeps_accounting_and_entry(self, monkeypatch):
        """A1：落库失败 → _db 保持旧值、条目不被 pop，下次同状态不会被去重吞。"""
        store = _FakeStore()
        _install(monkeypatch, store)
        _seed_task("tA", "queued", 5, ("running", 5))

        store.fail = True
        D._set_task("tA", {"progress_pct": 8, "message": "进行中"})

        with D._task_lock:
            t = D._tasks["tA"]
            assert t["progress_pct"] == 8          # 内存已更新
            assert t["_db"] == ("running", 5)       # 记账不提前于事实
            assert "tA" in D._tasks                  # 非终态不被 pop
        assert store.attempts == 1 and store.calls == []

    def test_recovery_same_state_retry_does_persist(self, monkeypatch):
        """A1：DB 恢复后，同状态的 _set_task 能补发（去重键比对到旧 _db 才生效）。"""
        store = _FakeStore()
        _install(monkeypatch, store)
        _seed_task("tA", "queued", 5, ("running", 5))

        store.fail = True
        D._set_task("tA", {"progress_pct": 8})
        store.fail = False
        D._set_task("tA", {"progress_pct": 8})   # 同状态重试

        assert store.attempts == 2                  # 两次都发了（第一次失败仍在途）
        assert len(store.calls) == 1                # 只有成功后入账
        assert store.calls[0]["progress_pct"] == 8
        with D._task_lock:
            assert D._tasks["tA"]["_db"] == ("running", 8)   # 落库成功才记账
            assert "tA" in D._tasks                          # running 不 pop


class TestA2TerminalConfirm:
    def test_failed_terminal_confirm_logs_keeps_entry(self, monkeypatch, capsys):
        """A2：终态确认写失败 → 独立日志、不 pop、_db 不动。"""
        store = _FakeStore()
        _install(monkeypatch, store)
        _seed_task("tB", "error", 40, ("running", 40))

        store.fail = True
        D._confirm_terminal_persist("tB")

        with D._task_lock:
            t = D._tasks["tB"]
            assert t["_db"] == ("running", 40)
            assert "tB" in D._tasks
        out = capsys.readouterr().out
        assert "TERMINAL persist failed in final confirm" in out

    def test_second_confirm_success_pops(self, monkeypatch):
        """A2：补写成功 → DB 终态 + 写缓存 pop。"""
        store = _FakeStore()
        _install(monkeypatch, store)
        _seed_task("tB", "error", 40, ("running", 40))

        store.fail = True
        D._confirm_terminal_persist("tB")
        store.fail = False
        D._confirm_terminal_persist("tB")

        assert store.calls[-1]["status"] == "error"
        assert store.calls[-1]["progress_pct"] == 40
        with D._task_lock:
            assert "tB" not in D._tasks

    def test_already_confirmed_no_write(self, monkeypatch):
        """A2：_db 已等于终态（pop 未执行的边缘）→ 不发多余写。"""
        store = _FakeStore()
        _install(monkeypatch, store)
        _seed_task("tC", "error", 40, ("error", 40))
        D._confirm_terminal_persist("tC")
        assert store.attempts == 0

    def test_entry_popped_no_write(self, monkeypatch):
        """A2：条目已被终态成功 pop → confirm 无事可做。"""
        store = _FakeStore()
        _install(monkeypatch, store)
        D._confirm_terminal_persist("tX")   # 内存里没有这个任务
        assert store.attempts == 0


# ── 路由测试（B / C / D）：独立 app + 真 SQLiteStore ────────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(_db_path(tmp_path))


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex[:8]}"


def _build_client(store, uid):
    app = FastAPI()
    app.include_router(D.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "testuser", "is_admin": False,
    }
    return TestClient(app)


def _seed_distill_row(store, task_id, user_id, *, status="running", pct=0,
                      character="甲", text_id="txt1", message="m"):
    _run_async(store.save_distill_task(
        task_id, user_id, text_id, character, status=status,
        progress_pct=pct, message=message, card_id="", awakening="",
    ))


class TestBParamsReadsDB:
    def test_params_from_db_when_memory_empty(self, store, user_id):
        """B：内存空（含终态 pop 后）仍能从 DB 取到 text_id/character。"""
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_distill_row(store, task_id, user_id, status="done", pct=100,
                          character="Alice", text_id="txt_abc")
        client = _build_client(store, user_id)
        with D._task_lock:                      # 显式保证无内存条目
            assert task_id not in D._tasks

        resp = client.get(f"/api/distill/task/{task_id}/params")
        assert resp.status_code == 200
        body = resp.json()
        assert body["text_id"] == "txt_abc"
        assert body["character"] == "Alice"

    def test_params_other_users_task_forbidden(self, store, user_id):
        """B：他人任务 → 403。"""
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_distill_row(store, task_id, "usr_other")
        client = _build_client(store, user_id)
        resp = client.get(f"/api/distill/task/{task_id}/params")
        assert resp.status_code == 403

    def test_params_nonexistent_404(self, store, user_id):
        """B：不存在 → 404。"""
        client = _build_client(store, user_id)
        resp = client.get(f"/api/distill/task/dt_{uuid.uuid4().hex}/params")
        assert resp.status_code == 404


class TestDStatusReadonlyStage:
    def _seed_text(self, store, user_id):
        tid = f"txt_{uuid.uuid4().hex}"
        _run_async(store.save_text(tid, "src.txt", "正文内容", user_id=user_id))
        return tid

    def test_db_running_plus_memory_rich_overrides_message_stage(self, store, user_id):
        """D：DB running + 内存活跃 → stage 取自内存、message 细化，status/pct 仍 DB。"""
        tid = self._seed_text(store, user_id)
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_distill_row(store, task_id, user_id, status="running", pct=5,
                          character="甲", text_id=tid, message="排队中(最多同时3个蒸馏)")
        with D._task_lock:
            D._tasks[task_id] = {"status": "analyzing", "progress_pct": 90,
                                 "message": "分析角色 3/10"}
        client = _build_client(store, user_id)

        body = client.get(f"/api/distill/task/{task_id}").json()
        assert body["status"] == "running"          # DB，非内存
        assert body["progress_pct"] == 5            # DB pct（90 在内存，不覆盖）
        assert body["stage"] == "analyzing"          # 内存 rich 展示字段
        assert body["message"] == "分析角色 3/10"
        assert body["text_id"] == tid

    def test_db_terminal_no_memory_no_stage(self, store, user_id):
        """D：DB done + 内存空 → stage 空串、其余全 DB。"""
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_distill_row(store, task_id, user_id, status="done", pct=100,
                          character="Bob", message="蒸馏完成 ✓", text_id="txt_abc")
        client = _build_client(store, user_id)

        body = client.get(f"/api/distill/task/{task_id}").json()
        assert body["status"] == "done"
        assert body["progress_pct"] == 100
        assert body["stage"] == ""
        assert body["message"] == "蒸馏完成 ✓"

    def test_db_terminal_ignores_stale_memory(self, store, user_id):
        """D：DB 非 running → 即使内存还有残留条目也不覆盖（防终态后残留误显示）。"""
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_distill_row(store, task_id, user_id, status="error", pct=40,
                          message="DB 错误信息", text_id="txt_abc")
        with D._task_lock:
            D._tasks[task_id] = {"status": "analyzing", "message": "残留的活跃信息"}
        client = _build_client(store, user_id)

        body = client.get(f"/api/distill/task/{task_id}").json()
        assert body["status"] == "error"
        assert body["stage"] == ""
        assert body["message"] == "DB 错误信息"

    def test_memory_pct_never_leaks_into_response(self, store, user_id):
        """D（KEY）：内存 pct 与 DB 分歧 → 响应 pct 永远以 DB 为准。"""
        tid = self._seed_text(store, user_id)
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_distill_row(store, task_id, user_id, status="running", pct=60,
                          character="甲", text_id=tid)
        with D._task_lock:
            D._tasks[task_id] = {"status": "merging", "progress_pct": 99,
                                 "message": "合并角色信息 1/5"}
        client = _build_client(store, user_id)

        body = client.get(f"/api/distill/task/{task_id}").json()
        assert body["progress_pct"] == 60          # 内存 99 不泄漏
        assert body["status"] == "running"


class _FailingSaveStore(SQLiteStore):
    """仅 save_distill_task 抛错，其余全走真 store（避免手写全量委托）。"""

    async def save_distill_task(self, *args, **kwargs):
        raise RuntimeError("db insert boom")


class TestCStartRefusesOnDBFailure:
    def test_save_failure_returns_503_and_no_thread(self, tmp_path, user_id, monkeypatch):
        """C：落库失败 → 503 拒绝启动，不建内存条目、不启后台线程。"""
        tid = f"txt_{uuid.uuid4().hex}"
        failing = _FailingSaveStore(_db_path(tmp_path))
        _run_async(failing.save_text(tid, "src.txt", "正文", user_id=user_id))
        # 既有仓库 seam：新 sqlite 库缺 embedding_key 列（ALTER ADD COLUMN IF NOT EXISTS
        # 在 sqlite 不支持，非本步引入的迁移缺陷，见 test_rag_unusable 同款 stub）。
        async def _no_api_cfg(_uid):
            return {}
        failing.get_user_api_config = _no_api_cfg
        client = _build_client(failing, user_id)

        monkeypatch.setattr("deps.get_distiller", lambda llm=None: object())
        started = []
        monkeypatch.setattr("core.telemetry.ctx_thread",
                            lambda *a, **k: started.append(a) or threading.Thread())

        resp = client.post("/api/distill/start",
                           json={"text_id": tid, "character_name": "甲", "force": False})
        assert resp.status_code == 503
        assert "蒸馏任务创建失败" in resp.json()["detail"]
        assert started == []                          # 未启后台线程
        with D._task_lock:
            assert D._tasks == {}                     # 未建内存写缓存条目
