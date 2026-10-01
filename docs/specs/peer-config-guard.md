# 节点间配置启动期校验 + 补发遇对端不可用整轮中止（2026-10-01）

> 放进 worktree 的 `docs/specs/peer-config-guard.md`；补充写进本文件末尾「补充」。
> **前置**：`docs/specs/inter-node-v2.md`（main `3923a24`）及其「补充 · 2026-10-01 前置核查结果与方案 A」（PR #102）。
> **执行方**：从最新 origin/main 新开分支 `fix/peer-config-guard`（`--no-track`）。
> **合并时机**：分支 CI 绿，**且**上线第 4 步（两台 `.env` 切 https）验收通过之后。

## 目标
2026-10-01 事故：`PEER_NODE_URL` 必须是 https 这条要求，代码只在每次发送时检查；配置不满足时 app 照常启动、健康检查照常通过，要等运行起来、每 60 秒补发一轮时才暴露，而且按行刷 ERROR（30 分钟内 SZ 990 条、SG 1890 条）。本段只做两件事：
1. **配置前提在启动时由机器检查**：不成立就起不来，报错点名变量；部署被已有的存活门拦下并自动回滚。
2. **对端整体不可用时，本轮补发立即中止**：只记一条 ERROR；单行失败照旧只影响那一行。

## 不做（及理由）
- 不改 `deploy.yml`、不加部署前预检：启动校验加上已有的存活门（约束 4）已经拦得住，再加一层就是重复。
- 不加重试、退避、熔断库：中止已经把黑洞时一轮的耗时从约 3000s 降到约 10s（约束 7），60s 的节拍本身就是退避。
- 不校验 `INTER_NODE_SIGN_VERSION` / `INTER_NODE_ACCEPT_V1` 的取值：不在本次事故的改动面内。
- 节点级失败的判定函数不放进 `peer_client`：目前只有补发一个使用方，`admin.py` 用不上（SPEC-STANDARD 一·4：抽象只为已存在的重复服务）。

## 已查实的约束（基线 `3923a24`；main 现为 `3242498`，`web/` 无差异，S0 复核）
1. **https 只在发送时查**：`web/peer_client.py:42-48`（`build_request` 里 `raise PeerNotSecure`），地址在 `:34` 读取。启动路径上没有任何检查。
2. **启动校验的先例和位置**：`web/server.py:121-123`，在 `_lifespan` 里依次调用 `validate_fernet_key / validate_jwt_secret / validate_inter_node_secret`。异常会经 `:112` 先接好的上报出口进 GlitchTip。
3. **校验必须早于迁移**：PG 迁移是懒加载，第一次用存储时才跑（`storage/postgres_store.py:181-203`，迁移在 `:197`）。`_lifespan` 里第一个碰存储的是 `:139-140`。所以新校验放在 `:123` 之后，失败时库还没迁移，回滚到 PREV_SHA 是安全的。**位置不能往后挪。**
4. **部署门**：`deploy.yml:362` 是存活检查（`/api/health`），不通过就回滚（SG 同构，从 `:490` 起）。`_lifespan` 抛错 → 进程退出 → 存活检查不过 → 回滚。
5. **`PeerNotSecure` 的使用方**：`web/routers/admin.py:192`（映射为 503）、`tests/test_inter_node_v2.py:124`、`web/peer_client.py` 本身。
6. **对端地址只能是节点专属域名**（PR #102 实测）：主域名按线路分流，两台都会解析回本机。所以「对端主机名 = 本机 `INTER_NODE_SELF_HOST`」是启动时就能判定的配置错误。
7. **补发规模**：三段各 `limit=100`（`web/cross_border_sync.py:196,210,224`），单次超时 10s（`peer_client.py:23`），节拍 60s（`cross_border_sync.py:262`）。对端黑洞时，一轮最坏 300 × 10s ≈ 50 分钟；现状是每行各记一条日志。
8. **日志级别按段区分**：`_forward`（`cross_border_sync.py:21` 起）按调用方传入的级别记录；DM 段和卡片段是 WARNING，发件箱段是 ERROR。所以中止那一条日志**必须固定为 ERROR**：否则 DM 段先失败时，整轮只有一条 WARNING，发件箱的告警就再也触发不了。
9. **级别规矩**：`AGENTS.md:56`，ERROR = 数据没存进去或用户请求失败。对端不可用意味着跨境数据没送到，所以记 ERROR。
10. **调用点**：
    - `web/routers/message.py:117`：按返回值决定是否 `mark_message_synced`；
    - `web/routers/market.py:577`：忽略返回值，外层有 `except Exception`；
    - `web/routers/admin.py:102-131`：直接用 `post_to_peer`，非 200 或异常都置 `peer_unreachable`；
    - `web/routers/admin.py:189-192`：直接用 `post_to_peer`，`PeerNotConfigured` / `PeerNotSecure` 映射为 503；
    - `web/cross_border_sync.py:201,215,177-179`：补发的三段。
11. **文档缺口**：`AGENTS.md:3-10`、`DEPLOY.md:28-35` 都没写节点专属域名；`.env.example:51-52` 只写了「https://对端域名」，没说明不能用主域名。

## 设计

### A. 配置校验（`web/peer_client.py`；只有一个解析函数）
- 用 `PeerConfigError(RuntimeError)` **替换** `PeerNotSecure`，不保留别名。`admin.py:192` 和 `test_inter_node_v2.py:124` 跟着改；503 的文案改为「对端节点配置无效」。`PeerNotConfigured` 保持不变（单节点不算错误）。
- 写一个私有函数解析 `PEER_NODE_URL`，`build_request` 和启动校验都调用它：
  - 值为空 → 单节点，直接返回；
  - 否则必须形如 `https://<host>[:port]`：scheme 是 https、有 hostname、没有路径（`/` 可以）、没有 query / fragment / userinfo；
  - 不满足就抛 `PeerConfigError`。
- `validate_peer_config()` 放在 `server.py` 的 `validate_inter_node_secret()` 之后、紧挨着调用。`PEER_NODE_URL` 非空时，还要求：
  - `INTER_NODE_SECRET` 已配置；
  - `INTER_NODE_SELF_HOST` 已配置，且与对端 hostname 不相等。比较时两边都转小写、去掉末尾的 `.` 和端口；本机 host 统一用 `inter_node_auth.self_host()` 读，不另写一份读取逻辑。
- 报错文案要点名变量、说明期望。可以回显 `PEER_NODE_URL` 和 `INTER_NODE_SELF_HOST` 的值，不许回显 `INTER_NODE_SECRET*`。

### B. 补发中止（只改 `web/cross_border_sync.py` 和 `message.py` 一处）
- 发送结果分三种（`Enum`）：
  - `DELIVERED`：已送达；
  - `FAILED`：没送达，保留该行，继续下一行（含单节点未配置的情况）；
  - `PEER_DOWN`：对端整体不可用。
- 判定规则只写在一个私有函数里：
  - `httpx.TransportError`（连接、超时、TLS）→ `PEER_DOWN`；
  - 状态码 401 / 403 / 429 / 502 / 503 / 504 → `PEER_DOWN`；
  - 200 → `DELIVERED`；
  - 其余状态码和其他异常 → `FAILED`。
- `_forward` 返回结果和原因，只对 `FAILED` 按原来的级别记一条日志；`PEER_DOWN` 由调用方处理。四个 `forward_*` 都改为返回这个枚举。
- `_resync_once`：任一段拿到 `PEER_DOWN`，就记一条固定 ERROR：`Peer unavailable, resync round aborted: section=%s path=%s reason=%s`（异常时带 `exc_info`），然后**立即返回**。已经送达的行照常标记或删除，`blocked` 顺序逻辑不变。
- `message.py:117`：改成 `is DELIVERED` 时才标记已同步；`PEER_DOWN` 记一条 WARNING，用户请求照常成功（告警由补发那一轮负责）。`market.py` 不改。

### C. 文档（单独一个提交）
- `.env.example:51-52`：注释改为
  - `PEER_NODE_URL`：「https://<对端节点专属域名>（SZ 填 sg.、SG 填 sz.），不能用主域名：主域名按线路分流，会解析回本机」；
  - `INTER_NODE_SELF_HOST`：「配置了 `PEER_NODE_URL` 时必填，且不能等于对端主机名，启动时校验」。
- `AGENTS.md`「部署拓扑」两台各补一行节点专属域名；`DEPLOY.md:28-35` 的表补同样两行。注明：只配了默认线路、证书 SAN 已包含、节点间只用这两个域名。
- `docs/specs/SPEC-STANDARD.md`「二」加第 7 条并写修订记录（**措辞需 Shiyu 在 PR 中确认**）：

  | 7 | 新代码依赖的部署前置条件（配置键、域名、证书）做成启动期校验，不靠 spec 文字；外部基础设施的事实（DNS、证书、对端地址）在服务器实测并贴原始输出；「上线步骤」的回滚列核对本版迁移是否向后兼容 | spec「上线步骤」每步写明由哪项机器检查把关；启动校验有测试 |

## 规模
| 数据 | 上限 | 来源 | 策略 |
|---|---|---|---|
| 每轮 DM / 卡片 / 发件箱 | 各 100 | 约束 7 | 遇到节点级失败就中止：每轮最多 1 次失败请求、1 条 ERROR |
| 单次请求 | 10s | 约束 7 | 不改 |

## 调用点矩阵（行 = 调用点，列 = 可观测输出 → 测试名）
| 调用点 | 节点级失败时 | 测试 |
|---|---|---|
| `_resync_once` DM 段先失败 | 1 次请求、1 条 ERROR、行全保留、后两段不发 | `test_resync_peer_down_aborts_round_once` |
| `_resync_once` 发件箱段失败 | 前两段已送达的已标记；1 条 ERROR | `test_resync_peer_down_in_outbox_keeps_delivered` |
| `_resync_once` 单行 400 | 只记该行，继续下一行 | `test_resync_row_failure_continues` |
| `message.py` 即时私信 | 200、未标记已同步、1 条 WARNING | `test_dm_send_peer_down_warns_and_succeeds` |
| `admin.py:192` | 配置错误时 503 | `test_inter_node_v2.py` 原用例，改为断言新异常 |
| `_lifespan` | 抛 `PeerConfigError`，存储未初始化 | `test_startup_rejects_bad_peer_config_before_storage` |
| 解析规则 | http / 带路径 / 与本机同名 / 缺密钥 / 缺 SELF_HOST 均拒；正常值通过；空值视为单节点 | `test_validate_peer_config[...]` |

测试名是约定，执行方可以改，但要一一对应，报告里贴出最终名称。

## 对账表
| # | 变异 | 预期红 |
|---|---|---|
| M1 | 删掉 `_lifespan` 里的校验调用 | `test_startup_rejects_bad_peer_config_before_storage` |
| M2 | 把校验调用挪到 `_reconcile_distill_tasks()` 之后 | 同上（断言存储未初始化） |
| M3 | 去掉「不等于本机」的判断 | `test_validate_peer_config[self_host_equal]` |
| M4 | 放行 http | `test_validate_peer_config[http]` |
| M5 | `PEER_DOWN` 时 `continue` 而不是返回 | `test_resync_peer_down_aborts_round_once`（请求数应为 1，不是 300） |
| M6 | 401 归为 `FAILED` | 同上的 401 参数化用例 |
| M7 | 中止日志沿用该段原来的级别 | `test_resync_peer_down_aborts_round_once`（DM 段先失败也必须是 ERROR） |
| M8 | 400 归为 `PEER_DOWN` | `test_resync_row_failure_continues` |

**作者未预跑**：起草环境没有 PG / Docker，这一点不符合 SPEC-STANDARD 三的「作者实跑」，在此写明。由执行方先写测试，在现有代码上确认它们是红的，实现后再逐条做 M1–M8。**任何一条变异存活就停下报告，不合并。**

## 测试
- 本地只跑受影响的文件：`tests/test_cross_border_sync.py`、`test_cross_border_outbox.py`、`test_inter_node_v2.py`、`test_inter_node_auth.py`、`test_admin_peer_disable.py`、`test_lifespan_undo.py`，以及新增的文件。
- 库用 `docker-compose.test.yml` 起 PG。合并前提是分支 CI 绿；合并只做 git 操作。
- 中止用例按约束 7 的真实规模造数据（三段各 100 行），不许缩成几行。
- 日志断言用 `caplog` 并带级别。
- lifespan 用例沿用 `test_lifespan_undo.py` 的启动方式。

## 步骤（3 个提交）
1. **[guard]** 设计 A 及测试（M1–M4）。
2. **[resync]** 设计 B 及测试（M5–M8）。
3. **[docs]** 设计 C。

skill：实现阶段用 `tdd`（每个提交先红后绿）；审计阶段用 `code-review-and-quality`（逐文件下结论）。其余不叠加。

## 上线
- 合并时机见头部。部署照常走 Actions → Deploy（target both）。
- 验收：两台运行 `docker logs character-distill-app-1 --since 10m 2>&1 | grep -cE 'PeerNotSecure|PeerConfigError|Peer unavailable'`，结果为 0；同时 `inter-node accepted` 持续出现。

## 范围规矩
新发现的问题属于本段改动面的，直接修；会撞车或需要 Shiyu 拍板的，停下报告；不许自行记账。

## S0（只读，全部成立才开始编码）
1. `Test-Path docs/specs/peer-config-guard.md`。
2. 在最新 origin/main 上逐条复核约束 1–11。行号漂移就按新行号做；事实不成立就停下报告。
3. 报告第 4 步（两台 `.env` 切 https）的状态：未完成可以先编码，但不合并。

## 补充
### 2026-10-01 实现偏差
- `forward_*` 返回 `(ForwardResult, 原因, path)`：原因用于路由和中止日志，`path` 供 `_abort_resync` 记录。
- 中止日志不带 `exc_info`：原因里已有 `repr(exc)`（含异常类型与消息），不必再带堆栈。
- `INTER_NODE_SELF_HOST` 不去端口：约定只填裸域名（`sz./sg.bookecho-shiyu.cn`），启动校验不 strip 端口。
