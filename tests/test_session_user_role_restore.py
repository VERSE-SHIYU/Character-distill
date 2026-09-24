# -*- coding: utf-8 -*-
"""缺陷 57：重建会话时把 `user_role` 从库里恢复回来（两条独立的重建路径，各一条锁）。

`web/routers/chat.py::_ensure_session`（发消息时后端发现内存里没有这个会话 → 懒重建）与
`web/routers/history.py::resume_session`（用户点重连 → 重建）各有一处
`if db_session.get("user_role"): engine.user_role = db_session["user_role"]`。

两处不是「可以合并的重复代码」：触发的入口不同（一个是发消息顺带、一个是显式重连），谁先
跑取决于客户端行为，只锁一条等于只锁了一半。故本文件一个路径一条用例，互不代偿。

这两句同时是缺陷 55（凭据落进了 `user_role`）射程的边界：进程内的旧会话一旦被驱逐，脏值就
只能经这里重新流回引擎、再随 prompt 进模型。所以本文件断言的是「重建后引擎的角色 == 库里
那一份」—— 库里是什么就恢复什么，恢复这条路本身是通的。

变异：删掉任一处兜底 → 对应那条用例红（引擎角色停在初值 `""`，不是库里的值）。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.text_manager import new_session_entry
from deps import get_sessions, get_storage
from routers.auth import get_current_user
from routers.chat import _ensure_session
from routers.history import router as history_router
from storage.sqlite_store import SQLiteStore

_ROLE = "骑士"


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"role_{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_id():
    return f"user_{uuid.uuid4().hex[:8]}"


class _StubEngine:
    """够两条重建路径走完的最小引擎替身。

    `user_role` 从空串起步 —— 于是「断言它变成了库里的值」才是在测那处兜底，
    而不是在测引擎自己的初值。
    """

    def __init__(self):
        self.history: list[dict] = []
        self.last_summary = ""
        self.user_role = ""
        self._session_id = ""

    def load_affinity(self, data, initialized=False):
        pass

    def generate_reunion_greeting(self, *_a, **_kw):
        return ""


def _seed(store, user_id, role=_ROLE) -> str:
    """建用户 + 卡 + 文本 + **带角色的**会话，返回 sid。"""
    from pwdlib import PasswordHash

    _run_async(store.create_user(
        user_id, f"u_{user_id[-6:]}", PasswordHash.recommended().hash("Pass1234")))

    tid = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", "content", user_id=user_id))
    card_id = f"card_{uuid.uuid4().hex}"
    _run_async(store.save_card(
        card_id, tid, "张三", '{"name": "张三"}', user_id=user_id))

    sid = f"ses_{uuid.uuid4().hex}"
    _run_async(store.save_session(sid, card_id, role, "", user_id=user_id))
    return sid


def _stub_text_manager(sessions, engine) -> object:
    """`_create_session` 把替身引擎塞进 `sessions` 的**原 id** 名下。

    替身按 `session_id` 入参登记（而不是自己编一个 id 再等路由搬过来）—— 路由那边
    「先造后改名」已经删了，替身若还照老样子登记，就会把真路径藏起来：用例照绿，
    可它验的是替身自己的行为。
    """

    class _StubIndexing:
        def get_rag_for_session(self, *_a, **_kw):
            return None

    class _StubTextManager:
        _indexing_service = _StubIndexing()

        async def _build_all_characters(self, *_a, **_kw):
            return [{"name": "张三", "aliases": []}]

        def _create_session(self, *_a, **kw):
            sid = kw["session_id"]
            sessions[sid] = new_session_entry(engine, None, "")
            return sid

    return _StubTextManager()


async def _fake_user_llm(*_a, **_kw):
    return object()


# ── 路径 A：POST /resume（用户点重连）────────────────────────────────────────


@pytest.fixture
def sessions():
    return {}


@pytest.fixture
def client(store, user_id, sessions):
    app = FastAPI()
    app.include_router(history_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_sessions] = lambda: sessions
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "username": "testuser", "role": "user",
    }
    return TestClient(app)


class TestResumeRestoresUserRole:
    def test_resume_restores_the_role_from_the_db(
        self, client, store, user_id, sessions, monkeypatch,
    ):
        sid = _seed(store, user_id)
        engine = _StubEngine()

        import deps
        monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
        monkeypatch.setattr(
            deps, "get_text_manager",
            lambda *_a, **_kw: _stub_text_manager(sessions, engine))

        resp = client.post(f"/api/history/{sid}/resume", json={})
        assert resp.status_code == 200, resp.text
        assert engine.user_role == _ROLE, (
            "resume 重建后引擎的角色不是库里的那一份 —— 兜底没走")


# ── 路径 B：_ensure_session（发消息时懒重建）────────────────────────────────


class TestEnsureSessionRestoresUserRole:
    def test_lazy_rebuild_restores_the_role_from_the_db(
        self, store, user_id, monkeypatch,
    ):
        sid = _seed(store, user_id)
        sessions: dict = {}
        engine = _StubEngine()

        import deps
        monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
        monkeypatch.setattr(
            deps, "get_text_manager",
            lambda *_a, **_kw: _stub_text_manager(sessions, engine))

        session = _run_async(_ensure_session(sid, store, sessions, user_id))
        assert session["engine"] is engine
        assert engine.user_role == _ROLE, (
            "_ensure_session 重建后引擎的角色不是库里的那一份 —— 兜底没走")


# ── 边界：库里的角色为空时不写引擎 ──────────────────────────────────────────


class TestEmptyRoleStaysEmpty:
    def test_blank_role_in_db_does_not_clobber_the_engine(
        self, store, user_id, monkeypatch,
    ):
        """兜底是 `if db_session.get("user_role")`：空角色不该把引擎的既有值抹成空串。"""
        sid = _seed(store, user_id, role="")
        sessions: dict = {}
        engine = _StubEngine()
        engine.user_role = "保留我"

        import deps
        monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
        monkeypatch.setattr(
            deps, "get_text_manager",
            lambda *_a, **_kw: _stub_text_manager(sessions, engine))

        _run_async(_ensure_session(sid, store, sessions, user_id))
        assert engine.user_role == "保留我"
