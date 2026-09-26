"""Tests for long-context routing: token estimation + threshold branching."""

import asyncio
import threading

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import adapters.llm_adapter as M
from core.distiller import DistillError, Distiller
from core.request_context import LLM_CALLER, Caller, current_user_id


class TestReduceAllEmptyBails:
    """缺陷 34：Reduce 全部 batch 返回空 → 显式失败并说明原因，不得落卡。

    假件照**生产实际形状**搭：Map 全成功（100 片 > SAFE_SINGLE_REDUCE=80，故走分批归并），
    归并批次全部失败（空正文）；而上游对「零条分析的归并请求」会**凭空产出**非空档案
    —— 那正是下游「输出为空即失败」那道门结构上拦不住的形态（缺陷 34 的一般化）。

    变异对象 = 批级失败判定（`_single_reduce_async` 里「空正文即抛」那一句，或把失败
    吞成空串）：两条用例都会落到 format 阶段 —— ``llm.chat.call_count == 0`` /
    ``formatting`` 帧断言各变红。
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

        def chat_stream_long(system, messages, max_tokens=None, **kwargs):
            # 用量随返回值交付（WP4）：桩不 return 的话，调用方会静默落回 last_usage。
            # 分批归并存分析的那条路（WP5 起归并走流式长输出）产出空正文 = 该批失败。
            if "你正在整合关于" in system:
                if "来源片段" in messages[0]["content"]:
                    yield from ()
                else:
                    yield self.FABRICATED   # 零条分析的归并，模型会凭空产出（缺陷 34）
                return {"prompt_tokens": 1, "completion_tokens": 1}
            yield self.CARD_JSON
            return {"prompt_tokens": 1, "completion_tokens": 1}

        llm.async_chat = AsyncMock(side_effect=async_chat)
        llm.chat = MagicMock(side_effect=chat)
        llm.chat_stream_long = MagicMock(side_effect=chat_stream_long)
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


class TestBatchReduceUsesTheLongOutputStream:
    """WP5 R1：分批归并走流式长输出 —— 不再受生成轮 45/60 s 墙钟。

    真 adapter + 真 socket（假 SSE fixture）：上游回 200 头后静默 2.0 s 再吐完。
    **非流式请求要等整包**，于是那 2.0 s 的静默就吃满了非流式的标量超时；流式那支
    `chat_stream_long` 把 read 放宽到 `_BATCH_STREAM_READ_S`（300 s），静默照过。

    变异「恢复 async_chat」：非流式请求在静默期就被读超时打死（`_GEN_*` 缩到同一量级
    才谈得上不真等），本用例红。
    """

    def test_reduce_batch_survives_prefill_silence(self, fake_sse, monkeypatch):
        monkeypatch.setattr(M, "_STREAM_ATTEMPT_S", 0.5)    # 与 WP1 同法：不真等
        monkeypatch.setattr(M, "_STREAM_DEADLINE_S", 1.5)
        monkeypatch.setattr(M, "_GEN_ATTEMPT_S", 0.5)       # 非流式那支的墙钟缩到同一量级
        monkeypatch.setattr(M, "_GEN_DEADLINE_S", 1.5)
        fake_sse.plan.update(silent_ms=2000, tokens=["合并", "结果"])

        d = Distiller(llm=fake_sse.adapter(), config_path=None)

        out = asyncio.run(d._single_reduce_async(["分析一", "分析二"], "角色"))

        assert out == "合并结果", f"分批归并没有走流式长输出（2.0s 静默被当故障）：{out!r}"


class TestSingleReduceUsesTheCardOutputCap:
    """WP13 S6：单次归并（≤80 片，每本普通书都走这条）的输出上限 = `CARD_MAX_TOKENS`。

    `_single_reduce_stream` 原先不传 `max_tokens` → 适配器落回 4096，只有分批归并
    （`_single_reduce_async`）与格式化的一半。WP11 起 `length` 截断是硬失败，这条
    就成了普通书蒸馏的必红路径 —— 所以两处必须同批上线。

    变异：删掉 `max_tokens=self.CARD_MAX_TOKENS` → 记录到 None，本条红。
    """

    def test_single_reduce_passes_card_max_tokens(self):
        seen: list = []

        class _LLM:
            last_usage = None

            def chat_stream_long(self, system, messages, max_tokens=None):
                seen.append(max_tokens)
                yield "档案"
                return {"prompt_tokens": 1, "completion_tokens": 1}

        d = Distiller(llm=_LLM(), config_path=None)
        out = "".join(d._single_reduce_stream(["分析一", "分析二"], "角色"))

        assert out == "档案"
        assert seen == [Distiller.CARD_MAX_TOKENS], (
            f"单次归并的输出上限没对齐 CARD_MAX_TOKENS：{seen}")


class TestAnyBatchFailureFailsTheWholeReduce:
    """WP5 R2：任一批失败即整体失败 —— 不落半张卡、不进格式化（D3）。

    100 片（2 批，`SAFE_SINGLE_REDUCE=80`）。第 2 批的归并请求由假上游按**请求体**
    识别（分批是并发发出的，按调用次序认会飘），分别造截断 / 空正文。

    断言：上屏恰一个 error 帧、无 `formatting` 帧、格式化 0 次；并断言分批归并的输出
    上限是 `CARD_MAX_TOKENS`。变异：① 截断照常交出半截 ② 恢复单批吞异常
    ③ 恢复空批跳过 ④ 不传 max_tokens。
    """

    CHUNK = 3000
    MARK = "M-标记分析"
    # 前 80 片（批 1）是 "AB"，后 20 片（批 2）是 "CD" —— 只有第 2 批的请求体含标记。
    # aliases 令每片都命中 match_terms，否则 relevant 回落成 chunks[:3]，分不出批。
    TEXT = "AB" * (1500 * 80) + "CD" * (1500 * 20)
    ALIASES = ["AB", "CD"]

    @pytest.mark.parametrize("rule", [
        pytest.param({"finish_reason": "length"}, id="截断"),
        pytest.param({"tokens": []}, id="空正文"),
    ])
    def test_second_batch_failure_aborts_before_format(self, fake_sse, rule):
        llm = fake_sse.adapter()
        real_long = llm.chat_stream_long
        calls = []

        def spy_long(system, messages, max_tokens=None):
            calls.append({"system": system, "max_tokens": max_tokens})
            return (yield from real_long(system, messages, max_tokens))

        llm.chat_stream_long = spy_long

        async def map_stub(system, messages, max_tokens=None, client=None, **kw):
            body = messages[0]["content"]
            analysis = (self.MARK if "CD" in body else "普通分析") + body[:8]
            return (analysis, {"prompt_tokens": 1, "completion_tokens": 1})

        llm.async_chat = map_stub
        fake_sse.plan.update(tokens=["全", "文"], body_rules=[(self.MARK, rule)])

        d = Distiller(llm=llm, config_path=None)
        d._longctx_threshold = 0
        d._chunk_size = self.CHUNK

        frames = list(d.distill_incremental_stream(
            self.TEXT, "角色", aliases=self.ALIASES, text_type="story"))

        errors = [f for f in frames if isinstance(f, dict) and "error" in f]
        assert len(errors) == 1, f"第 2 批失败没上屏成 error 帧：{frames[-3:]}"
        assert not any(
            isinstance(f, dict) and f.get("status") == "formatting" for f in frames
        ), "任一批失败却仍然进了格式化"

        reduce_calls = [c for c in calls if "你正在整合关于" in c["system"]]
        fmt_calls = [c for c in calls if "你正在整合关于" not in c["system"]]
        assert len(reduce_calls) >= 2, f"没跑到分批归并：{calls}"
        assert fmt_calls == [], f"格式化不该被调用：{[c['system'][:20] for c in fmt_calls]}"
        assert {c["max_tokens"] for c in reduce_calls} == {8192}, (
            f"分批归并的输出上限不是 CARD_MAX_TOKENS："
            f"{[c['max_tokens'] for c in reduce_calls]}"
        )


class TestBatchThreadCarriesCallerIdentity:
    """WP5 R3：批线程带上调用方身份 —— 记账与门的 fail-closed 都读 `LLM_CALLER`。

    变异：线程执行换成裸 `loop.run_in_executor(None, …)`（不拷 contextvar）→ 全 None。
    """

    def test_identity_reaches_every_batch_thread(self):
        seen: list[tuple[int, str | None]] = []
        caller_thread = threading.get_ident()

        class _LLM:
            last_usage = None
            model = "m"

            def chat_stream_long(self, system, messages, max_tokens=None):
                seen.append((threading.get_ident(), current_user_id()))
                yield "合并结果"
                return {"prompt_tokens": 1, "completion_tokens": 1}

            async def async_chat(self, system, messages, max_tokens=None, client=None):
                seen.append((threading.get_ident(), current_user_id()))
                return ("合并结果", {"prompt_tokens": 1, "completion_tokens": 1})

        token = LLM_CALLER.set(Caller(ip=None, user_id="u_ctx"))
        try:
            d = Distiller(llm=_LLM(), config_path=None)
            results = asyncio.run(
                d._run_reduce_concurrent([["分析一"], ["分析二"]], "角色"))
        finally:
            LLM_CALLER.reset(token)

        assert len(results) == 2 and all(r[1] == "合并结果" for r in results), results
        assert all(t != caller_thread for t, _ in seen), f"批次没在线程里跑：{seen}"
        assert [u for _, u in seen] == ["u_ctx", "u_ctx"], f"批线程丢了调用方身份：{seen}"


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
