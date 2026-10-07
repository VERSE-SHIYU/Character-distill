# spec：阶段未验证的处理（①补完 B）+ overlay 与卡片同形

基线 main `c97116b0`（2026-10-07 核） · 参考实现 `docs/specs/artifacts/arc-phase-unlocated-proto.patch`（sha256 `53e9166a21d91c2f521b189a3e1649d75aac0e4e7f2a8e8b7d60b3a5fcea0f4e`，在 `c97116b0` 上 `git apply --check` 通过）
决策记录：本对话 2026-10-06/07 与 Shiyu 逐条定的（D1–D4、方案 2 未定位区、d 同形 overlay、S15 选 A + 机械判定、锚点锁），总计划 `arc-reactions-plan.md`「①补完 B」一节。

**分 4 段、4 个 PR，按 1 → 2 → 3 → 4 合并**（段 1 与段 2 文件不重叠，可两条 lane 并行；段 3、4 串行）。每段先过本段的目标检查（§1），再对账本段变异（§7，脚本带段号），再开下一段。参考补丁是 4 段的合集：每段只取 §4 列给它的文件和改动。

---

## S0（执行方每段开工前做，分钟级）

1. `Test-Path` + `Get-FileHash` 核 worktree 里的 `docs/specs/arc-phase-unlocated.md` 与 `docs/specs/artifacts/arc-phase-unlocated-proto.patch`，补丁 sha256 必须等于上面那串；不符就停。
2. 逐条复核 §2「已查实的约束」里本段用到的坐标（`git show c97116b0:<file> | Select-String`），有一条对不上就停下报告，不自己改。
3. 只做坐标与点复现；任何全量扫描、变异只在「验证」一节跑一次。

---

## §1 目标检查（先跑这个，再对照 spec）

**目标：一条条目挂在哪个阶段，必须有原文位置作证据；没有证据的不进 prompt，也不丢；卡上每个文本叶子都能被引文核对和注入守卫走到。**

| 段 | 目标检查 | 文件 |
|---|---|---|
| 1 | 验收复现：阶段 overlay 里编造的口癖被撤回（`test_g3_…retracted`）；7 个带点字段 `neutralize_one` 全返回 True（`test_g1_…`）；根因锁：卡片与各阶段投影里任何一层键名不含「.」（`test_n5_…`，段 1 版夹具不带未定位区） | `tests/test_arc_phase_unlocated.py` 的 N、G 两组 |
| 2 | 编辑保存后 `source_fingerprint`、`unlocated` 原样还在（`EditCardModalArcKeep`）；store 里是服务端回来的 `selectable`（`applyServerCard`） | vitest |
| 3 | 《孔乙己》公版原文 + 混着对标/错标/编造摘录的草稿，走真实 `card_from_draft`，用**独立预言**（`str.find` 定阶段范围、逐段找摘录）核四条：G1 挂在 p 的都有摘录落在 p（唯一例外：无证据的经历类挂最后阶段）；G2 有合法标注的条目一条不丢；G3 任何阶段的投影卡里没有未定位条目；G4 卡上每个文本叶子守卫都清得掉；G0 夹具自检（确有改挂与挂最后，否则 G1–G3 空转） | `tests/test_arc_phase_unlocated_goal.py` |
| 4 | 挪进阶段 1 后，`project_card(card, 1)` 里有这一条（`test_m8_…`）；Playwright：单值交换前后两边内容、请求体、失败不改本地 | `test_arc_phase_unlocated_move.py`、`e2e/arc-phase-unlocated.spec.js` |

---

## §2 已查实的约束（main `c97116b0`，每条附读过的行）

### 2.1 根因与安全问题（段 1）

- `core/card_draft.py:326` 把登记表路径当键名存：`overlays.setdefault(idx, {})[path] = vals if spec.kind == "list" else vals[0]`；`:347` 同写法存 `key_memories`。
- `core/schema.py:122` `overlay: dict[str, list[str] | str] = {}`；`:144` `_check_overlay` 按扁平键查登记表。
- `core/arc_view.py:198/208/212` 读 overlay 用 `p.overlay.get(path)`（扁平）。
- `core/card_quotes.py:103` `_descend(card, path.split("."), "", out)` —— 按点拆，扁平键 `speaking_style.catchphrases` 被拆成两层，走不到 → 静默跳过。
- `core/moderation/card_guard.py:149` `_SEG_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")` 同样按点分段 → 清不掉。
- **沙箱复现（main 原样）**：
  ```
  retracted: ['character_arc.phases[0].overlay.personality_traits[0]']
  overlay after: {'speaking_style.catchphrases': ['编造口癖'], 'personality_traits': ['他说编造的话']}
  neutralize_one: False
  ```
  登记表里带点的 state/experience 路径 7 个（`speaking_style.tone/sentence_pattern/catchphrases`、`psyche.triggers/soft_spots`、`cognitive.speech_style/knowledge_scope`）：引文核对漏其中 4 个（口癖、软肋、说话风格、句式），守卫 7 个都清不掉。只影响 #116 之后蒸出的卡。

### 2.2 兜底与目的冲突（段 3）

- `core/phase_anchoring.py:182-186`：
  ```
  if not standing:                         # 规则 4：全部被去掉 → 兜底，退回合法标注
      fallback += 1
      logger.warning("兜底：%s %r 的标注全部被去掉，退回原标注 %s", ...
  ```
  摘录查到了、但落在别的阶段时也会走到这里，把**证据已经否定的标注**退回来。四处调用（做法、记忆、通用字段、关系态度）都经过这一个 `verify`。
- 样本：赵太爷「托邻居请人看旧货」（第六章，属阶段 1）被模型标在阶段 3。

### 2.3 编辑保存与出卡（段 2）

- `web/frontend/src/components/EditCardModal.jsx:153-156` 保存时 `character_arc: { axis, phases }` 整个替换 → 丢 `source_fingerprint`（vitest 已复现：保存出去的 `character_arc` 里没有 `source_fingerprint`）→ 编辑过的卡 `has_positions()` 为假、失去选阶段；段 3 之后还会丢未定位区。
- `web/routers/distill.py:1277` `return {"ok": True, "card": result}`（PATCH 不走 `out_card`）。
- `web/frontend/src/store/useAppStore.js:1790-1802` `updateCard` 写回的是**本地提交的** `cardJson`，不是服务端返回值 → store 里的 `selectable` 是旧值。
- `tests/test_arc_phase_fields_locks.py:322` S15 钉死 `callers == {"list_cards", "list_standalone_cards"}`。
- schema 对 `CharacterArc` 未声明的键（如派生的 `selectable`）忽略：已验证 `model_dump()` 里没有 `selectable`，编辑带回去不会落库。

### 2.4 常设变异驱动在 main 上已失效（段 3）

锚点锁原型在 main 上的原始输出：
```
arc_phase_anchoring_mutations.py | M17 …：锚点在 card_draft.py 命中 0 次（应恰 1）：'        nums = [occ.phase for o
arc_phase_anchoring_mutations.py | M30 …：锚点在 card_draft.py 命中 3 次（应恰 1）：'    occurrences: list[DraftOccurrence] = [
arc_phase_anchoring_mutations.py | M35 …：锚点在 phase_anchoring.py 命中 0 次（应恰 1）：'                logger.warning("去掉标注：做法 %r 在阶段 %d 没
```
另：`card_draft_mutations.py` M27 的锚点在 main 上恰一命中，但 #116 在「校验失败」分支后加了同形的 `DistillError` 分支，锚点滑到了没有测试的那一支 —— main 上把 M27 打进去，`tests/test_card_draft.py` 23 passed（空转）。元锁 `test_lock_coverage.py` 只核产物，两类都没发现。

### 2.5 环境（S0 起环境前查）

- 测试库：`docker-compose.test.yml` 的 PG 16，`charsim / ci_test_password / charsim_test`，端口 55432；被占时起一次性库（如 55433），跑完删，不动别人的容器。
- **main 基线已有的失败（与本 spec 无关，见 §12）**：`tests/test_arc_phase_fields_unit.py::test_relationship_batch_splits_and_merges` 在 2 核机器上 6/6 失败（main 原样）：断言并发批次的**调用开始顺序** `[10, 10, 3]`，`core/relationship_batch.py:82` 用 `ThreadPoolExecutor(max_workers=len(batches))` 并发，开始顺序不保证。
- `e2e/arc-phase-select.spec.js` 在 main 上 2 条失败：夹具没带 `selectable`（#116 起弹窗只认它）。段 2 一并修。

---

## §3 规则（段 3 的位置规则 + 段 1 的同形 + 段 4 的挪动）

### 3.1 按摘录位置定阶段（取代第二步规则 3、4 与兜底 D5）

`core/phase_anchoring.py::verify`：**每段摘录各是一份证据，摘录落在哪个阶段就挂哪个阶段。**

a. 一段摘录查不到、或最长段在原文出现超过 `MAX_OCCURRENCES`（3）次 → 不作证据（计 `unverified_quotes`）。
b. 有一处落在所标阶段 p → 只认 p，其余出现位置不加挂（否则一句反复出现的台词会给条目平白多挂几个阶段）。
c. 一处都不在 p → 挂到它所有出现位置所在的阶段（改挂，计入 `rehung`，按**条目**计，一条改挂两个标注也记 1）。
d. 一条条目各段证据**取并集**，再交 `card_draft.dispatch` 套类别规则：状态类挂并集里所有阶段；经历类只挂最早的那个（`_layer_phases`）。
e. 同一阶段既有自己的摘录、又有改挂来的摘录 → `source_quote` 取自己的。
f. 一条条目所有标注都没有证据 → 进 `Verification.unlocated`，**不退回模型原标注**。兜底 D5 只在整卡跳过（规则 2：锚点空/查不到/逆序）时保留。
g. 阶段号判定只此一处：`phase_at(ranges, pos)`，区间半开。
h. warning：去掉的标注各一条（`去掉标注：%s %r 在阶段 %d 没有能定位的摘录`）；改挂一条（`改挂：…标在阶段 %d，摘录落在阶段 %s`）；未定位一条。

### 3.2 没有证据的条目按类别处置（`card_draft.dispatch`，分发只此一处）

| 类别 | 去处 | 依据 |
|---|---|---|
| 状态类（做法、性格、价值观、语气等） | 未定位区：保留、展示、**不进 prompt** | CDT：不注入未验证的 |
| 经历类（记忆、知识范围） | 挂最后阶段 | 由 DREAM 时间约束推出：时间不明放最后，永不泄露后续剧情 |
| 无阶段的卡（`count == 0`） | 不做位置检查：两类都进顶层，不进未定位区（`test_u10_…`） | 规则 5：没有阶段就没有「挂错阶段」 |
| 整卡跳过 | 保留模型标注（现状） | 代码事实：这类卡只按最后阶段聊 |

### 3.3 关系态度（`card_draft._convert_relationships`）

- **每条态度单独**作为一个检查条目走同一个 `verify` 与 `dispatch`，再按关系收回（每个阶段的态度文字不同，不能整条挪）。不按关系聚合：聚合后同一阶段的几条态度共用位置证据，A 的摘录会把 B 也带走（补充·审计发现 1，`test_r7_…` 与目标夹具「孩子们」）。
- 撞车（两条态度落到同一阶段）：原本就标在这个阶段的优先，其余按标注顺序取第一条；**输掉且在别处没赢**的进未定位区，`note` 跟着走，带上原来标的阶段 `phase`（挪回时预选用）。在别处赢了的不进。
- 一条态度都不剩的关系 → 挂最后阶段、态度留空（不编造）。态度留空时：`context_engine.py` 卡片扩展层那一行不带冒号；`chat_engine.py` 那一行不带逗号；群聊回落「普通群聊关系」（`group_session.py:297`，已核）。
- 顶层 `attitude` 取挂上的最后一个阶段的态度；无阶段的卡取最后一条态度。占位的生成与识别（`schema.placeholder_phase_attitudes` / `is_placeholder_attitude`）、顶层取值（`schema.top_attitude`）、经历类无证据挂最后（`card_draft.experience_fallback`）各只一处，转卡与挪动共用。

### 3.4 未定位区形态

```
character_arc.unlocated = {
  behaviors: [SituationBehavior],                      # 同阶段做法
  overlay:   {…与卡片同形，只收 state 路径，一律列表…},   # 单值字段也可能有多条
  attitudes: [{target, attitude, note, phase}],        # phase = 原来标的阶段，0 = 不知道
}
```
- 登记表 `core/card_layers.py` 新增 3 条 none 层：`character_arc.unlocated.behaviors`（list）、`.overlay`（**`kind="map"`，只用于 none 层**）、`.attitudes`（list）；U1 总数 37 → 40。
- 未定位区 overlay 与阶段 overlay **用同一个校验器** `card_layers.check_overlay`（键名不含「.」、叶子须为登记路径、形态对）；未定位区只收 state、一律列表。
- `arc_view.project_card` 投影时清空未定位区；SillyTavern 导出不带（已测 `test_p3_…`）。
- 照样进引文核对（`card_quotes._overlay_paths` / `_behavior_paths` 从登记表与顶层路径派生，不另抄）和审核遍历（`moderation/card_text.iter_texts` 本来递归，补测试）。

### 3.5 overlay 与卡片同形（段 1）

- 阶段 overlay = 「这个阶段特有的那部分字段值」，长得和卡片一样：`{"personality_traits": [...], "speaking_style": {"catchphrases": [...]}}`。叶子路径就是登记表路径，读写一律 `get_path` / `set_path`。
- **唯一转换点**：`ArcPhase` 的 `model_validator(mode="before")` 用 `card_layers.nest_flat_keys` 把旧扁平键转嵌套（与①的 memories 迁移同一处）；同一路径两种写法并存时以嵌套为准。
- 校验 `check_overlay(overlay, layers=…, lists_only=…, where=…)`：① 任一层键名含「.」直接拒；② 叶子须是 `layers` 类的登记路径；③ 形态按 `kind`（未定位区一律列表）。
- 引文核对、注入守卫的解析代码**一行不改**：它们本来就能正确走嵌套结构。
- 前端 `ArcList` 遍历嵌套叶子、拼回路径查标签表；认不出的路径显示拼回的原名（旧卡、校验器之外的数据不静默丢）。
- 根因锁（`test_n5_…`）：卡片、各阶段投影里任何一层键名都不含「.」。以后谁再把路径当键存，当场红。

### 3.6 挪进阶段（段 4）

- 规则只在后端纯函数 `core/unlocated.py::move_unlocated(card, *, section, index, phase, path="")`：「列表还是单值」只认登记表 `kind`；列表追加；**单值交换**（原值退回未定位区，D4）；态度挪入先去掉「态度留空」占位（不然投影取 ≤k 最新一条会被空态度盖住），阶段 k 已有态度也交换。参数不合法抛 `ValueError`。序号漂移（两个标签页先后挪动、编辑后再挪）不在这里判：路由先按卡的 `revision` 核对（§13），卡变过即 409，传进来的就是调用方看到的那一版。（补充·审计发现 7 首轮的修法是请求带条目内容 `expected` 逐条比对，§13 落地后被版本核对取代、已删。）
- 接口 `POST /api/distill/card/{card_id}/unlocated/move`，body `{section, index, phase, path, revision}`：复用 `get_card_owned` 归属鉴权（非属主与不存在同判 404）；`revision` 不符或写入时比较失败 → 409（§13）；`ValueError` → 400「这一条已经不在未定位区，请刷新后重试」；**返回值走 `out_card`**。演示账号门禁自动拦住这个 POST（`test_demo_gate.py` 已跑过，绿）。
- 前端：按钮放卡片详情（`CharCard.jsx` 的 `CardDetail`）的「未定位的条目」一节，**只在 `useCanWrite()` 为真时渲染**；市场卡详情（`MarketCardDetail`）不渲染。不放编辑弹窗（弹窗有未保存的本地改动，会互相覆盖）。阶段选择用全站的 `common/Select`（Radix），条目与按钮复用现有样式类，不另写 CSS。默认选中：态度用它原来标的阶段（在 1..n 内），其余用最后阶段。
- **触发链**：选阶段（`Select` onChange → `MoveControl` 本地 state）→ 点「挪入」→ `MoveControl.run` → `onMove(phase)` → `CardDetail.handleMoveUnlocated(section, index, phase, path)` → `store.moveUnlocated(cardId, {..., revision: card.revision})` → POST → 成功：`store._applyServerCard(cardId, data.card)` 写 `cards` 与 `currentCard` → `CardDetail` 重渲染：`parseCardJson` → `ArcList` 与 `UnlocatedList` 都从新卡重算；失败：抛错 → `setError` 上屏，本地卡不动。

### 3.7 监测（`[phase_anchoring]` 一行）

去掉 `fallback`；新增 `rehung`（改挂）、`unlocated`（进未定位区）、`to_last`（无证据挂最后阶段）三项汇总，并按四类分别计：`kinds=做法:改挂/未定位/挂最后,字段:…,记忆:…,关系:…`。②③后统一重蒸时，用它判断残余集中在哪类，决定要不要上 D（分段生成）。实测样例行：
```
… rehung=2 unlocated=1 to_last=1 skipped_card=False memories_dropped=1 kinds=做法:1/0/0,字段:0/1/0,记忆:0/0/1,关系:1/0/0
```

---

## §4 分段

| 段 | 关注点 | 文件（参考补丁里取这些） | 测试 |
|---|---|---|---|
| **1** 安全 | overlay 同形 + 根因锁 | `core/card_layers.py`（`overlay_leaves`、`check_overlay`、`nest_flat_keys`；**不含**未定位区 3 条登记与 `map`）；`core/schema.py`（`ArcPhase` 的 overlay 类型、迁移调用、`_check_overlay` 调 `check_overlay`）；`core/arc_view.py`（3 处 `get_path`）；`core/card_draft.py`（overlay 写入改 `set_path`，含 `key_memories`）；`web/frontend/src/components/common/ArcList.jsx`；`tests/test_arc_phase_fields_readers.py`、`_unit.py` 里测试夹具改 `set_path` | `tests/test_arc_phase_unlocated.py` 的 N1–N4、N5（段 1 夹具不碰未定位区）、N6、G1–G3；`ArcListNested.test.jsx`；`arc-phase-fields.spec.js` 照绿 |
| **2** 正确性 | 编辑保存不丢阶段信息 + 出卡统一 | `EditCardModal.jsx`（`...data.character_arc`）；`distill.py` PATCH 返回 `out_card(result)`；`useAppStore.js` 的 `_applyServerCard` + `updateCard` 写服务端返回值；S15 锁（§6）；`e2e/arc-phase-select.spec.js` 夹具补 `selectable: true` + 等 `authUser` 再注入 | `EditCardModalArcKeep.test.jsx`、`applyServerCard.test.js`、S15 两个方向（§6）、`arc-phase-select.spec.js` |
| **3** 规则 | 位置规则 + 未定位区 + 关系逐条 + 监测 + 驱动修复 + 锚点锁 | `core/phase_anchoring.py`；`core/card_draft.py`（`dispatch` 三元组、`_convert_relationships`、`card_from_draft`、监测行）；`core/schema.py`（`UnlocatedAttitude`、`UnlocatedItems`、`CharacterArc.unlocated`）；`core/card_layers.py`（3 条登记 + `map`）；`core/card_quotes.py`；`core/arc_view.py`（投影清空未定位区）；`core/context_engine.py`、`core/chat_engine.py`（空态度）；`tests/perf/arc_phase_anchoring_mutations.py` + `card_draft_mutations.py` + 两个 `*_red_lines.json`（重跑生成）；`tests/test_mutation_anchors.py` | `test_arc_phase_unlocated_goal.py`；`test_arc_phase_unlocated.py` 其余各组（N4b、U、R、P、Q）；`test_phase_anchoring.py`（U7、U13–U15 改写，新增 U13b–d，U19、U20、U22 改写）；`test_arc_phase_fields_unit.py`（总数 40、`dispatch` 三元组）；`test_card_arc_behaviors.py`；`test_identify_failure_channels.py`（夹具正文补摘录，补充 8）；两个常设驱动；元锁；锚点锁 |
| **4** 功能 | 挪进阶段 + 乐观锁（§13） | `core/unlocated.py`；`distill.py` 挪动接口、PATCH 核对、`_persist_awakening`；`core/card_out.py`（`card_revision`、`CARD_CONFLICT`）；`storage/base.py` + 两个 store 的 `update_card`（比较后写入）；`useAppStore.js` 的 `moveUnlocated` / `updateCard` / `_applyServerCard`；`UnlocatedList.jsx`；`CharCard.jsx`；`TextPanel.jsx`（编辑保存走 `updateCard`）；S15 白名单加 `move_unlocated_item`；S11 导入锁放行 `core.fingerprint`；四处存储测试调用点补 `expected` | `test_arc_phase_unlocated_move.py`；`test_card_optimistic_lock.py`；`test_postgres_store.py::TestPgCardCompareAndSwap`；`UnlocatedList.test.jsx`、`CharCardUnlocated.test.jsx`、`moveUnlocated.test.js`、`applyServerCard.test.js`、`TextPanelEditSave.test.jsx`；`e2e/arc-phase-unlocated.spec.js` |

---

## §5 出处表

| 设计 | 出处 |
|---|---|
| 摘录落在哪就挂哪（3.1 b/c） | **文献（类比）**：CiteFix（ACL 2025 Industry Track）——查不到出处的事实多数是标错出处而非编造（Model C 约 80%，四个模型 66.6%–90.8%），按文本匹配改挂可纠正；DREAM（KDD 2026）按原文章节定事件时间、加时间戳防检索泄露后续 |
| 无证据的状态类不进 prompt（3.2） | **文献**：CDT（ACL 2026）未验证的规则有害 |
| 无证据的经历类挂最后阶段（3.2、3.3） | 由 DREAM 的时间约束**推出** |
| 部分去掉时只有态度进未定位区（D3） | CiteFix：对不上多数是引错，应改挂保留 |
| 撞车时原标注优先（3.3） | **工程原则，无文献** |
| 单值挪入交换（D4，3.6） | **工程原则，无文献**（不静默丢用户数据） |
| overlay 同形、`check_overlay` 唯一校验器、出卡只经 `out_card` | 代码事实（§2）+ 架构原则「同一知识只一处」 |

---

## §6 出卡统一（S15）—— 选 A：把锁改回本意

本意：凡是会流到开聊按钮的卡，都经 `out_card` 现算 `selectable`。PATCH 与挪动的返回值会写进 store、被开聊按钮读到，所以走 `out_card`。

**锁的机械判据**（`test_arc_phase_fields_locks.py::test_s15_…`，参考补丁已改）：
- 白名单 `{"list_cards", "list_standalone_cards", "update_card", "move_unlocated_item"}`；
- `_returns_result_of(distill.py, "update_card")`：AST 找「`x = await <obj>.update_card(...)` 且某个 `return` 用到 `x`」的函数，必须 `== {"update_card", "move_unlocated_item"}` 且 ⊆ 白名单。后台任务（`_run_distill_task`、`distill_stream._event_gen`）只写不返回，判据不算它们 —— 第一版按「调用了 update_card」判，把这三个误算进来，已改。
- 两个方向已验：PATCH 去掉 `out_card` → 红；挪动去掉 → 红。

**全量扫描：返回卡片内容的接口一共 18 个**（AST：路由函数的 `return` 里用到了返回 `card_json` 的存储方法结果）：

```
admin.py   GET /cards                      admin_list_cards      list_all_cards_admin
card.py    GET /trash                      list_trash            list_deleted_cards
card.py    GET /{card_id}/detail           get_card_detail       get_card_detail
card.py    GET /{card_id}                  get_card              get_card_owned
distill.py PATCH /card/{card_id}           update_card           update_card          ← 本 spec 接入
distill.py POST /card/{card_id}/unlocated/move  move_unlocated_item  update_card     ← 本 spec 接入
distill.py GET /cards/by-text/{text_id}    list_cards            list_cards           ← 已接入
distill.py GET /cards/standalone           list_standalone_cards list_standalone_cards ← 已接入
distill.py GET /cards/{card_id}/export     export_card           get_card_owned（返回导出文件，非卡片 JSON）
market.py  GET /featured  /list  /search  /global-search  /author/{user_id}  /card/{card_id}
           POST /{card_id}/publish   GET /{card_id}/versions   GET /{card_id}/forks   （9 个）
```
开聊按钮 `StartChatButton` 只在 `DistillWorkbench.jsx` 与 `CharCard.jsx` 用，取数是两个列表接口 + store 里的 `currentCard`。其余 14 个接口的卡目前不流到开聊按钮。数量多、改动面大 → **按你的规则先按 A 扩白名单，全面接入（「凡返回卡片一律走 out_card」+ 按返回值机械判定）由你定**，见 §12。

---

## §7 对账表（发出前已在沙箱预跑，原始输出见 `runlogs/`）

一次性脚本 `docs/specs/artifacts/arc_phase_unlocated_mutations.py [段号]`（不进元锁，返工经验 #10）。每段跑自己那份；段 4 合并后不带段号跑全部。

| 段 | 变异 | 方向 / 改坏什么 → 靶子 |
|---|---|---|
| 1 | X1 旧扁平键不转换 → N1；X2 扁平重复覆盖嵌套（过严）→ N2；X3 不查登记 → N3；X4 不查形态 → N4；X7 回到扁平存法（写入+不转换+不拒点，三处一起）→ N5 根因锁；X7b 守卫寻址不按点分层 → G1；X7c 带点字段不派生核对路径 → G2；X8 阶段 overlay 不进核对 → G3；F2 ArcList 不走嵌套 → vitest |
| 2 | F1 编辑保存整体替换 `character_arc` → vitest；F3 `updateCard` 写本地卡 → vitest |
| 3 | X5 不拒带点键名、X6 未定位区不走同一校验器 → N4b；X7d 根因复发 → 目标 G4；X9 恢复兜底 → 目标 G1；X10 未定位做法被丢 → 目标 G2；X11/X11b 未定位区进投影 → 目标 G3 / P1；X12 不改挂 → 目标 G0；X13 状态类被丢；X14 经历类被丢；X15 经历类挂阶段 1（泄露）；X16 经历类挂全部落点；X17 未定位单值存成单值；X18 整卡跳过也进未定位区（过严）；X19 分类计数漏关系；X20 撞车原标注不优先；X21 输掉的态度被丢；X22 在别处赢的也进未定位区；X23 无态度关系挂阶段 1；X24 无态度关系沿用模型态度；X25 空态度带冒号；X26 无阶段卡取第一条态度；X27/X27b 未定位做法不进核对；X28 未定位 overlay 不进核对；X29 未定位态度不进核对；X30 审核遍历跳过未定位区；X34/X34b 同一关系的态度共用位置证据 → R7 / 目标 G1；X37 无阶段卡的状态类进未定位区（过严）→ U10 |
| 4 | X31 单值不交换；X32 态度不去占位；X33 接口不走 `out_card`；F4 打错接口；F5 失败也写回；F6 态度默认阶段不用原标注；F7 传错路径；F8 只读账号也显示；F9 挪入没接 store；L1/L2 PATCH、挪动不核对 revision（放宽）；L3 存储不比较就写（放宽，真 PG）；L4 存储比较失败当成功；L5 唤醒语拿旧卡整卡写回；L6 PATCH 一律 409（过严）；L7 revision 按补过 selectable 的串算；F10/F13 编辑、挪动不带 revision；F11 写回后留着旧 revision；F12 角色管理改回自己发 PUT |

**预跑结果**：`结论：61/61 条全红`（审计后补 X34/X34b/X37，§13 补 L1–L7、F10–F13，删首轮的 X35/X36 与旧 F10；首轮 47/47），16 个目标文件还原后 sha256 逐字节一致。第一轮 X7b/X7c 没红：变异打在写侧，而对应测试测读侧 —— 改成读侧变异后红；写侧复发由 X7、X7d 管。

常设驱动（段 3）：
- `arc_phase_anchoring_mutations.py`：M1–M4 按新规则重写（M1 恒认所标、M2 命中也挂全部落点、M3 恢复兜底、M4 不改挂、M4b 只取最早落点），M7–M9、M11、M18、M21、M25、M34、M35 换锚点，M17、M30 修 main 上的锚点漂移，新增 M37（删改挂 warning）、M38（rehung 按标注计）、M39（改挂摘录压过自己的）。**42/42 红，产物重写，元锁 31 passed。** 预跑中补的判别器：M3 → `test_u13b`，M38 → `test_u13d`，M39 → `test_u13c`；M25 原写法让代码崩溃、元锁判「没撞判据」，改成不崩溃的等价写法。
- `card_draft_mutations.py`：M5 锚点跟 `dispatch` 三元组；M27 锚点钉到「校验失败」那一支（带上一行 `logger.error("Pydantic 校验 …")`）。**38/38 红，产物重写。**

---

## §8 机制缺口：锚点锁（段 3）

`tests/test_mutation_anchors.py`：加载 `tests/perf/*_mutations.py`（当前 7 个），按各自的 `GROUPS`（无则 `MUTATIONS`）把每条变异的编辑在**内存里**依次套到文件文本上，判据与 `mutation_framework._apply` 同一条（`repl` 锚点恰一命中；`append`/`hide` 目标存在；`write` 目标不存在）；条目按 `item[:4]` 取（兼容带 marker 的五元组）。0.1 秒跑完，进 CI。正控：命中 0 次、多次都必须报出。

- main 上：`1 failed, 8 passed`（M17/M30/M35，§2.4 原始输出）；改后：`9 passed`。
- 它管不到「恰一命中但落在没测试的同形分支」（M27 那类）——那由驱动跑出的空转判定（元锁）管；本锁只保证驱动还能跑起来。

---

## §9 规模表

| 数据 | 上限与来源 | 处理 |
|---|---|---|
| 每卡做法 | 5–13 条（4 张样本卡全量） | 未定位区同量级，列表全展示 |
| 摘录最多出现次数 | `MAX_OCCURRENCES = 3`（`phase_anchoring.py`） | 超过不作证据 |
| 阶段数 | 样本 2–4 | `common/Select` 全列 |
| 带点的 state/experience 路径 | 7（登记表扫描） | N1、G1、G2 逐个参数化 |
| 登记表叶子 | 37 → 40 | U1 断言 |

## §10 调用点矩阵（行 = 调用点，列 = 可观测输出 → 测试）

| 调用点 | 落卡阶段 / 未定位 | 监测行 | prompt | 守卫/核对 | 前端 |
|---|---|---|---|---|---|
| `card_from_draft` 做法 | U1、U10、U13、G0–G2 | U8、U22 | P2、G3 | Q1、Q2 | — |
| 通用字段（含带点） | U2、U3、N6 | U8 | P2 | G1–G3、Q3、Q6 | ArcListNested |
| 记忆 | U4、U9 | U8 | P2（正控：挂最后的只在阶段 3） | — | — |
| 关系态度 | R1–R4、R6、R7、目标「孩子们」 | U8 | R5 | Q4 | — |
| 整卡跳过 | U7 | 原有 | — | — | — |
| PATCH | §13 ×3 | — | — | — | applyServerCard、CharCardUnlocated、TextPanelEditSave、S15 |
| 唤醒语回写 | §13 ×2 | — | — | — | — |
| `update_card`（存储） | TestPgCardCompareAndSwap ×2 | — | — | — | — |
| 挪动接口 | M1–M8、route ×3、§13 挪动 ×2 | — | M5、M8 | — | UnlocatedList、CharCardUnlocated、moveUnlocated、e2e |

---

## §11 测试（每段固定写法）

- 本地只跑本段受影响的文件 + `npm test`；PG 用 docker 测试库（§2.5）。
- 本段变异：`python docs/specs/artifacts/arc_phase_unlocated_mutations.py <段号>`；段 3 另跑两个常设驱动与元锁、锚点锁。
- Playwright：`cd web/frontend && npx playwright test e2e/arc-phase-unlocated.spec.js`（段 4）/ `arc-phase-select.spec.js`（段 2）。截图 `e2e/arc-phase-unlocated-before.png` / `-after.png` 给 Shiyu 看排版。沙箱预跑（审计后重跑）：三个 spec 一起 10 passed。**首轮的「30/30」是改样式之前跑的**，改样式后 `arc-phase-unlocated.spec.js` 的 `.card-unlocated-item` 选择器已失效而未重跑（补充·审计发现 4）—— 改样式类后必须重跑 e2e。沙箱的 Playwright 浏览器版本与锁不符时，用临时配置 `launchOptions.executablePath` 指到 `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`（不入库）。
- 合并门是分支 CI；合并只做 `gh pr create` → `gh pr merge --merge`。报告里不写本地全量数字。

沙箱预跑（全部段合起来，审计后重跑）：后端受影响选集（46 个文件）995 passed / 1 failed（即 §2.5 main 原有的那条）；前端 469 passed；CI lint 0 error（10 个 warning 全在本 spec 没碰的文件，main 原样）。

## §12 要 Shiyu 定的（不在本 spec 范围内）

1. **出卡全面接入**：其余 14 个返回卡片的接口是否也一律走 `out_card`、S15 改成「凡返回卡片」的机械判定（§6）。
2. ~~挪动与 PATCH 的丢失更新~~ —— Shiyu 10-07 定「一起做」，已落地，见 §13。
4. **市场编辑（`PUT /api/market/{id}/publish`）的丢失更新与一处既有故障**（§13.5）：这条接口有 3 个调用方、语义不同 —— 市场详情「编辑」是读出-修改-写回（该加锁）；「恢复到某版本」与卡片页「再次分享」是有意整卡覆盖（不该锁）。要锁就得先把这条接口按语义拆开，另外它依赖市场详情的出卡带 `revision`（即第 1 条的一部分）。另：卡片页「再次分享」走 PUT，后端返回 `{version}`，前端却按 `data.card_id` 判成功（`CharCard.jsx` 发布确认按钮，缺陷 105 的修法引入）→ **再次分享实际成功、界面却报「发布失败：服务端未返回 card_id」**。这两件怎么处理？
3. **main 原有的失败用例** `test_relationship_batch_splits_and_merges`：断言并发调用的开始顺序，修法是只断言批大小的多重集与输出顺序（输出顺序才是被保证的性质）。是否另开一个小 PR。

## §13 乐观锁：整卡写回的丢失更新（Shiyu 10-07 定「一起做」）

### 13.1 已查实的约束（分支 `proto/arc-phase-unlocated`，基线 `ccf203a`）

1. 整卡写回的入口：`PATCH /api/distill/card/{id}`（编辑保存）、`POST …/unlocated/move`（挪动）、两处唤醒语回写（`_run_distill_task` 与 `distill_stream`，原先拿蒸馏时内存里的卡整卡写回）。四处都经 `storage.update_card`。
2. `cards.updated_at` **不能当版本号**：列是 `TEXT DEFAULT ''`（`storage/migrations_pg/001_init.sql:49`）；只有 `update_card` 写它，重蒸的 `save_card`（`postgres_store.py:696`）与市场编辑的 `update_published_card`（`:5245`）改了 `card_json` 却不碰它；`get_card_unscoped` / `list_cards` 的 SELECT 也不返回它。
3. 角色管理（`TextPanel.jsx` 内层 `CharacterManagement`）的编辑保存在 main 上本来就是坏的：前端发 `PUT /api/distill/card/{id}`、`card_json` 传字符串，后端只有 `@router.patch("/card/{card_id}")`（`distill.py`）→ 必 405。属本节改动面，一并修（补充 9）。
4. `store.updateCard` 失败时既 `set({error})` 又抛出，而两个调用方的编辑弹窗都自己呈现错误 → 同一个错报两遍（补充 10）。
5. 出卡到可编辑处的路径：卡片详情与角色管理的卡都来自两个列表接口（`list_cards` / `list_standalone_cards`，已走 `out_card`）；会话恢复建的 `currentCard` 不带 `card_json`，不进编辑。

### 13.2 设计（依据）

- **版本 = 内容指纹，出卡现算、不落库**：`core/card_out.py::card_revision(raw)` = 存储原文的 `content_fingerprint`（复用 `core/fingerprint.py`，不另写哈希）；`out_card` 在行上加 `revision`，按**存储原文**算（不按补过 `selectable` 的串）。任何写入方改了内容它自然就变，不需要「每处写入记得加一」，也不用迁移。依据：HTTP 的强校验器（RFC 9110 §8.8.1 / §8.8.3，ETag 常取内容摘要）。
- **两道核对**：① 路由先比 `card_revision(读到的原文)` 与请求带的 `revision`，不符 → 409；② 存储层 `update_card(card_id, card_json, *, expected)` 用同一条 `UPDATE … WHERE id = $2 AND card_json = $4` 比较后写入，返回 None → 409。①挡「看到的已过期」，②挡「核对之后、写入之前又被改」。`expected` 关键字必填，新调用点漏不掉。
- **冲突码 409 + 统一文案** `CARD_CONFLICT`「这张卡已在别处更新，请刷新后再改」（`core/card_out.py`）。版本放在 JSON 体里而不是 `If-Match` 头：列表里每张卡各有版本，头里放不下；做法同 Kubernetes 的 `resourceVersion`（冲突 409），本仓 409 也已用于状态冲突（`auth.py`、`memory.py`、`message.py`）。
- **唤醒语回写**改为 `_persist_awakening`（`distill.py`）：读库里当前那张卡 → 只改 `awakening_message` → 比较后写入；比较失败只打 warning（唤醒语本来就是 non-fatal）。不再拿内存里的旧卡整卡覆盖用户在这几秒里的编辑。
- **重蒸（`save_card`）不加锁**：重蒸本来就是有意整卡覆盖；它改了内容，用户手里的 revision 随之失效，之后的编辑会正确地 409。
- 挪动的 `expected`（审计发现 7 首轮修法）删除：版本核对已覆盖序号漂移，留着就是同一件事两套机制。
- 前端：`store.updateCard(cardId, cardJson, revision)` 与 `moveUnlocated(…, {revision})` 带上版本；`_applyServerCard` 把服务端返回的新 `revision` 写进 `cards` 与 `currentCard`（不写的话连续第二次保存必 409）；`updateCard` 失败只抛给调用方、不写全局 error。

### 13.3 触发链

- 编辑保存：编辑弹窗「保存」→ `onSave(cardJson)` →（卡片详情）`CardDetail.handleSaveEdit` /（角色管理）`CharacterManagement` 的 `onSave` → `store.updateCard(id, cardJson, card.revision)` → PATCH → 200：`_applyServerCard` 写回卡与新 revision，弹窗关闭；409：`fetchWithTimeout` 抛错 → 弹窗内显示「这张卡已在别处更新，请刷新后再改」，弹窗不关、用户的修改还在。
- 挪动：同 §3.6，409 时 `setError` 上屏、本地卡不动。

### 13.4 测试与变异

- `tests/test_card_optimistic_lock.py`（9 条）：出卡 revision 按原文且随内容变；PATCH 正确版本 → 200 且返回新 revision；PATCH 旧版本 → 409、卡不动；PATCH 竞争（核对后被改）→ 409、别人的写入保留；挪动旧版本、挪动竞争同理；唤醒语写进最新卡、保留用户编辑；唤醒语竞争 → 不写、有 warning；`card_revision` 等于存储原文的全量指纹（补充 13）。
- `tests/test_postgres_store.py::TestPgCardCompareAndSwap`（2 条，真 PG）：版本对 → 写入；版本旧 → 返回 None、库里是先写的那份。
- 前端：`applyServerCard.test.js`（请求带 revision、写回换新 revision、失败不写全局 error）、`CharCardUnlocated.test.jsx`（编辑保存与挪动都带 revision）、`TextPanelEditSave.test.jsx`（角色管理保存走 `updateCard` 且带 revision，不再自己发请求）。保存后的弹窗（§13.3，补充 12）：两个调用点各测两侧 —— 成功才关；失败不关、错误交回弹窗，`TextPanelEditSave` 用真弹窗断言报错上屏、用户改的内容还在。
- 一次性变异脚本新增 L1–L7、F10–F13（替换首轮的 X35/X36/F10），段 4 20/20 红，全部 61/61 红（§7）。
- Playwright 三个文件 10 passed（挪动请求体带 revision）。

### 13.5 没做的（要 Shiyu 定，见 §12 第 4 条）

市场编辑 `PUT /api/market/{id}/publish`：3 个调用方语义不同（编辑要锁；恢复版本、再次分享是有意覆盖），且市场详情出卡没走 `out_card`。只给它加锁要先拆接口，不在本节做。

## 自检表

| 项 | 结果 |
|---|---|
| 每条代码判断附了读过的行或原始输出 | §2 全部带坐标 / 输出 |
| 与自身规则冲突 | 首轮有：关系绕开 `dispatch`、顶层取值与占位各写两处（补充·审计发现 2）；已收为各一处 |
| 对照调研结论 | 「未验证不注入」= 阶段未验证的状态类不进 prompt；经历类挂最后不泄露 |
| 每条边界两侧都测 | 放宽 / 过严成对：X1/X2、X13/X15、X21/X22、X18、U13b/U13c |
| 拒侧用例另有能通过的标注 | `test_phase_anchoring.py` 约定沿用（约定 1 的说明已改为「否则整条进未定位区」） |
| 变异预跑无存活 | 61/61、42/42、38/38 |
| 目标检查独立于被测代码 | G1 用 `str.find` 预言，不调 `phase_at` / `verify` |

## 补充（审计发现写这里）

审计（中途报告，基线 `9c414c2`）7 条 + 修复时自查 1 条。每条：原因 → 处置 → 守它的测试 / 变异。

1. **关系态度撞车时归错阶段**（spec 设计错，§3.3 原写「按 `tag_targets` 逐条分发」）。`tag_targets` 按「条目 × 所标阶段」聚合，而条目是整条关系（拿 `DraftMemory` 当载体），同一阶段的几条态度共用位置证据。复现：两条都标阶段 1，A 的摘录在阶段 2、B 的在阶段 1 → 输出 `[(1,A),(2,A)]`，B 进未定位区。**处置**：每条态度单独过 `verify` 与 `dispatch`，再按关系收回。**测试**：`test_r7_…`；目标夹具加「孩子们」（旧实现下 G1 红在 `[(1, 'A撞车外')]`）+ 夹具自检 `test_g0_fixture_really_has_a_same_phase_collision`。**变异**：X34 / X34b。
2. **同一规则写在两处**（根因同 1：关系绕开了 `dispatch`）。**处置**：关系走 `dispatch`；`experience_fallback`（经历类无证据挂最后）、`top_attitude`（顶层取最后阶段）、`placeholder_phase_attitudes` / `is_placeholder_attitude`（占位生成与识别）各一处，转卡与挪动共用。**变异**：X15、X23 改打到这几处。
3. **§3.2「无阶段的卡：状态类进未定位区」与代码不符**（spec 写错，代码对：规则 5 无阶段不做位置检查）。**处置**：改 §3.2 那一行。**测试**：`test_u10_…`（原先无测试守这一行，`test_u6` 测的是单阶段卡）。**变异**：X37。
4. **e2e 选择器失效**（实现偏离：改样式类后没重跑 e2e）。`.card-unlocated-item` → `.card-unlocated .card-behavior-item`。审计后重跑三个 spec 10 passed，截图已重出。
5. **spec 文字没跟上代码**：§4 段 4 去掉 `global.css`；§3.3 `:298` → `:297`。
6. **目标文件的不足**：G2 补 `catchphrases`；docstring 写明预言共用 `normalize`、不模拟 `MAX_OCCURRENCES` 与规则 b（G1 只是必要条件）；补同一关系两条态度标同一阶段的夹具（见 1）。
7. **挪动接口只按序号寻址**（spec 设计，§3.6）。首轮处置是请求带 `expected`（调用方看到的那一条）逐条比对；它挡不住两个请求同时在途的丢失更新（PATCH 同样）。Shiyu 定「一起做」后改为乐观锁（§13），`expected` 被版本核对取代、已删。
8. **（修复时自查）本分支让 `test_identify_failure_channels.py::TestOneParseableCardOnBothChannels::test_bg_task_accumulates_one_card` 变红**，首轮预跑与审计都没跑到这个文件。原因：夹具的草稿摘录「开头甲甲甲」不在正文里，按 §3.2 状态类字段（`decision_style`）进未定位区、顶层为空；该用例考的是「4 组都并进来」，不考位置检查。**处置**：本组两条用例的正文补上这句摘录（`BODY` 类常量，注释写明为什么），`_run_bg` 也传同一份正文。main 上照绿，本分支修后照绿。教训：受影响选集要按「会走到 `card_from_draft` 的入口」划，不只按 import 了哪些模块划 —— 审计后的选集加了 `Distiller`，46 个文件。
9. **（§13 自查）角色管理的编辑保存在 main 上必 405**：前端发 `PUT`、后端只有 `PATCH`，且 `card_json` 传的是字符串。改为走 `store.updateCard`（唯一 PATCH 出口，带 revision）。**测试**：`TextPanelEditSave.test.jsx`。**变异**：F12。
10. **（§13 自查）`store.updateCard` 失败时同一个错报两遍**（全局 error + 弹窗）。改为只抛给调用方。**测试**：`applyServerCard.test.js` 失败那条。
11. **（§13 自查）一次性变异脚本的锚点没有锁**：§13 改了 `updateCard` 的缩进，F3 的锚点就失配，脚本跑到那条时断言崩溃（响亮，不是静默）。`tests/test_mutation_anchors.py` 只扫 `tests/perf/` 的常设驱动，不扫 `docs/specs/artifacts/`；一次性脚本本来就随 spec 结束，只修了锚点，不扩锁。

审计第二轮（独立审计员，基线 `c97116b0`，分支 HEAD `a720626`，专挑 §13）：自补变异放宽 9 条（A1–A9）+ 过严 4 条（B1–B4），10 条符合预期、**3 条存活**（= 下面 3 条发现）。脚本 `docs/specs/artifacts/arc_phase_unlocated_audit_mutations.py`（与上一条同处置：一次性产物，不登记进 `tests/perf/` 的覆盖闭合元锁）。三条都是**性质无测试守**，不是代码错：现有实现是对的，缺的是「把对的钉住」的用例。每条：原因 → 复现 → 建议修法。

12. **409 之后编辑弹窗不关、用户改动还在 —— 没有测试守**（存活变异 A2）。§13.3 明写「409：`fetchWithTimeout` 抛错 → 弹窗内显示…弹窗不关、用户的修改还在」，实现也对：`EditCardModal` 的 `handleSave` 把 `await onSave(cardJson)` 包在 `try/catch` 里，只 `setSaveError`、不关弹窗（`EditCardModal.jsx:166-173`）。但把 `CharCard.jsx:730-731` 的 `handleSaveEdit` 改成 `try { await updateCard(...) } catch { /* 吞 */ }` 再照常 `setShowEditModal(false)`（= 409 时关弹窗、丢用户改动），**A2 不红**：`EditCardModalArcKeep.test.jsx`（只有一条成功路径）、`TextPanelEditSave.test.jsx`（只有一条成功路径）、`CharCardUnlocated.test.jsx`（只断言 `updateCard` 被调时的实参）都从不让 `onSave` 抛错。**复现**：`python docs/specs/artifacts/arc_phase_unlocated_audit_mutations.py` → `A2 … 实得=green MISS`（其余 12 条 RED）。**建议修法**：加一条用例 —— `onSave` 返回被拒的 Promise，断言弹窗仍在 DOM、`saveError` 上屏、输入框里用户改的内容没被重置。这是 §13.3 唯一既没被常设用例、也没被一次性变异覆盖的行为。

13. **`revision` 必须等于「存储原文的全量内容指纹」—— 没有测试守**（存活变异 A4）。§13.2 把版本定义成原文的 `content_fingerprint`（`card_out.py:37`）；乐观锁挡不挡得住静默覆盖，取决于它的碰撞面。但 `test_card_optimistic_lock.py:85` 那条**自指**：断言 `out_card(...)["revision"] == card_revision(a)`，两边同一个函数、同一份实现，函数内部怎么变都过得去（`b` 也经同一个函数，`!=` 那半也照样真）。把 `card_out.py:37` 改成 `content_fingerprint(...)[:8]`（256 位掉到 32 位，约 6.5 万张卡就有生日碰撞 → 旧 revision 偶然相等 → 静默覆盖放行），**A4 不红**；全仓 tests/ 里也没有第二处拿真值比过 `card_revision`（`test_s6_content_fingerprint_has_one_implementation` 只证明 `content_fingerprint` 定义在 `fingerprint.py`，不证明 `card_revision` 用了它的全量）。**复现**：同上 → `A4 … 实得=green MISS`。**建议修法**：给 `card_revision` 加一条 `assert card_revision(raw) == content_fingerprint(raw)`（引 `core.fingerprint` 的真值），把「必须全量」钉住。

14. **未定位区渲染门 `hasUnlocated` 只测了两端**（存活变异 B2）。`UnlocatedList.jsx:39-44` 的门同时数 behaviors、attitudes、overlay 三种叶子（少一类就整块不渲染）。`UnlocatedList.test.jsx` 只有「全空」（`unlocated: {}`，行 21）和「三类都有」（行 11-18）两个夹具，中间态没测。把门改成只认 behaviors（`return (u.behaviors?.length || 0) > 0`），**B2 不红**：没有夹具是「只有 overlay 或只有态度、没有做法」。**影响**：这种卡（做法都挪走了、只剩一条没定阶段的「语气」值）整块未定位区会消失，残留条目在 UI 里够不着 —— 违反 §1「没有证据的条目不进 prompt、也不丢」（数据还在，但用户挪不动，等于丢）。`CharCardUnlocated.test.jsx:41` 的夹具也带 behaviors、`e2e/arc-phase-unlocated.spec.js` 的夹具三类齐全，都盖不到。**复现**：同上 → `B2 … 实得=green MISS`。**建议修法**：`UnlocatedList.test.jsx` 加一条夹具 `unlocated: { behaviors: [], overlay: { speaking_style: { tone: ['冷'] } }, attitudes: [] }`，断言 `.card-unlocated` 仍在、条目数为 1。

**补充 12–14 的处置（2026-10-07）**：三条都只加用例，实现一行没改。审计脚本改了三处：A4 的靶子改指新用例（原靶子自指，正是补充 13）；加 A2b（A2 的另一个调用点：角色管理）与 B5（A2 的另一侧：成功也不关）。

| 发现 | 加的用例 | 打红的变异 |
|---|---|---|
| 12 | `CharCardUnlocated.test.jsx`：「编辑保存成功：弹窗关闭」「编辑保存失败（409）：错误抛回弹窗，弹窗不关」（假弹窗记下 `onSave` 的结局）；`TextPanelEditSave.test.jsx`：「保存失败（409）：弹窗不关，报错上屏，用户改的内容还在」「保存成功：弹窗关闭」（真 `EditCardModal`） | A2、A2b、B5 |
| 13 | `test_card_optimistic_lock.py::test_card_revision_is_the_full_fingerprint_of_the_stored_text`：拿 `card_revision` 之外的真值比（`content_fingerprint(raw)` 与位宽 64） | A4 |
| 14 | `UnlocatedList.test.jsx`：三类条目各只剩一类（做法 / 字段值 / 关系态度）时整块仍渲染、条目数为 1（`it.each` 三例） | B2 |

沙箱重跑（基线 `77b68fe`，一次性 PG 16 @55432）：审计脚本 `结论：15/15 条符合预期`，7 个目标文件还原后 sha256 逐字节一致；`test_card_optimistic_lock.py` 9 passed；前端全量 92 文件 479 passed（原 472，+7）；CI lint（`eslint.ci.config.js --quiet`）对改动的三个文件 0 error。§13.4 原写「9 条」，当时实为 8 条，加上补充 13 这一条后是 9 条。
