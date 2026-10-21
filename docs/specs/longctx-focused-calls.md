# 长上下文专注调用（分段 1：抽出共用的按组格式化）

- 基线：main `9200a502`（S0 于 2026-10-20 复核）。
- 跟踪：「长上下文专注调用」10-20 19:05–20:15。
- 实现 skill：`@tdd`。
- 本文件两段共用。**本 PR 只做段 1（行为不变的抽取）**；段 2 在段 1 合并后的 main 上另开 PR，改动见 §8。

## 0. 问题与根因

一次读完路径（书 ≤ 90 万 token，线上所有书都走这条）现在一次调用出整张卡。SG 两张有草稿的卡（同一本书）显示：主调用里每条取值几乎只写 1 个 occurrence（47/47、46/49），摘录遍布第 13–506 章，但标的阶段几乎全是同一个（1 / 2）。关系态度是单独调用、先给阶段列表，阶段号与摘录章号一致。已排除「只取了前半本」「弧线后生成」「写法」三种解释（跟踪文档 10-20 19:05–20:15；`f3a0098b` 的提示词已要求每阶段各给一段，仍堆）。

结论：一次输出 17 个字段、每字段带规则时，阶段规则被挤掉。应按弧线 → 阶段×类别拆成专注调用。

### 出处表（原文照抄）

| 文献 | 原文 | 用在 |
|---|---|---|
| ExtractBench, arXiv 2602.12247 | “output volume, not input length or nesting depth”（结论节）；369 字段 schema “0% valid output across all frontier models” | 一次输出越少越可靠 → 拆成专注调用 |
| How Many Instructions Can LLMs Follow at Once?, NeurIPS 2025, arXiv 2507.11538 | 摘要：“bias towards earlier instructions”；性能随 instruction density 下降 | 一次 17 个字段、每字段带规则，阶段规则被挤掉 |
| ArcANE, arXiv 2606.05553 §3 / §3.2 | “The construction proceeds in two stages”；“For each (target phase, category) pair…” | 先定弧线，再按阶段×类别生成 |

链接：https://arxiv.org/abs/2602.12247 ・ https://arxiv.org/abs/2507.11538 ・ https://arxiv.org/abs/2606.05553

## 1. 目标检查（第一个提交，`xfail(strict=True)`）

| # | 检查 | 测的是目标 |
|---|---|---|
| C1 | 结构锁：`core/distiller.py` 里 `FORMAT_GROUPS` 的组循环与 `PHASE_DEPENDENT_GROUPS` 的代码引用，只出现在新抽出的共用方法内（AST 扫 Name/Attribute，不扫注释） | 「按组格式化」只有一处实现 |
| C2 | 行为锁：替身 LLM 跑 F1（分组·流式），记录每次组调用的 `(system, messages)` 与 formatting 之后的帧序列，与改前录下的 fixture **逐字相等** | 抽取不改变提示词、调用序列与 yield 序列 |

C2 在基线本来就绿（它是回归护栏，不是「达成目标」检查）；C1 在基线必红。实现后 C1 去掉 `xfail` 标记转绿，C2 保持绿。

## 2. 已查实的约束（@ `9200a502`，本轮逐行读过）

**产卡格式化现有 4 处实现（同一件事写了 4 遍）：**

| # | 路径 | 坐标 | 做法 | 生产调用方 |
|---|---|---|---|---|
| F1 | 分组·流式 | `core/distiller.py:2860-2958`，闭包 `_format_one_group` `:2873` | `system = DISTILL_PROMPT_BEFORE_NAME + name + format_prompt_after(group) + (phase_list_note…) + sub_schema`；非依赖组先并行 → 等 G6 → `PHASE_DEPENDENT_GROUPS` 带 `_phase_list_note(_arc_phases(group_data["G6"]))` → 按 `FORMAT_GROUPS` 顺序 `merged.update` → `_relationships_batched(merged, prefix=format_prompt_shared(name), material=format_input)` → `CardDraft.model_validate` → `yield json.dumps(draft.model_dump())` | 后台 `/start`（`web/routers/distill.py:382`）、SSE（`:1188`） |
| F2 | 分组·同步 | `core/distiller.py:2562-2590` | `system = DISTILL_PROMPT_BEFORE_NAME + name + DISTILL_PROMPT_AFTER_NAME + schema_str`（全量模板，一次）；`action="distill_format"`；关系分批 `prefix=format_prompt_shared(name)`、`material=profile_draft` | `TextManager.get_or_distill`（`core/text_manager.py:475`；`web/routers/distill.py:789`、`:1801`） |
| F3 | 一次读完·同步 | `_distill_longcontext` `:1923`（调用点 `:2458`）+ `_longcontext_prompt` `:1903` | `system = book_prefix(text) + "\n\n" + distill_instruction(name)`（全量模板，一次）→ `_relationships_batched(prefix=book_prefix(text), material="")` → `card_from_draft` | 同 F2 |
| F4 | 一次读完·流式 | `_distill_longcontext_stream` `:1944`（入口 `:2643`） | 同 F3 的提示词，逐 token yield，自己不解析 | 后台 `/start`（`web/routers/distill.py:430-488` 拼串解析 → `fill_relationships` `:483`）、SSE（`:1220-1240` → `:1234`） |

**分组与阶段依赖：**

| # | 事实 | 坐标 |
|---|---|---|
| F5 | `"G6": ("character_arc", "situation_behaviors", "key_memories")`；上方注释称「做法/记忆标的是阶段编号，必须和阶段在同一次调用里产出」 | `core/schema.py:344-346` |
| F6 | `PHASE_DEPENDENT_GROUPS` 由登记表推导：`g != _PHASE_SOURCE_GROUP and _has_phase_leaves(fields)`；`_PHASE_SOURCE_GROUP` = 含 `character_arc` 的组 | `core/distiller.py:441-445` |
| F7 | 维度 owner：E（关键记忆）`:271`、L（弧线）`:291`、O（情境→行为）`:310` 都是 `"G6"`；`_FORMAT_IMPORTANCE` 的弧线形态 `:418`、situation/memory 对象形态 `:419`、occurrences 的 phase 规则 `:420` 也是 `"G6"` | `core/schema.py` |
| F8 | `format_prompt_after(group)` 只按组拼提示词；`draft_schema(group)` 只按组取子 schema | `core/distiller.py:448-476`；`core/card_draft.py:205-221` |
| F9 | `book_prefix(text)` 是缓存前缀（SG 实测同书后续调用整段命中 816,640 token） | `core/distiller.py:496-502` |
| F10 | `fill_relationships` 只为 F4 的消费方存在；`_relationships_batched` 只处理「有 target、没 relation」的条目，已补全直接返回 | `core/distiller.py:1166-1174`、`:1048-1068` |
| F11 | `distill` `:1807`、`distill_stream` `:1840`（短文本截断模式）也用全量模板；`core` / `web` / `mcp_server` / `scripts` 里没有生产调用方（grep 只见 tests） | 本段不改（返工经验第 37 条） |
| F12 | 转卡时按摘录位置改挂已存在：`phase_anchoring.py:421`（`card_from_draft` 内）；本段不动，作兜底 | `core/phase_anchoring.py:400-425` |
| F13 | 结构锁现状：`tests/test_failure_alerting.py:439` 按函数名登记 `_format_one_group`；`tests/perf/card_draft_mutations.py` 的 M24–M28 锚点落在 F1 正文里；`tests/test_mutation_anchors.py` 要求每个锚点在当前代码上恰命中一次 | 本轮读过 |
| F14 | `PHASE_DEPENDENT_GROUPS == {"G2", "G3", "G4"}` 有直写断言 | `tests/test_arc_phase_fields_unit.py:432-434` |

**S0 两处与任务书不一致的核查结果：**

- `claude/handoff/B2-phase-where-check.md` 在基线仓库、`origin/main` 与全部本地历史里都不存在（`git log --all -- '*B2-phase-where-check.md'` 与 `git ls-tree -r origin/main` 均零命中）。它只用于段 2 合并后的真模型小重放，**段 1 不受影响**；段 2 前由 Tracy 提供或改口径。
- `distill` / `distill_stream` 的生产调用方：`grep` 未发现，只有 tests。与任务书一致。

## 3. 改动（段 1）

1. `core/distiller.py` 新增一个方法（名 `_format_groups`）承载 F1 的整段流程：组线程 → 等 G6 → 依赖组带阶段列表 → 按 `FORMAT_GROUPS` 顺序合并 → `_relationships_batched` → `CardDraft.model_validate`。
   - 参数只含路径间真正不同的东西：`character_name`、每组的 `user_message`、`system_prefix`、关系分批的 `rel_prefix` / `rel_material`、记账 `action`。
   - 方法内 `yield {"heartbeat": True}`（每组回来一次），**返回**校验后的 `CardDraft`。错误经模块私有 `_FormatGroupError(group, cause)` 上抛，由调用方决定上屏形态（F1 仍是 error 帧）。
   - `_format_one_group` 保持原名嵌套在新方法内：`tests/test_failure_alerting.py:439` 的豁免键不失效。
2. F1 改为 `draft = yield from self._format_groups(...)`，其余（`formatting` 帧、校验/关系失败的错误帧、最后一个草稿 JSON 串）原样留在 F1。
3. 新增 `tests/test_longctx_focused_calls_goal.py` 与 `tests/fixtures/longctx_focused_calls_f1_baseline.json`（C1/C2）。
4. 结构变化后失效的变异锚点 / 源码锁按新结构改（逐个见 §5），不删红源。

### 结构自检

| 问题 | 现在 | 本段后 |
|---|---|---|
| 「按组格式化 → 阶段依赖 → 合并 → 关系分批 → 校验」写了几处 | 4 处（F1–F4） | 1 处共用方法；4 个调用方只传参数（本段只接 F1，段 2 接其余三个） |
| 调用方是否被迫知道不该知道的事 | 是（路由要知道 F4 草稿缺关系） | 段 2 去掉；本段不改调用方 |
| 新增一个按阶段的字段要改几处 | 登记表 + 放进某组；一次读完路径仍被全量模板一次带出 | 段 2 后：登记表 + 放进某组，依赖由 `PHASE_DEPENDENT_GROUPS` 自动推导 |

## 4. 不做（段 1）

- 不改 `core/schema.py` 的分组（G6 仍含三个字段）、不改任何提示词文本、不改 `card_from_draft` / `phase_anchoring` / `relationship_batch` 的口径、不改 API 返回格式。
- 不删 `fill_relationships`、不改 F2/F3/F4（段 2）。
- 不调真模型、不花钱。

## 5. 对账表（复核方必验；变异由独立复核方跑并自补）

| 变异 | 方向 | 应红 |
|---|---|---|
| 把组循环或 `PHASE_DEPENDENT_GROUPS` 的消费复制回 F1（新方法保留一份） | 结构 | C1 |
| 任一组提示词改一个字（如 `format_prompt_after(group)` 换 `format_prompt_after()`） | 行为 | C2 |
| 每组 user 消息改一个字 | 行为 | C2 |
| 心跳少发/多发一次，或 `formatting` 帧挪到组调用之后 | 行为 | C2 |
| 依赖组不等 G6（去掉 `_phase_list_note` 或提前启动） | 行为 | C2（依赖组 system 缺阶段列表）、`tests/test_distiller_routing.py::TestFormatGroupsRunInParallel` |
| 合并顺序改成完成顺序 | 行为 | C2（草稿字段被后到组覆盖时可见） |
| 关系分批的 prefix/material 换掉 | 行为 | C2（若替身造出待补关系；本 fixture 用全量关系名单，故另由 `tests/test_arc_phase_fields_entries.py` 守调用点） |

**因结构变化需要同步改的锁（逐处理由）：**

- `tests/perf/card_draft_mutations.py` M24–M28：锚点落在 F1 正文，抽取后若命中次数不再是 1，按新位置改锚点文本，**变异意图不变**（M24 每组交一段 JSON；M25 草稿坏；M26 不校验；M27 错误帧键名；M28 在 distiller 里转卡）。M28 的替换代码改用新结构下可见的变量（`draft.model_dump()`），仍复现「occurrences 丢失」的缺陷形态。实际改动：M26 锚点由 12 空格缩进的 `draft = CardDraft.model_validate(merged)` 改为新方法里的 8 空格 `return CardDraft.model_validate(merged)`（命中 1 次）；M24、M25、M27 锚点原样命中 1 次；M28 替换改为 `card_from_draft(draft.model_dump(), text, retro=NOT_DETECTED)`。
- `tests/test_arc_phase_fields_entries.py::_assert_batched`：E14 原先断言 `distill_incremental_stream` 函数体内直接含 `_relationships_batched(`；抽取后调用点搬进 `_format_groups`。改为「直接调 **或** 委托 `_format_groups` 且该方法体含调用」，红源不删：入口不再委托、或共用方法丢掉关系分批，断言仍红。
- C1 的 `FORMAT_GROUPS` 扫描只数 `for … in FORMAT_GROUPS` 的组循环（`loops_only=True`）：`format_prompt_after` 里有 `group not in FORMAT_GROUPS` 的成员校验，那不是组循环/消费点；`PHASE_DEPENDENT_GROUPS` 仍数全部引用。
- `tests/test_arc_phase_fields_locks.py::test_s14_batch_prefix_only_from_shared_prefix`：共用的 `_format_groups` 把前缀作参数 `rel_prefix` 收进来，`_relationships_batched(prefix=…)` 不再是直接调用产出者。锁改为：直接调用仍须是 `book_prefix` / `format_prompt_shared`；若是 `rel_prefix`，则每个 `_format_groups(rel_prefix=…)` 调用点传的也必须是这两个产出者。判据（分批前缀只能出自共享前缀函数）不变。
- `tests/test_exception_pickle_lock.py`：`_FormatGroupError` 带自定义字段（组名 + 原始异常），登记进 `_REGISTRY` 并补 `__reduce__`（`cls(group, cause)` 可重建）；测试侧的 `_PickleCause` 只解决「`BaseException` 按身份比较、`vars()` 比不过去」的比对问题。
- C2 fixture 里保存改前的期望值；C1 的 AST 扫描按新方法名更新。除这五处外不改测试判据。

## 6. 测试

只跑受影响文件：`tests/test_longctx_focused_calls_goal.py tests/test_distiller_routing.py tests/test_arc_phase_select.py tests/test_arc_phase_fields_unit.py tests/test_arc_phase_fields_entries.py tests/test_card_draft.py tests/test_distill_usage_accounting.py tests/test_failure_alerting.py tests/test_mutation_anchors.py`（库用 Docker 起的 55432 测试库）。推送前 `scripts/check.sh preflight`。全量看分支 CI。

## 7. 未核实

- C2 在 CI 机器上是否同样稳定（替身按组回包、无网络）。
- `B2-phase-where-check.md` 不存在（见 §2 末）。

## 8. 段 2：一次读完路径按组专注调用（本 PR）

- 基线：main `eaba2fab`（#197 合并提交；S0 于 2026-10-20 复核 `git merge-base --is-ancestor 3021127 origin/main` 为真）。
- 目标：一次读完路径（F3/F4）不再一次调用出整张卡 —— 弧线单独一次调用，其余字段按组各一次、依赖组带阶段列表；F1–F4 共用 `_format_groups`。修 P3「标注堆在一个阶段」；根因与出处沿用 §0，不改。

### 8.1 现状复核（@ `eaba2fab`，本轮逐行读过）

| # | 事实 | 实际读到的行 |
|---|---|---|
| S2-1 | G6 仍含三字段，注释要求同一次调用 | `core/schema.py:346` `"G6": ("character_arc", "situation_behaviors", "key_memories"),`；`:344-345` `# 弧线与情境→行为、关键记忆同组：做法/记忆标的是阶段编号，必须和阶段在同一次调用` / `# 里产出…` |
| S2-2 | 维度 E / L / O 的 owner 都是 G6；`_FORMAT_IMPORTANCE` 三行也是 | `core/distiller.py`：`("G6", 'E. 关键记忆…`（:271）、`("G6", 'L. 角色弧线…`（:291）、`("G6", 'O. 情境→行为…`（:310）；`:418` 弧线形态、`:419` situation/memory 对象形态、`:420` occurrences 的 phase 规则均 `"G6"` |
| S2-3 | 依赖组由登记表推导，与任务书一致 | `core/distiller.py:441` `_PHASE_SOURCE_GROUP = next(g for g, fields in FORMAT_GROUPS.items() if "character_arc" in fields)`；`:442-445` `g != _PHASE_SOURCE_GROUP and _has_phase_leaves(fields)`。`core/card_layers.py:77` `"key_memories": FieldSpec("experience", …)` → 新 G7 自动成为依赖组 |
| S2-4 | `_format_groups` 现有调度：非依赖组先并行 → 等 G6 → 依赖组带阶段列表 | `core/distiller.py:2621` 起；`for group in FORMAT_GROUPS: if group not in PHASE_DEPENDENT_GROUPS: …start()` → `while _PHASE_SOURCE_GROUP not in group_data:` → 依赖组 `kwargs={"phase_list_note": _phase_list_note(_arc_phases(...))}` |
| S2-5 | F3 一次读完·同步仍走全量模板 | `:1938` `system_prompt, user_messages = self._longcontext_prompt(text, character_name)`；`:1941` `_chat_accounted(..., "整本蒸馏", "distill_longcontext", max_tokens=self.LONG_OUTPUT_MAX_TOKENS)` |
| S2-6 | F4 一次读完·流式只透传 token，不解析 | `:1959` 同一 prompt 构造函数；`yield {"status": "formatting"}`、逐 token、`if tc % 50 == 0: yield {"heartbeat": True}` |
| S2-7 | F2 同步分组仍走全量模板 | `:2583-2585` `DISTILL_PROMPT_BEFORE_NAME + character_name + DISTILL_PROMPT_AFTER_NAME + schema_str`；`:2593` `_chat_accounted(..., "最终格式化", "distill_format")` |
| S2-8 | 两处消费方在转卡前补关系 | `web/routers/distill.py:483` `distiller.fill_relationships(data, content, name)`；`:1234` `await asyncio.to_thread(distiller.fill_relationships, data, content, char_name)`（各带两行注释，注释引用的 `_longcontext_prompt` 本段删除） |
| S2-9 | `fill_relationships` 只为 F4 的消费方存在 | `core/distiller.py:1179`；方法体只是 `return self._relationships_batched(draft, prefix=book_prefix(text), character_name=…, material="")` |
| S2-10 | 清理后无调用方的判定（grep 原始输出） | `distill_instruction` 仅 `_longcontext_prompt`（`:1932`）调用 → 删；`DISTILL_PROMPT_AFTER_NAME` 另有 `:1833`、`:1866`（`distill` / `distill_stream`）→ 保留；`LONG_OUTPUT_MAX_TOKENS` 在 `:2368`、`:2396`、`:2414` 归并路径仍在用 → 保留 |

**任务书清单与 grep 的两处偏差（按「以 grep 原始输出为准」处理）：**

1. `fill_relationships` 的测试替身不是三个，是 **5 个**：`tests/test_card_redraft_goal.py:66`、`tests/test_distill_draft_persist.py:94`、`tests/test_distill_task_api.py:284`、`tests/test_identify_failure_channels.py:630`、`tests/test_phase_anchoring.py:513`；另有 2 处单测直接调用（`tests/test_arc_phase_fields_unit.py:666`、`:837`）与 1 处诊断日志函数名单（`tests/test_distill_diagnostic_logs_goal.py:281`）。全部处理，逐个见 8.6。
2. 旧分组/调度有 5 个测试文件直接写死 G6 三字段或「首批 = 非依赖组」：`tests/test_arc_phase_select.py`（U9 `:88`、U20 `:154-200`）、`tests/test_distiller_routing.py`（marker `:791-798`、并行屏障 `:883` 起、提示词顺序 `:354-401`）、`tests/test_card_arc_behaviors.py:60` 起、`tests/test_card_draft.py`（U8 `:137` 起、G6 模板 `:369`）、`tests/test_distill_output_bounds.py:87` 起、`tests/test_pre_story_memory_goal.py:125` 起。本地清单内的一次性改到位；清单外的静态改到新结构，全量确认只看分支 CI（8.8）。

### 8.2 改动

1. **分组**（`core/schema.py`）：`G6 = ("character_arc",)`；新 `G7 = ("situation_behaviors", "key_memories")`；`:344-345` 注释改为新依据（弧线先单发定阶段；做法/记忆等它回来再发）。维度 E、O 与 `_FORMAT_IMPORTANCE` 的 `:419`、`:420` owner 改 `"G7"`；`:418`（弧线形态）留 `"G6"`。`_FORMAT_FIELD_GROUP` 由 `FORMAT_GROUPS` 派生，自动跟随。
2. **调度，一处规则**（`_format_groups`）：**弧线组单独先发**，返回后其余所有组并行（`PHASE_DEPENDENT_GROUPS` 附 `_phase_list_note(_arc_phases(...))`，其余不附）。理由（规模，返工经验第 11 条）：一次读完的每次调用都带约 82 万 token 书文前缀；回忆区按书缓存命中时（`core/retro_cache.py:27-29`）蒸馏前没有预热调用，若首批 3 组并行可能 3 次都按未命中计费（每次约 $0.12–0.25），先单发弧线组则其后各组按命中计费（每次约 $0.0025–0.005）。分组路径（F1）同受此规则，它线上 0 次调用，代价只是 G1/G5 多等一次弧线。
3. **接入**：
   - 抽一个 F1/F4 共用的上屏协议方法 `_format_groups_stream`：`{"status": "formatting"}` → 每组一次心跳 → 一个草稿 JSON 串（失败一个 error 帧，口径与段 1 的 F1 相同）。F1 与 F4 都只传参数。理由：同一协议写两遍会让 M25/M27/M28 的锚点各命中两次，且错误帧口径必然漂移。
   - F4（`_distill_longcontext_stream`）：`system_prefix = book_prefix(text) + "\n\n"`、user = 「请基于以上全文为「{name}」输出模板中的字段」、`rel_prefix = book_prefix(text)`、`rel_material = ""`、`action = "distill_longcontext"`。
   - F3（`_distill_longcontext`）：同 F4 的参数，同步消费同一生成器（丢弃心跳、取返回的 `CardDraft`）；`card_from_draft(draft.model_dump(), text, retro=NOT_DETECTED)`。`_FormatGroupError` 解包成原因异常抛出（类型与改前直接抛出的相同）；`ValidationError`（含 `CardDraft.model_validate` 与 `card_from_draft`）→ 现有 `DistillError("蒸馏失败：LLM 返回格式不正确，请重试", …)`。
   - F2（`distill_incremental` Phase 3）：参数同 F1（user 模板同 F1、`rel_material = profile_draft`、`rel_prefix = format_prompt_shared(character_name)`），`action = "distill_format"`；同步消费与 F3 同一个解包方式。
   - `TextManager.get_or_distill`（`core/text_manager.py:433` 起）不改。
4. **清理**：删 `fill_relationships`（`core/distiller.py:1179`）与路由 `:483`、`:1234` 两处调用（含其上两行注释）；删 `_longcontext_prompt` 与因此无调用方的 `distill_instruction`；`DISTILL_PROMPT_AFTER_NAME` 保留（S2-10）。5 个测试替身与 2 处直接调用、1 处日志名单同步清理（8.6）。
5. **不改**：各组提示词的写法规则（`format_prompt_after(group)` 的文本一个字不动）；`card_from_draft` / `phase_anchoring` / `relationship_batch` 的口径；API 返回格式。
6. **不做**：`distill` / `distill_stream`（短文本截断模式，全量模板，生产调用方未见，记跟踪文档待 Tracy）；段 1 复核记下的既有覆盖缺口（组必填字段、组失败日志原因、校验失败文案）。

### 8.3 结构自检

| 问题 | 段 1 后 | 段 2 后 |
|---|---|---|
| 「按组格式化 → 阶段依赖 → 合并 → 关系分批 → 校验」写了几处 | 1 处共用方法，4 个调用方中只接了 F1 | 仍是 1 处（`core/distiller.py:2621` `_format_groups`），F1–F4 全部只传参数 |
| 调用方是否被迫知道不该知道的事 | 是（路由要知道 F4 草稿缺关系，自己补） | 否：草稿离开 distiller 时关系已补全；路由只转卡（`web/routers/distill.py` 两处 `fill_relationships` 删除） |
| 新增一个按阶段的字段要改几处 | 登记表 + 放进某组；一次读完路径仍被全量模板一次带出 | 登记表 + 放进某组；`PHASE_DEPENDENT_GROUPS` 自动推导，弧线先发规则对所有组生效 |

### 8.4 目标检查（第一个提交，全部 `xfail(strict=True)`）

| # | 检查 | 测的是目标 |
|---|---|---|
| G1 | 替身 LLM 原样记录 F3、F4 每次调用的 `(system, messages)`：每次 system 以 `book_prefix(text)` 开头；第一次且仅有一次只含弧线模板（G6）；其后各组调用都在 G6 返回之后；依赖组（含 G7）的 system 含 `_phase_list_note` 的输出，非依赖组不含；每次 `max_tokens == CARD_MAX_TOKENS` | 一次读完路径真的拆成专注调用、弧线先定阶段 |
| G2 | `FORMAT_GROUPS["G6"] == ("character_arc",)`；`"G7" in PHASE_DEPENDENT_GROUPS`；F3 断言（各组 ∪ `POST_FORMAT_FIELDS` == `CharacterCard` 字段、两两无交集）仍过 | 分组拆对了、阶段依赖由登记表推导 |
| G3 | F2/F3/F4 交出的草稿无「有 target 没 relation」的关系条目；`Distiller` 没有 `fill_relationships`；`web/routers/distill.py` 不再引用它 | 关系补全收回 distiller，消费方不再补 |
| G4 | F3、F4 任一组失败 → 整步失败（同步抛异常 / 流式一个 error 帧），不出草稿 | 失败不交半张卡 |

替身照生产行为写（返工经验第 35 条）：对 system 不改写；组调用按 `CharacterCard[Gn]` 回包；关系分批回关系详情。

段 1 的 C2 是 F1 行为锁，本段有意改变 F1（G6 拆分、弧线先单发）：按新期望重录 fixture，差异只允许来自改法 1、2（组表 + 调度），提示词文本与 user 消息不改。C1 结构锁保持（共用方法名不变）。

### 8.5 调用点矩阵

| 路径 | 生产消费方 | 入口 → 共用方法 | 本地清单内测试 | 仅 CI 测试 |
|---|---|---|---|---|
| F1 分组·流式 | 后台 `/start`、SSE | `distill_incremental_stream` → `_format_groups_stream` → `_format_groups` | `test_longctx_focused_calls_goal.py` C1/C2、`test_distiller_routing.py::TestFormatGroupsRunInParallel`、`test_distill_usage_accounting.py`、`test_arc_phase_fields_*`、`test_mutation_anchors.py` | `test_distill_output_bounds.py` |
| F2 分组·同步 | `TextManager.get_or_distill`（`core/text_manager.py:433`；路由 `:789`、`:1801`） | `distill_incremental` Phase 3 → `_format_groups` | `test_distiller_routing.py`（F2 提示词/记账）、`test_distill_usage_accounting.py::TestIncrementalFormatAccounting`、`test_distill_task_api.py`、`test_card_redraft_goal.py` | `test_distill_output_bounds.py` |
| F3 一次读完·同步 | 同 F2 | `_distill_longcontext` → `_format_groups` | `test_distiller_routing.py`（同步一次读完提示词）、`test_distill_usage_accounting.py::TestInitialCallAccounting`、`test_arc_phase_fields_entries.py::test_entry_longctx_fields` | — |
| F4 一次读完·流式 | 后台 `/start`（`web/routers/distill.py:357` 起）、SSE（`:1188` 起） | `_distill_longcontext_stream` → `_format_groups_stream` → `_format_groups` | `test_distill_draft_persist.py`、`test_distill_resume.py`、`test_error_user_facing.py`、`test_phase_anchoring.py`、`test_distill_usage_accounting.py::TestStreamReturnValueAccounting` | `test_identify_failure_channels.py` |

### 8.6 失效的锁、替身与 fixture（逐处理由；10-21 远端沙箱实做）

每处都只跟新结构改写法，守的东西不变；改过的锁 / 断言都在 8.9 注入原缺陷复核仍红。

| 落点 | 原来守什么 | 为什么红（8.2 哪一项） | 怎么改 |
|---|---|---|---|
| `tests/test_distiller_routing.py::TestRouting.setup_method` | 路由按阈值分流 | 2/3：分片同步改走流式 `_collect_stream`；替身是裸 `MagicMock`，`chat_stream_long` 的 `next()` 永不结束 → 无限 append（实测约 75MB/s，吃满内存致 Tracy 电脑黑屏） | 补一个有终点的 `chat_stream_long`（照生产：流读完即止） |
| 同文件 `test_the_one_pass_request_puts_the_whole_text_before_the_character_name` | 全文在角色名前、user 不含全文、输出上限 | 3：输出上限由单次整卡 `LONG_OUTPUT_MAX_TOKENS` 变为每组 `CARD_MAX_TOKENS` | 只改上限断言 |
| 同文件 `test_the_sync_one_pass_request_uses_the_same_prompt` | 同步一次读完同一提示词顺序 | 3：同步改走流式；替身只配了 `chat`（同上无限 append） | 替身改成有终点的 `chat_stream_long`；回包缺弧线字段 → 断言整步抛 `DistillError`；上限同上 |
| 同文件组识别 `_format_group_of` | 认出调用属于哪一组 | 1：手抄的键名表 G6=`"situation_behaviors"` 拆组后失效 | 改读子 schema 标题 `CharacterCard[Gn]`（`draft_schema` 生成），组表只认 `FORMAT_GROUPS` |
| 同文件 `TestFormatGroupsRunInParallel` | 并行、依赖组等弧线组、依赖组带阶段列表、每组一条账 | 2：首批不再是 G1/G5/G6 | 弧线组先到；其余 6 组同一个 barrier 并行；依赖组带阶段列表、**非依赖组不带**（新增断言） |
| `tests/test_arc_phase_select.py` U9 | 关键记忆拿得到阶段编号 | 1：原写「在 G6」 | 改守「所在组 ∈ `PHASE_DEPENDENT_GROUPS`」，按字段找组 |
| `tests/test_arc_phase_fields_unit.py` U13 | 依赖组由登记表推导 | 1 | 期望集合加 `G7` |
| 同文件 U22、R1 | 一次读完的关系分批：前缀是 `book_prefix`、不含「只出名单」；user 是本步请求 | 4：`fill_relationships` 已删 | 新助手 `_one_pass_rel_calls` **真跑** `_distill_longcontext`，从它发出的关系分批调用上取 system / messages（不改成直接传参，否则守不住「一次读完路径传的是什么」） |
| `tests/test_arc_phase_fields_entries.py::_assert_batched` | 每个入口都走关系分批 | 3：入口多一层 `_format_groups_stream` | 改为按调用链传递闭包认「会走到 `_relationships_batched` 的函数」，不写死中间函数名 |
| `tests/test_arc_phase_fields_locks.py` S14 | 分批前缀只出自共享前缀函数 | 3：`_format_groups_stream` 原样转交 `rel_prefix` | AST 认出「实参就是外层函数自己的 `rel_prefix` 形参」的转交并放行，其余实参仍须是产出者 |
| `tests/perf/card_draft_mutations.py` M21、M22、M23 | 同步两入口只经 `card_from_draft`；流式原样交出草稿 | 3：调用点文本变了；长文流式不再逐 token 转交 | 锚点跟新行；M23 落点移到 `_format_groups_stream` 交出草稿那一行 |
| `tests/perf/card_draft_mutations.py` M28 | 分组流式不在 distiller 里转成卡 | 3：交出草稿那一行搬进 `_format_groups_stream`，那里没有原文变量，原变异变成 NameError（空转） | 变异改传空原文，意图不变 |
| `tests/perf/card_draft_mutations.py` 新增 M39、M40；`tests/test_card_draft.py` 新增 `_accepted` | 同步两入口：坏草稿抛 `DistillError`、合法草稿出卡 | 3：坏草稿现在先被 `CardDraft.model_validate` 拦下，原来撞这两条 `pytest.raises` 的 M10 到不了；M21/M22 的变异体在入口内抛异常而不是断言失败（空转） | M39/M40 直接变异入口口径（格式错误被吞成空卡）；`_accepted` 把「合法草稿被入口拒绝」变成断言失败。产物 `card_draft_red_lines.json` 按驱动重跑刷新 |
| `tests/perf/arc_phase_anchoring_mutations.py` M29 | 分片同步转卡带原文 | 3：调用点文本变了 | 锚点跟新行 |
| `tests/test_card_draft.py` `_reply`、U8、G6 模板用例、`test_stream_longcontext_yields_draft` | 同步/流式入口交出草稿、occurrences 不被转换 | 1/3：同步按组调用；替身对每次调用回整份草稿 | `_reply` 按 `CharacterCard[Gn]` 回本组字段（坏值原样保留）；U8/模板用例按字段找组；流式用例复用 `_grouped(chunked=False)` |
| `tests/test_phase_anchoring.py` `_distiller`、`_reply` | 同上 | 同上 | 删掉与 test_card_draft 一模一样的副本，改为导入那一份（两份只改一份会再分叉） |
| `tests/test_distill_usage_accounting.py` 3 条 | 每次调用恰一条账、不重记、账取返回值 | 3：一次读完与分片同步都按组调用 | 期望改为 `len(FORMAT_GROUPS)` 条；替身用已有的按组回包 `_FormatGroupReturnUsageLLM` |
| `tests/test_distill_draft_persist.py` | 存的是转卡前**最后改动后**那份 data | 4：路由不再补关系，原判别点（`fill_relationships` 写 relation）消失 | 判别点移到路由转卡前仍会改草稿的 `locate_phase_starts`：替身就地补阶段 2 锚点（形态不对时不改不抛，照生产），断言存下的锚点是补后的 |
| `tests/test_card_arc_behaviors.py` 原 `test_g6_prompt_carries_arc_and_behaviors_together` | 原判据「弧线与做法同组同调用」 | **设计前提被 S2 数据推翻**（§0、跟踪文档 10-21） | 改为 `test_arc_first_then_behaviors_with_the_phase_list`：弧线组只有弧线；做法组 ∈ 依赖组且带维度 O 与形态规则；G4 两者都无 |
| `tests/test_distill_output_bounds.py`、`tests/test_pre_story_memory_goal.py` | 记忆在拿得到阶段编号的组；「故事前填 0」两处原句 | 1 | 按字段找组；原句逐字不变 |
| `tests/test_distill_diagnostic_logs_goal.py` g7 | 链上无 print | 4 | 名单去掉 `fill_relationships`，加 `_format_groups`、`_format_groups_stream` |
| `tests/test_card_redraft_goal.py`、`tests/test_distill_task_api.py`、`tests/test_identify_failure_channels.py` 替身 | — | 4 | 删掉不再被调用的 `fill_relationships` |

### 8.7 测试（只在远端沙箱跑，带内存上限）

本机不跑（10-21 黑屏事故，返工经验第 40、41 条）。沙箱逐文件、`ulimit -v 8000000`、`--timeout=60`：
`test_longctx_focused_calls_goal test_distiller_routing test_arc_phase_select test_arc_phase_fields_unit test_arc_phase_fields_entries test_arc_phase_fields_locks test_card_draft test_distill_usage_accounting test_failure_alerting test_mutation_anchors test_exception_pickle_lock test_distill_task_api test_distill_draft_persist test_card_redraft_goal test_phase_anchoring test_distill_resume test_error_user_facing test_card_arc_behaviors test_distill_diagnostic_logs_goal test_distill_output_bounds test_domain_exception_exit test_evidence_format_goal test_identify_failure_channels test_length_budget test_personality_distill_goal test_pre_story_memory_goal test_scalar_one_per_phase_goal test_usage_identity_context`（原清单 17 个 + grep 出的 11 个：凡引用 `fill_relationships` / `_longcontext_prompt` / `distill_instruction` / `FORMAT_GROUPS` / `PHASE_DEPENDENT_GROUPS` / `_distill_longcontext` / `distill_incremental` 的测试文件）。全量看分支 CI。

### 8.8 未核实

- 一次读完路径按 7 次组调用计费后的真实成本曲线（缓存命中率取决于回忆区是否已缓存该书）；本段不调真模型，**未核实**。付费小重放留到部署前（不在本 PR）。
- 本清单外测试的静态改动（8.6 后四行）只在分支 CI 上验证。
- 段 1 §7 的 C2 稳定性结论沿用；重录后的 fixture 首次在 CI 上跑。

### 8.9 沙箱复核（10-21，远端沙箱，本地 PG 55432）

- 28 个文件逐个跑：全部通过（见 PR 描述原始输出）。
- 改过的锁注入原缺陷，确认仍红（对照：未变异 338 passed）：

| 注入的缺陷 | 结果 | 打红的检查 |
|---|---|---|
| 其余组改回串行 | 红 | `TestFormatGroupsRunInParallel` |
| 其余组不等弧线组 | 红 | 同上（barrier 超时） |
| 依赖组缺阶段列表 | 红 | `TestFormatGroupsRunInParallel`、U20 |
| 非依赖组也带阶段列表 | 红 | `TestFormatGroupsRunInParallel`（新增断言） |
| 共用流程漏掉关系分批 | 红 | G3、E12–E14、`TestFormatGroupsRunInParallel` 等 6 条 |
| 一次读完关系前缀退回 `book_prefix + 全量模板` | 红 | U22（S14 只看前缀以 `book_prefix(` 开头，拦不住这一种——既有弱点，非本段引入，目标外） |
| 整本蒸外面再记一笔账 | 红 | `test_longcontext_records_once_not_twice` |
| 路由存重定位之前的草稿副本 | 红 | `test_start_persists_the_draft_with_the_distill_time_character` |
| 弧线组的阶段没传给依赖组 | 红 | C2 |
| M21 / M22 / M23 / M29（新锚点） | 均红 | test_card_draft / test_phase_anchoring |


### 8.10 补充（复核，10-21 本地静态复核 + 作者处理）

本地复核因测试库不可用只做了静态复核：0 阻断，6 条非阻断。处理如下（远端沙箱改、跑）：

| 复核发现 | 处理 |
|---|---|
| F2 用户消息里两句约束（「catchphrases 必须是原文中的真实口癖，不要编造」「personality_traits 每条必须附带具体场景证据」）不再出现 | **不是内容损失**：两条规则本来就在组提示词里——G2 维度 B「每个特质 + 原文中的具体场景作为证据」、G3 维度 C「口癖（直接从原文对话提取）」与原话规则「原文里的原话，逐字照抄」（`format_prompt_after("G2")` / `("G3")` 实测包含）。旧 F2 的 user 消息是重复写了一遍；F1 从来没有。记为有意变更，提示词不改（§8.2 第 4 条：写法不动） |
| `raise err.cause from err.cause` 的 `__cause__` 指向自身 | 改为 `raise err.cause` |
| `_distill_to_draft` 文档串仍写「补关系」 | 改为「草稿交出时关系已在 distiller 内补全」 |
| `_format_groups_stream` 文档写「失败一律收成一个 error 帧」 | 收窄为「已知失败」，写明其余异常交路由统一出口 |
| `test_stream_longcontext_yields_draft` 只比 `situation_behaviors` | 收紧为整份相等（期望值 = 默认值补齐后的同一份草稿过 `CardDraft`）；`card_draft_red_lines.json` 随之重跑刷新 |
| AST 匹配弱点（别名调用、重新绑名可绕过） | 既有问题，非本 PR 引入，目标外，记跟踪文档 |
| SSE 双 error 帧 | **定性更正（§8.11 发现 3）**：缺陷是既有的（`/run_stream` 的 dict 分支不认 `error` 键，`_distill_to_draft` 认 —— 同一件事两个消费方各写一遍），但改前 F4 从不出 error 帧，**本 PR 让它在一次读完主路径上可达**。代码不在本 PR 改：`/run_stream` 前端无调用方（grep 0 命中）；记跟踪文档待 Tracy 定 |

### 8.11 补充（独立复审，10-21 远端沙箱）

复审结论「不通过，阻断 1、非阻断 7」（head `0dadfed`）。本 PR 改动面内的按下表修；目标外的记跟踪文档。

| # | 发现 | 根因 | 处理 | 守护 |
|---|---|---|---|---|
| 1（阻断） | 弧线组失败被吞，全部测试仍绿 | 弧线组单独先发后有两个抛错出口（`_format_groups` 第一次 `fmt_queue.get()`、其后循环），G4 只测了后一个；§8.4 的 G4 写「任一组失败」但没要求两个出口都覆盖（spec 错） | G4 按 `bad_group ∈ {G6, G4}` 参数化；G6 失败时另断言其余组一次都没发 | `test_g4_any_group_failure_fails_the_whole_step[G6]`（复审 L5 变异下红） |
| 2 + 7 | 路由换个名字补关系照样全绿；重新出卡「不补关系」的守卫随 `fill_relationships` 一起被删 | 锁按名字查，不按职责查；§8.6 把守卫当成无用替身（定性错） | G3 改为按职责：后台 /start 走一次读完，关系分批调用恰一次（只由 distiller 调）；路由源码不出现任何补关系入口名；`_NoRedistillStub` 的守卫改挂在真正干这件事的 `_relationships_batched` 上 | G3（`getattr` 拼名绕过的变异下红：调用 2 次）；`test_card_redraft_goal.py` |
| 4 | 后台 /start 的进度在格式化阶段停在 40%，最后一跳 | 进度按流式 token 长度算；段 2 起 F4 只交一个草稿串（§8.5 漏写上屏进度的变化） | 心跳带组进度 `{"heartbeat", "current", "total"}`（`_format_groups` 两处）；路由在格式化阶段按心跳推进 40% → 95%，删掉按 token 长度估的旧算法（`EXPECT_CHARS`）。不新增状态值，帧种类不变（C2 fixture 不重录） | `test_g5_start_progress_advances_per_group`（改前路由下红：`[40, 56]`） |
| 6 | fixture 丢了 `baseline_commit`、`character_name` | 重录时没沿用原结构 | 补回；`baseline_commit` = 重录所用提交 `b291122` | — |
| 8 | 入口锁认不出 F4 那一支 | `distill_incremental_stream` 同时包 F1、F4，F1 走到分批就让 E14 绿 | 新增 E14b，单独钉 `_distill_longcontext_stream` | E14b（F4 不经共用流程的变异下红） |
| 3 | SSE 双 error 帧 | 见 §8.10 定性更正 | 代码不改，记跟踪文档 | — |
| 5 | 一组失败后其余组线程不取消，照常计费 | 线程无取消机制（既有设计，本 PR 扩到主路径；缓存命中时每次约 $0.0025–0.005） | 目标外，记跟踪文档 | — |
| — | 每组输出上限 8192（改前整卡 16384）够不够 | 未核实 | 真模型小重放看有无 `upstream_truncated` | — |

