"""Tests for long-context routing: token estimation + threshold branching."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import adapters.llm_adapter as M
from core.concurrency import AdaptiveGate
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



class TestFormatFieldGroups:
    """WP7 F3：4 组 ∪ 后置字段 == CharacterCard.model_fields，两两无交集。

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
        """组提示词带的子 schema 只列本组字段（由 CharacterCard 取，不手写）。"""
        from core.schema import FORMAT_GROUPS, CharacterCard, format_group_schema

        for group, fields in FORMAT_GROUPS.items():
            sub = format_group_schema(group)
            assert set(sub["properties"]) == set(fields), group
            assert set(sub.get("required", ())) <= set(fields), group
            # 嵌套模型定义必须带上，否则 $ref 解析不了
            assert "PsycheProfile" in sub.get("$defs", {}), group


# ── WP7：格式化按字段组并行 ───────────────────────────────────────────────

class _FakeAsyncClient:
    """Map 阶段要 `_make_async_client()` 才跑得起来（建 client → 跑 → 关）。"""

    async def close(self):
        pass


# 组模板里的键名互不重叠，故拿它认「这条调用是哪一组」。
_FORMAT_GROUP_MARKERS = (
    ("G1", '"name": "角色名"'),
    ("G2", '"personality_traits"'),
    ("G3", '"speaking_style"'),
    ("G4", '"relationships"'),
)
_FORMAT_GROUP_ORDER = [g for g, _ in _FORMAT_GROUP_MARKERS]


def _format_group_of(system: str) -> str | None:
    """从格式化系统提示词认出组别；认不出返回 None（该断言的用例会因此变红）。"""
    for group, marker in _FORMAT_GROUP_MARKERS:
        if marker in system:
            return group
    return None

_SAMPLE_FIELD_VALUES = {
    "name": "角色",
    "identity": "一句话身份",
    "background": "背景摘要",
    "personality_traits": ["特质（原文证据）"],
    "values": ["价值观"],
    "inner_tensions": ["内在矛盾"],
    "emotional_patterns": ["情感模式"],
    "decision_style": "谨慎型",
    "speaking_style": {"tone": "冷淡", "sentence_pattern": "短句", "catchphrases": ["哼"],
                       "vocabulary_level": "日常", "taboo_words": []},
    "dialogue_examples": ["对方：你来了\n角色：嗯"],
    "first_message": "你来了。",
    "cognitive": {"education_level": "普通", "knowledge_scope": "常识",
                  "speech_style": "平实", "vocabulary_level": "日常"},
    "relationships": [{"target": "某人", "relation": "朋友", "attitude": "亲近",
                       "note": "认识很久的朋友"}],
    "key_memories": ["关键经历"],
    "character_arc": ["阶段一"],
    "psyche": {"openness": 3, "conscientiousness": 3, "extraversion": 3,
               "agreeableness": 3, "neuroticism": 3, "affinity_baseline": 50,
               "volatility": "适中", "grudge_inertia": "一般",
               "triggers": ["雷点"], "soft_spots": ["软肋"]},
}


def _group_reply(group: str) -> str:
    """按组回一份字段齐备的 JSON —— 组字段表从 schema 读，不另抄一份。"""
    from core.schema import FORMAT_GROUPS as _FG

    return json.dumps({k: _SAMPLE_FIELD_VALUES[k] for k in _FG[group]})


class TestFormatGroupsRunInParallel:
    """WP7 F1：4 组并行、各记各账、`formatting` 帧恰 1 次且在 4 组之前。

    并行判据是 `threading.Barrier(4)` —— 串行实现等不到第 4 个，2 s 后破障，该组失败，
    成品卡就出不来（无 str 帧）。记账判据是 4 条 `distill_format` 各带不同 usage。

    变异：① 改回串行 ② 每组各发一次 `formatting`。
    """

    CHUNK = 3000
    TEXT = "AB" * (1500 * 50)   # 150000 字符 → 50 片（≤80，走单次合并）

    def test_four_groups_run_in_parallel_and_account_separately(self, monkeypatch):
        barrier = threading.Barrier(4, timeout=2)
        events: list[str] = []
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
                group = _format_group_of(system)
                barrier.wait()            # 串行实现到这里等不齐 4 个 → 破障
                idx = _FORMAT_GROUP_ORDER.index(group) if group else -1
                events.append(f"group:{group}")
                yield _group_reply(group)
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
        assert len(fmt_rows) == 4, f"distill_format 记账不是 4 条：{rows}"
        assert len({json.dumps(u, sort_keys=True) for u in fmt_rows}) == 4, (
            f"4 组账目分不开：{fmt_rows}"
        )
        assert events.count("formatting") == 1, f"formatting 帧不是恰 1 次：{events}"
        assert events[0] == "formatting", f"formatting 帧不在 4 组调用之前：{events}"
        assert sum(1 for e in events if e.startswith("group:")) == 4, events
        assert sum(1 for f in frames if isinstance(f, str)) == 1, "成品卡不是恰 1 个 str 帧"


class TestBatchCountDecidesTotalMerge:
    """WP7 F2：批数决定是否先总合并。

    100 片（2 批）→ 归并恰 2 次（**无总合并**），4 组输入都含两批结果；
    50 片（1 批）→ 归并恰 1 次，4 组输入就是这次合并的输出。

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
        assert len(formats) == 4, f"格式化不是 4 组：{len(formats)}"
        for body in formats:
            assert "合并甲" in body and "合并乙" in body, (
                f"4 组没有直接读到两批归并结果：{body[-120:]}"
            )

    def test_single_batch_merges_once_then_formats_from_it(self):
        reduces, formats = self._run(self.TEXT_50, [])

        assert reduces == ["合并甲"], f"≤80 片应恰 1 次归并：{reduces}"
        assert len(formats) == 4, f"格式化不是 4 组：{len(formats)}"
        for body in formats:
            assert body.endswith("合并甲"), (
                f"4 组输入不是这次合并的输出：{body[-120:]}"
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
    """WP14 A2：上游「超过 N 路即 429」时 Map 零失败，且在途数收敛到 N 附近。

    变异：闸只包整次调用（不进重试循环）—— 退避期间名额没还掉。`test_backoff_...`
    读的就是退避那一刻的 `gate.inflight`，直接打红；端到端那条负责证明「零失败」。

    片数取 80 而不是规格里写的 40：闸的初始上限就是 `map_concurrency`，冷启动那一波
    必然把它探满（实测 40 片整场都还在这个过渡里，后半程峰值仍有 8）。片数不够，就
    没有「收敛后」可判。峰值只看后半程，并列在失败信息里供复核。
    """

    CHUNKS = 80
    LIMIT = 5
    CAP = 20
    WARM = CHUNKS // 2

    async def test_map_finishes_with_zero_failures(self):
        upstream = _InflightUpstream(self.LIMIT, warm=self.WARM)
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

        n429 = upstream.statuses.count(429)
        assert n429 > 0, "假上游一次都没限流 —— 这条锁没走到被锁的地方"
        assert failures == [], f"限流把片打挂了：{failures[:3]}"
        assert len(results) == self.CHUNKS
        assert upstream.tail_peak <= self.LIMIT + 1, (
            f"收敛后仍在途 {upstream.tail_peak}，超过上游上限 {self.LIMIT}（+1 是 AIMD 的探针："
            f"不探到上限之上就永远学不到上限）；整场峰值 {upstream.peak}（含冷启动那波），"
            f"429 共 {n429} 次")

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
