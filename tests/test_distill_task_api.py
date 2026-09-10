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
from httpx import ASGITransport, AsyncClient

from core import telemetry as T
from core.distiller import text_fingerprint
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
    """每条用例后清空模块级内存表（任务缓存 + 按用户槽位），避免跨用例污染。"""
    yield
    with D._task_lock:
        D._tasks.clear()
        D._user_slots.clear()


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


class TestA2Wiring:
    """P0 兜底接线：_run_distill_task 里两处 _confirm_terminal_persist 真的被调到。

    TestA2TerminalConfirm 是直接调 D._confirm_terminal_persist()，没覆盖过生产调用点；
    变异验证（注释掉这两行之一，本类对应用例应变红）：
    - acquire 失败分支那行（return 前）→ test_acquire_timeout_calls_confirm 红
    - finally 里那行 → test_finally_confirms_before_release 红
    任务不 seed 进内存：_set_task 对不存在的条目 no-op，事件序只反映接线。
    """

    def _test_events(self, monkeypatch, acquired, distiller_boom):
        """搭好假信号量 + confirm spy + 可选 get_distiller 抛错，跑完整 _run_distill_task。

        distiller_boom=True 时需 _install 假 store —— except 分支的 cleanup 协程
        会打到真 get_storage 否则碰真库。返回事件序。
        """
        events: list[str] = []

        class _FakeSem:
            def acquire(self, timeout=0):
                events.append("acquire")
                return acquired

            def release(self):
                events.append("release")

        monkeypatch.setattr(D, "_DISTILL_SEMAPHORE", _FakeSem())
        monkeypatch.setattr(
            D, "_confirm_terminal_persist",
            lambda tid: events.append(f"confirm:{tid}"),
        )
        if distiller_boom:
            _install(monkeypatch, _FakeStore())
            monkeypatch.setattr(
                "deps.get_distiller", lambda llm=None: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        return events

    def test_acquire_timeout_calls_confirm(self, monkeypatch):
        """acquire(timeout=300) 超时拿不到 → confirm 兜底终态后 return，不放行、不 release。"""
        events = self._test_events(monkeypatch, acquired=False, distiller_boom=False)
        D._run_distill_task("tW1", "txt_x", "甲", False, "usr_x", "正文", "story")
        assert events == ["acquire", "confirm:tW1"]   # 无 release

    def test_finally_confirms_before_release(self, monkeypatch):
        """异常路径进 finally：confirm 必须发生在 release 之前。"""
        events = self._test_events(monkeypatch, acquired=True, distiller_boom=True)
        D._run_distill_task("tW2", "txt_x", "甲", False, "usr_x", "正文", "story")
        assert events == ["acquire", "confirm:tW2", "release"]


# ── 路由测试（B / C / D）：独立 app + 真 SQLiteStore ────────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(_db_path(tmp_path))


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex[:8]}"


def _build_app(store, uid):
    app = FastAPI()
    app.include_router(D.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "testuser", "is_admin": False,
    }
    return app


def _build_client(store, uid):
    return TestClient(_build_app(store, uid))


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

        class _StubDistiller:
            def effective_chunk_size(self, text_type="story"):
                return 3000

        monkeypatch.setattr("deps.get_distiller", lambda llm=None: _StubDistiller())
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


# ── 路由测试（E）：/start 任务级续跑门（断言 5 / 6）─────────────────────────
# 分片级三重门（指纹/非空/形状）在 tests/test_distill_resume.py 用确定性 mock 覆盖；
# 这里只管 router 的任务级门：切分参数 / 全文指纹任一不符 → 整批候选作废。

class _ResumeDistillerStub:
    """只提供 /start 用到的切分口径；不起真线程、不碰 LLM。"""

    def __init__(self, chunk_size: int = 3000):
        self.chunk_size = chunk_size

    def effective_chunk_size(self, text_type="story"):
        return self.chunk_size


def _seed_interrupted(store, user_id, tid, *, task_id, chunk_size, fp, chunks=()):
    """落一行 interrupted 任务（带 checkpoint 参数）+ 可选分片行。"""
    _run_async(store.save_distill_task(
        task_id, user_id, tid, "甲", status="interrupted", progress_pct=40,
        message="进程重启，任务中断", card_id="", awakening="",
        chunk_size=chunk_size, text_fingerprint=fp,
    ))
    for idx, result, c_fp in chunks:
        _run_async(store.save_distill_chunk(task_id, idx, result, fingerprint=c_fp))


def _capture_start(monkeypatch, store, user_id, tid, *, character="甲", force=False,
                   chunk_size=3000):
    """POST /start，返回 (resp, 交给后台线程的 resume_candidates)。

    ctx_thread 打桩：不真起线程，只捕获 args 元组（末位即 resume_candidates）。
    """
    async def _no_api_cfg(_uid):
        return {}
    store.get_user_api_config = _no_api_cfg
    monkeypatch.setattr("deps.get_distiller",
                        lambda llm=None: _ResumeDistillerStub(chunk_size))
    captured: list[tuple] = []
    monkeypatch.setattr("core.telemetry.ctx_thread",
                        lambda *a, **k: captured.append(k["args"]) or threading.Thread())

    client = _build_client(store, user_id)
    resp = client.post("/api/distill/start",
                       json={"text_id": tid, "character_name": character, "force": force})
    return resp, (captured[0][-1] if captured else "NO_THREAD")


class TestEResumeGate:
    def _text(self, store, user_id, body="新正文内容"):
        tid = f"txt_{uuid.uuid4().hex}"
        _run_async(store.save_text(tid, "src.txt", body, user_id=user_id))
        return tid

    def test_chunk_size_change_rejects_whole_batch(self, store, user_id, monkeypatch, capsys):
        """门 A：切分参数变 → 复用行整批作废、候选 None、日志点明原因、重新盖章。"""
        body = "角色说的话" * 20
        tid = self._text(store, user_id, body)
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_interrupted(store, user_id, tid, task_id=task_id,
                          chunk_size=9999, fp=text_fingerprint(body),
                          chunks=[(0, "分析A", "cfp0")])

        resp, cands = _capture_start(monkeypatch, store, user_id, tid, chunk_size=3000)

        assert resp.status_code == 200
        assert resp.json()["task_id"] == task_id      # 仍复用原 id（不是新铸）
        assert cands is None
        assert "切分参数变更" in capsys.readouterr().out
        assert _run_async(store.get_distill_task(task_id))["chunk_size"] == 3000

    def test_text_change_rejects_even_if_chunk_fp_wellformed(self, store, user_id,
                                                            monkeypatch, capsys):
        """门 B：原文变 → 整批重跑。

        行里挂一片「指纹格式完全合法（真 sha256）」的分片，证明分片级门根本没机会跑：
        任务级门先于分片门，整批已作废 —— 部分分片指纹再像也救不回来。
        """
        body = "新正文内容"
        tid = self._text(store, user_id, body)
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_interrupted(store, user_id, tid, task_id=task_id,
                          chunk_size=3000, fp=text_fingerprint("旧的正文字符串"),
                          chunks=[(0, "旧分析", text_fingerprint("旧的某个分片"))])

        resp, cands = _capture_start(monkeypatch, store, user_id, tid, chunk_size=3000)

        assert resp.status_code == 200
        assert resp.json()["task_id"] == task_id
        assert cands is None
        assert "原文变更" in capsys.readouterr().out
        assert _run_async(store.get_distill_task(task_id))["text_fingerprint"] == text_fingerprint(body)

    def test_matching_checkpoint_loads_candidates(self, store, user_id, monkeypatch):
        """正向对照：任务级门全过 → 分片行真的被读成候选（防上面两条门测试空过）。"""
        body = "新正文内容"
        tid = self._text(store, user_id, body)
        task_id = f"dt_{uuid.uuid4().hex}"
        _seed_interrupted(store, user_id, tid, task_id=task_id,
                          chunk_size=3000, fp=text_fingerprint(body),
                          chunks=[(0, "分析A", "cfp0"), (3, "分析B", "cfp3")])

        resp, cands = _capture_start(monkeypatch, store, user_id, tid, chunk_size=3000)

        assert resp.status_code == 200
        assert resp.json()["task_id"] == task_id
        assert cands == {0: {"result": "分析A", "fingerprint": "cfp0"},
                         3: {"result": "分析B", "fingerprint": "cfp3"}}

    def test_force_starts_fresh_ignoring_interrupted(self, store, user_id, monkeypatch):
        """force=True 是「重新蒸馏」：不发现遗留行 → 铸新 task_id、无候选。"""
        body = "新正文内容"
        tid = self._text(store, user_id, body)
        old_id = f"dt_{uuid.uuid4().hex}"
        _seed_interrupted(store, user_id, tid, task_id=old_id,
                          chunk_size=3000, fp=text_fingerprint(body),
                          chunks=[(0, "分析A", "cfp0")])

        resp, cands = _capture_start(monkeypatch, store, user_id, tid, force=True, chunk_size=3000)

        assert resp.status_code == 200
        assert resp.json()["task_id"] != old_id
        assert cands is None


# ── 路由测试（F）：按用户并发闸（步骤 4）────────────────────────────────────
# 两道门：同步预留位（进程内，挡并发 TOCTOU）+ DB 复核（带时效窗，挡跨重启残留）。
# 全局闸 _DISTILL_SEMAPHORE 语义不变，仍由 bg 线程 acquire(timeout=300)。


class _ChunkEmittingDistiller:
    """吐 N 片 map 回调后收尾 —— 让真后台线程跑出一条完整的落盘路径。

    故意不产出合法 JSON：格式解析失败会走 _set_task(error) 正常收口，而分片落库
    发生在那之前，所以分片计数与终止态无关，断言更稳。
    """

    N = 4

    def __init__(self):
        self.map_calls = 0

    def effective_chunk_size(self, text_type="story"):
        return 3000

    def identify_characters(self, content):
        return [{"name": "甲", "aliases": []}]

    def distill_incremental_stream(self, text, character_name, aliases=None,
                                   text_type="story", on_chunk_done=None,
                                   resume_candidates=None):
        for i in range(self.N):
            self.map_calls += 1
            if on_chunk_done:
                on_chunk_done(i, f"分析{i}", f"fp{i}")
            yield {"status": "analyzing", "current": i + 1, "total": self.N}


class _CapOnlySemaphore:
    """只数 acquire 次数、release 是 no-op —— 让第 cap+1 次 acquire 必然落空。

    这样「第 4 个用户撞上全局闸」可确定复现，不必真等 acquire(timeout=300) 的 5 分钟。
    """

    def __init__(self, cap):
        self.cap = cap
        self.acquires = 0

    def acquire(self, timeout=0):
        self.acquires += 1
        return self.acquires <= self.cap

    def release(self):
        pass


def _install_bg(monkeypatch, store, cap=3):
    """把 /start 的真后台线程装进测试：stub 掉 store/LLM 边界。

    返回 (蒸馏器, 信号量, 线程表)。run_on_main_loop 换成同步 asyncio.run —— bg 线程里
    没有事件循环，落库路径照跑。
    """
    async def _no_api_cfg(_uid):
        return {}
    store.get_user_api_config = _no_api_cfg

    distiller = _ChunkEmittingDistiller()
    monkeypatch.setattr("deps.get_distiller", lambda llm=None: distiller)
    monkeypatch.setattr("deps.get_text_manager", lambda llm=None: object())
    monkeypatch.setattr(D, "get_storage", lambda: store)
    monkeypatch.setattr(D, "run_on_main_loop", lambda coro, timeout=10: asyncio.run(coro))
    sem = _CapOnlySemaphore(cap)
    monkeypatch.setattr(D, "_DISTILL_SEMAPHORE", sem)

    threads = []
    real_ctx_thread = T.ctx_thread

    def _spy(target, args=(), **kw):
        t = real_ctx_thread(target, args=args, **kw)
        threads.append(t)
        return t

    monkeypatch.setattr("core.telemetry.ctx_thread", _spy)
    return distiller, sem, threads


def _age_row(store, task_id, minutes):
    """把一行的 updated_at 推到 N 分钟前（造幽灵行）。"""
    async def _go():
        async with await store._connect() as conn:
            await conn.execute(
                "UPDATE distill_tasks SET updated_at = datetime('now', ?) WHERE task_id = ?",
                (f"-{minutes} minutes", task_id),
            )
            await conn.commit()
    _run_async(_go())


class TestFPerUserGate:
    def _text(self, store, uid, body="角色说的话"):
        tid = f"txt_{uuid.uuid4().hex}"
        _run_async(store.save_text(tid, "src.txt", body, user_id=uid))
        return tid

    def test_second_serial_start_rejected(self, store, user_id, monkeypatch):
        """同一用户串行第二次 → 429 + 明确提示。"""
        tid = self._text(store, user_id)
        first, _ = _capture_start(monkeypatch, store, user_id, tid)
        assert first.status_code == 200

        second, _ = _capture_start(monkeypatch, store, user_id, tid)
        assert second.status_code == 429
        assert "已有蒸馏任务在运行" in second.json()["detail"]

    def test_different_users_do_not_interfere(self, store, monkeypatch):
        """不同用户各开 1 个 → 都 200（按用户闸不串台）。"""
        for uid in ("usr_a", "usr_b", "usr_c"):
            tid = self._text(store, uid)
            resp, _ = _capture_start(monkeypatch, store, uid, tid)
            assert resp.status_code == 200, resp.text

    def test_early_error_releases_slot(self, store, user_id, monkeypatch):
        """占坑之后早退（404 文本不存在）必须释放，否则该用户被自己永久挡住。"""
        bad, _ = _capture_start(monkeypatch, store, user_id, "txt_does_not_exist")
        assert bad.status_code == 404

        tid = self._text(store, user_id)
        ok, _ = _capture_start(monkeypatch, store, user_id, tid)
        assert ok.status_code == 200, ok.text

    def test_db_gate_blocks_live_row_but_not_ghost(self, store, user_id, monkeypatch):
        """第二道门：新鲜 running 行挡；推老到时效窗外的幽灵行不挡。"""
        tid = self._text(store, user_id)
        _seed_distill_row(store, "dtLive", user_id, status="running", text_id=tid)

        blocked, _ = _capture_start(monkeypatch, store, user_id, tid)
        assert blocked.status_code == 429
        assert "已有蒸馏任务在运行" in blocked.json()["detail"]

        _age_row(store, "dtLive", D.DISTILL_GHOST_IDLE_MIN + 90)
        allowed, _ = _capture_start(monkeypatch, store, user_id, tid)
        assert allowed.status_code == 200, allowed.text

    async def test_concurrent_double_start_same_target(self, store, user_id, monkeypatch):
        """并发双 /start 打同一 (text, character)：只起一条线程、分片只落一份。

        两个请求在同一 event loop 上用 asyncio.gather 真并发 —— 没有同步预留位时，
        它们会在 count / insert 的 await 点交错、双双通过。
        """
        tid = f"txt_{uuid.uuid4().hex}"
        await store.save_text(tid, "src.txt", "角色说的话" * 20, user_id=user_id)
        distiller, _sem, threads = _install_bg(monkeypatch, store)

        transport = ASGITransport(app=_build_app(store, user_id))
        payload = {"text_id": tid, "character_name": "甲", "force": False}
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1, r2 = await asyncio.gather(
                ac.post("/api/distill/start", json=payload),
                ac.post("/api/distill/start", json=payload),
            )
        for t in threads:
            t.join(timeout=30)

        assert sorted([r1.status_code, r2.status_code]) == [200, 429], (r1.text, r2.text)
        assert len(threads) == 1, "只该起一条后台线程"
        assert distiller.map_calls == _ChunkEmittingDistiller.N, "分片只该跑一遍"
        winner = r1 if r1.status_code == 200 else r2
        chunks = await store.get_distill_chunks(winner.json()["task_id"])
        assert len(chunks) == _ChunkEmittingDistiller.N, "分片只该落一份"

    def test_global_gate_still_caps_at_max_concurrent(self, store, monkeypatch):
        """全局闸仍生效：3 个不同用户各 1 个 → 第 4 个撞上全局闸（acquire 落空）。

        注：这里用假信号量断言「第 4 个确实 acquire 不到」，**没有真等** timeout=300 的
        5 分钟排队 —— 排队本身未被本用例验证。
        """
        _distiller, sem, threads = _install_bg(monkeypatch, store, cap=3)

        rows = []
        for i in range(4):
            uid = f"usr_global_{i}"
            tid = self._text(store, uid)
            resp = _build_client(store, uid).post(
                "/api/distill/start",
                json={"text_id": tid, "character_name": "甲", "force": False},
            )
            assert resp.status_code == 200, resp.text
            threads[-1].join(timeout=30)
            rows.append(_run_async(store.get_distill_task(resp.json()["task_id"])))

        assert sem.acquires == 4
        for i in range(3):
            assert "服务器繁忙" not in (rows[i]["message"] or ""), f"第 {i + 1} 个不该撞闸"
        assert "服务器繁忙" in (rows[3]["message"] or ""), "第 4 个该撞全局闸"
