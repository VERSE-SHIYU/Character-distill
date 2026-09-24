# Spec 96：会话身份在构造时注入 + 用户时区走「请求入口统一确定」

> 基线：main `7eb9127`（119 已合入；S0 已在此基线复核通过）。线上只跑 PG，一切以 PG 为准。
> skill：`@incremental-implementation`、`@tdd`、`@verification-before-completion`。commit message 用英文。
> 本文件是本段唯一的 spec，后续补充都写进本文件；上下文被压缩后请重读本文件，不要翻会话日志。

## 0. 目标

引擎一造出来就知道自己属于哪个会话、哪个群，之后不可改；用户时区按 Django / GitHub 的成熟做法，在请求入口统一确定、存进用户资料，业务代码直接取「当前时区」，不再由各处往引擎里塞。

方案已由 Shiyu 拍板（依据：Django 6.0 官方文档「Selecting the current time zone」；GitHub REST API「Timezones and the REST API」：`Time-Zone` 头 → 用户最后已知时区 → 默认）。**不重新讨论方案**，执行中发现方案本身走不通，停下报告。

## 1. 已查实的约束（基线 `7eb9127`，S0 已逐条复核通过；开工前若 main 又前进，重核行号，任一条不成立立即停下报告）

**身份（会话 id / 群 id）**
1. `ChatEngine.__init__`（`core/chat_engine.py:107`）无 `session_id` / `group_id` 参数；`:135-137` 把 `_session_id` / `_group_id` / `_user_tz` 初始化为空串。
2. `:153` `if not self._session_id:` 在构造函数里**永远为真**（`:135` 刚设为空串）——注释写「新会话才算初始好感度」，实际每次都算。改成构造注入后此判断会静默变成「续接不算」，**必须改为显式参数**，不能继续用 session id 有无来判断新旧。
3. `TextManager._create_session`（`core/text_manager.py:616`）先造引擎（`:641`）、后生成 id（`:649` `uuid4`），故 id 只能事后塞入。
4. 续接（`web/routers/chat.py:188-196`）与从历史恢复（`web/routers/history.py:253-272`）都是「用一个用完即弃的新 id 造引擎 → `sessions.pop` 改名成原 id」。
5. `/send` 两条路径每次无条件重写会话 id：`chat.py:323`、`:481`（因为 `web/routers/distill.py:1312` `POST /start_session` 造出的聊天引擎 id 一直为空；注意同文件 `:717` 的 `POST /start` 是蒸馏任务启动，与此无关）。
6. 外部写点共 12 行 / 4 文件：`chat.py:214/318/323/477/481`、`history.py:306/319`、`core/group_session.py:181/372`、`scripts/run_agent_eval.py:177/331/370`。
7. 群引擎在 `web/routers/group.py:148`（重建，`group_id` 是 `:70` 的入参）与 `:358`（新建）构造；新建路径的 `group_id` 在 `:379` 才生成，晚于造引擎。

**时区**
8. 时区只活在引擎内存里：库中无任何时区列；`UserClock`（`core/clock.py`）拿不到时区时回退 `DEFAULT_TZ = "Asia/Shanghai"`（`:8`）；`now(tz=None)` 有默认值，`to_user_tz(dt, tz)` 没有。回退值**保留上海**（Django 文档认可「主要用户群的时区」）。
9. `UserClock.now` / `to_user_tz` 共 8 处调用：`chat_engine.py:691/1037/1057/1306/1380/1403`、`distill.py:1408`、`history.py:322`。
10. 群聊从未接收时区：`group.py`、`group_session.py` 零处 `client_tz` / `_user_tz` → 群聊永远用上海时间（本段顺带修掉，属改动面内，不记账）。
11. `client_tz` 现以请求体字段传入：后端 `chat.py:244`（`ChatRequest`）、`:291`、`:317-318`、`:450`、`:476-477`、`:687`、`:692`、`:849`，`distill.py:63`，`history.py:38`；前端 `useAppStore.js:11`（`clientTz()`）与 `:1159/1245/1355/1407/1510/1690` 六处传参。
12. 前端发往本后端的请求头出处是 `web/frontend/src/api/client.js:41` `getAuthHeaders()`；`client.js:183`（SSE）、`:414`、`PrivateMessageChat.jsx:208` 都经由它。**唯一例外**：`client.js:81` 对 `/api/auth/refresh` 的裸 fetch（取 token 之前的请求，无身份，不需要时区，保持不动）。`getAuthHeaders()` 现为 `return token ? { Authorization } : {}`（`:41-44`），**时区头必须加在这个三元之外**，否则匿名 / 公开路径请求不带头。外部请求 `MinePage.jsx:194`、`:238`（nominatim）与 `CharCard.jsx:688`、`ChatArea.jsx:294` 的 `fetch(base64)` 不发往本后端，**不得**加时区头。
13. 请求入口：`web/server.py:321` `AuthMiddleware`，`:384` 设置 `LLM_CALLER`（紧接 `:385` `call_next`）；该上下文已被证明能传到路由、SSE 流与 `ctx_thread` / `asyncio.to_thread` 派生的后台线程。`:311` `_maybe_update_last_active`（`:377` 调用，60s 节流 + `asyncio.ensure_future` 异步写）是「请求入口顺手更新用户字段」的现成先例。
14. 中间件拿到的用户行来自 `get_user_by_id`（`storage/postgres_store.py:2195`），**显式列举列**（不是 `SELECT *`），新列必须加进去。
15. CORS（`server.py:291`）`allow_headers=["Authorization", "Content-Type"]`，不含 `Time-Zone`，跨域预检会拦下新头。
16. PG 迁移最大号 `027`，新迁移为 `028`。
17. 前端测试命令：`web/frontend/package.json` `"test": "vitest run"`。

18. 删掉请求模型里的 `client_tz` 安全：全仓无 `model_config` / `extra=` 配置，pydantic 2.13.5 默认 `extra='ignore'`（S0 运行期实证），旧前端继续发送只会被静默丢弃，不会 422。

## 2. 红线（违反任一条即停下报告，不得自行决定）

1. **必须复用**：`LLM_CALLER` 同一条上下文传播通道（新 ContextVar 与它并列设置，不另建传播机制）；`_maybe_update_last_active` 的写法（节流 + 异步写）；`UserClock` 作为唯一取时间入口；`getAuthHeaders()` 作为唯一请求头出处。
2. **不许新建**：新文件（迁移 `028`、其 SQLite 孪生 `095` 与测试文件除外）/ 新类 / 新中间件 / 新依赖 / 新配置项 / 新台账条目 / 时区设置页。
   （`095` 是 2026-09-24 拍板的例外：两侧真库的列集锁明文不许开豁免，只加 PG 列必红。`storage/migrations/README.md` 与 `AGENTS.md` 的「不为它做迁移」已同步改成「只加列时补一份同语义孪生迁移」。）
3. **不许**用 IP 推时区；不许在 `Caller` 里加时区字段（`Caller` 是身份，时区不是）。
4. 净删优先：不许留 `client_tz` 兼容分支、不许留 `_user_tz` 读取兜底、不许留「先生成再改名」作为后备路径。

## 3. 步骤（按依赖顺序，每步独立 commit）

### 步骤 1 [db] 用户表加时区列
- `storage/migrations_pg/028_users_timezone.sql`：`users` 加 `timezone TEXT NOT NULL DEFAULT ''`（空串 = 未知）。
- `get_user_by_id`（`:2195`）的列举加上 `u.timezone`；`StorageBase` 与 PG 实现加 `update_user_timezone(user_id, tz)`。SQLite 只保接口一致、能跑，不补测试。

### 步骤 2 [core] 「当前时区」上下文
- `core/clock.py` 加一个模块级 `ContextVar`（当前时区），`UserClock.now(tz=None)` / `to_user_tz(dt, tz=None)`（后者需补默认值）在 `tz` 未给时读它，读不到回退 `DEFAULT_TZ`。显式传 `tz` 的行为不变。
- 定义放在 `core/clock.py`，因为它是「用户本地时间的唯一抽象来源」；**不**放进 `core/request_context.py`。

### 步骤 3 [web] 请求入口统一确定时区
- `AuthMiddleware`（`:384` 旁）：按 GitHub 顺序确定本次请求时区——`Time-Zone` 请求头（能被 `ZoneInfo` 解析才算数）→ 用户已存 `timezone` → 不设（由 `UserClock` 回退）；设进步骤 2 的 ContextVar。
- 请求头有效且与已存值不同 → 按 `_maybe_update_last_active` 的写法异步更新 `users.timezone`。匿名 / 公开路径不写库。
- CORS `allow_headers` 加 `"Time-Zone"`。
- 前端 `getAuthHeaders()` 在 token 三元**之外**加 `Time-Zone: Intl.DateTimeFormat().resolvedOptions().timeZone`（取不到就不加），与 `Authorization` 合并返回；实现从 `useAppStore.js:11` `clientTz()` 移到 `client.js` 一处，不写第二份。`client.js:81` refresh 请求不动。

### 步骤 4 [清理] 删掉旧的时区传递
- 删 `ChatEngine._user_tz`（`:137`）；`chat_engine.py` 6 处、`history.py:322`、`distill.py:1408` 改为 `UserClock.now()` / `to_user_tz(dt)`，不传时区。
- 删后端 `client_tz` 字段及其写点（`chat.py:244/291/317-318/450/476-477/687/692/849`、`distill.py:63`、`history.py:38/318-319`）与前端 6 处传参（`useAppStore.js:1159/1245/1355/1407/1510/1690`）；`clientTz()` 已移到 `client.js`，store 里的定义删除。
- 判据：`git grep -n "client_tz\|_user_tz" -- web core` 零命中。

### 步骤 5 [core] 会话 / 群身份在构造时注入
- `ChatEngine.__init__` 在 `*` 之后加 keyword-only `session_id: str`、`group_id: str = ""`、`is_new_session: bool`；存为只读（下划线字段，外部不再赋值）。`:153` 改判 `is_new_session`。
- `_create_session` 加 keyword-only `session_id: str | None = None`：给了就用，不给就在**造引擎之前**生成；返回该 id。
- 续接（`chat.py:188-196`）与恢复（`history.py:253-272`）直接传原 `session_id`、`is_new_session=False`，删除 `sessions.pop` 改名；删 `chat.py:214/323/481`、`history.py:306`。
- 群：`group.py:358` 新建路径把 `group_id` 生成（`:379`）提前到造引擎之前；两处造群引擎（`:148`、`:358`）都传 `group_id`；删 `group_session.py:181/372`。
- `scripts/run_agent_eval.py` 三处改为构造时传 `session_id=`。
- 判据：`git grep -n "\._session_id = \|\._group_id = \|\._user_tz = " -- web core scripts` 零命中；`git grep -n "sessions.pop(new" -- web` 零命中。

## 4. 对账表

| 行为变化（含连带效果） | 守它的测试 | 让它变红的变异 | 改后能触发的具体状态 |
|---|---|---|---|
| 请求带合法 `Time-Zone` 头 → 本次请求 `UserClock.now()` 用该时区，并写入 `users.timezone` | 中间件测试：带头请求后读 ContextVar 与库中值 | 删掉写库那行 / 不设 ContextVar | 用户换设备或出差后的第一次请求 |
| 不带头 → 用已存时区；都没有 → 上海 | 同上两格 | 让已存值不参与 | 服务重启后的第一次请求 |
| 非法时区名的头被忽略，不写库 | 带 `Time-Zone: Mars/Base` 的请求 | 去掉 `ZoneInfo` 校验 | 客户端伪造 / 异常值 |
| 群聊用用户时区（原先恒为上海） | 群聊路径下 `UserClock.now()` 取到请求时区 | 群路径不经中间件上下文 | 非上海时区用户进群聊 |
| 后台线程（好感度评估）拿到请求时区 | `asyncio.to_thread` 内读 ContextVar | 改用裸 `run_in_executor` | 每轮对话后的后台评估 |
| `/start` 造出的引擎立即有会话 id | 造引擎后直接断言 id 非空 | 恢复「先造后生成」 | 新会话首条消息之前 |
| 续接 / 恢复不再经过「新 id → 改名」 | 续接后 `sessions` 只有原 id、没有临时 id | 恢复 `sessions.pop` 路径 | 服务重启后打开旧会话 |
| 续接的会话不重算初始好感度；新会话照算 | 两条：`is_new_session=True/False` | 恢复 `if not self._session_id` 判据 | 续接旧会话 |
| 群引擎构造即有 `group_id` | 造群后断言每个引擎的群 id | 恢复 `group_session` 里的事后赋值 | 新建群聊 |
| 发往本后端的请求带 `Time-Zone` 头（含未登录时），`/api/auth/refresh` 与外部请求不带 | vitest：有 token、无 token 两种情况下 `getAuthHeaders()` 都含该头 | 删掉那行 / 把它放回 token 三元里 | 所有 API 请求，含公开路径轮询 |

每条锁给出红源（改前先跑红）+ 变异读数。

## 5. 测试

- 本地只跑受影响的测试文件 + `cd web/frontend && npm test`；库用 Docker 起的测试 PG（按 `tests/conftest.py` 的方式）。
- **不跑本地全量**，报告里不出现本地全量数字。合并门是分支 CI。
- 需 Shiyu 手动确认一项：浏览器开发者工具里，任一 API 请求的请求头含 `Time-Zone`，且值是本机时区名。

## 6. 记账

不新增台账条目。本段改动面内的新发现直接修，写进交付报告；会撞车或需要拍板的，停下报告。台账 96 状态在最后一个 commit 改为「已修」，附本段 commit subject（未合入 main 前不写 sha）。

## 7. 交付报告

1. S0 已完成（`7eb9127`）。开工前若 main 又前进，merge 后只报行号有变的条目。
2. 每步：commit subject、改动文件、第 4 节对应行的测试名与变异红 / 绿读数。
3. 第 3 节两条 `git grep` 判据的现跑读数。
4. 分支 CI run 链接与 gate 结果；CI 绿后自己开 PR 并 `gh pr merge --merge`（禁止 squash / rebase），报 main 新 sha。合并只做 git 操作。
