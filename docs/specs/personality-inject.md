# ③ 性格注入 · 段 1：人格块与好感机制（spec）

基线：main `0b19f1ae`。分支：`feat/personality-inject`（已带本文件、目标检查、夹具、参考实现补丁，并已合入 main `0b19f1ae`）。
坐标说明：下面的行号是在 `4087b7d9` 上读的；`4087b7d9..0b19f1ae`（PR #121，一次性变异驱动重构）没有改动本文件提到的任何文件（`git diff --name-only 4087b7d9 0b19f1ae` 核过），行号仍然有效。
设计依据：设计稿 v6，`git show origin/docs/personality-3a-design:docs/specs/personality-3a-design.md`（`ba1ceb25`）§4、§5、§8。两者不一致时以本文件为准，并停下报告。
参考实现：`docs/specs/artifacts/personality-inject-proto.patch`（`core/` 的 6 个文件 + `tests/test_affinity_rules.py`；在本分支当前头上 `git apply --check` 通过）。它已经让目标检查和规则单测全绿，但只是参考：照它实现可以，S0 和 §5 一条都不能省。

## 0. 目标与分段

目标：原著里不好相处的角色，聊天时不被模型拉回「好说话」；关系真的变近后，在角色自己的范围内变暖。

验收三条（不改字）：①同等好感下，角色之间的差别还在；②低好感时，多轮对话不变软；③低宜人性角色进入亲近档所需轮数，明显多于高宜人性角色。

| 段 | 内容 | 状态 |
|---|---|---|
| **段 1（本文件）** | 5 个存卡字段与登记、人格块的三档做法与分面、好感规则表、评估改报事件、规则状态落库、评估 prompt 清理 | 待实现 |
| 段 2 | 蒸馏模板产出这些字段、引文核对、`motives` 字段 | 段 1 合并后补充到本文件 |
| 段 3 | system prompt 的「## 想要什么」 | 段 2 合并后补充到本文件 |

一个 PR 只做段 1。段 1 合并后的可见变化：所有卡的好感改由规则表计算（设计稿 Q4）；卡上还没有新字段，所以人格块文案不变、亲近条件用默认两条。新字段要等段 2 重蒸才有。

## S0. 开工前逐条复核（有一条不成立就停下报告）

行号是 `4087b7d9` 上的，见开头的坐标说明。

| # | 事实 | 坐标与关键行 |
|---|---|---|
| F1 | 评估在回复之后同步跑 | `core/chat_engine.py:586` `self._evaluate_affinity(user_message, reply)` |
| F2 | 评估调用的 system 固定 | `core/evaluation_pipeline.py:129` `"你是精确的JSON输出器，只输出JSON。"` |
| F3 | 好感现在由模型给绝对值，代码只截断单轮变化 | `core/affinity_service.py:347` `self.affinity = _clamp_delta(old_affinity, data.get("affinity"), …)` |
| F4 | `apply_evaluation` 只有一个生产调用点 | `core/evaluation_pipeline.py:80` `apply_evaluation(data, ctx.old_stage)`；其余调用都在 `tests/` |
| F5 | 人格块的语气是 6 档通用文案 | `core/chat_engine.py:901` `tone_rule = stage_tones.get(stage_name, "自然表现")` |
| F6 | 宜人性文案按分数写死三档 | `core/chat_engine.py:935` `if agreeableness >= 4:` |
| F7 | 投影整卡拷贝，没登记的字段也会带过去 | `core/arc_view.py:203` `proj = ProjectedCard(**card.model_dump())` |
| F8 | 评估结果里的 `reason` 是一段 JSON，单聊群聊都落库 | `core/affinity_service.py:370–378` `extended = {…}` → `self.affinity_reason = _json.dumps(extended, …)` |
| F9 | 单聊落库的是 `get()` 的整份 dict（含 `reason`） | `core/affinity_service.py:294` `to_persist` → `_json.dumps(self.get(), …)` |
| F10 | 群聊只落 5 个标量，其中有 `reason` | `core/evaluation_pipeline.py:200` `update_group_affinity(group_id, card_id, affinity, trust, mood, guard, svc.affinity_reason)` |
| F11 | 群聊恢复走 `load` 的旧格式分支，从 `reason` 解析 | `core/group_session.py:141、241` `load_affinity(prev)`；`core/affinity_service.py:105` `load` |
| F12 | 评估 prompt 的「性格特征」填的是 `values` | `core/affinity_service.py:209` `f"性格特征：{', '.join(_values[:3])}\n"` |
| F13 | 评估 prompt 里有与新规则矛盾的旧句子 | `core/affinity_service.py:233` `"- 连续3轮正面互动才能触发阶段性好感跃升…"`；`:175–` `has_custom_psyche` 两个分支里的「好感围绕基线波动 / 自然回到基线附近 / 基线上移」 |
| F14 | 登记表现有 40 条，锁里写死了这个数 | `tests/test_arc_phase_fields_unit.py:56` `assert len(REGISTRY) == 40` |

**未核实，S0 时请你查清并写进报告：**
- U1：`_post_turn` 有哪些调用点（重新生成、撤回、群聊各走哪条）。每个调用点都要确认经过 `EvaluationPipeline`，否则规则表有旁路。
- U2：`psyche.relational_modes.close` 这类三层路径带「阶段限时值」时投影对不对。原型只在全程值上跑过。段 1 的蒸馏模板不改，卡上不会出现限时值；但本段要加一条专测把它钉住（§5 的 T9），不通过就停下报告。

## 1. 目标检查（先跑它）

`tests/test_personality_goal.py` + `tests/fixtures/personality_goal.json`，已在第 1 个提交里。每个提交都跑：

```
python -m pytest tests/test_personality_goal.py -q
```

- 现在（未实现）：`36 failed, 7 passed`。
- 实现后：`43 passed`。

未实现时绿的 7 条，逐条说明：夹具自检 1 条；负对照 2 条；G9 的 2 条（本来就是「旧卡文案不变」的回归守卫，现在就该绿）；「代码不读记仇」1 条（旧代码也不读，现在就该绿）；大档上限 8 的 1 条（旧上限本来就是 +5 / −8，**巧合的绿，不算证据**）。

它走真实 `ChatEngine`，只换 LLM；桩只回放标签序列。期望值取自夹具 JSON 和本文件已定的几个数（73、3、档位上限 2 / 5 / 8、默认亲近条件），都是测试文件里的字面量，不 import 被测代码。**不要改这个文件来让它变绿**；觉得它有错就停下报告。

| 检查 | 验什么 |
|---|---|
| G0 | 夹具句子两两不同、互不为子串 |
| G1（验收 1） | 同一档位下，prompt 里只有这张卡自己这一档的做法和分面；没有别的卡的，没有通用文案 |
| G2（验收 2） | 全程闲聊 / 客气 / 冒犯进不了亲近档；客气最多涨到基线、单轮最多 2；没冒犯就停在平常档 |
| G3（验收 3 的同卡部分） | 全程做到亲近条件能进亲近档；起点高时正好在第 3 次进入；负对照 |
| G4 | 冒犯后下一轮换冲突档，且优先于亲近档；不再冒犯就退出；修复不超过冒犯前的值 |
| G5 | 模型给的数被档位卡住；档内的数原样生效 |
| G6 | 正向大档要前面连续 2 轮非负 |
| G7 | 评估 prompt 里是这张卡自己的亲近条件；没有才用默认两条；编号对不上的不算数 |
| G8 | 换引擎接着聊（单聊、群聊两种落库形态），次数、待修复的冒犯、冲突档都还在 |
| G9 | 卡上没有新字段、或三档没填全、或没有分面时，人格块仍是现在的文案。**段 1 上线后所有旧卡走的都是这条路** |
| G10 | 评估 prompt：「性格特征」「价值观」各归各位；不再有与新规则矛盾的旧句子；带记仇程度和「不要太快接受道歉」；代码不读记仇和波动 |
| G11 | 用默认亲近条件的卡：门槛同为 3 次；单轮最多涨 5 |

它验不了的：评估模型判得准不准、模型说出来的话软不软、跨角色谁快谁慢。这三样留给③全部合并后的演示卡重蒸验收，由人读。

## 2. 规则（权威表）

评估模型每轮只报四样：`affinity_event`、`affinity_tier`、`affinity_delta`、`met_condition_index`。好感怎么变全在 `core/affinity_rules.py`，别处不许再写一份。`trust`、`guard` 本段不改。

| # | 规则 | 出处 |
|---|---|---|
| R1 | 事件六类：`met_condition`、`friendly`、`neutral`、`offended`、`trigger`、`repair`。敷衍和纯应答算 `neutral` | 设计稿 Q10 |
| R2 | 档位区间：small 1–2，medium 3–5，large 6–8。只取模型给的整数的大小，截到该档区间；正负由事件类别定 | EIBench（arXiv 2606.15532 附录 G）；Q12 |
| R3 | `neutral`：好感不变 | Q6、Q10 |
| R4 | `friendly`：一律按 small 算。好感低于 `affinity_baseline` 才加，最多加到基线；在基线及以上不加 | Q6 |
| R5 | `met_condition`：编号必须在这张卡生效的亲近条件范围内，否则按 `friendly` 算并记 warning。按档加分，不受基线限制，次数 +1 | Q7、Q13 |
| R6 | 正向 large 只在 `met_condition` 且此前连续 ≥2 轮非负时生效，否则按 medium。`neutral` 和被降成 `friendly` 的那一轮都算非负 | EIBench；审计 I5、I6 |
| R7 | `offended` / `trigger`：按档扣分；若没有待修复的冒犯，先记下「冒犯前的值」 | Q9 |
| R8 | `repair`：有待修复的冒犯时按档加分，large 按 medium 算，最多加到「冒犯前的值」；没有待修复的冒犯时按 `friendly` 算 | Q9、Q16 |
| R9 | 「冒犯前的值」的清空：任何非负事件让好感回到或超过它，就清空 | 审计 I7 |
| R10 | 亲近门槛：好感原本 <73 且累计 `met_condition` 不足 3 次时，任何事件都不能把好感带到 73 及以上，封在 72 | 2609.00982、PatientAct；Q13；审计 I8 |
| R11 | R10 优先于 R4：基线 ≥73 的卡，客气也只能到 72。蒸馏模板的基线区间是 25–70，正常不会出现 | 审计 P9 |
| R12 | 代码不读 `volatility`、`grudge_inertia`；两者只写进评估 prompt，由模型判档时考虑 | Q14、Q16 |
| R13 | 亲近条件：卡上有就用卡上的；投影后为空才用默认两条。默认两条只定义一处 | Q7 |
| R14 | 三档取哪一档：上一轮生效的事件是 `offended` / `trigger` → 冲突档（优先）；好感 ≥73 → 亲近档；其余平常档 | 设计稿 §4 |
| R15 | 人格块：卡上三档做法**三句都非空**才用对应那一句取代通用语气；分面非空才用分面行为取代写死的宜人性文案。否则保持现在的文案 | 设计稿 §4，Q2 |
| R16 | 输入不合法（未知事件；非 `neutral` 时档位或整数不合法）：本轮好感和规则状态都不变，记 warning | 设计稿 §5 |
| R17 | 规则状态四项（上一轮事件、`met_condition` 次数、连续非负轮数、冒犯前的值）写进 `reason` 的 JSON（F8），`load` 时读回；旧存档没有这份状态就取默认值。单聊、群聊走同一处 | 设计稿 §5；F8–F11 |

| R18 | 用默认亲近条件的卡（卡上没有亲近条件），单轮最多涨 5；下跌由档位上限管，最多 8 | 设计稿 Q4「这类卡单轮变化不超过现有的 +5 / −8」 |

起点不改：新会话的好感起点继续按关系查表（Q5）。

Q16（记仇不由代码调档，只进评估 prompt）在设计稿里标着「待确认」，本文件按 Q16 写。Shiyu 若改回调档表，只影响 R8、R12 和对应测试，会补充到 §8。

## 3. 改哪些文件

| 文件 | 改动 |
|---|---|
| `core/affinity_rules.py`（新） | 规则表、常量、默认亲近条件、规则状态及其与 dict 的互转。纯函数，无 IO |
| `core/schema.py` | 新增 `RelationalModes`、`AgreeablenessFacet`；`PsycheProfile` 加 `warming_conditions`、`relational_modes`、`agreeableness_facets` |
| `core/card_layers.py` | 登记 5 条：`psyche.warming_conditions`（state / list）、`psyche.relational_modes.close|normal|conflict`（state / scalar）、`psyche.agreeableness_facets`（stable / list） |
| `core/affinity_service.py` | `apply_evaluation` 多收 `psyche`，好感改走规则表；规则状态写进 `extended`、`load` 读回；`AFFINITY_STAGES` 的 73 引用规则模块的常量。评估 prompt：JSON 段换成四个字段；加事件判定规则和亲近条件列表；F12 改成 `personality_traits`，`values` 另起一行；删掉 F13 的旧句子，基线那一段改成只讲「判档依据」。删掉不再有人用的 `AFFINITY_DELTA_UP` / `AFFINITY_DELTA_DOWN`（单轮 +5 的上限改由规则模块的常量管，只此一处），`apply_evaluation` 的文档字符串同步改 |
| `core/evaluation_pipeline.py` | `:80` 传 `ctx.card.psyche` |
| `core/chat_engine.py` | 人格块按 R14、R15 |
| `tests/` | 见 §5 |

不改：数据库结构、蒸馏模板、前端、群聊的落库接口签名。

## 4. 已查实的约束（原始输出）

**登记 5 条字段的连带影响**（原型上，跑了引用 `REGISTRY` / `_leaves` / `card_layers` / `card_draft` / `card_quotes` 的 36 个测试文件）：

```
前 18 个文件：1 failed, 424 passed      ← 唯一失败：test_registry_equals_leaf_set（计数 40 → 45）
后 18 个文件：849 passed, 9 skipped
```

**扫描命令**（上面 36 个文件怎么选的）：

```
grep -lE "REGISTRY|_leaves|card_layers|card_draft|card_quotes" tests/*.py
```

**协议变化要改写的现有测试**（原型 `187d6a99` 上）：

```
python -m pytest tests/test_affinity_clamp.py tests/test_arc_phase_fields_unit.py tests/test_catchwords.py tests/test_estrangement_shadow.py tests/test_evaluation_pipeline.py -q --continue-on-collection-errors
19 failed, 89 passed, 1 error
test_affinity_clamp.py 7 · test_arc_phase_fields_unit.py 1 · test_catchwords.py 1 · test_estrangement_shadow.py 10 · test_evaluation_pipeline.py 整个文件导入失败（共 11 条用例）
```

原因四类：`apply_evaluation` 多了必填参数；断言的是旧协议（模型给好感绝对值）；登记表计数；`test_evaluation_pipeline.py` 从 `core.affinity_service` 导入已删除的 `AFFINITY_DELTA_UP`。**样本范围**：只跑了上面这些文件，没跑全量。别的文件里若还有依赖旧协议的测试，由分支 CI 暴露，按同样四类处理；出现第五类原因就停下报告。

**评估这条路径上已有的机制，逐个核对会不会被改变：**

| 已有机制 | 坐标 | 本段后 |
|---|---|---|
| 模型调用失败重试 1 次，间隔 2 秒 | `core/evaluation_pipeline.py:126–139` | 不变。事件不合法不触发重试，本轮好感不变（R16） |
| 好感单轮截断 +5 / −8 | `core/affinity_service.py:347` | 换成规则表；+5 只留给默认条件卡（R18） |
| 信任、防御的单轮截断 | 同一函数里紧随其后的两行 | 不变 |
| 档位变化标记 `stage_upgraded` | `apply_evaluation` 内，`calc_stage` 之后 | 逻辑不变，由新算出的好感驱动 |
| 影子模式的 delta 环（疏远检测用） | `apply_evaluation` 内 `_record_delta_ring` | 逻辑不变，记的是实际变化量；好感不变的轮次记 0 |
| 起点按关系查表 | 设计稿 C11、C22 | 不变（Q5） |

**规模**（两张样本卡各发一轮，量评估 prompt 的字符数）：改前 3148–3205，改后 3713–3843，每轮多约 570–640 字符（约 +20%）。每轮仍是一次评估调用，没有新增调用。

## 5. 测试

本地只跑受影响的文件；测试库用 `docker compose -f docker-compose.test.yml up -d --wait`（起之前先查 55432 端口和容器名）。合并门是分支 CI。合并只做 git 操作。报告里不要出现本地全量的数字。

**要改写的**：§4 那几个文件。`test_affinity_clamp.py` 里针对好感 +5 / −8 截断的用例已无对应行为，改成对规则表的断言或删除，逐条写明去向；`trust`、`guard` 的截断用例保留。`test_evaluation_pipeline.py` 先修导入。U1 的计数改成 45。

**已随参考补丁给出的**：`tests/test_affinity_rules.py`（52 条），直接测纯函数，覆盖 R2–R18 和登记表的 5 条。照用即可，实现若与它冲突就停下报告。

**要你新增的**：

| # | 测什么 |
|---|---|
| T9 | U2：给 `psyche.relational_modes.close` 写一个阶段限时值，投影到该阶段取到它，投影到别的阶段取到全程值 |
| T10 | 群聊：真实走一遍「评估 → `update_group_affinity` 的参数 → `load_affinity(prev)`」，规则状态还在（G8 的群聊形态是按列名拼的，这条补上真实路径） |

**调用点 × 可观测输出：**

| 调用点 | system prompt | 好感 | 评估 prompt | 落库与恢复 |
|---|---|---|---|---|
| 单聊 `chat` | G1、G2、G4、G9 | G2、G3、G5、G6、G11 | G7、G10 | G8[single]、规则单测 R17 |
| 群聊 | 与单聊同一个 `_build_affinity_persona_block`（S0 的 U1 确认） | 同一个 `apply_evaluation`（F4） | 同上 | G8[group]、T10 |
| 重新生成、撤回 | S0 的 U1 查清后补到这里；有旁路就停下报告 | | | |

**对账表（发出前已在原型 `187d6a99` 上预跑：33 条全红，放宽 17、过严 16；原始输出在 `docs/specs/artifacts/personality-inject-mutations.txt`）：**

| 规则 | 变异 | 打红的检查 |
|---|---|---|
| R2 | 不截断 / 永远取上限 | G2、G5 |
| R3 | 闲聊向基线回落 | G2 |
| R4 | 基线以上也加分 / 基线以下不加分 / 按报的档算 | G2 |
| R5、R13 | 不核对编号 / 永远用默认条件 / 有卡上条件仍追加默认 | G7 |
| R6 | 不设门槛 / 大档一律降档 | G6 |
| R7 | 每次冒犯都重记冒犯前的值 | 规则单测 |
| R8 | 修复不设上限 / 修复不加分 | G4 |
| R9 | 冒犯前的值永不清空 | 规则单测 |
| R10、R11 | 门槛改 2 / 改 4 / 永远不开 / 只拦 `met_condition` | G3、规则单测 |
| R12 | 代码按记仇让小档道歉不加分 | G10 |
| R14 | 冲突档不触发 / 不退出 / 闲聊也触发 / 亲近档优先 | G2、G4 |
| R15 | 有三档仍用通用语气 / 有分面仍用写死文案 / 三档没填全也用 | G1、G9 |
| R16 | 未知事件当闲聊处理 | 规则单测 |
| R17 | 不落库 / 恢复时不读 | G8 |
| R18 | 默认条件卡不设上限 / 所有卡都套上限 | G11、G6 |
| F12 | 「性格特征」仍填 `values` | G10 |

你新写的 T9、T10 各配一条变异并跑，贴原始输出。变异用仓库现成的一次性变异驱动（`tests/perf/mutation_framework.py`，PR #121 刚合入）；**未核实**：我没读过它的接口，按它的实际接口用，用不上就停下报告，不要另写一套。

## 6. 不做、风险、回滚

- 不做：蒸馏模板、引文核对、`motives`、「## 想要什么」、`trust` / `guard` 的机制、前端。
- 风险：评估模型把「客气」报成「做到亲近条件」，规则表挡不住（只有编号核对和 3 次门槛）。上线后看日志里 `met_condition` 的占比和进亲近档的轮数分布。
- 行为变化：所有卡的好感变慢。基线以上靠客气不再涨，这是设计意图（Q4、Q6）。
- 回滚：revert 这个 PR 即可。旧存档的 `reason` 里多一个 `relation` 键，旧代码不读它（F11 的解析只取它认识的键）。

## 7. 自检

| 条目 | 结果 |
|---|---|
| 目标检查先行，原型跑通 | §1；未实现 36 红 7 绿，原型 43 绿 |
| 事实带坐标和读过的行 | S0 的 F1–F14；没读到的标了 U1、U2 |
| 全量扫描 | 没做全量：§4 写明了样本范围和范围之外怎么处理 |
| 一个 PR 一件事 | 段 1；段 2、段 3 另出 |
| 流程按风险配 | 没有结构锁；变异只打规则表 |
| 变异预跑 | 33 / 33，放宽 17、过严 16，原始输出进了 `artifacts/` |
| 往已有路径上加东西的机制表、规模 | §4 |
| 出处对照（设计稿 → 本文件） | §8 末尾，逐条对过 |
| 调用点矩阵 | §5；重新生成、撤回两格待 S0 填 |
| 数值出处 | R2、R6、R10 取自文献；没有自研数值 |

## 8. 补充（审计与后续发现写在这里）

**2026-10-07 原型独立审计（`495ac2cf`）的处理：**

| 审计项 | 处理 |
|---|---|
| P1–P5 目标检查五处漏洞 | 已补：G5 档内原样、G7、G2 平常档断言、G3 正好第 3 次、G6。审计的 5 条存活变异现在都红 |
| P6 标签错位漏修 | 进本段：F12、G10 |
| P7 旧句子残留 | 进本段：F13、G10 |
| P8 登记表 | 进本段：登记 5 条，计数 45 |
| P9 基线 ≥73 的边界 | 写成 R11 |
| P10 脚本形态与设计稿 §9 的表述不一致 | 以本文件 §1 为准 |
| I5–I8 设计稿没写、代码自己定的 | 写成 R6、R9、R10 的明文 |
| main 上两条「巧合的绿」 | 闲聊那条补了平常档断言后已变红；大档上限那条在 §1 注明不算证据 |

**2026-10-07 发出后对照返工经验逐条自查，发现并补上的：**

| 发现 | 违反的条目 | 处理 |
|---|---|---|
| 设计稿 Q4「默认条件卡单轮不超过 +5 / −8」（T16）在第一版 spec 里漏了 | 12（没有逐条对照出处） | 加 R18、G11、规则单测 |
| 设计稿 T21（记仇只进 prompt、代码不读）没有对应测试 | 12 | 加 G10 |
| R15 的回退一侧（没有新字段时文案不变）没有测试，而段 1 上线后所有旧卡走的正是这一侧 | 2、20 | 加 G9 |
| R7、R9、R11、R16 的变异留给了执行方，没有预跑 | 14 | 补规则单测并预跑，33 / 33 |
| 「`apply_evaluation` 只有一个生产调用点」是照抄审计报告，自己没查 | 7a | 已 grep 核实，只有 `evaluation_pipeline.py:80` |
| 没有列评估路径上已有的机制，也没算规模 | 11 | §4 的机制表和规模 |
| 原型里 `AFFINITY_DELTA_UP` / `AFFINITY_DELTA_DOWN` 成了没人用的常量；文档字符串过时 | 6 | 删除、改写，写进 §3 |
| 扫描只写了范围，没贴命令 | 8 | §4 补上 |

**出处对照（设计稿 v6 → 本文件）：**

| 设计稿 | 本文件 |
|---|---|
| §5 每档幅度、T24 | R2 · G5 |
| §5 正向大档门槛、T7 | R6 · G6 |
| §5 基线是一条线、T18 | R4、R11 · G2 |
| §5 无事件不动、敷衍、T17 | R1、R3 · G2 |
| §5 进亲近档的门槛、T8 | R10 · G3、G11 |
| §5 受伤后退、道歉修复、T20、T22 | R7–R9 · G4、G8 |
| §5 记仇（Q16）、T21 | R12 · G10 |
| §5 冲突档触发、§4 三档与分面、T5、T6 | R14、R15 · G1、G2、G4、G9 |
| §5 起点不改（Q5） | §2 末尾 |
| §5 默认亲近条件、T15、T19 | R5、R13 · G7 |
| §5 默认条件卡单轮上限、T16 | R18 · G11 |
| §5 评估解析、T10 | R16 · 规则单测 |
| §5 落库、T9、T22；群聊 T11 | R17 · G8、T10 |
| §5 附带修复、T12 | F12 · G10 |
| T1 登记表 | §3 · 规则单测里的登记表一条、U1 计数 |
| T2–T4、T13、T14（草稿派生、分发、引文核对） | 段 2 |
| T23「## 想要什么」 | 段 3 |
| Q8 用户补写亲近条件 | 不在③，随第四步 |

