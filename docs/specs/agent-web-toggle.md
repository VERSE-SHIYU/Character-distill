# spec：关掉「联网」后，工具模式不再联网搜索（E8）

基线：main `443e952c`（原稿写 `443e9520`，短 sha 抄错一位；行号已按此提交核过）。
放进 worktree 的 `docs/specs/`，用 `Test-Path` 确认；补充写回本文件。
依据：《阶段锚点核对失效：调查报告》「接下来要查什么」E8 一行。

## 解决什么、不解决什么

| 解决 | 不解决 |
|---|---|
| 用户没开「联网」时，工具模式下模型看不到也调不到 `web_search` | 路由提示词里「需要现实世界实时或事实信息→web_search」那句不改（第 37 条：不在目标内）：工具不在清单里，模型调不到；真调了走已有的「未知工具」出口 |
| MCP server 与两个诊断脚本的行为**不变**（见「调用方」） | 诊断脚本要不要跟着开关走：不改，记账 |
| 工具清单与执行分发合成一张表，以后加工具只改一处 | 工具循环最多判断 3 次的问题（E9）：等日志数据（第 28 条：没有证据不推荐） |

一个 PR。

## 影响面

前端默认「联网」关、工具模式开（`web/frontend/src/store/useAppStore.js:547` `webSearchEnabled: false`；`:550` `agentMode: true`）。所以**默认设置下**的每个用户，只要路由判断要查现实信息，就会联网搜索（DuckDuckGo 请求 + 一次「角色知识过滤」的模型调用，`core/context_engine.py:586`、`:598-612`），与用户的开关相反。

## 调用方（第 11 条：先列已有的使用者，逐个核对会不会被改变）

`AgentToolkit` 不只聊天在用（`grep -rn AgentToolkit` 全仓）：

| 调用方 | 怎么用 | 它的 `ContextEngine` 开关 | 本 PR 后要求 |
|---|---|---|---|
| 聊天 `core/chat_engine.py:390` | 每轮新建 `AgentToolkit(self._ctx_engine, …)` | 由路由按用户开关写（J1） | 随开关 |
| MCP `mcp_server/server.py:197` | `AgentToolkit(ctx_engine=None).get_schemas()` 列**静态** 3 个工具（`:4-5` 注释「list_tools 静态暴露 3 个工具」） | 无（`None`） | 仍列 3 个，**不能因 ctx 为 None 报错** |
| MCP `mcp_server/server.py:122-130` | 按卡建 `ContextEngine` + `AgentToolkit` 执行 | 从不设置 → 默认关（`context_engine.py:235`） | `web_search` 仍可执行（MCP 没有用户开关，行为不变） |
| 诊断 `scripts/diag_tool_use.py:72-73`、`scripts/run_agent_eval.py:400` | 从引擎取 toolkit | 默认关 | 跟着引擎开关走（会少 `web_search`）；不改脚本，记账 |

**仓库里没有任何 MCP 的测试**（`grep -rn "_tool_specs\|list_tools\|_make_toolkit" tests` 无结果）：MCP 被改坏，CI 不会红。所以本 PR 要补 MCP 两条检查（见目标检查 5、6）。

## 判断清单（基线 `443e9520`）

| # | 判断 | 坐标与原文 |
|---|---|---|
| J1 | 开关由路由写到上下文引擎上 | `web/routers/chat.py:335`、`:458` `engine._ctx_engine.web_search_enabled = web_search`；默认 `core/context_engine.py:235` `self.web_search_enabled = False` |
| J2 | 老路径（非工具模式）看开关 | `core/context_engine.py:332` `if self.web_search_enabled:` |
| J3 | 工具清单无条件列出 `web_search` | `core/agent/tools.py:94` `def get_schemas` 返回三个工具，第三个 `"name": "web_search"` |
| J4 | 执行分发是**另一张**表，也无条件含 `web_search`；不在表里的名字走「未知工具」 | `core/agent/tools.py:181` `"web_search": _ToolEntry(self._call_web_search, self.WEB_TIMEOUT, "web")`；`:185-186` `if entry is None:` … `f"未知工具: {name}"` |
| J5 | `_ToolEntry` 已是「工具名 → handler、超时、来源」的唯一映射 | `core/agent/tools.py:71-79` docstring「让工具名 → 来源的映射只此一份（本表）」 |
| J6 | 工具测试用的是真 `ContextEngine`（开关默认关） | `tests/evidence_fakes.py:272` `def build_ctx(...) -> ContextEngine`；`tests/test_agent_evidence.py:312` `AgentToolkit(ctx).execute("web_search", ...)` |

## 根因

工具有两张表：`get_schemas` 列给模型看，`execute` 里的 `dispatch` 管执行。两张表各写一份工具名，都没有「这个工具现在可不可用」的概念。开关只有老路径读。

未核实：工具模式不看开关是不是当初有意为之。`docs/specs/` 里没找到相关决定；本地是浅克隆，`git log -S` 查不到 `tools.py` 最初引入 `web_search` 的提交。接口注释只写了默认走工具编排（`web/routers/chat.py:242-244`），没说工具模式忽略开关；前端菜单里有这个开关（`web/frontend/src/components/ChatArea.jsx:627`）。按「开关应生效」处理。

## 方案取舍（技术题，我定；对照返工经验第 25、26、29 条）

| 方案 | 能否达成目标 | 取舍 |
|---|---|---|
| A. 在 `get_schemas` 和 `execute` 里各加一句 `if 开关` | 能 | 「加一个分支、逐个补」：工具名仍在两张表各写一份，判断也写两处（第 25 条），不取 |
| B. 两张表合成一张，表里每行带「现在可不可用」（**选这个**） | 能 | 工具名、可用性各一处；以后加工具改一处（第 26 条） |
| C. 把判断放进 `ContextEngine._search_web_ex` | 不能完全达成 | 工具仍在清单里，模型照样会调，只是拿到空结果，白白多一次决策调用 |

B 的落点：「全部工具」与「这次能用的工具」分开。全部工具是一张静态表（MCP 静态列表用它，不需要 ctx）；这次能用的 = 表里按 `ctx.web_search_enabled` 过滤。MCP 在自己建引擎处把 `web_search_enabled` 设为开（`mcp_server/server.py:122-130`，一行；MCP 无用户开关，等于保持现状）。

## 目标检查（第 1 个提交就带上）

用真 `ContextEngine`（`build_ctx`）：

1. `web_search_enabled = False`：`get_schemas()` 里没有 `web_search`；`execute("web_search", {"query": "x"})` 走「未知工具」，不发任何网络请求（用 `fake_ddg` 记调用次数为 0）。
2. `web_search_enabled = True`：清单里有 `web_search`，执行照常（现有 `test_agent_evidence.py` 的 web 用例在开关打开后照过）。
3. 工具模式整轮（照现有 `tests/test_agent_evidence.py:539` 的 `AgentLoop(ToolLLM(steps), AgentToolkit(ctx))` 写法）：开关关、路由替身请求 `web_search` → `retrieved` 里没有 `web_search` 的结果（`ChatEngine` 拼「[联网]」块只读它，`core/chat_engine.py:402-410`）；`fake_ddg`（`tests/evidence_fakes.py:207`）记的网络调用为 0。
4. 开关在两轮之间切换：同一个 `AgentToolkit` 构造后再改开关，清单跟着变（清单每次现读开关，不在构造时定死）。
5. MCP 静态列表：`mcp_server.server._tool_specs()` 仍返回 3 个工具、每个带必填 `card_id`，不报错。
6. MCP 执行：用 `_make_toolkit(card, rag=None)` 建 toolkit，`execute("web_search", …)` 照常检索（`fake_ddg` 记到 1 次调用），不是「未知工具」。搭法：monkeypatch `mcp_server.server._get_storage` 返回替身（它默认连真库，`server.py:67-75`）；不设 `DEEPSEEK_API_KEY` → `_lazy_llm` 返回 `None`（`server.py:101-102`）→ 过滤那步跳过（`core/context_engine.py:595-596` `if not self._llm: return items, ""`）。

## 改法

1. `AgentToolkit` 里把两张表合成一张：每个工具一行，含名字、给模型的 schema、handler、超时、来源，以及「现在可不可用」（`web_search` 读 `self._ctx.web_search_enabled`，另两个恒可用）。建议在现有的 `_ToolEntry`（J5）上扩字段，不另起类型；具体形态按实际代码定。
2. `get_schemas()` 与 `execute()` 都从这一张表取「当前可用的工具」：前者给 schema，后者查 handler。不可用的工具对 `execute` 来说就是不存在 → 已有的「未知工具」出口。
3. 「全部工具」的 schema 另有一个不需要 ctx 的出口，MCP 静态列表（`server.py:197`）改用它。
4. MCP 建引擎处设 `web_search_enabled = True`（见方案 B 的落点）。
5. 不改 `ContextEngine`、不改路由、不改 `AgentLoop`、不改诊断脚本。

## 结构自检

| 问题 | 答案 |
|---|---|
| 工具名写了几处 | 工具侧一处（合并后的表）。另有 `core/chat_engine.py:402-406` 的显示标签表也按工具名写了一份，不在本 PR 目标内，不改、已记入调查报告 |
| 「联网可不可用」判在几处 | 工具侧一处（表里那一行）；老路径 J2 原样。两处读的是同一个开关 |
| 调用方要知道开关吗 | 聊天、`AgentLoop` 不要；MCP 要明确声明自己开着联网（它没有用户开关，这是它的事实） |
| 以后加一个工具改几处 | 工具侧一处；要显示中文标签还得改 `chat_engine.py:402-406`（同上，已记账） |

## 测试

- 本地只跑：`tests/test_agent_loop.py`、`tests/test_agent_evidence.py`、`tests/test_agent_mode_default.py`、`tests/test_mutation_anchors.py`，加新增的 MCP 检查所在文件。前端没改，不跑 `npm test`。合并门是分支 CI。
- MCP 检查要 import `mcp_server/server.py`：`mcp` SDK 在开发依赖里（`requirements-dev.in:14` `mcp==1.26.0`），CI 装的是开发依赖（`.github/workflows/build.yml:86` `pip install … -r requirements-dev.txt`）。
- 替身照生产：`build_ctx` 出来的真 `ContextEngine` 开关默认关，现有 web 用例要显式打开开关；`FakeCtxEngine`（`tests/test_agent_loop.py:109`）补上 `web_search_enabled` 属性，默认与生产一致（关）。
- 新增：目标检查 4 条。

## 对账表（独立复核方验，自补放宽、过严两个方向的变异）

| 行为 | 守它的测试 | 让它变红的变异 | 触发状态 |
|---|---|---|---|
| 关时清单无 web | 目标检查 1 | 清单不按开关过滤 | 开关关 |
| 关时执行不联网 | 目标检查 1 | 只过滤清单、执行仍按全表分发 | 开关关、直接 execute |
| 开时照常 | 目标检查 2 | 恒过滤掉 web | 开关开 |
| 开关现读 | 目标检查 4 | 构造时缓存清单 | 构造后改开关 |
| 整轮不带联网块 | 目标检查 3 | 可用性只在 `get_schemas` 判、模型硬调 `web_search` 照样执行 | 工具模式整轮 |
| MCP 静态列表不变 | 目标检查 5 | 静态列表也按开关过滤 / ctx 为 None 时报错 | `ctx_engine=None` |
| MCP 仍能联网 | 目标检查 6 | 删掉 MCP 建引擎处那一行 | MCP 执行 `web_search` |

## 技能

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 按行号读代码 |
| 改动 | `@tdd` | 先写目标检查再改 |
| 交付 | `@verification-before-completion` | 贴受影响测试原始输出 |

## 交付

一个 PR，英文标题、描述、commit。不写部署步骤。不调真模型、不花钱、不发真网络请求。

## 实施记录（落线时补，只记事实）

- 改动：`core/agent/tools.py`（两表合一 + `ALL_SCHEMAS` / `_available()`）、
  `mcp_server/server.py`（静态列表改走 `ALL_SCHEMAS`；建引擎处一行 `web_search_enabled = True`）、
  `tests/evidence_fakes.py`（`build_ctx` 加 `web=` 开关，缺省关）、
  `tests/test_agent_evidence.py`（7 处 web 用例显式开）、
  `tests/test_agent_loop.py`（`FakeCtxEngine` 补 `web_search_enabled = False`；超时用例显式开）。
- 新增：`tests/test_agent_web_toggle.py`（目标检查 1-4，4 条）、
  `tests/test_mcp_tool_contract.py`（目标检查 5-6，2 条）。MCP 单独一个文件：`mcp` SDK
  装不上时 1-4 照样能收集。
- 变异自测（各打红它该打红的锁，改完文件逐字节还原）：清单不按开关过滤 / 只过滤清单而
  执行仍按全表分发 → 检查 1、3；恒过滤掉 web → 检查 2；构造时缓存清单 → 检查 4；
  MCP 静态列表改走开关过滤（ctx 为 None）→ 检查 5；删掉 MCP 建引擎处那一行 → 检查 6。
- 结构自检补正：「以后加一个工具改几处」= **3 处**（schema 常量、`_table()` 一行、
  `ALL_SCHEMAS` 一行），不是原稿的 1 处。工具**名**仍只写在 schema 的 `function.name`
  一处（`_ToolEntry.name` 现读），重复的只是「有哪几行」。原因：handler 是绑定方法、
  超时是类属性（`test_agent_loop` 会 monkeypatch `AgentToolkit.WEB_TIMEOUT`），工具表
  只能实例级构造，而 ctx-free 出口拿不到实例。
- 面外既存缺陷（只报不改，另行记账）：`mcp_server/server.py:122` 把 `CharacterCard`
  直接喂给只收 `ProjectedCard` 的 `ContextEngine`（`core/arc_view.py:47`）→ MCP 生产
  路径必抛 `TypeError`。故检查 6 传投影卡。
- 未跑 `npm test`（前端没改）。
