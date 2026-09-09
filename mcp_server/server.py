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


# ── 进程级缓存 ─────────────────────────────────────────────
# card_id → AgentToolkit；text_id → RAGEngine（照抄 group.py text_rag_cache 口径，
# RAG 是 per-text_id 而非 per-card：同文本多卡共享一个引擎，靠 card.name 过滤角色片段）。
# 多卡仍串行（exec_lock 串行化构建/执行），缓存更新无并发竞争，无需额外加锁。
_toolkit_by_card_id: dict[str, AgentToolkit] = {}
_rag_by_text_id: dict[str, "Any"] = {}
_storage: "Any" = None  # storage.get_store() 单例，首次在 serve 事件循环内惰性创建


class _NoCardError(Exception):
    """card_id 缺失 / 查无此卡 / card_json 解析失败 —— 协议层转 isError，禁止吞成空结果。"""


@contextlib.contextmanager
def _stdout_to_stderr():
    """把构建/执行产生的 print 日志转到 stderr —— stdio 通道独占 stdout（mcp 1.x 直写 buffer）。"""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _get_storage():
    """惰性 storage 单例。get_store() 会打 SQLite 迁移日志 → 撕 stdio 帧，故创建时转 stderr。"""
    global _storage
    if _storage is None:
        with _stdout_to_stderr():
            from storage import get_store

            _storage = get_store()
    return _storage


def _embed_rag_config() -> dict:
    """生产口径 RAG 配置（web.deps.get_rag_config，与 web 同一份 config.yaml，避免配置漂移）。

    生产 embedding key 是 per-user（会话注入）；MCP 无用户会话，config 缺 embedding_key 时
    用环境里的服务级 DashScope key 兜底。仍无 key → RAGEngine 构造抛清晰错误（不静默空检索）。
    """
    from web.deps import get_rag_config

    cfg = get_rag_config()
    if not cfg.get("embedding_key"):
        key = os.getenv("EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if key:
            cfg["embedding_key"] = key
            cfg["embedding_region"] = os.getenv("EMBEDDING_REGION", "cn")
    return cfg


def _memory_manager():
    """memory 全局单例 —— 与生产同源 web.deps.get_memory_manager()。"""
    from web.deps import get_memory_manager

    return get_memory_manager()


def _lazy_llm():
    """LLM 仅当环境给了 DEEPSEEK_API_KEY 才挂（web_search 的角色过滤需要它）。
    懒加载：LLMAdapter 顶层 import openai/yaml/dotenv，缺 key 时不应因 import 失败。"""
    if not os.getenv("DEEPSEEK_API_KEY"):
        return None, ""
    try:
        from adapters.llm_adapter import LLMAdapter

        llm = LLMAdapter()
        return llm, llm._model
    except Exception as exc:  # noqa: BLE001
        print(f"[MCP] LLMAdapter init failed, running without llm: {exc}", file=sys.stderr)
        return None, ""


def _make_toolkit(card, rag, card_id: str):
    """按 CharacterCard 构建 ContextEngine + AgentToolkit。构建会 print（ContextEngine
    预算、RAG），调用方需自行包 _stdout_to_stderr。"""
    from core.agent.tools import AgentToolkit
    from core.context_engine import ContextEngine

    llm, llm_model = _lazy_llm()
    ctx = ContextEngine(
        card=card,
        rag=rag,
        memory_manager=_memory_manager(),
        card_id=card_id,
        llm=llm,
        model=llm_model,
    )
    return AgentToolkit(ctx, current_mood=os.getenv("MCP_MOOD"))


def _rag_for_text_id(text_id: str, text_content: str | None):
    """per-text_id 取/建 RAGEngine（照抄 group.py:129-139）。text_id 空 → None。

    只读 load_existing(f"text_{text_id}")，不主动建集合——文本在蒸馏时就已索引，
    与生产一致：未索引文本 scenes 检索为空，而非 MCP 调用时突发重建烧 embed。
    """
    if not text_id:
        return None
    rag = _rag_by_text_id.get(text_id)
    if rag is not None:
        return rag
    from core.rag import RAGEngine

    rag = RAGEngine(_embed_rag_config())
    try:
        rag.load_existing(f"text_{text_id}")
    except Exception as exc:  # noqa: BLE001 —— 照抄 group.py：load 真抛错才回退重建
        if text_content:
            rag.index(text_content, collection_name=f"text_{text_id}")
        else:
            print(f"[MCP] text_{text_id} 无正文且 load_existing 失败：{exc}", file=sys.stderr)
    _rag_by_text_id[text_id] = rag
    return rag


def _build_toolkit_blocking(card_rec: dict, text_content: str | None, card_id: str):
    """同步构建 worker（chroma load_existing / ContextEngine 构建会 print → 转 stderr）。"""
    with _stdout_to_stderr():
        from core.schema import CharacterCard

        try:
            card = CharacterCard.model_validate_json(card_rec["card_json"])
        except Exception as exc:  # noqa: BLE001
            raise _NoCardError(f"card_id={card_id!r} card_json 解析失败：{exc}") from exc

        text_id = (card_rec.get("text_id") or "").strip()
        rag = _rag_for_text_id(text_id, text_content) if text_id else None
        return _make_toolkit(card, rag, card_id)


async def _toolkit_for(card_id: str):
    """按 card_id 从存储取卡构建 toolkit（进程内缓存）。卡不存在 / 解析失败 → _NoCardError。"""
    cached = _toolkit_by_card_id.get(card_id)
    if cached is not None:
        return cached

    card_rec = await _get_storage().get_card(card_id)
    if not card_rec:
        raise _NoCardError(f"card not found：{card_id!r}")

    text_id = (card_rec.get("text_id") or "").strip()
    text_content = None
    if text_id and text_id not in _rag_by_text_id:
        text_rec = await _get_storage().get_text(text_id)
        if text_rec:
            text_content = text_rec.get("content")

    toolkit = await asyncio.to_thread(_build_toolkit_blocking, card_rec, text_content, card_id)
    _toolkit_by_card_id[card_id] = toolkit
    return toolkit


def _build_toolkit() -> AgentToolkit:
    """遗留单卡路径（env MCP_CARD_FILE / example_card.json）。本步保留作 fallback，
    步骤 2 切到 call_tool 按 card_id 路由后删除。文件卡无 text_id → rag=None。"""
    card = _load_card()
    return _make_toolkit(card, rag=None, card_id=os.getenv("MCP_CARD_ID") or card.name)


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
