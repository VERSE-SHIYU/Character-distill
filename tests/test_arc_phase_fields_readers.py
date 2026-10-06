# -*- coding: utf-8 -*-
"""§6.2 调用点矩阵 E1–E6、E17（后端读者）—— 每个拼 prompt 的入口都只读阶段 k 的版本。"""

from __future__ import annotations

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
    card.character_arc.phases[idx].overlay[path] = value


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
