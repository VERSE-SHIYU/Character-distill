"""ContextEngine 三路检索的 function-calling 工具封装。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    tool: str
    ok: bool
    content: str
    elapsed_ms: int


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

    def _call_search_scenes(self, query: str) -> str:
        return self._ctx._retrieve_scenes(query)

    def _call_search_memory(self, query: str) -> str:
        return self._ctx._retrieve_memories(query, current_mood=self.current_mood)

    def _call_web_search(self, query: str) -> str:
        return self._ctx._search_web(query)

    def execute(self, name: str, arguments: dict) -> ToolResult:
        started = time.monotonic()

        dispatch = {
            "search_scenes": (self._call_search_scenes, self.SCENE_TIMEOUT),
            "search_memory": (self._call_search_memory, self.MEMORY_TIMEOUT),
            "web_search": (self._call_web_search, self.WEB_TIMEOUT),
        }

        entry = dispatch.get(name)
        if entry is None:
            elapsed = int((time.monotonic() - started) * 1000)
            print(f"[AgentTool] {name} args={arguments} ok=False {elapsed}ms")
            return ToolResult(
                tool=name,
                ok=False,
                content=f"未知工具: {name}",
                elapsed_ms=elapsed,
            )

        handler, timeout = entry
        query = arguments.get("query", "")
        if not query:
            elapsed = int((time.monotonic() - started) * 1000)
            print(f"[AgentTool] {name} args={arguments} ok=False {elapsed}ms")
            return ToolResult(
                tool=name,
                ok=False,
                content="缺少 query 参数",
                elapsed_ms=elapsed,
            )

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(handler, query)
            result = fut.result(timeout=timeout)
        except TimeoutError:
            elapsed = int((time.monotonic() - started) * 1000)
            print(f"[AgentTool] {name} args={arguments} ok=False {elapsed}ms")
            return ToolResult(
                tool=name,
                ok=False,
                content=f"工具执行超时（{timeout}s）",
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            print(f"[AgentTool] {name} args={arguments} ok=False {elapsed}ms")
            return ToolResult(
                tool=name,
                ok=False,
                content=f"执行异常: {exc}",
                elapsed_ms=elapsed,
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        elapsed = int((time.monotonic() - started) * 1000)
        ok = bool(result)
        print(f"[AgentTool] {name} args={arguments} ok={ok} {elapsed}ms")
        return ToolResult(
            tool=name,
            ok=ok,
            content=result or "未找到相关内容",
            elapsed_ms=elapsed,
        )
