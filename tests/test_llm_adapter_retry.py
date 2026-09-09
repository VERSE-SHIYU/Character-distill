# -*- coding: utf-8 -*-
"""D1a 回归：SDK 重试撤除 + _RetryBudget 单点控制不被破坏。

缺陷 #1 是 retry 嵌套（adapter budget × SDK max_retries=2 = 至多 9 次 HTTP，
决策 degrade 前固定烧 5s+10s）。这里锁：
  1. 三个 client 构造点 max_retries=0（嵌套乘法消失的前提）；
  2. 决策轮 500 风暴在 ~1s 内抛「failed after 2 attempts」，不再烧 5s+10s；
  3. deadline 门控真正封顶（deadline=0 → 单次尝试即弃，不 sleep）；
  4. chat_with_tools 的 400/工具不支持「不重试」语义保留；
  5. async 路径同预算（一次失败后成功 / 风暴 3 次封顶）；
  6. chat_stream 首 token 前有界补偿（create 失败重试，从不越 2 次）。
"""
import asyncio
import time
from types import SimpleNamespace

import pytest
from openai import BadRequestError

import adapters.llm_adapter as M
from adapters.llm_adapter import LLMAdapter, ToolsNotSupportedError


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, content=None, tool_calls=None):
        self.message = _Msg(content, tool_calls)


class _Delta:
    def __init__(self, content):
        self.content = content


class _StreamChunk:
    def __init__(self, content=None):
        self.usage = None
        self.choices = [] if content is None else [SimpleNamespace(delta=_Delta(content))]


class _Resp:
    def __init__(self, content="ok", tool_calls=None):
        self.choices = [_Choice(content, tool_calls)]
        self.usage = None


# 每轮 create 都调用 behavior()：可返回 _Resp，也可 raise。
class _SyncCompletions:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self.behavior()


class _SyncChat:
    def __init__(self, behavior):
        self.completions = _SyncCompletions(behavior)


class _SyncClient:
    def __init__(self, behavior):
        self.chat = _SyncChat(behavior)


class _AsyncCompletions:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return self.behavior()


class _AsyncChat:
    def __init__(self, behavior):
        self.completions = _AsyncCompletions(behavior)


class _AsyncClient:
    def __init__(self, behavior):
        self.chat = _AsyncChat(behavior)


class _FakeOpenAIClient:
    """替代真实 OpenAI/AsyncOpenAI 构造：本沙箱里真实构造会在 httpx ssl
    create_default_context 上偶发挂起（环境性，非代码缺陷）。此 fake 只捕获
    max_retries kwarg——正是 D1a 的回归信号（SDK 重试必须归 0）。"""

    def __init__(self, **kwargs):
        self.max_retries = kwargs.get("max_retries")


@pytest.fixture(autouse=True)
def _no_real_openai(monkeypatch):
    monkeypatch.setattr(M, "OpenAI", _FakeOpenAIClient)
    monkeypatch.setattr(M, "AsyncOpenAI", _FakeOpenAIClient)


class _RateLimitError429(RuntimeError):
    """非 openai 包的 429 假异常：status_code 驱动 _classify_retry，response.headers 回 Retry-After。"""

    def __init__(self, retry_after: str):
        super().__init__("429 upstream rate limit")
        self.status_code = 429
        self.response = SimpleNamespace(headers={"Retry-After": retry_after})


def _storm(exc):
    return lambda: (_ for _ in ()).throw(exc)


def _make_llm() -> LLMAdapter:
    return LLMAdapter(api_key="sk-test-fake")


def test_clients_max_retries_zero():
    llm = _make_llm()
    assert llm._client.max_retries == 0
    assert llm._async_client.max_retries == 0
    assert llm._make_async_client().max_retries == 0


def test_decision_500_storm_raises_fast_not_5s_10s_wall():
    llm = _make_llm()
    fake = _SyncClient(_storm(RuntimeError("upstream 500")))
    llm._client = fake
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0, f"decision should not burn 5s+10s wall, took {elapsed:.2f}s"
    assert fake.chat.completions.calls == 2


def test_decision_deadline_gate_caps_attempts(monkeypatch):
    monkeypatch.setattr(M, "_DECISION_DEADLINE_S", 0.0)
    llm = _make_llm()
    fake = _SyncClient(_storm(RuntimeError("upstream 500")))
    llm._client = fake
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="failed after 1 attempts"):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert time.monotonic() - t0 < 0.5
    assert fake.chat.completions.calls == 1  # deadline 到即弃，不 sleep 不重试


def test_decision_429_backoff_clamped_to_deadline(monkeypatch):
    # 必修1：Retry-After=30 不得让决策轮烧 30s——wait 被 clamp 到 deadline 剩余；
    # 剩余窗耗尽后下一次失败即判 exhausted（总调用 2 次，墙钟 ~deadline 而非 ~30s）。
    monkeypatch.setattr(M, "_DECISION_DEADLINE_S", 2.0)
    llm = _make_llm()
    fake = _SyncClient(_storm(_RateLimitError429("30")))
    llm._client = fake
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match=r"rate limited \(429\) after 2 attempts"):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    elapsed = time.monotonic() - t0
    assert fake.chat.completions.calls == 2
    assert elapsed < 5.0, f"backoff must be clamped to deadline, took {elapsed:.2f}s"


def test_decision_429_has_own_attempt_cap(monkeypatch):
    # 必修2：429 不计入非429 attempts，但有独立次数上限（_RATE_LIMIT_ATTEMPTS）。
    # Retry-After=1s 若只受 deadline(6s) 约束最多打 6 次；cap=3 → 3 次即抛、不再高频重打。
    # （真实 Retry-After 是整秒；isdigit 不认小数，故用整数秒走 _classify_retry 解析路径。）
    monkeypatch.setattr(M, "_RATE_LIMIT_ATTEMPTS", 3)
    llm = _make_llm()
    fake = _SyncClient(_storm(_RateLimitError429("1")))
    llm._client = fake
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match=r"rate limited \(429\) after 3 attempts"):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    elapsed = time.monotonic() - t0
    assert fake.chat.completions.calls == 3
    assert elapsed < 5.0


def test_with_tools_400_no_retry_preserved():
    llm = _make_llm()
    resp = SimpleNamespace(status_code=400, headers={}, request=SimpleNamespace(url="http://x"))
    # 其他 400：原样抛 BadRequestError，不重试
    fake = _SyncClient(_storm(BadRequestError("invalid request param", response=resp, body={})))
    llm._client = fake
    with pytest.raises(BadRequestError):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert fake.chat.completions.calls == 1
    # 400 + tool/function 关键词 → ToolsNotSupportedError，不重试
    fake2 = _SyncClient(_storm(BadRequestError("provider does not support function", response=resp, body={})))
    llm._client = fake2
    with pytest.raises(ToolsNotSupportedError):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert fake2.chat.completions.calls == 1


def test_async_chat_retries_once_then_succeeds(monkeypatch):
    monkeypatch.setattr(M, "_GEN_BACKOFF_S", 0.01)
    llm = _make_llm()
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return _Resp("ok")

    fake = _AsyncClient(behavior)
    llm._async_client = fake
    result, _usage = asyncio.run(llm.async_chat("sys", [{"role": "user", "content": "hi"}]))
    assert result == "ok"
    assert fake.chat.completions.calls == 2


def test_async_chat_500_storm_capped(monkeypatch):
    monkeypatch.setattr(M, "_GEN_BACKOFF_S", 0.01)
    llm = _make_llm()
    fake = _AsyncClient(_storm(RuntimeError("upstream 500")))
    llm._async_client = fake
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        asyncio.run(llm.async_chat("sys", [{"role": "user", "content": "hi"}]))
    assert fake.chat.completions.calls == 3


def test_chat_stream_retries_create_once_then_streams(monkeypatch):
    monkeypatch.setattr(M, "_STREAM_BACKOFF_S", 0.01)
    llm = _make_llm()
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connect reset")
        return iter([_StreamChunk("a")])

    fake = _SyncClient(behavior)
    llm._client = fake
    gen = llm.chat_stream("sys", [{"role": "user", "content": "hi"}])
    assert next(gen) == "a"
    assert calls["n"] == 2
    with pytest.raises(StopIteration):
        next(gen)


def test_chat_stream_create_storm_capped():
    llm = _make_llm()
    fake = _SyncClient(_storm(RuntimeError("connect reset")))
    llm._client = fake
    gen = llm.chat_stream("sys", [{"role": "user", "content": "hi"}])
    with pytest.raises(RuntimeError):
        next(gen)
    assert fake.chat.completions.calls == 2  # ≤_STREAM_ATTEMPTS
