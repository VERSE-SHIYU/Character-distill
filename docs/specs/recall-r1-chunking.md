# recall-r1-chunking：场景切块存全文（召回升级第 1 轮）

- 基线：main `a9b830ed`。**今晚从 main `a9b830ed` 拉分支 `feat/recall-r1-chunking`，只改产品代码与它的测试**；**不碰 0d 的文件**（`scripts/eval_recall.py`、`scripts/recall_questions.py`、`core/recall_eval.py`、`core/recall_synth.py`），这样 #208 复核后怎么改都不影响本分支。评测脚本的改动放到明早 #208 合并后、rebase 到 main 再加。
- **基线变了怎么办**：每轮量分都写明用的题库文件哈希与 0e 基线证据 id；题库或基线一旦变动，本轮用同一题库重跑一次评测（一条命令、只花查询嵌入钱），不改代码。
- **与 0d（#208）的重叠**：0d 改了 `core/indexing_service.py`（`_scene_index_job` 改名 `build_scene_index`）、`core/scene_indexer.py:81`（docstring）、`core/quotes.py`（加 `char_bigrams`）。本分支若改到这几个文件，rebase 时是不同行的文本冲突，照 0d 的版本保留改名即可。
- 修：D2（场景只存前 800 字）+ D3（只按空行切，网文单换行切不开）。见共享文档「0c 整条链路表」①②⑥。
- 本 PR 不碰：打分 / 候选数（第 2 轮）、检索词（第 3 轮）、角色标签（第 5 轮）、聊天记录切法 `_split_chat_scenes`。

## 0. 环境硬规则
同 0d：不动 Docker / WSL / 容器；退出码非 0/1 即停；不跑全量；SG 只在 `/tmp/recall_pkg` 跑本分支副本，不写 `/app`；**评测用的变体集合一律用 `eval_` 前缀的集合名，不重建任何线上 `scenes_{card_id}`**。

## 1. 目标与目标检查（第一个提交，xfail strict）

**目标**：场景集合每块存**完整文本**和起止位置，块按段落边界攒到预算大小；用 0d 题库在 A / B / C 三种切法里实测选一种，R@3 高于基线才合并。

目标检查（`tests/test_scene_chunking_goal.py`，夹具带省略号、中文引号、单换行段落、重复句）：
1. 块内文本 = 原文对应区间原样（不截断）；所有块首尾相接覆盖全文（除空白）。
2. 段落不被切断；单段超预算时只在句末（。！？…以及其后的右引号）切开；单句超预算整句成块。
3. A/B：块长 ≤ 预算（单句超长除外）；C：固定长度、相邻块重叠 50。
4. metadata 带 `npos` 与 `nend`；`chunk_spans` 有 `nend` 时直接用它，没有时退回下一块起点（旧集合）。
5. 0c 的理想检索对照在新切法下仍 = 1.0。
6. 切法版本：集合 metadata 新增 `chunk_scheme` 键；`index_scenes` 的复用条件在**存卡和开卡两条路径**都比对它，不一致就重建；`POS_SCHEMA` 不变（`core/rag.py:457` 的窗口判断依赖它，升版会让带阶段的会话候选为空）。

## 2. 已查实的事实（对目标的影响）

| # | 事实 | 坐标 | 影响 |
|---|---|---|---|
| F1 | `docs.append(scene[:800])` | `core/scene_indexer.py:133` | 800 字后内容检索不到、模型看不到 |
| F2 | 切点只认章标题、`\n{2,}`、时间词；>1000 字只按 `\n\n` 再切 | `:64-68`、`:151-172` | 实测《你却爱着一个傻逼》仅 271 处空行，9866 段、段长中位 34 字 → 平均一块约 1700 字 |
| F3 | 复用条件：指纹相同即复用；`pos_schema` 只在 `need_positions=True`（存卡）时比 | `:105-110` | 只改切法不改复用条件 → 旧集合永不重建 |
| F4 | `POS_SCHEMA = 1`；检索窗口要求集合 `pos_schema == POS_SCHEMA` | `core/rag.py:26-28`、`:457` | 不能靠升 `POS_SCHEMA` 触发重建 |
| F5 | metadata 只存 `npos`；`chunk_spans` 终点取下一块起点 | `core/scene_indexer.py:137-140`、`core/rag.py:36-52` | 写入 `nend` 后改 `chunk_spans` 一处 |
| F6 | 嵌入单条上限 `MAX_CHARS = 3000`，超长截断 | `core/embeddings.py:241`、`:313-314` | 512 token 的块远在限内 |
| F7 | 场景索引调度点 4 处，都走 `index_scenes` | `web/routers/distill.py:780`、`:1621`；`core/text_manager.py:561`、`:643` | 复用条件改在 `index_scenes` 一处即全覆盖 |

## 3. 候选切法与依据（无中文小说文献，由实测决定）

| 变体 | 做法 | 出处 |
|---|---|---|
| 基线 | 现切法 | — |
| A | 单换行切段落，连续段落攒到 **178 token** | QChunker（WWW 2026，中文 CRUD / MultiFieldQA_zh，块均 178 token） |
| B | 同上，攒到 **300 token** | 2601.14123 建议 150–300 token 的上限；2602.16974 同书内检索块大正相关（r≈0.5） |
| C | 固定 **512 token、重叠 50** | 2606.00881 固定长度配置，含小说数据集，稳定性 / 效果平衡最好 |

不做：LLM 定边界（LumberChunker 一类，每本书数小时，Shiyu 不接受）。
token→字数：S0 用一次百炼嵌入调用，对一段已知字数的原文读返回的 `usage.total_tokens` 得出比例，写进常量；A/B/C 都按字数执行。

## 4. 设计

| 块 | 位置 | 职责（只此一处） |
|---|---|---|
| 切块 | `core/scene_indexer.py`：`_split_scenes` 改为按 `CHUNK_SCHEME`（`{"mode": "para"\|"fixed", "chars": n, "overlap": k}`）切；返回 `(起点, 终点, 文本)` | 切法只在这里 |
| 入库 | 同文件：存全文；metadata 加 `nend`；集合 metadata 加 `chunk_scheme`（scheme 的稳定字符串） | — |
| 复用 | `index_scenes` 复用条件 = 指纹相同 ∧ `chunk_scheme` 相同（∧ 存卡时 `pos_schema` 相同，原逻辑） | F3 |
| 区间 | `core/rag.py` `chunk_spans`：有 `nend` 用 `nend` | F5 |
| 变体评测 | 评测入口加 `--chunk-scheme`：按指定 scheme 建 `eval_r1_<变体>_...` 集合（祈灼的卡也用 eval 集合名，窗口仍取卡），不动线上集合 | 0 处产品改动 |

选定后：`CHUNK_SCHEME` 常量设为胜出变体，其余变体不留代码路径（YAGNI）。

## 5. 步骤（每步一个提交）
- S0：复核 §2 坐标；量 token/字比例（一次嵌入调用）；查 SG 内存（`free -m`），不够就停。
- S1：目标检查 xfail。
- S2：切块 + 入库 + 复用 + 区间；目标检查绿；受影响测试：`tests/test_scene_index_dispatch.py`、`tests/test_recall_eval_goal.py`、`tests/test_rag*.py` 中引用 `index_scenes` / `chunk_spans` 的文件（S0 按「哪些用例会走到 index_scenes」grep 列出）。
- S3（明早，#208 合并并 rebase 后）：`--chunk-scheme`；SG 上对 基线 / A / B / C 各建 eval 集合、跑 0d 题库，报每种的 R@3（总体 + 按书 + 按角色）、越界率（祈灼）、块数、建索引耗时与嵌入 token。
- S4：按「总体 R@3 最高；且高于基线」定 `CHUNK_SCHEME`；都不高于基线 → 停，报 Shiyu。开 PR，CI 绿后等复核。

## 6. 规模
- 5 本书共约 378 万字（S0 报出的字数相加）。场景集合按角色建（每段只打一个角色名，同产品），10 个角色 ≈ 每本书嵌入 2 遍 → 每种变体约 750 万字；4 种约 3,000 万字 → 估 2 美元左右（百炼 $0.07 / 百万 token；token/字比例 S0 实测后重算）。
- 上线后：每张卡下次开卡时因 `chunk_scheme` 不同重建一次（用卡主自己的 key），重建期间回落 `text_` 集合检索。

## 7. 补充
（审计发现写这里。）
