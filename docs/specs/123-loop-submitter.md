@incremental-implementation @search-first @tdd

# 72 线 · 123 + 64 余项 + 113 复发：跨 loop 投递只有一条路

## 目标
没注册投递实现就投递、或在主 loop 线程上阻塞等主 loop，一律当场报错；修掉生产上「主 loop 等自己」卡 15 秒的两处；测试不再借退路过关，其中 3 条名不副实的用例改成真测；驱动过 lifespan 后进程级注册原样还原。

## 已查实的约束（基线 origin/main `7eb9127`；审计方已在本地 PG 上逐条实测，含原型改动后的全量）

**生产侧**
1. 退路：`core/scheduling.py::submit_to_main_loop` 未注册时 `wait=True` → `asyncio.run`（:51），`wait=False` → `create_task` / `asyncio.run`（:56）。
2. 生产注册：`web/server.py:119` `set_main_loop(loop)`；实现 `web/deps.py:216 _submit_to_main_loop` → `:223 run_coroutine_threadsafe` → `:225 fut.result(timeout)`。
3. 生产卡死：`web/routers/chat.py:221`、`web/routers/history.py:313` 在 async 路由里直接调 `engine.load_affinity(data)`（未初始化分支）→ `core/chat_engine.py:618 _save_affinity_state` → `submit_to_main_loop(wait=True, timeout=15)`（:628）。实测：主 loop 线程上调用即阻塞满超时、保存失败、异常被吞。
4. 全仓穷举（AST 扫描 `web/ core/ adapters/`）：能走到 `submit_to_main_loop(wait=True)` 且在 async 函数里被直接调用的，**只有**第 3 条这两处，以及同文件 `:218` / `:310` 的 `initialized=True` 分支（只读不存，当前不卡）。`core/group_session.py:141 / :242` 也直接调 `load_affinity`，但群聊引擎无 `_session_id`，`_save_affinity_state` 直接返回，不投递。其余 16 个投递点全在同步函数里、经 `to_thread` 或后台线程执行。
5. 独立进程：`scripts/smoke_eval_e2e.py:46`、`scripts/integration_check.py:50` 已自行注册投递实现；其余脚本存储为 `None`；`mcp_server/server.py` 在 `system_llm_context()` 下运行，`current_user_id()` 为 `None`（`core/request_context.py:72`），`core/utils.py:79 try_record_usage` 在无用户时直接返回，不投递。**拿掉退路不影响任何独立进程。**
6. 生产关停：lifespan 退出后再投递，`run_coroutine_threadsafe` 对已关闭 loop 报错并记日志 —— 已是显式失败，本步不改。
7. 原型实测：按本 spec 生产侧改动（拿掉退路 + 自等保护 + 两处 `to_thread`）跑全量 → 只有 `test_l14_unregistered_fallback_semantics`、`test_message_backfill::test_C6` 两条变红；自等保护在全量里零误伤。

**测试侧**
8. 退路命中共 21 次，全在 4 个文件（其中 19 次被宽 `except` 吞掉而显示通过）：
   - `tests/test_departure_notice.py` 16 次，源于 :37 `engine._storage = MagicMock()`
   - `tests/test_initial_affinity.py` 3 次，源于 :152 / :179 / :265 / :289 `storage=MagicMock()`
   - `tests/test_message_backfill.py::test_C6`（:501）1 次 —— 路由流程，`_client`（:192）用的是没有 lifespan 的裸 `FastAPI()`，投递实现未注册
   - `tests/test_llm_access_gate.py::test_l14_unregistered_fallback_semantics`（:1141）1 次 —— 本身在测退路
9. 名不副实的用例（退路一拿掉就暴露）：
   - `test_departure_notice.py::TestToAwareUtc::test_gap_with_aware_string / test_gap_with_naive_string`（:172 / :190）：给 `get_session` 设返回值，但引擎调的是 `get_session_unscoped`，且经退路失败被吞 —— 「PG / SQLite 时间串算间隔不抛 TypeError」**从没真测过**；断言 `== ""` 在任何间隔下都成立
   - `test_initial_affinity.py::test_load_affinity_uninitialized_computes_and_saves`（:173）：名字说「落库」，只断言算了初值，没断言写入
   - 其余 departure 用例把存储换成 `None` 后照常通过（已实测），它们不测存储
10. C6 的正解已实测：`_client` 的 app 带一个测试 lifespan（进入时 `deps.set_main_loop(当前运行 loop)`，退出时还原），用 `with` 进入 TestClient —— 拿掉退路后 `test_message_backfill.py` 15 条全绿。这与生产 `server.py:119` 同形。
11. 113 复发：`test_message_backfill.py:652` `async with server_mod._lifespan(_App())` 注册后不还原。复现：`pytest "tests/test_message_backfill.py::test_C9_shutdown_backfills_the_queues" "tests/test_ownership_404.py::TestOneToOneSessionOwnership::test_T2_independent_session_owner_can_chat"` → 4 条 never awaited。l11（`test_llm_access_gate.py:1026`）用 `_snapshot`（:170）自行还原。
12. 64 余项：`_snapshot`（:170）退出时直接 `write(prev)`，不核当前值是否仍是自己装进去的那个。

**S0**：动手前逐条复核 1–12 的坐标与复现；任何一条不成立，停下报告，不自行改方案。

## 约束
- 生产侧只改：`core/scheduling.py`、`web/deps.py::_submit_to_main_loop`、`chat.py:218/221`、`history.py:310/313`；不改 `web/server.py`、关停行为、`group_session.py`（约束 4 已查明不投递）
- 测试侧只新增一个机制：`tests/conftest.py` 里一个异步上下文管理器，进入时快照 `get_loop_submitter` / `get_call_guard`，退出时先核再还原；它有两种用法 —— 包住生产 `_lifespan`（C9、l11），或作为测试 app 的 lifespan 并在其中 `deps.set_main_loop(当前运行 loop)`（C6 的 `_client`）
- 引擎单元测试：不测存储的用例存储传 `None`；真测存储交互的用例用 `AsyncMock` 存储 + 显式注册一个测试投递实现（形状照 `scripts/smoke_eval_e2e.py:34 _run_await`），用例结束还原；不恢复任何退路
- 新发现属本段改动面的直接修；会撞车或需拍板的停下报告；不自行记账

## 步骤（按依赖顺序，每步独立 commit）

### 步骤 1：[core+web] 拿掉退路 + 自等保护 + 修两处卡死
- 先写失败用例：① 未注册时 `submit_to_main_loop` 两种 `wait` 都抛错；② 在已注册主 loop 的协程里直接调 `_save_affinity_state`，立即抛错而不是阻塞（用例超时设短，不得以超时方式变红）。确认红。
- `core/scheduling.py`：未注册时抛 `RuntimeError`（文案：未注册投递实现），删除 `asyncio.run` / `create_task` 分支与 `warnings`；docstring 同步改写
- `web/deps.py::_submit_to_main_loop`：`wait=True` 且当前运行的 loop 就是 `_main_loop` 时抛 `RuntimeError`（文案：主 loop 线程上不得阻塞等主 loop）
- `chat.py:218/221`、`history.py:310/313`：改为 `await asyncio.to_thread(engine.load_affinity, ...)`，两个分支同改
- l14（:1141）改写为断言「未注册即抛错」
- commit：`fix(scheduling): no silent loop fallback, and no blocking wait on the main loop from itself`

### 步骤 2：[tests] 引擎单元测试不再借退路过关
- `test_departure_notice.py:37`：存储改 `None`
- :172 / :190 两条改成真测：`AsyncMock` 存储的 `get_session_unscoped` 分别返回 PG 带时区串与 SQLite 无时区串、时间设为「现在 − 3 小时」，注册测试投递实现，断言算出了间隔且 `departure_notice` 非空（3 小时落在提醒窗口内，见同文件 `test_gap_3h_contains_notice`）
- `test_initial_affinity.py`：不测存储的用例改 `None`；:173 与 :260 roundtrip 改用 `AsyncMock` 存储 + 测试投递实现，断言 `save_affinity_state` **被 await**（不只是被调用）
- commit：`test(affinity): assert the storage round trip for real instead of riding the fallback`

### 步骤 3：[tests] 唯一入口 + C6 + C9/l11 + `_snapshot` 校验
- `tests/conftest.py` 加约束中的上下文管理器
- `test_message_backfill.py::_client`（:192）：app 用它作 lifespan，TestClient 以 `with` 进入（由夹具管理退出）
- C9（:652）、l11（:1026）改为包在它里面；l11 外层 `_snapshot` 那层删掉
- `_snapshot`（:170）退出时先断言 `read()` 仍是自己装进去的 `value`，不是则报错，再还原
- 约束 11 的复现写成用例（C9 之后投递实现与调用守卫等于进入前），先红后绿
- commit：`test(lifespan): one entry that registers and restores the process-wide submitter`

### 步骤 4：[docs] 台账
- 123、64 余项标已修（附 commit）；113 追加「C9（a275beb）触发记录的收敛条件，已收敛为 conftest 唯一入口」；112 按分支 CI 实数如实写（消失标已修，否则写现数）
- commit：`docs(ledger): close 123, 113's recurrence and 64's remainder`

## 验证
- 变异（各一次，跑完还原）：恢复 `asyncio.run` 退路 → 步骤 1 ① 变红；删自等保护 → 步骤 1 ② 变红；:172/:190 的存储改回只设 `get_session` → 步骤 2 两条变红；C9 改回直接 `async with server_mod._lifespan(...)` → 步骤 3 复现用例变红；`_snapshot` 去掉校验 → 对应用例变红
- 本地只跑（Docker PG）：`test_llm_access_gate.py`、`test_message_backfill.py`、`test_departure_notice.py`、`test_initial_affinity.py`、`test_ownership_404.py`、`test_secret_never_plaintext.py`、`test_chat.py`、`test_reunion_greeting.py`
- 约束 11 复现命令 never awaited 计数为 0
- 合并门：分支 CI；报告附 CI 日志中 `never awaited`、`No loop submitter`、`Event loop is closed` 三项计数
- 推送分支、开 PR；**审计通过前不合 main**
- 报告：每步 commit 与 `--stat`、S0 逐条复核结果、先红后绿记录、变异表、测试末行、CI 链接；不报本地全量数字
