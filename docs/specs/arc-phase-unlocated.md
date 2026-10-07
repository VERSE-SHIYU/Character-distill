# spec：阶段未验证的处理（①补完 B）+ overlay 与卡片同形

基线 main `c97116b0`（2026-10-07 核） · 参考实现 `docs/specs/artifacts/arc-phase-unlocated-proto.patch`（sha256 `4bf846dc2504bace1dacdc92043e3f6b33fa6b343dafe06ad61482e64fa3e9ea`，在 `c97116b0` 上 `git apply --check` 通过）
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
| 无阶段的卡（`count == 0`） | 经历类进顶层；状态类进未定位区 | — |
| 整卡跳过 | 保留模型标注（现状） | 代码事实：这类卡只按最后阶段聊 |

### 3.3 关系态度（`card_draft._convert_relationships`）

- 走同一个 `verify`，按 `Verification.tag_targets` **逐条**分发（每个阶段的态度文字不同，不能整条挪）。
- 撞车（两条态度落到同一阶段）：原本就标在这个阶段的优先，其余按标注顺序取第一条；**输掉且在别处没赢**的进未定位区，`note` 跟着走，带上原来标的阶段 `phase`（挪回时预选用）。在别处赢了的不进。
- 一条态度都不剩的关系 → 挂最后阶段、态度留空（不编造）。态度留空时：`context_engine.py` 卡片扩展层那一行不带冒号；`chat_engine.py` 那一行不带逗号；群聊回落「普通群聊关系」（`group_session.py:298`，已核）。
- 顶层 `attitude` 取挂上的最后一个阶段的态度；无阶段的卡取最后一条态度。

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

- 规则只在后端纯函数 `core/unlocated.py::move_unlocated(card, *, section, index, phase, path="")`：「列表还是单值」只认登记表 `kind`；列表追加；**单值交换**（原值退回未定位区，D4）；态度挪入先去掉「态度留空」占位（不然投影取 ≤k 最新一条会被空态度盖住），阶段 k 已有态度也交换。参数不合法抛 `ValueError`。
- 接口 `POST /api/distill/card/{card_id}/unlocated/move`，body `{section, index, phase, path}`：复用 `get_card_owned` 归属鉴权（非属主与不存在同判 404）；`ValueError` → 400「这一条已经不在未定位区，请刷新后重试」；**返回值走 `out_card`**。演示账号门禁自动拦住这个 POST（`test_demo_gate.py` 已跑过，绿）。
- 前端：按钮放卡片详情（`CharCard.jsx` 的 `CardDetail`）的「未定位的条目」一节，**只在 `useCanWrite()` 为真时渲染**；市场卡详情（`MarketCardDetail`）不渲染。不放编辑弹窗（弹窗有未保存的本地改动，会互相覆盖）。阶段选择用原生 `<select>`。默认选中：态度用它原来标的阶段（在 1..n 内），其余用最后阶段。
- **触发链**：选阶段（`<select>` onChange → `MoveControl` 本地 state）→ 点「挪入」→ `MoveControl.run` → `onMove(phase)` → `CardDetail.handleMoveUnlocated(section, index, phase, path)` → `store.moveUnlocated(cardId, {...})` → POST → 成功：`store._applyServerCard(cardId, data.card)` 写 `cards` 与 `currentCard` → `CardDetail` 重渲染：`parseCardJson` → `ArcList` 与 `UnlocatedList` 都从新卡重算；失败：抛错 → `setError` 上屏，本地卡不动。

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
| **3** 规则 | 位置规则 + 未定位区 + 关系逐条 + 监测 + 驱动修复 + 锚点锁 | `core/phase_anchoring.py`；`core/card_draft.py`（`dispatch` 三元组、`_convert_relationships`、`card_from_draft`、监测行）；`core/schema.py`（`UnlocatedAttitude`、`UnlocatedItems`、`CharacterArc.unlocated`）；`core/card_layers.py`（3 条登记 + `map`）；`core/card_quotes.py`；`core/arc_view.py`（投影清空未定位区）；`core/context_engine.py`、`core/chat_engine.py`（空态度）；`tests/perf/arc_phase_anchoring_mutations.py` + `card_draft_mutations.py` + 两个 `*_red_lines.json`（重跑生成）；`tests/test_mutation_anchors.py` | `test_arc_phase_unlocated_goal.py`；`test_arc_phase_unlocated.py` 其余各组（N4b、U、R、P、Q）；`test_phase_anchoring.py`（U7、U13–U15 改写，新增 U13b–d，U19、U20、U22 改写）；`test_arc_phase_fields_unit.py`（总数 40、`dispatch` 三元组）；`test_card_arc_behaviors.py`；两个常设驱动；元锁；锚点锁 |
| **4** 功能 | 挪进阶段 | `core/unlocated.py`；`distill.py` 挪动接口；`useAppStore.js` 的 `moveUnlocated`；`UnlocatedList.jsx`；`CharCard.jsx`；`global.css`；S15 白名单加 `move_unlocated_item` | `test_arc_phase_unlocated_move.py`；`UnlocatedList.test.jsx`、`CharCardUnlocated.test.jsx`、`moveUnlocated.test.js`；`e2e/arc-phase-unlocated.spec.js` |

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
| 3 | X5 不拒带点键名、X6 未定位区不走同一校验器 → N4b；X7d 根因复发 → 目标 G4；X9 恢复兜底 → 目标 G1；X10 未定位做法被丢 → 目标 G2；X11/X11b 未定位区进投影 → 目标 G3 / P1；X12 不改挂 → 目标 G0；X13 状态类被丢；X14 经历类被丢；X15 经历类挂阶段 1（泄露）；X16 经历类挂全部落点；X17 未定位单值存成单值；X18 整卡跳过也进未定位区（过严）；X19 分类计数漏关系；X20 撞车原标注不优先；X21 输掉的态度被丢；X22 在别处赢的也进未定位区；X23 无态度关系挂阶段 1；X24 无态度关系沿用模型态度；X25 空态度带冒号；X26 无阶段卡取第一条态度；X27/X27b 未定位做法不进核对；X28 未定位 overlay 不进核对；X29 未定位态度不进核对；X30 审核遍历跳过未定位区 |
| 4 | X31 单值不交换；X32 态度不去占位；X33 接口不走 `out_card`；F4 打错接口；F5 失败也写回；F6 态度默认阶段不用原标注；F7 传错路径；F8 只读账号也显示；F9 挪入没接 store |

**预跑结果**：`结论：47/47 条全红`，16 个目标文件还原后 sha256 逐字节一致。第一轮 X7b/X7c 没红：变异打在写侧，而对应测试测读侧 —— 改成读侧变异后红；写侧复发由 X7、X7d 管。

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
| 阶段数 | 样本 2–4 | `<select>` 全列 |
| 带点的 state/experience 路径 | 7（登记表扫描） | N1、G1、G2 逐个参数化 |
| 登记表叶子 | 37 → 40 | U1 断言 |

## §10 调用点矩阵（行 = 调用点，列 = 可观测输出 → 测试）

| 调用点 | 落卡阶段 / 未定位 | 监测行 | prompt | 守卫/核对 | 前端 |
|---|---|---|---|---|---|
| `card_from_draft` 做法 | U1、U13、G0–G2 | U8、U22 | P2、G3 | Q1、Q2 | — |
| 通用字段（含带点） | U2、U3、N6 | U8 | P2 | G1–G3、Q3、Q6 | ArcListNested |
| 记忆 | U4、U9 | U8 | P2（正控：挂最后的只在阶段 3） | — | — |
| 关系态度 | R1–R4、R6 | U8 | R5 | Q4 | — |
| 整卡跳过 | U7 | 原有 | — | — | — |
| PATCH | — | — | — | — | applyServerCard、S15 |
| 挪动接口 | M1–M8、route ×3 | — | M5、M8 | — | UnlocatedList、CharCardUnlocated、moveUnlocated、e2e |

---

## §11 测试（每段固定写法）

- 本地只跑本段受影响的文件 + `npm test`；PG 用 docker 测试库（§2.5）。
- 本段变异：`python docs/specs/artifacts/arc_phase_unlocated_mutations.py <段号>`；段 3 另跑两个常设驱动与元锁、锚点锁。
- Playwright：`cd web/frontend && npx playwright test e2e/arc-phase-unlocated.spec.js`（段 4）/ `arc-phase-select.spec.js`（段 2）。截图 `e2e/arc-phase-unlocated-before.png` / `-after.png` 给 Shiyu 看排版。沙箱预跑：新 spec `--repeat-each=10` 30/30；`arc-phase-select` + `arc-phase-fields` `--repeat-each=3` 21/21。
- 合并门是分支 CI；合并只做 `gh pr create` → `gh pr merge --merge`。报告里不写本地全量数字。

沙箱预跑（全部段合起来）：后端受影响选集 908 passed / 1 failed（即 §2.5 main 原有的那条）；前端 91 文件 468 passed；CI lint 0 error（10 个 warning 全在本 spec 没碰的文件，main 原样）。

## §12 要 Shiyu 定的（不在本 spec 范围内）

1. **出卡全面接入**：其余 14 个返回卡片的接口是否也一律走 `out_card`、S15 改成「凡返回卡片」的机械判定（§6）。
2. **main 原有的失败用例** `test_relationship_batch_splits_and_merges`：断言并发调用的开始顺序，修法是只断言批大小的多重集与输出顺序（输出顺序才是被保证的性质）。是否另开一个小 PR。

## 自检表

| 项 | 结果 |
|---|---|
| 每条代码判断附了读过的行或原始输出 | §2 全部带坐标 / 输出 |
| 与自身规则冲突 | 无：同形与未定位区共用一个校验器；分发、出卡、位置判定各只一处 |
| 对照调研结论 | 「未验证不注入」= 阶段未验证的状态类不进 prompt；经历类挂最后不泄露 |
| 每条边界两侧都测 | 放宽 / 过严成对：X1/X2、X13/X15、X21/X22、X18、U13b/U13c |
| 拒侧用例另有能通过的标注 | `test_phase_anchoring.py` 约定沿用（约定 1 的说明已改为「否则整条进未定位区」） |
| 变异预跑无存活 | 47/47、42/42、38/38 |
| 目标检查独立于被测代码 | G1 用 `str.find` 预言，不调 `phase_at` / `verify` |

## 补充（审计发现写这里）

（空）
