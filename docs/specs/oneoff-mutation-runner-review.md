# 独立审计：refactor/oneoff-mutation-runner

现在就可以审：本分支建在 B 的 `d68bb964` 之上，审计不依赖 B 合并。先进 plan mode 报计划，Shiyu 确认后执行。只审不改业务代码，不合并。**PR 等 B 合并后再开**（开之前先 rebase）。

## 核对

- 代码改动在 `1824c950`；之后的一个提交只加了本文件 `docs/specs/oneoff-mutation-runner-review.md`
- 只看 `git diff d68bb964..1824c950`：5 个文件，+143 / −254
- 开 PR 时（B 已合并）先 `git rebase origin/main`，预期没有冲突。有冲突就停下报告。

## 改了什么

一次性变异脚本原先各自抄一遍「基线门 → 改 → 跑 → 还原 → 核对 → 报告」，补充 20 就是抄错的那一份。现在这套流程只在 `tests/perf/mutation_framework.py` 一处：

| 名字 | 做什么 |
|---|---|
| `_trial` | 施加一条变异、跑靶子、按字节还原 |
| `_report_restore` | 还原核对 |
| `vitest(*files)` | 前端靶子 |
| `run_oneoff` | 一次性脚本的唯一跑法 |

- `run_matrix`（常设驱动）也改用 `_trial` 和 `_report_restore`，两类脚本走同一条执行路径。
- 这条线上的 4 份一次性脚本只剩变异表。
- 已结束的 spec（fields、select、b5b6）的脚本是历史记录，没动。

## 要跑的（就在中文 Windows 上，不设 `PYTHONUTF8`）

1. 常设驱动：`python tests/perf/arc_phase_anchoring_mutations.py`、`card_draft_mutations.py`、`distill_capacity_mutations.py`。期望全部符合预期，且 `git status` 干净（产物逐字节不变，证明 `run_matrix` 行为没变）。
2. `pytest tests/test_lock_coverage.py tests/test_mutation_anchors.py`
3. 一次性脚本：`docs/specs/artifacts/arc_phase_unlocated_mutations.py`（期望 61/61）、`_audit_mutations.py`（15/15）、`_audit3_mutations.py`（10/10）、`_audit4_mutations.py`（4/4）。跑完 `git status` 干净。
4. 反向对照，各一条即可：
   - 改一处注释的变异，必须报 MISS、退出码 1；
   - 停掉测试库，必须拒跑。

有发现就写清位置、根因和修法，写进本文件末尾新加的「审计记录」一节，单独一个 commit 推到本分支。没有发现，也写一段审计记录，附各项原始结尾行。

## skill

| 步骤 | skill | 用途 |
|---|---|---|
| 读 diff | `@code-review-and-quality` | 看抽象和职责划分是否合理 |
| 下结论前 | `@verification-before-completion` | 每个「通过」都要有亲手跑的输出 |

---

## 审计记录（独立审计员，2026-10-07）

坐标：分支 HEAD `411b9702`，代码改动 `1824c950`，基线 `d68bb964`。核对属实：`git diff d68bb964..1824c950`
5 文件 +143/−254；`1824c950` 之后仅 `411b9702` 加了本文件。

**环境**：中文 Windows，不设 `PYTHONUTF8`；Python 用本仓 `.venv/Scripts/python.exe`（3.12.10），
脚本 `ROOT` 自解析到本 worktree；前端 vitest 经目录 junction 用主仓 `web/frontend/node_modules`；
一次性 PG 16 容器 `cd-test-oneoff-mutation` @55436（库名 `charsim_test`，`TEST_DATABASE_URL` 指过去）。

**结论**：`run_matrix` 行为未变（3 个常设驱动全绿、产物内容逐字节一致）；4 份一次性脚本换成
`run_oneoff` 后跑出预期条数。**发现 1 条真回归**（F1：一次性驱动丢了一扇基线门）；另 3 条为观察。

### 各项原始结尾行

| 项 | 原始结尾行 / 退出码 |
|---|---|
| `arc_phase_anchoring_mutations.py`（42 条） | `全部符合预期。产物已写：tests/perf/arc_phase_anchoring_red_lines.json`（exit 0） |
| `card_draft_mutations.py`（38 条） | `全部符合预期。产物已写：tests/perf/card_draft_red_lines.json`（exit 0） |
| `distill_capacity_mutations.py`（21 条） | `全部符合预期。产物已写：tests/perf/distill_capacity_red_lines.json`（exit 0） |
| `pytest tests/test_lock_coverage.py tests/test_mutation_anchors.py` | `40 passed in 10.21s` |
| `arc_phase_unlocated_mutations.py` | `结论：61/61 条符合预期`（exit 0） |
| `arc_phase_unlocated_audit_mutations.py` | `结论：15/15 条符合预期`（exit 0） |
| `arc_phase_unlocated_audit3_mutations.py` | `结论：10/10 条符合预期`（exit 0） |
| `arc_phase_unlocated_audit4_mutations.py` | `结论：4/4 条符合预期`（exit 0） |

各步跑完 `git status` 干净。**一处与题面预期不符**：3 个常设驱动把产物重写成 CRLF，
`git status` 显示三个 `*_red_lines.json` 为 modified —— 内容（按行尾不敏感比较）逐字节等同于
入库版，纯行尾差异。`write_artifact` 不在本次改动面内（`lock_coverage.py` 未动），是本机已知形态
（`write_artifact` 未传 `newline=""`），非本次重构引入；核完已 `git checkout` 还原。

### 反向对照

- **改一处注释的变异 → 必须 MISS、退出码 1**：一次性脚本 `run_oneoff`（`gates=(MV,)`，一条只把
  `core/unlocated.py` 里 `# 交换：原值退回未定位区（D4）` 改成带 `[RC]` 的注释，不改行为）——
  `### RC-comment 只改注释（不改行为）   实得=green   MISS` / `结论：0/1 条符合预期` / exit 1。
  断言判档没有把「没红的变异」记成 OK。
- **停掉测试库 → 必须拒跑**：`docker stop cd-test-oneoff-mutation` 后跑 `audit4`——
  `tests/test_arc_phase_unlocated_move.py  跑不出来` / `基线不可用 —— 拒绝跑变异矩阵（红源说不清）`
  / exit 3（`EXIT_BASELINE_UNUSABLE`）。基线门把「跑不起来」与「跑了但红」分开，没有静默放行。

### 发现 F1（真回归）：一次性驱动 `arc_phase_unlocated_mutations.py` 丢了 `LOCK` 基线门

- **位置**：`docs/specs/artifacts/arc_phase_unlocated_mutations.py` `main()`
  （`1824c950` 后的第 295 行）。
- **现状**：`gates=[*(lock for lock in (UNL, GOAL, MV) if (ROOT / lock).exists()), _run_js]`。
  改动前是 `for lock in (UNL, GOAL, MV, LOCK)` —— 元组里的 **`LOCK` 被漏掉**。
- **根因**：`LOCK` = `tests/test_card_optimistic_lock.py`，正是本表 L1–L7 的靶子（`_l(...)`）所在文件。
  基线门的作用是「变异前先确认靶子绿」，好把「变异打红的」与「本来就红的」分开；漏掉 LOCK 后，
  一旦锁测试基线红，L1–L7 会「变异前就红 → 实得 RED → 记成符合预期」——假绿。移植时把
  `(UNL, GOAL, MV, LOCK)` 抄成 `(UNL, GOAL, MV)`，无注释、无替代门，判为笔误。
- **实跑对照（人为把 L1 靶子 `test_patch_with_stale_revision_is_409_and_card_untouched` 的基线改红）**：
  - 现门（无 LOCK）：`先验基线` 只列 UNL/GOAL/MV/前端四个门（LOCK 不在），L1–L7 全报
    `实得=RED   OK`，`结论：20/20 条符合预期`，exit 0 —— **红了的锁测试没被拦下，L1 是假绿**。
  - 门里加 LOCK：`tests/test_card_optimistic_lock.py  1 failed, 8 passed` →
    `基线不绿 —— 拒绝跑变异矩阵（红源说不清）`，exit 2 —— 这扇门本来就是拦得住这局的。
  （演示用的改红已 `git checkout` 还原，树干净。）
- **修法**：`main()` 的 gates 元组补回 `LOCK`：
  `gates=[*(lock for lock in (UNL, GOAL, MV, LOCK) if (ROOT / lock).exists()), _run_js]`。
- **同屏备注（非本 diff 引入，不记账）**：`PGT`（`tests/test_postgres_store.py`，L3 靶子 `_pg(...)`）
  历来不在任何门里；改动前后的 gates 都不含它，属既存缺口，另行处置。

### 观察（非回归，供参考）

- **F2 — `audit4` 的收窄（已在 docstring 声明）**：`1824c950` 把该脚本的基线门与变异靶子从
  「11 个后端文件 + `--deselect`」收窄到只 `MV`。实跑 4/4 全红，收窄没漏掉这 4 条（T1 的
  红源就落在 `tests/test_arc_phase_unlocated_move.py`）。判为有意的简化且当前成立；代价是这门比
  原来弱（MV 之外的回归不再由本脚本的基线门兜底）。是 author 声明的取舍，不算缺陷。
- **F3 — `vitest(*JS)` 去掉了原有的「只跑存在的文件」过滤**：改动前的 `_run_js` 是
  `[f for f in JS if (FE / f).exists()]`。现树里各脚本列的 JS 文件全在，故现役无差异；只在「早段」
  （后续段的 JS 用例尚未入库时）历史场景下会变红。无现役影响。
- **F4 — `run_oneoff` 比原一次性循环多查 `not run_problems`**：原循环丢弃 `realign_hits` 的坐标问题，
  现判档把它算作 MISS。4 份脚本全绿说明这些用例里 `problems` 恒空，属未触发的收严（更严，非更松）。

### 边界

- 只审不改业务代码、不合并、不开 PR（等 B 合并后先 rebase 再开）。改动面：仅本文件。
- 审计员的「通过」只报「已推送 + 自测绿」；是否算「审计通过」由 Shiyu 宣布。
