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

import os
import sys

from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.llm_adapter import IncompleteResponseError, _extract_content
from core.distiller import Distiller
from core.utils import estimate_usage_from_chars

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

    def chat(self, system, messages, max_tokens=None):
        return self._replies.pop(0)

    def _make_async_client(self):
        return _FakeClient()

    async def async_chat(self, system, messages, client=None):
        return "角色很沉默。", None


# ── 记账探针 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def usage(monkeypatch) -> list[str]:
    """替换 `_try_record_usage` 的唯一下游，同步收下每次记账的 action。"""
    calls: list[str] = []

    def _rec(*, storage, user_id, llm, action="chat", usage=None, source="core"):
        calls.append(action)

    monkeypatch.setattr("core.distiller.try_record_usage", _rec)
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


@pytest.fixture
def records(monkeypatch) -> list[tuple[str, dict | None]]:
    """同 `usage`，但连 usage 载荷一起收下 —— 估算账要验的正是载荷。"""
    out: list[tuple[str, dict | None]] = []

    def _rec(*, storage, user_id, llm, action="chat", usage=None, source="core"):
        out.append((action, usage))

    monkeypatch.setattr("core.distiller.try_record_usage", _rec)
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
