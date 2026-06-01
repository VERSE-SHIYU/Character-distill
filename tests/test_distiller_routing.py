"""Tests for long-context routing: token estimation + threshold branching."""

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

        self.distiller = Distiller(llm=mock_llm, config_path="config.yaml")
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
