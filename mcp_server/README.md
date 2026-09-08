# character-distill MCP server

把 `core/agent/tools.py` 的三个检索工具暴露为标准 [MCP](https://modelcontextprotocol.io) 工具：

| MCP 工具 | 底层 | 作用 |
|---|---|---|
| `search_scenes` | `AgentToolkit._call_search_scenes` → `ContextEngine._retrieve_scenes` | 检索角色原著场景 |
| `search_memory` | `AgentToolkit._call_search_memory` → `ContextEngine._retrieve_memories` | 检索角色长期记忆 |
| `web_search` | `AgentToolkit._call_web_search` → `ContextEngine._search_web` | 现实信息搜索（DuckDuckGo + 角色过滤） |

**纯协议适配层**：工具 schema 直接透传 `AgentToolkit.get_schemas()`（JSON Schema，未重写）；
执行仍走现有 `AgentToolkit.execute()`。**未改动 `core/` / `web/` / `storage/` 任何文件。**

## 运行环境（重要）

本 server `import core.context_engine`，它连带 `import core.rag → chromadb`。因此**不能**跑在一个
只装了 mcp 的薄 venv 里，必须跑在**能 import `core/` 的 Python 环境**——也就是装齐了项目根
`requirements.txt`（chromadb 等）的那个环境。MCP 相关依赖只新增一个，单独放在本目录：

```bash
# 在你的项目 Python 环境里执行（该环境需已装项目根 requirements.txt）
pip install -r mcp_server/requirements.txt
```

> `requirements.txt` 里 `mcp` 锁在 `>=1.9.0,<2`：本 server 用的是官方 SDK 1.x 的
> `@server.list_tools()` / `@server.call_tool()` decorator API；2.x 改成构造器回调式，不兼容。

## 启动

```bash
python mcp_server/server.py     # stdio 传输，等待 MCP 客户端 spawn
```

可用的环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MCP_CARD_FILE` | `mcp_server/example_card.json` | 角色卡 JSON 路径（`CharacterCard` schema） |
| `MCP_CARD_ID` | 卡名 | 角色 id（检索 memory 时用） |
| `MCP_MOOD` | 空 | 传给工具检索的当前情绪 |
| `DEEPSEEK_API_KEY` | 无 | **可选**。有则挂 LLM，`web_search` 的角色过滤步才生效 |

自测（真实 MCP stdio 往返，不 mock）：

```bash
python mcp_server/client_demo.py
# → initialize / list_tools(3) / 逐个 call_tool 各返回一次结构化结果 / 未知工具拒绝
```

## 客户端配置

命令行要用**装齐依赖的那个 python 的绝对路径**（下例以类 Unix 示意；Windows 用 `.venv\Scripts\python.exe`）。

**Claude Desktop** — `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "character-distill-tools": {
      "command": "/path/to/your/python",
      "args": ["/path/to/Character-distill/mcp_server/server.py"]
    }
  }
}
```

**Cline** — `mcpServers`：

```json
{
  "mcpServers": {
    "character-distill-tools": {
      "command": "/path/to/your/python",
      "args": ["/path/to/Character-distill/mcp_server/server.py"]
    }
  }
}
```

## v1 行为边界（诚实说明）

- `search_scenes` / `search_memory`：适配器构建的 `ContextEngine` **未接线 rag/memory**
  （`rag=None, memory_manager=None`），因此任何 query 都返回 `未找到相关内容`（`EMPTY_RESULT`，
  工具对「没检索到」的设计响应，非报错）。要在真实数据上跑通这两个工具，需要把适配器的
  ContextEngine 指向应用实际的 chroma 检索库 / mem0 记忆——这依赖应用运行期基础设施，超出本
  v1「纯协议适配」范围，故未在 server 内造轮子。
- `web_search`：走 DuckDuckGo Instant Answer 免费接口 + 角色 LLM 过滤。依赖：(1) 网络可达该接口
  且其返回内容（地域不同返回可能为空）；(2) `DEEPSEEK_API_KEY`（否则过滤步返回空）。
- 工具/`ContextEngine` 的 `print()` 日志被重定向到 stderr——stdio 传输独占 stdout，避免破坏 JSON-RPC 帧。

## 仓库约束

- 依赖：只新增本目录 `requirements.txt`（`mcp`），未动主 `requirements.txt`。
- 代码：未改 `core/`、`web/`、`storage/` 任何文件。
