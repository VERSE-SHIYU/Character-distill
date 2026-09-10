# -*- coding: utf-8 -*-
"""响应校验层回归：finish_reason 是唯一裁决点，未完成终态必须显式失败、绝不返回半截内容。

历史：全仓生产代码从不读 finish_reason → 截断 / 被思考吃光的响应被当成功返回并落库
（实测 52 字节半截内容落满 6 片）。本测试锁四件事：
  1. 已知未完成终态（length / content_filter / insufficient_system_resource）→
     抛 IncompleteResponseError，且异常不携带被截断的正文
  2. 三类处置在文案里可区分（抬预算 / 改输入 / 可重试），上层不会猜错动作
  3. stop / tool_calls → 正常返回
  4. 缺失 / 真正陌生的值 → 放行（不同供应商语义不一），但点名告警
外加一条兼容锁：老 mock 形态（choice 没有 finish_reason 属性）不得因此变红。
"""
import asyncio
from types import SimpleNamespace

import pytest

import adapters.llm_adapter as M
from adapters.llm_adapter import IncompleteResponseError, LLMAdapter


class _Msg:
    def __init__(self, content="ok", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, finish_reason=None, content="ok", tool_calls=None):
        self.finish_reason = finish_reason
        self.message = _Msg(content, tool_calls)


class _Resp:
    def __init__(self, choice):
        self.choices = [choice]
        self.usage = None


class _Delta:
    def __init__(self, content):
        self.content = content


class _StreamChunk:
    """content=None 表示只带终态、不带正文的收尾 chunk。"""

    def __init__(self, content=None, finish_reason=None):
        self.usage = None
        self.choices = [SimpleNamespace(delta=_Delta(content), finish_reason=finish_reason)]


class _Completions:
    def __init__(self, payload):
        self._payload = payload

    def create(self, **kwargs):
        return iter(self._payload) if kwargs.get("stream") else self._payload


class _AsyncCompletions:
    def __init__(self, payload):
        self._payload = payload

    async def create(self, **kwargs):
        return self._payload


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _Client:
    def __init__(self, completions):
        self.chat = _Chat(completions)


class _FakeOpenAIClient:
    """替代真实 OpenAI/AsyncOpenAI 构造：本沙箱里真实构造会在 httpx ssl
    create_default_context 上偶发挂起（环境性，非代码缺陷）。"""

    def __init__(self, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _no_real_openai(monkeypatch):
    monkeypatch.setattr(M, "OpenAI", _FakeOpenAIClient)
    monkeypatch.setattr(M, "AsyncOpenAI", _FakeOpenAIClient)


def _llm() -> LLMAdapter:
    return LLMAdapter(api_key="sk-test-fake")


_MSGS = [{"role": "user", "content": "hi"}]


# ── 非流式：截断是显式失败 ────────────────────────────────────────────

def test_length_raises_and_carries_no_content():
    llm = _llm()
    llm._client = _Client(_Completions(_Resp(_Choice(finish_reason="length", content="半截正文"))))
    with pytest.raises(IncompleteResponseError) as ei:
        llm.chat("sys", _MSGS)
    assert ei.value.finish_reason == "length"
    assert "半截正文" not in str(ei.value)  # 截断内容不出现在消息里（更不返回）
    assert "max_tokens" in str(ei.value)   # 处置=抬预算


def test_content_filter_raises_and_says_change_input():
    llm = _llm()
    llm._client = _Client(_Completions(_Resp(_Choice(finish_reason="content_filter"))))
    with pytest.raises(IncompleteResponseError) as ei:
        llm.chat("sys", _MSGS)
    assert ei.value.finish_reason == "content_filter"
    assert "改输入" in str(ei.value)        # 处置=改输入，不是重试、不是抬预算


def test_insufficient_resource_raises_and_says_retryable():
    llm = _llm()
    llm._client = _Client(_Completions(_Resp(_Choice(finish_reason="insufficient_system_resource"))))
    with pytest.raises(IncompleteResponseError) as ei:
        llm.chat("sys", _MSGS)
    assert "可重试" in str(ei.value)        # 处置=可重试，区别于前两者


def test_stop_returns_content():
    llm = _llm()
    llm._client = _Client(_Completions(_Resp(_Choice(finish_reason="stop", content="完整"))))
    assert llm.chat("sys", _MSGS) == "完整"


def test_async_chat_length_raises():
    llm = _llm()
    llm._async_client = _Client(_AsyncCompletions(_Resp(_Choice(finish_reason="length"))))
    with pytest.raises(IncompleteResponseError):
        asyncio.run(llm.async_chat("sys", _MSGS))


def test_chat_with_tools_length_raises():
    llm = _llm()
    llm._client = _Client(_Completions(_Resp(_Choice(finish_reason="length"))))
    with pytest.raises(IncompleteResponseError):
        llm.chat_with_tools("sys", _MSGS, tools=[{"type": "function"}])


# ── 放行路径：缺失 / 陌生值不得阻断 ──────────────────────────────────

def test_finish_reason_absent_attribute_passes():
    """老 mock 形态：choice 连 finish_reason 属性都没有 → 缺失，放行（兼容锁）。"""
    llm = _llm()
    llm._client = _Client(_Completions(_Resp(SimpleNamespace(message=_Msg("ok")))))
    assert llm.chat("sys", _MSGS) == "ok"


def test_unknown_finish_reason_warns_but_passes(capsys):
    """真正陌生的值放行，但必须点名——否则新方言的截断型终态会静默溜过。"""
    llm = _llm()
    llm._client = _Client(_Completions(_Resp(_Choice(finish_reason="some_new_vendor_value"))))
    assert llm.chat("sys", _MSGS) == "ok"
    assert "some_new_vendor_value" in capsys.readouterr().out


# ── 流式：终态在最后一个 chunk，先校验再吐该片 ────────────────────────

def test_stream_stop_yields_all_pieces():
    llm = _llm()
    llm._client = _Client(_Completions([_StreamChunk("a"), _StreamChunk("b"), _StreamChunk(None, "stop")]))
    assert list(llm.chat_stream("sys", _MSGS)) == ["a", "b"]


def test_stream_length_raises_before_that_piece():
    llm = _llm()
    llm._client = _Client(_Completions([_StreamChunk("a"), _StreamChunk("b", "length")]))
    gen = llm.chat_stream("sys", _MSGS)
    assert next(gen) == "a"          # 已流出的片无法收回，正常
    with pytest.raises(IncompleteResponseError):
        next(gen)                     # 带终态的那片不交付


def test_stream_without_terminal_chunk_passes(capsys):
    """流尽仍无终态 → 判缺失、放行、点名。"""
    llm = _llm()
    llm._client = _Client(_Completions([_StreamChunk("a"), _StreamChunk("b")]))
    assert list(llm.chat_stream("sys", _MSGS)) == ["a", "b"]
    assert "finish_reason=None" in capsys.readouterr().out
