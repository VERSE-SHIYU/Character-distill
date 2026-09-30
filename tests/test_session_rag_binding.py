# -*- coding: utf-8 -*-
"""锁：每个会话有**自己的**检索，会自己接上后台建好的集合；后台建集合一次只建一份。

**改前的病。**
  1. 新会话拿不到检索：`/api/distill/start_session`（原文分支）、
     `TextManager.get_or_distill`、`TextManager.save_distilled_card` 三处建会话都写死
     `rag=None`，场景工具在新会话里恒空。
  2. 同一本书的会话共用一个引擎：`IndexingService` 按「书 + key」缓存 `RAGEngine`
     并交给每个会话；场景预索引又把这同一个对象改指到 `scenes_{某张卡}`，A 卡的会话
     于是被改指到 B 卡的场景集合。
  3. 独立卡片（没有原文）的会话在重启后懒重建 / 重连时被当成「原文不存在」→ 404。
  4. 后台建集合：`wait_for` 超时取消不了工作线程，却提前释放了去重键；同一本书的原文
     集合首次构建也没有互斥 —— 两个作业会并发「先删后建」同一个集合。
  5. `/api/distill/reindex/{text_id}` 遍历内存里**所有用户**的会话，拿调用者的原文重建
     每个会话的检索。

**现在的约定。** 会话拿 `SessionRag`：建会话时不做 IO；按 `scenes_{card_id}` →
`text_{text_id}` 取第一个可用的集合（正在建 / 维度不符的跳过看下一个），装载结果一直用到
有集合建完或本会话查询失败才重读。建集合只在后台，经构建登记按集合名互斥；去重键在线程
真正结束后才释放，期间再来的调度按最新参数排在后面跑。

**观测面。** 服务层用假 chroma 客户端驱动真 `IndexingService` / 真 `SceneIndexer`；
调用点层用「按 (text_id, card_id) 返回不同对象」的索引服务替身。不碰真 chroma、不碰
数据库（存储用只实现走到的方法的内存替身）。

Run: pytest tests/test_session_rag_binding.py -v
"""
from __future__ import annotations

import asyncio
import time

import pytest
from chromadb.errors import NotFoundError
from fastapi import HTTPException

import core.indexing_service as IS
from core.rag import CollectionUnusableError, RAGEngine
from core.schema import CharacterCard
from core.text_manager import TextManager

TEXT = (
    "第一幕，讲的是魏无羡在云深不知处的一段旧事。那天他坐在廊下，说了很多话，"
    "也做了很多事，直到天色彻底暗下来，才慢慢起身回房。\n\n"
    "第二幕，讲的是江澄后来在莲花坞的日子。他把紫电擦了一遍又一遍，谁也不见，"
    "只让门外的弟子把当日的账册送进来，一直看到深夜才肯停下。"
)
KEY, REGION = "sk-test-binding", "cn"


# ── chroma 替身（语义照真件：不存在抛 NotFoundError、空集合 count()==0）─────────


class _Embedder:
    _dimensions = 8


class _Collection:
    def __init__(self, name: str, metadata: dict | None = None) -> None:
        self.name = name
        self.dim = _Embedder._dimensions  # 改成别的值 = 由别的 embedder 写的旧集合
        self.metadata = dict(metadata or {})
        self.docs: list[str] = []
        self.metas: list[dict] = []
        self.fail_next_query = False

    def add(self, documents=None, ids=None, metadatas=None):
        self.docs.extend(documents or [])
        self.metas.extend(metadatas or [{} for _ in (documents or [])])

    def count(self) -> int:
        return len(self.docs)

    def peek(self, limit: int = 1):
        return {"embeddings": [[0.0] * self.dim] if self.docs else []}

    def query(self, query_texts=None, n_results=3, include=None):
        if self.fail_next_query:
            self.fail_next_query = False
            raise RuntimeError(f"Collection {self.name} does not exist")
        k = min(n_results, len(self.docs))
        return {"ids": [[f"{self.name}_{i}" for i in range(k)]],
                "documents": [self.docs[:k]], "distances": [[0.1] * k],
                "metadatas": [self.metas[:k]]}


class _Client:
    def __init__(self) -> None:
        self.cols: dict[str, _Collection] = {}
        self.creates: list[str] = []

    def seed(self, name: str, *, dim: int = _Embedder._dimensions) -> None:
        col = _Collection(name)
        col.dim = dim
        col.add(documents=["x"], metadatas=[{"characters": "魏无羡"}])
        self.cols[name] = col

    def get_collection(self, name=None, embedding_function=None):
        if name not in self.cols:
            raise NotFoundError(f"Collection {name} does not exist")
        return self.cols[name]

    def create_collection(self, name=None, embedding_function=None, metadata=None):
        col = _Collection(name, metadata)
        self.cols[name] = col
        self.creates.append(name)
        return col

    def delete_collection(self, name=None):
        if name not in self.cols:
            raise NotFoundError(f"Collection {name} does not exist")
        del self.cols[name]


def _patch_engine(monkeypatch, client: _Client) -> list[RAGEngine]:
    """把 `IndexingService` 里造的引擎换成「共用假客户端、不跑 __init__」的真 RAGEngine。"""
    made: list[RAGEngine] = []

    def _factory(_config, *_a, **_kw):
        eng = object.__new__(RAGEngine)
        eng._client = client
        eng._embedding_function = _Embedder()
        eng._chunk_size, eng._chunk_overlap, eng._top_k = 200, 20, 3
        eng._collection_name = "rag_test"
        eng.collection = None
        eng.collection_name = None
        made.append(eng)
        return eng

    monkeypatch.setattr(IS, "RAGEngine", _factory)
    return made


def _svc() -> IS.IndexingService:
    return IS.IndexingService({"chunk_size": 200, "chunk_overlap": 20, "top_k": 3})


def _view(svc, text_id: str, card_id: str):
    return svc.get_rag_for_session(text_id, card_id=card_id, embedding_key=KEY, embedding_region=REGION)


def _ask(view) -> None:
    view.query_with_emotion_ex("那天廊下说了什么", current_emotion="平静",
                               character_name="魏无羡", top_k=3)


def _run_jobs(fn):
    """在事件循环里跑 fn（它会调度后台作业），等全部后台作业跑完。"""
    async def _scenario():
        tasks: list[asyncio.Task] = []
        real = asyncio.create_task

        def _track(coro):
            t = real(coro)
            tasks.append(t)
            return t

        IS.asyncio.create_task = _track
        try:
            fn()
            await asyncio.gather(*tasks)
        finally:
            IS.asyncio.create_task = real

    asyncio.run(_scenario())


def _schedule(svc, card_id: str, name: str = "魏无羡") -> None:
    svc.schedule_scene_index("t1", card_id, TEXT, name, embedding_key=KEY, embedding_region=REGION)


# ── 服务层：装载顺序、不建、不共用、自动接上、失败后重读 ──────────────────────


def test_a1_binds_the_cards_own_scenes_first(monkeypatch):
    client = _Client()
    for name in ("scenes_c1", "scenes_c2", "text_t1"):
        client.seed(name)
    _patch_engine(monkeypatch, client)

    view = _view(_svc(), "t1", "c1")
    _ask(view)

    assert view.collection_name == "scenes_c1", (
        f"会话该先绑自己卡的场景集合，实际绑到了 {view.collection_name!r}")


def test_a2_falls_back_to_the_text_collection(monkeypatch):
    client = _Client()
    client.seed("text_t1")
    _patch_engine(monkeypatch, client)

    view = _view(_svc(), "t1", "c1")
    _ask(view)

    assert view.collection_name == "text_t1", (
        f"本卡没有场景集合时应回落原文集合，实际 {view.collection_name!r}")


def test_a3_no_collection_is_empty_and_nothing_is_built(monkeypatch):
    client = _Client()
    _patch_engine(monkeypatch, client)
    # 只记录、不抛：替身若抛错，会被当成「检索失败」，本条就测不出「建了」。
    built: list = []
    monkeypatch.setattr(RAGEngine, "index", lambda self, *a, **kw: built.append(kw))

    view = _view(_svc(), "t1", "c1")
    hits = view.query_with_emotion_ex("问题", character_name="魏无羡", top_k=3)

    assert built == [], f"会话检索路径调用了 index()：请求里不许建集合（{built}）"
    assert list(hits) == [], "一个集合都没有时本轮应是空结果"
    assert client.creates == [], f"会话检索路径建了集合：{client.creates}"


def test_a4_no_io_when_the_session_is_created(monkeypatch):
    client = _Client()
    client.seed("text_t1")
    made = _patch_engine(monkeypatch, client)

    view = _view(_svc(), "t1", "c1")

    assert view is not None
    assert made == [], "建会话时就去读了 chroma —— 装载应推迟到第一次检索"


def test_b1_scene_indexing_of_another_card_does_not_repoint_a_session(monkeypatch):
    """同一本书两张卡：B 卡的场景预索引不许把 A 会话改指过去。"""
    client = _Client()
    client.seed("text_t1")
    _patch_engine(monkeypatch, client)
    svc = _svc()
    a = _view(svc, "t1", "cA")
    _ask(a)

    _run_jobs(lambda: _schedule(svc, "cB", "江澄"))
    _ask(a)
    b = _view(svc, "t1", "cB")
    _ask(b)

    assert "scenes_cB" in client.cols, "场景预索引没跑起来 —— 下面的断言会变成空转"
    assert a is not b
    assert a.collection_name == "text_t1", f"A 会话被改指到了 {a.collection_name!r}"
    assert b.collection_name == "scenes_cB", f"B 会话应绑 scenes_cB，实际 {b.collection_name!r}"


def test_b2_a_session_picks_up_its_scenes_once_built(monkeypatch):
    """方案 B 的核心：集合在后台建好后，已开着的会话下一轮检索自动接上。"""
    client = _Client()
    client.seed("text_t1")
    _patch_engine(monkeypatch, client)
    svc = _svc()
    view = _view(svc, "t1", "cA")
    _ask(view)
    assert view.collection_name == "text_t1", "前提：一开始只有原文集合，会话绑在它上面"

    _run_jobs(lambda: _schedule(svc, "cA"))
    _ask(view)

    assert view.collection_name == "scenes_cA", (
        f"后台建好本卡场景集合后，会话没有自动接上：{view.collection_name!r}")


def test_b3_bound_to_own_scenes_stops_rereading(monkeypatch):
    client = _Client()
    client.seed("scenes_c1")
    made = _patch_engine(monkeypatch, client)
    view = _view(_svc(), "t1", "c1")

    _ask(view)
    _ask(view)
    _ask(view)

    assert len(made) == 1, f"已绑上本卡场景集合却每轮都在重读（造了 {len(made)} 个引擎）"


def test_b4_a_failed_query_drops_the_engine_and_the_next_turn_rereads(monkeypatch):
    """集合被删了再建，旧句柄失效：本轮失败可见，下一轮重读，不永久坏掉。"""
    client = _Client()
    client.seed("scenes_c1")
    made = _patch_engine(monkeypatch, client)
    view = _view(_svc(), "t1", "c1")
    _ask(view)

    client.cols["scenes_c1"].fail_next_query = True
    with pytest.raises(CollectionUnusableError):
        _ask(view)
    _ask(view)

    assert len(made) == 2, f"查询失败后下一轮没有重读（造了 {len(made)} 个引擎）"
    assert view.collection_name == "scenes_c1"


def test_b5_unusable_scenes_fall_back_to_the_text_collection(monkeypatch):
    """本卡场景集合是别的 embedder 写的旧集合：跳过它，原文集合照样用。"""
    client = _Client()
    client.seed("scenes_c1", dim=384)
    client.seed("text_t1")
    _patch_engine(monkeypatch, client)
    view = _view(_svc(), "t1", "c1")

    _ask(view)

    assert view.collection_name == "text_t1", (
        f"场景集合不可用时没回落原文集合：{view.collection_name!r}")


def test_b6_no_usable_collection_fails_each_turn_but_loads_once(monkeypatch):
    """集合都不可用：每轮检索都记为失败，但不每轮重读 —— 直到有集合建完。"""
    client = _Client()
    client.seed("text_t1", dim=384)
    made = _patch_engine(monkeypatch, client)
    view = _view(_svc(), "t1", "c1")

    for _ in range(3):
        with pytest.raises(CollectionUnusableError):
            _ask(view)
    assert len(made) == 1, f"不可用的结论没留住，每轮都在重读（造了 {len(made)} 个引擎）"

    with IS._builds.building("text_t1"):
        client.seed("text_t1")
    _ask(view)

    assert len(made) == 2 and view.collection_name == "text_t1", (
        "集合重建好之后，下一轮没有重读")


def test_b7_a_collection_being_built_fails_the_turn_instead_of_reading_empty(monkeypatch):
    """原文集合正在重建（先删后建）：本轮是「失败」，不是「真没有」；建完下一轮接上。"""
    client = _Client()
    _patch_engine(monkeypatch, client)
    view = _view(_svc(), "t1", "c1")

    with IS._builds.building("text_t1"):
        with pytest.raises(IS.CollectionBuilding):
            _ask(view)
        client.seed("text_t1")
    _ask(view)

    assert view.collection_name == "text_t1"


def test_b8_a_session_without_text_gets_no_retrieval():
    assert _view(_svc(), "", "c1") is None, "独立卡片（没有原文）不该拿到检索"


# ── 后台：同一集合一次只建一份；去重键等线程结束才释放 ────────────────────────


def _fake_index(client: _Client, delay: float = 0.0, calls: list | None = None):
    def _index(self, text, collection_name=None, all_characters=None):
        if calls is not None:
            calls.append(collection_name)
        time.sleep(delay)
        col = client.create_collection(name=collection_name)
        col.add(documents=["x"], metadatas=[{"characters": "魏无羡"}])
        self.collection = col
        self.collection_name = collection_name
    return _index


def test_c1_two_cards_of_one_book_build_the_text_collection_once(monkeypatch):
    client = _Client()
    _patch_engine(monkeypatch, client)
    calls: list = []
    monkeypatch.setattr(RAGEngine, "index", _fake_index(client, delay=0.2, calls=calls))
    svc = _svc()

    def _both():
        _schedule(svc, "cA")
        _schedule(svc, "cB", "江澄")

    _run_jobs(_both)

    assert calls.count("text_t1") == 1, f"同一本书的原文集合被并发建了 {calls.count('text_t1')} 次"
    assert {"scenes_cA", "scenes_cB"} <= set(client.cols), "两张卡的场景集合都应建好"


def test_c2_dedup_key_is_held_until_the_worker_thread_finishes(monkeypatch):
    """超时取消不了工作线程：去重键若随超时释放，第二个作业会和还在写的第一个撞上。"""
    client = _Client()
    _patch_engine(monkeypatch, client)
    calls: list = []
    monkeypatch.setattr(RAGEngine, "index", _fake_index(client, delay=0.6, calls=calls))
    real_wait_for = asyncio.wait_for
    # 把所有 wait_for 的超时压到 0.05s：改前的 120s / 180s 在这里 0.05s 就到点。
    monkeypatch.setattr(asyncio, "wait_for", lambda aw, timeout=None: real_wait_for(aw, 0.05))
    svc = _svc()

    async def _scenario():
        tasks: list[asyncio.Task] = []
        real = asyncio.create_task
        IS.asyncio.create_task = lambda coro: tasks.append(real(coro)) or tasks[-1]
        try:
            _schedule(svc, "cA")
            await asyncio.sleep(0.2)          # 第一个作业的线程还在建（0.6s）
            seen["held"] = "scenes_cA" in IS._scene_index_in_flight
            _schedule(svc, "cA")              # 同一张卡再调度一次
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.to_thread(time.sleep, 1.0)   # 等所有工作线程收尾
        finally:
            IS.asyncio.create_task = real

    seen: dict = {}
    asyncio.run(_scenario())

    assert seen["held"], "工作线程还在建，去重键就被释放了"
    assert calls.count("text_t1") == 1, (
        f"第一个作业的线程还在写，第二个作业又建了一遍（原文集合建了 {calls.count('text_t1')} 次）")


def test_c3_a_job_scheduled_while_one_runs_is_run_after_it_with_the_latest_input(monkeypatch):
    """重建还在跑时又来两次：不丢，也不并发；前一个结束后按**最新**那次的名单再跑一次。"""
    client = _Client()
    _patch_engine(monkeypatch, client)
    rosters: list = []

    def _index(self, text, collection_name=None, all_characters=None):
        rosters.append(all_characters)
        time.sleep(0.2)
        col = client.create_collection(name=collection_name)
        col.add(documents=["x"], metadatas=[{}])
        self.collection, self.collection_name = col, collection_name

    monkeypatch.setattr(RAGEngine, "index", _index)
    svc = _svc()

    def _reindex(roster):
        svc.schedule_text_reindex("t1", TEXT, all_characters=roster,
                                  embedding_key=KEY, embedding_region=REGION)

    _run_jobs(lambda: (_reindex(["第一次"]), _reindex(["第二次"]), _reindex(["第三次"])))

    assert rosters == [["第一次"], ["第三次"]], (
        f"跑着的时候再来的调度应合并成一次、用最新的名单：{rosters}")
    assert "text_t1" not in IS._scene_index_in_flight and "text_t1" not in IS._pending_jobs


# ── 调用点层：每条建会话的路都把「本卡的」检索交给引擎 ─────────────────────────


class _Indexing:
    """按 (text_id, card_id) 返回互不相同的对象 —— 串卡 / 丢 card_id 都认得出来。"""

    def __init__(self) -> None:
        self.reindexed: list[tuple] = []

    def get_rag_for_session(self, text_id, *, card_id, embedding_key, embedding_region):
        # 与真件同一契约：没有原文（独立卡片）→ None。
        return ("rag", text_id, card_id) if text_id else None

    def schedule_scene_index(self, *_a, **_kw):
        return None

    def schedule_text_reindex(self, text_id, content, *, all_characters,
                              embedding_key, embedding_region):
        self.reindexed.append((text_id, embedding_key))


class _LLM:
    model = "stub"
    last_usage: dict = {}

    def preflight(self) -> None:
        return None

    def chat(self, *_a, **_kw) -> str:
        raise RuntimeError("本用例不调模型")


class _Store:
    """只实现各路径走到的方法。`standalone=True`：卡没有原文（独立卡片）。"""

    def __init__(self, uid: str, *, standalone: bool = False) -> None:
        self.uid = uid
        self.text = {"id": "t1", "content": TEXT, "user_id": uid}
        self.card = {"id": "c1", "text_id": None if standalone else "t1", "name": "魏无羡",
                     "card_json": CharacterCard(name="魏无羡").model_dump_json()}
        self.text_reads: list = []

    async def get_text_owned(self, text_id, user_id):
        self.text_reads.append(text_id)
        return self.text if text_id == "t1" else None

    async def get_card_owned(self, card_id, user_id):
        return self.card if card_id == "c1" else None

    async def list_cards(self, text_id, user_id):
        return [self.card]

    async def get_user_api_config(self, user_id):
        return {"embedding_key": KEY, "embedding_region": REGION}

    async def get_session_owned(self, session_id, user_id):
        return {"id": session_id, "card_id": "c1", "user_role": "", "user_id": user_id}

    async def save_session(self, *_a, **_kw):
        return None

    async def save_card(self, card_id, text_id, name, card_json, user_id):
        return {"id": "c1"}


def _tm(store, sessions, indexing) -> TextManager:
    tm = TextManager(lambda: store, None, _LLM(), sessions,
                     indexing_service=indexing, memory_manager=None)

    async def _chars(*_a, **_kw):
        return [{"name": "魏无羡", "aliases": []}]

    async def _guard(_card):
        from core.moderation.card_guard import GuardVerdict
        return GuardVerdict()

    tm._build_all_characters = _chars
    tm._guard_card = _guard
    tm.memory_for = lambda *_a, **_kw: None
    return tm


def _engine_rag(sessions, session_id):
    return sessions[session_id]["engine"].rag


async def _user_llm(*_a, **_kw):
    return _LLM()


def test_r1_start_session_hands_the_card_its_rag(monkeypatch):
    import deps
    import routers.distill as D

    store, sessions, indexing = _Store("u1"), {}, _Indexing()
    tm = _tm(store, sessions, indexing)
    monkeypatch.setattr(deps, "get_user_llm", _user_llm)
    monkeypatch.setattr(deps, "get_text_manager", lambda **_kw: tm)
    monkeypatch.setattr(D, "get_indexing_service", lambda: indexing)

    out = asyncio.run(D.start_session(
        D.StartSessionRequest(text_id="t1", card_id="c1"),
        user={"id": "u1"}, storage=store, sessions=sessions))

    assert _engine_rag(sessions, out["session_id"]) == ("rag", "t1", "c1"), (
        f"新开会话的引擎没拿到本卡的检索：{_engine_rag(sessions, out['session_id'])!r}")


def test_r2_get_or_distill_hands_the_card_its_rag():
    store, sessions, indexing = _Store("u1"), {}, _Indexing()
    tm = _tm(store, sessions, indexing)

    out = asyncio.run(tm.get_or_distill("t1", "魏无羡", user_id="u1",
                                        embedding_key=KEY, embedding_region=REGION))

    assert _engine_rag(sessions, out["session_id"]) == ("rag", "t1", "c1")


def test_r3_save_distilled_card_hands_the_card_its_rag():
    store, sessions, indexing = _Store("u1"), {}, _Indexing()
    tm = _tm(store, sessions, indexing)

    out = asyncio.run(tm.save_distilled_card("t1", CharacterCard(name="魏无羡"), "u1",
                                             embedding_key=KEY, embedding_region=REGION))

    assert _engine_rag(sessions, out["session_id"]) == ("rag", "t1", "c1")


class _Stop(Exception):
    """截断重建：拿到交给 `_create_session` 的 rag 就够了，后续步骤不在本文件射程。"""


def _recording_tm(indexing, seen: dict):
    class _TM:
        _indexing_service = indexing

        async def _build_all_characters(self, *_a, **_kw):
            return []

        def memory_for(self, *_a, **_kw):
            return None

        def _create_session(self, *_a, **kw):
            seen["rag"] = kw["rag"]
            raise _Stop

    return _TM()


def _ensure(monkeypatch, store) -> dict:
    import deps
    from routers.chat import _ensure_session

    seen: dict = {}
    monkeypatch.setattr(deps, "get_user_llm", _user_llm)
    monkeypatch.setattr(deps, "get_text_manager", lambda **_kw: _recording_tm(_Indexing(), seen))
    with pytest.raises(_Stop):
        asyncio.run(_ensure_session("s1", store, {}, "u1"))
    return seen


def _resume(monkeypatch, store) -> tuple[dict, int]:
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from deps import get_sessions, get_storage
    from routers.auth import get_current_user
    from routers.history import router as history_router

    seen: dict = {}
    monkeypatch.setattr(deps, "get_user_llm", _user_llm)
    monkeypatch.setattr(deps, "get_text_manager", lambda **_kw: _recording_tm(_Indexing(), seen))
    app = FastAPI()
    app.include_router(history_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_sessions] = lambda: {}
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": "user"}
    resp = TestClient(app, raise_server_exceptions=False).post("/api/history/s1/resume", json={})
    return seen, resp.status_code


def test_r4_lazy_rebuild_hands_the_card_its_rag(monkeypatch):
    seen = _ensure(monkeypatch, _Store("u1"))
    assert seen["rag"] == ("rag", "t1", "c1"), f"懒重建交给引擎的检索不对：{seen['rag']!r}"


def test_r5_resume_hands_the_card_its_rag(monkeypatch):
    seen, _status = _resume(monkeypatch, _Store("u1"))
    assert seen.get("rag") == ("rag", "t1", "c1"), f"重连交给引擎的检索不对：{seen.get('rag')!r}"


def test_r6_key_refresh_rebinds_the_card_its_rag(monkeypatch):
    import deps

    monkeypatch.setattr(deps, "get_indexing_service", lambda: _Indexing())

    class _Engine:
        card_id = "c1"
        rag = None

    got = asyncio.run(deps._rag_for_engine(_Engine(), "u1", _Store("u1"), KEY, REGION))

    assert got == ("rag", "t1", "c1"), f"换 key 后给活会话重取的检索不对：{got!r}"


# ── 独立卡片（没有原文）重启后恢复会话 ────────────────────────────────────────


def test_x1_lazy_rebuild_of_a_standalone_card_session(monkeypatch):
    store = _Store("u1", standalone=True)
    try:
        seen = _ensure(monkeypatch, store)
    except HTTPException as exc:
        pytest.fail(f"独立卡片会话懒重建被拒：{exc.status_code} {exc.detail}")
    assert seen["rag"] is None, "独立卡片没有原文，不该拿到检索"
    assert store.text_reads == [], f"独立卡片不该去读原文：{store.text_reads}"


def test_x1b_resume_of_a_standalone_card_session(monkeypatch):
    store = _Store("u1", standalone=True)
    seen, status = _resume(monkeypatch, store)
    assert "rag" in seen, f"独立卡片会话重连没走到建会话那一步（HTTP {status}）"
    assert seen["rag"] is None
    assert store.text_reads == [], f"独立卡片不该去读原文：{store.text_reads}"


# ── /reindex：只重建调用者自己这本书，不碰任何会话 ─────────────────────────────


def test_x4_reindex_touches_no_session_and_schedules_the_callers_text(monkeypatch):
    import deps
    import routers.distill as D
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from deps import get_sessions, get_storage
    from routers.auth import get_current_user

    touched: list = []

    class _OtherUsersRag:
        def index(self, *_a, **_kw):
            touched.append("index")

    class _Engine:
        rag = _OtherUsersRag()
        _all_characters: list = []

    other_sessions = {"s_other": {"engine": _Engine(), "user_id": "u2"}}
    indexing = _Indexing()

    async def _chars(*_a, **_kw):
        return [{"name": "魏无羡", "aliases": []}]

    monkeypatch.setattr(deps, "get_user_llm", _user_llm)
    monkeypatch.setattr(deps, "get_distiller", lambda **_kw: object())
    monkeypatch.setattr(D, "resolve_characters", _chars)
    monkeypatch.setattr(D, "get_indexing_service", lambda: indexing)
    app = FastAPI()
    app.include_router(D.router)
    app.dependency_overrides[get_storage] = lambda: _Store("u1")
    app.dependency_overrides[get_sessions] = lambda: other_sessions
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": "user"}

    resp = TestClient(app, raise_server_exceptions=False).post("/api/distill/reindex/t1")

    assert touched == [], "reindex 动了别的用户的会话检索"
    assert resp.status_code == 200, resp.text
    assert indexing.reindexed == [("t1", KEY)], f"没有在后台重建调用者这本书：{indexing.reindexed}"


def test_x5_reindex_without_embedding_key_spends_no_identify_call(monkeypatch):
    """建不了（没配向量检索 key）就先拒绝 —— 不先花一次识别调用。"""
    import deps
    import routers.distill as D
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from deps import get_storage
    from routers.auth import get_current_user

    identified: list = []

    async def _chars(*_a, **_kw):
        identified.append(1)
        return []

    class _NoKeyStore(_Store):
        async def get_user_api_config(self, user_id):
            return {}

    indexing = _Indexing()
    monkeypatch.setattr(deps, "get_user_llm", _user_llm)
    monkeypatch.setattr(deps, "get_distiller", lambda **_kw: object())
    monkeypatch.setattr(D, "resolve_characters", _chars)
    monkeypatch.setattr(D, "get_indexing_service", lambda: indexing)
    app = FastAPI()
    app.include_router(D.router)
    app.dependency_overrides[get_storage] = lambda: _NoKeyStore("u1")
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": "user"}

    resp = TestClient(app, raise_server_exceptions=False).post("/api/distill/reindex/t1")

    assert resp.status_code == 400, resp.text
    assert identified == [], "没配 key、建不了，却先跑了一次角色识别"
    assert indexing.reindexed == []
