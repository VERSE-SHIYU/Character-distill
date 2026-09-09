# character-distill MCP server

把 `core/agent/tools.py` 的三个检索工具暴露为标准 [MCP](https://modelcontextprotocol.io) 工具，
**不绑定单卡**——每个调用按 `card_id` 路由到存储里那张已蒸馏角色卡的检索引擎：

| MCP 工具 | 额外必填参数 | 底层 | 作用 |
|---|---|---|---|
| `search_scenes` | `card_id` + `query` | `AgentToolkit._call_search_scenes` → `ContextEngine._retrieve_scenes` | 检索该角色原著场景 |
| `search_memory` | `card_id` + `query` | `AgentToolkit._call_search_memory` → `ContextEngine._retrieve_memories` | 检索该角色长期记忆 |
| `web_search` | `card_id` + `query` | `AgentToolkit._call_web_search` → `ContextEngine._search_web` | 现实信息搜索（DuckDuckGo + 角色过滤） |

**纯协议适配层**：工具 schema 来自 `AgentToolkit.get_schemas()`（JSON Schema），deepcopy 后
为每个工具注入必填的 `card_id`——绝不原位改 `get_schemas()` 的返回值（那是给 agent 模式 router
LLM 用的干净 schema）。执行仍走现有 `AgentToolkit.execute()`。**未改动 `core/` / `web/` /
`storage/` 任何文件。**

按 `card_id` 路由：`call_tool` 从 arguments pop 出 `card_id` → `_toolkit_for(card_id)`（进程内
缓存 card→toolkit、text_id→RAGEngine）→ 其余参数原样交给该卡的 `AgentToolkit.execute()`。
`card_id` 缺失/空/查无此卡/card_json 解析失败 → 抛错由 MCP SDK 转成协议 `isError`，绝不返回空结果。

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

进程不读任何角色卡文件；卡都从数据库（`storage` 后端，同 web 的 `STORAGE_BACKEND` 配置）按
`card_id` 现取现建。可选环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MCP_MOOD` | 空 | 传给工具检索的当前情绪 |
| `DEEPSEEK_API_KEY` | 无 | **可选**。有则挂 LLM，`web_search` 的角色过滤步才生效 |
| `DASHSCOPE_API_KEY` | 无 | **必需**（或用 `EMBEDDING_API_KEY`）。无用户会话时做 embedding 的兜底 key |
| `MCP_CARD_ID` | 空 | （已废弃，v1 单卡遗留） |

自测（真实 MCP stdio 往返，不 mock）：

```bash
python mcp_server/client_demo.py
# → 用例(a) 按 card_id 路由隔离（toolkit/rag 引擎/memory 隔离键断言，不依赖检索内容）
#   / happy path 真实卡检索非 isError / 用例(b) 缺 card_id 报 isError / 用例(c) 未知 card_id 报 isError
```

## spawn 环境变量（重要坑）

server 启动即读环境配置，缺了直接 fail-fast：`STORAGE_BACKEND`（storage 后端必填）+ 配套
`DB_PATH`(sqlite) / `DATABASE_URL`(postgres)，以及无用户会话时的 embedding 兜底 key
（`DASHSCOPE_API_KEY` / `EMBEDDING_API_KEY`）。这些必须存在于**启动 server 的进程环境**里：
Claude Desktop / Cline 是 `command` 直接拉起 `server.py`，继承的是客户端应用自己的环境，
需确认配置已在那里（或用带 `.env` 来源的 wrapper 再 launch）。

**用官方 mcp SDK 以 stdio 子进程 spawn server 的客户端**（如本仓 `client_demo.py`）另有一个坑：
mcp 1.x 的 stdio 子进程 env 只取 `get_default_environment()`（白名单，仅 PATH 等极少数），
**不继承父进程 env**——`StdioServerParameters(..., env=None)` 时 server 会报
`STORAGE_BACKEND 未设置`。必须显式透传：`StdioServerParameters(command=..., args=..., env=dict(os.environ))`。
真实桌面客户端不经过这条 SDK spawn 路径，不受此限。

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

工具现在要求 `card_id`，LLM 侧（Claude Desktop 等）由客户端把目标卡 id 填进参数即可。

## 行为边界（诚实说明）

- `search_scenes` / `search_memory` 的检索引擎与生产同源：场景按该卡 `text_id` 读
  `text_{text_id}` chroma 集合（文本蒸馏时已索引，MCP 只 `load_existing`，不突发重建）；
  记忆走 mem0（按 `card_id`）。查无匹配 → `未找到相关内容`（`EMPTY_RESULT`，工具对「没检索到」
  的设计响应，非报错）。
  - ⚠️ 数据依赖：chroma 集合须由**当前 embedder（DashScope text-embedding-v4 / 1024 维）**索引。
    早于 2026-06-24 embedder 迁移（原 SentenceTransformer 384 维）的旧集合仍能被
    `load_existing` 加载（它只看 count>0），但查询会因 384/1024 维度不符被吞成空——web 单卡
    chat 同样如此，非本 server 缺陷。这类旧文本需按当前 embedder 重建后才能查到场景。
  - ⚠️ 环境依赖：本仓库 chroma 数据由 Linux 容器写入；Windows 宿主 Python 直接读会段错误
    （duckdb 平台段不匹配），真实检索验证须跑在 Linux 容器里。
- `web_search`：走 DuckDuckGo Instant Answer 免费接口 + 角色 LLM 过滤。依赖：(1) 网络可达该接口
  且其返回内容（地域不同返回可能为空）；(2) `DEEPSEEK_API_KEY`（否则过滤步返回空）。
- 工具/`ContextEngine` 的 `print()` 日志被重定向到 stderr——stdio 传输独占 stdout，避免破坏 JSON-RPC 帧。
- 多卡调用串行（单 `exec_lock`）：既避免 `redirect_stdout` 改全局 `sys.stdout` 的并发竞争，也避免
  构建/执行并发。MCP 场景下吞吐足够。

## 仓库约束

- 依赖：只新增本目录 `requirements.txt`（`mcp`），未动主 `requirements.txt`。
- 代码：未改 `core/`、`web/`、`storage/` 任何文件。
