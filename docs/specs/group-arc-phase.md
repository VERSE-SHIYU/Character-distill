# ⑤ 段 A：群聊里每个 AI 人物按建群时选的时期说话

基线：main `bc3e1df`（#139 合并后）。一个 PR、5 个 commit。
版本：v2（10-11 21:20，按「不打补丁、隔离、复用、高内聚低耦合」重核后改；v1 作废）。
记录与决定：⑤ 共享文档「⑤ 群聊接入时期：计划与调查记录」。

---

## 0. S0（开工前，执行方逐条做，有一条不成立就停下报告）

1. 本文件、目标检查、参考补丁都在 worktree 里，且和交付的一致：

   | 文件（worktree 内路径） | sha256 |
   | --- | --- |
   | `tests/test_group_arc_phase_goal.py` | `78f9da4065e49cc68976f6fd07136187826d7e4bbdc46d0ca4265bbb8ec4e662` |
   | `docs/specs/artifacts/group-arc-phase-proto.patch` | `ffe4f4357931fc3d3eebabd08d67b21287ff03b6147344ee9b6c5c5af64ed66e` |

   PowerShell：`Test-Path <路径>` 为真，`Get-FileHash -Algorithm SHA256 <路径>` 与上表一致。
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
  - PG：`storage/migrations_pg/037_group_member_phases.sql`（`ADD COLUMN IF NOT EXISTS`）。
  - SQLite 孪生：`storage/migrations/102_group_member_phases.sql`，并登记进 `sqlite_store.py` 次序表末尾。
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
- 每行：沿用成员条目的结构和样式类（F7：`group-member-item` > `Avatar` + `group-member-info` > `group-member-name`），右边 `common/Select size="sm"`（F8），`options` 与 `value` 来自 `phaseChoices`。
- **不新增 CSS。** 现有类在弹窗里排不开，停下报告，附截图，由 Tracy 定。

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

| 以后要加 / 改的 | 改哪里 | 处数 |
| --- | --- | --- |
| 群表再加一个 JSON 列 | `storage/group_row.py` | 1（加列本身的迁移另算） |
| 群引擎加一个构造参数 | `web/group_engines.build_group_engine` | 1 |
| 阶段校验规则 | `core/arc_view.valid_phase`（一对一、群聊共用） | 1 |
| 「没选 = 最后阶段」的前端规则 | `utils/card.effectiveArcPhase` | 1 |
| 阶段文字的格式 | `utils/card.arcPhaseDisplay`（一对一头部、群成员标签共用） | 1 |
| 「谁是 AI 成员」的前端规则 | `utils/groupMembers.aiMemberIds` | 1 |
| 建群表单加一个字段的重置 | `resetCreateForm` | 1 |
| 群对象加一个字段 | 后端返回里加；前端新建与点开都走 `enterGroup` | 1 |

## 5. 步骤（按依赖顺序，每步一个 commit）

1. **[数据库]** 加 `037_group_member_phases.sql` 与孪生 `102_group_member_phases.sql`，登记 SQLite 次序表；同一 commit 放入 `tests/test_group_arc_phase_goal.py`（此时红，正常）。
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

（审计发现写在这里。）
