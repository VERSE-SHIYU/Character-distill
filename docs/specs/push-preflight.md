# Spec：推送前预检（push-preflight）

> 规矩见 `docs/specs/SPEC-STANDARD.md`。基线：main `6a55b8d6`（2026-10-17）。
> 背景与依据：共享文档「工程流程强化」https://claude.ai/code/artifact/cc6847fb-22cf-47e0-bcca-ee64b405c727
> 先进 plan mode 报计划，Shiyu 确认后执行。不改任何业务代码（`core/` `web/` `storage/` `adapters/` 不动）。

## 0. 为什么做（一段话）

9-24 至 10-17 共 600 次 CI、失败 59 次。排除有意先红（12）和放弃的分支（7）后，32/52 是「改动牵连到本地没跑的测试」：现行第 3 条规定本地只跑受影响文件，CI 跑全量，两边口径不一致。另有 12 次是先推的目标检查本来就红。
本步做三件事：① 推送前本地跑和 CI **同一个入口**；② CI 失败时把失败测试名写到运行页面；③ 目标检查统一用 `xfail(strict=True)`，CI 不再因先红而红。
依据：PostHog `ci:preflight`（pre-push 门禁）、CircleCI 2026-02 对照实验与 RalphCI「推送前确定性检查」、OneUptime 2026-07「本地与 CI 调同一入口，YAML 不另写一份」、pytest 官方 xfail 文档。

**不解决**：原则卡（第 2 步）、`/code-review` 审查提示（第 3 步）、PR 审计门禁（暂缓）。中途开发「只跑受影响文件」不变。

## 1. 已查实的约束（S0 逐条复核，任何一条不成立就停下报告）

| # | 事实 | 坐标 @ 6a55b8d6 |
|---|---|---|
| C1 | gate 与 sentinel 两个 job 都直接写 `pytest tests/ -v --tb=short` | `.github/workflows/build.yml:144`、`:223` |
| C2 | gate 与 sentinel 的 Run tests 都设了 `REQUIRE_PG_TESTS: "1"` 与 `REQUIRE_COMPOSE_TESTS: "1"`；本地不设时这两类用例**显式 skip**（不是红） | `build.yml:131`、`:143`、`:219`、`:222`；`tests/test_pg_gate.py:78-80` |
| C3 | CI 把 docker compose 钉在 v5.5.1 并断言版本号 | `build.yml:106-118` |
| C4 | 前端门：`npx eslint . -c eslint.ci.config.js --quiet` 与 `npm test`（= `vitest run`），目录 `web/frontend` | `build.yml:257-267`；`web/frontend/package.json:9` |
| C5 | `pytest.ini` 只有 `asyncio_mode = auto` 与 `testpaths = tests`，没有 `xfail_strict` | `pytest.ini:1-3` |
| C6 | 仓库已有 strict xfail 先红的用法，全部写了 `strict=True` | `tests/test_llm_access_gate.py:7`、`tests/test_storage_contract_shape.py:102` |
| C7 | 入库与工作区一律 LF（`.bat` 除外），有锁 | `.gitattributes`；`tests/test_line_endings.py` |
| C8 | 仓库没有 `.githooks/`，也没有任何地方设 `core.hooksPath` | 全仓 grep 无命中 |
| C9 | 第 3 条现写「本地只跑受影响文件……报告里没有本地全量数字」 | `docs/specs/SPEC-STANDARD.md:19` |
| C10 | CI 全量 pytest 耗时中位数 7.6 分钟（6.0–8.8，15 次成功运行） | GitHub Actions API，gate job「Run tests」步骤起止时间 |

**S0 另需在本机实测并写进报告**（不成立就停）：
- E1 `docker compose version` 是否为 v5.5.1。不是的话，带 `REQUIRE_COMPOSE_TESTS=1` 在本机会与 CI 行为不同 → 停下报告，由 Shiyu 决定是否升级。
- E2 `bash --version`（Git Bash）可用：git 在 Windows 上靠它执行钩子。
- E3 `core.hooksPath` 设为相对路径 `.githooks` 后，在 `.claude/worktrees/<名字>` 里推送也会执行该 worktree 自己的 `.githooks/pre-push`（实测一次，贴输出）。

## 2. 改动

1. **新增 `scripts/check.sh`：唯一的检查入口**，本地与 CI 都只调它。
   - `scripts/check.sh backend`：导出 `REQUIRE_PG_TESTS=1`、`REQUIRE_COMPOSE_TESTS=1`，执行 `pytest tests/ -v --tb=short`；`set -o pipefail`，输出同时写到 `pytest.log`。
   - 失败且环境里有 `GITHUB_STEP_SUMMARY` 时：把 `pytest.log` 里 `FAILED`/`ERROR` 开头的行写进摘要，并输出一条 `::error title=失败的测试::<前 10 个测试 id>`。退出码保持 pytest 的原值。
   - `scripts/check.sh frontend`：在 `web/frontend` 执行 eslint（同 C4）与 `npm test`。不装依赖（安装留在 CI 的 YAML 里，属于环境）。
   - 无参数 = `backend` + `frontend`，任一失败即非零退出。
   - 数据库连接、`JWT_SECRET` 等**环境上下文**仍由 CI 的 YAML 提供；本地沿用 conftest 的缺省（55432 测试库）。脚本只管命令和「保证有 PG / compose」这两条声明。
2. **`.github/workflows/build.yml`**：gate 与 sentinel 的 Run tests 改为 `bash scripts/check.sh backend`；前端两步改为 `npm ci` 后 `bash scripts/check.sh frontend`。其余（compose 钉版本、env、artifact）不动。
3. **新增 `.githooks/pre-push`**：读 git 传入的推送范围；本次推送的改动**全部**在 `docs/` 下或为 `*.md` 时直接放行，否则执行 `scripts/check.sh`，失败就拒绝推送。新分支（远端 sha 全 0）以 `origin/main` 的 merge-base 为起点。检查命令可用环境变量 `PREFLIGHT_CMD` 覆盖（仅供测试）。可执行位入库。
4. **`pytest.ini`**：加 `xfail_strict = true`。
5. **`docs/specs/SPEC-STANDARD.md`**：第 3 条改为「中途只跑受影响文件；**推送前由钩子跑全量**（`scripts/check.sh`），与 CI 同口径」，验收列去掉「没有本地全量数字」；新增一条：「先写的目标检查一律 `xfail(strict=True)`（前端 `test.fails`）；实现后由 XPASS 报错提醒摘标记」。修订记录加一行。
6. **`AGENTS.md`「开发工作流约束」**加一条：克隆后执行一次 `git config core.hooksPath .githooks`；推送前测试库要已启动（`docker compose -f docker-compose.test.yml up -d --wait`），否则因 `REQUIRE_PG_TESTS=1` 直接红；`git push --no-verify` 只用于验收 A3，平时不用。

不新增依赖，不新增其他文件。

## 3. 测试与对账表

新增 `tests/test_push_preflight.py`，全部是**行为测试**（真跑脚本，不扫描源码文本）。用临时目录里的假 `pytest`/`npx`/`npm` 放到 `PATH` 最前，记录收到的参数和环境变量。

| 测试 | 断言 | 让它变红的变异 |
|---|---|---|
| T1 钩子拦失败 | `PREFLIGHT_CMD=false` 推代码改动 → 钩子非零退出 | M1 钩子忽略检查结果、恒为 0 |
| T2a 文档放行 | 范围内只有 `docs/x.md`，`PREFLIGHT_CMD=false` → 退出 0 | M2 去掉文档放行（恒跑检查） |
| T2b 代码不放行 | 范围内有 `docs/x.md` + `core/x.py`，`PREFLIGHT_CMD=false` → 非零 | M3 放行条件写成「有任一文档文件」 |
| T2c 新分支 | 远端 sha 全 0 时按 merge-base 取范围，行为同 T2a/T2b | M4 新分支直接放行 |
| T3 后端口径 | `check.sh backend` 调用的参数恰为 `tests/ -v --tb=short`，且 `REQUIRE_PG_TESTS=1`、`REQUIRE_COMPOSE_TESTS=1` | M5 去掉 `REQUIRE_COMPOSE_TESTS` |
| T4 退出码 | 假 pytest 退出 1 → `check.sh backend` 退出 1 | M6 去掉 `pipefail` |
| T5 失败摘要 | 假 pytest 打印 `FAILED tests/a.py::t1` 并退出 1，设 `GITHUB_STEP_SUMMARY` → 文件含该 id，stdout 含 `::error` | M7 不写摘要 |
| T6 前端口径 | `check.sh frontend` 先后调用 eslint（C4 参数）与 `npm test`；任一失败即非零 | M8 eslint 失败被吞 |
| T7 CI 走同一入口 | 解析 `build.yml`：gate、sentinel 的 Run tests 与前端步骤的命令调用 `scripts/check.sh`，且不再含 `pytest tests/` | M9 某个 job 改回直接写 pytest |
| T8 xfail 严格 | 在临时目录用本仓 `pytest.ini` 跑一条「标了 xfail 却通过」的用例 → 整体失败 | M10 删掉 `xfail_strict` |

T7 是唯一读文件的测试，它判的是「CI 不另写一份实现」这条结构（OneUptime 的核心要求），读的是 YAML 的解析结果而不是正则。
变异由执行方实跑，贴每条的实际输出；有存活变异不交付。本地只跑 `tests/test_push_preflight.py` + `tests/test_line_endings.py`；合并前提是分支 CI 绿。

## 4. 验收（Shiyu 能直接看到的证据）

- A1 在本地故意改坏一个测试，`git push` 被拒，终端显示失败的测试名；改回后推送成功。
- A2 只改一个 `.md` 并推送，几秒内放行，没有跑测试。
- A3 在临时分支用 `git push --no-verify` 推一个故意红的提交 → GitHub 运行页面的摘要里列出失败测试名；验完删掉该分支。
- A4 报告本机 `scripts/check.sh` 全量实际耗时（分钟），与 C10 对照。
- A5 本 PR 的分支 CI 全绿。

## 5. skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 复核约束、实测 E1–E3 | `@search-first` | 先查现状，不凭记忆 |
| 写脚本、钩子、CI 改动 | `@ci-cd-and-automation` | CI 与本地同口径 |
| 写测试、跑变异 | `@tdd` | 先写行为测试，变异两向 |
| 交付前 | `@verification-before-completion` | 贴命令与输出，不声称成功 |

## 补充（执行与审计发现写这里）

### 2026-10-17 执行补充

- **E1 判据改判（Shiyu 裁定）**：本机 docker compose v5.1.3 ≠ CI 钉的 v5.5.1，但 E1 判据改为行为探针：`compose_model.cli_capable()` 返回 `True` 即成立（依据 `tests/compose_model.py:88-104`、`build.yml:100`「实测 v5.0.0 起两条行为都满足」）。本机实跑 `capable: True / defect: None`，继续；原「版本须为 v5.5.1」作废。
- **E3 实测通过**：`core.hooksPath .githooks`（相对路径）下，在 `.claude/worktrees/push-preflight` 里推送，钩子输出 `E3_HOOK_RAN_FROM=.../.claude/worktrees/push-preflight`，确认执行的是该 worktree 自己的钩子。
- **解释器解析（Shiyu 裁定）**：`scripts/check.sh` 不依赖 PATH 上的 `pytest`（本机 PATH 上是 Hermes venv，不是本仓证据）。顺序：① 环境变量 `PYTHON`；② 用 `git rev-parse --path-format=absolute --git-common-dir` 定位主仓库根，Windows 用 `.venv/Scripts/python.exe`，否则 `.venv/bin/python`；③ 都没有则 `python`（CI）。调用统一为 `"$PYTHON" -m pytest tests/ -v --tb=short`。T3 改为用 `PYTHON` 指向假解释器断言 argv；新增 M11「解释器改回裸 pytest」。
- **新增 `.gitignore` 的 `/pytest.log`**：spec §2 未列，但 `check.sh` 的产物落在仓库根，不 ignore 会污染每次本地运行的工作区。属已声明偏离。
- **新增 `docs/specs/artifacts/push_preflight_mutations.py`**：11 条变异的产数脚本入库（仓规「证据产物必须入库」）。spec §2「不新增其他文件」按此口径理解为不新增业务源文件。

