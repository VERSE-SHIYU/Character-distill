"""Tests for long-context routing: token estimation + threshold branching."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.distiller import DistillError, Distiller


class TestReduceAllEmptyBails:
    """缺陷 34：Reduce 全部 batch 返回空 → 显式失败并说明原因，不得落卡。

    假件照**生产实际形状**搭：Map 全成功（100 片 > SAFE_SINGLE_REDUCE=80，故走分批归并），
    归并批次全部失败（返回空）；而上游对「零条分析的归并请求」会**凭空产出**非空档案。
    这一条必须模拟 —— 不模拟的话，「输出为空即失败」那道门（``:1726``）在用例里反而
    拦住了，测的就不是真缺口（正是缺陷 34 那条一般化）。

    变异对象 = ``_run_reduce_concurrent`` 末尾的输入守卫（删掉它）：两条用例都会落到
    format 阶段 —— ``assert llm.chat.call_count == 0`` / ``formatting`` 帧断言各变红。
    """

    TEXT = "AB" * 150000          # 300000 字符 ÷ chunk_size 3000 = 100 片
    FABRICATED = "凭空产出的角色档案"
    CARD_JSON = '{"name": "AB"}'

    class _Client:
        async def close(self):
            pass

    def _make_llm(self):
        llm = MagicMock()
        llm.last_usage = None
        llm._make_async_client = lambda: self._Client()
        # 判别归并调用靠 _reduce_system_prompt 的首句；「来源片段」只在分析数 > 0 时出现
        # （_reduce_user_prompt 用 join 渲染，空列表渲染出的正文里没有它）。

        async def async_chat(system, messages, max_tokens=None, **kwargs):
            if "你正在整合关于" in system:
                if "来源片段" in messages[0]["content"]:
                    return ("", {"prompt_tokens": 1, "completion_tokens": 0})
                return (self.FABRICATED, {"prompt_tokens": 1, "completion_tokens": 1})
            return ("分析结果", {"prompt_tokens": 1, "completion_tokens": 1})

        def chat(system, messages, max_tokens=None, **kwargs):
            if "你正在整合关于" in system:
                if "来源片段" in messages[0]["content"]:
                    return ""
                return self.FABRICATED
            return self.CARD_JSON

        def chat_stream(system, messages, max_tokens=None, **kwargs):
            # 用量随返回值交付（WP4）：桩不 return 的话，调用方会静默落回 last_usage
            if "你正在整合关于" in system:
                if "来源片段" not in messages[0]["content"]:
                    yield self.FABRICATED
                return {"prompt_tokens": 1, "completion_tokens": 1}
            yield self.CARD_JSON
            return {"prompt_tokens": 1, "completion_tokens": 1}

        llm.async_chat = AsyncMock(side_effect=async_chat)
        llm.chat = MagicMock(side_effect=chat)
        llm.chat_stream = MagicMock(side_effect=chat_stream)
        return llm

    def _make_distiller(self, llm) -> Distiller:
        d = Distiller(llm=llm, config_path=None)
        d._longctx_threshold = 0     # 强制走分片 MapReduce
        d._chunk_size = 3000
        return d

    def test_sync_reduce_all_empty_raises_before_format(self):
        """非 stream（/run 那条）：归并全空 → DistillError，format 阶段不启动。"""
        llm = self._make_llm()
        d = self._make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.distill_incremental(self.TEXT, "AB")

        assert "归并" in excinfo.value.user_message
        assert "归并" in str(excinfo.value)      # 运维口径同样点名归并段
        assert llm.chat.call_count == 0          # 格式化没跑 = 没落卡

    def test_stream_reduce_all_empty_yields_error_before_format(self):
        """stream（/start、/run_stream 那两条）：归并全空 → 上屏 error 帧，不进 format。"""
        llm = self._make_llm()
        d = self._make_distiller(llm)

        frames = list(d.distill_incremental_stream(
            self.TEXT, "AB", aliases=[], text_type="story"))

        errors = [f for f in frames if isinstance(f, dict) and "error" in f]
        assert len(errors) == 1
        assert "归并" in errors[0]["error"]
        assert not any(
            isinstance(f, dict) and f.get("status") == "formatting" for f in frames
        )


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
        # 真实的 `LLMAdapter._make_async_client()` 返回 AsyncOpenAI，它的 close() 是**协程**；
        # 裸 MagicMock 的属性是同步方法，`await client.close()` 会 TypeError。桩要和真实接口
        # 一致，否则用例考的是 mock 的瑕疵、不是被考的路径。
        async def _close() -> None:
            return None

        client = MagicMock()
        client.close = _close
        mock_llm._make_async_client = MagicMock(return_value=client)

        d = Distiller(llm=mock_llm, config_path=None)
        d._longctx_threshold = 0  # Force Map-Reduce chunked path
        d._chunk_size = 3000
        return d

    # With chunk_size=3000, "AB" * 5000 = 10000 chars → 4 chunks (range(0,10000,3000))

    # ── sync path (distill_incremental) ──────────────────────────────────

    def test_sync_429_bail(self):
        """>50% fail with 429 → DistillError；上屏说限流，运维口径（429 / 分片数）只进日志。

        缺陷 17：这两条断言的方向是相反的——上屏**不能**有 429 / 分片，``str()`` **必须**有，
        否则就是把「分了口径」做成了「删了信息」。
        """
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_429_failing_async(fail_after=1)  # 3/4 fail = 75%

        d = self._make_distiller(mock_llm)

        with pytest.raises(DistillError) as excinfo:
            d.distill_incremental("AB" * 5000, "AB")

        assert "限流" in excinfo.value.user_message
        assert "429" not in excinfo.value.user_message
        assert "个分片" not in excinfo.value.user_message
        assert "429" in str(excinfo.value)

    def test_sync_generic_bail(self):
        """>50% fail with non-429 → 上屏通用文案；最后错误只进日志（缺陷 17 不变量）。"""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_generic_failing_async(fail_after=1)

        d = self._make_distiller(mock_llm)

        with pytest.raises(DistillError) as excinfo:
            d.distill_incremental("AB" * 5000, "AB")

        assert "限流" not in excinfo.value.user_message
        assert "connection timeout" not in excinfo.value.user_message
        assert "connection timeout" in str(excinfo.value)   # 排障线索不丢

    # ── stream path (distill_incremental_stream) ─────────────────────────

    def test_stream_429_bail(self):
        """>50% fail with 429 → 上屏帧说限流，不带 429 / 分片（运维口径）。"""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_429_failing_async(fail_after=1)

        d = self._make_distiller(mock_llm)

        gen = d.distill_incremental_stream("AB" * 5000, "AB")
        results = list(gen)

        errors = [r for r in results if isinstance(r, dict) and "error" in r]
        assert len(errors) == 1
        assert "限流" in errors[0]["error"]
        assert "429" not in errors[0]["error"]
        assert "个分片" not in errors[0]["error"]

    def test_stream_generic_bail(self):
        """>50% fail with non-429 → 上屏帧是通用文案，最后错误不上屏（旧断言锁的正是泄漏）。"""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_generic_failing_async(fail_after=1)

        d = self._make_distiller(mock_llm)

        gen = d.distill_incremental_stream("AB" * 5000, "AB")
        results = list(gen)

        errors = [r for r in results if isinstance(r, dict) and "error" in r]
        assert len(errors) == 1
        assert "connection timeout" not in errors[0]["error"]
        # 用「个分片」而非裸「分片」：上屏的「部分片段处理失败」是合法中文，裸词会假阳
        assert "个分片" not in errors[0]["error"]
        assert "重试" in errors[0]["error"]
