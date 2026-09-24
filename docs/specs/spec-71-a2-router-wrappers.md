# Spec 71 / A-2（第一段）：路由不再兜底改抛 500，交给统一出口

> 基线：main `7eb9127`。线上以 PG 为准。只写代码与测试，不涉及部署或服务器操作。
> skill：`@tdd`、`@verification-before-completion`。
> 本段**不碰** `web/routers/chat.py`、`web/routers/history.py`（96 正在改）、`core/chat_engine.py`、`core/group_session.py`、`core/scheduling.py`。这两个路由文件里的 15 处，连同路由锁，留到 96 合入后的收尾段。

## 0. 开工方式

- worktree `cd-role`，分支 `feat/user-role`。先 `git fetch`，再 `git merge --ff-only origin/main`，HEAD 必须是 `7eb9127` 或它的后代。
- **S0**：逐条复核第 1 节。有一条不成立，就停下报告；其余情况直接做完。
- commit message 用英文。改动面内新发现的问题直接修；会和别的线撞车或需要拍板的，停下报告。**不许自行记账。**

## 1. 已查实的约束（坐标均基于 `7eb9127`）

1. **统一出口已经存在**（`web/server.py`）：
   - `_llm_error_handler` 按 `_LLM_ERROR_STATUS` 配码：`call_refused` 403、`incomplete:content_filter` 400、`incomplete:length` 502、`incomplete:insufficient_system_resource` 503，未登记的 502（`:211-217`）；
   - `DistillError` 由 `_domain_error_handler` 配 400（`:203`）；
   - 其余异常走全局处理器（`:266-275`）：返回 500 和 `{"detail": "服务器内部错误，请稍后重试"}`，同时 `logger.exception` 记一条带请求方法和路径的 ERROR，由 `AlertHandler` 发告警。
2. **`HTTPException` 走不到全局处理器**：Starlette 的 `ExceptionMiddleware` 当场就把它处理掉了。所以 `except Exception → raise HTTPException(500, ...)` 这种写法不告警、不上日志面板，前面的 `print` 只写 stdout。
3. **本段范围：33 处**，全部是「`except Exception` 之后改抛 `HTTPException(500, …)`」，都在普通路由里，没有一处在 SSE 生成器里：

   | 文件 | 行号 |
   |---|---|
   | `admin.py` | `:272`、`:409`、`:425` |
   | `auth.py` | `:375`、`:681` |
   | `card.py` | `:63` |
   | `distill.py` | `:712`、`:1240`、`:1254`、`:1280`、`:1340`、`:1389`、`:1519`、`:1535` |
   | `group.py` | `:322`、`:517` |
   | `inter_node.py` | `:57`、`:99`、`:129`、`:159`、`:194`、`:224`、`:254`、`:311` |
   | `market.py` | `:832`、`:859` |
   | `message.py` | `:197` |
   | `text.py` | `:207`、`:261`、`:386` |
   | `voice.py` | `:312`、`:429`、`:494` |

   这些分支里除了 `print`、`traceback.print_exc()`、`import traceback` 和那句 `raise`，**没有任何清理或回滚逻辑**，删掉不会丢行为。
4. **try 的结构**：
   - 25 处的 `try` 只有这一个 `except`，删掉后 `try` 要整体拆掉，块体反缩进；
   - `voice.py:494` 所在的 `try` 带 `finally`，`finally` 保留；
   - 同一个 `try` 里其他分支的处理：`except ValueError → HTTPException(400, str(exc))`（`admin.py:272`、`auth.py:681`、`distill.py:712/1535`）**保留**，那是写给用户看的校验文案，见 `distill.py:703-708` 的注释；`except DistillError: raise`（`distill.py:712/1535`）和 `except HTTPException: raise`（`admin.py:409`、`distill.py:1389`、`group.py:517`、`voice.py:494`），在 `except Exception` 删掉后就成了多余的透传，**一并删掉**。
5. **会被吞成错误状态码的 LLM 失败**：`market.py:852-859`（`at_reply`）直接调用 `llm.chat`，LLM 失败会被改成 500，删掉包装后会得到 403/400/502/503。蒸馏路由（`distill.py`）有没有未包装的 LLM 异常能冒到这一层，**S0 请复核**：如果有，删掉包装后它们也会自动拿到正确状态码，不需要额外处理。
6. **泄漏原文**：`inter_node.py` 的 8 处把 `{exc}` 拼进了 `detail`。对端只看状态码（`web/cross_border_sync.py::forward_delete_to_peer`），不读 `detail`。
7. **Starlette 的全局处理器处理完后总会再抛出一次异常**：默认的 `TestClient(raise_server_exceptions=True)` 会把这次重抛传给测试代码，而不是返回 500 响应。要断言 500 的用例必须用 `raise_server_exceptions=False`。现成的例子：`tests/test_security_authz.py` 的 `client_a_no_raise`（`:680-703`）。
8. **有先例**：`tests/test_identify_failure_channels.py:300-318` 的 `TestIdentifyFamilyRoutesKeepDistillError`，已经把三条识别路由上同样的包装删掉过，写法照它来。

9. **try 块内部现场（逐处读过）**：删掉包装后，**除提示文案以外**会变化的只有「原来被兜成 500 的异常，现在拿到它本来的状态码」：
   - `market.py:852`：`get_user_llm` 在 try 里面，它返回前会调用 `llm.preflight()`（`web/deps.py:120-134`），geo 拒绝在这一步就会抛出 `LLMCallRefused`，现在被兜成 500，改后返回 403。`get_user_llm` 返回 `None` 时 `llm.chat` 会抛 `AttributeError`，这是原有问题，改前改后都是 500，**本段不修**；
   - `admin.py:409`、`distill.py:1389`、`voice.py:494` 的 try 里本来就有 `raise HTTPException(404/400/503)`，原先靠 `except HTTPException: raise` 透传；删掉两个分支后照样透传，**不变**；
   - 其余 29 处 try 里面没有显式的 `raise HTTPException`，调用的存储层 getter（例如 `text.py:207` 的 `get_text_owned`、`voice.py:429` 的 `get_session_voice_ref_owned`）也不抛 `HTTPException`；
   - 蒸馏路由里如果有 `DistillError` 冒到没写透传的 try（`distill.py:1240/1254/1280/1340/1519`），原先是 500，改后是 400。这属于纠正，S0 请复核并在报告里列出。
10. **对端节点不读 `detail`**：`inter_node` 的 8 个接口都是对端推送进来的 POST；本端调对端时只看状态码（`web/cross_border_sync.py::forward_delete_to_peer`，`web/routers/admin.py:115` 先判断 `status_code == 200` 再 `resp.json()`）。所以去掉 `detail` 里的原文，不会影响节点之间的协作。

## 2. 锁定决策

- **做法 A**（Shiyu 已拍板）：删掉包装，交给统一出口，不新增处理器，也不新增异常类型。
- **用户可见的变化**（已接受）：
  - 这 33 处出错时，提示统一为「服务器内部错误，请稍后重试」。其中大多数原本就是「操作失败，请稍后重试」；
  - `at_reply` 的 LLM 失败改为返回正确的状态码和 LLM 对应文案；
  - `inter_node` 不再把异常原文放进 `detail`。
- **本段不加路由锁**。锁会扫到 `chat.py` 和 `history.py` 里还没改的地方而变红，所以留到收尾段和那 15 处一起加上。

## 目标

`web/routers` 下除 `chat.py`、`history.py` 以外的 33 处 500 包装全部删掉。失败时由统一出口配状态码、记带堆栈的 ERROR 并告警，响应里不再出现异常原文。

## 约束

- 只删 `except Exception` 分支，以及因此变得多余的 `except HTTPException: raise` / `except DistillError: raise`；`except ValueError → 400` 一律保留。
- 不改 `web/server.py`，不新增处理器或异常类型。
- 删掉的 `print` 不用改成日志：全局处理器会统一记录。
- 不碰第 0 节列出的 5 个文件。

## 步骤（按依赖顺序，每步独立 commit）

1. **[路由] 删掉 33 处包装**，按文件逐个处理。块体反缩进时只动缩进，不改其中的代码。
   commit：`fix(routers): let the 33 catch-all 500 wrappers fall through to the unified exits`
2. **[测试] 补上守住行为变化的用例**，只写 2 条，放在新文件 `tests/test_router_unified_exits.py`：
   - **at_reply 的 LLM 被门拦下**：让 `llm.chat` 抛出 `LLMCallRefused("<理由>", "<base_url>")`。测试里可以直接 `from adapters.llm_adapter import LLMCallRefused`（先例：`tests/test_live_llm_refresh.py:59`；边界锁只管 core/web/storage，不管 tests）。断言返回 **403**，并且 `detail` 等于那条理由；
   - **inter_node 失败时不泄漏原文**：任选一条（例如 `receive_card_delete`），让存储层抛出带「秘密」字样的 `RuntimeError`。断言返回 500，`detail` 等于统一文案且**不含**原文；`caplog` 里恰好有一条带 `exc_info` 的 ERROR。
   - **不写**「普通路由失败会发告警」的用例：「ERROR 会发告警」和「全局处理器会记 ERROR」已经由 `tests/test_failure_alerting.py` 守住，再写一遍只是重复。
   - **构造方式复用现有写法**：用生产的 `server.app`，加上 `TestClient(raise_server_exceptions=False)`，依赖覆盖照 `tests/test_security_authz.py` 或 `tests/test_identify_failure_channels.py` 里现成的 fixture 和构造函数（能 import 的直接 import），不另写一套 app 装配。
   - 现有用例如果断言了被删掉的文案或 500，按新行为改掉，和这一步放在同一个 commit。
   commit：`test(routers): pin the unified-exit behaviour of the unwrapped routes`
3. **[台账] AGENTS.md**：
   - **71**：状态改为「部分修复」。写明本段修了哪 33 处；剩下的是 `chat.py`、`history.py` 里的 15 处，以及路由锁，等 96 合入后的收尾段做。顺手把「147 处」更正为现数的 129 处，写明口径（`except Exception` 的出现次数）；
   - **119**：残留一节里写的「归 71 + 123」改为「归身份线 71 批」；
   - **80、112**：状态改为「已裁定·不修（SQLite 专属）」，正文不动。
   commit：`docs(ledger): record 71's first cut, and settle 80/112 as SQLite-only won't-fix`

## 测试

- 本地测试库用 `docker compose -f docker-compose.test.yml up -d --wait` 启动。
- **本地只跑受影响的文件**：
  - `tests/test_router_unified_exits.py`、`tests/test_security_authz.py`、`tests/test_identify_failure_channels.py`、`tests/test_failure_alerting.py`；
  - 再用 `git grep -l` 找出引用了本段改动路由的测试文件（按路由路径或被改动函数名搜索），一并跑；在报告里列出这份清单。
- **不跑全量**。合并门是分支 CI。
- **合并前**：先 `git fetch`，再 rebase 到最新的 `origin/main`，分支 CI 变绿后开 PR，由执行方自己合并。合并只做 git 操作，不跑测试，也不等 main 的 CI。

## 验证

- 步骤 1 之后：`web/routers` 下除 `chat.py`、`history.py` 外，「`except Exception` 之后改抛 5xx 的 `HTTPException`」为 **0 处**。用 AST 脚本数，脚本放在仓外，报告里写明口径；`chat.py` + `history.py` 应当仍是 15 处。
- 步骤 2 之后：新增的 2 条用例都是绿的；对账表里的每条变异都能打红。

## 对账表

| 行为变化 | 守它的测试 | 让它变红的变异 | 改后能触发的具体状态 |
|---|---|---|---|
| `at_reply` 的 LLM 被拦下时返回 403，而不是 500 | `test_router_unified_exits` 里的 at_reply 用例 | 在 `market.py` 的 LLM 调用处加回 `except Exception: raise HTTPException(500, …)` | 用户的 LLM 调用被 geo 门拒绝 |
| `inter_node` 失败时不泄漏原文，并留下 ERROR | inter_node 用例（断言 detail，且 caplog 里恰有一条 ERROR） | 加回 `raise HTTPException(500, f"...{exc}")` | 对端节点推来的数据写库失败 |

其余 31 处由收尾段的路由锁统一守住（「ERROR 会发告警」由 `test_failure_alerting.py` 守住）；本段期间没有锁，在报告里如实写明。

## 交付报告

1. S0：第 1 节 10 条逐条复核（成立或不成立，不成立的给出现场坐标）；约束 5 里蒸馏路由的复核结论。
2. 各 commit 的 sha、分支 CI 的 run 链接和结果（passed / failed / xfailed）、PR 号和合并 sha。
3. 验证那一节里 AST 计数的前后值（33 → 0；15 → 15）。
4. 对账表逐行：测试名，以及变异的红 / 绿结果。
5. 本地实际跑了哪些测试文件（清单）。
6. 偏离与事故：没有就写「无」。


## 补充（执行中提出的第 34 处，已裁定）

`web/routers/distill.py:846-850`（`start_distill` 建任务行那一段，`except Exception → HTTPException(503, "蒸馏任务创建失败，请稍后重试")`）**不纳入删除范围**，理由如下：

- 它和那 33 处不同，是**有意设计并且有用例钉住的契约**：落库失败时拒绝启动，返回 503，由 `tests/test_distill_task_api.py:428-448` 的 `TestCStartRefusesOnDBFailure` 守住。同一个 try 里 `affected == 0` 的分支也是显式抛 503，两条路径口径一致。仓里自己的原则就是「别为统一而统一」（`distill.py:703-708`）。
- 它**真正的问题**只有一个：`:849` 那句 `print` 写到 stdout，而 `HTTPException` 走不到全局处理器，所以这次失败**不告警**。

**处理方式：**
1. `:849` 的 `print` 改为 `logger.error("Create distill task row failed; refusing to start: %s", exc, exc_info=True)`，契约（503 + 原文案）不变，`except HTTPException: raise` 也保留。
2. 给 `TestCStartRefusesOnDBFailure` 补一条断言：`caplog` 里恰好有一条带 `exc_info` 的 ERROR。变异是把它改回 `print`，这条用例应当变红。
3. 验证那一节的 AST 口径改为：步骤 1 之后，`chat.py`、`history.py` 以外「`except Exception` 之后改抛 `HTTPException(500)`」为 **0**，改抛 **503** 的留 **1** 处（就是这一处有意保留的契约）。收尾段加路由锁时，这一处用 `policy_table` 登记为豁免，理由写「缺陷 C 的契约：落库失败拒绝启动 → 503，已记 ERROR」。
4. `:839` 的注释（「由下面 except 兜成 503」）**仍然成立，原样保留**。`:1385-1387` 的注释（属主门 404 那条）和 `web/routers/inter_node.py:3` 的模块 docstring 引用了被删掉的机制，按删改后的实际情况改掉。
5. `TestCStartRefusesOnDBFailure.test_save_failure_returns_503_and_no_thread`（`tests/test_distill_task_api.py:427`）的签名加上 `caplog`，补一条断言：恰好有一条 `levelno == ERROR` 且 `exc_info` 非空的记录。这一改动归入步骤 2 的 commit。
