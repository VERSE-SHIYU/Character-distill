"""标准 MCP server：把 core/agent/tools.py 的三路检索工具暴露为 MCP 工具。

本文件只做协议适配层，不改动任何 core/ 实现：
- 工具 schema 直接透传 AgentToolkit.get_schemas()（JSON Schema，不重写）
- 工具执行仍走 AgentToolkit.execute()
- ContextEngine 由本进程独立构建（v1：rag/memory 未接线，llm 有 key 才挂）

面向官方 mcp SDK 的 1.x decorator API（@server.list_tools / @server.call_tool）。
注意：import core.context_engine 会连带 import core.rag → chromadb，因此本进程必须
跑在能 import core/ 的 Python 环境里（即装齐项目 requirements.txt 的环境）。

运行：
    python mcp_server/server.py      # stdio 传输，供 MCP 客户端 spawn
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sys
from pathlib import Path

# 让本进程能 import 仓库内的 core/ / adapters/
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from core.agent.tools import AgentToolkit
from core.context_engine import ContextEngine
from core.schema import CharacterCard

SERVER_NAME = "character-distill-tools"
SERVER_VERSION = "0.1.0"
DEFAULT_CARD_FILE = Path(__file__).resolve().parent / "example_card.json"


def _load_card() -> CharacterCard:
    card_file = os.getenv("MCP_CARD_FILE") or str(DEFAULT_CARD_FILE)
    with open(card_file, encoding="utf-8") as f:
        return CharacterCard(**json.load(f))


def _build_toolkit() -> AgentToolkit:
    card = _load_card()

    # LLM 仅当环境给了 DEEPSEEK_API_KEY 才挂（web_search 的角色过滤需要它）。
    # 懒加载：LLMAdapter 顶层 import openai/yaml/dotenv，缺 key 时不应因 import 失败。
    llm = None
    llm_model = ""
    if os.getenv("DEEPSEEK_API_KEY"):
        try:
            from adapters.llm_adapter import LLMAdapter

            llm = LLMAdapter()
            llm_model = llm._model
        except Exception as exc:  # noqa: BLE001
            print(f"[MCP] LLMAdapter init failed, running without llm: {exc}", file=sys.stderr)

    ctx = ContextEngine(
        card=card,
        rag=None,  # v1 未接线：search_scenes 返回空
        memory_manager=None,  # v1 未接线：search_memory 返回空
        card_id=os.getenv("MCP_CARD_ID") or card.name,
        llm=llm,
        model=llm_model,
    )
    return AgentToolkit(ctx, current_mood=os.getenv("MCP_MOOD"))


def _tool_specs(toolkit: AgentToolkit) -> list[types.Tool]:
    """透传 AgentToolkit.get_schemas()，OpenAI function 形状 → MCP Tool。"""
    specs = []
    for entry in toolkit.get_schemas():
        fn = entry["function"]
        specs.append(
            types.Tool(
                name=fn["name"],
                description=fn["description"],
                inputSchema=fn["parameters"],
            )
        )
    return specs


def _execute_silent(toolkit: AgentToolkit, name: str, args: dict):
    """stdio 通道独占 stdout——把 AgentToolkit/ContextEngine 的 print 日志转到 stderr。"""
    with contextlib.redirect_stdout(sys.stderr):
        return toolkit.execute(name, args)


def main() -> None:
    toolkit = _build_toolkit()
    tools = _tool_specs(toolkit)

    server = Server(SERVER_NAME, version=SERVER_VERSION)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return tools

    # execute 内部自带单 worker 线程 + per-tool 超时；MCP 侧再加锁保证一次一个请求，
    # 同时避免 redirect_stdout 改全局 sys.stdout 时的并发竞争。
    exec_lock = asyncio.Lock()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        args = dict(arguments or {})
        async with exec_lock:
            result = await asyncio.to_thread(_execute_silent, toolkit, name, args)
        return [types.TextContent(type="text", text=result.content)]

    async def _serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
