"""ReAct 工具调用循环：决策 → 执行 → 回填，不做最终回答生成。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from adapters.llm_adapter import ToolsNotSupportedError


@dataclass
class AgentLoopResult:
    messages: list[dict]       # 追加了 assistant(tool_calls)/tool 消息后的完整数组
    steps: list[dict]          # 每步记录 {tool, args, ok, elapsed_ms}
    degraded: bool             # True = 上层应走 legacy 路径


class AgentLoop:
    """ReAct 工具调用循环。

    每次循环：LLM 决策 → 执行工具 → 结果回填 → 再次决策。
    达到 MAX_STEPS 或模型不再请求工具时结束。
    """

    MAX_STEPS = 3

    def __init__(self, llm: Any, toolkit: Any) -> None:
        self._llm = llm
        self._toolkit = toolkit

    def run(self, system_prompt: str, messages: list[dict]) -> AgentLoopResult:
        original = messages
        messages = list(messages)  # 工作副本，所有 append 只发生在副本上
        steps: list[dict] = []
        executed: set[tuple[str, str]] = set()

        for _ in range(self.MAX_STEPS):
            try:
                msg = self._llm.chat_with_tools(
                    system_prompt, messages, self._toolkit.get_schemas()
                )
            except ToolsNotSupportedError:
                return AgentLoopResult(messages=original, steps=steps, degraded=True)
            except Exception as exc:
                print(f"[AgentLoop] chat_with_tools error: {exc}")
                return AgentLoopResult(messages=original, steps=steps, degraded=True)

            if not msg.tool_calls:
                break

            # 追加 assistant 消息（含 tool_calls）
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                dedup_key = (name, json.dumps(args, sort_keys=True))
                if dedup_key in executed:
                    result_content = "（该工具已用相同参数调用过，请基于已有结果回答）"
                    ok = True
                else:
                    executed.add(dedup_key)
                    result = self._toolkit.execute(name, args)
                    result_content = result.content
                    ok = result.ok
                    steps.append({
                        "tool": name,
                        "args": args,
                        "ok": ok,
                        "elapsed_ms": result.elapsed_ms,
                    })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_content,
                })

        print(f"[AgentLoop] steps={len(steps)} tools={[s['tool'] for s in steps]}")
        return AgentLoopResult(messages=messages, steps=steps, degraded=False)
