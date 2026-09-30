# Spec profile-outbox：用户资料改走跨境 outbox，并补发存量

> 放 `docs/specs/profile-outbox.md`；附件放 `docs/specs/artifacts/`。补充写进本文件末尾「补充」。
> 遵守 `docs/specs/SPEC-STANDARD.md`。本 spec 只写代码与测试，**不含任何部署 / 服务器操作**。

## 头部

| 项 | 值 |
|---|---|
| 前置 | 交接文档 `docs/specs/HANDOFF-2026-09-30.md` 第 0 件（在分支 `claude/adoring-newton-qnmgo4`） |
| 分支起点 | main `5249fa58`，再合入 `origin/claude/adoring-newton-qnmgo4`（`1fed5ae2`） |
| 参考补丁 | `docs/specs/artifacts/profile-outbox.patch`，sha256 `f17e1dbf428dd07e6a18334d3b9fde2300f3234cb125f3d30db47e49d590d230`，基线 = 上一行的合并树 |
| 变异驱动 | `docs/specs/artifacts/mutate_profile_outbox.py`，sha256 `38a85d4f09b6b28dfc7d13ecff04265e34e0902f0fe0c4107ed18f057fbbb1b2（审计后重写，原 `ed72c249961b…` 见「补充 1」）` |

## 要解决的问题

注册路由把函数 `node_region` 当地区传给 `forward_user_profile_to_peer`，签名序列化抛错被吞，资料从没发出去。对端 `remote_user_profiles` 没有这些人，对端用户给他们发私信报 404「用户不存在」。分支上的一词修复只管以后的注册，而且只发一次、无重发；存量用户没人补。线上核查（2026-09-30，只读）：SZ→SG 缺 3 人；SZ 上有 1 个 `sg-singapore` 用户是演示账号 offerPass 的镜像（与 SG 同 id），不应由 SZ 对外宣告。

## 已查实的约束（坐标 @ main `5249fa58`，S0 逐条复核）

1. `web/routers/auth.py:437` `home_region = node_region()`；`:444-449` 注册后 best-effort 调 `forward_user_profile_to_peer`，`:447` 第三个实参是函数 `node_region`（分支 `1fed5ae2` 已改为 `home_region`）。
2. 全仓建用户只有 `auth.py:438` 一个调用点 → `storage/postgres_store.py:2192` `create_user`，其中 `:2199` 在事务里 `INSERT INTO users`。
3. 同步字段（id / username / home_region / avatar_data）在 PG store 里只有两处写入，见下文「全量扫描原文」：`:2199`（建用户）与 `:2633`（`update_user_avatar`，路由 `auth.py:754`）。
4. 接收端 `web/routers/inter_node.py:261` `/user/sync` 在 `:287` 调 `upsert_remote_user_profile`：重复收同一份资料无副作用。
5. 发件箱表 `storage/migrations_pg/009_delete_outbox.sql`：`payload TEXT DEFAULT ''`（`:17`，可空）、`UNIQUE(op_type, target_id)`（`:20`）。
6. 补发循环 `web/cross_border_sync.py:295` `_resync_once`：DM（`:310`）→ 卡片（`:324`）→ 发件箱（`:338`，`limit=100`，`postgres_store.py:4655` `ORDER BY created_at ASC`）；确认后 `remove_delete_propagation` 按 id 删（`postgres_store.py:4664`；声明 `storage/base.py:752`，SQLite `sqlite_store.py:5487`）。
7. 循环 `cross_border_sync.py:353`：每轮跑完再 `sleep(60)`（`:356`），外层无 try；由 `web/server.py:141` 建成后台任务。`forward_delete_to_peer` 的签名调用 `create_auth_header` 在 try 之外 → 签名抛错会让 `_resync_once` 抛出、后台任务终止。
8. 单进程：`web/server.py:736` `uvicorn.run` 未传 `workers`。
9. 节点地区唯一来源 `core/node.py:16` `node_region()`。PG 迁移只能写死 SQL、拿不到本节点地区 → 「只发本区用户」的过滤只能放在发送方。
10. 迁移账本 `postgres_store.py:179` `_run_migrations`：每份文件跑在自己的事务里、只跑一次；现有最大号 `029_comment_likes.sql`。`AGENTS.md:52`：SQLite 只在 PG **新增列**时补孪生迁移；本 spec 不加列 → 不补。
11. `scripts/backfill_users.py` 已存在：手动脚本，对全部用户调旧签名的 `forward_user_profile_to_peer`，**不分地区**（会把 offerPass 从 SZ 发到 SG）、无重发。本 spec 删除它，由迁移 030 取代。
12. 测试库：`docker-compose.test.yml` 端口 `55432`、库 `charsim_test`、数据目录挂 tmpfs。与开发库 5432 不冲突。

**全量扫描原文**（`git show 5249fa58:storage/postgres_store.py | grep -n "INSERT INTO users\|UPDATE users SET"`，去掉与同步字段无关的列）：

```
2199: INSERT INTO users (id, username, username_lower, email, email_verified, home_region) ...
2281: UPDATE users SET {set_clause}      -- set_user_privacy，白名单只含 4 个可见性列
2296: UPDATE users SET presence_visibility
2323: UPDATE users SET email ...
2417: UPDATE users SET last_login_at
2616: UPDATE users SET <user_parts>      -- 只含 embedding_key / embedding_region
2633: UPDATE users SET avatar_data = $1 WHERE id = $2
2669/2693/2705/2717: banner_data / bio / timezone / nickname
```

→ 只有 2199 与 2633 动同步字段；昵称不在同步字段内（隐私政策 3.2(1)）。

**路径上已有机制**（本 spec 往发件箱段加东西）：

| 机制 | 计时 / 计数起点 | 依赖前提 | 本 spec 是否改变前提 |
|---|---|---|---|
| 60 秒循环 | 上一轮跑完后开始 sleep | 单进程 | 否 |
| 每段 100 条、按 `created_at` 升序 | 每轮重新取 | — | 否（规模见下） |
| httpx 10s 超时 | 每次请求 | 串行 | 否 |
| `UNIQUE(op_type, target_id)` + `DO NOTHING` | 入队时 | 删除类 payload 不变 | **是**：资料行会被 `DO UPDATE` 换版本号 → 确认后按 id 删会丢新改动 → 同一 spec 改为「id + 发出时的 payload」条件删除 |
| 确认后删行 | 对端 200 | 行在发送途中不变 | 同上 |

**规模表**（线上 2026-09-30 实查）：

| 数据源 | 数量 | 策略 |
|---|---|---|
| SZ `users` | 11（本区 10） | 一轮 ≤100，一轮发完 |
| SG `users` | 14（本区 14） | 同上 |
| 对端宕机最坏一轮 | 14 × 10s = 140s | 循环串行，不重叠，只是晚一轮 |

**通道 × 执行上下文 × 守它的测试**：

| 通道 | 上下文 | 测试 |
|---|---|---|
| 注册 → `create_user` 入队 | async 路由协程，PG 事务 | `test_registration_queues_and_the_loop_sends_the_profile`、`test_failed_create_user_leaves_no_profile_row` |
| 改头像 → `update_user_avatar` 入队 | async 路由协程，PG 事务 | `test_avatar_change_requeues_and_unknown_user_does_not` |
| 迁移 030 入队 | 启动时 `_run_migrations`，独立事务 | `test_backfill_migration_*`（2 条） |
| 补发循环发送 / 删行 | lifespan 后台协程 | 其余 6 条 |

## 改动（全部在参考补丁里）

1. `storage/base.py`：新增常量 `USER_PROFILE_OP = "user_profile"`，注释写明 payload 是版本号；`remove_delete_propagation(id, payload)` 改为条件删除，docstring 写明原因。
2. `storage/postgres_store.py`：
   - 模块级 `_enqueue_profile_sync(conn, user_id)`：`INSERT ... VALUES (op, id, gen_random_uuid()::text) ON CONFLICT DO UPDATE SET payload = EXCLUDED.payload`；
   - `create_user` 在建用户的同一事务里调它；
   - `update_user_avatar` 改为事务，更新到行（复用 `_parse_rowcount`）才入队；
   - `remove_delete_propagation` 改为 `WHERE id = $1 AND payload IS NOT DISTINCT FROM $2`（payload 列可空）。
3. `storage/sqlite_store.py`：只对齐 `remove_delete_propagation` 签名，函数体不变（SQLite 已放下）。
4. `web/cross_border_sync.py`：
   - 抽出 `_post_to_peer(endpoint, body, what)`，签名放进 try，失败日志带 op_type / target_id / 状态码或异常；`forward_delete_to_peer` 改用它；
   - `forward_user_profile_to_peer(user_id, storage)` 改为「发送时读最新资料」：用户不存在或非本区 → 返回 True 结束该行（INFO 日志）；读库失败 → False；
   - `_forward_outbox_row` 按 op_type 分派；`_resync_once` 删行时传回发出时的 payload。
   - 邀请码两个函数不动（第 3 件的事）。
5. `web/routers/auth.py`：删掉注册后那段 best-effort 调用，换成一行注释。
6. `storage/migrations_pg/030_backfill_profile_sync.sql`：全部用户各入队一条，`ON CONFLICT DO NOTHING`。
7. 删除 `scripts/backfill_users.py`（约束 11）与 `tests/test_register_profile_sync.py`（其断言并入新测试第一条）。
8. `tests/test_cross_border_sync.py`：一处调用补传 payload。新增 `tests/test_profile_outbox.py`（11 条）。
9. 文档：`docs/TECHNICAL_REPORT.md` 跨区域同步一行、`docs/specs/HANDOFF-2026-09-30.md` 第 0 件状态。

**复用**：发件箱表、补发循环、`_parse_rowcount`、`node_region()`、接收端 upsert 全部是现成的；不引入新库（规模 25 人，方案对比已由 Shiyu 拍板 B′ 精简版，出处 microservices.io Transactional outbox + 本仓 `009` 删除同步先例）。

**行为变化**：新注册用户的资料最多约 60 秒后才到对端（原来是注册当时发）。

## 对账表（作者已在干净树上用上面 sha256 的补丁实跑：17 条全红、无存活）

| # | 行为变化 | 守它的测试 | 让它变红的变异 | 改后能触发的状态 |
|---|---|---|---|---|
| M1 | 注册入队 | registration_queues… | 删 `create_user` 里的入队 | 注册后对端收不到 |
| M2 | 入队与建用户同事务 | failed_create_user… | 入队挪到事务外 | 用户名重复时留下孤儿行 |
| M3 | 资料变更换版本号 | change_during_send… | `DO UPDATE` → `DO NOTHING` | 发送途中改头像，新头像丢失 |
| M4 | 改头像入队 | avatar_change… | 删头像入队 | 改头像对端看不到 |
| M5 | 不存在的用户不入队 | avatar_change… | 条件恒真 | 幽灵行 |
| M6 | 条件删除 | change_during_send… | 按 id 删 | 新改动被旧确认删掉 |
| M7 | NULL payload 可删 | acked_row_with_null_payload… | `IS NOT DISTINCT FROM` → `=` | NULL 行永远卡住 |
| M8 | 只发本区用户 | user_not_homed_here… | 去掉地区判断 | offerPass 从 SZ 发到 SG |
| M9 | 已删用户结束该行 | deleted_user… | 返回 False | 行永远重试 |
| M10 | 非 200 留行 | peer_rejection… | 非 200 返回 True | 失败被当成功，资料丢 |
| M11 | 按 op_type 分派 | registration_queues… | 资料行走删除分支 | 资料永远发不出 |
| M12 | 发字符串地区（原缺陷） | registration_queues… | `node_region` 函数入体 | 原缺陷复现 |
| M13 | 发送时读最新资料 | change_during_send… | 头像写死 `""` | 对端头像不更新 |
| M14 | 迁移 030 入队存量 | backfill_…sends_every_local_user_once | 迁移体改空 | 存量不补发 |
| M15 | 迁移不动已有待发行 | backfill_…keeps_a_pending_row_as_is | `DO NOTHING` → `DO UPDATE` | 已有行被改 |
| M16 | 删行传回发出时的 payload | change_during_send… | 传 `""` | 资料行永远删不掉 |
| M17 | 签名失败不带停循环 | signing_failure… | 签名挪出 try | 后台补发任务终止 |

## 步骤

| 步骤 | 内容 | skill | 用途 |
|---|---|---|---|
| S0 | 逐条复核「已查实的约束」1–12（在 main `5249fa58` 上现读），逐条写「成立 / 不成立」；再查 `git config core.autocrlf` 与 main 当前最大迁移号。**任一条不成立，停下报告** | `@search-first` | 先查实再动手 |
| S1 | `claude -w profile-outbox` 开 worktree（从 main），复制主目录的 `.env` / `config.yaml`；`git merge origin/claude/adoring-newton-qnmgo4` | `@git-workflow-and-versioning` | 起点与合并 |
| S2 | 核两个附件的 sha256 与头部一致；`git apply --check` 后 `git apply`。若 main 最大迁移号已 ≥ 030，把 030 顺延到 main 最大号 +1，并同步改测试里的 `_MIGRATION` 路径与变异脚本的 `MIG` | `@tdd` | 落地 |
| S3 | 跑测试（见下），再跑 `python docs/specs/artifacts/mutate_profile_outbox.py`，输出必须是「存活：无」 | `@verification-before-completion` | 验收 |
| S4 | 按审计标准逐文件写结论（改动 12 个文件）；commit（英文 message）、push、开 PR（英文标题与描述） | `@code-review-and-quality` | 自审与交付 |

## 测试

本地只跑受影响文件，库用 `docker-compose.test.yml` 起的 PG（55432）：

```
pytest tests/test_profile_outbox.py tests/test_cross_border_sync.py tests/test_storage_contract_shape.py tests/test_failure_alerting.py tests/test_ledger_substitution_markers.py tests/test_evidence_integrity.py tests/test_rag_evidence_contract.py tests/test_line_endings.py
```

无前端改动，不跑 `npm test`。合并门是分支 CI；合并只做 git 操作。

## 规矩

- 本段改动面内新发现的问题直接修；会与其他线撞车或需要 Shiyu 拍板时停下报告；不自行记账。
- 不做任何部署、不上服务器。

## 补充

### 补充 1：交付审计（2026-09-30，PR #96 @ `1e542c9`）

**逐文件清单**（改动 16 个文件，全部看过）：

| 文件 | 结论 |
|---|---|
| `storage/base.py` | 通过。`USER_PROFILE_OP` 与条件删除签名、docstring 与实现一致 |
| `storage/postgres_store.py` | 通过。入队在 `create_user` 事务内；`update_user_avatar` 按行数入队；条件删除用 `IS NOT DISTINCT FROM`。顺手修：`_enqueue_profile_sync` 前后空行（原 3 行 / 1 行，改为 2 / 2） |
| `storage/sqlite_store.py` | 通过。只对齐签名 |
| `storage/migrations_pg/030_backfill_profile_sync.sql` | 通过。`home_region` 是 `NOT NULL DEFAULT 'cn-shenzhen'`（`005_data_residency.sql:13`），没有空地区被发送方误丢 |
| `web/cross_border_sync.py` | 通过。`get_user_by_id` 的 SELECT 含 `avatar_data` / `home_region`，查不到返回 None；签名在 try 内；资料行排在 `user_purge` 前发送时用户已不存在即丢弃，换版本戳不改 `created_at`、不插队 |
| `web/routers/auth.py` | 通过。旧调用已删，全仓无旧签名调用点 |
| `scripts/backfill_users.py`（删除） | 通过。全仓无引用 |
| `tests/test_cross_border_sync.py` | 通过 |
| `tests/test_profile_outbox.py` | 有问题，已修（见下 2、3） |
| `docs/specs/artifacts/mutate_profile_outbox.py` | 有问题，已重写（见下 1、2） |
| `docs/specs/artifacts/profile-outbox.patch` | 通过。sha256 与头部一致；在 `86f0aaf` 上应用后与 `1e542c9` 在 `docs/specs` 之外逐字节相同 |
| `docs/specs/profile-outbox.md` | 本节 |
| `docs/TECHNICAL_REPORT.md` | 通过 |
| `docs/specs/HANDOFF-2026-09-30.md` | 通过。第 0 件状态已更新 |
| `docs/specs/artifacts/cross-border-outbox.WIP.patch`、`mutate_outbox.WIP.py` | 随合并带入，未审内容（第 3 件的 WIP）。交接文档已写明其中 `user_profile` 部分作废，重基时删掉 |

**发现与处理**（都在本段改动面内，直接修）：

1. **变异驱动只看 pytest 退出码。** 实测：解释器缺 `asyncpg` 时 pytest 在会话开始就崩，原驱动照样输出「17 条全红、存活：无」、退出码 0 —— 「跑不起来」与「红」共用一个信号。重写为复用仓内共享层：改文件 / 跑 / 还原用 `tests/perf/route_facts_mutations.py`（`_apply` / `_run` / `_restore`），基线门与判档用 `tests/lock_coverage.py`（`baseline_verdict` / `refuse_on_baseline` / `outcome`）；每条变异带红源标记，必须红在指定断言上。不放进 `tests/perf/`、不写 `*_red_lines.json`：它是本 spec 的一次性对账，不登记进覆盖闭合元锁。M2 原先的特判钩子（`M2_EXTRA`）并成一条两锚点的编辑。
2. **M14 是坏变异。** 原变异把迁移改成语法错误的 SQL，红源是 `PostgresSyntaxError`，不是「存量没入队」。改为真正的空迁移（`SELECT 1;`），红在「没被 030 入队」。
3. **五条断言没有消息、M17 红在逃逸的 `TypeError` 上**，红源无法指认。给 M2 / M7 / M8 / M13 / M15 / M16 对应的断言补了消息；`test_signing_failure_keeps_the_row_and_the_loop_alive` 把「补发循环不许被带停」写成显式断言。测试的命题不变。

**验收（本地，PG 16 @ 55432，Python 3.12，按锁装依赖）**：
- 重写后的驱动：基线 `11 passed`；M1–M17 全部 RED 且红源命中；三个靶子文件还原后 sha256 逐字节一致；退出码 0。
- 负控：用没装 `asyncpg` 的解释器跑，驱动报「基线不可用」、退出码 3（原驱动此时报「存活：无」、退出码 0）。
- 「测试」一节的文件 + `tests/test_lock_coverage.py` + `tests/test_collection_surface_lock.py`：293 passed、1 failed。失败的是 `test_evidence_integrity.py::TestManifestEntries::test_code_sha_resolves`：本地是浅克隆（`git rev-parse --is-shallow-repository` = true），证据清单里的历史 commit 不在本地；CI 全量历史下该条为绿，与本改动无关。

