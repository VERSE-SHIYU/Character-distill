# -*- coding: utf-8 -*-
"""缺陷 20（会话文件记作 F）：admin 删用户必须先停该用户的蒸馏线程，再删行。

不断线的后果不是「该清没清」，是会**新增不可达数据**：线程收尾调 save_card 落一张新卡，
而 cards.user_id 没有外键（migrations/001_init.sql 只在 text_id 上建了 FK）——删完用户
之后线程还能给一个已不存在的用户建出孤儿卡，且 delete_user 已经跑过去了、不会有第二次
清理。

本文件的判据是**顺序**，不是「有没有调用」：取证点在 spy 的 delete_user 内部读一眼
`_tasks`。只要 `cancel_distill_tasks_by_user_id` 被删掉或被挪到 delete_user 之后，快照里
那条 running 就不是 error —— 红。这比断言 mock 被调用过更强：调用过但顺序反了，
本仓之前的形态就是「行清了、线程照跑」。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_memory_manager, get_storage
from routers import admin as A
from routers import distill as D
from routers.admin import require_admin


@pytest.fixture(autouse=True)
def _clean_tasks():
    yield
    with D._task_lock:
        D._tasks.clear()


class _SpyStore:
    """delete_user 被调用的**那一刻**快照 _tasks —— 顺序断言的取证点。"""

    def __init__(self):
        self.snapshot: dict[str, str] | None = None
        self.delete_calls: list[str] = []

    async def get_user_by_id(self, user_id):
        return {"id": user_id, "username": user_id, "is_admin": False}

    async def get_user_card_ids(self, user_id):
        return []

    async def delete_user(self, user_id):
        with D._task_lock:
            self.snapshot = {tid: t.get("status") for tid, t in D._tasks.items()}
        self.delete_calls.append(user_id)
        return {"cards": 0, "texts": 0}


@pytest.fixture
def client( ):
    store = _SpyStore()
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[require_admin] = lambda: {"id": "admin1", "is_admin": True}
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_memory_manager] = lambda: None
    with TestClient(app) as c:
        c.store = store
        yield c


def _seed_task(task_id: str, user_id: str, status: str = "running") -> None:
    with D._task_lock:
        D._tasks[task_id] = {
            "status": status, "progress_pct": 30, "user_id": user_id,
            "text_id": f"txt_{uuid.uuid4().hex[:8]}", "character": "张三",
            "message": "分析角色 1/3", "card_id": "", "awakening": "",
        }


class TestSignalBeforeDelete:
    def test_single_delete_signals_own_running_task_first(self, client):
        _seed_task("t_mine_running", "victim")
        _seed_task("t_mine_done", "victim", status="done")
        _seed_task("t_other_running", "bystander")

        r = client.delete("/api/admin/users/victim")
        assert r.status_code == 200, r.text

        # 取证：delete_user 执行那一刻，属于 victim 的活跃任务已经是 error
        assert client.store.snapshot["t_mine_running"] == "error"
        # 终态任务不动（停信号只对活跃任务有意义）
        assert client.store.snapshot["t_mine_done"] == "done"
        # 别人跑得好好的，绝不误伤
        assert client.store.snapshot["t_other_running"] == "running"

    def test_batch_delete_signals_each_running_task_first(self, client):
        _seed_task("t_a", "victim_a")
        _seed_task("t_b", "victim_b")
        _seed_task("t_c", "bystander")

        r = client.post("/api/admin/users/batch-delete", json={"user_ids": ["victim_a", "victim_b"]})
        assert r.status_code == 200, r.text

        # 批量路径同样取证顺序。快照是「最后一个被删用户」那一刻的，两个 victim 都该已停。
        assert client.store.snapshot["t_a"] == "error"
        assert client.store.snapshot["t_b"] == "error"
        assert client.store.snapshot["t_c"] == "running"


class TestHelperDirect:
    """单元面：返回计数与「只动自己的、只动活跃的」两条边界。"""

    @pytest.mark.asyncio
    async def test_counts_and_scopes(self):
        _seed_task("t1", "u1")
        _seed_task("t2", "u1", status="done")
        _seed_task("t3", "u2")

        n = await D.cancel_distill_tasks_by_user_id("u1")
        assert n == 1
        with D._task_lock:
            assert D._tasks["t1"]["status"] == "error"
            assert D._tasks["t1"]["message"] == "账号已删除，任务已取消"
            assert D._tasks["t2"]["status"] == "done"
            assert D._tasks["t3"]["status"] == "running"

    @pytest.mark.asyncio
    async def test_unknown_user_is_noop(self):
        _seed_task("t1", "u1")
        assert await D.cancel_distill_tasks_by_user_id("nobody") == 0
        with D._task_lock:
            assert D._tasks["t1"]["status"] == "running"
