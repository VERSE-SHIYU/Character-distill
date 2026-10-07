# ③ 性格注入：③a（存卡与蒸馏）+ ③b-1（人格块与好感机制）设计稿 v2

基线：main `c97116b0`（2026-10-07）。分支：`docs/personality-3a-design`（只含本文件，不动代码）。
v2（2026-10-07）：Shiyu 定 Q1–Q4（§8）；补 Q1 核实结果、Q2 边界、Q4 三点（旧卡意图、事件评估的依据分级、负分统计口径）。
状态：**设计稿**，不是可执行 spec。①补完 B（`arc-phase-unlocated.md`，未推）合并后：先按 B 合并后的 main 复核 §1 全部坐标，再把本稿升为 spec（补变异预跑原始输出、S0、对账表）。
调研：「③ 性格注入：文献调研结论」（含 10-07 代码核对）、「③-好感速度调研」（10-07，方案 B2）。两份在 claude.ai，不进仓库。

## 0. 目的与达成标准

原著里不好相处的角色，聊天时不被模型拉回「好说话」；关系真的变亲近后，在角色自己的范围内变暖。验收原样三条（进 spec 时不改字）：

1. 同等好感下，角色之间的差别还在；
2. 低好感时，多轮对话不变软；
3. 低宜人性角色进入亲近档所需轮数，明显多于高宜人性角色。

范围：③a 新字段与蒸馏；③b-1 人格块（分面、三档语气）与好感机制（方案 B2）、上一轮信号落库、`affinity_service.py:209` 标签错位。③b-2（「## 想要什么」渲染）等②，不在本稿。

## 1. 已查实约束（main `c97116b0`；B 合并后逐条复核）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 字段类别登记表是所有进 prompt 字段的唯一来源；U1 锁「登记表 = `CharacterCard` 叶子全集」，现 37 条（B 改为 40）；`_leaves` 展开 BaseModel 子模型、**不展开** `list[BaseModel]` | `core/card_layers.py:39`；`tests/test_arc_phase_fields_unit.py:38–55` |
| C2 | 草稿形态由登记表派生：state / experience 叶子自动变成 `list[DraftTimed]`（`value` + `occurrences`），`card_from_draft` 的通用循环自动做摘录位置核对、分发到顶层与各阶段 overlay | `core/card_draft.py:102–126`、`:310–326` |
| C3 | 「需要等 G6 阶段的组」由登记表推导：组里有 state / experience 叶子就自动等 G6 | `core/distiller.py:355–363` |
| C4 | 现分组：G2 = 性格、价值观、内在矛盾、情感模式、决策；G4 = `psyche`（整个） | `core/schema.py:240–253` |
| C5 | 分组失败整次蒸馏中止（不会出现「G4 缺失、其余照常」的卡） | `core/distiller.py:2735–2742` |
| C6 | 引文核对清单 `_VERIFIED_TOP` / `_VERBATIM_TOP` 是唯一出处；overlay 下的同名路径由登记表自动补 | `core/card_quotes.py:28–72`（合成清单 `:71–72`） |
| C7 | 人格块：`stage_tones` 6 档通用语气（`:867`）；宜人性 ≥4 / ≤2 / 其余三档写死（`:904–921`）；guard 块 `>60`（`:888`） | `core/chat_engine.py:858–945` |
| C8 | 人格块经 `_build_all_enhancements` 进：一对一（`:333`）、主动消息（`:1474`）、群聊（`group_session.py:159/326/335`）；读的是投影卡 `self.card`（`:146`） | — |
| C9 | 好感评估：一对一与流式都经 `_post_turn` → `_evaluate_affinity`（`chat_engine.py:560`）；群聊 `group_session.py:183/373`、`web/routers/group.py:279`；评估模型 = 聊天模型（`:766`） | — |
| C10 | 评估 prompt 要模型输出好感**绝对值**（0–100），代码只截断单轮变化：好感 +5 / −8，信任 +5 / −8，防御 +8 / −5，psyche 不参与 | `core/affinity_service.py:27–53`、`:347–351`；评估 JSON 段 `:267–283` |
| C11 | 起点由 `_compute_initial_affinity` 按用户身份与卡片关系关键词查表（陌生 15、粉丝 38、相识 50、亲密 82、复杂 68、敌对 10、兜底 40）；不读 `affinity_baseline` | `core/chat_engine.py:1144–1239` |
| C12 | `affinity_baseline` / `volatility` / `grudge_inertia` 只进评估 prompt 文字；代码里没有往基线回落的机制 | `core/affinity_service.py:175–193` |
| C13 | 上一轮的好感变化与雷点命中只在内存环形缓冲（8 轮），从库恢复会话时清空 | `core/affinity_service.py:77–103`、`:113` |
| C14 | 单聊情感状态整块 JSON 落 `affinity_state`（`to_persist` / `from_persist`），加键不需要迁移；群聊另走 `update_group_affinity` | `core/affinity_service.py:294–300`；`core/chat_engine.py:663–676`；`core/evaluation_pipeline.py:200` |
| C15 | 编辑弹窗保存时 `...data` 展开原卡，顶层与 `psyche` 下的新字段原样保留（`character_arc` 被整个重建的问题由 B 修） | `web/frontend/src/components/EditCardModal.jsx:132` |
| C16 | 评估 prompt 第 209 行把 `values` 填在「性格特征」后 | `core/affinity_service.py:209` |
| C17 | 投影对 state / experience 类通用执行（新字段登记后自动按阶段投影） | `core/arc_view.py:181–216` |

## 2. ③a 存卡结构

| 路径 | 类别 | 形态 | 中文名 | 组 | 引文核对 | 说明 |
|---|---|---|---|---|---|---|
| `motives` | state | list | 动机 | G2 | `_VERIFIED_TOP` 加 `motives[]` | 「想要X；为此不惜Y」。只写想要什么和底线，不写具体手段（手段在「情境→做法」） |
| `psyche.warming_conditions` | state | list | 亲近条件 | G4 | 加 `psyche.warming_conditions[]` | 「要什么才肯更近一步」（B2）；对应 2609.00982 每个人设各自的门槛 |
| `psyche.relational_modes.close` | state | scalar | 对亲近的人 | G4 | 加该路径 | 三档关系做法。三个单值叶子而不是列表：投影按阶段取单值，渲染时按档位取一条 |
| `psyche.relational_modes.normal` | state | scalar | 对平常的人 | G4 | 同上 | |
| `psyche.relational_modes.conflict` | state | scalar | 起冲突时 | G4 | 同上 | |
| `psyche.agreeableness_facets` | stable | — | 宜人性分面 | G4 | `_VERBATIM_TOP` 加 `psyche.agreeableness_facets[].quote` | `list[AgreeablenessFacet]`，`AgreeablenessFacet = {facet: 同情/谦恭/信任, level: 低/中/高, behavior: str, quote: str}`。`list[BaseModel]` 按 C1 不展开，登记 1 条 |

- 登记表 + 6 条（`relational_modes` 是子模型，展开成 3 个叶子；分面 1 条；动机 1 条；亲近条件 1 条）。U1 计数在 B 之后的 40 上 +6 = 46。
- `psyche.openness` / `conscientiousness` / `extraversion` / `neuroticism`：stable → **none**（声明更正，10-07 定）；`agreeableness` 保持 stable（旧卡回退文案还读它）。
- 前三类新字段（动机、亲近条件、三档）是 state 类：自动走 C2 的摘录位置核对、B 的改挂与未定位区、C17 的投影。不写任何专用分发代码。
- 新字段放进 `psyche` 而不是顶层（亲近条件、三档、分面）：G4 本来就只有 `psyche`，组划分不用改；它们和雷点、软肋同属「好感机制的输入」，放一处。
- **Q1 核实（Shiyu 要求的两个条件，`c97116b0`）**：
  1. 登记表能把 `psyche.xxx` 声明为 state 类：已有先例 `psyche.triggers` / `psyche.soft_spots`（`core/card_layers.py:61–62`，state / list）。overlay 按同形路径存：`c97116b0` 上 overlay 用带点的扁平键，`psyche.*` 正是 B 发现的 7 个走错路径的字段之一；B 把 overlay 改成与卡片同形后成立。③a 本来就在 B 之后开，满足。三档是三层路径（`psyche.relational_modes.close`），B 的 `get_path` / `set_path` 不限层数，但现有先例最深两层，T14 专测三层。
  2. G4 产出时能拿到阶段：依赖组由登记表推导（`core/distiller.py:355–363`），G4 因含 `psyche.triggers` 等 state 叶子**现在就是依赖组**，U13 断言 `PHASE_DEPENDENT_GROUPS == {"G2","G3","G4"}`（`tests/test_arc_phase_fields_unit.py`）。新增 state 叶子不改变这一点。
  两条都成立 → 放 `psyche` 下，G4 组划分不改。
- 「亲近条件」不是固定参数：按阶段挂（state / list），同三档。

## 3. ③a 蒸馏模板

| 改动 | 位置 | 内容要点 |
|---|---|---|
| 维度 B 后加「动机」 | `_FORMAT_DIMS` G2 | 想要什么、为此不惜做到哪一步；不写手段（手段写在情境→做法）；负面动机（贪、算计、报复）照原文如实写，不美化；按阶段给（`_PHASE_DESCRIPTION_RULE`） |
| 维度 M 扩写 | `_FORMAT_DIM_M` | 加三项：①三档关系做法（对亲近的人 / 平常的人 / 起冲突时，各一句具体做法，按阶段给）；②亲近条件（1–3 条，对方要做到什么，此人才肯更近一步，按阶段给）；③宜人性三分面（同情、谦恭、信任各给高 / 中 / 低 + 一句概括性的行为 + 一段原文摘录） |
| 稳定层不写剧情 | `_STABLE_FIELD_RULE` 引用处（分面） | 「只写概括性的行为，不写具体剧情事件」；分面是 stable，所有阶段都会注入 |
| 模板键 | `_FORMAT_TEMPLATE_KEYS` | `motives` 用 `_TIMED_TPL`；`psyche` 模板加 `relational_modes`、`warming_conditions`、`agreeableness_facets` |
| 大五五个分数 | 维度 M 原句 | 保留（O/C/E/N 继续蒸馏、不注入）；分面打分要求附原文依据 |

模板片段只引用既有规则常量（`_PHASE_DESCRIPTION_RULE`、`_STABLE_FIELD_RULE`、`_TIMED_TPL`），不复制句子（锁 S4 数它们）。

## 4. ③b-1 人格块

| 现状 | 改为 | 条件 |
|---|---|---|
| `stage_tones` 6 档通用语气（C7） | 按档位取 `psyche.relational_modes` 的一句；好感数值、阶段名、心情、内心独白照旧注入 | 投影卡上三档都非空才替换；否则原样 |
| 宜人性三档写死（C7） | 三个分面各一行，只写 `behavior`，不写分数与等级 | 分面非空（Q2：唯一判据，不设 `psyche.profiled`）；否则原样 |
| volatility 两句、雷点、软肋 | 不变 | — |

档位规则（10-07 定，自研）：本轮冲突 → 冲突档（优先）；好感 ≥73 → 亲近档；其余平常档。「本轮冲突」的判定见 §5（方案 B2 下由事件类别触发，取代 `affinity_delta < 0`）。三档内容每轮可能变，留在人格块（system prompt 末段，RAG 之后，本来每轮重建），不进 `card_core`。

## 5. ③b-1 好感机制（方案 B2）

**原则**：模型只报「这一轮发生了哪类事」，状态怎么变由代码决定（2609.00982 的做法）；模型必须选档，不能自由给数（EIBench 的做法）。

**依据分级（Q4 第 2 点）**：「改成事件类别评估能减轻评估偏差」**没有直接的文献证据**，属于**自研，有间接依据**：
- 2609.00982 比的是「训练过的模拟器」与「prompt 驱动的前沿模型」（后者整体偏高 +0.521、门控读数偏离 46%），不是「分类 + 代码」与「模型直接给数」的对比；它的合成流程用「分类 + 代码状态机」，但没有对这一点做消融。
- EIBench 的「必须选档」是设计选择，没有消融。
- 真正不依赖模型宽松程度的，是代码里的门槛（`friendly` 封顶、正向大档门槛、进亲近档要累计 `met_condition`）。这些门槛反过来依赖模型能否把「客气」和「做到了亲近条件」分开 —— 模型若滥标 `met_condition`，门槛就失效。缓解：`met_condition` 必须同时给出满足的是第几条亲近条件（`met_condition_index`），代码核对编号在当前阶段的亲近条件范围内，否则降为 `friendly` 并 warning（T15）。能否真正减偏，只能靠验收第 3 条和上线后监测（`met_condition` 占比、各卡进亲近档轮数）。

**评估输出改动**（`build_evaluation_prompt` 的 JSON 段）：去掉 `affinity` 绝对值，换成：

| 字段 | 取值 |
|---|---|
| `affinity_event` | `met_condition`（做到了此人的亲近条件之一）/ `friendly`（一般友好）/ `neutral` / `perfunctory`（敷衍）/ `offended`（冒犯）/ `trigger`（触雷）/ `repair`（道歉、解释、补偿） |
| `affinity_tier` | `small` / `medium` / `large` |

亲近条件、雷点、软肋随卡片投影一起写进评估 prompt，供模型判 `met_condition` / `trigger`。`trust`、`guard` 本轮不改（见 Q3）。

**代码规则表**（新模块，纯函数；**所有数值是自研，spec 里逐个标出，上线后靠验收看**）：

| 规则 | 草案 | 依据 |
|---|---|---|
| 每档幅度 | small ±1–2，medium ±3–5，large ±6–8（EIBench 的分档） | 文献（档位）；具体取值随 `volatility` 收放是自研 |
| 正向大档门槛 | `large` 正向只在事件为 `met_condition` 且之前连续 ≥2 轮非负时生效，否则降一档 | EIBench「大幅正向通常要前面几轮一致」 |
| 一般友好封顶 | `friendly` 最多 small；好感在基线以上时 `friendly` 不加分 | 自研；目的是不让「客气」把刻薄角色推进亲近档 |
| 进亲近档的门槛 | 好感跨过 73 需要会话内累计 ≥N 次 `met_condition` | 2609.00982「不能跳级」；N 自研 |
| 受伤后退 | `offended` / `trigger` 负向；`grudge_inertia=记仇` 时之后 M 轮正向减半 | 2609.00982 非对称后退；数值自研 |
| 往基线回落 | 无事件（`neutral`）时向 `affinity_baseline` 回落 1 | Kuppens 基线 / Dejonckheere 2019（只有基线和波动有依据） |
| 冲突档触发 | 上一轮事件 ∈ {`offended`, `trigger`} → 冲突档 | 取代 `affinity_delta < 0`（10-07 定为 B2 时随之更新）。**统计口径（Q4 第 3 点）**：评估方式变了，负分 / 冲突事件的分布也会变；旧日志算出的负分占比只作参考，上线后用新评估方式的事件类别重新统计冲突档占比 |
| 起点 | 不改，继续按关系查表（C11） | EIBench 起点按关系 / 情境 |

**落库**：上一轮 `affinity_event`、`affinity_tier`、连续非负轮数、本会话 `met_condition` 计数、记仇剩余轮数写进 `affinity_state`（C14，加键无迁移）；群聊写进 `update_group_affinity` 的同一份 JSON（spec 阶段复核群聊行的结构）。环形缓冲（C13）保留给影子判定，不再是档位的数据源。

**旧卡（Q4 第 1 点：有意改变，不追求还原）**：没有亲近条件 / 三档的卡，评估同样输出事件类别（偏差来自评估模型，与卡片新旧无关）；规则表用默认参数（基线 50、波动适中、不记仇），`met_condition` 不可能出现，进亲近档改由 `friendly` 累计（N 取较大值）。意图写明：**这是有意改变**（目的就是去掉评估偏差），不还原现有曲线；只保留一条不变量 —— 单轮上下限不超过现有的 +5 / −8，旧卡变暖不会比现在快（T16）。上线瞬间所有进行中会话的好感曲线都会变，验收时旧卡抽样 2–3 张一起看。

**附带修复**：`affinity_service.py:209`「性格特征」改读 `personality_traits`，`values` 另起一行「价值观」。

## 6. 规模表

| 组 | 现状输出（四张公版样本，字符数，旧格式偏少） | 新增估算 | 上限 |
|---|---|---|---|
| G2 | 803–1,019 | 动机 2–4 条 ×（40 字 + 每阶段摘录 40 字 × ≤4）≈ ≤800 | `CARD_MAX_TOKENS` 8,192 |
| G4 | 305–339 | 三档 3 ×（≤4 个取值 × 80）≈ ≤960；亲近条件 ≤600；分面 3 × 100 = 300；合计 ≈ ≤1,900 | 8,192 |
| 评估调用 | — | 输出少一个数字、多两个短字段；输入多亲近条件几行 | 现有 |

样本是旧格式卡（无 occurrences），字符数只作量级参考；真实 token 数在演示卡重蒸时核对（并进本来要做的蒸馏，不单独花钱）。

## 7. 测试计划（每条边界两侧都写）

| 编号 | 对象 | 通过侧 | 拒绝侧 |
|---|---|---|---|
| T1 | 登记表 | 6 条新路径在册、类别 / 形态正确；O/C/E/N 为 none | 删任一条 → U1 红；O 改回 stable → 红 |
| T2 | 草稿派生 | `motives`、亲近条件、三档在草稿里是 `list[DraftTimed]` | 分面不是 `DraftTimed`（stable）|
| T3 | 分发 | 动机标全部阶段 → 顶层；只标阶段 2 → 只在阶段 2 overlay | 摘录落在别的阶段 → 按 B 规则改挂（另有一条能通过的标注，防兜底掩盖） |
| T4 | 引文核对 | 分面摘录查得到 → 保留 | 查不到 → 只清空 `quote`，分面条目保留 |
| T5 | 人格块三档 | 三档非空：冲突档 / 亲近档 / 平常档各取对应一句，不出现 `stage_tones` 原文 | 任一档空 → `stage_tones` 原样；冲突与亲近同时成立 → 取冲突档 |
| T6 | 人格块分面 | 有分面 → 三行 behavior，无分数 | 无分面 → 原 if/elif 文案逐字不变 |
| T7 | 规则表 | `met_condition`+large 且前 2 轮非负 → 按 large 生效 | 前 2 轮有负 → 降一档；`friendly`+large → 封顶 small |
| T8 | 亲近门槛 | 累计 N 次 `met_condition` 后可跨 73 | N−1 次时封在 72 |
| T9 | 落库 | 写入 → 新引擎加载 → 冲突档判定与写入前一致 | 只靠环形缓冲（旧做法）的变异 → 恢复后首轮档位错 |
| T10 | 评估解析 | 合法事件 / 档位 → 生效 | 非法枚举 / 缺字段 → 本轮好感不变 + warning（不静默当成 neutral） |
| T11 | 群聊 | 群聊同样走规则表并落群聊行 | — |
| T12 | 标签错位 | 评估 prompt「性格特征」后是 `personality_traits` | 是 `values` → 红 |
| T13 | 分面判据（Q2） | 分面摘录核对失败 → 只清空 `quote`、`behavior` 保留 → 分面非空 → 走分面文案 | 蒸馏漏掉分面（空列表）→ 走旧 if/elif 文案（可接受，写明）；变异「判据改成看 `quote` 非空」→ 第一侧红 |
| T14 | 三层路径 | `psyche.relational_modes.close` 草稿派生为 `list[DraftTimed]`，阶段 2 的取值进阶段 2 overlay 的同形嵌套位置，投影阶段 2 取到 | 投影阶段 1 取不到阶段 2 的取值 |
| T15 | `met_condition` 核对 | 编号在当前阶段亲近条件范围内 → 按 `met_condition` 生效 | 编号越界 / 缺失 / 当前阶段无亲近条件 → 降为 `friendly` + warning |
| T16 | 旧卡默认参数 | 旧卡单轮变化不超过 +5 / −8 | 变异「默认表放宽到 +8」→ 红 |

变异（spec 阶段在沙箱 PG 预跑，两个方向都要有）：放宽 —— 去掉正向大档门槛、`friendly` 不封顶、冲突档忘记读落库值；过严 —— 冲突档对所有负向都触发（含 `neutral`）、亲近门槛 N 恒为无穷、分面存在时仍走旧文案。

## 8. 已定（Shiyu 2026-10-07）

| # | 定为 | 附带要求（已落进本稿） |
|---|---|---|
| Q1 | 亲近条件、三档、分面放 `psyche` 下 | 两个条件已核实成立（§2）；三层路径专测（T14） |
| Q2 | 去掉 `psyche.profiled`，唯一判据「分面非空」（**修改 10-07 的决定**） | 摘录失败只清 `quote` 仍算非空；漏分面走旧逻辑，写明并两侧测（T13） |
| Q3 | 本轮只改好感；信任、防御另起一轮 | 冲突档不依赖 guard，不受影响 |
| Q4 | 所有卡（含旧卡）都改用事件类别评估 | ①旧卡是有意改变，不还原曲线，只保留单轮上下限不变量，验收抽样旧卡（§5、T16）；②「事件评估能减偏」标为自研、间接依据，加 `met_condition_index` 核对（§5、T15）；③负分 / 冲突占比上线后按新评估方式重新统计，旧日志只作参考（§5） |

## 9. 自检表（对照 SPEC-STANDARD 与 ways-of-working）

| 标准 | 本稿 |
|---|---|
| 已查实约束带 commit + 行号 | §1，基线 `c97116b0`；B 合并后整表复核（B 会改 `card_draft` / `phase_anchoring` / 登记表计数） |
| 设计问题先给 2–3 方案 | 好感机制 A/B/C 已在调研定 B2；剩余 Q1–Q4 各给选项 |
| 先找现成库、先搜代码库 | 新字段全部复用登记表派生、通用分发、引文核对、投影；不写专用分发；规则表是纯函数新模块 |
| 每条边界两侧都测 | §7 |
| 变异含放宽与过严 | §7 列出；**预跑原始输出缺**（设计稿阶段，升 spec 时补） |
| 出处标「文献 / 代码事实 / 自研」 | §1 代码事实；§5 每行标依据；所有数值标自研 |
| 规模表 | §6；真实 token 数缺（演示卡重蒸时核） |
| 调用点矩阵 | C8（人格块）、C9（评估）；升 spec 时展开成「调用点 × 可观测输出」 |
| 对账表 | **缺**（设计稿不出对账表；升 spec 时四列填满，不许带「缺」发出） |
| 不进仓库 | 无原文、无卡片；样本只引用字符数 |
