"""Regression tests for _compute_initial_affinity and ChatEngine user_role propagation.

Background: start_session constructed ChatEngine without passing user_role,
so _compute_initial_affinity fell into the "if not user" stranger branch even
when the caller had selected a role present in the card's relationships.
"""

from core.chat_engine import ChatEngine
from core.schema import CharacterCard, Relationship


class _StubLLM:
    model = ""


def _make_card(*relationships: Relationship) -> CharacterCard:
    """Minimal CharacterCard with given relationships."""
    return CharacterCard(
        name="测试角色",
        relationships=list(relationships),
    )


# ── Shared fixtures ──────────────────────────────────────────────────────────

ABU_REL = Relationship(target="阿布", relation="队友", attitude="")
ENEMY_REL = Relationship(target="仇人甲", relation="仇人", attitude="")


# ── A. Pure function: _compute_initial_affinity ──────────────────────────────


class TestComputeInitialAffinity:
    """Direct coverage of the pure-function branches."""

    def test_empty_role_is_stranger(self):
        """user_role="" → stranger branch: affinity<=18, mood="警觉", inner_voice contains "不认识"."""
        card = _make_card(ABU_REL)
        engine = ChatEngine(_StubLLM(), None, card, card_id="t")
        affinity = engine.get_affinity()
        assert affinity["affinity"] <= 18, affinity
        assert affinity["mood"] == "警觉", affinity
        assert "不认识" in affinity.get("inner_voice", ""), affinity

    def test_close_relationship(self):
        """user_role matches a close relationship → affinity>=55, stage != "陌生", mood != "警觉"."""
        card = _make_card(ABU_REL)
        engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
        affinity = engine.get_affinity()
        assert affinity["affinity"] >= 55, affinity
        assert affinity["stage"] != "陌生", affinity
        assert affinity["mood"] != "警觉", affinity

    def test_substring_match(self):
        """target is a substring of user_role → still matches close relationship."""
        card = _make_card(ABU_REL)
        engine = ChatEngine(_StubLLM(), None, card, card_id="t",
                            user_role="吴庚霖（炎亚纶/阿布）")
        affinity = engine.get_affinity()
        assert affinity["affinity"] >= 55, affinity

    def test_hostile_relationship(self):
        """relation="仇人" → mood="敌意", affinity<=15."""
        card = _make_card(ENEMY_REL)
        engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="仇人甲")
        affinity = engine.get_affinity()
        assert affinity["mood"] == "敌意", affinity
        assert affinity["affinity"] <= 15, affinity

    def test_unknown_role_falls_back_to_stranger(self):
        """user_role not in relationships → stranger fallback, affinity<=18."""
        card = _make_card(ABU_REL)
        engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="路人甲")
        affinity = engine.get_affinity()
        assert affinity["affinity"] <= 18, affinity


# ── B. Regression: ChatEngine propagates user_role to initial affinity ───────


class TestChatEnginePropagatesUserRole:
    """If someone omits user_role in ChatEngine() construction, these fail."""

    def test_with_user_role_shows_familiar(self):
        """user_role="阿布" + close rel → familiar stage (not stranger)."""
        card = _make_card(ABU_REL)
        engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
        affinity = engine.get_affinity()
        assert affinity["stage"] != "陌生", affinity
        assert affinity["mood"] != "警觉", affinity
        assert affinity["affinity"] >= 55, affinity

    def test_without_user_role_shows_stranger(self):
        """user_role="" (missing) → stranger stage."""
        card = _make_card(ABU_REL)
        engine = ChatEngine(_StubLLM(), None, card, card_id="t")
        affinity = engine.get_affinity()
        assert affinity["stage"] == "陌生", affinity
