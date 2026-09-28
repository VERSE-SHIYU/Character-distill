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
1. mem0 配置：`core/memory_manager.py:174-200`；LLM 部分 `provider: openai`、`model: "deepseek-chat"`（`:186`）、`api_key` 取自全局环境变量 `DEEPSEEK_API_KEY`（`:156`）；向量部分用 DashScope `text-embedding-v4`（`:198` 起），**不受影响、不改**；`Memory.from_config`（`:204`）；初始化失败记 ERROR（`:216`）；`add` 在 `:317`，调用 mem0 在 `:331`，失败记 WARNING（`:334`）；`add_manual` 失败同为 WARNING（`:367`）。
2. mem0 版本：仓内锁定 `mem0ai==2.0.20`（`requirements.txt:166`）。PyPI 最新为 2.2.1（2026-09-25）；两版源码核过：DeepSeek 提供方默认模型仍是 `deepseek-chat`（`mem0/llms/deepseek.py:37`），**没有传 `extra_body` 的通道**；提供方白名单写死在 `mem0/llms/configs.py` 的 `validate_config`；工厂有 `LlmFactory.register_provider`（`mem0/utils/factory.py:128`，2.2.1）；`add` 对 LLM 的调用是**一次** `generate_response(messages=[system, user], response_format={"type": "json_object"})`（2.0.20 `mem0/memory/main.py:956`），不带 tools。
3. DeepSeek 官方「思考模式」页（2026-09-28 核对）：思考默认开启；OpenAI 格式下关闭思考**只能**用 `extra_body={"thinking": {"type": "disabled"}}`，`reasoning_effort` 只调强度、关不掉。官方「模型 & 价格」页：`deepseek-flash` 支持 JSON Output 与 Tool Calls。
4. 项目适配器：`LLMAdapter.chat(system_prompt, messages, max_tokens)`（`adapters/llm_adapter.py:769`），**不支持 `response_format`**；关闭思考由 `_request_options`（`:748`）统一注入；出站守卫 `check_outbound_guard`（`:315`）→ 生产注册的 `geo_call_guard`（`web/llm_gate.py:53`），**拿不到调用方身份时 fail-closed 抛 `LLMCallerMissing`（`web/llm_gate.py:67`）**，报错原文要求「请求之外的调用请显式声明 `core.request_context.system_llm_context()`」；SYSTEM 调用方无条件放行（`:71`）。现成的 SYSTEM 身份写法：`core/request_context.py:54` 的 `system_llm_context()`。
5. 默认模型写死处：`adapters/llm_adapter.py:675`（`llm_cfg.get("model", "deepseek-v4-pro")`）、`web/deps.py:87`（用户自带 key 未填模型）、`web/routers/auth.py:608`（返回给前端的当前模型）、`storage/postgres_store.py:2554` 与 `storage/sqlite_store.py:3194`（`row[2] or "deepseek-v4-pro"`）、迁移里的列默认值 `storage/migrations_pg/001_init.sql:98`、`storage/migrations/018_user_api_config.sql:3`。`config.example.yaml:17` 已是 `model: deepseek-flash`。
6. `config.example.yaml:13-16` 的注释写「2026-09-14 12:00 起 `deepseek-v4-pro` 的请求已被全部路由到 V4.1-Flash 并按 Flash 单价计费」——与官方更新日志不符（官方 2026-09-10：V4-Pro 在 9 月 14 日后**继续提供**、计费不变）。
7. mem0 同步版 `Memory` 在 `__init__` 里设置 `self.llm = LlmFactory.create(...)`（2.0.20 `mem0/memory/main.py:499`），之后 `add`（`:956`）等都通过 `self.llm.generate_response` 调用；`:2184` 的重建属于 `AsyncMemory`，我们不用。`core/memory_manager.py` 只调 `add / search / get_all / update / delete / delete_all`，都不重建 `llm`。
8. 适配器的配置读取目前内联在 `LLMAdapter.__init__`（`adapters/llm_adapter.py:657-672`）；`web/deps.py:461` 另有 `get_config()`，但 `adapters` 不能依赖 `web`。
9. `core/memory_manager.py:188-190` 保留了本地压测用的环境变量 `MEM0_LLM_BASE_URL` 覆盖。

## 方案对比与拍板（Shiyu 已选 A）
| 方案 | 做法 | 依据 | 取舍 |
|---|---|---|---|
| **A（已选）** | mem0 提炼调用改走项目的 `LLMAdapter`：实现一个遵循 mem0 `LLMBase.generate_response` 接口的类，在 `Memory.from_config` 之后**注入**为 `self._mem.llm`（约束 7） | 约束 2、3；先例：阿里 AgentScope 对接 mem0 时同样实现 mem0 的 LLM 接口、把调用接到自己的模型层（其源码 `_mem0_long_term_memory.py`，走的是注册提供方 + 子类化配置绕过白名单）。**本 spec 选注入而非注册**：注入只依赖 `Memory.llm` 这一个属性与 `generate_response` 接口；注册要同时依赖工厂映射和两层 pydantic 配置校验，耦合面更大（审计后更正） | 能关闭思考；模型名、超时、重试、出站守卫复用适配器；依赖 mem0 的 `llm` 属性，靠锁定版本 + 测试守住 |
| B | mem0 自带提供方，模型名改为读配置 | mem0 官方配置参考 | 关不掉思考（约束 3）；仍绕开适配器 |

## 路径机制清单（mem0 提炼调用改走适配器后）
| 机制 | 计时/计数起点、前提 | 本改动怎么处理 |
|---|---|---|
| 出站守卫（fail-closed） | 需要调用方身份 | **S0 核对**：`MemoryManager.add` 在哪个线程被调用、`LLM_CALLER` 是否在场。mem0 用的是全局 key，按系统调用处理：在提供方内部用 `system_llm_context()`（`core/request_context.py:54`）包住调用 |
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
3. **[core] 新建 `core/mem0_llm.py`，只放 mem0 与适配器之间的这一层**（mem0 升级时只看这个文件）：一个类实现 mem0 `LLMBase.generate_response(messages, tools=None, tool_choice="auto", **kwargs)`——把 mem0 的 messages 拆成 system 与其余，用 `system_llm_context()` 包住调用 `LLMAdapter.chat(..., response_format=kwargs.get("response_format"))`，返回文本；收到 tools 即抛错。`core/memory_manager.py` 在 `Memory.from_config` 之后把它注入为 `self._mem.llm`；构造 mem0 时 LLM 配置里的模型名改用 `default_model()`（不再写死）；适配器实例用全局 key，`base_url` 仍尊重 `MEM0_LLM_BASE_URL`（约束 9）。向量（embedder）配置不动。
4. **[core] 日志级别**：`Mem0 add failed`（`:334`）与 `Mem0 manual add failed`（`:367`）改为 ERROR（写入失败即记忆丢失、不会自愈）。放在第 3 步之后，避免修好前刷屏。
5. **[docs]** 更正 `config.example.yaml:13-16` 注释为官方口径（V4-Pro 9 月 14 日后继续提供），附官方更新日志出处。

## 测试
本地只跑受影响的测试文件，再加一次 `npm test`；库用 Docker 起的 PG；合并门是分支 CI；**合并只做 git 操作，不跑测试、不等 CI**。

需要的测试（假服务，不花钱）：
- 注入与请求形态：用锁定的 mem0 版本构造 `Memory.from_config` 并注入后，`add` 的请求打到假服务，请求体里模型名 = `default_model()`、带关闭思考参数、带 `response_format=json_object`；**变异：去掉注入（请求改由 mem0 自带客户端发出）/ 去掉关闭思考 → 变红**。
- 出站守卫：在没有调用方上下文的线程里调用也能通过；**变异：去掉 `system_llm_context()` → 抛 `LLMCallerMissing` 变红**。
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
