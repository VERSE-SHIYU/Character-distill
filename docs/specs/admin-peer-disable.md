# 管理后台跨节点禁用 / 启用 + 禁用/启用/封禁的三处缺陷（2026-09-30，Shiyu 已定：方案甲）

> 放进 worktree 的 `docs/specs/admin-peer-disable.md`；以后的补充写进本文件末尾「补充」一节。
> **执行方**：从 origin/main（基线 `19d2343`）新开分支 `feat/admin-peer-disable`（`--unset-upstream`）。
> **参考补丁**：`docs/specs/artifacts/admin-peer-disable.patch`（sha256 `4166d1ddf71914f2d901234970156c1dc1a4beb18c2ff79dd65b6962f0d73107`），
> 就是下文「对账表」预跑用的那一版。执行方可以直接 `git apply` 后逐条复核，不必重写。
> **不在本段**：节点间签名协议 v2（另一份 spec，见末节「转交」）。

## 目标
1. 在一台的管理后台里，对**对端**节点的用户点「禁用 / 启用」，由对端用本地同一段代码执行，结果回传；对端不可达时明确报错，不假装成功。
2. 本地禁用 / 启用 / 封禁三条路由的缺陷一起修：找不到用户回 404（现在回 `{"ok": true}`）；不能禁用自己；封禁的操作者只取登录身份（现在可以用请求体冒充）。

## 已查实的约束（基线 `19d2343`，每条都现读）
1. **禁用 / 启用找不到用户也回成功**：`web/routers/admin.py:157` / `:169` 直接 `await storage.set_user_disabled(...)` 后 `return {"ok": True}`；存储层 `storage/postgres_store.py:2508` 的 `UPDATE` 不看影响行数。
2. **能禁用自己**：`admin.py:149-170` 两条路由都没有自我判断（封禁那条 `:589` 有）。
3. **封禁的操作者可冒充**：`admin.py:575-576` `BanUserRequest.admin_id`，`:591` `admin_id = req.admin_id or admin_user["id"]`；该值写进 `card_comment_reports.resolver_id`（`postgres_store.py:3010-3015`）。前端 `client.js:380` 发的是 `body: '{}'`，**生产调用方从不传这个字段** → 去掉不改任何现有调用。
4. **封禁找不到用户也回成功**：`postgres_store.py:3007` 的 `UPDATE` 不看行数；`ban_user_and_contents` **没有在 `storage/base.py` 声明为抽象方法**（`grep -n ban_user_and_contents storage/base.py` 零命中），所以 `tests/test_storage_contract_shape.py` 不核它两侧签名。
5. 先例：`set_user_role`（`postgres_store.py:2489-2503`）已是「写 0 行抛 `ValueError`，路由翻 404」（缺陷 68 同形），本段照抄这个形态。
6. **两台的管理员账号整号复制、id 相同**（`docs/specs/admin-peer-rows.md`「问题」节）⇒ 从一台禁用对端同 id 的账号 = 把自己锁在对端外面。对端必须用同一条「不能禁用自己」规则拦，操作者 id 随请求传过去。
7. **禁用即时生效，无需吊销会话**：`web/routers/auth.py:278` 每次鉴权都现读 `is_disabled`（`resolve_identity` → `Verdict.DISABLED` → `IDENTITY_REJECTIONS` = 403）；登录 `:514`、刷新 `:549`/`:572` 也查。
8. **对端字段白名单已含 `is_disabled`**：`postgres_store.py:2404`；隐私政策 `web/frontend/src/legal/privacy_v3.md:109-112` 第 (5) 条写明用途「执行必要的合规治理（如违规账户处置）」→ 跨节点禁用在已公示的用途内，不改隐私政策。
9. **节点间签名只覆盖「时间戳 + 请求体」**：`web/inter_node_auth.py` `_sign` = `f"{timestamp}:{json.dumps(payload, sort_keys=True)}"`，±30s（`_MAX_AGE_MS`），不含方法、路径、nonce。
10. **现有 9 个接收端的请求体字段**（全量扫描见下）：`/card/delete`、`/dm/retract`、`/user/purge` 三个都只读 `target_id`。新接口的请求体**不许用 `target_id` 这类别处已用的字段名**，并带操作名 `op` 由接收端校验 —— 这是在现有签名机制内把请求体绑定到本接口。
11. **「PEER_NODE_URL → 签名 → httpx POST」已手抄 7 份**：`web/cross_border_sync.py` 6 份 + `web/routers/admin.py:98-113` 1 份。本段抽 `web/peer_client.py::post_to_peer`，**联邦列表与新功能改用它**；`cross_border_sync.py` 那 6 份由签名协议 v2 迁移（那条线要逐个改这 6 处的签名，一次改完）。
12. 联邦列表 `/api/admin/users/federated` **原先没有任何测试**（`grep -rn federated tests/` 零命中）。本段改了它的发送方式，所以补两条测试。
13. `web/demo_gate.py:29` 与 `:126` 的 docstring 写着「8 条 `/api/inter-node/*`」，而基线实际是 **9 条**（全量扫描），本段后是 10 条。改成不写死数量的说法。
14. nginx 对 `/api/inter-node/` 有对端 IP 白名单 + WAF/CC（`nginx/nginx.conf:45-48`、`:115-121`）；**80 端口那一段也直接转发**（`:45` 在 `listen 80` 的 server 里）。生产 `PEER_NODE_URL` 是 http 还是 https 本仓核不到 → 见「运维核查」，结果写进本文件「补充」。本段功能不依赖它（请求体不含机密），但签名 v2 的设计依赖它。

## 出处对照表
| 规范条目 | 出处 | 本 spec 行为 |
|---|---|---|
| 写操作转发到数据所在站点，由那边作为完整读写实例执行；带关联 ID 让两边日志对得上 | GitLab Docs「Geo → Secondary sites」+「Geo: Proxying」（docs.gitlab.com/administration/geo/secondary_proxy/） | 行为 B1（转发执行）、B6（`request_id` 两端日志） |
| 签名请求要把请求绑定到目标接口，否则可被挪到另一个接口重放 | RFC 9421 §7.2.1、§1.4 | 行为 B4（`op` 校验 + 字段名不与他处重合）；完整绑定（方法+路径）归 v2 |
| 写 0 行不回成功 | 本仓先例 `set_user_role`（约束 5） | 行为 A1 |
| 管理员不能对自己做锁号操作 | 本仓先例 `set_user_role` 不能改自己（`admin.py:199`）、封禁不能封自己（`admin.py:589`） | 行为 A2、B3 |

## 全量扫描（基线 `19d2343`，原始输出）
```
$ git grep -n "is_disabled = " 19d2343 -- web storage core
storage/postgres_store.py:1069:  ... AND is_disabled = 0          （读，搜索过滤）
storage/postgres_store.py:2508:  UPDATE users SET is_disabled = $1 WHERE id = $2
storage/postgres_store.py:3007:  UPDATE users SET is_disabled = 1 WHERE id = $1
storage/sqlite_store.py:1601:    ... AND is_disabled = 0          （读）
storage/sqlite_store.py:3145:    UPDATE users SET is_disabled = ? WHERE id = ?
storage/sqlite_store.py:3705:    UPDATE users SET is_disabled = 1 WHERE id = ?
$ git grep -n "set_user_disabled\|ban_user_and_contents" 19d2343 -- web core
web/routers/admin.py:157:    await storage.set_user_disabled(user_id, True)
web/routers/admin.py:169:    await storage.set_user_disabled(user_id, False)
web/routers/admin.py:592:    counts = await storage.ban_user_and_contents(user_id, admin_id)
$ git grep -n "create_auth_header(" 19d2343 -- web core scripts mcp_server
web/cross_border_sync.py:42 / :99 / :156 / :199 / :232 / :275
web/inter_node_auth.py:8（docstring）/ :59（定义）
web/routers/admin.py:106
$ git grep -n "^@router.post" 19d2343 -- web/routers/inter_node.py
:23 /dm/receive  :59 /card/receive  :98 /card/delete  :125 /dm/retract  :152 /invite-code/receive
:184 /invite-code/delete  :211 /user/purge  :238 /admin/users  :261 /user/sync
$ git grep -nE '(payload|msg|card)\.get\("|required = ' 19d2343 -- web/routers/inter_node.py
:41-44 dm/receive: id, sender_id, receiver_id, content
:78    card/receive required: id, user_id, name, card_json, visibility, origin_region
:116   card/delete: target_id      :143 dm/retract: target_id      :229 user/purge: target_id
:170   invite-code/receive: code   :202 invite-code/delete: code
:279-282 user/sync: id, username, home_region, avatar_data
（/admin/users 不读任何字段，只读列表）
$ git grep -n "disableUser\|enableUser\|banUser" 19d2343 -- web/frontend/src ":!*__tests__*"
web/frontend/src/api/client.js:301 / :303 / :380
web/frontend/src/components/AdminPanel.jsx:357 / :359 / :777 / :1493
```
写入 `is_disabled` 的只有 4 处（两个 store × 两个函数），路由调用点只有 3 处；本段把这 3 处全部收进共用函数。

## 路径机制清单
| 机制 | 位置 | 计时/计数从哪开始、依赖什么前提 | 新组件是否改变前提 |
|---|---|---|---|
| 管理员鉴权 `require_admin` | `admin.py:30-35` | 每请求现读身份 | 否。新发起端路由同样挂它；接收端不走 JWT，走节点签名 |
| 应用层限流 30 次/分 | `@limiter.limit`，按客户端 IP | 每个路由独立计数 | 发起端两条新路由各 30/分；接收端新加 30/分，键是对端节点 IP |
| nginx 对端 IP 白名单 + WAF/CC | `nginx/nginx.conf:45-48`、`:115-121` | 按 `/api/inter-node/` 前缀 | 否。新接收端在同一前缀下，自动受护 |
| 节点签名 | `inter_node_auth.py`，±30s 从发起端 `time.time()` 算 | 两台时钟偏差 < 30s | 否。新请求体带 `op`，接收端校验 |
| 禁用生效 | `auth.py:278`、`:514`、`:549`、`:572` | 每请求现读 | 否。测试钉住「禁用后下一次请求 403」 |
| 批量勾选 | `AdminPanel.jsx:486` `selectable` 排除对端 | — | 否。批量不跨节点 |
| 对端 HTTP 超时 10s | 7 处转发同值 | 从发请求起算 | 沿用，`peer_client.TIMEOUT_S = 10` |

### 通道 × 执行上下文 × 守它的测试
| 通道 | 执行上下文 | 测试 |
|---|---|---|
| 本地路由 → `admin_user_ops` → PG | async 协程（请求 loop） | `tests/test_admin_user_ops.py` 全部 |
| 发起端路由 → `post_to_peer` → httpx | async 协程 | `test_admin_peer_disable.py::test_peer_*`、`test_no_peer_configured_is_503` |
| 接收端路由 → `admin_user_ops` → PG | async 协程 | `test_admin_peer_disable.py::test_receiver_*`、`test_signed_disable_body_*` |
| 联邦列表 → `post_to_peer` | async 协程 | `test_admin_peer_disable.py::test_federated_list_*` |
| 前端按钮 → `adminAPI.peer*` | 浏览器 | `AdminPanelPeerRows.test.jsx` |
不涉及同步线程、后台任务、重试、缓存。

## 规模表
| 数据源 | 上限 | 策略 |
|---|---|---|
| 请求体 | 5 个字段，< 300 字节 | 单次 POST |
| 联邦用户列表 | 无上限，全量一次拉取（现状，不改）。2026-09-17 两台各约 10 人 | 整表渲染 |
| 对端调用耗时 | 超时 10s（与既有 7 处同值） | 超时即 502，不重试（管理员手动重点，重复禁用/启用是幂等的） |

## 设计
- **共用函数** `web/admin_user_ops.py`：`set_user_disabled(storage, *, target_id, operator_id, disabled)`、`ban_user(storage, *, target_id, operator_id)`。前置条件只写这一份：禁用/封禁自己 → `HTTPException(400)`；存储层写 0 行抛 `ValueError` → `HTTPException(404)`。直接抛 `HTTPException`，不新造异常类（新异常类会触发 `test_exception_pickle_lock` 的登记，而两类调用方都是路由，本来就要这个形态）。启用自己不拦（被禁用的管理员登录不了；跨节点启用自己是解锁）。
- **存储层**：两侧 `set_user_disabled` / `ban_user_and_contents` 写 0 行抛 `ValueError`（封禁在事务内抛，后两条写一条都不落）；`storage/base.py` 补 `ban_user_and_contents` 抽象声明。SQLite 只同步到「接口还能跑」。
- **`web/peer_client.py`**：`post_to_peer(path, payload)`（未配置对端抛 `PeerNotConfigured`，网络错误原样上抛），`_client(timeout)` 是唯一建 httpx 客户端处（测试替换它，生产代码不留测试钩子）。
- **发起端**：`POST /api/admin/peer/users/{id}/disable|enable`。请求体 `{op: "admin_set_user_disabled", subject_user_id, operator_id: 登录身份, disabled, request_id: uuid4}`。映射：200 → ok；对端 400/404 → 原样转回（带对端 detail）；对端其它状态码 → 502；`httpx.HTTPError` → 502；未配置对端 → 503。`except` 只接 `httpx.HTTPError`（宽 `except` 会撞 `test_router_unified_exits` 的锁，也会把编程错误误报成「对端不可达」）。两端日志都带 `request_id`。
- **接收端**：`POST /api/inter-node/admin/user-disabled`，验签 → `op` 必须等于 `admin_set_user_disabled` → 字段齐全（`disabled` 必须是 bool）→ 调共用函数；成功记 INFO、被拒记 WARNING，都带 request_id / operator / subject。
- **前端**：`client.js` 加 `peerDisableUser` / `peerEnableUser`；`AdminPanel.jsx` 加 `setDisabledFor(u, disabled)`（选本地还是跨节点接口，只在这一处判）；对端行操作列：非自己的行显示「禁用/启用」+「其余操作请在对端节点进行」；**本地与对端「自己」那行都不给禁用按钮**；确认框对对端行写「确定禁用对端节点的用户…」。

## 步骤（4 个 commit）
1. **[storage]** 两侧 0 行抛 `ValueError`；`base.py` 补抽象声明。
2. **[web 后端]** `admin_user_ops.py`、`peer_client.py`；`admin.py` 三条本地路由改调共用函数、删 `BanUserRequest`、联邦列表改用 `post_to_peer`、加两条发起端路由；`inter_node.py` 加接收端；`demo_gate.py` 两处 docstring 去掉写死的「8 条」。
3. **[web 前端]** `client.js`、`AdminPanel.jsx`。
4. **[tests]** 新增 `tests/test_admin_user_ops.py`（8 条）、`tests/test_admin_peer_disable.py`（12 条）；改 `AdminPanelPeerRows.test.jsx`（首条改写 + 新增 3 条）。

## 调用点矩阵
| 调用点 ＼ 输出 | 不存在 → 404 | 自己 → 400 | 操作者取值 | 下一次请求 403 | 对端失败 → 502/503 | 日志 |
|---|---|---|---|---|---|---|
| 本地 disable | `test_unknown_target_is_404[disable]` | `test_cannot_target_self[disable]` | —（无此参数） | `test_disabled_user_is_refused_on_next_request` | — | — |
| 本地 enable | `test_unknown_target_is_404[enable]` | 不拦（设计） | — | `test_disable_then_enable_round_trip` | — | — |
| 本地 ban | `test_unknown_target_is_404[ban]` | `test_cannot_target_self[ban]` | `test_ban_operator_is_the_logged_in_admin` | 同 disable（同一列） | — | — |
| 发起端 peer disable/enable | `test_peer_unknown_user_is_relayed_as_404` | `test_peer_self_disable_is_relayed_as_400` | 登录身份写进 `operator_id`（同左测试） | `test_peer_disable_then_enable` | `test_peer_failure_is_502[unreachable/401]`、`test_no_peer_configured_is_503` | 发起端 INFO/ERROR（不单测） |
| 接收端 | 同上（经发起端） | 同上 | 请求体 `operator_id`（签名覆盖） | 同上 | 验签失败 `test_receiver_rejects_bad_signature`；op 不符 `test_receiver_rejects_other_op`；搬到别的接口 `test_signed_disable_body_is_refused_by_every_other_write_receiver` | `test_receiver_logs_who_did_what` |
| 联邦列表 | — | — | — | — | `test_federated_list_degrades_when_peer_is_down`；正路 `test_federated_list_reads_peer_users_via_signed_request` | — |
| 前端对端行 | — | `AdminPanelPeerRows::自己那两行…` | — | — | 错误走既有 `setActionError` | — |
| 前端按钮路由 | — | — | — | — | `AdminPanelPeerRows::对端行禁用走跨节点接口…`、`…启用走跨节点接口` | — |
| 两侧签名一致 | `tests/test_storage_contract_shape.py::test_impl_signature_matches_base[ban_user_and_contents]`（新增的抽象声明让它开始核） | | | | | |

## 测试（本地只跑受影响文件 + `npm test`；库用 Docker 起的 PG；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）
- 起库：`docker compose -f docker-compose.test.yml up -d --wait`（55432 / `charsim_test`）。
- 后端：`pytest tests/test_admin_user_ops.py tests/test_admin_peer_disable.py tests/test_storage_contract_shape.py tests/test_exception_pickle_lock.py tests/test_router_unified_exits.py tests/test_demo_gate.py tests/test_auth_param_used.py`
- 前端：`npx vitest run src/components/__tests__/AdminPanelPeerRows.test.jsx src/components/__tests__/AdminPanelRoleSelect.test.jsx`、`npm test`、`npx eslint . -c eslint.ci.config.js --quiet`。
- 报告里不写本地全量数字。

## 对账表（起草方在沙箱预跑，基线 `19d2343` + 参考补丁）
**改前**（新测试、旧代码）：`test_admin_user_ops.py` 5 failed / 3 passed（红的是 404×3、禁用自己、封禁冒充；绿的 3 条是现有行为：封禁自己已拦、禁用/启用往返、禁用后 403）；`test_admin_peer_disable.py` 收集即失败（`peer_client` 不存在）；`AdminPanelPeerRows.test.jsx` 4 failed / 3 passed。
**改后**：`test_admin_user_ops.py` + `test_admin_peer_disable.py` + `test_storage_contract_shape.py` 共 158 passed；前端 2 文件 10 passed，`npm test` 59 文件 297 条全绿，CI 口径 lint exit 0。

| 变异 | 预跑红源 |
|---|---|
| M1 PG `set_user_disabled` 不判 0 行 | `test_unknown_target_is_404[disable]`、`[enable]`、`test_peer_unknown_user_is_relayed_as_404` —— 3 条 |
| M2 PG `ban_user_and_contents` 不判 0 行 | `test_unknown_target_is_404[ban]` |
| M3 `_refuse_self` 失效 | `test_cannot_target_self[disable]`、`[ban]`、`test_peer_self_disable_is_relayed_as_400` —— 3 条 |
| M4 封禁操作者退回「请求体 `admin_id` 优先」 | `test_ban_operator_is_the_logged_in_admin` |
| M5 接收端不校验 `op` | `test_receiver_rejects_other_op` |
| M6 接收端不验签 | `test_receiver_rejects_bad_signature` |
| M7 对端网络错误时回 ok | `test_peer_failure_is_502[unreachable]` |
| M8 对端 400/404 不转回 | `test_peer_self_disable_is_relayed_as_400`、`test_peer_unknown_user_is_relayed_as_404` |
| M9 未配置对端时回 ok | `test_no_peer_configured_is_503` |
| M10 接收端不记日志 | `test_receiver_logs_who_did_what` |
| M11 请求体里多带 `target_id`（与 `/user/purge` 撞字段） | `test_signed_disable_body_is_refused_by_every_other_write_receiver` |
| M12 SQLite `ban_user_and_contents` 签名与 base 漂移 | `test_impl_signature_matches_base[ban_user_and_contents]` |
| M13 联邦列表对端出错不标不可达 | `test_federated_list_degrades_when_peer_is_down` |
| M14 对端回 401 当成功 | `test_peer_failure_is_502[401]` |
| F1 `setDisabledFor` 不分对端 | `对端行禁用走跨节点接口…`、`已禁用的对端行启用走跨节点接口` |
| F2 对端行不给禁用按钮 | `对端行只有「禁用」一个写按钮…` + F1 那 2 条 |
| F3 本地「自己」那行给禁用按钮 | `自己那两行（本地 + 对端同 id）都不给禁用按钮` |

17 条全红，0 存活；每条跑完逐字节还原（sha256 核对）。执行方落位后按同样 17 条复跑，红源不一致就停下报告。

另：沙箱全量 `tests/`（本地 PG）唯一与本段无关的红是 `test_evidence_integrity.py::TestManifestEntries::test_code_sha_resolves` —— 沙箱克隆是浅克隆（`git rev-parse --is-shallow-repository` = true），**未改动的 main 上同样红**；CI 用 `fetch-depth: 0` 不受影响。

## 起环境前的冲突核查
- 测试库固定 55432 / 容器名 `character-distill-test-postgres-1`（`docker-compose.test.yml` 的 `name:`），与开发库 5432 不冲突；数据目录 tmpfs，起来即空库。执行方先 `docker ps --filter publish=55432` 确认端口没被别的东西占。
- 沙箱无 Docker，起草方用本机 PG16 `initdb` 起在 55432；数据目录第一次放在会话 scratchpad，**被沙箱重置权限后 PG 中途 PANIC**，改放 `/var/lib/postgresql/` 后正常。与执行方环境无关，只作记录。

## 运维核查（Shiyu 在两台各跑一次，结果写进「补充」；只给命令骨架，不打印值）
```
cd <项目路径> && grep -c '^PEER_NODE_URL=https://' .env
```
输出 1 = https；0 = http 或未配置。这个结果决定签名 v2 是否必须带「请求体加密 / 强制 TLS」一项，**不影响本段上线**。

## 范围规矩
新发现的问题属于本段改动面的直接修；只有会撞车或需要 Shiyu 拍板时才停下报告；不许自行记账。

## S0（只读；全部成立直接编码）
1. `Test-Path docs/specs/admin-peer-disable.md` 与 `docs/specs/artifacts/admin-peer-disable.patch` 为真，patch 的 `Get-FileHash` 与上方 sha256 一致。
2. `git fetch origin`，确认基线；若 origin/main 已前进，逐条复核「已查实的约束」1–14 与「全量扫描」的行号（漂移按新行号做，不停；**事实不成立就停下报告**）。
3. `git apply --check docs/specs/artifacts/admin-peer-disable.patch` 通过。

## 验收（部署后）
1. 部署两台（顺序任意：新接收端在对端未部署时，发起端会拿到 404 → 原样转回「Not Found」，不会误操作本地；**两台都部署完之前别点对端行的按钮**）。
2. 在 SG 后台对一个 SZ 测试账号点「禁用」→ 列表刷新后显示「已禁用」；该账号在 SZ 下一次请求被拒；再点「启用」恢复。
3. SZ 容器日志：`docker logs character-distill-app-1 2>&1 | grep "peer admin set_disabled"` 能看到同一个 `request_id` 的 applied 行。

## 转交：节点间签名协议 v2（不在本段，方案待 Shiyu 拍板后另出 spec）
本段不改签名协议，下列两项由 v2 处理，**不是记账**：
- `cross_border_sync.py` 的 6 处手抄转发迁到 `post_to_peer`；
- 现有 `/card/delete`、`/dm/retract`、`/user/purge` 三个接收端共用 `target_id` 字段、都不校验 `op`，彼此之间 30s 内可挪用签名（约束 9、10）。本段新接口已用 `op` + 专用字段名把自己隔开，不让情况变坏。

## 补充
### 2026-09-30 运维核查结果（Shiyu 经执行方在两台只读执行，原始输出）
```
SZ: https=0 http=1 secret_ok=1 utc=1790753151
SG: https=0 http=1 secret_ok=1 utc=1790753159
```
- **两台节点间都走明文 http**（`PEER_NODE_URL=http://…`），经 nginx 80 端口那段 `/api/inter-node/` 转发（约束 14）。本段功能不受影响：新请求体只有 id、布尔值和 request_id，不含机密。但现有同步通道里私信正文、角色卡内容、用户名/昵称都是明文跨境传输 —— 这一项**不在本段**，已作为节点间通信修复的第一项另行报告，方案待 Shiyu 拍板。
- 签名密钥两台都已配置且 ≥ 32 字符。
- 两台 UTC 时间读数相差 8 秒（两条 ssh 并行发起，含发起时差），在签名 ±30s 窗口内，本段上线不受影响。

### 2026-09-30 转交项已出 spec
「转交」一节的两项已写进 `docs/specs/inter-node-v2.md`（节点间切 https + 签名 v2）。它以本段为前置：先合本段，再做 v2。
