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

    svc = IS.IndexingService({"top_k": 3})
    assert svc.get_rag_for_session(
        "txt_x", card_id="card_x", embedding_key="", embedding_region="cn") is None
    assert no_rag_engine.built == [], "没有 embedding key 却去构造了 RAGEngine"


def test_T4b_scene_index_without_embedding_key_is_not_scheduled(monkeypatch):
    import core.indexing_service as IS

    scheduled: list = []
    monkeypatch.setattr(IS.asyncio, "create_task", lambda coro: scheduled.append(coro))
    svc = IS.IndexingService({"top_k": 3})
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


# ── T5–T7 / T10：长期记忆的工厂 —— 只用用户自己的两把 key ─────────────────────

class _FakeMemory:
    """`Memory.from_config` 的替身：记下收到的配置，提供不调模型的查看 / 删除。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.deleted: list[str] = []

    def get_all(self, filters=None):
        return {"results": []}

    def delete_all(self, user_id=None):
        self.deleted.append(user_id)


@pytest.fixture
def mem_factory(monkeypatch, tmp_path):
    """真 `MemoryManager`（本体逻辑），只把 `Memory.from_config` 换成记账的替身。"""
    import mem0
    from core.memory_manager import MemoryManager

    built: list[_FakeMemory] = []

    def _from_config(cfg):
        m = _FakeMemory(cfg)
        built.append(m)
        return m

    monkeypatch.setattr(mem0.Memory, "from_config", _from_config)
    mm = MemoryManager({}, db_dir=tmp_path / "mem0_db")
    mm.built = built
    return mm


def _user_llm(key: str, model: str = "m"):
    from adapters.llm_adapter import LLMAdapter

    return LLMAdapter(api_key=key, base_url="https://api.deepseek.com", model=model, is_user_key=True)


def _embed_key_of(view) -> str:
    return view._mem.embedding_model._dashscope._client.api_key


def test_T5_view_uses_the_users_own_keys_and_shared_client(mem_factory):
    llm = _user_llm("user-llm-A")
    view = mem_factory.for_user(user_id="A", llm=llm, embedding_key="user-emb-A")

    assert view is not None
    assert _embed_key_of(view) == "user-emb-A", "向量化没用用户自己的百炼 key"
    extractor = view._mem.llm._adapter
    assert extractor.credential_fingerprint() == llm.credential_fingerprint(), "提炼没用用户自己的 LLM key"
    cfg = view._mem.cfg
    assert cfg["vector_store"]["config"]["client"] is mem_factory._shared_client(), \
        "没经官方 `client` 参数共用同一个 qdrant 客户端"
    # T10：历史库必须落在数据目录里（mem0 默认在 ~/.mem0，容器重建即丢）
    assert cfg["history_db_path"].startswith(str(mem_factory._db_dir)), cfg["history_db_path"]


def test_T6_views_are_per_user_and_follow_key_changes(mem_factory):
    va = mem_factory.for_user(user_id="A", llm=_user_llm("kA"), embedding_key="eA")
    vb = mem_factory.for_user(user_id="B", llm=_user_llm("kB"), embedding_key="eB")
    assert va is not vb and _embed_key_of(va) == "eA" and _embed_key_of(vb) == "eB", "两个用户的 key 串了"
    assert mem_factory.for_user(user_id="A", llm=_user_llm("kA"), embedding_key="eA") is va, "同一份凭据没命中缓存"
    va2 = mem_factory.for_user(user_id="A", llm=_user_llm("kA"), embedding_key="eA-new")
    assert va2 is not va and _embed_key_of(va2) == "eA-new", "换了百炼 key 仍拿到旧视图"
    va3 = mem_factory.for_user(user_id="A", llm=_user_llm("kA-new"), embedding_key="eA")
    assert va3 is not va, "换了 LLM key 仍拿到旧视图"


def test_T7_missing_either_key_gives_no_view_and_builds_nothing(mem_factory):
    assert mem_factory.for_user(user_id="C", llm=None, embedding_key="e") is None
    assert mem_factory.for_user(user_id="C", llm=_user_llm("k"), embedding_key="") is None
    assert mem_factory.built == [], "缺 key 却构造了 mem0 实例"


def test_T7b_base_view_never_calls_a_model(mem_factory):
    from core.memory_manager import MemoryKeyMissing

    base = mem_factory.base()
    assert mem_factory.get_all("card1") == []
    assert mem_factory.delete_all("card1") is True and base._mem.deleted == ["card1"]
    with pytest.raises(MemoryKeyMissing):
        base._mem.embedding_model.embed("x")
    with pytest.raises(MemoryKeyMissing):
        base._mem.llm.generate_response([])


def test_T5b_real_mem0_views_share_one_local_qdrant(tmp_path, monkeypatch):
    """真 mem0 + 真本地 qdrant：两个用户视图 + 底层视图同开一个目录，不撞文件锁。

    本地 qdrant 同一目录只允许一个客户端（第二个 `RuntimeError: ... already accessed`），
    本条就是「共用官方 `client` 参数」这个设计的实测依据。遥测关掉：否则 mem0 会往
    外网发事件。
    """
    import mem0.memory.main as mem0_main
    import mem0.memory.telemetry as mem0_telemetry
    from core.memory_manager import MemoryManager

    # 两处都要关：main 里那份只管遥测向量库，发事件的判断在 telemetry 模块自己那份。
    monkeypatch.setattr(mem0_main, "MEM0_TELEMETRY", False)
    monkeypatch.setattr(mem0_telemetry, "MEM0_TELEMETRY", False)
    mm = MemoryManager({}, db_dir=tmp_path / "mem0_db")
    va = mm.for_user(user_id="A", llm=_user_llm("kA"), embedding_key="eA")
    vb = mm.for_user(user_id="B", llm=_user_llm("kB"), embedding_key="eB")
    base = mm.base()
    clients = {id(v._mem.vector_store.client) for v in (va, vb, base)}
    assert len(clients) == 1, "三个视图没共用同一个 qdrant 客户端"
    assert base.get_all("card1") == []


# ── T8：记忆接口 —— 查看 / 删除不需要 key；写入没 key → 409 ─────────────────

@pytest.fixture
def mem_client(client, mem_factory):
    from deps import get_memory_manager

    server.app.dependency_overrides[get_memory_manager] = lambda: mem_factory
    try:
        yield client
    finally:
        server.app.dependency_overrides.pop(get_memory_manager, None)


def test_T8_memory_routes_without_own_keys(mem_client, store, user):
    _assert_premise(store, user)
    src_id, _ = _seed_public_pair(store, user["id"])

    r = mem_client.get(f"/api/memory/list/{src_id}", headers=_token(user))
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False and r.json()["enabled"] is True

    r = mem_client.delete(f"/api/memory/clear/{src_id}", headers=_token(user))
    assert r.status_code == 200, f"没 key 就清不掉自己的记忆：{r.status_code} {r.text}"

    r = mem_client.post(f"/api/memory/add/{src_id}", json={"text": "一条记忆"}, headers=_token(user))
    assert r.status_code == 409, r.text
    assert "百炼 Key" in r.json()["detail"]


def test_T7c_text_manager_hands_its_own_llm_to_the_factory(mem_factory):
    """建会话处取记忆视图走 `TextManager.memory_for`：LLM 用它手里那个（用户自己的）。"""
    from core.text_manager import TextManager

    llm = _user_llm("user-llm-T")
    tm = TextManager(lambda: None, None, llm, {}, memory_manager=mem_factory)
    view = tm.memory_for("T", "user-emb-T")
    assert view is not None, "两把 key 齐了，建会话处却没拿到记忆视图"
    assert view._mem.llm._adapter.credential_fingerprint() == llm.credential_fingerprint()
    assert tm.memory_for("T", "") is None, "没有百炼 key 却给了记忆视图"
    assert TextManager(lambda: None, None, llm, {}, memory_manager=None).memory_for("T", "e") is None


# ── T9：活会话中保存新 key → 下一轮 LLM / 记忆 / 检索都换成新的 ─────────────────

class _RefreshStorage:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    async def get_user_api_config(self, user_id):
        return dict(self.cfg)

    async def get_card_owned(self, card_id, user_id):
        return {"id": card_id, "text_id": "txt_live"}

    async def get_text_owned(self, text_id, user_id):
        return {"content": "原文正文"}


def test_T9_saving_new_keys_reaches_the_live_session(mem_factory, monkeypatch):
    from core.chat_engine import ChatEngine
    from core.text_manager import new_session_entry
    import core.indexing_service as IS

    seen: list[str] = []

    class _Svc:
        def get_rag_for_session(self, text_id, *, card_id,
                                embedding_key="", embedding_region="cn"):
            seen.append(embedding_key)
            return ("rag-for", embedding_key)

    monkeypatch.setattr(deps, "get_memory_manager", lambda: mem_factory)
    monkeypatch.setattr(deps, "get_indexing_service", lambda: _Svc())

    uid = f"usr_{uuid.uuid4().hex[:8]}"
    old_llm = _user_llm("old-llm")
    engine = ChatEngine(llm=old_llm, rag=None, card=CharacterCard(name="甲"), card_id="c_live",
                        storage=None, session_id="s_live", is_new_session=True)
    assert engine._memory is None and engine.rag is None, "前提破了：会话建出来时就已有记忆 / 检索"
    deps.get_sessions()["s_live"] = new_session_entry(engine, None, uid)
    try:
        storage = _RefreshStorage({"api_key": "new-llm", "base_url": "https://api.deepseek.com",
                                   "model": "m", "embedding_key": "new-emb", "embedding_region": "cn"})
        swapped = _run(deps.refresh_user_llm(uid, storage))

        assert swapped == 1
        assert engine.llm is not old_llm, "LLM 没换"
        assert engine._memory is not None, "补上两把 key 后活会话仍没有记忆"
        assert _embed_key_of(engine._memory) == "new-emb", "记忆没用新百炼 key"
        for holder in (engine._ctx_engine.memory, engine._reflection_service._memory,
                       engine._event_service._memory):
            assert holder is engine._memory, "记忆引用有一处没换（那一处还会用旧 key 出站）"
        assert engine.rag == ("rag-for", "new-emb") and engine._ctx_engine.rag is engine.rag, \
            "检索没按新百炼 key 换"
    finally:
        deps.get_sessions().pop("s_live", None)
        deps.clear_user_llm_cache(uid)


def test_T5c_load_test_override_keeps_the_users_key(mem_factory, monkeypatch):
    """`MEM0_LLM_BASE_URL`（本地压测接 mock）只换地址，key 仍是用户自己的。"""
    monkeypatch.setenv("MEM0_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    view = mem_factory.for_user(user_id="P", llm=_user_llm("user-llm-P"), embedding_key="e")
    extractor = view._mem.llm._adapter
    assert extractor.base_url == "http://127.0.0.1:9/v1"
    assert extractor._api_key == "user-llm-P"
