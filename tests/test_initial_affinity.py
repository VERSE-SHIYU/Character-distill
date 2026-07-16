"""Regression tests for _compute_initial_affinity and ChatEngine user_role propagation.

Background: start_session constructed ChatEngine without passing user_role,
so _compute_initial_affinity fell into the "if not user" stranger branch even
when the caller had selected a role present in the card's relationships.
"""

from unittest.mock import MagicMock

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


# ── C. Regression: affinity_state 序列化 + 存量升级路径 ─────────────


def test_from_persist_error_handling():
    """from_persist 对空/非法/合法输入均不抛异常。"""
    from core.affinity_service import AffinityService

    # 空字符串 → None
    assert AffinityService.from_persist("") is None
    # 非 JSON → None
    assert AffinityService.from_persist("not json") is None
    # 合法 JSON → dict
    result = AffinityService.from_persist('{"affinity":65,"inner_voice":"你好"}')
    assert isinstance(result, dict)
    assert result["affinity"] == 65
    assert result["inner_voice"] == "你好"


def test_load_affinity_initialized_flag_skips_recompute():
    """load_affinity(parsed_data, initialized=True) 不调用 _compute_initial_affinity。

    新架构：affinity_initialized=1 的会话 → 直接 from_persist 恢复，绝不重算。
    """
    card = _make_card(ABU_REL)
    engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
    original_aff = engine.get_affinity()

    # 插桩验证 _compute_initial_affinity 不被调用
    original = engine._compute_initial_affinity
    called = False

    def _spy(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)

    engine._storage = None  # prevent _save_affinity_state from being called
    engine._compute_initial_affinity = _spy
    # 模拟 affinity_initialized=1 的数据（from_persist 格式）
    engine.load_affinity(original_aff, initialized=True)

    assert not called, "_compute_initial_affinity should NOT be called when initialized=True"


def test_load_affinity_legacy_upgrade():
    """旧格式已评估数据（affinity=72, reason 含 inner_voice）→ 加载 + 升级写入。

    存量行 affinity_initialized=0 但实际有评估 → 启发式判断+load → _save_affinity_state 升级。
    """
    card = _make_card(ABU_REL)
    engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
    engine._session_id = "legacy_upgrade"
    engine._storage = MagicMock()

    old_data = {
        "affinity": 72, "trust": 55, "mood": "开心", "guard": 28,
        "reason": '{"inner_voice":"测试","mood_emoji":"😊","user_catchwords":[]}',
    }
    engine.load_affinity(old_data)

    assert engine._affinity == 72
    assert engine._trust == 55
    assert engine._mood == "开心"
    assert engine._inner_voice == "测试"
    # 升级落库被调用
    engine._storage.save_affinity_state.assert_called_once()
    # state_json 包含关键字段
    state_json = engine._storage.save_affinity_state.call_args[0][1]
    assert '"inner_voice"' in state_json
    assert '"affinity": 72' in state_json or '"affinity":72' in state_json


def test_load_affinity_uninitialized_computes_and_saves():
    """load_affinity(默认数据) → 计算初始值并落库。

    纯默认值行 → _compute_initial_affinity 被调用一次。
    """
    card = _make_card(ABU_REL)
    engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
    engine._session_id = "test_sesh"
    engine._storage = MagicMock()

    called = False
    original = engine._compute_initial_affinity

    def _spy(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)

    engine._compute_initial_affinity = _spy
    engine.load_affinity({"affinity": 50, "trust": 30, "mood": "平静", "guard": 70})

    assert called, "_compute_initial_affinity SHOULD be called on default data"


# ── D. Roundtrip: to_persist → from_persist → load（新格式）────────


def test_affinity_service_roundtrip():
    """全 11 字段非默认值 → to_persist → from_persist → 新实例 load → 逐字段一致。"""
    from core.affinity_service import AffinityService

    # 1. 构造非默认状态（affinity=80 → stage="亲近"/"🥰"）
    svc = AffinityService()
    svc.affinity = 80
    svc.trust = 60
    svc.mood = "心软"
    svc.guard = 20
    svc.inner_voice = "这家伙今天有点不一样……"
    svc.mood_emoji = "🫣"
    svc.stage = "亲近"
    svc.stage_emoji = "🥰"
    svc.user_catchwords = ["好吧", "随便你"]
    svc.affinity_reason = '{"legacy":"compat"}'

    # 2. 序列化 → 反序列化
    raw = svc.to_persist()
    parsed = AffinityService.from_persist(raw)
    assert parsed is not None

    # 3. 新实例 load
    svc2 = AffinityService()
    svc2.load(parsed)

    # 4. 逐字段断言
    assert svc2.affinity == 80
    assert svc2.trust == 60
    assert svc2.mood == "心软"
    assert svc2.guard == 20
    assert svc2.inner_voice == "这家伙今天有点不一样……"
    assert svc2.mood_emoji == "🫣"
    assert svc2.stage == "亲近"
    assert svc2.stage_emoji == "🥰"
    assert svc2.user_catchwords == ["好吧", "随便你"]
    assert svc2.affinity_reason == '{"legacy":"compat"}'


def test_affinity_engine_roundtrip():
    """engine 全字段 → _save_affinity_state → 新 engine load_affinity(initialized=True) → get_affinity 一致。"""
    from core.affinity_service import AffinityService

    card = _make_card(ABU_REL)
    engine = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
    engine._session_id = "roundtrip_engine"
    engine._storage = MagicMock()

    # 设非默认值
    engine._affinity = 80
    engine._trust = 60
    engine._mood = "心软"
    engine._guard = 20
    engine._inner_voice = "这家伙今天有点不一样……"
    engine._mood_emoji = "🫣"
    engine._affinity_service.user_catchwords = ["好吧", "随便你"]

    # 保存前快照
    before = engine.get_affinity()

    # 模拟持久化
    engine._save_affinity_state()
    assert engine._storage.save_affinity_state.called
    state_json = engine._storage.save_affinity_state.call_args[0][1]

    # 新引擎加载
    parsed = AffinityService.from_persist(state_json)
    assert parsed is not None

    engine2 = ChatEngine(_StubLLM(), None, card, card_id="t", user_role="阿布")
    engine2._session_id = "roundtrip_engine"
    engine2._storage = MagicMock()
    engine2.load_affinity(parsed, initialized=True)

    after = engine2.get_affinity()

    for key in before:
        assert before[key] == after[key], f"Roundtrip mismatch for {key!r}: {before[key]!r} != {after[key]!r}"
