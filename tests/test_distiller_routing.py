"""Tests for long-context routing: token estimation + threshold branching."""

import asyncio

import pytest
from unittest.mock import MagicMock, patch

from core.distiller import Distiller


class TestEstimateTokens:
    def test_short_text_below_threshold(self):
        """~4w token text → _estimate_tokens < 150000."""
        text = "测试" * 66667
        assert Distiller._estimate_tokens(text) < 150000

    def test_long_text_at_or_above_threshold(self):
        """~20w token text → _estimate_tokens >= 150000."""
        text = "测试" * 350000
        assert Distiller._estimate_tokens(text) >= 150000

    def test_empty_text(self):
        assert Distiller._estimate_tokens("") == 0

    def test_ascii_text(self):
        """ASCII chars also work with the same multiplier."""
        text = "hello" * 10000
        tokens = Distiller._estimate_tokens(text)
        assert tokens == int(len(text) * 0.6)


class TestRouting:
    def setup_method(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = '{"name": "T", "identity": "T"}'
        mock_llm.last_usage = None
        mock_llm.chat_stream.return_value = iter([])

        async def fake_async(*args, **kwargs):
            return ("分析结果", None)
        mock_llm.async_chat = fake_async

        self.distiller = Distiller(llm=mock_llm, config_path=None)
        self.distiller._longctx_threshold = 150000

    # ── distill_incremental (sync) ────────────────────────────────────

    def test_below_threshold_calls_longcontext(self):
        """< threshold → _distill_longcontext is called, _do_reduce is NOT."""
        text = "测试" * 66667
        with patch.object(self.distiller, '_distill_longcontext') as mock_long:
            with patch.object(self.distiller, '_do_reduce') as mock_reduce:
                try:
                    self.distiller.distill_incremental(text, "角色")
                except Exception:
                    pass
                mock_long.assert_called_once()
                mock_reduce.assert_not_called()

    def test_above_threshold_does_not_call_longcontext(self):
        """>= threshold → _distill_longcontext is NOT called."""
        text = "测试" * 350000
        with patch.object(self.distiller, '_distill_longcontext') as mock_long:
            try:
                self.distiller.distill_incremental(text, "角色")
            except Exception:
                pass
            mock_long.assert_not_called()


    # ── distill_incremental_stream ────────────────────────────────────

    def test_stream_below_threshold_calls_longcontext(self):
        """Streaming: < threshold → _distill_longcontext_stream is called."""
        text = "测试" * 66667
        with patch.object(
            self.distiller, '_distill_longcontext_stream', return_value=iter([])
        ) as mock_long:
            gen = self.distiller.distill_incremental_stream(text, "角色")
            list(gen)
            mock_long.assert_called_once()

    def test_stream_above_threshold_does_not_call_longcontext(self):
        """Streaming: >= threshold → _distill_longcontext_stream is NOT called."""
        text = "测试" * 350000
        with patch.object(self.distiller, '_distill_longcontext_stream') as mock_long:
            gen = self.distiller.distill_incremental_stream(text, "角色")
            try:
                list(gen)
            except Exception:
                pass
            mock_long.assert_not_called()


class TestAsyncChatClientParam:
    """async_chat(client=...) uses the right client without affecting the default."""

    async def test_custom_client_isolation(self):
        """Concurrent calls: one with custom client, one without — each uses the correct client."""
        from unittest.mock import AsyncMock

        from adapters.llm_adapter import LLMAdapter

        llm = LLMAdapter(api_key="test-key", base_url="http://localhost:0", model="test")

        default_client = MagicMock()
        default_client.chat.completions.create = AsyncMock(return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="from-default"))], usage=None
        ))
        llm._async_client = default_client

        custom_client = MagicMock()
        custom_client.chat.completions.create = AsyncMock(return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="from-custom"))], usage=None
        ))

        async def call_custom():
            return await llm.async_chat("sys", [{"role": "user", "content": "a"}], client=custom_client)

        async def call_default():
            return await llm.async_chat("sys", [{"role": "user", "content": "b"}])

        r1, r2 = await asyncio.gather(call_custom(), call_default())

        assert r1[0] == "from-custom", f"expected custom result, got {r1[0]}"
        assert r2[0] == "from-default", f"expected default result, got {r2[0]}"
        assert custom_client.chat.completions.create.await_count == 1
        assert default_client.chat.completions.create.await_count == 1


class TestMapPhaseFailureHandling:
    """>50% Map chunk failures → early bail with clear error message."""

    # ── helpers ──────────────────────────────────────────────────────────

    def _make_429_failing_async(self, fail_after: int = 1):
        """Return async_chat that succeeds for first `fail_after` calls, then raises 429."""
        call_count = [0]

        async def fake(system, messages, max_tokens=None, **kwargs):
            call_count[0] += 1
            if call_count[0] > fail_after:
                raise RuntimeError("rate limited (429) after 5 attempts: req-abc123")
            return ("分析结果", {"prompt_tokens": 10, "completion_tokens": 5})

        return fake

    def _make_generic_failing_async(self, fail_after: int = 1):
        """Return async_chat that succeeds first, then raises generic error."""
        call_count = [0]

        async def fake(system, messages, max_tokens=None, **kwargs):
            call_count[0] += 1
            if call_count[0] > fail_after:
                raise RuntimeError("connection timeout")
            return ("分析结果", {"prompt_tokens": 10, "completion_tokens": 5})

        return fake

    def _make_distiller(self, mock_llm) -> Distiller:
        d = Distiller(llm=mock_llm, config_path=None)
        d._longctx_threshold = 0  # Force Map-Reduce chunked path
        d._chunk_size = 3000
        return d

    # With chunk_size=3000, "AB" * 5000 = 10000 chars → 4 chunks (range(0,10000,3000))

    # ── sync path (distill_incremental) ──────────────────────────────────

    def test_sync_429_bail(self):
        """>50% fail with 429 → ValueError with 'API 限流'."""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_429_failing_async(fail_after=1)  # 3/4 fail = 75%

        d = self._make_distiller(mock_llm)

        with pytest.raises(ValueError, match="API 限流"):
            d.distill_incremental("AB" * 5000, "AB")

    def test_sync_generic_bail(self):
        """>50% fail with non-429 → ValueError describing failures."""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_generic_failing_async(fail_after=1)

        d = self._make_distiller(mock_llm)

        with pytest.raises(ValueError) as excinfo:
            d.distill_incremental("AB" * 5000, "AB")

        msg = str(excinfo.value)
        assert "API 限流" not in msg
        assert "connection timeout" in msg

    # ── stream path (distill_incremental_stream) ─────────────────────────

    def test_stream_429_bail(self):
        """>50% fail with 429 → yields {'error': 'API 限流'}."""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_429_failing_async(fail_after=1)

        d = self._make_distiller(mock_llm)

        gen = d.distill_incremental_stream("AB" * 5000, "AB")
        results = list(gen)

        errors = [r for r in results if isinstance(r, dict) and "error" in r]
        assert len(errors) == 1
        assert "API 限流" in errors[0]["error"]

    def test_stream_generic_bail(self):
        """>50% fail with non-429 → yields {'error': describing failures}."""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_generic_failing_async(fail_after=1)

        d = self._make_distiller(mock_llm)

        gen = d.distill_incremental_stream("AB" * 5000, "AB")
        results = list(gen)

        errors = [r for r in results if isinstance(r, dict) and "error" in r]
        assert len(errors) == 1
        assert "API 限流" not in errors[0]["error"]
        assert "connection timeout" in errors[0]["error"]
