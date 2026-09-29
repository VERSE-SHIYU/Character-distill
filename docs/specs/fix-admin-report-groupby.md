# Spec：管理端举报列表 PG 分组错误

基线：main `dd9f673`。只改 PG 存储层 + 补 PG 用例，不涉及路由、前端、部署。SQLite 已停止维护（`AGENTS.md:52`），本 spec 不改 SQLite、不为它写用例。
设计决定：无（SQL 语义修正，无分歧）。变异：**已在真 PG 上预跑**（见下）。

## 目标

`GET /api/admin/reports` 与 `GET /api/admin/card-reports`（`admin.py:485`）在 PG 上返回 200；返回字段名与结构不变。

## S0（先做，不过就停）

0. 在 worktree 路径 `.claude/worktrees/<name>/docs/specs/fix-admin-report-groupby.md` 上 `Test-Path` + 与源文件 hash 比对，不通过即停。
1. 逐条复核下表；任一不符即停并报告。
2. 起 PG 前查 `55432` 端口与 `character-distill-test` 项目名是否被别的 worktree 占用。

## 已查实的约束（基线 `dd9f673`）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 评论举报查询 SELECT 了 `r.card_id`，GROUP BY 只有 `r.comment_id, c.content, c.user_id, c.username` → `GroupingError: column "r.card_id"…`（Sentry 事件同文本；**真 PG 复现，见证据**） | `storage/postgres_store.py:3868`、`:3878` |
| C2 | 卡片举报查询 `GROUP BY r.card_id`，SELECT 了 `c.name`、`u.username` → `GroupingError: column "c.name"…`（**真 PG 复现**，此前只是推断，现已证实；线上尚无报错，因还没人点开该页） | `postgres_store.py:3940-3949` |
| C3 | SQLite 孪生查询允许裸列，已停止维护，**不动** | `storage/sqlite_store.py:4634`、`:4713`；`AGENTS.md:52` |
| C4 | 调用方 | `web/routers/admin.py:441`（评论）、`:485`（卡片） |
| C5 | 主键：`card_comments.id`、`cards.id`、`users.id`；`card_comment_reports.card_id NOT NULL` FK→cards；`card_comments.card_id NOT NULL` | `storage/migrations_pg/001_init.sql:35-36`、`:90-91`、`:202-214`、`:303-314` |
| C6 | 现有测试没有任何用例在 PG 上执行这两个方法（scope-lock 只登记名字）——这才是它上线才炸的原因 | `tests/test_storage_scope_lock.py:297`、`:323` |
| C7 | `add_comment_report` 的 `card_id` 由调用方传入，可能与评论自身 `card_id` 不等；**采用 `c.card_id` 后不影响**：异常数据下仍是「一评论一行」（真 PG 实测：3 条举报合并为 1 行，`r.card_id` 分组反而会拆成 2 行） | `postgres_store.py:3818-3828` |
| C8 | PG 测试编排：项目名固定 `character-distill-test`，宿主端口固定 `55432`（并行 worktree 会撞） | `docker-compose.test.yml:23`、`:33` |

## 出处对照表

PostgreSQL 文档「SELECT → GROUP BY」中关于「主键可推出同表其他列」的条目：**沙箱未放行 postgresql.org，未能拉取原文**。本 spec 的依据改为真库实跑证据（下）；S0 补链接与章节，填进本表。

| 依据 | 行为 |
|---|---|
| 真库：按 `c.id`（PK）分组可直接取 `c.content/user_id/username/card_id` | B1 评论查询 |
| 真库：`r.card_id, c.id, u.id` 分组可取 `c.name`、`u.username` | B2 卡片查询 |

## 全量扫描原文

命令：`grep -n "GROUP BY" storage/postgres_store.py`
```
2460:  GROUP BY created_at::date
3260:  ... GROUP BY created_at::date ORDER BY date DESC LIMIT 30
3266:  ... GROUP BY action
3275:  ... GROUP BY model
3299:  GROUP BY u.id
3878:  GROUP BY r.comment_id, c.content, c.user_id, c.username
3949:  GROUP BY r.card_id
```
结论：仅 3878、3949 有问题；3299 按 `users.id` 主键分组，可推出 `u.username`，无问题。

## 预跑证据（本地 PG，`storage/migrations_pg/*.sql` 全量建表，脚本可复现）

- 原查询：评论 → `GroupingError r.card_id`；卡片 → `GroupingError c.name`
- 修后查询（同一份数据）：评论 1 行 `report_count=2, reasons='spam | spam', first_reported_at=较早者`；卡片 1 行 `report_count=2`，字段名与原一致

## 约束

- 返回字段名不变：`comment_id, card_id, comment_content, comment_author_id, comment_author_name, report_count, reasons, first_reported_at`；卡片侧 `card_id, card_name, card_author_name, report_count, reasons, first_reported_at`。
- 不用「补列进 GROUP BY 或包 MAX() 过关」；按实体主键分组，其余列由主键推出。
- 不新增抽象。

## 步骤（每步独立 commit）

1. [存储层·PG] 评论查询：`c.id AS comment_id`、`c.card_id`，`GROUP BY c.id`。
2. [存储层·PG] 卡片查询：`GROUP BY r.card_id, c.id, u.id`。
3. [测试] 加进现有 PG 套件 `tests/test_postgres_store.py`（无 `DATABASE_URL` 时该文件恒为 error，见 `AGENTS.md:180`），用例见矩阵。

## 调用点矩阵（行 = 调用点，列 = 可观测输出，格 = 测试名；全部新增）

| 调用点 | 字段名与结构 | `report_count` | `reasons` | `first_reported_at` | 空结果 |
|---|---|---|---|---|---|
| `admin.py:441` → `get_comment_reports_grouped` | `test_comment_reports_grouped_shape` | `test_comment_reports_grouped_counts_duplicates` | 同左 | `test_comment_reports_grouped_first_reported_is_min` | `test_comment_reports_grouped_empty` |
| `admin.py:485` → `get_card_reports_grouped` | `test_card_reports_grouped_shape` | `test_card_reports_grouped_counts_duplicates` | 同左 | `test_card_reports_grouped_first_reported_is_min` | `test_card_reports_grouped_empty` |

路由本身只透传，HTTP 200 由方法级用例间接覆盖，不另写路由用例。
测试数据要求：同一评论/卡片 2 条举报，**理由相同、创建时间倒序**（否则 M3、M4 杀不死）。

## 对账表（变异，已预跑；执行端在分支上按同一数据复跑，存活即不合并）

| 变异 | 预跑结果 | 被谁杀死 |
|---|---|---|
| M1 去掉 SELECT 的 `card_id` | 字段缺失 | `*_shape` |
| M2 评论 GROUP BY 退回 `r.comment_id` | PG 报 `GroupingError c.id` | 全部评论用例 |
| M2b 卡片 GROUP BY 退回 `r.card_id` | PG 报 `GroupingError c.name` | 全部卡片用例 |
| M3 `COUNT(*)`→`COUNT(DISTINCT r.reason)` | `report_count` 2→1 | `*_counts_duplicates` |
| M4 `MIN`→`MAX` | `first_reported_at` 变为较晚者 | `*_first_reported_is_min` |

## 规模表

| 数据源 | 上限 | 策略 |
|---|---|---|
| 待处理举报（两个端点） | SQL 无 LIMIT、路由无分页，行数随举报量增长 | 本次不改（改了就是新功能）；管理员页数据量小，记入交付报告即可 |

## 测试

- 本地只跑 `tests/test_postgres_store.py`（docker PG）；不涉及前端，无需 `npm test`。
- 合并门槛是分支 CI；**合并只做 git 操作，不跑测试、不等 CI**；执行报告不出现本地全量数字。

## 交付

- 报告开头先列本段改动的文件清单。
- 新发现：属于本段改动面的直接修；只有会撞车或需拍板时才停下报告，不自行记账。
