# 节点间通信：切到 https + 签名协议 v2（RFC 9421）（2026-09-30，Shiyu 已定：签名用方案 A、传输用 T1、先 https 后签名）

> 放进 worktree 的 `docs/specs/inter-node-v2.md`；以后的补充写进本文件末尾「补充」一节。
> **前置**：`docs/specs/admin-peer-disable.md`（方案甲）已合进 main —— 本段在它引入的
> `web/peer_client.py` 上继续做。
> **执行方**：从合入方案甲之后的 origin/main 新开分支 `feat/inter-node-v2`（`--unset-upstream`）。
> **参考补丁**：`docs/specs/artifacts/inter-node-v2.patch`（sha256 `ba011cd30ca92d77d93d1807aca1da0f68ff5eefa2636de835aef856713f408a`），
> 基线 = `3dbc58b` + `admin-peer-disable.patch`。对账表就是用它预跑的。

## 目标
1. 节点间请求一律走 https（现在两台都是明文 http，私信正文、角色卡内容、用户名/昵称明文跨境）。
2. 签名换成 RFC 9421 标准：签名覆盖方法、目标节点、路径、查询串、请求体摘要；每个请求带 nonce 防重放；支持换密钥。
3. 上线不中断同步：「双收 → 切发 → 停收旧版」，每一步都只改配置、可回滚。

## 方案来源（已拍板，这里只记出处）
| 规范 / 先例 | 出处 | 本 spec 行为 |
|---|---|---|
| 服务间通信要保证传输保密性与完整性；mTLS 需自建 PKI、难点是证书发放/吊销/轮换 | OWASP Microservices Security Cheat Sheet「Service-to-service authentication → Mutual transport layer security」（github.com/OWASP/CheatSheetSeries，`cheatsheets/Microservices_Security_Cheat_Sheet.md`） | B1（只走 https）；不做 mTLS，身份由签名承担 |
| RFC 9421 签名：必须带 `created`；必须签 `@method` 与目标地址；必须带并签名 `Content-Digest`（RFC 9530） | Mastodon 文档 `content/en/spec/security.md`「HTTP Message Signatures (RFC9421)」（github.com/mastodon/documentation）；RFC 9421 §2.2、§7.2 | B2、B3、B4 |
| nonce 防重放 | RFC 9421 §2.3（`nonce` 参数）、§7.2.2 | B5 |
| 政策修订只在「处理目的、方式、范围的实质性变化」时才需显著提示 / 重新同意 | 本站隐私政策 `web/frontend/src/legal/privacy_v3.md` 第 7.1 条（基线 `:191`） | B6：补传输加密一句属如实描述安全措施，不升版本号 |
| 库：`http-message-signatures` 2.0.1（Apache-2.0，2026-01-19 发布，Python ≥ 3.10，唯一运行依赖 `cryptography`，锁里已有 50.0.1） | PyPI | 全部签名 / 验签 |

## 已查实的约束（基线 `3dbc58b`，另标出处的除外）
1. **两台节点间都是明文 http**：2026-09-30 只读核查，SZ / SG 都是 `https=0 http=1`（原始输出记在 `admin-peer-disable.md`「补充」）。
2. **nginx 80 端口也转发节点间请求**：`nginx/nginx.conf:40-55`（`listen 80` 的 server 里 `location /api/inter-node/`）；443 那段在 `:115-128`。两段都是 `server_name _`（`:41`、`:66`）+ `proxy_set_header Host $host`（`:50`、`:124`）。
3. **Host 头不可信**：因为约束 2，nginx 原样透传请求方自己写的 Host。所以接收端算 `@authority` 必须用**本节点配置**的对外域名（新配置 `INTER_NODE_SELF_HOST`），不能用请求头。沙箱实测：信 Host 头时，「发给 A 的请求原样送到 B」签名照样通过（变异 V4）。
4. **现行签名**（`web/inter_node_auth.py:52-56` `_sign`）只覆盖「时间戳 + 请求体」，±30s，无 nonce、不绑路径 → 现有 `/card/delete`、`/dm/retract`、`/user/purge` 三个接收端都只读 `target_id`，彼此之间可挪用签名（`web/routers/inter_node.py:116`、`:143`、`:229`）。
5. **库的四个缺省值 / 行为必须显式处理**（读 `http_message_signatures/signatures.py` 源码 + 沙箱实测）：
   - `verify(max_age=...)` 缺省 **1 天**（`signatures.py:231`）→ 显式传 30 秒；
   - `HTTPMessageVerifier.max_clock_skew` 缺省 **5 秒**（`:138`）；两台实测时钟读数差 **8 秒**（含 ssh 发起时差，样本 = 2026-09-30 一次读数）→ 设 30 秒，否则时钟领先的一台发出的正常请求会被判「来自未来」；
   - 验签按**请求自己声明**的覆盖清单来验（`_verify_one` 用 `list(sig_input)`），**不检查必须覆盖哪些** → 验签通过后由我们核一遍必需组件；
   - **不核对 `Content-Digest` 与请求体是否一致**（实测：换请求体、沿用旧摘要头，库照样验过）→ 我们自己算摘要比对；
   - 库按大小写敏感的方式取 `Signature-Input` 头 → 传给它的头要用它自带的 `CaseInsensitiveDict`。
6. **6 份手抄的发送代码**：`web/cross_border_sync.py:20/61/125/182/220/252`，各自 `os.getenv("PEER_NODE_URL")` + `create_auth_header` + `httpx.AsyncClient(timeout=10)`。方案甲已抽出 `web/peer_client.py::post_to_peer`（管理后台两处在用），本段把这 6 份也迁过去。
7. **三类转发没有补发**：邀请码新增（`cross_border_sync.py:182`）、邀请码删除（`:220`）、用户资料（`:252`），docstring 写明 no retry —— 对端拒收一次就丢。私信、卡片、删除（`:295` `_resync_once`）有 60 秒一轮的补发。⇒ **上线顺序必须保证切换期间对端一次都不拒收**：先让两台都能收 v2（双收），再切发送。
8. **`.env.example` 里没有 `PEER_NODE_URL`**（`:49-50` 只有 `INTER_NODE_SECRET`、`PEER_NODE_IP`）；app 容器用 `env_file: .env`（`docker-compose.prod.yml:116`），新增变量不用改 compose。
9. **密钥指纹函数已有**：`core/embeddings.py::key_fingerprint`，但该模块顶层 `from chromadb...`（`:10`）。本段把函数挪到 `core/fingerprint.py`，三个调用方（`embeddings.py`、`indexing_service.py:14`、`memory_manager.py:298`）改 import，不留转发。
10. **接收端 10 个接口各复制了一份「解析 + 验签」**（基线 9 个 + 方案甲 1 个）。本段收成 `_verified_payload` 一处，v2 的摘要必须算在**原始字节**上，所以先 `request.body()` 再解析。

## 全量扫描（基线 `3dbc58b`，原始输出）
```
$ git grep -n "PEER_NODE_URL" 3dbc58b -- '*.py' ':!tests'
web/cross_border_sync.py:29 / :68 / :140 / :189 / :225 / :263 / :302（docstring）/ :304
web/routers/admin.py:70（docstring）/ :98
$ git grep -n "create_auth_header(" 3dbc58b -- web core scripts mcp_server
web/cross_border_sync.py:42 / :99 / :156 / :199 / :232 / :275
web/inter_node_auth.py:8（docstring）/ :59（定义）
web/routers/admin.py:106
$ git grep -n "^@router.post" 3dbc58b -- web/routers/inter_node.py
:23 /dm/receive  :59 /card/receive  :98 /card/delete  :125 /dm/retract  :152 /invite-code/receive
:184 /invite-code/delete  :211 /user/purge  :238 /admin/users  :261 /user/sync
（方案甲另加 /admin/user-disabled）
$ git show 3dbc58b:nginx/nginx.conf | grep -n "server_name\|location /api/inter-node\|proxy_set_header Host\|listen"
40: listen 80;  41: server_name _;  45: location /api/inter-node/ {  50: proxy_set_header Host $host;
65: listen 443 ssl http2;  66: server_name _;  115: location /api/inter-node/ {  124/140/152: proxy_set_header Host $host;
```
本段之后：`PEER_NODE_URL` 只在 `web/peer_client.py` 读；`create_auth_header(` 只在 `peer_client.py` 调；`verify_auth_header` 只在 `inter_node_auth.verify_inter_node_request` 调。

## 路径机制清单
| 机制 | 位置 | 计时/计数起点、前提 | 本段是否改变前提 |
|---|---|---|---|
| nginx 对端 IP 白名单 + WAF/CC | `nginx.conf:115-121` | 按 `/api/inter-node/` | 否；删掉 80 端口那段后，443 这段是唯一入口 |
| 签名时间窗 | v1 ±30s（`_MAX_AGE_MS`）；v2 `MAX_AGE` 30s + `CLOCK_SKEW` 30s | 从发送端 `created` 算 | v2 新增；时钟差实测 8s，在窗口内 |
| nonce 登记 | 新表 `inter_node_nonces`，保留 120s | 验签通过后登记；每次登记顺带删 120s 前的行 | 新增；保留时长必须 ≥ 有效期 + 偏差（60s），测试钉住 |
| 补发循环 | `_resync_once` 每 60s | 私信、卡片、删除各自独立 | 否；只是发送方式换成 `post_to_peer` |
| 对端 HTTP 超时 10s | `peer_client.TIMEOUT_S` | 从发请求起算 | 否 |
| 应用层限流 | 仅方案甲那个接收端 30/分 | — | 否 |

### 通道 × 执行上下文 × 守它的测试
| 通道 | 上下文 | 测试 |
|---|---|---|
| 6 个 `forward_*` → `_forward` → `post_to_peer` | async 协程（补发循环 / 请求 loop） | `tests/test_cross_border_sync.py`（改打桩位置后全部保留） |
| 管理后台 → `post_to_peer` | async 协程 | `tests/test_admin_peer_disable.py` |
| 接收端 10 个接口 → `_verified_payload` → `verify_inter_node_request` → nonce 表 | async 协程 | `tests/test_inter_node_v2.py` |
不涉及同步线程。

## 规模表
| 数据源 | 上限 | 策略 |
|---|---|---|
| nonce 表行数 | 120 秒内到达的节点间请求数。补发每轮最多私信 100 + 卡片 100 + 删除若干，按两轮计也在千行以内 | 每次登记顺带删旧行，有 `created_at` 索引 |
| 单次请求 | 超时 10s | 超时即失败；有补发的三类下一轮再发，无补发的三类见约束 7 |
| 跨境 https 耗时 | **待核**（见「运维前置核查」的 curl 耗时） | 必须明显小于 10s |

## 设计
- **`web/inter_node_auth.py`**：保留 v1；新增 v2 —— `sign_request_v2(httpx.Request)`（加 `Content-Digest`，签 `@method @authority @path @query content-digest`，带 nonce、`alg`，keyid = 密钥指纹）；`verify_inter_node_request(request, body, payload, storage)` 是接收端唯一验签入口：带 `Signature-Input` 按 v2 验（摘要 → 签名 → 必需组件 → nonce 登记），否则按 v1 验（`INTER_NODE_ACCEPT_V1=0` 时拒）。
- **`web/peer_client.py`**：唯一出站出口；`PEER_NODE_URL` 不是 https 抛 `PeerNotSecure`（不降级）；`INTER_NODE_SIGN_VERSION` 选签名版本。
- **`web/cross_border_sync.py`**：6 个 `forward_*` 只留「载荷 + 日志文字」，发送走 `_forward`（非 200 与异常都按级别记一条，带状态码 / 异常；不记邀请码本身）。
- **`web/routers/inter_node.py`**：10 个接口改调 `_verified_payload`；每次接受记 INFO `inter-node accepted: path=… version=N`，拒绝记 WARNING —— 观察期就数这两行。
- **存储**：`claim_inter_node_nonce(nonce, *, keep_seconds) -> bool`（base + PG + SQLite）；PG `030_inter_node_nonces.sql` + SQLite 孪生 `097`（表集锁要求两侧相等）。
- **nginx**：删 80 端口那段 `/api/inter-node/`；443 段注释里的 `curl http://` 改 https。
- **隐私政策**（Shiyu 2026-09-30 已定要补）：`privacy_v3.md` 第 3.3 节（基线 `:124`）在「节点间传输鉴权」前加一条「节点间传输加密：所有跨节点请求经 TLS（HTTPS）加密传输」，「最后更新日期」（`:3`）改为 2026 年 10 月。**不升版本号**：`web/legal_versions.py` / `versions.js` 的版本号一改，注册接口就会要求所有人按新版本重新同意（`auth.py:422`），而按 7.1 条这次不属于实质变化。这句话必须在 https 真的生效后才对外可见 —— 它随第 1b 步部署，而第 1a 步（切 https）在它之前完成。
- **新配置**（`.env.example` 补齐）：`PEER_NODE_URL`（https）、`INTER_NODE_SELF_HOST`、`INTER_NODE_SIGN_VERSION`（缺省 1）、`INTER_NODE_ACCEPT_V1`（缺省 1）、`INTER_NODE_SECRET_PREV`（换密钥时用）。
- **依赖**：`requirements.in` 加 `http-message-signatures>=2.0.1`，按文件头的配方（带 `--constraint requirements.txt`）重锁；实测 131 个包一个不动，只多这一条，`cryptography` 多一条 via 注解。

## 步骤（5 个 commit）
1. **[deps]** `requirements.in` / `requirements.txt`。
2. **[storage]** 两条迁移 + 次序表登记 + `claim_inter_node_nonce` 三处；`core/fingerprint.py` 挪函数 + 三处 import。
3. **[web]** `inter_node_auth.py` v2、`peer_client.py`、`cross_border_sync.py` 迁移、`inter_node.py` 共用验签、`admin.py` 加 `PeerNotSecure → 503`。
4. **[deploy]** `nginx/nginx.conf`、`.env.example`、`web/frontend/src/legal/privacy_v3.md`（只动第 3 行日期与第 3.3 节一行）。
5. **[tests]** 新增 `tests/test_inter_node_v2.py`（21 条）；改 `test_cross_border_sync.py`（打桩改到 `peer_client._client` + MockTransport、地址改 https，断言内容不变）、`test_admin_peer_disable.py`（地址改 https）、`test_router_unified_exits.py`（打桩对象改为 `verify_inter_node_request`）。

## 调用点矩阵
| 调用点 ＼ 输出 | 正路通过 | 挪路径 | 挪节点 | 换请求体 | 重放 | 时间窗 | 缺组件 | 换密钥 | v1 双收/停收 | 非 https |
|---|---|---|---|---|---|---|---|---|---|---|
| 接收端（任一接口） | `test_v2_signed_request_is_accepted`、`test_v2_write_carries_non_ascii_body_intact` | `test_path_swap_is_rejected` | `test_authority_swap_is_rejected`、`test_v2_refused_when_self_host_unset` | `test_body_swap_with_stale_digest_is_rejected`、`…recomputed_digest…` | `test_replay_is_rejected` | `test_time_window[±20/40/-60]` | `test_missing_required_component_is_rejected` | `test_unknown_key_is_rejected`、`test_previous_key_is_accepted_during_rotation` | `test_v1_still_accepted_by_default`、`test_v1_rejected_once_retired` | — |
| 所有接收端都过验签 | `test_every_inter_node_route_requires_a_signature`（路由表现取） | | | | | | | | | |
| 发送端 `post_to_peer` | `test_sender_version_switch` | | | | | | | | | `test_sender_refuses_plain_http` |
| 6 个 `forward_*` | `test_cross_border_sync.py` 全部 | | | | | | | | | |
| nonce 表 | `test_claim_prunes_rows_older_than_the_window`（含「保留时长 ≥ 有效期 + 偏差」） | | | | | | | | | |

## 测试（本地只跑受影响文件 + `npm test`；库用 Docker 起的 PG；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）
- 起库：`docker compose -f docker-compose.test.yml up -d --wait`（55432 / `charsim_test`，tmpfs 空库）；先 `docker ps --filter publish=55432` 确认端口没被占。
- `pytest tests/test_inter_node_v2.py tests/test_cross_border_sync.py tests/test_inter_node_auth.py tests/test_admin_peer_disable.py tests/test_router_unified_exits.py tests/test_storage_contract_shape.py tests/test_migration_dispatch.py tests/test_sqlite_fresh_schema.py tests/test_postgres_store.py`
- `npm test`（前端本段不改，只确认没被带坏）。
- 报告里不写本地全量数字。

## 对账表（起草方在沙箱预跑，基线 `3dbc58b` + 方案甲补丁 + 本补丁）
**改前**：`test_inter_node_v2.py` 收集即失败（`sign_request_v2` / `build_request` 不存在）。**改后**：`test_inter_node_v2.py` + `test_cross_border_sync.py` + `test_inter_node_auth.py` + `test_admin_peer_disable.py` 共 68 passed。

| 变异 | 预跑红源 |
|---|---|
| V1 不核请求体摘要 | `test_body_swap_with_stale_digest_is_rejected` |
| V2 不核必需组件 | `test_missing_required_component_is_rejected` |
| V3 不登记 nonce | `test_replay_is_rejected` |
| V4 `@authority` 改信请求 Host 头 | `test_authority_swap_is_rejected`、`test_v2_refused_when_self_host_unset` |
| V5 时钟偏差退回库缺省 5s | `test_time_window[20-200]` |
| V6 有效期退回库缺省 1 天 | `test_time_window[-60-401]` |
| V7 不认上一把密钥 | `test_previous_key_is_accepted_during_rotation` |
| V8 停收 v1 的开关失效 | `test_v1_rejected_once_retired` |
| V9 发送端不查 https | `test_sender_refuses_plain_http` |
| V10 一个接收端（`/admin/users`）绕过验签 | `test_every_inter_node_route_requires_a_signature` 等 11 条 |
| V11 nonce 保留 30s（短于窗口） | `test_claim_prunes_rows_older_than_the_window` |
| V12 发送端版本开关失效（恒 v1） | `test_sender_version_switch` 等 6 条 |
| V13 未配 `INTER_NODE_SELF_HOST` 时退回 Host 头 | `test_v2_refused_when_self_host_unset` |
| V14 PG 登记时不清旧行 | `test_claim_prunes_rows_older_than_the_window` |
| V15 转发非 200 不记日志 | `test_cross_border_sync.py::test_forward_delete_to_peer_logs_status_on_non_200` |

15 条全红，0 存活，每条跑完逐字节还原。沙箱全量 `tests/`（本地 PG）唯一的红仍是 `test_evidence_integrity.py::…::test_code_sha_resolves`（浅克隆，main 上同样红）。执行方落位后按这 15 条复跑，红源不一致就停下报告。

## 运维前置核查（**未完成**：结果回填到「补充」之前，不许执行上线第 1 步）
两台各做一次只读核查（提示词见会话，要点）：用 `openssl s_client` 看对端 443 的证书（subject / issuer / notAfter / SAN），再用 `curl -X POST https://<对端域名>/api/inter-node/admin/users -d '{}'`（**不加 `-k`**、不带签名）看状态码和耗时。
- 两个方向都是 **401** → 证书、域名解析、IP 白名单、路由全通，T1 成立；
- 403 → 对端白名单拦了，先查出口 IP；证书报错 → T1 不成立，停下重新选方案；
- 耗时要明显小于 10s；`notAfter` 太近要先确认证书会自动续期。

## 上线步骤（每步只改配置或部署，都可回滚；一步验收通过才进下一步）
| 步 | 做什么 | 验收 | 回滚 |
|---|---|---|---|
| 1a（T1，只改配置） | 两台 `.env`：`PEER_NODE_URL=https://<对端域名>`；重启 app（**旧代码**）。 | 两台日志里补发不报错；对端 nginx 访问日志 `/api/inter-node/` 请求来自 443 | `PEER_NODE_URL` 改回原值、重启 |
| 1b（部署本段代码 = 双收） | 两台 `.env` 加 `INTER_NODE_SELF_HOST=<本机域名>`、`INTER_NODE_SIGN_VERSION=1`、`INTER_NODE_ACCEPT_V1=1`；`deploy.yml` 分别部署（顺序任意）。 | 两台 `inter-node accepted: … version=1` 持续出现，`inter-node rejected` 为 0 | 重新部署上一版（PREV_SHA）；旧代码只认 v1，而本段此时也只发 v1，两台可以各自回滚 |
| 2（切发） | 先一台 `INTER_NODE_SIGN_VERSION=2` 重启；对端出现 `version=2` 且无 rejected 后，另一台再切 | 对端日志 `version=2` 出现、`rejected` 为 0；补发积压不上涨 | 改回 `1` 重启 |
| 3（停收旧版） | 两台都连续 7 天 `version=1` 为 0 后，两台 `INTER_NODE_ACCEPT_V1=0` 重启 | `rejected` 仍为 0 | 改回 `1` 重启 |

观察命令（只读）：`docker logs character-distill-app-1 --since 24h 2>&1 | grep -c "inter-node accepted: .*version=1"`（把 1 换成 2、把 accepted 换成 rejected 同理）。
v1 代码的删除不在本段：第 3 步稳定后另开一个小改动删掉，届时 `INTER_NODE_ACCEPT_V1` 一并去掉。

## 范围规矩
新发现的问题属于本段改动面的直接修；只有会撞车或需要 Shiyu 拍板时才停下报告；不许自行记账。

## S0（只读；全部成立直接编码）
1. `Test-Path docs/specs/inter-node-v2.md` 与 `docs/specs/artifacts/inter-node-v2.patch` 为真，patch 的 `Get-FileHash` 与上方 sha256 一致。
2. 确认方案甲已在 origin/main；`git apply --check docs/specs/artifacts/inter-node-v2.patch` 通过。若不通过或行号漂移，逐条复核「已查实的约束」1–10 与「全量扫描」；**事实不成立就停下报告**。
3. 「补充」一节已有运维前置核查结果，且两个方向都是 401；否则只做代码、不做上线。

## 需要 Shiyu 另行拍板（本段不改）
- 约束 7 的三类无补发转发（邀请码新增 / 删除、用户资料）：本段只保证切换期间不丢；是否补上重发是另一个设计问题，方案对比见会话，拍板后另出 spec。

## 补充
### 2026-09-30 隐私政策一句已定
Shiyu 定：补「节点间传输经 TLS 加密」。已并入本段设计与步骤 4，参考补丁同步更新（新 sha256 见文件头）；前端 `npm test` 60 文件 300 条全绿。

### 2026-09-30 参考实现改为分支提交
参考补丁文件已删除，理由同 `admin-peer-disable.md` 同日补充。本段代码是分支 `feat/cross-border-outbox` 上的提交 `feat(inter-node): https transport and RFC 9421 request signatures`，前一个提交是方案甲。「S0」里核对补丁文件、`git apply --check` 两步不再适用；上线前的证书核查照旧。
