"""Tests for AgentLoop, AgentToolkit, and ToolsNotSupportedError degradation.

Zero external dependencies — all fakes, no network/DB/LLM.
"""

from __future__ import annotations

import json
import time
from typing import Any

from adapters.llm_adapter import ToolsNotSupportedError
from core.agent.agent_loop import AgentLoop
from core.agent.tools import EMPTY_RESULT, AgentToolkit, ToolResult
from core.embeddings import current_embed_deadline  # D2：回归(a) 验证 scope 进 executor


# ── Fake objects ────────────────────────────────────────────────


class FakeFunction:
    """Simulates openai ChoiceMessage.Function."""

    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    """Simulates openai ChoiceMessage.ToolCall."""

    def __init__(self, name: str, arguments: dict, id: str = "call_1") -> None:
        self.id = id
        self.function = FakeFunction(name, json.dumps(arguments, ensure_ascii=False))

    def model_dump(self) -> dict:
        return {
            "id": self.id,
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments,
            },
            "type": "function",
        }


class FakeMessage:
    """Simulates the message object returned by LLMAdapter.chat_with_tools."""

    def __init__(self, content: str = "", tool_calls: list[FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeLLM:
    """Pops FakeMessage sequentially; can be configured to raise on Nth call."""

    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = list(messages)
        self.call_count = 0
        self.fail_step: int | None = None
        self.fail_error: type[Exception] = ToolsNotSupportedError

    def chat_with_tools(
        self, system_prompt: str, messages: list[dict], tools: list[dict]
    ) -> FakeMessage:
        self.call_count += 1
        if self.fail_step is not None and self.call_count >= self.fail_step:
            raise self.fail_error("simulated degradation")
        if self._messages:
            return self._messages.pop(0)
        return FakeMessage()


class FakeToolkit:
    """Returns configured ToolResult; records execute calls for assertion."""

    def __init__(
        self,
        schemas: list[dict] | None = None,
        result: ToolResult | None = None,
    ) -> None:
        self._schemas = schemas or []
        self._result = result or ToolResult(
            tool="", ok=True, content="fake result", elapsed_ms=0
        )
        self.execute_calls: list[tuple[str, dict]] = []

    def get_schemas(self) -> list[dict]:
        return self._schemas

    def execute(self, name: str, arguments: dict) -> ToolResult:
        self.execute_calls.append((name, arguments))
        return ToolResult(
            tool=name,
            ok=self._result.ok,
            content=self._result.content,
            elapsed_ms=self._result.elapsed_ms,
        )


class FakeCtxEngine:
    """Configurable context engine for testing real AgentToolkit."""

    def __init__(self) -> None:
        self.scenes_result = ""
        self.scenes_sleep = 0.0
        self.scenes_exception: Exception | None = None
        self.memory_result = ""
        self.memory_sleep = 0.0
        self.memory_hang_until_deadline = False  # D2：模拟库内 embed 挂死但尊重 scope
        self.memory_deadline_seen: float | None = None  # D2：worker 读到的截止时刻
        self.web_result = ""
        self.web_sleep = 0.0
        self.web_exception: Exception | None = None

    def _retrieve_scenes(self, query: str) -> str:
        if self.scenes_exception:
            raise self.scenes_exception
        if self.scenes_sleep > 0:
            time.sleep(self.scenes_sleep)
        return self.scenes_result

    def _retrieve_memories(self, query: str, current_mood: str | None = None) -> str:
        dl = current_embed_deadline()
        if dl is not None:
            self.memory_deadline_seen = dl
        if self.memory_hang_until_deadline:
            # 真实路径等价物：库内 embed 挂死被 scope 夹逼 → _call_api_bounded 到截止自断
            # → 上层(MemoryManager.search/context_engine)吞成空检索 → 返回 ""。若 scope
            # 没传进 worker（回归应失败），无截止可等 → 睡到 fut.result(timeout) 先弃船。
            if dl is not None:
                rem = dl - time.monotonic()
                if rem > 0:
                    time.sleep(rem)
            else:
                time.sleep(30.0)
            return ""
        if self.memory_sleep > 0:
            time.sleep(self.memory_sleep)
        return self.memory_result

    def _search_web(self, query: str) -> str:
        if self.web_exception:
            raise self.web_exception
        if self.web_sleep > 0:
            time.sleep(self.web_sleep)
        return self.web_result


# ── Helper ──────────────────────────────────────────────────────


def _make_msg(role: str = "user", content: str = "") -> dict:
    return {"role": role, "content": content}


# ── Tests ───────────────────────────────────────────────────────


def test_no_tool_call_passthrough():
    """LLM returns plain answer without tool_calls → no extra messages."""
    llm = FakeLLM([FakeMessage(content="hello there")])
    toolkit = FakeToolkit()
    loop = AgentLoop(llm, toolkit)

    messages = [_make_msg(content="hi")]
    result = loop.run("system prompt", messages)

    assert len(result.messages) == len(messages)
    assert result.steps == []
    assert result.degraded is False
    assert llm.call_count == 1


def test_tool_call_then_answer():
    """LLM calls a tool, gets result, then answers → messages and steps correct."""
    tc = FakeToolCall("search_memory", {"query": "test"}, id="call_42")
    llm = FakeLLM([
        FakeMessage(tool_calls=[tc]),
        FakeMessage(content="I remember now"),
    ])
    toolkit = FakeToolkit()
    loop = AgentLoop(llm, toolkit)

    messages = [_make_msg(content="do you remember?")]
    result = loop.run("system", messages)

    # original + assistant(tool_calls) + tool result
    assert len(result.messages) == len(messages) + 2
    assert result.messages[-2]["role"] == "assistant"
    assert result.messages[-2]["tool_calls"] is not None
    assert result.messages[-1]["role"] == "tool"
    assert result.messages[-1]["tool_call_id"] == "call_42"
    assert len(result.steps) == 1
    assert result.steps[0]["tool"] == "search_memory"
    assert result.degraded is False
    # retrieved 应包含成功且有内容的工具结果
    assert len(result.retrieved) == 1
    assert result.retrieved[0][0] == "search_memory"
    assert result.retrieved[0][1] == "fake result"


def test_max_steps_cutoff():
    """LLM requests tools every round → stops at MAX_STEPS, no 4th call."""
    tcs = [
        FakeToolCall("search_scenes", {"query": "q1"}, id="call_1"),
        FakeToolCall("search_scenes", {"query": "q2"}, id="call_2"),
        FakeToolCall("search_scenes", {"query": "q3"}, id="call_3"),
    ]
    llm = FakeLLM([FakeMessage(tool_calls=[tc]) for tc in tcs])
    toolkit = FakeToolkit()
    loop = AgentLoop(llm, toolkit)

    messages = [_make_msg(content="hi")]
    result = loop.run("system", messages)

    assert llm.call_count == AgentLoop.MAX_STEPS  # exactly 3, not 4
    assert len(result.steps) == AgentLoop.MAX_STEPS
    assert result.degraded is False


def test_dedup_same_tool_same_args():
    """Same (tool, args) twice → second is skipped, execute called once."""
    tc1 = FakeToolCall("search_scenes", {"query": "same"}, id="call_1")
    tc2 = FakeToolCall("search_scenes", {"query": "same"}, id="call_2")
    llm = FakeLLM([
        FakeMessage(tool_calls=[tc1]),
        FakeMessage(tool_calls=[tc2]),
        FakeMessage(content="done"),
    ])
    toolkit = FakeToolkit(
        result=ToolResult(tool="search_scenes", ok=True, content="result", elapsed_ms=1)
    )
    loop = AgentLoop(llm, toolkit)

    messages = [_make_msg(content="hi")]
    result = loop.run("system", messages)

    # execute only called once
    assert len(toolkit.execute_calls) == 1
    assert len(result.steps) == 1

    # two tool result messages, second is dedup hint
    tool_msgs = [m for m in result.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert "已用相同参数调用过" in tool_msgs[1]["content"]
    assert result.degraded is False
    # retrieved 应只有一次有效结果（dedup 不重复收集）
    assert len(result.retrieved) == 1
    assert result.retrieved[0][0] == "search_scenes"


def test_degraded_returns_original_messages():
    """After a tool_call round, ToolsNotSupportedError → degraded with clean messages.

    Asserts both the returned value AND that the caller's original list
    was never mutated.
    """
    tc = FakeToolCall("search_memory", {"query": "test"}, id="call_1")
    llm = FakeLLM([FakeMessage(tool_calls=[tc])])
    llm.fail_step = 2  # raise on second chat_with_tools call
    llm.fail_error = ToolsNotSupportedError

    toolkit = FakeToolkit()
    loop = AgentLoop(llm, toolkit)

    input_msgs = [_make_msg(content="hi")]
    input_snapshot = list(input_msgs)

    result = loop.run("system", input_msgs)

    assert result.degraded is True
    # returned messages match original content
    assert result.messages == input_snapshot
    # returned messages IS the same list object (referential equality)
    assert result.messages is input_msgs
    # caller's list was never mutated
    assert input_msgs == input_snapshot
    # first call happened, second raised
    assert llm.call_count == 2


def test_retrieved_collection():
    """仅成功且有内容的工具结果出现在 retrieved 中。"""
    tcs = [
        FakeToolCall("search_memory", {"query": "q1"}, id="call_1"),
        FakeToolCall("search_scenes", {"query": "q2"}, id="call_2"),
    ]
    llm = FakeLLM([
        FakeMessage(tool_calls=[tcs[0], tcs[1]]),
        FakeMessage(content="done"),
    ])

    class _PickyToolkit:
        """Returns ok=True with content for search_memory, empty for search_scenes."""
        def get_schemas(self) -> list[dict]:
            return []
        def execute(self, name: str, arguments: dict) -> ToolResult:
            if name == "search_memory":
                return ToolResult(tool=name, ok=True, content="real memory", elapsed_ms=1)
            return ToolResult(tool=name, ok=True, content="", elapsed_ms=1)

    loop = AgentLoop(llm, _PickyToolkit())
    result = loop.run("hint", [_make_msg(content="remember?")])

    assert len(result.retrieved) == 1
    assert result.retrieved[0] == ("search_memory", "real memory")
    assert len(result.steps) == 2  # both tools executed
    assert result.degraded is False


def test_toolkit_timeout_and_exception(monkeypatch):
    """Real AgentToolkit: timeout and exception handling.

    a) _search_web that sleeps → times out → ok=False, content says 超时
    b) _retrieve_scenes that raises → ok=False, exception not propagated
    """
    monkeypatch.setattr(AgentToolkit, "WEB_TIMEOUT", 1)
    monkeypatch.setattr(AgentToolkit, "SCENE_TIMEOUT", 1)

    ctx = FakeCtxEngine()
    ctx.web_sleep = 5.0   # exceeds 1s timeout
    ctx.scenes_exception = ValueError("scene retrieval exploded")

    toolkit = AgentToolkit(ctx)

    # a) Timeout: must return quickly, not sleep the full duration
    t0 = time.monotonic()
    r = toolkit.execute("web_search", {"query": "test"})
    elapsed = time.monotonic() - t0
    assert r.ok is False
    assert "超时" in r.content
    assert elapsed < 3.0  # didn't wait 15s

    # b) Exception: caught, returned as ok=False
    r2 = toolkit.execute("search_scenes", {"query": "test"})
    assert r2.ok is False
    # The error message should be in the content (caught by except Exception)
    assert r2.content != ""


def test_execute_embed_deadline_reaches_worker(monkeypatch):
    """回归(a)：tools.execute 把 embed deadline scope 传进 executor 线程，库内挂死被夹逼。

    fake ctx 尊重 current_embed_deadline()（等价于真实路径里 mem0/chroma 库内 embed 被
    _call_api_bounded 到截止自断、上层吞成空检索）→ handler 在 fut.result(timeout) 弃船前
    自行返回 "" → execute 拿到空检索结果而非"工具执行超时"，无弃船、无线程残留。

    D2 前会失败的两点：
      1. ContextVar 不跨裸线程 → worker 读不到 scope → memory_deadline_seen 为 None；
      2. handler 无人夹逼 → 睡满 → fut.result(5s) 弃船 → content 含"工具执行超时"，
         handler 线程残留到 embed 放弃（leak_probe 实测 ~16.5s）。
    """
    monkeypatch.setattr(AgentToolkit, "MEMORY_TIMEOUT", 5)
    ctx = FakeCtxEngine()
    ctx.memory_hang_until_deadline = True
    toolkit = AgentToolkit(ctx)

    t0 = time.monotonic()
    r = toolkit.execute("search_memory", {"query": "novel hang query"})
    elapsed = time.monotonic() - t0

    assert ctx.memory_deadline_seen is not None  # scope 真进了 executor worker 线程
    assert r.ok is False
    assert "工具执行超时" not in r.content  # 不是 fut.result 弃船
    assert r.content == EMPTY_RESULT  # 空检索结果的正常文案
    assert elapsed < 5.0  # 早于 MEMORY_TIMEOUT=5 返回，没等弃船
    # budget = timeout − margin = 4s：deadline ≈ 提交时刻 + 4s，handler 睡到截止返回
    assert abs(r.elapsed_ms / 1000.0 - 4.0) < 1.0
