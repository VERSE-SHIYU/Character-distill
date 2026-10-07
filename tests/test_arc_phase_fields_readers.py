# -*- coding: utf-8 -*-
"""§6.2 调用点矩阵 E1–E6、E17（后端读者）—— 每个拼 prompt 的入口都只读阶段 k 的版本。"""

from __future__ import annotations

from core.card_layers import set_path
from core.schema import ArcPhase, CharacterArc, CharacterCard, Relationship, PhaseAttitude


def make_card(n_phases: int = 2, fingerprint: str = "fp") -> CharacterCard:
    phases = [ArcPhase(label=f"P{i + 1}", state=f"状态{i + 1}", start=i * 10)
              for i in range(n_phases)]
    return CharacterCard(
        name="甲", identity="身份", background="背景",
        character_arc=CharacterArc(axis="从A到B", phases=phases,
                                   source_fingerprint=fingerprint),
    )


def set_overlay(card: CharacterCard, idx: int, path: str, value) -> None:
    set_path(card.character_arc.phases[idx].overlay, path, value)


# ── E1 卡片层 / 扩展层 ─────────────────────────────────────────────────────
def test_ctx_layers_projected():
    from core.arc_view import project_card
    from core.context_engine import ContextEngine

    c = make_card(2)
    c.personality_traits = ["全程"]
    set_overlay(c, 0, "personality_traits", ["早期"])
    set_overlay(c, 1, "personality_traits", ["后期"])
    set_overlay(c, 0, "key_memories", ["早忆"])

    eng = ContextEngine(project_card(c, 1)[0], rag=None, storage=None)
    text = eng._build_card_core() + eng._build_card_ext()
    assert "早期" in text and "早忆" in text
    assert "后期" not in text


# ── E2 心理注入 ────────────────────────────────────────────────────────────
def test_psyche_projected():
    from core.chat_engine import ChatEngine

    c = make_card(2)
    c.psyche.triggers = ["顶层雷"]
    set_overlay(c, 0, "psyche.triggers", ["早期雷"])
    set_overlay(c, 1, "psyche.triggers", ["后期雷"])

    eng = ChatEngine(None, None, c, arc_phase=1, storage=None,
                     session_id="s", is_new_session=True)
    block = eng._build_affinity_persona_block()
    assert "早期雷" in block and "后期雷" not in block


# ── E3 认知注入 ────────────────────────────────────────────────────────────
def test_cognitive_projected():
    from core.chat_engine import ChatEngine

    c = make_card(2)
    c.cognitive.knowledge_scope = "顶层"
    set_overlay(c, 0, "cognitive.knowledge_scope", "早期")
    set_overlay(c, 1, "cognitive.knowledge_scope", "后期")

    eng = ChatEngine(None, None, c, arc_phase=1, storage=None,
                     session_id="s", is_new_session=True)
    block = eng._build_cognitive_block()
    assert "顶层；早期" in block and "后期" not in block


# ── E4 关系口径 ────────────────────────────────────────────────────────────
def test_relationship_note_projected():
    from core.chat_engine import ChatEngine

    c = make_card(2)
    c.relationships = [Relationship(
        target="乙", relation="同窗", attitude="平淡", note="顶层口径",
        phase_attitudes=[PhaseAttitude(phase=1, attitude="平淡", note="普通同学"),
                         PhaseAttitude(phase=2, attitude="亲密", note="生死之交")],
    )]
    eng = ChatEngine(None, None, c, arc_phase=2, storage=None,
                     session_id="s", is_new_session=True)
    assert eng.card.relationships[0].note == "生死之交"


# ── E5 好感评估 ────────────────────────────────────────────────────────────
def test_affinity_reads_projected():
    from core.affinity_service import AffinityService
    from core.arc_view import project_card

    c = make_card(2)
    c.psyche.triggers = ["顶层雷"]
    set_overlay(c, 0, "psyche.triggers", ["早期雷"])
    set_overlay(c, 1, "psyche.triggers", ["后期雷"])

    prompt = AffinityService().build_evaluation_prompt(
        project_card(c, 1)[0], "你好", "回应", "甲", "一般")
    assert "早期雷" in prompt and "后期雷" not in prompt


# ── E6 / U19 开场白 ────────────────────────────────────────────────────────
def test_opening_projected_fields():
    from core.arc_view import project_card
    from core.opening import build_opening_prompt

    c = make_card(2)
    c.speaking_style.catchphrases = ["顶层口癖"]
    set_overlay(c, 0, "speaking_style.catchphrases", ["早期口癖"])
    set_overlay(c, 1, "speaking_style.catchphrases", ["后期口癖"])

    prompt = build_opening_prompt(project_card(c, 1)[0], user_role="甲")
    assert "早期口癖" in prompt and "后期口癖" not in prompt


def test_opening_rejects_raw_card():
    import pytest
    from core.opening import build_opening_prompt

    with pytest.raises(TypeError, match="ProjectedCard"):
        build_opening_prompt(make_card(2))


# ── E17 引文核对 / 注入守卫覆盖 overlay ──────────────────────────────────────
def test_quotes_cover_overlay():
    from core.card_quotes import VERIFIED_FIELDS

    assert "character_arc.phases[].overlay.key_memories[]" in VERIFIED_FIELDS


# ── B3：顶层列表已满「取前 N」时，阶段 k 的条目仍进得了读者 ────────────────────
#
# 下面六处读者按 N 取前几条（`[:3]` / `[:2]`）。投影若把全程条目排在前，顶层条数一
# 到 N，阶段 k 才成立的人设就被整段切掉。每处都让顶层 ≥N 条、阶段 k 加一条独有标记，
# 断言标记仍出现在读者产出里（阶段 k 特有在前 + 全程在后）。

def test_b3_chat_soft_spots_reach_reader():
    """chat_engine `_build_affinity_persona_block`：软肋 `[:3]`。"""
    from core.chat_engine import ChatEngine

    c = make_card(2)
    c.psyche.soft_spots = ["顶软1", "顶软2", "顶软3"]      # 顶层已 3 条 = 读者上界
    set_overlay(c, 0, "psyche.soft_spots", ["阶段一软肋"])

    eng = ChatEngine(None, None, c, arc_phase=1, storage=None,
                     session_id="s", is_new_session=True)
    assert "阶段一软肋" in eng._build_affinity_persona_block()


def test_b3_chat_triggers_reach_reader():
    """chat_engine `_build_affinity_persona_block`：雷点 `[:3]`。"""
    from core.chat_engine import ChatEngine

    c = make_card(2)
    c.psyche.triggers = ["顶雷1", "顶雷2", "顶雷3"]
    set_overlay(c, 0, "psyche.triggers", ["阶段一雷"])

    eng = ChatEngine(None, None, c, arc_phase=1, storage=None,
                     session_id="s", is_new_session=True)
    assert "阶段一雷" in eng._build_affinity_persona_block()


def test_b3_dialogue_examples_reach_reader():
    """context_engine `_build_card_ext`：对话示范 `[:3]`。"""
    from core.arc_view import project_card
    from core.context_engine import ContextEngine

    c = make_card(2)
    c.dialogue_examples = ["顶对白1", "顶对白2", "顶对白3"]
    set_overlay(c, 0, "dialogue_examples", ["阶段一对白"])

    eng = ContextEngine(project_card(c, 1)[0], rag=None, storage=None)
    text = eng._build_card_core() + eng._build_card_ext()
    assert "阶段一对白" in text


def test_b3_affinity_values_reach_reader():
    """affinity_service `build_evaluation_prompt`：性格特征 `values[:3]`。"""
    from core.affinity_service import AffinityService
    from core.arc_view import project_card

    c = make_card(2)
    c.values = ["顶值1", "顶值2", "顶值3"]
    set_overlay(c, 0, "values", ["阶段一价值"])

    prompt = AffinityService().build_evaluation_prompt(
        project_card(c, 1)[0], "你好", "回应", "甲", "一般")
    assert "阶段一价值" in prompt


def test_b3_affinity_tensions_reach_reader():
    """affinity_service `build_evaluation_prompt`：内在矛盾 `inner_tensions[:2]`。"""
    from core.affinity_service import AffinityService
    from core.arc_view import project_card

    c = make_card(2)
    c.inner_tensions = ["顶矛1", "顶矛2"]                  # 顶层已 2 条 = 读者上界
    set_overlay(c, 0, "inner_tensions", ["阶段一矛盾"])

    prompt = AffinityService().build_evaluation_prompt(
        project_card(c, 1)[0], "你好", "回应", "甲", "一般")
    assert "阶段一矛盾" in prompt


def test_b3_opening_traits_reach_reader():
    """opening `build_opening_prompt`：性格 `personality_traits[:3]`。"""
    from core.arc_view import project_card
    from core.opening import build_opening_prompt

    c = make_card(2)
    c.personality_traits = ["顶性1", "顶性2", "顶性3"]
    set_overlay(c, 0, "personality_traits", ["阶段一性格"])

    prompt = build_opening_prompt(project_card(c, 1)[0], user_role="甲")
    assert "阶段一性格" in prompt


def test_guard_covers_overlay():
    from core.moderation.card_guard import leaf_texts

    c = make_card(2)
    set_overlay(c, 0, "key_memories", ["阶段一才有的记忆内容"])
    assert any("阶段一才有的记忆内容" in t for _, t in leaf_texts(c.model_dump()))


# ── E20 出卡：`selectable` 现算（B4）─────────────────────────────────────────
#
# `character_arc.selectable` 是派生值，不随卡落库。存量 `card_json` 里可能带着过期值
# （位置是后台作业补的，补完那张卡存下的仍是 false）或根本没这个键（旧卡）。出卡这一处
# 按**当前** phases / 指纹现算，故陈值与缺键都要能被纠正。

def _stored(card: CharacterCard, *, selectable=None) -> dict:
    """一张存下来的行：`card_json` 是字符串，可注入一个过期的 `selectable`。"""
    import json

    data = card.model_dump()
    if selectable is not None:
        data["character_arc"]["selectable"] = selectable
    return {"id": 1, "name": "甲", "card_json": json.dumps(data, ensure_ascii=False)}


def _arc_of(card_json) -> dict:
    import json

    return (json.loads(card_json) if isinstance(card_json, str) else card_json)["character_arc"]


def test_out_card_recomputes_stale_false_to_true():
    """E20：存量 `selectable:false`（位置补上前存下的陈值）在出卡时按当前 phases 纠正为 true。"""
    from core.card_out import out_card

    c = make_card(2)                       # 起点齐全 + 指纹 → 应当可选
    assert c.character_arc.has_positions() is True
    assert _arc_of(out_card(_stored(c, selectable=False))["card_json"])["selectable"] is True


def test_out_card_recomputes_stale_true_to_false():
    """E20：存量 `selectable:true` 但起点已缺 → 出卡时为 false（陈值双向都要纠正）。"""
    from core.card_out import out_card

    c = make_card(2)
    for p in c.character_arc.phases:       # 起点缺失 → 不可选
        p.start = None
    assert c.character_arc.has_positions() is False
    assert _arc_of(out_card(_stored(c, selectable=True))["card_json"])["selectable"] is False


def test_out_card_fills_missing_key():
    """E20：旧卡 `card_json` 没有这个键 → 出卡时补上现算的值。"""
    from core.card_out import out_card

    c = make_card(2)
    assert "selectable" not in _arc_of(_stored(c)["card_json"])   # 旧卡没这个键
    assert _arc_of(out_card(_stored(c))["card_json"])["selectable"] is True


def test_out_card_passes_everything_else_through():
    """E20：除 `selectable` 外整卡与行的其它列逐字透传（不含 selectable 后的两份 JSON 相等）。"""
    import json

    from core.card_out import out_card

    c = make_card(2)
    c.personality_traits = ["全程"]
    row = _stored(c, selectable=False)
    row["created_at"] = "2026-01-01"       # 行的其它列

    out = out_card(row)
    assert out["id"] == 1 and out["created_at"] == "2026-01-01"

    before = json.loads(row["card_json"])
    after = json.loads(out["card_json"])
    before["character_arc"].pop("selectable")
    after["character_arc"].pop("selectable")
    assert after == before


def test_out_card_accepts_dict_card_json():
    """E20：`card_json` 已是 dict（调用方解好的）也收，且同样现算。"""
    from core.card_out import out_card

    c = make_card(2)
    out = out_card({"id": 1, "card_json": c.model_dump()})
    assert out["card_json"]["character_arc"]["selectable"] is True


def test_out_card_without_card_json_is_untouched():
    """E20：没有 `card_json` 的行（如聚合行）原样返回。"""
    from core.card_out import out_card

    row = {"id": 1, "name": "甲"}
    assert out_card(row) == row


# ── E7 市场 @ 回复读投影卡（行为面，替换 MA15 的结构锁兜底）──────────────────
#
# 结构锁 S3 只能证明源码里有 `project_card(`；行为面要证明**拼进 system_prompt 的确实是
# 投影卡**（最后阶段才成立的性格在提示里、顶层没有的也在）。

def test_market_reply_reads_projected_card(monkeypatch):
    import deps
    import server
    from fastapi.testclient import TestClient
    import web.routers.market as M

    recorded: list[str] = []

    class _LLM:
        model = "stub"
        last_usage: dict = {}

        def preflight(self):
            return None

        def chat(self, system, messages, *a, **kw):
            recorded.append(system)
            return "收到"

    card = make_card(2)
    card.personality_traits = ["全程性格"]
    set_overlay(card, 1, "personality_traits", ["末阶段才有的性格"])
    row = {"text_id": "t1", "name": "甲", "visibility": "public",
           "author_username": "u", "card_json": card.model_dump_json()}

    class _Store:
        async def get_card_unscoped(self, card_id):
            return {**row, "id": card_id}

        async def add_ai_reply_comment(self, *a, **kw):
            return {"ok": True}

    async def _user_llm(*a, **kw):
        return _LLM()

    monkeypatch.setattr(deps, "get_user_llm", _user_llm)
    monkeypatch.setattr(M, "try_record_usage", lambda *a, **kw: None)

    from deps import get_storage
    from routers.auth import get_current_user

    app = server.app
    app.dependency_overrides[get_storage] = lambda: _Store()
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "u"}
    try:
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/api/market/card_src/comments/at-reply",
            json={"at_card_id": "card_at", "comment_content": "在吗"})
    finally:
        app.dependency_overrides.pop(get_storage, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    assert recorded and "末阶段才有的性格" in recorded[0], (
        "市场 @ 回复的 system_prompt 里没有最后阶段才成立的人设 —— 没走投影卡")


# ── E19 新会话开场变体读投影卡（行为面，替换 MA23 的结构锁兜底）───────────────

def test_opening_variation_reads_projected_card(monkeypatch):
    import asyncio

    import core.opening as opening
    from core.arc_view import ProjectedCard
    from core.text_manager import TextManager

    seen: dict = {}
    real = opening.build_variation_prompt

    def spy(card, **kw):
        seen["card"] = card
        return real(card, **kw)

    monkeypatch.setattr(opening, "build_variation_prompt", spy)

    card = make_card(2)
    card.first_message = "原始开场白"

    class _LLM:
        model = "stub"
        last_usage: dict = {}

        def preflight(self):
            return None

        def chat(self, *a, **kw):
            return "换个说法的开场白"

    class _Store:
        async def get_text_owned(self, text_id, user_id):
            return {"id": "t1", "content": "正文", "user_id": user_id}

        async def get_card_owned(self, card_id, user_id):
            return self.card

        async def list_cards(self, text_id, user_id):
            return [self.card]

        async def get_user_api_config(self, user_id):
            return {"embedding_key": "", "embedding_region": "cn"}

        async def get_session_owned(self, session_id, user_id):
            return {"id": session_id, "card_id": "c1", "user_role": "", "user_id": user_id}

        async def save_session(self, *a, **kw):
            return None

        async def save_card(self, card_id, text_id, name, card_json, user_id):
            return {"id": "c1"}

    store = _Store()
    store.card = {"id": "c1", "text_id": "t1", "name": card.name,
                  "card_json": card.model_dump_json()}
    tm = TextManager(lambda: store, None, _LLM(), {}, indexing_service=None,
                     memory_manager=None)

    async def _chars(*a, **kw):
        return [{"name": card.name, "aliases": []}]

    async def _guard(_card):
        from core.moderation.card_guard import GuardVerdict
        return GuardVerdict()

    tm._build_all_characters = _chars
    tm._guard_card = _guard
    tm.memory_for = lambda *a, **kw: None

    asyncio.run(tm.get_or_distill("t1", card.name, user_id="u1"))

    assert "card" in seen, "开场变体没走 core.opening.build_variation_prompt"
    assert isinstance(seen["card"], ProjectedCard), "变体提示词拿到的不是投影卡"
