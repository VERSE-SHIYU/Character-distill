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
from core.schema import SourceTrace
from core import telemetry as T  # OTel 埋点（OTEL_ENABLED 关时装饰器原样返回，零开销）
from core.utils import try_record_usage

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
    # **实际执行过**的工具调用台账：每条 = 一次执行 {tool, args, ok, elapsed_ms}。
    # 去重（dedup，见 run()）没发生执行 → 不入。与 MAX_STEPS **不同义**：后者数的是
    # 决策轮次，一轮可发多个调用，故本字段条数可大于轮数（一轮三调用 → +3 条一轮）。
    steps: list[dict]
    degraded: bool                   # True = 上层应走 legacy 路径
    retrieved: list[tuple[str, str]] = field(default_factory=list)  # 仅 ok=True 且内容非空的结果
    # 证据侧出口：每次真实工具调用的 SourceTrace，按调用顺序。与 retrieved 是**两个口径**——
    # retrieved 只收「ok 且块非空」（prompt 侧口径），evidence 收全部真实调用含空/失败/超时
    # （证据侧口径）。空与失败不许在这里被吞成「没检索过」，否则前端看到的「检索来源」会静默
    # 少几条。去重（dedup）的调用不产生新 trace：同一查询没发生第二次检索。
    evidence: list[SourceTrace] = field(default_factory=list)


class AgentLoop:
    """ReAct 工具调用循环。

    每次循环：中性路由器 prompt + 截断角色背景 → LLM 决策 → 执行工具 → 结果回填 → 再次决策。
    达到 MAX_STEPS 或模型不再请求工具时结束。
    检索结果通过 ``result.retrieved`` 返回，不混入 messages。
    """

    MAX_STEPS = 3

    def __init__(self, llm: Any, toolkit: Any, storage: Any = None) -> None:
        self._llm = llm
        self._toolkit = toolkit
        # storage 由调用方（ChatEngine）注入；缺省 None → try_record_usage 显式报「无法记账」，
        # 不静默丢弃。每步决策都是一次真实 LLM 花费，必须落账。
        # **归属不由构造注入**：谁在调由记账出口自己读上下文（缺陷 83/84）。
        self._storage = storage

    @T.spanned("agent.plan", op="plan")
    def run(self, character_hint: str, messages: list[dict]) -> AgentLoopResult:
        """执行工具决策循环。

        *character_hint* 是角色背景提示（如完整 system prompt 或角色一句话），
        实际发给 LLM 的是 ROUTER_SYSTEM_PROMPT + 截断至前 300 字符的角色背景。

        三个台账**各记各的事实**，互不代偿（同一事件在三处的表述必须一致）：

          ``messages``  = **模型经历的** —— 模型视野的完整数组，含去重那一轮的回填
          ``steps``     = **实际执行的** —— 每次真实调用一条，带耗时与成败
          ``evidence``  = **检索发生的** —— 每次真实调用的 ``SourceTrace``，含空/失败/超时

        去重（dedup）是**编排层决策**，不是工具执行结果：没有执行、没有检索，故只进
        ``messages``。把它塞进 ``steps`` 会让同一事件在两个台账里说法相反。
        """
        original = messages
        messages = list(messages)  # 工作副本，所有 append 只发生在副本上
        steps: list[dict] = []
        executed: set[tuple[str, str]] = set()
        retrieved: list[tuple[str, str]] = []
        evidence: list[SourceTrace] = []

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
                return AgentLoopResult(
                    messages=original, steps=steps, degraded=True,
                    retrieved=retrieved, evidence=evidence,
                )
            except Exception as exc:
                print(f"[AgentLoop] chat_with_tools error: {exc}")
                return AgentLoopResult(
                    messages=original, steps=steps, degraded=True,
                    retrieved=retrieved, evidence=evidence,
                )

            # 每步决策都是一次真实 LLM 花费，紧跟调用后落账。last_usage 在 chat_with_tools
            # 入口已置 None，故厂商未回 usage 时记「无数据」而非冒用上一轮的值（串号比漏记更糟）。
            try_record_usage(self._storage, self._llm, "chat_agent_route", source="AgentLoop")

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
                    # 编排层决策，**不是**工具执行结果：不记 steps（没发生执行，也就无所谓耗时
                    # 与成败）、不产新 trace（同一查询没发生第二次检索，Evidence commit 3 定死）。
                    # 模型确实收到了这一轮回填 —— 那记在 messages 里。三个台账对同一事件一致。
                    result_content = "（该工具已用相同参数调用过，请基于已有结果回答）"
                else:
                    executed.add(dedup_key)
                    with T.span("agent.execute_tool", op="execute_tool", attrs={"tool": name}):
                        result = self._toolkit.execute(name, args)
                    result_content = result.content
                    ok = result.ok
                    steps.append({
                        "tool": name,
                        "args": args,
                        "ok": ok,
                        "elapsed_ms": result.elapsed_ms,
                    })
                    # 定位：**执行台账**（「实际跑了哪几次、各花多久、块空不空」）。三键当前
                    # 无判定读者（全仓只读 tool 名与条数），保留是给调试/可观测留的；见缺陷 31。
                    # 证据侧：与 ok 无关地收下（空/失败/超时也是「检索发生过」的证据）；
                    # trace=None（未知工具/缺 query）表示压根没检索，不入。
                    if result.trace is not None:
                        evidence.append(result.trace)
                    # 收集成功且非空的结果
                    if ok and result_content and result_content != EMPTY_RESULT:
                        retrieved.append((name, result_content))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_content,
                })

        print(f"[AgentLoop] steps={len(steps)} tools={[s['tool'] for s in steps]}")
        return AgentLoopResult(
            messages=messages, steps=steps, degraded=False,
            retrieved=retrieved, evidence=evidence,
        )
