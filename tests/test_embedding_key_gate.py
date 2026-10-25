# -*- coding: utf-8 -*-
"""目标检查：没配百炼 key 的面向用户入口一律 400 + 配置提示。

`docs/specs/embedding-key-gate.md` §1 / §4。每个入口一次请求，判三件事：

1. 状态码 400，detail **整句**等于 `EMBEDDING_KEY_REQUIRED`；
2. 返回前没有副作用：没写会话 / 群 / 任务行，没起后台线程，没调度索引；
3. LLM key 也没配时仍是原来的 503（顺序：先 LLM 503，再百炼 400）。

写法照 `tests/test_session_rag_binding.py::test_x5_reindex_without_embedding_key_spends_no_identify_call`
（只实现走到的方法的内存存储替身 + 路由级 TestClient）。

Run: pytest tests/test_embedding_key_gate.py -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.concurrency as C
import deps
import routers.chat as CH
import routers.distill as D
import routers.group as G
import routers.history as H
from core.embeddings import EMBEDDING_KEY_REQUIRED
from core.schema import CharacterCard
from deps import get_sessions, get_storage
from routers.auth import get_current_user

_LLM_CONFIG = {"api_key": "k", "base_url": "https://api.deepseek.com", "model": "m"}
_EMB_CONFIG = {"embedding_key": "sk-emb", "embedding_region": "cn"}


class _Boom:
    """任何使用都算「门没拦住」—— 门后的一切替身都是这个。"""

    def __getattr__(self, name):
        raise AssertionError(f"没配百炼 key 却继续使用了 {name}")


class _Store:
    """只实现走到的方法；其余属性一律记成「写库」（门失守时会留下痕迹）。"""

    def __init__(self) -> None:
        self.uid = f"usr_{uuid.uuid4().hex[:8]}"
        self.cid = f"card_{uuid.uuid4().hex[:8]}"
        self.tid = f"txt_{uuid.uuid4().hex[:8]}"
        self.sid = f"ses_{uuid.uuid4().hex[:8]}"
        self.gid = f"grp_{uuid.uuid4().hex[:8]}"
        self.card = {
            "card_id": self.cid,
            "card_json": CharacterCard(name="甲").model_dump_json(),
            "text_id": "",
        }
        self.text = {"id": self.tid, "content": "正文", "text_type": "story"}
        self.group = {"id": self.gid, "user_id": self.uid}
        self.config = dict(_LLM_CONFIG)
        self.mutations: list[str] = []

    async def get_user_api_config(self, user_id: str) -> dict:
        return dict(self.config)

    async def get_session_owned(self, session_id: str, user_id: str):
        if session_id != self.sid:
            return None
        return {"id": self.sid, "card_id": self.cid}

    async def get_card_owned(self, card_id: str, user_id: str):
        return self.card if card_id == self.cid else None

    async def get_text_owned(self, text_id: str, user_id: str):
        return self.text if text_id == self.tid else None

    async def list_cards(self, text_id: str, user_id: str):
        return []

    async def get_group_session_owned(self, group_id: str, user_id: str):
        return self.group if group_id == self.gid else None

    async def count_running_distills(self, user_id: str, window_minutes: int = 0) -> int:
        return 0

    def __getattr__(self, name):
        async def _record(*_a, **_kw):
            self.mutations.append(name)
            return None

        return _record


class _Indexing:
    def __init__(self) -> None:
        self.reindexed: list = []
        self.scenes: list = []

    def schedule_text_reindex(self, *a, **kw) -> None:
        self.reindexed.append((a, kw))

    def schedule_scene_index(self, *a, **kw) -> None:
        self.scenes.append((a, kw))


def _make_case(name: str):
    """返回 (store, uid, method, path, body)。每个入口只造它走到的那几行数据。"""
    s = _Store()
    cases = {
        "chat_rebuild": (
            "post", "/api/chat/send",
            {"session_id": s.sid, "message": "在吗"},
        ),
        "history_resume": ("post", f"/api/history/{s.sid}/resume", {}),
        "group_create": (
            "post", "/api/group/create",
            {"card_ids": [s.cid, f"card_{uuid.uuid4().hex[:8]}"],
             "user_persona_type": "director"},
        ),
        "group_rebuild": (
            "post", f"/api/group/{s.gid}/send",
            {"target_card_id": s.cid, "message": "在吗"},
        ),
        "distill_run": (
            "post", "/api/distill/run",
            {"text_id": s.tid, "character_name": "甲"},
        ),
        "distill_start": (
            "post", "/api/distill/start",
            {"text_id": s.tid, "character_name": "甲"},
        ),
        "distill_stream": (
            "post", "/api/distill/run_stream",
            {"text_id": s.tid, "character_name": "甲"},
        ),
        "start_session": ("post", "/api/distill/start_session", {"card_id": s.cid}),
        "legacy_distill": (
            "post", "/api/distill", {"text": "正文", "character_name": "甲"},
        ),
        "reindex": ("post", f"/api/distill/reindex/{s.tid}", None),
    }
    method, path, body = cases[name]
    return s, s.uid, method, path, body


_ENTRIES = [
    "chat_rebuild", "history_resume", "group_create", "group_rebuild",
    "distill_run", "distill_start", "distill_stream", "start_session",
    "legacy_distill", "reindex",
]

# reindex 今天已经是 400（原有的唯一一处拦截），所以它不算「待达成」的 xfail。
_GATE_PARAMS = [
    pytest.param(
        name,
        marks=[] if name == "reindex" else [
            pytest.mark.xfail(strict=True, reason="PR A 未实现：该入口当前静默降级")
        ],
    )
    for name in _ENTRIES
]


def _build(store: _Store):
    app = FastAPI()
    for r in (CH.router, H.router, G.router, D.router, D.legacy_router):
        app.include_router(r)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_sessions] = lambda: {}
    app.dependency_overrides[get_current_user] = lambda: {"id": store.uid, "role": "user"}
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    """LLM 侧替身：配了 key 的用户得到一个不是 None 的实例；门后的一切一碰就炸。"""
    async def _llm(*_a, **_kw):
        return object()

    monkeypatch.setattr(deps, "get_user_llm", _llm)
    monkeypatch.setattr(G, "get_user_llm", _llm)
    monkeypatch.setattr(deps, "get_distiller", lambda **_kw: _Boom())
    monkeypatch.setattr(deps, "get_text_manager", lambda **_kw: _Boom())
    monkeypatch.setattr(deps, "get_user_memory", lambda *_a, **_kw: _Boom())
    monkeypatch.setattr(G, "get_group_sessions", dict)
    with D._task_lock:
        D._tasks.clear()
        D._user_slots.clear()
    yield
    with D._task_lock:
        D._tasks.clear()
        D._user_slots.clear()


def _post(name: str, monkeypatch):
    store, _uid, method, path, body = _make_case(name)
    indexing = _Indexing()
    threads: list = []
    identified: list = []

    async def _identify(*_a, **_kw):
        identified.append(1)
        return []

    def _thread(*a, **kw):
        threads.append((a, kw))
        raise AssertionError("没配百炼 key 却起了后台线程")

    monkeypatch.setattr(D, "get_indexing_service", lambda: indexing)
    monkeypatch.setattr(D, "resolve_characters", _identify)
    monkeypatch.setattr(C, "ctx_thread", _thread)
    client = _build(store)
    resp = getattr(client, method)(path, json=body)
    return resp, store, indexing, threads, identified


@pytest.mark.parametrize("name", _GATE_PARAMS)
def test_gate(name, monkeypatch):
    resp, store, indexing, threads, identified = _post(name, monkeypatch)

    assert resp.status_code == 400, resp.text
    assert resp.json() == {"detail": EMBEDDING_KEY_REQUIRED}, (
        f"{name}: 没配 key 时只该给配置提示：{resp.json()}")
    assert store.mutations == [], f"{name}: 没配 key 却写了库：{store.mutations}"
    assert threads == [], f"{name}: 没配 key 却起了后台线程"
    assert indexing.reindexed == [] and indexing.scenes == [], (
        f"{name}: 没配 key 却调度了索引")
    assert identified == [], f"{name}: 没配 key 却跑了角色识别"


@pytest.mark.parametrize("name", _ENTRIES)
def test_llm_503_precedes_embedding_400(name, monkeypatch):
    """两把 key 都没配：仍是原来的 503 —— 顺序不能倒。"""
    async def _none(*_a, **_kw):
        return None

    monkeypatch.setattr(deps, "get_user_llm", _none)
    monkeypatch.setattr(G, "get_user_llm", _none)
    # 生产里 LLM 解析不到时，这两个工厂同样给 None —— 把 503 门照实复现出来。
    monkeypatch.setattr(deps, "get_distiller", lambda llm=None, **_kw: None)
    monkeypatch.setattr(deps, "get_text_manager", lambda llm=None, **_kw: None)
    store, _uid, method, path, body = _make_case(name)
    client = _build(store)
    resp = getattr(client, method)(path, json=body)
    assert resp.status_code == 503, f"{name}: 先 LLM 503 再百炼 400：{resp.status_code} {resp.text}"


@pytest.mark.xfail(strict=True, reason="PR A 未实现：reindex 把「服务未装配」也报成 400")
def test_reindex_service_unassembled_is_503(monkeypatch):
    """服务没装配不是用户没配置 —— 两者原先混在同一个 400 里。"""
    store, _uid, _m, path, _body = _make_case("reindex")
    store.config = dict(_EMB_CONFIG)
    client = _build(store)
    resp = client.post(path)
    assert resp.status_code == 503, resp.text
