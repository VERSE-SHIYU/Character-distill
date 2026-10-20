# 单值字段每阶段一条（修复包 B1 / P4）

- 基线：main `5df7052c`（#191）。前置 spec：`distill-diagnostic-logs.md`（撞车日志）、`personality-inject.md`（R15 三档取代通用语气）、`state-baseline-zero.md`（底色标 0）。
- 跟踪：「祈灼卡修复 · 当前问题」P4 行；总文档 二表第 5 行。
- 实现 skill：`@tdd`（SPEC-STANDARD 第四节：一个阶段一个）。

## 0. 问题与根因（一句话）

单值字段（语气、句式、决策风格…）一个阶段只能放一句，但：
1. **提示词把它当列表写**：维度说明和模板都没说「每阶段一条」，模型就给同一阶段写两条（10-18 祈灼：tone / sentence_pattern / decision_style 阶段 1 各 2 条，第二条全是「对她时…」）；
2. **改挂撞车**：位置核对把别的阶段的取值挪进来，`_cap_slot` 按输出顺序留第一条，可能留下挪进来的、丢掉标对的；而关系态度遇到同样撞车是「原标此阶段优先」——同一条「一格留谁」规则写了两处、写法相反。

## 1. 目标检查（第一个提交，`tests/test_scalar_one_per_phase_goal.py`，全部 `xfail(strict=True)`）

| # | 检查 | 测的是目标 |
|---|---|---|
| G1 | 登记表里每个「按阶段 + 单值」路径（`kind == "scalar"` 且 `layer in ("state","experience")`，现为 8 个），在**它所在组**的 `format_prompt_after(组)` 里：维度说明带单值规则整句、模板里该字段示范两条且阶段号不同 | 模型被告知每阶段一条 |
| G2 | monkeypatch 登记表把 `values` 改成 scalar → `values` 的维度说明与模板随之变成单值写法；改回 list 不带单值规则（照 `test_evidence_format_goal.py::test_v8` 写法） | 规则由登记表决定，不手写 |
| G3 | 单值规则整句含「只写他这一阶段对一般人的样子」；G4 的三档说明含「做法与语气」 | 「对谁不同」有去处，不丢 |
| G4 | 改挂撞车：B 标阶段 1、摘录在阶段 2；A 标阶段 2、摘录在阶段 2 → 阶段 2 留 **A**；日志 `保留「A」（原标此阶段），丢弃「B」（由阶段 1 改挂进来）` | 标对的那条不被顶掉 |
| G5 | 两条都原标阶段 2 → 留先写的那条（与现行为一致，回归守卫） | 不误伤 |
| G6 | 结构锁：`card_draft.py` 里「一格留谁」只有一个函数；`_cap_slot` 与 `_convert_relationships` 都调它；不再有第二份判据（AST 查 `.phase == t` 只在该函数内） | 规则只写一处 |
| G7 | 结构锁：`_FORMAT_DIMS` / `_FORMAT_DIM_M` 里不直接拼 `_PHASE_DESCRIPTION_RULE` / `_PHASE_VERBATIM_RULE`，一律经 `_phase_rule("<路径>")`；每个路径都在登记表、且是按阶段的路径（照 `test_v9`） | 以后加一个单值字段只改登记表 |

**真模型效果不在本 PR 验**（规则 34）：B2 合并后的小重放（祈灼、谢凛羽各 2 次）里，日志不得出现「保留…（原标此阶段），丢弃…（原标此阶段）」——即来路 1 归零。写进 B2 spec 的通过条件。

## 2. 已查实的约束（main `5df7052c`，每条附这一轮读过的行）

| # | 事实 | 坐标 |
|---|---|---|
| F1 | 两条路径共用一份提示词拼装：一次读完 `distill_instruction` 用 `DISTILL_PROMPT_AFTER_NAME = format_prompt_after()`；分组路径用 `format_prompt_after(group)` | `core/distiller.py:428-450`、`:453`、`:477-485` |
| F2 | 草稿形态：列表与单值都是 `list[DraftTimed]`（`value` + `occurrences`），由登记表派生，**本 PR 不改形态** | `core/card_draft.py:108-116`、`:160-161` |
| F3 | 按阶段的单值字段 8 个：state 7 个 `decision_style`、`speaking_style.tone`、`speaking_style.sentence_pattern`、`cognitive.speech_style`、`psyche.relational_modes.close/normal/conflict`；experience 1 个 `cognitive.knowledge_scope` | `core/card_layers.py:69-75`、`:78` |
| F4 | 这 8 个的维度说明全部手工拼 `_PHASE_DESCRIPTION_RULE`（列表规则：「一条取值只写一次」，无「每阶段一条」） | 规则 `distiller.py:208-212`；拼接处 :236-237（C 语气/句式）、:262（K）、:278-279（N）、:298（M 三档） |
| F5 | 模板已按路径生成，只认逐字清单：`_timed_tpl(path)` | `distiller.py:225-228`；模板调用 :315-316、:338、:365-367、:377-378 |
| F6 | 组归属：decision_style ∈ G2，speaking_style / cognitive ∈ G3，psyche ∈ G4；分组路径下是独立调用 → **G3 的提示词不能指挥 G4 的字段**，规则只能各自定义 | `core/schema.py:334-346` |
| F7 | 单值撞车：`dispatch` 里 `kind == "scalar"` → `_cap_slot` 留 `slot[:1]`（输出顺序） | `card_draft.py:340-348` |
| F8 | 关系态度撞车：`next((j for j in cands if flat[j][1].phase == t), cands[0])` ——原标此阶段优先 | `card_draft.py:405-414` |
| F9 | 字段循环已有每条的原始标注（`rows[i].occurrences`）与位置结果 `res.tag_targets`；撞车日志 `describe` 用后者区分「原标 / 改挂」 | `card_draft.py:474-498` |
| F10 | 聊天时：tone / sentence_pattern 每轮原样注入；三档在 `relational_modes.complete` 时按好感档取一句**取代通用语气**，段头写「影响你的语气和态度」 | `core/context_engine.py:386-389`；`core/chat_engine.py:921-940`；档位 `core/affinity_rules.py:71-75`；设计出处 `docs/specs/personality-inject.md` R14/R15 |
| F11 | 现有测试锁着来路 2 的旧行为：g9 断言保留「由阶段 1 改挂进来」的 B | `tests/test_distill_diagnostic_logs_goal.py:312-327` |
| F12 | 规则常量各定义一次的锁 S4 | `tests/test_arc_phase_fields_locks.py:118-124` |
| F13 | z8 按「口癖按阶段给，」「用词水平是稳定项」切段核底色句 → 维度 C 这两处措辞保留 | `tests/test_state_baseline_zero_goal.py:151-175` |

**依据（出处表，规则 32）**

| 设计点 | 原文 |
|---|---|
| 「对谁不同」不混进单一语气字段 | Bell 1984, *Language Style as Audience Design*：「linguistic style-shifting occurs primarily in response to a speaker's audience」（[摘录](https://en.wikipedia.org/wiki/Audience_design)） |
| 同上 | Occhipinti, Guerini, Nissim, ACL 2025（[PDF](https://aclanthology.org/2025.acl-long.879.pdf)）：「highlighting the importance of who we are speaking with over what we are speaking about」 |
| 三档就是按对象取的语气 | 本仓库 R15（F10） |

## 3. 改动

### 3.1 提示词（`core/distiller.py`，一处定义、按登记表选用）

1. 新常量 `_PHASE_SCALAR_RULE`（定义一次，加入 S4 锁的名单）：
   > 取值按阶段给，写成 {"value": "结论（「原话」）", "occurrences": [{"phase": 1, "quote": "该阶段原文摘录"}]} —— **每个阶段只写一条：同一个阶段只能出现在一条取值的 occurrences 里**；此人对不同的人表现不同时，只写他这一阶段对一般人的样子，只对某个特定的人才有的表现不写在这里；＋ `_BASELINE_ZERO_RULE`
2. 新函数 `_phase_rule(path)`：逐字清单 → `_PHASE_VERBATIM_RULE`；`REGISTRY[path].kind == "scalar"` → `_PHASE_SCALAR_RULE`；否则 `_PHASE_DESCRIPTION_RULE`。与 `_timed_tpl` 同一判据来源。
3. `_FORMAT_DIMS` / `_FORMAT_DIM_M` 里所有按阶段的拼接改成 `_phase_rule("<路径>")`（列表字段的文字不变，只是改由函数给）。维度 C「语气/句式按阶段给，」「口癖按阶段给，」「用词水平是稳定项」措辞保留（F13）。
4. `_timed_tpl(path)` 改为返回**整个数组**：列表 → `[元素, ...]`（与现在逐字相同）；单值 → `[元素(phase 1), 元素(phase 2)]`。模板各处去掉外层方括号。
5. 维度 M 三档：「各一句具体做法」→「各一句具体做法与语气」（F10：它在聊天时取代的就是语气）。

### 3.2 撞车（`core/card_draft.py`）

1. 新函数 `slot_winner(cands, phase, tagged)`：`tagged(i)` 返回第 i 条**模型原标**的阶段集合；返回 cands 里第一个原标含 `phase` 的，没有则 `cands[0]`。顶层格（phase 0）同理：原标含 0 的优先。
2. `_cap_slot` 用它选赢家（落败者照旧丢 + WARNING，日志文本与 `describe` 不变）；`dispatch` 增加 `tagged` 参数透传。
3. 字段循环传 `tagged=lambda i: {o.phase for o in rows[i].occurrences}`；`_convert_relationships` 的 F8 那行改调 `slot_winner`，`tagged=lambda j: {flat[j][1].phase}`。
4. 「原标」以模型标注为准，不用 `tag_targets`：整卡跳过（D7）时 `tag_targets` 为空，用它会退化成「排前面」。

### 3.3 结构自检（规则 36）

| 三问 | 现在 | 本 PR 后 |
|---|---|---|
| 同一规则写几处 | 「一格留谁」2 处、写法相反（F7/F8）；单值用哪条规则靠每个维度手选（F4） | 各 1 处：`slot_winner`；`_phase_rule` |
| 调用方是否被迫知道不该知道的 | 维度说明要知道哪个字段是单值 | 只给路径，登记表判断 |
| 加一个单值字段改几处 | 登记表 + 维度说明 + 模板 3 处 | 登记表 1 处（G2/G7 守） |

## 4. 不做（目标外，已记跟踪文档，Tracy 定）

- 撞车落败者去向不一致：态度落败进未定位区（`card_draft.py:416-421`），单值落败直接丢（:344-348）。本 PR 只统一「谁赢」。
- 阶段数（4 段 vs 2 段）、P3 标注堆在一个阶段：B2。

## 5. 对账表（复核方必验的清单；变异只由独立复核方跑并自补，返工经验 #14）

| 变异 | 方向 | 应红 |
|---|---|---|
| `_phase_rule` 对 scalar 仍返回描述规则 | 放宽 | G1、G2 |
| 单值规则删掉「对一般人的样子」那句 | 放宽 | G3 |
| 三档说明改回「各一句具体做法」 | 放宽 | G3 |
| `slot_winner` 恒取 `cands[0]` | 放宽 | G4、g9（翻转后） |
| `slot_winner` 优先「改挂进来的」 | 过严/反向 | G4、G5 |
| `_convert_relationships` 恢复内联判据 | 结构 | G6 |
| 某个维度说明直接拼 `_PHASE_DESCRIPTION_RULE` | 结构 | G7 |
| `_timed_tpl` 单值示范两条同一阶段 | 放宽 | G1 |

## 6. 测试

只跑受影响文件，库用 `docker compose -f docker-compose.test.yml up -d --wait`（55432）：
`tests/test_scalar_one_per_phase_goal.py tests/test_distill_diagnostic_logs_goal.py tests/test_arc_phase_fields_locks.py tests/test_evidence_format_goal.py tests/test_personality_distill_goal.py tests/test_state_baseline_zero_goal.py tests/test_state_inertia_goal.py tests/test_arc_phase_unlocated.py tests/test_arc_phase_unlocated_goal.py tests/test_card_arc_behaviors.py tests/test_card_draft.py tests/test_distill_output_bounds.py tests/test_pre_story_memory_goal.py`。全量看分支 CI。

**改写的旧断言（属旧规则本身，复核方逐条核）**：g9 第一段 `overlay["decision_style"] == "B"` → `"A"`，日志改为 `保留「A」（原标此阶段），丢弃「B」（由阶段 1 改挂进来）`。其余断言不许改；要改就停下报告。

## 7. 未核实

- 新规则对真模型是否足够（模型会不会仍写两条）：只有 B2 后的小重放能答，见第 1 节末。

## 补充（复核，合并后）

复核对象：`98a8d363`（测试）→ `7cd4540a`（提示词）→ `1e07845c`（撞车），基线 `5df7052c`；合并 `0c9467ca`（PR #193）。复核方未改实现。

### 结论

**通过**（实现行为符合本 spec 的目标）：四次目标检查分别为 `1 passed, 6 xfailed`、`5 passed, 2 xfailed`、`7 passed`、`7 passed`；关系态度回归 `78 passed`；第 6 节受影响文件 `220 passed`。未发现需要回滚或立即修改的实现缺陷；以下是需跟进项。

### 偏离判定

1. **G5 未标 xfail：spec 错，实现对。** 第 1 节标题写「全部 `xfail(strict=True)`」，同节 G5 又写「与现行为一致，回归守卫」。已绿用例标 `xfail(strict=True)` 会 XPASS 判失败，两者不能同时成立。实现按 G5 的描述办，正确。
2. **`_cap_slot` 空槽提前返回：实现必需，不是行为偏离。** 旧 `_cap_slot([])` 走 `slot[:1]` 返回 `[]`；新 `slot_winner` 会取 `cands[0]`，而 `dispatch` 对顶层格无条件调用 `_cap_slot`（顶层为空是常见情形），没有该守卫会 `IndexError`。空槽行为与旧版一致。`dispatch` 里 `if slot else slot` 因此成为多余但无害的一层，可删。

### 变异（只跑对应测试文件；每条跑完 `git checkout --` 复原）

| 变异 | 方向 | 结果 |
|---|---|---|
| M1 `_phase_rule` 对 scalar 返回描述规则 | 放宽 | G1、G2、G3 红 |
| M2 单值规则删「对一般人的样子」 | 放宽 | G1、G3 红 |
| M3 三档改回「各一句具体做法」 | 放宽 | G3 红 |
| M4 `slot_winner` 恒取 `cands[0]` | 放宽 | G4 红；g9 红 |
| M5 `slot_winner` 优先改挂进来的（回落末条） | 过严/反向 | G4、G5 红 |
| M6 `_convert_relationships` 恢复内联判据 | 结构 | G6 红 |
| M7 维度说明直接拼 `_PHASE_DESCRIPTION_RULE` | 结构 | G7 红 |
| M8 `_timed_tpl` 单值两条同阶段 | 放宽 | G1、G2 红 |
| M9 字段循环忘传 `tagged`（用默认空集） | 放宽 | G4 红 |
| M10 用位置核对结果当原标 | 放宽 | G4 红 |
| M11 三档只把 `close` 当单值 | 放宽 | G1、G2 红 |
| M12 顶层格不做「原标 0 优先」 | 放宽 | **全绿（存活）→ F1** |
| M13 `_phase_rule` 先判 scalar 后判逐字清单 | 结构 | 无红（当前无 scalar∩verbatim，判序不可观测） |

### 需跟进

- **F1（目标检查缺口）**：3.2.1 要求顶层格「原标含 0 的优先」，G1–G7 未覆盖。`dispatch([[1,2],[]], 2, kind="scalar", zero_only=[1], tagged=…)` 在干净代码上留原标 0 的行，M12 后留第一条；两种结果都全绿。建议补 G8。
- **F2（入库台账失效）**：`docs/specs/artifacts/arc_phase_unlocated_mutations.py` X20 与 `arc_phase_fields_mutations.py` MB2/MB3 的锚点仍指向本次被替换的旧文本（当前命中 0 次），`mutation_framework` 的「锚点恰一命中」会当场 assert。根因：重构改了锚点文本，未同步。建议把 X20 改锚 `slot_winner` 的返回行，MB2/MB3 改锚新的 `_cap_slot(...)` 调用行。
- **F3（静默兜底）**：`dispatch(tagged=...)` 默认 `lambda i: set()` 会让新单值调用方忘传时静默退回「排前面优先」（M9 只剩 G4 能抓）。建议改成 `None` + `kind == "scalar"` 时硬失败，或把单值分发拆成必传 `tagged` 的入口。
- **F4**：第 1 节「全部 xfail」对 G5 不成立（见偏离 1），标题应改为「G1–G4、G6、G7 xfail；G5 为已绿回归守卫」。
- **F5**：3.1 第 4 条「列表 → `[元素, ...]`（与现在逐字相同）」对口癖不成立：旧模板是 `[元素]`，改后多出 `, ...`。口癖本身是列表，提示词变化可接受；建议订正 spec 措辞，或若要逐字保留则给口癖单独分支。
- **F6**：第 6 节漏了 `tests/test_arc_phase_fields_unit.py`（直接调用 `dispatch(kind="scalar")`）。复核时补跑：`62 passed`。

### 其他

- 「一格留谁」生产代码只在 `slot_winner` 一处（`grep -rn "cands\[0\]\|phase in tagged\|phase == t" --include=*.py core web` 仅命中该函数）；两份一次性变异脚本仍有旧文本锚点，见 F2。
- g9 只改了第一段的两行（断言值与日志文本），其余断言未动。
- 关系态度改调 `slot_winner` 后：`tests/test_arc_phase_unlocated.py tests/test_arc_phase_unlocated_goal.py` → `78 passed`。
- 口癖 `catchphrases` 模板 diff 是唯一 spec 未要求的提示词变化（F5）。
