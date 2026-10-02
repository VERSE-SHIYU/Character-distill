# Spec cross-region-user-search：账号目录 + 可见性规则表 —— 跨区搜到用户、对端主页、账号状态同步

> 放 `docs/specs/cross-region-user-search.md`。补充写进本文件末尾「补充」。
> 遵守 `docs/specs/SPEC-STANDARD.md`。本 spec 只写代码与测试，**不含任何部署 / 服务器操作**。
> **已取代**本 spec 之前的两版（参考提交 `08821eef`、`ef1aa260`）。第一版把规则散落在各处；第二版引入了数据库视图，并改了 SQLite。本版**只动 PG**，SQLite 保持 main 原样，唯一例外是 AGENTS.md 强制要求的孪生列迁移 `099`。

## 头部

| 项 | 值 |
|---|---|
| 分支起点 | main `a9dc3945`（PR #106） |
| 参考实现 | 分支 `feat/cross-region-user-search`，单提交 `da60ff57`；以 git bundle 交付：`cross-region-user-search.bundle`，sha256 `90193cd69c5b92538469440f6ab42f2cc7e9a18c01588e1960fc89c7308e7111`，前置提交 `a9dc3945` |
| 取用 | `git fetch <bundle路径> feat/cross-region-user-search:feat/cross-region-user-search` |
| 拍板记录 | 2026-10-02：①跨区要能搜到用户、点进主页（照小红书 / rednote）；②禁用状态走方案 A（资料同步带 `is_disabled`，主页显示封禁提示）；③不打补丁，按「账号目录 + 规则表 + 能力下发」重构，合并在库里完成（方案 A）；④**不用 SQLite**：只实现 PG |

## 要解决的问题

搜索栏搜不到其他地区的用户。两个库隔离，对端用户在本库只以 `remote_user_profiles` 存在，而搜索和作者主页都只查 `users` 表。同一个根因还导致：对端角色卡作者名为空（显示「匿名」，点进去 404）；私信页取不到对方地区；对端用户注销后资料不删；对端用户被禁用后本库不知道。

## 设计：三层抽象（每条规则只写在一个地方）

| 层 | 抽象 | 唯一位置 | 谁用 |
|---|---|---|---|
| 数据 | **账号目录**：SQL 常量 `_PUBLIC_ACCOUNTS` = 本地 `users` ∪ 对端 `remote_user_profiles`，同 id 以本地为准，`is_remote` 标来源，`is_disabled` 两边同口径。合并、去重、排序、LIMIT 都在库里完成 | `storage/postgres_store.py` 模块顶部 | `get_public_account` / `search_discoverable_accounts` |
| 规则 | **可见性规则表**：`view_of(账号, 查看者)` 算出 self / local / remote / disabled 四种视图，`CAPABILITIES` 给出每种视图的能力，`IDENTITY_FIELDS` 给出字段白名单 | `web/account_visibility.py` | `GET /api/market/author/{id}` |
| 展示 | **按能力渲染**：主页响应带 `view` 和 `capabilities`，前端只读这两个字段，缺省全关（default deny）；「跨区」标签收成 `<RegionTag>` 组件 | `AuthorPage.jsx`、`common/RegionTag.jsx` | 主页、搜索、私信顶栏 |

「能不能被搜到」必须在库里过滤，LIMIT 才准，所以不归规则表管。这条判据只在 `search_discoverable_accounts` 一处：禁用账号不可被搜到。

以后关注、粉丝列表、@提及要支持对端用户，读账号目录即可，不需要再写「先查本地、查不到再查对端」。

**为什么不用视图了**：方案 A 选视图的本意是「合并规则在库里只有一份」。SQLite 出局后，只剩 PG 一个后端，一个 SQL 常量就能做到这一点；而视图会带来新迁移，还会撞上列集锁（PG 侧读到视图的列，SQLite 侧没有对应的视图）。

**怎么做到「不碰 SQLite」**：新方法在 `StorageBase` 上声明为**非抽象**方法，基类缺省抛 `NotImplementedError`，只有 `PostgresStore` 覆写。这样 SQLite 无需实现任何东西，契约锁仍会逐格比对 PG 的签名（该锁对「被覆写的非抽象方法」也做比对）。代价：在 SQLite 上调用作者主页或搜索会明确报错。SQLite 已冻结、不部署，这个代价可以接受。

| 照抄来源 | 本 spec 的行为 |
|---|---|
| 小红书 / rednote：两地分库，公开资料和公开内容互通 | B1、B3 |
| 小红书：被封账号的主页显示「违反社区规范、内容无法查看」 | B4（本地、对端同一规则） |
| 隐私政策 3.2(1)：公开资料「用于相互发现与展示」 | 只用已同步的字段；3.2(1) 补上账号状态和搜索用途 |
| 3.2(6)：注销时副本一并删除 | B6 |

## 已查实的约束（坐标 @ main `a9dc3945`，S0 逐条复核）

1. 用户搜索只查本库：`storage/postgres_store.py:1060` `global_search`，`:1097-1098` `FROM users WHERE (username ILIKE $1 OR nickname ILIKE $1) AND is_disabled = 0`。路由 `web/routers/market.py:170`。
2. PG 文本搜索区分大小写：`postgres_store.py:1088` 用 `title LIKE $2 OR filename LIKE $2`，同函数其余两组用的是 `ILIKE`。
3. `remote_user_profiles`（`migrations_pg/012_remote_user_profiles.sql:1`）的字段是 id / username / home_region / avatar_data / created_at，**没有昵称**。
4. 作者主页只查本库：`market.py:213` `get_author`，`:222` 查不到就 404。
5. 私信页靠作者接口取对方地区：`web/frontend/src/components/PrivateMessageChat.jsx:319`，对端用户在这里 404。「跨区」标签在 `:469` 手写成 `<span className="dm-peer-tag">`。
6. 对端卡作者名写死为空串，全量扫描见下文：PG 3 处 + 详情回落 1 处，SQLite 2 处 + 详情回落 1 处。
7. `purge_remote_user_data`（`postgres_store.py:4780`）只删 `remote_cards`（`:4786`）和 `direct_messages`（`:4790`）。
8. `is_disabled` 只有 2 个写入点（全量扫描见下文），都由 `web/admin_user_ops.py` 调用：`set_user_disabled` 的本地入口在 `routers/admin.py:145`、跨节点入口在 `routers/inter_node.py:311`，`ban_user` 在 `:44`。
9. 资料同步在发送时读最新资料：`web/cross_border_sync.py:205` 组 payload，PG 的 `_enqueue_profile_sync`（`postgres_store.py:146`）在调用方事务里换版本戳。SQLite 侧没有对应的 helper，`create_user` 和 `update_user_avatar` 各自手拼 payload。
10. 接收端 `inter_node.py:277` 按字段名逐个取值。新旧版本混跑时两个方向都兼容：新字段会被旧接收端忽略，旧发送端不带的字段在新接收端取缺省值。
11. 迁移最大号：PG `033`，SQLite `098`（`sqlite_store.py:155`）。PG 新增列必须有 SQLite 孪生迁移。
12. 资料载荷的字段集有全等锁：`tests/test_profile_outbox.py:123`。
13. SQLite 的 `global_search` 在 main 上本来就是坏的（括号包带 LIMIT 的 UNION）。属于 SQLite 专属问题，按 AGENTS.md:53 不修。
14. 契约锁 `tests/test_storage_contract_shape.py`：抽象方法在两个实现里必须逐格相同；`StorageBase` 上的**非抽象**公开方法只比对「被某个实现覆写的」，没覆写的不要求必须覆写。所以只在 PG 覆写的新方法不会让 SQLite 报红，PG 的签名仍然受这把锁检查。
15. 列集锁 `tests/test_postgres_store.py::TestPgFreshSchemaClosure` 要求两侧真库的列集相等、不许开豁免，所以 PG 新增列必须补 SQLite 孪生迁移（AGENTS.md:53 的例外）。本版不建视图、不碰这把锁。

**全量扫描原文**（`git show a9dc3945:<file> | grep -n ...`）：

```
# '' AS author_name, '' AS author_avatar
storage/postgres_store.py:921   list_public_cards（tag 分支）
storage/postgres_store.py:957   list_public_cards（无 tag）
storage/postgres_store.py:1028  search_public_cards
storage/sqlite_store.py:1525    list_public_cards
storage/sqlite_store.py:1601    search_public_cards
# remote["author_name"] = ""
storage/postgres_store.py:768   get_market_card_detail 回落（SQLite 同函数同写法）
# UPDATE users SET is_disabled
storage/postgres_store.py:2574  set_user_disabled
storage/postgres_store.py:3093  ban_user_and_contents
storage/sqlite_store.py:3250    set_user_disabled
storage/sqlite_store.py:3829    ban_user_and_contents
```

**规模**：搜索每组最多 5 条（不变），LIMIT 在视图上执行，结果准确。对端主页的角色卡数等于该用户已同步的公开卡数，和本地主页一样不分页。`034` 回填条数等于本库禁用用户数，走现有 outbox 循环（每轮 100 条）。

## 行为

| 编号 | 行为 |
|---|---|
| B1 | 账号目录（只 PG）：`_PUBLIC_ACCOUNTS` 的列为 id / username / nickname / avatar_data / home_region / is_disabled / is_remote。`get_public_account(id)` 和 `search_discoverable_accounts(kw, limit)`（不区分大小写，匹配 username 或 nickname，排除禁用账号，按 username 排序）返回的 `is_disabled` / `is_remote` 都是 bool。`global_search` 的用户部分和卡片详情的作者回落都改为调这两个方法。这几个方法在 `StorageBase` 上为非抽象方法，缺省抛 `NotImplementedError`。 |
| B2 | 对端卡作者名统一经 `postgres_store.py` 的 `REMOTE_CARD_AUTHOR_JOIN` / `REMOTE_CARD_AUTHOR_COLS` 获取，替换 PG 的 3 处空串和详情回落；SQLite 的 2 处不动。PG 文本搜索改为 `ILIKE`。 |
| B3 | `get_author`：账号目录查不到 → 404；否则用 `view_of` 判定视图。remote 视图返回 `identity()` 白名单字段和 `get_remote_user_cards`；self / local 视图走原有逻辑（含对方的隐私设置）。所有响应都带 `view` 和 `capabilities`。粉丝、文本、动态、在线状态在对端库，remote 视图不返回（是缺失，不是 0）。 |
| B4 | disabled 视图：本地或对端账号被禁用、且查看者不是本人时，只返回白名单里的身份字段（id / username / home_region），`cards: []`，能力全关。本人看自己仍是 self 视图。 |
| B5 | 规则表（`CAPABILITIES`）：self = 全部能力，去掉 message 和 follow；local = 全部能力；remote = 只有 message 和 cards；disabled = 全部关闭。 |
| B6 | PG 的 `purge_remote_user_data` 补删 `remote_user_profiles`，返回的计数里多一个 `remote_user_profiles` 键。 |
| B7 | 账号状态随资料同步：发送方 payload 加 `is_disabled`（bool）；接收方按 `is True` 严格解析，缺省为 False，调 `upsert_remote_account` 一次写入资料和状态，每次同步都覆盖。旧方法 `upsert_remote_user_profile` 签名不变，在 PG 里改为委托 `upsert_remote_account(is_disabled=False)`。`set_user_disabled`（禁用、启用两个方向）和 `ban_user_and_contents` 在同一事务里重新入队，用户不存在时整个事务回滚、不入队。PG `034` 加列并回填已禁用用户；SQLite 只有 `099` 一行加列（AGENTS.md:53 的唯一例外，列集锁强制要求），`sqlite_store.py` 只多出迁移次序表里的一行登记。 |
| B8 | 前端：`AuthorPage` 只按 `capabilities` 渲染每个区块和动作，缺省全关；`view === 'disabled'` 时显示「该账号已被封禁，内容无法查看」，`view === 'remote'` 时显示 `<RegionTag>`。`<RegionTag>` 统一替换私信顶栏、搜索结果、主页三处的「跨区」标签。搜索请求失败时显示「搜索失败，请稍后重试」。 |
| B9 | 隐私政策 3.2(1)：同步字段加「账号状态」，用途补上「可被搜索」和「禁用后不再展示」。按 7.1 如实补述，不升版本号（先例 3.2(4)）。**是否算实质性变化、要不要重新征得同意，由 Shiyu 判断。** |

## 调用点矩阵（每格对应一个测试名）

后端：`tests/test_cross_region_search.py`（下表记为 X），`tests/test_profile_outbox.py`（记为 O）

| 调用点 \ 可观测输出 | 来源 / is_remote | 排除禁用 | 本地同 id 去重 | 大小写 | 昵称匹配 | 作者名 |
|---|---|---|---|---|---|---|
| `_PUBLIC_ACCOUNTS` + `get_public_account` | X `test_directory_resolves_local_and_remote` | — | X `test_search_local_row_wins_over_mirrored_remote_row` | — | — | — |
| `search_discoverable_accounts`（经 `global_search`） | X `test_search_finds_local_and_remote_users` | X `test_search_skips_disabled_remote_user` | 同上 | X `test_search_remote_is_case_insensitive` | X `test_directory_search_matches_local_nickname` | — |
| `global_search` 文本 | — | — | — | X `test_search_texts_is_case_insensitive` | — | — |
| 对端卡 4 个查询 + 详情回落 | — | — | — | — | — | X `test_remote_card_author_resolved_everywhere` |

| 调用点 \ 可观测输出 | view | capabilities | 字段白名单 | cards |
|---|---|---|---|---|
| `view_of`（6 种组合） | X `test_view_of` | — | — | — |
| 规则表 | — | X `test_capability_table` | — | — |
| `get_author` 对端 | X `test_author_page_of_remote_user` | 同左 | 同左 | 同左 |
| `get_author` 对端禁用 | X `test_author_page_of_disabled_remote_user` | — | 同左 | 同左 |
| `get_author` 本地禁用 / 本人 | X `test_author_page_of_disabled_local_user` | — | 同左 | — |
| `get_author` 本地 | X `test_author_page_of_local_user_carries_local_view` | 同左 | — | — |
| `get_author` 不存在 | X `test_author_page_of_unknown_id_is_404` | | | |

| 写入 / 同步点 \ 可观测输出 | 入队 | 载荷里的状态 | 落库 / 覆盖 |
|---|---|---|---|
| `set_user_disabled` 禁用→启用 | O `test_disable_and_enable_requeue_and_send_the_status` | 同左 `[False, True, False]` | — |
| `set_user_disabled` 用户不存在 | O `test_disable_unknown_user_queues_nothing` | — | — |
| `ban_user_and_contents` | O `test_ban_requeues_the_profile` | — | — |
| 迁移 034 | O `test_034_backfill_queues_disabled_users_only` | — | — |
| 载荷字段集（锁） | — | O `test_registration_queues_and_the_loop_sends_the_profile` | — |
| `/user/sync`（True / False / 缺省） | — | — | X `test_receiver_stores_disabled_flag` |
| upsert 来回覆盖 | — | — | X `test_resync_overwrites_disabled_flag_both_ways` |
| `purge_remote_user_data` | — | — | X `test_purge_removes_remote_profile` |
| `get_remote_user_cards` | — | — | X `test_remote_user_cards_only_that_user` |
| 新列 `is_disabled` 两侧一致 | `tests/test_postgres_store.py::TestPgFreshSchemaClosure`（未修改） | | |

前端：`web/frontend/src/components/__tests__/CrossRegionAuthor.test.jsx`

| 组件 \ 可观测输出 | 跨区标签 | 能力控制的区块 / 动作 | 提示 / 空态 |
|---|---|---|---|
| AuthorPage remote | `shows the cross-region tag…` | 同左：关注、使用、书架、动态、粉丝 | `does not render an offline time…`（在线状态） |
| AuthorPage disabled | — | `identity + notice only…` | 同左：封禁提示 |
| AuthorPage local | `keeps follow, bookshelf and stats` | 同左 | — |
| AuthorPage 缺能力 | — | `renders nothing actionable when capabilities are missing` | — |
| GlobalSearchBox | `tags peer-node users as cross-region` | — | `says the search failed…` / `still says nothing was found…` |

## 对账表（变异已在 `da60ff57` 上预跑，33/33 全红）

| 变异 | 结果 | 变异 | 结果 |
|---|---|---|---|
| M1 搜索排除对端 | RED（3） | M17 封禁不入队 | RED |
| M2 搜索不排除禁用 | RED | M18 034 回填不过滤 | RED |
| M3 目录不按本地去重 | RED | M19 重新同步不覆盖状态 | RED |
| M4 搜索区分大小写 | RED（2） | M20 对端卡不按作者过滤 | RED（2） |
| M5 文本搜索区分大小写 | RED | M21 目录把对端行标成本地 | RED（3） |
| M6 共用片段作者名写回 `''` | RED | F1 搜索失败被吞 | RED |
| M7 详情回落作者名写回 `""` | RED | F2 搜索不标跨区 | RED |
| M8 purge 不删资料 | RED | F3 在线状态无视能力 | RED |
| M9 主页不查账号目录 | RED（4） | F4 关注无视能力 | RED（3） |
| M10 非本地视图下发整份账号 | RED（3） | F5 「使用」无视能力 | RED |
| M11 规则表不拦禁用 | RED（4） | F6 书架无视能力 | RED（2） |
| M12 禁用优先于本人 | RED（2） | F7 私信无视能力 | RED（2） |
| M13 对端可关注 | RED | F8 角色区无视能力 | RED（2） |
| M14 接收端丢弃状态 | RED | F9 去掉封禁提示 | RED |
| M15 发送端不带状态 | RED（2，含字段集锁） | F10 统计无视能力 | RED |
| M16 禁用不入队 | RED | F11 主页不标跨区 | RED |
| M22 接收端改用不带状态的写法 | RED | | |

## 步骤

| 步 | 内容 | 产出 |
|---|---|---|
| S0 | 在当前 main 上逐条复核约束 1–15，逐条写「成立 / 不成立」；同时核对当前最大迁移号。**有一条不成立，停下报告** | 复核表 |
| S1 | 从 bundle 建分支（命令见头部）。main 如已前进，rebase 到最新 main；迁移号冲突时按「main 上现有最大号顺延」改号，孪生迁移（034↔099）一起改，`sqlite_store.py` 的登记行也同步改 | 分支 |
| S2 | 按「测试」一节跑一遍，贴出结果数字 | 数字 |
| S3 | 开 PR，标题和描述用英文；分支 CI 绿了才合并 | PR |

新发现的问题如果属于本段改动面，直接修；只有会和其他线撞车、或需要 Shiyu 拍板时才停下报告。不允许自行记账。

## 测试

- 测试库：`docker compose -f docker-compose.test.yml up -d --wait`（端口 55432，库名 `charsim_test`）。
- 本地只跑受影响的文件：

```
pytest tests/test_cross_region_search.py tests/test_profile_outbox.py tests/test_storage_contract_shape.py tests/test_store_failure_visibility.py tests/test_cross_border_outbox.py tests/test_cross_border_sync.py tests/test_postgres_store.py tests/test_migration_dispatch.py tests/test_schema_parity.py tests/test_sqlite_fresh_schema.py tests/test_admin_peer_disable.py tests/test_admin_user_ops.py tests/test_store_commit_contract.py tests/test_storage.py tests/test_identity_resolution.py tests/test_demo_gate.py tests/test_following_api.py
cd web/frontend && npx eslint . -c eslint.ci.config.js --quiet && npm test
```

- 参考实现的实跑结果（沙箱，PG 16）：
  - 后端上面这组 **748 passed**（SQLite 已回到 main 原样）。
  - 前端 lint 通过，`npm test` **63 个文件 / 313 条全过**（main 上是 62 / 305）。
  - SQLite：除 `099` 以外不做任何改动，也不做任何验证（AGENTS.md:53）。
- 合并门是分支 CI。**合并只做 git 操作，不跑测试、不等 CI。**

## 不在本 spec 内（需要时另行拍板）

- 对端角色卡的 fork：现状下市场详情页点「使用」也会 404，本次只是在规则表里关掉了 remote 视图的 fork。
- 关注对端用户：关注表只认本地用户。将来要支持时，改 `CAPABILITIES` 并给关注表接上账号目录即可。

## 补充

（空）
