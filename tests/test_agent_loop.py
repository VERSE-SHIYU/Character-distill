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
from core.context_engine import RetrievalResult
from core.embeddings import current_embed_deadline  # D2：回归(a) 验证 scope 进 executor
from evidence_fakes import make_item, make_trace


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
    """Returns configured ToolResult; records execute calls for assertion.

    只回放**预制**结果，不认识工具名 —— 真件里 name→source 的映射由 AgentToolkit 的
    分发表唯一持有，这里再抄一份就是第二份会漂移的清单。要看真的 source 映射，用
    ``test_agent_evidence.py`` 里的真 AgentToolkit + 真 ContextEngine。
    """

    def __init__(
        self,
        schemas: list[dict] | None = None,
        result: ToolResult | None = None,
    ) -> None:
        self._schemas = schemas or []
        self._result = result or ToolResult(
            content="fake result", elapsed_ms=0,
            trace=make_trace("memory", "hit", text="fake result"),
        )
        self.execute_calls: list[tuple[str, dict]] = []

    def get_schemas(self) -> list[dict]:
        return self._schemas

    def execute(self, name: str, arguments: dict) -> ToolResult:
        self.execute_calls.append((name, arguments))
        return ToolResult(
            content=self._result.content,
            elapsed_ms=self._result.elapsed_ms,
            trace=self._result.trace,
        )


class FakeCtxEngine:
    """Configurable context engine for testing real AgentToolkit.

    只实现 ``_ex`` 三个出口 —— 真 ContextEngine 的 ``_retrieve_scenes`` 等字符串出口
    就只是 ``_ex(...).block``，工具层走的是 ``_ex``。假件补一份字符串出口等于给已经
    没有调用方的接口续命。
    """

    def __init__(self) -> None:
        self.scenes_result = ""
        self.scenes_sleep = 0.0
        self.scenes_exception: Exception | None = None
        self.memory_result = ""
        self.memory_sleep = 0.0
        self.memory_exception: Exception | None = None
        self.memory_hang_until_deadline = False  # D2：模拟库内 embed 挂死但尊重 scope
        self.memory_deadline_seen: float | None = None  # D2：worker 读到的截止时刻
        self.web_result = ""
        self.web_sleep = 0.0
        self.web_exception: Exception | None = None

    @staticmethod
    def _mk(source: str, block: str) -> RetrievalResult:
        """空块 = 真无匹配（empty），非空 = hit 且带一条合法 item。"""
        if not block:
            return RetrievalResult(source=source, block="", items=[], status="empty")
        return RetrievalResult(
            source=source, block=block, items=[make_item(source)], status="hit"
        )

    def _retrieve_scenes_ex(self, query: str) -> RetrievalResult:
        if self.scenes_exception:
            raise self.scenes_exception
        if self.scenes_sleep > 0:
            time.sleep(self.scenes_sleep)
        return self._mk("scene", self.scenes_result)

    def _retrieve_memories_ex(
        self, query: str, current_mood: str | None = None
    ) -> RetrievalResult:
        if self.memory_exception:
            raise self.memory_exception
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
            return self._mk("memory", "")
        if self.memory_sleep > 0:
            time.sleep(self.memory_sleep)
        return self._mk("memory", self.memory_result)

    def _search_web_ex(self, query: str) -> RetrievalResult:
        if self.web_exception:
            raise self.web_exception
        if self.web_sleep > 0:
            time.sleep(self.web_sleep)
        return self._mk("web", self.web_result)


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
        result=ToolResult(
            content="result", elapsed_ms=1, trace=make_trace("scene", "hit", text="result")
        )
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
        """search_memory 有内容，search_scenes 命中但块空（= web 改写失败那个形态）。"""
        def get_schemas(self) -> list[dict]:
            return []
        def execute(self, name: str, arguments: dict) -> ToolResult:
            source = "memory" if name == "search_memory" else "scene"
            text = "real memory" if name == "search_memory" else ""
            return ToolResult(
                content=text, elapsed_ms=1, trace=make_trace(source, "hit", text=text)
            )

    loop = AgentLoop(llm, _PickyToolkit())
    result = loop.run("hint", [_make_msg(content="remember?")])

    assert len(result.retrieved) == 1
    assert result.retrieved[0] == ("search_memory", "real memory")
    assert len(result.steps) == 2  # both tools executed
    assert result.degraded is False
    # 证据侧：两条都收（命中但块空的也算「检索发生过」），与 retrieved 口径不同
    assert [(t.source, t.status) for t in result.evidence] == [
        ("memory", "hit"), ("scene", "hit"),
    ]


def test_toolkit_timeout_and_exception(monkeypatch):
    """Real AgentToolkit: 超时与异常是**两态**，各自带专属红源，不可互换。

    a) _search_web 睡满 → fut.result 弃船 → trace.status="timeout"
    b) _retrieve_scenes 抛异常 → execute 兜底 except → trace.status="failed"
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
    assert r.trace.status == "timeout" and r.trace.source == "web"
    assert "超时" in r.content
    assert elapsed < 3.0  # didn't wait 15s

    # b) Exception: caught, returned as ok=False，且与超时**不是同一态**
    r2 = toolkit.execute("search_scenes", {"query": "test"})
    assert r2.ok is False
    assert r2.trace.status == "failed" and r2.trace.source == "scene"
    # The error message should be in the content (caught by except Exception)
    assert r2.content != ""

    # c) 失败路径没有 RetrievalResult 可投影，来源只能由分发表盖章 —— 三条入口各验一次，
    #    否则「memory 那一行 source 写错」无人发现（a/b 只覆盖 web/scene 两条）
    ctx2 = FakeCtxEngine()
    ctx2.memory_exception = RuntimeError("mem 炸了")
    r3 = AgentToolkit(ctx2).execute("search_memory", {"query": "test"})
    assert r3.trace.status == "failed" and r3.trace.source == "memory"


def test_toolkit_unknown_tool_and_missing_query_have_no_trace():
    """未发生检索的两条路径 trace=None —— 「没检索」不许伪造成「检索了但空」。"""
    toolkit = AgentToolkit(FakeCtxEngine())

    r = toolkit.execute("no_such_tool", {"query": "x"})
    assert r.trace is None and r.ok is False
    assert r.content == "未知工具: no_such_tool"

    r2 = toolkit.execute("search_scenes", {})
    assert r2.trace is None and r2.ok is False
    assert r2.content == "缺少 query 参数"


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
    # 决定性判据：没弃船 → 是「真无」而不是「超时」。只断言 content 的话，超时态的
    # 文案里也没有 EMPTY_RESULT，两态分不开。
    assert r.trace.status == "empty"
    assert elapsed < 5.0  # 早于 MEMORY_TIMEOUT=5 返回，没等弃船
    # budget = timeout − margin = 4s：deadline ≈ 提交时刻 + 4s，handler 睡到截止返回
    assert abs(r.elapsed_ms / 1000.0 - 4.0) < 1.0
