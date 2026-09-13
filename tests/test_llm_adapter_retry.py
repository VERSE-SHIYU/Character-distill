# -*- coding: utf-8 -*-
"""D1a/D1b 回归：SDK 重试撤除 + _RetryBudget 单点控制不被破坏。

缺陷 #1 是 retry 嵌套（adapter budget × SDK max_retries=2 = 至多 9 次 HTTP，
决策 degrade 前固定烧 5s+10s）。这里锁：
  1. 三个 client 构造点 max_retries=0（嵌套乘法消失的前提）；
  2. 决策轮 500 风暴在 ~1s 内抛「failed after 2 attempts」，不再烧 5s+10s；
  3. deadline 门控真正封顶——D1a 期 deadline=0 单次即弃，D1b 后剩余−margin<min 直接拒发（不 create）；
  4. chat_with_tools 的 400/工具不支持「不重试」语义保留；
  5. async 路径同预算（一次失败后成功 / 风暴 3 次封顶）；
  6. chat_stream 首 token 前有界补偿（create 失败重试，从不越 2 次）；
  7. D1b per-attempt timeout：create 收 timeout=min(ceiling, 剩余−margin)；首 attempt 由 role ceiling
     定（决策≈5 / 生成≈45，生成档证明非 deadline 裸奔）；紧 deadline 下被剩余窗夹逼（1.0 而非 30）；
     死亡窗（剩余−margin<min=0.25）由 _RetryBudget 直接判 exhausted，create 调用数不含那次。
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
# timeouts 记录每次 create 收到的 timeout kwarg——D1b 证据：单次超时 = min(ceiling, 剩余−margin)。
class _SyncCompletions:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0
        self.timeouts: list = []

    def create(self, **kwargs):
        self.calls += 1
        self.timeouts.append(kwargs.get("timeout"))
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
        self.timeouts: list = []

    async def create(self, **kwargs):
        self.calls += 1
        self.timeouts.append(kwargs.get("timeout"))
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


def test_deadline_insufficient_remaining_no_create(monkeypatch):
    # D1b 边界洞回归：deadline=0 → 剩余−margin(0−1) < min(0.25)，撑不起一次有效 attempt。
    # _RetryBudget 必须直接判 exhausted（attempt_timeout 抛 "no time for an attempt"），
    # 不发出那个会假失败的死亡窗 create（timeout=max(0.25, 0−1)=0.25s）；断言 create 调用数 =0。
    monkeypatch.setattr(M, "_DECISION_DEADLINE_S", 0.0)
    llm = _make_llm()
    fake = _SyncClient(_storm(RuntimeError("upstream 500")))
    llm._client = fake
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="no time for an attempt"):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert time.monotonic() - t0 < 0.5
    assert fake.chat.completions.calls == 0  # 没发出任何 create，直接抛


def test_decision_first_attempt_timeout_equals_ceiling():
    # D1b 证据：决策轮首 attempt timeout == min(ceiling=5, 剩余−margin≈deadline−1=5) ≈ 5.0，
    # 即剩余窗宽裕时由 role ceiling 决定，不让单次请求潜在吃掉整个 6s deadline。
    llm = _make_llm()
    fake = _SyncClient(lambda: _Resp("ok"))
    llm._client = fake
    llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert fake.chat.completions.calls == 1
    assert fake.chat.completions.timeouts[0] == pytest.approx(M._DECISION_ATTEMPT_S, abs=0.2)


def test_gen_first_attempt_timeout_capped_by_ceiling_not_deadline():
    # D1b 证据：生成轮首 attempt timeout == min(ceiling=45, 剩余−margin≈59) = 45.0。
    # 若没 ceiling 夹逼会发 59s 的 create（≈整个 60s deadline 裸奔）；45 封顶证明 ceiling 生效。
    llm = _make_llm()
    fake = _SyncClient(lambda: _Resp("ok"))
    llm._client = fake
    llm.chat("sys", [{"role": "user", "content": "hi"}])
    assert fake.chat.completions.calls == 1
    assert fake.chat.completions.timeouts[0] == pytest.approx(M._GEN_ATTEMPT_S, abs=0.2)


def test_decision_429_backoff_clamped_to_deadline(monkeypatch):
    # 必修1 + D1b：Retry-After=30 不得让决策轮烧 30s——wait 被 clamp 到 max_wait=剩余−(margin+min)；
    # 下一次超时由 on_failure 预计算（非时钟重推）→ 紧界下也确定：attempt1 timeout=deadline−margin=1.0，
    # attempt2 timeout=0.25（睡满 0.75 后恰剩 min 窗），随后剩余 < margin+min → 判 exhausted（共 2 次）。
    # 墙钟 ~0.75s（非 ~30s）。若重推会因 sleep 过冲在 1/2 次间非确定摇摆。
    monkeypatch.setattr(M, "_DECISION_DEADLINE_S", 2.0)
    llm = _make_llm()
    fake = _SyncClient(_storm(_RateLimitError429("30")))
    llm._client = fake
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match=r"rate limited \(429\) after 2 attempts"):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    elapsed = time.monotonic() - t0
    assert fake.chat.completions.calls == 2
    tos = fake.chat.completions.timeouts
    assert tos[0] == pytest.approx(1.0, abs=0.05)   # min(ceiling, deadline−margin) 夹逼 < ceiling
    assert tos[1] == pytest.approx(0.25, abs=0.05)  # 预计算的最后一次有效窗
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


# ── 缺陷 8：超时族全走 env（口径统一）──────────────────────────────────
# 三个 ceiling 早已 env 化，三个 deadline 漏网 → 「超时可调」名不副实。这里锁：
#   1. _env_timeout_s 缺变量取默认、有变量取变量、0/负被 floor 夹回（防静默关超时）；
#   2. 六个模块常量真的消费 env（reload 实跑，而非只看 helper 行为）；
#   3. 无 env 时默认值一字不变（决策 6/60/8、ceiling 5/45/7）。


def test_env_timeout_s_default_override_and_floor(monkeypatch):
    monkeypatch.delenv("UT_S", raising=False)
    assert M._env_timeout_s("UT_S", 9.0, 1.0) == 9.0
    monkeypatch.setenv("UT_S", "42")
    assert M._env_timeout_s("UT_S", 9.0, 1.0) == 42.0
    for bad in ("0", "-3", "0.0"):
        monkeypatch.setenv("UT_S", bad)
        assert M._env_timeout_s("UT_S", 9.0, 1.25) == 1.25, f"{bad} 未夹 floor → 超时被静默关掉"


def test_timeout_family_defaults_unchanged():
    assert (M._DECISION_DEADLINE_S, M._GEN_DEADLINE_S, M._STREAM_DEADLINE_S) == (6.0, 60.0, 8.0)
    assert (M._DECISION_ATTEMPT_S, M._GEN_ATTEMPT_S, M._STREAM_ATTEMPT_S) == (5.0, 45.0, 7.0)


def test_timeout_family_constants_consume_env(monkeypatch):
    """reload 实跑：证明六个常量确由 env 派生，堵住「只测 helper、常量没用它」的假绿。"""
    import importlib

    monkeypatch.setenv("LLM_GEN_DEADLINE_S", "120")
    monkeypatch.setenv("LLM_STREAM_DEADLINE_S", "0")     # → floor
    monkeypatch.setenv("LLM_DECISION_DEADLINE_S", "-1")  # → floor
    monkeypatch.setenv("LLM_GEN_ATTEMPT_S", "0")         # → floor
    try:
        importlib.reload(M)
        assert M._GEN_DEADLINE_S == 120.0
        assert M._STREAM_DEADLINE_S == M._ATTEMPT_WINDOW_S
        assert M._DECISION_DEADLINE_S == M._ATTEMPT_WINDOW_S
        assert M._GEN_ATTEMPT_S == M._ATTEMPT_MIN_S
    finally:
        monkeypatch.undo()
        importlib.reload(M)  # 复原无 env 的模块常量，避免污染后续用例
