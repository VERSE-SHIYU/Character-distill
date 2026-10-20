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

- `tests/perf/card_draft_mutations.py` M24–M28：锚点落在 F1 正文，抽取后若命中次数不再是 1，按新位置改锚点文本，**变异意图不变**（M24 每组交一段 JSON；M25 草稿坏；M26 不校验；M27 错误帧键名；M28 在 distiller 里转卡）。M28 的替换代码改用新结构下可见的变量（`draft.model_dump()`），仍复现「occurrences 丢失」的缺陷形态。
- C2 fixture 里保存改前的期望值；C1 的 AST 扫描按新方法名更新。除这两处外不改测试判据。

## 6. 测试

只跑受影响文件：`tests/test_longctx_focused_calls_goal.py tests/test_distiller_routing.py tests/test_arc_phase_select.py tests/test_arc_phase_fields_unit.py tests/test_arc_phase_fields_entries.py tests/test_card_draft.py tests/test_distill_usage_accounting.py tests/test_failure_alerting.py tests/test_mutation_anchors.py`（库用 Docker 起的 55432 测试库）。推送前 `scripts/check.sh preflight`。全量看分支 CI。

## 7. 未核实

- C2 在 CI 机器上是否同样稳定（替身按组回包、无网络）。
- `B2-phase-where-check.md` 不存在（见 §2 末）。

## 8. 段 2（段 1 合并后另开 PR，此处只记边界）

1. `core/schema.py`：G6 只留 `character_arc`；新组 G7 = `(situation_behaviors, key_memories)`；E/O 维度与 `_FORMAT_IMPORTANCE:419/:420` 的 owner 改 G7（`:418` 留 G6）；改 `:344-345` 注释。
2. F2/F3/F4 改用 `_format_groups`：一次读完（F3/F4）`system_prefix = book_prefix(text) + "\n\n"`、user = 「请基于以上全文为「{name}」输出模板中的字段」、关系分批 `prefix=book_prefix(text)`、`material=""`、`action="distill_longcontext"`；F2 参数同 F1、`action="distill_format"`；F4 改为 F1 同协议（心跳 + 最后交一个草稿 JSON 串）。
3. 删 `fill_relationships` 及 `web/routers/distill.py:483`、`:1234` 两处调用；清理因此无调用方的代码（以 grep 为准，逐个记录）。
4. 各组提示词的写法规则不改。
5. 段 2 目标检查：G1 一次读完同步/流式每次调用以 `book_prefix(text)` 开头、G6 单独一次、依赖组（含 G7）在 G6 之后且 system 含阶段列表；G2 分组断言；G3 所有路径草稿关系已补全且 `fill_relationships` 不存在；G4 任一组失败整步失败、不出半张卡。
