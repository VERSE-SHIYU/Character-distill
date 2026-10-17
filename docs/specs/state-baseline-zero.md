# spec：状态类字段「一直成立」标 0，进全程基线（E1 第 1 段）

基线：main `6a55b8d6`。放进 worktree 的 `docs/specs/`，用 `Test-Path` 确认；补充写回本文件。
依据：《阶段锚点核对失效：调查报告》E1 行、「E1 再调研」与「E1 定案」两节；Shiyu 10-17 同意分段方案。

## 解决什么、不解决什么

| 解决 | 不解决 |
|---|---|
| 状态类字段（性格、价值观、动机、说话方式、雷点软肋、三档、亲近条件、决策风格……）里「一直成立」的那条，模型标 0 → 进卡片顶层（全程），**选哪个阶段都看得到**，不再因为阶段 1 太短找不到摘录而被挪走或去掉 | 情境→做法、关系态度：它们标 0 照旧撤回（第 37 条：不在目标内；做法「阶段下的只在那个阶段成立」是 ② 定下的语义，`core/schema.py:144`） |
| 提示词：状态类写法改成「一直如此写 0；因某人某事才有的，标它成立的阶段」 | 后来才有的条目怎么标阶段：**照旧**「标出它确实成立的阶段、每阶段一段该阶段的摘录」，不改成「只标开始那一阶段」。原因见判断 J7 |
| 每条按 0 进顶层的条目打一行 info，方便第 2 段人工逐条核对有没有误标 | 已有旧卡：要重蒸一次（第 2 段，约 ¥2.5） |

一个 PR。

## 根因（已查实）

`core/distiller.py:198-203` 要求状态类字段「每个阶段各给一段**该阶段里的**原文摘录……从头到尾都成立时标全部阶段」。祈灼阶段 1 只占全书 2.2%（10-14 日志），一直成立的底色几乎找不到落在阶段 1 的原文，于是：
- 摘录落在后面的阶段，被改挂过去（8 条）；
- 摘录定不到位，标注被去掉（4 条：性格、语气、亲近条件、决策风格）。

结果凑不齐「全部阶段」，进不了顶层（`dispatch` 的顶层判据 `len(final) == count`，`core/card_draft.py:324`）。

## 调用方清单（第 11 条，全仓搜索原始输出）

```
$ grep -rn "_valid_number_rows\|pre_story\|ValidRows" core tests scripts mcp_server web --include=*.py
core/card_draft.py:232:class ValidRows(NamedTuple):
core/card_draft.py:239:def _valid_number_rows(items, count: int, kind: str, *, layer: str = "state") -> ValidRows:
core/card_draft.py:365:        items, _valid_number_rows(items, count, "态度").valid, anchors,
core/card_draft.py:431:        _valid_number_rows(draft.situation_behaviors, count, "做法").valid,
core/card_draft.py:433:    m_rows = _valid_number_rows(draft.key_memories, count, "记忆", layer="experience")
core/card_draft.py:450:        vr = _valid_number_rows(rows, count, path, layer=spec.layer)
（另有 card_draft.py 内 docstring 与 dispatch 的 pre_story 参数；tests/scripts/mcp_server/web 无直接调用，
 tests 里只有 test_pre_story_memory_goal.py、test_phase_tagged_goal.py 的 docstring 提到规则 0）
```

```
$ grep -rln "card_from_draft\|_FORMAT_IMPORTANCE\|_FORMAT_DIMS\|format_prompt_after" tests scripts mcp_server web/routers core --include=*.py
core/card_draft.py  core/distiller.py  core/phase_anchoring.py  core/schema.py  web/routers/distill.py
tests/perf/arc_phase_anchoring_mutations.py  tests/perf/card_draft_mutations.py
tests/test_arc_phase_fields_entries.py  tests/test_arc_phase_fields_locks.py  tests/test_arc_phase_fields_unit.py
tests/test_arc_phase_select.py  tests/test_arc_phase_unlocated.py  tests/test_arc_phase_unlocated_goal.py
tests/test_card_arc_behaviors.py  tests/test_card_draft.py  tests/test_distill_draft_persist.py
tests/test_distill_output_bounds.py  tests/test_evidence_format_goal.py  tests/test_personality_distill_goal.py
tests/test_personality_motives_goal.py  tests/test_phase_anchoring.py  tests/test_phase_tagged_goal.py
tests/test_pre_story_memory_goal.py  tests/test_state_inertia_goal.py
```

```
$ grep -rn "全阶段都成立\|标全部阶段\|确实成立的阶段\|从头到尾都成立时" tests scripts docs mcp_server --include=*.py
（无结果：没有测试钉住要改的这几句提示词）
```

## 判断清单（基线 `6a55b8d6`）

| # | 判断 | 坐标与原文 |
|---|---|---|
| J1 | 规则 0 只此一处，经历类已认 0 = 故事开始前 → 顶层、不做位置核对（E3，#168） | `core/card_draft.py:253` `pre = (layer == "experience" and count and 0 in nums`；`:255` `if layer == "experience" and count:`；`:263` `logger.info("[card_draft] 故事开始前：%s", …)` |
| J2 | 状态类字段走同一个调用点，`layer` 取登记表 | `core/card_draft.py:450` `vr = _valid_number_rows(rows, count, path, layer=spec.layer)`；`:456` `dispatch(..., pre_story=vr.pre_story)` |
| J3 | 做法、态度不传 `layer`，靠默认值 `"state"` —— 与状态类字段**分不开** | `:239` `layer: str = "state"`；`:365`（态度）、`:431`（做法） |
| J4 | 登记表里做法、关系的类别是 `custom` | `core/card_layers.py:82` `"relationships": FieldSpec("custom", …)`；`:84` `"situation_behaviors": FieldSpec("custom", …)` |
| J5 | 现在状态类标 0 → 编号非法，整条撤回 + warning | `:258-260`（`not valid and not pre` → warning）；`core/phase_anchoring.py:191` `if not valid:  # 规则 0 已整条撤回` |
| J6 | 顶层的状态类条目在**每个阶段**的投影里都有（列表 = 阶段格 + 顶层；单值 = 阶段格有值用之，其次顶层） | `core/arc_view.py:218-227`；`core/card_layers.py:7-9` |
| J7 | 阶段格**整格替换**：阶段 k 自己有值就只用自己的，不并早期阶段 | `docs/specs/state-inertia.md` §2 规则 1「整格替换，列表也不与早期合并」。所以后来才有的条目若只标开始阶段，到下一个有自己值的阶段就没了 → 后来条目的标法不能改 |
| J8 | 要改的提示词：描述类、原话类、重要事项各一句 | `core/distiller.py:202` `'标出它确实成立的阶段；从头到尾都成立时标全部阶段。'`（`_PHASE_DESCRIPTION_RULE`，锁 S4）；`:208` `'一条取值只写一次，标出它确实成立的阶段。'`（`_PHASE_VERBATIM_RULE`，锁 S4）；`:378` `…（1 起），全阶段都成立时把每个阶段都标上'` |
| J9 | 知识范围（经历类）也引用描述类规则；经历类 0 本来就进顶层，口径一致 | `core/distiller.py:269`；`core/card_layers.py:78` `"cognitive.knowledge_scope": FieldSpec("experience", …)` |
| J10 | 变异锚点钉着规则 0 的几行，改的时候这几行**一字不动** | `tests/perf/arc_phase_anchoring_mutations.py:117`（`nums = …`）、`:143`；`tests/perf/card_draft_mutations.py:82`、`:88`、`:94`（warning 文本、warning 条件、`valid = …`） |
| J11 | 缺 `phase` 键按 0 处理 | `core/card_draft.py:78-92` `PhaseTagged`（`phase: PhaseNumber = 0`，warning「缺阶段编号」） |

## 方案取舍（技术题，我定；对照返工经验第 25、26、29 条）

| 方案 | 取舍 |
|---|---|
| A. 状态类字段跳过位置检查、信模型标注（10-15 旧方案） | 不取：5 条与云绮有关的（如「小乖」）会留在阶段 1 |
| B. 新加一种「全程」标记（新字段 / 新值） | 不取：发给模型的结构要变，还要另写一套规则（第 25 条） |
| C. **复用规则 0 的 0：状态类与经历类一样，只标 0 → 顶层**（选这个） | 一处规则、不改结构；经历类的 0 已经是「进顶层」，状态类只是同一条规则扩一类 |

依据：PHASE-Tree（arXiv 2608.06975，§3.4）和 STAGE（arXiv 2601.08510v9，§3.1.2）都是「基线 + 有证据的变化」；arXiv 2610.17402 测的 DeepSeek-V4.1-Flash 判先后约 0.91，回忆段落约 0.74。

## 目标检查（第 1 个提交就带上，新文件 `tests/test_state_baseline_zero_goal.py`）

走真实代码 `card_from_draft` → `project_card`。预言用本文件的字面量。3 阶段卡，阶段 1 很短（照 10-14 的形态）。

| # | 情形 | 期望 | main 上 |
|---|---|---|---|
| Z1 | 性格只标 0，摘录落在阶段 3 | 进顶层 `personality_traits`；不在任何阶段格；`project_card(card, 1)` 看得到 | 红 |
| Z2 | 同 Z1，摘录**查不到**（10-14「被去掉」的形态） | 同 Z1（0 不做位置核对） | 红 |
| Z3 | 只标 0 → 不打「阶段编号不合法」warning；打一行 info「全程：<取值>」 | — | 红 |
| Z4 | 后来才有的「唤她小乖」标 2、3，摘录各在阶段 2、3 | 阶段 2、3 有，`project_card(card, 1)` **没有**（回归守卫） | 绿 |
| Z5 | 单值语气：「冷淡」标 0，「温软」标 3 | 阶段 1、2 投影 = 冷淡，阶段 3 = 温软 | 红 |
| Z6 | 口癖（原话类）只标 0 | 进顶层 | 红 |
| Z7 | 状态类标 0 + 2，摘录在阶段 2 | 忽略 0，挂阶段 2，不进顶层，不 warning（与经历类 P4 同） | 红 |
| Z8 | 提示词：`format_prompt_after("G2")`、`("G3")` 里有 `_BASELINE_ZERO_RULE` 的**整句**（期望句写成本文件的字面量，不从被测常量取；删掉任一个词即红，第 33 条），口癖那段也带它；没有「从头到尾都成立时标全部阶段」「全阶段都成立时把每个阶段都标上」 | — | 红 |

现有的 `test_pre_story_memory_goal.py` P5（做法标 0 撤回）、P6（态度标 0 撤回）、P3（经历类 info 写「故事开始前」）、P7（维度 E 与 G6 那两句）必须**原样绿**，不改。

## 改法

1. **规则 0**（`core/card_draft.py`，只改这一处）：
   - 「0 进顶层」的类别从只有 `experience` 扩成 `state` 与 `experience`，用一张模块级小表写明每类 0 的含义：`{"experience": "故事开始前", "state": "全程"}`。`:253`、`:255` 两处的判断改成查这张表；`:263` 的 info 按类别取这张表里的词（经历类仍写「故事开始前」，P3 不变）。
   - `layer` 改成**必填关键字参数**（去掉默认 `"state"`，J3）。做法、态度两个调用点改传登记表里的类别：`REGISTRY["situation_behaviors"].layer`、`REGISTRY["relationships"].layer`（都是 `custom`，J4），所以它们标 0 照旧撤回。不写字段名分支。
   - `ValidRows.pre_story` 与 `dispatch(..., pre_story=)` 改名为 `zero_only`（含义已不只是「故事开始前」）；`dispatch` docstring 里「`state` 类不会产出它」改掉。J10 列的锚点行一字不动（变量名 `pre` 保留）。
   - 同步改 docstring：`:50`、`:81`、`:108`、`_valid_number_rows`、`dispatch` 里写「经历类除外」的地方，改成「状态类、经历类除外」并写明各自含义。
2. **提示词**（`core/distiller.py`）：「一直如此填 0」这条规则**只写一处**——新常量 `_BASELINE_ZERO_RULE`，三个地方都引用它，不各写一份措辞：
   ```python
   _BASELINE_ZERO_RULE = (
       '从头到尾一直如此的（此人的底色），只写一条，phase 填 0；'
       '故事里因某人某事才出现或改变的（如对某人的称呼），不填 0，标出它确实成立的阶段。'
   )
   ```
   - `_PHASE_DESCRIPTION_RULE`（`:201-202`）：「一条取值只写一次，标出它确实成立的阶段；从头到尾都成立时标全部阶段。」→ `'一条取值只写一次；' + _BASELINE_ZERO_RULE`。
   - `_PHASE_VERBATIM_RULE`（`:208`）：「一条取值只写一次，标出它确实成立的阶段。」→ `'一条取值只写一次；' + _BASELINE_ZERO_RULE`。
   - 重要事项（`:378`）：「（1 起），全阶段都成立时把每个阶段都标上」→ `'（1 起）；' + _BASELINE_ZERO_RULE`。
   - 锁 S4（`tests/test_arc_phase_fields_locks.py:121`）的常量名单加上 `_BASELINE_ZERO_RULE`。
   - 维度 E（`:238`）、G6（`:382`）、无阶段说明（`:489`）不改（经历类「故事开始前」是另一层意思，E3 已定）。
   - 措辞是自拟的：文献只支持「基线 + 变化」的结构（见「方案取舍」），没有现成的提示词原文可照抄（第 32 条：如实标出）。措辞对不对，靠下面「真模型检查」验。
3. 不改 `phase_anchoring`、`arc_view`、登记表、草稿结构、`relationship_batch`。

## 结构自检

按第 36 条三问，附代码坐标（基线 `6a55b8d6`）：

| 问题 | 改前 | 改后 |
|---|---|---|
| 同一条规则写了几处 | 「0 怎么处理」在代码里一处（`card_draft.py:253-263`）；提示词里「全程怎么标」写了两种说法（`distiller.py:202`「标全部阶段」、`:378`「把每个阶段都标上」），原话类（`:208`）没写 | 代码一处（规则 0 的类别表）；提示词一处（`_BASELINE_ZERO_RULE`，三处引用） |
| 调用方是否被迫知道不该知道的事 | 做法、态度靠默认值 `"state"` 混进状态类（`:239`、`:365`、`:431`），调用方不知道自己被当成状态类 | 四个调用点都显式传登记表的类别（`card_layers.py:82`、`:84`、`:450` 的 `spec.layer`），规则 0 不认识任何字段名 |
| 加一种情况要改几处 | — | 加一个状态类字段：0 处（`card_draft.py:157` `_GENERIC_PATHS` 按登记表自动收）；加一种认 0 的类别：类别表一行 |
| 发给模型的结构变了吗 | — | 没变（`draft_schema` 不变，`test_phase_tagged_goal.py` K5 守着） |

## 已知残余风险（写进 PR 描述）

1. 模型把后来才有的东西标成 0 → 阶段 1 就能看到（泄露）。拦法：提示词点名「因某人某事才有的不填 0」+ 每条 0 打 info；第 2 段逐条核。口癖最容易出这种错（「小乖」），第 2 段重点看。
2. 缺 `phase` 键按 0 处理（J11）：状态类缺编号的条目从「撤回」变成「进顶层」。与 E3 后的经历类同口径；「缺阶段编号」warning 照旧打，第 2 段日志里看有没有。
3. 10-14 被挪的 8 条里，若有属于「情境→做法」的，本 PR 不管（只改状态类字段）；第 2 段日志能看出来。

## 测试

- 本地只跑：新增目标检查，加上面第二段 grep 搜出来的全部测试文件（`tests/perf/` 两份驱动由 `tests/test_mutation_anchors.py` 覆盖，必须跑它）。库用 docker PG，合并门是分支 CI。前端没改，不跑 `npm test`。
- 影响面参考（Claude 在沙箱照「改法」做了原型，含 `_BASELINE_ZERO_RULE`，只作影响面的事实，不保证正确）：上面这些文件 364 passed（含 `test_mutation_anchors.py`、`test_distill_draft_persist.py`），原型目标检查在 main 上该红的红、改后全绿。实现后如果红了别的，就停下报告，不要顺手改测试。

## 真模型检查（第 34 条；花钱，Shiyu 定做不做）

代码部分替身能验，**模型会不会照新提示词标 0 替身验不了**，这是本段最大的不确定。线上还没有一次模型标 0 的数据（E3 上线后没蒸过）。

| 做法 | 花费 | 能看出什么 | 看不出什么 |
|---|---|---|---|
| a. 直接第 2 段：蒸祈灼一次 | 约 ¥2.5（10-14 实耗） | 全部 | — 一次不过就白花 |
| b. 先用一本公版短篇试蒸两次（第 34 条：连跑两次都过），再做 a | 每次估几角钱（**未核实**，按字数比例推算） | 一直成立的标没标 0、后来才有的有没有误标 0 | 阶段 1 极短（2.2%）这种长书形态 |

现有通道没有「只重跑最后一步」的便宜办法：已完成的任务不复用分片（`web/routers/distill.py:842-843`「done 也不复用」）。
- **CI 绿了才报「可合并」**。

## 对账表（独立复核方验，自补放宽、过严两个方向的变异）

| 行为 | 守它的测试 | 让它变红的变异 | 触发状态 |
|---|---|---|---|
| 状态类只标 0 → 顶层 | Z1、Z2、Z6 | 类别表去掉 `state` | 状态类标 0 |
| 0 不做位置核对 | Z2 | 0 的条目也走位置检查 | 摘录查不到 |
| 0 + 阶段号 → 忽略 0 | Z7 | 0 + 阶段号也进顶层 | 标 0、2 |
| 单值基线与阶段值并存 | Z5 | 单值 0 进阶段 1 格而不是顶层 | 语气标 0 + 3 |
| 做法、态度不认 0 | P5、P6 | 类别表加上 `custom` / 两个调用点不传 `layer` 而用默认 | 做法、态度标 0 |
| 后来的不泄露到阶段 1 | Z4 | 标 2、3 的也进顶层 | 后来条目 |
| 日志可核 | Z3、P3 | info 两类写成同一个词 | 两类各标 0 |
| 提示词 | Z8 | 换回旧句 | `format_prompt_after` |

## 设计原则核对（Shiyu：不过度工业化、不打补丁、隔离抽象复用、高内聚低耦合）

| 原则 | 本 spec 怎么守 |
|---|---|
| 不打补丁 | 不为状态类另写规则，扩的是 E3 已有的那一处 |
| 复用 | 0 的语义、顶层判据、`dispatch` 全部沿用 |
| 低耦合 | 认不认 0 看登记表的类别，不写字段名；不改 `phase_anchoring` / `arc_view` |
| 不过度工业化 | 一个小表 + 一个必填参数；不加新字段、新类型 |

## 技能

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 按判断清单逐条读代码，对不上就停下报告 |
| 改动 | `@tdd` | 先写目标检查（Z1–Z8）再改 |
| 交付 | `@verification-before-completion` | 贴受影响测试原始输出与 CI 结果 |

## 交付

一个 PR，英文标题、描述、commit。不写部署步骤。不调真模型、不花钱。

## 复核补充（10-17）

- **Z8 收紧为各段分开断言**：原来只在 G2/G3 整组里搜整句，同一句在描述类、原话类、重要事项三处都出现，删掉任一处的 `+ _BASELINE_ZERO_RULE` 仍能在别处命中 —— 复核的 M5/M6 两个变异因此没被抓到。现在维度 B 段（守 `_PHASE_DESCRIPTION_RULE`）、「重要：」对象字段行（守 `_FORMAT_IMPORTANCE`）、口癖段（守 `_PHASE_VERBATIM_RULE`）各断言一次；两个变异分别删描述类规则、重要事项之后 Z8 都变红。
- **记忆调用点改取登记表**：`core/card_draft.py` 的 `key_memories` 调用点原来写死 `layer="experience"`，与另外三个调用点（态度、做法、通用字段）从 `REGISTRY[...].layer` 取不是同一种写法；改成 `layer=REGISTRY["key_memories"].layer`，加一种认 0 的类别时只看登记表。
