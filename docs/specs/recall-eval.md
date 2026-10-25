# recall-eval：召回评测集与基线（召回升级第 0 步 · 段 0a）

- 基线：main `08a0fb15`（2026-10-25 读；之后只合并了 #204，只改测试）。基线 tag：`recall-baseline-2026-10` → `401b570a`。
- 共享文档：召回做法调研 · 2026 →「已定执行顺序」「标注口径（10-25 15:52）」。
- 分两段，一段一个 PR：
  - **0a（本 spec）**：评测工具 + 一处小重构 + 标注模板。不碰真数据、不跑真检索。
  - **0b（另给提示词）**：Shiyu 按模板标注祈灼的查询 → 在服务器上跑一次 → 记下基线数字。

## 1. 目标与目标检查

**目标**：有一个评测工具，给它「查询 + 标好的原文片段」，它用**和聊天完全相同的那次检索调用**取回前 3 段，算出两个数：

- **R@3**：前 3 段里命中了「该找回的片段」的查询占比；
- **越界率**：前 3 段里出现了「他不该知道的片段」的查询占比（口径：他没参与就不知道——Shiyu 10-25 定）。

以后每个召回 PR（重排、查询双路、混合候选、角色事件入库）只改检索内部，评测工具不用改就能量出新数字。

**目标检查**（第一个提交，`xfail(strict=True)`）`tests/test_recall_eval_goal.py`：

1. **同一次调用**：把 `core/scene_retrieval.scene_hits` 替换成记录参数的替身后，`ContextEngine._scene_items` 与评测工具发出的调用参数完全相同（`top_k`、`character_name`、`window`、`current_emotion`）——以后改检索只改一处，评测自动跟上。
2. **指标算得对**（手写小原文 + 手写命中，离线）：命中段与「该找回片段」在原文里有重叠 → 算命中；命中段文本被截断（只含片段前半）但位置重叠 → 仍算命中；与「不该知道片段」重叠 → 计入越界；都不重叠 → 两项都不计。
3. **标注先自检**：片段在原文里找不到、出现不止一处、短于 10 字、或「该找回片段」落在所选阶段之后 → 评测在检索前报错并列出是哪一条，**不发任何嵌入调用**。

## 2. 已查实的事实

| # | 事实 | 坐标 |
|---|---|---|
| F1 | 聊天里取原文场景的唯一调用：`self.rag.query_with_emotion_ex(query, current_emotion=_detect_emotion(query), character_name=self.card.name, top_k=3, window=self.arc_view.window if self.arc_view is not None else None)` | `core/context_engine.py:548-559` |
| F2 | 阶段视图由 `arc_view(card, arc_phase)` 算出，`window` 来自 `phases[k].start` | `core/arc_view.py:84-99` |
| F3 | 命中的 `EvidenceItem` 只带 `text`（场景前 800 字）和 `meta.chunk_id`，**不带原文位置**；`meta.chapter` 恒 None | `core/schema.py:382-396`、`:439-455`；`core/rag.py:610-622`；`core/scene_indexer.py:133`（`docs.append(scene[:800])`） |
| F4 | 原文规范化与定位已有现成函数：`normalize`、`locate_in_normalized(source_norm, quote) -> list[int] \| None`；片段最短 10 字的下限已有常量 `MIN_ELIDED_FRAGMENT_LEN = 10` | `core/quotes.py:59-74`、`:87` |
| F5 | 取会话检索的现成入口：`IndexingService.get_rag_for_session(text_id, card_id=..., embedding_key=..., embedding_region=...)`，返回的对象有 `query_with_emotion_ex` | `core/indexing_service.py:135-147`；调用例 `web/routers/chat.py:195-197` |
| F6 | 探针数字的唯一落点：`tests/perf/evidence_writer.write_evidence(id, payload, claim=, script=, env=, code_sha=)` → `docs/evidence/<id>.json` 并登记清单；正文引用写 `ev:<id>` | `tests/perf/evidence_writer.py:1-40`、`:112`；`.claude/rules/project.md`「调试脚本不入 main」条 |
| F7 | 在服务器上跑只读脚本的先例：`scripts/` 不进镜像，`docker cp` 进容器后 `cd /app && PYTHONPATH=/app python /tmp/<脚本>.py`；按 `--user-id` 用该用户自己的配置 | `docs/specs/replay-distill.md:63`（K7）；`scripts/replay_distill.py:627-661` |
| F8 | 现有评测样例格式是 JSONL，放在 `tests/eval/` | `tests/eval/agent_eval_cases.jsonl` |
| F9 | 「prompt 侧产出逐字节不变」由 `tests/test_rag_evidence_contract.py` 锁着；本段的重构不能改它 | `core/rag.py:598-604` 的 docstring |

**未核实**：祈灼那张卡在 SG 还是 SZ（0b 才需要，Shiyu 知道）；卡的 `card_id` / `text_id`（0b 由 Shiyu 给）。

## 3. 设计

按职责分四块，各管一件事：

| 块 | 文件 | 职责 | 依赖 |
|---|---|---|---|
| 检索调用（唯一一处） | **新增 `core/scene_retrieval.py`** | `scene_hits(rag, query, *, character_name, window) -> EvidenceHits`：把 F1 那次调用原样搬进来（含 `top_k=3`、`_detect_emotion`） | `core.scene_indexer._detect_emotion` |
| 聊天 | `core/context_engine.py` | `_scene_items` 改为调 `scene_hits(...)`；`rag is None` 的判断留在原处 | `scene_retrieval` |
| 指标（纯函数，零 IO） | **新增 `core/recall_eval.py`** | 读标注、在原文里定位片段、按位置判命中、算 R@3 与越界率 | `core.quotes`（复用 F4，不另写定位） |
| 命令行（薄壳） | **新增 `scripts/eval_recall.py`** | 读卡和原文 → 取会话检索（F5）→ 对每条查询调 `scene_hits` → 交给 `recall_eval` → `write_evidence`（F6） | 以上三者 + storage |

要点：

- **为什么要抽 `scene_hits`**：评测如果自己再写一遍那次调用，参数一变（PR 3 去掉情绪、PR 4 加原句双路）两边就会对不上，量的就不是聊天实际在用的东西。抽出来后，`ContextEngine` 和评测调同一个函数，之后几个召回 PR 只改这一处。
- **按位置判命中，不按段 id**（F3）：标注写的是原文片段，命中段只有文本没有位置，所以两边都用 `locate_in_normalized` 换算成原文位置，**区间有重叠就算命中**。这样不依赖场景怎么切——以后重切场景（选项 B）、换成按事件入库，标注都不用重做。命中段文本在原文里出现不止一处时，取所有位置，任一重叠即算。
- **标注先自检**（目标检查 3）：在任何检索之前跑完，错了就停，不花一次嵌入调用。
- **评测脚本只读**：不写库、不建会话、不调大模型；每条查询只花一次查询嵌入（50 条合计远低于 ¥0.01）。用 `--user-id` 读该用户自己的百炼 key；没有 key 就以 `EMBEDDING_KEY_REQUIRED` 退出（`core/embeddings.py:432`，复用）。

**标注文件**：`tests/eval/recall/<名字>.jsonl`，一行一条：

```json
{"id": "q01", "phase": 3, "query": "你还记得满月宴那天吗",
 "should": ["原文里能代表这段的一句话，至少10字"],
 "should_not": ["他没参与的那件事里的一句话，至少10字"],
 "note": "可选，给自己看的备注"}
```

- `phase`：聊天时选的阶段（决定 `window`）；`should` 可多条，命中任一即算；`should_not` 可为空；每条片段 10–40 字、原文里唯一。
- 0a 只交付模板文件 `tests/eval/recall/TEMPLATE.jsonl`（3 行示例，示例用假设内容并注明）和填写说明 `tests/eval/recall/README.md`（含口径原文：「他没参与就不应该知道；亲历、目击、被当面告知、自己偷听到、自己读到的 → 知道；背着他发生、只被别人提到的 → 不知道」）。

**结果格式**（`write_evidence` 的 payload）：`{"r_at_3": float, "leak_rate": float, "n": int, "per_query": [{"id", "hit", "leak", "top3_chunk_ids"}]}`；`claim` 写「祈灼 R@3 x / 越界率 y（n=…，基线 tag recall-baseline-2026-10）」。

**以后改几处**：换检索做法 → `scene_retrieval.py` 一处；改指标定义 → `recall_eval.py` 一处；换一本书 / 一个角色 → 新加一个标注文件，代码不改。

## 4. 调用点矩阵

| 调用点 | 改前 | 改后 | 测试 |
|---|---|---|---|
| `ContextEngine._scene_items`（`context_engine.py:548-559`） | 直接调 `rag.query_with_emotion_ex` | 调 `scene_hits` | 目标检查 1；`test_rag_evidence_contract.py`、`test_context_engine_evidence.py` 保持绿 |
| `scripts/eval_recall.py`（新） | — | 调 `scene_hits` | 目标检查 1 |

## 5. 结构自检

| 问题 | 回答 |
|---|---|
| 同一条规则写了几处 | 「聊天怎么取场景」1 处（`scene_hits`）；「片段怎么定位」1 处（`core.quotes`，复用）；「指标怎么算」1 处（`recall_eval`） |
| 调用方是否被迫知道不该知道的事 | 评测脚本不知道 `top_k`、情绪、窗口怎么算；`recall_eval` 不知道数据库和检索 |
| 加一种情况要改几处 | 加一本书：加一个标注文件；改检索：改 `scene_hits` |

## 6. S0（开工前逐条复核，有一条不成立就停）

1. `git rev-parse HEAD` 为 `401b570a` 或其后代；`git diff 08a0fb15 -- core/context_engine.py core/arc_view.py core/schema.py core/rag.py core/quotes.py core/indexing_service.py core/scene_indexer.py` 为空。
2. 逐条核对 §2 的 F1–F9，贴出实际读到的行。
3. 确认 `tests/test_rag_evidence_contract.py` 与 `tests/test_context_engine_evidence.py` 在改动前是绿的（只跑这几个）。

## 7. 测试与执行

- 本地只跑：`tests/test_recall_eval_goal.py`、`tests/test_rag_evidence_contract.py`、`tests/test_context_engine_evidence.py`；不跑全量，合并门是分支 CI。
- 提交顺序：① 目标检查（红，`xfail(strict=True)`）；② `scene_retrieval.py` + `context_engine.py` 改调（契约测试保持绿）；③ `recall_eval.py` + `scripts/eval_recall.py`（目标检查转绿）；④ 标注模板与说明。
- `scripts/eval_recall.py` 在 0a 里**不跑真数据**；它的取数部分用替身测（替身行为照生产：没有 key 时报同一句提示）。
- Skill：纯后端，不需要文档、前端类 skill；测试或审查类 skill 每阶段最多一个。
- 目标外发现写进报告，不并进本 PR。

## 8. 不做

- 不改检索内部（情绪权重、`top_k`、候选数都原样搬，留给后续 PR）。
- 不做记忆路评测（记忆路已定暂缓）。
- 不调大模型，不做自动标注。
- 0a 不跑服务器、不产出基线数字（0b 做）。

## 发出前判断清单

| 判断 | 依据 |
|---|---|
| 聊天取场景只有 `_scene_items` 这一处调用 | 读过 `context_engine.py:548-559`；`grep -n "query_with_emotion_ex"` 在 `core/` 下只有 `context_engine.py:552` 一个调用方（另两处是定义 / 透传：`indexing_service.py:100`、`:144`） |
| 命中不带位置 | F3 读过的四处 |
| 定位函数与 10 字下限可复用 | F4 读过 `quotes.py:59-74`、`:87` |
| 服务器跑法 | F7 读过 replay-distill spec K7 与脚本参数 |
| 50 条查询花费远低于 ¥0.01 | 推算（每条一次查询嵌入，几十 token）；**未实测** |
| `scene_hits` 抽出后 prompt 字节不变 | 原样搬参数，由 F9 的契约测试守住；**以 S0-3 与提交 ② 的测试结果为准** |

## 补充（10-25 审计）

**问题**：`core/recall_eval.py` 的 `_intervals` 复用 `locate_in_normalized` 定位。该函数
按省略号切段后只返回**最长一段**的起点（`core/quotes.py:87-100`），调用方却按**整段文本**
的长度算区间宽度 —— 命中段含「……」时区间被拉长，跨过中间无关段落，判成假命中
（越界率也随之虚高）。

**根因**：§2 F4 指定「复用 `locate_in_normalized`，不另写定位」时**没有读函数体**，
只看了函数名与签名。该函数是为「省略号拼接核对」设计的（各段都在即通过、位置取最长段），
而「按位置判命中」需要的是**连续原文**的区间 —— 两者语义不同。

**修法**：`_intervals` 改为 `n = normalize(text)` → 在 `source_norm` 上
`re.finditer(re.escape(n))` 取全部起点，区间 `(p, p + len(n))`；`n` 为空返回 `[]`。
`validate_cases` 也走 `_intervals`（找不到 / 不唯一 / 落在阶段之后同一条定位规则只此一处）；
标注片段含省略号（`……` 或 `...`）直接报错。`score_case` 每行加 `unlocated`
（前 3 段中在原文定位不到的段数），`aggregate` 不变。

**更正 §2 坐标**：

- F4：`normalize` 在 `core/quotes.py:43-51`（原写 `:59-74`，那是 `normalized_starts`）；
  `locate_in_normalized` 在 `:87`；`MIN_ELIDED_FRAGMENT_LEN` 在 `:74`。
- F5：`IndexingService.get_rag_for_session` 在 `core/indexing_service.py:207-225`
  （原写 `:135-147`，那是 `SessionRag.query_with_emotion_ex`）；调用例在
  `web/routers/chat.py:185-187`（原写 `:195-197`）。
