# -*- coding: utf-8 -*-
"""开聊时选阶段：蒸馏侧记忆归位 / 分组调序 / 会话列 / 导出 / 接线。

§5 里不属 `test_arc_view.py`（纯投影）、`test_arc_positions.py`（坐标与检索）、
`test_arc_phase_select_locks.py`（结构锁）的其余条目都在这：
U8（记忆按位置分发 + 起点/指纹）、U9（G4→G6）、U10（会话 arc_phase 列）、
U16（导出带阶段记忆）、U20（G5 等 G6）、U21/E10（投影只在构造处）、
E1（start_session 写阶段）、E2（自动重建恢复阶段）、M15（检索带上界）。
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import threading
import uuid
from pathlib import Path

from core.card_draft import card_from_draft
from core.chat_engine import ChatEngine
from core.export import export_tavern_json
from core.schema import FORMAT_GROUPS, POST_FORMAT_FIELDS, CharacterCard, RetrievalWindow

_REPO = Path(__file__).resolve().parent.parent

# 三段独特 token：normalize 后 len 15，三字起点 0 / 5 / 10（同 test_card_draft）。
_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q1, _Q3 = "开头甲甲甲", "结尾丙丙丙"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── U8 记忆按位置分发；起点与指纹写入；整卡跳过不写起点 ──────────────────────

def _mem_draft(rows):
    return {
        "name": "孔乙己",
        "character_arc": {"axis": "从死要面子到不再分辩", "phases": [
            {"label": "死要面子", "state": "断腿之前", "anchor": ""},
            {"label": "不再分辩", "state": "断腿之后", "anchor": _Q3},
        ]},
        "key_memories": rows,
        "situation_behaviors": [],
    }


def _mem(memory, *occ):
    return {"memory": memory, "occurrences": [{"phase": p, "quote": q} for p, q in occ]}


def test_u8_memories_dispatched_by_phase_with_starts_and_fingerprint():
    from core.fingerprint import content_fingerprint

    draft = _mem_draft([
        _mem("全程都有的事", (1, _Q1), (2, _Q3)),
        _mem("只在早期", (1, _Q1)),
        _mem("只在后来", (2, _Q3)),
    ])
    card = card_from_draft(draft, _SRC)

    assert card.key_memories == ["全程都有的事"], "全程成立的记忆没留在顶层"
    assert [p.overlay.get("key_memories") for p in card.character_arc.phases] == [
        ["只在早期"], ["只在后来"]]
    assert [p.start for p in card.character_arc.phases] == [0, 10], (
        f"阶段起点不是规范化坐标：{[p.start for p in card.character_arc.phases]}")
    assert card.character_arc.source_fingerprint == content_fingerprint(_SRC)


def test_u8_skipped_card_writes_no_starts():
    draft = _mem_draft([_mem("只在早期", (1, _Q1))])
    draft["character_arc"]["phases"][1]["anchor"] = "查不到的锚点"
    card = card_from_draft(draft, _SRC)
    assert [p.start for p in card.character_arc.phases] == [None, None], (
        "整卡跳过位置检查时仍写了起点")


# ── U9 分组：key_memories 从 G4 挪到 G6 ─────────────────────────────────────

def test_u9_key_memories_moved_from_g4_to_g6():
    assert FORMAT_GROUPS["G4"] == ("psyche",), f"G4 还挂着别的字段：{FORMAT_GROUPS['G4']}"
    assert "key_memories" in FORMAT_GROUPS["G6"]
    assert "key_memories" not in FORMAT_GROUPS["G4"]

    from core.card_draft import draft_schema
    ref = draft_schema("G6")["properties"]["key_memories"]["items"]["$ref"]
    assert ref.endswith("/DraftMemory"), f"G6 的记忆不是草稿记忆类型：{ref}"


def test_u9_groups_union_equals_card_fields():
    buckets = [set(f) for f in FORMAT_GROUPS.values()] + [set(POST_FORMAT_FIELDS)]
    assert set().union(*buckets) == set(CharacterCard.model_fields)


# ── U10 会话 arc_phase 列：save_session 不覆盖；所有会话 SELECT 都返回它 ──────

def _sqlite_store(tmp_path):
    from storage.sqlite_store import SQLiteStore
    return SQLiteStore(str(tmp_path / f"{uuid.uuid4().hex}.db"))


def test_u10_save_session_does_not_clobber_arc_phase(tmp_path):
    store = _sqlite_store(tmp_path)
    uid = f"usr_{uuid.uuid4().hex[:12]}"
    tid, cid, sid = (f"t_{uuid.uuid4().hex}", f"c_{uuid.uuid4().hex}", uuid.uuid4().hex[:12])
    _run(store.save_text(tid, "s.txt", "正文", user_id=uid))
    _run(store.save_card(cid, tid, "张三", json.dumps({"name": "张三"}), user_id=uid))
    _run(store.save_session(sid, cid, "旧的", "", uid))
    _run(store.set_session_arc_phase(sid, uid, 2))
    _run(store.save_session(sid, cid, "新的", "", uid))   # 聊天中改身份

    row = _run(store.get_session_owned(sid, uid))
    assert row["arc_phase"] == 2, f"save_session 把阶段覆盖了：{row.get('arc_phase')}"
    assert row["user_role"] == "新的"


def test_u10_every_session_select_returns_arc_phase():
    """每个返回 `s.user_role` 的会话 SELECT，都必须同时返回 `s.arc_phase`（C14）。"""
    for rel in ("storage/postgres_store.py", "storage/sqlite_store.py"):
        text = (_REPO / rel).read_text(encoding="utf-8")
        blocks = re.findall(r"SELECT\b.*?FROM\s+sessions", text, re.S | re.I)
        selects = [b for b in blocks if "s.user_role" in b]
        assert len(selects) >= 4, f"{rel} 只找到 {len(selects)} 处会话 SELECT"
        missing = [b[:60] for b in selects if "s.arc_phase" not in b]
        assert not missing, f"{rel} 有会话 SELECT 没返回 arc_phase：{missing}"


def test_u10_storage_exposes_set_session_arc_phase():
    from storage.base import StorageBase
    assert hasattr(StorageBase, "set_session_arc_phase"), "基类没有声明 set_session_arc_phase"


# ── U16 导出带阶段记忆 ──────────────────────────────────────────────────────

def test_u16_export_includes_phase_memories():
    card = card_from_draft(_mem_draft([
        _mem("全程都有的事", (1, _Q1), (2, _Q3)),
        _mem("只在早期", (1, _Q1)),
        _mem("只在后来", (2, _Q3)),
    ]), _SRC)
    blob = export_tavern_json(card)
    assert "只在早期" in blob and "只在后来" in blob, "阶段记忆没进导出"
    assert "死要面子" in blob or "不再分辩" in blob, "阶段记忆没标出所属阶段"


# ── U20 分组蒸馏：依赖组（G2/G3/G4）等 G6，其余组先并行 ──────────────────────

def test_u20_dependent_groups_wait_for_g6(monkeypatch):
    from test_distiller_routing import (
        _FakeAsyncClient, _SAMPLE_RELATION_DETAILS, _format_group_of, _group_reply)
    from core.distiller import Distiller, PHASE_DEPENDENT_GROUPS

    barrier = threading.Barrier(len(PHASE_DEPENDENT_GROUPS), timeout=2)   # 依赖组一起等破障
    events: list[str] = []
    dep_systems: list[str] = []

    class _LLM:
        last_usage = None
        model = "m"

        def _make_async_client(self):
            return _FakeAsyncClient()

        async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
            return ("片段分析", {"prompt_tokens": 1, "completion_tokens": 1})

        def chat_stream_long(self, system, messages, max_tokens=None, **kw):
            if "你正在整合关于" in system:
                yield "合并结果"
                return {"prompt_tokens": 1, "completion_tokens": 1}
            if "只产出这几个人物与主角的关系" in system:   # 关系分批调用
                yield json.dumps(_SAMPLE_RELATION_DETAILS, ensure_ascii=False)
                return {"prompt_tokens": 1, "completion_tokens": 1}
            group = _format_group_of(system)
            if group in PHASE_DEPENDENT_GROUPS:
                dep_systems.append(system)
                barrier.wait()          # 串行实现等不齐 → 破障
            events.append(group)
            yield _group_reply(group)
            return {"prompt_tokens": 1, "completion_tokens": 1}

    d = Distiller(llm=_LLM(), config_path=None)
    d._longctx_threshold = 0
    d._chunk_size = 3000
    frames = list(d.distill_incremental_stream("AB" * (1500 * 50), "AB", aliases=[], text_type="story"))

    cards = [f for f in frames if isinstance(f, str)]
    assert cards, f"没出卡：{[f for f in frames if isinstance(f, dict) and 'error' in f]}"
    assert events.index("G6") < min(events.index(g) for g in PHASE_DEPENDENT_GROUPS), (
        f"依赖组没等 G6：{events}")
    assert dep_systems and all("阶段一" in s for s in dep_systems), (
        f"依赖组的提示词没带上 G6 的阶段列表：{dep_systems[:1]}")


# ── U21 / E10 投影只在构造处：ChatEngine 拿到的就是投影卡 ────────────────────

def _phase_card(relationships=None):
    return CharacterCard.model_validate({
        "name": "甲",
        "key_memories": ["顶层"],
        "relationships": relationships or [],
        "character_arc": {"axis": "从冷到热", "source_fingerprint": "FP", "phases": [
            {"label": "冷", "state": "起初", "memories": ["只在早期的事"], "start": 0},
            {"label": "热", "state": "后来", "memories": ["后来的事"], "start": 10},
        ]},
    })


class _LLM:
    model = "stub"
    last_usage: dict = {}


def _engine(card, arc_phase=None, **kw):
    return ChatEngine(_LLM(), None, card, card_id="c", storage=None,
                      session_id="s", is_new_session=False, arc_phase=arc_phase, **kw)


def test_u21_engine_card_is_the_projected_card():
    card = _phase_card()
    eng = _engine(card, 1)
    assert eng.card is not card, "引擎拿的是原卡，没投影"
    assert [p.label for p in eng.card.character_arc.phases] == ["冷"]
    assert eng.card.key_memories == ["顶层", "只在早期的事"], (
        f"投影卡没把阶段 1 的记忆并进顶层：{eng.card.key_memories}")


def test_u21_no_arc_phase_projects_to_last_phase():
    eng = _engine(_phase_card(), None)
    assert [p.label for p in eng.card.character_arc.phases] == ["冷", "热"]
    assert eng.card.key_memories == ["顶层", "只在早期的事", "后来的事"]


def test_e10_relationship_consumer_reads_the_phase_attitude():
    # 三阶段卡、选阶段 2（k=2 < n=3）：消费方读到的应是 ≤2 的最新态度「中」。
    # （k=n 时按 U17/M29 一律用顶层可编辑的 attitude，不是这条用例要测的路。）
    rels = [
        {"target": "乙", "relation": "友", "attitude": "晚", "phase_attitudes": [
            {"phase": 1, "attitude": "早"}, {"phase": 2, "attitude": "中"},
            {"phase": 3, "attitude": "晚"}]},
    ]
    card = CharacterCard.model_validate({
        "name": "甲", "key_memories": ["顶层"], "relationships": rels,
        "character_arc": {"axis": "从冷到热", "source_fingerprint": "FP", "phases": [
            {"label": "冷", "state": "起初", "start": 0},
            {"label": "温", "state": "中间", "start": 10},
            {"label": "热", "state": "后来", "start": 20},
        ]},
    })
    eng = _engine(card, 2)
    by = {r.target: r for r in eng.card.relationships}
    assert by["乙"].attitude == "中", f"关系消费方读到的不是阶段 2 的态度：{by['乙'].attitude}"


# ── E1 /start_session 写阶段 ────────────────────────────────────────────────

def test_e1_start_session_wires_arc_phase():
    src = (_REPO / "web" / "routers" / "distill.py").read_text(encoding="utf-8")
    assert "set_session_arc_phase" in src, "/start_session 没写 arc_phase"
    from routers.distill import StartSessionRequest
    assert "arc_phase" in StartSessionRequest.model_fields


# ── E2 自动重建恢复阶段（chat.py:199 的 session_identity）─────────────────────

_PHASE_CARD_JSON = json.dumps({
    "name": "张三",
    "key_memories": ["顶层"],
    "character_arc": {"axis": "从A到B", "source_fingerprint": "FP", "phases": [
        {"label": "早期", "state": "初始", "memories": ["只在早期的事"], "start": 0},
        {"label": "后来", "state": "之后", "memories": ["后来的事"], "start": 10},
    ]},
}, ensure_ascii=False)


def test_e2_rebuild_restores_arc_phase(tmp_path, monkeypatch):
    from routers.chat import _ensure_session
    from test_session_identity_injection import _MemMgr, _NoRAG, _text_manager
    import deps

    store = _sqlite_store(tmp_path)
    uid = f"usr_{uuid.uuid4().hex[:12]}"
    tid, cid, sid = (f"t_{uuid.uuid4().hex}", f"c_{uuid.uuid4().hex}", uuid.uuid4().hex[:12])
    _run(store.save_text(tid, "src.txt", "正文", user_id=uid))
    _run(store.save_card(cid, tid, "张三", _PHASE_CARD_JSON, user_id=uid))
    _run(store.save_session(sid, cid, "角色A", "", uid))
    _run(store.set_session_arc_phase(sid, uid, 1))

    sessions: dict = {}

    async def _fake_user_llm(*_a, **_kw):
        return _LLM()

    monkeypatch.setattr(deps, "_storage", store)
    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_text_manager", lambda llm=None: _text_manager(store, sessions))

    session = _run(_ensure_session(sid, store, sessions, uid))
    engine = session["engine"]
    assert getattr(engine, "arc_phase", None) == 1, (
        f"自动重建没恢复阶段：arc_phase={getattr(engine, 'arc_phase', None)!r}")
    assert [p.label for p in engine.card.character_arc.phases] == ["早期"], (
        f"重建后的引擎没拿到投影卡：{[p.label for p in engine.card.character_arc.phases]}")


# ── M15 检索带上界：_scene_items 把 before 与指纹交给检索 ────────────────────

def test_m15_scene_items_passes_the_bound_and_fingerprint():
    from core import arc_view as av
    from core.context_engine import ContextEngine

    seen: dict = {}

    class _RAG:
        def query_with_emotion_ex(self, query, **kw):
            seen.update(kw)
            return []

    card = _phase_card()
    view = av.arc_view(card, 1)
    ce = ContextEngine(card=card, rag=_RAG(), storage=None, arc_view=view)
    ce._scene_items("某段查询")

    assert seen.get("window") == RetrievalWindow(10, "FP"), (
        f"检索没带时间窗（上界 + 正文指纹）：{seen.get('window')!r}")


# ── E3 恢复存档：history.py 的重建路恢复身份与阶段 ───────────────────────────

def test_resume_restores_identity(tmp_path, monkeypatch):
    from routers.history import resume_session
    from core.chat_engine import ChatEngine
    from test_session_identity_injection import _StubLLM, _text_manager
    import deps

    store = _sqlite_store(tmp_path)
    uid = f"usr_{uuid.uuid4().hex[:12]}"
    tid, cid, sid = (f"t_{uuid.uuid4().hex}", f"c_{uuid.uuid4().hex}", uuid.uuid4().hex[:12])
    _run(store.save_text(tid, "src.txt", "正文", user_id=uid))
    _run(store.save_card(cid, tid, "张三", _PHASE_CARD_JSON, user_id=uid))
    _run(store.save_session(sid, cid, "存档的身份", "", uid))
    _run(store.set_session_arc_phase(sid, uid, 1))

    sessions: dict = {}

    async def _fake_user_llm(*_a, **_kw):
        return _StubLLM()

    monkeypatch.setattr(deps, "_storage", store)
    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_text_manager", lambda llm=None: _text_manager(store, sessions))
    # 重逢问候会调模型：本用例只验身份恢复，置空绕过。
    monkeypatch.setattr(ChatEngine, "generate_reunion_greeting", lambda self, *a, **k: "")

    class _Body:
        voice_mode = False

    _run(resume_session(sid, _Body(), None, {"id": uid}, store, sessions))

    engine = sessions[sid]["engine"]
    assert engine.user_role == "存档的身份", f"恢复存档没恢复身份：{engine.user_role!r}"
    assert getattr(engine, "arc_phase", None) == 1, (
        f"恢复存档没恢复阶段：arc_phase={getattr(engine, 'arc_phase', None)!r}")
    assert [p.label for p in engine.card.character_arc.phases] == ["早期"], (
        f"恢复后的引擎没拿到投影卡：{[p.label for p in engine.card.character_arc.phases]}")


# ── E4 agent 工具 search_scenes：走 _scene_items 的那条路也带上界 ─────────────

def test_agent_search_respects_window():
    from core import arc_view as av
    from core.context_engine import ContextEngine

    seen: dict = {}

    class _RAG:
        def query_with_emotion_ex(self, query, **kw):
            seen.update(kw)
            return []

    card = _phase_card()
    view = av.arc_view(card, 1)
    ce = ContextEngine(card=card, rag=_RAG(), storage=None, arc_view=view)
    ce._retrieve_scenes_ex("某段查询")

    assert seen.get("window") == RetrievalWindow(10, "FP"), (
        f"agent 检索没带时间窗：{seen.get('window')!r}")


# ── E5 / E11 群聊：无阶段列 → k=n 投影，无上界 ──────────────────────────────

_GROUP_PHASE = {"axis": "从冷到热", "source_fingerprint": "FP", "phases": [
    {"label": "冷", "state": "起初", "start": 0},
    {"label": "热", "state": "后来", "start": 10},
]}


def _group_card(name):
    return json.dumps({"name": name, "character_arc": copy.deepcopy(_GROUP_PHASE)},
                      ensure_ascii=False)


def _group_engines(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.auth import get_current_user
    from routers.group import router as group_router, get_group_sessions
    from test_session_identity_injection import _MemMgr, _ReplyLLM, _NoopRAG
    import routers.group as group_mod
    import deps

    store = _sqlite_store(tmp_path)
    uid = f"usr_{uuid.uuid4().hex[:12]}"

    async def _fake_user_llm(*_a, **_kw):
        return _ReplyLLM()

    monkeypatch.setattr(deps, "get_llm", _ReplyLLM)
    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
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
        "id": uid, "username": "testuser", "role": "user"}
    client = TestClient(app)

    tid = f"txt_{uuid.uuid4().hex}"
    _run(store.save_text(tid, "src.txt", "content", user_id=uid))
    cards = []
    for name in ("甲", "乙"):
        cid = f"card_{uuid.uuid4().hex}"
        _run(store.save_card(cid, tid, name, _group_card(name), user_id=uid))
        cards.append(cid)
    r = client.post("/api/group/create", json={
        "card_ids": cards, "user_persona_type": "stranger", "user_persona_name": "路人"})
    assert r.status_code == 200, f"建群失败：{r.status_code} {r.text[:200]}"
    return get_group_sessions()[r.json()["group_id"]]


def test_group_uses_last_phase(tmp_path, monkeypatch):
    group = _group_engines(tmp_path, monkeypatch)
    assert group.engines, "没造出群引擎，用例没验到东西"
    for engine in group.engines.values():
        assert engine.arc_phase is None, "群聊没有阶段列，引擎的阶段应为 None（k=n）"
        assert [p.label for p in engine.card.character_arc.phases] == ["冷", "热"], (
            f"群聊应投影到 k=n（全部阶段），实得 {[p.label for p in engine.card.character_arc.phases]}")


def test_group_engine_projected(tmp_path, monkeypatch):
    from core.chat_engine import ChatEngine

    passed_cards: list = []
    real_init = ChatEngine.__init__

    def _spy(self, *a, **kw):
        passed_cards.append(a[2] if len(a) > 2 else kw.get("card"))
        return real_init(self, *a, **kw)

    monkeypatch.setattr(ChatEngine, "__init__", _spy)
    group = _group_engines(tmp_path, monkeypatch)

    assert passed_cards, "群引擎构造没被观测到，用例没验到东西"
    for engine in group.engines.values():
        assert not any(engine.card is c for c in passed_cards), (
            "群引擎拿的是原卡对象，没投影")
        assert [p.label for p in engine.card.character_arc.phases] == ["冷", "热"]


# ── E6 新会话：text_manager 的两处新会话默认为 None → k=n ────────────────────

def test_new_session_defaults_last(tmp_path):
    from test_session_identity_injection import _StubLLM, _NoRAG, _MemMgr
    from core.text_manager import TextManager

    store = _sqlite_store(tmp_path)
    sessions: dict = {}
    tm = TextManager(lambda: store, None, _StubLLM(), sessions,
                     indexing_service=_NoRAG(), memory_manager=_MemMgr())

    sid = tm._create_session(_phase_card(), user_id="u1", memory=None)
    engine = sessions[sid]["engine"]
    assert engine.arc_phase is None, "新会话默认应为 None（k=n），不该自己挑一个阶段"
    assert [p.label for p in engine.card.character_arc.phases] == ["冷", "热"], (
        "新会话应投影到 k=n（全部阶段）")


# ── E7 开场白：生成处按阶段投影（prompt 才会含当前阶段）─────────────────────

def test_opening_uses_phase():
    src = (_REPO / "web" / "routers" / "distill.py").read_text(encoding="utf-8")
    assert "project_card(" in src, (
        "开场白没有按阶段投影卡片 —— 提示里不会含当前阶段（S14：投影只此两处）")


# ── E9 存卡调度：save_distilled_card 带起点的卡调度补位置 ────────────────────

def test_save_card_schedules_positions():
    from test_arc_positions import _Idx, _Store, _tm, _save, _card_with_positions

    idx = _Idx()
    _save(_tm(idx, _Store()), _card_with_positions())
    assert idx.scene_calls, "根本没调度场景索引"
    assert idx.scene_calls[-1].get("need_positions") is True, (
        f"存卡没给带起点的卡调度补位置：{idx.scene_calls[-1]}")
