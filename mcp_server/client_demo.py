"""MCP stdio 客户端自测：spawn server.py → initialize → 列工具 → 逐个调用。

纯协议级验证，不 mock：
  python mcp_server/client_demo.py
打印每个工具的 schema 名 + 一次真实 call_tool 往返结果。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PY = Path(__file__).resolve().parent / "server.py"


def _content_texts(res) -> list[str]:
    out = []
    for c in getattr(res, "content", []) or []:
        if getattr(c, "type", "") == "text":
            out.append(getattr(c, "text"))
    return out


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER_PY)], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"[init] server={init.serverInfo.name} v{init.serverInfo.version} "
                  f"protocol={init.protocolVersion}")

            listed = await session.list_tools()
            tools = getattr(listed, "tools", [])
            print(f"[list_tools] {len(tools)} tools")
            for t in tools:
                schema = getattr(t, "inputSchema", {}) or {}
                req = sorted(schema.get("required") or [])
                props = ", ".join(schema.get("properties", {}).keys())
                print(f"  - {t.name}: required={req} props=[{props}]")

            for tool, query in [
                ("search_scenes", "角色在藏书楼里的旧事"),
                ("search_memory", "上次聊过的一本书"),
                ("web_search", "北京今日天气"),
            ]:
                print(f"\n[call] {tool} query={json.dumps(query, ensure_ascii=False)}")
                res = await session.call_tool(tool, {"query": query})
                print(f"  -> isError={getattr(res, 'isError', False)} "
                      f"content={json.dumps(_content_texts(res), ensure_ascii=False)}")

            print("\n[call] no_such_tool（应报未知工具文本）")
            res = await session.call_tool("no_such_tool", {"query": "x"})
            print(f"  -> isError={getattr(res, 'isError', False)} "
                  f"content={json.dumps(_content_texts(res), ensure_ascii=False)}")


if __name__ == "__main__":
    asyncio.run(main())
