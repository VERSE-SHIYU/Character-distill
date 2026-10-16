# 保存蒸馏草稿，改了转卡规则可免费重新转卡（E7）

- 日期：2026-10-16。基线：`main` @ `a9da1b85`（PR #167 合并后）。下面所有坐标都是这个提交上的。
- 分支：`feat/distill-draft-persist`（worktree `.claude/worktrees/distill-draft-persist`）。
- 依据：《阶段锚点核对失效：调查报告》「接下来要查什么」E7 一行。Shiyu 10-16 同意「一个个来」。
- 交付：一个 PR（英文标题/描述/commit）。不写部署步骤。不调真模型、不花钱。

## 1. 这份 spec 解决什么、不解决什么、分几段

| 解决 | 不解决 |
|---|---|
| 蒸馏时把模型产出的草稿存下来（第 1 段，本 PR） | 已有旧卡：草稿当时没存，只能再蒸一次（之后不用再蒸） |
| 改了转卡规则后，用存下的草稿重新转卡，不重蒸（第 2 段，下一个 PR，本文只写轮廓） | `/api/distill/run`（同步通道，`TextManager.get_or_distill`）不存草稿：草稿在 `Distiller` 内部转完就丢，前端不走这条（`web/frontend/src/store/useAppStore.js:873` 只调 `/api/distill/start`） |
| | 模型自己没写进草稿的内容：在卡片编辑页手动补 |

分段：**第 1 段存草稿（本 PR）→ 复核 → 合并上线 → 重蒸祈灼一次（存下草稿）→ 第 2 段重新转卡（另写 spec）**。第 1 段对用户无可见变化。

## 2. 已查实的约束（S0 逐条核对 @ `a9da1b85`）

按「判断清单」逐条读代码，全部与原文一致。

| # | 事实 | 坐标 @ a9da1b85 | S0 |
|---|---|---|---|
| J1 | 转卡只经 `card_from_draft` 一处，入参是草稿 dict 与整本原文 | `core/card_draft.py:412` `def card_from_draft(data: Any, source_text: str) -> CharacterCard`；`:424` `draft = CardDraft.model_validate(data)` | ✅ |
| J2 | 后台通道（前端用的 `/start`）：补完关系后转卡、后置、落卡；草稿是局部变量 `data`，角色名是 `name` | `web/routers/distill.py:546` `card = card_from_draft(data, content)`；`:555` `distiller.finalize_card(card, content, name, aliases, chars)`；`:577` `tm.save_distilled_card(text_id, card, user_id, …)` | ✅ |
| J3 | SSE 通道 `/run_stream` 同形，角色名是 `char_name` | `web/routers/distill.py:1178` `card_from_draft, data, content`；`:1190` `card, content, char_name, aliases, chars)`（`finalize_card` 的入参）；`:1198` `text_manager.save_distilled_card(` | ✅ |
| J4 | 两条通道都经 `save_distilled_card` 落卡；`save_card` 按 text_id+name+user_id upsert（重蒸写回同一行） | `core/text_manager.py:573-587`；`storage/postgres_store.py:686-706` | ✅ |
| J5 | 不来自草稿的卡字段已有唯一清单 | `core/schema.py:350` `POST_FORMAT_FIELDS = ("tags", "awakening_message", "dialogue_examples")` | ✅ |
| J6 | 后置步骤里「贴对话示例」**调模型**（挑编号）；去重与核对引文不调模型 | `core/distiller.py:2037-2040`（`dedupe_relationship_targets` → `retract_unverified` → `attach_dialogue_examples`）；`:1997` `picked = self._pick_dialogue_examples(...)` | ✅ |
| J7 | 落卡前的注入审核调模型，会把被判注入的字段清掉再存 | `core/text_manager.py:583` `verdict = await self._guard_card(card)`；`core/moderation/card_guard.py:206` `judge_card(card_obj.model_dump(), llm, …)` | ✅ |
| J8 | 存储先例：一列 JSON 文本 + `save_*` / `load_*_unscoped`，PG 与 SQLite 孪生迁移，列集锁要求两侧相等 | `storage/migrations_pg/040_history_digest.sql`；`storage/base.py:802-814`；`storage/sqlite_store.py:169-172`；`tests/test_postgres_store.py:1343` | ✅ PG 孪生 040 ↔ SQLite 105；SQLite 迁移文件在 `storage/migrations/`（非 `migrations_sqlite/`），列表末尾 106 → 新号 107；PG 侧按 `migrations_pg/` 目录自动发现（`postgres_store.py:24` 记账式执行器），文件尾 041 → 新号 042 |
| J9 | 读卡方法写明列名，新列不会跟着卡片行返回前端 | `storage/postgres_store.py:751`、`:768`（`get_card_unscoped` / `get_card_owned` 的 SELECT 列表） | ✅ |
| J9b | 导出只读 `card_json` | `web/routers/distill.py:1466` `CharacterCard.model_validate_json(record["card_json"])` | ✅ |
| J9c | 生产代码调 `*_unscoped` 要登记进白名单并写明为何没有身份；`tests/` 整目录豁免 | `tests/test_storage_scope_lock.py:18-19`、`:32`、`:193-194` | ✅ |
| J10 | 原文不可改（只改封面、名单、可见性、软删） | `storage/postgres_store.py` 里 `UPDATE texts` 只有 `:408`（cover_data）、`:475`（characters_json）、`:521`（deleted_at 软删）、`:550`（deleted_at 恢复）、`:5142`（visibility） | ✅ |

### 改动面内的连带点（S0 一并查实）

| # | 事实 | 坐标 @ a9da1b85 |
|---|---|---|
| C1 | 变异驱动用整段源码文本作锚点，钉住两条 `save_distilled_card` 调用 | `tests/perf/arc_phase_anchoring_mutations.py:47`（`_SAVE_CALL`，含整段调用到 `)`）与 `:151`（`_SAVE_CALL + _SAVE_CALL`）；`tests/perf/card_draft_mutations.py:280-283`（M33，前缀锚点到 `user_id,\n`） |
| C2 | 锚点锁要求每个 `repl` 锚点在当前源码上恰命中一次 | `tests/test_mutation_anchors.py:80-81` |
| C3 | `save_distilled_card` 的替身签名（必填补） | `tests/test_card_draft.py:337`、`tests/test_distill_task_api.py:308`、`tests/test_phase_anchoring.py:535`；`tests/test_identify_failure_channels.py:533` 用 `**kw` 不必改 |
| C4 | 直接调**真** `save_distilled_card` 的用例（必填补） | `tests/test_arc_positions.py:375`（`_save`，被 `tests/test_arc_phase_select.py:520` 复用）、`tests/test_session_rag_binding.py:572` |
| C5 | `save_distilled_card` 里补位置调度由源码锁钉住（函数体内须出现 `need_positions` / `has_positions`） | `tests/test_arc_phase_select_locks.py:124-129` |
| C6 | 「失败不影响主流程」的唯一构造 | `core/nonfatal.py:69-96` `nonfatal(source, what, *, level=ERROR)`；范例 `core/examples_pending.py:49` |

## 3. 目标检查（第 1 个提交就带上）

文件 `tests/test_distill_draft_persist.py`，用真实路径（`TestClient` 打 `/api/distill/start`，模型用现有测试替身）蒸一张有阶段的卡，然后：

1. 库里这张卡有草稿：`load_card_draft_owned(card_id, user_id)` 不为空，解出 `{"character": <蒸馏时的角色名>, "draft": <转卡前的草稿>}`。
2. **用存下的草稿能还原出同一张卡**：`card_from_draft(draft, 原文)` 依次过 `dedupe_relationship_targets`、`retract_unverified` 后，与库里的卡相比，除 `POST_FORMAT_FIELDS` 外的字段全等。这条证明存下的就是那次转卡真正用的草稿（没被转卡过程改过、没存错时刻）。
3. 同一本书同一角色再蒸一次，草稿被新的一次覆盖（与卡同行 upsert）。
4. `get_card_owned` / `get_card_unscoped` 返回的行里没有草稿列；导出接口（`/cards/{card_id}/export`）的内容里也没有。
5. 写草稿失败不连累落卡（对账表末行）：替身存储的 `save_card_draft` 抛错时，`/start` 照常返回、卡照常落库，且 `core.nonfatal` 上有一条 ERROR 上报（`caplog` 抓 `save card distill draft`）。

SSE 通道 `/run_stream` 同样做第 1 条。

## 4. 改法

1. **存储**（照 J8 的先例）：
   - PG 迁移 `042_card_distill_draft.sql`：`ALTER TABLE cards ADD COLUMN IF NOT EXISTS distill_draft TEXT;`；SQLite 孪生 `107_card_distill_draft.sql`，登记进 `sqlite_store.py` 的迁移列表。注释写明：NULL = 没有草稿（旧卡、同步通道、复制出来的卡）；只由 `save_card_draft` 写；读卡方法不投影这一列。
   - `StorageBase` 加 `save_card_draft(card_id, draft_json)` 与 `load_card_draft_owned(card_id, user_id) -> str`（按 `cards.user_id` 过滤；没有返回 `""`），PG 与 SQLite 各实现一份。用 `_owned` 不用 `_unscoped`：第 2 段的读者是属主接口，有身份可传，不必进白名单（J9c）。
2. **写入只在一处**：`TextManager.save_distilled_card` 增加必填关键字参数 `draft: dict` 和 `character: str`，在拿到 `actual_card_id`（`text_manager.py:587`）之后写 `{"character": character, "draft": draft}`。两个调用点（J2、J3）把各自的 `data` 和角色名（`name` / `char_name`）传进去。
   - 写草稿失败不让落卡失败（卡已存好，少的只是以后免费重转的机会），用现有 `nonfatal` 上报（`core/examples_pending.py:49` 同款）。
3. 草稿存的是**审核前**的模型产出，只在服务端用、不返回前端；第 2 段重转时必须重新过注入审核（J7），写进第 2 段 spec。

变异驱动的锚点跟着源码改（C1）：`_SAVE_CALL` 补上新增的两行实参，M33 前缀锚点原本仍恰命中一次，但它的替换体要一并补实参，免得变异后的「重复落卡」因为首个调用缺必填参而 TypeError、红得不是它声称的形态。

## 5. 结构自检

| 问题 | 答案 |
|---|---|
| 草稿在几处写 | 一处：`save_distilled_card` |
| 调用方要知道怎么存吗 | 不要：只把草稿和角色名交给 `save_distilled_card` |
| 以后再加一条产卡通道要改几处 | 0 处：参数必填，经 `save_distilled_card` 落卡就自动存 |
| 存储是不是新起一套 | 不是：照 `history_digest` 的一列 + 一对方法 |

## 6. 不能破的现有原则

| 原则 | 出处 | 本 PR 怎么守 |
|---|---|---|
| 转卡只经 `card_from_draft` | `card_draft.py:13-14` 模块说明 | 不改转卡，只存它的入参 |
| 两侧列集相等 | `tests/test_postgres_store.py:1343` | PG 042 + SQLite 107 孪生 |
| 卡片行不带内部列 | J9 | 读卡 SELECT 不加 `distill_draft` |
| 注入内容不进返回前端的卡 | `text_manager.py:581-583` 注释 | 草稿不出服务端；第 2 段重转必过审核 |
| 重蒸覆盖原卡 | Shiyu 2026-09-23 产品规则 | 草稿随卡 upsert 覆盖 |

## 7. 对账表（独立复核方验，自补放宽、过严两个方向的变异）

| 行为 | 守它的测试 | 让它变红的变异 | 触发状态 |
|---|---|---|---|
| 落卡存草稿 | 目标检查 1 | 删掉写草稿那一行 | 后台通道蒸卡 |
| 存的是转卡用的那份 | 目标检查 2 | 存 `fill_relationships` 之前的 `data` 副本 / 存 `card.model_dump()` | 有关系、有阶段的草稿 |
| 角色名是蒸馏时那个 | 目标检查 1 | 存 `card.name` | 角色名与卡名不同的替身 |
| 重蒸覆盖 | 目标检查 3 | 只在 INSERT 时写草稿 | 同角色蒸两次 |
| 不出服务端 | 目标检查 4 | 读卡 SELECT 加上该列 | 读卡 / 导出 |
| 写草稿失败不连累落卡 | 目标检查 5 | 去掉 nonfatal 包裹 | 替身存储写草稿抛错 |

## 8. 第 2 段轮廓（下一个 PR，另写 spec，现在不做）

`POST /api/distill/card/{card_id}/rebuild`（属主、带 `revision` 版本核对，同 `examples/refind`，`web/routers/distill.py:1335-1366`）：读草稿与原文 → `card_from_draft` → `finalize_card`（与蒸馏同一套后置，含一次挑示例的小调用）→ 从当前卡带过 `tags`、`awakening_message` → 重过注入审核 → 比较后写入 → 重记「待补」。会覆盖手动编辑，前端要二次确认。花费：挑示例 + 审核各一次小调用，不重读整本书。细节等第 1 段上线后按代码再核。

## 9. 测试

本地只跑受影响的文件：`tests/test_postgres_store.py`、`tests/test_distill_task_api.py`、`tests/test_card_draft.py`、`tests/test_identify_failure_channels.py`、`tests/test_session_rag_binding.py`、`tests/test_arc_positions.py`、`tests/test_arc_phase_select.py`、`tests/test_arc_phase_select_locks.py`、`tests/test_phase_anchoring.py`，**加 `tests/test_storage_scope_lock.py`、`tests/test_mutation_anchors.py`**（`tests/perf/` 下两份变异驱动里有钉 `save_distilled_card` 调用的锚点，10-16 #168 就是漏跑它红的）。库用 docker PG，合并门是分支 CI。

- 测试替身照生产：替身 `save_distilled_card` 的签名跟着改；替身草稿用 `CardDraft` 真实形态。
- 新增：上面「目标检查」5 条（含 SSE 通道第 1 条）。
- 另跑（列外但同改动面）：`tests/test_schema_parity.py`、`tests/test_sqlite_fresh_schema.py`、`tests/test_migration_dispatch.py`、`tests/test_pg_gate.py`（迁移/列集侧的既有门）。

## 10. 技能

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 按行号读代码 |
| 改动 | `@tdd` | 先写目标检查再改 |
| 交付 | `@verification-before-completion` | 贴受影响测试原始输出 |
