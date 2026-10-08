# ③ 性格注入 · S1：评估时冒犯、触雷、修复只判一次（spec）

基线：main `b03bcd99`（段 1–3 已合并：PR #122、#123、#124）。新分支 `feat/evaluation-single-judgment`。
来源：交接 v3「等 Shiyu 定的」第 2 条（S1）；段 1 spec `personality-inject.md` §8 第二轮审计表的 S1 行。Claude 10-08 判定 S1 是「有明确答案的技术题」（返工经验第 29 条）：同一件事判两遍，是一条规则写了两处（第 6 条）。

流程：
1. Claude 写本文件和目标检查；
2. 执行方实现；
3. Claude 读 diff、跑变异审计（Shiyu 10-08 定）；
4. Shiyu 合并（Squash and merge，英文标题）。

## 0. 目标与范围

段 1 之后，好感按评估模型报的事件（`affinity_event`）走。但评估 prompt 里还留着一套平行的判定，只供疏远检测的影子日志使用：

| 旧字段 | 和事件的重叠 | 只给谁用 |
|---|---|---|
| `trigger_hit` | 事件 `trigger` | `chat_engine.py:807` 数触雷次数 |
| `in_story_conflict` | 「剧情内演戏的冲突」只在这里说明，**事件说明里没有** | `:808–811` 从触雷次数里减掉 |
| `repair_signal` | 事件 `repair` | `:852–853` 只进日志 |

两个问题：
1. 模型对同一件事给两个答案，可能互相矛盾，比如报了 `trigger`，`trigger_hit` 却是 false。
2. 「剧情内演戏的冲突不算」这条只写在旧的那一套里。好感用的事件说明里没有，模型可能把剧情里的吵架判成 `offended`，好感跟着往下掉。

做：
1. 事件判定规则里加一句：剧情内演戏的冲突不算冒犯、不算触雷，按 `neutral` 处理。这句话只定义一次、渲染一次。
2. 删掉评估 prompt 里的「雷点命中判定规则」「修复信号判定规则」两段，以及 JSON 段里的三个旧字段。
3. 修复的细分（道歉 / 解释 / 行动补偿 / 软肋）保留下来，改成事件的附属字段 `repair_kind`：只在事件是 `repair` 时有意义，字段名和取值只定义在协议模块里。
4. 疏远检测的环形缓冲从「字符串键的 dict」改成一个小数据类 `TurnSignal`，记「实际生效的事件」和 `repair_kind`。「这一轮算不算触雷」「`repair_kind` 什么时候才记」这两条判断都放进 `TurnSignal`；`ChatEngine` 只读它的属性，不再知道事件名和字段名。`EvalResult` 上没人读的三个字段删掉。

不做：
- 疏远检测的判定条件（累计、急降、阈值）不改。「急降」用的是 `MAX_DROP`（大档上限 8），保留现在的含义：一轮大档的冒犯 = 急降。
- 好感规则表不动。
- 不做前端，也不做疏远检测的第二阶段（它还在影子模式）。

## 1. 目标检查（先跑它）

`tests/test_evaluation_single_judgment_goal.py`（随本 spec 交付，**第一个提交只放它和本文件**）：

```
python -m pytest tests/test_evaluation_single_judgment_goal.py -q
```

在 main `b03bcd99` 上实跑（Claude 沙箱 PG，2026-10-08），4 红 2 绿：

```
FAILED tests/test_evaluation_single_judgment_goal.py::test_e1_old_parallel_fields_are_gone_and_repair_kind_is_asked
FAILED tests/test_evaluation_single_judgment_goal.py::test_e2_in_story_conflict_is_not_offence_in_event_rules
FAILED tests/test_evaluation_single_judgment_goal.py::test_e3_trigger_hits_are_counted_from_events
FAILED tests/test_evaluation_single_judgment_goal.py::test_e4_repair_kind_is_logged_only_with_a_real_repair
```

红的原因（原始输出摘录）：
- E1：`评估 prompt 还在要旧字段 trigger_hit`；
- E2：`事件说明里没写剧情内演戏的冲突不算冒犯 / 触雷`；
- E3：日志为 `would_enter=brewing … trigger_hits=0`，两轮 `trigger` 没被数成触雷；
- E4：日志为 `repair=none`，修复细分没记下。

main 上绿的 2 条都是负对照：
- `test_e3_negative_offended_is_not_a_trigger_hit`
- `test_e4_repair_without_pending_offence_logs_no_repair`

main 上它们绿，是因为旧代码本来就不数事件、也不认 `repair_kind`。**算回归守卫，不算证据**。

本分支完成后应为 `6 passed`。

可行性：Claude 在沙箱里按 §2 做了原型。目标检查加上好感相关的 7 个测试文件共 `171 passed`；`tests/test_estrangement_shadow.py` 有 13 条照预期失败，因为它们测的就是被删的旧字段，按 §5.1 改写。原型跑完已撤回，没有提交。

| # | 检查什么 |
|---|---|
| E1 | 评估 prompt 里没有 `trigger_hit`、`in_story_conflict`、`repair_signal`；JSON 段里有 `"repair_kind"` |
| E2 | 事件判定规则那段里写了「剧情」：剧情内演戏的冲突不算冒犯、不算触雷 |
| E3 | 角色有雷点时，两轮 `trigger` → 影子日志 `trigger_hits=2`、`would_enter=active`、`trigger(2hits)`；负对照：两轮 `offended` → `trigger_hits=0` |
| E4 | 先 `offended`、再 `repair` + `repair_kind=apology` → 日志 `repair=apology`；没有待修复的冒犯时报 `repair`（规则表按 friendly 算）→ 日志 `repair=none` |

## 2. 改动（坐标是 `b03bcd99`）

结构原则（返工经验第 6 条）：
- 事件的词汇（事件名、`repair_kind` 的取值、「剧情内不算」这句话）只在好感模块里：`affinity_protocol.py` 管线上格式，`affinity_service.py` 管怎么记；
- `ChatEngine` 的影子判定只读 `TurnSignal` 的属性，不写任何事件名或字段名字面量。

| 文件 | 位置 | 改什么 |
|---|---|---|
| `core/affinity_protocol.py` | `:11–12` 字段名常量之后 | 加 `FIELD_REPAIR_KIND = "repair_kind"`、`REPAIR_KIND_HELP`（`apology` 道歉 / `explanation` 解释原因 / `action` 用行动补偿 / `soft_spot` 戳中你的软肋）、`_IN_STORY_RULE`（「剧情内演戏的冲突（小说式旁白、剧情动作包裹的争吵）不算 offended / trigger，按 neutral」）。都**只在这里**定义 |
| `core/affinity_protocol.py` | `render_rules`（`:31–42`），「只判断对方这一轮做了哪类事」那一行之后 | 渲染一行 `- {_IN_STORY_RULE}`。`EVENT_HELP` 不改，不把这句话拆进各个事件 |
| `core/affinity_protocol.py` | `render_json_fields`（`:45–51`） | 加一行 `repair_kind`：取值从 `REPAIR_KIND_HELP` 生成，注明「仅事件为 repair 时填，否则 null」 |
| `core/affinity_protocol.py` | `read_verdict`（`:55` 起）之后 | 加 `read_repair_kind(data) -> str`：不认识的值、`None`、缺键都返回 `""` |
| `core/affinity_service.py` | `class AffinityService` 之前 | 新增冻结数据类 `TurnSignal`：字段 `affinity_delta`、`trust_delta`、`guard_delta`、`event`（实际生效的事件，不合法的一轮为 `""`）、`repair_kind`；类方法 `of(...)` 负责「只有 `event == "repair"` 才保留 `repair_kind`」；属性 `is_trigger`。这两条判断**只在这里** |
| `core/affinity_service.py` | `:81` 缓冲的类型、`_record_delta_ring`（`:86–100`）、`get_estrangement_window`（`:102–104`） | 缓冲改存 `TurnSignal`；`_record_delta_ring` 收一个 `TurnSignal`；`get_estrangement_window` 的返回类型同步改 |
| `core/affinity_service.py` | `:272–279` | 删「雷点命中判定规则」「修复信号判定规则」两段 |
| `core/affinity_service.py` | `:293–295` | 删 JSON 段的三行旧字段 |
| `core/affinity_service.py` | `apply_evaluation`（`:344` 起，`:356–363` 的 `apply_event` 与 `:376–382` 的记录） | `apply_event` 成功时，记下 `self.relation.last_event` 作为生效事件；`ValueError`（R16）时记 `""`。用 `TurnSignal.of(..., event=生效事件, repair_kind=affinity_protocol.read_repair_kind(data))` 记一轮 |
| `core/chat_engine.py` | `_shadow_estrangement_check`（`:796–858`） | 改读属性：`e.affinity_delta`、`e.is_trigger`、`e.repair_kind`；触雷次数 = `is_trigger` 的轮数；去掉 `in_story` 的计数和日志字段 |
| `core/evaluation_pipeline.py` | `EvalResult`（`:51–54`）与构造处（`:103–106`） | 删三个旧字段（全仓没有读者，§4 第 6 条） |

为什么记「实际生效的事件」，而不是模型报的那个：规则表会把一些事件改判，例如没有待修复的冒犯时，`repair` 按 `friendly` 算（`affinity_rules.py:90–91`）。疏远检测应该看真正发生了什么，而且这样它和好感用的是同一个结论，不会再出现两套说法。

可行性（第二版原型）：Claude 在沙箱里按本表改完，跑目标检查和好感相关的 17 个测试文件，`289 passed`；改完后 `chat_engine.py` 里不再有事件名字面量（`"trigger"` 只出现在 `TurnSignal.is_trigger`）。原型已撤回，没有提交。

## 3. 规则

| # | 规则 |
|---|---|
| S1 | 冒犯、触雷、修复只由 `affinity_event` 判一次；评估 prompt 里没有任何平行字段 |
| S2 | 剧情内演戏的冲突不算 `offended` / `trigger`，按 `neutral` |
| S3 | `repair_kind` 是 `repair` 的附属字段，字段名与取值只定义在 `affinity_protocol.py`；「生效事件不是 `repair` 时不记」只在 `TurnSignal.of` 判一次 |
| S4 | 影子日志的触雷次数 = 窗口内 `TurnSignal.is_trigger` 为真的轮数；`ChatEngine` 里没有事件名字面量 |
| S6 | 「剧情内演戏的冲突不算」只在 `_IN_STORY_RULE` 定义一次、在 `render_rules` 渲染一次 |
| S5 | 输入不合法的那一轮（R16），环形缓冲记 `event=""`，不当作任何事件 |

## 4. 判断清单（第 7b 条；基线 `b03bcd99`）

执行方第 0 步逐条复核，有一条不成立就停下报告。

| # | 判断 | 读过的行 / 命令 |
|---|---|---|
| 1 | 旧的两段判定规则在评估 prompt 里 | `affinity_service.py:272` `"雷点命中判定规则（用于 trigger_hit / in_story_conflict 字段）：\n"`、`:277` `"修复信号判定规则（用于 repair_signal 字段）：\n"`；下一段从 `:280` `"输出严格JSON格式…"` 开始 |
| 2 | JSON 段里有三行旧字段 | `:293` `'  "trigger_hit": true或false, …'`、`:294` `in_story_conflict`、`:295` `'  "repair_signal": "", …'` |
| 3 | 事件说明里没有剧情内冲突的说法 | `affinity_protocol.py:19` `"offended": "冒犯",`、`:20` `"trigger": "触到雷点",` |
| 4 | 环形缓冲记的是旧字段 | `affinity_service.py:86–100` `_record_delta_ring(… trigger_hit, in_story_conflict, repair_signal)`；`:376–382` 从 `data` 里取 |
| 5 | 影子判定用旧字段数触雷 | `chat_engine.py:807` `raw_trigger_hits = sum(1 for e in window if e["trigger_hit"])`、`:809` 减掉 `in_story`、`:852` 取 `repair_signal`、`:858` 日志带 `in_story=` |
| 6 | `EvalResult` 的三个旧字段没有读者 | `core`、`web` 下 grep `trigger_hit\|in_story_conflict\|repair_signal`，只命中 `evaluation_pipeline.py:52–54`（定义）、`:104–106`（构造）、`affinity_service.py`（prompt 与环形缓冲）、`chat_engine.py`（影子判定）；`.trigger_hit` 等属性读取 0 命中；`scripts/`、`web/frontend/src` 0 命中 |
| 7 | 规则表会把无待修复冒犯的 `repair` 改判成 `friendly` | `affinity_rules.py:90–91`：`if event == "repair" and state.pre_offence is None: event = "friendly"` |
| 8 | `apply_event` 不合法时抛 `ValueError`，`apply_evaluation` 接住、好感不变 | `affinity_service.py:356–363`：`try: self.affinity, self.relation, warns = apply_event(…)` / `except ValueError as exc: logger.warning("Affinity event rejected …")` |
| 9 | 生效事件写在 `relation.last_event` | `affinity_rules.py` `apply_event`：`state = replace(state, …, last_event=event, …)`，其中 `event` 是 `_settle` 规整后的值 |
| 10 | 测这些旧字段的测试只有一个文件 | `tests/` 下 grep 三个旧字段名，只命中 `tests/test_estrangement_shadow.py` |
| 11 | 「急降」用 `MAX_DROP`，本段不动 | `chat_engine.py:814` `sharp_drop = any(e["affinity_delta"] <= -MAX_DROP for e in window)` |

## 5. 测试

### 5.1 必须改写的旧测试（`tests/test_estrangement_shadow.py`）

原型下有 13 条照预期失败，按下表改写，**不许删掉了事**。每条改写后仍守住它原来要守的行为：

| 旧测试 | 改写成 |
|---|---|
| `TestEvalResultCompat` 的 4 条（`test_default_trigger_hit_is_false` 等） | 删掉这组：三个字段已经删除，「默认值兼容」这件事不存在了。另加一条：`EvalResult` 上没有这三个属性 |
| `TestDeltaRing` 的 4 条 | `_run_eval` 去掉三个旧键；窗口里是 `TurnSignal`，断言改读属性 |
| `test_trigger_condition` | 用两轮生效的 `trigger` 事件触发 |
| `test_story_conflict_excluded_from_trigger_count` | 改为：两轮 `neutral`（剧情冲突按 neutral 报）→ 触雷次数 0 |
| `test_repair_signal_logged` | 改为 `repair_kind` 版：先冒犯再修复 |
| `test_ring_recorded_after_apply`、`test_missing_keys_tolerated` | 断言改看 `event` / `repair_kind`；缺 `repair_kind` 时记 `""` |

### 5.2 新增单测

| # | 一侧 | 另一侧 |
|---|---|---|
| T1 | `render_json_fields()` 含 `repair_kind`，取值正好是 `REPAIR_KIND_HELP` 的键 | `read_repair_kind` 遇到不认识的值（如 `"hug"`）、`None`、缺键都返回 `""` |
| T2 | 输入不合法的一轮（未知事件）→ 环形缓冲记 `event=""` | 合法的 `trigger` → 记 `event="trigger"` |
| T3 | `render_rules(...)` 里「剧情内演戏的冲突」正好出现一次 | `EVENT_HELP` 的各项都不含「剧情」（不把这句话拆进各个事件） |
| T4 | `TurnSignal.of(event="repair", repair_kind="apology", …).repair_kind == "apology"` | `TurnSignal.of(event="friendly", repair_kind="apology", …).repair_kind == ""`；`is_trigger` 只对 `event="trigger"` 为真 |

放进 `tests/test_affinity_protocol.py`（T1、T3）和 `tests/test_estrangement_shadow.py`（T2、T4）。

### 5.3 本地只跑受影响的文件（第 15 条）

库用 docker PG（先查 55432 端口和容器名，被占就另起一个，跑完删掉）。

清单来源：在 `tests/` 下 grep `affinity_protocol|affinity_service|AffinityService|evaluation_pipeline|EvalResult|_shadow_estrangement|estrangement`（Claude 在 `b03bcd99` 上实跑），命中 15 个文件。去掉两份非测试辅助文件 `affinity_verdict.py`、`census_llm_call_contexts.py`，再加上：
- 规则表单测 `test_affinity_rules.py`；
- 好感读取接口 `test_affinity_read_api.py`；
- 段 1–3 的三份目标检查。

全跑：

```
python -m pytest tests/test_evaluation_single_judgment_goal.py tests/test_personality_goal.py tests/test_personality_distill_goal.py tests/test_personality_motives_goal.py tests/test_affinity_clamp.py tests/test_affinity_protocol.py tests/test_affinity_rules.py tests/test_affinity_read_api.py tests/test_arc_phase_fields_readers.py tests/test_catchwords.py tests/test_departure_notice.py tests/test_estrangement_shadow.py tests/test_evaluation_pipeline.py tests/test_initial_affinity.py tests/test_live_llm_swap.py tests/test_reunion_greeting.py tests/test_session_identity_injection.py tests/test_storage_scope_lock.py -q
```

已知：`tests/test_arc_phase_fields_unit.py::test_relationship_batch_splits_and_merges` 在 main 上本来就不稳定，不在这份清单里；如果 CI 上它失败，不算本段的问题。合并门是分支 CI。

### 5.4 审计时要验的变异（Claude 审计时跑）

| # | 方向 | 变异 | 应被谁打红 |
|---|---|---|---|
| V1 | 放宽 | 留着「雷点命中判定规则」那段 | E1 |
| V2 | 放宽 | JSON 段不加 `repair_kind` | E1、T1 |
| V3 | 放宽 | `render_rules` 不渲染 `_IN_STORY_RULE` | E2、T3 |
| V4 | 过严 | 触雷次数改成数 `offended` | E3 负对照 |
| V5 | 放宽 | 触雷次数恒为 0 | E3 |
| V6 | 放宽 | 环形缓冲记模型报的事件，而不是生效的事件 | E4 另一侧（无待修复冒犯时的 `repair` 会被记成修复） |
| V7 | 放宽 | `TurnSignal.of` 不看事件、`repair_kind` 一律保留 | E4 另一侧、T4 |
| V8 | 过严 | 不合法的一轮也记成某个事件 | T2 |
| V9 | 放宽 | `read_repair_kind` 不过滤未知值 | T1 |
| V10 | 过严 | `is_trigger` 改成 `event in ("trigger", "offended")` | E3 负对照、T4 |

## 6. 执行方第 0 步（S0）

1. 从 main `b03bcd99` 开分支 `feat/evaluation-single-judgment`。本文件放到 `docs/specs/evaluation-single-judgment.md`，目标检查放到 `tests/test_evaluation_single_judgment_goal.py`，用 `Test-Path` 确认两个文件都在。
2. 逐条复核 §4 判断清单，每条写「成立 / 不成立 + 看到的行」。有一条不成立就停下报告。
3. 跑目标检查，应为 `4 failed, 2 passed`，失败的名字与 §1 一致。不一致就停下报告。
4. 第一个提交只放这两个文件（commit message 用英文），推送。CI 上这一提交应当是红的。
5. 写实现、改写和新增测试，跑 §5.3，推送，等分支 CI 三道门全绿。
6. **不要开 PR，不要合并。** 报告里贴每一步的原始输出、§4 逐条的复核结果、最终提交号、CI 原始输出（`gh run view <id> --json conclusion,jobs`）。

执行中新发现的问题：在本段改动面内的直接修，需要拍板的才停下报告，不自行记账。commit message 一律英文。

## 7. 用哪些 skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 先搜代码库，复核判断清单 |
| 实现 | `@test-driven-development` | 目标检查先红、改写和补单测、再写实现 |
| 交付前 | `@verification-before-completion` | 报告里贴原始输出 |

## 8. Claude 定的取舍（第 28、29 条）

- **修复细分保留成 `repair_kind`，不删。** 依据：它是影子日志里唯一记「怎么修复的」的信息；疏远检测以后要上线，出疏远多半要看修复方式。保留成 `repair` 的附属字段，信息不丢，也不再是一套平行判定。
- **「急降」的含义不改。** 依据：影子模式只写日志；「一轮大档的冒犯」作为急降，是合理的新含义。改不改阈值，应该等疏远检测正式设计时连同真实日志一起定，不在本段动。
- **环形缓冲改成 `TurnSignal` 数据类，而不是继续用字符串键的 dict。** 依据：旧写法里同一组键名在 `affinity_service.py` 写、在 `chat_engine.py` 读，事件名 `"trigger"` 也会散到 `chat_engine.py` 里，换个字段就得改两处（第 6 条）。数据类把「算不算触雷」「修复细分何时保留」收进好感模块，`ChatEngine` 只读属性。
- **「剧情内不算」写成一句常量、渲染一次，不拆进 `offended`、`trigger` 两条说明。** 依据：同一句话写两遍就是两处（第 6 条）。
- **删 `in_story_conflict` 字段，把「剧情内不算」并进事件判定规则。** 依据：它唯一的作用是从触雷次数里减掉剧情冲突；现在由事件判定直接排除，结果一样，判断只在一处。

**要 Shiyu 定的：无。**

## 9. 风险

- 评估 prompt 改了，真模型判事件的倾向可能变。例如剧情冲突以后按 `neutral` 报，好感不再因此下降。这正是本段的目标，但会不会把真冒犯也误判成剧情，只能在演示卡验收时看。验收时专门设一段剧情冲突、一段真冒犯。
- 删掉旧字段后，日志里的 `[estrangement-shadow]` 行少了 `in_story=`。如果有人按这一列写过统计脚本，会受影响。`scripts/` 下 grep 0 命中；服务器上的临时脚本无从得知，**未核实**。
