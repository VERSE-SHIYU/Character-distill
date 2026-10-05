# arc-phase-anchoring：按原文位置校正「做法属于哪个阶段」

> 基线：main @ `0d3d40b0`（PR #111 合并后）。分支：`feat/arc-phase-anchoring`。
> 本 spec 由 Claude 写、执行方（本地 Claude Code）实现、Claude 在沙箱 PG 上亲手跑变异审计。进度写 §8，补充写 §9，末尾 §10 为对照 Shiyu 标准的自检表。

## 0. 目的

arc-behaviors 审计（spec `arc-behaviors-draft` §9.6）发现：阶段边界与章节不对齐时，模型给做法归阶段不准（孔乙己「下回还清」被放进顶层、阿Q「偷萝卜」与赵太爷「买旧货」被放进更晚的阶段）。本段让**每个阶段标注都要有该阶段里的原文摘录作证**，由**代码**按摘录在原文中的位置检查标注是否成立，不成立的去掉。不增加模型调用，存卡结构不变。

## 1. 已拍板（Shiyu 2026-10-05）

| # | 决定 |
|---|---|
| D1 | 方案 A：每个阶段标注附一段逐字核对得上的摘录；模型给每阶段写起点锚点；代码按位置检查 |
| D2 | 检查放在唯一出口：`card_from_draft(data, source_text)`，5 个调用点传入手上的原文；草稿信息检查完即丢，**存卡结构不变** |
| D3 | 草稿格式：做法写成 `occurrences: [{phase, quote}]`（阶段与摘录一一绑定），阶段加 `anchor` |
| D4 | 核对沿用现有 `core/quotes.py` 的规范化（繁转简、去全部空白与标点、并异体字、按省略号分段），**不加相似度模糊匹配** |
| D5 | 兜底：一条做法的标注全部被去掉时，退回模型原标注并 warning |
| D6 | 重复摘录：同一摘录在原文出现多次时，任意一次落在所标阶段即通过，记「位置不唯一」 |
| D7 | 锚点核对不上或顺序颠倒：整张卡跳过位置检查，保留模型标注并 warning |
| D8 | 上线后监测三项：摘录清空率、位置不唯一比例、被去掉的标注数 |
| D9 | 旧卡不动（沿用 10-02「旧卡不处理」）；证据注入暂缓 |

## 2. 已查实约束（坐标为 `0d3d40b0`；S0 由执行方逐条复核，任一不成立即停下报告）

C1. 唯一出口 `core/card_draft.py:64` `card_from_draft(data)`：按 `DraftBehavior.phases`（`:27`）分发，所有阶段都标 → 顶层，否则挂到所标阶段；编号不合法打 warning（`:83-85`）。手上**没有原文**。
C2. 调用点 5 处，手上的原文变量：`core/distiller.py:1497`（`distill` 的 `text`）、`:1606`（`_distill_longcontext` 的 `text`）、`:2207`（`distill_incremental` 格式化，`text`）、`web/routers/distill.py:507`（后台任务，`content`，函数签名 `:320-322`）、`:1126`（`/run_stream`，`content`）。
C3. 存卡做法 `core/schema.py:69-78` `SituationBehavior{situation, behavior, source_quote}`，`source_quote` 只用于展示、落卡前核对（`:78`）；草稿 `DraftBehavior` 继承它（`card_draft.py:25`），所以草稿里也有一个 `source_quote`。
C4. 阶段 `core/schema.py:84-93` `PhaseState{label, state}`，草稿与存卡共用。
C5. 核对工具 `core/quotes.py:41-63`：`normalize`（繁转简、去全部空白与标点、并异体字）与 `verbatim_in_normalized`（按省略号分段、各段都在即通过）。**没有「返回位置」的函数**。
C6. 落卡后还有一道摘录核对：`core/card_quotes.py:127` `retract_unverified(card, content)`，由 `finalize_card`（`core/distiller.py:1770`）调用；查不到的摘录清空、条目保留。本段不改它，它照常复核存卡里的 `source_quote`。
C7. 提示词：维度 L `core/distiller.py:190` 起、维度 O `:205` 起（`phases` 说明 `:209`），G6 模板 `:259`，G6 格式说明 `:291`。
C8. 引用旧草稿形态（`phases` 整数数组）的测试与脚本：`tests/test_card_draft.py`、`tests/test_card_arc_behaviors.py`、`tests/test_distiller_routing.py`、`tests/perf/card_draft_mutations.py`、`tests/perf/card_draft_red_lines.json`（见附录 A）。`tests/perf/arc_draft_sample_check.py` 读的是**存卡**，不受影响。
C9. 实测（沙箱，2026-10-05 复测）：`normalize` 125 万字整本 0.32s；在规范化全文里找一个短串的全部位置约 1ms。
C11. 现有核对**不要求省略号各段的先后顺序**：`verbatim_in_normalized` 只判各段都在（`:61`）。沙箱实测（`tests/fixtures/kongyiji.txt`，规范化后 2187 字）：`verbatim_in_normalized(s, "下回还清罢……温一碗酒")` 为 True（倒序也过）。
C12. 省略号首段可能极短：模型真实写法「这……下回还清罢」，首段「这」在规范化全文出现 22 次、首次在第 64 字（开篇）；「下回还清罢」在第 1907 字（断腿后）。**若以首段定位置，正好把要纠正的这条错放回阶段 1**。故位置取**最长一段**（§3.1）。
C13. 原文口径：路由两处（`:507` `:1126`）手上是原始 `content`，聊天类文本在 `distiller` 内部经 `_layer2_character_context` 预处理后才给模型（`core/distiller.py:2258/2271`）；落卡后的 `retract_unverified` 也拿原始 `content` 核对（`:1791`）。本段与之同口径：一律传原始全文。
C14. 文档：`AGENTS.md:1799` 描述草稿格式（「每条带 `phases` 阶段编号」），随本段同步改写；`docs/specs/arc-behaviors-draft-reference.json` 与 `arc-behaviors-draft-samples/` 是历史审计数据，全仓库无代码读取（`git grep` 只命中 `docs/specs/arc-behaviors-draft.md`），不改。
C15. 前端只读存卡：`CharCard.jsx:835`、`EditCardModal.jsx:88`、`MarketCardDetail.jsx:750` 读 `character_arc.phases`（存卡形态）；存卡结构不变，前端不改、不跑 `npm test`。
C10. 执行上下文：`web/routers/distill.py:1126` 在 `/run_stream` 的 async 生成器里**同步**调用 `card_from_draft`（事件循环线程）；`:507` 在后台任务线程里。

### 2.1 路径机制表（通道 × 执行上下文 × 守它的测试）

| 通道 | 新增工作 | 执行上下文 | 处理 | 守它的测试 |
|---|---|---|---|---|
| `/start` 后台任务（`:507`） | 整本 normalize + 位置查找 | 后台线程 | 直接调用 | E4 |
| `/run_stream`（`:1126`） | 同上（约 0.3s CPU） | **事件循环** | 改为 `await asyncio.to_thread(card_from_draft, data, content)`，不阻塞其他请求 | E5（含「不在事件循环线程执行」断言） |
| 同步三入口（`:1497/1606/2207`） | 同上 | 调用方线程 | 直接调用 | E1–E3 |

不改任何重试、超时、心跳与计数。

### 2.2 规模表

| 量 | 上限 | 来源 | 处理 |
|---|---|---|---|
| 阶段数 | 提示词「最多 4 个」 | 维度 L | 锚点数 = 阶段数（阶段 1 可空） |
| 做法条数 | 6–12 | 维度 O | 每条 `occurrences` ≤ 阶段数 |
| 摘录长度 | 10–40 字 | 维度 O | 不按长度设限；能否作位置证据看出现次数（下一行） |
| 摘录最长段在原文中的出现次数 | `MAX_OCCURRENCES = 3`（`core/phase_anchoring.py` 内具名常量，只此一处） | 样本实测：公版《孔乙己》两张卡 14 段摘录，出现次数全部为 1 或 2；《阿Q正传》原文不在仓库，另两张卡 20 段未测 | > 3 次不作位置证据（按该段「核对不上」处理）；样本外（台词在全书反复出现，如「我真傻，真的」）同样按此处理，必要时走兜底 D5 |
| 原文 | 小说 < 90 万 tokens（约 120–130 万字） | `core/length_budget.py` | 每次转换只 normalize 一次整本（C9：0.29s） |
| 整本规范化次数 | 每张卡 2 次（本段 `card_from_draft` 1 次 + 落卡后 `retract_unverified` 1 次，`card_quotes.py:139`），各约 0.29s | C9 | 不合并：合并须改 `finalize_card` 签名，超出本段改动面；成本已量化，记为已知代价 |
| 额外输出 | 每卡约 80–130 tokens，最坏约 1,200 | 四张样本实算 | 远低于 `LONG_OUTPUT_MAX_TOKENS` 16384 |

### 2.3 出处对照表

| 依据 | 条目 | 对应 | 性质 |
|---|---|---|---|
| ArcANE（arXiv 2606.05553 §3.1） | 阶段用章节范围 + 锚定该状态的关键时刻表示 | D1、D3 的 `anchor`、§3.2 范围计算 | 文献 |
| Papalampidi, Keller, Lapata（EMNLP 2019，TRIPOD） | 转折点把叙事切成主题单元；转折点识别困难、部分类型模糊 | `anchor` = 阶段起点；D7 必要性 | 文献 |
| E²RAG（EACL 2026） | 节点记录文档位置，先后靠位置判定 | 位置由代码计算 | 文献 |
| GraphLit / Chen 2026；ConStory-Bench 2026 | 模型排序情节、时间推理不可靠 | 不信模型自报的阶段 | 文献 |
| Verifiable by Construction（arXiv 2609.15964）；VetScore（arXiv 2608.03675） | 说法逐条绑定逐字摘录；各模型逐字准确率 42%–93%，「Fuzzy」指省略号跳过 | D3、D4 | 文献 |
| `core/quotes.py:41-63` | 现有规范化已覆盖格式差异 | D4 | 代码事实 |
| D2、D5、D6、D7 的具体规则 | —— | —— | **自研**，无文献直接验证 |
| §3.2 规则 0（先过滤编号再查位置） | `card_draft.py:82-90` 现有过滤 | 规则 0、U9 | 代码事实（保持现有行为） |
| 位置取最长段 | C11、C12 实测 | §3.1、U8、M15 | **自研**，依据为本仓库实测 |
| 同阶段多段摘录任一通过、`source_quote` 取值（规则 3、4.1） | —— | U10、M18 | **自研** |
| 最长段出现 > `MAX_OCCURRENCES` 次不作位置证据 | 样本实测（§2.2） | 规则 3、U3、M19 | **自研**（Shiyu 2026-10-05 选方案 A）；N=3 取样本最大值 2 之上一档，上线后看「位置不唯一」比例 |

## 3. 设计

### 3.1 结构（隔离与复用）

- **`core/schema.py`**：抽出共用底 `BehaviorCore{situation, behavior}`；`SituationBehavior(BehaviorCore)` 加 `source_quote`（存卡不变）。理由：草稿做法不应再继承 `source_quote`（C3），用共用底而不是复制字段，做法同已有的 `PhaseState` / `ArcAxis`。
- **`core/card_draft.py`**（草稿契约，改）：
  - `DraftOccurrence{phase: int, quote: str}`；
  - `DraftBehavior(BehaviorCore)` + `occurrences: list[DraftOccurrence]`（删去 `phases`）；
  - `DraftPhase(PhaseState)` + `anchor: str = ""`；`DraftArc.phases: list[DraftPhase]`；
  - `card_from_draft(data, source_text)`：校验草稿 → 调 `phase_anchoring.verify(draft, source_text)` 得到每条做法的最终阶段 → 按原规则分发（全阶段 → 顶层，否则挂所标阶段）→ 存卡的 `source_quote`：挂在阶段 k 下用阶段 k 那段摘录，顶层用第一段。
- **`core/phase_anchoring.py`**（新，纯计算，无 IO）：只做「锚点 → 阶段范围」「摘录位置 → 标注是否成立」「兜底」「监测计数」，返回结果对象，不改草稿、不碰存卡。
- **`core/quotes.py`**（加一个函数，复用现有规范化与分段）：`locate_in_normalized(source_norm, quote) -> list[int] | None`——
  - 按现有方式按省略号分段；任一段不在全文 → `None`（= 核对不上，与今天口径一致，**不新增顺序要求**，C11）；
  - 否则返回**最长一段**在全文中的全部起点（C12：短段不作位置证据）；
  - `verbatim_in_normalized` 改为 `locate_in_normalized(...) is not None`，**分段与匹配只写一次**，判定结果与改前逐字一致。

### 3.2 规则

0. **先后次序**：先做现有的编号合法性过滤（越界编号去掉并 warning，全部不合法则整条撤回——**现有行为不变**，`card_draft.py:82-90`），再对剩下的合法标注做位置检查；兜底（规则 4）退回的是**合法**标注。
1. **阶段范围**：用 `locate_in_normalized` 找每个锚点的位置（同样取最长段）；阶段 k 的范围是 `[锚点 k 位置, 锚点 k+1 位置)`，阶段 1 锚点为空时从 0 开始，最后一阶段到全文末尾。锚点重复出现时取第一次出现。
2. **整卡跳过（D7）**：阶段 1 以外的锚点查不到**或为空**、或锚点位置不严格递增 → 整卡不做位置检查，保留模型标注，warning 一条（含卡名与原因）。
3. **单条标注**：同一阶段若有多段摘录，按阶段合并，任一段通过即该阶段成立。摘录查不到，或最长段在原文出现超过 `MAX_OCCURRENCES` 次 → 该段不作证据；有任一位置落在所标阶段范围内（D6）→ 保留，若位置数 > 1 记「位置不唯一」；否则去掉，每个不成立的（做法, 阶段）各打一条 warning。
4. **兜底（D5）**：一条做法的标注全部被去掉 → 退回它原来的全部标注，warning。
4.1 **存卡 `source_quote` 取值**：挂在阶段 k 下取阶段 k 中第一段通过的摘录，没有通过的（兜底 / 整卡跳过时）取阶段 k 的第一段；顶层取第一段。是否逐字由落卡后的 `retract_unverified` 照常把关（C6），本段不重复核对。
5. **无阶段的卡**：不做位置检查，做法全放顶层（同现状），`source_quote` 取第一段摘录。
6. **监测（D8）**：每张卡转换后打一行结构化 INFO：`[phase_anchoring] card=… tags=… dropped=… ambiguous=… unverified_quotes=… fallback=… skipped_card=…`。`tags` 与 `dropped` 按（做法, 阶段）计，整卡跳过时 `tags` 照常计；`unverified_quotes` 按摘录计。

### 3.3 提示词（`core/distiller.py`）

- 维度 L：每个阶段加 `anchor`——原文中标志这一阶段开始的一句（10–40 字，逐字照抄）；阶段 1 可留空。
- 维度 O：删去 `phases` 与 `source_quote` 两条说明，换成 `occurrences`——这个做法在哪些阶段出现过，每个阶段各给一段**该阶段里**的原文摘录（10–40 字，逐字照抄），写成 `[{"phase": 1, "quote": "…"}]`；维度 L 没有阶段时只写一条，`phase` 填 0。
- G6 模板（`:259`）与格式说明（`:291`）同步。

### 3.4 调用点接线

5 处调用点全部改为 `card_from_draft(data, <手上的原文>)`；`:1126` 改走 `asyncio.to_thread`（§2.1）。不新增第二个出口。

## 4. 测试计划（先在 `0d3d40b0` 上红、再在本分支上绿；每条边界两侧都测）

### 4.1 单元（`tests/test_phase_anchoring.py`，新）

**本表是规则→测试的索引，不复述规则** —— 规则的权威表达只在 §3.2，摘录匹配的权威表达在 §2.3（C5）。左列填 §3.2 的规则号，E 行对应 §4.2 的调用点；右列填守它的测试函数，函数名一律以 `tests/test_phase_anchoring.py`（规则 5 在 `tests/test_card_draft.py`）里 `def test_` 的实际定义为准，旧 U 编号不再使用。

| §3.2 规则 / §2.3 依据 / §4.2 调用点 | 守它的测试函数 |
|---|---|
| 规则 0（编号合法性过滤） | `test_u10_out_of_range_number_alone_retracts_the_row`、`test_u11_in_range_number_kept_alongside_an_out_of_range_one`、`test_u12_invalid_number_warns` |
| 规则 1（阶段范围） | `test_u1_phase_ranges_are_half_open`、`test_u1b_anchor_position_belongs_to_the_later_phase_not_the_earlier`、`test_u1c_character_just_before_the_anchor_belongs_to_the_earlier_phase` |
| 规则 2（整卡跳过） | `test_u16_missing_anchor_keeps_every_tag_unchecked`、`test_u17_missing_anchor_warns_skip`、`test_u18_reversed_anchors_skip_the_whole_card`、`test_u23_empty_anchor_on_last_phase_skips_the_whole_card`、`test_u24_empty_anchor_on_a_middle_phase_skips_with_its_own_reason` |
| 规则 3（单条标注） | `test_u7_tag_kept_when_quote_is_in_the_tagged_phase_dropped_when_not`、`test_u8_quote_not_found_is_dropped`、`test_u9_up_to_max_occurrences_is_evidence`、`test_u9b_over_max_occurrences_is_not_evidence`、`test_u19_duplicate_quote_any_hit_keeps_and_all_miss_drops`、`test_u20_phase_absent_when_no_quote_is_in_range`、`test_u27_each_dropped_tag_warns_once` |
| 规则 4（兜底） | `test_u13_fallback_restores_when_every_tag_was_dropped`、`test_u14_fallback_warns`、`test_u15_partial_drop_does_not_fallback` |
| 规则 4.1（存卡 `source_quote` 取值） | `test_u21_source_quote_is_the_passing_one_not_the_first` |
| 规则 5（无阶段的卡） | `test_u5_card_without_phases_keeps_everything_top_level`（`tests/test_card_draft.py`） |
| 规则 6（监测） | `test_u22_monitoring_line_reports_counts`、`test_u25_monitoring_counts_tags_not_quotes`、`test_u26_skipped_card_still_counts_its_tags` |
| §2.3 C5（`core/quotes.py` 的 `locate_in_normalized` / `verbatim_in_normalized`，规则 1、3 依赖） | `test_u2_locate_takes_the_longest_segment`、`test_u3_locate_returns_every_start_of_the_segment`、`test_u4_locate_is_none_when_any_segment_is_absent`、`test_u5_verbatim_is_false_when_any_segment_is_absent`、`test_u6_verbatim_ignores_segment_order` |
| E1 `distill`（`:1497`） | `test_entry_distill_anchors` |
| E2 `_distill_longcontext`（`:1606`） | `test_entry_longcontext_anchors` |
| E3 `distill_incremental` 格式化（`:2207`） | `test_entry_incremental_anchors` |
| E4 `/start`（`:507`） | `test_bg_task_anchors` |
| E5 `/run_stream`（`:1126`） | `test_run_stream_anchors` |

**拒侧用例的写法（沙箱预跑时发现）**：兜底 D5 会把「全部被去掉」的标注退回，所以拒侧用例里这条做法必须另有一个**能通过**的标注；否则被测的标注即使没被去掉、结果也一样，变异 M1 会存活。

用《孔乙己》公版原文（`tests/fixtures/kongyiji.txt`）构造：「下回还清」只在断腿后出现 → 标在阶段 1 的那条被去掉（复刻 §9.6 的真实错误）。

### 4.2 调用点矩阵（每个入口喂同一份草稿 + 原文）

| 调用点 \\ 输出 | 位置检查生效（错标被去掉） | 存卡 `source_quote` 按阶段取 | 不在事件循环线程 | 测试名 |
|---|---|---|---|---|
| E1 `distill`（`:1497`） | ✓ | ✓ | — | `test_entry_distill_anchors` |
| E2 `_distill_longcontext`（`:1606`） | ✓ | ✓ | — | `test_entry_longcontext_anchors` |
| E3 `distill_incremental` 格式化（`:2207`） | ✓ | ✓ | — | `test_entry_incremental_anchors` |
| E4 `/start`（`:507`） | ✓ | ✓ | — | `test_bg_task_anchors` |
| E5 `/run_stream`（`:1126`） | ✓ | ✓ | ✓ | `test_run_stream_anchors` |

### 4.3 结构锁

| 编号 | 断言 |
|---|---|
| S1 | `card_from_draft(` 的生产调用点恰为上表 5 处，且每处都传了第二个参数 |
| S2 | 「规范化 + 找位置」只在 `core/quotes.py`：`core/` 其余文件不出现对规范化全文的 `.find(` / `re.finditer(` |
| S3 | 草稿 `DraftBehavior` 不再有 `phases` 与 `source_quote` 字段；存卡 `SituationBehavior` 字段与改前一致 |

## 5. 变异清单（「放宽」与「过严」两个方向都要有；执行方实现后跑，Claude 审计时在沙箱复跑）

**本表为发出前快照；实施后以 `tests/perf/arc_phase_anchoring_mutations.py` 与 `_red_lines.json` 为准（M1–M36）。**

| 编号 | 方向 | 变异 | 应红 |
|---|---|---|---|
| M1 | 放宽 | 位置检查恒通过（所有标注保留） | U2 拒侧、E1–E5 |
| M2 | 过严 | 位置检查恒不通过（所有标注去掉，兜底后全退回） | U2 收侧 |
| M3 | 过严 | 删兜底（全部去掉时整条消失） | U5 |
| M4 | 放宽 | 兜底在部分去掉时也退回 | U5 另一侧 |
| M5a | 放宽 | 锚点查不到时当作全文末尾，照常检查（不跳过） | U6 |
| M5b | 放宽 | 去掉锚点「严格递增」检查 | U6（顺序颠倒一侧） |
| M6 | 过严 | 锚点正常也整卡跳过 | U6 另一侧、E1 |
| M7 | 过严 | 重复摘录要求**所有**位置都在范围内 | U4 收侧 |
| M8 | 放宽 | 重复摘录不检查范围直接通过 | U4 拒侧 |
| M9 | 边界 | 范围用 `<=`（锚点位置归前一阶段） | U1 |
| M10 | 接线 | 某一调用点传空串作原文 | 对应 E 行 |
| M11 | 取值 | 阶段 k 的 `source_quote` 一律取第一段 | 矩阵第二列 |
| M12 | 上下文 | `:1126` 去掉 `to_thread` | E5 线程断言 |
| M13 | 监测 | 不打监测行或计数错 | U7 |
| M14 | 复用 | `verbatim_in_normalized` 不再走 `locate_in_normalized`（另写一份匹配） | S2、U8 |
| M15 | 取值 | 位置取首段而非最长段 | U8（孔乙己「这……」） |
| M16 | 过严 | 定位时要求省略号各段按顺序出现 | U8 倒序侧 |
| M17 | 次序 | 兜底退回含越界编号的原标注 | U9 |
| M18 | 过严 | 同阶段多段摘录要求每段都在范围内 | U10 收侧 |
| M19a | 边界 | 出现次数判定改 `>=`（恰好 3 次也不作证据） | U3 收侧 |
| M19b | 放宽 | 删去次数限制 | U3 拒侧 |

### 5.1 发出前预跑（Claude 沙箱，2026-10-05）

在沙箱写了纯计算层的原型（`locate_in_normalized` + 位置检查 + 兜底 + 计数，不含调用点接线），按 §4.1 写 15 条单元用例，逐条注入变异：

```
RED   M1  放宽 恒通过            | 8 failed, 7 passed
RED   M2  过严 恒不通过          | 9 failed, 6 passed
RED   M3  过严 删兜底            | 1 failed, 14 passed
RED   M4  放宽 部分去掉也退回    | 7 failed, 8 passed
RED   M5a 放宽 锚点失败照查      | 1 failed, 14 passed
RED   M5b 放宽 不查顺序          | 1 failed, 14 passed
RED   M6  过严 锚点正常也跳过    | 10 failed, 5 passed
RED   M7  过严 重复须全在范围    | 1 failed, 14 passed
RED   M8  放宽 重复不查范围      | 1 failed, 14 passed
RED   M9  边界 <=                | 2 failed, 13 passed
RED   M13 监测 不计 dropped      | 1 failed, 14 passed
RED   M15 取首段                 | 2 failed, 13 passed
RED   M16 过严 要求顺序          | 1 failed, 14 passed
RED   M18 过严 同阶段每段都须在  | 3 failed, 12 passed
RED   M19a 边界 >=               | 1 failed, 14 passed
RED   M19b 放宽 删次数限制       | 1 failed, 14 passed
原型未变异：15 passed
```

预跑中暴露两处，已改进正文：
- M5 初版写成「锚点失败当 0」，被「严格递增」检查顺带拦住，测不出东西 → 拆成 M5a / M5b；
- 拒侧用例被兜底 D5 掩盖 → §4.1 加拒侧写法。

**未预跑**：M10、M11、M12、M14、M17 依赖调用点接线、`card_from_draft` 或结构锁，要等实现后才有代码；审计时由 Claude 在沙箱 PG 上亲手跑并贴输出。原型只用来验证测试设计，不交付，以执行方的实现为准。

驱动基于 `tests/perf/mutation_framework.py`，产物 `tests/perf/arc_phase_anchoring_red_lines.json`（`tests/test_lock_coverage.py` 要求）。旧驱动 `card_draft_mutations.py` 里引用 `phases` 的锚点随草稿格式同步更新，其产物重新生成。

## 6. 本地命令（只跑受影响的文件；合并门是分支 CI；库只用 PG）

```powershell
docker ps --format "{{.Names}} {{.Ports}}" | Select-String "55432"   # 被占用就停下报告
docker compose -f docker-compose.test.yml up -d --wait
python -m pytest -q tests/test_phase_anchoring.py tests/test_card_draft.py tests/test_card_arc_behaviors.py tests/test_distiller_routing.py tests/test_card_quotes.py tests/test_quotes.py tests/test_distiller_dialogue_pick.py tests/test_distill_task_api.py tests/test_lock_coverage.py
python tests/perf/arc_phase_anchoring_mutations.py
python tests/perf/card_draft_mutations.py
```

不跑本地全量；合并只做 git 操作。

## 7. 已知局限与上线后监测

- 「摘录位置代替章节号」与 D5–D7 属自研，提升幅度无文献数据。
- 分片路径（只有超长聊天记录会走）出卡时看不到全文，摘录核对不上会更多，兜底更常触发。
- 摘录对得上 ≠ 做法本身正确（Verifiable by Construction）；本段只用摘录定位置。
- 上线后从日志统计 D8 三项；清空率或兜底率明显偏高时停下评估，不自行改阈值。

## 8. 进度

- [ ] 交接：`Test-Path docs/specs/arc-phase-anchoring.md`；`git log --oneline -3` 与远端一致
- [ ] 执行方 S0：C1–C10 复核；附录 A 重跑比对
- [ ] 测试先行：§4 在 `0d3d40b0` 上红（贴输出）
- [ ] 实现 §3，§6 全绿；§5 变异全红（贴结论行）
- [ ] 推分支、报 CI；Claude 沙箱审计（亲手跑变异、逐行读完）；Shiyu 合并

## 9. 补充

本段改动面内新发现的问题直接修并写进这里；需要拍板的停下报告，不自行记账。

### 9.1 发出前自审（Claude，2026-10-05，`0d3d40b0`）

初稿三处缺口，已改进正文：
1. 初稿让 `verbatim_in_normalized = bool(positions)` 且定位要求省略号各段有序 —— 会给现有核对**新增顺序要求**，与 U8「结果与改前一致」自相矛盾（C11 实测倒序节选今天判通过）。改为 `locate_in_normalized`，存在性判定不变。
2. 初稿以首段定位置 —— 「这……下回还清罢」会被定到开篇，恰好把本段要纠正的错放回阶段 1（C12 实测）。改为取最长段，加 M15。
3. 初稿没写「编号合法性过滤」与位置检查、兜底的先后，也没写同阶段多段摘录 —— 补规则 0、4.1，U9、U10，M17、M18。

### 9.2 PG 端口冲突与处置（执行方，2026-10-05）

本机 `55432`（共享 `charsim_test`）已被上一个任务占用，而 §6 的门要求「被占用就停下报告」。
按用户裁定用方案 1：严格对齐 `docker-compose.test.yml` 的配置、只换端口，起一次性容器

```
docker run -d --name cd-test-arc-anchoring -p 55433:5432 --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=charsim -e POSTGRES_PASSWORD=ci_test_password -e POSTGRES_DB=charsim_test \
  postgres:16-alpine
```

`TEST_DATABASE_URL` 指向 55433；本段全部测试跑完后删除该容器（不碰共享的 55432）。

### 9.3 `:2207` 同步分片入口传的 `text`（执行方，2026-10-05）

C2 说 `:2207` 传 `text`，C13 说「一律传原始全文」。同步 `distill_incremental` 里 `text` 在
`text_type="chat"` 时被重新赋值为 L2 预处理后的文本 —— story/classic 判据一致，chat 不一致。
生产没有用同步路径跑 chat 的调用方（路由走 `distill_incremental_stream`），且此处 `text`
正是模型看到的原文，传它自洽。按 spec 字面传 `text`。

### 9.4 变异实测（执行方，2026-10-05）

`tests/perf/arc_phase_anchoring_mutations.py` M1–M30 全部 RED，覆盖域 32 条判别器逐条有撞
（`tests/test_lock_coverage.py` 31 passed）。M17 按 spec 原形（兜底退回含越界编号的原标注）
会让越界编号流入分发、`by_phase[p-1]` 抛 IndexError（崩溃而非判据变红，元锁会记成空转），
改为等价缺陷形态「越界编号连同其摘录夹到末阶段」，照样红 U9 三条。

### 9.5 审计（Claude，2026-10-05，分支 `d63ddae1`）

**复现**：沙箱 PG 16 上 §6 命令 → `239 passed`；`arc_phase_anchoring_mutations.py` EXIT=0，M1–M30 实得全部 RED；`card_draft_mutations.py` EXIT=0；另手跑旧 M3（编号下界 1→0）→ `test_u4_no_valid_number_retracts_the_row_with_a_warning[phases1]` 红，仍有覆盖。三处偏离（M17、M27、U18）认可。§9.3（同步 chat 路径传预处理文本）认可。

**逐文件**：

| 文件 | 结论 |
|---|---|
| `AGENTS.md` | 看过。写了「锚点为空（阶段 1 除外）视为不可定位」，代码没做到（见 A1），A1 修完后成立 |
| `core/card_draft.py` | 看过，通过 |
| `core/distiller.py` | 看过，通过（提示词 L/O、模板、格式说明、3 处调用点） |
| `core/phase_anchoring.py` | 看过，**A1、A2、A3、A4 不通过** |
| `core/quotes.py` | 看过，通过 |
| `core/schema.py` | 看过，通过 |
| `docs/specs/arc-phase-anchoring.md` | 看过，通过 |
| `tests/perf/arc_phase_anchoring_mutations.py` + `_red_lines.json` | 看过并复跑，通过 |
| `tests/perf/card_draft_mutations.py` + `card_draft_red_lines.json` | 看过并复跑，通过 |
| `tests/test_card_draft.py` | 看过，**A5 不通过** |
| `tests/test_distill_task_api.py` | 看过，通过 |
| `tests/test_phase_anchoring.py` | 看过，缺 A1/A2/A3 的用例 |
| `web/routers/distill.py` | 看过，通过（两处传 `content`，`:1126` 走 `to_thread`） |

**A1（违反规则 2）末阶段空锚点不跳过整卡。** `phase_ranges` 对 i>0 的空锚点取 `len(source_norm)`：中间阶段靠递增检查碰巧跳过，**末阶段**不跳过，得到空区间 `[len, len)`，该阶段所有标注被静默去掉。实测（孔乙己原文，两阶段，阶段 2 锚点为空，做法标 [1,2] 且两段摘录各在本阶段）：落卡为「阶段 1 一条、顶层 0 条」；锚点正常时为「顶层 1 条」。修法：i>0 的空锚点与「查不到」同处理，返回原因「阶段 k 锚点为空」整卡跳过。

**A2（监测口径错）`tags` / `dropped` 按摘录计，不按标注计。** 同阶段第一段摘录不通过、第二段通过时，`dropped` 仍 +1；通过之后的摘录不计入 `tags`。实测（阶段 2 两段摘录一错一对 + 阶段 1 一条错标）：输出 `tags=3 dropped=2`，实际 2 个标注、去掉 1 个。修法：`tags` = 被检查的（做法, 阶段）数，`dropped` = 最终不成立的（做法, 阶段）数；`unverified_quotes` 保持按摘录计；整卡跳过时 `tags` 仍计数（比例才有分母）。

**A3（违反规则 3「去掉都打 warning」）** 去掉单个标注时没有 warning，只有兜底和整卡跳过打了。修法：每个不成立的（做法, 阶段）打一条 warning（情境 + 阶段号），由 `verify` 打（与兜底、跳过的 warning 同处）。

**A4（死代码）** `core/phase_anchoring.py` 的 `_first_quote` 无调用方（`grep -n "_first_quote(" core/` 只命中定义与 `card_draft.py` 自己的同名函数）。删掉。

**A5（断言部分空转）** `tests/test_card_draft.py:148-149` 的 `for b in [rows]` 让 `b` 是列表本身，`"occurrences" not in b` 恒真（实测：做法里带 `occurrences`、阶段不带 `anchor` 时整句为 True）。改成两条直写：每条做法的键里没有 `occurrences`，每个阶段的键里没有 `anchor`。

**补的测试与变异（两个方向）**：
- U6 扩：末阶段空锚点 → 整卡跳过；中间阶段空锚点 → 整卡跳过；阶段 1 空锚点 → 不跳过（已有）。变异 M31 放宽：i>0 空锚点退回取 `len(source_norm)`（现状写法）→ 末阶段用例红。变异 M32 过严：阶段 1 空锚点也跳过 → 阶段 1 用例红。
- U7 扩：同阶段两段摘录（一错一对）+ 另一阶段错标 → 精确断言 `tags=2 dropped=1 unverified_quotes=…`。变异 M33：`dropped` 改回按摘录计 → 红；变异 M34：`tags` 改回按摘录计 → 红。
- 规则 3 warning：caplog 断言去掉的每个标注各一条。变异 M35：删去该 warning → 红。
- 以上进 `arc_phase_anchoring_mutations.py`，覆盖闭合由 `test_lock_coverage.py` 核。

### 9.6 A1–A5 修复（Claude 在沙箱直接实现，2026-10-05；Shiyu 授权推分支）

- `core/phase_anchoring.py`：`phase_ranges` 中 i>0 的空锚点返回「阶段 k 锚点为空」整卡跳过（A1）；计数改为按（做法, 阶段），整卡跳过时 `tags` 照常计（A2）；每个不成立的标注打一条「去掉标注」warning（A3）；删死代码 `_first_quote`（A4）。
- `tests/test_card_draft.py`：`test_u9` 断言拆直写（A5）。
- `tests/test_phase_anchoring.py`：新增 U23–U27。`test_u9` / `test_u9b` 原先 `n=3` 却只给了阶段 2 的锚点 —— 依赖了 A1 的错误行为（阶段 3 空区间）；改为 `n=2`，`test_u9` 两阶段都成立，按分发规则进顶层。
- 变异 M31–M36 进 `arc_phase_anchoring_mutations.py`；M18 的锚点随新计数代码改写（语义不变）。
- 红：新用例在修复前的 `d63ddae1` 上 5 条 FAILED（U23–U27）；修复后 §6 全绿；两个驱动 EXIT=0（M1–M36 全 RED）；`test_lock_coverage` 通过。
- 独立性说明：本轮实现与审计同为 Claude，复核由 CI + 执行方读 diff 承担。

### 9.7 单一来源整理（Claude，2026-10-05，分支 `docs/arc-anchoring-single-source`）

纯文档：**不改代码与测试**。目标——每类知识的权威表达只留一处，其余各处指向它。

- **§3.2 补全三条规则**（先读 `core/phase_anchoring.py` 对应代码再写）：规则 2 补「**或为空**」——此前只写「查不到」，与 `phase_ranges` 里 i>0 空锚点返回原因的分支（`core/phase_anchoring.py:66-70`）不符；规则 3 把「去掉都打 warning」明确成「每个不成立的（做法, 阶段）各一条 warning」（`verify` 的 `:142-145`）；规则 6 补计数单位——`tags`/`dropped` 按（做法, 阶段）计、整卡跳过时 `tags` 照常计（`:118`、`:126`），`unverified_quotes` 按摘录计（`:131`）。
- **§4.1 表改为规则→测试的索引**：由「U 编号 | 用例 | 两侧」改为「§3.2 规则号 | 守它的测试函数」，覆盖规则 0–6、§2.3 C5 与调用点矩阵 E1–E5。函数名取自 `grep -n "^def test_" tests/test_phase_anchoring.py` 的实际输出（规则 5 的锁在 `tests/test_card_draft.py`），旧 U 编号不再使用。规则本身不再在 §4.1 复述——唯一权威在 §3.2。
- **§5 表上方加一行**：本表是发出前快照，实施后以 `tests/perf/arc_phase_anchoring_mutations.py` 与 `_red_lines.json`（M1–M36）为准；表体不动。
- §9.1–§9.6 为历史记录，不动。

## 10. 自检表（对照 Shiyu 的标准）

| 标准 | 落在哪 | 状态 |
|---|---|---|
| 事实在最新 main 现读、带坐标、S0 复核 | §2（`0d3d40b0`）、§8 | ✅ |
| 设计问题先给方案、拍板后写 spec | 位置检查放哪 A/B、草稿格式 A/B、核对规则——均已拍板（§1） | ✅ |
| 测试一节固定写法 | §6 | ✅ |
| spec 交 `.md` 文件 | 本文件 | ✅ |
| 审计逐文件清单 | 审计时执行 | — |
| 新问题不自行记账 | §9 | ✅ |
| 路径机制表 | §2.1（发现 `/run_stream` 在事件循环上，改 `to_thread`） | ✅ |
| 真实规模算一遍 | §2.2、C9（整本 0.29s） | ✅ |
| 样本前提写明范围 | §7；U 用例用公版孔乙己 | ✅ |
| ① 出处对照表（含性质：文献 / 代码 / 自研） | §2.3 | ✅ |
| ② 全量扫描原文 | 附录 A | ✅ |
| ③ 规模表 | §2.2 | ✅ |
| ④ 调用点矩阵 | §4.2 | ✅ |
| 每条边界两侧都测；变异含放宽与过严两方向 | §4.1「两侧」列；§5「方向」列 | ✅ |
| 变异发出前先实跑 | 纯计算层 16 条变异已在沙箱原型上预跑，全部打红（§5.1）；接线相关的 5 条审计时跑 | ✅（部分，范围已写明） |
| 先找现成库 / 先搜代码库 | 复用 `core/quotes.py` 的规范化与分段（C5），只加一个返回位置的函数并让原函数调它；不引入模糊匹配库（D4） | ✅ |
| 修改不夹带行为变化 | 编号合法性过滤原样保留（规则 0）；`verbatim_in_normalized` 判定不变，不新增顺序要求（C11、U8、M16） | ✅ |
| 不打补丁、隔离、抽象、复用 | 新逻辑独立成 `core/phase_anchoring.py`；字段用共用底 `BehaviorCore`；匹配只写一份；出口仍唯一 | ✅ |
| skill 用上不过度 | 执行方：`@search-first`、`@verification-before-completion` | ✅ |

### 附录 A：全量扫描输出（`0d3d40b0`）

```
$ git grep -n -I -E "card_from_draft\(|phases|source_quote|anchor|occurrences" -- core/card_draft.py core/schema.py core/distiller.py core/quotes.py core/card_quotes.py web/routers/distill.py
core/card_draft.py:26:    """情境→行为：此人遇到某类情境时的具体做法；phases 是这个做法出现过的阶段编号（从 1 开始）。"""
core/card_draft.py:27:    phases: list[int] = []
core/card_draft.py:32:    phases: list[PhaseState] = []
core/card_draft.py:64:def card_from_draft(data: Any) -> CharacterCard:
core/card_draft.py:69:    没有阶段的卡，所有做法放顶层，`phases` 不看。
core/card_draft.py:72:    count = len(draft.character_arc.phases)
core/card_draft.py:77:        # 带着 phases 也无妨：存卡的 SituationBehavior 不收这一项，校验时丢掉。
core/card_draft.py:82:        valid = sorted({p for p in row.phases if 1 <= p <= count})
core/card_draft.py:83:        if len(valid) != len(set(row.phases)) or not valid:
core/card_draft.py:85:                           count, row.phases, row.situation)
core/card_draft.py:95:    for phase, behaviors in zip(card["character_arc"]["phases"], by_phase):
core/card_quotes.py:39:    "character_arc.phases[].behaviors[].behavior",
core/card_quotes.py:46:    "situation_behaviors[].source_quote",
core/card_quotes.py:47:    "character_arc.phases[].behaviors[].source_quote",
core/distiller.py:190:        'L. 角色弧线：此人从故事开始到结束，心态或立场发生了怎样的变化？无明显变化则 axis 留空、phases 输出空数组 []。\n'
core/distiller.py:192:        '   - phases：按故事顺序排列。只在心态或立场确实变了的地方分段，通常2-3个、最多4个；同一种心态下的不同场景不算新阶段。每个阶段都必须是此人还在场、还能与人交谈的时期，死亡、失踪、离场不单列为阶段。\n'
core/distiller.py:195:        '   - 阶段按 phases 的顺序从 1 开始编号，维度 O 用这个编号。'
core/distiller.py:208:        '   - source_quote：从原文原样复制一小段（10-40字）体现这个做法；找不到逐字原文就留空字符串。\n'
core/distiller.py:209:        '   - phases：原文里此人在哪几个阶段这样做过，填维度 L 的阶段编号（如 [1, 2]）。只填原文里确实这样做过的阶段，不要因为性格没变就把其余阶段也填上。同一类情境在不同阶段做法不同的，分成两条，各标各的阶段。维度 L 的 phases 为空时，phases 写空数组 []。'
core/distiller.py:254:        '    "phases": [{"label": "阶段心态", "state": "故事时期，此时的心态与行事方式"}]\n'
core/distiller.py:259:        '    {"situation": "一类情境", "behavior": "具体做法", "source_quote": "原文摘录", "phases": [1, 2]}\n'
core/distiller.py:290:    ("G6", '- character_arc 是【对象】，含 axis 与 phases；phases 的每个元素是【对象】，含 label/state'),
core/distiller.py:291:    ("G6", '- situation_behaviors 的每个元素是【对象】，含 situation/behavior/source_quote/phases；phases 是整数数组'),
core/distiller.py:1497:            return card_from_draft(data)
core/distiller.py:1606:            return card_from_draft(data)
core/distiller.py:2207:            card = card_from_draft(data)
core/schema.py:78:    source_quote: str = ""   # 原文摘录，只用于展示；落卡前核对，查不到即清空
core/schema.py:108:        """旧卡的弧线是阶段列表 → 当作没有 axis 的 phases。"""
core/schema.py:109:        return {"phases": value} if isinstance(value, list) else value
core/schema.py:114:    phases: list[ArcPhase] = []
web/routers/distill.py:507:            card = card_from_draft(data)
web/routers/distill.py:1126:            card = card_from_draft(data)

$ git grep -l -I -E "\"phases\": \[|phases=\[|\.phases|source_quote" -- tests
tests/perf/card_draft_mutations.py
tests/perf/card_draft_red_lines.json
tests/test_card_arc_behaviors.py
tests/test_card_draft.py
tests/test_distiller_routing.py
```

### 9.2 按 Shiyu 准则复查（Claude，2026-10-05）

- 受影响测试漏列：`verbatim_in_normalized` 的全部调用方（`git grep` 全量）为 `core/card_quotes.py:98/117`、`core/quotes.py:75`；直接测它的是 `tests/test_quotes.py`、`tests/test_distiller_dialogue_pick.py`，§6 原先漏列，已补。
- 出处表漏列本轮新增的自研规则，已补 4 行并标性质。
- 整本规范化做两次：已写进 §2.2 规模表，作为已知代价。
- 变异预跑：M1–M18 中，只有涉及现有行为的判据可以在基线上预跑（倒序节选判通过：`0d3d40b0` 实测为 True）；其余变异要等实现后才有代码可跑，审计时在沙箱亲手跑并贴输出。
- 已定（Shiyu 选 A）：去掉「< 4 字」阈值，改为「最长段出现 > 3 次不作位置证据」，N 依据与样本范围见 §2.2；补 U3 两侧与 M19。

