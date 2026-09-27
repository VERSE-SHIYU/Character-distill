"""ContextEngine 三路检索的 function-calling 工具封装。"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

from core import concurrency as C  # 派生与上下文传播（ctx_submit）
from core.embeddings import embed_deadline  # D2：库内 embed 的 deadline scope
from core.schema import EvidenceKind, SourceTrace

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # 仅类型标注用：避免 import 期把 chromadb 拖进工具模块
    from core.context_engine import RetrievalResult

EMPTY_RESULT = "未找到相关内容"

# D2：工具执行器给 embed 的预算 margin。外层 fut.result(timeout) 到期只是"放弃 future"，
# 杀不掉已启动的 handler 线程（Python 线程不可 kill）。scope 必须在 fut.result 到期前让
# 库内 embed 自行收手，否则 handler 线程残留到 embed 放弃（leak_probe 实测 +16.5s）。
# budget = timeout − margin：embed 截止先于弃船约 margin 秒，给 handler 从容 unwinding、
# 返回空结果（上层把 embed 异常吞成空检索）的窗口，fut.result 拿到的就不是 TimeoutError。
_EXECUTE_DEADLINE_MARGIN_S = 1.0


def _run_with_deadline(deadline: float | None, fn, arg):
    """在真正跑 handler 的线程内打开 embed deadline scope 再调 handler。

    `ctx_submit` 会把调用方的**整个** contextvar 上下文拷进 worker（C2b 起与开关无关，
    见 core/concurrency.py），_EMBED_DEADLINE 也在其中。仍然必须在 worker 这条线程上
    重新开 scope：拷过来的是调用方那一刻的值，而这里的 deadline 是本路径按自己的
    timeout 现算的预算 —— rebind 而非继承，才让这个预算说了算。mem0/chroma 的库内
    embed 在 worker 同线程读当前值，故 scope 必须开在这条线程上。

    调用点恒传实数（`timeout − margin`，见 execute），故上面那段继承在这里不发生。
    """
    with embed_deadline(deadline):
        return fn(arg)


@dataclass
class ToolResult:
    """一次工具调用的结果。

    ``trace`` 是证据侧出口：``None`` 表示**压根没发生检索**（未知工具 / 缺 query），
    而不是「检索了但没结果」—— 后者是 status="empty" 的 SourceTrace。两者不许混，
    本仓最大的盲点就是「没发生」被当成「发生了但空」。

    ``ok`` 由 ``trace`` 派生而非独立字段：写成字段就要与 ``trace.status`` 手工对齐，
    两个必须一致却无人强制的字段必然漂移。派生后不可能不一致。
    """
    content: str
    elapsed_ms: int
    trace: SourceTrace | None = None

    @property
    def ok(self) -> bool:
        # 与旧 ``bool(block)`` 逐路径等价：唯一 status="hit" 但块为空的路径是 web 二阶段
        # 改写失败（_web_items 给 body=""），那时 content 已是 EMPTY_RESULT。
        return (
            self.trace is not None
            and self.trace.status == "hit"
            and self.content != EMPTY_RESULT
        )


class _ToolEntry(NamedTuple):
    """工具分发表的一行：handler + 超时预算 + 该工具对应的来源词汇。

    ``source`` 在这里而非从工具名解析：超时/异常路径压根没有 RetrievalResult 可投影，
    得就地合成 SourceTrace，而合成需要知道来源。让工具名 → 来源的映射只此一份（本表），
    比在两条失败路径里各写一次好。
    """
    handler: Callable[[str], "RetrievalResult"]
    timeout: int
    source: EvidenceKind


class AgentToolkit:
    """把 ContextEngine 的三路检索注册为 OpenAI function-calling 工具。"""

    SCENE_TIMEOUT = 5
    MEMORY_TIMEOUT = 5
    WEB_TIMEOUT = 15

    def __init__(self, ctx_engine, current_mood: str | None = None) -> None:
        self._ctx = ctx_engine
        self.current_mood = current_mood

    def get_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_scenes",
                    "description": "检索角色原著中与当前话题相关的场景片段。当对话涉及角色的过去经历、原著情节、与其他角色的关系时调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词，用中文描述当前话题涉及的角色经历或情节。",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_memory",
                    "description": "检索你（角色）与对方过往交流的长期记忆。当对方提及之前聊过的事、共同经历、或你需要回忆对方的偏好/信息时调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词，用中文描述你想回忆的过往对话内容或对方信息。",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "搜索现实世界的实时信息。仅当对话明确需要角色不可能内在知晓的现实时事/事实时调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词，用中文描述你想查的现实信息。",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

    def _call_search_scenes(self, query: str) -> "RetrievalResult":
        return self._ctx._retrieve_scenes_ex(query)

    def _call_search_memory(self, query: str) -> "RetrievalResult":
        return self._ctx._retrieve_memories_ex(query, current_mood=self.current_mood)

    def _call_web_search(self, query: str) -> "RetrievalResult":
        return self._ctx._search_web_ex(query)

    def _finish(
        self,
        name: str,
        arguments: dict,
        started: float,
        trace: SourceTrace | None,
        content: str,
    ) -> ToolResult:
        """五条出口共用：算耗时、打日志、装结果。日志里的 ok= 读派生值，与旧输出一致。"""
        res = ToolResult(
            content=content,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            trace=trace,
        )
        print(f"[AgentTool] {name} args={arguments} ok={res.ok} {res.elapsed_ms}ms")
        return res

    def execute(self, name: str, arguments: dict) -> ToolResult:
        started = time.monotonic()

        dispatch = {
            "search_scenes": _ToolEntry(self._call_search_scenes, self.SCENE_TIMEOUT, "scene"),
            "search_memory": _ToolEntry(self._call_search_memory, self.MEMORY_TIMEOUT, "memory"),
            "web_search": _ToolEntry(self._call_web_search, self.WEB_TIMEOUT, "web"),
        }

        entry = dispatch.get(name)
        if entry is None:
            return self._finish(name, arguments, started, None, f"未知工具: {name}")

        query = arguments.get("query", "")
        if not query:
            return self._finish(name, arguments, started, None, "缺少 query 参数")

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            # context 传播点：submit 不拷贝 contextvar → 用 ctx_submit，
            # 让 handler 内检索/embed 子 span 挂到 execute_tool 下而非孤儿。
            # D2：_run_with_deadline 在 worker 内开 embed scope，预算 = timeout − margin，
            # 让库内 embed 在 fut.result(timeout) 弃船前自行收手（见模块注释）。
            budget_s = entry.timeout - _EXECUTE_DEADLINE_MARGIN_S
            deadline = time.monotonic() + budget_s if budget_s > 0 else None
            fut = C.ctx_submit(pool, _run_with_deadline, deadline, entry.handler, query)
            result = fut.result(timeout=entry.timeout)
        except TimeoutError:
            # 超时是**调用层**事实（弃船），不是检索本体说了什么 —— 故它是 SourceStatus
            # 的第四态，不进引擎的三态 RetrievalStatus（见 core/schema.py 的说明）。
            return self._finish(
                name, arguments, started,
                SourceTrace(source=entry.source, status="timeout", items=[]),
                f"工具执行超时（{entry.timeout}s）",
            )
        except Exception as exc:
            # 交给下游的只有「折进 SourceTrace 随 evidence 上屏」—— 上屏不算留痕（GlitchTip
            # 与告警都看不见）。此处补 WARNING：工具失败不致命（agent 拿到 failed 态继续），
            # 但「哪件工具、为什么失败」要能查。
            logger.warning("Agent tool %s failed: %s: %s", name, type(exc).__name__, exc,
                           exc_info=True)
            return self._finish(
                name, arguments, started,
                SourceTrace(source=entry.source, status="failed", items=[]),
                f"执行异常: {exc}",
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        return self._finish(
            name, arguments, started, result.trace(), result.block or EMPTY_RESULT
        )
