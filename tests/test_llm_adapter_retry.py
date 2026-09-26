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
import ast
import asyncio
import pathlib
import time
from types import SimpleNamespace

import httpx2
import pytest
from conftest import cause_chain
from openai import BadRequestError
from openai import OpenAI as _REAL_OPENAI
from openai import Timeout as _OpenAITimeout

import adapters.llm_adapter as M
from adapters.llm_adapter import LLMAdapter, ToolsNotSupportedError, user_facing_error


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
        self.retry_after = retry_after
        self.status_code = 429
        self.response = SimpleNamespace(headers={"Retry-After": retry_after})

    def __reduce__(self):
        return (self.__class__, (self.retry_after,))


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


class _FakeClock:
    """可注入的单调时钟 + sleep：把「等 1s」变成「记账 + 拨表」，不再真等墙钟。

    adapter 没有可注入的时钟（`time.sleep` / `time.monotonic` 都是直调），故替换模块里
    的 `time` 绑定 —— 只影响 `adapters.llm_adapter` 这一份引用，比打 `time` 模块本身干净。
    """

    def __init__(self):
        self.now = 1000.0
        self.slept: list = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def test_decision_429_has_own_attempt_cap(monkeypatch):
    # 必修2：429 不计入非429 attempts，但有独立次数上限（_RATE_LIMIT_ATTEMPTS）。
    # Retry-After=1s 若只受 deadline(6s) 约束最多打 6 次；cap=3 → 3 次即抛、不再高频重打。
    # （真实 Retry-After 是整秒；isdigit 不认小数，故用整数秒走 _classify_retry 解析路径。）
    # 断言的是「打了几次」与「每次按 Retry-After 等了多久」，不是墙钟时长。
    monkeypatch.setattr(M, "_RATE_LIMIT_ATTEMPTS", 3)
    clock = _FakeClock()
    monkeypatch.setattr(M, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    llm = _make_llm()
    fake = _SyncClient(_storm(_RateLimitError429("1")))
    llm._client = fake
    with pytest.raises(RuntimeError, match=r"rate limited \(429\) after 3 attempts"):
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert fake.chat.completions.calls == 3
    assert clock.slept == [1.0, 1.0], f"每次退避都应取 Retry-After=1s，实得 {clock.slept}"


def _status_error(code: int) -> Exception:
    """带状态码的假上游异常：驱动 _classify_retry 与 _UPSTREAM_USER_MESSAGES。

    状态码挂**类属性**、不写自定义 `__init__` —— 免得本文件多一个带自定义状态的异常类，
    去惊动 tests/test_exception_pickle_lock.py 的全仓普查。
    """
    return type("_StatusError", (RuntimeError,), {"status_code": code})(f"upstream {code}")


@pytest.mark.parametrize("code,expected", [
    (401, "API Key 无效或无权限，请到设置页检查"),
    (402, "账户余额不足，请充值后重试"),
    (429, "请求过于频繁，请稍后再试"),
    (500, "服务暂时不可用，请稍后重试"),  # 未登记 → 通用文案，原文绝不因此上屏
])
def test_exhausted_upstream_failure_carries_user_message(code, expected, monkeypatch):
    """缺陷 94（泄漏那半）：次数耗尽时抛 UpstreamFailure，上屏口径由 adapters 这张表统一给。

    未登记的状态码落通用文案 —— 路由层据此上屏，内部原文（含状态码与上游报错）只留在
    str(exc) 里进日志。str() 逐字不变：core 侧既有的 "failed after N attempts" /
    "rate limited (429)" 判据继续命中。
    """
    monkeypatch.setattr(M, "_DECISION_ATTEMPTS", 1)
    monkeypatch.setattr(M, "_RATE_LIMIT_ATTEMPTS", 1)
    llm = _make_llm()
    fake = _SyncClient(_storm(_status_error(code)))
    llm._client = fake
    with pytest.raises(RuntimeError) as ei:
        llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    exc = ei.value
    assert user_facing_error(exc) == expected
    assert f"upstream {code}" in str(exc), "原文必须留在 str() 里进日志，不上屏"


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


# ── WP12 S5：async_chat 用生成轮墙钟预算，重试不被 60s 总闸饿死 ──────────────
#
# 旧 `async_chat` 复用 `_GEN_DEADLINE_S=60`：第 1 次 attempt 吃满 45s ceiling 一晃掉，
# 退避后第 2 次只剩约 9s、第 3 次分不到时间（生产：02:56:16 超时 → 02:56:31「2 次
# 尝试均失败」；同一片单发 10s 就完了，60 并发整轮 31s 零失败）。假时钟把「等 45s」
# 变成拨表，不真等墙钟。


def _starved_retry_llm(monkeypatch, succeed_on: int):
    """前 `succeed_on - 1` 次 attempt 各吃满 `_GEN_ATTEMPT_S` 才超时，之后成功。"""
    clock = _FakeClock()
    monkeypatch.setattr(M, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    llm = _make_llm()
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        if calls["n"] < succeed_on:
            clock.now += M._GEN_ATTEMPT_S           # 该 attempt 用满 ceiling 才超时
            raise RuntimeError("upstream read timeout")
        return _Resp("ok")

    llm._async_client = _AsyncClient(behavior)
    return llm, calls


def test_async_chat_retry_not_starved_by_total_deadline(monkeypatch):
    llm, calls = _starved_retry_llm(monkeypatch, succeed_on=3)

    result, _usage = asyncio.run(llm.async_chat("sys", [{"role": "user", "content": "hi"}]))

    assert result == "ok"
    assert calls["n"] == 3, "重试被总墙钟饿死：第 3 次没轮到"


def test_achat_keeps_the_interactive_deadline(monkeypatch):
    """群聊 / 审核走 `achat`，仍按交互式 60s 封顶 —— 长预算只给批量 Map。"""
    llm, calls = _starved_retry_llm(monkeypatch, succeed_on=3)

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        asyncio.run(llm.achat("sys", [{"role": "user", "content": "hi"}]))

    assert calls["n"] == 2


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
#   2. 六个模块常量真的消费 env（真跑模块顶层，而非只看 helper 行为）；
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
    # 长输出的读超时天花板（WP1）：不做 env 出口，故只在默认值这一处钉住。
    assert M._BATCH_STREAM_READ_S == 300.0


def _load_env_probe():
    """按另一个模块名加载 `adapters/llm_adapter.py` 的一份**私有副本**，读它顶层算出的常量。

    **不 reload `sys.modules` 里那一份**：`importlib.reload(M)` 把模块属性整体换新，异常类
    对象也跟着换 —— 别的测试文件在收集期 import 到的旧类引用（`pytest.raises(
    IncompleteResponseError)`）随即抓不到新类，按「retry → error → chat」的文件顺序跑就红 4 条
    （既有污染，`3d1d171` 引入）。

    副本**不写进 `sys.modules`**：注册了就等于又替了一份全局单例，别处再 `import
    adapters.llm_adapter` 会拿到这份带 env 的版本。命题（常量确由 env 派生）不变 ——
    它判的是模块顶层那几行 `_env_timeout_s(...)`，与是哪一份实例无关。
    """
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "_llm_adapter_env_probe", pathlib.Path(M.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_timeout_family_constants_consume_env(monkeypatch):
    """真跑模块顶层：证明六个常量确由 env 派生，堵住「只测 helper、常量没用它」的假绿。"""
    monkeypatch.setenv("LLM_GEN_DEADLINE_S", "120")
    monkeypatch.setenv("LLM_STREAM_DEADLINE_S", "0")     # → floor
    monkeypatch.setenv("LLM_DECISION_DEADLINE_S", "-1")  # → floor
    monkeypatch.setenv("LLM_GEN_ATTEMPT_S", "0")         # → floor

    probe = _load_env_probe()

    assert probe._GEN_DEADLINE_S == 120.0
    assert probe._STREAM_DEADLINE_S == probe._ATTEMPT_WINDOW_S
    assert probe._DECISION_DEADLINE_S == probe._ATTEMPT_WINDOW_S
    assert probe._GEN_ATTEMPT_S == probe._ATTEMPT_MIN_S


# ── WP1 S1：长输出流放宽读超时（真 openai 客户端 + 真 socket）──────────
# 桩客户端能记下 create() 收到的 timeout 形状（下面第三条），却不会真的在读上超时 ——
# 「静默 1.0s 之后到底还活不活」只能对真 socket 判（缺陷：生产识别 500 于 7s 读超时）。
# 本文件的 autouse `_no_real_openai` 把 M.OpenAI 换成了假构造，故这两条先换回真客户端。

_LONG_MSGS = [{"role": "user", "content": "hi"}]


def test_long_output_stream_survives_prefill_silence(fake_sse, monkeypatch):
    """S1：长输出流容忍首字节前的静默（大 prefill 段无数据 ≠ 故障）。"""
    monkeypatch.setattr(M, "OpenAI", _REAL_OPENAI)
    monkeypatch.setattr(M, "_STREAM_ATTEMPT_S", 0.5)    # 不真等：只留 0.5s 的读窗
    monkeypatch.setattr(M, "_STREAM_DEADLINE_S", 1.5)
    fake_sse.plan.update(silent_ms=1000, tokens=["long-ok"])

    got = list(fake_sse.adapter().chat_stream_long("sys", _LONG_MSGS))

    assert got == ["long-ok"], f"1.0s 静默被当成故障了：{got!r}"


def test_chat_stream_still_times_out_on_silence(fake_sse, monkeypatch):
    """S1 的另一半：聊天流（走短路径 `chat_stream`）逐字维持现状 —— 同样的静默仍按读超时失败。"""
    monkeypatch.setattr(M, "OpenAI", _REAL_OPENAI)
    monkeypatch.setattr(M, "_STREAM_ATTEMPT_S", 0.5)
    monkeypatch.setattr(M, "_STREAM_DEADLINE_S", 1.5)
    fake_sse.plan.update(silent_ms=1000, tokens=["never"])

    with pytest.raises(Exception) as ei:
        list(fake_sse.adapter().chat_stream("sys", _LONG_MSGS))

    assert any(isinstance(e, httpx2.ReadTimeout) for e in cause_chain(ei.value)), (
        f"聊天流的静默必须仍是读超时（不然放宽就没作用在长输出这一侧）："
        f"{[type(e).__name__ for e in cause_chain(ei.value)]}"
    )


def test_long_output_widens_read_timeout_only():
    """S1 变异②：放宽只作用于 read；connect / write / pool 仍取本次 attempt 的 ceiling。

    放宽成 `Timeout(_BATCH_STREAM_READ_S)`（整体放大）会让上游不可达时的连接等待从
    7s 变成 5 分钟 —— 决策/生成两轮的 connect 口径不该被长输出读窗连坐。
    """
    llm = _make_llm()
    fake = _SyncClient(lambda: iter([_StreamChunk("a")]))
    llm._client = fake

    assert list(llm.chat_stream_long("sys", _LONG_MSGS)) == ["a"]

    t = fake.chat.completions.timeouts[0]
    assert isinstance(t, _OpenAITimeout), f"长输出必须传分项 Timeout，实际 {t!r}"
    assert t.read == M._BATCH_STREAM_READ_S == 300.0
    assert t.connect == pytest.approx(M._STREAM_ATTEMPT_S, abs=0.2), "connect 不得被连坐放大"

    fake2 = _SyncClient(lambda: iter([_StreamChunk("b")]))
    llm._client = fake2
    assert list(llm.chat_stream("sys", _LONG_MSGS)) == ["b"]
    assert isinstance(fake2.chat.completions.timeouts[0], float), (
        "聊天流必须逐字维持现状（标量超时），否则 default 之外的行为也变了"
    )


# ── WP1.4（路 D）：长输出流只有一个入口 ────────────────────────────────

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _llm_call_sites(src: str, rel: str) -> list[tuple[str, int]]:
    """`self._llm.<name>(...)` 形态的调用点 → [(方法名, 行号)]。"""
    sites = []
    for n in ast.walk(ast.parse(src, filename=rel)):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "_llm"):
            sites.append((n.func.attr, n.lineno))
    return sites


def test_long_output_has_exactly_one_entry():
    """distiller 侧只许走长输出入口，不得直接调短路径。

    短路径的读超时是本次 attempt 的 ceiling（7s），长输入一 prefill 就假失败；漏一处
    就是一条会在生产上静默读超时的路，而它从调用点本身看不出来（变异：任一处改回
    `self._llm.chat_stream(` → 本条红）。入口侧的「放宽只在一个函数里发生」由
    `test_long_output_widens_read_timeout_only` 逐参数钉住，不在这里重复。
    """
    distiller = (_REPO_ROOT / "core/distiller.py").read_text(encoding="utf-8")
    short = [ln for name, ln in _llm_call_sites(distiller, "core/distiller.py")
             if name == "chat_stream"]
    assert not short, f"core/distiller.py 仍有直接调短路径的调用点（漏改）：{short}"
