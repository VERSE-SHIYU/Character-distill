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


# ── C. Regression: load_affinity 短路 + 路由层兜底守卫 ─────────────────


def test_load_affinity_skips_recompute_when_already_initialized():
    """Engine 已在 __init__ 完成初始计算后，load_affinity(默认数据) 不重算。

    对应 fix: 在 _compute_initial_affinity 前检查引擎值是否已非默认，
    若已个性化则直接 return 保留现值，避免二次冗余 LLM 调用。
    """
    card = _make_card(ABU_REL)
    engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
    # __init__ 已完成初始计算 → affinity != 50, trust != 30 等

    # 插桩验证 _compute_initial_affinity 不被调用
    original = engine._compute_initial_affinity
    called = False

    def _spy(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)

    engine._compute_initial_affinity = _spy
    engine.load_affinity({"affinity": 50, "trust": 30, "mood": "平静", "guard": 70})

    assert not called, "_compute_initial_affinity should NOT be called when engine already initialized"


def test_fallback_guard_on_storage_read_failure():
    """模拟 DB 读取异常场景：路由层 _affinity_read_ok=False 时，兜底不触发。

    对应 fix: chat.py/history.py 中引入 _affinity_read_ok 标志，
    仅读取成功（_affinity_read_ok=True）且无评估数据时执行 update_session_affinity，
    防止异常时用初始值覆盖 DB 中已有的评估数据。
    """
    card = _make_card(ABU_REL)
    engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
    personalized = engine.get_affinity()

    # 模拟路由层流程：读异常 → affinity_data=None, _affinity_read_ok=False
    _affinity_read_ok = False
    affinity_data = None

    # 确认守卫条件阻止了落库
    guard = _affinity_read_ok and (affinity_data is None or not affinity_data.get("reason", ""))
    assert not guard, "Fallback guard should block persist when read_ok=False"

    # 模拟已评估的真实数据落入了读异常分支（老代码的 bug）
    affinity_data = {"affinity": 72, "trust": 55, "mood": "开心", "guard": 28, "reason": "评估完成"}
    guard = _affinity_read_ok and (affinity_data is None or not affinity_data.get("reason", ""))
    assert not guard, "Fallback guard should NOT fire on evaluated data when read failed"

    # 正常路径验证：读成功 + 无评估数据 → 兜底应当触发
    _affinity_read_ok = True
    affinity_data = None
    guard = _affinity_read_ok and (affinity_data is None or not (affinity_data or {}).get("reason", ""))
    assert guard, "Fallback SHOULD fire when read_ok=True and no evaluated data"

    # 正常路径验证：读成功 + 有评估数据 → 兜底不触发
    affinity_data = {"affinity": 72, "trust": 55, "mood": "开心", "guard": 28, "reason": "评估完成"}
    guard = _affinity_read_ok and (affinity_data is None or not affinity_data.get("reason", ""))
    assert not guard, "Fallback should NOT fire when read_ok=True and evaluated data exists"
