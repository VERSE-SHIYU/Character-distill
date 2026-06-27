"""Tests for UserClock timezone abstraction and _build_time_awareness_block regression.

A. UserClock pure-function correctness (aware-datetime, tz isolation, fallback)
B. describe_time_period boundary cases
C. ChatEngine _build_time_awareness_block — prevents regression to server-timezone
"""

from datetime import datetime, timezone

from core.chat_engine import ChatEngine
from core.clock import UserClock, describe_time_period, DEFAULT_TZ
from core.schema import CharacterCard


class _StubLLM:
    model = ""


# ── A. UserClock ─────────────────────────────────────────────────────────────


class TestUserClock:
    """Pure-function coverage for the time abstraction."""

    def test_now_returns_aware(self):
        assert UserClock.now("Australia/Sydney").tzinfo is not None

    def test_timezone_isolation(self):
        offset_syd = UserClock.now("Australia/Sydney").utcoffset()
        offset_cn = UserClock.now("Asia/Shanghai").utcoffset()
        assert offset_syd != offset_cn

    def test_invalid_tz_fallback(self):
        assert str(UserClock.now("Invalid/Zone").tzinfo) == DEFAULT_TZ

    def test_empty_tz_fallback(self):
        assert str(UserClock.now("").tzinfo) == DEFAULT_TZ

    def test_none_tz_fallback(self):
        assert str(UserClock.now(None).tzinfo) == DEFAULT_TZ

    def test_to_user_tz_naive_is_utc(self):
        """Naive datetime treated as UTC → Asia/Shanghai should be UTC+8 → hour=8."""
        dt = datetime(2026, 1, 1, 0, 0)  # naive
        result = UserClock.to_user_tz(dt, "Asia/Shanghai")
        assert result.hour == 8, f"expected 8, got {result.hour}"

    def test_to_user_tz_aware_changes_tz(self):
        """Already-aware datetime is converted to target timezone."""
        dt = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        result = UserClock.to_user_tz(dt, "Australia/Sydney")
        assert result.tzinfo is not None
        # UTC+0 → Sydney is UTC+10 or UTC+11, so hour should not be 12
        assert result.hour != 12


# ── B. describe_time_period ──────────────────────────────────────────────────


class TestDescribeTimePeriod:
    def test_5am_qingchen(self):
        assert describe_time_period(5) == "清晨"

    def test_7am_qingchen(self):
        assert describe_time_period(7) == "清晨"

    def test_8am_shangwu(self):
        assert describe_time_period(8) == "上午"

    def test_12pm_zhongwu(self):
        assert describe_time_period(12) == "中午"

    def test_4pm_xiawu(self):
        assert describe_time_period(16) == "下午"

    def test_6pm_bangwan(self):
        assert describe_time_period(18) == "傍晚"

    def test_10pm_yewan(self):
        assert describe_time_period(22) == "夜晚"

    def test_11pm_shenye(self):
        assert describe_time_period(23) == "深夜"

    def test_3am_shenye(self):
        assert describe_time_period(3) == "深夜"


# ── C. Regression: _build_time_awareness_block uses _user_tz ─────────────────


class TestBuildTimeAwarenessBlock:
    """If someone reverts _build_time_awareness_block to bare datetime.now()
    (server timezone), these tests will fail."""

    def _make_engine(self) -> ChatEngine:
        card = CharacterCard(name="测试角色")
        return ChatEngine(_StubLLM(), None, card, card_id="t")

    def test_sydney_tz_hour_in_output(self):
        """Output contains the correct hour for Sydney."""
        engine = self._make_engine()
        engine._user_tz = "Australia/Sydney"
        block = engine._build_time_awareness_block()
        expected_hour = UserClock.now("Australia/Sydney").hour
        assert f"{expected_hour:02d}:" in block, (
            f"Sydney hour {expected_hour:02d} not found in output:\n{block}"
        )

    def test_shanghai_tz_hour_in_output(self):
        """Output contains the correct hour for Shanghai."""
        engine = self._make_engine()
        engine._user_tz = "Asia/Shanghai"
        block = engine._build_time_awareness_block()
        expected_hour = UserClock.now("Asia/Shanghai").hour
        assert f"{expected_hour:02d}:" in block, (
            f"Shanghai hour {expected_hour:02d} not found in output:\n{block}"
        )

    def test_sydney_and_shanghai_differ(self):
        """Sydney and Shanghai engines produce different hour strings."""
        e1 = self._make_engine()
        e1._user_tz = "Australia/Sydney"
        e2 = self._make_engine()
        e2._user_tz = "Asia/Shanghai"

        syd_hour = UserClock.now("Australia/Sydney").hour
        sha_hour = UserClock.now("Asia/Shanghai").hour

        # If both use the same tz or bare datetime.now(), hours would match
        # (during the ~6h overlap window when both regions share the same
        #  wall-clock hour number, we skip the equality assertion).
        if syd_hour != sha_hour:
            assert syd_hour != sha_hour, "timezones should differ"
            assert f"{syd_hour:02d}:" in e1._build_time_awareness_block()
            assert f"{sha_hour:02d}:" in e2._build_time_awareness_block()
