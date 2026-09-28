# mem0 的 LLM 改走项目适配器 + 默认模型只定义一处（2026-09-28，Shiyu 已拍板方案 A）

> 放到 worktree 的 `docs/specs/mem0-llm-via-adapter.md`。以后的补充写进本文件。
> **执行方**：`distill-mainline` 窗口，从 origin/main 新开分支 `fix/mem0-llm-via-adapter`（`--unset-upstream`）。**执行方 skill**：`@search-first @tdd`。
> 与 WP17 互不相干，可并行；须在部署生产（演示账号上线）之前合入。

## 目标
让聊天记忆（mem0）重新可用：mem0 提炼记忆的 LLM 调用改走项目自己的 `LLMAdapter`（关闭思考、读配置里的模型名），失败时让人看得见；项目默认模型名只在一处定义。

## 问题（证据）
- `core/memory_manager.py:186` 把 mem0 的模型写死为 `deepseek-chat`。DeepSeek 官方公告（V4 发布，news260424）：`deepseek-chat` / `deepseek-reasoner` 于 **2026-07-24 15:59 UTC 完全停用、无法访问**；已有开源项目报告此后用这两个名字的请求直接失败。
- mem0 写入失败只记 WARNING（`:334`），不进 GlitchTip 的问题列表；SZ、SG 近 48 小时无真实聊天请求，日志里没有样本。
- 项目默认模型名写死在多处，且与 `config.example.yaml`（`deepseek-flash`）不一致（见约束 5）。

## 已查实的约束（基线 main `ae89f7d`；执行方 S0 逐条复核，不成立即停）
1. mem0 配置：`core/memory_manager.py:174-203`；LLM 部分 `provider: openai`（`:184`）、`model: "deepseek-chat"`（`:186`）、`api_key` 取自全局环境变量 `DEEPSEEK_API_KEY`（`:156`）；向量部分用 DashScope `text-embedding-v4`（`:198` 起），**不受影响、不改**；`Memory.from_config`（`:204`）；初始化失败记 ERROR（`:216`）；`add` 在 `:317`，调用 mem0 在 `:331`，失败记 WARNING（`:334`）；`add_manual` 失败同为 WARNING（`:367`）。
2. mem0 版本：仓内锁定 `mem0ai==2.0.20`（`requirements.txt:166`）。PyPI 最新为 2.2.1（2026-09-25）。本仓用的是 mem0 的 **openai 提供方**（`mem0/llms/openai.py:85-152`），`deepseek-chat` 是**我们自己写死在 `:186` 的**；openai 与 deepseek 两个提供方都**没有传 `extra_body` 的通道**（mem0 自带 DeepSeek 提供方默认也是 `deepseek-chat`，`mem0/llms/deepseek.py:37`，仅作背景）；每轮 `add` 恰好 1 次 LLM 调用（`:956`；`:2014` 的程序性记忆只在 `agent_id` + PROCEDURAL 时进入，我们不传；视觉解析需 `enable_vision`，我们没开）；提供方白名单写死在 `mem0/llms/configs.py` 的 `validate_config`；工厂有 `LlmFactory.register_provider`（`mem0/utils/factory.py:128`，2.2.1）；`add` 对 LLM 的调用是**一次** `generate_response(messages=[system, user], response_format={"type": "json_object"})`（2.0.20 `mem0/memory/main.py:956`），不带 tools。
3. DeepSeek 官方「思考模式」页（2026-09-28 核对）：思考默认开启；OpenAI 格式下关闭思考**只能**用 `extra_body={"thinking": {"type": "disabled"}}`，`reasoning_effort` 只调强度、关不掉。官方「模型 & 价格」页：`deepseek-flash` 支持 JSON Output 与 Tool Calls。
4. 项目适配器：`LLMAdapter.chat(system_prompt, messages, max_tokens)`（`adapters/llm_adapter.py:769`），**不支持 `response_format`**；关闭思考由 `_request_options`（`:748`）统一注入；出站守卫 `check_outbound_guard`（`:315`）→ 生产注册的 `geo_call_guard`（`web/llm_gate.py:53`），**拿不到调用方身份时 fail-closed 抛 `LLMCallerMissing`（`web/llm_gate.py:65-70`）**，报错原文要求「请求之外的调用请显式声明 `core.request_context.system_llm_context()`」；SYSTEM 调用方无条件放行（`:71`）。现成的 SYSTEM 身份写法：`core/request_context.py:54` 的 `system_llm_context()`。
5. 默认模型写死处：`adapters/llm_adapter.py:675`（`llm_cfg.get("model", "deepseek-v4-pro")`）、`web/deps.py:87`（用户自带 key 未填模型）、`web/routers/auth.py:608`（返回给前端的当前模型）、`storage/postgres_store.py:2554` 与 `storage/sqlite_store.py:3194`（`row[2] or "deepseek-v4-pro"`）、迁移里的列默认值 `storage/migrations_pg/001_init.sql:98`、`storage/migrations/018_user_api_config.sql:3`。`config.example.yaml:17` 已是 `model: deepseek-flash`。
6. `config.example.yaml:13-16` 的注释写「2026-09-14 12:00 起 `deepseek-v4-pro` 的请求已被全部路由到 V4.1-Flash 并按 Flash 单价计费」——与官方更新日志不符（官方 2026-09-10：V4-Pro 在 9 月 14 日后**继续提供**、计费不变）。
7. mem0 同步版 `Memory` 在 `__init__` 里设置 `self.llm = LlmFactory.create(...)`（2.0.20 `mem0/memory/main.py:499`），之后 `add`（`:956`）等都通过 `self.llm.generate_response` 调用；`:2184` 的重建属于 `AsyncMemory`，我们不用。`core/memory_manager.py` 只调 `add / search / get_all / update / delete / delete_all`，都不重建 `llm`。
8. 适配器的配置读取目前内联在 `LLMAdapter.__init__`（`adapters/llm_adapter.py:657-688`）；`web/deps.py:461` 另有 `get_config()`，但 `adapters` 不能依赖 `web`。
9. `core/memory_manager.py:190` 保留了本地压测用的环境变量 `MEM0_LLM_BASE_URL` 覆盖。
10. **调用方身份在场（S0 实测）**：`MemoryManager.add` 唯一调用点 `core/chat_engine.py:536`（`_post_turn`）；生产经 `web/routers/chat.py:609` 的 `asyncio.to_thread` → `core/memory_manager.py:336` 的 `C.ctx_thread`（`core/concurrency.py:74` 拷贝上下文）→ 写入线程里 `LLM_CALLER` 是**发起聊天的真实用户**（Web 单聊，`web/server.py:429` 设置）；MCP 路径已在 `system_llm_context()` 内（`mcp_server/server.py:260`）。群聊不写 mem0（`core/group_session.py:164`）。

## 方案对比与拍板（Shiyu 已选 A）
| 方案 | 做法 | 依据 | 取舍 |
|---|---|---|---|
| **A（已选）** | mem0 提炼调用改走项目的 `LLMAdapter`：实现一个遵循 mem0 `LLMBase.generate_response` 接口的类，在 `Memory.from_config` 之后**注入**为 `self._mem.llm`（约束 7） | 约束 2、3；先例：阿里 AgentScope 对接 mem0 时同样实现 mem0 的 LLM 接口、把调用接到自己的模型层（其源码 `_mem0_long_term_memory.py`，走的是注册提供方 + 子类化配置绕过白名单）。**本 spec 选注入而非注册**：注入只依赖 `Memory.llm` 这一个属性与 `generate_response` 接口；注册要同时依赖工厂映射和两层 pydantic 配置校验，耦合面更大（审计后更正） | 能关闭思考；模型名、超时、重试、出站守卫复用适配器；依赖 mem0 的 `llm` 属性，靠锁定版本 + 测试守住 |
| B | mem0 自带提供方，模型名改为读配置 | mem0 官方配置参考 | 关不掉思考（约束 3）；仍绕开适配器 |

## 路径机制清单（mem0 提炼调用改走适配器后）
| 机制 | 计时/计数起点、前提 | 本改动怎么处理 |
|---|---|---|
| 出站守卫（地理合规） | 按**调用方身份**判定：大陆 IP 的用户 + 非白名单主机即拒绝（`web/geo_guard.py:204`） | **不改身份**：mem0 提炼的内容是该用户的聊天，出站应归属该用户，地理合规判定照常生效；`add` 路径上身份已在场（约束 10），无需也**不应**用 `system_llm_context()` 绕过。缺身份时 fail-closed 是正确行为 |
| 关闭思考（`_request_options`） | 按方言注入 | 经适配器自动生效，写测试锁住 |
| JSON 输出 | mem0 传 `response_format=json_object` | 适配器 `chat` 增加可选 `response_format`，原样透传给 API；不传时行为不变 |
| 超时 | 原先 mem0 自带的 OpenAI 客户端用 SDK 默认超时 | **前提改变**：改为适配器生成轮预算（单次 45 秒、总 60 秒，`adapters/llm_adapter.py:105/116`）；按下方核算单次数秒，装得下 |
| 并发 | 多个用户同时聊天会并发触发 `add` | 共用一个全局适配器实例（全局 key），OpenAI 客户端线程安全；不接 WP14 的闸（非 Map 路径） |
| tools | mem0 向量记忆的 `add` 不带 tools | 提供方收到 tools 时明确抛错（不静默忽略） |
| 记账 | 现有记账出口在蒸馏 / 路由层 | **S0 报**：mem0 路径现在有没有记账；本改动不新增记账 |

## 真实规模核算
- 每轮聊天触发一次 `add` → mem0 一次 LLM 调用（约束 2），输入约 1–2k token、输出几百 token；Flash 按官方价约 0.002–0.003 元 / 轮，关闭思考后单次数秒，远低于单次 45 秒。
- 若 `add` 在聊天回复的同步路径上（S0 核对），关闭思考即是不拖慢回复的前提；方案 A 满足。

## 步骤（按依赖顺序，每步独立 commit）
1. **[adapter] 默认模型只定义一处**：把 `LLMAdapter.__init__` 里内联的配置读取（约束 8）提成模块级函数，`__init__` 与新的 `default_model()` 都调用它（不复制读取逻辑）。回落值统一改为 `value or default_model()`：`adapters/llm_adapter.py:675`、`web/deps.py:87`、`web/routers/auth.py:608`。**存储层不再知道默认模型**：`storage/postgres_store.py:2554`、`storage/sqlite_store.py:3194` 改为原样返回库里的值（未设置即空），由上面几处调用方回落——存储层只管存取，默认值归解析层。**迁移里的列默认值本轮不动**（改它会影响新用户数据，属于单独的设计决定，待 Shiyu 另行拍板）；已有用户行不改（V4-Pro 官方仍在提供）。
2. **[adapter] `chat` 增加可选 `response_format`**，原样透传；不传时请求体与现在完全一致。
3. **[core] 新建 `core/mem0_llm.py`，只放 mem0 与适配器之间的这一层**（mem0 升级时只看这个文件）：一个类实现 mem0 `LLMBase.generate_response(messages, tools=None, tool_choice="auto", **kwargs)`——把 mem0 的 messages 拆成 system 与其余，直接调用 `LLMAdapter.chat(..., response_format=kwargs.get("response_format"))`（**不包 `system_llm_context()`**，沿用调用方身份，理由见路径机制清单），返回原始文本（mem0 在 `:956` 之后自行去代码块、解析 JSON）；收到 tools 即抛错。`core/memory_manager.py` 在 `Memory.from_config` 之后把它注入为 `self._mem.llm`；构造 mem0 时 LLM 配置里的模型名改用 `default_model()`（不再写死）；适配器实例用全局 key，`base_url` 仍尊重 `MEM0_LLM_BASE_URL`（约束 9）。向量（embedder）配置不动。
4. **[core] 日志级别**：`Mem0 add failed`（`:334`）与 `Mem0 manual add failed`（`:367`）改为 ERROR（写入失败即记忆丢失、不会自愈）。放在第 3 步之后，避免修好前刷屏。同处 `:332` 的 `result_len` 按 2.0.20 的返回形态（`{"results": [...]}`，`mem0/memory/main.py:878`）改正——本段改动面，直接修。
5. **[docs]** 更正 `config.example.yaml:13-16` 注释为官方口径（V4-Pro 9 月 14 日后继续提供），附官方更新日志出处。

## 测试
本地只跑受影响的测试文件，再加一次 `npm test`；库用 Docker 起的 PG；合并门是分支 CI；**合并只做 git 操作，不跑测试、不等 CI**。

需要的测试（假服务，不花钱）：
- 注入与请求形态：用锁定的 mem0 版本构造 `Memory.from_config` 并注入后，`add` 的请求打到假服务，请求体里模型名 = `default_model()`、带关闭思考参数、带 `response_format=json_object`；**变异：去掉注入（请求改由 mem0 自带客户端发出）/ 去掉关闭思考 → 变红**。
- 出站守卫：在带真实用户身份（大陆 IP）的上下文里调用、`base_url` 为非白名单主机 → 被拒（`LLMCallRefused`）；白名单主机 → 通过；**变异：提供方内部改为 `system_llm_context()` → 大陆 IP + 非白名单也放行，变红**。
- tools：`generate_response` 收到 tools 抛错。
- 默认模型：`default_model()` 读配置；库里模型为空时，`web/deps.py` 与 `auth.py` 的结果等于 `default_model()`；**变异：存储层恢复写死回落值 → 变红**。
- 超时不另写测试：本改动没有新增计时机制，mem0 调用走适配器现有的生成轮预算（已有测试覆盖）；单次数秒 ≪ 45 秒，见真实规模核算。

## 范围规矩
执行中新发现的问题，属于本段改动面的直接修；只有会撞车或需要 Shiyu 拍板时才停下报告，不自行记账。

## S0（执行方先做，只读）
1. 逐条复核「已查实的约束」1–9 的坐标（含 mem0 2.0.20 源码里的位置：`llms/deepseek.py:37`、`llms/configs.py` 的 `validate_config`、`utils/factory.py:128`、`memory/main.py:499/956/2184`）；不成立就停下报告。
2. 报 `MemoryManager.add` 的调用点与所在线程，以及此时 `LLM_CALLER` 是否在场（坐标）。
3. 核对 `system_llm_context()`（`core/request_context.py:54`）的用法与现有调用点（坐标），第 3 步复用它。
4. 报 mem0 路径现在有没有记账（坐标）。
报完停下等审计。

## 验收（部署后，免费）
部署后用演示账号和一张卡聊两轮：容器日志无 `Mem0 add failed`，第二轮能检索到第一轮写入的记忆。按 Flash 价约 0.005 元。


---

## 补充 1（2026-09-28，审计 PR #44 / `505d1ab` 后）

> **执行方**：`distill-mainline` 窗口。先 `git fetch origin`，从 `origin/main`（`505d1ab`）新开分支 `fix/mem0-followups`（`--unset-upstream`）；**不要**在已合入的 `fix/mem0-llm-via-adapter` 上继续做。
> 起因：审计已合入的 PR #44 时发现以下 4 处。前 3 处是本 spec 漏写的，不是执行偏差。本节与上文冲突时，以本节为准。

### 审计清单（PR #44，`ae89f7d..505d1ab`；受影响 4 个测试文件 39/39 通过，变异 7 条中 5 条变红）
| 文件 | 看过 | 结论 |
|---|---|---|
| `adapters/llm_adapter.py` | 是 | 通过（`default_model`、`response_format` 透传） |
| `core/mem0_llm.py` | 是 | 通过 |
| `core/memory_manager.py` | 是 | 问题 2（采样参数） |
| `storage/postgres_store.py`、`storage/sqlite_store.py` | 是 | 问题 1（PG 一侧无测试；「PG 存储恢复写死」变异存活） |
| `web/deps.py`、`web/routers/auth.py` | 是 | 通过（「deps 恢复写死」变异存活，但它是等价变异：键存在时 `get` 默认值不生效，适配器仍回落 `default_model()`） |
| `config.example.yaml` | 是 | 通过 |
| `tests/test_mem0_llm.py`、`test_memory_manager.py`、`test_llm_adapter_thinking.py`、`test_default_model.py` | 是 | 通过；`test_default_model.py` 只测了 SQLite（问题 1）；`result_len` 的改正只影响打印，不补测试 |
| 前端 `ApiConfigPanel.jsx`、`scripts/backfill_psyche.py` | 是 | 不在 PR 内，审计时发现：问题 3、4 |

### 目标
- 默认模型的「只定义一处」真正做完：前端也要跟上，且存储层的改动在 PG 上有测试守着。
- mem0 提炼沿用 mem0 原本的采样参数。
- 仓内不再有已停用的模型名。

### 问题与证据（基线 main `505d1ab`）
1. **PG 存储层没有测试守着**：`tests/test_default_model.py:46-47` 的 `store` fixture 只用 `SQLiteStore`。把 `storage/postgres_store.py:2554` 改回 `row[2] or "deepseek-v4-pro"`，测试照样全绿（审计已实测）。
2. **提炼的采样参数被悄悄改了**：
   - mem0 2.0.20 默认 `temperature=0.1`（`mem0/configs/llms/base.py:19`），openai 提供方只发 temperature，不发 presence_penalty；
   - 现在走适配器，用的是全局配置 `temperature: 0.7`（`config.example.yaml:21`）加 `presence_penalty: 0.3`（`:19`）；
   - 原 spec 的路径机制清单漏了「采样参数」这一项。
3. **前端写死了模型名**：`web/frontend/src/components/ApiConfigPanel.jsx` 有三处：
   - `:59` 的 `savedModel || 'deepseek-v4-pro'`；
   - `:108` 点「DeepSeek」卡片时，填入 `model: 'deepseek-v4-pro'`；
   - `:174` 的提示文案写死「推荐使用 deepseek-v4-pro 或 claude-sonnet-4-20250514」。

   另外，前端读的 `/api/settings/config` 返回的是 `str(llm.get("model", ""))`（`web/server.py:525`，管理员保存后的返回在 `:596`），配置里没写模型时给的是空串，不是 `default_model()`。
4. **已停用的模型名**：`scripts/backfill_psyche.py:160-170` 直接新建 OpenAI 客户端，并写死 `model="deepseek-chat"`。这个名字官方 2026-07-24 起已停用，而且这条调用绕过了适配器的关闭思考和出站守卫。全仓非测试代码里只剩这一处（grep `deepseek-chat|deepseek-reasoner`）。

### 已查实的约束（S0 逐条复核，不成立即停）
- a. `LLMAdapter.__init__`（`adapters/llm_adapter.py:683`）已有关键字参数 `temperature`（`:700` 使用）。`presence_penalty` 目前只从配置读取（`:702`），没有构造参数。
- b. mem0 的适配器实例在 `core/memory_manager.py:178` 构造。
- c. PG 存储 fixture 的现成写法在 `tests/test_demo_seed.py:43-48`（`PostgresStore(os.environ["DATABASE_URL"])` + `_ensure_initialized` + `close`）。
- d. 请求之外调用 LLM 的合法身份是 `core/request_context.py:54` 的 `system_llm_context()`。缺身份时守卫 fail-closed，见 `web/llm_gate.py:65-70`。
- e. `default_model()` 在 `adapters/llm_adapter.py`（`505d1ab`）。它读配置文件的顺序（config.yaml → config.example.yaml）与 `web/deps.py:36-38` 的 `_CFG_PATH` 一致；管理员保存设置时会先落盘，所以保存后立刻读到的就是新值。

### 路径机制清单（本改动碰到的机制）
| 机制 | 前提 | 本改动怎么处理 |
|---|---|---|
| 采样参数（适配器实例级） | 全局配置 0.7 / 0.3 是按角色扮演选的 | mem0 实例单独传 0.1 / 0.0，其他实例不变 |
| 出站守卫（脚本） | 请求之外没有身份时 fail-closed | 脚本调用包在 `system_llm_context()` 里 |
| 关闭思考 | 走适配器才会注入 | 脚本改走适配器后自动生效，写测试锁住 |

### 通道 × 执行上下文（本补充碰到的调用点）
| 调用点 | 执行上下文 | 身份 | 守它的测试 |
|---|---|---|---|
| mem0 提炼（`core/memory_manager.py` 写入线程） | 后台线程（`C.ctx_thread`，拷贝了调用方上下文） | 发起聊天的用户（原 spec 约束 10），本补充不改 | T2 |
| 回填脚本 `_call_llm_for_psyche` | 命令行脚本，同步，不在任何请求里 | 没有 → 包 `system_llm_context()` | T5 |
| `/api/settings/config` | FastAPI 同步路由 | 不涉及 LLM | T3 |

### 步骤（按依赖顺序，每步独立 commit）
1. **[test] 默认模型测试改到 PG 上**：`tests/test_default_model.py` 的 `store` fixture 改用 PG（写法同约束 c）。SQLite 不再为此单独写测试（PG 为准）。
2. **[adapter + core] 提炼参数**：
   - `LLMAdapter.__init__` 增加关键字参数 `presence_penalty: float | None = None`，写法与 `temperature` 对称。
   - `core/memory_manager.py` 定义两个常量并注明出处：
     - `_EXTRACT_TEMPERATURE = 0.1`，出处 mem0 2.0.20 `configs/llms/base.py:19`；
     - `_EXTRACT_PRESENCE_PENALTY = 0.0`，出处：mem0 openai 提供方不发该参数，即 API 默认值 0。
   - 构造 mem0 的适配器时（`:178`）传入这两个常量。
3. **[web + frontend] 前端不写死模型名**：
   - 后端：`web/server.py` 读设置（`:519-529`）与保存设置后的返回（`:592-598`）各拼了一份相同的设置字典，只有 `api_key` 的判定不同（读取时还看环境变量）、保存时多一个提示字段。
     - 收成一个模块内的私有函数 `_settings_payload(llm, voice, has_key)`，两处都调用它，`"model"` 在这一处改为 `default_model()`；
     - 保存那处在返回值上再加它自己的提示字段；
     - 不要逐处改两行。在模块顶部 `from adapters.llm_adapter import default_model`，T3 靠替换模块属性来测。
   - 前端 `ApiConfigPanel.jsx`：从 `/api/settings/config` 取 `model` 存为默认模型。
     - `:59` 的回落改用它；
     - `:108` 点 DeepSeek 卡片时填入它；
     - `:174` 的文案改为「推荐使用 {默认模型}」，删掉写死的模型名。
4. **[scripts] 回填脚本改走适配器**：
   - `_call_llm_for_psyche` 改用 `LLMAdapter(api_key=api_key, temperature=0.3, max_tokens=1024).chat(PSYCHE_SYSTEM_PROMPT, [{"role": "user", "content": prompt}])`，外面包 `system_llm_context()`。
   - 后面的 JSON 提取和字段校验不动。

### 测试（本地只跑受影响文件，改前端的一步加跑 `npm test`；库用 docker PG；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）
- **T1**：`tests/test_default_model.py` 在 PG 上的现有 6 条全部通过。
- **T2 提炼参数**：经 `MemoryManager` 构造出的适配器（把它的 `_client` 换成记录请求的假客户端）发出的请求体里：
  - `temperature == 0.1`、`presence_penalty == 0.0`；
  - 同时断言一个普通 `LLMAdapter()` 仍为配置值，证明没有改到全局。
- **T3 后端**：把 `web/server.py` 模块命名空间里的 `default_model`（测试里 `import server`，同 `tests/test_client_config.py:26`；即 `monkeypatch.setattr(server, "default_model", ...)`）替换为返回探针值 `probe-model` 的函数，`/api/settings/config` 返回的 `model == "probe-model"`。不要去改真实的配置文件：`default_model()` 读的是磁盘文件，与 `get_config()` 的内存副本是两份。
  - 读设置与管理员保存设置两个接口都要测：两者返回的 `model` 都等于探针值。
- **T4 前端**（新文件 `web/frontend/src/components/__tests__/ApiConfigPanelDefaultModel.test.jsx`）：mock `/api/settings/config` 返回 `{model: 'probe-model'}`，`/api/auth/me` 返回 `{model: ''}`。
  - 模型输入框的值为 `probe-model`；
  - 点 DeepSeek 卡片后，值仍为 `probe-model`；
  - `/me` 返回 `{model: 'mine'}` 时，值为 `mine`。
- **T5 脚本**：`_call_llm_for_psyche` 发出的请求：
  - 模型为 `default_model()`；
  - 带关闭思考参数；
  - 装上生产守卫（`set_call_guard(geo_call_guard)`，写法同 `tests/test_mem0_llm.py` 的 `gate` fixture）后，在没有请求身份的线程里调用，不被 fail-closed 拒绝。

### 对账表（S0 先审：逐条确认变异在改后代码上可观测、每个决定都有测试；有问题先停下报告）
| 行为变化（含连带效果） | 守它的测试 | 让它变红的变异 | 改后能触发的具体状态 |
|---|---|---|---|
| PG 存储层不再自带默认模型 | T1 | `postgres_store.py:2554` 恢复 `or "deepseek-v4-pro"` | PG 上未设模型的用户，拿到的是配置里的模型 |
| mem0 提炼用 temperature 0.1 | T2 | 删掉传入的 temperature | 提炼请求体的 temperature 是 0.1 |
| mem0 提炼用 presence_penalty 0 | T2 | 删掉传入的 presence_penalty | 请求体的 presence_penalty 是 0 |
| 全局实例的采样参数不变（连带） | T2 | 构造参数的默认值改成 0.1 | 聊天请求仍是 0.7 / 0.3 |
| 设置接口回落到默认模型 | T3 | `_settings_payload` 里恢复 `llm.get("model", "")`（返回真实配置值，不等于探针值） | 配置缺模型时，设置页显示配置默认值而不是空 |
| 读、存两个接口共用一份设置字典 | T3（保存接口用例） | 保存接口改回自己拼字典 | 管理员保存后，返回的模型名与读取时一致 |
| 前端初值回落用默认模型 | T4 | `:59` 恢复写死值 | 未设模型的用户在设置页看到的是配置模型 |
| DeepSeek 卡片填默认模型 | T4 | `:108` 恢复写死值 | 点卡片后保存，存下的是配置模型 |
| 脚本不再用停用模型名 | T5 | 恢复直连 OpenAI + `deepseek-chat` | 回填脚本能正常请求 |
| 脚本关闭思考 | T5 | 脚本改回不走适配器 | 回填请求不进入思考模式 |
| 脚本有合法系统身份 | T5 | 去掉 `system_llm_context()` | 回填不会被 fail-closed 拦下 |

### skill
| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 复核坐标与 grep 结论 |
| 1–4 | `@tdd` | 先写 T1–T5 变红，再改代码 |

### 范围规矩
执行中新发现的问题，属于本段改动面的直接修；只有会撞车或需要 Shiyu 拍板时才停下报告，不自行记账。

### S0（执行方先做，只读，报完停下等审计）
1. 在 `505d1ab` 上复核「问题与证据」1–4 和约束 a–e 的坐标，不成立即停。
2. 重新 grep 前端 `web/frontend/src` 里所有写死的模型名，报清单；清单与问题 3 不一致就停下报告。
3. 审对账表。
4. 交付报告只报受影响文件与分支 CI，不写本地全量数字。

### 验收
无需真跑。部署后打开设置页，模型栏显示的是配置里的模型（当前为 `deepseek-flash`）。
