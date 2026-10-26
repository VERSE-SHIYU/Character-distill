# recall-r2-rerank：候选池 50 + qwen3-rerank 重排（召回升级第 2 轮）

- 基线：main `a9b830ed`。**今晚从 main `a9b830ed` 拉分支 `feat/recall-r2-rerank`，只改产品代码与它的测试**；**不碰 0d 的文件**（`scripts/eval_recall.py`、`scripts/recall_questions.py`、`core/recall_eval.py`、`core/recall_synth.py`），这样 #208 复核后怎么改都不影响本分支。评测脚本的改动放到明早 #208 合并后、rebase 到 main 再加。
- **基线变了怎么办**：每轮量分都写明用的题库文件哈希与 0e 基线证据 id；题库或基线一旦变动，本轮用同一题库重跑一次评测（一条命令、只花查询嵌入钱），不改代码。
- **与 0d（#208）的重叠**：0d 改了 `core/indexing_service.py`（`_scene_index_job` 改名 `build_scene_index`）、`core/scene_indexer.py:81`（docstring）、`core/quotes.py`（加 `char_bigrams`）。本分支若改到这几个文件，rebase 时是不同行的文本冲突，照 0d 的版本保留改名即可。
- 修：D4（0.7×语义 + 0.3×情绪，语义按本批最大距离归一化被压到 ≤0.07，排序由关键词情绪决定）+ D5（候选只有 3×4=12 条）。见共享文档「0c 整条链路表」④⑤。
- 不碰：切块（第 1 轮，与本轮并行，见 §6）、检索词（第 3 轮）、角色标签（第 5 轮）。
- **Shiyu 10-26 16:45 定**：重排出错退回向量顺序（A）；候选池默认 50，0e 量向量检索在前 30 / 前 50 的候选召回再最终确认。

## 0. 环境硬规则
同 0d：不动 Docker / WSL / 容器；退出码非 0/1 即停；不跑全量；SG 只在 `/tmp/recall_pkg` 跑本分支副本，不写 `/app`；评测集合用 `eval_` 前缀；**不合并**。

## 1. 目标与目标检查（第一个提交，xfail strict）

**目标**：场景检索 = 向量取前 `RERANK_POOL`（50）条候选 → qwen3-rerank 打分 → 取前 3；重排接口出错时退回向量顺序、聊天照常。在 0d 题库上 R@3 高于基线才合并。

目标检查（`tests/test_scene_rerank_goal.py`，重排用替身，替身对空文档列表、超时、429 的行为照生产 —— 返工经验 35；候选文本带省略号、中文引号、换行 —— 返工经验 54）：
1. 交给重排的候选 ≤ `RERANK_POOL`，带角色过滤时也一样（`keep = keep[:want]`）；不足时如实少给。
2. 前 3 = 重排分数最高的 3 条（按 `output.results[].index` 映射回候选），与向量顺序无关。
3. 重排超时 / 429 / 5xx：返回向量顺序前 3，`EvidenceHits` 带 `rerank_fallback=True`，记一条 WARNING（写明超时 / 限流 / 其他、集合名）；不抛到聊天。
4. 重排 4xx（非 429，如 401 key 无效）：同 3 退回，WARNING 写明状态码（不让用户回复失败；key 无效另由「没配 key」入口拦）。
4b. 有 `embed_deadline` 时：重排总耗时不超过剩余预算；预算已不够发一次 → 不发，直接退回向量顺序（`rerank_fallback=True`、WARNING 写「deadline」）。用假时钟 / 慢替身测，不真睡。
5. 重排模块不读「当前用户」：key / region 只来自构造它的 `RAGEngine`（与嵌入同一来源）；MCP 链路（`mcp_server/server.py:127` 起，无登录用户）照常可用。
6. 情绪不再参与排序：`_rank_with_emotion`、`_EMOTION_KEYWORDS`、`_detect_emotion` 删除（S0 grep 确认无其他使用者）；`scene_hits` 不再传情绪。
7. `SceneMeta` 字段改为 `rerank_score`、`vector_rank`（契约测试 `tests/test_rag_evidence_contract.py` 有意重写，提交说明写明）。

## 2. 已查实的事实（对目标的影响）

| # | 事实 | 坐标 | 影响 |
|---|---|---|---|
| F1 | 打分唯一实现 `_rank_with_emotion`：`final = 0.7·semantic + 0.3·emotion_affinity`，semantic 按本批 max_dist 归一化 | `core/rag.py:505-560`；`core/schema.py:382-394` | 整体删除，由重排替代 |
| F2 | 候选：`want = max(need, 1)`；有角色过滤时 `n = want × CHARACTER_FILTER_MULTIPLIER(4)`，过滤后不足再 ×4 重取 1 次；`_take` 返回**全部** keep | `core/rag.py:114-115`、`:450-500` | 不截断时最多远超 want → 加 `keep = keep[:want]` |
| F3 | 唯一调用链：`scene_hits` → `SessionRag.query_with_emotion_ex` → `RAGEngine.query_with_emotion_ex`；`scene_hits` 传 `_detect_emotion(query)` | `core/scene_retrieval.py:10,24-29`；`core/indexing_service.py:158`；`core/rag.py:610` | 改名为 `query_scenes`（去掉 emotion），三处一起改 |
| F4 | `_detect_emotion` 的唯一外部使用者是 `scene_retrieval.py:10`；scene_indexer 内部写 `emotion` metadata | grep 输出（S0 复跑） | 删排序后 `emotion` metadata 无人读 → 一并删 |
| F5 | 嵌入客户端：`OpenAI(api_key, base_url, timeout=8.0, max_retries=2)`；地域 → 主机 `DASHSCOPE_BASE_URLS` | `core/embeddings.py:166-172`、`:230-232` | 重排用同一组超时 / 重试数字；主机从同一张表取（去掉 `/compatible-mode/v1` 换成重排路径） |
| F6 | 重排老地址 `https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank` + `qwen3-rerank`：HTTP 200，返回 `output.results[]` 每项 `index` + `relevance_score`，用量 `usage.total_tokens` | Shiyu 10-25 实测（共享文档 S0 核实 ①） | cn 区已实测；**intl 区未测**，S0 用 intl key 实测一次，不通就停 |
| F7 | 重排限制：单次 ≤500 文档、每文档 ≤4,000 token、总 ≤12 万 token | 官方文档（共享文档「未核实」节） | 50 块 × 约 300–512 token 在限内 |
| F8 | 重试判定的现成实现 `_status_code` / `_is_deterministic_failure`（408/409/429/5xx 与连接错误可重试） | `adapters/llm_adapter.py:35-64` | 是为 openai 异常写的；能否用于 httpx 异常 **S0 核实**，不能就照它的状态码集合写一处，不另定 |
| F9 | 日志级别：ERROR 发邮件、WARNING 只上面板 | `AGENTS.md:1626` | 退回用 WARNING |
| F11 | **聊天路径上已有截止时刻机制**：agent 工具在 worker 线程里开 `embed_deadline(deadline)`（`core/agent/tools.py:30-41`，deadline = 工具 timeout − margin）；嵌入 `_call_api` 读它，有 deadline 时每次尝试 `timeout = min(8.0, 剩余 − margin)`、≤3 次、剩余不够就拒发（`core/embeddings.py:51-58`、`:248-262`）；没有 deadline（非工具路径）才走 `timeout=8.0, max_retries=2` | 见左 | **重排加在同一条路径上，必须读同一个 deadline**：否则重排最坏 8 s × 3 = 24 s，压在聊天回复上，还会越过工具的总时限。按返工经验 11：重排与查询嵌入共用一份剩余预算 |
| F10 | `RAGEngine` 层拿不到用户 id（只有集合名，含 card_id） | `core/rag.py` 构造参数 | WARNING 写集合名；**不为写日志把用户 id 下传**（会让检索层知道身份） |

## 3. 设计

| 块 | 位置 | 职责（只此一处） |
|---|---|---|
| 重排 | 新增 `core/reranker.py`：`rerank(query, docs, *, api_key, region) -> list[tuple[int, float]]`；HTTP 用已有依赖 `httpx==0.28.1`；**截止时刻照嵌入的 D2 做法**：读 `current_embed_deadline()`，有则每次尝试 `timeout = min(8.0, 剩余 − margin)`、≤3 次、剩余不够直接放弃；无则 `timeout=8.0`、≤3 次（F5、F8、F11）。能直接复用 `core/embeddings.py` 的常量 / 判定函数就复用，S0 给结论，不另定数字；失败抛 `RerankUnavailable(kind, status)` | 唯一调用重排接口的地方 |
| 候选池 | `core/rag.py` 新增常量 `RERANK_POOL = 50`；作为 `need` 传给 `_fetch_candidates`；`_fetch_candidates` 末尾 `keep = keep[:want]` | `top_k=3` 只用于重排后截取，两个数各管一件 |
| 排序 | `RAGEngine.query_scenes(query, character_name, top_k, window)`：取候选 → `rerank` → 取前 top_k；`RerankUnavailable` → 向量顺序前 top_k + `rerank_fallback=True` + WARNING | 替代 `_rank_with_emotion` |
| key | `RAGEngine` 构造时已有的 `embedding_key` / `embedding_region` 传给 `rerank` | F10、目标检查 5 |
| 评测（明早，#208 合并并 rebase 后） | `eval_recall.py` 汇总里加 `rerank_fallbacks` 计数 | 一次限流不会悄悄拉低分数 |

## 4. 步骤（每步一个提交）
- S0：复核 §2 坐标；grep `_detect_emotion` / `_EMOTION_KEYWORDS` / `query_with_emotion_ex` 全部使用者（含 tests）；**列出所有替身（`MagicMock` / 手写假 rag / 假 SessionRag）是否配了改名后的 `query_scenes`、行为是否有终点（返工经验 40）**，有疑问的测试标 ☁、不在本地跑；F8、F11 能否复用的结论；intl 区老地址实测明早做。
- S1：目标检查 xfail。
- S2：`core/reranker.py` + 候选池 + 排序 + 删情绪 + 改名；目标检查绿；受影响测试按「哪些用例会走到 `query_with_emotion_ex` / `_rank_with_emotion` / `SceneMeta`」列出后只跑这些（返工经验 55）。
- S3：开 PR，等 CI。
- S4：0e 基线出来后，SG 上用 0d 题库跑本分支：报 R@3（总体 / 按书 / 按角色）、越界率（祈灼）、`rerank_fallbacks`、每次重排延迟（中位 / p95）、重排 token。R@3 不高于基线 → 停，报 Shiyu。

## 5. 规模
- 每次聊天检索多 1 次重排调用：50 块 × 约 300 token ≈ 1.5 万 token，约 $0.001–0.002（qwen3-rerank 国际区 $0.1 / 百万 token，共享文档记录）。
- 评测：约 220 条 × 1 次重排 ≈ 330 万 token ≈ $0.3（估）。

## 6. 与第 1 轮并行的冲突
- 第 1 轮改 `core/scene_indexer.py` 的切块与 `core/rag.py` 的 `chunk_spans`；本轮改 `core/rag.py` 的 `_fetch_candidates` / 排序、删 `scene_indexer.py` 的情绪表与 `emotion` metadata。**同文件不同函数**；后合并的一方 rebase 时解冲突，冲突点：`scene_indexer.py` 入库循环里的 `emotion` 一行（第 1 轮改写入库、本轮删 emotion）。
- 两轮各自对 0e 基线量；合并顺序 1 → 2，合并后整体再量一次。

## 7. 补充
（审计发现写这里。）
