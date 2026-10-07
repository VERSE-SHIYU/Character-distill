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
