# 简历可写数字 — 由证据清单渲染

> **本文件是渲染产物，勿手改。** 改动一律改 `docs/evidence/manifest.json`，再重跑：
> `python tests/perf/render_evidence.py`。
>
> 判据：只有 `status = verified`（产物在仓库、脚本可重跑）的数字可以写进简历。
> 数字的**口径**（聚合方式、样本数、字段名对照）认清单的 `notes` 与 `docs/evidence/` 下的
> 叙述档 —— 本表是索引，不是第二份真源。

## 一、可写（`verified`，8 条）

| 数字 / 结论 | 证据 | 一句话复现口径 |
|---|---|---|
| 8 条删除路径删完后 distill_tasks / distill_chunks 残留矩阵（8 格）；sqlite 与 PG 逐格相同 | `ev:distill-orphan-matrix` | `DATABASE_URL=<一次性 PG> python tests/perf/distill_orphan_matrix.py` （脚本 `tests/perf/distill_orphan_matrix.py`） |
| 删卡保留行后，find_interrupted_distill 仅命中 status=['interrupted'] 的行（其余状态整批重跑）；sqlite 与 PG 逐格相同 | `ev:distill-resume-reachability` | `DATABASE_URL=<一次性 PG> python tests/perf/distill_resume_reachability.py` （脚本 `tests/perf/distill_resume_reachability.py`） |
| 断点续跑：mock 截断下 6 片各 52 字节全部落库且闸 2 放行；二次续跑 map=0（分片未重算） | `ev:incomplete-v5` | `python -m pytest tests/test_distill_resume.py -q` （脚本 `tests/test_distill_resume.py`（**只佐证**，非产出脚本）） |
| 属主 404 的 32 条 A 类 + 3 条 B 类 + 7 条文案一致性用例，各自命中的 raise 站点与 status（本机有 key） | `ev:ownership-reachability` | `PYTHONPATH=".;tests/perf" PROBE_EVIDENCE_ID=ownership-reachability python -m pytest tests/test_ownership_404.py -q -p raise_probe` （脚本 `tests/perf/raise_probe.py`） |
| 同上，PROBE_NO_KEY=1 模拟无 key 机器；两档站点表逐行相同，用例数均 42 passed | `ev:ownership-reachability-nokey` | `PROBE_NO_KEY=1 PYTHONPATH=".;tests/perf" PROBE_EVIDENCE_ID=ownership-reachability-nokey python -m pytest tests/test_ownership_404.py -q -p …` （脚本 `tests/perf/raise_probe.py`） |
| 顶到 8192 时 3 条里 2 条 content_chars=0、reasoning_content 12441/12413、finish_reason=length —— 思考与正文共享 max_tokens 预算 | `ev:thinking-capfield` | `PROBE_DB=data/character_sim.db PROBE_EVIDENCE_ID=thinking-capfield python tests/perf/capfield_probe.py` （脚本 `tests/perf/capfield_probe.py`） |
| 修复后 map 自然输出 out_tokens p50 1245、max 2097、撞 8192 探针上限 0/14、空正文 0/14（n=14） | `ev:thinking-maplen-after` | `PROBE_DB=data/character_sim.db PROBE_EVIDENCE_ID=thinking-maplen-after python tests/perf/map_len_probe.py` （脚本 `tests/perf/map_len_probe.py`） |
| 修复前 map 自然输出 out_tokens p50 8191、max 8192、撞 8192 探针上限 7/14、空正文 3/14（n=14） | `ev:thinking-maplen-before` | `git checkout f2dfd23^ -- adapters/llm_adapter.py && PROBE_DB=data/character_sim.db PROBE_EVIDENCE_ID=thinking-maplen-before python tests/pe…` （脚本 `tests/perf/map_len_probe.py`） |

## 二、不可写（`runtime-measured` / `unverifiable`，4 条）

| 结论 | 证据 | 为什么不可写 |
|---|---|---|
| 把 _run_distill_task 的两处 confirm 兜底调用从 finally 里整行删掉，原有 14 条蒸馏用例仍全绿 —— 该接线当时没有任何用例覆盖；随后补 TestA2Wiring 两条（commit 9d2a9e4） | `ev:a2-wiring-mutation` | 运行期实测：环境还在，但产物没有入库 —— 按 `notes` 里的扫描方法自行重做。扫描方法：变异实验本身（删掉兜底调用 → 跑当时树上的蒸馏用例 → 观察全绿），仓内可核的是**补齐动作**：`git show 9d2a9e4 -- tests/test_distill_task_api.py`（+51 行，TestA2Wiring）。原始记载只在未入库的 … |
| chunk_size 无统一标准：3000 起于 2026-05-19（commit 5aee0609），代码默认 3000，classic 地板 6000（effective_chunk_size）；「3000/4500/6000/12000 四档实测对比」经全仓扫描无出处 | `ev:chunk-size-provenance` | 运行期实测：环境还在，但产物没有入库 —— 按 `notes` 里的扫描方法自行重做。扫描方法（2026-09-12 现跑，判定「四档对比无出处」这个**否定命题**）：`git log -S 4500 --all --oneline` 得 3 处、`-S 12000` 得 5 处，逐一核对后**无一处把这两个数当 chunk_size** —— 命中的是 AG… |
| 运行配置的两个值：`distill.chunk_size = 5000`、`distill.longctx_threshold = 150000`（≈25 万字符，与 `_estimate_tokens = int(len*0.6)` 同口径）；二者的来源 `config.ya… | `ev:config-yaml-values` | 运行期实测：环境还在，但产物没有入库 —— 按 `notes` 里的扫描方法自行重做。扫描方法：直接读运行环境的 `config.yaml`（本机现值 `chunk_size: 5000` / `longctx_threshold: 150000`）；`git ls-files config.yaml` **零命中**。即这两个数字引用的是一个**未入库、且随环… |
| graphify 知识图谱快照（2026-08-15，构建 commit eb72a3bd）的统计组：语料 369 文件/~65 万词、节点 4686、边 9606、社区 434（展示 227）、语义标注 94% EXTRACTED · 6% INFERRED（603 边，平均… | `ev:graphify-snapshot-2026-08-15` | 当时结论、现已不可复现：环境或快照已不存在 —— 不得当现状引用。为什么现在复现不了（三件事写全）：① 数字产生于 2026-08-15，当时工作树为 commit eb72a3bd，图谱产物 graphify-out/graph.json **从未入库**（`.gitignore:253` 覆盖），引用它等于没有出处；② 环境已变 —— 此… |

## 三、出处索引（全部 12 条）

| id | status | 产物 | 脚本 | 脚本角色 | 产出 commit | 测量日 |
|---|---|---|---|---|---|---|
| `a2-wiring-mutation` | `runtime-measured` | — | — | — | `9d2a9e4` | 2026-09-10 |
| `chunk-size-provenance` | `runtime-measured` | — | — | — | `e389fdc` | 2026-09-12 |
| `config-yaml-values` | `runtime-measured` | — | — | — | `e389fdc` | 2026-09-12 |
| `distill-orphan-matrix` | `verified` | docs/evidence/distill-orphan-matrix.json | tests/perf/distill_orphan_matrix.py | producer | `e389fdc` | 2026-09-12 |
| `distill-resume-reachability` | `verified` | docs/evidence/distill-resume-reachability.json | tests/perf/distill_resume_reachability.py | producer | `e389fdc` | 2026-09-12 |
| `graphify-snapshot-2026-08-15` | `unverifiable` | — | — | — | `eb72a3bd` | 2026-08-15 |
| `incomplete-v5` | `verified` | docs/evidence/incomplete-v5.json | tests/test_distill_resume.py | corroborating | `unknown(scratch)` | 2026-09-10 |
| `ownership-reachability` | `verified` | docs/evidence/ownership-reachability.json | tests/perf/raise_probe.py | producer | `5bacc48` | 2026-09-12 |
| `ownership-reachability-nokey` | `verified` | docs/evidence/ownership-reachability-nokey.json | tests/perf/raise_probe.py | producer | `5bacc48` | 2026-09-12 |
| `thinking-capfield` | `verified` | docs/evidence/thinking-capfield.json | tests/perf/capfield_probe.py | producer | `5ba7b9e` | 2026-09-10 |
| `thinking-maplen-after` | `verified` | docs/evidence/thinking-maplen-after.json | tests/perf/map_len_probe.py | producer | `bc19511` | 2026-09-10 |
| `thinking-maplen-before` | `verified` | docs/evidence/thinking-maplen-before.json | tests/perf/map_len_probe.py | producer | `bc19511` | 2026-09-10 |
