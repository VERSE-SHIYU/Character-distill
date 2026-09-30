"""模型调用只用用户自己的 key —— `docs/specs/user-own-keys.md`。

走生产 app（真中间件 + 真统一出口 + 真解析出口 `get_user_llm`），只把 storage 换成用例
自己那份（与 `tests/test_router_unified_exits.py` 的 client 夹具同源）。每条用例的前提
都是「用户没配 key，而全局 key **可用**」：全局不可用时 503 本来就成立，证不了「不回落」。
全局实例装成毒实例，被碰即炸。

Run: pytest tests/test_user_own_keys.py -v
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

import deps
import server
from core.schema import CharacterCard
from routers.auth import _create_access_token, get_jwt_secret
from storage.sqlite_store import SQLiteStore

_PW_HASH = PasswordHash.recommended().hash("Pass1234")
_NO_KEY = "请先在设置页配置 API Key"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _PoisonLLM:
    """全局实例的替身：任何属性访问都炸 —— 回落到它的那一刻用例就红，且红在成因上。"""

    def __getattr__(self, name):
        raise AssertionError(f"碰了全局 LLM 实例（.{name}）—— 面向用户的调用回落到了全局 key")


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "own_keys.db"))


@pytest.fixture
def user(store):
    """没配任何 key 的真用户行。"""
    uid = f"usr_{uuid.uuid4().hex[:12]}"
    _run(store.create_user(uid, "U_" + uuid.uuid4().hex[:8], _PW_HASH))
    deps.clear_user_llm_cache(uid)
    return _run(store.get_user_by_id(uid))


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(deps, "_storage", store)
    poison = _PoisonLLM()
    monkeypatch.setattr(deps, "get_llm", lambda: poison)
    return TestClient(server.app, raise_server_exceptions=False)


def _token(user: dict) -> dict:
    return {"Authorization": f"Bearer {_create_access_token(user['id'], user['username'], get_jwt_secret())}"}


def _assert_premise(store, user) -> None:
    assert not _run(store.get_user_api_config(user["id"])).get("api_key"), \
        "前提破了：这个用户居然有 key"
    assert isinstance(deps.get_llm(), _PoisonLLM), "前提破了：全局实例不可用，503 证不了「不回落」"


def _seed_public_pair(store, owner_id: str) -> tuple[str, str]:
    """同一本『书』下两张卡：`src`（被评论的）+ `at`（public、被 @ 的）。返回 (src, at)。"""
    text_id = f"txt_{uuid.uuid4().hex[:12]}"
    src_id, at_id = f"card_{uuid.uuid4().hex[:12]}", f"card_{uuid.uuid4().hex[:12]}"
    card_json = CharacterCard(name="甲", identity="测试用角色").model_dump_json()
    _run(store.save_text(text_id, "src.txt", "正文", user_id=owner_id))
    _run(store.save_card(src_id, text_id, "乙", card_json, owner_id))
    _run(store.save_card(at_id, text_id, "甲", card_json, owner_id))
    assert _run(store.update_card_visibility(at_id, "public")), "种子前提破了：at 卡没能置为 public"
    return src_id, at_id


# ── T2：at_reply 没配 key → 503，不是 AttributeError 炸成的 500 ──────────────

def test_T2_at_reply_without_own_key_is_503(client, store, user):
    _assert_premise(store, user)
    src_id, at_id = _seed_public_pair(store, user["id"])

    resp = client.post(
        f"/api/market/{src_id}/comments/at-reply",
        json={"at_card_id": at_id, "comment_content": "在吗"},
        headers=_token(user),
    )

    assert resp.status_code == 503, f"{resp.status_code} {resp.text}"
    assert resp.json()["detail"] == _NO_KEY


# ── T3：群聊会话被逐出内存后，属主没配 key 重建 → 503，不是 404「会话已过期」 ─────

def test_T3_group_rebuild_without_own_key_is_503_not_404(client, store, user):
    _assert_premise(store, user)
    card_json = CharacterCard(name="甲", identity="测试用角色").model_dump_json()
    text_id = f"txt_{uuid.uuid4().hex[:12]}"
    card_id = f"card_{uuid.uuid4().hex[:12]}"
    _run(store.save_text(text_id, "src.txt", "正文", user_id=user["id"]))
    _run(store.save_card(card_id, text_id, "甲", card_json, user["id"]))
    gid = f"grp_{uuid.uuid4().hex}"
    _run(store.create_group_session(gid, "群聊A", [card_id], user_id=user["id"]))
    assert gid not in deps.get_group_sessions(), "前提破了：会话在内存里，走不到重建"

    resp = client.post(
        f"/api/group/{gid}/send",
        json={"target_card_id": card_id, "message": "在吗"},
        headers=_token(user),
    )

    assert resp.status_code == 503, f"{resp.status_code} {resp.text}"
    assert resp.json()["detail"] == _NO_KEY


# ── T4：没有 embedding key → 不建 RAG、不调度场景索引、不拿空 key 去试 ──────────

class _NoRagEngine:
    """被构造就记一笔再抛错。判据是**记录**（`built`），不是异常 ——
    `get_rag_for_session` 自带宽 `except Exception` 会把异常吞成 None，只看返回值的话，
    「拿空 key 去试、失败后降级」与「根本没去试」长得一模一样。"""

    built: list = []

    def __init__(self, *_a, **_kw):
        type(self).built.append(_kw or _a)
        raise RuntimeError("没有 embedding key 却构造了 RAGEngine（会拿空 key 去调百炼）")


@pytest.fixture
def no_rag_engine(monkeypatch):
    import core.indexing_service as IS
    import core.rag as core_rag  # group.py 在函数内 `from core.rag import RAGEngine`，打源头

    _NoRagEngine.built = []
    monkeypatch.setattr(IS, "RAGEngine", _NoRagEngine)
    monkeypatch.setattr(core_rag, "RAGEngine", _NoRagEngine)
    return _NoRagEngine


def test_T4a_rag_for_session_without_embedding_key_is_none(no_rag_engine):
    import core.indexing_service as IS

    svc = IS.IndexingService(storage=None, rag_config={"top_k": 3})
    assert svc.get_rag_for_session("txt_x", "正文", embedding_key="", embedding_region="cn") is None
    assert no_rag_engine.built == [], "没有 embedding key 却去构造了 RAGEngine"


def test_T4b_scene_index_without_embedding_key_is_not_scheduled(monkeypatch):
    import core.indexing_service as IS

    scheduled: list = []
    monkeypatch.setattr(IS.asyncio, "create_task", lambda coro: scheduled.append(coro))
    svc = IS.IndexingService(storage=None, rag_config={"top_k": 3})
    svc.schedule_scene_index("txt_x", "card_x", "正文", "甲", embedding_key="", embedding_region="cn")
    assert scheduled == [], "没有 embedding key 却调度了场景索引"
    assert "scenes_card_x" not in IS._scene_index_in_flight, "去重表里留下了一条没调度的任务"


def test_T4c_group_without_embedding_key_builds_no_rag(client, store, user, monkeypatch, no_rag_engine):
    """用户配了 LLM key、没配百炼 key：群聊照建，但每个引擎都不检索，也不构造 RAGEngine。"""
    monkeypatch.setattr(deps, "get_memory_manager", lambda: None)
    _run(store.update_user_api_config(user["id"], "user-llm-key", "https://api.deepseek.com", "m"))
    deps.clear_user_llm_cache(user["id"])

    text_id = f"txt_{uuid.uuid4().hex[:12]}"
    card_ids = []
    _run(store.save_text(text_id, "src.txt", "正文", user_id=user["id"]))
    for name in ("甲", "乙"):
        cid = f"card_{uuid.uuid4().hex[:12]}"
        _run(store.save_card(cid, text_id, name, CharacterCard(name=name).model_dump_json(), user["id"]))
        card_ids.append(cid)

    resp = client.post("/api/group/create", json={"card_ids": card_ids}, headers=_token(user))
    assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
    gid = resp.json()["group_id"]
    try:
        engines = deps.get_group_sessions()[gid].engines
        assert engines and all(e.rag is None for e in engines.values())
        assert no_rag_engine.built == [], "没有 embedding key 却去构造了 RAGEngine"
    finally:
        deps.get_group_sessions().pop(gid, None)
