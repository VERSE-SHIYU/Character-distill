# 给本地 Claude Code 的提示词：② 「情境→做法」注入 —— 独立复核（验收）

> 新开一个会话做（不带实现时的上下文）。**先进 plan mode 报计划，Shiyu 确认后再执行。**
> skill：`@search-first`（核坐标与现有实现）、`@verification-before-completion`（贴实际输出再下结论）。

## 背景

- 分支 `feat/arc-behavior-inject`（远端已推），叠在 `proto/arc-phase-unlocated`（①补完 B，HEAD `a469358f`）之上，比它多 5 个提交（实现 + 单测 + 变异脚本、目标检查、spec + 补丁、变异日志）。
- spec：`docs/specs/arc-behavior-inject.md`（全部规则、坐标、对账表都在里面）。
- 实现方是 Claude（沙箱），所以要你独立复核。**本次只验收，不合并、不开 PR**：B 还没合进 main（10-07 定合成 1 个 PR），现在开 PR 会把 B 的提交一起带进去。

## 步骤

| 步骤 | skill | 做什么 |
|---|---|---|
| 1 起环境 | `@search-first` | 在主目录 `E:\Study\ANU\ai coding\Character-distill` 用 `claude -w arc-inject-review` 开 worktree；把主目录的 `.env`、`config.yaml` 复制进去；然后 `git fetch origin feat/arc-behavior-inject` → `git checkout --detach origin/feat/arc-behavior-inject`。起测试库前先查 55432 是否被占（`Get-NetTCPConnection -LocalPort 55432 -ErrorAction SilentlyContinue`）；被占就按 spec §2 起一次性库（如 55433，配置对齐 `docker-compose.test.yml`），用完删掉，不动别人的容器 |
| 2 S0 | `@search-first` | 按 spec 的 S0 做，第 2 条（在 main 上 `git apply --check`）**本次跳过**，等 B 合并后开 PR 时再做。核补丁 sha256：`Get-FileHash docs\specs\artifacts\arc-behavior-inject-proto.patch` 必须是 `E5343DBC9212C41B02C6D8DE866FEFD9CBE0FF55916AF217DD30ECC1999DD376`。逐条复核 §2 的坐标：`git show 40e9c75b:core/arc_view.py \| Select-String "def _project_custom"` 等，内容对不上就停下报告 |
| 3 读 diff | `@search-first` | `git diff a469358f HEAD -- core scripts tests` 逐文件读完（共 6 个代码/测试文件），每个文件写一行「看过，结论」；没读完不下「通过」 |
| 4 跑测试 | `@verification-before-completion` | **先跑目标检查** `tests\test_arc_behavior_inject_goal.py`，再跑单测与受影响选集，命令见下；贴汇总行 |
| 5 跑变异 | `@verification-before-completion` | `.venv\Scripts\python.exe docs\specs\artifacts\arc_behavior_inject_mutations.py`，必须 `结论：27/27 条全红`，且还原核对三个 `True` |
| 6 自补变异 | `@verification-before-completion` | 对照 spec §5 对账表找缺口，**放宽、过严两个方向**各自补几条（例如：群聊也被重注入、agent 路径没带上、恢复会话后计数错位）。手改 → 跑对应测试 → 还原，贴实际输出 |
| 7 报告 | — | 结论写进 spec 末尾「## 补充」一节（每条：原因 → 复现 → 建议修法），提交到本地分支，**不推、不合并**，把报告贴给 Shiyu |

## 第 4 步命令（PowerShell）

```powershell
docker compose -f docker-compose.test.yml up -d --wait
.venv\Scripts\python.exe -m pytest -q tests\test_arc_behavior_inject_goal.py
.venv\Scripts\python.exe -m pytest -q tests\test_arc_behavior_inject.py
$sel = Get-ChildItem tests\*.py |
  Select-String -Pattern 'chat_engine|context_engine|arc_view|group_session|ChatEngine|ContextEngine|project_card|run_agent_eval|text_manager|routers\.(chat|history|distill|group)' -List |
  ForEach-Object { $_.Path }
$sel.Count
.venv\Scripts\python.exe -m pytest -q $sel
```

## 验收标准（全部满足才算通过）

1. 补丁 sha256 一致，§2 坐标全部对得上。
2. 目标检查 `tests\test_arc_behavior_inject_goal.py`：12 passed；单测 `tests\test_arc_behavior_inject.py`：32 passed。
3. 受影响选集：沙箱是 77 个文件（含目标检查文件）、1465 passed、1 skipped。数量不同要说明原因；唯一允许的失败是 main 上原有的 `test_relationship_batch_splits_and_merges`（B spec §2.5）。
4. 变异脚本 27/27 全红，三个文件还原一致。
5. 自补变异没有存活；有存活就写进「补充」，不要自己改实现。
6. 对照 spec §1 目标逐条确认：选阶段 1 时 prompt 里有阶段 1 和全程的做法，没有后期和未定位区的；第 5、9、13 句带提醒，其余轮不带；提醒不进对话记录（流式、非流式都查）。

## 不做

- 不跑真实模型、不花钱：真实效果并进演示卡重蒸时再验（spec §10）。
- 不改实现代码；发现问题只记录、只报告。
- 不写任何部署、上服务器的步骤。
