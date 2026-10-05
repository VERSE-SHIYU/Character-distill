# 第三步 ①：开聊时选阶段（spec v5）

基线：main `7d1afe12`（2026-10-05）。分支：`feat/arc-phase-select`。
总计划：`arc-reactions-plan.md` 第三步 ①。本段只做①；②「情境→做法」注入、③大五注入不在本段。
v5 修订（2026-10-05，对照调研结论）：按 DREAM 两层人设，把随剧情变化的字段（关系态度、示范台词、开场白）也按阶段投影，背景只写稳定层；边界示范改为蒸馏时按角色、按阶段生成（RoleKE-Bench 做法），问题不得含真实后续情节；G5 改为等 G6 后再跑；投影收拢在 `ChatEngine` 构造这一处。v4 修订：存卡调度与检索上界共用 `has_positions`；切分函数在切分时给出位置；S11 措辞与既有名单重建区分；补写锚点失败卡不截断的局限。v3 修订：重建触发改到唯一的存卡汇合点 `save_distilled_card`，删去「重建入口拿不到原文」的局限；分发函数、提示词片段、按卡偏好各收拢一份。v2 修订：补 D9 行为变化、§3.6 定为构造时传、§2 路径既有机制与真实规模、扫描原始输出、调用点矩阵补行、出处链接、Playwright 验收、纯计算层变异预跑。

## 0. 一句话目标

用户开聊前选「这次聊的是哪个时期的他」；角色只按那个时期及之前的状态说话，检索和记忆不给出之后的剧情，名著自带的记忆用边界说明 + 示范压住。

## 1. 已定决策（Shiyu 2026-10-05，依据见 §2.3）

| 编号 | 决策 |
|---|---|
| D1 | 在身份弹窗 `RoleSetupModal` 里选阶段（原生单选），和「我扮演」并列；建会话时随 `start_session` 提交；恢复旧存档以存档记录为准 |
| D2 | 注入阶段 1..k 的 label + state；k<n 时之后的阶段和「从…到…」变化轴都不注入；放 `card_core`（永不裁剪），不做每轮重新注入 |
| D3 | 检索按阶段截断：只取当前阶段终点之前的原文片段 |
| D4 | 关键记忆按阶段归位：只在某阶段成立的挂 `ArcPhase.memories`，全程成立的留顶层 `key_memories`；聊天注入顶层 + 阶段 1..k |
| D5 | k<n 时 `card_core` 加「只知道到此为止」说明 + **阶段 k 自己的边界示范**（D12）；k=n 不加；旧卡没有示范时只加说明，不放未经验证的通用示范 |
| D6 | 只能从已有阶段里选，不能自写 |
| D7 | 默认最后阶段；一个存档固定一个阶段；没有 `arc_phase` 的会话、越界编号都按最后阶段 |
| D8 | 旧卡不迁移：没有起点 / 指纹的卡不做检索截断；没有 `memories` 的旧卡，顶层记忆照旧全注入 |
| D10 | **人设按 DREAM 两层处理**：稳定层（身份、性格、价值观、语言风格、心理档案）全书一份；事件层按阶段投影 —— 弧线、关键记忆、关系态度、示范台词、开场白都只给阶段 1..k 的版本（§3.3 `project_card`） |
| D11 | 顶层 `background` 只写**故事开始前就成立**的出身与处境；故事里发生的事由阶段描述和阶段记忆承载（只改提示词，不加字段） |
| D12 | 边界示范在蒸馏时为每个阶段生成 2–4 组，回答用该角色在该阶段的口吻；问题只能是「这个阶段的他不可能知道、书里也没有发生的事」，**不得出现书中真实的后续情节**（否则示范本身就把未来告诉了模型） |
| D13 | 关系态度按阶段：分组蒸馏里 G5 等 G6 完成、拿到阶段列表后再跑；每条关系写成按阶段的态度列表并附摘录，复用第二步的定位与分发 |
| **D9** | **弧线注入对所有会话生效**（含旧存档、群聊），没有 `arc_phase` 的按最后阶段。这是**有意的行为变化**：改完后，所有带阶段的卡在聊天时都会看到自己的弧线（今天一个字都不注入，C1） |

## 2. 已查实的约束（S0 逐条复核，任一不成立就停下）

C1. 聊天 prompt 目前**完全不注入** `character_arc` / `situation_behaviors`：`core/context_engine.py` `_build_card_core`（:331）、`_build_card_ext`（:376）。
C2. 关键记忆：`core/schema.py:132` `key_memories: list[str]`；聊天注入 `context_engine.py:375-376`；导出 `core/export.py:27-28`（只导顶层）；核对路径 `core/card_quotes.py:30`；前端 `CharCard.jsx:798`、`MarketCardDetail.jsx:713`、`EditCardModal.jsx:23/75/110/147/221`（扫描见附录 A）。
C3. 分组蒸馏：`core/schema.py:153` `FORMAT_GROUPS`，`key_memories` 在 G4，弧线在 G6；G6 注释写明「阶段和做法必须在同一次调用里产出」。只有 `distill_incremental_stream`（`core/distiller.py:2225` 起，:2491–2536）按组分调用；其余三个入口一次调用整份 `draft_schema()`。每组输出上限 `CARD_MAX_TOKENS = 8192`（:534）。
C4. `ArcPhase(PhaseState)` 只有 `behaviors`（`schema.py:104-106`）；`CharacterArc.phases`（:120-122）。
C5. 第二步的阶段范围在**规范化文本**坐标里算：`core/phase_anchoring.py` `phase_ranges`；`card_from_draft(data, source_text)` 是转卡唯一出口。
C6. 规范化 `core/quotes.py:normalize`（:41）= t2s → 去空白和标点 → 并异体字。**沙箱实测**：原文按 300 个随机切点分段、逐段规范化后累加长度，与「规范化整段前缀」**全部相等**（孔乙己 ×50，简体与繁体各 131150 字，0/301 不一致）。样本外（t2s 恰把跨切点的短语换成不同长度）理论上可能差几个字，对以千字计的阶段范围无影响。
C7. 检索唯一汇合点：agent 的 `search_scenes`（`core/agent/tools.py:149-150`）与非 agent（`context_engine.py:295`）都走 `_retrieve_scenes_ex` → `_scene_items`（:488）→ `query_with_emotion_ex`；会话侧 `SessionRag.query_with_emotion_ex`（`core/indexing_service.py:135-147`，透传 kwargs）；RAGEngine 两个公开出口（`rag.py:497`、`:522`）都落到 `_fetch_candidates`（:359），chroma 调用唯一一处 :384。
C8. Chroma 1.5.9（`requirements.txt:59`）。官方 where 语法：`$lt` 适用 int/float（https://docs.trychroma.com/reference/where-filter）。**沙箱实测**：`where={"npos": {"$lt": 600}}` 返回 npos=0/599 的条目，**不带 npos 键的条目被排除** —— 只能对「已带位置的集合」加过滤。
C9. 场景索引 `scenes_{card_id}`（`core/scene_indexer.py`），元数据带正文指纹（键 `content_fingerprint`，:34；`_content_fingerprint` 整段 sha256，:37-39），指纹不变不重建；切分 `_split_scenes`（:123）；条目元数据只有 emotion/characters/scene_index（:103-112）。
C10. 原文索引 `text_{text_id}`：`IndexingService._build_text_collection`（`indexing_service.py:225`），已存在就复用，**无指纹、无版本**；**已有重建入口** `schedule_text_reindex`（:319，后台、带 `rebuild=True`，现被 `distill.py:1226` 用于名单变化）；切片 `RAGEngine._chunk_text`（`rag.py:209`）从头顺序切，起点已知；写入 `rag.py:277-330`；`mark_built`（:31）先并原元数据再改构建号。
C11. 指纹：`core/fingerprint.py` 只有凭据用的 `key_fingerprint`；正文指纹只在 `scene_indexer._content_fingerprint`。
C12. `sessions`（`storage/migrations_pg/001_init.sql:56`）无阶段列。PG 迁移最新 034；SQLite 已冻结，但**新增列必须写孪生迁移并登记**（`storage/migrations/README.md`，列集锁 `tests/test_postgres_store.py::TestPgFreshSchemaClosure`），SQLite 最新 099。
C13. `save_session`（PG `postgres_store.py:1374`）是 upsert，**冲突时覆盖所有列**；调用方 `text_manager.py:560/622`、`chat.py:322/468`（聊天中更新身份）、`distill.py:1413`。阶段**不能**走它。
C14. 会话读取的 SELECT 是显式列清单：PG `postgres_store.py:1402/1419`（`get_session_owned` 等）、`:1590/1601`（`list_sessions`）；SQLite `sqlite_store.py:1925/1944/2128/2140`。
C15. 引擎唯一构造点 `TextManager._create_session`（`text_manager.py:666`，keyword-only）。调用方：`text_manager.py:549/616`（新会话）、`distill.py:1388/1405`（`/start_session` 两分支）、`chat.py:199`（自动重建）、`history.py:236`（恢复存档）。`ChatEngine.__init__` 已有构造参数 `user_role`（`chat_engine.py:113`）。
C16. 重建后恢复身份**已重复两份**：`chat.py:221` 与 `history.py:276` 各写 `engine.user_role = db_session["user_role"]`。
C17. `ChatEngine._compose_context`（`chat_engine.py:308`）是构建 prompt 的唯一出口，把 `self.user_role` 交给 `build_ex`；群聊也走它。
C18. 开场白：`distill.py:1417-1452` 用卡片性格与 `user_role` 生成，不知道阶段。
C19. 前端：`RoleSetupModal.jsx` 两步（输入 → 确认，:56-66 / :79-127），身份预设按钮 `.user-role-presets`（`global.css:8121`）；`StartChatButton` → 弹窗 → `startChat`（`useAppStore.js:1188`），有存档时弹存档框。`start_session` 请求体在 store **写了三份**（:1170、:1255、:1364）；`sessionUserRole` 赋值**五处**（:1184、:1291、:1325、:1383、:1718），:1325/:1718 恢复存档时取 `session.user_role`。`ChatArea.jsx:637-641` 只读显示「我扮演：」（`.user-role-bar/.user-role-label/.user-role-locked`，`global.css:4491/4500/4530`）。阶段列表组件 `common/ArcList.jsx`，样式 `.card-arc-list/.card-arc-item/.card-arc-index`（`global.css:3849/3856/3936`）。
C20. Playwright 验收：`web/frontend/e2e/`，`repro-chat-view.spec.js` 等用 `page.route` 模拟接口并截图（:61）；共享 `e2e/helpers.cjs` 本段不改。
C22. 每张蒸馏出的卡都经过唯一的存卡汇合点 `TextManager.save_distilled_card`（`core/text_manager.py:579`；调用方 `distill.py:537`、`:1148`），它**自己读原文内容**（:608-609）并在 :630 调度场景索引。`save_card` 按 text_id + 名字 upsert，**重蒸同名角色沿用原 card_id**（:592）—— 原 `scenes_{card_id}` 指纹相同就不重建，不会带上位置。
C23. 前端「按卡存身份」：`useAppStore.js:314-333`（读、写、一次性迁移旧全局值）。
C24. 原文内容不可改：存储只提供 `update_text_cover`、`update_text_visibility`（`postgres_store.py:402`、`:5031`），没有改正文的接口。卡上指纹与索引指纹不一致只会出现在同步分片路径的聊天类文本（上一段 §9.3：那里传的是预处理后的文本），生产不走该路径。
C25. 两个切分函数都**只返回文本、不带位置**：`SceneIndexer._split_scenes`（`scene_indexer.py:123-139`，`re.split` 后 strip、过滤短段、长段再按空行拆）与 `_split_chat_scenes`（:141）；`RAGEngine._chunk_text`（`rag.py:209`）。
C26. 读 `card.relationships` 的聊天侧消费方有四处：`context_engine.py:379-381`（卡片扩展层）、`chat_engine.py:1018` 起（近期对话命中时注入关系口径）、`chat_engine.py:1154` 起（按用户身份匹配关系定初始好感）、`group_session.py:291-296`（群聊里对上一位发言者的态度）。都读 `attitude`。
C27. `ChatEngine` 的构造点有三处：`text_manager.py:711`（经唯一构造点 `_create_session`）、`web/routers/group.py:173`、`:372`（群聊）。`ChatEngine.__init__` 收 `card`（`chat_engine.py:111`）并交给 `ContextEngine`。
C28. 示范台词：候选由 `core/quotes.py:162` `extract_candidates` 按出场顺序抽取，`Candidate`（:148-159）**不带原文位置**；挑选后存为顶层 `dialogue_examples: list[str]`（`schema.py:137`），属后置字段（:169），由 `Distiller.attach_dialogue_examples`（`distiller.py:1740`）附上。
C29. 分组蒸馏的各组**并行**（`distiller.py:2478-2536`，每组一条线程，按完成先后收，按组序合并）；流式一旦开始吐字就没有总时长夹逼（:795），只有单次尝试的超时。G5 改为等 G6 后再跑，只增加总耗时（≈ G6 + G5 而非两者取大），不撞任何总时限；每组返回时照常发心跳（:2533）。
C30. `first_message` 的消费方：开场白生成 `distill.py:1424`（当底稿）、`text_manager.py:522`（新会话开场变体）、导出 `export.py:52`。
C21. 端口与容器：测试 PG 若 55432 被占，起一次性 PG 55433（对齐 `docker-compose.test.yml`），跑完删除；S0 把本机实际检查结果写进 §9。

### 2.1 路径上已有的机制（加东西前逐个核对前提）

| 机制 | 位置 | 计时 / 计数从哪开始、依赖什么 | 本段会不会改变它的前提 |
|---|---|---|---|
| 按角色过滤的超取与重取 | `rag.py:79-80`（×4，最多重取 1 次）、`:380-404` | 每次查询内计数；「取回条数 < 请求数」判定为取尽 | 加 `where` 后候选池变小，取尽判定仍成立（取回少即真没有）；不改常量 |
| 情感重排 | `rag.py:425` 起 `_rank_with_emotion` | 对已取回候选排序 | 不变，只是候选更少 |
| SessionRag 版本令牌 | `indexing_service.py:96-147` | 每次检索前比构建号；构建号变化即重载 | `text_` 重建后构建号变 → 会话自动重载到带位置的新集合；**适用性判断只读已装载集合的元数据，不加 IO** |
| 建索引互斥 | `_builds.building` / `is_building`（`indexing_service.py:194`） | 正在建的集合装载时跳过 | 重建期间 `text_` 被跳过 → 本轮检索为空或用 `scenes_`，与今天名单重建时一致 |
| agent 检索工具超时 | `core/agent/tools.py:86` `SCENE_TIMEOUT = 5` 秒 | 工具调用开始计时 | 查询路径只多一个元数据比较和 `where`，不新增网络调用；**重建调度不放在查询路径**（§3.5） |

### 2.2 路径机制表（通道 × 执行上下文 × 测试）

| 通道 | 执行上下文 | 本段改动 | 守它的测试 |
|---|---|---|---|
| `/start_session` 建会话、写阶段 | async 路由；`_create_session` 在 `to_thread` | 新增一次写入 | E1、U10 |
| `save_distilled_card` 存卡后调度索引补位置 | async；作业在后台线程 | 卡带起点时调度 `text_` 补位置、场景索引带 `need_positions` | E9、U15 |
| 自动重建 `chat.py:199` / 恢复存档 `history.py:236` | async 路由 + `to_thread` | 两处改用同一个 `session_identity(db_row)` 传参 | E2、E3、S5 |
| prompt 构建 `build_ex` | 同步（线程池 / `to_thread`） | 读 `ArcView` | U1–U3 |
| 检索 `_fetch_candidates` | 同步，`ctx_submit` 线程池或工具超时线程 | 加 `where` | U6、E4 |
| 适用性判断 `SessionRag` | 同上 | 只读元数据 | U7 |

### 2.3 规模表（真实规模）

| 对象 | 规模 | 上限来源 | 策略 |
|---|---|---|---|
| 阶段数 | 提示词要求 2–3、最多 4 | **软约束**：只在提示词维度 L（`distiller.py:192`），代码不截断 | 选择框列出全部 n 项（原生单选，n 大时弹窗内滚动）；`arc_view` 不假设 n ≤ 4 |
| 关键记忆 | 提示词要求 3–5 | 软约束：维度 E（`distiller.py:174`） | 全部注入，不截断（与今天一致） |
| 注入的弧线 + 边界说明 + 示范 | 约 300–450 token（4 阶段 × ~70 字 + 说明与示范 ~300 字） | 估算 | 放 `card_core`，不裁剪 |
| G6 输出 | 估算 < 2500 token（12 条做法 × ~160 字 + 5 条记忆 × ~140 字） | `CARD_MAX_TOKENS = 8192` | 不改上限 |
| `text_` 重建（125 万字上限的书） | 切片 500 字、重叠 50（`config.example.yaml:22-25`）→ 约 2,778 片；每批 10 条（`embeddings.py:240`）→ 约 278 次调用；约 140 万 token；按第三方价目表 $0.07 / 百万 token（官方价以百炼控制台为准）约 $0.1 | 第一步的 90 万 token 上限 | 每份原文最多一次；在**存卡时**（C22）只要卡带起点且 `text_` 无位置就调度，用户开聊时通常已建好；**仍在建时这一路检索为空（不泄露）**，耗时未实测，测试只复刻「建中 → 空、建完 → 截断结果」的先后关系，不涉及任何时限 |
| 检索过滤 | 每片元数据多一个整数 | C8 | 无额外调用 |

### 2.4 出处表（性质：文献 / 代码事实 / 自研）

| 设计点 | 出处 | 落点 | 性质 |
|---|---|---|---|
| 人设只含当前时间点之前的内容；检索按时间戳截断 | DREAM，KDD 2026（https://arxiv.org/abs/2608.05170，DOI 10.1145/3770855.3818027）§3.2.1、§3.3.1；Table 3（泄露未来一项对 RAG 胜率 > 90%）、Table 4（去掉时间戳后分数下降） | D2–D4，§3.3–3.5 | 文献 |
| 条目标适用时期以守时间边界 | MDRP，Findings of ACL 2026（https://aclanthology.org/2026.findings-acl.1175/）Table 7 `time_scope` | D4 | 文献 |
| 示范对「超出认知」最有效，CoT / 自我反思无效 | RoleKE-Bench，EMNLP 2025（https://aclanthology.org/2025.emnlp-main.1689/）Table 3 | D5 | 文献（测的是时代错乱，「同书后续剧情」未直接验证） |
| 不做每轮重新注入 | Persistent Personas，EACL 2026（https://aclanthology.org/2026.eacl-long.246/）§5.1、Fig. 15 | D2 | 文献（未测缓解手段） |
| 不开放自由文本 | Findings of ACL 2026 角色扮演越狱（https://aclanthology.org/2026.findings-acl.349.pdf）；RoleBreak，COLING 2025（https://aclanthology.org/2025.coling-main.494/） | D6 | 文献 |
| 原生单选 | 原生 `input[type=radio]` 自带键盘、读屏、焦点，不替换 | §4 | 代码事实 |
| 过滤用 Chroma 原生 `where` | 官方文档（C8 链接）+ 沙箱实测 | §3.5 | 代码事实 |
| 坐标用逐段累加 | C6 实测 | §3.4 | 代码事实 |
| 选择放身份弹窗、请求体 / 身份 / 恢复各一份 | C16、C19 | §3.6、§4 | 代码事实 + 自研 |

## 3. 设计（后端）

### 3.1 存卡结构（`core/schema.py`）

- `ArcPhase` 加 `memories: list[str] = []` 与 `start: int | None = None`（该阶段起点在**规范化原文**中的位置）。
- `CharacterArc` 加 `source_fingerprint: str = ""`。
- 顶层 `key_memories` 形态不变。

### 3.2 蒸馏与转卡

- `FORMAT_GROUPS`：`key_memories` 从 G4 挪到 G6（C3 的既有不变式）。G4 只剩 `psyche`。
- 草稿：`DraftMemory(BaseModel)`：`memory: str`、`occurrences: list[DraftOccurrence]`（复用第二步）；`CardDraft.key_memories: list[DraftMemory]`。
- 「每个阶段各给一段该阶段里的原文摘录，写成 `[{"phase": …, "quote": "…"}]`；没有阶段时 `phase` 填 0」抽成**一个**共享提示词片段常量，维度 E 与维度 O 都引用它，不各写一遍。模板（`distiller.py:240`）与格式说明同步。
- `core/phase_anchoring.py`：`verify` 改为对**任意带 `occurrences` 的条目列表**工作（做法、记忆各调一次）；编号合法性过滤从 `card_from_draft` 抽成一个函数两类共用；`Verification` 增加 `starts: list[int] | None`（整卡跳过时 None）。
- `card_from_draft`：抽出**一个**分发函数 `dispatch(rows, final_phases, count)`（全阶段成立 → 顶层，否则挂阶段），做法与记忆各调一次，不写第二份；写 `ArcPhase.start` 与 `CharacterArc.source_fingerprint`（`content_fingerprint(source_text)`）；整卡跳过时不写起点。监测行加 `memories_dropped`。
- `core/card_quotes.py` `VERIFIED_FIELDS` 加 `"character_arc.phases[].memories[]"`。
- `core/export.py`：导出时在关键记忆后按阶段附上 `memories`（标「阶段 k · label」），避免挂到阶段下的记忆从导出里消失。

### 3.3 阶段视图（新模块 `core/arc_view.py`，纯计算）

`project_card(card, arc_phase) -> (CharacterCard, ArcView)`：把卡**投影到阶段 k**，返回投影后的卡与视图。投影规则（k<n 时；k=n 时只做第 4 条的「无阶段态度则保留」与合并，其余原样）：
1. `character_arc.phases` 只留 1..k（只留 label、state）；`axis` 置空。
2. `key_memories` = 顶层 + 阶段 1..k 的 `memories`。
3. `dialogue_examples` = 顶层 + 阶段 1..k 的 `dialogue_examples`。
4. `relationships`：有 `phase_attitudes` 的，取阶段 ≤k 中**最新**的态度；它最早的阶段 >k（之后才认识）→ 去掉；没有 `phase_attitudes` 的（旧卡）原样。k=n 时一律用顶层 `attitude`（用户可编辑）。
5. `first_message` 置空（开场白改由阶段描述生成，D10）。
6. 视图带上阶段 k 的 `boundary_examples`（k<n 时）与检索上界。

**投影只在 `ChatEngine.__init__` 做一次**（收 `arc_phase`，`self.card` 即投影后的卡）：C27 的三个构造点、C26 的四个关系消费方、卡片扩展层、群聊都**不用改**，读到的天然是阶段 k 的版本。开场白（`distill.py:1417`）同样调 `project_card` 后再用。

`arc_view(card, arc_phase) -> ArcView`：`k`、`n`、要注入的阶段（1..k）、`show_axis`（k==n）、`boundary`（k<n）、要注入的记忆（顶层 + 阶段 1..k）、检索上界 `before`（k<n 且 `has_positions(card)` 时取阶段 k+1 的起点，否则 None）。`has_positions(card)`（起点齐全、严格递增、指纹非空）定义在本模块，**存卡调度与检索上界共用这一个判定**、`source_fingerprint`。`arc_phase` 为 None / 越界 / 卡无阶段 → k=n。**阶段相关取舍只在这里写一次**，注入、检索、开场白、重建调度都读它。

### 3.4 坐标（`core/quotes.py`）

`normalized_starts(text, raw_starts) -> list[int]`：对升序原文起点逐段规范化累加（C6）。只此一处，两类索引都调它。

### 3.5 检索截断

- `core/fingerprint.py` 加 `content_fingerprint(text)`（整段 sha256）；`scene_indexer._content_fingerprint` 改为调用它。
- 两类集合写入时每条加 `npos`，集合元数据加 `pos_schema: 1` 与 `content_fingerprint`（`text_` 首次写入指纹）。**位置在切分时产生**：`_split_scenes`、`_split_chat_scenes`、`_chunk_text` 改为返回「(原文起点, 片段)」（C25），切分过程本来就知道下标；不允许事后用 `text.find` 回找（重复段落会找错）。原文起点再经 `normalized_starts` 换成规范化坐标。
- `RAGEngine._fetch_candidates` 增加 `before: int | None`，非 None 时 `collection.query(..., where={"npos": {"$lt": before}})`。**机制只在这一处**；两个公开出口透传。
- **适用性只在 `SessionRag.query_with_emotion_ex`**：上界非 None 时，已装载集合的元数据须 `pos_schema == 1` 且指纹等于卡上的 `source_fingerprint`；不满足 → 返回空 `EvidenceHits` 并 warning。**不在查询路径调度任何东西**。
- **补位置的调度只在存卡汇合点**（C22，它自己有原文内容）：`save_distilled_card` 在 :630 调度场景索引处，若 `has_positions(card)`（§3.3），① 场景索引带 `need_positions=True` 调度 —— 作业在「指纹相同」时额外检查 `pos_schema`，缺就重建（覆盖重蒸同名卡沿用 card_id 的情况）；不带该标志的既有调度（`/start_session` 等）幂等规则不变，旧卡不会被重新嵌入；② `text_{text_id}` 元数据无 `pos_schema` 时调用已有的 `schedule_text_reindex`（C10）。会话入口（建会话、自动重建、恢复存档）都不调度，也不需要。
- 依据：按时间截断的检索要「有、且只给之前的」，而不是不给 —— DREAM 消融里去掉检索掉分最多（约 8.9%），时间戳只决定给哪些（§2.4）。
- 已知局限：① 片段起点在边界前、内容跨过边界时，最多泄露一个片段（≤ 500 字）；② 存卡后索引尚在建时，选了非最后阶段的会话本轮检索为空；③ 第二步位置检查整卡跳过的新卡（锚点核对不上或乱序）没有起点，`has_positions` 为假，检索不截断 —— 与 D8 旧卡同一处理，比例看监测行 `skipped_card`。

### 3.6 会话阶段（定为**构造时传**）

- 迁移：PG `035_session_arc_phase.sql` `ALTER TABLE sessions ADD COLUMN IF NOT EXISTS arc_phase INTEGER`；SQLite 孪生 `100` 并登记（C12）。
- 存储接口加 `set_session_arc_phase(id, user_id, phase)`，**只在 `/start_session` 调用一次**；`save_session` 不碰这一列（C13）；C14 的八处 SELECT 都加 `s.arc_phase`。
- `StartSessionRequest` 加 `arc_phase: int | None = None`；服务端校验：卡无阶段或越界 → 存 None。
- `ChatEngine.__init__` 加 `arc_phase: int | None = None`（与 `user_role` 同位）；`_create_session` 加 keyword-only `arc_phase` 并传入；`_compose_context` 把它交给 `build_ex`。
- 新增 `session_identity(db_row) -> {"user_role": ..., "arc_phase": ...}`（`core/text_manager.py`，紧挨 `_create_session`）。`chat.py:199` 与 `history.py:236` 都改为 `_create_session(..., **session_identity(db_session))`，并删掉 `chat.py:221`、`history.py:276` 的事后赋值（消除 C16）。

### 3.7 注入（`core/context_engine.py`）

- `_build_card_core` 读（已投影的）`card.character_arc` 与视图，追加「## 此刻的你」：k/n、阶段 1..k 的 label 与 state；k==n 时附变化轴。卡无阶段则不追加。
- k<n 时追加 §3.8 的边界说明与阶段 k 的示范。
- 关键记忆、关系、示范台词由既有代码从投影后的卡读出，**不改**。
- `_scene_items` 把 `ArcView.before` 与指纹交给检索。
- 开场白（C18）提示里加入当前阶段 label 与 state。

### 3.8 边界说明与示范（k<n 时进 `card_core`）

说明（常量，只此一处）：

```
你只知道到此刻为止发生过的事。之后会发生什么，你一概不知：别人提起你没经历过的事、没见过的人、还没发生的变故，你会困惑、追问或否认，绝不顺着往下说，也不预言自己的将来。
```

其后附「示范（只学应对方式，不照抄措辞）」+ 阶段 k 的 `boundary_examples`（每组「对方：… / 你：…」）。旧卡没有示范 → 只放说明。

### 3.9 蒸馏侧新增（G6 与 G5）

- `ArcPhase` 再加 `boundary_examples: list[BoundaryExample]`（`ask`、`reply`）与 `dialogue_examples: list[str]`。
- **G6**（维度 L）每个阶段输出 2–4 组 `boundary_examples`，提示词写明 D12 的约束（问题只能是此阶段的他不可能知道、书里也没有发生的事；不得出现书中真实的后续情节；回答用此阶段的口吻）。D12 的「不是真实情节」**代码无法完全核对**，按卡片质量规矩：演示卡由 Claude 对照原文核对并出纠错清单。
- **G5**（关系）：草稿每条关系 `target`、`relation`、`note`、`attitudes: [{phase, attitude, quote}]`；没有阶段时只写一条 `phase` 0。分组蒸馏里 G5 的线程在 G6 返回后才启动，提示词附 G6 的阶段列表（编号、label、state）；其余组照旧并行（C29）。单次调用的三个入口一次写全，不受影响。
- 转卡：关系态度复用第二步的位置检查（`verify` 已改为通用条目）与编号过滤；分发结果写 `Relationship.phase_attitudes: list[{phase, attitude}]`，顶层 `attitude` 取它出现的最后一个阶段的态度（k=n 与前端编辑都读它）。`card_quotes` 加 `relationships[].phase_attitudes[].attitude`。
- **示范台词按位置归阶段**：`extract_candidates` 抽取时记下每条候选在原文中的起点（C28，在抽取时产生，不回找）；`attach_dialogue_examples` 挑中后，用 `arc_view.phase_of(card, 规范化位置)` 归到阶段，挂 `ArcPhase.dialogue_examples`；卡无起点时留顶层。
- **背景**（D11）：维度 A（`distiller.py:170`「基本信息：名字、身份、背景」，G1）的背景部分改为只写故事开始前就成立的出身与处境；关系是维度 F（:176，G5），边界示范挂在维度 L（:190 起，G6）。

## 4. 设计（前端，触发链）

- **选择**：卡有阶段时，`RoleSetupModal` 输入步在身份输入下方显示原生单选组（`fieldset` + `input[type=radio]`），每项「阶段 k · label」附一行 state；默认最后阶段；每张卡记住上次选择：把 C23 的读、写、迁移抽成一个通用的「按卡存偏好」函数（参数为键名），身份与阶段都用它，不复制第二套；确认步的文案带上所选阶段。卡无阶段不渲染。
  - 触发链：选中某项 → `onChange` → `setArcPhase(cardId, k)` → 本卡默认值更新 → 确认步文案重算。
- **请求体单一构造**：新增 `startSessionBody(card)` → `{text_id, card_id, user_role, arc_phase}`；:1170、:1255、:1364 三处改用它。
  - 触发链：确认进入 → `startChat` → 无存档 / 选「新建」→ `postJSON('/api/distill/start_session', startSessionBody(card))`。
- **会话身份单一出口**：新增 `applySessionIdentity({user_role, arc_phase})`，五处赋值都改走它；新建从卡片默认值取，恢复存档从 `session.user_role` / `session.arc_phase` 取。
  - 触发链：恢复存档 → 读 `session` → `applySessionIdentity` → `sessionArcPhase` 更新 → 聊天头部重算。
- **显示**：`ChatArea` 在「我扮演：」旁只读显示「阶段 k/n · label」（复用 `.user-role-label` / `.user-role-locked` 样式，不新增颜色变量）；卡无阶段不显示；`sessionArcPhase` 为 null 显示最后阶段。
- **卡片展示**：`ArcList` 在每个阶段下显示 `memories`；`EditCardModal` 只保证原样交回（既有测试覆盖），不加编辑入口。
- **自动验收**：新增 `web/frontend/e2e/arc-phase-select.spec.js`，用 `page.route` 模拟 `/api/distill/start_session`、卡片与存档接口（C20 的既有写法），断言：有阶段卡弹窗出现单选且默认最后阶段；选阶段 1 后请求体 `arc_phase=1`；进入聊天头部显示「阶段 1/n · label」；无阶段卡不出现选择与头部显示；恢复存档时显示存档里的阶段。截图弹窗与聊天头部各一张给 Shiyu 看外观。不改 `e2e/helpers.cjs`。

## 5. 测试计划（先在基线上红，再在分支上绿；每条边界两侧）

**拒侧写法沿用上一段**：被测条目必须另有一个能通过的标注，否则被兜底掩盖。

### 5.1 单元

| 编号 | 用例 | 两侧 |
|---|---|---|
| U1 | `arc_view`：None / 0 / 1 / n / n+1 → k = n/n/1/n/n | 边界 0、1、n、n+1 |
| U2 | k<n 不含之后阶段、无变化轴、有边界说明；k=n 有变化轴、无边界说明 | 两侧 |
| U3 | 记忆 = 顶层 + 阶段 1..k；阶段 k+1 的不出现 | 收 / 拒 |
| U4 | 上界：起点齐全递增且有指纹 → 阶段 k+1 起点；缺起点 / 不递增 / 无指纹 / k=n → None | 两侧 |
| U5 | `normalized_starts` 与「规范化前缀长度」逐点相等 | — |
| U6 | 检索：上界 600 只返回 npos ≤ 599；上界 None 全返回 | 599 / 600 |
| U7 | 适用性：`pos_schema` 缺或指纹不符 → 空 + warning；相符 → 按上界过滤；上界 None → 不过滤 | 两侧 |
| U8 | 蒸馏：记忆按位置校正后分发；起点与指纹写入；整卡跳过不写起点 | 两侧 |
| U9 | G4 不含 `key_memories`、G6 含；各组 ∪ 后置字段 == 卡字段（沿用 F3 锁） | — |
| U10 | `set_session_arc_phase` 后再 `save_session`（改身份）→ `arc_phase` 不变；八处 SELECT 都返回 `arc_phase` | 两侧 |
| U11 | 前端 `startSessionBody`：有阶段带 `arc_phase`；无阶段为 null | 两侧 |
| U12 | 前端 `applySessionIdentity`：恢复用 session 值；新建用卡片默认值 | 两侧 |
| U13 | 前端弹窗：有阶段渲染单选且默认最后；无阶段不渲染 | 两侧 |
| U14 | 前端头部：显示「阶段 k/n · label」；无阶段不渲染 | 两侧 |
| U17 | 投影·关系：k<n 取 ≤k 的最新态度；之后才认识的关系去掉；旧卡关系原样；k=n 用顶层态度 | 两侧 |
| U18 | 投影·台词：顶层 + 阶段 1..k；`phase_of` 在起点上归后一阶段 | 两侧（99/100） |
| U19 | 投影·边界示范：k<n 只有阶段 k 的；k=n 没有；旧卡只有说明 | 两侧 |
| U19b | 投影·开场白：k<n 置空、开场白由阶段描述生成；k=n 保留 | 两侧 |
| U20 | 分组蒸馏：G5 在 G6 返回后才启动，且提示词含 G6 的阶段列表；其余组仍并行 | 两侧 |
| U21 | 投影只在构造处：三个构造点得到的 `engine.card` 都是投影版；四个关系消费方读到阶段 k 的态度 | — |
| U16b | 切分函数返回的原文起点与片段一致：`text[start:start+len(片段)] == 片段`（场景、聊天场景、原文切片各一；含重复段落） | 两侧 |
| U15 | 存卡调度：带起点的卡 → 场景索引带 `need_positions`、`text_` 无位置时调度补建；不带起点的卡 → 都不调度；场景作业：指纹相同 + 无 `pos_schema` + `need_positions` → 重建，指纹相同 + 无标志 → 跳过 | 两侧 |
| U16 | 导出：挂阶段的记忆出现在导出里 | 收 / 拒 |

### 5.2 调用点矩阵（行 = 入口，列 = 可观测输出；空格 = 不适用）

| 入口 \ 输出 | 存档 `arc_phase` | prompt 阶段 1..k 且不含之后 | 检索带上界 | 其他 | 测试名 |
|---|---|---|---|---|---|
| E1 `/start_session` 有原文分支（`distill.py:1388`） | 写入 | ✓ | ✓ | — | `test_start_session_arc_phase_text` |
| E1b `/start_session` 独立卡分支（:1405） | 写入 | ✓ | 无 RAG | — | `test_start_session_arc_phase_standalone` |
| E2 自动重建 `chat.py:199` | 读回 | ✓ | ✓ | 身份同时恢复 | `test_rebuild_restores_identity` |
| E3 恢复存档 `history.py:236` + 列表接口 | 读回 + 返回 | ✓ | ✓ | 身份同时恢复 | `test_resume_restores_identity` |
| E4 agent 工具 `search_scenes` | — | — | ✓ | — | `test_agent_search_respects_window` |
| E5 群聊 | 无列 | k=n | 无上界 | — | `test_group_uses_last_phase` |
| E6 `text_manager.py:549/616` 新会话 | None | k=n | 无上界 | — | `test_new_session_defaults_last` |
| E7 开场白 `distill.py:1417` | — | 提示含当前阶段 | — | — | `test_opening_uses_phase` |
| E10 关系消费方 `context_engine.py:379`、`chat_engine.py:1018`、`:1154`、`group_session.py:291` | — | 阶段 k 的态度 | — | 读投影卡 | `test_relationship_consumers_see_phase` |
| E11 群聊构造 `group.py:173/372` | — | k=n 投影 | — | — | `test_group_engine_projected` |
| E9 存卡 `save_distilled_card`（`distill.py:537`、`:1148` 两个调用方） | — | — | — | 调度补位置 | `test_save_card_schedules_positions` |
| E8 导出 `export.py:27` | — | — | — | 含阶段记忆 | U16 |
| F1–F3 前端请求体 :1170 / :1255 / :1364 | 提交 | — | — | — | U11 + S3 |
| F4 前端身份赋值五处 | — | — | — | 头部显示 | U12 + S7 |
| F5 卡片展示 `ArcList`（CharCard、MarketCardDetail） | — | — | — | 显示阶段记忆 | `ArcListMemories.test.jsx` |

### 5.3 结构锁

| 编号 | 断言 |
|---|---|
| S1 | `collection.query(` 在 `core/` 只出现一处 |
| S2 | 阶段切片只在 `core/arc_view.py`：其他模块不出现 `.phases[:` |
| S3 | 前端只有 `startSessionBody` 构造 `start_session` 请求体（字面量只此一份） |
| S4 | `save_session` 的 SQL 不含 `arc_phase` |
| S5 | `core/`、`web/` 不再出现 `engine.user_role =` |
| S6 | 正文指纹只有 `core/fingerprint.content_fingerprint` 一份实现 |
| S7 | 前端 `sessionUserRole:` 只在 `applySessionIdentity` 里赋值 |
| S8 | `card_draft.py` 里分发到阶段（`by_phase[`）只出现在 `dispatch` 一处 |
| S9 | 「该阶段里的原文摘录」提示词片段在 `distiller.py` 只定义一次 |
| S10 | `localStorage` 的按卡偏好读写只在通用函数里出现 |
| S11 | 以 `pos_schema` 为条件的补位置调度只在 `save_distilled_card`（`distill.py:1226` 名单变化时的既有重建不在此列） |
| S12 | 「起点齐全 / 递增 / 有指纹」的判定只在 `arc_view.has_positions`，其他模块不出现 `.start` 的齐全或递增检查 |
| S13 | `core/` 不出现对原文的 `.find(` 回找片段位置 |
| S14 | `project_card(` 只在 `ChatEngine.__init__` 与开场白生成处调用；其他消费方不读 `phase_attitudes` / `ArcPhase.memories` / `ArcPhase.dialogue_examples` |
| S15 | 边界说明文案常量只定义一次 |

## 6. 变异清单

| 编号 | 方向 | 变异 | 应红 | 预跑 |
|---|---|---|---|---|
| M1 | 放宽 | k<n 时仍注入之后阶段 | U2 | ✅ |
| M2 | 过严 | k=n 也加边界说明 | U2 | ✅ |
| M3 | 放宽 | 越界编号原样用 | U1 | ✅ |
| M4 | 过严 | 下界写成 2（选阶段 1 被当越界）※ | U1 | ✅ |
| M5 | 放宽 | 阶段 k+1 的记忆也注入 | U3 | ✅ |
| M6 | 过严 | 只注入阶段 k 的记忆 | U3 | ✅ |
| M7 | 放宽 | 上界用阶段 k+2 起点 | U4 | ✅ |
| M7b | 放宽 | 不查起点递增 | U4 | ✅ |
| M8 | 边界 | `$lt` 改 `$lte` | U6 | ✅ |
| M9 | 放宽 | 无位置集合照常检索 | U7 | ✅ |
| M10 | 过严 | 指纹相符也返回空 | U7 | ✅ |
| M11 | 放宽 | 场景作业忽略 `need_positions`（指纹相同就跳过） | U15 | 实现后 |
| M11b | 过严 | 不带标志的调度也因无 `pos_schema` 而重建（旧卡被重新嵌入） | U15 | 实现后 |
| M12 | 放宽 | `save_session` 覆盖 `arc_phase` | U10、S4 | 实现后 |
| M13 | 接线 | `/start_session` 不写 `arc_phase` | E1 | 实现后 |
| M14 | 接线 | `session_identity` 漏 `arc_phase` | E2、E3 | 实现后 |
| M15 | 接线 | `_scene_items` 不传上界 | E1、E4 | 实现后 |
| M16 | 接线 | 记忆留在 G4 | U9 | 实现后 |
| M17 | 坐标 | 用原文坐标 | U5、U6 | ✅ |
| M18 | 前端 | `startSessionBody` 漏 `arc_phase` | U11 | 实现后 |
| M19 | 前端 | 恢复存档用卡片默认值 | U12 | 实现后 |
| M20 | 导出 | 不导阶段记忆 | U16 | 实现后 |
| M23 | 接线 | `ChatEngine` 用原卡而不是投影卡 | U21、E10 | 实现后 |
| M24 | 放宽 | 之后才认识的关系也注入（用顶层态度） | U17 | ✅ |
| M25 | 取值 | 取最早而非 ≤k 的最新态度 | U17 | ✅ |
| M26 | 放宽 | 注入所有阶段的边界示范 | U19 | ✅ |
| M26b | 过严 | k=n 也注入边界示范 | U19 | ✅ |
| M27 | 接线 | G5 与 G6 并行（不等阶段） | U20 | 实现后 |
| M28 | 边界 | 落在起点上的台词归前一阶段 | U18 | ✅ |
| M29 | 取值 | k=n 用阶段态度而非可编辑的顶层态度 | U17 | ✅ |
| M30 | 放宽 | k<n 仍用全书开场白 | U19b | ✅ |
| M31 | 放宽 | 之后阶段的台词也注入 | U18 | ✅ |
| M21 | 坐标 | 切分后用 `text.find` 回找起点（重复段落取到第一处） | U16b | 实现后 |
| M22 | 复用 | 存卡调度另写一份「起点齐全」判定，漏掉指纹检查 | U15、S12 | 实现后 |

※ 原 M4「编号 n 当越界」是**等价变异**（越界本就回落到 n，结果相同，预跑存活），已换成下界方向。

### 6.1 发出前预跑（Claude 沙箱原型，纯计算层，2026-10-05）

原型 = `arc_view` + `normalized_starts` + 适用性判断 + 真 Chroma 1.5.9 的 `where` 查询，7 条用例，逐条注入变异：

```
RED   M1 放宽 k<n 注入之后阶段 | 1 failed, 6 passed in 0.76s
RED   M2 过严 k=n 也加边界 | 1 failed, 6 passed in 0.73s
RED   M3 放宽 越界原样用 | 1 failed, 6 passed in 1.05s
RED   M4 过严 下界写成 2（选阶段 1 被当越界） | 2 failed, 5 passed in 0.76s
RED   M5 放宽 k+1 记忆注入 | 1 failed, 6 passed in 0.87s
RED   M6 过严 只注入阶段 k 记忆 | 1 failed, 6 passed in 0.80s
RED   M7 放宽 上界用 k+2 | 1 failed, 6 passed in 0.90s
RED   M7b 放宽 不查递增 | 1 failed, 6 passed in 0.81s
RED   M8 边界 $lt→$lte | 1 failed, 6 passed in 0.93s
RED   M9 放宽 无位置集合照常检索 | 1 failed, 6 passed in 0.80s
RED   M10 过严 指纹相符也返回空 | 1 failed, 6 passed in 0.79s
RED   M17 坐标用原文坐标 | 1 failed, 6 passed in 0.78s
原型未变异：7 passed
```

第二批原型（投影 `project_card` + `phase_of`，4 条用例；预跑中把会崩溃的 M24 改成等价的非崩溃形态、把测不出 M25 的用例加了阶段 2 的态度）：

```
RED   M24 放宽 之后才出现的关系也注入 | 1 failed, 3 passed in 0.02s
RED   M25 取最早而非 ≤k 的最新态度 | 1 failed, 3 passed in 0.02s
RED   M26 放宽 注入全部阶段的边界示范 | 1 failed, 3 passed in 0.02s
RED   M26b 过严 k=n 也注入边界示范 | 1 failed, 3 passed in 0.02s
RED   M28 边界 落在起点上的台词归前一阶段 | 1 failed, 3 passed in 0.02s
RED   M29 k=n 用阶段态度而非可编辑的顶层态度 | 1 failed, 3 passed in 0.02s
RED   M30 放宽 k<n 仍用全书开场白 | 1 failed, 3 passed in 0.02s
RED   M31 放宽 之后阶段的台词也注入 | 1 failed, 3 passed in 0.02s
```

接线、存储、前端、导出、蒸馏编排的 13 条（M11–M16、M18–M23、M27）要等实现后才有代码可跑，审计时 Claude 在沙箱 PG 上亲手跑并贴输出。

## 7. 测试与环境

- 后端本地只跑受影响文件 + 新文件；测试库用 Docker PG（C21）。前端 `npx vitest run` 只跑本段新增与相关文件；Playwright 只跑 `e2e/arc-phase-select.spec.js`。合并门是分支 CI；合并只做 git 操作。
- 不跑真实模型；验收并进演示卡本地蒸馏。

## 8. 执行顺序

S0：Test-Path + sha256 核对本文件；逐条复核 C1–C21；本机端口 / 容器名 / 数据卷冲突检查结果写进 §9。
然后：§5 测试先在 `7d1afe12` 上跑红（贴输出）→ 实现 §3、§4 → §7 全绿 → §6 变异全红（贴结论行）→ Playwright 截图 → 推分支，报 CI。

## 9. 补充

本段改动面内新发现直接修并写进这里；需要拍板的停下报告，不自行记账。

### S0 复核（2026-10-05，基线 `origin/main = 7d1afe12`）

- `sha256` 前 16 位 `d64a86e8373676b8` ✓。本 worktree 由 `7d1afe12` 新建，分支 `feat/arc-phase-select`。
- 复核用的 worktree（`arc-phase-anchoring`@`9bfadecb`）tree `c9307e81` 与 `7d1afe12` 相同，坐标一致。
- `C1–C30` 逐条成立。两处轻微指针偏移（不改实质）：
  - C1 `_build_card_ext` def 在 `core/context_engine.py:368`（spec 写的 `:376` 是它内部 `key_memories` 注入那一行）。
  - C5 `card_from_draft` 在 `core/card_draft.py:81`（spec 那句与 `phase_anchoring.py` 并排写，S8 已正确指向 `card_draft.py`）。
- 环境（本机实测）：
  - 端口 **55432 被占用**：`character-distill-test-postgres-1`（共享 `charsim_test`，**不动**）→ 本段用一次性 PG **55433**；`55434` 是已退出的旧容器 `distill-429-test-pg`。
  - 另在跑：dev PG `5432`（`character-distill-postgres-1`）、identify PG `5433`（`distill-identify-postgres-1`）、app `7861→7860`（`character-distill-app-1`）、`jaeger-otel`。无与 `arc-phase-select` 同名的容器 / 数据卷冲突。
- 锁文件补充（§7 测试清单缺）：新增 `set_session_arc_phase`（abstract → 两后端镜像）+ `sessions` 加列，必碰
  `tests/test_storage_scope_lock.py`、`tests/test_postgres_store.py::TestPgFreshSchemaClosure`、
  `tests/test_schema_parity.py`、`tests/test_sqlite_fresh_schema.py`、`tests/test_storage_contract_shape.py`。
  本地清单已带上这 5 份。

### 偏离记录（Shiyu 已批准，2026-10-05）

两处实现侧的既有测试与本次改动冲突，按下列口径改；两处都附带「改完仍需打红原判据」的自证条件：

1. **`tests/perf/card_draft_mutations.py` 的 M9 重锚。** 本次把「记忆按位置校正后分发」抽进 `card_draft.py`
   的 `dispatch()`，M9 原来锚定的代码行（`card_from_draft` 里 `if not final: continue`）会被搬走。
   重锚到 `dispatch()` 的新位置后，**必须仍打红「无位置集合照常检索」的原判据（U7/U8 的两侧）**——
   即重锚不是换判据，只是换注入点；`card_draft_red_lines.json` 用重锚后的脚本重新生成。
2. **`tests/test_distiller_routing.py::TestFormatGroupsRunInParallel` 改写。** 不许只把
   `threading.Barrier` 的人数改小让它通过。按本次新约束重写为「除 G5 外各组并行；G5 在 G6 返回后才
   启动」，与 U20 同一判据；且**两个方向都要能打红**它：① G5 不等 G6（改回并行）、② 所有组串行。
   `_FORMAT_GROUP_MARKERS` 按新分组同步（G4 的标记从 `"key_memories"` 改为 `"psyche"`）。

### 实现期补记（2026-10-06）

**A. 变异驱动的落位（`tests/perf/` → `docs/specs/artifacts/`）。** `arc_phase_select_mutations.py` 放在
`docs/specs/artifacts/`、**不登记进覆盖闭合元锁**。判据：`tests/test_lock_coverage.py` 只 glob
`tests/perf/*_mutations.py` 与 `tests/perf/*_red_lines.json`；本段新增判别器（四份新测试 + 前端用例）
远多于 34 条变异能撞到的集合，闭合在此不可能（也不该把新测试加豁免名单）。同既有处置
`docs/specs/artifacts/mutate_profile_outbox.py`。结果：`python docs/specs/artifacts/arc_phase_select_mutations.py`
→ exit 0，**34/34 条全红**，14 个靶子文件逐字节还原。

**B. `tests/perf/card_draft_mutations.py` 重锚（承接已批准的偏离 1）。** 本次把「编号过滤 → 分发 → 落卡」
的 inline 逻辑抽进 `card_draft.py` 的 `dispatch()`（做法/记忆共用），并新增 `_row_label`/`DraftMemory`、
改非法编号 warning 文本、import 变多行、drain 循环拆两段。原 10 条锚点（M1、M2、M3、M5、M6、M9、
M13、M15、M24、M35）全部落空（`锚点命中 0 次`），逐条重锚到新代码文本。

- **M35 的转义**：它锚的是 `distiller.py` 提示词**字符串字面量内部**的文本。驱动源里必须写 `\\n`
  （双反斜杠 → 求值为反斜杠 `+ n`）；与「锚源码真换行」（写 `\n`）恰好相反。写反会把真换行写进
  `distiller.py` 的字符串字面量里 → SyntaxError 或锚点 0 命中。
- **M37 从「等价变异」重锚为「复现缺陷形态」。** 原 M37 删 `count == 0:` 直通分支。抽进 `dispatch()`
  后该分支写作 `if count == 0 or len(final) == count:`；但 `count==0` 时 `_valid_number_rows` 恒返回
  空行（`1 <= p <= 0` 无解），故 `len(final) == count` 即 `0 == 0` 已覆盖 count==0 —— 只删
  `count == 0 or ` 是**等价变异**，打不红任何判据。按「按修复前代码长什么样改」（memory
  `mutation-must-reproduce-the-defect`）改为 `if count > 0 and len(final) == count:`：count==0 落空、
  `elif final`（空）不接 → 整条被丢，正是「无阶段的卡丢掉全部做法」，打红
  `test_u5_card_without_phases_keeps_everything_top_level`（`assert len(...) == 2`）。
- 重锚后 `python tests/perf/card_draft_mutations.py` → exit 0，「全部符合预期。产物已写」；
  `card_draft_red_lines.json` 重新生成，内容（去 CR）与旧版**逐字节相同** → M9 仍打红原判据
  `assert card.situation_behaviors == []`（U4「编号全废 → 整条撤回」的两侧）。
- `tests/test_lock_coverage.py` **31 passed**：覆盖闭合仍成立。
- Windows 上 `write_artifact` 无 `newline=""` → 本机重生 CRLF；入库由 git 归一到 LF，故工作区 CRLF
  与 HEAD 的 LF 内容一致、`git diff` 无实质差异（memory `mutation-artifacts-crlf-on-windows`）。

**C. S2 测试两侧补强（写测试时发现判据不具分辨力，当场补，不改设计）。**

- `tests/test_arc_view.py::_rels()`：顶层态度 `"顶层"` 与阶段 3 态度 `"晚"` 刻意取不同值，
  `test_u17_last_phase_uses_top_attitude` 断言 `== "顶层"` —— 否则 M29（k=n 误用阶段态度）打不红。
- 新增 `tests/test_arc_positions.py::test_u15_card_with_starts_but_no_fingerprint_schedules_neither`：
  原 U15 只证「起点齐全 → 调度」，M22（另写「起点齐全」判定、漏指纹检查）无判据；新用例断言
  「起点齐全但指纹缺失 → `need_positions is False` 且不重建」。
- `test_u16b_split_scenes_start_matches_segment` 追加「起点严格递增」断言 —— 仅断言
  `text[start:start+len(seg)] == seg` 时，M21（切分后用 `text.find` 回找）在重复段落上取到**第一处**、
  内容照样相等而假绿。

**D. 已批准偏离的落地证据。** 偏离 2（并行测试改写）已落：`TestFormatGroupsRunInParallel` 用
`Barrier(len(FORMAT_GROUPS) - 1)`（G5 不参与破障），G5 判据为「提示词带 G6 定出的阶段列表」，与 U20
同口径；`_FORMAT_GROUP_MARKERS` 的 G4 标记改 `"psyche"`、key_memories 归 G6。两方向变异（所有组串行 /
G5 不等 G6）见 §6 与驱动。node_modules junction 不入库：`git ls-files web/frontend/node_modules` 为空。

**E. `_scene_items` 的指纹只在真有上界时传。** `before is None` 时不传 `source_fingerprint`：无上界时
`SessionRag` 根本不看它，而裸 `RAGEngine`（离线测评等）不认这个 kwarg，传了会 `TypeError`。故指纹
打包成条件 kwargs（`core/context_engine.py:_scene_items`）。

## 10. 自检表

| 标准 | 落点 | 状态 |
|---|---|---|
| 事实现读、带坐标、S0 复核 | §2 C1–C21（`7d1afe12`） | ✅ |
| 设计问题先定 | D1–D9 已拍板；§3.6 已定为构造时传 | ✅ |
| 路径既有机制逐个核对前提 | §2.1 | ✅ |
| 真实规模算一遍 | §2.3（重建片数、调用数、token、费用；耗时未实测已注明） | ✅ |
| 样本前提写明范围 | C6、C8 | ✅ |
| 出处表（链接 + 章节 + 性质） | §2.4 | ✅ |
| 全量扫描原始输出 | 附录 A | ✅ |
| 规模表含上限来源与策略 | §2.3 | ✅ |
| 调用点矩阵（含前端、开场白、导出） | §5.2 | ✅ |
| 路径机制表 | §2.2 | ✅ |
| 两侧测试、两方向变异 | §5、§6 | ✅ |
| 变异发出前预跑 | 纯计算层 20 条全红（§6.1 两批）；其余 13 条实现后跑，已注明 | ⚠️ 部分 |
| 与调研结论一致 | DREAM 两层人设 + 时间截断检索（D3、D4、D10、D11）；RoleKE-Bench 按角色生成示范且避开被测内容（D12）；ArcANE 隐藏之后阶段（D2）；Persistent Personas 放 system（D2）；越狱文献（D6） | ✅ |
| 能自动验的不让 Shiyu 手动 | Playwright 断言 + 两张截图只看外观 | ✅ |
| 复用、隔离、单一出口 | 投影只在 `ChatEngine` 构造一处，既有消费方不改；阶段取舍只在 `arc_view`；过滤只在 `_fetch_candidates`；适用性只在 `SessionRag`；补位置调度只在 `save_distilled_card` 并复用 `schedule_text_reindex`；分发函数、摘录提示词片段、按卡偏好、请求体、身份赋值、重建恢复、指纹各一份，均有结构锁（S1–S11） | ✅ |
| 不打补丁 | 消除 C16、C19、C23 的既有重复；补位置放在本来就有原文的汇合点；位置在切分时产生，不事后回找；调度与上界共用一个判定 | ✅ |
| 行为变化写明 | D9：所有会话开始注入弧线（Shiyu 已确认） | ✅ |
| 先找现成方案 | Chroma 原生 `where`、原生单选、已有 `schedule_text_reindex`；未引入新库 | ✅ |
| skill 不过度 | 执行方：`@search-first`、`@verification-before-completion` | ✅ |

## 附录 A：全量扫描原始输出（`7d1afe12`）

```
$ git grep -n -E "_create_session[,(]" -- core web
core/text_manager.py:549:                self._create_session, card,
core/text_manager.py:616:            self._create_session, card,
core/text_manager.py:666:    def _create_session(
web/routers/chat.py:199:        text_manager._create_session, card,
web/routers/distill.py:1388:            text_manager._create_session, card,
web/routers/distill.py:1405:            text_manager._create_session, card,
web/routers/history.py:236:                text_manager._create_session,

$ git grep -n -E "\.save_session\(|get_session_owned\(|list_sessions\(" -- core web storage
core/text_manager.py:560:            await self._storage.save_session(session_id, card_id, "", "", user_id)
core/text_manager.py:622:        await self._storage.save_session(session_id, actual_card_id, "", "", user_id)
storage/base.py:416:    async def get_session_owned(self, id: str, user_id: str) -> dict | None:
storage/base.py:445:    async def list_sessions(
storage/postgres_store.py:1391:            return await self.get_session_owned(id, user_id) or {}
storage/postgres_store.py:1413:    async def get_session_owned(self, id: str, user_id: str) -> dict | None:
storage/postgres_store.py:1456:    async def list_sessions(self, keyword: str, character: str, text_id: str, page: int, page_size: int, user_id: str = "", card_id: str = "") -> dict:
storage/sqlite_store.py:1914:            return await self.get_session_owned(id, user_id) or {}
storage/sqlite_store.py:1938:    async def get_session_owned(self, id: str, user_id: str) -> dict | None:
storage/sqlite_store.py:1985:    async def list_sessions(
web/routers/chat.py:151:    db_session = await storage.get_session_owned(session_id, user_id)
web/routers/chat.py:320:                db_s = await storage.get_session_owned(session_id, user_id)
web/routers/chat.py:322:                    await storage.save_session(
web/routers/chat.py:466:                db_s = await storage.get_session_owned(session_id, user_id)
web/routers/chat.py:468:                    await storage.save_session(
web/routers/chat.py:770:    db_session = await storage.get_session_owned(session_id, user["id"])
web/routers/chat.py:822:    db_session = await storage.get_session_owned(session_id, user["id"])
web/routers/distill.py:1413:        await storage.save_session(session_id, req.card_id, req.user_role, user.get("avatar_data", ""), user_id)
web/routers/history.py:43:async def list_sessions(
web/routers/history.py:56:    return await storage.list_sessions(keyword, character, text_id, page, page_size, user_id, card_id)
web/routers/history.py:107:    session = await storage.get_session_owned(session_id, user["id"])
web/routers/history.py:133:    session = await storage.get_session_owned(session_id, user["id"])
web/routers/history.py:188:    db_session = await storage.get_session_owned(session_id, user_id)

$ git grep -n -E "engine\.user_role\s*=" -- core web
web/routers/chat.py:221:        engine.user_role = db_session["user_role"]
web/routers/history.py:276:        engine.user_role = db_session["user_role"]

$ git grep -n -E "query_with_emotion(_ex)?\(|_fetch_candidates\(|collection\.query\(" -- core
core/context_engine.py:492:        hits = self.rag.query_with_emotion_ex(
core/indexing_service.py:135:    def query_with_emotion_ex(self, query_text: str, **kwargs: Any) -> EvidenceHits:
core/indexing_service.py:142:            return self._engine.query_with_emotion_ex(query_text, **kwargs)
core/rag.py:351:        cand = self._fetch_candidates(
core/rag.py:359:    def _fetch_candidates(
core/rag.py:384:                results = self.collection.query(
core/rag.py:444:        cand = self._fetch_candidates(
core/rag.py:497:    def query_with_emotion(
core/rag.py:522:    def query_with_emotion_ex(

$ git grep -n "key_memories" -- core web/routers web/frontend/src ":!*__tests__*"
core/card_quotes.py:30:    "key_memories[]",
core/context_engine.py:375:        if c.key_memories:
core/context_engine.py:376:            memories = "\n".join(f"- {m}" for m in c.key_memories)
core/distiller.py:240:    ("key_memories", '  "key_memories": ["关键经历1（原文出处）", "关键经历2（原文出处）"]'),
core/export.py:27:    if card.key_memories:
core/export.py:28:        personality_lines.append("关键记忆：" + "；".join(card.key_memories))
core/schema.py:132:    key_memories: list[str] = []      # 3-5个关键经历
core/schema.py:161:    "G4": ("key_memories", "psyche"),
web/frontend/src/components/CharCard.jsx:798:        {data.key_memories?.length > 0 && (
web/frontend/src/components/CharCard.jsx:801:              {data.key_memories.map((m, i) => (
web/frontend/src/components/EditCardModal.jsx:23:  key_memories: { max: 800, maxLines: 8, perLine: 80 },
web/frontend/src/components/EditCardModal.jsx:75:      key_memories: splitLines(data.key_memories),
web/frontend/src/components/EditCardModal.jsx:99:    key_memories: '关键记忆',
web/frontend/src/components/EditCardModal.jsx:110:      { field: 'key_memories', lines: form.key_memories.split('\n').filter(Boolean) },
web/frontend/src/components/EditCardModal.jsx:147:      key_memories: joinLines(form.key_memories),
web/frontend/src/components/EditCardModal.jsx:221:          <Field label="关键记忆（每行一条）" mono field="key_memories" value={form.key_memories}>
web/frontend/src/components/EditCardModal.jsx:222:            <textarea className="modal-textarea" rows={3} value={form.key_memories} onChange={(e) => update('key_memories', e.target.value)} maxLength={LIMITS.key_memories.max} />
web/frontend/src/components/MarketCardDetail.jsx:713:              {cardData.key_memories?.length > 0 && (
web/frontend/src/components/MarketCardDetail.jsx:717:                    {cardData.key_memories.map((m, i) => (

$ git grep -n "character_arc" -- web/frontend/src ":!*__tests__*"
web/frontend/src/components/CharCard.jsx:835:        {data.character_arc?.phases?.length > 0 && (
web/frontend/src/components/CharCard.jsx:837:            <ArcList arc={data.character_arc} />
web/frontend/src/components/EditCardModal.jsx:81:      arc_axis: data.character_arc?.axis || '',
web/frontend/src/components/EditCardModal.jsx:88:    setArcPhases(withRowKeys(data.character_arc?.phases).map((p) => ({ ...p, behaviors: withRowKeys(p.behaviors) })))
web/frontend/src/components/EditCardModal.jsx:153:      character_arc: {
web/frontend/src/components/MarketCardDetail.jsx:750:              {cardData.character_arc?.phases?.length > 0 && (
web/frontend/src/components/MarketCardDetail.jsx:753:                  <ArcList arc={cardData.character_arc} />
web/frontend/src/utils/card.js:9:// character_arc 的旧形态是字符串数组（每阶段一句）；现在是 { axis, phases: [{ label, state, behaviors }] }。
web/frontend/src/utils/card.js:31:    const arc = normalizeArc(out.character_arc)
web/frontend/src/utils/card.js:32:    if (arc !== out.character_arc) out = { ...out, character_arc: arc }
web/frontend/src/utils/card.test.js:38:    const c = parseCardJson({ card_json: { name: 'A', character_arc: ['起初冷漠', '学会信任'] } })
web/frontend/src/utils/card.test.js:39:    expect(c.character_arc).toEqual({
web/frontend/src/utils/card.test.js:47:    const cardJson = { name: 'A', character_arc: arc }
web/frontend/src/utils/card.test.js:50:    expect(c.character_arc).toBe(arc)
web/frontend/src/utils/card.test.js:57:    expect('character_arc' in c).toBe(false)
web/frontend/src/utils/card.test.js:61:    const card = { card_json: { character_arc: ['a'] } }
web/frontend/src/utils/card.test.js:63:    expect(card.card_json.character_arc).toEqual(['a'])

$ git grep -n -E "start_session|sessionUserRole:" -- web/frontend/src/store/useAppStore.js
web/frontend/src/store/useAppStore.js:342:  sessionUserRole: '',
web/frontend/src/store/useAppStore.js:343:  setSessionUserRole: (role) => set({ sessionUserRole: role }),
web/frontend/src/store/useAppStore.js:1111:  // AbortController for in-flight start_session requests
web/frontend/src/store/useAppStore.js:1170:        const result = await postJSON('/api/distill/start_session', {
web/frontend/src/store/useAppStore.js:1184:    set({ _pendingChatCardId: null, sessionId, resumeLoading: false, sessionUserRole: get().getUserRole(_cardId) })
web/frontend/src/store/useAppStore.js:1255:        const result = await postJSON('/api/distill/start_session', {
web/frontend/src/store/useAppStore.js:1291:      sessionUserRole: get().getUserRole(cardId),
web/frontend/src/store/useAppStore.js:1325:      sessionUserRole: session.user_role || get().getUserRole(session.card_id),
web/frontend/src/store/useAppStore.js:1364:      const result = await postJSON('/api/distill/start_session', {
web/frontend/src/store/useAppStore.js:1383:        sessionUserRole: get().getUserRole(cardId),
web/frontend/src/store/useAppStore.js:1718:        sessionUserRole: session.user_role || get().getUserRole(session.card_id),

$ grep -n -E "^\.(user-role-bar|user-role-label|user-role-locked|user-role-presets|role-setup-card|role-setup-hint|card-arc-list|card-arc-item|card-arc-index)" web/frontend/src/styles/global.css
3849:.card-arc-list {
3856:.card-arc-item {
3936:.card-arc-index {
4491:.user-role-bar {
4500:.user-role-label {
4530:.user-role-locked {
7548:.role-setup-card {
7552:.role-setup-hint {
7559:.role-setup-hint strong {
8121:.user-role-presets {
```
