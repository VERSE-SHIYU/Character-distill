# ①补完 A：所有进 prompt 的字段按阶段（spec v3）

基线：main `15b0b7c2`（2026-10-06）。分支：`feat/arc-phase-fields`。
v3（2026-10-06）：按「功能隔离、不泄露」补三条：投影卡做成独立类型，拼角色扮演 prompt 的地方只收这个类型（DA18）；模块职责与导入方向写死并上锁（DA19）；开场白两份实现合一（DA20）；关系分批独立成模块。
v2：对照 Shiyu 的 spec 标准逐条补齐（路径机制表、规模表、出处表、全量扫描原始输出、调用点矩阵、变异预跑、先找现成库、能自动验的不手动）；6 个设计决定经 Shiyu 2026-10-06 按「先达成目的、再取最简单」拍板（DA12–DA17）。
调研与方案：`arc-stable-layer-research.md`。

## 0. 目的与达成标准

选了阶段 k 的会话里，模型能看到的一切（prompt + 检索）同时满足：
1. **经历类**只含阶段 1..k 发生的事；
2. **状态类**（性格、价值观、说话方式、雷点软肋、对人的口径、对白示例……）是阶段 k 那个人的样子；
3. 阶段 k 之后的事不出现。

另：**关系条数再多，也不能因输出上限截断或丢人**（DA17）。代码能保证的由代码保证；保证不了的进 §8。

## 1. 已定决策

| 编号 | 决策 | 来源 |
|---|---|---|
| DA1 | 经历类取 1..k；状态类只取阶段 k（全程成立的 + 阶段 k 特有的）；稳定类全书一份 | Shiyu |
| DA2 | 字段类别登记表覆盖 `CharacterCard` 全部 37 个叶子字段（附录 B）；结构锁「登记表 = 叶子全集」默认拒绝 | Shiyu 选 A |
| DA4 | 按类别分发：状态类挂到每个成立阶段；经历类只挂到最早阶段（修 C14） | 本 spec |
| DA5 | 关系口径 `note` 随阶段（`PhaseAttitude` 加 `note`），取 ≤k 最新一条 | 修 C8 |
| DA6 | 状态 / 经历字段的草稿每条 = 描述 + occurrences（阶段 + 该阶段摘录）；描述抽象、不讲剧情，例子只放摘录；稳定类提示词「只写从头到尾都成立的」 | MDRP、DREAM |
| DA7 | 「需要阶段编号的组」由登记表推导，统一等 G6；删写死的 G5 | 修 C6 |
| DA8 | `has_positions` 为假的卡不提供选阶段：`valid_phase` 恒 None；前端按 `character_arc.selectable` 显示 | Shiyu |
| DA9 | 拼人设 prompt 的地方全部读投影卡（含市场 @ 回复）；导出用最后阶段投影；审核遍历全部叶子 | 修 C11、C12 |
| DA10 | 旧卡不迁移内容；②③后统一重蒸 | Shiyu |
| DA12 | 按路径读写嵌套字段用 **pydash 8.1.0**（MIT，仅依赖 typing-extensions；沙箱实测对 pydantic 模型读写通过；dpath 实测失败） | Shiyu 拍板 |
| DA13 | 阶段特有内容存 `ArcPhase.overlay: dict[登记路径, list[str] \| str]`，校验器保证键与类型 | Shiyu 拍板 |
| DA14 | ①的 `ArcPhase.memories`、`dialogue_examples` 并入 overlay，加载时转换一次；`behaviors` 留给② | Shiyu 拍板 |
| DA15 | `dialogue_examples` 归状态类（只取阶段 k） | Shiyu 拍板 |
| DA16 | 卡片页 / 市场详情 / 群聊顶部只展示「全程成立的」（与编辑一致），各阶段特有内容在弧线列表里逐阶段展示 | Shiyu 拍板 |
| DA17 | **关系单独成步、按人分批生成，所有蒸馏入口都适用**：主调用只出关系对象名单 `relationship_targets`；之后按批（B=10 人，§2.3 推导）用**该入口主调用的同一前缀**再调用，命中缓存 | Shiyu 拍板（唯一能达成「不截断、不丢人」的选项） |

| DA18 | **类型隔离，防泄露**：`project_card` 返回 `ProjectedCard`（`CharacterCard` 的子类型，只能由 `project_card` 构造）。所有拼**角色扮演 prompt** 的入口只收 `ProjectedCard`，收到原卡直接抛错：`ChatEngine` 内部、`ContextEngine`、开场白、苏醒台词、市场 @ 回复。原卡只允许流向「处理卡片本身」的地方（存储、审核、引文核对、打标签、导出前的投影） | Shiyu「功能隔离，不要泄露」 |
| DA19 | **模块职责与导入方向**（§4.0），用导入锁守住：声明（`card_layers`）→ 纯计算（`arc_view`、`card_draft`、`relationship_batch`）→ 编排（`distiller`、路由）；下层不得导入上层 | 同上 |
| DA20 | 开场白生成**合并为一个函数**（收 `ProjectedCard`）：`/start_session`（`distill.py:1417` 起）与新会话开场变体（`text_manager.py:521-530`）现在各写一份 | C26 |

## 2. 已查实的约束（S0 逐条复核，任一不成立就停下）

C1. 叶子字段 37 个（附录 B 原始输出）；嵌套模型 `schema.py:18-24`（说话风格）、`:26-38`（关系）、`:45-56`（认知）、`:59-73`（心理）、`:117-123`（`ArcPhase`）、`:143-163`（`CharacterCard`）。
C2. ①投影 `arc_view.py:107` `project_card` 逐字段手写；登记之外字段原样。
C3. 注入点（附录 A）：`context_engine.py:348/353-355/364-370/418-419/422-423/426-427`；`chat_engine.py:146`（投影只在此）、`:906-935`、`:1005-1013`、`:1045`；`affinity_service.py:170-192`（经 `evaluation_pipeline.py:119` 传 `ctx.card`，即引擎卡）；`group_session.py:291`。
C4. 不经投影的读者：`market.py:840-845`；`export.py:20-37`；`auto_review.py:126-147`；`card_guard.py:74` 起（递归遍历，天然覆盖嵌套）。
C5. 维度与分组：`distiller.py:179-240`、`schema.py:171` `FORMAT_GROUPS`（A→G1；B/D/G/J/K→G2；C/H/N→G3；M→G4；F→G5；L/E/O→G6）。
C6. 分组路径写死 G5 等 G6：`distiller.py:2584`、`:2590-2599`。
C7. 输出上限：每组 `CARD_MAX_TOKENS = 8192`（:574）；一次读完 `LONG_OUTPUT_MAX_TOKENS` 16384（:580）。
C8. `note` 进 prompt（`chat_engine.py:1045`），`PhaseAttitude` 只有 `phase`、`attitude`（`schema.py:26-30`）。
C9. 前端（附录 D）：选择框 `RoleSetupModal.jsx:139-155`，阶段来自 `StartChatButton.jsx:41`；顶部展示 `CharCard.jsx:760-827`、`MarketCardDetail.jsx:677-704`、`GroupChatPage.jsx:1198-1303`；`ArcList.jsx:21-23` 读 `p.memories`；编辑 `EditCardModal.jsx:16-28/68-160/193-242` 只编辑顶层。
C10. `card_draft.py:133` `dispatch`：覆盖全部 → 顶层，否则挂到**每个**阶段；做法与记忆共用。
C11. 审核扁平化只取顶层若干字段 + 其余长度 > 20 的字符串（`auto_review.py:126-147`），`character_arc` 下内容（①以来的阶段记忆、做法、边界示范）都没进审核。
C12. `market.py:840` 不经投影；A 后顶层只剩全程成立部分。
C13. 样本卡（4 张，`docs/specs/arc-behaviors-draft-samples/`，10-04 产出）对照原文：5 字段 56 条里 22 条带后期剧情。**样本范围**：鲁迅两篇短篇；长篇未测。
C14. ①缺陷，沙箱在 `15b0b7c2` 实测：`dispatch([[2,3]], 3)` → `([], [[], [0], [0]])`；阶段 2、3 各挂「M」的卡，`project_card(c, 3).key_memories` → `['M', 'M']`。
C15. 端口与容器：一次性 PG 55433（S0 实查写进 §10）。
C16. ①阶段记忆 / 对白示例的全部读写点（附录 C）。
C17. 对白示例注入只取前 3 条（`context_engine.py:422-423`）。
C18. 关系条数**没有代码上限**：维度 F 要求覆盖「前置步骤」里模型自己枚举的、与本角色有直接言行往来的全部角色（`distiller.py:167-168`、`:188-199`）；该名单不回传给代码。
C19. 一次读完的提示词：系统段 =「以下是完整的文本内容：」+ 全文 + 指令（`distiller.py:1605-1630` `_longcontext_prompt`，注释写明为同书不同角色命中 Context Caching 而把全文放最前）。
C20. 非流式生成有 45 秒单次 / 60 秒总墙钟（`adapters/llm_adapter.py:134` `_GEN_ATTEMPT_S = 45`；`distiller.py:834` 注释）；**长输出必须走流式**（`_collect_stream`，:828）。
C21. 后置步骤唯一汇合点 `Distiller.finalize_card`（:1832）：关系去重 → 引文核对 → 贴对白示例；三条产卡通道各调一次（`distill.py:517` 等）。
C22. 产草稿的 4 个入口：`distill`（:1537 `card_from_draft`）、`_distill_longcontext`（:1646）、`distill_incremental`（:2269）、`distill_incremental_stream`（流式：一次读完 :2317-2322 → `_distill_longcontext_stream`；超长 → 分组 :2560 起）。
C23. 官方价目（https://api-docs.deepseek.com/quick_start/pricing，2026-10-06 S0 读取页面原始表格）：现行模型 `deepseek-flash` 每百万 token —— 输入缓存命中 $0.003（错峰）/ $0.006（高峰），未命中 $0.15（错峰）/ $0.3（高峰），输出 $0.6（错峰）/ $1.2（高峰）；错峰价为高峰价的一半（页面脚注）。页面脚注另明：`deepseek-v4-flash` 已退役，其请求由 DeepSeek-V4.1-Flash 承接、按 Flash 价计费。（原「v4-flash $0.007/$0.014/$0.22/$0.66、高峰页截断」取自搜索摘要、且是已退役模型的价，与官方页面不符，S0 已更正，见 §10。）
C25. 读原卡的全部地方（附录 E 原始扫描）。其中**拼角色扮演 prompt** 的：`chat_engine`（构造时投影）、`context_engine`（收引擎卡）、`distill.py:1417` 起（开场白，已投影）、`distill.py:291` `_generate_awakening`（苏醒台词，**原卡**）、`text_manager.py:521-530`（新会话开场变体，**原卡**）、`market.py:837-845`（**原卡**）。处理卡片本身的：`card_draft`、`card_quotes`、`card_relationships`、`text_manager._guard_card`、`export`、`_auto_tag`、各路由的读写。
C26. 开场白有两份实现：`distill.py:1417-1452` 与 `text_manager.py:521-530`，提示词各写各的。
C24. 实测卡片规模（仓库自带 DeepSeek tokenizer `core/tokens.py`）：样本卡 2,074–3,894 token；本 spec 涉及的状态 / 经历条目 24–29 条；一段 25 字摘录的 occurrence 约 33 token。
C27. 线上模型是 `deepseek-flash`（`adapters/llm_adapter.py:717` `_FALLBACK_MODEL = "deepseek-flash"`；`config.example.yaml:18` `model: deepseek-flash`）。`deepseek-v4-flash` 退役不影响本段 —— 本段不跑真实模型，价目只用于 §2.3 估算。

### 2.1 路径上已有的机制（加东西前逐个核对前提）

| 机制 | 位置 | 计时 / 计数从哪开始、依赖什么 | A 是否改变前提 | 处理 |
|---|---|---|---|---|
| 分组并行线程 + 结果队列 | `distiller.py:2560-2610`（`ctx_thread`、`fmt_queue`） | 组间独立并行；G5 等 G6 写死 | **改变**：依赖组变为 G2、G3、G4（G5 改为只出名单、不再依赖） | DA7：依赖由登记表推导；总耗时 ≈ G6 + max(依赖组、关系分批) |
| 每组返回发心跳 | 同上 `yield {"heartbeat": True}` | 每收一组一次 | 等 G6 期间心跳间隔 = G6 时长（与今天 G5 等 G6 相同） | 不变 |
| 每组输出上限 | `CARD_MAX_TOKENS` 8192 | 每组一次调用 | 关系从组里拆走（DA17），其余组加摘录后见 §2.3 | 规模表核算 |
| 一次读完输出上限 | `LONG_OUTPUT_MAX_TOKENS` 16384 | 整卡一次调用 | 关系拆走后，整卡（含摘录）≤ §2.3 估算 | 规模表核算 |
| 非流式墙钟 | `_GEN_ATTEMPT_S` 45 秒 / 60 秒总墙钟 | 每次非流式生成 | 关系分批调用若走非流式会撞墙 | DA17 分批调用**必须走 `_collect_stream`** |
| Context Caching | `_longcontext_prompt` 前缀 | 前缀逐字相同才命中 | 分批调用要命中，前缀必须由同一函数产出 | 分批调用复用该函数的前缀部分（抽成一个前缀函数），不另拼 |
| 后置步骤汇合点 | `finalize_card` | 三通道各一次 | 关系分批要在 `card_from_draft` 之前（位置核对需要草稿锚点） | 分批在各入口产草稿处调用同一个 `_relationships_batched`，见 §4.5 |

### 2.2 通道 × 执行上下文 × 守它的测试

| 通道 | 执行上下文 | 本段改动 | 测试 |
|---|---|---|---|
| 蒸馏 4 个入口（C22） | 同步线程 / 流式生成器线程 | 草稿形态、关系分批 | E11–E14 |
| 分组编排 | 生成器 + 每组一条线程 | 依赖组推导 | U13 |
| 聊天 prompt 构建 | `to_thread` / 线程池 | 读投影卡 | E1–E5 |
| 开场白 / 市场 @ 回复 | async 路由 | 投影 | E6、E7 |
| 审核 | 后台 | 遍历全部叶子 | U14 |
| 前端选择框 / 弧线列表 | 浏览器 | `selectable`、overlay 展示 | E9、E10 + Playwright |

### 2.3 规模表（真实数字）

| 对象 | 规模 | 上限来源 | 策略 |
|---|---|---|---|
| 状态 / 经历条目 | 24–29 条 / 卡（C24 实测） | 提示词条数（软约束，如性格 3-5 个） | 不截断 |
| 每条加摘录后的增量 | 一般 1–2 段摘录 ≈ 33–66 token；最坏每条 4 段 ≈ 132 token | C24 | 整卡增量一般约 1,400 token，最坏约 3,800 token |
| 一次读完整卡输出（关系拆走后） | 现 2,074–3,894 + 增量 ≤ 3,800 → **≤ 约 7,700 token** | 16384 | 不撞上限 |
| G2（性格等 5 字段）一组 | 约 15 条 × 最坏 132 ≈ 2,000 token | 8192 | 不撞上限 |
| **关系** | **无代码上限**（C18）。每人最坏：4 阶段 ×（态度 40 + 口径 50 + 摘录 33 + JSON 20）+ 对象名与关系 30 ≈ **600 token** | 每批调用 8192 | **B = 10 人 / 批**（10 × 600 = 6,000，留 25% 余量）；批数 = ⌈人数 / 10⌉ |
| 分批费用（例：50 万 token 的书、40 人 = 4 批） | 每批输入 ≈ 50 万 token，首批之后命中缓存：命中 4 × 0.5M × $0.003–0.006 = **$0.006–0.012**；首批若未命中另加 0.5M × $0.15–0.3 = **$0.075–0.15**；输出 24,000 × $0.6–1.2/M = **$0.0144–0.0288** | C23 | 单卡一次性；命中率由用量字段 `prompt_cache_hit_tokens` 记录进日志 |
| 分批耗时 | **未实测**；各批之间互不依赖，可并行 | — | 并行发出；测试复刻「批数 > 1 时仍全部汇总、任一批失败整步失败」的关系，不涉及时限 |

### 2.4 出处表

| 设计点 | 出处（链接 + 章节） | 落点 |
|---|---|---|
| 稳定层只放与生俱来的；其余人设随事件增量更新 | DREAM，KDD 2026，https://arxiv.org/abs/2608.05170 ，§3.1.1 式(1)、附录 Table 11 | DA1、§3 |
| 证据与描述分离；条目带适用时期 | MDRP，Findings of ACL 2026，https://aclanthology.org/2026.findings-acl.1175/ ，附录 C Table 7 | DA6 |
| 性格写成「情境 → 表现」并附原文片段 | ReverieMem（预印本），https://arxiv.org/abs/2606.25632 ，§3.2 Personality Layer | DA6 描述规则 |
| 不变特质与随经历变化的状态分开 | DPC，ACL 2026，https://aclanthology.org/2026.acl-long.1336/ | DA1 |
| 只给当前时期状态 | ArcANE（预印本）§3.1；CFSM（ICLR 2026，②调研文档已读） | DA1 状态类 |
| `computed_field` 计算字段随序列化输出 | pydantic 官方文档 Fields › Computed fields：https://docs.pydantic.dev/latest/concepts/fields/#the-computed_field-decorator | DA8 |
| `model_validator(mode="before")` 加载时转换 | pydantic 官方文档 Validators › Model validators：https://docs.pydantic.dev/latest/concepts/validators/#model-validators | DA14 |
| pydash `get` / `set_` | https://pypi.org/project/pydash/ 8.1.0；文档 https://pydash.readthedocs.io/ | DA12 |
| Context Caching 前缀规则 | https://api-docs.deepseek.com/guides/kv_cache （代码注释已引，C19） | DA17 |
| 价目 | C23 | §2.3 |

## 3. 登记表（`core/card_layers.py`，唯一来源；附录 B 的 37 个路径）

| 类别 | 投影规则 | 路径 |
|---|---|---|
| 稳定 | 原样 | `name`、`identity`、`background`、`speaking_style.vocabulary_level`、`speaking_style.taboo_words`、`cognitive.education_level`、`cognitive.vocabulary_level`、`psyche.openness/conscientiousness/extraversion/agreeableness/neuroticism/affinity_baseline/volatility/grudge_inertia`（数值归③再议） |
| 状态（列表） | 阶段 k 特有在前 + 顶层（B3 顺序契约：读者按 `[:3]`/`[:2]` 取前几条时，阶段 k 的条目不被全程条目挤掉） | `personality_traits`、`values`、`inner_tensions`、`emotional_patterns`、`speaking_style.catchphrases`、`psyche.triggers`、`psyche.soft_spots`、`dialogue_examples`（DA15） |
| 状态（单值） | 阶段 k 有值用之，否则顶层 | `decision_style`、`speaking_style.tone`、`speaking_style.sentence_pattern`、`cognitive.speech_style` |
| 经历（列表） | 顶层 + 1..k | `key_memories` |
| 经历（单值） | 顶层 + 1..k 用「；」连接 | `cognitive.knowledge_scope` |
| 专门投影 | 各自函数 | `character_arc.axis`、`character_arc.phases`、`relationships`（DA5）、`first_message`、`situation_behaviors`（②接入） |
| 不进 prompt | — | `tags`、`awakening_message`、`character_arc.source_fingerprint` |

每个 `FieldSpec` 另带 `label`（字段中文名，非空且唯一）—— 后端中文名的唯一来源（B5）。

## 4. 设计（后端）

### 4.0 模块职责与导入方向（DA19）

| 模块 | 职责 | 可以导入 | 不得导入 |
|---|---|---|---|
| `core/card_layers.py`（新） | 只做声明：登记表、`FieldSpec`、按路径读写（pydash 薄封装） | `core.schema`、pydash | 其余 core 模块 |
| `core/arc_view.py` | 纯计算：阶段归一、通用投影、`ProjectedCard` 的唯一构造处 | `schema`、`card_layers` | 任何 IO、LLM、路由 |
| `core/card_out.py`（新） | 出卡：存储行 → 前端卡片负载的**唯一一处**；`character_arc.selectable` 按当前 phases / 指纹现算（派生值不落库，B4） | `core.schema` | 其余 core 模块、路由 |
| `core/card_draft.py` | 纯计算：草稿 → 存卡、按类别分发 | `schema`、`card_layers`、`phase_anchoring`、`quotes` | `distiller`、IO |
| `core/relationship_batch.py`（新） | 关系分批：切批、并行、汇总、失败口径；**调用模型的函数由调用方注入** | `schema`、`card_layers`、`concurrency` | `distiller`、`adapters`、路由 |
| `core/opening.py`（新） | 开场白 / 苏醒台词的提示词与生成，收 `ProjectedCard` 与注入的模型调用 | `schema`、`arc_view` | 路由、存储 |
| `core/distiller.py` | 编排：提示词、分组、入口；给 `relationship_batch` 注入 `_collect_stream` 与前缀 | 以上全部 | 路由 |
| 拼角色扮演 prompt 的模块（`context_engine`、`chat_engine`、`group_session`、`affinity_service`、`opening`、`market.py`） | 只读 `ProjectedCard` | `arc_view` | 不得调用 `CharacterCard.model_validate` 后直接拼 prompt |


### 4.1 登记表与通用投影
- `core/card_layers.py`：`REGISTRY: dict[str, FieldSpec(layer, kind, label)]`；读写用 pydash（DA12）。`label` 是**字段中文名的唯一来源**（B5）：导出正文、阶段表头一律取它，不再各自写死中文名。
- `project_card` 改为按登记表通用投影；custom 路径调各自函数（沿用①）。逐字段手写分支删除。
- `arc_view` 另出两个纯函数供「展示/导出」用，字段名也只从登记表取（B5、B6）：`phase_header(index, label)` = 「阶段 i·label」（无 label 则「阶段 i」）—— 表头写法只此一处；`card_outline(card) -> (全程, 逐阶段)` —— 全程 = 原卡顶层的 state/experience 字段，逐阶段 = 第 i 个阶段的 `overlay`，两层都按登记表遍历，一行是 `(路径, 中文名, 文本)`。导出与展示共用它，阶段特有内容不再既进全程段又进阶段段（B6）。
- `ArcPhase.overlay` 校验：键 ∈ 登记表 state / experience 路径，值类型与 `kind` 一致。①格式 `memories` / `dialogue_examples` 在 `model_validator(mode="before")` 搬进 overlay（唯一转换点）。
- `CharacterArc.has_positions()`（判定从 `arc_view` 挪入 schema，是「能否选阶段」的唯一口径）；`valid_phase` 在 `has_positions` 为假时恒 None。
- **`selectable` 不是模型字段（B4）**：它是派生值（起点齐全 + 指纹），随序列化落库就会过期 —— 旧卡没这个键；位置由后台作业补，补完那张卡存下的仍是 `false`。改由 `core/card_out.py::out_card` 在**出卡时**按当前 `phases` / 指纹现算；两个开聊接口（`/cards/by-text/{id}`、`/cards/standalone`，即 `StartChatButton` 的两条取数路径）过它，其余接口不接（市场、群聊不走开聊按钮）。

### 4.2 分发（`card_draft.py`）
- `dispatch` 加 `layer` 参数：state → 每个最终阶段（全部 → 顶层）；experience → 只最早阶段（从阶段 1 起覆盖全部 → 顶层）。`by_phase[` 仍只此一处。
- 单值 state：同一阶段多条保留第一条 + warning。
- 草稿形态由登记表派生（state / experience 路径 → `list[DraftTimed]`），禁止逐字段手写；位置检查复用 `phase_anchoring.verify`，编号过滤复用 `_valid_number_rows`。

### 4.3 蒸馏提示词
- 维度 B、C（语气、句式、口癖）、D、G、J、K、M（雷点、软肋）、N（说话方式、知识范围）改成「描述 + occurrences」，引用 `_PHASE_EXCERPT_RULE`；描述规则、稳定类规则各一个常量。
- 维度 F 改为：主调用只出 `relationship_targets`（对象名列表，标准名）；关系详情由 §4.5 生成。

### 4.4 分组编排
- `PHASE_DEPENDENT_GROUPS` 由登记表推导（组内有 state / experience 路径即依赖）：**G2、G3、G4 依赖；G1、G5 不依赖**。删 `if group != "G5"`。
- G5 不再出关系详情，只出 `relationship_targets`（对象名不需要阶段编号，所以 G5 改为不依赖、与 G1、G6 一起先跑）；关系详情在 G6 返回后由 §4.5 分批生成。

### 4.5 关系分批（DA17，所有入口同一个函数，独立模块）
- `core/relationship_batch.py`：`batch_relationships(targets, phases, *, stream_call, prefix, batch_size=REL_BATCH_SIZE) -> list[DraftRelationship]`，`stream_call` 由 `Distiller` 注入（即 `_collect_stream` 的绑定），本模块不认识 `Distiller`。语义：按 B=10 切批，**每批走 `_collect_stream`**（C20），批间并行；前缀由与主调用相同的前缀函数产出（一次读完：全文前缀，C19；分组：与格式化组相同的共享前缀）；任一批失败 → 整步失败（与格式化组失败口径一致），不静默丢人。
- 4 个入口（C22）在拿到含阶段的草稿后、`card_from_draft` 之前调用它，结果填回草稿的 `relationships`，然后照常 `card_from_draft`（位置核对、分发不变）。
- 用量与缓存命中写入现有记账（`_chat_accounted` / `_collect_stream` 已在本级记账）。

### 4.6 其余读者
- `market.py:840`：`project_card(char, None)` 后再拼。
- 苏醒台词 `distill.py:291` 与两处开场白（C26）：都改为调用 `core/opening.py`，入参 `project_card(card, arc_phase)` 的结果（苏醒台词用最后阶段）。
- `ContextEngine.__init__` 与 `core/opening.py` 的入口对非 `ProjectedCard` 入参直接抛 `TypeError`。
- `export.py`：导出用 `card_outline(card)` 的两层视图 —— 全程字段写一遍，阶段特有内容只在**该阶段的段落**里出现一次（B6）；字段名取登记表 `label`（B5）；表头取 `phase_header`（B6）。说话风格单列进 `mes_example`，不重复进人设正文。
- `auto_review._flatten_card` 与 `card_guard` 共用一个递归遍历函数（抽到 `core/moderation/`）。
- `card_quotes.VERIFIED_FIELDS` 的 overlay 路径由登记表派生。

## 5. 设计（前端，触发链）

- 选择框：`StartChatButton` 传 `selectable`；`RoleSetupModal` 仅在为真时渲染。触发链：打开弹窗 → 读 `selectable` → 假则不渲染、提交不带 `arc_phase`。
- 弧线列表：`ArcList` 每阶段遍历 `overlay`，字段标签用前端映射表，未知键显示原名；只读。
- 卡片页 / 市场详情 / 群聊顶部：不改（DA16，展示全程成立的部分）。
- 编辑：`EditCardModal` 保存时 overlay 原样交回（补断言）。
- **自动验收**：新增 `e2e/arc-phase-fields.spec.js`（`page.route` 模拟）：`selectable` 为假时不出现选择框；为真时出现；弧线列表展示阶段 2 的性格与口头禅；截图一张弧线列表给 Shiyu 看排版。不改 `e2e/helpers.cjs`。

## 6. 测试（先在 `15b0b7c2` 上红，再绿；两侧）

### 6.1 单元
U1 登记表 = 叶子全集（多、少都红）；U2 状态列表取「顶层 + k」；U3 状态单值回落；U4 嵌套路径；U5 经历 1..k；U6 稳定原样、不改原卡；U7 分发按类别（[2,3] 两侧）；U8 单值同阶段两条；U9 关系 note 随阶段；U10 `selectable` 与 `valid_phase`；U11 ①格式卡迁移后投影与①一致（且 C14 不再重复）；U12 overlay 非法键 / 类型；U13 依赖组推导与等待（G2、G3、G4 依赖；G1、G5 不依赖）；U14 审核覆盖 overlay 等；U15 市场 @ 回复与导出读最后阶段；**U16 关系分批**：23 人 → 3 批，每批 ≤10，全部汇总、顺序稳定；任一批失败整步失败；每批走流式；前缀与主调用前缀逐字相同；**U17** B 由常量定义一次；**U18** 原卡传给 `ContextEngine` / `opening` 抛 `TypeError`，`ProjectedCard` 通过；**U19** 两处开场白走同一函数、读投影卡（选阶段 1 时提示里没有后期口头禅）；**U29** 导出里阶段记忆 / 阶段性格各只出现一次，且落在对应阶段的段落里（B6；`test_export_phase_content_appears_once_in_its_section`）；**U30** 导出正文的字段名取自登记表 `label`（改登记表即改导出，B5；`test_export_field_names_come_from_registry`）。

### 6.2 调用点矩阵
| 入口 \ 输出 | 状态取 k | 经历取 1..k | 关系口径按阶段 | 关系不截断不丢人 | 测试 |
|---|---|---|---|---|---|
| E1 卡片层 / 扩展层 | ✓ | ✓ | — | — | `test_ctx_layers_projected` |
| E2 心理注入 | ✓ | — | — | — | `test_psyche_projected` |
| E3 认知注入 | ✓ | ✓ | — | — | `test_cognitive_projected` |
| E4 关系口径、群聊 | — | — | ✓ | — | `test_relationship_note_projected` |
| E5 好感评估 | ✓ | — | — | — | `test_affinity_reads_projected` |
| E6 开场白 | ✓ | ✓ | — | — | `test_opening_projected_fields` |
| E7 市场 @ 回复 | 最后阶段 | ✓ | — | — | `test_market_reply_reads_projected_card`（行为面，替换 MA15 的结构锁兜底） |
| E8 导出 | 全程 + 逐阶段 | ✓ | — | — | U29、U30 |
| E9 前端选择框 | `selectable` | — | — | — | `RoleSetupModalSelectable.test.jsx` + e2e |
| E10 前端弧线列表 | overlay 展示 | — | — | — | `ArcListOverlay.test.jsx` + e2e |
| E11 `distill` | 草稿 → overlay | ✓ | ✓ | ✓ | `test_entry_distill_fields` |
| E12 `_distill_longcontext` | 同上 | ✓ | ✓ | ✓ | `test_entry_longctx_fields` |
| E13 `distill_incremental` | 同上 | ✓ | ✓ | ✓ | `test_entry_incremental_fields` |
| E14 `distill_incremental_stream`（一次读完 / 分组两支） | 同上 | ✓ | ✓ | ✓ | `test_entry_stream_fields`（两支各一） |
| E15 卡片顶部展示 3 处 | 全程成立部分 | — | — | — | `CardTopShowsLifelong.test.jsx` |
| E16 编辑保存 | overlay 原样交回 | — | — | — | `EditCardModalObjectLists` 补断言 |
| E17 引文核对 / 注入守卫 | overlay 覆盖 | — | — | — | `test_quotes_cover_overlay`、`test_guard_covers_overlay` |
| E18 苏醒台词 | 最后阶段投影 | ✓ | — | — | `test_awakening_projected` |
| E19 新会话开场变体 | 投影卡 | ✓ | — | — | `test_opening_variation_reads_projected_card`（行为面，替换 MA23 的结构锁兜底） |
| E20 出卡（两个开聊接口） | `selectable` 现算 | — | — | — | `test_out_card_recomputes_stale_false_to_true` 等 6 条 |

### 6.3 结构锁
S1 登记表 = 叶子全集；S2 `project_card` 不出现具体字段名分支（custom 除外）；S3 拼人设 prompt 的模块只读投影卡（`market.py`、开场白含 `project_card(`）；S4 描述规则、稳定类规则常量各一次；S5 `distiller.py` 无写死 `"G5"` 依赖；S6 `by_phase[` 只在 `dispatch`；S7 起点判定只在 `CharacterArc.has_positions`；S8 关系分批只有 `_relationships_batched` 一处、批大小常量一次；S9 分批调用只经 `_collect_stream`；S10 审核与守卫共用一个遍历函数；**S3（改）** 拼角色扮演 prompt 的入口参数类型为 `ProjectedCard`，`ProjectedCard(...)` 只在 `arc_view.py` 出现；**S11** 导入方向（§4.0）：AST 扫描各模块导入，违反即红；**S12** 开场白提示词只在 `core/opening.py`；**S13** 关系生成口径（五条规则）只在 `relationship_batch.py` 一处、`_batch_prompt` 确实引用它；**S14** 分批前缀只能由共享前缀函数（`book_prefix` / `format_prompt_shared`）产出、且确为各组提示词的前缀；**S15 出卡只有一处（B4）**：`out_card` 仅在 `core/card_out.py` 定义一次、只被 `list_cards` / `list_standalone_cards` 调用；`schema.py` 无 `computed_field` / `def selectable`；`"selectable"` 字面只在 `card_out.py`。**S16（B5、B6）**：`FieldSpec.label` 非空且**互不重复**；`phase_header` 只在 `core/arc_view.py` 定义一次、四个产表头的模块（`export` / `context_engine` / `distiller` / `opening`）都经它、别处无内联 `f"阶段 {…}·"`；`card_outline` 只由 `export` 用；`ArcView` 无 `memories` 字段（同一份记忆不存两处，B5）。

## 7. 变异（两方向）与预跑

| 编号 | 变异 | 应红 | 预跑 |
|---|---|---|---|
| MA1–MA7 | 状态累加 / 取全部 / 丢顶层；单值不回落；经历 1..k+1 / 只取 k；改原卡 | U2–U6 | ✅（原型一） |
| MA8 / MA8b / MA8c | 登记表漏字段（原型 / 真实 schema）、多一个不存在的路径 | U1 | ✅ |
| MA9 / MA10 | experience 挂每阶段 / state 只挂最早 | U7 | ✅（原型二） |
| MA12 | 起点判定不看指纹 | U10 | ✅（真实函数副本） |
| MA11 | 关系 note 不随阶段 | U9 | 实现后 |
| MA13 | 依赖组不等 G6 | U13 | 实现后 |
| MA14 | 审核跳过 overlay | U14 | 实现后 |
| MA15 | 市场 @ 回复不投影 | `test_market_reply_reads_projected_card`（E7 行为面） | ✅（本段） |
| MA16 | ①格式卡不迁移 | U11 | 实现后 |
| MA17 | overlay 接受未登记键 | U12 | 实现后 |
| MA18 | 分批丢最后一批 / 批大小越界 | U16 | 实现后 |
| MA19 | 分批走非流式 | U28（`test_relationships_batched_goes_through_stream`，行为面） | ✅（本段） |
| MA20 | 分批前缀与主调用不同 | U16 | 实现后 |
| MA21 | `ContextEngine` 收原卡不抛错 | U18 | ✅（原型三） |
| MA22 | `relationship_batch` 导入 `distiller` | S11 | ✅（原型三） |
| MA23 | 新会话开场变体绕过 `opening.py` 用原卡 | `test_opening_variation_reads_projected_card`（E19 行为面） | ✅（本段） |
| MA1–MA3 / MB1–MB3（改） | 状态顺序契约（阶段 k 在前）/ 分发只有一套（`kind` 定格子容量） | U2、U8 | ✅（B1、B3） |
| MB4 | 每个字段各自归一化整本原文 | U20 | ✅（效率 #1） |
| MB5–MB7 | 分批前缀退回主调用系统提示 / 关系口径内联回维度 F / 组前缀塞进整段格式提示 | U22、S13、S14 | ✅（B2 提示词、B7） |
| MB8 | 阶段 note 不落卡 | U23 | ✅（B7） |
| MB9 / MB10 | 缺人不补跑 / 补跑后仍缺只丢人不报错 | U24、U25 | ✅（B2 关系完整性） |
| **MB11** | **出卡不重算 `selectable`（退回透传存量值，B4 的旧形态）** | **E20** | **✅（本步）** |

B5/B6（步骤 7）的判别性另由一次性脚本核实（导出把各阶段内容塞进全程段 → U29 红；导出写死字段
中文名 → U30 红；登记表两条 `label` 重复 → S16 红），脚本入库
`docs/specs/artifacts/arc_phase_fields_b5b6_mutations.py`。按 Shiyu 口径本轮**只往常设驱动加 4 条
跨模块规则**（步骤 1/3/4/6），步骤 7 的这三条不入常设驱动，仅作本步判别性凭据。MA15/MA23 的靶子
指回本节 §6.2 的行为测试名，由步骤 8 随驱动一并改。

预跑原始输出（原型一：登记表驱动的通用投影）：

```
RED   放宽 状态取 1..k 累加 | 1 failed, 5 passed in 0.03s
RED   放宽 状态取全部阶段 | 3 failed, 3 passed in 0.03s
RED   过严 状态丢顶层 | 2 failed, 4 passed in 0.03s
RED   过严 标量阶段空时不回落顶层 | 1 failed, 5 passed in 0.02s
RED   放宽 经历取 1..k+1 | 1 failed, 5 passed in 0.02s
RED   过严 经历只取阶段 k | 1 failed, 5 passed in 0.03s
RED   改原卡 | 1 failed, 5 passed in 0.02s
RED   漏注册字段（knowledge_scope） | 1 failed, 5 passed in 0.02s
```

预跑原始输出（原型二：按类别分发 + 真实 schema 登记表 + 真实 `has_positions` 副本）：

```
RED   MA9  experience 挂每个阶段 | 1 failed, 4 passed in 0.11s
RED   MA10 state 只挂最早阶段 | 1 failed, 4 passed in 0.12s
RED   MA8b 登记表漏 cognitive.knowledge_scope | 1 failed, 4 passed in 0.13s
RED   MA8c 登记表多一个不存在的路径 | 1 failed, 4 passed in 0.10s
RED   MA12 起点判定不看指纹 | 1 failed, 4 passed in 0.11s
```

预跑原始输出（原型三：类型隔离 + 导入方向锁）：

```
RED   MA21 ContextEngine 收原卡不抛错 | 1 failed, 1 passed in 0.10s
RED   MA22 relationship_batch 导入 distiller | 1 failed, 1 passed in 0.12s
```

## 8. 已知局限
1. 描述文字里夹带的后期例子，代码查不出；②③后统一重蒸时对照原文统计，明显时再议分段生成。
2. 名著的模型自带记忆：各方案都管不了，由①边界说明压住。
3. 阶段边界本身准不准：依赖第二步锚点。
4. 稳定类字段只靠提示词。
5. 关系分批的耗时未实测；首批是否命中缓存取决于主调用是否已写入缓存，命中率记日志。

## 9. 测试与环境
本地只跑受影响文件 + 新文件 + 相关 vitest + 本段 e2e；一次性 PG 55433（先查冲突，跑完删除）。合并门是分支 CI；合并只做 git 操作。不跑真实模型。新增依赖 pydash 8.1.0 进 `requirements.txt`（按仓库锁文件规矩）。

## 10. 补充
本段改动面内新发现直接修并写进这里；需要拍板的停下报告。

### S0 复核记录（2026-10-06，`15b0b7c2`）

- **C23 价目更正**：C23 原数字取自搜索摘要、且是**已退役** `deepseek-v4-flash` 的价，与官方
  页面不符。S0 用 `curl -L` 取 `https://api-docs.deepseek.com/quick_start/pricing` 的**原始
  HTML 表格**（非模型转述）：整页 HTML 中 `$0.007` / `$0.014` / `$0.22` **零命中**；现行
  `deepseek-flash` 命中 $0.003/$0.006、未命中 $0.15/$0.3、输出 $0.6/$1.2（错峰/高峰），
  高峰未命中与输出**并未截断**。页面脚注：`deepseek-v4-flash` 已退役、按 Flash 价计费。
  已按现行页面更正 C23 与 §2.3 的分批费用例（B=10 由 token 上限推导，不变），并新增 C27。
- **环境实查（C15）**：`docker ps` 显示 `character-distill-test-postgres-1` 占 **55432**
  （共享 `charsim_test`，**不动**）、`character-distill-postgres-1` 占 5432、
  `distill-identify-postgres-1` 占 5433。**55433 空闲**（无监听、无容器）→ 本段一次性 PG 用
  **55433**，容器名 `cd-test-arc-phase-fields`（`--tmpfs` 数据目录，跑完 `docker rm -f`）。
  55434 被**已退出**的 `distill-429-test-pg` 占着（未监听，不影响）。
- **依赖**：`pydash` **未安装**（`import pydash` → ModuleNotFoundError），需按 §9 入 `requirements.txt`。
- **附录 A 复现瑕疵**：附录 A 抬头写「原始输出」，但在 `core` + `web` 上按字段名 `git grep -n`
  会多出 `core/distiller.py`（提示词/草稿读写方）与 `core/schema.py`（定义方）两类行，附录 A
  把它们剔除了。剔除对「读者扫描」的目的合理，但「原始输出」与可复现性不符。属文档瑕疵，
  **不影响 C3/C4/C25 的实质结论**（那些行已在附录 A 中且逐字复现）。
- **C1/附录 B**：按「展开 BaseModel、不展开 `list[BaseModel]`」枚举 = **37** 叶子，与附录 B 逐字一致。
- **C14**：实跑复现 `dispatch([[2,3]],3)` → `([], [[], [0], [0]])`；`project_card(c,3).key_memories` → `['M','M']`。
- **C24**：实测样本卡 token 掌柜 2074 / 孔乙己 2675 / 赵太爷 2789 / 阿Q 3894（区间与规格一致）。
- 附录 C、D、E 扫描逐行复现（附件命令原样可重跑）。

### 效率 #1 实测（2026-10-06，`7c3aec77` 前后各一次，本机 `.venv`）

判据：一次 `card_from_draft` 里「规范化原文 + 各阶段区间」这个上下文建几次（= `core.phase_anchoring`
自己的 `normalize` 被调几次）。修复前**每个字段各建一次**（做法 / 记忆 / 关系 + 每个 state、
experience 字段），修复后只在 `card_from_draft` 开头建一次。样本卡 + 原文 = 仓内
`tests/fixtures/kongyiji.txt`（公版；2623 字，规范化后 2187 字），草稿按 §2.3 规模
（6 阶段、26 条状态/经历条目 + 做法 6 条 + 记忆 4 条 + 关系 3 人）。

| | `normalize(整本原文)` 次数 | 耗时 ms（30 次：最小 / 中位 / 均值 / 最大） |
|---|---|---|
| 修复前（每字段各归一化一遍） | 16 | 15.90 / 17.47 / 17.85 / 22.13 |
| 修复后（开头建一次） | 1 | 4.59 / 5.12 / 5.35 / 6.79 |

「次数」这一列与书的大小无关（16 → 1）；「耗时」这列的省量随书线性变大（2.6KB 样本省约 12ms，
50 万 token 的书同理到秒级）。判别器 `U20`（`test_anchors_built_once_per_card`）钉住次数这一列，
变异 `MB4` 打红它。产数脚本入库：`docs/specs/artifacts/arc_phase_fields_perf.py`（原样可重跑）。

### B2 提示词 / B7 关系口径（2026-10-06，本步一个提交）

**缺陷**：关系分批（`_relationships_batched`）把**主调用的整段系统提示**当前缀传给
`core.relationship_batch._batch_prompt`。主提示里有维度 F 的「关系的类型、态度与阶段变化由
后续单独生成，这里**不要**写」——分批这一步要的恰恰是关系详情，等于把「不要写」带进了
「给我写」。同一条规则的另一个症状：五条关系生成口径（单向视角 / note 是注入立场 / 只写
态度变了的阶段 / 无阶段 phase 0 / quote 是该阶段原文摘录）哪一处都没写成常量。

**改法**（提示词 = 共享前缀 + 本步指令）：

- `book_prefix(text)` = 「以下是完整的文本内容：」+ 正文（一次读完路径的共享段）；
  `distill_instruction(name)` = 角色行 + 分析维度 + 输出规则 + 草稿 JSON。`_longcontext_prompt`
  = 两者拼接，**输出逐字节不变**（已验证）。
- `format_prompt_shared(name)` = 各组提示词共同的开头（角色行 + 头像句 + 铁律），是每个
  `format_prompt_after(group)` 的前缀（已验证）—— 组路径的共享段。
- 五个 `_relationships_batched` 调用点的 `prefix=` 全部换成这两个产出者之一；主调用的
  `system_prompt` 不再出现在 `prefix=` 位置（锁 S14）。
- 五条口径收进 `core.relationship_batch.RELATIONSHIP_RULES`，只由 `_batch_prompt` 引用
  （锁 S13）；与维度 F 的「只出名单」是同一件事的两步、两句话。
- `DraftAttitude` 加 `note`；`_convert_relationships` 把它写进 `phase_attitudes[i].note`
  （`arc_view._project_relationships` 早已按 ≤k 最新一条投影，只缺草稿侧这一半）。

**判据**：`U22`（分批系统提示以 `book_prefix` 开头、不含「只出名单」）、`U23`（按阶段 note
从草稿流到投影）；锁 `S13 / S14 / S14b`。变异 `MB5`（前缀改回主提示）打红 `U22`、`MB6`
（口径内联回维度 F）打红 `S13`、`MB7`（组共享前缀退回整段格式提示）打红 `S14b`、`MB8`
（阶段 note 不落卡）打红 `U23` —— 四条均实测 RED。

### B2 关系完整性（2026-10-06，本步一个提交；Shiyu 更正后的口径）

**缺陷**：主调用（维度 F / G5）只出关系**名单**，详情由 `core.relationship_batch` 按 10 人一批
补。模型在某一批里漏掉一个（或把 target 写成别名 / 写错名）时，**整批仍算成功** —— 漏掉的那个人
就被静默丢掉，落卡后从成品看不出来。既有的失败口径只覆盖「某一批抛异常」，不覆盖「这一批少回了一个」。

**契约**：名单是**我们发出的**，提示词要求 `target` 逐字照抄，故比对是**逐字字符串**
（`dedupe_relationship_targets` 同口径：`str(target)`）。**不做别名解析** —— 别名→标准名要
`{name, aliases}` 名单，那要读存储（`character_roster.aliases_for`），蒸馏入口（`distill` /
`distill_incremental` / `_distill_longcontext` / 组路径 / `fill_relationships`）手里都没有；
让 `relationship_batch` 去认识 roster 又违反「本模块不认识 Distiller、roster」的注入原则。
分三步走的这条路里，第一步给名单、第二步照名单写，名字本就是同一批字符串。

**改法**（只动 `core/relationship_batch.py` 一处）：

- 切批/并行/汇总抽成 `_fan_out(batches, run)`；首轮跑完全部批次后，按**逐字比对**算
  `missing = [谁在名单里但没回]`。
- `missing` 非空 → **只对缺的那些补跑一次**（补跑那次的提示词多一句 `_EXACT_TARGET_NOTE`：
  「target 逐字使用上面名单里的写法」；首次不加，避免无谓改动主路径提示词）。
- 补跑后仍缺 → `RelationshipBatchError(f"关系生成缺少人物：{'、'.join(still)}")` 点名是谁。
- 名单外的条目（写错名 / 多写的）在 `_merge` 里丢弃；`target` 原样保留（不规范化）。

**判据**：`U24`（漏 1 人 → 补回，且 `calls == [[全体], [缺的那个]]` —— 只多调 1 次、只补缺的）、
`U25`（补后仍缺 → `RelationshipBatchError`，`match="乙"` 点名）、`U26`（写错名算缺人 + 名单外条目
被丢弃）、`U28`（行为锁：`_relationships_batched` 每批经适配器的 `chat_stream_long` 而非 `chat`）。
变异 `MB9`（缺人不补跑）打红 `U24`/`U26`、`MB10`（补后仍缺只丢人不报错）打红 `U25`；`MA19`
的靶子从结构锁 `S9`（源码字面）改为行为锁 `U28` —— 原判据把调用换成非流式也照绿，正是
「变异打不红 = 判据不具分辨力」。均实测 RED。

### B3 状态列表顺序契约（2026-10-06，本步一个提交）

**缺陷**：投影把状态类列表拼成 `顶层 + 阶段 k`（全程在前）。但真正消费这些列表的 6 处读者都
**按 N 取前几条**（`[:3]` / `[:2]`）——`chat_engine` 软肋 / 雷点 `[:3]`、`context_engine` 对话示范
`[:3]`、`affinity_service` `values[:3]` / `inner_tensions[:2]`、`opening` 性格 `[:3]`。顶层条目一到
N，阶段 k 才成立的人设就被整段切掉 —— 与「选阶段 k 只看到阶段 k 那个人」直接冲突，且从成品看不出来。

**契约**（写进 `project_card` docstring，读者可依赖）：状态类列表 = **阶段 k 特有在前 + 全程在后**。
经历类列表仍是顶层在前 + 1..k（读者整体使用，不取前 N），不受影响。

**改法**（只动 `core/arc_view.py` 一行）：`set_path(proj, path, base + list(kth or []))`
→ `set_path(proj, path, list(kth or []) + base)`。规则仍在 `REGISTRY` 循环里通用执行，不写字段名分支。

**判据**：`U2`（改名 `test_state_list_is_phase_k_first_then_top`，断言 `["二", "全程"]`）、`U4`（嵌套
路径 `catchphrases` / `triggers` 顺序）、`test_arc_view::U18`（`dialogue_examples == ["d2", "dt"]`）；
读者侧新增 `tests/test_arc_phase_fields_readers.py::test_b3_*` 六条 —— 每处让顶层恰好 ≥N 条、阶段 k
加一条独有标记，断言标记仍进得了读者产出（先证对 `base + kth` 老顺序全红、改后全绿）。变异 `MA1`
（取 1..k 累加）/`MA2`（取全部阶段）/`MA3`（丢顶层）靶子与锚随之更新，均实测 RED。

### B4 出卡只有一处（2026-10-06，本步一个提交）

**缺陷**：`CharacterArc.selectable` 是 `@computed_field`，`model_dump_json()`（存库那刻）会把它算一遍
写进 `card_json`。它是**派生值**（起点齐全 + 有正文指纹），会过期：旧卡没这个键；位置由后台作业
补，补完那张卡存下的仍是 `false`。前端 `StartChatButton` 读 `data.character_arc?.selectable` 决定
是否渲染阶段选择框 —— 读到陈值 ⇒ 该能选阶段的卡不显示选择框。

**契约**：派生值**不落库**；出卡时按**当前** `phases` / 指纹现算。判定口径仍只有
`CharacterArc.has_positions()` 一处。

**改法**：① 模型去掉 `@computed_field` / `selectable`（`core/schema.py` 导入改回 `BaseModel,
model_validator`）；② 新增 `core/card_out.py::out_card(row)` —— 存储行 → 前端卡片负载的唯一一处，
只补 `character_arc.selectable` 这一个派生键、其余逐字透传，`card_json` 字符串 / dict 都收；
③ 两个开聊接口（`/cards/by-text/{id}`、`/cards/standalone`，即 `StartChatButton` 的两条取数路径）
返回 `[out_card(r) for r in …]`；市场、群聊不走开聊按钮，不接（Shiyu 已核实范围）。

**判据**：`E20` 六条（`tests/test_arc_phase_fields_readers.py::test_out_card_*`）—— 陈值 `false`→`true`、
陈值 `true`→`false`、缺键补齐、除 `selectable` 外逐字透传、`card_json` 为 dict 也收、无 `card_json`
原样返回；结构锁 `S15` —— `out_card` 仅 `core/card_out.py` 定义一次、只被两个接口调用、`schema.py`
无 `computed_field` / `def selectable`、`"selectable"` 字面只在 `card_out.py`。变异 `MB11`（出卡不重算、
退回透传存量值）靶 `test_out_card_recomputes_stale_false_to_true`，实测 RED、逐字节还原。受影响的既有
测试：`test_card_arc_behaviors.py`（三处 `model_dump()` 断言去掉 `"selectable"`）、
`test_arc_phase_fields_unit.py::U10`（改断言 `has_positions()`）。

### B5 字段名只登记一次 / B6 阶段内容不重复（2026-10-06，本步一个提交）

**缺陷**：同一件事有两份写法。
- **B5 字段名**：导出正文写死中文名（「性格：」「关键记忆：」…），登记表里没有名字 —— 新增字段要么改导出、要么漏；`ArcView.memories` 又按「顶层 + 1..k」另算了一遍投影卡上已有的记忆，同一份记忆存两处。
- **B6 阶段内容重复**：导出先 `project_card(card, None)`（把各阶段 overlay 并进顶层字段），再按阶段另起一行列出 overlay —— 阶段特有的记忆 / 性格**既在全程段又在阶段段**各出现一次；阶段表头的写法「阶段 i·label」在 export / context_engine / distiller / opening 四处各写一遍。

**契约**：字段中文名只有一份（登记表 `FieldSpec.label`）；阶段表头只有一份（`phase_header`）；阶段特有内容在导出里只出现一次、且落在**对应阶段的段落**里。

**改法**：① `FieldSpec` 加 `label`，37 条各配唯一中文名；② `arc_view` 加 `phase_header(index, label)`（表头唯一写法）与 `card_outline(card) -> (全程, 逐阶段)`（两层视图，按登记表遍历，行 = `(路径, 中文名, 文本)`）；③ 导出改用这两者，不再经 `project_card`；`context_engine` / `distiller` / `opening` 的阶段表头改调 `phase_header`；④ 删 `ArcView.memories`（记忆只在投影卡上算一次），同步改 `tests/test_arc_view.py::test_u3_memories_top_plus_upto_k`。

**判据**：`U29`（阶段记忆 / 性格各只出现一次且在对应段落）、`U30`（改登记表 `label` 即改导出）、`E7`/`E19` 两条行为测试（`test_market_reply_reads_projected_card` / `test_opening_variation_reads_projected_card`，替换 MA15 / MA23 原来只靠结构锁兜底）；结构锁 `S16`（label 唯一、`phase_header` 一处、`card_outline` 只由 export 用、`ArcView` 无 `memories`）。判别性由一次性脚本核实（导出把各阶段内容也塞进全程段 → U29 红；导出写死字段名 → U30 红；label 重复 → S16 红），三条均实测 RED、逐字节还原；脚本入库 `docs/specs/artifacts/arc_phase_fields_b5b6_mutations.py`（按 Shiyu 口径，步骤 7 的变异不入常设驱动）。受影响的既有测试：`test_arc_view.py::test_u3`、`test_arc_phase_select_locks.py::S12`（`has_positions` 调用方名单补 `card_out.py`，B4 的后果）。本步测试：`tests/test_arc_phase_fields_unit.py`（U21/U22）、`readers`（E7/E19）、`locks`（S16）全绿；受影响套件 185 + 272 条全绿。

### 本轮审计（B1–B7 收尾，2026-10-06）

**全绿证据（本机 `.venv`，现跑现数）**：§6 四份基线 —— `test_arc_phase_fields_unit.py` 40 passed
(3.29s) / `_readers.py` 23 passed (6.02s) / `_entries.py` 5 passed (0.46s) / `_locks.py` 19 passed
(6.68s)；本轮受影响套件（arc/phase + select + arc_view + card_arc_behaviors + arc_positions）
**185 passed (23.69s)**；另跑 distiller / context_engine / chat / card_draft / phase_anchoring 等
相关套件 **272 passed (49.78s)**。

**变异驱动**（`docs/specs/artifacts/arc_phase_fields_mutations.py`）：**37/37 条全红、逐字节还原**。
其中本轮新增的**跨模块 4 条**（Shiyu 口径）：`MB1–MB3`（步骤 1：分发只有 `dispatch` 一处、单值
格子容量由 `kind` 定）、`MB5–MB7`（步骤 3：分批前缀与关系口径各只写一处）、`MB9–MB10`（步骤 4：
缺人补跑一次、补不齐点名）、`MB11`（步骤 6：出卡 helper 现算）。`MA15` / `MA19` / `MA23` 的靶子
已从结构锁指回行为测试（`test_market_reply_reads_projected_card` /
`test_relationships_batched_goes_through_stream` / `test_opening_variation_reads_projected_card`）。
步骤 7 的三条（B5/B6）按口径不入常设驱动，另存
`docs/specs/artifacts/arc_phase_fields_b5b6_mutations.py`（3/3 全红）。

**B1–B7 对账（本表只做索引，细节在各自小节）**：

| 缺陷 | 步 | 修复 | 判据 | 变异 | 小节 |
|---|---|---|---|---|---|
| B1 分发两套（`dispatch` / `dispatch_single`） | 1 | 单值/列表同一套分发，格子容量由 `kind` 定 | U7/U8、S6（`by_phase[` 只在 `dispatch`） | MB1–MB3 | §10 |
| 效率 #1 位置核对上下文每字段各建一次 | 2 | `build_anchors` 只在 `card_from_draft` 开头建一次 | U20（次数=1） | MB4 | 效率 #1 实测 |
| B2 提示词 = 共享前缀 + 本步指令 | 3 | `book_prefix` / `format_prompt_shared`；分批前缀同源 | U22、S13/S14/S14b | MB5–MB7 | B2 提示词 / B7 |
| B7 关系生成口径五条只写一处 | 3 | 常量入 `relationship_batch`，`_batch_prompt` 引用它 | S13 | MB6 | B2 提示词 / B7 |
| B2 关系完整性（缺人静默丢） | 4 | 缺人补调一次、补不齐 `RelationshipBatchError` 点名 | U24/U25/U26/U28 | MB9–MB10 | B2 关系完整性 |
| B3 投影后状态列表顺序无关 | 5 | 契约：阶段 k 特有在前、全程在后 | U2 等、`test_b3_*` 六处 | MA1–MA3 | B3 状态列表顺序契约 |
| B4 出卡 `selectable` 陈值落库 | 6 | 派生值不落库，出卡 `out_card` 现算 | E20 六条、S15 | MB11 | B4 出卡只有一处 |
| B5 字段中文名两处/散落 | 7 | `FieldSpec.label` 唯一来源；删 `ArcView.memories` | U30、S16 | B5/B6 脚本 | B5 / B6 |
| B6 阶段内容导出重复 + 表头四处写 | 7 | `phase_header` + `card_outline` 两层视图 | U29、S16 | B5/B6 脚本 | B5 / B6 |

**扫描一：每条规则只许一处**（`core/`，2026-10-06）：

```
-- by_phase[ --
core/card_draft.py:216:    ... **`by_phase[` 只在这里出现**
core/card_draft.py:226:                by_phase[p - 1].append(i)
core/phase_anchoring.py:59:    return {p: by_phase[p][0] for p in phases if by_phase.get(p)}
-- out_card( --
core/card_out.py:22:def out_card(row) -> ...:
web/routers/distill.py:1290:    return [out_card(r) for r in result]
web/routers/distill.py:1301:    return [out_card(r) for r in rows]
-- phase_header( --
core/arc_view.py:100:def phase_header(index, label="") -> str:      # 定义唯一一处
core/context_engine.py:390 / core/distiller.py:448 / core/export.py:43 / core/opening.py:24
-- card_outline( --
core/arc_view.py:106:def card_outline(card):                        # 定义唯一一处
core/export.py:33:    lifelong, per_phase = card_outline(card)   # 只由 export 用
-- has_positions( --
定义 core/schema.py:175；调用 arc_view ×2 / card_out.py / distiller.py / text_manager.py（均在 S12 名单内）
```

**扫描二：拼 prompt 的入口只读投影卡**（`core/` + `web/`，2026-10-06）：

```
-- project_card( 调用 --
core/chat_engine.py:146:        self.card, self.arc_view = project_card(card, arc_phase)
core/opening.py:80:    proj, _view = project_card(card, arc_phase)
core/text_manager.py:529:                variation_prompt = build_variation_prompt(project_card(card, None)[0])
web/routers/distill.py:307:        prompt = build_awakening_prompt(project_card(card, None)[0])
web/routers/market.py:840:    char = project_card(char, None)[0]
-- require_projected( 守卫 --
core/context_engine.py:210 / core/opening.py:29,54,65
```

注：`core/export.py` 已不在 `project_card(` 名单里（B6 改走 `card_outline`，见偏离）。

**耗时数字**：效率 #1 的判别器计数 16 → 1、样本耗时中位 17.47 → 5.12 ms（原始表见「效率 #1
实测」）。本段新增测试的耗时并入上面各套件的总耗时（无单独计时）。

**本轮偏离索引**（详见「偏离与旁证」）：① 导出不再经 `project_card`（B6 的正确形态，§4.6 同步
改写）；② `mes_example` 仍从原卡 `speaking_style` 取（避免导出内部新造重复）；③ `_phase_labels`
保持裸名；④ 前端两张标签表合一 + 前后端名字一致的锁本轮不做（待办）；⑤ `MA8/MA8b/MA8c` 锚点
随 `FieldSpec` 加 `label` 同步更新；⑥ `S12` 名单补 `card_out.py`（B4 的后果）。

### 偏离与旁证（随步记录）

- **B2 组路径/档案路径的共享前缀**：spec 只点名了「一次读完路径拆 `book_prefix`」与「组路径
  用组共有段」。`distill_incremental`（档案路径，非组）的主调用系统提示本身没有正文段，故其
  分批前缀取 `format_prompt_shared(name)`（组共享段）—— 给出角色行与铁律、不含维度 F，且与
  组提示词同前缀。它不是该路径主调用提示的**字面前缀**（该路径主调用不是组调用），这是本段
  的一处解释性选择。
- **效率 #1 的实现形态**：spec 说「verify 改为收这个对象」。实际把 `verify(items, valid_rows,
  phases, source_text)` 改成 `verify(items, valid_rows, anchors)`（`anchors` 是新增的
  `PhaseAnchors` NamedTuple，含 `n / source_norm / ranges / reason`），`build_anchors(phases,
  source_text)` 是唯一的产地；`phase_ranges` 保持原签名（既有测试直接调它）。
- **B2 完整性：取消别名注入（Shiyu 更正）**：初版按 spec 原文给 `batch_relationships` 加了
  注入式 `canonical(name) -> 标准名`。核实后取消：`core/card_relationships.py` 里**没有**名字
  规范化函数（只有逐字去重的 `str(target)`），而真正的别名→标准名要在存储里读名单
  （`character_roster.aliases_for(chars, name)`），蒸馏入口拿不到。改为**逐字比对**、
  `target` 原样保留；「模型用别名回话」不再算在场，算缺人（补跑提示里点明逐字照抄名单）。
- **变异锚点维护（同段内必然）**：本步改写了 `_relationships_batched` 与 `_batch_prompt` 的
  形态，牵动两处既有变异锚：`MB7` 的靶子名（`test_s14b_shared_prefix_producers_defined_once`
  → `_is_a_real_shared_prefix`，上一步改名时漏改，本步一并修正）、`MA20` 的前缀锚（`_run`
  多出的 `exact=exact` 实参）。另 `MA18` 的锚（`for i, fut in enumerate(futures):`，8 空格）
  经 `_fan_out` 后仍在同一缩进、恰一命中，未改。
- **B3 顺序契约：两处既有测试改名（同段内必然）**：顺序翻转后 `test_state_list_is_top_plus_phase_k`
  （状态）与 `test_u18_dialogues_top_plus_phase_k`（对白）两个名字已与事实相反，随内容一并改为
  `..._phase_k_first_then_top`；`MA1`/`MA2`/`MA3` 的靶子名与载荷锚同步更新。这两处改名属本步改动
  的直接后果，不另计偏离。
- **B4 范围边界（Shiyu 已核实，本段按此执行）**：`selectable` 现算只接在**两个开聊接口**上
  （`/cards/by-text/{id}`、`/cards/standalone`）。这意味着前端**必须重新取数**才看得到纠正后的值：
  若列表对象在位置补写之前取过一次并留在 store 里，要等下次拉列表才更新。这是本次点名的范围
  （市场、群聊不走开聊按钮，不接），非本段引入的新问题 —— 旧实现里该值更糟（落库那刻就固化，
  永不更新）。不另计偏离。
- **B4 去 `computed_field` 属更正而非偏离**：spec §4.1 原写「`selectable`（`computed_field`）」，
  正是缺陷形态（派生值随序列化落库）。本步把 §4.1 一并改写，规则从「模型字段」挪到「出卡现算」，
  口径仍只有 `has_positions` 一处。
- **B5/B6：导出不再经 `project_card`（本步偏离 spec §4.6 原文）**：spec 步骤 7 原文写「导出改用这两个（视图函数）」，`§4.6` 旧文写「导出 `project_card(card, None)`」。旧形态正是 B6 缺陷源（投影把各阶段 overlay 并进顶层 ⇒ 阶段内容重复）。本步把 `§4.6` 一并改写为「导出用 `card_outline` 两层视图」，`export.py` 里 `project_card` 的导入与调用一并删除。这是**按缺陷形态取的正确形态**，非范围扩大；§4.6 已同步。
- **B5/B6：`mes_example`（说话风格）仍从原卡 `speaking_style` 取（本步解释性选择）**：`card_outline` 的全程/逐阶段行会在导出正文里渲染，若把 `speaking_style.*` 也纳入，说话风格会**既进正文又进 `mes_example`**，等于在导出内部新造一处 B6 形态的重复。故 `body()` 过滤 `speaking_style.` 前缀，`mes_example` 仍单列、但字段名改取 `_label(...)`（B5）。`speaking_style.*` 的中文名因此只在 `mes_example` 出现一次。
- **B5/B6：`_phase_labels`（`distiller.py`）保持裸名（本步解释性选择）**：它给关系分批的提示词列出「阶段 1 / 阶段 2 …」的**裸名列表**（供模型按编号指认），与「阶段 i·label」表头是两种用途；未改用 `phase_header`。结构锁 `S16` 只要求四个**产表头**的模块经 `phase_header`，`_phase_labels` 不产表头，不在其列。
- **B5/B6：前端两张标签表合一 + 前后端名字一致的锁 —— 本轮不做（Shiyu 定，记 §10 待办）**：spec 步骤 7 原文要求「前端两张标签表合成 `utils/card.js` 一张；加一个测试，核对它的键和名字与登记表一致」。Shiyu 明确本轮不做，只做后端三件（FieldSpec 加 label、导出用视图函数、删 `ArcView.memories`）。**待办**：前端 `utils/card.js` 合并两张标签表，并加一条前端测试断言其键与后端登记表一致（跨语言，可用一份 JSON 导出的登记表快照做对照）。
- **B5 连带：变异锚点 `MA8/MA8b/MA8c` 随 `FieldSpec` 加 `label` 同步更新（同段内必然）**：
  登记表每行多了第三个实参，三条锚的 `old` 字面（`FieldSpec("state", "list")` 等）命中数变 0，
  `_apply` 当场 assert。这是本步改 `FieldSpec` 的直接后果，按新字面更新锚点，不另计偏离。
- **B4 连带：`S12`（`test_arc_phase_select_locks.py`）`has_positions` 调用方名单补 `card_out.py`**：
  步骤 6 的出卡 helper 是 `has_positions` 的新调用方，名单不补即红。同段内必然，不另计偏离。
- **本轮收尾：`MA15/MA19/MA23` 靶子改指行为测试（Shiyu 口径）**：三条原以结构锁（S3/S9/S12）
  为靶；行为面测试就位后改指 `test_market_reply_reads_projected_card` /
  `test_relationships_batched_goes_through_stream` / `test_opening_variation_reads_projected_card`，
  驱动 37/37 全红、逐字节还原。
- **旁证（非本段改动面）**：注册变异驱动 `tests/perf/arc_phase_anchoring_mutations.py` 有三条
  锚点已在更早的分支提交上失真（M17 锚 `row.occurrences`、M30 锚 `occurrences: list[...] = []`
  计数 3、M35 锚旧版 warning 文案），`_apply` 的「锚点恰一命中」会当场 assert，该驱动**跑不完**。
  这不是本段引入的（`881fb6bf` 时已如此），也不在 CI 路径上（CI 只跑 pytest，`test_lock_coverage`
  读产物、产物记的是 `test_phase_anchoring.py` 的判别器身份，未受影响、仍绿）。处置需拍板：修锚点
  要重跑该驱动（需测试 PG），本段先不动，报告里点出。

## 11. 自检表（逐条对照 Shiyu 的 spec 标准）
| 标准 | 落点 | 状态 |
|---|---|---|
| 事实现读、带坐标、S0 复核 | §2 C1–C27（`15b0b7c2`）；C23 已按官方页面更正，见 §10 | ✅ |
| 设计问题先给方案、拍板后写 | DA12–DA17 均经 Shiyu 拍板；按「先达成目的、再取最简单」 | ✅ |
| 测试写法（受影响文件、Docker PG、合并只做 git） | §9 | ✅ |
| 交付 .md | 本文件 | ✅ |
| 审计逐文件 | 审计时执行 | — |
| 新发现不记账 | §10 | ✅ |
| 路径既有机制逐个核对前提 | §2.1 | ✅ |
| 真实规模算一遍 | §2.3（tokenizer 实测 + 官方价目）；耗时未实测已注明 | ✅ |
| 样本前提写明范围 | C13、C24 | ✅ |
| 通道 × 执行上下文 × 测试 | §2.2 | ✅ |
| 出处表（链接 + 章节） | §2.4 | ✅ |
| 全量扫描原始输出 | 附录 A–D | ✅ |
| 规模表（含无上限数据源的策略） | §2.3（关系：分批） | ✅ |
| 调用点矩阵 | §6.2（E1–E17） | ✅ |
| 变异发出前预跑 | 15 条全红；其余 11 条依赖实现，审计时亲手跑 | ⚠️ 部分 |
| 先找现成库 | pydash（3 个库实测对比，DA12） | ✅ |
| 原生控件 | 不涉及 | — |
| 行为清单照抄权威来源 | pydantic / DeepSeek 官方文档链接 | ✅ |
| 能自动验的不手动 | Playwright + 截图只看排版 | ✅ |
| 交接可核对 | S0 Test-Path + hash | ✅ |
| 环境冲突 | C15 | ✅ |
| 功能隔离、不泄露 | DA18 类型隔离（原卡进不了角色扮演 prompt）；DA19 模块职责与导入锁；关系分批、开场白独立成模块；U18、S3、S11、S12 | ✅ |
| 不打补丁 | 默认拒绝的登记表；修 C8/C11/C12/C14；关系分批取代可能截断的单次输出 | ✅ |
| 与调研结论一致 | §2.4 | ✅ |

## 附录 A：字段读者扫描（后端，`15b0b7c2`，原始输出）

```
== personality_traits
core/card_quotes.py:28:    "personality_traits[]",
core/context_engine.py:353:        traits = "、".join(c.personality_traits)
core/export.py:23:    if card.personality_traits:
core/export.py:24:        personality_lines.append("性格：" + "；".join(card.personality_traits))
core/moderation/auto_review.py:133:    for key in ("personality_traits", "values", "inner_tensions", "speaking_style"):
core/moderation/auto_review.py:144:        if k not in ("name", "identity", "background", "personality", "personality_traits", "values", "inner_tensions", "speaking_style"):
web/routers/distill.py:1436:        traits = "，".join(proj_card.personality_traits[:3])
web/routers/market.py:840:    traits = "\n".join(f"- {t}" for t in (char.personality_traits or []))
== \.values
core/context_engine.py:354:        values = "、".join(c.values)
core/export.py:25:    if card.values:
core/export.py:26:        personality_lines.append("价值观：" + "；".join(card.values))
web/deps.py:164:    for session in _sessions.values():
web/deps.py:168:    for group in _group_sessions.values():
web/deps.py:170:            for engine in group.engines.values():
web/deps.py:172:    return list(seen.values())
web/deps.py:441:    for sess in list(sessions.values()):
web/routers/admin.py:1030:    timestamps = [s.get("last_active") for s in sessions.values() if s.get("last_active")]
web/routers/distill.py:284:        for task in _tasks.values():
== inner_tensions
core/affinity_service.py:171:        _tensions = getattr(card, 'inner_tensions', []) or []
core/card_quotes.py:31:    "inner_tensions[]",
core/context_engine.py:355:        tensions = "、".join(c.inner_tensions)
core/export.py:33:    if card.inner_tensions:
core/export.py:34:        personality_lines.append("内在矛盾：" + "；".join(card.inner_tensions))
core/moderation/auto_review.py:133:    for key in ("personality_traits", "values", "inner_tensions", "speaking_style"):
core/moderation/auto_review.py:144:        if k not in ("name", "identity", "background", "personality", "personality_traits", "values", "inner_tensions", "speaking_style"):
== emotional_patterns
core/card_quotes.py:32:    "emotional_patterns[]",
core/context_engine.py:418:        if c.emotional_patterns:
core/context_engine.py:419:            emo = "；".join(c.emotional_patterns)
== decision_style
core/card_quotes.py:33:    "decision_style",
core/context_engine.py:426:        if c.decision_style:
core/context_engine.py:427:            parts.append(f"【决策方式】\n{c.decision_style}")
core/moderation/card_guard.py:25:config fields* (decision_style / speaking_style / values) carrying precedence
core/text_manager.py:110:    # field landings (decision_style/speaking_style/values + precedence wording).
== speaking_style
core/card_quotes.py:36:    "speaking_style.sentence_pattern",
core/card_quotes.py:47:    "speaking_style.catchphrases[]",
core/context_engine.py:364:        catch = "、".join(c.speaking_style.catchphrases)
core/context_engine.py:365:        taboo = "、".join(c.speaking_style.taboo_words)
core/context_engine.py:368:            f"语气：{c.speaking_style.tone}　"
core/context_engine.py:369:            f"句式：{c.speaking_style.sentence_pattern}　"
core/context_engine.py:370:            f"用词：{c.speaking_style.vocabulary_level}\n"
core/export.py:20:    style = card.speaking_style
core/moderation/auto_review.py:133:    for key in ("personality_traits", "values", "inner_tensions", "speaking_style"):
core/moderation/auto_review.py:144:        if k not in ("name", "identity", "background", "personality", "personality_traits", "values", "inner_tensions", "speaking_style"):
core/moderation/card_guard.py:11:``speaking_style`` / ``vocabulary_level`` extraction this project is built
core/moderation/card_guard.py:25:config fields* (decision_style / speaking_style / values) carrying precedence
== triggers
core/affinity_service.py:179:            or bool(psyche.triggers)
core/affinity_service.py:189:            if psyche.triggers:
core/affinity_service.py:190:                baseline_rules += f"- 以下是你的雷点，被触碰会明显掉好感/防御飙升：{', '.join(psyche.triggers)}\n"
core/affinity_service.py:260:            "- trigger_hit：本轮用户言行是否戳中了你的雷点（pshche.triggers），让你感到被冒犯、不被尊重或触及底线\n"
core/chat_engine.py:809:        # 条件 2：雷点命中（排除剧情内冲突），仅当角色有 triggers 定义
core/chat_engine.py:811:        has_triggers = bool(getattr(psyche, "triggers", None) if psyche else False)
core/chat_engine.py:813:        condition_trigger = has_trigger_hits and has_triggers
core/chat_engine.py:906:            triggers = getattr(psyche, "triggers", [])
core/chat_engine.py:933:            if triggers:
core/chat_engine.py:935:                    f"你的雷点：被触到「{'、'.join(triggers[:3])}」这些时会翻脸或防御飙升。"
== soft_spots
core/affinity_service.py:180:            or bool(psyche.soft_spots)
core/affinity_service.py:191:            if psyche.soft_spots:
core/affinity_service.py:192:                baseline_rules += f"- 以下是你的软肋，被戳中会让你心软、好感回升更快：{', '.join(psyche.soft_spots)}\n"
core/affinity_service.py:266:            "- 简单的「对不起」算 apology；详细解释原因算 explanation；用行动表示改变算 action；戳中 psyche.soft_spots 的内容算 soft_spot\n\n"
core/card_quotes.py:34:    "psyche.soft_spots[]",
core/chat_engine.py:907:            soft_spots = getattr(psyche, "soft_spots", [])
core/chat_engine.py:929:            if soft_spots:
core/chat_engine.py:931:                    f"你的软肋：被戳中「{'、'.join(soft_spots[:3])}」这些点时会心软，短暂破防。"
== knowledge_scope
core/chat_engine.py:1006:        if not cog or not cog.knowledge_scope:
core/chat_engine.py:1011:            f"你的知识范围：{cog.knowledge_scope}"
== speech_style
core/card_quotes.py:35:    "cognitive.speech_style",
core/chat_engine.py:1013:            f"你的说话方式：{cog.speech_style}，"
== \.note
core/chat_engine.py:1045:            note = (rel.note or "").strip()
web/routers/admin.py:930:    note = (req.note or f"管理员 {admin.get('username', '')} 复核通过").strip()
== identity
core/chat_engine.py:1371:            f"你是「{self.card.name}」，性格：{self.card.identity}\n"
core/chat_engine.py:1404:            f"你是「{self.card.name}」，性格：{self.card.identity}\n"
core/context_engine.py:348:            f"身份：{c.identity}\n"
core/context_engine.py:574:            f"角色身份：{self.card.identity}\n"
core/export.py:37:    description_lines: list[str] = [card.identity]
core/group_session.py:68:        """Build user identity context to inject into each AI character's system prompt."""
core/moderation/auto_review.py:129:    for key in ("name", "identity", "background", "personality"):
core/moderation/auto_review.py:144:        if k not in ("name", "identity", "background", "personality", "personality_traits", "values", "inner_tensions", "speaking_style"):
core/text_manager.py:740:def session_identity(db_row: dict | None) -> dict[str, Any]:
web/account_visibility.py:79:def identity(account: dict, view: AccountView) -> dict:
web/demo_gate.py:121:         - 「公开」（`request.state.identity_optional`，中间件给的**事实**）：中间件
web/demo_gate.py:141:    if getattr(request.state, "identity_optional", False) and not _route_reads_credentials(route):
```

## 附录 B：`CharacterCard` 叶子字段（原始输出）

```
$ python3 -c "<枚举 CharacterCard 叶子字段：递归 model_fields，遇 BaseModel 子模型展开>"
name | <class 'str'>
identity | <class 'str'>
personality_traits | list[str]
speaking_style.tone | <class 'str'>
speaking_style.sentence_pattern | <class 'str'>
speaking_style.catchphrases | list[str]
speaking_style.vocabulary_level | <class 'str'>
speaking_style.taboo_words | list[str]
values | list[str]
key_memories | list[str]
relationships | list[core.schema.Relationship]
inner_tensions | list[str]
background | <class 'str'>
first_message | <class 'str'>
dialogue_examples | list[str]
emotional_patterns | list[str]
decision_style | <class 'str'>
character_arc.axis | <class 'str'>
character_arc.phases | list[core.schema.ArcPhase]
character_arc.source_fingerprint | <class 'str'>
situation_behaviors | list[core.schema.SituationBehavior]
tags | list[str]
psyche.openness | <class 'int'>
psyche.conscientiousness | <class 'int'>
psyche.extraversion | <class 'int'>
psyche.agreeableness | <class 'int'>
psyche.neuroticism | <class 'int'>
psyche.affinity_baseline | <class 'int'>
psyche.volatility | <class 'str'>
psyche.grudge_inertia | <class 'str'>
psyche.triggers | list[str]
psyche.soft_spots | list[str]
cognitive.education_level | <class 'str'>
cognitive.knowledge_scope | <class 'str'>
cognitive.speech_style | <class 'str'>
cognitive.vocabulary_level | <class 'str'>
awakening_message | <class 'str'>
```

## 附录 C：①阶段记忆 / 对白示例读写点

```
$ git grep -n -E "\.memories\b|phases\[\]\.memories|dialogue_examples" -- core web/routers web/frontend/src ':!*__tests__*'
core/arc_view.py:74:    memories = list(card.key_memories) + [m for p in phases[:k] for m in p.memories]
core/arc_view.py:118:    proj.key_memories = list(view.memories)
core/arc_view.py:119:    proj.dialogue_examples = list(card.dialogue_examples) + [
core/arc_view.py:120:        d for p in card.character_arc.phases[:k] for d in p.dialogue_examples
core/card_quotes.py:41:    "character_arc.phases[].memories[]",
core/context_engine.py:422:        if c.dialogue_examples:
core/context_engine.py:423:            exs = "\n---\n".join(c.dialogue_examples[:3])
core/distiller.py:1687:        `attach_dialogue_examples` 再调一次 —— 同一个纯函数、同样的输入，结果必然相同，
core/distiller.py:1706:    def pick_dialogue_examples(
core/distiller.py:1712:        """挑出的示例文本（对外形态，原样保留）。位置归阶段由 `attach_dialogue_examples` 用
core/distiller.py:1713:        `_pick_dialogue_examples` 的起点做，本方法只是 `list[str]` 那个旧口径。"""
core/distiller.py:1714:        return [text for text, _ in self._pick_dialogue_examples(candidates, name, others)]
core/distiller.py:1716:    def _pick_dialogue_examples(
core/distiller.py:1755:            "name": "pick_dialogue_examples",
core/distiller.py:1792:    def attach_dialogue_examples(
core/distiller.py:1803:        就把 `pick_dialogue_examples` 的 `DistillError` 抛出去，由调用方按任务失败处理。
core/distiller.py:1820:        picked = self._pick_dialogue_examples(candidates, name, others)
core/distiller.py:1824:            card_dict["dialogue_examples"] = []
core/distiller.py:1827:                    phase_of(card, norm_pos) - 1]["dialogue_examples"].append(text)
core/distiller.py:1829:            card_dict["dialogue_examples"] = [text for text, _ in picked]
core/distiller.py:1846:        （`attach_dialogue_examples`）。失败口径不变：挑选失败照旧抛 `DistillError` 交给
core/distiller.py:1854:        return self.attach_dialogue_examples(card, content, name, aliases, roster)
core/export.py:30:        if phase.memories:                       # 只在那阶段成立的记忆，别从导出里消失
core/export.py:32:            personality_lines.append(f"{head}记忆：" + "；".join(phase.memories))
core/schema.py:123:    dialogue_examples: list[str] = []         # 属于这一阶段的原文对话示例
core/schema.py:155:    dialogue_examples: list[str] = []   # 2-3轮原文对话示例，体现角色说话风格
core/schema.py:187:POST_FORMAT_FIELDS: tuple[str, ...] = ("tags", "awakening_message", "dialogue_examples")
web/frontend/src/components/ChatArea.jsx:416:      setMemories(data.memories || [])
web/frontend/src/components/EditCardModal.jsx:30:  dialogue_examples: { max: 600 },
web/frontend/src/components/EditCardModal.jsx:82:      dialogue_examples: Array.isArray(data.dialogue_examples)
web/frontend/src/components/EditCardModal.jsx:83:        ? data.dialogue_examples.join('\n\n')
web/frontend/src/components/EditCardModal.jsx:84:        : (data.dialogue_examples || ''),
web/frontend/src/components/EditCardModal.jsx:160:      dialogue_examples: joinLines(form.dialogue_examples.replace(/\n\n+/g, '\n\n')),
web/frontend/src/components/EditCardModal.jsx:262:          <Field label="对话示例（每组间空行分隔）" mono field="dialogue_examples" value={form.dialogue
web/frontend/src/components/EditCardModal.jsx:263:            <textarea className="modal-textarea" rows={4} value={form.dialogue_examples} o
web/frontend/src/components/common/ArcList.jsx:21:              {p.memories?.length > 0 && (
web/frontend/src/components/common/ArcList.jsx:23:                  {p.memories.map((m, j) => <li key={j} className="card-arc-memory">{m}</l
```

## 附录 D：前端字段读者扫描（原始输出）

```
$ git grep -n -E "personality_traits|emotional_patterns|decision_style|inner_tensions|catchphrases|soft_spots|triggers|knowledge_scope|speech_style|\.values|\.note" -- web/frontend/src ":!*__tests__*"
web/frontend/src/components/CharCard.jsx:760:        {data.personality_traits?.length > 0 && (
web/frontend/src/components/CharCard.jsx:762:            <TraitList items={data.personality_traits} />
web/frontend/src/components/CharCard.jsx:774:            {style.catchphrases?.length > 0 && (
web/frontend/src/components/CharCard.jsx:775:              <div className="card-catchphrases">
web/frontend/src/components/CharCard.jsx:776:                {style.catchphrases.map((c, i) => (
web/frontend/src/components/CharCard.jsx:787:        {data.values?.length > 0 && (
web/frontend/src/components/CharCard.jsx:790:              {data.values.map((v, i) => (
web/frontend/src/components/CharCard.jsx:824:        {data.inner_tensions?.length > 0 && (
web/frontend/src/components/CharCard.jsx:827:              {data.inner_tensions.map((t, i) => (
web/frontend/src/components/EditCardModal.jsx:16:  personality_traits: { max: 600, maxLines: 8, perLine: 100 },
web/frontend/src/components/EditCardModal.jsx:19:  catchphrases: { max: 240, maxLines: 6, perLine: 40 },
web/frontend/src/components/EditCardModal.jsx:24:  inner_tensions: { max: 500, maxLines: 5, perLine: 100 },
web/frontend/src/components/EditCardModal.jsx:27:  emotional_patterns: { max: 550, maxLines: 6, perLine: 70 },
web/frontend/src/components/EditCardModal.jsx:28:  decision_style: { max: 180 },
web/frontend/src/components/EditCardModal.jsx:68:      personality_traits: splitLines(data.personality_traits),
web/frontend/src/components/EditCardModal.jsx:71:      catchphrases: splitLines(style.catchphrases),
web/frontend/src/components/EditCardModal.jsx:74:      values: splitLines(data.values),
web/frontend/src/components/EditCardModal.jsx:76:      inner_tensions: splitLines(data.inner_tensions),
web/frontend/src/components/EditCardModal.jsx:79:      emotional_patterns: splitLines(data.emotional_patterns),
web/frontend/src/components/EditCardModal.jsx:80:      decision_style: data.decision_style || '',
web/frontend/src/components/EditCardModal.jsx:95:    personality_traits: '性格特征',
web/frontend/src/components/EditCardModal.jsx:96:    catchphrases: '口癖',
web/frontend/src/components/EditCardModal.jsx:100:    inner_tensions: '内在矛盾',
web/frontend/src/components/EditCardModal.jsx:101:    emotional_patterns: '情感模式',
web/frontend/src/components/EditCardModal.jsx:106:      { field: 'personality_traits', lines: form.personality_traits.split('\n').filter(Boolean) },
web/frontend/src/components/EditCardModal.jsx:107:      { field: 'catchphrases', lines: form.catchphrases.split('\n').filter(Boolean) },
web/frontend/src/components/EditCardModal.jsx:109:      { field: 'values', lines: form.values.split('\n').filter(Boolean) },
web/frontend/src/components/EditCardModal.jsx:111:      { field: 'inner_tensions', lines: form.inner_tensions.split('\n').filter(Boolean) },
web/frontend/src/components/EditCardModal.jsx:112:      { field: 'emotional_patterns', lines: form.emotional_patterns.split('\n').filter(Boolean) },
web/frontend/src/components/EditCardModal.jsx:137:      personality_traits: joinLines(form.personality_traits),
web/frontend/src/components/EditCardModal.jsx:142:        catchphrases: joinLines(form.catchphrases),
web/frontend/src/components/EditCardModal.jsx:146:      values: joinLines(form.values),
web/frontend/src/components/EditCardModal.jsx:148:      inner_tensions: joinLines(form.inner_tensions),
web/frontend/src/components/EditCardModal.jsx:151:      emotional_patterns: joinLines(form.emotional_patterns),
web/frontend/src/components/EditCardModal.jsx:152:      decision_style: form.decision_style,
web/frontend/src/components/EditCardModal.jsx:193:          <Field label="性格特征（每行一条）" mono field="personality_traits" value={form.personality_traits}>
web/frontend/src/components/EditCardModal.jsx:194:            <textarea className="modal-textarea" rows={3} value={form.personality_traits} onChange={(e) => update('personality_traits', e.target.value)} maxLength={LIMITS.personality_traits.max} />
web/frontend/src/components/EditCardModal.jsx:205:          <Field label="口癖（每行一条）" mono field="catchphrases" value={form.catchphrases}>
web/frontend/src/components/EditCardModal.jsx:206:            <textarea className="modal-textarea" rows={2} value={form.catchphrases} onChange={(e) => update('catchphrases', e.target.value)} maxLength={LIMITS.catchphrases.max} />
web/frontend/src/components/EditCardModal.jsx:217:          <Field label="核心价值观（每行一条）" mono field="values" value={form.values}>
web/frontend/src/components/EditCardModal.jsx:218:            <textarea className="modal-textarea" rows={2} value={form.values} onChange={(e) => update('values', e.target.value)} maxLength={LIMITS.values.max} />
web/frontend/src/components/EditCardModal.jsx:225:          <Field label="内在矛盾（每行一条）" mono field="inner_tensions" value={form.inner_tensions}>
web/frontend/src/components/EditCardModal.jsx:226:            <textarea className="modal-textarea" rows={2} value={form.inner_tensions} onChange={(e) => update('inner_tensions', e.target.value)} maxLength={LIMITS.inner_tensions.max} />
web/frontend/src/components/EditCardModal.jsx:237:          <Field label="情感模式（每行一条）" mono field="emotional_patterns" value={form.emotional_patterns}>
web/frontend/src/components/EditCardModal.jsx:238:            <textarea className="modal-textarea" rows={2} value={form.emotional_patterns} onChange={(e) => update('emotional_patterns', e.target.value)} maxLength={LIMITS.emotional_patterns.max} />
web/frontend/src/components/EditCardModal.jsx:241:          <Field label="决策风格" mono field="decision_style" value={form.decision_style}>
web/frontend/src/components/EditCardModal.jsx:242:            <textarea className="modal-textarea" rows={2} value={form.decision_style} onChange={(e) => update('decision_style', e.target.value)} maxLength={LIMITS.decision_style.max} />
web/frontend/src/components/GroupChatPage.jsx:320:        const flat = Object.values(grouped).flat()
web/frontend/src/components/GroupChatPage.jsx:554:    const flat = Object.values(grouped).flat()
web/frontend/src/components/GroupChatPage.jsx:1187:                                      || Object.values(cardCache).find(c => (c.name || '').toLowerCase() === name)
web/frontend/src/components/GroupChatPage.jsx:1198:                                      personality_traits: parsed.personality_traits || [],
web/frontend/src/components/GroupChatPage.jsx:1207:                                      personality_traits: [],
web/frontend/src/components/GroupChatPage.jsx:1299:                        {selectedCharCardInfo.personality_traits?.length > 0 && (
web/frontend/src/components/GroupChatPage.jsx:1303:                              {selectedCharCardInfo.personality_traits.slice(0, 3).map((t, i) => (
web/frontend/src/components/MarketCardDetail.jsx:677:              {cardData.personality_traits?.length > 0 && (
web/frontend/src/components/MarketCardDetail.jsx:680:                  <TraitList items={cardData.personality_traits} />
web/frontend/src/components/MarketCardDetail.jsx:684:              {cardData.values?.length > 0 && (
web/frontend/src/components/MarketCardDetail.jsx:688:                    {cardData.values.map((v, i) => (
web/frontend/src/components/MarketCardDetail.jsx:703:                  {cardStyle.catchphrases?.length > 0 && (
web/frontend/src/components/MarketCardDetail.jsx:704:                    <div className="card-catchphrases">
web/frontend/src/components/MarketCardDetail.jsx:705:                      {cardStyle.catchphrases.map((c, i) => (
web/frontend/src/components/MarketCardDetail.jsx:739:              {cardData.inner_tensions?.length > 0 && (
web/frontend/src/components/MarketCardDetail.jsx:743:                    {cardData.inner_tensions.map((t, i) => (
web/frontend/src/styles/global.css:3806:.card-catchphrases {
web/frontend/src/utils/mergeMessages.js:13:  return [...byId.values()].sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
```

## 附录 E：读原卡的全部地方（原始输出）

```
$ git grep -n -E "CharacterCard\.model_validate|CharacterCard\(|: CharacterCard|-> CharacterCard" -- core web ":!*test*"
core/arc_view.py:38:def valid_phase(card: CharacterCard, arc_phase: int | None) -> int | None:
core/arc_view.py:49:def _normalize_k(card: CharacterCard, arc_phase: int | None) -> int:
core/arc_view.py:55:def has_positions(card: CharacterCard) -> bool:
core/arc_view.py:69:def arc_view(card: CharacterCard, arc_phase: int | None) -> ArcView:
core/arc_view.py:107:def project_card(card: CharacterCard, arc_phase: int | None) -> tuple[CharacterCard, ArcView]:
core/arc_view.py:126:def phase_of(card: CharacterCard, pos: int) -> int:
core/card_draft.py:184:def card_from_draft(data: Any, source_text: str) -> CharacterCard:
core/card_draft.py:238:    return CharacterCard.model_validate(card)
core/card_quotes.py:129:def retract_unverified(card: CharacterCard, content: str) -> tuple[CharacterCard, list[dict]]:
core/card_quotes.py:155:    return CharacterCard.model_validate(dump), retracted
core/card_relationships.py:24:def dedupe_relationship_targets(card: CharacterCard) -> tuple[CharacterCard, list[dict]]:
core/card_relationships.py:51:    return CharacterCard.model_validate(dump), dropped
core/chat_engine.py:112:        card: CharacterCard,
core/chat_engine.py:1146:        card: CharacterCard,
core/context_engine.py:199:        card: CharacterCard,
core/distiller.py:1511:    def distill(self, text: str, character_name: str) -> CharacterCard:
core/distiller.py:1632:    def _distill_longcontext(self, text: str, character_name: str) -> CharacterCard:
core/distiller.py:1794:        card: CharacterCard,
core/distiller.py:1799:    ) -> CharacterCard:
core/distiller.py:1830:        return CharacterCard.model_validate(card_dict)
core/distiller.py:1834:        card: CharacterCard,
core/distiller.py:1839:    ) -> CharacterCard:
core/distiller.py:2120:    ) -> CharacterCard:
core/distiller.py:2281:                card = CharacterCard.model_validate(card_dict)
core/export.py:10:def to_tavern_json(card: CharacterCard, first_message: str = "") -> dict:
core/export.py:80:def export_tavern_json(card: CharacterCard, first_message: str = "") -> str:
core/schema.py:143:class CharacterCard(BaseModel):
core/text_manager.py:112:    async def _guard_card(self, card: CharacterCard) -> GuardVerdict:
core/text_manager.py:449:        card: CharacterCard | None = None
core/text_manager.py:456:                        card = CharacterCard.model_validate_json(c["card_json"])
core/text_manager.py:581:        self, text_id: str, card: CharacterCard, user_id: str,
core/text_manager.py:681:        card: CharacterCard,
web/routers/chat.py:175:    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/distill.py:291:def _generate_awakening(llm, card: CharacterCard, storage=None) -> str:
web/routers/distill.py:525:                card = CharacterCard.model_validate(card_dict)
web/routers/distill.py:1255:        validated = CharacterCard.model_validate(req.card_json)
web/routers/distill.py:1306:    card = CharacterCard.model_validate_json(record["card_json"])
web/routers/distill.py:1362:    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/group.py:144:                    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/group.py:159:            card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/group.py:359:        card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/history.py:207:    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/market.py:837:    char = CharacterCard.model_validate(_json.loads(card_json_str))
```

### 第二轮审计（2026-10-06，Claude；R1、R2 由 Claude 修）

**目标检查（先跑）**：一张 3 阶段卡，每个 state / experience 路径在顶层与各阶段放可辨认标记，k=1..3 用真实代码拼 `card_core` + `card_ext` + 开场白。修前：prompt 层 0 违规；投影卡数据层有后期关系态度（k<n）与前几阶段 overlay（k=n）。修后：两层均 0 违规。

| 编号 | 问题 | 根因 | 修法 |
|---|---|---|---|
| R1 | 关系分批的 user 消息沿用主调用的「生成角色卡 / 输出角色卡」，与「只输出关系数组」冲突；一次读完路径的 system 里没写主角 | 与 B2 同根：本步指令有一半从调用方继承（B2 只修了 system 那一半） | `_batch_prompt` 产出 `(system, user)`，本步指令全归 `relationship_batch`；`_relationships_batched` 不再收 `messages`，改收 `character_name` 与 `material`（分组路径的分析档案进本步 user；一次读完路径为空，正文已在前缀） |
| R2a | 投影卡里留着全部阶段的 `phase_attitudes`；k=n 时阶段表留着各阶段 overlay | 投影卡「只装阶段 k 能看到的」没在数据层兑现 | `phase_attitudes` 截到 ≤k；阶段表一律只留 1..k 的 label / state（k=n 同） |
| R2b | 阶段口径为空时回落顶层（全书）口径；生成规则没要求第一条写在开始有交集的阶段；顶层 attitude / note 写什么没有定义 | 投影依赖的两条生成约定没写进规则；顶层那句没有口径，模型多半写成全书（即后期）立场 | 口径只取 ≤k 最近一条非空，不回落顶层；`RELATIONSHIP_RULES` 补第 3 条前半句与第 6 条（投影依赖的两条约定）、第 7 条（顶层写最初的关系，让卡片页、导出这些不经投影的地方也不带后期立场；投影仍不读它） |

新测试：`test_r1_*`（2 条）、`test_r2a_*`（2 条）、`test_r2b_*`（2 条）。变异驱动新增 MC1–MC6（6 条新测试各有一条），并跟进 MA11 / MA19 / MA20 / MB5 锚点：43/43 全红、逐字节还原。

复核跟进（执行方复核 a4f2a5a9 提出）：MC6 补上；`arc_view._project_relationships` 的说明与规则第 7 条对齐；S13 锁的「五条规则」措辞更新；分批调用点实为 5 处（含 SSE 侧 `fill_relationships`），均已核对。

**待办（低优先级，未修）**：R3 `out_card` 逐行校验弧线，一张坏卡会让整个列表 500；R4 导出丢了阶段特有的说话风格（阶段段落过滤 `speaking_style.*`，说话风格节只取顶层）。
