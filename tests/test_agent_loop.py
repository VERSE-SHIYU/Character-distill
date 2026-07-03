"""Tests for AgentLoop, AgentToolkit, and ToolsNotSupportedError degradation.

Zero external dependencies — all fakes, no network/DB/LLM.
"""

from __future__ import annotations

import json
import time
from typing import Any

from adapters.llm_adapter import ToolsNotSupportedError
from core.agent.agent_loop import AgentLoop
from core.agent.tools import AgentToolkit, ToolResult


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


def test_toolkit_timeout_and_exception():
    """Real AgentToolkit: timeout and exception handling.

    a) _search_web that sleeps → times out → ok=False, content says 超时
    b) _retrieve_scenes that raises → ok=False, exception not propagated
    """
    # Speed up timeouts for testing
    AgentToolkit.WEB_TIMEOUT = 1
    AgentToolkit.SCENE_TIMEOUT = 1

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
