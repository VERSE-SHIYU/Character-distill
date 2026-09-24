"""Tests for departure notice — gap-aware inner_voice hint for resumed conversation."""

from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from core.affinity_service import AffinityService
from core.chat_engine import (
    ChatEngine, WATCH_GAP_MIN_HOURS, WATCH_GAP_MAX_HOURS,
    _departure_notice_dates,
)
import core.scheduling as scheduling


def _run_await(coro, *, wait: bool = True, timeout: float = 600):
    """测试投递实现：本文件的用例是同步函数，直接在本线程跑完协程。

    形状照 `scripts/smoke_eval_e2e.py::_run_await`。退路已从 `core.scheduling` 拿掉，
    要投递就得由用例显式注册、用完还原。
    """
    return asyncio.run(coro)


class TestGapLogic:
    """ChatEngine gap threshold logic via mocked _evaluate_affinity."""

    def _make_engine(self, gap_hours: float | None = None) -> ChatEngine:
        """Create ChatEngine with controllable gap."""
        card = MagicMock()
        card.name = "Test"
        card.values = []
        card.inner_tensions = []
        card.psyche.affinity_baseline = 50
        card.psyche.volatility = "适中"
        card.psyche.grudge_inertia = "一般"
        card.psyche.triggers = []
        card.psyche.soft_spots = []

        engine = ChatEngine.__new__(ChatEngine)
        # Minimal init to avoid real dependencies
        engine.card = card
        engine.llm = MagicMock()
        engine.rag = MagicMock()
        engine._session_id = "test-session"
        engine._user_tz = "Asia/Shanghai"
        engine._memory = None
        # 本类不测存储：存储传 None，走不到投递，也就用不着注册投递实现。
        engine._storage = None
        engine._card_id = "test-card"
        engine._group_id = ""
        engine._affinity_service = AffinityService()
        engine._reaction_service = MagicMock()
        engine._last_reaction_id = 0
        engine._last_importance = 5
        engine._pipeline = MagicMock()
        engine._event_service = MagicMock()
        engine._reflection_service = MagicMock()
        engine._ctx_engine = MagicMock()
        engine.history = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "嗨"}]
        engine._last_user_msg_at = None
        engine.user_role = "朋友"

        if gap_hours is not None:
            engine._last_user_msg_at = datetime.now(timezone.utc) - timedelta(hours=gap_hours)
        return engine

    @patch("core.chat_engine._departure_notice_dates", {})
    def test_gap_3h_contains_notice(self):
        """3h gap produces departure notice in ctx."""
        engine = self._make_engine(gap_hours=3)
        ctx_arg = {}

        def fake_run(ctx):
            ctx_arg["departure_notice"] = ctx.departure_notice
            from core.evaluation_pipeline import EvalResult
            return EvalResult()

        engine._pipeline.run = fake_run
        engine._evaluate_affinity("你好吗", "我很好")
        notice = ctx_arg.get("departure_notice", "")
        assert "对方离开了" in notice, f"Expected notice for 3h gap, got: {notice!r}"

    @patch("core.chat_engine._departure_notice_dates", {})
    def test_gap_1h_no_notice(self):
        """1h gap produces no departure notice."""
        engine = self._make_engine(gap_hours=1)
        ctx_arg = {}

        def fake_run(ctx):
            ctx_arg["departure_notice"] = ctx.departure_notice
            from core.evaluation_pipeline import EvalResult
            return EvalResult()

        engine._pipeline.run = fake_run
        engine._evaluate_affinity("你好吗", "我很好")
        assert ctx_arg["departure_notice"] == "", f"Expected empty for 1h gap"

    @patch("core.chat_engine._departure_notice_dates", {})
    def test_gap_7h_no_notice(self):
        """7h gap (beyond 6h max) produces no departure notice (reunion handles it)."""
        engine = self._make_engine(gap_hours=7)
        ctx_arg = {}

        def fake_run(ctx):
            ctx_arg["departure_notice"] = ctx.departure_notice
            from core.evaluation_pipeline import EvalResult
            return EvalResult()

        engine._pipeline.run = fake_run
        engine._evaluate_affinity("你好吗", "我很好")
        assert ctx_arg["departure_notice"] == "", f"Expected empty for 7h gap"

    @patch("core.chat_engine._departure_notice_dates", {})
    def test_same_day_second_gap_suppressed(self):
        """Same-day second gap → frequency gate suppresses."""
        engine = self._make_engine(gap_hours=3)
        _departure_notice_dates.clear()
        calls = []

        def fake_run(ctx):
            calls.append(ctx.departure_notice)
            from core.evaluation_pipeline import EvalResult
            return EvalResult()

        engine._pipeline.run = fake_run
        # First call: should trigger
        engine._evaluate_affinity("你好吗", "我很好")
        # Second call same gap: should be suppressed
        engine._evaluate_affinity("又来了", "嗯")
        assert len(calls) == 2
        assert "对方离开了" in calls[0], f"First call should have notice: {calls[0]!r}"
        assert calls[1] == "", f"Second call should be suppressed: {calls[1]!r}"


class TestToAwareUtc:
    """_to_aware_utc type coercion — naive/aware/str/None → safe aware UTC."""

    def test_naive_str_sqlite(self):
        """Naive ISO string (SQLite style) parses correctly and gaps compute."""
        from core.chat_engine import _to_aware_utc
        result = _to_aware_utc("2026-07-02T10:00:00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.hour == 10
        assert result.tzinfo is timezone.utc

    def test_aware_str_pg(self):
        """Aware ISO string with offset (PG style) parses correctly."""
        from core.chat_engine import _to_aware_utc
        result = _to_aware_utc("2026-07-02T10:00:00+00:00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.hour == 10  # already UTC

    def test_aware_str_with_offset(self):
        """Aware string with non-UTC offset normalises to UTC."""
        from core.chat_engine import _to_aware_utc
        result = _to_aware_utc("2026-07-02T18:00:00+08:00")
        assert result is not None
        assert result.hour == 10  # 18:00 +08:00 → 10:00 UTC

    def test_naive_datetime(self):
        """Naive datetime treated as UTC."""
        from core.chat_engine import _to_aware_utc
        dt = datetime(2026, 7, 2, 10, 0, 0)
        result = _to_aware_utc(dt)
        assert result.tzinfo is timezone.utc
        assert result.hour == 10

    def test_aware_datetime(self):
        """Aware datetime stays aware."""
        from core.chat_engine import _to_aware_utc
        dt = datetime(2026, 7, 2, 10, 0, 0, tzinfo=timezone.utc)
        result = _to_aware_utc(dt)
        assert result == dt

    def test_none(self):
        """None returns None."""
        from core.chat_engine import _to_aware_utc
        assert _to_aware_utc(None) is None

    @patch("core.chat_engine._departure_notice_dates", {})
    def test_gap_with_aware_string(self):
        """PG 带时区串作 updated_at → 引擎读出它、算出 3 小时间隔、提醒非空。

        改前这条给 `get_session` 设返回值，而引擎读的是 `get_session_unscoped`，
        经退路失败又被吞 —— 「不抛 TypeError」从没真测过，断言 `== ""` 在任何间隔下
        都成立。现在投递实现由本用例显式注册，读不回来就红。
        """
        engine = TestGapLogic()._make_engine(gap_hours=None)
        engine._last_user_msg_at = None
        engine._storage = AsyncMock()
        engine._storage.get_reactions_after_unscoped.return_value = []
        engine._storage.get_session_unscoped.return_value = {
            "updated_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        }
        ctx_arg = {}

        def fake_run(ctx):
            ctx_arg["departure_notice"] = ctx.departure_notice
            from core.evaluation_pipeline import EvalResult
            return EvalResult()

        engine._pipeline.run = fake_run
        prev = scheduling.get_loop_submitter()
        scheduling.set_loop_submitter(_run_await)
        try:
            engine._evaluate_affinity("你好吗", "我很好")
        finally:
            scheduling.set_loop_submitter(prev)
        assert "对方离开了" in ctx_arg["departure_notice"]

    @patch("core.chat_engine._departure_notice_dates", {})
    def test_gap_with_naive_string(self):
        """SQLite 无时区串作 updated_at → 同样算出 3 小时间隔、提醒非空。"""
        engine = TestGapLogic()._make_engine(gap_hours=None)
        engine._last_user_msg_at = None
        engine._storage = AsyncMock()
        engine._storage.get_reactions_after_unscoped.return_value = []
        engine._storage.get_session_unscoped.return_value = {
            "updated_at": (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        ctx_arg = {}

        def fake_run(ctx):
            ctx_arg["departure_notice"] = ctx.departure_notice
            from core.evaluation_pipeline import EvalResult
            return EvalResult()

        engine._pipeline.run = fake_run
        prev = scheduling.get_loop_submitter()
        scheduling.set_loop_submitter(_run_await)
        try:
            engine._evaluate_affinity("你好吗", "我很好")
        finally:
            scheduling.set_loop_submitter(prev)
        assert "对方离开了" in ctx_arg["departure_notice"]


class TestBuildEvaluationPrompt:
    """Pure-function regression: departure_notice param does not break existing callers."""

    def _make_card(self):
        from types import SimpleNamespace
        psy = SimpleNamespace(
            affinity_baseline=50, volatility="适中", grudge_inertia="一般",
            triggers=[], soft_spots=[],
        )
        return SimpleNamespace(name="Test", values=[], inner_tensions=[], psyche=psy)

    def test_without_departure_notice_unchanged(self):
        """Default (no departure_notice) produces identical prompt."""
        svc = AffinityService()
        card = self._make_card()
        p1 = svc.build_evaluation_prompt(card, "hi", "hello", "朋友", "")
        p2 = svc.build_evaluation_prompt(card, "hi", "hello", "朋友", "")
        assert p1 == p2

    def test_departure_notice_appended(self):
        """Departure notice text appears in the built prompt."""
        svc = AffinityService()
        card = self._make_card()
        notice = "对方离开了约3.0小时才回来。以你的性格决定是否在意——在意的话..."
        prompt = svc.build_evaluation_prompt(
            card, "hi", "hello", "朋友", "",
            departure_notice=notice + "\n\n",
        )
        assert "对方离开了" in prompt
        assert "在意的话" in prompt

    def test_short_gap_no_notice(self):
        """Short gap (1h) should NOT produce a notice (ChatEngine logic)."""
        svc = AffinityService()
        card = self._make_card()
        p = svc.build_evaluation_prompt(card, "hi", "hello", "朋友", "")
        assert "对方离开了" not in p

    def test_departure_notice_before_inner_voice(self):
        """Notice appears in the prompt before '用你自己的口吻写出'."""
        svc = AffinityService()
        card = self._make_card()
        notice = "对方离开了约3.0小时才回来。\n\n"
        prompt = svc.build_evaluation_prompt(
            card, "hi", "hello", "朋友", "",
            departure_notice=notice,
        )
        assert prompt.index(notice) < prompt.index("用你自己的口吻写出")
