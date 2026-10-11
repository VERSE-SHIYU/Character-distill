# ⑤ 段 A：群聊里每个 AI 人物按建群时选的时期说话

基线：main `bc3e1df`（#139 合并后）。一个 PR、5 个 commit。
版本：v2（10-11 21:20，按「不打补丁、隔离、复用、高内聚低耦合」重核后改；v1 作废）。
记录与决定：⑤ 共享文档「⑤ 群聊接入时期：计划与调查记录」。

---

## 0. S0（开工前，执行方逐条做，有一条不成立就停下报告）

1. 本文件、目标检查、参考补丁都在 worktree 里，且和交付的一致：

   | 文件（worktree 内路径） | sha256 |
   | --- | --- |
   | `tests/test_group_arc_phase_goal.py` | `210539dfebaf57831589e16922c649356ebe5e3eab8858afd78c10a616c72ccc` |
   | `docs/specs/artifacts/group-arc-phase-proto.patch` | `ffe4f4357931fc3d3eebabd08d67b21287ff03b6147344ee9b6c5c5af64ed66e` |

   目标检查 sha 为 **10-11 修复轮作者更新：G4 改断言（建群返回与列群同形状）、新增 G7**（旧值 `78f9da40…` 作废）。

   PowerShell：`Test-Path <路径>` 为真，`Get-FileHash -Algorithm SHA256 <路径>` 与上表一致。

   修复提示词里写的 S0「9 绿 1 红」是作者预判错了。`_create` 改读 `r.json()["group"]["id"]` 以后，R4 落地之前 10 条都会红；commit 2 之后 10 条全绿，与实际一致。
2. `git log -1` 在 `bc3e1df` 或它之后；`main` 上 `storage/migrations_pg/` 最大号是 036、`storage/migrations/` 最大号是 101。**如果 ④ 段 1 已先合并、占用了 037 / 102，本段顺延到下一个号**（两处文件名、`sqlite_store.py` 次序表、迁移注释里的孪生号一起改）。
3. 逐条复核 §3「已查实的约束」：打开对应行，确认内容还在。行号漂了只要内容在就继续，内容变了就停。

---

## 1. 目标检查（第 1 个 commit 就带上，之后每个 commit 都跑）

目标：**群里每个 AI 人物，按建群时给他选的时期说话做事，和一对一选同一时期一样。**

文件：`tests/test_group_arc_phase_goal.py`（随本 spec 交付，原样放入，不改）。真 PG、真接口；重启用「清内存表 → `_get_owned_group` 重建」模拟。

| 编号 | 检查 | main 上 | 原型上 |
| --- | --- | --- | --- |
| G1 | 建群时甲选阶段 1、乙不选：甲只投影到阶段 1 且带边界说明；乙是最后阶段 | 红 | 绿 |
| G2 | 服务重启后（从库里重建）每个人物的阶段不变 | 红 | 绿 |
| G3 | 越界（0、3、-1）和不可选阶段的卡 → 最后阶段，且不落库；同群合法的那个不受影响 | 红 | 绿 |
| G4 | 建群响应和 `/api/group/list` 都带回 `member_phases` | 红 | 绿 |
| G5 | 用户扮演的人物传了时期也不落库，不建引擎 | 红 | 绿 |
| G6 | 阶段 1 的人物提示词里有边界说明、没有阶段 2 的状态；最后阶段的人物相反 | 红 | 绿 |

Claude 已在沙箱实跑（Python 3.13，PG 16，库 `charsim_test`）：main `bc3e1df` 上 **9 failed**；套上参考补丁后 **9 passed**。受影响的后端测试（§6 列的文件）在补丁上全过；`test_evidence_integrity.py::test_code_sha_resolves` 在沙箱 main 上同样失败，原因是沙箱浅克隆查不到旧 commit，与本段无关。

前端没有放进目标检查（要浏览器），由 §6 的 Playwright 断言守。

---

## 2. 已定的范围（Tracy 10-11 定）

- 只做「不同人物各选一个时期」。同一人物的不同时期同时进群（段 B）不做。
- 旧群不管：库里没有这一列的值 → 全员最后阶段。建群后时期不能改。
- 进群的人物不带一对一存档的记忆（④ 0a 已做到，`core/chat_engine.py:182–183`）。
- 没有可靠时期的卡（`valid_phase` 恒 None）不给选，只按最后阶段。
- 用户扮演的人物不选时期。
- 时期只在群聊页「成员」标签里显示，不在每条消息旁显示。
- 界面一律复用网站已有组件和样式，不写原生控件、不另写样式。
- 存法：新加一列存「卡 id → 阶段号」，`card_ids` 不动（方案 A）。选时期的位置：建群弹窗第 2 步选完身份后（方案 A）。

---

## 3. 已查实的约束（main `bc3e1df`，Claude 10-11 读过的行）

**后端**

| # | 事实 | 位置 | 读到的关键行 |
| --- | --- | --- | --- |
| C1 | 建群请求没有时期字段 | `web/routers/group.py:38–45` | `class CreateGroupRequest` 只有 `name / card_ids / user_persona_*` |
| C2 | 建引擎有两处，写法重复，都不传 `arc_phase` | 恢复群 `group.py:114–184`；建群 `group.py:324–383` | 两处都是 `get_indexing_service()` → 读 `get_user_api_config` → `resolve_embedding` → `get_user_memory` → `get_rag_for_session` → `ChatEngine(per_user_llm, rag, card, memory_manager=…, card_id=…, storage=…, session_id="", group_id=…, is_new_session=True)` |
| C3 | 引擎收 `arc_phase`，投影只在构造里做一次；None / 越界 → 最后阶段 | `core/chat_engine.py:131`、`:162` | `self.card, self.arc_view = project_card(card, arc_phase)` |
| C4 | 阶段校验规则：卡不可选（`has_positions` 假）或越界 → None | `core/arc_view.py:67–75` | `if not card.character_arc.has_positions(): return None` / `return _phase_in_range(card, arc_phase)` |
| C5 | 一对一的写法：入口 `valid_phase` 校验后存；恢复时把库里的值原样交给引擎 | `web/routers/distill.py:1525`、`:1583`；`core/text_manager.py:734–740` | `arc_phase = valid_phase(card, req.arc_phase)`；`return {"user_role": …, "arc_phase": row.get("arc_phase")}` |
| C6 | 群表成员只有 `card_ids` 一列（JSON） | `storage/migrations_pg/001_init.sql:174–185` | `card_ids TEXT NOT NULL DEFAULT '[]'` |
| C7 | 两个后端各有 4 处把 `card_ids` 从 JSON 解开，写法相同 | PG `storage/postgres_store.py:1845、1862、1884、1904`；SQLite `storage/sqlite_store.py:2444、2462、2485、2506` | `d["card_ids"] = json.loads(d["card_ids"])` |
| C8 | 建群写库：两后端 `create_group_session` 签名相同 | PG `postgres_store.py:1809`；SQLite `sqlite_store.py:2404` | `(id, name, card_ids, user_id, user_persona_type=…, user_persona_card_id=…, user_persona_name=…, user_persona_desc=…)` |
| C9 | 存储改动只保证 PG；PG 加列时 SQLite 补一份孪生迁移并登记次序表；列集锁要求两侧列相等 | `AGENTS.md:53`；`sqlite_store.py:150–163`（次序表末项 `"101_cards_examples_pending.sql"`）；PG 迁移按目录排序执行 `postgres_store.py:285–286` | 先例：PG 035 ↔ SQLite 100（`sessions.arc_phase`） |
| C10 | 扮演的卡不建引擎 | 建群 `group.py:362–367`；恢复 `group.py:139–154` | `if persona_type == "character" and card_id == persona_card_id: … continue` |
| C11 | 列群接口直接返回存储层的行 | `group.py:450–458` | `groups = await storage.list_group_sessions(user["id"])` … `return {"groups": groups}` |
| C12 | 边界说明本来就管「别人说的」，群聊不用改 | `core/context_engine.py:31–34`、`:421–426` | 「……**别人提起**你没经历过的事……」 |
| C13 | 关系态度、群好感评估都读这个人物投影后的卡，传了时期自动跟着走 | `core/group_session.py:291–297`；群好感 `group.py:244–280` 调 `engine._evaluate_affinity` | `group_session.py`：`for rel in engine.card.relationships`；`chat_engine.py:700` `_evaluate_affinity` 内 `:772` `card=self.card`（`self.card` 即 `:162` 的投影卡） |
| C14 | 整个仓库造 `ChatEngine` 只有三处 | `group.py:174`、`group.py:373`、`core/text_manager.py:718` | 扫描命令见 §8 |

**前端**

| # | 事实 | 位置 |
| --- | --- | --- |
| F1 | 一对一头部的阶段文字直接写在组件里：有阶段就显示「阶段 k/n · label」，k 为 null / 越界 → n；无阶段不显示；**不看 selectable** | `web/frontend/src/components/ChatArea.jsx:159–165`、`:674–678` |
| F2 | 卡片是否可选阶段，前端只认 `character_arc.selectable`（后端 `out_card` 现算） | `RoleSetupModal.jsx:32–35`；`store/useAppStore.js:357–366` |
| F3 | 「默认最后阶段」已有一处：`defaultArcPhase(card)` | `store/useAppStore.js:351–354` |
| F4 | 建群弹窗两步：第 1 步勾角色（`:1483–1528`），第 2 步选身份（`:1531–1605`）；`handleCreate` 组请求体（`:566–624`） | `GroupChatPage.jsx` |
| F5 | 建群成功后 `setCurrentGroup({...})` 和 `setGroups(prev => [...])` 手拼字段 | `GroupChatPage.jsx:606–616` |
| F6 | 进入已有群：`setCurrentGroup({ ...group, card_ids })`，列群返回的字段原样带上 | `GroupChatPage.jsx:400–417` |
| F7 | 成员标签条目：`group-member-item` > `Avatar` + `group-member-info`（`group-member-name`、`group-member-identity`、`group-member-affinity`） | `GroupChatPage.jsx:1405–1417`；样式 `styles/global.css:12782–12785`、`:13561` |
| F8 | 共用下拉 `common/Select`（Radix）：`value` 为字符串，`options: [{value, label}]`，`size='sm'` 有样式 | `components/common/Select.jsx`；`global.css:6991` |
| F9 | 一对一身份弹窗的选阶段是原生 `<input type="radio">`（**本段不照搬**） | `RoleSetupModal.jsx:143–155` |
| F10 | 「AI 成员 = 去掉扮演的那张卡」这条规则前端已经写了 3 遍 | `GroupChatPage.jsx:404`（进群）、`:573`（建群前数 AI 个数）、`:604`（建群后设当前群） |
| F11 | 建群弹窗的表单重置已经写了 3 遍（打开弹窗、建群成功、点「上一步」），每遍逐个 `setXxx` | `GroupChatPage.jsx:527–531`、`:596–601`、`:1611` |
| F12 | 「没选 = 最后阶段」前端写了 2 遍 | `ChatArea.jsx:164`；`store/useAppStore.js:351–354`（`defaultArcPhase`） |
| F13 | `parseCardIds` 在两个组件里各写一份，内容相同 | `GroupChatPage.jsx:32–35`；`HistoryPanel.jsx:19` |
| F14 | 建群成功后不走「进群」那条路，而是手拼当前群（`setCurrentGroup({...})`）、手拼列表条目（`setGroups(...)`）、手动清消息。手拼的列表条目**漏了 `user_persona_*`**：刷新前从侧栏再点进这个新群，`enterGroup` 读不到扮演的卡，会把它当 AI 成员（现有缺陷，读代码得出，未在浏览器复现） | `GroupChatPage.jsx:602–617`（`:616` 的条目只有 `id / name / card_ids / created_at`）；进群那条路 `enterGroup` `:400–420`（`:403` 读 `group.user_persona_type`） |

---

## 4. 设计

### 4.1 存（数据库 + 存储层）

- 新列 `group_sessions.member_phases TEXT NOT NULL DEFAULT '{}'`：JSON，卡 id → 阶段号。**只存选了且有效的**；没出现的卡 = 最后阶段。只在建群时写一次。
  - PG：`storage/migrations_pg/038_group_member_phases.sql`（`ADD COLUMN IF NOT EXISTS`）。
  - SQLite 孪生：`storage/migrations/103_group_member_phases.sql`，并登记进 `sqlite_store.py` 次序表末尾。
- **JSON 列的编解码只写一处**：新模块 `storage/group_row.py`，两个函数：
  - `encode_member_phases(member_phases) -> str`（写库用）。
  - `decode_group_row(d) -> dict`：解开 `card_ids`，行里有 `member_phases` 就一并解开。
  - 两个后端那 8 处 `d["card_ids"] = json.loads(...)`（C7）全部改成 `decode_group_row(d)`。以后群表再加 JSON 列，只改这个文件。
- `create_group_session` 两后端都加关键字参数 `member_phases: dict[str, int] | None = None`，写进新列（签名两侧保持逐格相同）。
- `get_group_session_unscoped` / `get_group_session_owned` / `list_group_sessions` 的 SELECT 带上 `member_phases`。`get_deleted_group_sessions` 不用带（回收站不显示时期）。
- SQLite 只同步到「接口能跑」（C9），不为它写用例。

### 4.2 建（后端）

**群成员引擎单独成一个模块（C2 的根治）**：新建 `web/group_engines.py`，路由只留 HTTP（鉴权、成员规则、落库、响应）。模块里三样东西，建群与恢复群都只调它们：

- `GroupEngineDeps` + `async group_engine_deps(user_id, per_user_llm, storage, *, where)`：读用户配置、算 embedding、取记忆视图、取检索服务。`group.py` 里原来两段一样的代码删掉。`where` 只进日志。
- `resolve_member_phases(ai_cards, requested) -> dict[str, int]`：**建群时的阶段规则只此一处**。对每个 AI 成员 `valid_phase(card, requested.get(card_id))`（与一对一同一条规则，C4、C5），非 None 才收。纯函数，不碰 IO。扮演的卡由路由先剔除（C10），请求里多出来的卡 id 自然不进。
- `build_group_engine(gdeps, card, *, card_id, text_id, group_id, arc_phase) -> ChatEngine`：取 RAG + 构造 `ChatEngine`。**群里造引擎只此一处**（C14 由三处变两处：这里和 `text_manager`）。

路由的改法：

- `CreateGroupRequest` 加 `member_phases: dict[str, int] = Field(default_factory=dict)`。
- 建群：成员循环里只收集 `ai_cards`（卡 id → 卡）和 `card_infos`；循环后 `member_phases = resolve_member_phases(ai_cards, req.member_phases)`，再逐个 `build_group_engine(..., arc_phase=member_phases.get(card_id))`；`create_group_session(..., member_phases=member_phases)`；响应加 `"member_phases": member_phases`。
- 恢复群：`member_phases = session.get("member_phases") or {}`，`build_group_engine(..., arc_phase=member_phases.get(card_id))`（与一对一恢复同口径，C5：库里的值原样交给引擎，越界由投影归一）。

**以后改动要动几处**：加一个引擎构造参数 → 只改 `build_group_engine`；改阶段规则 → 只改 `valid_phase`（一对一与群聊共用）；加一个依赖 → 只改 `group_engine_deps`。

参考实现：`docs/specs/artifacts/group-arc-phase-proto.patch`（Claude 沙箱原型，§1 的目标检查在它上面全绿）。**只作参考**：import 位置等细节按仓库规范放，不必照抄。

### 4.3 阶段的前端规则（抽到 `utils/card.js`，一处）

F12 显示「没选 = 最后阶段」写了两遍。本段要第三次用到它，所以先合成一处：

- `effectiveArcPhase(card, arcPhase) -> number | null`：卡没有阶段 → `null`；`arcPhase` 在 1..n 内取它，否则取 n。**前端「没选 = 最后阶段」只此一处。**
- `arcPhaseDisplay(card, arcPhase) -> string`：用 `effectiveArcPhase` 取 k；`null` → `''`；否则 `阶段 k/n` + （有 label 时）` · label`。规则与 F1 一字不差（含「不看 selectable」）。
- `arcPhaseOptions(card) -> [{ value, label }]`：卡不可选（`character_arc.selectable` 不为真，F2）或没有阶段 → `[]`；否则每个阶段一项，`value` 为阶段号的字符串（`common/Select` 收字符串，F8），`label` 为 `阶段 k · label`（无 label 只写「阶段 k」）。

改用它们的地方：

- `store/useAppStore.js` 的 `defaultArcPhase(card)` 改为 `return effectiveArcPhase(card, null)`（返回值不变：有阶段返回 n，没有返回 `null`）。
- `ChatArea.jsx` 头部（F1）：删掉 `:162–165` 的现算，`:674–678` 渲染 `arcPhaseDisplay(currentCard, sessionArcPhase)`，空串不渲染。行为不变，现有 `ChatAreaArcPhase.test.jsx` 必须原样通过。

### 4.4 群成员的前端规则（抽到 `utils/groupMembers.js`，一处）

这个文件已经是「群聊成员规则的前端一侧」（`blockedBySameCharacter` 在这里）。加两样：

- `aiMemberIds(cardIds, personaType, personaCardId) -> string[]`：扮演的卡（`personaType === 'character'` 时的 `personaCardId`）剔除，其余原序返回。**F10 那 3 处全部改调它**，本段的选时期列表也调它。
- `parseCardIds(raw)`：从 `GroupChatPage.jsx:32–35` 搬过来；`GroupChatPage` 与 `HistoryPanel`（F13）都改为从这里 import，删掉两份副本。

### 4.5 建群弹窗第 2 步选时期（`GroupChatPage.jsx`）

**表单重置合成一处（F11）**：新增 `resetCreateForm()`，把身份那几项和新加的 `memberPhases` 一起重置。打开弹窗、建群成功、点「上一步」三处都只调它，删掉三处逐个 `setXxx`。

**选时期的数据只算一次**：新增状态 `memberPhases`（`{卡 id: 阶段号}`，只存用户改过的），和一个派生值 `phaseChoices`（`useMemo`，依赖 `selectedCardIds / personaType / personaCardId / memberPhases / 卡片缓存`）：

```
aiMemberIds(selectedCardIds, personaType, personaCardId)
  → 每个 id 取卡 → options = arcPhaseOptions(card)
  → 只留 options 非空的
  → { cardId, card, options, value: memberPhases[cardId] ?? effectiveArcPhase(card, null) }
```

渲染和请求体**都只读 `phaseChoices`**，不各算一遍。

**界面**：第 2 步「你以谁的身份参与？」那一段下面，`phaseChoices` 非空时新加一段 `group-create-section`：

- 标题：`modal-label`「每个角色的时期」。
- 每行：行布局类 `group-create-row`（`Avatar` + `group-create-card-info` > `group-create-card-name`），右边 `common/Select size="sm"`（F8），`options` 与 `value` 来自 `phaseChoices`。组件抽成 `components/GroupPhasePicker.jsx`（纯展示，props 只收 `choices` / `onChange`，不读 store、不发请求）。
- 类拆法（`.group-create-card` 拆成 `.group-create-row` 行布局 + `.group-create-card` 卡片交互）与抽 `GroupPhasePicker`，是 **Tracy 10-11 22:01** 看截图后选「B：补样式」时定的（同一次也确认了样式补充 v2 的方案：拆类、抽组件）。原「借用成员条目类 / 不新增 CSS / 排不开就停下」的写法**被这条决定取代**（R6 记录缺失的补记）。

**建群成功走「进群」那条路（F14 的根治）**：成功后组一个群对象 `created`（`id / name / card_ids: selectedCardIds / user_persona_* / member_phases: data.member_phases / created_at`），`setGroups(prev => [created, ...prev])`，然后 `enterGroup(created)`。删掉原来手拼的 `setCurrentGroup({...})`、`setGroupUserAvatar(null)`、`setMessages([])`。这样新建的群和点开已有的群走同一条路，`member_phases` 不用再单独接一遍；以后群对象加字段只改一处。

**触发链（每条都要在 Playwright 里有断言）**：

1. 点「下一步」→ `setPersonaStep(true)` → `phaseChoices` 按当前身份算出 → 每个 AI 可选卡一个下拉，显示 `value`（默认最后阶段）。
2. 选「扮演已选角色」并点某张卡 → `setPersonaCardId(id)` → `phaseChoices` 重算 → 那张卡的下拉消失；改回别的身份 → 下拉回来。
3. 选下拉 → `onChange(v)` → `setMemberPhases(prev => ({ ...prev, [id]: Number(v) }))` → `phaseChoices` 重算 → 下拉显示新值。
4. 点「创建群聊」→ `handleCreate` → 请求体 `member_phases = Object.fromEntries(phaseChoices.map(c => [c.cardId, c.value]))`（扮演的卡、不可选的卡本来就不在 `phaseChoices` 里）→ POST `/api/group/create` → 成功 → `setGroups` + `enterGroup(created)` → `resetCreateForm()`。
5. 点「上一步」/ 打开弹窗 → `resetCreateForm()` → `memberPhases` 清空。

**运行时假设（Playwright 要测出来）**：`enterGroup` 会对新群请求一次历史和好感（`loadHistory`、`fetchAffinities`），新群两者都应为空，界面与原来手动清空一致。

### 4.6 成员标签显示时期（`GroupChatPage.jsx`）

- 成员条目（F7）里，`group-member-identity` 之后、好感那一行之前，加一行 `<span className="group-member-identity">{arcPhaseDisplay(card, currentGroup.member_phases?.[cardId])}</span>`，空串不渲染。复用 `group-member-identity` 这个类，不新增样式。
- 触发链：`enterGroup`（点开已有群，或 4.5 建群成功后）→ `setCurrentGroup({ ...group, card_ids })`，`member_phases` 随群对象带上 → 切到「成员」标签 → 每个 AI 人物显示「阶段 k/n · label」。
- 旧群没有 `member_phases` → 取不到 → 按最后阶段显示（与一对一「没选 = 最后阶段」一致）。卡没有阶段 → 不显示。

### 4.7 自检：以后加一个东西要改几处

（R1–R7 修复后重写：每行写出**现在的唯一位置**。）

| 以后要加 / 改的 | 唯一位置 | 处数 |
| --- | --- | --- |
| 群表再加一个 JSON 列 | `storage/group_row.py`（读取列清单 `GROUP_SESSION_COLUMNS` + 写入映射 `group_insert_values`；store 的 `create_group_session` 参数是接口定义，不算重复规则） | 1（加列本身的迁移另算） |
| 群会话读整行的列清单 | `storage/group_row.GROUP_SESSION_SELECT`（PG / SQLite 三个读点都引用） | 1 |
| 群引擎加一个构造参数 | `web/group_engines.build_group_engine` | 1 |
| 阶段校验规则 | `core/arc_view.valid_phase`（一对一、群聊共用） | 1 |
| 「没选 = 最后阶段」的前端规则 | `utils/card.effectiveArcPhase` | 1 |
| 阶段文字的格式 | `utils/card.phaseHeader`（`arcPhaseDisplay` / `arcPhaseOptions` 与五处调用共用） | 1 |
| 「谁是 AI 成员」的前端规则 | `utils/groupMembers.aiMemberIds` | 1 |
| 「谁是 AI 成员」的后端规则 | `web/group_engines.ai_member_ids` | 1 |
| 建群选时期的算法（AI 成员 → 可选卡 → 当前值） | `utils/groupMembers.groupPhaseChoices`（请求体由 `memberPhasesPayload` 从它生成） | 1 |
| 建群表单加一个字段的重置 | `resetCreateForm` | 1 |
| 群对象加一个字段 | 后端返回整行（`get_group_session_owned`）；前端新建与点开都走 `enterGroup` | 1 |

## 5. 步骤（按依赖顺序，每步一个 commit）

1. **[数据库]** 加 `038_group_member_phases.sql` 与孪生 `103_group_member_phases.sql`，登记 SQLite 次序表；同一 commit 放入 `tests/test_group_arc_phase_goal.py`（此时红，正常）。
2. **[存储]** 新建 `storage/group_row.py`；两后端 8 处解码改调 `decode_group_row`；`create_group_session` 收 `member_phases`；三个读点带新列。
3. **[后端]** 新建 `web/group_engines.py`（4.2 三样）；`group.py` 建群与恢复群改调它、删掉重复段；请求字段与响应字段。**本 commit 后目标检查 9 条全绿。**
4. **[前端]** `utils/card.js` 三个函数及单测，`defaultArcPhase` 与 `ChatArea` 改用；`utils/groupMembers.js` 加 `aiMemberIds`、搬入 `parseCardIds` 及单测，F10、F13 各处改用。此步不改任何行为。
5. **[前端]** 建群弹窗选时期、`resetCreateForm`、建群成功走 `enterGroup`（4.5）；成员标签显示时期（4.6）；新增 `e2e/group-arc-phase.spec.js`。

## 6. 验证

本地只跑受影响的文件，库用 Docker 起的测试 PG；合并门是分支 CI；报告里不写本地全量数字。

**每个 commit 后**：`pytest tests/test_group_arc_phase_goal.py`（commit 1–2 红，3 起全绿）。

**后端受影响的文件**（commit 2、3 后跑）：

```
pytest tests/test_group_arc_phase_goal.py tests/test_group_members.py \
  tests/test_group_affinity_list_api.py tests/test_group_save_failure.py \
  tests/test_arc_phase_select.py tests/test_postgres_store.py \
  tests/test_sqlite_fresh_schema.py tests/test_migration_dispatch.py \
  tests/test_memory_delete_goal.py tests/test_memory_archive_scope_goal.py
```

其中 `test_postgres_store.py::TestPgFreshSchemaClosure` 是列集锁（两侧新库列相等），`test_arc_phase_select.py::test_group_uses_last_phase` 守「不传时期 = 最后阶段」，都必须原样通过。

**前端**（commit 4、5 后跑）：

- `npm test`（vitest）：
  - `utils/card.test.js`：`effectiveArcPhase` 无阶段 → null，k 为 null / 0 / 越界 → n，合法 → k；`arcPhaseDisplay` 有 / 无 label；`arcPhaseOptions` 不可选或无阶段 → `[]`，可选 → 每阶段一项且 `value` 为字符串。
  - `utils/groupMembers.test.js`：`aiMemberIds` 三种身份；扮演的卡不在结果里，其余顺序不变。`parseCardIds` 数组 / JSON 字符串 / 坏字符串。
  - 现有 `ChatAreaArcPhase.test.jsx` 原样通过（头部行为不变）；`store/arcPhase.test.js` 里 `defaultArcPhase` 的用例原样通过。
- `npx playwright test e2e/group-arc-phase.spec.js`（`page.route` 模拟接口，沿用 `e2e/arc-phase-select.spec.js` 的写法），断言：
  1. 第 2 步：可选的 AI 卡各有一个下拉，默认最后阶段；不可选的卡没有下拉。
  2. 选「扮演」某张卡 → 它的下拉消失；改回「导演」→ 回来。
  3. 把甲改成阶段 1 → 创建 → 请求体 `member_phases` 里甲为 1、乙为最后阶段、扮演的卡不在。
  4. 成员标签：甲显示「阶段 1/2 · 冷」；响应里没有 `member_phases` 的旧群显示最后阶段；无阶段的卡不显示阶段行。
  5. 建群成功后直接进入新群，消息区为空、成员标签显示「尚未评估」（验证 4.5 的运行时假设）；不刷新、从侧栏再点进这个新群，扮演的卡不出现在成员里（F14 的缺陷修掉了）。
  6. 截图两张（建群第 2 步、成员标签），交 Tracy 看样子。

**最后**：开 PR，等分支 CI 绿。复核由另开的会话做（写和验分开）。

---

## 7. 不做 / 面外

- 段 B（同一张卡不同时期同时进群）：`member_phases` 以卡 id 为键，到时要换结构，现在不为它改。
- 群聊本场记录：排在 ④ 段 1 之后单独做。
- 建群后改时期：不做（定了的规则）。
- 「清理孤儿卡」（`update_group_card_ids`）只改 `card_ids`，`member_phases` 里可能留下已删卡的键：读的时候按卡 id 取，多出来的键不影响任何结果，不处理。
- 不改 `ChatEngine`、`context_engine`、`group_session.py`、评估调用（④ 段 1 会改评估调用，两边不碰同一处）。
- 一对一身份弹窗的原生单选（F9）本段不改：那是另一个界面，改它要单独验收；`RoleSetupModal` 读阶段的方式与本段新函数一致，以后要改再接。

---

## 8. 扫描原始输出（main `bc3e1df`）

```
$ grep -rn "ChatEngine(" --include=*.py web core
web/routers/group.py:174:        engine = ChatEngine(
web/routers/group.py:373:        engine = ChatEngine(
core/text_manager.py:718:        engine = ChatEngine(

$ grep -n 'd\["card_ids"\] = json.loads' storage/postgres_store.py storage/sqlite_store.py
storage/postgres_store.py:1845  1862  1884  1904
storage/sqlite_store.py:2444  2462  2485  2506

$ grep -rn "INSERT INTO group_sessions\|group_sessions SET card_ids" --include=*.py . | grep -v tests/
storage/sqlite_store.py:2414   INSERT INTO group_sessions …
storage/sqlite_store.py:2827   UPDATE group_sessions SET card_ids = ? …
storage/postgres_store.py:1817 INSERT INTO group_sessions …
storage/postgres_store.py:2208 UPDATE group_sessions SET card_ids = $1 …

$ ls storage/migrations_pg | tail -2 ; ls storage/migrations | grep -v README | tail -2
035_session_arc_phase.sql
036_cards_examples_pending.sql
100_session_arc_phase.sql
101_cards_examples_pending.sql
```

（第二、三条输出为便于阅读把同文件的行号合并到一行，原始输出每个命中一行。）

---

## 9. 判断清单（发出前逐条对过读过的行）

| 判断 | 依据 |
| --- | --- |
| 群聊现在不传时期，一律最后阶段 | C1、C2、C3 |
| 传了 `arc_phase` 后，卡、做法、性格、关系态度、检索、边界说明都跟着走 | C3（投影在构造里一次）、C12、C13；G1、G6 实测 |
| 越界 / 不可选 → 最后阶段 | C4；G3 实测 |
| 重启后不丢 | 恢复群从行里取；G2 实测 |
| 改动只此几处、不漏 | C14 扫描：群里造引擎两处合成一处；C7 扫描：解码 8 处合成一处；F10–F14：前端 4 条重复规则各合成一处；4.7 逐项答「改几处」 |
| 前端头部文字规则 | F1 原文；4.3 要求一字不差，现有测试守 |
| 弹窗里复用成员条目的样式排得开 | **未核实**（没在浏览器里看过），见 4.4 的停止条件 |
| 建群成功改走 `enterGroup` 后，界面与原来一致 | **未核实**（`enterGroup` 会多请求一次空的历史和好感），Playwright 第 5 条测 |
| 成员标签里 `resolveCard` 拿到的卡带 `character_arc.phases` | **未核实**（卡来自 `fetchCardsByText` 经 `out_card`，没逐行读到 `resolveCard` 的取值链）；Playwright 第 4 条会测出来 |

---

## 补充

### 独立复核（10-11，复核 PR 头 `47975db`，基线 `bc3e1df`）

**实跑结果**（PG 16 一次性库，端口 55433；每次换新库）：

- 目标检查：`bc3e1df` 9 failed，PR 头 9 passed。
- §6 后端 9 个文件：两边都是 193 passed / 1 skipped，无差异。（同一个库先跑 PR 再跑 base 会让 base 的 `TestPgFreshSchemaClosure` 3 条红——是 037 已落库，不是代码问题；必须每次换新库。）
- `npm test`：99 文件 / 552 条全过。
- **Playwright 未执行**：复核沙箱拉不到浏览器（`cdn.playwright.dev` 被网络白名单拦）。两张截图也没看到。e2e 结论只来自读代码，见 R3。

**变异**（每条改一处、跑完即还原）：

| 编号 | 改法 | 被谁抓到 |
| --- | --- | --- |
| B1 | 恢复群 `arc_phase=None` | G2、G3×4、G6 |
| B2 | `resolve_member_phases` 不过 `valid_phase`，原样收 | G3×4 |
| B3 | 扮演的卡一起进 `resolve_member_phases` | G5 |
| B4（过严） | 选了最后阶段也不收 | 无——**等价变异**（不存 = 最后阶段，行为相同），不算缺口 |
| B5（过严） | 恢复群也过 `valid_phase` | **无**，见 R5 |
| B6（过严） | `decode_group_row` 把 `member_phases` 解成 `{}` | G2–G6 共 8 条 |
| F1 | `aiMemberIds` 不剔除 | `groupMembers.test` |
| F2 | `effectiveArcPhase` 越界不回落 | `card.test` |
| F3 | 请求体 `member_phases` 改为对全部 AI 成员（含不可选卡）取值 | **无**（vitest 无覆盖；e2e 也抓不到，见 R3） |
| F4（过严） | 阶段 1 也回落到最后 | `card.test` ×2、`ChatAreaArcPhase.test` |
| F5（过严） | 少于 3 阶段的卡不给选 | `card.test` |

**问题**（R = 复核发现；「实现偏离」= 代码没照 spec；「spec 错」= spec 自己的结论不成立）

**R1｜spec 错：4.7「群表再加一个 JSON 列 → 1 处」不成立。** 实测要改：`group_row.py`、PG/SQLite 各 3 条 SELECT 列清单、各 1 条 INSERT、各 1 个 `create_group_session` 签名——约 9 处。`group_row` 只集中了「解码」，没集中「列清单」。
根因：列名在每条 SQL 里手写，与解码不在同一处。
改法：`storage/group_row.py` 加 `GROUP_COLUMNS`（列名元组）与 `GROUP_SELECT`（拼好的列清单），两个 store 的 3 条 SELECT 都引用它；4.7 这一行改写为实际处数，或做完上述收拢后再写 1。

**R2｜spec 错：4.7「阶段文字的格式 → 1 处」不成立。** `阶段 k · label` 这串格式现在有 4 处：`utils/card.js:50`（`arcPhaseDisplay`）、`:59`（`arcPhaseOptions`，本段新写）、`common/UnlocatedList.jsx:28`、`RoleSetupModal.jsx:75/157`。后端已有唯一出口 `core/arc_view.phase_header`，前端没有对应物。
根因：`arcPhaseDisplay` 与 `arcPhaseOptions` 各自拼串，本段又新增了一处重复。
改法：`utils/card.js` 加 `phaseHeader(k, label)`（与后端 `phase_header` 同名同义），`arcPhaseDisplay`、`arcPhaseOptions`、`UnlocatedList` 都调它；`RoleSetupModal` 按 §7 不动，但在表里注明它是剩下的例外。

**R3｜实现偏离（测试）：e2e 第 3 条标题与断言不符，F3 永远抓不到。** 标题写「扮演的卡不在」，但这条用的是导演身份、只选了甲乙（都可选）：请求体里从没出现过扮演的卡或不可选的卡，断言 `{a:1, b:2}` 对这两种越界都是空检。F3 变异在 vitest 和这条 e2e 下都存活。
根因：请求体规则（「只发 `phaseChoices` 里的卡」）只在 e2e 里守，而 e2e 的场景没构造出要被排除的卡。
改法：这条 e2e 改为选甲、乙、丙（丙不可选），身份「扮演乙」；断言 `member_phases` 恰为 `{a: 1}`。

**R4｜spec 错：4.7「群对象加一个字段 → 1 处」不成立。** `handleCreate` 里的 `created` 仍逐个手抄 `data.*` 字段（`GroupChatPage.jsx:608–619`）；建群响应（`group.py` 末尾手写的 dict）和列群返回的行（`list_group_sessions`）是两份形状。加字段要改：建群响应、`created` 手抄、列群 SELECT（R1）——至少 3 处。4.5 把 F14 的「漏字段」修好了，但「手拼」本身没去掉。
根因：建群接口没有返回「群行」，而是另拼一份。
改法：建群成功后由存储层读回这一行（`get_group_session_owned` → `decode_group_row`），响应体 `{"group": 行}`；前端 `enterGroup(data.group)`，`setGroups(prev => [data.group, ...prev])`，删掉 `created` 手抄。新建与点开就真正是同一个形状、同一条路。

**R5｜spec 缺守护：设计决定 b 没有测试。** 恢复群不过 `valid_phase`（B5）的反向变异全绿。决定本身成立：与一对一 `session_identity` 同口径，且 `arc_view.valid_phase` 注释写明归一不吃 `has_positions` 门（DA8）。但没有一条检查区分这两种写法，以后有人「顺手」在恢复路径加校验不会被发现。
改法：目标检查加一条：建群时卡可选、存了阶段 1 → 改库里的卡让 `has_positions` 为假 → 重建 → 引擎仍在阶段 1（与一对一恢复一致）。

**R6｜实现偏离 spec 流程：`47975db` 新增了 CSS。** 4.5 写的是「不新增 CSS；排不开就停下报告，附截图，由 Tracy 定」。这个 commit 把 `.group-create-card` 拆成 `.group-create-row`（布局）+ `.group-create-card`（交互），属于拆类、没有新视觉值，第 1 步角色卡同时加了两个类，复核未发现漏改的用法。但 spec 没有记录这次改动是谁定的。
改法：若是 Tracy 定的，在 4.5 补一行「10-11 Tracy 定：允许拆出 `.group-create-row`」；否则按 4.5 退回。

**R7｜面外，记录不处理：后端「谁是 AI 成员」写了 3 遍**（`routers/group.py:127`、`:288`、`:324`）。4.7 只声明前端一处，没错；但后端同一规则 3 处，与本段「每条规则只写一处」的原则不一致。建议排进后续段，抽 `group_engines.ai_member_ids(card_ids, persona_type, persona_card_id)`。

**对 5 个设计决定的结论**

- a（新加列，不改 `card_ids` 结构）：成立。`card_ids` 被清理孤儿卡、列群、前端 `parseCardIds` 多处读，改结构牵连更大；孤儿键按卡 id 取不影响结果（§7 已写）。
- b（恢复群不过 `valid_phase`）：成立，但缺守护（R5）。
- c（建群成功走 `enterGroup`）：方向对，修好了 F14；没做彻底（R4）。
- d（`web/group_engines.py`）：成立。C14 由三处构造变两处，两条路共用依赖准备；`text_id` 移到循环外经核实是全群同一本书（`_check_group_members` 保证），不是错。
- e（4.7 表）：8 行里 3 行不成立（R1、R2、R4），1 行是逐值参数时要改两个调用点（加「每个成员不同」的构造参数时，建群与恢复两处都要传）。

**合并前必须处理**：R3（测试空检）、R6（流程记录）。R1、R2、R4 至少要把 4.7 改成真实处数；按「每条规则只写一处」的原则，建议本段一起收拢。R5 建议本段加。R7 不在本段。

### 作者处理意见（10-11，Claude，spec 作者）

逐条判定（依据：复核实跑结果，以及作者在 PR 头 e816577 上重核的行）：

- R1｜认，spec 错。4.7 把「解码集中」当成了「加列只改一处」。修：列清单收进 storage/group_row.py。
- R2｜认，spec 错，而且本段新写的 arcPhaseOptions 又加了一处重复。修：前端 phaseHeader 一处，五个用处都改调。后端 phase_header（「阶段 i·label」，给提示词用）与前端（「阶段 k · label」，给界面用）格式不同、语言不同，不强行合并。
- R3｜认，测试空检。修：请求体算法抽成纯函数加 vitest（不依赖浏览器）；e2e 第 3 条构造出要被排除的卡。
- R4｜认，spec 错：4.5 只修了漏字段，没去掉手拼。修：建群响应改为返回这一行群；目标检查 G4 改为断言建群返回与列群返回同形状。
- R5｜认。目标检查加 G7：卡在建群后变得不可选，重建仍在存下的阶段。作者已在 PR 头上实跑：G7 绿；套上 B5 变异（恢复群也过 valid_phase）后 G7 红。
- R6｜不成立，是记录缺失。拆出 .group-create-row 是 Tracy 10-11 22:01 定的（看截图后选「B：补样式」，同时确认了样式补充 v2 的方案：拆类、抽 GroupPhasePicker）。4.5 补记这一句。
- R7｜认，本段处理（项目规矩：发现的问题本轮修，不记账）。修：`web/group_engines.ai_member_ids`；`routers/group.py` 现有 2 个调用点（`:144` 恢复群、`:290` 建群），替代了旧的 3 处内联判断。

### 「成员间距 / 16 条死选择器」当时的结论（样式补充问题 2）

结论：**停下报告，没有改**。依据是样式补充说明第 4 条 —— 判定为「换布局」而非「改名」就停下，附截图交 Tracy 定。这一段原位记录当时的判定与 git log 原始输出：

```
$ git log -1 --format='%h %ad %s' --date=short 0ac7149d
0ac7149d 2026-07-04 refactor(chat): adopt shared shell across chat/dm/group, flatten mobile containers

$ git log -S "group-chat-layout" --oneline -- web/frontend/src | head
9a83f9f6 refactor(theme): 落地主题系统 v2（删 data-theme 维度 + 存储 key 冲突 + 遗留别名迁移）
685271f3 fix(mobile): keyboard vvchange only on height shrink, respect userScrolledUp
aed1d42d fix(mobile): keyboard-aware chat container height via visualViewport
0ac7149d refactor(chat): adopt shared shell across chat/dm/group, flatten mobile containers
…

$ grep -rn "group-chat-layout" web/frontend/src/styles/ | wc -l
16
$ grep -rn "group-chat-layout" web/frontend/src --include=*.jsx --include=*.js | wc -l
0
```

判定过程：`git show 0ac7149d` 把 `<div className="private-chat group-chat-layout">` 手写分栏换成
`<SplitOrFullscreen>` 组件（adopt shared shell）。`group-chat-page` 根节点在该 commit **前后都在**，与
`group-chat-layout` 共存，故**不是改名**。16 条里 `.group-members-panel` / `.group-members-title` 及其媒体查询的
宿主元素已不存在，`.group-target-chip` 现存于建群弹窗（不在群聊布局里）→ 前缀替换不是机械改名，会部分复活 /
部分仍死 / 部分错套。所以本段不动这 16 条，样式补充问题 1（时期行拆类）照做。

Tracy 10-12 定：成员间距的根因是旧布局（0ac7149d 换成 SplitOrFullscreen）留下的死样式，⑤ 不在这里零碎地修，并入单独的『CSS 死选择器』一项，全量调查后统一处理。⑤ 段 A 不动这 16 条，e2e 也不加成员间距的断言。

### 迁移号顺延（10-12，#140 先合并）

10-12 #140（session-record）先合并，占用了 037 / 102，本段按 §0 第 2 条顺延到 038 / 103（两处文件名、`sqlite_store.py` 次序表、迁移注释里的孪生号一起改）。
