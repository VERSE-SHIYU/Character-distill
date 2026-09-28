# -*- coding: utf-8 -*-
"""`LLMAdapter.select_by_schema`（按编号挑选对话示例）的三条锁：发往哪、带不带 strict、用哪档预算。

为什么要单开一个文件：这里锁的是这一步独有的取舍 —— 「只有 DeepSeek 用 strict」「用生成轮
预算而不是决策轮」。混进 `chat_with_tools` 那批回归里，改坏它们的只有这一条用例会红，而它
读起来像在测别的东西。

**变异**（每条用例都配一个）：去掉 strict / 发往正式地址 / 换回决策轮预算 → 各自变红。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import adapters.llm_adapter as M
from adapters.llm_adapter import LLMAdapter

DEEPSEEK_OFF = {"thinking": {"type": "disabled"}}
QWEN_OFF = {"enable_thinking": False}

PICKED = {"picks": [2, 5]}
TOOL = {
    "name": "pick_dialogue",
    "description": "按编号挑选对话示例",
    "parameters": {
        "type": "object",
        "properties": {"picks": {"type": "array", "items": {
            "type": "integer", "minimum": 1, "maximum": 7}}},
        "required": ["picks"],
    },
}
MSGS = [{"role": "user", "content": "候选…"}]


def _resp(arguments):
    """一条响应：arguments 为 JSON 文本时带 tool_call，为 None 时 tool_calls 为空。"""
    calls = [] if arguments is None else [
        SimpleNamespace(function=SimpleNamespace(name=TOOL["name"], arguments=arguments))]
    msg = SimpleNamespace(content=None, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
                           usage=None)


class _Completions:
    """记录每次 create 的 kwargs/timeout；可选地按「读入耗时」占住连接。"""

    def __init__(self, arguments=json.dumps(PICKED), delay_s=0.0, clock=None):
        self.kwargs: dict = {}
        self.calls = 0
        self.timeouts: list = []
        self._arguments = arguments
        self._delay = delay_s
        self._clock = clock

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        self.timeouts.append(kwargs.get("timeout"))
        if self._delay:
            # 真实上游按输入长度占住连接：读完要 delay_s；客户端 timeout 先到就中断。
            if kwargs["timeout"] < self._delay:
                self._clock.sleep(kwargs["timeout"])
                raise M.Timeout("read timed out")
            self._clock.sleep(self._delay)
        return _resp(self._arguments)


class _FakeOpenAI:
    """替代 OpenAI 构造：留下构造 kwargs（base_url 是 strict 那条的判据），并给一个可用的 create。

    本沙箱里真构造会在 httpx ssl create_default_context 上偶发挂起（环境性，非代码缺陷），
    与 `tests/test_llm_adapter_retry.py` 用同一个替身手法。
    """

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=_Completions())


@pytest.fixture(autouse=True)
def _no_real_openai(monkeypatch):
    monkeypatch.setattr(M, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(M, "AsyncOpenAI", _FakeOpenAI)


def _llm(base_url=None) -> LLMAdapter:
    return LLMAdapter(api_key="sk-test-fake", base_url=base_url)


# ── 发往哪 + strict ───────────────────────────────────────────────────

def test_deepseek_picking_goes_to_beta_with_strict_and_thinking_off():
    """DeepSeek 方言：发往 /beta、function 带 strict: true、强制调用该工具、关闭思考。

    变异：① 把 strict 去掉 → 断言 red（服务端就不校验 schema 了）；
          ② 客户端仍用 `self._client` → base_url 断言 red 且「正式客户端零调用」red；
          ③ 不注入 `_request_options()` → extra_body 断言 red（思考模式带 tools 且不回传
             reasoning_content 会 400，所以 /beta 客户端也必须关思考）。
    """
    llm = _llm("https://api.deepseek.com")
    assert llm.select_by_schema("sys", MSGS, TOOL) == PICKED

    beta = llm._beta_client
    assert beta.kwargs["base_url"] == "https://api.deepseek.com/beta"
    assert beta.kwargs["max_retries"] == 0
    sent = beta.chat.completions.kwargs
    assert sent["tools"] == [{"type": "function", "function": {**TOOL, "strict": True}}]
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "pick_dialogue"}}
    assert sent["extra_body"] == DEEPSEEK_OFF
    assert llm._client.chat.completions.calls == 0, "严格模式的请求不该落在正式地址那台客户端上"


def test_the_beta_client_is_created_once_and_v1_base_urls_still_land_on_beta():
    llm = _llm("https://api.deepseek.com/v1")
    first = llm._strict_client()
    assert first.kwargs["base_url"] == "https://api.deepseek.com/beta"
    assert llm._strict_client() is first, "每次调用都新建客户端会把连接池丢掉"


def test_other_dialects_pick_on_the_normal_client_without_strict():
    """其他方言：同一工具、不带 strict、走现客户端；关闭思考照旧注入。

    变异：无条件加 strict → 第一条断言 red（多数供应商不认这个字段，400 会让该用户
    永久失败）；无条件建 /beta 客户端 → 第二条 red。
    """
    llm = _llm("https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert llm.select_by_schema("sys", MSGS, TOOL) == PICKED

    assert llm._beta_client is None, "非 DeepSeek 方言不该建 /beta 客户端"
    sent = llm._client.chat.completions.kwargs
    assert sent["tools"][0]["type"] == "function"
    assert sent["tools"][0]["function"]["name"] == "pick_dialogue"
    assert "strict" not in sent["tools"][0]["function"]
    assert sent["extra_body"] == QWEN_OFF


# ── 参数校验（非 strict 时模型可能给不合法 JSON 或编造参数）─────────────

@pytest.mark.parametrize("arguments, needle", [
    (None, "没有调用工具"),
    ('{"picks": [', "不是合法 JSON"),
    ('"picks"', "不是 JSON 对象"),
])
def test_unusable_tool_arguments_fail_the_call(arguments, needle, monkeypatch):
    """没有 tool_calls、非法 JSON、不是对象 —— 三种都判失败，不把垃圾当挑选结果。

    变异：直接 `json.loads(msg.tool_calls[0].function.arguments)` 不做存在性/类型检查 →
    前两条会以 AttributeError/JSONDecodeError 的形状漏出去，调用方分不清是挑选失败还是
    代码错误。
    """
    monkeypatch.setattr(M, "_GEN_ATTEMPTS", 1)
    llm = _llm("https://dashscope.aliyuncs.com/compatible-mode/v1")
    llm._client = _FakeOpenAI()
    llm._client.chat.completions = _Completions(arguments=arguments)
    with pytest.raises(RuntimeError, match=needle):
        llm.select_by_schema("sys", MSGS, TOOL)


def test_a_parse_failure_is_retried_like_any_failed_request(monkeypatch):
    """解析失败也走重试：它和一次失败的请求一样值得重发，而不是整步作废。

    变异：把 `extract` 挪到循环**外**（拿到 message 之后再解析）→ 只发一次，
    断言 red（非 strict 的供应商偶发一次坏 JSON 就整任务失败）。
    """
    monkeypatch.setattr(M, "_GEN_BACKOFF_S", 0.01)
    llm = _llm("https://dashscope.aliyuncs.com/compatible-mode/v1")
    llm._client = _FakeOpenAI()
    llm._client.chat.completions = _Completions(arguments="{不是 JSON")
    with pytest.raises(RuntimeError, match="不是合法 JSON"):
        llm.select_by_schema("sys", MSGS, TOOL)
    assert llm._client.chat.completions.calls == M._GEN_ATTEMPTS


# ── 预算：输入大，决策轮必超时 ─────────────────────────────────────────

class _FakeClock:
    """可注入的单调时钟 + sleep：把「真等」变成「拨表」，时限按比例缩小才敢这么写。"""

    def __init__(self):
        self.now = 1000.0
        self.slept: list = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def test_a_read_that_outlives_the_decision_budget_survives_the_generation_budget(monkeypatch):
    """读入耗时落在「决策轮上限」与「生成轮上限」之间：生成轮一次读完，决策轮必超时。

    本步骤输入最大约 7 万 token（宝玉），读入本身就要数秒 —— 决策轮那档（单次 5s / 总 6s，
    前提是「短小的路由决策」）撑不住。四档时限按同一比例缩小，免得用例真等 45 秒；两档之间
    的关系（决策 ceiling < 读入 < 生成 ceiling）不变。
    """
    SCALE = 0.25
    dec_single, dec_total = 5.0 * SCALE, 6.0 * SCALE
    gen_single, gen_total = 45.0 * SCALE, 60.0 * SCALE
    latency = 3.0                      # dec_single(1.25) < 3.0 < gen_single(11.25)

    clock = _FakeClock()
    monkeypatch.setattr(M, "time", SimpleNamespace(monotonic=clock.monotonic,
                                                   sleep=clock.sleep))
    monkeypatch.setattr(M, "_DECISION_ATTEMPT_S", dec_single)
    monkeypatch.setattr(M, "_DECISION_DEADLINE_S", dec_total)
    monkeypatch.setattr(M, "_GEN_ATTEMPT_S", gen_single)
    monkeypatch.setattr(M, "_GEN_DEADLINE_S", gen_total)

    llm = _llm("https://dashscope.aliyuncs.com/compatible-mode/v1")
    slow = _Completions(delay_s=latency, clock=clock)
    llm._client = _FakeOpenAI()
    llm._client.chat.completions = slow

    assert llm.select_by_schema("sys", MSGS, TOOL) == PICKED
    assert slow.calls == 1, "一次读完就该成功，不该重试"
    assert slow.timeouts[0] == pytest.approx(gen_single), \
        "单次超时由生成轮的 ceiling 定，不是决策轮的"
