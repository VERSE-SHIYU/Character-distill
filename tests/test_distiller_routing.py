"""Tests for long-context routing: token estimation + threshold branching."""

import asyncio
import json
import math
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import adapters.llm_adapter as M
from adapters.llm_adapter import IncompleteResponseError, UpstreamFailure
from core.concurrency import AdaptiveGate
from core.distiller import IDENTIFY_JUDGE_PROMPT, DistillError, Distiller, _map_failure_message
from core.request_context import LLM_CALLER, Caller, current_user_id
from core.schema import FORMAT_GROUPS


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


class TestSingleReduceUsesTheLongOutputCap:
    """WP13 S6：单次归并（≤80 片，每本普通书都走这条）的输出上限 = `LONG_OUTPUT_MAX_TOKENS`。

    `_single_reduce_stream` 原先不传 `max_tokens` → 适配器落回 4096，只有分批归并
    （`_single_reduce_async`）与格式化的一半。WP11 起 `length` 截断是硬失败，这条
    就成了普通书蒸馏的必红路径 —— 所以两处必须同批上线。归并产物是**整份档案**而非
    卡的分片，故不与格式化共用 `CARD_MAX_TOKENS`。

    变异：删掉 `max_tokens=self.LONG_OUTPUT_MAX_TOKENS` → 记录到 None，本条红。
    """

    def test_single_reduce_passes_the_long_output_cap(self):
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
        assert seen == [Distiller.LONG_OUTPUT_MAX_TOKENS], (
            f"单次归并的输出上限没对齐 LONG_OUTPUT_MAX_TOKENS：{seen}")


class TestAnyBatchFailureFailsTheWholeReduce:
    """WP5 R2：任一批失败即整体失败 —— 不落半张卡、不进格式化（D3）。

    100 片（2 批，`SAFE_SINGLE_REDUCE=80`）。第 2 批的归并请求由假上游按**请求体**
    识别（分批是并发发出的，按调用次序认会飘），分别造截断 / 空正文。

    断言：上屏恰一个 error 帧、无 `formatting` 帧、格式化 0 次；并断言分批归并的输出
    上限是 `LONG_OUTPUT_MAX_TOKENS`。变异：① 截断照常交出半截 ② 恢复单批吞异常
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
        assert {c["max_tokens"] for c in reduce_calls} == {Distiller.LONG_OUTPUT_MAX_TOKENS}, (
            f"分批归并的输出上限不是 LONG_OUTPUT_MAX_TOKENS："
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

            async def async_chat(self, system, messages, max_tokens=None, client=None, gate=None):
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

    # ── 默认阈值（上面每个用例都把 150000 钉死了，默认值只有这里看得到）──

    def test_a_book_at_hongloumeng_scale_takes_one_pass_at_the_default_threshold(self):
        """默认阈值下：52 万 token 走一次读完，95 万 token 走分片。

        挡住：把默认值退回 150000 —— 红楼梦（实测 519,689 token）会掉进分片路径，
        也就是归并撞输出上限那条路。判据只看**走哪条路**，不看阈值是几。
        """
        d = Distiller(llm=self.distiller._llm, config_path=None)
        text = "测试" * 100
        with patch("core.distiller.count_tokens", return_value=519_689):
            with patch.object(d, "_distill_longcontext_stream", return_value=iter([])) as one_pass:
                list(d.distill_incremental_stream(text, "角色"))
            one_pass.assert_called_once()
        with patch("core.distiller.count_tokens", return_value=950_000):
            with patch.object(d, "_distill_longcontext_stream") as one_pass:
                try:
                    list(d.distill_incremental_stream(text, "角色"))
                except Exception:
                    pass
            one_pass.assert_not_called()

    def test_the_one_pass_request_puts_the_whole_text_before_the_character_name(self):
        """一次读完的请求里，全文排在角色名之前 —— 否则跨角色前缀不共享，缓存恒不命中。

        Context Caching 按**前缀**匹配（命中要求从头逐 token 相同，
        https://api-docs.deepseek.com/guides/kv_cache），所以共享的正文必须落在请求
        最前面。挡住：把角色名放回系统提示开头（改前的结构，同一本书换个角色从第 1
        个 token 就分叉）。
        """
        llm = MagicMock()
        llm.last_usage = None
        seen: list[tuple] = []
        llm.chat_stream_long.side_effect = lambda system, messages, max_tokens=None: (
            seen.append((system, messages, max_tokens)), iter([])
        )[1]
        d = Distiller(llm=llm, config_path=None)
        text = "此处是正文。" * 40
        name = "独一无二的测试角色"
        with patch("core.distiller.count_tokens", return_value=1_000):
            list(d.distill_incremental_stream(text, name))

        system, messages, max_tokens = seen[0]
        assert text in system, "全文不在一次读完请求里"
        assert system.index(text) < system.index(name), "角色名出现在全文之前，前缀无法共享"
        assert text not in "".join(m["content"] for m in messages), "全文被送了两遍（system 里一份、user 里又一份）"
        assert max_tokens == Distiller.LONG_OUTPUT_MAX_TOKENS

    def test_the_sync_one_pass_request_uses_the_same_prompt(self):
        """同步那条一次读完与流式共用同一个提示词构造函数 —— 顺序不能只对流式成立。

        `_distill_longcontext` 曾自己拼提示词、停在旧顺序（角色名写在系统提示开头），
        于是「全文在前」只对了一半的调用。挡住：同步那条再各拼各的。
        """
        llm = MagicMock()
        llm.last_usage = None
        seen: list[tuple] = []
        llm.chat.side_effect = lambda system, messages, max_tokens=None: (
            seen.append((system, messages, max_tokens)), '{"name": "T", "identity": "T"}'
        )[1]
        d = Distiller(llm=llm, config_path=None)
        text = "此处是正文。" * 40
        name = "独一无二的测试角色"
        with patch("core.distiller.count_tokens", return_value=1_000):
            d.distill_incremental(text, name)

        system, messages, max_tokens = seen[0]
        assert system.index(text) < system.index(name), "角色名出现在全文之前，前缀无法共享"
        assert text not in "".join(m["content"] for m in messages), "全文被送了两遍"
        assert max_tokens == Distiller.LONG_OUTPUT_MAX_TOKENS


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


# 上游文案的权威来源 = `adapters/llm_adapter._UPSTREAM_USER_MESSAGES`（401/403 共用
# 同一条）。按本仓惯例**手抄字面量**，不从源码 import —— import 就自证自明，变异杀不掉
# （同 `test_identify_failure_channels` 的 EMPTY_ROSTER_TEXT / PARSE_FAIL_TEXT 口径）。
KEY_TEXT = "API Key 无效或无权限，请到设置页检查"
BALANCE_TEXT = "账户余额不足，请充值后重试"
RATE_TEXT = "请求过于频繁，请稍后再试"
# 适配层给「未登记状态码 / 裸传输层故障」的通用文案（`_GENERIC_USER_ERROR`）。
UNAVAILABLE_TEXT = "服务暂时不可用，请稍后重试"
# 出口自己的兜底（非适配层文案）；判定阶段传的是它自己那一句。
GENERIC_TEXT = "部分片段处理失败，请重试"
JUDGE_FALLBACK_TEXT = "全书角色分组判定未能取得一致结论，请重试"


def _map_distiller(mock_llm) -> Distiller:
    """Map 阶段失败用例共用的蒸馏器：真实形状的 async client + 强制走分片。

    真实的 `LLMAdapter._make_async_client()` 返回 AsyncOpenAI，它的 close() 是**协程**；
    裸 MagicMock 的属性是同步方法，`await client.close()` 会 TypeError。桩要和真实接口
    一致，否则用例考的是 mock 的瑕疵、不是被考的路径。
    """
    async def _close() -> None:
        return None

    client = MagicMock()
    client.close = _close
    mock_llm._make_async_client = MagicMock(return_value=client)

    d = Distiller(llm=mock_llm, config_path=None)
    d._longctx_threshold = 0  # Force Map-Reduce chunked path
    d._chunk_size = 3000
    return d


def _upstream_failing_async(exc: Exception, fail_after: int = 1):
    """async_chat 替身：前 `fail_after` 次成功，之后每次都抛 `exc`。"""
    call_count = [0]

    async def fake(system, messages, max_tokens=None, **kwargs):
        call_count[0] += 1
        if call_count[0] > fail_after:
            raise exc
        return ("分析结果", {"prompt_tokens": 10, "completion_tokens": 5})

    return fake


def _identify_body() -> str:
    """多分片正文：三段各自超过 chunk_size，各带 uuid 避开识别缓存（按文本指纹缓存）。"""
    tag = uuid.uuid4().hex
    return "\n\n".join("角色甲道：" + "话" * 400 + tag for _ in range(3))


def _identify_ok_judge_async(judge_reply):
    """逐片识别回合法名单；判定样本回 `judge_reply`（异常对象即抛出，字符串即原样返回）。

    识别与判定共用 `_run_map_with_client` 同一条骨架，靠系统提示词分流。
    """
    async def fake(system, messages, max_tokens=None, **kwargs):
        if system == IDENTIFY_JUDGE_PROMPT:
            if isinstance(judge_reply, Exception):
                raise judge_reply
            return (judge_reply, {"prompt_tokens": 1, "completion_tokens": 1})
        return (json.dumps([{"name": "角色"}], ensure_ascii=False),
                {"prompt_tokens": 1, "completion_tokens": 1})

    return fake


class TestMapPhaseFailureHandling:
    """>50% Map chunk failures → early bail with clear error message."""

    # ── helpers ──────────────────────────────────────────────────────────

    def _make_429_failing_async(self, fail_after: int = 1):
        """限流重试耗尽 —— 真 `UpstreamFailure`，文案由适配层登记表给（不再用纯文本 "429"）。"""
        return _upstream_failing_async(
            UpstreamFailure("rate limited (429) after 5 attempts: req-abc123",
                            user_message=RATE_TEXT),
            fail_after,
        )

    def _make_generic_failing_async(self, fail_after: int = 1):
        """非上游的本地异常（超时等）—— 出口据「不是 UpstreamFailure」落兜底。"""
        return _upstream_failing_async(RuntimeError("connection timeout"), fail_after)

    def _make_distiller(self, mock_llm) -> Distiller:
        return _map_distiller(mock_llm)

    # With chunk_size=3000, "AB" * 5000 = 10000 chars → 4 chunks (range(0,10000,3000))

    # ── sync path (distill_incremental) ──────────────────────────────────

    def test_sync_429_bail(self):
        """>50% fail with 429 → DistillError；上屏说限流，运维口径（429 / 分片数）只进日志。

        缺陷 17：这两条断言的方向是相反的——上屏**不能**有 429 / 分片，``str()`` **必须**有，
        否则就是把「分了口径」做成了「删了信息」。文案本身只在适配层定义一处。
        """
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = self._make_429_failing_async(fail_after=1)  # 3/4 fail = 75%

        d = self._make_distiller(mock_llm)

        with pytest.raises(DistillError) as excinfo:
            d.distill_incremental("AB" * 5000, "AB")

        assert excinfo.value.user_message == f"蒸馏失败：{RATE_TEXT}"
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
        assert errors[0]["error"] == f"蒸馏失败：{RATE_TEXT}"
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


class TestUpstreamReasonOnScreen:
    """整批失败时上屏**上游真实原因** —— 出口纯函数 + 四条通道。

    缺陷形态：识别 / 判定 / 同步 Map / 流式 Map 四处各自拼上屏文案，其中三处用
    ``"429" in str(exc)`` 自判限流（报错文本里偶然出现 "429" 就当限流），判定阶段更是
    完全不看失败原因。真实原因早由适配层算好放在 ``UpstreamFailure.user_message`` 里，
    到了 core 被丢掉 —— 余额不足 / key 无效一律降级成「部分片段处理失败」。

    期望文案按本仓惯例**手抄字面量**（出处见文件上方那几个常量），断言一律用 ``==``：
    运维口径（分片数 / 报错原文 / request_id）一个字都不许上屏。
    """

    # ── 出口纯函数（`_map_failure_message`）───────────────────────────────

    @pytest.mark.parametrize("user_message,expected", [
        (KEY_TEXT, KEY_TEXT),          # 401 / 403 在适配层表里共用同一条
        (BALANCE_TEXT, BALANCE_TEXT),  # 402
        (RATE_TEXT, RATE_TEXT),        # 429
        ("", UNAVAILABLE_TEXT),        # 未登记状态码（如 500）→ 适配层落通用文案
    ])
    def test_the_exit_carries_the_adapters_wording(self, user_message, expected):
        exc = UpstreamFailure("upstream failed", user_message=user_message)
        assert _map_failure_message("蒸馏失败", [(0, exc)]) == f"蒸馏失败：{expected}"

    def test_status_code_only_no_text_matching(self):
        """报错原文里出现 "429" 不改判 —— 402 仍是余额不足（旧实现按子串自判）。"""
        exc = UpstreamFailure("rate limited (429): req-abc123", user_message=BALANCE_TEXT)
        assert _map_failure_message("蒸馏失败", [(0, exc)]) == f"蒸馏失败：{BALANCE_TEXT}"

    def test_plain_text_429_without_an_upstream_failure_is_not_limiting(self):
        """纯文本 "429"、没有 `UpstreamFailure` → 兜底，不按限流说（不再兼容纯文本）。"""
        exc = RuntimeError("rate limited (429) after 5 attempts")
        assert _map_failure_message("蒸馏失败", [(0, exc)]) == f"蒸馏失败：{GENERIC_TEXT}"

    def test_truncation_as_last_failure_falls_back(self):
        """最后一个失败是**截断**（同一条骨架上的另一种已知失败）→ 兜底，不冒充上游原因。

        出口只认边界出口给的上游 ``kind``：截断的 kind 是 ``incomplete:<finish_reason>``，
        去掉 kind 判断就会把「回复未完成」当成上游原因上屏。
        """
        exc = IncompleteResponseError("length", "identify chunk 0", "半截正文")
        assert _map_failure_message("蒸馏失败", [(0, exc)]) == f"蒸馏失败：{GENERIC_TEXT}"

    def test_no_failure_object_falls_back(self):
        """零个失败对象（识别阶段只有解析失败）→ 兜底。"""
        assert _map_failure_message("识别失败", []) == f"识别失败：{GENERIC_TEXT}"

    def test_mixed_failures_take_the_last(self):
        """混合失败取**完成最晚**那片 —— 锁「取最后一个」这一规则本身。"""
        seq = [(0, UpstreamFailure("429", user_message=RATE_TEXT)),
               (1, UpstreamFailure("402", user_message=BALANCE_TEXT))]
        assert _map_failure_message("蒸馏失败", seq) == f"蒸馏失败：{BALANCE_TEXT}"

    # ── 识别 / 判定通道 ──────────────────────────────────────────────────

    def _identify_distiller(self, mock_llm) -> Distiller:
        d = _map_distiller(mock_llm)
        d._chunk_size = 200      # 正文切成多片，走 `_identify_over_chunks`
        return d

    def test_identify_402_shows_balance(self):
        """逐片识别全失败 → 上屏「识别失败：账户余额不足…」，不是通用文案。"""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = _upstream_failing_async(
            UpstreamFailure("402 payment required: req-abc123", user_message=BALANCE_TEXT))
        d = self._identify_distiller(mock_llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(_identify_body())

        assert excinfo.value.user_message == f"识别失败：{BALANCE_TEXT}"
        assert "个分片" not in excinfo.value.user_message

    def test_judge_402_shows_balance(self):
        """逐片成功、判定样本全失败 → 上屏同一句上游原因（判定不再自己编文案）。"""
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = _identify_ok_judge_async(
            UpstreamFailure("402 payment required: req-abc123", user_message=BALANCE_TEXT))
        d = self._identify_distiller(mock_llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(_identify_body())

        assert excinfo.value.user_message == f"识别失败：{BALANCE_TEXT}"

    def test_judge_disagreement_keeps_own_message(self):
        """执行器一次没失败、只是样本都不合法 → 落判定阶段自己的文案，不带上游原因。

        这是兜底参数唯一的守门用例（`fallback=` 只在判定这一处传非默认值）。
        """
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = _identify_ok_judge_async("这不是 JSON")
        d = self._identify_distiller(mock_llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(_identify_body())

        assert excinfo.value.user_message == f"识别失败：{JUDGE_FALLBACK_TEXT}"

    # ── 同步 / 流式 Map 通道 ─────────────────────────────────────────────

    def _map_failing(self, exc: Exception) -> Distiller:
        mock_llm = MagicMock()
        mock_llm.last_usage = None
        mock_llm.async_chat = _upstream_failing_async(exc)
        return _map_distiller(mock_llm)

    def test_sync_402_bail_shows_balance(self):
        """>50% 分片 402 → 同步 Map 上屏「蒸馏失败：账户余额不足…」。"""
        d = self._map_failing(
            UpstreamFailure("402 payment required: req-abc123", user_message=BALANCE_TEXT))

        with pytest.raises(DistillError) as excinfo:
            d.distill_incremental("AB" * 5000, "AB")

        assert excinfo.value.user_message == f"蒸馏失败：{BALANCE_TEXT}"
        assert "个分片" not in excinfo.value.user_message

    def test_stream_402_bail_shows_balance(self):
        """>50% 分片 402 → 流式 Map 的错误帧是同一句。"""
        d = self._map_failing(
            UpstreamFailure("402 payment required: req-abc123", user_message=BALANCE_TEXT))

        results = list(d.distill_incremental_stream("AB" * 5000, "AB"))

        errors = [r for r in results if isinstance(r, dict) and "error" in r]
        assert len(errors) == 1
        assert errors[0]["error"] == f"蒸馏失败：{BALANCE_TEXT}"
        assert "个分片" not in errors[0]["error"]


class TestFormatFieldGroups:
    """WP7 F3：各组 ∪ 后置字段 == CharacterCard.model_fields，两两无交集。

    分组与后置字段定义在 core/schema.py 一处（紧挨 CharacterCard），本测试直接读那份
    定义，不另存副本。变异 = 从某组删一个字段 → 并集缺项，union 断言变红。
    """

    def test_groups_cover_model_fields_exactly_once(self):
        from core.schema import FORMAT_GROUPS, POST_FORMAT_FIELDS, CharacterCard

        all_fields = set(CharacterCard.model_fields)
        buckets = [set(fields) for fields in FORMAT_GROUPS.values()]
        buckets.append(set(POST_FORMAT_FIELDS))

        union = set().union(*buckets)
        assert union == all_fields, (
            f"分组未覆盖全部字段：缺={sorted(all_fields - union)} "
            f"多（不存在于 model_fields）={sorted(union - all_fields)}"
        )

        for i in range(len(buckets)):
            for j in range(i + 1, len(buckets)):
                overlap = buckets[i] & buckets[j]
                assert not overlap, f"第 {i} 桶与第 {j} 桶重叠：{sorted(overlap)}"

    def test_group_schema_is_scoped_to_the_group(self):
        """组提示词带的子 schema 只列本组字段（由模型输出契约 CardDraft 取，不手写）。"""
        from core.card_draft import draft_schema
        from core.schema import FORMAT_GROUPS

        for group, fields in FORMAT_GROUPS.items():
            sub = draft_schema(group)
            assert set(sub["properties"]) == set(fields), group
            assert set(sub.get("required", ())) <= set(fields), group
            # 嵌套模型定义必须带上，否则 $ref 解析不了（草稿形态由登记表派生，名字不写死）
            refs = [r.rsplit("/", 1)[-1] for r in _refs(sub)]
            assert refs, group
            missing = [r for r in refs if r not in sub.get("$defs", {})]
            assert not missing, f"{group} 的 $ref 解析不了：{missing}"


# ── WP7：格式化按字段组并行 ───────────────────────────────────────────────

class _FakeAsyncClient:
    """Map 阶段要 `_make_async_client()` 才跑得起来（建 client → 跑 → 关）。"""

    async def close(self):
        pass


# 组模板里的键名互不重叠，故拿它认「这条调用是哪一组」。key_memories 已随阶段编号从
# G4 挪到 G6（本段 §3.2），G4 只剩 psyche。
_FORMAT_GROUP_MARKERS = (
    ("G1", '"name": "角色名"'),
    ("G2", '"personality_traits"'),
    ("G3", '"speaking_style"'),
    ("G4", '"psyche"'),
    ("G5", '"relationships"'),
    ("G6", '"situation_behaviors"'),
)
_FORMAT_GROUP_ORDER = [g for g, _ in _FORMAT_GROUP_MARKERS]


# 关系分批的提示词不是任何一个字段组（§4.5）：主调用只出名单，细节这一跳按 10 人一批补。
# 认成一个「伪组」是为了让共用桩的那几个模块不必各自再写一支分支 —— 组回复里回详情即可。
REL_BATCH_GROUP = "REL_BATCH"
_REL_BATCH_MARKER = "只产出这几个人物与主角的关系"


def _format_group_of(system: str) -> str | None:
    """从格式化系统提示词认出组别；关系分批那一跳返回 `REL_BATCH_GROUP`；认不出返回 None。"""
    if _REL_BATCH_MARKER in system:
        return REL_BATCH_GROUP
    for group, marker in _FORMAT_GROUP_MARKERS:
        if marker in system:
            return group
    return None


def _timed(value: str) -> dict:
    """状态/经历类字段的草稿形态：一条取值 + 它在哪一段原文里成立（§4.2 由登记表派生）。"""
    return {"value": value, "occurrences": [{"phase": 1, "quote": "开头甲甲甲"}]}


def _refs(node):
    """子 schema 里出现的所有 `$ref`（判「嵌套定义都带上了」用，名字不写死）。"""
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for v in node.values():
            yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


_SAMPLE_FIELD_VALUES = {
    "name": "角色",
    "identity": "一句话身份",
    "background": "背景摘要",
    "personality_traits": [_timed("特质（原文证据）")],
    "values": [_timed("价值观")],
    "inner_tensions": [_timed("内在矛盾")],
    "emotional_patterns": [_timed("情感模式")],
    "decision_style": [_timed("谨慎型")],
    "speaking_style": {"tone": [_timed("冷淡")], "sentence_pattern": [_timed("短句")],
                       "catchphrases": [_timed("哼")],
                       "vocabulary_level": "日常", "taboo_words": []},
    "first_message": "你来了。",
    "cognitive": {"education_level": "普通", "knowledge_scope": [_timed("常识")],
                  "speech_style": [_timed("平实")], "vocabulary_level": "日常"},
    # G5 只出名单（维度 F）：细节由关系分批补齐，故组回复里只有 target。
    "relationships": [{"target": "某人"}],
    "key_memories": [{"memory": "关键经历",
                      "occurrences": [{"phase": 1, "quote": "开头甲甲甲"}]}],
    "character_arc": {"axis": "从甲到乙", "phases": [{"label": "阶段一", "state": "开头时的状态"}]},
    "situation_behaviors": [{"situation": "被人质疑", "behavior": "先反问再解释",
                             "source_quote": ""}],
    "psyche": {"openness": 3, "conscientiousness": 3, "extraversion": 3,
               "agreeableness": 3, "neuroticism": 3, "affinity_baseline": 50,
               "volatility": "适中", "grudge_inertia": "一般",
               "triggers": [_timed("雷点")], "soft_spots": [_timed("软肋")]},
}


_SAMPLE_RELATION_DETAILS = [
    {"target": "某人", "relation": "朋友", "attitude": "亲近", "note": "认识很久的朋友",
     "attitudes": [{"phase": 1, "attitude": "亲近", "quote": ""}]},
]


def _group_reply(group: str) -> str:
    """按组回一份字段齐备的 JSON —— 组字段表从 schema 读，不另抄一份。

    `REL_BATCH_GROUP` 回关系详情（分批那一跳要的是 `list[DraftRelationship]`，不是字段组）。
    """
    if group == REL_BATCH_GROUP:
        return json.dumps(_SAMPLE_RELATION_DETAILS, ensure_ascii=False)
    from core.schema import FORMAT_GROUPS as _FG

    return json.dumps({k: _SAMPLE_FIELD_VALUES[k] for k in _FG[group]})


class TestFormatGroupsRunInParallel:
    """WP7 F1 + 本段 §4.4：不依赖阶段的组（G1/G5/G6）先并行，依赖组（G2/G3/G4）等 G6 返回后并行。

    并行判据是两批各自的 `threading.Barrier`：串行实现等不齐、2 s 后破障、该组失败，
    成品卡出不来（无 str 帧）。依赖判据是 G6 的事件早于依赖组的第一条事件，且依赖组的提示词
    带上了 G6 定出的阶段列表（G6 没先返回两者都拿不到）。记账判据是每组一条 `distill_format`、
    各带不同 usage。组划分从 `PHASE_DEPENDENT_GROUPS`（登记表推导）读，组数从 `FORMAT_GROUPS`
    读，都不写死。

    变异：① 所有组改回串行 ② 依赖组不等 G6 就启动（提示词缺 G6 的阶段列表）。
    """

    CHUNK = 3000
    TEXT = "AB" * (1500 * 50)   # 150000 字符 → 50 片（≤80，走单次合并）

    def test_dependent_groups_wait_for_g6_and_both_batches_run_parallel(self, monkeypatch):
        from core.distiller import PHASE_DEPENDENT_GROUPS

        n_groups = len(FORMAT_GROUPS)
        first = [g for g in FORMAT_GROUPS if g not in PHASE_DEPENDENT_GROUPS]
        second = sorted(PHASE_DEPENDENT_GROUPS)
        barriers = {"first": threading.Barrier(len(first), timeout=2),
                    "second": threading.Barrier(len(second), timeout=2)}
        events: list[str] = []
        dep_systems: list[str] = []
        rows: list[tuple[str, dict | None]] = []

        def _record(*, storage, llm, action, usage, source):
            rows.append((action, usage))

        monkeypatch.setattr("core.distiller.try_record_usage", _record)

        class _LLM:
            last_usage = None
            model = "m"

            def _make_async_client(self):
                return _FakeAsyncClient()

            async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
                return ("片段分析", {"prompt_tokens": 1, "completion_tokens": 1})

            def chat_stream_long(self, system, messages, max_tokens=None, **kw):
                if "你正在整合关于" in system:
                    yield "合并结果"
                    return {"prompt_tokens": 1, "completion_tokens": 1}
                if "只产出这几个人物与主角的关系" in system:   # 关系分批调用
                    events.append("rel")
                    yield json.dumps(_SAMPLE_RELATION_DETAILS, ensure_ascii=False)
                    return {"prompt_tokens": 900, "completion_tokens": 90}
                group = _format_group_of(system)
                if group in PHASE_DEPENDENT_GROUPS:
                    dep_systems.append(system)
                barriers["first" if group in first else "second"].wait()
                events.append(f"group:{group}")
                yield _group_reply(group)
                idx = _FORMAT_GROUP_ORDER.index(group)
                return {"prompt_tokens": 10 + idx, "completion_tokens": idx}

        d = Distiller(llm=_LLM(), config_path=None)
        d._longctx_threshold = 0
        d._chunk_size = self.CHUNK

        frames = []
        for f in d.distill_incremental_stream(
            self.TEXT, "AB", aliases=[], text_type="story"
        ):
            frames.append(f)
            if isinstance(f, dict) and f.get("status") == "formatting":
                events.append("formatting")

        fmt_rows = [u for a, u in rows if a == "distill_format"]
        assert len(fmt_rows) == n_groups, f"distill_format 记账不是 {n_groups} 条：{rows}"
        assert len({json.dumps(u, sort_keys=True) for u in fmt_rows}) == n_groups, (
            f"各组账目分不开：{fmt_rows}"
        )
        assert events.count("formatting") == 1, f"formatting 帧不是恰 1 次：{events}"
        assert events[0] == "formatting", f"formatting 帧不在各组调用之前：{events}"
        assert sum(1 for e in events if e.startswith("group:")) == n_groups, events
        assert sum(1 for f in frames if isinstance(f, str)) == 1, "成品卡不是恰 1 个 str 帧"
        # 依赖组等到了 G6：G6 的事件在前，且提示词带上了 G6 定出的阶段
        g6_at = events.index("group:G6")
        assert all(events.index(f"group:{g}") > g6_at for g in second), (
            f"依赖组没等 G6：{events}")
        assert dep_systems and all("阶段一" in s for s in dep_systems), (
            f"依赖组的提示词没带 G6 的阶段列表：{dep_systems[:1]}")
        # 关系分批在依赖组之后跑（它要 G5 的名单 + G6 的阶段）
        assert events.index("rel") > g6_at, events


class TestBatchCountDecidesTotalMerge:
    """WP7 F2：批数决定是否先总合并。

    100 片（2 批）→ 归并恰 2 次（**无总合并**），各组输入都含两批结果；
    50 片（1 批）→ 归并恰 1 次，各组输入就是这次合并的输出。

    变异：① 恢复总合并（100 片那条变 3 次）② 一律跳过合并（50 片那条变 0 次）。
    """

    CHUNK = 3000
    MARK = "M-标记分析"
    TEXT_100 = "AB" * (1500 * 80) + "CD" * (1500 * 20)   # 100 片，后 20 片带标记
    TEXT_50 = "AB" * (1500 * 50)                          # 50 片
    ALIASES = ["AB", "CD"]

    def _run(self, text: str, aliases: list[str]) -> tuple[list[str], list[str]]:
        seen = {"reduce": [], "format": []}
        mark = self.MARK

        class _LLM:
            last_usage = None
            model = "m"

            def _make_async_client(self):
                return _FakeAsyncClient()

            async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
                body = messages[0]["content"]
                return ((mark if "CD" in body else "普通分析"),
                        {"prompt_tokens": 1, "completion_tokens": 1})

            def chat_stream_long(self, system, messages, max_tokens=None, **kw):
                body = messages[0]["content"]
                if "你正在整合关于" in system:
                    reply = "合并乙" if mark in body else "合并甲"
                    seen["reduce"].append(reply)
                elif "只产出这几个人物与主角的关系" in system:   # 关系分批，不算格式化组
                    reply = json.dumps(_SAMPLE_RELATION_DETAILS, ensure_ascii=False)
                else:
                    seen["format"].append(body)
                    reply = _group_reply(_format_group_of(system))
                yield reply
                return {"prompt_tokens": 1, "completion_tokens": 1}

        d = Distiller(llm=_LLM(), config_path=None)
        d._longctx_threshold = 0
        d._chunk_size = self.CHUNK
        list(d.distill_incremental_stream(text, "角色", aliases=aliases, text_type="story"))
        return seen["reduce"], seen["format"]

    def test_two_batches_skip_the_total_merge(self):
        reduces, formats = self._run(self.TEXT_100, self.ALIASES)

        assert reduces == ["合并甲", "合并乙"] or reduces == ["合并乙", "合并甲"], (
            f"分批归并次数不对（应恰 2 次、无总合并）：{reduces}"
        )
        assert len(formats) == len(FORMAT_GROUPS), f"格式化不是各组一次：{len(formats)}"
        for body in formats:
            assert "合并甲" in body and "合并乙" in body, (
                f"各组没有直接读到两批归并结果：{body[-120:]}"
            )

    def test_single_batch_merges_once_then_formats_from_it(self):
        reduces, formats = self._run(self.TEXT_50, [])

        assert reduces == ["合并甲"], f"≤80 片应恰 1 次归并：{reduces}"
        assert len(formats) == len(FORMAT_GROUPS), f"格式化不是各组一次：{len(formats)}"
        for body in formats:
            assert body.endswith("合并甲"), (
                f"各组输入不是这次合并的输出：{body[-120:]}"
            )


class TestFormatGroupFailureYieldsNoCard:
    """WP7 F5：任一组失败不拼半张卡 —— 有 error 帧、无 str 帧。

    变异：失败组填 `{}` 继续（其余组合得出一张「看起来合法」的卡 → 出现 str 帧）。
    """

    CHUNK = 3000
    TEXT = "AB" * (1500 * 50)

    def test_third_group_failure_yields_error_and_no_card(self):
        class _LLM:
            last_usage = None
            model = "m"

            def _make_async_client(self):
                return _FakeAsyncClient()

            async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
                return ("片段分析", {"prompt_tokens": 1, "completion_tokens": 1})

            def chat_stream_long(self, system, messages, max_tokens=None, **kw):
                if "你正在整合关于" in system:
                    yield "合并结果"
                    return {"prompt_tokens": 1, "completion_tokens": 1}
                group = _format_group_of(system)
                if group == "G3":
                    raise RuntimeError("G3 组炸了")
                yield _group_reply(group)
                return {"prompt_tokens": 1, "completion_tokens": 1}

        d = Distiller(llm=_LLM(), config_path=None)
        d._longctx_threshold = 0
        d._chunk_size = self.CHUNK
        frames = list(d.distill_incremental_stream(
            self.TEXT, "AB", aliases=[], text_type="story"
        ))

        errors = [f for f in frames if isinstance(f, dict) and "error" in f]
        assert len(errors) == 1, f"组失败没上屏 error 帧：{frames[-4:]}"
        assert not any(isinstance(f, str) for f in frames), "有组失败却拼出了卡"


# ── WP14 A2：Map 并发按账户自适应（闸只在每次 create 外，退避不占名额）─────────


def _reply(handler, status: int, payload: dict, headers: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    for name, value in headers.items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


class _InflightUpstream:
    """非流式假上游，按**在途请求数**限流：> `limit` 即 429（DeepSeek 原文形态）。

    为什么必须是真 socket：A2 要判的是「闸把**同时**在途的请求数压住了」，而重叠只有
    上游侧看得见 —— 桩 client 只能记「调了几次」，看不见两个请求有没有叠在一起。

    `peak` 是整场的在途峰值；`tail_peak` 跳过前 `warm` 个请求之后才计（冷启动必然从
    `map_concurrency` 探起 —— 那不是「没收敛」，是 AIMD 的第一探，故峰值判据只看尾部）；
    `statuses` 按到达次序记每个请求的结果。

    429 带 `Retry-After: 0`：走 `_classify_retry` 的「有则读」分支，退避瞬时，用例不必
    真等 2/4/8s。取 0 是安全的 —— 乘性下调落在「还名额」之前，重发抢不到旧上限。

    `hold_ms` 是每个请求占住连接的时间。**不是装饰**：本地回一个 JSON 只要几十微秒，
    20 个并发请求在服务端几乎不重叠（实测峰值 2），那就什么也没测。撑开一个窗口，重叠
    才成为看得见的事实 —— 实测 hold_ms=60 时 20 并发能看到峰值 20。
    """

    def __init__(self, limit: int, *, warm: int = 0, retry_after: int = 0,
                 hold_ms: int = 60) -> None:
        self.limit = limit
        self.retry_after = retry_after
        self.hold_ms = hold_ms
        self.peak = 0
        self.tail_peak = 0
        self.statuses: list[int] = []
        self._warm = warm
        self._served = 0
        self._inflight = 0
        self._lock = threading.Lock()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                if n:
                    self.rfile.read(n)
                with outer._lock:
                    outer._inflight += 1
                    outer._served += 1
                    outer.peak = max(outer.peak, outer._inflight)
                    if outer._served > outer._warm:
                        outer.tail_peak = max(outer.tail_peak, outer._inflight)
                    throttled = outer._inflight > outer.limit
                    outer.statuses.append(429 if throttled else 200)
                try:
                    time.sleep(outer.hold_ms / 1000.0)
                    if throttled:
                        _reply(self, 429, {"error": {
                            "message": (
                                "Too many requests. Your current concurrency is "
                                f"{outer._inflight}, which exceeds your concurrency limit "
                                f"of {outer.limit} based on your remaining balance."),
                            "type": "rate_limit_error", "param": None,
                            "code": "invalid_request_error",
                        }}, {"Retry-After": str(outer.retry_after)})
                    else:
                        _reply(self, 200, {
                            "id": "fake-upstream", "object": "chat.completion", "created": 0,
                            "model": "fake-model",
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant", "content": "分析"}}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                      "total_tokens": 2},
                        }, {})
                finally:
                    with outer._lock:
                        outer._inflight -= 1

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/v1"

    def adapter(self):
        return M.LLMAdapter(api_key="sk-fake-local", base_url=self.base_url, model="fake-model")

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


class TestMapConcurrencyAdaptsToTheAccount:
    """WP14 A2：上游「超过 N 路即 429」时 Map 零失败，且闸放行时在途从不超过它当时的自身上限。

    变异：闸只包整次调用（不进重试循环）—— 退避期间名额没还掉。`test_backoff_...`
    读的就是退避那一刻的 `gate.inflight`，直接打红；端到端那条负责证明「零失败」。

    片数取 80 而不是规格里写的 40：闸的初始上限就是 `map_concurrency`（AIMD 从 cap 探起），
    冷启动那一波齐发即大面积 429；片数够多，`admissions` 才覆盖得到「上限已被乘性下调、
    闸仍按新上限放行」的样本。
    """

    CHUNKS = 80
    LIMIT = 5
    CAP = 20

    async def test_map_finishes_with_zero_failures(self, monkeypatch):
        """上游限流时 Map 零失败；**闸自己的不变量** = 每次放行时在途 ≤ 当时上限。

        两层判据：
        ① 闸的不变量 —— 每次放行后记 (ceiling, inflight)，断言 inflight ≤ ceiling。准入是
           `while self._inflight >= self._ceiling: await wait()` 再 +1（`core/concurrency.py`），
           这把锁的就是它。仪器包在 `__aenter__` 之外，**不读上游侧的在途数** —— 那是另一
           回事：上限乘性下调时已在途的那批会暂时高于**新**上限
           （`tests/test_concurrency.py::TestAcquireWaitsWhileTheCeilingSitsBelowInflight`
           正锁着这个「高于上限就不再放行」的行为）。
        ② 端到端 —— 全部分片零失败，且假上游确实限过流（`n429 > 0`，证明走到了被锁处）。

        **不**断言上游侧在途峰值 ≤ LIMIT+1：闸的上限是 AIMD 锯齿逼近的估计值，满载成功会
        把它加性上探（`on_success`，探到 cap 为止），而 200 不含「离上限多远」的信息 ——
        收敛期上限探到 LIMIT+2 是算法的正常一步（见 `TestMapConcurrencyRemembersTheAccount`
        的注释：实测 4–7、负载高时见过一次 9）。AIMD 的算术（乘性下调、加性上探、退避不
        占名额、排队顺延）由 A1 的 `tests/test_concurrency.py` 与同文件 A3/A4 的确定性用例守着。

        变异：准入改成 `while self._inflight > self._ceiling`（允许越限一格）→ 某次放行
        记到 inflight = ceiling + 1，① 变红。
        """
        admissions: list[tuple[int, int]] = []

        class _RecordingGate(AdaptiveGate):
            """记下每次放行后的 (ceiling, inflight)。

            子类化而非包装实例：闸由 `_run_map_concurrent` 自己构造，只有替换类才拿得到
            每一次准入。`__aenter__` 先走父类（真准入），再读闸自报的状态。
            """

            async def __aenter__(self):
                gate = await super().__aenter__()
                admissions.append((self.ceiling, self.inflight))
                return gate

        # 全仓只 `core/distiller.py` 一处构造闸（`C.AdaptiveGate`），替换模块属性即覆盖。
        monkeypatch.setattr("core.concurrency.AdaptiveGate", _RecordingGate)

        upstream = _InflightUpstream(self.LIMIT)
        llm = upstream.adapter()
        try:
            d = Distiller(llm=llm, config_path=None)
            d._map_concurrency = self.CAP
            client = llm._make_async_client()
            try:
                results, failures = await d._run_map_concurrent(
                    [f"第{i}片正文" for i in range(self.CHUNKS)],
                    lambda chunk: ("你是角色分析专家", f"分析：{chunk}"),
                    "distill_map", client=client,
                )
            finally:
                await client.close()
        finally:
            upstream.close()

        assert upstream.statuses.count(429) > 0, "假上游一次都没限流 —— 这条锁没走到被锁的地方"
        assert failures == [], f"限流把片打挂了：{failures[:3]}"
        assert len(results) == self.CHUNKS
        # 每片至少放行一次；少于此说明仪器没覆盖到全部放行（锁空转）。
        assert len(admissions) >= self.CHUNKS, (
            f"只记到 {len(admissions)} 次放行（< {self.CHUNKS} 片）—— 这条锁空转")
        over = [(ceil, inf) for ceil, inf in admissions if inf > ceil]
        assert not over, (
            f"闸放行时在途超过了自己当时的上限（ceiling, inflight）：{over[:3]}"
            f"（共 {len(over)} 次越限 / 全部 {len(admissions)} 次放行）")

    async def test_backoff_does_not_hold_a_slot(self, monkeypatch):
        """退避睡眠必须落在闸**外**：睡着的请求若占着名额，上限永远探不上去。

        读数是「退避那一刻闸的在途数」—— 它不随墙钟变，只随「名额有没有提前还掉」变，
        正是这条锁的对象。
        """
        upstream = _InflightUpstream(limit=0, retry_after=0)  # 永远 429
        llm = upstream.adapter()
        gate = AdaptiveGate(cap=8)
        seen: list[int] = []
        real_sleep = asyncio.sleep

        async def _spy(seconds, *a, **kw):
            seen.append(gate.inflight)
            await real_sleep(0)  # 不真等退避

        monkeypatch.setattr(M, "asyncio", SimpleNamespace(sleep=_spy))
        try:
            with pytest.raises(M.UpstreamFailure):
                await llm.async_chat("sys", [{"role": "user", "content": "u"}], gate=gate)
        finally:
            upstream.close()

        assert seen, "一次退避都没发生 —— 这条锁没走到被锁的地方"
        assert seen == [0] * len(seen), f"退避时仍占着名额：{seen}"
        assert gate.ceiling < 8, "429 没喂到闸上"


class TestMapConcurrencyRemembersTheAccount:
    """WP14 A3：同一个 adapter 连跑两轮，第二轮从第一轮学到的上限起步。

    判据是**行为**：第二轮只在冷启动那一波露初值，故第二轮只跑 `COLD` 片 —— 冷启动那
    一波就是初值本身。等长两轮的「429 明显更少」实测比值在 1.6–3.1x 之间乱摆（第二轮
    自己也在探针上撞），那种计数比会 flaky，不拿来当判据。只读
    `llm.learned_map_concurrency` 非空也不足以证明第二轮**用了**它 —— 初值没接上时那个
    属性照样有值，退回的是 cap。

    峰值界取 `max(learned, LIMIT) + 1` 而不是 `learned + 1`：第一轮学到的值可能**低于**
    上游真实上限（负载下多撞几下就偏低），第二轮从那儿合法地继续上探到 `LIMIT` 再撞一次
    探针 —— 那是 AIMD 在工作，不是缺陷。`+1` 仍是「必须探到上限之上才学得到上限」。

    三条变异各打红一处：不写回 → 属性为 None；写了但不作初值 → 冷启动峰值从 `learned`
    变成 `COLD`（20 片在 cap=20 下全放）；写回的是配置值而非收敛值 → `learned` 顶到 cap。
    """

    LEARN_CHUNKS = 120
    COLD = 20
    LIMIT = 5
    CAP = 20

    async def _round(self, d, llm, tag: str, n: int):
        client = llm._make_async_client()
        try:
            return await d._run_map_concurrent(
                [f"{tag}{i}片正文" for i in range(n)],
                lambda chunk: ("你是角色分析专家", f"分析：{chunk}"),
                "distill_map", client=client,
            )
        finally:
            await client.close()

    async def test_the_second_round_starts_from_the_learned_ceiling(self):
        upstream = _InflightUpstream(self.LIMIT)
        llm = upstream.adapter()
        try:
            d = Distiller(llm=llm, config_path=None)
            d._map_concurrency = self.CAP

            await self._round(d, llm, "一", self.LEARN_CHUNKS)
            learned = llm.learned_map_concurrency
            first_429 = upstream.statuses.count(429)
            upstream.peak = 0
            upstream.tail_peak = 0
            upstream.statuses.clear()

            _, failures = await self._round(d, llm, "二", self.COLD)
            second_429 = upstream.statuses.count(429)
        finally:
            upstream.close()

        assert learned is not None, "第一轮没把学到的上限写回 adapter"
        assert failures == []
        # 界不是拍的：146 次采样（60 / 120 片各半）实测落在 4–7，只在机器负载高时见过
        # 一次 9（尾部排水期变长，闸把「没撞 429」读成余量、继续加性上探）。取「真实上限
        # 的两倍」把收敛值与配置值分开 —— 写回 cap 的变异给的是 20，余量还有一半。
        assert learned <= self.LIMIT * 2, (
            f"学到的上限 {learned} 超过上游真实上限 {self.LIMIT} 的两倍 —— 写回的是配置值"
            f"cap={self.CAP} 而不是闸收敛出来的值")
        bound = max(learned, self.LIMIT) + 1
        assert upstream.peak <= bound, (
            f"第二轮冷启动在途峰值 {upstream.peak}，高于 {bound}（= max(学到的 {learned}, "
            f"上游上限 {self.LIMIT}) + 1）—— 没有从学到的上限起步，{self.COLD} 片一起放了。"
            f"第一轮 {self.LEARN_CHUNKS} 片 {first_429} 次 429、"
            f"第二轮 {self.COLD} 片 {second_429} 次")


class TestQueueingDoesNotEatTheCallDeadline:
    """WP14 A4：等闸（排队）不占本调用的总时限。

    缺陷形态（修前的 :816/:824）：`_RetryBudget` 在**进闸之前**就建好、时钟当场起算，
    排队时长直接吃总预算 —— 尾部分片可能一次 create 都没发出去就被判「没时间了」。
    真机形态：红楼梦 242 片、账号上限 18，末尾的片光排队就超过 153s 的总预算。

    判据是**行为**：排队时长远超单片预算（取值见类属性注释），顺延生效时全部成功；不生效时尾部的片判超时。

    变异：去掉 `async_chat` 里 `budget.extend_deadline(...)` 那一行 → 尾部分片红。

    不走 `_run_map_concurrent`：它不接受单片预算，而 `_GEN_BATCH_DEADLINE_S` 是**函数定义
    时**绑进 `async_chat` 形参默认值的（monkeypatch 模块常量改不动它）。直接以 `gate=` +
    `deadline_s=` 调 `async_chat` 走的是同一段代码，且落在真正的接缝上。
    """

    LIMIT = 3        # 上游：在途 > 3 即 429；闸的 cap 也取它，闸不会上探到限流
    HOLD_MS = 200
    # 单片预算 = 收尾余量 + 5 倍单次占用：进闸后单次超时是「预算 − 余量」，负载下单次
    # 尝试慢到 5 倍仍在超时内。数值由适配器常量推出，不写死。
    DEADLINE_S = M._ATTEMPT_TIMEOUT_MARGIN_S + 5 * HOLD_MS / 1000
    # 片数：最后一波排队（≈ 片数 / 上限 × 占用）至少是预算的 2 倍，去掉顺延必然判超时。
    N = LIMIT * math.ceil(2 * DEADLINE_S / (HOLD_MS / 1000))

    async def test_tail_chunks_survive_the_queue(self):
        upstream = _InflightUpstream(self.LIMIT, hold_ms=self.HOLD_MS)
        llm = upstream.adapter()
        # cap 取上游上限：本条只判「排队不占预算」，不引入 AIMD 上探撞出的 429 ——
        # 429 重试吃的是本片自己的预算（设计如此，重试预算另有用例锁），混进来会让
        # 负载下的单次变慢把判据搅成 flaky。
        gate = AdaptiveGate(cap=self.LIMIT, initial=self.LIMIT)
        client = llm._make_async_client()
        t0 = time.monotonic()
        try:
            results = await asyncio.gather(*[
                llm.async_chat("sys", [{"role": "user", "content": f"第{i}片"}],
                               client=client, gate=gate, deadline_s=self.DEADLINE_S)
                for i in range(self.N)
            ], return_exceptions=True)
        finally:
            await client.close()
            upstream.close()
        elapsed = time.monotonic() - t0

        failures = [r for r in results if isinstance(r, BaseException)]
        # 判据放在空转门**之前**：去掉顺延的变异下，挂掉的片跑得快，整场反而不到 2s，
        # 空转门会先炸、把真正的红源（片被判超时）盖掉。顺序只影响报错信息，判绿判红不变。
        notime = sum("no time for an attempt" in repr(f) for f in failures)
        assert failures == [], (
            f"排队把 {len(failures)} 片判成超时，其中 {notime} 片是「预算里挤不出一次 "
            f"attempt」（修复前正是这样挂的，尾部那批）：{failures[:2]!r}")
        assert upstream.peak >= self.LIMIT, (
            f"上游在途峰值只有 {upstream.peak} < 上限 {self.LIMIT} —— 闸没被占满，排队压力没形成")
        assert elapsed > self.DEADLINE_S, (
            f"整场只跑了 {elapsed:.2f}s ≤ 单片预算 {self.DEADLINE_S}s —— 排队没超过单片预算，"
            f"这条锁空转")
        assert len(results) == self.N
