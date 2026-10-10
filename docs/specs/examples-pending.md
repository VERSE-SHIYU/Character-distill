# 待补对话示例：缺示例时弹出编辑页，可让系统重新找一次

- 日期：2026-10-10（本文件取代 21:15 发出的同名文件；那一版作废，差别见 §13）
- 基线：`main` @ `3e61cd6`
- 前置 spec：`docs/specs/dialogue-fallback.md`（保底出卡）、`docs/specs/edit-card-modal-form.md`（编辑弹窗第 1 段）
- 属于：共享记录「蒸馏遗留：问题与调查记录」方案第 6 项的第 2、3 段，外加 Shiyu 10-10 点名一起做的三条
- 分支：从最新 main 开 `feat/examples-pending`
- 参考实现：随本 spec 交付两份改动文件（Claude 在沙箱 `3e61cd6` 上的原型，已跑通 §1 的全部目标检查）。只供对照，判据以 §1、§4 为准
  - `goal-checks-examples-pending.diff`：6 个目标检查文件
  - `proto-examples-pending.diff`：其余全部改动
- 执行方 skill：`@tdd`

## 1. 目标与目标检查

**定好的目标（Shiyu 2026-10-10，原话整理）**

1. 蒸馏出来的卡如果缺对话示例，打开这张卡时自动弹出编辑页面，让用户自己填。
2. 用户填不出来，编辑页面里有一个按钮：让系统重新找一次并填上。
3. 这个按钮只能用一次。
4. 重新找了还是没有，就认定这张卡没有，不再提醒。
5. 聊天时没有就跳过，照常聊。

补充：提醒只针对新蒸馏出来的卡，只有蒸馏好的这一次（旧卡不弹，换设备不再弹，不靠浏览器记忆）。真的调了模型才算用掉那一次；调用出错不算；不花钱就能判断找不到的，直接认定没有。弹窗关掉之后不再留提示，用户想补仍可自己点「编辑」。**修的过程不能影响原有卡片的建立。**

**一起做的三条（都在同一个编辑页上）**

- A. 编辑页开着时这张卡在别处变了：保存不能悄悄盖掉，要报冲突。角色页换到另一张卡时，编辑页跟着关。
- B. 保存（或重新找）没结束时不让关编辑页：关了，失败提示就没处显示。
- C. 广场卡的编辑保存加版本锁。只锁「编辑保存」；「恢复版本」「更新发布」本来就是整张覆盖，不加。

**不做**：对话示例 600 字上限不改（§4.8）。

**目标检查**（随本 spec 交付，第 1 个提交就带上，之后每个提交都跑）。都走真实路由 / 真实组件，期望值是字面量。

| 文件 | 条目 | 在 `3e61cd6` 上 |
|---|---|---|
| `tests/test_examples_pending_goal.py` | P1–P17，加 P1b、P3b、P4b、P4c、P4d、P11b，共 23 条 | 22 红 1 绿（绿：P17 聊天那条，回归守卫） |
| `tests/test_market_edit_lock_goal.py` | L1–L6 | 4 红 2 绿（红：L1、L4、L5、L6） |
| `web/frontend/src/components/__tests__/ExamplesPendingModalGoal.test.jsx` | M1–M9、M6b、M7b | 11 红 |
| `web/frontend/src/components/__tests__/CharCardExamplesPendingGoal.test.jsx` | C1–C8，加 C3b、C7b | 7 红 3 绿（绿：C2、C3b、C6） |
| `web/frontend/src/store/examplesPending.test.js` | 7 条 | 6 红 1 绿（绿：「挪动」那条） |
| `web/frontend/src/components/__tests__/MarketCardDetailEditLock.test.jsx` | K1 | 1 红 |

以上是 Claude 在沙箱干净的 `3e61cd6` 上实跑的结果；原型上全部绿。

## 2. 现状

- 蒸馏没配上示例时卡照常保存（保底出卡，PR #130），只在完成文案里说了一句。之后没有任何入口提醒用户补，也没有办法让系统再找一次。
- 角色页保存时带的是保存那一刻的最新版本号（§3 第 16 条），所以编辑页开着时卡变了，旧内容也拦不住。
- 广场卡的保存接口不核对版本，存储层无条件写入（§3 第 21 条）。

## 3. 已查实的约束（`文件:行 @ 3e61cd6`，每条都是本轮读过的行；S0 逐条复核，不成立即停）

前端路径省略前缀 `web/frontend/src/`。

**蒸馏与落卡**

1. 前端只用后台任务这一条蒸馏通道：`store/useAppStore.js:876` `postJSON('/api/distill/start', …)`。
2. 三条产卡通道落卡都经 `TextManager`：`core/text_manager.py:582`（`save_distilled_card`，后台任务 `web/routers/distill.py:575`、SSE `:1187` 调它）和 `:489`（`get_or_distill`，`/run` 在 `web/routers/distill.py:732` 调它）。`save_card` 对同文本同名是写回同一行：`:584` `actual_card_id = result_card.get("id") or card_id`。
3. 有没有示例只有一处判据：`core/schema.py:318` `has_dialogue_examples`（顶层或任一阶段下有一组就算有）。
4. 贴示例是 `Distiller.attach_dialogue_examples`（`core/distiller.py:1937`），它的注释已写明「只重跑这一步的入口直接调本方法」（`:1950`）。有起点的卡：顶层清空（`:1970`），阶段下是**追加**（`:1974-1975` `setdefault(…).append(text)`）；没有起点的卡：顶层整体赋值（`:1977`）。
5. 找不到时抛 `DistillError`：名单里没有别人（`:1836`）、原文里没有这个角色的对话句（`:1840`）、模型没选出可用的（`:1933`）。前两种在调模型之前。调用模型出错抛的不是它：`adapters/llm_adapter.py` 不 import、不抛 `DistillError`（全文两处都在注释里，`:499`、`:636`）。
6. 名单：`core/character_roster.py:98` `resolve_characters`，命中缓存直接返回（`:110`），没命中才调模型识别（`:112`）。

7. **卡上的名字是模型写的，代码不校正。** 格式化模板让模型自己填 `"name": "角色名"`（`core/distiller.py:274`），提示词里点了角色名（`:428`、`:447`、`:1669`、`:1702`），转卡时照模型写的收（`core/card_draft.py:388`），落卡用的也是它（`core/text_manager.py:490`、`:582` 的 `card.name`）。全量扫描 `grep -rnE "\[.name.\] *=[^=]|\.name *=[^=]" core web/routers --include=*.py` 输出为空：没有任何地方把它改回蒸馏时指定的角色名。而蒸馏时贴示例用的是**任务的角色名**，不是卡上的名字：`web/routers/distill.py:553`（后台任务）、`:1178`（SSE）、`core/text_manager.py:479`（`/run`）。所以重新找不能用卡上的名字。
8. **名单缓存只会因为识别版本升级而失效。** 缓存在 `texts` 行上，只有一处写（`storage/postgres_store.py:474`），版本不符当没有缓存（`:499`）；版本号是代码常量 `Distiller.IDENTIFY_VERSION = 4`（`core/distiller.py:688`）。三条通道蒸馏之前都先经 `resolve_characters` 把名单写好：`web/routers/distill.py:409`、`:1100`、`core/text_manager.py:467`。所以一张刚蒸馏出来的卡，名单缓存一定在；只有蒸馏之后、重新找之前部署了一个升了识别版本的新版本，才会没命中，那时 `resolve_characters` 按新口径识别一次并写回缓存，这是它的本意（`core/character_roster.py:23`）。
9. 落卡之后还要补做的事，现成的先例是苏醒台词：两条通道各在落卡后调一次同一个函数（`web/routers/distill.py:605`、`:1207` 调 `_persist_awakening`）。

**卡的内容与行上的状态**

10. 仓库的规矩是「不是卡的固有内容就不随卡落库」：`core/card_out.py:5`。
11. `card_json` 会被原样复制：复制卡（`storage/postgres_store.py:1200`，INSERT 逐列列出）、发布的版本快照（`:5218`、`:5256`）、对端同步（`:4842`）、原样导出（`web/routers/distill.py:1406`）。
12. 加列的现成做法：`storage/migrations_pg/035_session_arc_phase.sql:14`、SQLite 孪生 `storage/migrations/100_session_arc_phase.sql:3`、次序表 `storage/sqlite_store.py:159`、专用写入方法。规矩见 `AGENTS.md:53`（存储改动只保证 PG；PG 加列时补 SQLite 孪生迁移，两侧列集锁要求相等）。
13. 路由测试的 `store` 夹具是 SQLite：`tests/test_identify_failure_channels.py:105-106`。所以 SQLite 这边的接口也要能跑。
14. 版本锁：路由核对 `web/routers/distill.py:1299-1300`，存储层比较后写入 `storage/postgres_store.py:716`；判据与文案 `core/card_out.py:31`、`:28`；出卡 `:40`。
15. 结构锁逐个列出了「哪些接口把卡返回给页面」：`tests/test_arc_phase_fields_locks.py:346-349`。新增接口要登记。

**聊天**

16. 聊天时的示例 = 阶段下的 + 顶层的（`core/card_layers.py:67` 登记为 `state` 列表），取前 3 组，没有就没有这一节（`core/context_engine.py:450-452`）。用户手填在顶层的，对有阶段的卡也生效。

**角色页与 store**

17. 角色页换卡时详情组件是同一个实例：`components/CharCard.jsx:130`、`:144` 的 `<CardDetail card={currentCard} …>` 没有 `key`；换卡只换 `currentCard`（`store/useAppStore.js:1138`）。
18. 角色页保存取的是此刻的卡：`components/CharCard.jsx:729-730` `await updateCard(card.id || card.card_id, cardJson, card.revision)`。
19. 打开一张卡的三条路都从 `cards` 列表里取对象：`components/CharCard.jsx:210`、`components/DistillTaskBar.jsx:65`、`components/AwakeningToast.jsx:26`；列表来自 `/api/distill/cards/by-text`（`store/useAppStore.js:837` 起的 `loadCards`、`:942`）。
20. store 写回服务端返回的卡只有一处：`store/useAppStore.js:1787` `_applyServerCard`；`updateCard`（`:1812`）、`moveUnlocated`（`:1824`）都经它。非 2xx 的响应带着后端的提示文字抛出：`api/client.js:154-160`。

**编辑页**

21. 编辑页现状：`components/EditCardModal.jsx:80` 外壳、`:85` 表单；保存时底子取的是此刻的 `data`（`:138` `...data`），只交回卡的内容（`:171` `await onSave(cardJson)`）；点遮罩和「取消」直接关（`:181`、`:274`）。三个宿主：`components/CharCard.jsx:949-954`、`components/TextPanel.jsx:786-805`、`components/MarketCardDetail.jsx`（见第 22 条）。

**广场卡**

22. 详情页读卡有两个接口：打开时读 `/api/cards/{id}/detail`（`components/MarketCardDetail.jsx:99`，后端 `web/routers/card.py:115-127`），保存 / 恢复版本之后重读 `/api/market/card/{id}`（`:427`、`:458`，后端 `web/routers/market.py:405-417`）。两个都不带版本号。
23. 保存接口 `PUT /api/market/{id}/publish`（`web/routers/market.py:537`）有三个调用方：编辑保存（`components/MarketCardDetail.jsx:440-443`）、恢复版本（`:414`）、角色页的更新发布（`components/CharCard.jsx:1010`）。存储层无条件写入（`storage/postgres_store.py:5245`），返回空时报 500（`web/routers/market.py:563`）。
24. 广场页自己拦了「保存中不让关」：`components/MarketCardDetail.jsx:87`、`:1154`。

**测试现状**

25. `components/__tests__/CharCardUnlocated.test.jsx:102` 的假弹窗只给 `onSave` 传一个参数。
26. Claude 在沙箱实跑：前端 `npm test` 在 `3e61cd6` 上是 94 个文件 500 条，原型上是 98 个文件 527 条，全过；CI 的 lint 命令退出码 0。后端全量在原型上只有 `tests/test_evidence_integrity.py::TestManifestEntries::test_code_sha_resolves` 一条失败，它在没改动的代码上同样失败（沙箱是浅克隆，缺历史提交），与本改动无关。

## 4. 设计

### 4.1 一条规则只写一处

| 规则 | 写在哪 | 谁用 |
|---|---|---|
| 「待补」何时记、何时清、怎么重新找 | 新模块 `core/examples_pending.py`（`mark_after_distill` / `settle` / `refind`） | 三条产卡通道落卡后各调一次 `mark_after_distill`；三个路由出口调 `settle`；重新找的路由调 `refind` |
| 「待补」存在哪 | `cards.examples_pending_for` 一列（空 = 不待补；非空 = 待补，值是蒸馏时用的角色名），只由 `set_card_examples_pending` 写 | 上面那个模块 |
| 怎么找示例、怎么贴 | 现有的 `Distiller.attach_dialogue_examples`，不另写 | 蒸馏流程和 `refind` |
| 卡变过没有 | 现有的版本锁（`card_revision` + 比较后写入） | 保存、挪动、重新找、广场卡编辑保存 |
| 表单编辑的是哪张卡、哪一版 | `EditCardModal` 的表单在挂载时记下，保存时交回 | 三个宿主都用它交回的，不各自取 |
| 保存或重新找没结束不让关 | `EditCardModal` 的表单 | 三个宿主；广场页自己那份删掉 |
| 示例在表单里的形态（拆、拼、各阶段下的取法） | 现有的 `utils/dialogueExamples.js` | 编辑页 |

以后加一个清掉「待补」的出口：调一次 `settle`。加一条产卡通道：落卡后调一次 `mark_after_distill`。加一个用编辑页的宿主：把交回的卡号和版本号传给保存。

### 4.2 存储：行上的一列

- PG 迁移 `036_cards_examples_pending.sql`：`ALTER TABLE cards ADD COLUMN IF NOT EXISTS examples_pending_for TEXT;`（可空，没有默认值）。
- SQLite 孪生 `101_cards_examples_pending.sql`，并登记进 `storage/sqlite_store.py` 的次序表。
- 语义：空 = 不待补；非空 = 待补，值是蒸馏时找示例用的角色名。
- `StorageBase.set_card_examples_pending(card_id, user_id, character: str | None) -> bool`：属主过滤在 SQL，返回是否写到了行。两个 store 各实现。**`save_card`、`update_card` 不碰这一列。**
- 读：`get_card_owned`、`get_card_unscoped`、`list_cards`（两条查询）的列清单加上这一列，两个 store 都加。`update_card` 返回的是 `get_card_unscoped` 的行，保存和挪动的响应因此带着这一列。

**为什么不放进 `card_json`**：它是这一行的流程状态，不是角色的内容（§3 第 10 条）；放进去会跟着卡被复制到别人的复制卡、发布的版本、导出文件里（§3 第 11 条），还要动 `CharacterCard`、登记表和蒸馏分组。放在行上，这三样都不用动，`save_card` 也不用动。

**为什么连名字一起记**：重新找要用蒸馏时的那个名字，而卡上的名字不保证和它一致（§3 第 7 条）。蒸馏时用的名字只有在落卡那一刻才知道，和「待补」一起写下，重新找时原样取回。

### 4.3 `core/examples_pending.py`

- `mark_after_distill(storage, card_id, user_id, card, character)`：`card.has_dialogue_examples()` 为假就记下 `character`，为真就清掉。每次落卡都重记（重新蒸馏同一个角色配上了，上一次的「待补」随之清掉）。记不上不让落卡失败：包在现有的 `core.nonfatal.nonfatal` 里上报。
- `settle(storage, card_id, user_id, row) -> dict`：是「待补」就清掉，返回清过的行。
- `refind(storage, distiller, record, user_id) -> CharacterCard | None`：名字取行上记下的那个；取原文和名单（`resolve_characters`），在线程里调 `attach_dialogue_examples`，入参与蒸馏时那一步相同。找到了返回贴好的卡；原文已不在，或它抛 `DistillError`，返回 `None`；其余异常（含名单识别失败）照实抛。只算结果，不写库、不清「待补」。

**三条产卡通道各在落卡后调一次 `mark_after_distill`**，传的是那条通道蒸馏时用的角色名：

| 通道 | 调用处 | 角色名 |
|---|---|---|
| 后台任务 | `_run_distill_task` 里落卡成功之后、生成苏醒台词之前；经 `submit_to_main_loop` 投递，外面再包一层 `nonfatal_sync`（投递超时也不让任务失败） | `name` |
| SSE | `distill_stream` 里 `save_distilled_card` 成功之后 | `char_name` |
| `/run` | `TextManager.get_or_distill` 里 `save_card` 之后（只在这次真的蒸馏了的分支里），卡号用 `save_card` 返回的那一行的 | `character_name` |

放在通道里而不是 `save_distilled_card` 里：它不知道蒸馏时的角色名；给它加参数要改两处现有调用、两个现有测试（`tests/test_arc_positions.py:375`、`tests/test_session_rag_binding.py:572`）和一份旧变异脚本的锚点（`tests/perf/arc_phase_anchoring_mutations.py:48-51`，`tests/test_mutation_anchors.py` 会核对它）。落卡后由各通道补做一步，与苏醒台词的做法相同（§3 第 9 条）。三条通道各有一条目标检查守着（P1、P3b、P4）。

`attach_dialogue_examples` 改成「贴 = 换掉」：有起点的分支里，贴之前先把各阶段 overlay 里的 `dialogue_examples` 清掉。新蒸馏的卡阶段下本来没有示例，蒸馏结果不变；同一张卡贴两次，结果与只贴后一次相同。

顺手改掉这个函数附近两处已经过期的注释和一个过期的测试名（改动文件里有）：`_pick_dialogue_examples` 注释里的「两处判据因此同源」和「任务是失败，不是落一张没有对话示例的卡」；`tests/test_distiller_dialogue_pick.py` 里名字带 `is_a_task_failure` 的那条。

### 4.4 路由（`web/routers/distill.py`）

- `PATCH /card/{id}`：写成功后调 `settle`。
- 新增 `POST /card/{id}/examples/refind`，请求体 `{revision}`，形状照 `move_unlocated_item`：
  1. 非属主 / 不存在 → 404；
  2. 不是「待补」→ 409「这张卡已经处理过了，不能再重新找」；
  3. 版本号不符 → 409（现有的冲突文案）；
  4. 没配 API Key → 503（现有文案）；
  5. `refind`：有结果就 `update_card`（比较后写入，失败 409）；
  6. `settle`；返回 `{ok, found, card}`，卡经 `out_card`。
  调用模型出错时异常从第 5 步抛出，走统一出口，到不了第 6 步，「待补」还在。
- 新增 `POST /card/{id}/examples/dismiss`：404 / `settle` / 返回 `{ok, card}`。
- 两个新接口登记进结构锁（§3 第 15 条）。

### 4.5 编辑页（`components/EditCardModal.jsx`）

- **打开时记下这张卡、这一版**：表单挂载时把 `{data, cardId, revision}` 记成 `opened`，之后读的、保存时用作底子的都是 `opened.data`；保存交回 `onSave(cardJson, { cardId, revision })`。新增 prop `revision`。
- **没结束不让关**：保存或重新找进行中，点遮罩和「取消」都不关；「保存」按钮同时禁用。
- **重新找一次**：新增可选 prop `onRefindExamples(opened) -> Promise<boolean>`。传了才显示顶部的提示和按钮（抽成只管显示的小组件 `ExamplesNotice`）。点击：表单改过就先 `window.confirm`；调用；成功后换一份新表单（外面加一层 `EditCardSession`，换 `key` 重新挂载），按找过之后的卡重新取初值，并显示结果（找到了 / 没找到）；出错则错误上屏，表单和按钮都留着。
- **各阶段下的示例**：在「对话示例」栏下面只读地列出来（取法 `examplesByPhase` 放进 `utils/dialogueExamples.js`）。有阶段的卡，找到的示例贴在阶段下，不显示用户会以为没找到。
- 为补示例而开时（待补，或刚找过），把「对话示例」那一栏滚到眼前。

重新找成功后换新表单这一步，依赖宿主在 `onRefindExamples` 返回之前已经把卡换成新的。角色页是这样的：store 先写回卡再返回（§4.6）。

### 4.6 角色页与 store

- `components/CharCard.jsx`：两处 `<CardDetail>` 加 `key`（卡号）。换卡即重新挂载，编辑页随之关掉，这个组件里其余按卡初始化的状态也不再串。
- `CardDetail`：`examplesPending = canWrite && Boolean(card.examples_pending_for)`；它变真时打开编辑页；关闭时如果仍是「待补」就调 `dismissExamplesPending`；保存用编辑页交回的卡号和版本号；把 `revision` 和 `onRefindExamples`（只在「待补」时）传给编辑页。
- `components/TextPanel.jsx`：传 `revision`，保存用交回的卡号和版本号。这里不弹、不给「重新找」的入口；在这里保存同样会清掉「待补」（后端规则）。
- store：`_applyServerCard` 把返回行上的 `examples_pending_for` 一并写回；新增 `refindExamples(cardId, revision)`（写回返回的卡，返回找到没有；出错抛给调用方）和 `dismissExamplesPending(cardId)`（本地先清，再告诉服务端；没告诉成只记一条 warn）。

### 4.7 广场卡编辑保存的版本锁

- 两个读卡接口（§3 第 22 条）的响应加 `revision`，用现有的 `card_revision`。
- `UpdatePublishRequest` 加可选的 `revision`。带了且不符 → 409（现有冲突文案），**排在发布预审之前**。
- `PostgresStore.update_published_card` 改成比较后写入（`AND card_json = $6`，用调用方读到的 `old_json`）；没写到就不落版本记录、返回 `None`；路由把原来的 500 改成 409。SQLite 不改（已冻结）。
- `components/MarketCardDetail.jsx`：把 `card.revision` 传给编辑页，编辑保存的请求带上交回的 `revision`；删掉 `editing` 这份状态和关闭时的拦截（§4.1）。恢复版本、更新发布不带版本号，照旧。

### 4.8 不改：600 字上限

`components/EditCardModal.jsx:31` 的上限只作用在输入框上；保存时不检查它（`:109-118` 只检查七个「每行一条」的字段）。所以系统写进去的内容超过 600 字时照样显示、照样能保存，只是不能再往里加字，这正是上限的本意。样本：Claude 用公版原文量了 9 个角色 28 条候选，单组最长 241 字，最长三组合计 304 字。样本之外未核实。

## 5. 改动面

新增：
- `core/examples_pending.py`
- `storage/migrations_pg/036_cards_examples_pending.sql`、`storage/migrations/101_cards_examples_pending.sql`
- §1 的 6 个目标检查文件

修改：
- `core/distiller.py`、`core/text_manager.py`
- `storage/base.py`、`storage/postgres_store.py`、`storage/sqlite_store.py`
- `web/routers/distill.py`、`web/routers/market.py`、`web/routers/card.py`
- `web/frontend/src/components/EditCardModal.jsx`、`CharCard.jsx`、`TextPanel.jsx`、`MarketCardDetail.jsx`
- `web/frontend/src/store/useAppStore.js`、`web/frontend/src/utils/dialogueExamples.js`、`web/frontend/src/styles/global.css`
- 测试：`tests/test_postgres_store.py`（新增一组真 PG 用例）、`web/frontend/src/utils/dialogueExamples.test.js`（`examplesByPhase`），以及 §6 的三处

不动：`CharacterCard` 和登记表、蒸馏分组、`save_card`、`finalize_card`、聊天、`utils/objectRows.js`、`ObjectListField.jsx`。

## 6. 现有测试要改的三处（都是因为契约变了，不是为了让它过）

| 文件 | 改什么 | 为什么 |
|---|---|---|
| `tests/test_arc_phase_fields_locks.py`（S15） | 两个集合里加上 `refind_dialogue_examples`、`dismiss_examples_pending` | 结构锁列举的是「把卡返回给页面的接口」，多了两个 |
| `web/frontend/src/components/__tests__/CharCardUnlocated.test.jsx:102` | 假弹窗保存时多交回 `{ cardId, revision }` | 真弹窗现在交回两样，宿主用第二样提交 |
| `tests/test_distiller_dialogue_pick.py` | 一条测试改名（§4.3） | 名字里说的「任务失败」已不成立 |

除此之外没有现有测试需要改（§3 第 26 条）。

## 7. 测试

- 本地只跑受影响的文件加 `npm test`，库用 Docker 起的 PG；再跑一次 CI 的 lint 命令（`npx eslint . -c eslint.ci.config.js --quiet`，在 `web/frontend` 下）。合并门是分支 CI；合并只做 git 操作。报告里不写本地全量的数字。
- 后端受影响的文件至少包括：两个目标检查、`tests/test_postgres_store.py`、`tests/test_arc_phase_fields_locks.py`、`tests/test_card_optimistic_lock.py`、`tests/test_arc_phase_unlocated_move.py`、`tests/test_dialogue_fallback_goal.py`、`tests/test_distiller_dialogue_pick.py`、`tests/test_market_publish_policy.py`、`tests/test_identify_failure_channels.py`、`tests/test_storage_contract_shape.py`、`tests/test_schema_parity.py`、`tests/test_sqlite_fresh_schema.py`、`tests/test_migration_dispatch.py`、`tests/test_distill_task_api.py`、`tests/test_mutation_anchors.py`。

## 8. 对账表（独立复核方必须验的清单；变异由复核方跑，并自己再补，放宽和过严两个方向都要有）

| 行为 | 守它的测试 | 让它变红的变异 |
|---|---|---|
| 没配上示例的卡落库后是「待补」，三条通道都是 | P1、P3b、P4 | 三条通道任一条不调 `mark_after_distill` |
| 落卡时记下的是蒸馏时的角色名 | P1b | `mark_after_distill` 记成 `card.name` |
| 配上了就不是；重蒸配上了会清掉 | P2、P3 | `mark_after_distill` 只在没有示例时才写 |
| 重新找用的是蒸馏时的角色名，不是卡上的名字 | P11b | `refind` 改用 `card.name` |
| 记不上不让落卡失败、不让任务失败 | P4b、P4c（写库出错）、P4d（投递没成） | 去掉 `mark_after_distill` 里的 `nonfatal`（P4c 红）；后台任务那处去掉 `nonfatal_sync`（P4d 红） |
| 保存清掉「待补」 | P5 | PATCH 不调 `settle` |
| 关掉清掉「待补」，卡不动 | P6 | dismiss 不调 `settle` |
| 重新找：找到了贴上、原文照抄 | P7、P11 | `refind` 不调 `attach_dialogue_examples`；路由不写回 |
| 找不到也清掉；候选为空时不调模型 | P8、P9 | 找不到时不调 `settle` |
| 调用模型出错不清 | P10 | `refind` 把所有异常都当成找不到 |
| 只在「待补」时能用 | P12 | 去掉「不是待补 → 409」 |
| 卡变过不贴 | P13 | 去掉版本核对 |
| 别人的卡 404 | P14 | 两个新接口改用不带属主的读 |
| 贴 = 换掉 | P15 | 去掉清阶段下示例的那两行 |
| 「待补」不在卡的内容里 | P16、`TestPgCardExamplesPending` | 把标记写进 `card_json` |
| 复制卡不带走；`save_card` / `update_card` 不碰 | `TestPgCardExamplesPending` | `save_card` 或 `update_card` 的 SQL 里写这一列 |
| 待补的卡打开就弹；别的卡不弹；游客不弹 | C1、C2、C6 | 去掉那个 effect；去掉 `canWrite` |
| 关掉 → 告诉服务端一次；之后不弹；不是待补的卡关掉不告诉 | C3、C3b、store「关掉」两条 | 关闭时不调 dismiss；不看是不是待补一律调；本地不先清 |
| 保存、重新找用的都是打开时的卡号和版本号 | C4、C8、M7、M7b | 角色页改回取此刻的 `card`（C8 红）；表单底子改回取此刻的 `data`（M7 红）；重新找带此刻的版本号（M7b 红） |
| 换卡时编辑页关掉（宽屏、手机两处） | C7、C7b | 去掉对应那一处 `CardDetail` 的 `key` |
| 有入口才有按钮 | M1、C1 | 不看 `onRefindExamples` 一律显示 |
| 找到的示例看得到、保存不清掉（顶层 / 阶段下） | M2、M3 | 重新找成功后不换表单；去掉阶段示例的只读显示 |
| 没找到说明白；出错可再点 | M4、M5 | 出错也当成找过了 |
| 改过再找先问 | M6、M6b | 去掉 `confirm`；没改也问 |
| 没结束不让关 | M8、M9 | `close` 不看 `busy` |
| store 以返回的行为准 | store 7 条 | `_applyServerCard` 不写 `examples_pending_for` |
| 广场卡：旧版本保存报冲突，不跑预审 | L1 | 去掉路由里的核对；把核对挪到预审之后 |
| 广场卡：当前版本、不带版本号照旧 | L2、L3 | 把 `revision` 改成必填 |
| 两个读卡接口都带版本号 | L4 | 任一接口不加 |
| 存储层比较后写入；没写到时路由报冲突 | L5、L6 | 去掉 `AND card_json = $6`；路由不看返回值 |
| 广场页编辑保存带版本号 | K1 | 请求体不带 `revision` |

这张表在 10-10 的审计里逐行跑过一遍（§14）。

## 9. 上线步骤

| 步骤 | 由什么把关 | 回滚 |
|---|---|---|
| 部署新版本；启动时迁移账本自动跑 PG 036 | `tests/test_postgres_store.py` 的两侧列集锁；`tests/test_migration_dispatch.py` | 向后兼容：只加一列、带默认值。旧版本的 SQL 都是逐列写的，不读不写这一列，可以直接回滚代码，列留着不碍事 |

没有新的配置键，没有外部依赖。

**本地注意**：21:15 那一版的 036 加的是另一个名字的列。如果已经拿那一版的改动文件跑过测试，测试库的迁移账本里记着旧内容，新文件会被跳过。先重建测试库：`docker compose -f docker-compose.test.yml down -v` 再 `up -d --wait`。那一版没有进过任何分支和线上库。

## 10. 已知的后果、样本之外、未核实

- **用户会看到的变化**：新蒸馏的卡缺示例时，打开它会弹出编辑页；编辑页开着时卡在别处变了，保存会被拦下并提示；保存没结束时关不掉编辑页；广场卡两处同时编辑，后保存的会被拦下。
- **「重新找」时已经改了别的栏**：确认后那些没保存的修改会丢。
- **关掉时没告诉成服务端**（断网）：下次打开这张卡还会弹一次。
- **换设备**：标记在服务端，在任何一台设备上处理过，别处都不再弹。
- **名单缓存没命中**：已核实（§3 第 8 条）。只在蒸馏之后、重新找之前部署了升识别版本的新版本时出现；那时会按新口径识别一次并写回缓存，多一次模型调用。这是 `resolve_characters` 对所有路由的统一行为，不改。
- **卡上的名字与蒸馏时的角色名不一致**：已核实，代码不保证一致（§3 第 7 条）。本段不依赖卡上的名字：蒸馏时用的角色名记在行上，重新找原样取回（P11b）。不一致的情况在真实数据里有多少：未核实，也不再影响结果。
- SQLite 的广场卡保存没有比较后写入（已冻结，只同步到接口能跑）。
- **`get_card_unscoped` 的行里也带这一列**：它的调用方里有公开视图。暴露的是一个角色名（待补时）或空，本段没有再做遮蔽。
- 弹出只在角色页；文本页那张角色列表里点「编辑」不弹、没有「重新找」。
- 小尼姑那类「说话人署名和名单不一致」导致的找不到，重新找也还是找不到。这是候选抽取的问题，不在本段。

## 11. S0（执行方先做，只读）

1. 逐条复核 §3 的 1–25，报「成立」或新坐标；有一条不成立就停下报告。
2. `git apply goal-checks-examples-pending.diff`，在没改代码的分支上跑 §1 的 6 个文件，红绿数应与 §1 的表一致。对不上就停下报告。
3. 确认测试库是 Docker 起的 `charsim_test`（55432）。
4. 都成立就直接往下做，不必等回复。

## 12. 提交与范围规矩

一个 PR，按下面的次序提交，每个提交之后都跑目标检查：

1. spec + 6 个目标检查（此时是红的）
2. 存储：迁移两份、`set_card_examples_pending`、读的列清单、PG 用例
3. core：`examples_pending.py`、`attach_dialogue_examples` 改成换掉、`TextManager.get_or_distill` 落卡后记
4. 路由：后台任务和 SSE 落卡后记、PATCH 清掉、两个新接口、结构锁登记
5. 编辑页、store、角色页、文本页
6. 广场卡的版本锁（后端 + 前端）

本段改动面内新发现的问题直接修；会撞车或需要 Shiyu 拍板的才停下报告；不自行记账。不做：600 字上限、候选抽取（别名）、U12、任何蒸馏提示词的改动。

## 13. 与 21:15 那一版的差别

Shiyu 21:29 要求把当时标着「未核实」的两条核实掉。核实的结果改了设计：

- 卡上的名字不保证等于蒸馏时的角色名（§3 第 7 条），所以「待补」那一列从布尔 `examples_pending` 改成文本 `examples_pending_for`，连蒸馏时用的角色名一起记；`refind` 用它，不用卡上的名字。
- 蒸馏时的角色名只有各通道知道，所以「落卡后记」从 `TextManager.save_distilled_card` 里挪到三条通道各自落卡之后（§4.3 的表）。
- 目标检查相应改了：P1–P4 改成走真实的三条通道，新增 P3b（SSE）和 P11b（名字不一致）。
- 名单缓存那一条核实后不需要改设计（§3 第 8 条）。

## 14. 补充（审计，2026-10-10，对象 `d2086b2c`）

审计方：Claude（本 spec 的作者，沙箱）。分支上的代码与交付的原型逐文件相同，所以这是自查，不是独立复核。

**结论：功能符合五条目标；改后合并。** 查出的都是测试没守住的地方和一处重复的拦截，没有查出行为上的错。

做了什么：目标检查在分支上全绿；沿真实路径核对了进角色页的每一条路（角色列表、蒸馏完成后自动选中、任务条、苏醒台词的提示、蒸馏工作台）拿到的卡都来自 `/cards/by-text`，都带「待补」这一列；§8 对账表逐行做变异，另补了十几条。

**活下来的变异（实现是对的，测试没守住），已各补一条目标检查**

| 变异 | 为什么活下来 | 补的检查 |
|---|---|---|
| 落卡时记成卡上的名字 | 测试里卡名和角色名恰好相同 | P1b |
| 去掉 `mark_after_distill` 里的 `nonfatal` | 后台任务外面还有一层，SSE 和 `/run` 没有写库出错的用例 | P4c |
| 后台任务去掉外层 `nonfatal_sync` | 里面那层先吞了；投递没成这种情况没有用例 | P4d |
| 广场保存：存储层没写到时路由仍报成功 | L5 只测了存储层，L1 只测了路由里的核对 | L6 |
| 不是待补的卡关掉编辑页也去告诉服务端 | 没有这条反向断言 | C3b |
| 角色页保存改回取此刻的卡 | 测试里此刻的版本号和打开时的恰好相同 | C8 |
| 去掉手机单栏那一处 `CardDetail` 的 `key` | 只测了宽屏那一处 | C7b |
| 重新找带的是此刻的版本号 | 同上，两者恰好相同 | M7b |

**一处重复的拦截，已删**：`EditCardSession` 里「找过之后不再把 `onRefindExamples` 传给表单」。还能不能再找由宿主说了算（它看的是服务端的「待补」），这里再判一次是同一条规则写了两处。

**等价的两处，不补测试**：文本页保存用编辑页交回的卡号和版本号，与用它自己记的那份结果相同（它打开时就把卡记成了快照）；改成用交回的只是为了三个宿主写法一致。

**新增一条已知的后果**：角色页开着某张卡的编辑页时，同一本书里另一个角色蒸馏完成，页面会自动切到新卡（现有行为），编辑页随之关掉，没保存的修改丢失。改动前这种情况下编辑页留着、保存会写错卡；现在不会写错，但会丢修改。本段不改自动切卡。
