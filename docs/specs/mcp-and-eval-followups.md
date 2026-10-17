# spec：MCP 调工具必报错（E10）+ 诊断脚本拿不到联网工具（E8 遗留）

基线：PR #170 head `c94405c6`（**先合并 #170 再开工**，本 spec 的行号以它为准）。放进 worktree 的 `docs/specs/`，用 `Test-Path` 确认；补充写回本文件。
依据：《阶段锚点核对失效：调查报告》E10 一行；#170 复核报告「遗留」两条。Shiyu 10-17：不记账，修掉。

## 解决什么、不解决什么、分几段

| 解决 | 不解决 |
|---|---|
| E10：MCP 按 card_id 调工具不再抛 `TypeError` | MCP 的其他行为（不挂长期记忆、不建集合）不变 |
| 两个诊断脚本：`diag_tool_use.py` 拿到全部 3 个工具；`run_agent_eval.py` 的 realtime 用例能调 `web_search` | `run_agent_eval.py:156` 的 `run_case` 没有调用方（`grep "run_case(" scripts/run_agent_eval.py` 只有定义那一行）——不删（第 37 条：不在目标内），只让它也走同一个构造 |

两个 PR，互不依赖：**PR-A（E10，MCP）**、**PR-B（诊断脚本）**。

## 调用方清单（第 11 条，全仓搜索原始输出）

```
$ grep -rn "project_card(" --include=*.py core web mcp_server
core/opening.py:80:    proj, _view = project_card(card, arc_phase)
core/chat_engine.py:166:        self.card, self.arc_view = project_card(card, arc_phase)
core/text_manager.py:524:                variation_prompt = build_variation_prompt(project_card(card, None)[0])
web/routers/market.py:858:    char = project_card(char, None)[0]
web/routers/distill.py:342:        prompt = build_awakening_prompt(project_card(card, None)[0])
```

`mcp_server/` 里一处都没有 —— MCP 是唯一不投影就建 `ContextEngine` 的地方。

```
$ grep -n "engine = ChatEngine(\|AgentToolkit(\|get_schemas" scripts/run_agent_eval.py scripts/diag_tool_use.py
scripts/run_agent_eval.py:169:    engine = ChatEngine(
scripts/run_agent_eval.py:331:    engine = ChatEngine(
scripts/run_agent_eval.py:372:    engine = ChatEngine(
scripts/run_agent_eval.py:400:        toolkit = AgentToolkit(engine._ctx_engine, current_mood=engine._mood)
scripts/diag_tool_use.py:72:    toolkit = AgentToolkit(engine._ctx_engine)
scripts/diag_tool_use.py:73:    schemas = toolkit.get_schemas()
```

## PR-A：MCP 建引擎前先投影（E10）

### 判断清单（基线 `c94405c6`）

| # | 判断 | 坐标与原文 |
|---|---|---|
| A1 | `ContextEngine` 只收投影卡，收原卡直接报错 | `core/context_engine.py:228` `self.card = require_projected(card)`；`:19` `from core.arc_view import ProjectedCard, phase_header, require_projected`；`core/arc_view.py:47` `def require_projected(card)`，`:52` `if not isinstance(card, ProjectedCard):` |
| A2 | MCP 把存储里解析出的**原卡**一路传给 `ContextEngine` | `mcp_server/server.py:168` `card = CharacterCard.model_validate_json(card_rec["card_json"])` → `:174` `return _make_toolkit(card, rag)` → `:122-123` `ctx = ContextEngine(card=card, …)` |
| A3 | `project_card(card, None)` = 最后阶段；无阶段卡 = 0 | `core/arc_view.py:78-81` `_normalize_k`：「有效 → 原样；否则 → n（最后阶段）；无阶段卡 → 0」 |
| A4 | 其他没有会话的地方都用 `project_card(card, None)[0]` | 上面「调用方清单」第一段：`text_manager.py:524`、`market.py:858`、`distill.py:342` |
| A5 | 现有 MCP 检查 6 为了绕开这个缺陷，自己先投影再传 | `tests/test_mcp_tool_contract.py:45` `card = project_card(make_card(), None)[0]` |

### 根因

MCP 建引擎时漏了投影这一步；仓库里没有走真实 MCP 路径的测试（检查 6 绕开了），所以一直没人发现。

### 目标检查（第 1 个提交就带上）

1. **走真实路径**：替身存储的 `get_card_unscoped` 返回一条 `{"card_json": <原卡 JSON>, "text_id": ""}`，`await _toolkit_for(card_id)` 不报错；拿到的 toolkit `execute("search_scenes", {"query": "x"})` 正常返回（`text_id` 为空 → `rag` 为 `None`，`server.py:143-144`）。
2. 有阶段的卡：MCP 引擎里的卡是**最后阶段**的投影（与 A4 那几处一致），例如阶段专属的字段取到最后阶段的值。
3. 检查 6 改成传**原卡**（`make_card()`，不再先投影），照常能联网 —— 生产里 `_make_toolkit` 收到的就是原卡。

### 改法

1. 投影只在 MCP 建引擎那一处：`_make_toolkit`（`server.py:113`）里 `ContextEngine(card=project_card(card, None)[0], …)`；docstring 写明「MCP 没有会话、不选阶段，按最后阶段，与苏醒台词 / 市场等无会话处同口径（A4）」。
2. `_build_toolkit_blocking` 与 `_toolkit_for` 不改。

### 结构自检

| 问题 | 答案 |
|---|---|
| MCP 投影写在几处 | 一处：`_make_toolkit` |
| 「无会话 = 最后阶段」是新规则吗 | 不是：沿用 `_normalize_k` 的缺省（A3），与 A4 同口径 |

## PR-B：诊断脚本跟着用途拿工具

### 判断清单（基线 `c94405c6`）

| # | 判断 | 坐标与原文 |
|---|---|---|
| B1 | `diag_tool_use.py` 只是把工具清单交给模型看它会不会调，不执行工具 | `scripts/diag_tool_use.py:72-73` 建 toolkit 只为 `get_schemas()`；`:81` `llm.chat_with_tools(A_SYSTEM, messages, schemas)`；之后用 `llm._client.chat.completions.create(..., tools=schemas)` |
| B2 | 全量清单已有不看开关的出口 | `core/agent/tools.py:265` `ALL_SCHEMAS = tuple(e.schema for e in AgentToolkit(None)._table())` |
| B3 | 评测集里有 realtime 用例，期望调 `web_search` | `tests/eval/agent_eval_cases.jsonl:31` `"realtime_01" … "expected_tools": ["web_search"]`（该文件共 10 行含 `web_search`） |
| B4 | 评测脚本里 3 处建 `ChatEngine`，参数只差 `memory_manager=None`，而它本来就是缺省值 | `scripts/run_agent_eval.py:169`、`:331`、`:372`；`core/chat_engine.py:136` `memory_manager=None,` |
| B5 | 老路径（`agent_mode = False`）也看开关 | `core/context_engine.py:332` `if self.web_search_enabled:` |

### 根因

两个脚本都直接用生产的 `ChatEngine`，开关默认关（`context_engine.py:235`）。#170 之前工具模式不看开关，所以评测能调到联网；#170 修好开关之后，评测的 realtime 用例就调不到了。诊断脚本要的是「全部工具」，却拿了「这次能用的工具」。

### 目标检查（按风险配，第 10 条：诊断脚本不进生产，不搭成套检查）

1. `run_agent_eval.py`：新增一条小测试，调脚本里那个构造函数（替身 LLM），断言 `_ctx_engine.web_search_enabled is True`。
2. `diag_tool_use.py`：不加测试。改动只是换成全量出口 `ALL_SCHEMAS`（它的内容已由 `tests/test_mcp_tool_contract.py` 守着）；交付时贴一条 `python -c "import ast,sys; ast.parse(open('scripts/diag_tool_use.py').read())"` 与 `grep -n "ALL_SCHEMAS\|AgentToolkit" scripts/diag_tool_use.py` 的原始输出即可。

### 改法

1. `diag_tool_use.py`：去掉 `:72` 的 toolkit，`schemas` 直接取 `ALL_SCHEMAS`（B2）；import 同步改。
2. `run_agent_eval.py`：3 处 `ChatEngine(...)` 合成一个本脚本内的构造函数（B4：参数实际相同），在里面设 `engine._ctx_engine.web_search_enabled = True`，并写一句为什么：「评测量的是工具调用决策，realtime 用例需要联网（B3）；老路径同样看开关（B5）」。
   - 设开关照路由的写法（`web/routers/chat.py:335` `engine._ctx_engine.web_search_enabled = web_search`），不为脚本给 `ChatEngine` 新加公开接口（不过度设计）。

### 结构自检

| 问题 | 答案 |
|---|---|
| 评测引擎的开关在几处设 | 一处：脚本内那个构造函数 |
| 诊断脚本还依赖开关吗 | 不依赖：只要 schema，用全量出口 |

## 测试

- PR-A 本地只跑：`tests/test_mcp_tool_contract.py`，加 `grep -rln "mcp_server" tests` 搜出来的文件：`tests/test_embeddings.py`、`tests/test_usage_accounting_lock.py`、`tests/test_rag_unusable.py`、`tests/test_storage_scope_lock.py`（`tests/census_llm_call_contexts.py` 是脚本，不是测试）。
- PR-B 本地只跑：新增的那条小测试，加 `grep -rln "run_agent_eval\|diag_tool_use" tests` 搜出来的 `tests/test_storage_scope_lock.py`（它登记了两个脚本的 `load_card`，`:76-78`）。
- 两段都加 `tests/test_mutation_anchors.py`。前端没改，不跑 `npm test`。库用 docker PG，合并门是分支 CI。
- **CI 绿了才报「可合并」**；CI 红又拿不到日志时，才在本地整套跑一遍复现。

## 对账表（独立复核方验，自补放宽、过严两个方向的变异）

| 行为 | 守它的测试 | 让它变红的变异 | 触发状态 |
|---|---|---|---|
| MCP 真实路径不报错 | A 检查 1 | 去掉 `_make_toolkit` 里的投影 | 替身存储给原卡 JSON |
| MCP 用最后阶段 | A 检查 2 | 投影到阶段 1 | 有阶段的卡 |
| 评测引擎开着联网 | B 检查 1 | 构造函数里删掉那一行 | 调构造函数 |

## 设计原则核对（Shiyu 10-17：不过度工业化、不打补丁、隔离抽象复用、高内聚低耦合）

| 原则 | 本 spec 怎么守 |
|---|---|
| 不打补丁 | 投影只加在 MCP 唯一建引擎处；评测 3 处构造合成一处再设开关，不是 3 处各加一行 |
| 复用 | 投影复用 `project_card(card, None)` 与其他无会话处同口径；诊断复用 #170 的 `ALL_SCHEMAS` |
| 低耦合 | 不给 `ChatEngine` 加新接口，脚本照路由已有写法设开关；不改 `core/` |
| 不过度工业化 | PR-A 是生产缺陷，配目标检查 + 对账表；PR-B 是诊断脚本，只加一条小测试，不建变异矩阵 |

## 技能

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 按行号读代码，跑上面两段 grep 核对 |
| 改动 | `@tdd` | 先写目标检查再改 |
| 交付 | `@verification-before-completion` | 贴受影响测试原始输出与 CI 结果 |

## 交付

两个 PR，英文标题、描述、commit。不写部署步骤。不调真模型、不花钱、不发真网络请求。
