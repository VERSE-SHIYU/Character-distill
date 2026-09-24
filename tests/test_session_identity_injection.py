# -*- coding: utf-8 -*-
"""缺陷 96 步骤 5 的锁：会话 / 群身份在**构造时**注入，之后无人再赋值。

**改前形态。** `ChatEngine.__init__` 把 `_session_id` / `_group_id` 初始化成空串，身份
靠事后赋值补上：`TextManager._create_session` 先生成 id 再造引擎（id 只能事后塞）、续接与
恢复用「一个用完即弃的新 id 造引擎 → `sessions.pop` 改名成原 id」、群聊在**真正要跑好感度
评估时**才给引擎补 `_group_id`。三处是同一个形态：**引擎出生时不知道自己在哪。**

**为什么这不只是清理。** `:153` 的 `if not self._session_id:` 注释写着「新会话才算初始
好感度」，可构造函数刚把 `_session_id` 设成空串 —— 该判断在构造时**永远为真**，于是每次
构造都算一遍初值。改成构造注入后它会**静默反过来**（续接的引擎也带 id → 永远为假），
所以新旧必须变成显式入参 `is_new_session`，不能继续拿 id 有无去推。本文件那对正反用例
（`test_new_session_...` / `test_resumed_session_...`）就是咬这一条的：把 :153 恢复成
`if not self._session_id:` 先红，把 `is_new_session` 忽略掉（一律算 / 一律不算）也红。

**观测面。** 会话侧看 `_create_session` 的返回值与 `_sessions` 里的引擎；群侧经真路由
`/api/group/create` 建群、再看 `get_group_sessions()` 里每个引擎 —— 事后赋值那条路
（`core/group_session.py` 在评估前补 `_group_id`）只在这条路上才暴露。

**边界**：锁的是「构造出来的引擎带着什么」。不锁 `new_session_entry` 的字段
（那是 `test_session_entry_shape.py`），不锁 `_create_session` 的 keyword-only 形态
（那是 `test_create_session_kwonly_lock.py`）。

Run: pytest tests/test_session_identity_injection.py -v
"""
from __future__ import annotations

import asyncio
import inspect
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
from core.chat_engine import ChatEngine
from core.schema import CharacterCard
from core.text_manager import TextManager
from routers.auth import get_current_user
from routers.chat import _ensure_session
from routers.group import get_group_sessions, _rebuild_group_session
from routers.group import router as group_router
from storage.sqlite_store import SQLiteStore


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── 桩：只钉住「会拉真网络 / 真向量库」的那几格 ─────────────────────────────


class _StubLLM:
    model = "stub"
    last_usage: dict = {}

    def preflight(self) -> None:
        return None

    def chat(self, *_a, **_kw) -> str:
        return "固定回复"

    async def achat(self, *_a, **_kw) -> str:
        return "固定回复"


class _NoRAG:
    """索引服务替身：真身会拉 chroma → fastembed → onnxruntime（本机崩）。"""

    def get_rag_for_session(self, *_a, **_kw):
        return None

    def schedule_scene_index(self, *_a, **_kw):
        return None


class _MemMgr:
    enabled = False

    def get_all(self, _card_id):
        return []

    def search(self, *_a, **_kw):
        return []

    def add(self, *_a, **_kw):
        return True


def _text_manager(store, sessions) -> TextManager:
    return TextManager(lambda: store, None, _StubLLM(), sessions,
                       indexing_service=_NoRAG(), memory_manager=_MemMgr())


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex[:12]}"


def _seed(store, uid) -> tuple[str, str]:
    """一张卡 + 一条会话行 —— `_ensure_session` 的重建分支至少要读到这些。"""
    tid = f"txt_{uuid.uuid4().hex}"
    cid = f"card_{uuid.uuid4().hex}"
    sid = uuid.uuid4().hex[:12]
    _run(store.save_text(tid, "src.txt", "content", user_id=uid))
    _run(store.save_card(cid, tid, "张三", json.dumps({"name": "张三"}), user_id=uid))
    _run(store.save_session(sid, cid, "", "", uid))
    return sid, cid


# ── 1. 建会话：引擎造出来就带着 id ───────────────────────────────────────────


def test_a_new_engine_is_born_with_its_session_id(store, user_id):
    """`/start` 那条路（`_create_session`）返回的 id，引擎**当场**就有 —— 不再事后塞。"""
    sessions: dict = {}
    tm = _text_manager(store, sessions)

    sid = tm._create_session(CharacterCard(name="测试角色"), user_id=user_id)

    assert sid, "建会话没返回 id"
    engine = sessions[sid]["engine"]
    assert engine._session_id == sid, (
        f"引擎出生时不知道自己的会话 id：库里是 {sid}，引擎里是 {engine._session_id!r}")


def test_create_session_derives_new_or_resumed_from_the_id_alone():
    """F1：`_create_session` 不再收 `is_new_session` —— 给了原 id 就是续接。

    改前签名里两个参数并存，`session_id=<原 id>` + 默认的 `is_new_session=True` 是一个
    能编译、能跑、**静默错**的组合：续接的引擎重算初值，盖掉库里恢复出来的状态。
    这一层只有新建 / 续接两条路，id 有无即判据；显式参数留在 `ChatEngine` 那边 ——
    群引擎有真 `group_id`、`session_id` 为空但仍属新建，id 有无在那一层不是判据。
    """
    params = inspect.signature(TextManager._create_session).parameters
    assert "is_new_session" not in params, (
        "`is_new_session` 又回到 `_create_session` 签名里了 —— 它与 `session_id` 并存时，"
        "「传了原 id 却按新会话处理」这个错组合就有了落脚点")


def test_lazy_rebuild_constructs_the_engine_under_the_original_id(store, user_id, monkeypatch):
    """续接 / 恢复：原 id 进构造，不再「新 id → `sessions.pop` 改名」。

    判据落在**构造时的入参**上，因为改名路在内存里只留下一瞬（新 id 建了又搬走），
    改前改后「最后只剩原 id」这件事都成立 —— 只看结果分不出两条路。
    """
    sid, _cid = _seed(store, user_id)
    sessions: dict = {}
    seen: dict = {}
    born: dict = {}

    real = TextManager._create_session
    real_init = ChatEngine.__init__

    def _spy(self, card, **kw):
        seen.update(kw)
        return real(self, card, **kw)

    def _spy_init(self, *a, **kw):
        born.update(kw)
        return real_init(self, *a, **kw)

    async def _fake_user_llm(*_a, **_kw):
        return _StubLLM()

    monkeypatch.setattr(TextManager, "_create_session", _spy)
    monkeypatch.setattr(ChatEngine, "__init__", _spy_init)
    monkeypatch.setattr(deps, "_storage", store)
    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(
        deps, "get_text_manager",
        lambda llm=None: _text_manager(store, sessions))

    session = _run(_ensure_session(sid, store, sessions, user_id))

    assert seen.get("session_id") == sid, (
        f"重建时没有把原会话 id 交给构造函数（收到 {seen.get('session_id')!r}）—— "
        "还是「先造后改名」那条路")
    # 观测点从「调用点传了什么」下移到「构造引擎时喂了什么」：`_create_session` 已经
    # 不收 `is_new_session`（F1），它由 `session_id is None` 现算 —— 这条仍在咬
    # 「续接的引擎被判成新会话」，只是换到值真正生效的那一层看。
    assert born.get("is_new_session") is False, (
        f"续接的引擎被判成了新会话（is_new_session={born.get('is_new_session')!r}）—— "
        "它会重算初始好感度，把库里恢复出来的状态盖掉")
    assert set(sessions) == {sid}, (
        f"重建后内存里出现了别的会话 id：{sorted(sessions)} —— 临时的那个没搬走")
    assert session["engine"]._session_id == sid


# ── 2. 新旧是显式入参，不是「id 有没有」─────────────────────────────────────
#
# 两条配一对才咬得住 :153 那个判断：新会话即便**带着 id** 也要算初值（恢复
# `if not self._session_id:` 会红），续接的引擎不许算（把 `is_new_session` 忽略成
# 「一律算」也会红）。


def _engine(session_id: str, is_new_session: bool) -> ChatEngine:
    return ChatEngine(
        _StubLLM(), None, CharacterCard(name="测试角色"),
        card_id="t", storage=None,
        session_id=session_id, is_new_session=is_new_session,
    )


def test_new_session_computes_the_initial_affinity():
    """新会话：无身份 → 陌生人初值（`AffinityService` 的容器默认是 50 / 平静）。"""
    affinity = _engine("ses_brand_new", is_new_session=True).get_affinity()

    assert affinity["affinity"] <= 18, (
        f"新会话没算初始好感度，拿到容器默认值 {affinity['affinity']} —— "
        "「新会话才算」这个判断被 id 的有无带偏了")
    assert affinity["mood"] == "警觉", affinity


def test_resumed_session_does_not_recompute_the_initial_affinity():
    """续接：引擎出生时不算初值，等 `load_affinity` 把库里的状态装回来。"""
    affinity = _engine("ses_resumed", is_new_session=False).get_affinity()

    assert affinity["affinity"] == 50, (
        f"续接的引擎自己算了一遍初值（{affinity['affinity']}）—— "
        "库里的状态还没装回来就先被盖了一层")
    assert affinity["mood"] == "平静", affinity


# ── 3. 群引擎：构造即有群 id ────────────────────────────────────────────────

_GROUP_CARD = '{"name": "张三"}'


class _ReplyLLM(_StubLLM):
    pass


class _NoopRAG:
    def load_existing(self, *_a, **_kw):
        return None

    def index(self, *_a, **_kw):
        return None

    def query_with_emotion_ex(self, *_a, **_kw):
        return []


@pytest.fixture
def group_client(store, user_id, monkeypatch):
    """真群路由 + 钉死的 ambient 依赖（同 `test_group_save_failure` 的口径）。"""
    import routers.group as group_mod

    async def _fake_user_llm(*_a, **_kw):
        return _ReplyLLM()

    monkeypatch.setattr(deps, "get_llm", _ReplyLLM)
    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    # group.py 是**模块级** import，绑死在自己的命名空间里，打 `deps` 那份打不到它。
    monkeypatch.setattr(group_mod, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_memory_manager", lambda: _MemMgr())
    monkeypatch.setattr(
        deps, "get_rag_config",
        lambda: {"chunk_size": 500, "chunk_overlap": 50, "top_k": 3})
    monkeypatch.setattr("core.rag.RAGEngine", lambda *_a, **_kw: _NoopRAG())
    monkeypatch.setattr(deps, "_storage", store)

    app = FastAPI()
    app.include_router(group_router)
    app.dependency_overrides[deps.get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "username": "testuser", "role": "user"}
    return TestClient(app)


def _create_group(client, store, uid) -> tuple[str, str]:
    tid = f"txt_{uuid.uuid4().hex}"
    cid = f"card_{uuid.uuid4().hex}"
    _run(store.save_text(tid, "src.txt", "content", user_id=uid))
    _run(store.save_card(cid, tid, "张三", _GROUP_CARD, user_id=uid))
    r = client.post("/api/group/create", json={
        "card_ids": [cid], "user_persona_type": "stranger", "user_persona_name": "路人"})
    assert r.status_code == 200, (
        f"建群这一步就失败了，后面的断言无从谈起：{r.status_code} {r.text[:200]}")
    return r.json()["group_id"], cid


def _assert_all_engines_know_the_group(group, gid: str, where: str) -> None:
    assert group.engines, f"{where}：一个引擎都没有，用例没验到东西"
    for cid, engine in group.engines.items():
        assert engine._group_id == gid, (
            f"{where}：角色 {cid} 的引擎没带上群 id（拿到 {engine._group_id!r}）—— "
            "评估好感度时 `_group_id` 是空的，状态就落到无主的键上")


def test_group_engines_are_born_with_the_group_id(group_client, store, user_id):
    """新建群（`group.py` 的建群路径）：id 先于引擎生成，逐个喂进构造函数。"""
    gid, _cid = _create_group(group_client, store, user_id)

    _assert_all_engines_know_the_group(get_group_sessions()[gid], gid, "新建群")


def test_rebuilt_group_engines_are_born_with_the_group_id(group_client, store, user_id):
    """服务重启后重建（`_rebuild_group_session`）：群 id 是**入参**，不经事后赋值。"""
    gid, _cid = _create_group(group_client, store, user_id)
    get_group_sessions().pop(gid, None)

    group = _run(_rebuild_group_session(gid, user_id, store))

    assert group is not None, "重建返回了 None —— 用例外围就没跑到建引擎那段"
    _assert_all_engines_know_the_group(group, gid, "重建群")
