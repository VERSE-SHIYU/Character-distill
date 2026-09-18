# Spec v5：LLM 访问门收敛 + core 反向依赖根除

- **状态**：执行中。v5 **全量取代** v1–v4。
- **基线**：`main@909081c`；本地已提交未 push 的有 C0（`b1e2d95`、`b9df8de`）与 C1（`c7856cf`）。
- **位置一律写符号名，不写行号**（AGENTS §四）。

---

## 0. 已锁决策

| # | 决策 |
|---|---|
| D1 | 被 geo 拦截：全仓统一 **403**，不回落全局 key |
| D2 | 用户无 key，或 per-user 初始化失败：回落全局 key。初始化失败要留下原因并打日志，台账登记为「有意保留的失败吞成成功」 |
| D3 | 请求内所有 LLM 使用都受 geo 门约束 |
| 门位 | **调用点门**：adapter 出站前调用注入的守卫；身份经 contextvar 传递；没有上下文就 fail-closed |
| 范围 | 并入与门同一机制的三件事：**core→web 反向依赖**、**跨 loop 投递原语**、**上下文传播的归属** |

## 1. 事实（审计现跑，作为动机）

| 编号 | 事实 |
|---|---|
| F1 | `/start`：用户无 key、全局可用时，909081c 返回 503，a7f5d50 返回 200（回归来自 bb62311） |
| F2 | 后台蒸馏线程在同一条件下，自 9864608 起报「请先配置 API Key」 |
| F3 | `get_user_llm` 缓存命中时跳过 geo 检查 |
| F4 | geo 策略有三份且互相矛盾：`get_user_llm`、`_distill_start_impl`、`_run_distill_task` |
| F5 | 6 处 `get_user_llm` 调用不传 IP |
| F6 | 聊天和群聊会话常驻 LLM 实例，命中后不再做任何 geo 检查 |
| F7 | 派生原语有两个：`ctx_thread`（9 处）和 `ctx_submit`（3 处），OTEL 关闭时都不传播 contextvar。`ctx_submit` 中 2 处会到达 adapter：coref 和 agent 的 web_search |
| F8 | core 中有 **10 处** `from deps import`：`run_on_main_loop` 7 处（`chat_engine` 5、`evaluation_pipeline` 2），`get_llm` 2 处（`auto_review`），`get_memory_manager` 1 处（`TextManager` 的会话装配） |
| F9 | 投递写法有三种：`deps.run_on_main_loop`（web 12 处、core 7 处）、`core.utils.record_usage` 自建 loop 写库（违反前者 docstring）、审计投递 |
| F10 | `_do_chat` 只放行 `llm_error_payload` 能识别的异常；web 自定义的拒绝异常会被吞成 500 |
| F11 | 边界锁 `test_no_exception_class_leaks_into_core_web_storage` 禁止 core/web/storage 出现 LLM 异常类名 |

### 1.1 执行期实测订正（C1′ 现跑现数）

- **F9 的函数名**：`core/utils.py` 里的符号是 `try_record_usage`（不是 `record_usage`）——
  「自建 loop 的线程写库」在它的嵌套 `_do()` 里。
- **§2.10 的函数名**：market 里那处无参 `LLMAdapter()` 所在符号是 `_publish_preflight`
  （不是 `_publish_pregate`），且 `web/routers/market.py` 中不存在 `_publish_pregate`。
- **F7 的计数口径**：`ctx_submit` 是 **4 行 / 3 个所属函数**（`ContextEngine.build_ex` 一个
  `with` 里派生两次）。「3 处」是 owner 数，不是行数。
- **F9 的计数口径订正**：`run_on_main_loop` 的**真实调用点 14 处**（core 7：`chat_engine` 5 +
  `evaluation_pipeline` 2；web 7：`distill` 6 + `text` 1）。F9 的「web 12 处」是**文本出现次数**，
  含 `distill.py` 里 2 处 import 与 3 处 docstring 提及 —— 这正是 AGENTS §四「判据 grep
  要分清注释与调用」那条的又一例。`scripts/` 另有 3 处：2 处是 `_fake.run_on_main_loop = ...`
  的 shim、1 处注释。**迁移时 script 的 shim 必须跟着改指新名字**，否则它们静默失去拦截
  （仍会绿，但拦不到东西）。
- **L12 的现状读数**（生产、入库 .py、去 tests/ 与 scripts/）：
  `check_api_allowed(` 4 处调用、`record_geo_block(` 3 处调用（另有 3 处同名的 storage 方法**定义**，不计）。
- **L13 的现状读数**：`core/` + `adapters/` 里 10 处 `from deps import`，与 F8 逐条对上。
- **`web/app.py` 是死代码**：Gradio，docstring 标 `.. deprecated::`，`Dockerfile` /
  `docker-compose*.yml` / `start_all.bat` / `.github/workflows/` 零引用（AGENTS.md 已记）。
  故 L10 的扫描面不含它。
- **L15 有半条命题没有观测点** → **裁定：补配对读口**。L15 的后半「OTEL 开启时 telemetry
  已注册载体」在 v5 里没有可判定的观测点 —— 载体注册不像守卫那样有与
  `register_context_carrier` 配对的读口（`set_call_guard` / `get_call_guard` 那对）。
  故 C2b 建 `core/concurrency.py` 时**一并加 `set_context_carrier` / `get_context_carriers`
  配对**，L15 后半改为可判定（OTEL 开启时 telemetry 注册的载体出现在读口里）。
  C1′ 的锁只钉了前半与「协议被派生路径驱动」，C2b 补齐。
- **`auto_review_card` 零生产调用点** → **裁定：保留，`llm` 改必填**。现跑现数：全仓唯一
  生产出现是它自己的 `def`，其余只在 `tests/test_auto_review.py`。与 `auto_review_split`
  同一处理（§2.4 的字面），不删。对照：`auto_review_split` 有 1 处生产调用点
  （`market._publish_preflight`），而 `tests/test_auto_review.py` 有一条 `llm=None` 的用例
  正断言着将被删掉的回落路径 —— 那条用例的处置属于 C2a 的范围。
- **PG 形态的落地** → **裁定：在本机 compose PG 上建专用测试库**。`REQUIRE_PG_TESTS=1` 的
  兜底 DSN（`postgres:postgres@localhost:5432/charsim_test`）被拒，两处本机 postgres 都对
  该默认口令返回 `InvalidPasswordError`；`.env` 只有 `POSTGRES_USER/PASSWORD/DB`（**没有**
  `DATABASE_URL`）。故用 `.env` 凭据在本机 compose PG 上建**独立库 `charsim_test`**，
  只跑它，`charsim`（应用库）不动。真实读数见 C1′ 的补测：**1307 passed, 1 skipped,
  40 xfailed, 0 failed, 0 errors**（skip 形态那 64 条里 63 条转为通过）。
  **此后 C2a–C5 每一步的「带 PG 形态」都按这个 DSN 真跑**，不再只记录读数。

### 1.2 C2a 执行期订正

- **L14 的 `wait=False` 回退判据原本判不出来**。C1′ 写的那条只断言「协程跑了」——
  一个**阻塞**的错回退（把协程跑完才返回）照样绿。这正对上 spec §3 的变异 ③
  「未注册时 `wait=False` 改成阻塞」，即**变异打不红**。C2a 把它改成记序：
  用 `await asyncio.sleep(0)` 让出一次，要求 `["RETURNED", "CORO"]` ——
  丢协程（缺 `CORO`）与阻塞（顺序颠倒）两种错法各被一半判据钉住。
  订正后变异 ③ 实测红在 `test_l14_unregistered_fallback_semantics`。
- **同类订正：投递测试的还原不能置 `None`**。C1′ 的 L9/L14 用例在 `finally` 里
  `set_loop_submitter(None)`；生产 app 一旦跑过 lifespan，`deps.set_main_loop` 就注册了
  实现，**同一个进程**里后续用例会被这个 `None` 拆掉注册、静默改走回退分支。新增
  `_SetSubmitter`，按**原值**还原（与 `_SetGuard` 同一个道理）。
- **被删回落路径的两处测试痕迹**（`tests/test_auto_review.py`）：一条用
  `patch("deps.get_llm", return_value=None)` 装了**已失效的**替身（惰性，会静默通过），
  一条断言 `deps.get_llm` 被调用过（正是被删的那条）。前者删掉，后者改成
  「装一个**一被调用就炸**的哨兵、断言 `calls == []`」—— 证明 core 不再向 web 取 LLM，
  比原来更硬。
- **`wait=False` 的异常落点**：`web/deps.py` 的 `_log_loop_error` 先查 `fut.cancelled()` ——
  已取消的 future 上 `exception()` 会抛 `CancelledError`，会以「回调里又有异常」的形态
  漏出来。

## 2. 目标架构

### 2.1 分层与依赖方向（只允许向下依赖）

```
web/           server（装配、注册）  deps（工厂、缓存、解析出口）
               llm_gate（上下文、守卫、审计）  llm_resolution（纯策略）  geo_guard（既有）
core/          scheduling（投递接口）  concurrency（派生与传播）  telemetry（OTel 载体）
               业务模块（chat_engine、distiller……）只依赖 core 与 adapters
adapters/      llm_adapter（守卫钩子契约、拒绝异常、失败边界）
```

**不变量**：core 和 adapters 不 import web（由 L13 锁住）。web 在启动时向下**注册**实现：守卫、投递器、OTel 载体。

### 2.2 `core/scheduling.py`：跨 loop 投递的唯一原语

- `set_loop_submitter(fn | None)`；`submit_to_main_loop(coro, *, wait: bool = True, timeout: float = 600)`。
- 已注册时，委托给注册的实现。
- 未注册时的回退**只在这里定义一次**，语义与现在的 `run_on_main_loop` 相同：
  - `wait=True`：`asyncio.run` 并发出 warning。
  - `wait=False`：当前线程有运行中的 loop 就 `create_task`，否则 `asyncio.run`。
- `web/deps.py` 保留主 loop 的捕获逻辑，提供注册实现：`run_coroutine_threadsafe`；`wait=True` 时取 `result(timeout)`；`wait=False` 时不取结果，由 done-callback 记录异常。
- **删除 `deps.run_on_main_loop`**。web 12 处、core 7 处调用全部改为 `submit_to_main_loop`，**不留别名**。
- `core.utils.record_usage` 删掉自建 loop 的线程写库，改为 `submit_to_main_loop(..., wait=False)`。

### 2.3 `core/concurrency.py`：派生与传播

- `ctx_thread` 和 `ctx_submit` 从 telemetry **迁到这里**，调用点全部改 import，**不做 re-export**。
- 两者共用一个内部包装器：
  - 在调用方线程 `copy_context()`，并对已注册的载体逐个 `capture()`；
  - 在子线程的拷贝 context 内，按顺序 `restore()` 各载体 → 执行 fn → 逆序 `release()`；
  - **OTEL 开还是关都一样**。
- `register_context_carrier(carrier)`：载体协议为 `capture() -> state`、`restore(state) -> token`、`release(token)`。
- `core/telemetry.py` 在启用 OTel 时注册 OTel 载体。**concurrency 模块内不出现 opentelemetry**（L15）。docstring 里记录的 token 冲突在此由结构解决：attach 发生在拷贝出的 context 内。
- `web/server.py` 的 `set_default_executor` 注记订正：`asyncio.to_thread` 会拷贝 context，裸 `run_in_executor` 不会。

### 2.4 其余反向依赖改为注入

- `core/moderation/auto_review.py`：`auto_review_split` 和 `auto_review_card` 的 `llm` 改为**必填**，删除 `from deps import get_llm` 回落。
  - `auto_review_card` 在生产中没有调用点。先核 tests：若只有测试在用，**停下报告**，不要擅自删除。
- `TextManager`：构造参数增加 `memory_manager`，由 `deps._assemble_text_manager` 注入，删除会话装配里的 `from deps import get_memory_manager`。沿用「唯一装配出口」，加参数只需改一处。

### 2.5 `adapters/llm_adapter.py`：守卫钩子与失败边界

- `set_call_guard(fn | None)`，契约为 `fn(base_url) -> str | None`（返回拒绝理由或 None）。未注册时不做检查，独立进程保持现状。
- `LLMAdapter._before_call()`：拿到理由就 `raise LLMCallRefused(reason, base_url)`。**所有出站方法**都在出站前调用它，流式方法在建流之前调用。
- `LLMAdapter.preflight()`：与 `_before_call()` 同一实现，公开给解析出口使用。
- `LLMAdapter.base_url`：只读属性。
- `LLMCallRefused(RuntimeError)`：实现 `__reduce__`（缺陷 18），并登记进 `llm_error_types()`。
- `llm_error_payload` 统一产出判别键 **`kind`**：
  - 未完成终态为 `incomplete:<finish_reason>`；
  - 拒绝为 `call_refused`，`error` 取拒绝理由。
  - 保留现有 `code` 和 `finish_reason` 字段。

### 2.6 `web/llm_gate.py`：门（只管门）

- `Caller(ip, user_id)`、`SYSTEM`、`LLM_CALLER: ContextVar`、`system_llm_context()`（当前生产中没有调用点；它是 fail-closed 的唯一合法出口，docstring 写明）、`LLMCallerMissing`。
- `geo_refusal(ip, base_url, user_id) -> str | None`：**唯一**调用 `check_api_allowed` 的地方；被拦时调用 `emit_geo_block_audit`，然后返回理由。
- `geo_call_guard(base_url)`：读取 `LLM_CALLER`。
  - 值为 None → `raise LLMCallerMissing`；
  - 值为 SYSTEM → 返回 None；
  - 否则 → 返回 `geo_refusal(caller.ip, base_url, caller.user_id)`。
- `emit_geo_block_audit(...)`：**唯一**调用 `record_geo_block` 的地方，经 `submit_to_main_loop(wait=False)` 投递。写入失败只记日志，不改变判定（沿用原容忍理由的注释）。
- `install_llm_gate(app)`：执行 `set_call_guard(geo_call_guard)`。生产和测试最小 app 共用这一个注册函数。
- `update_api_config` 的保存时检查改为 `geo_refusal(...)`，返回理由时回 403。

### 2.7 `web/llm_resolution.py`：解析策略（纯函数）

- `Source`（`USER` / `GLOBAL` / `UNAVAILABLE`）、`Resolution(source, llm, reason)`。
- `resolve_llm(config, *, build_user, get_global)` 规则：
  - 有 key 且构造成功 → USER；
  - 有 key 但构造抛错 → GLOBAL，reason 写明异常；
  - 无 key → GLOBAL；
  - 全局也是 None → UNAVAILABLE。

### 2.8 `web/deps.py`：工厂与唯一出口

- `_make_user_llm(config)` 与 `_make_global_llm()` 是**仅有的两处** `LLMAdapter(` 构造点。`get_llm` 和 `reset_llm_and_dependents` 都调用 `_make_global_llm`，先例是 `_make_indexing_service`。
- `get_user_llm(user_id, storage=None)`：
  - **删除 `client_ip` 参数**；
  - 流程：读配置 → `resolve_llm` → 只缓存 USER 结果 → reason 非空时打日志（D2）→ **每次**返回前调用 `llm.preflight()`，缓存命中也要调；
  - 删除原有的 geo 检查、审计，以及 `except HTTPException` 分支。

### 2.9 `web/server.py`：装配

- `AuthMiddleware.dispatch` 重构为**只有一个 `call_next` 出口**：公开路径的 user 为 `{}`，在出口处**只设置一次** `LLM_CALLER = Caller(get_client_ip(request), user_id)`。
- 装配时调用 `install_llm_gate(app)`，并向 core 注册投递器；启用 OTel 时由 telemetry 注册载体。
- 配码只保留一张表：`_INCOMPLETE_STATUS` 改名为 `_LLM_ERROR_STATUS`，按 `kind` 查表，新增 `call_refused → 403`，缺省为 502。

### 2.10 调用方

- `_distill_start_impl`：删除手抄的解析逻辑，改为 `await get_user_llm(user_id, storage)`；embedding 二元组在这里取一次。
- `_run_distill_task`：签名把 `api_config` 换成 `llm`、`embedding_key`、`embedding_region`；删除线程内的解析和 geo 逻辑，删除 `llm_for_save = get_llm()` 回落；入口断言 `llm is not None`。
- 所有 `get_user_llm(..., client_ip=...)` 调用去掉该实参。
- `market` 的 `_publish_preflight`（spec 原文写作 `_publish_pregate`，见 §1.1）：`LLMAdapter()` 改为 `get_llm()`，删除随之失效的局部 import。`get_llm()` 为 None 时的行为要实测，应进入 `auto_review_split` 的 error 分支转人工。

## 3. 提交序列与门

**每个门**都包括：
- 全量测试（带 PG 与不带 PG 两种形态各一次，读数写进 commit message）；
- 本步列出的锁由红转绿；
- 本步列出的变异**逐条变红，再按字节还原**；
- **push 后报 sha**。

不过门就停下报告。不跳步，不顺手修越界项。

| 步 | 内容 | 翻转 | 变异（每条必须打红） |
|---|---|---|---|
| **C1′** | 按 v5 订正 C1 的锁；本 spec 入库 `docs/specs/llm-access-gate.md` | — | — |
| **C2a** | §2.2 投递原语；§2.4 注入 | L13、L14 | ① core 任一处恢复 `from deps import` → L13 红；② `record_usage` 恢复自建 loop → L14 红；③ 未注册时 `wait=False` 改成阻塞 → L14 红 |
| **C2b** | §2.3 传播归属与载体；§2.9 中间件单出口；executor 注记 | L8、L15 | ① 包装器不做 `copy_context` → L8 红（thread 与 submit 各有 OTEL 关的一条）；② 中间件改为只在鉴权分支设值 → L8 的公开路径条红；③ concurrency 中 import opentelemetry → L15 红 |
| **C3** | §2.5；§2.6；§2.9 装配与配码 | L4、L6、L7、L9、L11 | ① 删掉任一出站方法的 `_before_call` → L6 红；② 无上下文时放行 → L7 红；③ 删掉 `install_llm_gate` → L11、L4 红；④ `llm_error_payload` 去掉 `call_refused` → L4 红；⑤ 配码表删掉 `call_refused` → L9 红；⑥ 审计不再投递 → L9 红；⑦ 审计抛错时向上传播 → L9 红 |
| **C4** | §2.7；§2.8；§2.10（不含 market） | L1、L2、L3、L5 | ① 恢复 `/start` 手抄解析 → L1 红；② 后台线程自行构造 → L2 红；③ 缓存命中时跳过 preflight → L3 红；④ 初始化失败时返回 None → L5 红 |
| **C5** | market 改走 `get_llm()`；结构锁；台账 | L10、L12 | ① 新增一个 `LLMAdapter(` → L10 红；② market 改回 `LLMAdapter()` → L10 红；③ `update_api_config` 直接调用 `check_api_allowed` 或 `record_geo_block` → L12 红 |

C5 的台账内容：
- 订正缺陷 37 与 C/B′ 的前提；
- 撤回 1083 条「验不了」的论断；
- 新增缺陷 F3、F4、F6、F8、F9；
- D2 登记进「失败吞成成功」族；
- `ctx_thread` 与投递原语的迁移记录；
- `test_usage_accounting_lock` 的出口识别随 `record_usage` 改动而调整，改动理由和变异证据写进条目。

## 4. 锁

| 锁 | 命题 | 层 | 翻转 |
|---|---|---|---|
| L1 | `/start`：用户无 key、全局可用 → 200，且后台拿到全局实例 | 0 | C4 |
| L2 | 后台线程拿到的 llm 就是请求时解析出的实例（`is` 判等）；签名中没有 `api_config` | 0 | C4 |
| L3 | 缓存热时换成被拦 IP 调用 `get_user_llm` → 拒绝，判据为 `kind == "call_refused"` | 0 | C4 |
| L4 | 活会话持有非白名单实例时，用被拦 IP 发 `/send` → 403，且毒客户端未被触碰 | 0 | C3 |
| L5 | `resolve_llm` 四种情形的表驱动测试，其中初始化失败的 reason 非空 | 0 | C4 |
| L6 | 注册「总拒绝」守卫后，每个出站方法都拒绝，且客户端未被触碰。方法集现算；`_NON_CALL` 双向校验 | 0 | C3 |
| L7 | 无上下文 → `LLMCallerMissing`；在 `system_llm_context()` 内 → 放行 | 0 | C3 |
| L8 | contextvar 在端点、流式响应体、公开路径、`ctx_thread`、`ctx_submit`、嵌套派生、线程内 `asyncio.run` 中都可见，OTEL 开关各一条 | 0 | C2b |
| L9 | ① 最小 app 经 `install_llm_gate` 装配后，拒绝 → 403 且文案为理由；② 审计投递一次，user 与 ip 取自 Caller；③ 审计抛错不影响拒绝，也不阻塞 | 0 | C3 |
| L10 | web/ 中 `LLMAdapter(` 恰为 2 处，分别在 `_make_user_llm` 与 `_make_global_llm` 内。已知盲区：别名绕过 | ② | C5 |
| L11 | 导入生产 app 后，已注册的守卫就是 `geo_call_guard` | 0 | C3 |
| L12 | 生产代码中 `check_api_allowed(` 恰为 1 处且在 `geo_refusal` 内；`record_geo_block(` 恰为 1 处且在 `emit_geo_block_audit` 内；两者都带非空守卫 | ② | C5 |
| L13 | core/ 与 adapters/ 中不 import `deps`、`web`、`routers`（扫描全部 import 形态，包括函数体内的局部 import） | 0（import 语句即事实） | C2a |
| L14 | ① 已注册时 `submit_to_main_loop` 委托给注册实现；② 未注册时两种 wait 的回退语义；③ `record_usage` 经由它投递（spy） | 0 | C2a |
| L15 | `core/concurrency.py` 不 import opentelemetry；OTEL 开启时 telemetry 已注册载体 | 0 | C2b |

## 5. 越界项（只报告，另开议题）

- web/routers 下有 147 处 `except Exception`，只有 2 处放行 LLM 失败。其余路由里的拒绝会变成 500：调用照样被挡住，但状态码错误。与缺陷 38 同族。
- 活会话持有旧 key 实例：`clear_user_llm_cache` 不重建会话。
- `core/embeddings.py` 与 mem0 的出站不经过 adapter，是否需要 geo 约束待定。
- embedding 配置读取有 5 处完全相同的写法，含变体约 8 处。
- `market.at_reply` 等路由的就地异常处理（同第一条）。

## 6. 停止条件

以下任一情况出现，立即停下报告：
- L8 在 `BaseHTTPMiddleware`、流式响应体或 OTEL 开启时不成立；
- 任一变异打不红；
- 全量测试出现本步之外的变化；
- `auto_review_card` 仍有非测试用途，或只剩测试在用；
- `test_usage_accounting_lock` 需要改的地方超出「出口识别」的范围；
- 任何一步需要保留别名或 re-export 才能过。
