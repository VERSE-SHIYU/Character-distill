# spec：故事前的经历不再被丢（E3）+ 带条件的推托不记成承诺（E2）

基线：main `d07bfff0`。放进 worktree 的 `docs/specs/`，用 `Test-Path` 确认；补充写回本文件。
依据：共享文档《阶段锚点核对失效：调查报告》「E1–E3 调查结果」「E1–E3 方案调研」两节。Shiyu 10-16 定：E3 方案 1、E2 方案 2、E1 先不动。

**两个 PR，各自独立，互不依赖：PR-E3（蒸馏侧）、PR-E2（本场记录）。**

## 这份 spec 解决什么、不解决什么

| 解决 | 不解决 |
|---|---|
| E3：经历类（关键记忆等）标在「故事开始前」的条目，不再被当成编号非法整条丢掉 | E1：底色字段被挪出阶段 1（证据不足，先攒数据） |
| E2：以后「等你明儿清醒了再说」这类带条件的推托，不再记进「答应的事」 | E2：已经记下的旧承诺不会自动过期（方案 2 的已知代价） |

## 设计原则

不打补丁：不在调用处加分支或布尔开关。每条规则放进它所属的地方、只写一处；以后加一个经历类字段，不用改任何代码。只跑受影响的测试，不预跑变异（变异由独立复核方跑）。

## S0（先做，不写代码）

按下面「判断清单」逐条打开文件读代码。有一条对不上就停下报告。

## 判断清单（基线 `d07bfff0`，每条附读过的代码）

| # | 判断 | 坐标与原文 |
|---|---|---|
| J1 | 编号合法性只收 1..n，0 被撤回并 warning；做法、记忆、态度、各字段共用 | `core/card_draft.py:231-244` `_valid_number_rows`：`valid = sorted({p for p in nums if 1 <= p <= count})` |
| J2 | 记忆走 `verify` 后按经历类分发 | `core/card_draft.py:405-411`：`m = phase_anchoring.verify(draft.key_memories, _valid_number_rows(draft.key_memories, count, "记忆"), …)`；`dispatch(m.phases, count, layer="experience", …)` |
| J3 | 经历类字段还有 `cognitive.knowledge_scope`，走通用字段循环 | `core/card_layers.py:77-78`；`core/card_draft.py:419-426` `for path, spec in _GENERIC_PATHS:` … `dispatch(res.phases, count, layer=spec.layer, …)` |
| J4 | 分发规则只在 `dispatch` 一处；`len(final) == count` 进顶层 | `core/card_draft.py:266` 起，docstring「分发只此一处」 |
| J5 | 记忆的提示：维度 E 定义 + 共用摘录规则；G6 重要事项里「有阶段时 phase 用维度 L 编号（1 起）；没有阶段时 … 填 0」 | `core/distiller.py:235-237`、`:183-186`（`_PHASE_EXCERPT_RULE`，注释「只定义一次，锁 S9 数它」）、`:380` |
| J6 | 「答应的事」一栏已写「只是推托、没有明确应下的不记」 | `core/session_record.py:92-95` |
| J7 | 示范里已有一例纯推托（「到时候再看吧」→ 只记 requests） | `core/session_record.py:218-221` |
| J8 | 测试守示范：须有「对方请求 + 角色应允」和「推托」两类示范，示范不得用验收对话里的词 | `tests/test_session_record_goal.py:493-529`（`test_X1b`、`test_X3`） |

## PR-E3：经历类的「0 = 故事开始前」

### 目标检查（第 1 个提交就带上）

有 3 个阶段的草稿：
- 一条关键记忆只标 `phase: 0` → 转卡后在**顶层** `key_memories`，选任一阶段都看得到；不打「阶段编号不合法」warning。
- 只标 `phase: 0` 的那条记忆，有一行 info 日志写明「故事开始前」+ 条目文字（下次蒸卡人工翻看有没有误标）。
- 一条关键记忆标 `phase: 0` 和 `phase: 2`（摘录在阶段 2）→ **忽略 0**，按阶段 2 照常核对，结果与今天相同：挂阶段 2，**不进顶层**（不新增泄露面）。
- 一条**做法**标 `phase: 0` → 仍按编号非法撤回（只有经历类认 0）。

### 改法

数据流照现有的 `unlocated` 那一套走（`verify` 产出序号 → `dispatch` 按类别处置，J4 docstring），不另起炉灶：

1. **认 0 只在规则 0 一处**（J1）：`_valid_number_rows` 增加 `layer` 参数（默认 `"state"`），返回值从「每条的合法编号」变成一个小 NamedTuple：每条的合法编号 + 故事前条目的序号。
   - `layer == "experience"` 且**只标了 0**：记为故事前，该条的合法编号给空（`verify` 走「规则 0 已整条撤回」那一支，自然**不做位置核对**——故事前的经历在原文里多半是后文回忆，摘录位置定不了时间，与问题 C 同理）；打一行 info「故事开始前：<条目文字>」，不 warning。
   - `layer == "experience"` 且 **0 与 1..n 并存**：忽略 0（不 warning），其余编号照常走位置核对。理由：模型同时给了故事内阶段，就有位置证据可用；直接进顶层会让误标的后期经历在阶段 1 可见，违反「永不泄露后续剧情」（`card_draft.py:248`）。
   - 0 以外的越界编号照旧 warning；其他类别 0 照旧撤回 + warning，行为不变。
2. **放到哪只在 `dispatch` 一处**（J4）：加参数 `pre_story`（序号列表，与 `unlocated` 并列）。经历类 → 最终阶段置为全部阶段，由已有判据 `len(final) == count` 进顶层（`_layer_phases` 对覆盖全部阶段的条目原样保留，`card_draft.py:261-262`）。`state` 类不会产出 `pre_story`。
3. 调用点只是把规则 0 的结果原样传下去：记忆（`:405-411`）与通用字段循环（`:419-426`）传 `layer` 并把 `pre_story` 交给 `dispatch`；做法（`:402-404`）、态度（`:337`）只取合法编号，不传 `layer`。调用点不写任何关于 0 的判断。
4. 同步改两处已过时的 docstring：`card_draft.py:50`「0 是无阶段哨兵…撤回」、`:80`「缺 phase 键按 0 处理…撤回」——改成「非经历类撤回；经历类 0 = 故事开始前」。
5. 提示词：
   - 维度 E（J5，`distiller.py:235-237`）加一句：「故事开始之前就发生的经历（如童年、身世），phase 填 0；故事里发生的，用维度 L 的阶段编号。」
   - G6 重要事项（`:380`）改成与之不矛盾：「有阶段时，occurrences 的 phase 用维度 L 的阶段编号（1 起）；关键记忆发生在故事开始之前的填 0；没有阶段时只写一条，phase 填 0。」
   - 共用的 `_PHASE_EXCERPT_RULE` 不改（锁 S9；它也被做法用，做法不认 0）。

依据：[PHASE-Tree](https://arxiv.org/html/2608.06975)（arXiv 2608.06975）：「The identity root (name, gender, backstory) is immutable and never updated」；[TimeChara](https://arxiv.org/html/2405.18027v1)：过去的事在之后任一时间点都知道。

### 结构自检

| 问题 | 答案（附行号） |
|---|---|
| 「经历类认 0」判在几处 | 一处：`_valid_number_rows` |
| 「故事前 → 顶层」放在几处 | 一处：`dispatch`，与 `unlocated` 并列 |
| 调用点有没有关于 0 的分支 | 没有：只传 `layer`、转交 `pre_story` |
| `verify` 改了吗 | 没有（它「不分类别」，docstring `phase_anchoring.py:150-170`） |
| 以后新增一个经历类字段要改几处 | 0 处：登记表标 `experience`，走通用循环即生效 |

## PR-E2：带条件的推托不记成承诺

### 目标检查

渲染出的抽取提示词（`render_extraction_prompt`）里：
- `promises` 栏定义含新加的那句（整句断言）；
- 示范里有一例「带条件的推托 → 只记 requests、不记 promises」。

### 改法

1. `promises` 栏定义（J6）在「只是推托、没有明确应下的不记」后补一句：「附条件往后拖、没说具体做什么的，也不记（如『等你明儿清醒了再说』『改天再说』）。」
2. 示范（J7 后面）加一例，人物与句子另编（J8：不能用验收对话里的词，如「清醒」「粥」「阿羽」）：

```
Current state: （还没有）
对方：今晚陪我看完这部电影吧？
老陈：等你明天还有空再说吧，到时候一定陪你。
Output: {"record_ops": [{"col": "requests", "text": "对方请老陈今晚陪他看完电影。", "evidence": "今晚陪我看完这部电影吧", "supersedes": null}]}
```

   （写进代码时按现有示范的格式用 `{ops}` / `{sup}` 占位，与 J7 那几例一致。）

3. 不改取代机制、不加过期。

### 结构自检

| 问题 | 答案 |
|---|---|
| 「推托不记」写了几处 | 一处：`promises` 栏定义；示范只示范，不另立规则 |
| 改了抽取以外的东西吗 | 没有 |

## 测试

- 本地只跑受影响文件。PR-E3：`tests/test_card_draft.py`、`tests/test_phase_anchoring.py`、`tests/test_phase_tagged_goal.py`、`tests/test_arc_phase_unlocated.py`、`tests/test_arc_phase_fields_unit.py`、`tests/test_arc_phase_select.py`。`tests/perf/card_draft_mutations.py:81` 锚着 warning 那行原文，改动若碰到那行，同步锚点。PR-E2：`tests/test_session_record_goal.py`。库用 docker PG，合并门是分支 CI。
- 测试替身照生产：草稿用 `CardDraft` 真实形态，不手搓卡。
- 新增：上面两段的目标检查；E3 另加「标 0 的做法仍撤回」「标 0 的态度仍撤回」。

## 对账表（独立复核方验，自补放宽、过严两个方向的变异）

| 行为 | 守它的测试 | 让它变红的变异 | 触发状态 |
|---|---|---|---|
| 经历类 0 → 顶层 | 只标 0 的记忆在顶层 | 经历类也撤回 0 | 记忆标 0 |
| 0 与阶段号并存 → 忽略 0、其余照常核对 | 标 0+2、摘录落在阶段 2 的记忆挂阶段 2、不在顶层 | 并存时当成只标 0 整条撤回 | 记忆标 0 和 2 |
| 非经历类 0 仍撤回 | 标 0 的做法、态度被撤回 | 所有类别都认 0 | 做法标 0 |
| 只标 0 的故事前条目不做位置核对、直接进顶层 | 只标 0 的记忆在顶层、无「改挂」日志 | 只标 0 的经历也走 `verify` 兜底挂最后阶段 | 记忆标 0 |
| 经历类 0 不 warning | 无「阶段编号不合法」 | 照旧 warning | 记忆标 0 |
| 提示词 E 维度含新句 | 整句断言 | 删句中任一词 | 渲染 G6 提示 |
| promises 定义含新句 | 整句断言 | 删句中任一词 | 渲染抽取提示 |
| 带条件推托的示范 | 示范只记 requests | 示范里加上 promises | 渲染抽取提示 |

## 技能

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 按行号读代码 |
| 改动 | `@tdd` | 先写目标检查再改 |
| 交付 | `@verification-before-completion` | 贴受影响测试原始输出 |

## 交付

两个 PR，英文标题、描述、commit；spec 文件只放进 PR-E3。不写部署步骤。不调模型、不花钱。
上线后验收：E3 等下次正常蒸卡看顶层 `key_memories` 有没有童年、身世类经历；E2 在新会话里遇到角色推托时，看本场记录「答应的事」有没有记进去。

---

## 实施补充（2026-10-16）

**S0 判断清单核对。** 基线 `d07bfff0`，上表 J1–J8 逐条打开代码读过、行号与原文全部一致，无一条对不上。

**对账表两处修正（原表两行与「改法」「目标检查」自相矛盾，以后者为准，已改入本表）。**

- 原第 2 行写「0 与阶段号并存 → 顶层」。这与 §改法第 1 条第 2 点「0 与 1..n 并存：忽略 0，其余编号照常走位置核对」及目标检查第 3 条「挂阶段 2，**不进顶层**（不新增泄露面）」直接冲突。按改法/目标检查实现，本表第 2 行改为「忽略 0、其余照常核对」，变异改为「并存时当成只标 0 整条撤回」。
- 原第 4 行写「标 0+2、摘录落在阶段 3 的记忆仍在顶层」——同上一处冲突，且「不做位置核对」只适用于**只标 0** 的条目（并存时其余编号照常核对）。第 4 行改为「只标 0 的故事前条目不做位置核对、直接进顶层」，标 0+2 的触发归第 2 行。

**实现落点。**

- `core/card_draft.py`：`_valid_number_rows` 增加 `layer` 参数，返回 `ValidRows(valid, pre_story)`（认 0 只在此一处）；`dispatch` 增加 `pre_story` 参数（与 `unlocated` 并列），经历类故事前 → 最终阶段置全覆盖 → 顶层；记忆与通用字段循环两个调用点只传 `layer`、转交 `pre_story`；做法、态度只取 `.valid`；两处过时 docstring（`:50`、`:80`）已改。
- `core/distiller.py`：维度 E 加一句、G6 重要事项 `:380` 改写；`_PHASE_EXCERPT_RULE` 未动（S9 片段仍恰 1 次）。
- 目标检查：新增 `tests/test_pre_story_memory_goal.py`（P1–P7，另加 P4b 覆盖「并存时仍走位置核对」）。

**未预跑变异**（按 §测试）。但逐条核对了变异锚点在新源码里仍原样命中、各恰一处：`tests/perf/card_draft_mutations.py` 的 M5/M6/M7/M8、M1/M2/M3/M9；`docs/specs/artifacts/arc_phase_unlocated_mutations.py` 里 `dispatch` 的 `unlocated` 块。故本 PR 无需同步任何锚点。

**受影响测试原始输出**：`tests/test_card_draft.py`、`tests/test_phase_anchoring.py`、`tests/test_phase_tagged_goal.py`、`tests/test_arc_phase_unlocated.py`、`tests/test_arc_phase_fields_unit.py`、`tests/test_arc_phase_select.py`、两张锁文件、`tests/test_card_arc_behaviors.py`、`tests/test_lock_coverage.py` 合计 **310 passed**；`tests/test_pre_story_memory_goal.py` **9 passed**。
