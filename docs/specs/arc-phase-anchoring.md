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
C9. 实测（沙箱）：`normalize` 120 万字整本 0.29s；在规范化全文里找一个短串的全部位置约 1ms。
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
| 摘录长度 | 10–40 字 | 维度 O | 太短（规范化后 < 4 字）不作位置证据，按「核对不上」处理 |
| 原文 | 小说 < 90 万 tokens（约 120–130 万字） | `core/length_budget.py` | 每次转换只 normalize 一次整本（C9：0.29s） |
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

## 3. 设计

### 3.1 结构（隔离与复用）

- **`core/schema.py`**：抽出共用底 `BehaviorCore{situation, behavior}`；`SituationBehavior(BehaviorCore)` 加 `source_quote`（存卡不变）。理由：草稿做法不应再继承 `source_quote`（C3），用共用底而不是复制字段，做法同已有的 `PhaseState` / `ArcAxis`。
- **`core/card_draft.py`**（草稿契约，改）：
  - `DraftOccurrence{phase: int, quote: str}`；
  - `DraftBehavior(BehaviorCore)` + `occurrences: list[DraftOccurrence]`（删去 `phases`）；
  - `DraftPhase(PhaseState)` + `anchor: str = ""`；`DraftArc.phases: list[DraftPhase]`；
  - `card_from_draft(data, source_text)`：校验草稿 → 调 `phase_anchoring.verify(draft, source_text)` 得到每条做法的最终阶段 → 按原规则分发（全阶段 → 顶层，否则挂所标阶段）→ 存卡的 `source_quote`：挂在阶段 k 下用阶段 k 那段摘录，顶层用第一段。
- **`core/phase_anchoring.py`**（新，纯计算，无 IO）：只做「锚点 → 阶段范围」「摘录位置 → 标注是否成立」「兜底」「监测计数」，返回结果对象，不改草稿、不碰存卡。
- **`core/quotes.py`**（加一个函数，复用现有规范化）：`positions_in_normalized(source_norm, quote) -> list[int]`，返回摘录在规范化全文中的全部起点（按省略号分段时，后续各段须在前一段之后出现）。`verbatim_in_normalized` 改为调用它（`bool(positions)`），**同一套匹配只写一次**。

### 3.2 规则

1. **阶段范围**：在规范化全文中找每个锚点的位置；阶段 k 的范围是 `[锚点 k 位置, 锚点 k+1 位置)`，阶段 1 锚点为空时从 0 开始，最后一阶段到全文末尾。锚点重复出现时取第一次出现。
2. **整卡跳过（D7）**：任一锚点（阶段 1 空锚点除外）查不到，或锚点位置不严格递增 → 整卡不做位置检查，保留模型标注，warning 一条（含卡名与原因）。
3. **单条标注**：摘录规范化后 < 4 字或查不到 → 该标注去掉；有任一位置落在所标阶段范围内（D6）→ 保留，若位置数 > 1 记「位置不唯一」；否则去掉。去掉都打 warning。
4. **兜底（D5）**：一条做法的标注全部被去掉 → 退回它原来的全部标注，warning。
5. **无阶段的卡**：不做位置检查，做法全放顶层（同现状），`source_quote` 取第一段摘录。
6. **监测（D8）**：每张卡转换后打一行结构化 INFO：`[phase_anchoring] card=… tags=… dropped=… ambiguous=… unverified_quotes=… fallback=… skipped_card=…`。

### 3.3 提示词（`core/distiller.py`）

- 维度 L：每个阶段加 `anchor`——原文中标志这一阶段开始的一句（10–40 字，逐字照抄）；阶段 1 可留空。
- 维度 O：删去 `phases` 与 `source_quote` 两条说明，换成 `occurrences`——这个做法在哪些阶段出现过，每个阶段各给一段**该阶段里**的原文摘录（10–40 字，逐字照抄），写成 `[{"phase": 1, "quote": "…"}]`；维度 L 没有阶段时只写一条，`phase` 填 0。
- G6 模板（`:259`）与格式说明（`:291`）同步。

### 3.4 调用点接线

5 处调用点全部改为 `card_from_draft(data, <手上的原文>)`；`:1126` 改走 `asyncio.to_thread`（§2.1）。不新增第二个出口。

## 4. 测试计划（先在 `0d3d40b0` 上红、再在本分支上绿；每条边界两侧都测）

### 4.1 单元（`tests/test_phase_anchoring.py`，新）

| 编号 | 用例 | 两侧 |
|---|---|---|
| U1 | 范围：锚点 2 的位置恰属阶段 2，前一字属阶段 1 | 边界两侧各一 |
| U2 | 摘录落在所标阶段 → 保留；落在别的阶段 → 去掉 | 收 / 拒 |
| U3 | 摘录查不到、规范化后 < 4 字 → 去掉 | 拒（另有 4 字恰好通过一条） |
| U4 | 重复摘录：一处在范围内 → 保留并记不唯一；全不在范围内 → 去掉 | 收 / 拒 |
| U5 | 兜底：全部被去掉 → 退回原标注；只去掉部分 → 不退回 | 两侧 |
| U6 | 锚点查不到 / 顺序颠倒 → 整卡跳过；锚点正常 → 不跳过 | 两侧 |
| U7 | 监测行字段齐全且计数正确（caplog） | —— |
| U8 | `positions_in_normalized`：省略号分段要求顺序；`verbatim_in_normalized` 结果与改前一致（`core/quotes.py` 现有测试全部保持绿） | 两侧 |

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

| 编号 | 方向 | 变异 | 应红 |
|---|---|---|---|
| M1 | 放宽 | 位置检查恒通过（所有标注保留） | U2 拒侧、E1–E5 |
| M2 | 过严 | 位置检查恒不通过（所有标注去掉，兜底后全退回） | U2 收侧 |
| M3 | 过严 | 删兜底（全部去掉时整条消失） | U5 |
| M4 | 放宽 | 兜底在部分去掉时也退回 | U5 另一侧 |
| M5 | 放宽 | 锚点失败也照常检查（不跳过） | U6 |
| M6 | 过严 | 锚点正常也整卡跳过 | U6 另一侧、E1 |
| M7 | 过严 | 重复摘录要求**所有**位置都在范围内 | U4 收侧 |
| M8 | 放宽 | 重复摘录不检查范围直接通过 | U4 拒侧 |
| M9 | 边界 | 范围用 `<=`（锚点位置归前一阶段） | U1 |
| M10 | 接线 | 某一调用点传空串作原文 | 对应 E 行 |
| M11 | 取值 | 阶段 k 的 `source_quote` 一律取第一段 | 矩阵第二列 |
| M12 | 上下文 | `:1126` 去掉 `to_thread` | E5 线程断言 |
| M13 | 监测 | 不打监测行或计数错 | U7 |
| M14 | 复用 | `verbatim_in_normalized` 不再走 `positions_in_normalized`（另写一份匹配） | S2、U8 |

驱动基于 `tests/perf/mutation_framework.py`，产物 `tests/perf/arc_phase_anchoring_red_lines.json`（`tests/test_lock_coverage.py` 要求）。旧驱动 `card_draft_mutations.py` 里引用 `phases` 的锚点随草稿格式同步更新，其产物重新生成。

## 6. 本地命令（只跑受影响的文件；合并门是分支 CI；库只用 PG）

```powershell
docker ps --format "{{.Names}} {{.Ports}}" | Select-String "55432"   # 被占用就停下报告
docker compose -f docker-compose.test.yml up -d --wait
python -m pytest -q tests/test_phase_anchoring.py tests/test_card_draft.py tests/test_card_arc_behaviors.py tests/test_distiller_routing.py tests/test_card_quotes.py tests/test_distill_task_api.py tests/test_lock_coverage.py
python tests/perf/arc_phase_anchoring_mutations.py
python tests/perf/card_draft_mutations.py
cd web/frontend; npm test
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
| 变异发出前先实跑 | 本段由执行方实现，Claude 无法在 spec 前预跑；改为审计时在沙箱亲手复跑并贴输出 | ⚠️ 说明 |
| 先找现成库 / 先搜代码库 | 复用 `core/quotes.py` 的规范化（C5），只加一个返回位置的函数并让原函数调它；不引入模糊匹配库（D4） | ✅ |
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
