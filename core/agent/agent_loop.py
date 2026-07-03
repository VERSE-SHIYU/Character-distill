"""ReAct 工具调用循环：决策 → 执行 → 回填，不做最终回答生成。

决策与生成解耦：决策轮使用中性路由器 prompt + 截断角色背景，
避免完整角色 prompt 压制工具调用。检索结果通过 retrieved 字段
返回给上层拼装，不留在 messages 中。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from adapters.llm_adapter import ToolsNotSupportedError
from core.agent.tools import EMPTY_RESULT

ROUTER_SYSTEM_PROMPT = (
    "你是一个对话系统的检索决策器。你的唯一职责是判断："
    "为了让角色更好地回复用户的最新消息，是否需要检索信息。"
    "需要则调用相应工具（可多个），不需要则不调用任何工具、直接返回空内容。"
    "不要回答用户的问题本身。"
    "判断依据："
    "对方提及过往交流或共同回忆→search_memory；"
    "涉及角色原著经历/情节→search_scenes；"
    "需要现实世界实时或事实信息→web_search；"
    "纯寒暄、情绪表达、即兴互动→不调用。"
)


@dataclass
class AgentLoopResult:
    messages: list[dict]             # 追加了 assistant(tool_calls)/tool 消息后的完整数组
    steps: list[dict]                # 每步记录 {tool, args, ok, elapsed_ms}
    degraded: bool                   # True = 上层应走 legacy 路径
    retrieved: list[tuple[str, str]] = field(default_factory=list)  # 仅 ok=True 且内容非空的结果


class AgentLoop:
    """ReAct 工具调用循环。

    每次循环：中性路由器 prompt + 截断角色背景 → LLM 决策 → 执行工具 → 结果回填 → 再次决策。
    达到 MAX_STEPS 或模型不再请求工具时结束。
    检索结果通过 ``result.retrieved`` 返回，不混入 messages。
    """

    MAX_STEPS = 3

    def __init__(self, llm: Any, toolkit: Any) -> None:
        self._llm = llm
        self._toolkit = toolkit

    def run(self, character_hint: str, messages: list[dict]) -> AgentLoopResult:
        """执行工具决策循环。

        *character_hint* 是角色背景提示（如完整 system prompt 或角色一句话），
        实际发给 LLM 的是 ROUTER_SYSTEM_PROMPT + 截断至前 300 字符的角色背景。
        """
        original = messages
        messages = list(messages)  # 工作副本，所有 append 只发生在副本上
        steps: list[dict] = []
        executed: set[tuple[str, str]] = set()
        retrieved: list[tuple[str, str]] = []

        # 构建路由器 system prompt：路由指令 + 截断至 300 字符的角色背景
        truncated_hint = (character_hint or "")[:300]
        if truncated_hint:
            router_sp = ROUTER_SYSTEM_PROMPT + "\n\n【角色背景】" + truncated_hint
        else:
            router_sp = ROUTER_SYSTEM_PROMPT

        for _ in range(self.MAX_STEPS):
            try:
                msg = self._llm.chat_with_tools(
                    router_sp, messages, self._toolkit.get_schemas()
                )
            except ToolsNotSupportedError:
                return AgentLoopResult(messages=original, steps=steps, degraded=True, retrieved=retrieved)
            except Exception as exc:
                print(f"[AgentLoop] chat_with_tools error: {exc}")
                return AgentLoopResult(messages=original, steps=steps, degraded=True, retrieved=retrieved)

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
                    # 收集成功且非空的结果
                    if ok and result_content and result_content != EMPTY_RESULT:
                        retrieved.append((name, result_content))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_content,
                })

        print(f"[AgentLoop] steps={len(steps)} tools={[s['tool'] for s in steps]}")
        return AgentLoopResult(messages=messages, steps=steps, degraded=False, retrieved=retrieved)
