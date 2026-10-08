# ③ 性格注入 · 段 2：蒸馏产出新字段（spec）

基线：main `443c8ee0`（段 1 已合并，PR #122）。新分支 `feat/personality-distill`。
上游：设计稿 v6（分支 `docs/personality-3a-design`，`docs/specs/personality-3a-design.md`）的 §2、§3、§6、§7 T1–T4、T13、T14。本文件只把那几节落到代码上，不改设计。
流程（返工经验第 30 条）：Claude 写本文件和目标检查 → 执行方实现 → 另开会话独立复核 → Claude 读 diff → Shiyu 合并。Claude **没有**预跑变异（第 14 条已作废），§6 的变异由复核方跑。

## 0. 目标与范围

段 1 让聊天读得懂三档做法、亲近条件、宜人性分面，段 3 会读动机。但在 main 上，蒸馏还**不会产出**这些字段：

- G4 的 JSON 模板里没有它们，模板末尾又写着「不要添加自定义字段」（`core/distiller.py:306–319`、`:339`）；
- 引文核对清单里也没有它们（`core/card_quotes.py:30–51`）；
- 动机字段还不存在。

段 2 就是把「模型被要求写什么 → 草稿 → 存卡 → 投影」这条路接通。

做：
1. `CharacterCard` 顶层加 `motives`（state、list、G2），登记，并加进核对清单；
2. O / C / E / N 四个大五分数的登记类别 stable → none（声明更正，没有读者，见 §4 第 9 条）；
3. 蒸馏模板：G2 加「动机」维度；维度 M 加三档做法、亲近条件、三个宜人性分面；JSON 模板补齐这些键；
4. 引文核对：动机、亲近条件、三档做法进 `_VERIFIED_TOP`，分面的 `quote` 进 `_VERBATIM_TOP`。

不做：「## 想要什么」的渲染（段 3）；S1（雷点 / 修复两套判定合成一套，单独一步）；前端显示与编辑（第四步）；旧卡重蒸（验收时一次做）。

## 1. 目标检查（先跑它）

`tests/test_personality_distill_goal.py`（随本 spec 交付，**第一个提交只放它和本文件**）：

```
python -m pytest tests/test_personality_distill_goal.py -q
```

在 main `443c8ee0` 上的实跑结果（Claude 在沙箱 PG 上跑，2026-10-08）：

```
FAILED tests/test_personality_distill_goal.py::test_d1_every_distilled_field_is_in_its_group_template
FAILED tests/test_personality_distill_goal.py::test_d1_motives_is_a_distilled_field_of_g2
FAILED tests/test_personality_distill_goal.py::test_d2_g2_asks_for_motives_without_means
FAILED tests/test_personality_distill_goal.py::test_d2_g4_asks_for_relational_modes_warming_and_facets
FAILED tests/test_personality_distill_goal.py::test_d3_motives_dispatched_by_phase_and_projected
FAILED tests/test_personality_distill_goal.py::test_d5_facet_quote_is_verbatim_checked_behavior_kept
FAILED tests/test_personality_distill_goal.py::test_d5_unverified_quotes_in_new_state_fields_are_unquoted
7 failed, 3 passed in 2.09s
```

D1 在 main 上列出的漏项正好是段 1 登记的 5 条（`psyche.agreeableness_facets`、`psyche.warming_conditions`、`psyche.relational_modes.close / normal / conflict`）。

main 上绿的 3 条，逐条说明：

- **D0** 是夹具自检。
- **D4** 的 2 条测的是「三档做法按阶段一路到聊天 prompt」。段 1 已经登记了这几个字段，草稿派生和分发是通用的，所以在 main 上本来就绿。**它们是回归守卫，不算段 2 的证据。**

本分支完成后：`10 passed`。目标检查有错就先停下报告，不要直接改它。

| # | 检查什么 |
|---|---|
| D0 | 夹具句子两两不同、互不为子串 |
| D1 | 登记表里由蒸馏产出的叶子（stable / state / experience，顶层不在 `POST_FORMAT_FIELDS`），键名都出现在该组的 JSON 模板里；`motives` 归 G2 |
| D2 | G2 维度说明有「动机」「手段」「如实」「美化」；G4 维度说明有「亲近条件」「对亲近的人」「对平常的人」「起冲突时」「同情」「谦恭」「信任」，分面那段引用稳定类写法（含「这类字段全程不变」）并提到不写「剧情」；G4 模板里分面元素有 `facet` / `level` / `behavior` / `quote` 四个键 |
| D3 | 动机：标全部阶段 → 顶层；只标阶段 2 → 只在阶段 2 的 overlay；投影阶段 1 看不到阶段 2 的；投影阶段 2 是「阶段 2 的在前、全程的在后」 |
| D4 | 三档做法按阶段挂（三层路径），选阶段 k、亲近档，聊天 prompt 里只有阶段 k 那句；分面行为在（回归守卫） |
| D5 | 分面摘录查不到 → `quote` 清空、`behavior` 保留；查得到 → 保留。动机、亲近条件、三档做法里查不到的引文去引号（顶层和阶段 overlay 都管），文字保留；查得到的不动 |

## 2. 规则

| # | 规则 | 出处 |
|---|---|---|
| P1 | `motives: list[str]`，放 `CharacterCard` 顶层；登记 `"motives": FieldSpec("state", "list", "动机")`；归 G2。每条写成「想要 X；为此不惜 Y」 | 设计稿 §2 |
| P2 | `psyche.openness / conscientiousness / extraversion / neuroticism` 改 none；`psyche.agreeableness` 保持 stable（旧卡回退文案还读它） | 设计稿 §2 |
| P3 | 维度 B（核心性格）之后加「动机」维度：想要什么、为此不惜做到哪一步；不写手段（手段在「情境→做法」）；负面动机（贪、算计、报复）照原文如实写，不美化；按阶段给，引用 `_PHASE_DESCRIPTION_RULE` | 设计稿 §3 |
| P4 | 维度 M 加三项：① 三档关系做法（对亲近的人 / 对平常的人 / 起冲突时，各一句具体做法，按阶段给，引用 `_PHASE_DESCRIPTION_RULE`）；② 亲近条件（1–3 条：对方要做到什么，此人才肯更近一步，按阶段给，引用同一常量）；③ 宜人性三分面（同情、谦恭、信任，各给高 / 中 / 低、一句概括性的行为、一段原文摘录；引用 `_STABLE_FIELD_RULE`；只写概括性行为，不写具体剧情事件）。原有大五五个分数照旧蒸馏 | 设计稿 §3 |
| P5 | JSON 模板：`motives` 用 `_TIMED_TPL`；`psyche` 模板加 `warming_conditions`（`_TIMED_TPL` 列表）、`relational_modes`（`close` / `normal` / `conflict` 各为 `[_TIMED_TPL]`）、`agreeableness_facets`（元素写 `{"facet": "同情/谦恭/信任", "level": "高/中/低", "behavior": "…", "quote": "原文摘录"}`） | 设计稿 §3 |
| P6 | 模板片段只引用已有的规则常量，不复制句子（锁 S4 数定义次数，`tests/test_arc_phase_fields_locks.py:119–123`） | 设计稿 §3 |
| P7 | `_VERIFIED_TOP` 加 `motives[]`、`psyche.warming_conditions[]`、`psyche.relational_modes.close`、`.normal`、`.conflict`；`_VERBATIM_TOP` 加 `psyche.agreeableness_facets[].quote`。阶段 overlay 与未定位区下的同名路径由 `_overlay_paths` 自动补，不手写 | 设计稿 §2「引文核对」列；`core/card_quotes.py:58–87` |
| P8 | 分面摘录对不上只清空 `quote`，条目保留；分面非空即生效（段 1 R15 / Q2） | 设计稿 Q2、T13 |

## 3. 改动清单（坐标是 `443c8ee0`）

| 文件 | 位置 | 改什么 |
|---|---|---|
| `core/schema.py` | `CharacterCard`（`:295–317`），`values` 一行（`:301`）附近 | 加 `motives: list[str] = []` |
| `core/schema.py` | `FORMAT_GROUPS["G2"]`（`:325–326`） | 加 `"motives"` |
| `core/card_layers.py` | 状态列表区（`:57–66`） | 加 `motives` 一条（P1） |
| `core/card_layers.py` | `:45–47`、`:49` | O / C / E / N 改 none（P2） |
| `core/distiller.py` | `_FORMAT_DIMS` 的 G2 项（`:199` 之后） | 加动机维度（P3） |
| `core/distiller.py` | `_FORMAT_DIM_M`（`:251–258`） | 加三项（P4） |
| `core/distiller.py` | `_FORMAT_TEMPLATE_KEYS`（`:265` 起）、`psyche` 模板（`:306–319`） | 加 `motives` 键；`psyche` 加三个键（P5） |
| `core/distiller.py` | `_FORMAT_IMPORTANCE` 的 G4 第一条（`:331`） | 「triggers 和 soft_spots 放在 psyche 内部」改成同时点到新的三个键 |
| `core/card_quotes.py` | `_VERIFIED_TOP`（`:30–44`）、`_VERBATIM_TOP`（`:48–51`） | P7 |

不需要改：
- `core/card_draft.py`：草稿形态由登记表派生，分发走通用循环（`:104–105`、`:359–383`）。
- `core/arc_view.py`：投影走登记表（`:205–221`）。
- `core/distiller.py:359` 的依赖组推导：G2 本来就是依赖组。

## 4. 已查实的约束（判断清单，第 7b 条）

每条都写明读过的行或跑过的命令。执行方在第 0 步逐条复核（§7），有一条不成立就停下报告。

| # | 判断 | 读过的行 / 命令（`443c8ee0`） |
|---|---|---|
| 1 | 段 1 已登记 5 条，登记表现为 45 条 | `card_layers.py:55` `psyche.agreeableness_facets`、`:64` `psyche.warming_conditions`、`:71–73` 三档；`tests/test_arc_phase_fields_unit.py:56` `assert len(REGISTRY) == 45` |
| 2 | 草稿 schema 已经有这 5 个键，三层路径派生成了 `DraftRelationalModes` | 沙箱实跑 `draft_schema('G4')`：`DraftPsycheProfile` 的属性里有 `agreeableness_facets`、`relational_modes`、`warming_conditions`；`$defs` 有 `DraftRelationalModes: ['close','conflict','normal']`；`warming_conditions` 是 `{'items': {'$ref': '#/$defs/DraftTimed'}, 'type': 'array'}` |
| 3 | G4 的 JSON 模板里没有它们 | `distiller.py:306–319` 的 `psyche` 模板只有大五、基线、波动、记仇、雷点、软肋；目标检查 D1 在 main 上列出的正是这 5 条 |
| 4 | 模板末尾要求「不要添加自定义字段」 | `distiller.py:339` `(None, '- 所有字段必须按此模板输出，不要添加自定义字段')` |
| 5 | 分组提示词里同时附了草稿 schema | `distiller.py:2684` `draft_schema(group), ensure_ascii=False, indent=2` |
| 6 | 草稿 → 存卡的分发对所有 state / experience 叶子通用 | `card_draft.py:104–105`（`:103` 注释）`_GENERIC_PATHS = [(p, s) for p, s in REGISTRY.items() if s.layer in ("state", "experience") and p not in _DEDICATED]`；`:359` `for path, spec in _GENERIC_PATHS:` |
| 7 | 核对清单里还没有新字段；overlay 路径自动补 | `card_quotes.py:30–44`、`:48–51`（grep `warming\|relational\|agreeableness\|motives` 0 命中）；`:84–87` `VERIFIED_FIELDS = (_VERIFIED_TOP + _behavior_paths(...) + _overlay_paths(_VERIFIED_TOP))` |
| 8 | VERBATIM 字段对不上时，对象里的键清空，对象本身保留 | `card_quotes.py:141–144`：`if isinstance(parent, list): del parent[key]` / `else: parent[key] = ""` |
| 9 | O / C / E / N 没有读者 | 在 `core`、`web` 下 grep `openness\|neuroticism\|extraversion\|conscientiousness`，排除 `card_layers.py`、`schema.py`、`distiller.py` 后 0 命中；前端 grep 0 命中 |
| 10 | G2 依赖组不变 | `tests/test_arc_phase_fields_unit.py:412` `assert PHASE_DEPENDENT_GROUPS == {"G2", "G3", "G4"}`；G2 已含 state 字段（`values` 等） |
| 11 | 每个 `CharacterCard` 字段必须归一个组或在 `POST_FORMAT_FIELDS` | `tests/test_arc_phase_select.py:98–99`（`assert set().union(*buckets) == set(CharacterCard.model_fields)`），所以 `motives` 必须进 `FORMAT_GROUPS` |
| 12 | 每组草稿 schema 的顶层属性 = 该组字段 | `tests/test_card_draft.py:140–141`（`assert set(draft_schema(group)["properties"]) == set(fields), group`） |
| 13 | 卡片输出上限 | `distiller.py:651` `CARD_MAX_TOKENS = 8192`；规模见 §5 |
| 14 | 落卡前先核对引文 | `distiller.py:1979` `card, _ = retract_unverified(card, content)` |
| 15 | 段 2 的新字段在 G2、G4 的聊天里怎么用 | 段 1 已实现（三档、分面、亲近条件），动机由段 3 渲染。**本段不改任何读者** |
| 16 | 新增 `motives` 后其他读取卡字段的地方要不要跟着改 | **未核实全量**。已查：`inner_tensions` 在 `core`、`web` 的读者只有 `context_engine.py:369`、`affinity_service.py:202`；前端有 `EditCardModal`、`MarketCardDetail`、`ArcList`、`CharCard` 四处列字段。动机的前端显示属于第四步，本段不加；执行方第 0 步确认这四处遇到未知顶层字段不会报错（`EditCardModal.jsx:134` 用 `...data` 展开保留） |

## 5. 规模（第 11 条）

照设计稿 §6，样本是旧格式卡，只作量级参考：

| 组 | 现状输出（字符） | 新增估算（字符） | 上限 |
|---|---|---|---|
| G2 | 803–1,019 | 动机 ≤800 | `CARD_MAX_TOKENS` 8,192 tokens |
| G4 | 305–339 | 三档 ≤960 + 亲近条件 ≤600 + 分面 300 ≈ ≤1,900 | 8,192 tokens |

两组新增后都远低于上限。真实 token 数在演示卡重蒸时用接口的 `completion_tokens` 核对，不单独花钱跑。

## 6. 测试

### 6.1 执行方要补的单测（每条两侧都写）

| # | 一侧 | 另一侧 | 放哪 |
|---|---|---|---|
| T1 | `motives` 在登记表、类别 state / list、中文名「动机」；O / C / E / N 为 none；`agreeableness` 仍为 stable | 计数 45 → 46（改 `test_arc_phase_fields_unit.py:56`） | `tests/test_arc_phase_fields_unit.py` |
| T2 | `draft_schema("G2")` 的 `motives` 是 `DraftTimed` 数组 | 分面在草稿里**不是** `DraftTimed`（stable，元素是对象） | `tests/test_card_draft.py` |
| T3 | 动机的摘录落在别的阶段 → 按 B 规则改挂（另放一条能通过的标注，防兜底掩盖） | 一处都定不了位 → 进未定位区 overlay，不进投影 | `tests/test_arc_phase_unlocated.py` 或 `test_card_draft.py` |
| T4 | `VERIFIED_FIELDS` 含 P7 的路径及其 `character_arc.phases[].overlay.` 与 `character_arc.unlocated.overlay.` 两份；`VERBATIM_FIELDS` 含分面 `quote` | 分面 `behavior` **不在** `VERBATIM_FIELDS`（否则对不上会被清空） | `tests/test_card_quotes.py` |
| T13 | 分面 `quote` 被清空后，人格块仍用分面行为 | 卡上没有分面 → 用旧的写死文案 | 段 1 已覆盖（`test_personality_goal.py` 的 G1、G9），确认仍绿即可 |
| T14 | 三层路径：阶段 2 的 `relational_modes.close` 进阶段 2 overlay 的同形嵌套位置 | 投影阶段 1 取不到阶段 2 的取值 | 目标检查 D4 已覆盖，不重复 |

### 6.2 本地只跑受影响的文件（第 15 条）

库用 docker PG（先查 55432 端口和容器名，被占就另起一个，跑完删掉）。清单是 grep `REGISTRY|FORMAT_GROUPS|_FORMAT_|VERIFIED_FIELDS|VERBATIM_FIELDS|card_quotes|format_prompt_after|draft_schema|PsycheProfile` 在 `tests/` 下的全部命中（24 个文件）。我没有逐个读完这些文件来判断哪些无关，所以一个不删，全跑：

```
python -m pytest tests/test_personality_distill_goal.py tests/test_personality_goal.py tests/test_affinity_clamp.py tests/test_affinity_protocol.py tests/test_affinity_rules.py tests/test_arc_phase_fields_locks.py tests/test_arc_phase_fields_readers.py tests/test_arc_phase_fields_unit.py tests/test_arc_phase_select.py tests/test_arc_phase_unlocated.py tests/test_card_arc_behaviors.py tests/test_card_draft.py tests/test_card_quotes.py tests/test_catchwords.py tests/test_departure_notice.py tests/test_deploy_image_pulls.py tests/test_distill_output_bounds.py tests/test_distill_usage_accounting.py tests/test_distiller_routing.py tests/test_estrangement_shadow.py tests/test_evaluation_pipeline.py tests/test_exception_pickle_lock.py tests/test_identify_failure_channels.py tests/test_pg_gate.py tests/test_usage_identity_context.py -q
```

（`test_personality_goal.py` 不在 grep 命中里，但 T13 要确认它仍绿，所以加上。）合并门是分支 CI。

### 6.3 复核方必须验的变异（第 14 条新写法：Claude 没预跑）

复核方按下表逐条做，贴原始输出，**并且自己再补几条**，放宽、过严两个方向都要有。

| # | 方向 | 变异 | 应被谁打红 |
|---|---|---|---|
| M1 | 放宽 | `_VERIFIED_TOP` 去掉 `motives[]` | D5 第二条 |
| M2 | 放宽 | `_VERIFIED_TOP` 去掉 `psyche.relational_modes.close` | D5 第二条 |
| M3 | 放宽 | `_VERBATIM_TOP` 去掉分面 `quote` | D5 第一条 |
| M4 | 过严 | `_VERBATIM_TOP` 加分面 `behavior` | D5 第一条（行为被清空） |
| M5 | 放宽 | 模板删掉 `motives` 键 | D1 |
| M6 | 放宽 | `psyche` 模板删掉 `relational_modes` | D1 |
| M7 | 过严 | `motives` 登记成 stable | D3 |
| M8 | 过严 | `motives` 登记成 experience | D3（经历类只挂最早阶段、顺序也不同） |
| M9 | 放宽 | 动机维度删掉「不写手段」那句 | D2 |
| M10 | 放宽 | 分面那段不引用 `_STABLE_FIELD_RULE` | D2 |
| M11 | 过严 | `motives` 放进 G4 | D1（`motives` 应归 G2） |
| M12 | 过严 | O / C / E / N 改回 stable | T1 |

## 7. 执行方第 0 步（S0）

1. 从 main `443c8ee0` 开分支 `feat/personality-distill`。本文件放到 `docs/specs/personality-distill.md`，目标检查放到 `tests/test_personality_distill_goal.py`，用 `Test-Path` 确认两个文件都在。
2. 逐条复核 §4 判断清单，每条写「成立 / 不成立 + 看到的行」。一条不成立就停下报告，不要写代码。
3. 跑目标检查，应为 `7 failed, 3 passed`，失败的名字与 §1 一致。不一致就停下报告。
4. 第一个提交只放这两个文件（commit message 用英文），推送，CI 上这一提交应当是红的（目标检查失败）。之后的提交才写实现。

执行中新发现的问题：在本段改动面内的直接修，撞车或需要拍板的才停下报告，不自行记账。

## 8. 用哪些 skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 动手前先搜代码库，复核判断清单 |
| 实现 | `@test-driven-development` | 先让目标检查红、补单测，再写实现 |
| 交付前 | `@verification-before-completion` | 报告里贴原始输出 |
| 复核方 | `@code-review-and-quality` | 逐文件写结论，问题带根因 |

## 9. 要 Shiyu 定的

无。对照设计稿 §8，段 2 没有未定项。Claude 自己定了一处技术取舍，依据如下（第 28 条）：

- **D1 做成由登记表驱动的模板覆盖检查。** 依据：段 1 登记的 5 个字段进了草稿 schema，却没进手写的 JSON 模板，模型因此不会产出它们。这和「派生值被缓存」是同一类问题：手写的模板和登记表各存一份，迟早漂开（返工经验第 6、26 条）。有了 D1，以后登记新字段时漏写模板，测试会红。

## 10. 不做、风险

- 风险：模型可能仍会漏写新字段（模板里写了，但输出有长度压力）。这一点只能在演示卡重蒸时看，日志里看新字段是否为空。
- 风险：分面打分偏高（模型倾向高估宜人性，调研结论里有）。要求每个分面附原文摘录，并在演示卡验收时由人抽查。
- 不做：动机渲染（段 3），前端显示（第四步），S1。
