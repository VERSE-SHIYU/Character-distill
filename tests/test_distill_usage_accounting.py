# -*- coding: utf-8 -*-
"""缺陷 16：蒸馏调用的 usage 记账收敛到**唯一出口**。

三个站点各调一次 `_chat_accounted`：`distill`（短文本截断模式）、
`_distill_longcontext`（整本蒸）、`distill_incremental`（MapReduce 收尾格式化）。
原状口径不一致 —— 后两处各自紧跟一条 `_try_record_usage`，而 `distill` 那处
**根本没有**，短文本路径的初次调用（真实烧掉的 token）一条都不记。

收敛后不变量：**一次 `_chat_accounted` 调用恰记一条 usage，与它结果如何无关** ——
token 花出去就花出去了。初次与重修同走这一个原语（重修环不再自带记账，否则流式那支
与 `_collect_stream` 的本级记账双计）。**旧口径是「`raise` 那条路不记」**，本轮改掉：
非流式的截断支（缺陷 91）与硬失败支（缺陷 92）都拿不到 `last_usage`，各自按字符估算
补记后再交出去 / 上抛，与流式支 `_collect_stream` 的 except 支同口径。

本文件锁的是**出口发了几次、带什么 action**，不锁 storage 落库链路（`record_usage`
的线程化落库另有独立测试）。故直接替换 `core.distiller.try_record_usage` 做同步计数 ——
它正是 `_try_record_usage` 的唯一被调下游，也是主路径从 `core/utils.py` 绑定进来的名字。
"""

from __future__ import annotations

import inspect
import os
import sys
import threading

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.llm_adapter import IncompleteResponseError, _extract_content
from core.distiller import Distiller
from core.schema import FORMAT_GROUPS
from core.utils import estimate_usage_from_chars, try_record_usage

# WP7：格式化改成按字段组并行（4 组），桩 LLM 得按组回 JSON 才走得完那一阶段。
# 认组 / 造回复都复用 WP7 那批件，不另抄一份字段样例 —— 抄一份就是第二处「组字段表」。
from test_distiller_routing import _format_group_of, _group_reply

# CharacterCard 只有 name 是必填
CARD = '{"name": "角色"}'
BAD = "这一段不是 JSON，会被重修环接住"

TEXT = "\n\n".join(
    f"角色第{i}段：角色说了第{i}句话，这里还有角色的别的话。" for i in range(3)
)


# ── 假 LLM ───────────────────────────────────────────────────────────────────

class _FakeClient:
    async def close(self):
        pass


class _FakeLLM:
    """`chat` 按序吐预设回复，`async_chat`（Map 阶段）恒返回一段分析。"""

    def __init__(self, chat_replies: list[str]):
        self._replies = list(chat_replies)
        self.last_usage = {"prompt_tokens": 7, "completion_tokens": 3}
        self._model = "test-model"

    @property
    def model(self) -> str:
        """真 adapter 的 `model` 是只读属性（分片断点键要带上它）。"""
        return self._model

    def chat(self, system, messages, max_tokens=None):
        return self._replies.pop(0)

    def _make_async_client(self):
        return _FakeClient()

    async def async_chat(self, system, messages, client=None):
        return "角色很沉默。", None


# ── 记账探针 ─────────────────────────────────────────────────────────────────

def _recorder(sink: Callable[[str, dict | None], None]):
    """记账替身：**唯一**一份，`usage` 与 `records` 两个夹具共用。

    自己不写签名 —— 收 `*args, **kwargs`，先拿真函数
    `inspect.signature(core.utils.try_record_usage).bind()` 校验一遍，过了再收进 sink。
    签名因此只有真函数一份：真函数**加了无默认值的参数**或**删了参数**，调用方那串
    kwargs 就 bind 不上，替身当场 TypeError 红掉。旧版是两份手抄签名，`user_id` 那次
    只有一份跟着改，另一份要到合并后才炸。

    真函数加**带默认值**的参数不算漂移：调用方不传是合法调用，bind 通过、替身照绿。
    """
    real = inspect.signature(try_record_usage)

    def _rec(*args, **kwargs):
        bound = real.bind(*args, **kwargs)
        bound.apply_defaults()
        sink(bound.arguments["action"], bound.arguments["usage"])

    return _rec


@pytest.fixture
def usage(monkeypatch) -> list[str]:
    """替换 `_try_record_usage` 的唯一下游，同步收下每次记账的 action。"""
    calls: list[str] = []

    def _sink(action, _usage):
        calls.append(action)

    monkeypatch.setattr("core.distiller.try_record_usage", _recorder(_sink))
    return calls


def _distiller(chat_replies: list[str]) -> Distiller:
    return Distiller(_FakeLLM(chat_replies), config_path=None)


# ── 初次调用：一成功一记账 ───────────────────────────────────────────────────

class TestInitialCallAccounting:
    def test_distill_records_initial_call(self, usage):
        """回归主体：`distill` 初次成功原本**零记账**（三站点里漏记的那一处）。

        变异：把 `_chat_accounted` 里那条 `_try_record_usage(action)` 删掉 → 本用例红。
        """
        _distiller([CARD]).distill("文本", "角色")
        assert usage == ["distill"]

    def test_distill_repair_records_both_calls(self, usage):
        """初次坏 + 重修好 = 两笔真实调用 → 两条，不是一条也不是三条。

        变异：把重修出口（`_parse_json_with_retry` 的 554/591）也删掉 → 只余一条，
        本用例红；把初次出口改成「每次尝试都记」→ 条数超 2，也红。
        """
        _distiller([BAD, CARD]).distill("文本", "角色")
        assert usage == ["distill", "distill"]

    def test_longcontext_records_once_not_twice(self, usage):
        """整本蒸原本记**两条**（外部一条 + 新增出口一条）。

        变异：把 `_distill_longcontext` 里随本轮删掉的那条外部
        `_try_record_usage("distill_longcontext")` 加回来 → 变成 2 条，本用例红。
        这是本轮唯一的**多记**风险点。
        """
        _distiller([CARD])._distill_longcontext("全文", "角色")
        assert usage == ["distill_longcontext"]

    def test_truncated_initial_call_is_still_recorded(self, usage):
        """`length` 截断那一路也是成功调用：token 已烧、半截正文还要进重修环 → 记。

        这条钉的是 `_chat_accounted` 里 `truncated = True` 那条返回分支 —— 只记「正常
        return」不记「截断 return」的实现会漏掉它，本用例红。走真实 `_extract_content`
        造异常（不直接构造异常类），否则「去掉 content 传递」的变异测不出来。
        """
        llm = _FakeLLM([])
        llm.chat = MagicMock(side_effect=[_truncated_exc("chat"), CARD])

        card = Distiller(llm, config_path=None).distill("文本", "角色")
        assert card.name == "角色"
        assert llm.chat.call_count == 2, "一次截断 + 一次重修"
        assert usage == ["distill", "distill"], "截断那次同样烧了 token，必须记"


# ── 流式通道：截断也落一条估算账（记账落在发起调用的那一级） ──────────────────

PARTIAL = '{"name": "阿'   # 被 max_tokens 截断的半截角色卡
SYSTEM, USER = "系统提示", "用户正文"


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, finish_reason, content):
        self.finish_reason = finish_reason
        self.message = _Msg(content)


def _truncated_exc(where: str) -> IncompleteResponseError:
    """经**真实**的 `_extract_content` 造截断异常，不直接构造异常类。

    直接 `IncompleteResponseError("length", where, content=...)` 会绕过 content 的传递
    链，于是「去掉 content 传递」的变异测不出来 —— 那是个没判别力的用例。
    """
    with pytest.raises(IncompleteResponseError) as ei:
        _extract_content(_Choice("length", PARTIAL), where=where)
    return ei.value


class _FakeStreamLLM(_FakeLLM):
    """`chat_stream` 吐完预设片段后抛上游截断信号 —— usage chunk 排在它后面，永不交付。"""

    def __init__(self, pieces: list[str], exc: BaseException):
        super().__init__([])
        self._pieces = pieces
        self._exc = exc

    def chat_stream(self, system, messages, max_tokens=None):
        yield from self._pieces
        raise self._exc

    # 长输出入口在生产里是 chat_stream 的薄委托（只放宽读超时）：桩共用同一份记录
    chat_stream_long = chat_stream


@pytest.fixture
def records(monkeypatch) -> list[tuple[str, dict | None]]:
    """同 `usage`，但连 usage 载荷一起收下 —— 估算账要验的正是载荷。"""
    out: list[tuple[str, dict | None]] = []

    def _sink(action, usage):
        out.append((action, usage))

    monkeypatch.setattr("core.distiller.try_record_usage", _recorder(_sink))
    return out


class TestStreamChannelAccounting:
    def test_truncated_stream_records_one_estimated_entry(self, records):
        """流式截断（finish_reason=length）→ 补一条**估算**账，半截正文照常交出去。

        `chat_stream` 的 usage chunk 排在 finish_reason **之后**，校验不过就不交付，
        故截断时 `last_usage` 必为 None —— 不按字符估算补记，这一笔在生产里就是
        静默不落库（只记成功 = 统计系统性偏低）。

        变异：把 `_collect_stream` 里异常/截断那条 `_try_record_usage` 删掉 → 本用例红。
        """
        exc = IncompleteResponseError("length", "chat_stream", content=PARTIAL)
        d = Distiller(_FakeStreamLLM([PARTIAL], exc), config_path=None)

        text, truncated = d._chat_accounted(
            SYSTEM, [{"role": "user", "content": USER}],
            "识别合并", "distill_identify", stream=True,
        )

        assert (text, truncated) == (PARTIAL, True), "半截正文是重修的证据，必须交出去"
        assert [a for a, _ in records] == ["distill_identify"], records
        payload = records[0][1]
        assert payload["estimated"] is True, "拿不到 last_usage，只能标估算"
        assert payload == estimate_usage_from_chars(
            len(SYSTEM) + len(USER), len(PARTIAL)), "prompt/completion 两侧都要按已见字符算"


class TestNonStreamTruncationAccounting:
    def test_truncated_chat_records_one_estimated_entry(self, records):
        """非流式 `length` 截断 → 与流式支同口径，补一条**估算**账。

        `chat()` 进本轮就先 `last_usage = None`（`adapters/llm_adapter.py:634`），而
        `_extract_content` 的抛出点在 usage 回写（`:651`）**之前** —— 故这条路上
        `_try_record_usage(action)` 拿到 `usage=None`，落到 `try_record_usage` 的
        `if not usage` 分支：打印一行「no usage data」就 return，**一条也不落库**。
        形如「只记成功」的静默偏低，与流式支修掉的那条同形态。

        变异：把非流式截断支那条 `estimate_usage_from_chars(...)` 退回 `usage=None`
        （即本用例加入前的实现）→ `payload` 变 None，本用例红。
        """
        llm = _FakeLLM([])
        llm.chat = MagicMock(side_effect=_truncated_exc("chat"))

        text, truncated = Distiller(llm, config_path=None)._chat_accounted(
            SYSTEM, [{"role": "user", "content": USER}], "角色蒸馏", "distill",
        )

        assert (text, truncated) == (PARTIAL, True), "半截正文是重修的证据，必须交出去"
        assert [a for a, _ in records] == ["distill"], records
        payload = records[0][1]
        assert payload is not None, "截断时拿不到 last_usage，必须按字符估算补记"
        assert payload["estimated"] is True, "估的账要标出来，别冒充真实读数"
        assert payload == estimate_usage_from_chars(
            len(SYSTEM) + len(USER), len(PARTIAL)), "prompt/completion 两侧都要按已见字符算"


class TestNonStreamHardFailureAccounting:
    def test_hard_failure_records_one_estimated_entry(self, records):
        """非流式**硬失败**（网络 / content_filter / 资源不足）同样烧了 token → 记一条估算账。

        与截断支、以及流式支 `_collect_stream` 的 except 支**同一口径**：token 已经花出去了
        （重试墙下正是空烧），只记成功会让统计系统性偏低。旧口径「`raise` 那条路不记」
        让同一个事实在流式与非流式两侧记出两个数 —— 而这两支本来就是一次调用的两种收法。

        变异：把 `_chat_accounted` 硬失败支那条 `_try_record_usage` 删掉（即本用例加入前的
        实现）→ `records` 为空，本用例红。
        """
        llm = _FakeLLM([])
        llm.chat = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            Distiller(llm, config_path=None)._chat_accounted(
                SYSTEM, [{"role": "user", "content": USER}], "角色蒸馏", "distill",
            )

        assert [a for a, _ in records] == ["distill"], records
        payload = records[0][1]
        assert payload is not None, "硬失败时拿不到 last_usage，必须按字符估算补记"
        assert payload["estimated"] is True, "估的账要标出来，别冒充真实读数"
        assert payload == estimate_usage_from_chars(len(SYSTEM) + len(USER)), (
            "硬失败没有产出任何正文，completion 侧按 0 算；prompt 侧照实")


# ── 收尾格式化站点：第三次收敛后的独立锁 ─────────────────────────────────────

class TestIncrementalFormatAccounting:
    def test_format_step_records_exactly_once(self, usage):
        """`distill_incremental` 走完 Map→Reduce→Format：格式化站点恰记一条。

        `_longctx_threshold = 1` 强制落到 MapReduce 分支，否则短文本会被路由去
        `_distill_longcontext`，压根到不了这一步。`chat` 的消费次序是
        reduce 草稿 → 格式化卡 JSON → auto-tag。

        变异：把格式化站点原来那条外部 `_try_record_usage("distill_format")` 加回来
        （而出口也记）→ 本断言变 2 → 红。
        """
        d = _distiller(["这是一份档案草稿。", CARD, "[]"])
        d._longctx_threshold = 1
        d._chunk_size = 200

        card = d.distill_incremental(TEXT, "角色", text_type="story")

        assert card.name == "角色"
        assert usage.count("distill_format") == 1, usage


# ── R4：并发流式各记各的账（usage 随调用返回，不读共享属性） ────────────────────

class _BarrierStreamLLM(_FakeLLM):
    """三条流**几乎同时**结束：各自吐完末片后先在汇合点等齐，再交出本次 usage。

    汇合点是这条用例的红源。三条流都过了 barrier 才算结束，故「流结束后读共享属性」
    的实现必然读到同一份（最后写入者）—— 三行落成同一个数，就是缺陷 20 那条
    300/300 串号的形状（三行全记成最后一条的 303）。`return` 的那一份才是本次调用的
    真实用量，它按线程各自保管，不经过任何共享槽。
    """

    def __init__(self, barrier: threading.Barrier):
        super().__init__([])
        self._barrier = barrier
        self._lock = threading.Lock()
        self._next_ct = 101

    def chat_stream_long(self, system, messages, max_tokens=None):
        with self._lock:                      # 101 / 202 / 303，谁先到谁先领
            ct = self._next_ct
            self._next_ct += 101
        yield "x"
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": ct, "estimated": False}
        self._barrier.wait(timeout=10)
        return {"prompt_tokens": 1, "completion_tokens": ct, "estimated": False}


class TestConcurrentStreamAccounting:
    def test_concurrent_streams_each_keep_their_own_usage(self, records):
        """R4：三批并发，落账三行 == {101, 202, 303}，不是一个数写三遍。

        变异：`_collect_stream` 改回读 `self._llm.last_usage`（即 `_try_record_usage`
        不传 usage、落回 `core/utils.py:83` 的回退）→ 三行全等于最后写入者的那个数，
        本用例红。
        """
        barrier = threading.Barrier(3)
        d = Distiller(_BarrierStreamLLM(barrier), config_path=None)
        outcomes: list[object] = [None] * 3

        def run(i: int) -> None:
            try:
                outcomes[i] = d._chat_accounted(
                    SYSTEM, [{"role": "user", "content": USER}],
                    "角色蒸馏", "distill", stream=True,
                )
            except BaseException as exc:      # 记下来，别让线程里的异常静默消失
                outcomes[i] = exc

        threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not any(isinstance(o, BaseException) for o in outcomes), outcomes
        assert [o[0] for o in outcomes] == ["x"] * 3, outcomes
        assert [a for a, _ in records] == ["distill"] * 3, records
        # (u or {})：改前那条路传的是 None，报 [None, None, None]；改成显式读共享属性
        # 那条路报 [k, k, k]（同一个数三遍，缺陷 20 的形状）—— 两种错法都要读得出来。
        got = sorted(((u or {}).get("completion_tokens") for _, u in records),
                     key=lambda v: (v is None, v))
        assert got == [101, 202, 303], (
            f"三批并发记出了 {got} —— 记的是共享属性而不是各自调用返回的那一份")


# ── R4b：`yield from` 的两处也记本次返回值 ─────────────────────────────────────

class _ReturnUsageLLM(_FakeLLM):
    """流吐完 `return` 404，同时把共享属性写成 999 —— 读的是哪一份一眼可分。"""

    def chat_stream_long(self, system, messages, max_tokens=None):
        yield "x"
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 999, "estimated": False}
        return {"prompt_tokens": 1, "completion_tokens": 404, "estimated": False}


class _FormatGroupReturnUsageLLM(_ReturnUsageLLM):
    """格式化那一跳回**该组**的 JSON；归并那一跳照基类吐 `"x"`。

    回 `"x"` 的话每组会被 `_parse_json_with_retry` 重修两次 —— 账上是每组 3 条，
    「一次调用恰一条」这条判据就淹没在重试里（实测 12 条）。组也别靠调用次序认：
    4 组是并发发的，次序不定。
    """

    def chat_stream_long(self, system, messages, max_tokens=None):
        if "你正在整合关于" in system:            # 归并那一跳
            yield from super().chat_stream_long(system, messages, max_tokens)
            return
        yield _group_reply(_format_group_of(system))
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 999, "estimated": False}
        return {"prompt_tokens": 1, "completion_tokens": 404, "estimated": False}


class TestStreamReturnValueAccounting:
    """这三处的共同判据：账上的数取自 `yield from` / 迭代拿到的**返回值**。

    变异（每条各自红）：该处改回读 `self._llm.last_usage` → 落账 999；
    该处不传 usage → `records[i][1]` 为 None（落到 `core/utils.py:83` 的回退）。
    """

    def _assert_404(self, records, action: str) -> None:
        assert [a for a, _ in records] == [action], records
        assert records[0][1] is not None, "没把返回值交给 _try_record_usage，落回了共享属性"
        assert records[0][1]["completion_tokens"] == 404, (
            f"记的是共享属性（999）而不是本次调用返回的那一份：{records[0][1]}")

    def test_distill_stream_accounts_returned_usage(self, records):
        """`distill_stream`：`yield from` 之后记账，账取返回值。"""
        d = Distiller(_ReturnUsageLLM([]), config_path=None)
        assert list(d.distill_stream("文本", "角色")) == ["x"]
        self._assert_404(records, "distill_stream")

    def test_longcontext_stream_accounts_returned_usage(self, records):
        """`_distill_longcontext_stream`：滤思考态的是显式 `next()` 环，值照样要接住。"""
        d = Distiller(_ReturnUsageLLM([]), config_path=None)
        tokens = [t for t in d._distill_longcontext_stream("全文", "角色") if isinstance(t, str)]
        assert tokens == ["x"], tokens
        self._assert_404(records, "distill_longcontext")

    def test_single_reduce_stream_accounts_returned_usage(self, records):
        """`_single_reduce_stream`：归并流式版，账取返回值。"""
        d = Distiller(_ReturnUsageLLM([]), config_path=None)
        assert list(d._single_reduce_stream(["分析一"], "角色")) == ["x"]
        self._assert_404(records, "distill_reduce")

    def test_incremental_format_stream_accounts_returned_usage(self, records):
        """流式格式化：4 组各一条，账取 `_collect_stream` 收下的返回值。

        `distill_incremental_stream` 要整条跑完才到得了 Phase 3 —— 这条同时当「那一处确实
        跑到了」的仪器：`fmt` 为空即说明本轮根本没走到格式化，红的是覆盖面而不是取值。
        组数从 `FORMAT_GROUPS` 读（WP7 起是 4 组并行，每组一次 `_chat_accounted`）。
        Reduce 那一段（`_single_reduce_stream`）也落一条 `distill_reduce`，两个站点在本轮
        都不许读共享属性，故两处变异各自会红一条：断言只挑 `distill_format`，免得一个站点的
        变异把另一站点的用例也染色（那就分不清是谁坏了）。
        """
        d = Distiller(_FormatGroupReturnUsageLLM([]), config_path=None)
        d._longctx_threshold = 1          # 强制落到 MapReduce 分支，否则短文本路由去长上下文
        d._chunk_size = 200

        list(d.distill_incremental_stream(TEXT, "角色", text_type="story"))

        fmt = [u for a, u in records if a == "distill_format"]
        assert len(fmt) == len(FORMAT_GROUPS), (
            f"没跑到流式格式化（或组数不对）：{len(fmt)} 条，应为 {len(FORMAT_GROUPS)}")
        assert all(u is not None for u in fmt), (
            f"有组没把返回值交给 _try_record_usage，落回了共享属性：{fmt}")
        assert all(u["completion_tokens"] == 404 for u in fmt), (
            f"记的是共享属性（999）而不是本次调用返回的那一份：{fmt}")
