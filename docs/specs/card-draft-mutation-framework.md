# card-draft 变异驱动接入共享框架 + 主循环收口（修分支 CI 红）

> 全部内容都在这个文件里。补充一律写进第 12 节，不在聊天里答复。
> 分支 `feat/arc-behaviors-draft`；本 spec 的基线是 **`3cc07e7d`**（`14524a47` 之上两个提交：测试搬迁 `c4a6364c`、pg_gate 锚点修复 `3cc07e7d`，均已由 Claude web 验证并推上去）。
> 上一份 spec `docs/specs/arc-behaviors-draft.md` §9.4 记下了 CI 红，本 spec 负责关掉它。

## 0. 交接与 S0（先做，不过就停）

1. `git fetch; git switch feat/arc-behaviors-draft; git log --oneline -3`，第一行必须是 `3cc07e7d`（或之后由你自己追加的提交）。用 `claude -w card-draft-fw` 开 worktree，复制 `.env` / `config.yaml`。
2. `Test-Path docs/specs/card-draft-mutation-framework.md` 为 True。
3. **先进 plan mode**：只读本 spec 和第 2 节点到的代码，报执行计划，等 Shiyu 点头再动手。
4. **S0 逐条复核第 2 节 C1–C17**（打开文件看行号与原文）。任一条不成立，停下报告，不改代码。
5. **S0 复核第 7 节对账表**：逐条确认「变异在改后代码上可观测、每个决定都有测试」。有疑问先报告，再写代码。
6. 起测试库前查冲突：`docker ps` 看 55432 端口与容器名 `character-distill-test-postgres-1` 是否已被占；被占就停。然后 `docker compose -f docker-compose.test.yml up -d --wait`（tmpfs，空库）。

## 1. 目标与边界

**目标**：分支 CI 的红源 `tests/test_lock_coverage.py::test_every_mutation_driver_has_an_artifact_and_vice_versa`（「有驱动却没产物：`['card_draft']`」）消失，且补上产物后，**覆盖闭合那条**（`test_discriminators_and_red_lines_are_the_same_set[card_draft_red_lines]`）同样是绿的。

**根因**（不只是漏写一行）：`tests/perf/card_draft_mutations.py` 没有复用仓里现成的执行框架，自己另写了一套，而且用 `-x -q` 跑 pytest，拿不到「红在哪一行」，**产物根本写不出来**。仓里另外三处同样的「抄一份主循环」已经各自漂移（第 3 节），这次一并收口。

**做**
- A. 执行原语从 `route_facts_mutations.py` **原样搬**到不认领域的新模块 `tests/perf/mutation_framework.py`；五个驱动共用的主循环抽成其中一个函数 `run_matrix`。
- B. 五个驱动（alerting / ping / pg_gate / route_facts / card_draft）都改为调用 `run_matrix`；驱动只留：变异表、开跑前的门、各自的预筛。
- C. 重写 `card_draft_mutations.py`：变异表照抄附录 B（38 条，Claude 已预跑全红、44/44 覆盖），跑出并提交 `card_draft_red_lines.json`。
- D. 新增 `tests/test_mutation_framework.py` 守住框架的每个决定（第 7 节）。
- E. `AGENTS.md:760` 那句「三个驱动都复用 `route_facts_mutations.py` 的 `_apply` / `_restore` / `_run`」改成新模块名；`docs/specs/arc-behaviors-draft.md` §9.4 末尾加一行「已由 card-draft-mutation-framework 关闭」。

**不做**
- 不改 `tests/lock_coverage.py`、`tests/test_lock_coverage.py`、`tests/lock_coverage_gaps.py`（元锁与名单不动）。
- 不改任何业务代码（`core/` `web/` `storage/`）。`git diff 3cc07e7d -- core web storage adapters` 必须为空。
- 不重新生成、不提交另外四个驱动的产物（见第 6 节：入库产物是在 Windows 上生成的，环境不同本来就会不同）。
- 不改测试文件（`tests/test_card_draft.py` 等）：搬迁已在 `c4a6364c` 完成并验证。
- 不加钩子表、注册表、插件机制；`run_matrix` 只开第 4 节列出的那几个口子。

## 2. 已查实的约束（基线 `3cc07e7d`；行号都按这个提交）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 执行原语的唯一实现在一个**领域驱动**里：`_hidden` / `_TOUCHED` / `_apply` / `_restore` / `_run` / `_run_py` | `tests/perf/route_facts_mutations.py:631/638/641/663/672/713` |
| C2 | ping 与 pg_gate 从它 import：`import route_facts_mutations as framework` | `ping_mutations.py:65`、`pg_gate_mutations.py:94` |
| C3 | alerting **自己抄了一份** `_apply` / `_restore` / `_run` | `alerting_mutations.py:212/231/238` |
| C4 | 汇总行取法两份实现**不一致**：框架取**第一条**匹配行；alerting 取**最后一条**（注释写明原因：traceback 里也会出现「… errors in …」） | 框架 `route_facts_mutations.py:696`；alerting `:260` |
| C5 | 红源摘要两份不一致：框架只收 `FAILED`/`ERROR`/含 `AssertionError` 的行；alerting 另收 `E ` 开头的行 | `route_facts_mutations.py:695`；`alerting_mutations.py:256` |
| C6 | 变异后的还原：alerting 用 `try/finally`；ping / pg_gate / route_facts **没有**（`_run` 抛异常时树不还原） | `alerting:308-311`；`ping:326-329`；`pg_gate:467-469`；`route_facts:823-833` |
| C7 | `write` 动作只用于**新建**文件（全仓两处：alerting M9 写 `core/_spec119_syntax_probe.py`、pg_gate G-20 写 `docker-compose.override.yml`）；收尾删除各写一份：alerting 按名字删 `PROBE`，pg_gate 用 `_CREATED` + `_restore_all` | `alerting:154,231-235`；`pg_gate:105,310,432-437,494` |
| C8 | 只有 alerting 在主循环里就判「空转」（红了但没有落进域的红源 → mismatch，不写产物）、判编号重复；另外三个靠元锁事后发现 | `alerting:293,326-327` |
| C9 | marker：alerting / route_facts 接受单个串或一串；ping / pg_gate 只接受单个串 | `alerting:322`；`route_facts:855`；`ping:357`；`pg_gate:480` |
| C10 | skip：只有 ping 处理（`_MAY_SKIP` 登记过的进 `skipped`，没登记的记 mismatch）；pg_gate / alerting 遇到 skip 会落到「期望 RED 实得 本环境不适用」 | `ping:245,343-353` |
| C11 | route_facts 的两个特例：`expect == "RED-container"` 默认跳过（`--with-container` 才跑）；`_ORDER_SWAP` 不是 pytest 靶子，用 `_run_py` 跑一段代码、结果判 `OK`/`FAIL` | `route_facts:818-822,824-827,452,713` |
| C12 | 覆盖域：ping / pg_gate / route_facts 用 `lock_coverage.domain_of(GROUPS)`（**全部组**，与 `--group` 选了哪几组无关）；alerting 写死 `DOMAIN` | `ping:317`；`pg_gate:458`；`route_facts:809`；`alerting:72` |
| C13 | 元锁：驱动 ↔ 产物按文件名闭包；每份产物做覆盖闭合（未覆盖 ⊆ 名单、名单 ⊆ 未覆盖、无空转）；名单只收 `lock_coverage_gaps.py` 建档提交 `d781d860` 时就存在的判别器 —— **`test_card_draft.py` 的判别器全是新的，不能入名单** | `tests/test_lock_coverage.py:48`（闭包）、`:69-70`（覆盖闭合）、`:197`（名单来路） |
| C14 | 覆盖域按**文件**计。草稿契约的判别器原先散在 3 个文件；`c4a6364c` 已把路由两条用例与 G6 模板半句断言搬进 `tests/test_card_draft.py`，域只剩这一个文件：**44 条判别器**（附录 A1） | `c4a6364c` 的 diff |
| C15 | CI：`build.yml` 的 test job 跑 `pytest tests/ -v --tb=short`（`:144`）；build job `needs: [test, frontend-lint]`（`:270`）—— 元锁红 ⇒ 镜像不构建 | `.github/workflows/build.yml` |
| C16 | 测试库：`docker-compose.test.yml`，端口 55432、库 `charsim_test`、tmpfs；`tests/conftest.py:31-35` 强制连它 | — |
| C17 | **pg_gate 驱动在 main 上已经烂了，`3cc07e7d` 已修**：`e2c3eb50`（9-29，已在 main）给两份编排的 app 加了 `data-perms` 依赖，G-10/G-11 的锚点（只含 postgres 的依赖块）命中 0 次，驱动跑到 G-10 就 assert 退出。驱动不在 CI 里跑，入库产物仍过元锁，所以没人发现。修法只改锚点：G-10 把整块（含 data-perms）改成短式列表，G-11 只把 postgres 的 condition 改成 service_started。重跑 25 条全部符合预期，**重新生成的产物与入库逐字节相同** | `tests/perf/pg_gate_mutations.py:126-129,247,252`；附录 A4 |

**路径上已有机制（`run_matrix` 收口时逐个保住前提）**

| 机制 | 计数/计时起点 | 依赖前提 | 本次是否改变前提 |
|---|---|---|---|
| `_TOUCHED` 坐标搬移（`realign_hits`） | 每条变异 `_apply` 时记下**变异前**源文本，`_restore` 清空 | 每条变异之后必有一次 `_restore` | 不改；`try/finally` 让前提在异常时也成立 |
| 先验基线门 | 各驱动 `_baseline_gate()` | 在任何变异之前跑 | 不改，门留在驱动里 |
| 产物身份（缺陷 54） | `write_artifact` 当场把行号翻成身份 | hits 已搬回变异前坐标 | 不改 |
| 计数门 / docker 门 / 别名门 | 驱动 `main` 开头 | 在基线门之前或之后的现有次序 | 不改，次序照旧 |
| 子进程 pytest | `_run` 每条一个进程 | `--tb=long`，`cwd=ROOT` | 不改 |

## 3. 四个主循环的漂移（全量对照，收口时以「统一后」一列为准）

| 行为 | alerting | ping | pg_gate | route_facts | 统一后 | 理由 |
|---|---|---|---|---|---|---|
| 汇总行 | 最后一条 | 第一条（框架） | 第一条 | 第一条 | **最后一条**，抽成纯函数 `summary_of(out)` | 第一条会把 traceback 里含 `error … in` 的源码行当成汇总行。**本次预跑实测踩到**：card_draft M34 的「汇总行」被取成 `> assert any(isinstance(p, dict) and "error" in p for …`（附录 A2），判档碰巧仍是 RED |
| 红源摘要 | 含 `E ` 行 | 不含 | 不含 | 不含 | **含 `E ` 行** | 非断言失败在 FAILED 行里不带异常原文，marker 无从匹配（alerting 注释） |
| 异常时还原 | try/finally | 无 | 无 | 无 | **try/finally** | 没有它，`_run` 一抛，树就停在变异态 |
| 新建文件清理 | 按名字删 | `_CREATED` | — | — | **`_apply` 记下 `write` 新建的路径，`_restore` 删掉** | 两份手写清单收成一处；`write` 遇到已存在的文件当场 assert（同 alerting） |
| 空转 | 循环内判 | 元锁事后 | 元锁事后 | 元锁事后 | **循环内判**，有空转不写产物 | 产物只收核对过的红源 |
| 编号重复 | assert | — | — | — | **assert** | 重复编号在产物里互相覆盖 |
| marker | 串或一串 | 只串 | 只串 | 串或一串 | **串或一串** | — |
| skip | — | 登记过的进 skipped | — | — | **`may_skip`（标签集合）**：登记过的进 skipped，没登记的记 mismatch | — |
| 期望值 | RED | RED | RED | RED / green / OK / RED-container | `{"RED","RED-container"}→RED`，`"OK"→OK`，其余 → green；非 RED 的进 `controls` | 与 route_facts 现行映射一致 |
| 跑不起来 | mismatch | mismatch | mismatch | mismatch | mismatch（文案统一） | — |

## 4. 设计（已定，不再拍板）

### 4.1 `tests/perf/mutation_framework.py`（新模块，只放执行，不认任何领域）

- **原样搬入**：`ROOT`、`_hidden`、`_TOUCHED`、`_apply`、`_restore`、`_run`、`_run_py`（C1）。只做第 3 节「统一后」要求的改动，其余逐字不动。
- `_apply`：`write` 动作新建前断言路径不存在，并把路径记进模块级 `_CREATED`；`_restore` 删掉 `_CREATED` 里的路径并清空。
- `summary_of(out: str) -> str | None`：纯函数，取**最后一条**含 `passed`/`failed`/`error` 且含 ` in ` 的行；`_run` 改用它。
- `run_matrix(items, *, domain, targets, artifact, driver_rel, root=ROOT, may_skip=frozenset(), pre_skipped=()) -> int`
  - `items`：`(label, target, edits, expect[, marker])`，与现有四个驱动的条目形状相同。
  - `target`：字符串 → `_run(target)`；**可调用对象** → 调用它，返回 `(got, keep, lines, problems)`，`got` 已是判档（route_facts 的 `_ORDER_SWAP` 用这个口子：包一层 `_run_py`，`"OK"`/`"FAIL"`）。这是唯一新开的口子。
  - `domain` 由驱动传入（C12：按全部组算，与 `--group` 无关）。
  - `pre_skipped`：驱动预筛掉的标签（route_facts 的 `RED-container`），原样进产物的 `skipped`。
  - 逐条：编号查重 → `_apply` → `try: 跑 finally: _restore` → 判档、按第 3 节表格归类 → 收尾核 `targets` 的 sha256、`.hidden` 残留、`_CREATED` 残留 → 无 mismatch 才 `lock_coverage.write_artifact(...)`；返回退出码（0 / 1）。
- 不放：任何门、任何变异表、任何领域路径。

### 4.2 各驱动改法

| 驱动 | 删掉 | 留下 | 调用 |
|---|---|---|---|
| route_facts | `_hidden` `_TOUCHED` `_apply` `_restore` `_run` `_run_py` 的本体、`main` 里的循环与收尾 | 变异表、`_baseline_gate` `_alias_gate` `_count_gate`、`--group` / `--with-container` 解析、`RED-container` 预筛、`_ORDER_SWAP` 包成可调用对象 | `run_matrix(..., pre_skipped=被筛掉的标签)` |
| ping | 循环与收尾 | 变异表、`_count_gate` `_baseline_gate` | `run_matrix(..., may_skip={含 B-1 / B-1b 的两条标签})` |
| pg_gate | 循环与收尾、`_CREATED`、`_restore_all` | 变异表、`_count_gate` `_docker_gate` `_baseline_gate` | `run_matrix(...)` |
| alerting | 自抄的 `_apply` `_restore` `_run`、循环与收尾 | 变异表、`--list`、`_baseline_gate`（改用框架 `_run`）、**「变异不许落在域文件上」改成开跑前的门**：遍历全部 edits，路径等于域文件即拒跑、退出码 2 | `run_matrix(...)` |
| card_draft | 全部自写执行代码 | 变异表（附录 B 照抄）、`_baseline_gate`（靶子 `tests/test_card_draft.py`） | `run_matrix(..., domain=domain_of({"M": MUTATIONS}))` |

所有驱动 `import mutation_framework as framework`，不再 `import route_facts_mutations`。

## 5. 步骤

| 步骤 | 内容 | 产出 |
|---|---|---|
| S0 | 第 0 节全部 | 复核记录写进第 11 节 |
| S1 | **改动前**基准：在 `3cc07e7d` 上依次跑 alerting / ping / pg_gate / route_facts 四个旧驱动；每跑完一个，把 `(退出码, 产物文件, MISMATCH 行的标签集合)` 存到 `$env:TEMP\eq\before\<驱动>`，然后 `git checkout -- tests/perf/*_red_lines.json` 还原入库产物；`git status --short` 必须为空 | 四份基准 |
| S2 | 写 `mutation_framework.py`，四个旧驱动改为调用（第 4 节）；先写 `tests/test_mutation_framework.py`（第 7 节），**先红后绿** | — |
| S3 | **改动后**：同样跑四个驱动、同样存到 `...\eq\after\`、同样还原；逐个比对（第 6 节） | 比对结果 |
| S4 | 重写 `card_draft_mutations.py`，跑它，提交 `tests/perf/card_draft_red_lines.json` | 产物 |
| S5 | 第 7 节对账表里 F 系列变异逐条跑（框架新代码上才有，Claude 发 spec 前无法预跑） | 结果贴第 11 节 |
| S6 | 第 8 节测试命令；AGENTS.md / 旧 spec 两处文字；推分支，看分支 CI | CI 号 |

## 6. 等价验收（S1 / S3）

- **比的是同一台机器上的改动前 vs 改动后，不是跟入库产物比。** 入库产物是在 Windows 上生成的；Claude 在 Linux 沙箱实测，同一份旧代码的 ping 产物就和入库的不同（B-1 / B-1b 在 Linux 上是真红、Windows 上是 skip），这是环境差异，不是代码差异。
- 每个驱动三项都相同才算过：
  1. 退出码；
  2. 产物（若写了）逐字节相同：`(Get-FileHash a).Hash -eq (Get-FileHash b).Hash`；
  3. MISMATCH 行里的**标签集合**相同（文案按第 3 节统一过，不比文案）。
- 如果某一项不同，**唯一允许的解释**是第 3 节某一行的统一造成的（例如旧驱动因为取第一条汇总行而判错档）。逐条写清是哪一行、哪条变异，贴两次的原始输出；解释不了就停下报告。
- 某个旧驱动在本机根本跑不出产物（拒跑或有 mismatch），照样比上面三项（产物一项记「双方都没写」），不要为了让它跑出来去改环境或代码。
- Claude 审计时会在 Linux 沙箱把 S1 / S3 独立再做一遍（沙箱已备好 PG 16 与 compose v5.5.1，sha256 与 CI 钉的值一致）。

## 7. 对账表（行为变化｜守它的测试｜让它变红的变异｜改后能触发的具体状态｜预跑）

**K 系列 —— card_draft 接入元锁（Claude 已在 `c4a6364c` + 模拟产物上预跑；`3cc07e7d` 只改 pg_gate 驱动，不影响这些结果，输出见附录 A3）**

| # | 行为变化 | 守它的测试 | 变异 | 触发状态 | 预跑 |
|---|---|---|---|---|---|
| K0 | 正控：产物完整 | `test_lock_coverage.py` 全部 | — | 38 条变异、44/44 覆盖 | 29 passed |
| K1 | 每条判别器都有变异撞到 | `test_discriminators_and_red_lines_are_the_same_set[card_draft_red_lines]` | 产物去掉 M38 | `:309 assert len(saved) == 1` 无人撞 | 红，点名 `:309` |
| K2 | 产物里没有空转变异 | 同上 | 产物加一条空的 `MX` | 变异红但没落进域 | 红，点名 `MX` |
| K3 | 删掉的死判据不许回来 | 同上 | 把 `/run_stream` 坏草稿之后的 `assert len(saved) == 1` 加回去 | 新判别器不在名单、也撞不到 | 红，点名 `:316` |
| K4 | 驱动 ↔ 产物闭包 | `test_every_mutation_driver_has_an_artifact_and_vice_versa` | 删掉产物 | 现在的 CI 红 | 红（即本分支现状） |
| K5 | 草稿契约的 44 条判别器各有专属红源 | 驱动本身 + K1 | 附录 B 的 M1–M38 逐条 | 见附录 A2 每条的「撞到」 | 38/38 RED，44/44 覆盖，四个业务文件还原后 `git diff -- core web` 为空 |

**F 系列 —— 框架收口（新代码上才有，发 spec 前无法预跑；执行方在 S2 先红后绿、S5 逐条跑，Claude 审计时复跑）**

测试都写在新文件 `tests/test_mutation_framework.py`，用合成输入（可调用靶子 + `tmp_path` 里的小域文件），**不起子进程**。

| # | 行为变化（第 3 节） | 测试 | 变异 | 触发状态 |
|---|---|---|---|---|
| F1 | 汇总行取最后一条 | `test_summary_is_the_last_matching_line`：输入用附录 A2 里 M34 的真实输出片段（前面有 `assert any(... "error" in p for ...)`，最后是 `1 failed, 23 passed …`） | `summary_of` 改回取第一条 | 断言源码里同时有 `error` 和 ` in ` |
| F2 | 红源摘要含 `E ` 行 | `test_keep_includes_exception_lines` | 去掉 `"E "` | 非断言异常（如 RuntimeError） |
| F3 | 跑的时候抛异常也还原 | `test_targets_are_restored_when_a_target_raises`（可调用靶子直接 raise） | 去掉 `finally` | 靶子执行中抛异常 |
| F4a | `write` 新建的文件收尾删除 | `test_written_file_is_removed_on_restore` | `_restore` 不删 `_CREATED` | 变异新建文件 |
| F4b | `write` 只许新建 | `test_write_refuses_an_existing_path` | 去掉存在断言 | `write` 指向已有文件 |
| F5 | 空转 → mismatch、不写产物 | `test_vacuous_red_is_a_mismatch_and_writes_no_artifact` | 删掉空转判断 | RED 但红行不在域内 |
| F6 | 编号重复拒跑 | `test_duplicate_labels_are_refused` | 删查重 | 两条同标签 |
| F7 | marker 收串或一串 | `test_marker_accepts_a_string_or_a_sequence`（一串里缺一个 → mismatch） | 只按串处理 | 一条变异要红两条断言 |
| F8a | 登记过的 skip 进 skipped、不进 hits | `test_registered_skip_goes_to_skipped` | 删 `may_skip` 分支 | 汇总行含 skipped |
| F8b | 没登记的 skip 是 mismatch | `test_unregistered_skip_is_a_mismatch` | skip 一律放行 | 同上，标签不在 `may_skip` |
| F9 | 期望 green / OK 进 controls | `test_expected_green_and_ok_go_to_controls` | controls 并进 hits | 反证型变异 |
| F10 | 可调用靶子的判档原样采用 | `test_callable_target_verdict_is_used_as_is`（回 `"OK"`，期望 `"OK"` → 无 mismatch） | 对可调用靶子也走 `outcome` | route_facts `_ORDER_SWAP` |
| F11 | 预筛掉的标签进产物 `skipped` | `test_pre_skipped_labels_land_in_the_artifact` | 丢掉 `pre_skipped` | 不带 `--with-container` 跑 route_facts |
| F12 | 跑不起来是 mismatch | `test_runaway_is_a_mismatch` | 当成 green | 拿不到汇总行 |
| F13 | 收尾核 sha256 | `test_unrestored_target_is_a_mismatch`（monkeypatch `_restore` 为空操作） | 删掉 sha 比对 | 还原失败 |
| F14 | 执行原语与主循环只有一份 | `test_drivers_define_no_execution_primitives`：AST 扫 `tests/perf/*_mutations.py`，不得定义 `_hidden` `_apply` `_restore` `_run` `_run_py`，且每个驱动都调用 `run_matrix` | 在 alerting 里留一份 `def _run` | 有人再抄一份 |
| F15 | alerting：变异不许落在域文件上（门） | `test_alerting_refuses_edits_on_its_domain` | 删掉这道门 | 某条 edits 的路径是 `tests/test_failure_alerting.py` |
| F16 | 四个旧驱动行为不变 | 第 6 节 S1/S3 比对（机器比对，不是 pytest） | 例：`run_matrix` 不按域过滤红行 | 四个驱动各跑一遍 |

对账表之外，**每个变异跑完必须还原**；S5 结束时 `git status --short` 只允许出现本 spec 要改的文件。

## 8. 测试（固定写法）

- 库：`docker compose -f docker-compose.test.yml up -d --wait`（PG 16，55432）。
- 本地只跑受影响的文件：
  ```powershell
  python -m pytest -q tests/test_mutation_framework.py tests/test_lock_coverage.py tests/test_card_draft.py tests/test_card_arc_behaviors.py tests/test_distill_task_api.py
  ```
  再加上五个驱动各自 `_baseline_gate` 里的靶子文件（驱动运行时会自己先跑一遍）。不改前端，不跑 `npm test`。
- 五个驱动本身就是本次的「测试」：S1 / S3 / S4 各跑一次，结论行原样贴第 11 节。
- **不跑本地全量**，报告里不出现全量数字。合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI。

## 9. 规模表与调用点矩阵

**规模（Claude 在 Linux 沙箱实测，Windows 上会更慢）**

| 驱动 | 条目数 | 每条跑的靶子 | 实测总耗时 | 覆盖域 |
|---|---|---|---|---|
| alerting | 14 | `test_failure_alerting.py` | 73 s | 1 文件 |
| ping | 15（Windows 上 2 条 skip） | 3 个锁文件之一 | 91 s | 3 文件 |
| pg_gate | 25 | 2 个锁文件之一 | 31 s | 2 文件 |
| route_facts | 45（含 6 条反证、1 条容器档默认跳过） | 4 个锁文件之一，单条最长约 35 s | 十几分钟量级（本次未单独计时） | 4 文件 |
| card_draft | 38 | `test_card_draft.py`（每条约 2.4 s） | 约 100 s | 1 文件，44 条判别器 |

S1 + S3 共要把四个旧驱动各跑两遍；route_facts 最慢，可以放后台跑，结论行贴回来即可。

**调用点矩阵（行 = 调用 `run_matrix` 的驱动；列 = 可观测输出；格 = 守它的东西）**

| | 退出码 | 产物字节 | MISMATCH 标签集合 | 收尾树干净 |
|---|---|---|---|---|
| alerting | S1/S3 比对 | S1/S3 比对 + 元锁 | S1/S3 比对 | F13 + `git status` |
| ping | S1/S3 | S1/S3 + 元锁 | S1/S3 + F8a/F8b | F13 + `git status` |
| pg_gate | S1/S3 | S1/S3 + 元锁 | S1/S3 | F4a + F13 + `git status` |
| route_facts | S1/S3 | S1/S3 + 元锁 | S1/S3 + F9/F10/F11 | F13 + `git status` |
| card_draft | S4 | K0–K5 | S4（应为空） | F13 + `git status` |

## 10. skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0–S2 | `@search-first` | 动手写任何函数前，先在仓里找现成实现（本次的错就是没找） |
| S1–S6 | `@verification-before-completion` | 每一项「通过」都附本次实际运行的命令和输出原文 |

## 11. 进度（执行方追加）

**Claude web 已完成（`c4a6364c`）**
- [x] 测试搬迁：路由两条用例与 draft 专用桩搬进 `test_card_draft.py`（通用桩从 `test_distill_task_api` import，不复制）；G6 模板的 `"phases": [1, 2]` 拆成本文件的单独用例；删掉一条死判据（理由写在用例注释里，K3 守住它不回来）。受影响三个文件 85 passed（PG 16）。
- [x] 附录 B 的 38 条变异在 `c4a6364c` 上逐条预跑：全部 RED，44/44 覆盖，0 空转；用这份结果模拟写出产物，`test_lock_coverage.py` 29 passed（K0）。
- [x] pg_gate 锚点修复 `3cc07e7d`（C17）。
- [x] 四个旧驱动在 Linux 沙箱的基准（附录 A4）：alerting / route_facts / pg_gate（修锚点后）的产物与入库逐字节相同；ping 不同，原因见第 6 节。

**执行方待做**
- [ ] S0（第 0 节）
- [ ] S1 改动前基准
- [ ] S2 框架 + 四个驱动 + `test_mutation_framework.py`（先红后绿，贴输出）
- [ ] S3 改动后比对（第 6 节三项，逐驱动）
- [ ] S4 card_draft 驱动 + 产物
- [ ] S5 F1–F15 逐条变异结果
- [ ] S6 第 8 节命令输出；两处文字；推分支；分支 CI 号

## 12. 补充

本段改动面内新发现的问题直接修，写进这里；会撞车或需要拍板的停下报告，不自行记账。

## 附录 A —— 扫描与预跑的原始输出

### A1 新覆盖域 `tests/test_card_draft.py` 的全部判别器（`c4a6364c`，`lock_coverage.discriminators`）

```
49 assert dump["situation_behaviors"] == [_B]
50 assert [p["behaviors"] for p in dump["character_arc"]["phases"]] == [[_A], [_C]]
51 assert [p["label"] for p in dump["character_arc"]["phases"]] == ["死要面子", "不再分辩"]
69 assert len(card.situation_behaviors) == 1
70 assert all(p.behaviors == [] for p in card.character_arc.phases)
75 assert card.situation_behaviors == []
76 assert [b.situation for b in card.character_arc.phases[1].behaviors] == ["x", "y"]
77 assert card.character_arc.phases[0].behaviors == card.character_arc.phases[2].behaviors == []
83 assert [len(p.behaviors) for p in card.character_arc.phases] == [1, 0]
84 assert sum("阶段编号不合法" in r.getMessage() for r in caplog.records) == 1
91 assert card.situation_behaviors == []
92 assert all(p.behaviors == [] for p in card.character_arc.phases)
93 assert sum("阶段编号不合法" in r.getMessage() for r in caplog.records) == 1
99 assert len(card.situation_behaviors) == 2
100 assert not caplog.records
106 assert [p.state for p in card.character_arc.phases] == arc
110 with pytest.raises(ValidationError):
116 assert full["title"] == "CharacterCard"
117 assert "phases" in full["$defs"]["DraftBehavior"]["properties"]
118 assert "behaviors" not in full["$defs"]["PhaseState"]["properties"]
120 assert set(draft_schema(group)["properties"]) == set(fields), group
121 assert g6["properties"]["situation_behaviors"]["items"]["$ref"].endswith("/DraftBehavior")
127 assert rows and all("phases" not in b for b in rows)
128 assert CharacterCard.model_validate(dump).model_dump() == dump
180 with pytest.raises(DistillError):
188 with pytest.raises(DistillError):
196 with pytest.raises(DistillError):
210 assert json.loads(out) == KONG_DRAFT
233 assert len(out) == 1
235 assert draft["situation_behaviors"] == KONG_DRAFT["situation_behaviors"]
236 assert CardDraft.model_validate(draft)
240 assert not [p for p in bad if isinstance(p, str)]
241 assert any(isinstance(p, dict) and "error" in p for p in bad)
275 assert len(saved) == 1
280 assert saved == []
281 assert {"status": "error", "message": "蒸馏失败：数据校验错误，请重试"}.items() <= snaps[-1].items()
304 assert resp.status_code == 200
308 assert frames[-1].get("done") is True
309 assert len(saved) == 1
315 assert frames[-1] == {"error": "蒸馏失败：数据校验错误，请重试"}
320 assert '"phases": [1, 2]' in format_prompt_after("G6")
343 assert _calls(r"model_json_schema\(") == {"core/card_draft.py": 1}
347 assert _calls(r"\bcard_from_draft\(") == {"core/distiller.py": 3, "web/routers/distill.py": 2}
365 assert CharacterCard.model_validate_json(row["card_json"]) == card
```

### A2 附录 B 38 条变异逐条预跑（`c4a6364c`，框架原语 `_apply` / `_run` / `_restore`，靶子 `tests/test_card_draft.py`）

「hits」= 落在新域里、且是判别器的红行。M34 那一行的「汇总」就是 F1 要修的错取。

```
M1 RED 10 failed, 14 passed, 1 warning in 2.32s hits=[49, 75, 83] 
M2 RED 1 failed, 23 passed, 1 warning in 2.24s hits=[93] 
M3 RED 1 failed, 23 passed, 1 warning in 2.18s hits=[92] 
M4 RED 4 failed, 20 passed, 1 warning in 2.36s hits=[84, 93] 
M13 RED 9 failed, 15 passed, 1 warning in 2.47s hits=[50, 83] 
M14 RED 8 failed, 16 passed, 1 warning in 2.35s hits=[51] 
M15 RED 9 failed, 15 passed, 1 warning in 2.54s hits=[49, 69] 
M16 RED 9 failed, 15 passed, 1 warning in 2.34s hits=[50, 70] 
M17 RED 1 failed, 23 passed, 1 warning in 2.28s hits=[76] 
M18 RED 10 failed, 14 passed, 1 warning in 2.40s hits=[50, 77, 83] 
M19 RED 2 failed, 22 passed, 1 warning in 2.36s hits=[83, 92] 
M20 RED 3 failed, 21 passed, 1 warning in 2.38s hits=[91] 
M21 RED 1 failed, 23 passed, 1 warning in 2.46s hits=[99] 
M22 RED 1 failed, 23 passed, 1 warning in 2.35s hits=[100] 
M23 RED 6 failed, 18 passed, 1 warning in 2.24s hits=[110, 180, 188, 196, 280, 315] 
M24 RED 1 failed, 23 passed, 1 warning in 2.31s hits=[116] 
M25 RED 17 failed, 7 passed, 1 warning in 2.63s hits=[117, 235, 275, 308] 
M26 RED 1 failed, 23 passed, 1 warning in 2.20s hits=[118] 
M27 RED 1 failed, 23 passed, 1 warning in 2.30s hits=[120] 
M28 RED 1 failed, 23 passed, 1 warning in 2.27s hits=[121] 
M29 RED 16 failed, 8 passed, 11 warnings in 2.34s hits=[49, 70, 76, 83, 92, 106, 127, 365] 
M10 RED 1 failed, 23 passed, 1 warning in 2.36s hits=[106] 
M11 RED 1 failed, 23 passed, 1 warning in 2.44s hits=[106] 
M7 RED 2 failed, 22 passed, 1 warning in 2.37s hits=[49, 347] 
M8 RED 2 failed, 22 passed, 1 warning in 2.29s hits=[235, 347] 
M9 RED 1 failed, 23 passed, 1 warning in 2.29s hits=[343] 
M12 RED 1 failed, 23 passed, 1 warning in 2.32s hits=[320] 
M30 RED 1 failed, 23 passed, 1 warning in 2.46s hits=[210] 
M31 RED 1 failed, 23 passed, 1 warning in 2.32s hits=[233] 
M32 RED 1 failed, 23 passed, 1 warning in 2.29s hits=[236] 
M33 RED 1 failed, 23 passed, 3 warnings in 2.44s hits=[240] 
M34 RED > assert any(isinstance(p, dict) and "error" in p for hits=[241] 
M5 RED 2 failed, 22 passed, 1 warning in 2.38s hits=[49, 347] 
M6 RED 2 failed, 22 passed, 1 warning in 2.44s hits=[49, 347] 
M35 RED 1 failed, 23 passed, 1 warning in 2.52s hits=[281] 
M36 RED 3 failed, 21 passed, 1 warning in 2.39s hits=[106, 128, 365] 
M37 RED 1 failed, 23 passed, 1 warning in 2.40s hits=[304] 
M38 RED 1 failed, 23 passed, 1 warning in 2.55s hits=[309] 
域判别器 44 撞到 44 未撞到 []
```

### A3 元锁侧预跑（K0–K4，模拟产物由 A2 的 hits 经 `lock_coverage.write_artifact` 写出，跑完已删除）

```
K0 29 passed in 1.57s
K1 1 failed, 28 passed in 1.68s
K2 1 failed, 28 passed in 1.66s
K3 1 failed, 28 passed in 1.67s
K4 1 failed, 27 passed in 1.51s
E       AssertionError: tests/perf/card_draft_mutations.py 长出了名单上没有的缺口（判别器还在、但没有任何变异撞它 —— 「变异红 ≠ 判别器起作用」的第四种形态，不会有人发现）：
E           tests/test_card_draft.py:309  assert len(saved) == 1    [同文本第 2 处]
E       AssertionError: tests/perf/card_draft_mutations.py 里这些变异一条判别器都没撞到（空转变异 —— 它红了，但红的不是任何一条判据，红源说不清）：['MX']
E           tests/test_card_draft.py:316  assert len(saved) == 1    [同文本第 3 处]
```

### A4 四个旧驱动在 Linux 沙箱的基准（PG 16 + compose v5.5.1，sha256 `db188918…` 与 CI 钉的值一致）

```
alerting: 全部符合预期。产物已写：tests/perf/alerting_red_lines.json
ping: 全部符合预期。产物已写：tests/perf/ping_red_lines.json
route_facts: 全部符合预期。产物已写：tests/perf/route_facts_red_lines.json
pg_gate（14524a47 原样）:     raise AssertionError(
pg_gate（3cc07e7d 修锚点后）: 全部符合预期。产物已写：tests/perf/pg_gate_red_lines.json  耗时 31s

与入库产物逐字节比对（git status）：alerting / route_facts / pg_gate 无差异；ping 有差异：
  + mutations 新增 "B-1 …" / "B-1b …"（Linux 上真红，各撞 tests/test_storage_ping.py 的 pytest.raises(sqlite3.OperationalError, match="locked")）
  - skipped 去掉这两条（Windows 上它们 skip）
```

## 附录 B —— card_draft 变异表（照抄进 `tests/perf/card_draft_mutations.py`；条目形状与另外四个驱动相同，期望一律 RED、不设 marker）

```python
# 预跑实测：每条在 c4a6364c 上全红，「撞到」= 新域 tests/test_card_draft.py 里被撞到的判别器行号。
TARGET = "tests/test_card_draft.py"
DRAFT, SCHEMA, DIST, ROUTE = (ROOT / "core" / "card_draft.py", ROOT / "core" / "schema.py",
                              ROOT / "core" / "distiller.py", ROOT / "web" / "routers" / "distill.py")

MUTATIONS = [
    # 撞到 [49, 75, 83]
    ('M1  分发：只要有一个合法编号就放顶层（「所有阶段都标了」退化成「标了任一阶段」）',
     TARGET, [("repl", DRAFT, [
         ('if len(valid) == count:',
          'if len(valid) >= 1:'),
     ])], "RED"),
    # 撞到 [93]
    ('M2  warning 条件漏掉「编号全部作废」：整条撤回时不留痕',
     TARGET, [("repl", DRAFT, [
         ('if len(valid) != len(set(row.phases)) or not valid:',
          'if len(valid) != len(set(row.phases)):'),
     ])], "RED"),
    # 撞到 [92]
    ('M3  阶段编号下界 1 → 0：0 号被当成合法编号',
     TARGET, [("repl", DRAFT, [
         ('if 1 <= p <= count',
          'if 0 <= p <= count'),
     ])], "RED"),
    # 撞到 [84, 93]
    ('M4  删掉非法编号的 warning',
     TARGET, [("repl", DRAFT, [
         ('            logger.warning("[card_draft] 做法的阶段编号不合法（共 %d 个阶段）%s：%s",\n                           count, row.phases, row.situation)\n',
          '            pass\n'),
     ])], "RED"),
    # 撞到 [50, 83]
    ('M13  挂阶段时下标算反：阶段 p 的做法挂到倒数第 p 个阶段',
     TARGET, [("repl", DRAFT, [
         ('by_phase[p - 1].append(behavior)',
          'by_phase[count - p].append(behavior)'),
     ])], "RED"),
    # 撞到 [51]
    ('M14  转卡时丢掉阶段的 label',
     TARGET, [("repl", DRAFT, [
         ('    card = draft.model_dump()\n',
          '    card = draft.model_dump(exclude={"character_arc": {"phases": {"__all__": {"label"}}}})\n'),
     ])], "RED"),
    # 撞到 [49, 69]
    ('M15  顶层判据写成 len(valid) > count：标了所有阶段的做法永远进不了顶层',
     TARGET, [("repl", DRAFT, [
         ('if len(valid) == count:',
          'if len(valid) > count:'),
     ])], "RED"),
    # 撞到 [50, 70]
    ('M16  顶层与阶段不再互斥：进了顶层的做法同时挂到各阶段',
     TARGET, [("repl", DRAFT, [
         ('            general.append(behavior)\n        else:\n            for p in valid:',
          '            general.append(behavior)\n        if True:\n            for p in valid:'),
     ])], "RED"),
    # 撞到 [76]
    ('M17  同一阶段内做法倒序（append → insert(0)）',
     TARGET, [("repl", DRAFT, [
         ('by_phase[p - 1].append(behavior)',
          'by_phase[p - 1].insert(0, behavior)'),
     ])], "RED"),
    # 撞到 [50, 77, 83]
    ('M18  有合法编号就挂到所有阶段，不看标了哪几个',
     TARGET, [("repl", DRAFT, [
         ('            for p in valid:\n',
          '            for p in (valid and range(1, count + 1)):\n'),
     ])], "RED"),
    # 撞到 [83, 92]
    ('M19  越界编号被夹到最后一个阶段，而不是撤回',
     TARGET, [("repl", DRAFT, [
         ('valid = sorted({p for p in row.phases if 1 <= p <= count})',
          'valid = sorted({min(p, count) for p in row.phases if 1 <= p})'),
     ])], "RED"),
    # 撞到 [91]
    ('M20  编号全部作废的做法改放顶层，而不是整条撤回',
     TARGET, [("repl", DRAFT, [
         ('if len(valid) == count:',
          'if len(valid) == count or not valid:'),
     ])], "RED"),
    # 撞到 [99]
    ('M21  无阶段的卡丢掉全部做法',
     TARGET, [("repl", DRAFT, [
         ('        if count == 0:\n            general.append(behavior)\n            continue\n',
          '        if count == 0:\n            continue\n'),
     ])], "RED"),
    # 撞到 [100]
    ('M22  无阶段的卡也走编号校验：做法照收，但每条打一次 warning',
     TARGET, [("repl", DRAFT, [
         ('        if count == 0:\n            general.append(behavior)\n            continue\n',
          ''),
     ])], "RED"),
    # 撞到 [110, 180, 188, 196, 280, 315]
    ('M23  形态不对时宽容兜底（situation_behaviors 不是列表就当空列表），不再抛 ValidationError',
     TARGET, [("repl", DRAFT, [
         ('    draft = CardDraft.model_validate(data)\n',
          '    if not isinstance(data.get("situation_behaviors", []), list):\n        data = {**data, "situation_behaviors": []}\n    draft = CardDraft.model_validate(data)\n'),
     ])], "RED"),
    # 撞到 [116]
    ('M24  草稿 schema 标题回到 CardDraft：模型看到的不再是「角色卡」',
     TARGET, [("repl", DRAFT, [
         ('    model_config = ConfigDict(title="CharacterCard")\n',
          ''),
     ])], "RED"),
    # 撞到 [117, 235, 275, 308]
    ('M25  草稿做法丢掉 phases 字段',
     TARGET, [("repl", DRAFT, [
         ('    phases: list[int] = []\n',
          ''),
     ])], "RED"),
    # 撞到 [118]
    ('M26  草稿弧线的阶段改用存卡的 ArcPhase（阶段下带 behaviors）',
     TARGET, [("repl", DRAFT, [
         ('    phases: list[PhaseState] = []\n',
          '    phases: list[ArcPhase] = []\n'),
         ('ArcAxis, CharacterCard, PhaseState,',
          'ArcAxis, ArcPhase, CharacterCard, PhaseState,'),
     ])], "RED"),
    # 撞到 [120]
    ('M27  分组 schema 漏进组外字段（name 进了每一组）',
     TARGET, [("repl", DRAFT, [
         ('if k in fields},',
          'if k in fields or k == "name"},'),
     ])], "RED"),
    # 撞到 [121]
    ('M28  分组 schema 取自存卡 CharacterCard：G6 的做法引用变成 SituationBehavior',
     TARGET, [("repl", DRAFT, [
         ('    full = CardDraft.model_json_schema()\n',
          '    full = (CardDraft if group is None else CharacterCard).model_json_schema()\n'),
     ])], "RED"),
    # 撞到 [49, 70, 76, 83, 92, 106, 127, 365]
    ('M29  转卡跳过校验（model_construct）：phases 标注留在存卡里',
     TARGET, [("repl", DRAFT, [
         ('    return CharacterCard.model_validate(card)\n',
          '    return CharacterCard.model_construct(**card)\n'),
     ])], "RED"),
    # 撞到 [106]
    ('M10  旧卡字符串阶段被当成 label 而不是 state',
     TARGET, [("repl", SCHEMA, [
         ('        return {"state": value} if isinstance(value, str) else value',
          '        return {"label": value} if isinstance(value, str) else value'),
     ])], "RED"),
    # 撞到 [106]
    ('M11  旧卡阶段列表转换时丢掉第一个阶段',
     TARGET, [("repl", SCHEMA, [
         ('        return {"phases": value} if isinstance(value, list) else value',
          '        return {"phases": value[1:]} if isinstance(value, list) else value'),
     ])], "RED"),
    # 撞到 [49, 347]
    ('M7  distiller 同步入口退回 CharacterCard.model_validate（不经 card_from_draft）',
     TARGET, [("repl", DIST, [
         ('            return card_from_draft(data)\n        except ValidationError as exc:\n            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n\n    def distill_stream',
          '            return CharacterCard.model_validate(data)\n        except ValidationError as exc:\n            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n\n    def distill_stream'),
     ])], "RED"),
    # 撞到 [235, 347]
    ('M8  分组流式在 distiller 里就转成卡，交出的不再是草稿',
     TARGET, [("repl", DIST, [
         ('        yield json.dumps(draft.model_dump(), ensure_ascii=False)',
          '        yield json.dumps(card_from_draft(merged).model_dump(), ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [343]
    ('M9  distiller 另起一处 model_json_schema（schema 不再单一出处）',
     TARGET, [("repl", DIST, [
         ('    def distill(self, text: str, character_name: str) -> CharacterCard:',
          '    def distill(self, text: str, character_name: str) -> CharacterCard:\n        _unused = CharacterCard.model_json_schema()'),
     ])], "RED"),
    # 撞到 [320]
    ('M12  G6 提示词模板的示例做法去掉 phases 编号',
     TARGET, [("repl", DIST, [
         (', "phases": [1, 2]}\\n',
          '}\\n'),
     ])], "RED"),
    # 撞到 [210]
    ('M30  长文流式改写了模型原文（交出的草稿被改动）',
     TARGET, [("repl", DIST, [
         ('            yield token\n            tc += 1\n',
          '            yield token.replace(\'"phases": [1, 2]\', \'"phases": [1]\')\n            tc += 1\n'),
     ])], "RED"),
    # 撞到 [233]
    ('M31  分组流式每组回来就交出一段 JSON：交出的字符串不止一个',
     TARGET, [("repl", DIST, [
         ('            group_data[group] = payload\n            yield {"heartbeat": True}',
          '            group_data[group] = payload\n            yield json.dumps(payload, ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [236]
    ('M32  分组流式交出的 JSON 不是合法草稿（character_arc 被写成字符串）',
     TARGET, [("repl", DIST, [
         ('        yield json.dumps(draft.model_dump(), ensure_ascii=False)',
          '        yield json.dumps({**draft.model_dump(), "character_arc": "未定"}, ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [240]
    ('M33  分组流式合并后不校验（model_construct）：坏草稿也交出字符串',
     TARGET, [("repl", DIST, [
         ('            draft = CardDraft.model_validate(merged)\n',
          '            draft = CardDraft.model_construct(**merged)\n'),
     ])], "RED"),
    # 撞到 [241]
    ('M34  分组流式校验失败的错误帧键名不是 error',
     TARGET, [("repl", DIST, [
         ('            yield {"error": user_facing_error(exc)}\n            return\n\n        yield json.dumps(draft.model_dump()',
          '            yield {"message": user_facing_error(exc)}\n            return\n\n        yield json.dumps(draft.model_dump()'),
     ])], "RED"),
    # 撞到 [49, 347]
    ('M5  路由 R1（/start 后台任务）退回 CharacterCard.model_validate',
     TARGET, [("repl", ROUTE, [
         ('            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n            card = card_from_draft(data)\n        except Exception as exc:\n            # ValidationError',
          '            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n            card = CharacterCard.model_validate(data)\n        except Exception as exc:\n            # ValidationError'),
     ])], "RED"),
    # 撞到 [49, 347]
    ('M6  路由 R2（/run_stream）退回 CharacterCard.model_validate',
     TARGET, [("repl", ROUTE, [
         ('            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n            card = card_from_draft(data)\n        except Exception as exc:\n            logger.error("Card validation failed: %s"',
          '            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n            card = CharacterCard.model_validate(data)\n        except Exception as exc:\n            logger.error("Card validation failed: %s"'),
     ])], "RED"),
    # 撞到 [281]
    ('M35  R1 校验失败的上屏文案变了',
     TARGET, [("repl", ROUTE, [
         ('_set_task(task_id, {"status": "error", "message": "蒸馏失败：数据校验错误，请重试", "character": name})',
          '_set_task(task_id, {"status": "error", "message": "蒸馏失败：服务内部异常，请重试", "character": name})'),
     ])], "RED"),
    # 撞到 [106, 128, 365]
    ('M36  旧卡阶段校验器非幂等：每校验一次给 state 加一次前缀（存了再读不等于自己）',
     TARGET, [("repl", SCHEMA, [
         ('        return {"state": value} if isinstance(value, str) else value',
          '        return {"state": value} if isinstance(value, str) else {**value, "state": "（旧）" + value.get("state", "")}'),
     ])], "RED"),
    # 撞到 [304]
    ('M37  /run_stream 的响应码不是 200',
     TARGET, [("repl", ROUTE, [
         ('        media_type="text/event-stream",\n    )\n\n\n@router.post("/reindex/{text_id}")',
          '        media_type="text/event-stream", status_code=202,\n    )\n\n\n@router.post("/reindex/{text_id}")'),
     ])], "RED"),
    # 撞到 [309]
    ('M38  /run_stream 同一张卡落库两次',
     TARGET, [("repl", ROUTE, [
         ('            result = await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n',
          '            await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n                embedding_key=emb.key, embedding_region=emb.region,\n            )\n            result = await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n'),
     ])], "RED"),
]
```
