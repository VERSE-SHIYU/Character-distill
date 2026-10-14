# spec：态度不挪阶段 + 阶段列表单一渲染 + 时间感知按需注入 + 边界块按好感收放

基线：main `630b73a0`（2026-10-13）。放进 worktree 的 `docs/specs/`，用 `Test-Path` 确认；补充写回本文件。
调查依据：共享文档《阶段锚点核对失效：调查报告》第三之二、三之三、六之二节。

**两个 PR，按顺序做，各自过目标检查再开下一个：**

- **PR-A 蒸馏侧**：A1 态度只核对不挪阶段，A2 阶段列表只在一处渲染，A3 位置检查的日志写明原因。
- **PR-B 聊天侧**：B1 时间感知抽成独立模块、按需注入，B2 边界块在亲近档以上不注入。

## 设计原则（本 spec 的硬要求）

- **不打补丁**：不在调用处加 `if` 分支、不加布尔开关去绕旧逻辑。规则放进它所属的模块，只写一处。
- **高内聚**：位置检查的规则留在 `core/phase_anchoring.py`；阶段列表怎么写进提示词只在一个函数里；时间感知的判定与文案在一个纯函数模块里；「哪些好感算亲近」只在 `core/affinity_rules.py`。
- **低耦合**：`core/relationship_batch.py` 不再认识阶段的内部结构，只收一段已渲染好的阶段说明；`ChatEngine` 只负责取数据（存储、时钟），判定与文案交给纯函数。
- **复用**：用现有的 `phase_header`、`describe_time_period`、`CLOSE_FROM`，不另写同义的东西。
- 每段末尾的「结构自检」三问必须答「一处」。

## S0（先做，不写代码）

1. 按下面「判断清单」逐条打开文件、对照行号读代码。有一条对不上就停下报告，不要猜。
2. 标「未核实」的条目由你核实后再动手；核实结果写回本文件「补充」节。

## 判断清单（每条附这一轮读过的代码）

| # | 判断 | 坐标与原文 |
|---|---|---|
| J1 | 摘录没落进所标阶段时，改挂到落点 | `core/phase_anchoring.py:204-207`：`else: … for t in landed: moved.setdefault(t, quote)` / `hit.update(landed)` |
| J2 | 查不到与超 3 次走同一分支，日志不分原因 | `core/phase_anchoring.py:195-197`：`if loc is None or len(loc) > MAX_OCCURRENCES: unverified += 1; continue`；`:211-212` 只打「没有能定位的摘录」 |
| J3 | 态度每条单独过 `verify`，撞车时原标阶段优先，落败者静默进未定位区 | `core/card_draft.py:333`：`res = phase_anchoring.verify(`；`:357`：`next((j for j in cands if flat[j][1].phase == t), cands[0])`；`:360-363` 无日志 |
| J4 | 位置检查汇总行是 INFO，生产 `docker logs` 只收 WARNING 以上 | `core/card_draft.py:457`：`logger.info("[phase_anchoring] …`；`core/stdout_logging.py`：`logging.Handler.__init__(self, logging.WARNING)` |
| J5 | 关系分批只拿阶段名 | `core/distiller.py:476-479` `_phase_labels` 只取 `label`；`:1034` 传 `_phase_labels(draft)`；`core/relationship_batch.py:65`：`"；".join(f"{i}. {p}" …)`，`:73`：`阶段依次为：{stage}` |
| J6 | 分组路径给依赖组的是「阶段 i·label · state」 | `core/distiller.py:482-493` `_phase_list_note(g6)`；唯一调用 `:2798` |
| J7 | `batch_relationships(targets, phases: list[str], …)` 的测试调用点 | `tests/test_arc_phase_fields_unit.py:536,550,564,710,734,756` |
| J8 | 时间块每轮接在当前用户消息末尾，含行为示范 | `core/chat_engine.py:535`：`extra = self._build_time_awareness_block() + …`；`:1209`：「比如深夜会关心对方怎么还不睡」 |
| J9 | **现有「距上次对话」永远判成「刚刚还在聊」**：路由先存用户消息再调引擎，存消息会把 `sessions.updated_at` 刷成现在 | `web/routers/chat.py:470-478` 先 `save_message(session_id, "user", …)`，`:487` 才 `engine.chat_stream(...)`；`storage/postgres_store.py:1757-1758`：`UPDATE sessions SET updated_at = CURRENT_TIMESTAMP`；`core/chat_engine.py:1168-1186` 用 `updated_at` 算间隔 |
| J10 | 重逢问候也调用时间块 | `core/chat_engine.py:1540`：`time_block = self._build_time_awareness_block()` |
| J11 | 结构锁登记了 `_build_time_awareness_block` 这个名字（它读存储） | `tests/test_storage_scope_lock.py:42` |
| J12 | 边界块不看好感，每轮注入 | `core/chat_engine.py:1005-1060`，开头只判 `affinity_enabled`；`:1131` 每轮拼入 |
| J13 | 亲近档下界只此一处 | `core/affinity_rules.py:16`：`CLOSE_FROM = 73`；`:70`：`"close" if affinity >= CLOSE_FROM`；`core/affinity_service.py:22-23`：亲近、心意相通两档从 `CLOSE_FROM` 起 |
| J14 | 非流式聊天路径也是先存用户消息 | `web/routers/chat.py:356` **未核实是否早于调引擎**，S0 核实 |

## PR-A：蒸馏侧

### 目标检查（第 1 个提交就带上，之后每个提交都跑）

用一份手写草稿 + 原文跑 `card_from_draft`。原文里一句话只出现在阶段 3 的范围内：
- 一条**态度**标阶段 1、摘录用这句 → 转卡后这条态度在阶段 1。
- 一条**做法**标阶段 1、摘录用同一句 → 转卡后这条做法在阶段 3。
- 一条态度的摘录原文里没有 → 进 `unlocated.attitudes`。

再断言：关系分批的 system 文本里，每个阶段都有它的 state 与锚点原句。

### A1 态度只核对、不挪阶段

- 在 `core/phase_anchoring.py` 定义落点策略（枚举，例 `Placement.RELOCATE` / `Placement.KEEP_TAGGED`），作为 `verify` 的参数，默认 `RELOCATE`。分支只在 J1 那一处：`KEEP_TAGGED` 时，摘录查到（不超 3 次）即算命中所标阶段 p，不改挂。
- 唯一传 `KEEP_TAGGED` 的地方是 `_convert_relationships`（J3 的调用），并在那里用一句注释写明原因：态度常在后文被回忆复述，摘录位置不等于态度成立时间（见调查文档第三之二节）。
- 做法、记忆、各字段保持默认，行为不变。撞车规则不变。

### A2 阶段列表只在一处渲染

- 把 `_phase_list_note` 改为接收阶段列表（不再收 `g6` 整体），每行写「`phase_header(i, label)` · state · 起始原句：anchor」，anchor 为空写「（从开头起）」；无阶段时返回原有那句「本角色没有阶段……」。
- 分组路径（J6）与关系分批都用它。关系分批：`batch_relationships` / `_batch_prompt` 的 `phases: list[str]` 改为 `phase_note: str`（已渲染好的文字），删除 `_batch_prompt` 里自己编号的那段（J5 的 `:61-65`）与 `_phase_labels`。
- 分组路径的依赖组提示因此多出锚点原句。这是有意的：阶段说明全仓只有一种写法。
- 同步改 J7 的测试调用点。

### A3 位置检查日志写明原因

- J2 的分支分别计「查不到」与「出现 N 次」，「去掉标注」那行写明每段摘录的原因。
- J3 撞车落败时打一行 WARNING：`撞车落败：态度 '<对象>' 标在阶段 <p>，阶段 <t> 采用了另一条`。
- J4 的汇总行改为 WARNING，字段不变。

### PR-A 结构自检

| 问题 | 答案（附行号） |
|---|---|
| 「挪不挪阶段」写了几处 | 一处：`verify` 内的分支 |
| 阶段列表的提示词写法写了几处 | 一处：`_phase_list_note` |
| `relationship_batch` 还需要知道阶段的内部字段吗 | 不需要，只收字符串 |
| 以后再有一类条目要「不挪」，要改几处 | 一处：它的调用点传策略 |

## PR-B：聊天侧

### 目标检查

构造引擎跑两轮，前后间隔 1 分钟、同一时段 → 第二轮的用户消息末尾没有时间块。上一条消息在 12 小时前 → 有时间块，且块内有间隔句、没有「关心对方怎么还不睡」、有剧情时间优先句。好感 100 → 系统提示里没有边界块；好感 60 → 有。

### B1 时间感知抽成独立模块

- 新建 `core/time_awareness.py`，只放纯函数（无 IO）：
  - 由「现在」「上次对话时间（可为空）」算出间隔句：把 `core/chat_engine.py:1162-1193` 的分档原样搬过来。
  - 判定是否注入：首轮，或距上次对话 ≥ 600 秒，或两者的 `describe_time_period` 不同 → 注入；否则不注入。
  - 渲染块：只写事实「【现实时间】现在是{时段}（HH:MM）。」+ 间隔句，再加一句：「对话里已写明剧情时间（如『第二天早上』）时以剧情为准；对方问起现实时间时才按这里回答。」**删掉** `:1208-1212` 的行为示范。
- `ChatEngine._build_time_awareness_block` 保留这个名字（J11 的结构锁），只做两件事：取数据、调纯函数。
- **J9 必须一起修**：「上次对话时间」要取**当前这条用户消息之前**最后一条消息的时间，不能再用 `sessions.updated_at`。用存储层现有的读消息方法取；没有合适的方法就停下报告，不要自己新写一套查询。
- J10 的重逢问候走同一个函数。它在离开超过 6 小时后才触发，判定必然注入，行为不变。

### B2 边界块：亲近档以上不注入

- 在 `core/affinity_rules.py` 加一个判定，例 `is_close(affinity) -> bool`，即 `affinity >= CLOSE_FROM`。`relational_tier` 改用它（J13），保证「亲近」只有一个定义。
- `_build_boundary_block` 开头：`is_close(self._affinity)` 为真即返回空串。其余档不变，包括 `in_character < 40` 时加重、挪位。
- 不另加任何内容限制。

### PR-B 结构自检

| 问题 | 答案 |
|---|---|
| 「什么时候注入时间」写了几处 | 一处：`core/time_awareness.py` |
| 「亲近」的定义写了几处 | 一处：`CLOSE_FROM` + `is_close` |
| `ChatEngine` 里还有时间文案吗 | 没有 |

## 测试

- 本地只跑受影响的文件。PR-A：`tests/test_phase_anchoring.py`、`tests/test_arc_phase_unlocated.py`、`tests/test_arc_phase_unlocated_goal.py`、`tests/test_arc_phase_fields_unit.py`。PR-B：`tests/test_clock.py`、`tests/test_reunion_greeting.py`、`tests/test_storage_scope_lock.py`、新模块的测试、边界块所在测试（先 grep `_build_boundary_block` 定位）。
- 库用 docker PG。合并门是分支 CI，合并只做 git 操作。
- 测试替身照生产行为写：存消息会刷新 `updated_at`、读消息的返回形状与生产一致。

## 对账表（复核方必须验的清单；变异由独立复核方自己跑，并自补放宽、过严两个方向）

| 行为变化 | 守它的测试 | 让它变红的变异 | 改后能触发的具体状态 |
|---|---|---|---|
| 态度不挪阶段 | 目标检查第 1 条 | 态度调用点改回默认策略 | 态度摘录只在后面阶段出现 |
| 其他条目照旧挪 | 目标检查第 2 条 | 默认策略改成 `KEEP_TAGGED` | 做法摘录只在后面阶段出现 |
| 查不到仍进未定位 | 目标检查第 3 条 | `KEEP_TAGGED` 把查不到也算命中 | 态度摘录原文没有 |
| 关系分批带 state 与锚点 | 断言 system 文本 | `_phase_list_note` 去掉 state 或锚点 | 有阶段的草稿走关系分批 |
| 两路阶段说明同一写法 | 分组路径与关系分批的阶段行逐字相等 | 任一路自己拼 | 同一份阶段列表 |
| 日志分原因 | 两种原因文案各一条 | 原因写死成一种 | 摘录查不到 / 出现 4 次 |
| 撞车落败有日志 | 撞车用例断言该行 | 删掉该行 | 同对象两条态度落同一阶段 |
| 汇总行 WARNING | 断言级别 | 改回 INFO | 任一有阶段的卡 |
| 间隔取上一条消息时间 | 上一条消息 12 小时前 → 有间隔句 | 改回读 `updated_at` | 存完当前消息后再算间隔 |
| 同时段 10 分钟内不注入 | 间隔 1 分钟同时段 → 无块 | 判定恒为真 | 连续两轮 |
| 时段变化注入 | 22:55 → 23:05 → 有块 | 去掉时段比较 | 跨时段 |
| 无行为示范 | 块不含「怎么还不睡」 | 恢复示范句 | 任一注入轮 |
| 剧情时间优先句 | 块含该句（整句断言） | 删句中任一词 | 任一注入轮 |
| 亲近档以上无边界块 | 好感 73、100 → 无 | 判定改 `>` 或去掉 | 好感 ≥ 73 |
| 亲近档以下照旧 | 好感 72、60 → 有 | 判定改 `>= 55` | 好感 < 73 |

## 技能

| 步骤 | skill | 用途 |
|---|---|---|
| S0 复核判断清单 | `@search-first` | 按行号读代码，找可复用的现有函数与读消息方法 |
| A1–A3、B1–B2 | `@tdd` | 先写目标检查和失败测试，再改 |
| 交付前 | `@verification-before-completion` | 跑受影响测试，贴原始输出 |

## 交付

- 两个 PR，标题、描述、commit 用英文。
- 每个 PR 回报：改动文件清单；判断清单逐条复核结果；结构自检表；受影响测试原始输出。
- 不写部署步骤。PR-B 合并后由 Shiyu 在 SG 部署实聊；PR-A 合并后重蒸祈灼卡，看「初遇惊艳」是否回到前面阶段、云绮在阶段 2 是否有态度。

## 补充

（S0 核实结果与执行中的新决定写在这里。）

### 补充 1（2026-10-14 16:05，回答执行方 J9 提问）：「上次对话时间」从哪取 —— 定选项 3 的改进版

- **路由在本轮任何写库之前**，用已有的 `storage.get_session_owned(session_id, user_id)` 读一次会话，取 `updated_at` 作为「上次对话时间」，传给引擎（例：`engine.chat_stream(llm_msg, voice_mode=…, last_activity_at=…)`；非流式路径同理）。
  - 必须早于 `web/routers/chat.py:418-430` 的 user_role 落库（`save_session` 也会刷新 `updated_at`，`storage/postgres_store.py:1403`）和 `:470-478` 的存用户消息。
- 引擎的时间块不再自己读存储：`_build_time_awareness_block` 改为接收 `last_activity_at`，删掉其中的 `get_session_unscoped` 调用；同时删掉 `tests/test_storage_scope_lock.py:42` 那条白名单登记（该函数不再读存储）。
- 重逢问候（`core/chat_engine.py:1540` 附近）已在自己的流程里读过会话的 `updated_at`（此时没有新消息），直接把它传进同一个函数。
- 不选 1：每轮全量读消息，长会话 O(n)。不选 2：要动存储层基类，按约定 SQLite 不为新功能改动。
- 本改动让引擎的时间路径变成纯计算（无 IO），比原 B1「引擎自己取数据」耦合更低；原 B1 中「`ChatEngine` 只负责取数据」一句以本补充为准。

### 补充 2（2026-10-14，S0 判断清单逐条核实结果）

核实基线仍为 main `630b73a0`；行号指**核实当时**的代码（PR-A 改动后本文件内行号会移）。

| # | 结论 | 核实时坐标 |
|---|---|---|
| J1 | 属实 | `core/phase_anchoring.py:204-207`：`else:` 分支 `for t in landed: moved.setdefault(t, quote)` / `hit.update(landed)`；`landed = sorted({phase_at(ranges, x) for x in loc})` |
| J2 | 属实 | 同文件 `if loc is None or len(loc) > MAX_OCCURRENCES:` 合并且 `unverified += 1; continue`；`"去掉标注：…没有能定位的摘录"` 一行不区分原因 |
| J3 | 属实 | `core/card_draft.py` `res = phase_anchoring.verify(`；`next((j for j in cands if flat[j][1].phase == t), cands[0])`；落败者无日志 |
| J4 | 属实 | `core/card_draft.py` `logger.info("[phase_anchoring] …`；`core/stdout_logging.py`：`logging.Handler.__init__(self, logging.WARNING)` → `info` 不进 `docker logs` |
| J5 | 属实 | `core/distiller.py` `_phase_labels` 只取 `label`；`core/relationship_batch.py` `"；".join(f"{i}. {p}" …)` 与 `阶段依次为：{stage}` 各写一次编号 |
| J6 | 属实 | `core/distiller.py` 分组路径 `_phase_list_note(g6)` 渲染「阶段 i·label · state」，唯一调用点 |
| J7 | 属实 | `tests/test_arc_phase_fields_unit.py` 的 6 处 `batch_relationships(...)` 调用点 |
| J8 | 属实 | `core/chat_engine.py` `extra = self._build_time_awareness_block() + …`；行为示范句「比如深夜会关心对方怎么还不睡」 |
| J9 | 属实 | `web/routers/chat.py` **先** `save_message(session_id, "user", …)` **后** `engine.chat_stream(...)`；`storage/postgres_store.py` 存消息 `UPDATE sessions SET updated_at = CURRENT_TIMESTAMP`；引擎用 `updated_at` 算间隔 → 恒判「刚刚还在聊」 |
| J10 | 属实 | `core/chat_engine.py` 重逢问候 `time_block = self._build_time_awareness_block()` |
| J11 | 属实 | `tests/test_storage_scope_lock.py:42` 登记 `_build_time_awareness_block`（因为它读存储） |
| J12 | 属实 | `core/chat_engine.py` `_build_boundary_block` 开头只判 `affinity_enabled`，不看好感档；每轮拼入系统提示 |
| J13 | 属实 | `core/affinity_rules.py` `CLOSE_FROM = 73`；`"close" if affinity >= CLOSE_FROM`；`core/affinity_service.py` 亲近、心意相通两档从 `CLOSE_FROM` 起 |
| J14 | **不属实** | spec 猜「非流式路径也先存用户消息」。实测 `web/routers/chat.py` 的 `_do_chat`：`storage.get_session_owned` 只在 user_role 分支里读一次；`engine.chat(...)` 在 `:325` 被调用，用户消息在 `:363` 才 `outbox.write(_save_user_message, …)` 落库 —— **引擎先于存消息**，故 `updated_at` 尚未被本轮的存消息刷新。**J9 的「恒判刚刚还在聊」只影响流式路径**；非流式路径本来就没有这个偏差。B1 的 `last_activity_at` 仍按「补充 1」由路由在读库之前取 `updated_at` 统一传入，两条路径一致，非流式路径不会因此变差。 |

**判断清单外、动手时同步做的同面修理（都属于本 spec 改动面，已随 PR-A 完成）：**

- `tests/perf/arc_phase_anchoring_mutations.py` 的 8 处源码锚点因 A1/A3 改了被锚文本而重定（`_IN_TAGGED` 换实现分支、M19a/M19b 锚 `len(loc) > MAX_OCCURRENCES`、M33 锚注释、M35 锚新的两行 warning、M18 改锚 `loc is None` 的 `continue`）；对应产物 `tests/perf/arc_phase_anchoring_red_lines.json` 重生成并按 LF 归一。
- `tests/perf/card_draft_red_lines.json` 因 `test_u5` 断言改写而重生成（`test_lock_coverage` 存的是判据身份：路径+源码片段+序号）。
- 台账里另外两个 **非 CI** 的 oneoff 变异脚本（`docs/specs/artifacts/fix_rel_batch_phase_numbering_mutations.py` 的 MG1/MG2、`docs/specs/artifacts/arc_phase_fields_mutations.py` 的 MA20）锚点随 A2 改了 `_phase_list_note` / `batch_relationships` 的签名而重定，为记录完整一并改。

**补充 2 追加（2026-10-14，PR #164 审计发现）：** `web/routers/chat.py` 的 `_do_chat` 与 `_do_chat_stream` 各带一份相同的「写库前读会话取 `updated_at`」代码块（读 + `try/except` + WARNING）。已抽成模块内私有 `_read_session_before_writes(storage, session_id, user_id) -> dict | None`，两条路径都调用它，再从返回值取 `db_s` 与 `updated_at`；行为不变，只消除重复。改在 PR-B 分支（`fix/arc-attitude-time-boundary`），本条记在本文件（PR-A 分支）。受影响测试 `tests/test_message_evidence.py` + `tests/test_time_awareness.py` 重跑 **48 passed**。

