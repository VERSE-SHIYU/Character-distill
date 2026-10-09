# ③ 性格注入 · 段 1：人格块与好感机制（spec）

**状态（2026-10-08）：段 1 已在本分支实现；独立审计（`cf2b90e1`）的发现已处理，见 §8，待复核。** 分支 `feat/personality-inject`，基线 main `0b19f1ae`。分支上没有补丁文件，代码就是实现本身。
本地全量（`tests/test_*.py` 分 5 批）0 失败；合并门仍是分支 CI。
设计依据：设计稿 v6，`git show origin/docs/personality-3a-design:docs/specs/personality-3a-design.md`（`ba1ceb25`）§4、§5、§8。两者不一致时以本文件为准。
坐标说明：S0 的行号是在 main `4087b7d9` 上读的；`4087b7d9..0b19f1ae`（PR #121）没有改动本文件提到的任何文件。

## 0. 目标与分段

目标：原著里不好相处的角色，聊天时不被模型拉回「好说话」；关系真的变近后，在角色自己的范围内变暖。

验收三条（不改字）：①同等好感下，角色之间的差别还在；②低好感时，多轮对话不变软；③低宜人性角色进入亲近档所需轮数，明显多于高宜人性角色。

| 段 | 内容 | 状态 |
|---|---|---|
| **段 1（本文件）** | 5 个存卡字段与登记、人格块的三档做法与分面、好感规则表、评估改报事件、规则状态落库、评估 prompt 清理 | 已实现，待审计 |
| 段 2 | 蒸馏模板产出这些字段、引文核对、`motives` 字段 | 段 1 合并后补充到本文件 |
| 段 3 | system prompt 的「## 想要什么」 | 段 2 合并后补充到本文件 |

一个 PR 只做段 1。段 1 合并后的可见变化：所有卡的好感改由规则表计算（设计稿 Q4）；卡上还没有新字段，所以人格块文案不变、亲近条件用默认两条。新字段要等段 2 重蒸才有。

## S0. 实现前查实的事实（审计时逐条复核）

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

**原先标「未核实」的两处，实现时已查清：**
- U1 评估有没有旁路：没有。`_post_turn` 的 4 个调用点都在 `core/chat_engine.py`（`:443、:465、:485、:583`，普通回复与沉默回复、流式与非流式），都经 `_evaluate_affinity` → `self._pipeline.run(ctx)`；`apply_evaluation` 的生产调用点只有 `core/evaluation_pipeline.py:80`（`grep -rn "apply_evaluation(" core web`）。群聊用的是同一个 `ChatEngine`。
- U2 三层路径的阶段限时值：成立。投影对登记表里的路径是通用的（`core/arc_view.py:205–` `get_path(phases[k - 1].overlay, path)` / `set_path(proj, path, …)`），目标检查 G12 用 `psyche.relational_modes.close` 和 `psyche.warming_conditions` 各钉了一例。（2026-10-09：状态类的「只看阶段 k」语义已被 `docs/specs/state-inertia.md` 取代 —— 阶段 k 没值时沿用 1..k 里最近一次。）

## 1. 目标检查（先跑它）

`tests/test_personality_goal.py` + `tests/fixtures/personality_goal.json`，已在第 1 个提交里。每个提交都跑：

```
python -m pytest tests/test_personality_goal.py -q
```

- 本分支：`49 passed`。
- 只带上这个文件和夹具放到 main `0b19f1ae` 上跑：`39 failed, 10 passed`。

main 上绿的 10 条，逐条说明：夹具自检 1 条；负对照 2 条；G9 的 2 条（本来就是「旧卡文案不变」的回归守卫，在 main 上就该绿）；「代码不读记仇」1 条（旧代码也不读）；大档上限 8 的 1 条、口头禅保存后次数还在的 1 条、G8「到过亲近档换引擎后还在」的 2 条（旧代码的上限本来就是 +5 / −8、也没有次数门槛，所以两次 `met_condition` 从 66 就能自由越过 73，**这四条是巧合的绿，不算证据**）。

它走真实 `ChatEngine`，只换 LLM；桩只回放标签序列。期望值取自夹具 JSON 和本文件已定的几个数（73、3、档位上限 2 / 5 / 8、默认亲近条件），都是测试文件里的字面量，不 import 被测代码。审计时如果觉得它有错，先报告，不要直接改它。

| 检查 | 验什么 |
|---|---|
| G0 | 夹具句子两两不同、互不为子串 |
| G1（验收 1） | 同一档位下，prompt 里只有这张卡自己这一档的做法和分面；没有别的卡的，没有通用文案 |
| G2（验收 2） | 全程闲聊 / 客气 / 冒犯进不了亲近档；客气最多涨到基线、单轮最多 2；没冒犯就停在平常档 |
| G3（验收 3 的同卡部分） | 全程做到亲近条件能进亲近档；起点高时正好在第 3 次进入；负对照；门槛只管第一次进亲近档——到过的掉下去后做到一次就能回去，从没到过的照旧 |
| G4 | 冒犯后下一轮换冲突档，且优先于亲近档；不再冒犯就退出；修复不超过冒犯前的值；起点就在亲近档的关系掉出去后，修复能回到原处 |
| G5 | 模型给的数被档位卡住；档内的数原样生效 |
| G6 | 正向大档要前面连续 2 轮非负 |
| G7 | 评估 prompt 里是这张卡自己的亲近条件；没有才用默认两条；编号对不上的不算数 |
| G8 | 换引擎接着聊（单聊、群聊两种落库形态），次数、待修复的冒犯、冲突档、是否到过亲近档都还在；口头禅单独保存那一次也不丢 |
| G9 | 卡上没有新字段、或三档没填全、或没有分面时，人格块仍是现在的文案。**段 1 上线后所有旧卡走的都是这条路** |
| G10 | 评估 prompt：「性格特征」「价值观」各归各位；不再有与新规则矛盾的旧句子；带记仇程度和「不要太快接受道歉」；代码不读记仇和波动 |
| G11 | 用默认亲近条件的卡：门槛同为 3 次；单轮最多涨 5 |
| G12 | 三档做法、亲近条件只在某个阶段成立时：选了那个阶段才用，别的阶段用全程的。**状态类投影语义 2026-10-09 已被 `state-inertia.md` 取代**（阶段 k 没值就沿用 1..k 最近一次） |

它验不了的：评估模型判得准不准、模型说出来的话软不软、跨角色谁快谁慢。这三样留给③全部合并后的演示卡重蒸验收，由人读。

## 2. 规则（权威表）

评估模型每轮只报四样：`affinity_event`、`affinity_tier`、`affinity_delta`、`met_condition_index`。好感怎么变全在 `core/affinity_rules.py`，别处不许再写一份。`trust`、`guard` 本段不改。

| # | 规则 | 出处 |
|---|---|---|
| R1 | 事件六类：`met_condition`、`friendly`、`neutral`、`offended`、`trigger`、`repair`。敷衍和纯应答算 `neutral` | 设计稿 Q10 |
| R2 | 档位区间：small 1–2，medium 3–5，large 6–8。只取模型给的整数的大小，截到该档区间（报 0 也按该档下界算）；正负由事件类别定。好感始终在 0–100 之间。各种上限只在好感上涨时起作用 | EIBench（arXiv 2606.15532 附录 G）；Q12 |
| R3 | `neutral`：好感不变 | Q6、Q10 |
| R4 | `friendly`：一律按 small 算。好感低于 `affinity_baseline` 才加，最多加到基线；在基线及以上不加 | Q6 |
| R5 | `met_condition`：编号必须在这张卡生效的亲近条件范围内，否则按 `friendly` 算并记 warning。按档加分，不受基线限制，次数 +1 | Q7、Q13 |
| R6 | 正向 large 只在 `met_condition` 且此前连续 ≥2 轮非负时生效，否则按 medium。`neutral` 和被降成 `friendly` 的那一轮都算非负 | EIBench；审计 I5、I6 |
| R7 | `offended` / `trigger`：按档扣分；若没有待修复的冒犯，先记下「冒犯前的值」 | Q9 |
| R8 | `repair`：有待修复的冒犯时按档加分，large 按 medium 算，最多加到「冒犯前的值」；没有待修复的冒犯时按 `friendly` 算 | Q9、Q16 |
| R9 | 「冒犯前的值」的清空：任何非负事件让好感回到或超过它，就清空 | 审计 I7 |
| R10 | **亲近门槛只管第一次进亲近档**：好感原本 <73 且累计 `met_condition` 不足 3 次时（本轮这一次算在内），好感不能到 73 及以上，封在 72。已到过亲近档的关系（含起点就在亲近档）不受此限，修复、做到亲近条件都照常（「到过」的置位时机见 R17） | 2609.00982、PatientAct；Q13；审计 I8；第二轮审计 B2 |
| R11 | R10 优先于 R4：基线 ≥73 的卡，客气也只能到 72。蒸馏模板的基线区间是 25–70，正常不会出现 | 审计 P9 |
| R12 | 代码不读 `volatility`、`grudge_inertia`；两者只写进评估 prompt，由模型判档时考虑 | Q14、Q16 |
| R13 | 亲近条件：卡上有就用卡上的；投影后为空才用默认两条。默认两条只定义一处。按投影后的卡取：所选阶段的在前、全程的在后，`met_condition_index` 按这个顺序编号 | Q7 |
| R14 | 三档取哪一档：上一轮生效的事件是 `offended` / `trigger` → 冲突档（优先）；好感 ≥73 → 亲近档；其余平常档 | 设计稿 §4 |
| R15 | 人格块：卡上三档做法**三句都非空**才用对应那一句取代通用语气；分面非空才用分面行为取代写死的宜人性文案。否则保持现在的文案 | 设计稿 §4，Q2 |
| R16 | 输入不合法（未知事件；非 `neutral` 时档位或整数不合法）：本轮好感和规则状态都不变，记 warning。「上一轮事件」因此也不更新，冲突档会延续到下一次合法评估 | 设计稿 §5 |
| R17 | 规则状态五项（上一轮事件、`met_condition` 次数、连续非负轮数、冒犯前的值、是否到过亲近档）写进 `reason` 的 JSON（F8），`load` 时读回；旧存档没有这份状态、或键的类型不对，就取默认值。**「到过亲近档」的置位时机**：回合开始时好感 ≥73 即记为到过亲近档（含起点就在亲近档）；跨进亲近档的那一轮，落库值在下一轮开头补记。单聊、群聊走同一处。**`reason` 是 `AffinityService` 的只读属性**，由当前状态现算 —— 没有可赋值的字段，就没有「漏打包」；原始 JSON → 状态的解码只在 `AffinityService.parse_reason` 一处 | 设计稿 §5；F8–F11；第二轮审计 B1；第三轮复核 P2 |

| R18 | 用默认亲近条件的卡（投影后卡上没有亲近条件），单轮最多涨 5；下跌由档位上限管，最多 8。按投影后的卡判断，所以只在某个阶段有亲近条件的卡，这条上限会随所选阶段变 | 设计稿 Q4「这类卡单轮变化不超过现有的 +5 / −8」 |

起点不改：新会话的好感起点继续按关系查表（Q5）。

Q16（记仇不由代码调档，只进评估 prompt）在设计稿里标着「待确认」，本文件按 Q16 写。Shiyu 若改回调档表，只影响 R8、R12 和对应测试，会补充到 §8。

## 3. 结构：每个模块只管一件事

| 模块 | 只管什么 | 不管什么 |
|---|---|---|
| `core/affinity_rules.py`（新） | 规则表：一轮事件 → 新好感、新状态。常量、默认亲近条件、规则状态及其与 dict 的互转。纯函数 | 不认识 prompt、落库、引擎 |
| `core/affinity_protocol.py`（新） | 评估协议：四个字段名、给模型看的事件与档位说明、提示词片段的生成、从回答里取字段 | 不算数值；档位区间和事件名从规则表的常量生成，不手写第二份 |
| `core/schema.py` | 数据模型自己回答「三档填全了吗」「这一档是哪句」「分面行为有哪些」 | — |
| `core/affinity_service.py` | 持有状态、拼评估 prompt、调规则表、由状态**现算** `reason`（只读属性）、从 `reason` 解码回状态（解码只在 `parse_reason` 一处） | 不含任何好感数值规则，不含字段名字面量 |
| `core/chat_engine.py` | 人格块里取一句、列几行 | 不判断填没填全，不决定哪一档 |
| `core/evaluation_pipeline.py` | 把这张卡的画像传给 `apply_evaluation`（`:80`） | — |
| `core/card_layers.py` | 登记 5 条：`psyche.warming_conditions`（state / list）、`psyche.relational_modes.close|normal|conflict`（state / scalar）、`psyche.agreeableness_facets`（stable / list） | — |

规则表内部分三步：规整事件（降级）→ 按档算变化 → 取所有上限里最低的一条。**以后加一条上限规则，只在 `_CEILINGS` 里加一个函数；加一个事件类别，改 `EVENTS` 和协议模块的 `EVENT_HELP` 两处（有断言守着两者一致）。**

`core/affinity_service.py` 同时做了的清理：F12 改成 `personality_traits`，`values` 另起一行；删掉 F13 的旧句子，基线那一段改成只讲「判档依据」；删掉不再有人用的 `AFFINITY_DELTA_UP` / `AFFINITY_DELTA_DOWN`。

不改：数据库结构、蒸馏模板、前端、群聊的落库接口签名。

## 4. 已查实的约束（原始输出）

**登记 5 条字段的连带影响**（实现前在原型上查的；实现后本地全量 0 失败，结论不变。当时跑了引用 `REGISTRY` / `_leaves` / `card_layers` / `card_draft` / `card_quotes` 的 36 个测试文件）：

```
前 18 个文件：1 failed, 424 passed      ← 唯一失败：test_registry_equals_leaf_set（计数 40 → 45）
后 18 个文件：849 passed, 9 skipped
```

**扫描命令**（上面 36 个文件怎么选的）：

```
grep -lE "REGISTRY|_leaves|card_layers|card_draft|card_quotes" tests/*.py
```

**改写了的现有测试**（原因都是协议变了，不是实现有错）：

| 文件 | 改了什么 |
|---|---|
| `tests/affinity_verdict.py`（新，测试辅助） | 把「这一轮好感要变多少」翻成评估协议的字段。旧测试表达意图只走这一处，字段名不散落 |
| `tests/test_affinity_clamp.py` | 7 条：好感的截断改成对规则表的断言（默认条件卡 +5、下跌 −8、档内原样）；`trust` / `guard` 的截断用例没动 |
| `tests/test_estrangement_shadow.py` | 10 条：辅助函数 `_run_eval` 把「目标好感」翻成事件，各用例的意图不变 |
| `tests/test_evaluation_pipeline.py` | 评估回复改成报事件；手写的画像替身换成真的 `PsycheProfile`；新增一条群聊落库恢复 |
| `tests/test_catchwords.py` | 1 条：补参数 |
| `tests/test_departure_notice.py` | 4 条：手写的画像替身（`SimpleNamespace`）没有新字段，换成真的 `PsycheProfile` |
| `tests/test_arc_phase_fields_unit.py` | 登记表计数 40 → 45 |

`test_departure_notice.py` 不在最初抽样的 5 个文件里，是本地全量跑出来的。

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

| 测试文件 | 条数 | 验什么 |
|---|---|---|
| `tests/test_personality_goal.py` | 49 | 目标检查（§1） |
| `tests/test_affinity_rules.py` | 58 | 规则表 R2–R18，纯函数；登记表的 5 条 |
| `tests/test_affinity_protocol.py` | 3 | 提示词里的事件名、档位区间来自规则表；字段名读写一致 |
| `tests/test_evaluation_pipeline.py` 里新增的一条 | 1 | 群聊：真实走「评估 → `update_group_affinity` 的参数 → 按 5 列 `load`」，规则状态还在 |
| `tests/test_catchwords.py`（改写的两条 + 新增的一条） | 3 | 改口头禅后不调任何打包函数，`reason` 里就有新口头禅与规则状态；`reason` 不可赋值（赋值抛 `AttributeError`）；`parse_reason` 五种输入的形状（合法 dict / 纯文本 / 非 dict JSON / 空串 / None） |

**调用点 × 可观测输出：**

| 调用点 | system prompt | 好感 | 评估 prompt | 落库与恢复 |
|---|---|---|---|---|
| 单聊 `chat`、`chat_stream`（含沉默回复，U1） | G1、G2、G4、G9、G12 | G2、G3、G5、G6、G11 | G7、G10、G12 | G8[single]、规则单测 R17 |
| 群聊 | 同一个 `ChatEngine`（U1） | 同一个 `apply_evaluation`（F4） | 同上 | G8[group]、`test_group_row_carries_the_rule_state_and_restores_it` |
| 重新生成、撤回 | 没有单独的评估入口：改好感的只有 `apply_evaluation` 一处（U1） | 同上 | 同上 | 同上 |

**对账表（在本分支的实现上跑：48 条全红，放宽 24、过严 24。脚本 `docs/specs/artifacts/personality_inject_mutations.py`，用仓库的 `tests/perf/mutation_framework.py` 的 `run_oneoff` 跑，带基线门和按字节还原；原始输出在同目录的 `personality-inject-mutations.txt`）：**

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
| R10、R11 | 门槛改 2 / 改 4 / 永远不开 / 只拦 `met_condition`；忽略「到过亲近档」（G4 打红） / 起点就在亲近档的不算到过（#16，G4 打红） / 所有关系都当作到过亲近档（G3 打红） / 起点正好 73 不算到过（`>=`→`>`，规则单测打红） | G3、G4、规则单测 |
| R12 | 代码按记仇让小档道歉不算数 | G10 |
| R14 | 冲突档不触发 / 不退出 / 闲聊也触发 / 亲近档优先 | G2、G4 |
| R15 | 有三档仍用通用语气 / 有分面仍用写死文案 / 三档没填全也用 | G1、G9 |
| R16 | 未知事件不拒绝 | 规则单测 |
| R17 | 不落库 / 恢复时不读 / 恢复时不读「到过亲近档」（G8 打红） / 纯文本 `reason` 不再落到 `inner_voice`（`test_catchwords.py` 打红） | G8、`test_catchwords.py` |
| R18 | 默认条件卡不设上限 / 所有卡都套上限 | G11、G6 |
| F12 | 「性格特征」仍填 `values` | G10 |
| 协议 | 读回答时读回旧字段 / 评估 prompt 不带亲近条件 | G2、G7 |
| 两轮审计里存活过的 | 修复的大档不降档 / 好感不设下限 / 评估 prompt 不带这张卡的记仇值 / `trigger` 不记冒犯前的值 / 修复被门槛拦住 / 口头禅保存不带规则状态 / 脏的事件值让恢复崩掉 / 关掉 R18 后服务层用例仍绿 | 规则单测、G4、G8、G10、`test_catchwords.py`、`test_affinity_clamp.py` |


## 6. 不做、风险、回滚

- 不做：蒸馏模板、引文核对、`motives`、「## 想要什么」、`trust` / `guard` 的机制、前端。
- 风险：评估模型把「客气」报成「做到亲近条件」，规则表挡不住（只有编号核对和 3 次门槛）。上线后看日志里 `met_condition` 的占比和进亲近档的轮数分布。
- 行为变化：所有卡的好感变慢。基线以上靠客气不再涨，这是设计意图（Q4、Q6）。
- 回滚：revert 这个 PR 即可。旧存档的 `reason` 里多一个 `relation` 键，旧代码不读它（F11 的解析只取它认识的键）。

## 7. 自检

| 条目 | 结果 |
|---|---|
| 目标检查先行 | §1；main 上 39 红 10 绿，本分支 49 绿 |
| 事实带坐标和读过的行 | S0 的 F1–F14；U1、U2 已查清 |
| 全量扫描 | 没做全量：§4 写明了样本范围和范围之外怎么处理 |
| 一个 PR 一件事 | 段 1；段 2、段 3 另出 |
| 流程按风险配 | 没有正则结构锁：`reason` 不可赋值由语言保证（B1）；变异只打规则表 |
| 变异 | 48 / 48，放宽 24、过严 24，脚本和原始输出都在 `artifacts/`，走仓库的变异驱动 |
| 一条规则只写一处 | 档位区间、事件名、字段名、默认亲近条件、73、单轮 +5 各只定义一处（§3） |
| 往已有路径上加东西的机制表、规模 | §4 |
| 出处对照（设计稿 → 本文件） | §8 末尾，逐条对过 |
| 调用点矩阵 | §5，没有空格 |
| 数值出处 | R2、R6、R10 取自文献；没有自研数值 |

## 8. 补充（审计与后续发现写在这里）

**2026-10-07 原型独立审计（`495ac2cf`）的处理：**

| 审计项 | 处理 |
|---|---|
| P1–P5 目标检查五处漏洞 | 已补：G5 档内原样、G7、G2 平常档断言、G3 正好第 3 次、G6。审计的 5 条存活变异现在都红 |
| P6 标签错位漏修 | 已修：F12、G10 |
| P7 旧句子残留 | 已修：F13、G10 |
| P8 登记表 | 已做：登记 5 条，计数 45 |
| P9 基线 ≥73 的边界 | 写成 R11 |
| P10 脚本形态与设计稿 §9 的表述不一致 | 以本文件 §1 为准 |
| I5–I8 设计稿没写、代码自己定的 | 写成 R6、R9、R10 的明文 |
| main 上两条「巧合的绿」 | 闲聊那条补了平常档断言后已变红；大档上限那条在 §1 注明不算证据（2026-10-08 加 G8「到过亲近档换引擎后还在」两条后，§1 的巧合绿共四条） |

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

**2026-10-08 结构重排（Shiyu 指出：分支是红的、靠补丁文件交付、没有做好隔离与复用）：**

| 原来的问题 | 根因 | 现在 |
|---|---|---|
| 分支上只有红的目标检查和一个 `.patch`，实现要别人去套 | 把「参考实现」当成了交付物 | 实现直接在分支上，补丁文件删除，全量 0 失败 |
| 档位区间在规则表里是常量，在评估 prompt 里又手写了一遍「1–2 / 3–5 / 6–8」 | 一条规则写了两处 | 提示词片段由协议模块从规则表常量生成 |
| 四个字段名同时出现在 prompt 字符串和 `data.get(...)` 里 | 协议没有自己的归属 | 新增 `core/affinity_protocol.py`，字段名只在那里 |
| 「三档填没填全」「有没有分面」的判断写在引擎的人格块里 | 数据的问题让引擎回答 | 挪到数据模型的属性上，引擎只取一句、列几行 |
| 规则函数是一串 if / elif，每加一条上限就再补一个分支 | 没有抽出「上限」这个概念 | 上限成了一张函数表，取最低的一条 |
| 旧测试各自手写「模型给目标好感」的 JSON | 没有共用的测试辅助 | `tests/affinity_verdict.py` 一处翻译 |
| 测试里手写的画像替身缺新字段 | 替身和 schema 走散 | 换成真的 `PsycheProfile` |

**2026-10-08 段 1 实现的独立审计（`cf2b90e1`，结论：不通过）的处理：**

| 审计项 | 属于 | 根因 | 处理 |
|---|---|---|---|
| B1 反思触发后规则状态丢失 | 实现偏离 R17 | `reason` 的 JSON 有两个写入处（服务、引擎的口头禅保存），新键只加了一处 | 打包收拢成 `AffinityService.pack_reason` 一处，引擎改为调用它；加结构锁、G8 的口头禅用例、服务层用例 |
| B2 起点 ≥73 的关系掉出亲近档后永远修不回去 | spec 本身的冲突（R8 对 R10） | R10 写成了「任何事件」，没想到修复是回到已有的位置 | **Shiyu 定为 b**：门槛只管第一次进亲近档，已到过亲近档（含起点就在亲近档）的关系不再受门槛限制。理由：这符合门槛本意（只拦「第一次挣到亲近档」）；只放开 `repair` 会出现「道歉比做到亲近条件更管用」的倒挂。副作用：在旧代码下就已经 ≥73 的存档，加载后的第一轮开头（回合开始好感 ≥73）即补记为到过；曾经到过、现在已掉到 73 以下的旧存档无从得知，仍受门槛。R10 改写、G4 加一条、规则单测改写 |
| T1 `test_affinity_clamp.py` 声称测 R18 实际没测 | 测试漏洞 | 新状态下大档先被 R6 降成中档，中档上限本来就是 5 | 先让大档生效再断言；变异「关掉 R18」现在能打红它 |
| T2 修复的大档按中档算没测到 | 测试漏洞 | 用例里冒犯只扣 5，中档和大档结果一样 | 加一条冒犯前的值离得远的用例 |
| T3 `trigger` 单独发生时记不记冒犯前的值没测 | 测试漏洞 | 用例里 `trigger` 只出现在 `offended` 之后 | 加一条 |
| T4 G10 断言 prompt 里有「记仇」二字，说明文案里本来就有 | 测试漏洞 | 断言的不是这张卡的取值 | 改成断言方括号里的取值，并换一个取值再断言一次 |
| T5 好感下限 0 没测 | 测试漏洞 | — | 加上下限两头的用例；R2 写明 0–100 |
| I1 `last_event` 是列表或字典时恢复崩掉 | 实现偏离自身契约 | 用集合的成员判断去查不可哈希的值 | 先判是不是字符串；用例加脏数据 |
| S2 疏远检测里的 `-8` 是档位上限的又一份字面量 | 结构 | 一个数写了两处 | 规则表导出 `MAX_DROP`，疏远检测引用它 |
| 变异只有结果、没有可复跑的脚本 | — | 用了一次性脚本 | 改写成仓库约定的脚本，走 `run_oneoff` |
| 6 条「代码自己定了、spec 没写」 | spec 缺口 | — | 写进 R2、R10、R13、R16、R18 的明文 |
| S1 评估 prompt 里 `trigger` / `repair` 事件和原有的 `trigger_hit` / `repair_signal` 是两套平行判定 | spec 没覆盖 | 后两个字段只供疏远检测的影子日志用（`core/chat_engine.py` 的 `_shadow_estrangement_check`），本段没动它们 | **未处理，等 Shiyu 定**。建议：好感只认事件；`trigger_hit` 改由代码从事件派生，提示词里删掉它的判定段；`repair_signal` 留作 `repair` 的细分；「剧情内冲突」写明不算冒犯。这会动疏远检测的输入，另开一步做 |
| delta 环里的键 `affinity_delta` 与协议字段同名、含义不同 | 结构，低 | 历史命名 | 未改：改任何一边都要动已有数据或设计稿定的字段名。记在这里 |
| `test_relationship_batch_splits_and_merges` 在审计方本地不稳定 | 与本分支无关 | 断言并发批次的完成顺序 | 未处理，不在本段范围；我这边全量时它是绿的 |

**2026-10-08 收尾 b 与第三轮独立复核（`8766ef65`）：**

| # | 发现 | 根因 | 处理 / 现状 |
|---|---|---|---|
| P4 | 变异 #16（起点就在亲近档的不算到过）在 `0ea62920` 上仍存活 | `reached_close` 在 `apply_event` 里置位两次（`:146` 回合前、`:158` 回合后），同一条规则写了两处 | `8766ef65` 已删掉回合后那一处，置位只在 `:146`；本轮新变异「起点正好 73 不算到过」（`>=`→`>`）在规则单测上打红 |
| P2 | 上表 B1 行把根因记成「`reason` 的 JSON 有两个写入处」，认定不准 | 真正的问题是「派生值被缓存」：`affinity_reason` 是可赋值字段，内容完全由别的字段算出，每改一项状态都得记着重新打包；正则结构锁只是在守这份缓存，拦不全 | 改为「派生值不存、读时现算」：`affinity_reason` 改成 `AffinityService` 的只读属性，由当前状态现算；删 `pack_reason`、引擎的 `_affinity_reason` 转发与开局两处赋值、`_compute_initial_affinity` 里的 `reason` 键；解码收拢到 `parse_reason` 一处。结构锁删除，换成「赋值抛 `AttributeError`」。**本条取代上表 B1 行的处理** |
| P1 | R10 门槛边界（好感正好 73）没有两侧用例 | 复核实跑发现：把 `affinity_rules.py:146` 的 `>=` 改成 `>` 后变异存活 | 已补：`test_r10_reaching_close_is_recorded` 里回合开始 73 → `reached_close is True`；72 → `False` |
| P5 | §7「流程按风险配」写「没有结构锁」，在 `8766ef65` 上已不成立（B1 那次加了正则结构锁） | 文档没跟上代码：加了锁，§7 那句没同步改 | P2 删掉正则锁后，§7 改为「没有正则结构锁：`reason` 不可赋值由语言保证」，与代码一致 |
| P6 | delta 环里的键 `affinity_delta` 与评估协议字段同名不同义 | 历史命名 | §8 第二轮审计表已记；本段不改（改任一边都要动已有数据或设计稿定的字段名） |
| 行为变化 | 旧代码里 `reason` 若是非 dict 的 JSON（如 `"[1]"`），`load` 会抛 `AttributeError`（`except` 只接 `JSONDecodeError`/`TypeError`） | 解码后没检查结果是不是 dict，`.get` 在 list 上直接抛 | 现在 `parse_reason` 对非 dict 结果返回 `None`，按纯文本落到 `inner_voice`；`test_parse_reason_shape_matrix` 覆盖 |

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
| §5 落库、T9、T22；群聊 T11 | R17 · G8、`test_group_row_carries_the_rule_state_and_restores_it` |
| §5 附带修复、T12 | F12 · G10 |
| T1 登记表 | §3 · 规则单测里的登记表一条、U1 计数 |
| T2–T4、T13、T14（草稿派生、分发、引文核对） | 段 2 |
| T23「## 想要什么」 | 段 3 |
| Q8 用户补写亲近条件 | 不在③，随第四步 |

