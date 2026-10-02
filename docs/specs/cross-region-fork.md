# Spec cross-region-fork（P1a）：公开作品目录 + 跨区 fork + 卡能力表

> 放 `docs/specs/cross-region-fork.md`。补充写进本文件末尾「补充」。遵守 `docs/specs/SPEC-STANDARD.md`。
> 本 spec 只写代码与测试，**不含部署**。**只改 PG**，SQLite 不动（AGENTS.md:53；本 spec 无新增列，连孪生迁移也没有）。

## 头部

| 项 | 值 |
|---|---|
| 分支起点 | main `42b068ba`（PR #108） |
| 参考实现 | 分支 `feat/cross-region-fork`，单提交 `a8235c04`；bundle `cross-region-fork.bundle`，sha256 `5f3b2be0a4adf1b465319fb8c529b9fb41260797ffd90fea2aedcb41e27413be`，前置 `42b068ba` |
| 取用 | `git fetch <bundle路径> feat/cross-region-fork:feat/cross-region-fork` |
| 拍板记录 | 2026-10-02：跨区目标——数据存在产生者的归属地；**B′**：保留「资料 + 公开作品」两类副本，其余实时读；P1 拆成 P1a（本 spec）/ P1b（fork 血缘 + 跨区 fork 列表）/ P1c（实时读 + 看对方空间，等 privacy_v4）；fork 删除规则 **a**：fork 出来的卡归 fork 者，原作删除或作者注销都不影响 |

## 要解决的问题

对端地区的公开卡在市场里看得到，但点进详情页会被弹回（`/api/cards/{id}/detail` 只查本地 `cards`，404），点「使用角色」也会 404（`fork_card` 只查本地 `cards`）。

## 设计（不过度设计：一张 SQL 常量 + 一张规则表，各自复用上一条线的模式）

| 层 | 抽象 | 唯一位置 | 复用了什么 |
|---|---|---|---|
| 数据 | **公开作品目录** `_PUBLIC_CARDS` = 本地公开且未删的卡 ∪ `remote_cards`，同 id 以本地为准；对端卡的 `text_id` 为 NULL（文本在对端库），带 `is_remote` / `origin_region` | `storage/postgres_store.py` 模块顶部 | 与账号目录 `_PUBLIC_ACCOUNTS` 同形；读取方法 `get_public_card` 和账号目录的方法一样，是 `StorageBase` 上的 PG-only 非抽象方法 |
| 规则 | **卡能力表** `card_visibility.py`：local / remote 两种视图 × 6 项能力（fork / like / comment / history / report / moderate） | `web/card_visibility.py` | 与 `account_visibility.py` 同形（视图 → 能力 → `page_meta` 下发） |
| 展示 | `MarketCardDetail` 只按 `capabilities` 渲染，缺省全关；属主、写权限、管理员这些「谁在看」的判断照旧，与能力组合使用 | `MarketCardDetail.jsx` | 与 `AuthorPage` 同一做法 |

几处刻意的简化：

- **fork 不需要实时读**：内容就是本库 `remote_cards` 里的副本，也就是用户在详情页看到的那份，不依赖对端在线。
- **fork 源卡只查一次**：`fork_card` 原来要查 3 次（`get_card_unscoped`、可见性检查、`get_card_avatar_unscoped`），现在只查一次目录。
- **两个详情读取共用 `_remote_card_detail`**：对端卡的详情响应只在这一处组装，替换掉 `get_market_card_detail` 里原有的手写回落逻辑。
- **不新增列**：fork 卡上记录来源地区的列，留给 P1b 和血缘修复一起设计（卡 id 全局唯一，P1a 用不到这一列）。

| 照抄来源（本仓既有决定） | 本 spec 的行为 |
|---|---|
| 隐私政策 3.2(2)：公开作品连同 `card_json` 同步到对端 | B1：目录直接读 `remote_cards`，不新增任何出境数据 |
| 同区 fork 的语义：独立的私密副本，原卡删除不影响（`get_card_forks` 只按 `forked_from` 查） | B2、B3：跨区 fork 的语义相同 |
| 账号线的三层模式（PR #108） | 本 spec 的三层 |

## 已查实的约束（坐标 @ main `42b068ba`，S0 逐条复核）

1. 详情页加载走 `/api/cards/{id}/detail`（`web/frontend/src/components/MarketCardDetail.jsx:94`）→ `routers/card.py:123` → `storage.get_card_detail`（`postgres_store.py:735`），只查本地 `cards`。对端卡在这里 404，前端 `catch` 后执行 `navigateBack`，表现为「点进去被弹回」。
2. 同一个 `get_card_detail` 的 WHERE 只有 `c.id = $1 AND c.deleted_at IS NULL`（`:750`），**他人的私密卡可以按 id 读到**。这属于本 spec 的改动面，按规则 6 直接修复（B5）。
3. `fork_card`（`:1143`）用 `get_card_unscoped` 读源卡，再查一次可见性，再用 `get_card_avatar_unscoped`（`:1180`）读头像，三处都只查 `cards`。路由在 `routers/market.py:983`，`ForkRequest.text_id` 的默认值是 `""`（`:69`）。
4. `remote_cards`（`migrations_pg/008_remote_cards.sql:2-11`）有 `card_json`、`avatar_data`、`market_description`、`market_tags`、`origin_created_at`（TEXT 类型），**没有 `text_id`**。fork 需要的字段都在。
5. `cards.forked_from` 是 `TEXT DEFAULT ''`，**没有外键**（`001_init.sql:44`），可以直接存对端卡的 id。
6. `get_market_card_detail`（`:766`）已经有对端回落（`:796` `_get_remote_card`），回落里手写组装了 9 个字段，`get_card_detail` 没有回落。两处的形状要统一。
7. 前端要按能力控制的入口：举报 `:531`、删除 `:541`、点赞 `:645`、「使用角色」`:657`、评论 / 版本 / 衍生标签页 `:760`、底部评论输入框 `:906`。现在这些入口对对端卡全都显示，点了全部 404。
8. 隐私政策 3.2(2)（`privacy_v3.md:92`）写的用途是「用于跨地域浏览」，触发条件在 `:95`，没有写 fork。
9. **对 store 类做整体约束的锁**（上一条线的教训，全量扫描见下文）：`test_storage_scope_lock.py` 有三把相关的锁——方法集镜像（`PG_ONLY_METHODS` 在 `:583`）、`*_unscoped` 调用白名单（`postgres_store.py:fork_card` 登记在 `:64`）、白名单的自清检查。本 spec 新增 PG-only 方法、并让 PG 的 `fork_card` 不再调用 `*_unscoped`，两张表都要同步更新。

**全量扫描原文**：

```
# ② 对 store 类做整体约束的锁
$ grep -ln "postgres_store\|PostgresStore" tests/*.py | xargs grep -ln "ClassDef\|_public\b\|_public(\|dir("
tests/test_failure_alerting.py
tests/test_postgres_store.py
tests/test_request_timezone.py
tests/test_storage_scope_lock.py
（逐个看过：前三个不按方法集约束，只有 scope_lock 相关）

# fork / 详情 / 头像 unscoped 的全部调用点 @ 42b068ba
web/routers/market.py:413   storage.get_market_card_detail(...)
web/routers/market.py:983   storage.fork_card(...)
web/routers/card.py:123     storage.get_card_detail(...)
storage/postgres_store.py:796   self._get_remote_card(card_id)        ← market 详情回落
storage/postgres_store.py:1180  self.get_card_avatar_unscoped(card_id) ← 唯一调用方是 fork_card
```

**规模**：目录按主键取一行；`NOT EXISTS` 走 `cards` 主键。fork 从 3 次查询加 2 次写读，变为 1 次目录查询加 1 次去重查询、1 次插入、1 次回读。没有列表，也没有新的上限。

**路径机制清单**：本 spec 没有往调用路径上加闸、重试、超时、缓存或记账，不适用。

## 行为

| 编号 | 行为 |
|---|---|
| B1 | 公开作品目录：`get_public_card(id)` 返回 id / name / card_json / avatar_data / user_id / text_id / market_description / market_tags / created_at / is_remote(bool) / origin_region；私密卡、已删卡、不存在的 id 都返回 None。同 id 以本地为准 |
| B2 | `fork_card` 经目录取源卡：对端卡和本地卡同样能 fork；副本是 fork 者的私密卡，`forked_from` = 源卡 id；`new_text_id=None`（沿用原卡关联）时，对端源卡的关联为 NULL；同一用户对同一源卡、同一文本重复 fork，返回同一张卡；源卡不是存活的公开卡时返回 None（路由给 404） |
| B3 | 删除规则 a：对端作者注销（`purge_remote_user_data`）只删 `remote_cards` 等副本，不碰本地的 fork |
| B4 | 对端卡详情：`get_card_detail` 和 `get_market_card_detail` 都经 `_remote_card_detail` 返回同一形状（作者经账号目录取；赞、评论、版本按「无」：`likes = 0`、`comment_count = 0`、`liked_by_me = False`）。两个函数对同一张对端卡返回完全相同的结果 |
| B5 | `get_card_detail` 只返回「自己的卡（任意可见性）或公开卡」；他人的私密卡与不存在同样返回 None（不能借此探测 id 是否存在） |
| B6 | 卡能力表：local 视图 6 项全开；remote 视图只有 `fork`（赞、评论、版本、衍生、举报的数据都在对端库；源头在对端，本节点也不能下架）。两个详情路由都返回 `view` + `capabilities` |
| B7 | `MarketCardDetail`：举报 = `caps.report`；删除 = `caps.moderate`；点赞 = `caps.like`；使用 = `caps.fork`；标签页区块和评论输入框 = `caps.comment`；「版本历史」「衍生角色」两个标签 = `caps.history`。各自再与原有的 `canWrite` / 属主 / 管理员判断组合。缺能力时一律不显示 |
| B8 | 隐私政策 3.2(2) 补一句用途：其他地域用户可以「使用」公开作品；副本归使用者所有，存放在使用者所在的地域；原作删除或作者注销不影响副本 |
| B9 | 锁登记：`PG_ONLY_METHODS` 加入 `get_public_card`；`UNSCOPED_ALLOWLIST` 删掉 `postgres_store.py:fork_card`（PG 的 `fork_card` 已不再调用 `*_unscoped`，留着会被自清检查报红），SQLite 那条保留并注明原因 |

## 调用点矩阵（每格对应一个测试名；后端 `tests/test_cross_region_fork.py`，下表记为 X）

| 调用点 \ 可观测输出 | 来源 / 字段 | 过滤私密 / 已删 | 副本属主与可见性 | 去重 | text 关联 | 原作者注销后 |
|---|---|---|---|---|---|---|
| `get_public_card` | X `test_directory_resolves_local_public_and_remote` | X `test_directory_hides_private_and_deleted_local_cards` | — | — | 同第一格（对端为 NULL） | X `test_fork_survives_peer_purge` |
| `fork_card` 对端卡 | X `test_fork_remote_card_makes_a_private_local_copy` | — | 同左 | X `test_fork_remote_card_twice_returns_the_same_copy` | X `test_fork_remote_card_with_inherited_text_link_is_unlinked` | X `test_fork_survives_peer_purge` |
| `fork_card` 本地卡 | X `test_fork_local_card_unchanged` | 同左（私密卡返回 None） | — | — | — | — |
| `POST /api/market/{id}/fork` | X `test_fork_route_on_remote_card` | | | | | |

| 调用点 \ 可观测输出 | 对端卡可读 | 形状 / 作者 | 他人私密卡 | view + capabilities |
|---|---|---|---|---|
| `get_card_detail` | X `test_card_detail_of_remote_card` | 同左 | X `test_card_detail_hides_someone_elses_private_card` | — |
| `get_market_card_detail` | X `test_market_detail_of_remote_card_shares_the_shape` | 同左（与上一行全等） | — | — |
| `GET /api/cards/{id}/detail`、`GET /api/market/card/{id}` | — | — | — | X `test_detail_routes_carry_card_capabilities`（参数化两条路由） |
| 规则表 | — | — | — | X `test_card_capability_table` |

前端 `web/frontend/src/components/__tests__/CrossRegionCardDetail.test.jsx`：

| 场景 \ 输出 | 使用 | 点赞 | 标签页 / 评论输入 | 版本 / 衍生标签 | 举报 | 删除 |
|---|---|---|---|---|---|---|
| remote | `remote card: only “use” is offered`（有） | 同左（无） | 同左（无） | 同左（无） | 同左（无） | 同左（无，查看者是管理员） |
| local | `local card: everything as before`（全有） | ← | ← | ← | ← | ← |
| history 关、comment 开 | — | — | `history is its own switch…`（有） | 同左（无） | — | — |
| 缺能力 | `missing capabilities: nothing actionable`（全无） | ← | ← | — | ← | ← |

另外改了一个既有 fixture：`GuestWriteEntries.test.jsx` 的 `CARD` 补上 `view` / `capabilities`。详情接口现在必定返回这两个字段，fixture 要跟接口契约一致；该文件测的是游客和用户的区别，不受影响。

## 对账表（变异已在 `a8235c04` 上预跑，22/22 全红）

| 变异 | 结果 | 变异 | 结果 |
|---|---|---|---|
| M1 目录去掉对端分支 | RED（9） | M12 对端卡可点赞 | RED |
| M2 目录包含私密本地卡 | RED（2） | M13 对端卡不能 fork | RED |
| M3 目录包含已删本地卡 | RED | M14 视图不看来源 | RED（3） |
| M4 对端卡的 text_id 不为空 | RED（2） | M15 详情路由不带能力 | RED |
| M5 fork 拒绝对端源卡 | RED（5） | F1 「使用」无视能力 | RED |
| M6 fork 不去重 | RED | F2 点赞无视能力 | RED（2） |
| M7 fork 出公开卡 | RED | F3 标签页区块无视能力 | RED（2） |
| M8 purge 连带删 fork | RED | F4 评论输入框无视能力 | RED（2） |
| M9 他人私密卡可读 | RED | F5 版本 / 衍生标签无视能力 | RED |
| M10 卡详情不回落对端 | RED（3） | F6 举报无视能力 | RED（2） |
| M11 对端详情不取作者 | RED | F7 删除无视能力 | RED（2） |

预跑时有两个变异存活，已在发出前处理掉：

- **M8 第一版是「等价变异」**：purge 先删 `remote_cards`，之后再按它做子查询删 fork，什么也删不到，所以测试不会红。改为在删 `remote_cards` **之前**插入删 fork 的语句，就能打红。
- **F5 是真实的漏测**：remote 视图下 `comment` 和 `history` 同时为假，单独让 `history` 失效看不出效果。补了「comment 开、history 关」这条用例。

## 步骤

| 步 | 内容 | 产出 |
|---|---|---|
| S0 | 在当前 main 上逐条复核约束 1–9，逐条写「成立 / 不成立」。**有一条不成立就停下报告** | 复核表 |
| S1 | 从 bundle 建分支；main 如已前进，rebase 到最新 main | 分支 |
| S2 | 按「测试」一节跑，贴出数字 | 数字 |
| S3 | 开 PR，标题和描述用英文；分支 CI 绿了才合并 | PR |

新发现的问题如果属于本段改动面，直接修；只有会和其他线撞车、或需要 Shiyu 拍板时才停下报告。不允许自行记账。

## 测试

- 测试库：`docker compose -f docker-compose.test.yml up -d --wait`。如果与其他 worktree 冲突，可以像上一条线那样用一次性容器，并在报告里说明。
- 本地只跑受影响的文件：

```
pytest tests/test_cross_region_fork.py tests/test_cross_region_search.py tests/test_storage_scope_lock.py tests/test_storage_contract_shape.py tests/test_store_failure_visibility.py tests/test_postgres_store.py tests/test_published_copy_relation.py tests/test_published_from_backfill.py tests/test_storage.py tests/test_jwt_secret_lazy.py tests/test_cross_border_outbox.py tests/test_security_authz.py tests/test_market_publish_policy.py
cd web/frontend && npx eslint . -c eslint.ci.config.js --quiet && npm test
```

- 参考实现的实跑结果（沙箱，PG 16）：后端上面这组 **688 passed**；前端 lint 通过，`npm test` **64 个文件 / 317 条全过**。
- 合并门是分支 CI。**合并只做 git 操作，不跑测试、不等 CI。**

## 不在本 spec 内

- fork 血缘缺陷（发布副本不带 `forked_from`，导致 fork 列表查不到）和跨区 fork 列表 → P1b。
- 对端卡的点赞、评论 → P2；看对方空间 → P1c。规则表里对应的能力保持关闭，到时候只改 `CAPABILITIES`。

## 待 Shiyu 拍板

- B8 只是在 v3 里补了一句用途，**没有升版本号**。fork 会让原作者的公开作品长期留在另一个地区，可以算作用途扩展。建议在 P1c 升 `privacy_v4` 时一并重新征得同意；如果你认为这次就要升版，P1a 的上线就要等 v4。

## 补充

（空）
