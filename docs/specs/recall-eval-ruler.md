# recall-eval-ruler：评测按块完整区间判命中（召回升级 0c）

- 基线：main `97852be1`（2026-10-25 读）。行号均指此提交。
- 共享文档「召回做法调研 · 2026」→「问题与修复日志」D1、「0c 整条链路表」（Shiyu 已认可）、「修复总方案」0c 行。
- 本 PR 只做一件事：修评测的尺子（链路第 ⑦ 环节）。检索本身（①–⑥）一行不改。
- 分支：从最新 main 新建 `feat/recall-ruler`；**cherry-pick** `feat/recall-baseline` 上的 `7ed2f51a`（登记证据 id）与 `52a9c9cd`（算写分开）；**不要** `37eca5e5`（0.00 那份证据作废）。`feat/recall-baseline` 不合并。

## 1. 目标与目标检查（第一个提交，`xfail(strict=True)`）

**目标**：评测判「命中 / 越界」时，看的是**标注片段落在哪一块**，那一块进了前 3 就算——与返回文本截到多少字无关（依据：2610.31606，证据片段映射到所有包含它的块）。

**目标检查** `tests/test_recall_eval_goal.py` 新增（用真实样式原文：含「……」、中文引号、换行、重复句）：

1. **理想检索对照**：每条标注，把「含该条 should 片段的那一块」直接当作前 3 交给 `score_case`，R@3 必须 = 1.0；把「含 should_not 片段的那一块」交进去，`leak` 必须为 True。**片段位于块内第 800 字之后**的用例必须有（这是 D1 的原样复现）。
2. **跨块片段**：片段跨两块边界时，两块中任一进前 3 都算命中（2610.31606「所有包含它的块」）。
3. **无关块不算**：块与所有片段都不重叠 → hit / leak 都为 False；块 id 不在区间表里 → 计入 `unlocated`，不算命中。
4. **区间表与检索同源**：`SessionRag.chunk_spans()` 读的必须是 `SessionRag.collection_name` 那个集合（检索实际用的那个，`scenes_*` 或回退的 `text_*`）。

## 2. 已查实的事实（含「对目标的影响」）

| # | 事实 | 坐标 | 对目标的影响 |
|---|---|---|---|
| F1 | 场景只存前 800 字：`docs.append(scene[:800])` | `core/scene_indexer.py:133` | 返回给评测的文本最多 800 字；按返回文本定位，片段在 800 字后永远判不中（实测 29/40）——本 PR 要消除的就是这个 |
| F2 | metadata 只有起点：`"npos": npos[i]`，没有终点 | `core/scene_indexer.py:137-140`；原文集合同样只写起点 `core/rag.py:355-359` | 块终点只能取「下一块起点」；第 1 轮切块写入显式终点时只改区间函数 |
| F3 | 命中项带块 id：`chunk_id=s.chunk_id`（chroma 真 id，如 `scene_3`） | `core/rag.py:610-617`；`core/schema.py:395` | 可由 id 查区间；`SceneMeta` 键集被契约锁住（`test_rag_evidence_contract.py`），本 PR **不加**新键 |
| F4 | `SessionRag.collection_name` 是只读属性，返回实际装载的集合名 | `core/indexing_service.py:118-120` | 区间表必须取这个集合，否则可能与检索用的不是同一份（`scenes_*` 缺失时回退 `text_*`，`:88-90`） |
| F5 | chroma `collection.get(include=["metadatas"])` 按**写入顺序**返回，不按 `npos` 排序（见 §3 复用清单探针输出） | 探针 | 区间函数必须先按 `npos` 排序再取下一块起点，否则终点全错 |
| F6 | `score_case` 现在用返回文本定位：`hit_iv = intervals_in_normalized(source_norm, item.text)` | `core/recall_eval.py:65-80` | 本 PR 改为按块 id 查区间 |
| F8 | 结构锁：`core/scene_indexer.py`、`core/rag.py`、`core/quotes.py`、`core/arc_view.py` 里不许出现 `.find(`（`tests/test_arc_phase_select_locks.py:146-151` S13）；`pos_schema` / 指纹的 `.get(...)` 只许在 rag / scene_indexer / indexing_service（:65-70 S1b）；`re.finditer(`、` in source_norm`、`_ELLIPSIS.split(` 三种写法只许在 `core/quotes.py`（`tests/test_phase_anchoring.py:598-612` `test_s2_normalized_match_is_written_once`） | 见行 | `chunk_spans` 只用排序与相邻取值，不用 `.find(`、不读 `pos_schema`、不写这三种写法（注意变量名别凑出 ` in source_norm` 字样）；本地必须多跑这两个锁文件 |
| F7 | 评测用的是用户原话；agent 模式下实际检索的是路由写的关键词 | `core/agent/tools.py:86` | **本评测不覆盖路由改写这一段**（第 3 轮加一档）——按返工经验第 59 条写明 |

## 3. 复用清单（第 52 条：函数体原文 + 真实样式输入实跑）

| 复用 | 函数体里决定行为的原文 | 实跑命令 | 输出 |
|---|---|---|---|
| `core/quotes.intervals_in_normalized`（:103-112） | `n = normalize(text)`；`if not n: return []`；`return [(m.start(), m.start() + len(n)) for m in re.finditer(re.escape(n), source_norm)]` | 原文 `'第一章\n甲说：“你来了……我等了很久。”\n乙沉默不语，转身离去。\n第二章\n甲说：“你来了……我等了很久。”'`，查 `'乙沉默不语，转身离去'` 与重复句 | `norm_len 35`；`frag [(13, 22)]`；`dup [(3, 13), (25, 35)]`（重复句两处都找到，区间宽度 = 片段自身） |
| chroma `Collection.get(include=["metadatas"])` | （库函数）返回 `ids` 与 `metadatas` 两个等长列表 | 依次写入 `scene_2(npos=40)`、`scene_0(0)`、`scene_1(13)` 后 `get` | `[('scene_2', 40), ('scene_0', 0), ('scene_1', 13)]` → **写入顺序，未排序**（F5） |

（以上探针在 main `97852be1`、chromadb 依 `requirements.txt` 锁定版本下运行；S0 第 3 条由执行方原样复跑。）

## 4. 设计

| 块 | 文件 | 职责 |
|---|---|---|
| 区间函数（唯一一处） | `core/rag.py` 新增 `chunk_spans(collection, source_norm_len) -> dict[str, tuple[int, int]]` | 读集合全部 `npos`，**按 npos 排序**，块区间 = `[本块 npos, 下一块 npos)`，最后一块到 `source_norm_len`。放在 `core/rag.py`：位置格式（`POS_SCHEMA_KEY`、`npos`）的唯一出处在这里，`scene_*` 与 `text_*` 两种集合都由它定义格式（`core/scene_indexer.py:14` 从这里导入）；第 1 轮改格式时同处修改 |
| 检索门面 | `core/indexing_service.SessionRag` 新增 `chunk_spans(source_norm_len)` | 对**已装载的同一引擎**调用上面的函数（先 `_refresh()`，未装载返回 `{}`）；不新开集合。同步改类 docstring（`:100` 现写「对外只提供 query_with_emotion_ex」） |
| 指标（纯函数） | `core/recall_eval.score_case(source_norm, case, hits, spans)` | 片段区间仍用 `intervals_in_normalized`；命中块区间改为 `spans[chunk_id]`；重叠即算；查不到 id 计 `unlocated` |
| 命令行 | `scripts/eval_recall.py` | 检索前取一次 `rag.chunk_spans(len(source_norm))`，传给 `score_case`；其余不变 |

**以后改几处**：第 1 轮切块写入显式终点 → 只改 `chunk_spans`；指标定义 → 只改 `score_case`。

## 5. 调用点矩阵

| 调用点 | 改前 | 改后 | 测试 |
|---|---|---|---|
| `scripts/eval_recall.py` 评测循环 | `score_case(source_norm, case, hits)` | 加 `spans` | 目标检查 1–3 |
| `SessionRag.chunk_spans`（新） | — | 供评测取区间 | 目标检查 4 |
| `ContextEngine` / 聊天 | 不改 | 不改 | 契约测试保持绿 |

## 6. 结构自检

| 问题 | 回答 |
|---|---|
| 同一条规则写了几处 | 「块在原文哪里」1 处（`chunk_spans`）；「片段在原文哪里」1 处（`intervals_in_normalized`，复用）；「怎样算命中」1 处（`score_case`） |
| 调用方是否被迫知道不该知道的事 | 评测脚本不知道 metadata 字段名和排序规则；`recall_eval` 不碰数据库 |
| 加一种情况要改几处 | 换切法 → `chunk_spans` 一处 |

## 7. S0（开工前逐条复核，有一条不成立就停）

1. 另：本 spec 2026-10-25 20:35 修订（区间函数放 `core/rag.py`、补结构锁 F8）。
2. `git rev-parse HEAD` 为 `97852be1` 或其后代；`git diff 97852be1 -- core/scene_indexer.py core/rag.py core/indexing_service.py core/recall_eval.py core/quotes.py scripts/eval_recall.py` 为空（cherry-pick 后只应有 `52a9c9cd` 对 `scripts/eval_recall.py` 与测试的改动）。
3. 逐条核对 §2 的 F1–F8，贴实际读到的行。
4. 原样复跑 §3 两条探针，贴输出；与表中不一致就停。

## 8. 测试与执行

- 环境硬规则：禁止启动、重启、停止 Docker Desktop / WSL / 任何容器；禁止用 cmd.exe / PowerShell 启动程序；测试库由 Shiyu 起好，看不到就停；命令退出码不是 0/1 立即停、不重试、不排查系统；不跑全量。
- 只跑：`tests/test_recall_eval_goal.py`、`tests/test_rag_evidence_contract.py`、`tests/test_session_rag_binding.py`，以及结构锁 `tests/test_arc_phase_select_locks.py`、`tests/test_phase_anchoring.py`。
- 提交顺序：① cherry-pick 两个提交；② 目标检查（xfail strict，红）；③ `chunk_spans` + `SessionRag.chunk_spans` + `score_case` + 脚本（转绿，去掉 xfail）。
- 本 PR **不跑真数据、不出基线**（0d 出题、0e 出基线另做）。
- Skill：纯后端小改动；测试或审查类 skill 每阶段最多一个。

## 9. 不做

- 不改切段、入库、候选、打分、注入（①–⑥）。
- 不加 `SceneMeta` 新键（契约锁）。
- 不出题、不跑基线、不改运行地点（0d / 0e）。

## 发出前判断清单

| 判断 | 依据 |
|---|---|
| 返回文本 ≤800 字 | F1 读过 `scene_indexer.py:133` |
| 只有起点无终点 | F2 读过 `scene_indexer.py:137-140`、`rag.py:355-359` |
| 命中带 chroma 真 id | F3 读过 `rag.py:610-617` |
| `collection_name` 是实际装载的集合 | F4 读过 `indexing_service.py:118-120`、`:88-90` |
| `get` 不排序 | §3 探针输出 |
| 三把结构锁的范围 | F8 读过 `test_arc_phase_select_locks.py:65-70`、`:146-151`；`test_phase_anchoring.py:598-612` 读过 |
| `chunk_spans` 放 `core/rag.py` | `core/scene_indexer.py:14` 从 `core.rag` 导入位置格式；`core/indexing_service.py:31` 同 |
| 定位函数行为 | §3 函数体原文 + 探针输出 |
