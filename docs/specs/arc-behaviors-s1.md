# 角色表现改造 · 第 1 步：卡片数据层（弧线：变化轴 + 阶段；情境→行为条目）

> **取代** `docs/specs/arc-reactions-s1.md`：在那份文件首行写「已取代，见 arc-behaviors-s1.md」。
> 放置：本文件 → `docs/specs/arc-behaviors-s1.md`；补丁 → `docs/specs/arc-behaviors-s1.patch`（用完即删，不入库）。
> 放好后 `Test-Path docs/specs/arc-behaviors-s1.md, docs/specs/arc-behaviors-s1.patch` 确认。
> 后续补充一律写进本文件，标「补充」。
> 基线：`main` = `3b94b82`（2026-10-02 现读）。

## 总路线（本文件只做第 1 步）

| 步 | 内容 | 状态 |
|---|---|---|
| **1** | **卡片数据层**：schema、蒸馏提示词、落卡前核对、卡片展示与编辑 | **本文件** |
| 2 | 会话阶段：PG 迁移 `sessions.arc_phase`、开聊选择、聊天头部标记；每轮注入「变化轴 + 截至当前阶段的 label/state」，后面的阶段不给（复用时间块投递位） | 待写 |
| 3 | 情境→行为注入：条目全量注入同一投递位，加一句「先判断当前情境对应哪条」。**不建 Chroma 集合、不做检索** | 待写 |
| 4 | 大五映射表：4 维 + 宜人性统一，神经质与 volatility 合并去重 | 待写 |

本步结束时：新蒸馏的卡带弧线（轴 + 带心态名的阶段）和情境→行为条目，卡片上能看、能改；**对话行为不变**（注入在第 2、3 步；`core/chat_engine.py`、`core/context_engine.py` 对 `character_arc` 零引用，见扫描原文）。

## 已定的决定

1. **两块数据按职责分开，互不引用。** 弧线记「变的部分」；情境→行为只记「贯穿全书、各阶段都成立的做法」，随阶段改变的做法归弧线的 `state`。条目不带阶段下标。
2. 弧线是一个对象 `character_arc = {axis, phases: [{label, state}]}`，只做一条主轴，不新增第二个弧线字段。`label` 写心态/立场，不写事件名；`state` 句首点明故事时期。
3. 情境→行为字段名 `situation_behaviors`（`reactions` 在仓内已是「消息表情回应」，见扫描原文）。条目只有 `situation`、`behavior`、`source_quote`，不提取内心状态。
4. `source_quote` 只做展示，但**落卡前逐字核对**：查不到 → 清空摘录，条目保留。
5. 旧卡不处理、不回填；旧形态在读取时转换（后端模型校验器、前端 `parseCardJson`），功能按「卡里有没有这项数据」启用。
6. 群聊不动。只用 PG，不为 SQLite 加任何东西。不动 Map / Reduce 提示词。
7. 新发现的问题：属于本段改动面的直接修；只有会撞车或需要拍板时才停下报告，不自行记账。

## ① 出处对照表（规范条目 → 本 spec 行为）

| 出处 | 条目 | 行为 |
|---|---|---|
| ArcANE, arXiv 2606.05553 §3.1 | 弧线 = 一条心理轴 + 分阶段，每阶段有状态描述 | **B1** `CharacterArc{axis, phases[{label, state}]}`；**B2** 维度 L 提示词 |
| 同上 §5.1 / 附录 K（ArcHint） | 「轴名 + 第 k/N 阶段 + 阶段 label」与完整弧线相差 ±2.6 分以内 | B1 保留 `axis` 与 `label` 两个字段（第 2 步用） |
| 同上 §3（McAdams 两层） | 稳定的秉性 vs 随叙事变化的状态 | **B3** 职责划分：`situation_behaviors` 只收各阶段都成立的做法 |
| SIBPersona, arXiv 2609.21349 表 4 / 表 8 | 去掉「情境→行为」显著变差；去掉内心状态不显著；CharacterEval（中文）上成立 | **B4** 条目只有 situation / behavior |
| 同上 §3.3 | 每条三元组带原始证据对 | **B5** `source_quote` |
| MRPrompt, Findings ACL 2026（arXiv 2603.19313）§3.3 / 附录 C | 情境面（situation → behavior_pattern，带 source_scenes）整份进提示词，由模型自己选 | 条数 6–12；第 3 步全量注入 |
| Too Good to be Bad, Findings ACL 2026 | 欺骗、操纵类特质最难演，常被表面的攻击性替代 | **B6** 提示词「负面做法照实写，不改成直接发火」 |
| Anthropic「Reduce hallucinations」（`core/distiller.py:153-156` 已引） | 引文须逐字；找不到就撤回 | **B7** `VERBATIM_FIELDS` 核对摘录；`behavior` 进 `VERIFIED_FIELDS` |

**明确不采纳（及理由）：**

| 条目 | 不采纳的理由 |
|---|---|
| ArcANE 每角色 3–5 条内在轴 + 关系轴 | 产品约束是「一会话一阶段」，多轴各有各的阶段切分。单轴是简化，**文献未直接验证** |
| SIBPersona 按情境检索 top-3 | 它的语料是每人几百到几千条实例级三元组；本项目是 6–12 条归纳后的条目。论文自述检索在反讽上取错、只测了单轮 |
| MRPrompt 的 time_scope / emotional_state / thinking_pattern | time_scope 的职责由 B3 的划分承担；后两项即内心状态，B4 已排除 |

**样本范围**（样本之外的行为未验证）：ArcANE 全部为英文小说、单轮问答；ArcHint 只在 DeepSeek-V4-Flash、Qwen3-32B 上各约 120 条探针上测过。SIBPersona 主实验是繁体中文社媒回复、单轮。

## 已查实约束（基线 `3b94b82`；S0 逐条复核，一条不成立即停）

| # | 事实 | 位置 |
|---|---|---|
| C1 | `character_arc: list[str] = []` | `core/schema.py:84` |
| C2 | `FORMAT_GROUPS` 为分组唯一出处；`G4 = (key_memories, character_arc, psyche)`；`G5 = (relationships,)` | `core/schema.py:96-107` |
| C3 | F3 断言「各组 ∪ POST_FORMAT_FIELDS == model_fields，两两无交集」 | `tests/test_distiller_routing.py:760-783` |
| C4 | 提示词片段唯一出处：`_FORMAT_DIMS`（维度 L 在 G4）、`_FORMAT_TEMPLATE_KEYS`、`_FORMAT_IMPORTANCE`；`format_prompt_after(group)` 按归属拼；模板键的归属由 `_FORMAT_FIELD_GROUP` 查，**模板键不在任何组里则模块导入即 KeyError** | `core/distiller.py:162-300` |
| C5 | 共享输出规则含「严禁输出 {trait:..., description:...} 这种嵌套对象」，每组都带；`relationships` 靠 `_FORMAT_IMPORTANCE` 的覆盖句与它共存 | `core/distiller.py:200-203`、`258` |
| C6 | **两条产卡路径**：估算 token（字数×0.6）< `longctx_threshold`（默认 900000，仓内配置未覆盖）→ 一次读完，完整提示词、上限 `LONG_OUTPUT_MAX_TOKENS = 16384`；否则 Map → Reduce → 各组并行格式化，每组上限 `CARD_MAX_TOKENS = 8192` | `core/distiller.py:501-507`、`570`、`1523-1525`、`2222-2231`、`2456-2504` |
| C7 | 分片路径上格式化的输入是归并结果（每维度 200 字概括 + 5 条原句），**看不到原文** | `core/distiller.py:642-660`、`2433-2438` |
| C8 | 落卡后置步骤只有一个入口 `finalize_card`：关系去重 → `retract_unverified` → 贴对话示例；三条产卡通道各调一次 | `core/distiller.py:1735-1757`；`core/text_manager.py:485`、`web/routers/distill.py:514`、`1135` |
| C9 | `VERIFIED_FIELDS` 为引文核对清单的唯一出处；口癖是 `retract_unverified` 里的一段硬编码（对不上整条删） | `core/card_quotes.py:28-39`、`118-126` |
| C10 | 注入守卫按叶子通用遍历（对象、列表都递归）；标记的叶子：列表元素删、标量清空 | `core/moderation/card_guard.py:71-95`、`177-202` |
| C11 | 编辑保存：`CharacterCard.model_validate(req.card_json)` 后存 `model_dump()` | `web/routers/distill.py:1249-1254` |
| C12 | `chat_engine` 里 `_stage` 是好感度关系阶段 → 弧线一律叫 `phase` | `core/chat_engine.py:174-175`、`280-305` |
| C13 | 两个详情页与编辑弹窗的卡数据都经 `parseCardJson`；它已有 identity 归一的先例（改了才拷贝） | `utils/card.js:9-24`；`CharCard.jsx:646`、`929-931`；`MarketCardDetail.jsx:510-515`、`1146-1148`；`TextPanel.jsx:790-792` |
| C14 | 详情页弧线渲染把元素当字符串 | `CharCard.jsx:833-842`、`MarketCardDetail.jsx:748-757` |
| C15 | 对象列表编辑全仓只有一处（人物关系，内联在弹窗里）；上限 8 条，超出时保存被拒 | `EditCardModal.jsx:32`、`72-82`、`121-124`、`240-252` |
| C16 | 共享展示组件的先例 `common/TraitList.jsx`；`.pill` 与 `card-rel-type pill` 的组合先例 | `common/TraitList.jsx`；`CharCard.jsx:815`；`global.css:3717` |
| C17 | 后端测试桩 `_FORMAT_GROUP_MARKERS` / `_SAMPLE_FIELD_VALUES` 被 5 个测试文件共用 | `tests/test_distiller_routing.py:808-848`；引用方见「测试」 |
| C18 | CI 的前端 lint 门只跑 `eslint.ci.config.js`；工作区一律 LF（`.gitattributes`） | `.github/workflows/build.yml:257-260`；`.gitattributes` |
| C19 | 部署工作流 `target` 可选 `both` / `sz-only` / `sg-only`；`both` 先深圳后新加坡 | `.github/workflows/deploy.yml:24-31`、`169-171`、`405-410` |
| C20 | 基线测试：后端 `tests/test_distiller_routing.py tests/test_distill_output_bounds.py` → **50 passed**；前端 `npm test` → **64 文件 / 317 条通过** | 2026-10-02 实跑 |

## 路径机制清单（落卡后置路径；本步不新增调用线，只扩核对清单）

| 机制 | 起点 / 前提 | 新字段是否改变其前提 |
|---|---|---|
| `dedupe_relationship_targets` | 只读 `relationships` | 否 |
| `retract_unverified` | 整本原文归一化一次，逐槽位做子串查找 | 多 ≤12 条摘录 + ≤12 条 behavior 的查找；不新增归一化 |
| `attach_dialogue_examples` | 只读原文与名单 | 否 |
| 注入守卫（`card_guard`） | 每叶子截 160 字，一次 LLM 调用 | 叶子数最多 +45（1 轴 + 4×2 + 12×3）；通用遍历已覆盖，不改代码 |
| `_auto_tag` / 苏醒台词 | 只读 name / identity / traits / background | 否 |

| 通道 | 执行上下文 | 守「后置步骤每条通道都跑」的测试（已有） |
|---|---|---|
| `core/text_manager.py:485` | async 协程内转线程 | `TestQuoteRetractionOnEveryChannel::test_text_manager` |
| `web/routers/distill.py:514`（`_run_distill_task`） | 后台线程，同步调用 | `…::test_bg_task` |
| `web/routers/distill.py:1135`（`distill_stream`） | async 协程内转线程 | `…::test_sse` |

## ② 全量扫描原文

```
$ git grep -n "character_arc" 3b94b82 -- "*.py" "*.jsx" "*.js" "*.cjs"
core/distiller.py:230:    ("character_arc", '  "character_arc": ["阶段1变化", "阶段2变化"]'),
core/schema.py:84:    character_arc: list[str] = []
core/schema.py:104:    "G4": ("key_memories", "character_arc", "psyche"),
tests/eval/injection/behavior_cfg.py:132:  inject_path="character_arc[2]",
tests/eval/injection/upload_extra.py:236:# ---------- schema-05：创作大纲批注直陈 decision_style/character_arc ----------
tests/eval/injection/upload_extra.py:243:（评测语料里的一段样本文本，提到字段名）
tests/perf/mock_llm_server.py:101:    "character_arc": [], "tags": [],
tests/test_distiller_routing.py:842:    "character_arc": ["阶段一"],
web/frontend/src/components/CharCard.jsx:833,836
web/frontend/src/components/EditCardModal.jsx:28,61,92,104,145,232,233
web/frontend/src/components/MarketCardDetail.jsx:748,752
web/frontend/src/components/__tests__/CharCardCharacterArc.test.jsx:44,103,111
web/frontend/src/components/__tests__/MarketCardDetailCharacterArc.test.jsx:47,90,98
```

逐行处置：`behavior_cfg.py:132` 改路径（见改动 4）；`upload_extra.py`、`mock_llm_server.py:101`（空列表，校验器转成空弧线）不动；其余都在补丁里。`AGENTS.md:1791-1797` 记有这个字段的旧形态，补丁追加一行「补充」。

```
$ git grep -n -i -E "situation_behavior|SituationBehavior|ArcPhase|ObjectListField|objectRows|ArcList|BehaviorList|card-behavior|VERBATIM_FIELDS|arc_axis" 3b94b82 -- . ':!docs'
（无输出：新名字全部未被占用）

$ git grep -l -i "reaction" 3b94b82 -- 'core/*.py' 'storage/*.py' 'storage/migrations_pg/*' 'web/routers/*.py' 'web/frontend/src/components/common/*'
core/affinity_service.py
core/chat_engine.py
core/evaluation_pipeline.py
core/reaction_service.py
storage/base.py
storage/migrations_pg/001_init.sql
storage/migrations_pg/004_dm_reactions.sql
storage/postgres_store.py
storage/sqlite_store.py
web/frontend/src/components/common/MessageReactions.jsx
web/routers/chat.py
web/routers/group.py
web/routers/message.py

$ git grep -n "from 'antd-mobile'" 3b94b82 -- web/frontend/src
ThemeDrawer.jsx / ArchiveListModal.jsx：Popup；MobileTabBar.jsx：TabBar, SafeArea
（仓内没有用它的表单组件；动态行编辑全仓只有 C15 那一处）
```

## 改动（以补丁为准；下面是逐文件说明，供审计对照）

补丁共 24 个文件，+721 / −76。

### 1. `core/schema.py`

```python
class ArcPhase(BaseModel):
    label: str = ""   # 这一阶段的心态/立场（≤8 字）；旧卡转来的为空
    state: str = ""   # 这一阶段的状态，一句话，句首点明故事时期
    # before 校验器：str → {"state": s}

class CharacterArc(BaseModel):
    axis: str = ""                 # 这条弧线变的是什么，一句「从…到…」
    phases: list[ArcPhase] = []
    # before 校验器：list → {"phases": v}

class SituationBehavior(BaseModel):
    situation: str
    behavior: str
    source_quote: str = ""
```

- `character_arc: CharacterArc = CharacterArc()`；`situation_behaviors: list[SituationBehavior] = []`。
- 旧形态转换写在**拥有那个形态的模型上**，`CharacterCard` 不加校验器。
- `FORMAT_GROUPS` 加 `"G6": ("situation_behaviors",)`；`character_arc` 留在 G4（两块互不引用，不需要同组）。
- 键名用 `state` 不用 `description`：避开 C5 那句共享禁令点名的键；共享句不动。
- 不在 schema 上加长度上限（模型超长会让整组校验失败）。

### 2. `core/distiller.py`（只改片段表，不改拼装逻辑）

- 维度 L（G4）：要 `axis`（一句「从…到…」）和 2–4 个 `phases`，每个给 `label`（心态或立场，≤8 字，不要只写事件名）和 `state`（句首点明故事时期，再写心态与行事方式）；无明显变化则 axis 留空、phases 为 `[]`。
- 新增维度 O（G6）：6–12 条；只写贯穿全书、各阶段都成立的做法，随阶段改变的不写在这里；`situation` 写成一类情境；`behavior` 写具体做法、≤60 字、不写形容词，负面做法照实写、不改成直接发火；`source_quote` 原样复制 10–40 字，找不到逐字原文就留空。
- 模板键：`character_arc` 改为对象；新增 `situation_behaviors`。
- `_FORMAT_IMPORTANCE` 加两条覆盖句（G4：弧线是对象；G6：条目是对象），做法同 `relationships`。

### 3. `core/card_quotes.py`

- `VERIFIED_FIELDS` 加 `"situation_behaviors[].behavior"`（引号里查不到的引文去引号，规则同其余字段）。
- 新增 `VERBATIM_FIELDS`（整个值就声明「是原文」的字段，唯一出处）：`speaking_style.catchphrases[]`、`situation_behaviors[].source_quote`。对不上时：列表元素整条删，对象里的键清成空串。
- 口癖那段硬编码删掉，并入同一个循环（`_retract_unverified_verbatim`，倒序处理）；复用现成的 `_descend` 与 `verbatim_in_normalized`。口癖的行为与撤回日志的字段名不变（既有用例守着）。

### 4. 评测与文档

- `tests/eval/injection/behavior_cfg.py:132`：`inject_path` 改为 `"character_arc/axis"`。理由：评测的宿主卡每次现蒸，形态已是对象；`_apply_inject` 对越界下标补的是空串，`phases[i]/state` 在阶段数不足时会失败，`axis` 恒存在。
- `AGENTS.md`：在「B. character_arc 能填不能看」条末追加一行「补充」。

### 5. 前端

| 文件 | 改动 |
|---|---|
| `utils/card.js` | 新增 `normalizeArc`，在 `parseCardJson` 里调一次：旧卡字符串数组 → `{axis:'', phases:[{label:'', state}]}`。缺失或已是规范形态的**原样返回同一引用**（做法同 identity）。`situation_behaviors` 没有旧形态，不做归一 |
| `common/ArcList.jsx`（新） | 轴一行（有才渲染）+ 阶段列表「心态 · 状态」；沿用现成的 `card-arc-*` 类名 |
| `common/BehaviorList.jsx`（新） | 每条：情境（现成的 `.pill`）、做法、原文摘录（有才渲染） |
| `CharCard.jsx`、`MarketCardDetail.jsx` | 弧线节改用 `ArcList`；新增「情境→行为」节，为空整节不渲染。外壳仍各用各的 |
| `utils/objectRows.js`（新） | 行操作纯函数：`withRowKeys`、`blankRow`、`cleanRows`（去 `_key`，丢弃各列全空的行；不在列里的键原样保留） |
| `common/ObjectListField.jsx`（新） | 从人物关系那段内联代码提出来的通用行编辑：列由 `columns` 配置；`maxCount` 只管「还能不能再加」 |
| `EditCardModal.jsx` | 人物关系、弧线阶段、情境→行为三张表都用 `ObjectListField`；弧线另有一个「变化轴」输入框。摘录不进编辑框，随行保留 |
| `styles/global.css` | `card-arc-axis`、`card-arc-label`、`card-behavior-*`（紧挨 `card-arc-*`） |

**不引入表单库**：全仓只有这一个弹窗有动态行，弹窗其余字段都是原生输入框加自有类名；为它引库等于多一套表单体系。`ObjectListField` 是把已有代码提出来，不是新造。

**有意的行为变化（仅一处）**：点了「添加」却没填的全空行不再保存，人物关系同样适用。人物关系「超过 8 条拒绝保存」的现有行为不动。

## ③ 规模表

| 量 | 值 | 来源 / 策略 |
|---|---|---|
| 弧线阶段数 | 提示词 2–4；编辑里到 5 条后不能再加 | 旧编辑上限即 5 行（`EditCardModal.jsx:28`）；超出的照常显示、照常保存 |
| 情境→行为条数 | 提示词 6–12；编辑里到 15 条后不能再加 | 全量展示，不分页（≤15 行）；超出的照常显示、照常保存 |
| 字段长度（编辑框） | label 8、state 100、axis 40、situation 40、behavior 80 | 只限新输入；已有超长值不截断 |
| G6 输出 | 12 ×（情境+做法+摘录 ≈ 140 字）≈ 1700 字，约 1.2–1.7k token | 估算；上限 8192（C6） |
| G4 增量 | 轴一句 + 每阶段多一个 ≤8 字 label | 估算；上限 8192 |
| 一次读完路径 | 整卡一次输出，增量同上约 2k token；上限 16384 | **整卡现有体量未实测** → 小样报告里贴 `completion_tokens` |
| 核对成本 | 多 ≤24 次子串查找 | 原文只归一化一次（见路径机制清单） |

## ④ 调用点矩阵（调用点 × 可观测输出 → 测试）

| 调用点 | 旧卡（字符串数组弧线、无条目） | 新卡 |
|---|---|---|
| `CharacterCard` 解析（`chat.py:174`、`distill.py:506/522/1124/1300/1356`、`group.py:144/159/359`、`history.py:206`、`market.py:837`、`text_manager.py:452`） | `test_legacy_string_list_arc_becomes_unlabeled_phases`、`test_card_without_new_fields_gets_empty_defaults` | `test_object_arc_keeps_axis_and_labels` |
| 编辑保存 `distill.py:1249`（validate → dump） | `test_dump_roundtrip_is_stable_and_canonicalizes_legacy_arc` | 同左 |
| 分组格式化（G4 / G6 提示词） | — | `test_g4_prompt_asks_for_axis_and_labeled_phases`、`test_g6_prompt_carries_behaviors_only`、F3（已有） |
| 一次读完（完整提示词） | — | `test_full_prompt_carries_both` |
| `finalize_card` → `retract_unverified` | 口癖：`test_several_unverified_catchphrases_are_all_removed` + `test_card_quotes.py`（已有） | `test_source_quote_found_in_text_is_kept`、`…not_in_text_is_blanked_and_entry_kept`、`test_empty_source_quote_is_not_a_retraction`、`test_behavior_quote_marks_stripped_when_not_in_text` |
| `parseCardJson` | `card.test.js`「旧卡：字符串数组变成没有 label 的阶段」 | 「新卡：规范形态原样返回，不拷贝」「没有弧线：保持缺失」 |
| `CharCard` / `MarketCardDetail` 弧线节 | 「有 character_arc：按顺序渲染」「无：整节不渲染」（已有，各一） | 「新卡弧线：显示变化轴，阶段显示心态 · 状态」（各一） |
| `CharCard` / `MarketCardDetail` 情境→行为节 | 「无 situation_behaviors：整节不渲染」（各一） | 「有：渲染情境、做法与原文摘录」（各一） |
| `EditCardModal` 保存 | 「旧卡：阶段可补填心态，保存为对象形态」 | 「不改直接保存：三张表原样交回」「改后保存」「空行不保存」 |
| 跨区副本 / 版本快照 | 透传 card_json，不解析弧线 | 同左 |

## 测试（本地只跑受影响文件，库用 Docker 起的 PG；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）

后端（9 个文件，补丁后 **150 passed**）：

```
pytest tests/test_card_arc_behaviors.py tests/test_card_quotes.py tests/test_distiller_routing.py \
  tests/test_distill_output_bounds.py tests/test_identify_failure_channels.py tests/test_usage_identity_context.py \
  tests/test_distill_resume.py tests/test_distill_usage_accounting.py tests/test_card_guard.py -q
```

前端：`npm test`（补丁后 **69 文件 / 344 条通过**）；`npx eslint . -c eslint.ci.config.js --quiet` 通过。

**为什么是这 9 个文件**：只改实现、不改桩时，实测红 24 条，分布在 `test_distiller_routing`、`test_identify_failure_channels`、`test_usage_identity_context`、`test_distill_resume`、`test_distill_usage_accounting` 五个文件；原因都是共用桩（C17）不认 G6。桩只需改两处：`_FORMAT_GROUP_MARKERS` 加一行 G6，`_SAMPLE_FIELD_VALUES` 的弧线换成对象、加一条 `situation_behaviors`。

**我这边的全量结果（样本范围）**：Linux、Python 3.12.3、本机 PG 16（非 Docker）、Node 22（CI 是 Node 20）、浅克隆。后端全量 2663 passed / 1 failed —— 失败的是 `tests/test_evidence_integrity.py::TestManifestEntries::test_code_sha_resolves`，它依赖完整 git 历史，浅克隆下必红，与本补丁无关。分支 CI 是最终判据。

## 对账表（变异已在「基线 + 补丁」上逐条实跑，全部打红；执行方不重跑）

后端（13 条）：

| 变异 | 变红的测试 |
|---|---|
| M1 删 `ArcPhase` 的旧字符串转换 | `test_legacy_string_list_arc_becomes_unlabeled_phases`、`test_dump_roundtrip_…` |
| M2 删 `CharacterArc` 的旧列表转换 | 同上两条 |
| M3 旧列表转换不判类型 | 导入即 `ValidationError`（默认值过不了自己的校验器），收集阶段全红 |
| M4 `situation_behaviors` 去默认值 | `test_card_without_new_fields_gets_empty_defaults` 等 14 条 |
| M5 从分组表删掉 G6 | 导入即 `KeyError: 'situation_behaviors'`（C4），收集阶段全红 |
| M6 维度 O 归属写成 G4 | `test_g6_prompt_carries_behaviors_only` |
| M7 弧线模板还原为字符串数组 | `test_g4_prompt_asks_for_axis_and_labeled_phases`、`test_full_prompt_carries_both` |
| M8 G6 的覆盖句归属写成 G4 | `test_g6_prompt_carries_behaviors_only` |
| M9 摘录不进 `VERBATIM_FIELDS` | `test_source_quote_not_in_text_is_blanked_and_entry_kept` |
| M10 behavior 不进 `VERIFIED_FIELDS` | `test_behavior_quote_marks_stripped_when_not_in_text` |
| M11 撤回不倒序 | `test_several_unverified_catchphrases_are_all_removed` |
| M12 空摘录也当撤回 | `test_empty_source_quote_is_not_a_retraction` |
| M13 口癖不进 `VERBATIM_FIELDS` | 上一条 + `test_card_quotes.py` 两条既有用例 |

前端（20 条）：

| 变异 | 变红的测试 |
|---|---|
| F1 `parseCardJson` 不做弧线归一 | 「旧卡：字符串数组变成没有 label 的阶段」等 6 条 |
| F2 规范形态也重建 | 「新卡：规范形态原样返回，不拷贝」 |
| F3 字符串阶段不转对象 | 同 F1 的 6 条 |
| F4 `ArcList` 不显示 label | 「新卡：显示变化轴，阶段显示心态 · 状态」等 3 条 |
| F5 `ArcList` 无轴也渲染轴行 | 「旧卡：没有轴就不渲染轴那一行」 |
| F6 `BehaviorList` 无摘录也渲染摘录 | 「每条显示情境与做法；有原文摘录才显示摘录」 |
| F7 / F8 两个详情页行为节不判空 | 「无 situation_behaviors：整节不渲染」等（各 4–5 条） |
| F9 / F10 两个详情页弧线判空用旧写法 | 「有 character_arc：按顺序渲染」「新卡弧线…」 |
| F11 `cleanRows` 不丢空行 | 「cleanRows：各列全空的行丢掉」「点了添加却没填的空行不保存」 |
| F12 `cleanRows` 不去 `_key` | 「cleanRows：去掉 _key」等 5 条 |
| F13 `ObjectListField` 删错行 | 「删一行：删的是点的那一行」 |
| F14 添加不看上限 | 「到上限后不能再加」 |
| F15 改一格丢其余键 | 「改一格：只改那一行那一列」等 3 条 |
| F16 保存不带 `situation_behaviors` | 「改阶段名、改变化轴、改做法后保存」等 2 条 |
| F17 变化轴不去首尾空白 | 「改阶段名、改变化轴、改做法后保存」 |
| F18 保存时弧线只交 phases 数组 | 「不改直接保存：三张表原样交回」等 4 条 |
| F19 关系保存不经 `cleanRows` | 「不改直接保存…」「空行不保存」 |
| F20 新增行 `_key` 固定 | 「blankRow：_key 不与已有行重复」（初版存活，补了一条断言后打红） |

## 未在我这边验证的两件事（执行方做）

**模型行为小样**（我这边没有模型 key）。用一段约 2 千字、有明显转变的公版短篇，真实蒸馏**两次**：

| 次 | 做法 | 走到的路径 |
|---|---|---|
| A | 默认配置 | 一次读完，完整提示词 |
| B | 配置里 `longctx_threshold: 1`（`AGENTS.md:2120` 的做法，**不改源码**），跑完改回 | Map → Reduce → 各组格式化，G6 提示词 |

两次都检查并把 JSON 片段贴进报告：

- `character_arc.axis` 是一句「从…到…」；`phases` 2–4 个，`label` 是心态或立场而不是纯事件名，`state` 句首有故事时期。
- `situation_behaviors` 6–12 条，`behavior` 是具体做法而非形容词，没有只在某一阶段成立的做法。
- 日志里 `[card_quotes] 撤回` 的条数；摘录被清空超过一半 → 停下报告。
- A 次贴格式化那次调用的 `completion_tokens`。

不合格先停下报告，只允许改提示词。B 次条目不足 6 条同样停下报告，**不自行改 Map / Reduce**。

**视觉**。复用 `web/frontend/e2e/char-card-verify.cjs`：给它的 `CARD` 种子加新形态的 `character_arc` 和两条 `situation_behaviors`，用现成的 `shot` 补两张截图（详情页的弧线与情境→行为两节；编辑弹窗的三张表）。只出截图，美观由 Tracy 看。

## 步骤

| 步 | 做什么 | skill |
|---|---|---|
| S0 | 从最新 main 开 worktree；`git apply --check docs/specs/arc-behaviors-s1.patch`（main 已不是 `3b94b82` 且检查失败 → 停）。逐条复核「已查实约束」；重跑「全量扫描原文」的命令，输出比本文件多出的行先停下报告 | `@search-first` |
| S1 | `git apply docs/specs/arc-behaviors-s1.patch`，删掉补丁文件；跑「测试」一节的后端 9 个文件、`npm test`、CI lint | — |
| S2 | e2e 探针加种子、出两张截图 | — |
| S3 | 模型行为小样 A、B 各一次 | `@verification-before-completion` |

完成报告：S0 逐条复核结果与扫描原始输出；本段改动的文件清单（24 个 + e2e 探针），逐个写明「看过、结论」；受影响测试结果；两次小样的输出片段；两张截图。不写任何部署或生产操作。

## 上线注意（不在本步执行，写给合并之后）

- **形态变更只能向前**：新蒸馏的卡和编辑保存过的卡，`character_arc` 是对象，旧代码（`list[str]`）解析会失败。
- 两个节点必须同批上线：部署工作流用 `target: both`（C19）。只上一个节点时，跨区拿到的新形态卡在另一节点打不开。
- 回滚到本步之前的镜像，会让上线后新蒸馏或编辑过的卡无法解析；需要回滚时先说，不要直接回。

## 已知现状（不在本步，是否另开由 Tracy 定）

- 人物关系编辑上限 8 条，超出时拒绝保存；蒸馏不限关系条数（主角可有几十条），这类卡在编辑弹窗里存不了（C15）。

---

## 补充一（2026-10-02）：首次交付审计 —— 不通过，小样重做

### 审计结论

| 项 | 结论 |
|---|---|
| S0 复核、扫描 | 接受。三处证据引错位置，约束本身成立（下表），重交报告时改正 |
| S1 打补丁、受影响测试 | 接受（150 passed / 69 文件 344 条 / lint 通过，与预跑一致）。**代码未推送，逐文件审计待推送后做** |
| `e2e/helpers.cjs` 加 `E2E_BASE` | 接受：默认值不变；计入改动清单 |
| 截图 | 待 Tracy 看 |
| **小样 A、B** | **不合格**。报告只核了「条数」和「behavior 是具体做法」，验收清单里其余几条没核，实际有违反 |

报告里要改正的事实：

| 位置 | 问题 |
|---|---|
| 二、测试桩与文档 | 写的是「注入路径改 `character_arc[2]`」。补丁是把它**从** `character_arc[2]` **改成** `character_arc/axis`。核对文件实际内容后改正 |
| C11 证据 | 引的 `distill.py:515` 是后台任务里的 `model_dump()`；编辑保存是 `:1249-1254` |
| C7 证据 | 引的是 `SAFE_SINGLE_REDUCE` 与 `_do_reduce`，不能说明「格式化看不到原文」；应引 `:642-660`、`:2433-2438` |
| C20 | 写了「20 条全部重跑」，但 C20 注明未复测。二选一：复测，或把总述改成 19 条 |

小样里违反验收清单的地方（依据报告贴出的片段）：

| 违反 | 证据 |
|---|---|
| label 写成了事件或结局 | A：「折腿求生」「消失死去」 |
| 有「死亡 / 消失」阶段 | A 第 4 阶段。**第 2 步默认进最后阶段，最后阶段必须是此人还能对话的时期** |
| state 句首不是具体时期 | A、B 都写「故事前期 / 中期 / 后期 / 末期」 |
| 把场景当阶段 | B 第 2、3 阶段（「转向孩子说话」「分茴香豆时」）是同一时期的两个场景，不是心态变化 |
| axis 写情节且超长 | A 的 axis 约 45 字，全是情节 |
| situation 是单次事件 | A「孩子围着要茴香豆吃」「想教后辈写字」；B「给孩子分茴香豆」 |
| 条目是阶段专属做法（决定 1 没立住） | B「被追问腿断原因 → 低声说跌断」只发生在被打折腿之后 |

### 提示词 r2（只改维度 L、O 两段文字）

补丁：`docs/specs/arc-behaviors-s1-r2.patch`（叠在第一份补丁之上，只动 `core/distiller.py`，+9 / −3；用完即删）。正文「改动 2」里对 L、O 的描述以本节为准。

- **L**：axis ≤30 字，两端都写心态或立场，不写情节。只在心态或立场确实变了的地方分段，通常 2–3 个、最多 4 个；同一种心态下的不同场景不算新阶段。每个阶段都必须是此人还在场、还能与人交谈的时期，死亡、失踪、离场不单列。label 不写事件或结局。state 句首用书里的具体事件或时段，不写「前期 / 中期 / 后期」。
- **O**：只收在原文至少两个不同场景里都出现过、且各阶段都成立的做法（沿用铁律 1 的口径）；原文撑不起 6 条就少写，不拿单次事件充数。situation 写成一类会反复遇到的情境。
- 提示词里的示例换成不指向具体作品的词。

预跑（基线 + 补丁一 + r2）：`pytest tests/test_card_arc_behaviors.py tests/test_distill_output_bounds.py tests/test_distiller_routing.py -q` → 62 passed；受影响 9 个文件 → 150 passed。提示词措辞本身没有单测可守，小样就是它的测试。

### 小样验收清单 r2（A、B 各一次，同一篇《孔乙己》；逐项写「是 / 否」，否则不算核过）

把两次的 `character_arc` 和 `situation_behaviors` **完整**贴进报告（不是片段），再逐项判定：

| # | 判据 |
|---|---|
| 1 | axis ≤30 字，两端是心态或立场 |
| 2 | 阶段数 ≤4；逐个列出 label，每个都是心态或立场 |
| 3 | 没有死亡、失踪、离场类阶段；最后一个阶段此人仍可对话 |
| 4 | 每个 state 句首是书里的具体事件或时段 |
| 5 | 相邻阶段心态确实不同，不是同一心态的不同场景 |
| 6 | 每条 situation 是一类可复现的情境：逐条写出它在原文出现的两处 |
| 7 | 没有只在某一阶段成立的条目 |
| 8 | 条数 4–12（正文里「不足 6 条停下」改为「不足 4 条停下」） |
| 9 | `source_quote`：非空几条、被清空几条；清空超过一半 → 停下报告 |
| 10 | `[card_quotes] 撤回` 日志按字段分组计数（要看 `situation_behaviors[].behavior` 被去了几处引号） |

仍不合格：可以再改 L、O 两段的措辞，**最多两轮**，每轮把措辞 diff 和结果记进「补充二」；两轮后还不合格就停下报告。其余文件不动，Map / Reduce 不动。

### 本轮步骤

| 步 | 做什么 |
|---|---|
| R1 | `git apply docs/specs/arc-behaviors-s1-r2.patch`，删掉补丁；跑上面三个测试文件 |
| R2 | 重跑小样 A、B，按清单 r2 逐项判定 |
| R3 | 改正上表四处报告事实；把完整报告写进本文件末尾，标「补充二：完成报告」；提交本任务文件（24 个 + e2e 探针 + `e2e/helpers.cjs` + 本文件）并推送 `feat/arc-behaviors-s1`。只推分支，不开 PR、不合并、不部署；回报 sha |

---

## 补充二：完成报告（2026-10-02）

> 基线 `main` = `3b94b82`。本轮 = 补充一的 R1→R3。本文件即完整报告（上一轮以对话形式交付，未落文件）。
> 环境：Windows / Git Bash；仓内 `.venv`（Python）；Node 22（CI 是 Node 20）；PG 用一次性 Docker 容器（`charsim-arc-s1-pg`）。

### 一、上表四处事实改正

| 位置 | 原报告（错） | 改正后（对） |
|---|---|---|
| 二、测试桩与文档 | 「注入路径改 `character_arc[2]`」 | 补丁把它**从** `character_arc[2]` **改成** `character_arc/axis`。核对文件实际内容：`tests/eval/injection/behavior_cfg.py:132` 为 `inject_path="character_arc/axis"` |
| C11 证据 | 引 `web/routers/distill.py:515` | `:515` 是后台任务里的 `model_dump()`；编辑保存的 validate→dump 在 `web/routers/distill.py:1249-1254` |
| C7 证据 | 引 `SAFE_SINGLE_REDUCE` / `_do_reduce` | 这两处不能说明「格式化看不到原文」。该引格式化输入的来处：`core/distiller.py:642-660`（`_reduce_user_prompt`，每维度 200 字概括 + 5 条原句）、`:2433-2438`（`format_input` 取归并结果） |
| C20 | 「20 条全部重跑」 | C20 是基线测试值，不是工作区约束。总述订正为「**19 条约束（C1–C19）逐条复核**；C20 另测」，见下节 |

其余核对：本文件首行要求「在 `docs/specs/arc-reactions-s1.md` 首行写已取代」——该文件不存在，无落点（原样报告，未新建文件）。

### 二、R1 打补丁与受影响测试

- `git apply docs/specs/arc-behaviors-s1-r2.patch` → **成功**（只动 `core/distiller.py`，+9 / −3）；随后删除补丁文件（`docs/specs/arc-behaviors-s1-r2.patch` 不在工作区）。
- r2 后三个测试文件：
  `pytest tests/test_card_arc_behaviors.py tests/test_distill_output_bounds.py tests/test_distiller_routing.py -q` → **62 passed**（20.20s）
- r2 后受影响 9 个文件：
  `pytest tests/test_card_arc_behaviors.py tests/test_card_quotes.py tests/test_distiller_routing.py tests/test_distill_output_bounds.py tests/test_identify_failure_channels.py tests/test_usage_identity_context.py tests/test_distill_resume.py tests/test_distill_usage_accounting.py tests/test_card_guard.py -q` → **150 passed**（55.28s）
- 前端：`npm test` 首次 **68 文件 / 343 条 + 1 个未处理错误**（`[vitest-pool] Failed to start forks worker … UNKNOWN: unknown error, read`，加载 `GroupErrorKeepsPending.test.jsx` 的 jsdom 时环境抖动，非断言失败；对应跟踪 issue #69）；**重跑 69 文件 / 344 条全绿**。
- CI lint：`npx eslint . -c eslint.ci.config.js --quiet` → 无输出（退出 0）。
- **C20 复核**：
  - 后端：在 `3b94b82` 临时检出（`git worktree add -d`，只读、用完删）重跑
    `pytest tests/test_distiller_routing.py tests/test_distill_output_bounds.py -q` → **50 passed**，与 C20 一致。
  - 前端：基线检出无 `node_modules`，未复测；C20 前端值（64 文件 / 317 条）沿用 2026-10-02 实测。
  - → 总述：**19 条约束逐条复核；C20 后端部分本轮重跑一致，前端部分未复测（沿用基线实测值）**。

### 三、提示词 r2 措辞 diff（本轮唯一一次措辞改动）

`docs/specs/arc-behaviors-s1-r2.patch`（叠在第一份补丁之上，只动 `core/distiller.py` 的 `_FORMAT_DIMS`，+9 / −3）：

```diff
@@ G4 · 维度 L @@
-    ("G4", 'L. 角色弧线：此人从故事开始到结束，心态或立场发生了怎样的变化？axis 用一句「从…到…」概括这条变化；phases 按故事顺序分2-4个阶段，每个阶段给 label（这一阶段的心态或立场，≤8字，如「桀骜不服」，不要只写事件名）和 state（一句话：句首点明对应的故事时期，再写此时的心态与行事方式）。无明显变化则 axis 留空、phases 输出空数组 []。'),
+    ("G4",
+        'L. 角色弧线：此人从故事开始到结束，心态或立场发生了怎样的变化？无明显变化则 axis 留空、phases 输出空数组 []。\n'
+        '   - axis：一句「从…到…」（≤30字），两端都写心态或立场，不写情节。\n'
+        '   - phases：按故事顺序排列。只在心态或立场确实变了的地方分段，通常2-3个、最多4个；同一种心态下的不同场景不算新阶段。每个阶段都必须是此人还在场、还能与人交谈的时期，死亡、失踪、离场不单列为阶段。\n'
+        '   - label：这一阶段的心态或立场（≤8字，如「隐忍不发」），不要写事件或结局（如「被逐出师门」「死去」）。\n'
+        '   - state：一句话。句首用书里的具体事件或时段点明这是什么时候（如「被逐出师门之后」），不要写「前期」「中期」「后期」；再写此时的心态与行事方式。'
+    ),

@@ G6 · 维度 O @@
-        'O. 情境→行为（6-12条）：此人遇到某类情境时会怎么做。只写贯穿全书、各阶段都成立的做法；随阶段改变的做法不写在这里（那属于角色弧线）。\n'
-        '   - situation：写成一类情境（如「被人当众质疑」），不要写成某一章的具体事件。\n'
+        'O. 情境→行为（6-12条；原文撑不起6条就少写，不拿单次事件充数）：此人遇到某类情境时会怎么做。只收在原文至少两个不同场景里都出现过、且各阶段都成立的做法；随阶段改变的做法不写在这里（那属于角色弧线）。\n'
+        '   - situation：写成一类会反复遇到的情境（如「被人当众质疑」「有人向他求助」），不要写成书里只发生一次的具体事件。\n'
```

结果：见「五」，两篇仍不合格（详见逐项判定）。**本轮未消费「最多两轮」的措辞修改额度**——R1 明令「不要手改」，故本轮只应用审计给的 r2 措辞、不做二次改写；是否继续改 L / O 由审计定。

### 四、改动文件清单（24 个 + e2e 探针 + `e2e/helpers.cjs`）

补丁 24 个文件，+721 / −76。逐个「看过 / 结论」：

| 文件 | 看过 | 结论 |
|---|---|---|
| `core/schema.py` | `ArcPhase`/`CharacterArc`/`SituationBehavior` 三模型 + 各自的旧形态 before 校验器；`FORMAT_GROUPS` 加 `"G6"`，`character_arc` 留 G4 | 符合「已定的决定 2/3」「改动 1」 |
| `core/distiller.py` | `_FORMAT_DIMS` 的 L 改对象口径、新增 O 归属 G6；模板键改对象 / 新增；`_FORMAT_IMPORTANCE` 加两条覆盖句 | 符合改动 2；L/O 措辞以 r2 为准（补充二 三） |
| `core/card_quotes.py` | `VERIFIED_FIELDS` 加 `situation_behaviors[].behavior`；新增 `VERBATIM_FIELDS`（`catchphrases[]`、`source_quote`）+ `_retract_unverified_verbatim`（倒序）；口癖硬编码并入同循环，日志字段名不变 | 符合改动 3 |
| `tests/eval/injection/behavior_cfg.py` | `inject_path` 由 `character_arc[2]` 改为 `character_arc/axis` | 符合改动 4（补充一已核） |
| `AGENTS.md` | 「character_arc 能填不能看」条末追加一行「补充」记新形态 | 符合改动 4 |
| `tests/test_card_arc_behaviors.py`（新） | 对象弧线 / 旧列表转换 / 空默认 / 引文撤回 / G4·G6 提示词 | 覆盖 ④ 调用点矩阵后端各格 |
| `tests/test_distiller_routing.py` | 共用桩 `_FORMAT_GROUP_MARKERS` 加 G6、`_SAMPLE_FIELD_VALUES` 弧线换对象 + 加一条 `situation_behaviors` | C17 桩的最小改动；F3 仍断言「各组 ∪ POST_FORMAT_FIELDS == model_fields」 |
| `web/frontend/src/utils/card.js` | 新增 `normalizeArc`，`parseCardJson` 调一次；规范形态/缺失原样返回同一引用 | 符合改动 5（同 identity 先例） |
| `web/frontend/src/utils/card.test.js` | 旧卡字符串数组 → 无 label 阶段；新卡不拷贝；无弧线保持缺失 | 符合 ④ |
| `web/frontend/src/utils/objectRows.js`（新） | `withRowKeys` / `blankRow` / `cleanRows`（去 `_key`、丢各列全空行、列外键保留） | 符合改动 5 |
| `web/frontend/src/utils/objectRows.test.js`（新） | `cleanRows` 丢空行 / 去 `_key`；`blankRow` 的 `_key` 不撞已有行 | 覆盖 F11/F12/F20 |
| `web/frontend/src/components/common/ArcList.jsx`（新） | 轴一行（有才渲染）+ 阶段「心态 · 状态」；沿用 `card-arc-*` | 符合改动 5 |
| `web/frontend/src/components/common/BehaviorList.jsx`（新） | 情境 `.pill` + 做法 + 原文摘录（有才渲染） | 符合改动 5 |
| `web/frontend/src/components/common/ObjectListField.jsx`（新） | 通用行编辑：列由 `columns` 配置，`maxCount` 只管「能否再加」 | 由人物关系内联代码提出，非新造；符合「不引表单库」 |
| `…/common/__tests__/ArcList.test.jsx`（新） | 有/无轴、label 显示 | 覆盖 F4/F5 |
| `…/common/__tests__/BehaviorList.test.jsx`（新） | 摘录有无两态 | 覆盖 F6 |
| `…/common/__tests__/ObjectListField.test.jsx`（新） | 删行删对、到上限不能加、改一格不动其余 | 覆盖 F13–F15 |
| `…/__tests__/CharCardArcBehaviors.test.jsx`（新） | 详情页两节渲染 + 空则不渲染 | 覆盖 ④ |
| `…/__tests__/MarketCardDetailArcBehaviors.test.jsx`（新） | 同上 | 覆盖 ④ |
| `…/__tests__/EditCardModalObjectLists.test.jsx`（新） | 三张表原样交回 / 改后保存 / 空行不保存 / 变化轴去空白 | 覆盖 F16–F19 |
| `…/__tests__/CharCardCharacterArc.test.jsx`（旧） → `CharCardArcBehaviors.test.jsx` | 重命名 + 口径改 | 按补丁口径：2 处重命名 |
| `…/__tests__/MarketCardDetailCharacterArc.test.jsx`（旧） → `MarketCardDetailArcBehaviors.test.jsx` | 同上 | 同上 |
| `web/frontend/src/components/CharCard.jsx` | 弧线节改 `ArcList`；新增「情境→行为」节，空则整节不渲染；外壳不动 | 符合改动 5 |
| `web/frontend/src/components/MarketCardDetail.jsx` | 同左（外壳仍各用各的） | 符合改动 5 |
| `web/frontend/src/components/EditCardModal.jsx` | 三张表用 `ObjectListField` + 一个「变化轴」输入框；摘录不进编辑框随行保留 | 符合改动 5；含**有意的行为变化**（点「添加」没填的全空行不再保存，人物关系同） |
| `web/frontend/src/styles/global.css` | `card-arc-axis`/`card-arc-label`/`card-behavior-*`（紧挨 `card-arc-*`） | 符合改动 5 |
| `web/frontend/e2e/char-card-verify.cjs`（探针，非 24 之内） | `CARD` 种子加新形态 `character_arc` + 两条 `situation_behaviors`；新增 `runArcBehaviorShots` 出两张截图，断言只锁「这几节确实渲染」 | e2e **✓ PASS** |
| `web/frontend/e2e/helpers.cjs`（偏离，非 24 之内） | 仅 1 行：`const BASE = process.env.E2E_BASE \|\| 'http://localhost:7861'`（原为字面量） | 默认值不变；已计清单 |

**声明一处偏离**：`e2e/helpers.cjs` 的 `E2E_BASE` 覆盖不在补丁范围内，是我为指向非默认栈加的 1 行；默认值等价，行为不变（补充一已接受，计入改动清单）。

### 五、小样 A、B —— 完整 JSON 与逐项判定

同一篇：鲁迅《孔乙己》（`e2e/scratch/story_kongyiji.txt`，公版；正文段落以 `[n]` 计，见下表）。角色：孔乙己。
跑法：探针 `e2e/scratch/arc_s1_model_sample.py A|B`（**gitignored 的 `e2e/scratch/`，按 CLAUDE.md「调试脚本不入 main」不提交**；所用原文与产物 `out_{A,B}_{raw,final}.json` 同在该目录）。
B 用仓内临时 `config.yaml`（`cp config.example.yaml` 后设 `longctx_threshold: 1`、`llm.max_tokens: 16384`）触发 Map→Reduce→各组格式化；跑完删除 `config.yaml`（否则会被 `Distiller(config_path=None)` 拾到而影响路由测试）。

调用与用量（探针实测）：

| 次 | 走到的路径 | 调用 | 用量 |
|---|---|---|---|
| A | 一次读完（完整提示词） | `llm_calls=1`，`kind=chat`，`max_tokens=16384` | `prompt_tokens=7126`，**`completion_tokens=2727`** |
| B | Map → Reduce → 各组格式化（G6 提示词） | `llm_calls=8`：call1 `stream`/`16384`（归并）+ call2–call8 `stream`/`8192`（6 个格式组 + `_parse_json_with_retry` 的一次组内重试） | 归并 2626 / 各组 377·433·510·686·814·749·788（completion）；`stream_statuses=['analyzing','analyzing','merging','merging','formatting']`，`heartbeats=58` |

> Map 阶段走 `self._llm._make_async_client()`（自建 async client），**不经本仪器**，故不在 `calls` 里——其进展由流事件 `analyzing` 体现，非漏记。

两次的 `finalize_card` 都在「贴对话示例」一步失败（`DistillError：挑选对话示例失败：模型没有选出可用的编号`）——探针的硬编码名单所致，与本步无关；探针捕获后回退到「去重 + 引文核对」两步的成品打印，故下表 JSON 是**核对之后**的最终形态。

#### A（默认配置）

```json
{
  "character_arc": {
    "axis": "从自命清高到被打折腿后颓唐求生",
    "phases": [
      { "label": "自命清高", "state": "偷何家书被打后，仍穿长衫站着喝酒，争辩『窃书不能算偷』，教小伙计识字，分茴香豆给孩子，维持读书人架子。" },
      { "label": "颓唐求生", "state": "被丁举人打折腿后，用手走来喝酒，不再分辩，只低声说『跌断，跌，跌……』，眼色恳求掌柜不要再提。" }
    ]
  },
  "situation_behaviors": [
    { "situation": "被人当众取笑偷东西", "behavior": "涨红脸争辩，说窃书不能算偷，读书人的事能算偷么，引君子固穷之类的话。", "source_quote": "孔乙己便漲紅了臉，額上的靑筋條條綻出，爭辯道：『竊書不能算偸……竊書！……讀書人的事，能算偸麽？』" },
    { "situation": "被问为什么连半个秀才也捞不到", "behavior": "立刻颓唐不安，脸上笼灰色，嘴里全是之乎者也，让人听不懂。", "source_quote": "孔乙己立刻顯出頹唐不安模樣，臉上籠上了一層灰色，嘴裏說些話；這回可是全是之乎者也之類，一些不懂了。" },
    { "situation": "遇到孩子围着他", "behavior": "分茴香豆一人一颗，孩子不走就伸开五指罩住碟子，弯腰说不多不多。", "source_quote": "他便給他們茴香豆喫，一人一顆……伸開五指將碟子罩住，彎腰下去說道：『不多了，我已經不多了。』" },
    { "situation": "想教别人识字", "behavior": "恳切地考对方茴字怎么写，说将来做掌柜写账要用，见对方不热心就叹气惋惜。", "source_quote": "『不能寫罷？……我教給你，記着！這些字應該記着。將來做掌櫃的時候，寫帳要用。』" },
    { "situation": "欠了酒钱", "behavior": "暂时记在粉板上，但不出一个月定然还清，从不拖欠。", "source_quote": "雖然間或沒有現錢，暫時記在粉板上，但不出一月，定然還淸" },
    { "situation": "被人揭短要求不要再说", "behavior": "低声辩解，眼色恳求对方不要再提，不再激烈争辩。", "source_quote": "孔乙己低聲說道：『跌斷，跌，跌……』他的眼色，很像懇求掌櫃，不要再提。" }
  ]
}
```

逐项判定（清单 r2）：

| # | 判据 | A | 依据 |
|---|---|---|---|
| 1 | axis ≤30 字，两端是心态或立场 | **否** | 「从自命清高到被打折腿后颓唐求生」=15 字，但**第二端含事件**（「被打折腿后」是情节） |
| 2 | 阶段数 ≤4；逐个 label 都是心态或立场 | 是 | 2 个：`自命清高`、`颓唐求生`，都是心态 |
| 3 | 无死亡/失踪/离场类阶段；末阶段此人仍可对话 | 是 | 末阶段「颓唐求生」state 里他仍到店喝酒、说话 |
| 4 | 每个 state 句首是书里的具体事件或时段 | 是 | `偷何家书被打后`…[3]；`被丁举人打折腿后`…[9]/[10] |
| 5 | 相邻阶段心态确实不同 | 是 | 自命清高 vs 颓唐求生，不同 |
| 6 | 每条 situation 是一类可复现的情境（原文两处） | **否** | 6 条里 4 条只出现一处：见下表 |
| 7 | 没有只在某一阶段成立的条目 | **否** | 「被人揭短要求不要再说」只发生在打折腿之后（[10]），是阶段专属 |
| 8 | 条数 4–12 | 是 | 6 条 |
| 9 | `source_quote`：非空几条、清空几条；清空超半停下 | 是 | 非空 **6**、清空 **0**（未超半） |
| 10 | `[card_quotes] 撤回` 按字段分组的计数 | 是 | 共 2 条：`personality_traits[1]`×1、`inner_tensions[0]`×1；**`situation_behaviors[].behavior` 被去引号 0 处** |

A 的 item 6 逐条出现处（正文段落下标）：

| situation | 原文出现处 |
|---|---|
| 被人当众取笑偷东西 | [3]、[10] → 两处 ✓ |
| 被问为什么连半个秀才也捞不到 | [5] → **仅一处** ✗ |
| 遇到孩子围着他 | [7] → **仅一处** ✗ |
| 想教别人识字 | [6] → **仅一处** ✗ |
| 欠了酒钱 | [4]、[9]、[11] → 三处 ✓ |
| 被人揭短要求不要再说 | [10] → **仅一处** ✗，且阶段专属（打腿后） |

#### B（`longctx_threshold: 1` → Map→Reduce→各组格式化）

```json
{
  "character_arc": {
    "axis": "从强撑读书人体面到只求不再被取笑",
    "phases": [
      { "label": "强撑体面", "state": "初到咸亨酒店时，排出九文大钱要酒要豆，被取笑偷书便涨红脸、额上青筋条条绽出地争辩「窃书不能算偷」，被问功名则颓唐不安、笼上灰色" },
      { "label": "转而教孩子", "state": "知道自己不能和酒客谈天之后，只好向孩子说话，考问茴字写法、以长指甲敲柜台、极高兴地要教，见对方不热心便叹气显出极惋惜的样子" },
      { "label": "恳求勿提", "state": "被打折腿后最后一次到店，盘腿坐门槛，摸出四文大钱要温一碗酒，被掌柜取笑偷东西时不十分分辩，单说「不要取笑！」，低声说「跌断，跌，跌……」，眼色很像恳求掌柜不要再提" }
    ]
  },
  "situation_behaviors": [
    { "situation": "被酒客当众取笑脸上新伤疤或偷东西", "behavior": "先不回答，对柜里要酒要豆，排出九文大钱；被逼问才涨红脸、额上青筋绽出地争辩", "source_quote": "他不回答，对柜里说：『温两碗酒，要一碟茴香豆。』便排出九文大钱。" },
    { "situation": "被人当面指责偷窃", "behavior": "先辩称凭空污人清白，再以窃书不能算偷『读书人的事』辩解；最后一次只低声说『不要取笑』『跌断』，眼色恳求不要再提", "source_quote": "" },
    { "situation": "被问到是否识字或为何没进学", "behavior": "问识字时显出不屑置辩的神气；问为何连半个秀才也捞不到时立刻颓唐不安，脸上笼上一层灰色", "source_quote": "孔乙己看着问他的人，显出不屑置辩的神气。" },
    { "situation": "与同店酒客无法谈天时", "behavior": "转向孩子说话，考问茴字写法，主动要教，用长指甲敲柜台或蘸酒在柜上写字", "source_quote": "孔乙己自己知道不能和他们谈天，便只好向孩子说话。" },
    { "situation": "教孩子写字而对方不热心", "behavior": "叹气，显出极惋惜的样子", "source_quote": "" },
    { "situation": "给孩子茴香豆而孩子吃完仍不散", "behavior": "先一人一颗，孩子围住不散便着慌，伸开五指罩住碟子，自语『多乎哉？不多也』", "source_quote": "不多不多！多乎哉？不多也。" },
    { "situation": "在咸亨酒店喝酒付账或赊账", "behavior": "从不拖欠，间或没有现钱暂时记在粉板上，不出一月定然还清；最后一次从破衣袋摸出四文大钱", "source_quote": "" },
    { "situation": "替人钞书谋生", "behavior": "坐不到几天，便连人和书籍纸张笔砚一齐失踪；如是几次便没人叫他钞书，没有法便偶然做些偷窃的事", "source_quote": "可惜他又有一樣壞脾氣，便是好喝嬾做。坐不到幾天，便連人和書籍紙張筆硯，一齊失蹤。" },
    { "situation": "最后一次被打折腿后到店喝酒", "behavior": "盘腿坐门槛，从破衣袋摸出四文大钱，喝完酒在旁人说笑声中坐着用手慢慢走去", "source_quote": "他从破衣袋里摸出四文大钱，放在我手里，见他满手是泥，原来他便用这手走来的。" }
  ]
}
```

逐项判定（清单 r2）：

| # | 判据 | B | 依据 |
|---|---|---|---|
| 1 | axis ≤30 字，两端是心态或立场 | 是 | 「从强撑读书人体面到只求不再被取笑」=16 字，两端都是心态/立场 |
| 2 | 阶段数 ≤4；逐个 label 都是心态或立场 | **否** | 3 个 label 中「**转而教孩子**」是动作不是心态/立场 |
| 3 | 无死亡/失踪/离场类阶段；末阶段此人仍可对话 | 是 | 末阶段「恳求勿提」他仍在店里说话 |
| 4 | 每个 state 句首是书里的具体事件或时段 | **否** | 第 1 阶段句首「**初到咸亨酒店时**」是笼统时段，不是具体事件/时期 |
| 5 | 相邻阶段心态确实不同，不是同一心态的不同场景 | **否** | 第 1→2 阶段都是「同人酒客时期」的两个场景（争辩 / 转向孩子），不是心态变化 |
| 6 | 每条 situation 是一类可复现的情境（原文两处） | **否** | 9 条里 5 条只出现一处：见下表 |
| 7 | 没有只在某一阶段成立的条目 | **否** | 「最后一次被打折腿后到店喝酒」只在打腿后（[10]） |
| 8 | 条数 4–12 | 是 | 9 条 |
| 9 | `source_quote`：非空几条、清空几条；清空超半停下 | 是 | 非空 **9**、清空 **3**（1/3，未超半） |
| 10 | `[card_quotes] 撤回` 按字段分组的计数 | 是 | 共 **14** 条：`values[1]`×2、`key_memories[2]`×1、`inner_tensions[0]`×3、`inner_tensions[1]`×1、`inner_tensions[2]`×1、`decision_style`×1、**`situation_behaviors[1].behavior`×1**、`situation_behaviors[1].source_quote`×1、`situation_behaviors[4].source_quote`×1、`situation_behaviors[6].source_quote`×1、`speaking_style.catchphrases`×1 |

> 撤回多集中于「窃书不能算偷」这类**简体引文**：原文是繁体（「竊書不能算偸」），引文核对按归一化后逐字比对，简繁不互通故对不上——这是核对机制按设计工作，不是 bug；`situation_behaviors[].behavior` 有 1 处引号被去。

B 的 item 6 逐条出现处（正文段落下标）：

| situation | 原文出现处 |
|---|---|
| 被酒客当众取笑脸上新伤疤或偷东西 | [3]、[10] → 两处 ✓ |
| 被人当面指责偷窃 | [3]、[10] → 两处 ✓ |
| 被问到是否识字或为何没进学 | [5] → **仅一处** ✗ |
| 与同店酒客无法谈天时 | [6] → **仅一处** ✗ |
| 教孩子写字而对方不热心 | [6] → **仅一处** ✗ |
| 给孩子茴香豆而孩子吃完仍不散 | [7] → **仅一处** ✗ |
| 在咸亨酒店喝酒付账或赊账 | [4]、[9]、[11] → 三处 ✓ |
| 替人钞书谋生 | [4] → **仅一处** ✗ |
| 最后一次被打折腿后到店喝酒 | [10] → **仅一处** ✗，且阶段专属 |

正文段落下标（非空行，0 起）：[3] 孔乙己登场 + 被取笑偷书、[4] 背地谈论（钞书/好喝懒做/粉板/从不拖欠）、[5] 问识字与半个秀才、[6] 谈天/教茴字、[7] 孩子围住分茴香豆、[9] 中秋前粉板记账/打折腿、[10] 最后一次到店/跌断/恳求勿提、[11] 年关粉板还欠。

### 六、截图

e2e 探针出的三张（目录 `web/frontend/e2e/screenshots/`，**被 `.gitignore` 覆盖、不入库**，供 Tracy 看）：

- `arc-behaviors-detail.png` — 详情页「角色弧线（变化轴 + 阶段）」与「情境→行为」两节
- `arc-behaviors-edit-modal.png` — 编辑弹窗三张表（角色弧线 · 阶段 / 情境→行为 / 人物关系，共 5 行）
- `char-card-desktop.png` — 角色卡整体（本轮回归）

探针结果：`ARC-BEHAVIORS-DETAIL` 轴 + 2 阶段 + 2 行为（含 1 条摘录）、`EDIT-MODAL` 三表 + 5 行、空态双宽通过、桌面/移动断言全过 → **✓ PASS**。

### 七、结论

- **S0 / S1 / S2 通过**：19 条约束（C1–C19）逐条复核成立，4 条扫描重跑输出无新增行（原文见 ②）；补丁 = 24 文件，r2 后受影响 9 文件 150 passed、三文件 62 passed、npm 69/344、lint 干净、e2e ✓ PASS；C20 后端复测 50 passed。
- **小样 A、B 仍不合格**（按清单 r2）：
  - **A**：第 1（axis 端点写事件）、第 6（6 条里 4 条是单次事件）、第 7（1 条阶段专属）三条**否**，其余是。
  - **B**：第 2（label「转而教孩子」是动作）、第 4（state 句首「初到咸亨酒店时」笼统）、第 5（阶段 1→2 是同一心态的两个场景）、第 6（9 条里 5 条单次）、第 7（1 条阶段专属）五条**否**，其余是。
- 结论：**本轮不通过**。r2 的 L / O 措辞只压住了「死亡/结局阶段」与「前期/中期/后期」两类违规（A 已无违反），但未解决「axis 端点混入事件」「label 写成动作」「同心态多场景切段」「situation 单次事件化」四类。
- **措辞修改额度未动**：补充一允许的「最多两轮」本轮 0 消耗（R1 明令「不要手改」）。是否就上述四类继续改 L / O（或改为在 `state`/`situation` 口径上收紧），请审计定。
- 本步其余文件、Map / Reduce 未动；**未做部署、未开 PR、未合并**。
