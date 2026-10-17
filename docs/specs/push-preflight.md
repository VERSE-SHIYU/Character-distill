# Spec：推送前预检（push-preflight）

> 规矩见 `docs/specs/SPEC-STANDARD.md`。基线：main `6a55b8d6`（2026-10-17）。
> 背景与依据：共享文档「工程流程强化」https://claude.ai/code/artifact/cc6847fb-22cf-47e0-bcca-ee64b405c727
> **2026-10-17 改口径**：推送前不再跑全量，只跑预检（6 个元锁 + 前端 eslint）；以文末「改口径补充」为准，与之冲突的 §0–§4 条目作废。
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
- **A3 实跑发现并修复（2026-10-17）**：CI 日志里 `grep: pytest.log: binary file matches` —— 真实 pytest 输出含 NUL，grep 默认把日志当二进制、不输出匹配行，导致摘要与 `::error` 都没写。修复：两处 grep 加 `-a`。T5 增加 `FAKE_BINARY` 让假解释器输出 NUL，复现该形态（不加 `-a` 时 T5 实测红，摘要内容是 `Binary file pytest.log matches`）；变异表新增 M12（去掉 `-a`），总数 11 → 12。
- **A3 复跑又发现一处（同日修复）**：全量跑时 `test_push_preflight` 的 T3–T5 会在仓库根再调 `check.sh`，内层 `tee pytest.log` 截断了外层正在写的同一文件，摘要里混进了假解释器的 `tests/a.py::t1`。修复：`check.sh` 的日志路径改为 `${PYTEST_LOG:-pytest.log}`，测试的 `_check` 把内层日志指到临时目录。另：CI 的 `::error` 注解在该 step 已有 11 条失败注解后被 GitHub 丢弃（日志里仍可见 `##[error]<ids>`），A3 以 step summary 为准。
- **A1 复跑发现钩子环境泄漏（同日修复）**：从 `.claude/worktrees/<名字>` 里 push 时，Git 会给 pre-push 钩子注入 `GIT_DIR`（指向该 worktree 的 gitdir）。T1–T2c 因此把临时仓库的 `git init`/`remote add` 落到外层仓库，红在 `remote origin already exists` —— 钩子跑全量的环境与手敲 shell 不同，同口径的前提被破坏。修复：钩子在跑检查前用子 shell `unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES`。新增 T9（钩子必须对检查隐藏 `GIT_DIR`）与 M13（去掉 unset → T9 红）；变异总数 12 → 13。spec §3 的测试表只列到 T8，T9 与 M13 属执行中的已声明新增。


### 2026-10-17 改口径补充（Shiyu 已拍板；本节优先于 §0–§4）

**为什么改**：本机推送前跑全量要约 24 分钟（A4 实测；CI 7.6 分钟），不可接受。根因是 3672 条串行 + Windows 平台开销，无单一热点（Linux 复现 `--durations=0`：最慢文件 `test_usage_accounting_lock` 占 9.4%）。依据与调查过程见共享文档「工程流程强化」。

**新口径**

| 层 | 跑什么 | 依据 |
|---|---|---|
| pre-push | `scripts/check.sh preflight` = 6 个元锁 + 前端 eslint | 回放：09-24 起 59 次可回放的 CI 红里，6 个元锁会红 17 次；Linux 单次约 3 秒 |
| CI gate | 全量（不变，仍 `check.sh backend` / `frontend`） | — |
| protect-main | gate 设为必需检查（Shiyu 在 GitHub 设置，不在本 PR） | 现 ruleset 只有 deletion / non_fast_forward / pull_request，无必需检查（API 实读） |

6 个元锁：`tests/test_lock_coverage.py`、`tests/test_mutation_anchors.py`、`tests/test_mutation_framework.py`、`tests/test_evidence_integrity.py`、`tests/test_evidence_integrity_mutations.py`、`tests/test_ledger_substitution_markers.py`（@3b022889 均存在）。

**已查实的约束（@ `feat/push-preflight` 3b022889，S0 逐条复核，不成立就停）**

| # | 事实 | 坐标 |
|---|---|---|
| P1 | 钩子默认执行无参数的 `check.sh`（= backend + frontend 全量） | `.githooks/pre-push:45`；`scripts/check.sh:61-66` |
| P2 | `check.sh` 的目标只有 `backend`、`frontend`，其他参数退出 2 | `scripts/check.sh:69-77` |
| P3 | `run_frontend` = `npx eslint . -c eslint.ci.config.js --quiet && npm test` | `scripts/check.sh:57-59` |
| P4 | `conftest` 会话开始即连测试库，连不上整场 `pytest.exit`——任何 pytest 调用（含只跑元锁）都要求测试库已启动 | `tests/conftest.py:70-84` |
| P5 | SPEC-STANDARD 第 3 条与修订记录写的是「推送前由钩子跑全量」「32/52」 | `docs/specs/SPEC-STANDARD.md:19`、`:61` |
| P6 | AGENTS.md 推送前预检一行写的是「钩子里的 `REQUIRE_PG_TESTS=1`」与全量口径 | `AGENTS.md:55` |
| P7 | T7 只断言 CI 调 `check.sh backend` / `check.sh frontend`，不涉及钩子 | `tests/test_push_preflight.py:323-333` |

未核实（执行方实测写进报告即可，不停）：本机 `preflight` 实际耗时；本机 eslint 单独耗时。

**改动（只改这些）**

1. `scripts/check.sh` 新增目标 `preflight`：用解析出的解释器跑上面 6 个文件（`-q`），再跑前端 eslint（与 `run_frontend` 的 eslint 同一条命令，抽成一处供两者复用）。**元锁清单只写在 `check.sh` 一处**。不导出 `REQUIRE_PG_TESTS` / `REQUIRE_COMPOSE_TESTS`（元锁不需要）。失败摘要逻辑与 backend 共用。
2. `.githooks/pre-push`：默认命令改为 `check.sh preflight`。文档放行、`GIT_DIR` 清理、`PREFLIGHT_CMD` 覆盖不动。
3. `docs/specs/SPEC-STANDARD.md` 第 3 条改为「中途只跑受影响文件 + `npm test`；推送前钩子跑预检（`scripts/check.sh preflight`：元锁 + eslint）；全量只在 CI；合并前提是分支 CI 绿……」，验收列改为「钩子输出」；`:61` 修订记录改正数字为「59 次可回放的 CI 红中元锁会红 17 次」并写明改口径原因（本机全量 24 分钟）。
4. `AGENTS.md:55` 同步：推送前跑预检；测试库仍须先起（P4）；去掉全量口径的描述。

不新增文件、不改 `conftest`、不改 CI。

**目标检查（先写，`xfail(strict=True)`，第一个提交带上）**

- G1 推送范围含代码改动、且未设 `PREFLIGHT_CMD` 时，钩子调用的是 `check.sh preflight`，不是无参数全量（怎么记录调用由执行方按现有测试写法决定）。
- G2 `check.sh preflight` 交给解释器的参数里：每个测试路径都是存在的 `tests/test_*.py` 文件、不含裸 `tests/`、个数 ≥ 1；随后调用了 eslint；且**不调用** `npm test`。不在测试里重抄 6 个文件名（清单只此一处）。
- G3 元锁失败（假解释器退出 1）→ `check.sh preflight` 非零。

实现后摘掉 xfail。变异：M14 钩子改回无参数 `check.sh` → G1 红；M15 `preflight` 改成跑 `tests/` → G2 红；M16 吞掉元锁退出码 → G3 红。加进 `docs/specs/artifacts/push_preflight_mutations.py`，与原 13 条一起实跑，贴输出。

**本地只跑**：`tests/test_push_preflight.py`、`tests/test_line_endings.py`，以及一次 `bash scripts/check.sh preflight`（报耗时）。**不跑全量**，不做真推送的红绿验收。

**验收（替换 §4 的 A1、A4）**

- A1' 本地 `bash scripts/check.sh preflight` 全绿，报实际耗时；超过 5 分钟先停下报告。
- A2' 推送本分支一次，钩子跑的是预检（终端可见 6 个元锁 + eslint），报钩子耗时。
- A5 分支 CI 全绿（gate 仍跑全量）。
- A2、A3 已于首版验收通过，不重做。

**预算**：整段 30 分钟以内；超出先停下报告当前进度，不继续。

审计（2026-10-17）：G3 只覆盖元锁失败，补 G4（eslint 失败也拦）与 M17。
