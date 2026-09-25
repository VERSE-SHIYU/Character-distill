# Character-distill 生产运维

## 部署拓扑

双区独立部署，各自独立 PostgreSQL（无主从同步），各备各的。

- **SZ（深圳）**：`ssh admin@47.107.42.111`，项目路径 `/opt/character-distill`，异地备份目标 = 阿里云 OSS
- **SG（新加坡）**：`ssh ubuntu@43.134.55.201`，项目路径 `/home/ubuntu/Character-distill`，异地备份目标 = 腾讯云 COS
- Compose 文件：两台都是 `docker-compose.prod.yml`（**不是**默认 `docker-compose.yml`）。所有 `docker compose` 命令必须带 `-f docker-compose.prod.yml`，否则报 "no configuration file provided"
- 镜像：`ghcr.io/verse-shiyu/character-distill-app` 和 `ghcr.io/verse-shiyu/character-distill-nginx`，tag = commit_sha（40 位 hex）。另有浮动 `latest` tag（指向旧版，清理时忽略，勿删）

## SSH 接入

- SZ：密钥文件 `~/.ssh/shenzhen_deploy`，用户 `admin`
- SG：密钥文件 `~/.ssh/singapore_deploy`，用户 `ubuntu`
- 两台均有 passwordless sudo
- **禁止用 root 直连**（触发告警）

## 部署方式

- 部署只走 GitHub Actions `.github/workflows/deploy.yml`（手动 `workflow_dispatch`，选 `sz-only` / `sg-only` / `both`）。构建走 `build.yml`（push 触发，只构建不部署）
- 容器版本由 `APP_IMAGE_TAG=${COMMIT_SHA}` pin，重启不会换版本

## 镜像清理规则（防误删）

- **deploy.yml 部署成功后自动清理**：保留「当前运行版 + 上一版（PREV_SHA，rollback 用）」→ 删其余旧 tag + 超 2GB 的 build cache + dangling
- **手动清理铁律**：
  - 绝不删正在运行的镜像
  - 绝不删 PREV_SHA（上一版，rollback 命脉）
  - 绝不 `docker rmi latest`（浮动 tag 删了连累底层）
  - grep 排除列表必须含：`COMMIT_SHA` / `PREV_SHA` / `latest` / `<none>`
- Build cache 是隐形大头（曾达 9.6GB），用 `docker builder prune -f --keep-storage 2GB` 安全清理

## 备份系统

- 两台每日 03:00 cron 自动备份：`. /root/.backup_key; .../scripts/backup.sh`
- 产物：`backups/` 下 db dump + data tar + 加密 `.env`，7 天轮转
- 口令指针：备份加密口令在 `/root/.backup_key`（`chmod 600`），不在仓库、不在对话明文
- 还原演练：`scripts/restore_verify.sh`（临时容器验证，不碰生产库）
- 异地上传（OSS / COS）尚未启用（bucket + RAM 子账号待建），目前备份只在本机

## 安全铁律

- 任何密钥 / 口令 / AccessKey 绝不出现在：命令行参数、shell history、对话输出、git。用 `read -s` 输入，用 600 权限文件 + source 传递
- 改 SSH 配置必须先验证密钥登录可用、保留旧 session，防锁死
- 凡涉及凭据的命令，只给命令骨架，让用户自行填入后续参数

## 开发工作流约束

- **调试脚本不入 main**：一次性复现/调试脚本必须留在 `.gitignore` 覆盖的本地目录（如 `scripts/` 或 `e2e/scratch/`），绝不 `git add` 入库。**但被正文引用数字的探针，其产物经 `tests/perf/evidence_writer.py` 写入 `docs/evidence/` 并登记清单；这不是例外，是该类产物的唯一路径** —— 落点固定、写时按白名单脱敏、登记在写入时完成，三件都由出口强制，不由作者记性保证。正文引用这类数字一律写 `ev:<id>`，契约见 `docs/evidence/README.md`
- **发现 spec 外 bug 先报告**：执行过程中发现未纳入当前 spec 的 bug — 停下，口头报告根因与修复方案，经确认后才单独立项修复
- **存储改动只保证 PG（2026-09-24 起）**：新的存储改动只保证 PG 正确；SQLite 只同步到「接口还能跑」为止，不为它写用例。SQLite 自 2026-09-24 起不再测试、不再维护，计划择期退役。**迁移上有一条例外**：PG 新增**列**时，`storage/migrations/` 里补一份同语义的孪生迁移并登记进 `sqlite_store.py` 的迁移次序表，此外不加。理由是冻结后第一次加列暴露了「接口还能跑」与「不做迁移」互斥 —— 新列没有那个列就谈不上接口能跑，而两侧真库的列集锁（`tests/test_postgres_store.py::TestPgFreshSchemaClosure::test_fresh_sqlite_and_fresh_pg_have_the_same_columns`）要求列集相等且不许开豁免，**列集锁是二者的仲裁**
- **跑测试前先起测试库**：本地一律 `docker compose -f docker-compose.test.yml up -d --wait`，测试连它的 `charsim_test`（55432）。`tests/conftest.py` 会话开始时核一次库名，不以 `_test` 结尾即整场中止 —— 开发库 `charsim` 不再有任何被测试碰到的路径
- **失败必须进 logging，不许只 `print`**：`print` 写 fd 1，既不上面板（`core/log_collector.py` 的 `RingBufferHandler`，收 WARNING+）也不进告警邮件（`core/alerting.py` 的 `AlertHandler`，`ALERT_LEVEL = logging.ERROR`）—— 只 `print` 的失败等于只有翻容器 stdout 才看得见。
  - **往上抛的错误**不必逐处改：`web/server.py` 的全局异常处理器统一记一条带堆栈的 ERROR。**打印后 `raise` 的 `print` 保留不动。**
  - **吞掉的错误**必须走 `core/nonfatal.nonfatal`（异步、整块可失败后继续）或模块 `logger`（同步，或需要给调用方一个兜底返回值）。级别就是「发不发邮件」：**ERROR = 数据没有存进去，或者用户的请求失败了；WARNING = 已经兜底、不影响结果的后台动作**（好感度评估、阅读进度、预热、缓存）。
  - 判据是 `tests/test_failure_alerting.py::test_no_print_swallowed_failures_left_in_production_code`（扫 `storage/postgres_store.py` / `web` / `core`：「`print` 文本含 `fail` 且下一条语句不必然 `raise`」的结果必须为 0）。断言这类日志的用例走 `caplog`，**不要断言 stdout** —— 只断言「有日志」会让 ERROR 写成 WARNING 也照绿，而那一档之差就是发不发邮件
  - 例外不在名单里，在判据里：`core/distiller.py` 那处 print 后跟 `if truncated: raise … / raise …`，属「必然抛出」，由全局处理器接住

## 蒸馏管线

> 本节引用代码一律用符号名（函数/常量/测试名），不写行号。配置与数值现读现取，核不到的标「待验证」，未凭记忆写成断言。

### 一、两条路由（最容易被误解的事）

`distill_incremental_stream`（`core/distiller.py`）按 `_estimate_tokens(text)` 与 `self._longctx_threshold` 分流：

- **低于阈值 → 长上下文单次调用**：直接 `_distill_longcontext_stream`，全文喂一次。**不分片、不写 `distill_chunks`**，中途崩了整本重来（docstring 自述）
- **达到阈值 → MapReduce 分片**：走 `effective_chunk_size` + `_split_chunks`
- 同一分流的非流式版本在 `distill_incremental`
- `_estimate_tokens` = `int(len(text) * 0.6)`（`core/distiller.py`）。配合 `longctx_threshold: 150000` → **约 25 万字符以下的文本全部走整本单次调用**。一本 652990 字符的长篇 → 估算 ~39 万 > 15 万，才进分片

**结论**：断点续跑（`distill_chunks` 落库 + 三重门复用）**只在分片路径生效**；短文本从来不进这条路，也就永远不产生分片检查点。

### 二、模型与参数现值

config.yaml 现值（现读，非转述）：

- `distill.chunk_size: 5000`
- `distill.longctx_threshold: 150000`
- `llm.max_tokens: 4096`
- `llm.model: deepseek-v4-pro`
- `llm.temperature: 0.7`
- 另有 `rag.chunk_size: 500` —— **是 RAG 检索切块，与蒸馏分片无关，勿混**
- `map_concurrency` 不在 config.yaml → 走代码默认 **30**
- classic 档分片强制放大到 ≥6000：`effective_chunk_size`

**模型规格**：deepseek-v4-pro 上下文 1M token / 最大输出 384K —— **待验证**（代码与仓库内无此声明，来自口述的「官方 GA 0813」，未找到出处）。

**chunk_size 的来历**：**待验证·无记载**（证据：`ev:chunk-size-provenance`）。「实测对比 3000/4500/6000/12000 四档、以卡片质量对标整本喂、12000 太慢 3000 太碎」——`git log -S 4500` / `-S 12000` 有命中，但逐处核对后**无一处把这两个数当 chunk_size**（命中的是本节自述行、`core/distiller.py` 的 `max_profile_len` 下限、`web/frontend/e2e/avatar-fallback-verify.cjs` 的 `settleMs`）；`docs/` 与 `.claude/sessions/` 亦无记录。可查到的只有：3000 起于 2026-05-19（commit `5aee0609`），代码默认 3000（`distill_cfg.get("chunk_size", 3000)`），现值 5000 与 `longctx_threshold: 150000`（均出自**未入库**的 `config.yaml`，见缺陷 14 的 `ev:config-yaml-values`），classic 地板 6000（`effective_chunk_size`）。结论：**暂定值，无统一标准**。扫描口径与逐处核对写在清单条目的 `notes` 里。

**max_tokens 的来历**（数值轨迹有据，「为什么最终是 4096」无量化依据）：

| 日期 | commit | 值 | 标题 |
|---|---|---|---|
| 2026-05-16 | `c2086347` | 4096 | 首版全链路 |
| 2026-05-19 | `5aee0609` | 4096 | chunk_size→3000 |
| 2026-05-19 | `8955a683` | **16384** | 蒸馏卡死 — max_tokens 4096→16384 |
| 2026-05-20 | `70bd104f` | **1024** | 摘要后台化 + 回复长度规则 |
| 2026-05-20 | `24855554` | **4096** | max_tokens 修复 |

→ 停在 4096 只留了 commit 标题，**没有量化依据，待考**。

**关掉思考后的实测基线**（2026-09-10，方言层落地 `f2dfd23` 之后）：

同一批 14 次 map 调用，真实语料 + 生产提示词，同一份探针（`tests/perf/map_len_probe.py`；探针内把 `max_tokens` 抬到 8192，测的是**自然输出长度**而非被截断后的长度）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `out_tokens` p50 / max | 8191 / 8192 | **1245 / 2097** |
| `out_chars == 0`（空正文） | 3 | **0** |
| 撞 8192 探针上限 | 7 | **0** |
| 单次耗时 min–max | 12.0–160.0s | **1.4–31.3s** |
| 耗时 > 45s（生产单次 ceiling） | 12/14 | **0/14** |
| 耗时 > 60s（生产 deadline） | 11/14 | **0/14** |
| tokens / 正文字符 | 3.85 | **0.62** |

**口径**：n=14、单模型（deepseek-v4-pro）、单供应商、`temperature` 取生产默认 0.7。探针在 scratch 内把 `_GEN_ATTEMPT_S` / `_GEN_DEADLINE_S` 抬到 120 / 240 以测「模型自然输出」，故表中 45s / 60s 两行是**按生产口径回算**的，不是探针真实超时。修复前 / 修复后原始产物见 `docs/evidence/thinking-maplen-before.json` / `docs/evidence/thinking-maplen-after.json`（证据：`ev:thinking-maplen-before` / `ev:thinking-maplen-after`），脚本、前置配置与逐行复算口径见 `docs/evidence/thinking_budget_evidence.md`。

**4096 维持结论**：`max_tokens=4096` 不动 —— 0/14 达到其 80%（≥3277），max 2097，约 2× 余量；且已有环境变量出口（`_resolve_max_tokens`，`adapters/llm_adapter.py`，阶梯=显式 arg > `LLM_MAX_TOKENS` > config.yaml > 4096），要动不用发版。另：3 片 `out_chars=1` 是模型按提示词答「无」（U+65E0），**属正常应答，不是缺陷**。

### 三、已知缺陷

> **全表状态口径（2026-09-24 现跑现数）**：**全表 125 条**（＝从 1 到当前最大号的全部条目行，不再只算前 98 条）—— **已修 111**（含 32、33；40：commit 一 `53bed63` + commit 二；42：`7009d77` → `3e2670d` → `2c9fee9` → `fbb9066` → `efa36a6` → `c959553` → 结案四提交 → 收口一提交；59–63 与 65 同批修完，锁在 `tests/test_llm_access_gate.py`；35：`dc7b09f` + `34bf075`；36：`0605bdf` → `ef43b38`；**本轮 2026-09-22 收口：83：`15c6a6a` + `8efb67c` + `b259e82`、84：随 `8efb67c`、85：`238610d` + `680eafe`**；**本轮 2026-09-23 收口（六条从「记账」移入「已修」）：Spec B 五条 —— 68：`f7fd0b6`、69：`17626d6`、95：`c417827`、56：`c826ed8`、57：`8580286`；Spec 82 一条 —— 82**；**本轮 2026-09-23 收口（一条从「记账」移入「已修」）：Spec 80/81 一条 —— 81（80 仍留「记账」桶，见其条目：PG 不可达，按主次不修）**；**2026-09-24 扩为全表时的对齐**：旧行标着「只覆盖 1–98」且与条目实际状态有出入（旧行写 1–98 已修 75 / 记账 12，条目现状态是 1–98 已修 87 / 记账 3），本轮按现数配方重算、范围放宽到全表，把 58、64、66、67、73、74、86、87、88、89（旧行记在「记账」/「另开议题」）与 94（旧行单列「可见性已修、口径待定」）按条目现状态核入已修；99 以上一并核入：99（见本线 commit `5148ab6`）、100（`e8906f6` + `0ec13ce` + `8942c3f`）、101（`2c7b815` + `e8906f6` + `0ec13ce`）、102（`d14fe29`）、103（`d14fe29`）、104（`04dc4ef`）、105（`9fae212`）、106（`de49658`）、107（`dbdbf9c` + `7a5992a`）、108（`dbdbf9c` + `b728ca8`）、109（`dbdbf9c`）、111（`5947d18`）、113（`8dfb1f8`）、114（`841b113`）、115（`bd685e2`）、116（`4950936`）、117（`70cdba1` + `23ac407`）、118（`6c8d805`）、120（`2af4ee8`）、124（`015cae9`）—— 各条明细仍以其条目为准，此处只记本行这次对齐；下面 99–104 / 105–107 / 108–113 / 114–116 / 117 各条注里「整行重算留到下次收口」的顺延，本轮即该次收口；**2026-09-24 并入 main 后的再对齐**：main 已把 121（`cd756a6`）、122（`cd756a6` + `3d0c07a`）标为已修，本线原记「记账」，按条目现状态核入已修 —— 本行由「已修 108 / 记账 8」变为「已修 110 / 记账 6」）**/ **记账 3**（80、112、123）/ **部分修复 1**（119：只做到「把 `print` 换成日志」，三项残留归 71 + 123）/ **已裁定 5**（3 不修、31 保留、47 上游跟踪、50 结案、70 不设锁）/ **已移出 1**（10，见「三之二」）/ **证伪 1**（37）/ **另开议题 1**（71，见「三之三」E）/ **并入 107 1**（110）。**待办 = 记账 3**（119 从记账移入「部分修复」，不在待办里）。**2026-09-24 再对齐一次**：90 结案（PG 侧 `5148ab6` 已修、SQLite 侧判为不修，见该条）→ 本行由「已修 110 / 记账 6」变为「已修 111 / 记账 5」，记账桶去掉 90。 **同日再对齐（119）**：本条由「已修」改判「部分修复」（只做到把 `print` 换成日志，三项残留归 71 + 123）→ 本行由「已修 111 / 记账 5」变为「已修 111 / 记账 4 / 部分修复 1」。 **同日再对齐（96）**：96 随 Spec 96 `96-session-identity-and-timezone` 收口 → 本行由「已修 111 / 记账 4 / 部分修复 1」变为「已修 112 / 记账 3 / 部分修复 1」，记账桶去掉 96。**2026-09-24 再对齐（Spec 71 A-2 首段）**：71 由「另开议题 1」改判「部分修复」（33 处已修，余 `chat.py` 3 + `history.py` 12 与收尾段的 route lock）、80 与 112 由「记账」改判「已裁定·不修（SQLite 专属）」→ 本行由「已修 112 / 记账 3 / 部分修复 1 / 已裁定 5 / 另开议题 1」变为「已修 112 / 记账 1 / 部分修复 2 / 已裁定 7」，**待办 = 记账 1**（123）。（合计仍 125：112+1+2+7+1+1+1） **2026-09-24 再对齐（Spec 71 A-2 尾段）**：71 由「部分修复」移入「已修」（尾段 `7dad544` + `5af404b` + `d5b0c92`：17 处删净 + route lock 就位）→ 本行由「已修 112 / 部分修复 2」变为「**已修 113 / 记账 1 / 部分修复 1 / 已裁定 7**」，**待办 = 记账 1**（123）。（合计仍 125：113+1+1+7+1+1+1） **2026-09-24 再对齐（Spec 123 及其补充 1）**：123 由「记账」移入「已修」（`1e6be37` + `33984c4` + `12268e0`，补充 1 又补 `3c3d712` + `5e91497` + `87e6f79`）→ 本行由「已修 113 / 记账 1」变为「**已修 114 / 记账 0 / 部分修复 1 / 已裁定 7**」，**待办清零**。（合计仍 125：114+0+1+7+1+1+1） 64 的余项在同轮补完（`12268e0`，见其条目），不改变本条目的桶。 **2026-09-25 再对齐（Spec 119 残留）**：119 由「部分修复」移入「已修」（残留四处分别由 `17db7e4` + `597aa1f` + `f99dad6` + `7367b9a` 解决：19 处 print 换 logger（spec 记 18，见条目读数）、判据改按结构判定、路由锁补关键字写法、M11–M13 钉住新判据）→ 本行由「已修 114 / 部分修复 1」变为「**已修 115 / 记账 0 / 已裁定 7**」，**待办清零**（123 已在 `1e6be37` + `33984c4` + `12268e0` 等收口）。（合计仍 125：115+0+7+1+1+1）
> **上面两处顺延（75–82；35 / 36 与 83–90）已由这次重算销账（2026-09-22）** —— 那一行改标现数日期后，75–79 / 91 / 92 与 35 / 36 已在「已修」桶内、80–82 与 86–90 在「记账」桶内、83–85 随本轮收口从「记账」移入「已修」，都不必再逐条另述。**同轮还有 93 / 97 / 98 移入「已修」、94 单列「可见性已修、口径待定」**（上一次重算漏了这四条，桶数因此虚记 —— 与「83–85」同形，一并销账）。**那两句顺延句已删**：它们留着会与重算后的行当场矛盾（「那行仍标着 2026-09-19」「35 已算多」），正是 §四「台账状态行不是事实」的老毛病；按 120 行那次重算的先例，理由失效即自然删除。
> **99–104 是 `published_from` 一案（本分支）并入 main 时按 main 现有最大号**顺延**过来的六条**（并线时本线原编 86–91，与 main 的 86–91 撞号 —— 撞的是编号不是内容，按「按 main 上现有最大编号顺延」改号，两边条目都留）。状态：**已修 6**（99–104）/ **记账 0**（**99 已于 2026-09-23 随见本线 commit `5148ab6` 从「记账」移入「已修」**：PG 侧补了 applied 表；SQLite 侧没有对应机制，那一面归 90）—— **本条原写「不重算上面那行的桶……整行重算留到下次收口（此处只写顺延、不改数）」，该顺延已于 2026-09-24 销账**：上面那行当日重算并放宽到全表，99–104 已逐条核入「已修」，见其正文。**同轮订正**：原本括注的「那一面归 90，故 90 仍记账」也已过期 —— 90 已于 2026-09-24 结案（PG 侧 `5148ab6` 已修、SQLite 侧判为不修，见该条），行内数字随之改标「已修 111 / 记账 5」。**注意本分支已推送的 commit message 里仍写着旧号 86–91**（`d17ee54` / `ef6deb6` / `04dc4ef` / `b86b1fd`）：推送后不改写历史，故那几处是历史坐标，以台账现号为准。
> **本行的重算已执行（2026-09-17）**：触发条件（本轮收口批次 30 / 43 / 54 全部走完）已满足 → 整行按现数重算 → 原先那句顺延（「**不逐条订正**：本轮批次里的 30 / 54 尚未收口，今天改完明天又滞后」）**理由随之失效，自然删除**。**顺延本身是正当的**（判据没收口时逐条订正，明天又滞后），**错的是它当时兜着一个事实错误**：30 已于 `885735c` 收口、54 已于 `23fb813` 收口，却写成「尚未收口」—— 与同一行前半的「记账待补 0（30 已于 `885735c` 收口）」当场自相矛盾。**要顺延就写顺延，但顺延句里不许出现会过期的断言**；断言会过期，就是「台账状态行不是事实」的又一次显形。任何「还剩几条 / 某条什么状态」一律走下一行的现数配方。
> **105–107 是 2026-09-22 新记的三条**（`users.role` 一案并入 main 后、合并前补齐项里顺带读出的），按 main 现有最大号顺延：**105 归 Spec 2、106 归蒸馏线、107 归 D 组**。同样**不重算**上面那条口径行（它标着 1–98 现跑现数，这三条产生更晚）。**订正（2026-09-22，72 线收尾）**：原写「状态全部『记账』」已过期 —— **107 已修**（`dbdbf9c` + `7a5992a`，由 72 线第 1 步的 503 门收口）；105 / 106 仍为记账。**同轮还按 main 现最大号顺延补记了 108–113 六条**（§三 末，72 线第 1 步收尾）—— 原拟 75–80，与 main 现有的 75–80 **撞号**，按「按 main 上现有最大编号顺延」改号（撞的是编号不是内容）。**判据是编号而非行序**：本文件编号跨小节、**与行序不同调**，按行尾取 max 只会取到 74 而漏掉真正最大的 107。
> **本分支（`users.role` 一案）并入时补记的三条，按 main 现最大号顺延为 114–116**（原编 108 / 109 / 110，与 main 已在 §三 末占用的 108–110 **撞号** —— 撞的是编号不是内容，两边条目都留）：**114 归 Spec 3（紧接本份）、115 归前端通用组件、116 归蒸馏线**；状态**全部记账**（只报告不修）。**105 / 106 订正**：上面那条「105 / 106 仍为记账」已过期 —— 两条随本分支修完，**105 已修**（`9fae212`）、**106 已修**（`de49658`），见 §三 对应条目。同样**不重算**上面那条口径行（它标着 1–98 现跑现数，这三条产生更晚）。
> **117 是 2026-09-22 按 main 现最大号顺延新记的一条**（CI 在 `Run tests` 挂死，**当天已发生两次**，见 §三 末），归属 CI / 测试基础设施，状态**记账**。**同轮订正两条**：**115 已修**（`bd685e2` —— 只报告不修的例外，本份用户明确要求「修到根上」）、**116 的归属改判为 Spec 3**（原记蒸馏线）。同样**不重算**上面那条 1–98 口径行。**订正（2026-09-23）**：上面「当天已发生两次」与「状态记账」两句都已过期 —— 实际**四次**挂起（run `35703112463` / `35716206496` / `35747175886` / `35747536487`），已定位并在 `70cdba1` + `23ac407` 修复、经 `35750857228` 验收全绿，状态**已修**；详见 §三 该条。
> 引用任何「还剩几条 / 某条什么状态」之前**现数一遍**：取所有 `^\*\*(\d+)\. ` 的标题行，抽出 `状态：\*\*(.+?)\*\*`。**范围＝全表**（从 1 到当前最大号的全部条目行，口径行本身也按全表报，不再只算前 98 条 —— 只算前 98 会把 99 以上的未结条目藏起来，待办数看着比实际小，正是「台账状态行不反映事实」的又一次显形；2026-09-24 起本配方与那条口径行都按全表算）。**分组按主词，不按字面值** —— 「已修（commit `x`）」「已修（2026-09-13）」属同一个「已修」桶，括号里的是附注不是类别；照字面值分组与头部声明的桶**不是一个口径**（`2026-09-17 现数：字面值 21 组、主词 7 桶`），那时先怀疑分组口径而不是台账。**格式不变式：每条标题行必须带 `状态：`、且状态值用 `**` 加粗、`N.` 后带空格** —— 否则该条会从这次统计里**静默消失**（字段缺失不报错，正是 §四 那条「缺口不会自己报错」）。**禁用「已修 1–33」这类区间表述** —— 30–33 全在记账桶里，一个区间就把整桶抹掉；**摘要与台账不一致比缺陷本身贵**：照摘要决定下一步，会直接漏掉四条。**引 commit 的体例：判据是「那个 commit 在不在 main 里」，不是在不在本分支里** —— 只要 `git merge-base --is-ancestor <sha> origin/main` 不成立，本条就不写 sha，改用 **commit subject + 日期**指认（例：见本线 commit `fix(distill): stop rendering identify failures as an empty roster`，2026-09-22），**sha 在合进 main 时统一回填**。理由：分支合入前至少要 rebase 一次，rebase 重写 sha —— 引用写下的当天是对的，合入后即悬空，读的人照 sha 去 `git show` 会一无所获，而 subject 是 rebase 不会改的内容。先例：125 / 86 的 `13e12d8`、89 的 `e1d3cbc` 就是这么坏的（都是 rebase 后的残骸，`git rev-list --all` 不含它们）。扫一遍本分支用没用到悬空 sha 的判据：`git cat-file -t <sha>` 报不到对象 → 悬空；报得到对象但 `git merge-base --is-ancestor <sha> origin/main` 不成立 → 未合入，同按本规则改。

**1. thinking 参数写错（方言不对）** —— 状态：**已修**（commit `f2dfd23`，2026-09-10）
- 现象（实测）：3 条探针里 2 条吐 `reasoning_content` 12441 / 12413 字符、`content` **0** 字符、`finish_reason='length'`，耗时 161.9s / 122.4s（第 3 条 reasoning 8735、`content` 2060、`stop`、131.5s）。产物见 `docs/evidence/thinking-capfield.json`（证据：`ev:thinking-capfield`）
- 根因（修复前）：四处调用点都传 `extra_body={"enable_thinking": False}`——那是 Qwen 方言，DeepSeek 不认、静默忽略。DeepSeek 写法是 `extra_body={"thinking": {"type": "disabled"}}`（外部文档，见本节末来源）
- 后果链：思考默认开启（effort=high）→ 思考与正文**共享** `max_tokens` 预算 → 思考吃光预算 → `content` 为空 + `finish_reason='length'` → 落一条空串行 → 前端显示「本片无信息」。且思考模式下 `temperature` / `presence_penalty` 被忽略（同来源，修复前传的值不起作用）
- 连锁：`llm.max_tokens=4096`，而修复前实测思考单项就 >8k token → 大分片的失败形态不只是空 content，还大概率直接撞 `_GEN_DEADLINE_S` 超时（见缺陷 8）
- 修法：收敛为单一控制点。`_THINKING_DISABLED` 一张表表达「意图 → 方言 payload」，`_detect_dialect` 由 `base_url`（优先）/`model` 解析供应商，四个调用点只调 `_request_options()`。**未知供应商不传 extra_body**（安全默认：宁可开着思考，也不发一个可能被 400 拒的未知字段）。`base_url` 用户可配这点按方言判断处理，没有一刀切
- 回归锁：`tests/test_llm_adapter_thinking.py` 断言三种 base_url 的 wire format + 四个调用点都接上控制点（修复前该文件断言的恰是**错的**方言）

**2. 适配器从不读 `finish_reason`** —— 状态：**已修**（commit `64d2d14`，2026-09-10；`content_filter` 等未完成值收严于 `d068242`）
- 证据（修复前）：全仓生产代码零命中，仅测试 mock 出现（`tests/perf/mock_llm_server.py` 的 mock 分支）
- 后果（修复前）：截断/饿死的响应被当成功返回并落库
- 实测证据：`docs/evidence/incomplete-v5.json`（证据：`ev:incomplete-v5`）——52 字节半截内容落满 6 片、二次续跑 map 调用 = 0（第二道门判「非空」即复用）。产物于 2026-09-12 从 scratch 迁入，只留统计量，正文段按白名单挡在库外
- 修法：`_check_finish_reason` 为唯一裁决点。`_INCOMPLETE_FINISH_REASONS` = `{length, content_filter, insufficient_system_resource}` → 抛 `IncompleteResponseError`（带 `finish_reason` 字段）；`stop` / `tool_calls` 放行；**真正陌生的值**与缺失点名 WARN 后放行（供应商语义确实不一，不阻断——但 `content_filter` / `insufficient_system_resource` 不是陌生值，是已知未完成终态，放行等于当成功）
- 四个提取点收敛：`chat` / `async_chat` → `_extract_content`，`chat_with_tools` → `_checked_message`，`chat_stream` 每 chunk 先校验再吐、流尽仍无终态判缺失
- 可辨性：截断（`IncompleteResponseError`）≠ 网络失败（`RuntimeError`）≠ 空内容（放行但返回 `""`）
- 处置建议分两张表：`_INCOMPLETE_ACTIONS`（运维口径：抬预算 / 改输入 / 可重试）与 `_INCOMPLETE_USER_MESSAGES`（上屏口径，经 `IncompleteResponseError.user_message` 取）
- SSE 侧：`web/routers/chat.py` 的 `_stream_error_payload` 给未完成终态附 `code=incomplete_response` + `finish_reason`，前端 `web/frontend/src/api/client.js` 透传为 `AppError.code`
- 注：截断**不会**让 SSE 硬断流——外层 `except` 早已 yield 错误帧，缺的是可识别性而非连接存活性
- **截断自愈接回重修环**（2026-09-11）——本层引入的回归，同轮修掉：
  - 回归形态：`_parse_json_with_retry` 本是**截断感知**的自愈路径（`_looks_truncated` 判形 → 发「上次输出因长度超限被截断，请精简」的重修 prompt）。本层让 `chat` 对 `length` 先抛，半截文本永远到不了那里 → **为截断专门建的自愈环，主触发路径不可达**，只剩「模型自然 stop 但 JSON 不完整」还能自愈；且环内两处宽泛 `except` 会把重修过程中的截断吞成一句泛错误。受影响的是 `distill` / `distill_longcontext` / `distill_format` 三个非流式站点
  - 性质：不是新增失败，是把**静默降质**换成了**显式报错**（以前半截卡照样生成、缺一截没人知道）。所以本轮是「保持诚实，把可用性拿回来」，不是回退裁决
  - 修法：`IncompleteResponseError` 携带 `content`（**只作属性、不进 message**——message 经路由层截首行上屏）；新增边界出口 `incomplete_response_info`（与 `llm_error_payload` 同构，core 侧不 import 异常类）；`_chat_initial` **一处**解包（`length` + 非空正文才当截断证据，其余原样上抛），三个调用点只换两行、usage 记账原位不动；`upstream_truncated` 只预置 `_parse_json_with_retry` 里 `truncated` 的初值——**自愈环零复制，接的是触发器不是逻辑**；环内两处 `except` 前置一条只认未完成终态的分支（把「重修也被截断」记准并置 `truncated`，不改上限）
  - **重修预算对齐**：重修调用原不传 `max_tokens` → 落适配器默认（`LLM_MAX_TOKENS` / config，4096），只有初次（`CARD_MAX_TOKENS` 8192）的一半。重修 prompt 要求「精简输出」但短不到一半，预算砍半把「重修又被截断」从边缘情形变成常见情形——故显式传 `CARD_MAX_TOKENS`，与初次一致
  - 上限不变：仍是 3 次尝试（1 初次 + 2 重修），到顶后抛的是「超长被截断」而非「输出格式异常」（重修截断也置 `truncated`）
  - **明确不动**：`async_chat`（map 的恢复粒度是「整片重算 + checkpoint/续跑」，接重修会与 `_resume_hit` 指纹缓存协议打架——修好的片不是缓存的片；且 map 产物是散文分析非 JSON，修复 prompt 不适用，等于另写第二份自愈）；`chat_stream`（token 已逐片交付、SSE 已渲染，半截已过界，重修只会双份渲染；且流式消费方不在 `_parse_json_with_retry` 这条链上）
  - 回归锁：`tests/test_distiller_truncation_selfheal.py`（4 条）。**回环内的截断信号必须经真实 `_extract_content` 产出**——直接构造异常会绕过 content 传递链，使「去掉 content」的变异测不出来（实测：直接构造时该变异只红 adapter 用例，正向自愈用例绿；改走真实提取点后 3 条齐红）
  - **明确不在本期**：`identify_characters` 不走 `_parse_json_with_retry`（自带 `_parse_list` + 一次重试，且要的是 list 不是 dict，形状契约不同）→ 无环可接，本轮不动
- 回归锁：`tests/test_llm_adapter_finish_reason.py`（含变异验证）、`tests/test_chat_stream_error.py`

**3. 第二道门是「非空」门，不是「结构合法」门** —— 状态：**已裁定·不修**（2026-09-22）—— 有意设计的纵深防御：主屏障在上游（失败 Map 片**不落 checkpoint**），门的范围由 `tests/test_distill_resume.py::test_truncated_nonempty_result_passes_second_gate` 的活断言锁定，放宽或收紧都由它说话。
- `_resume_hit` 门 2：`if not (isinstance(cand["result"], str) and cand["result"].strip()): return None`
- 只判非空，挡不住非空的截断文本；docstring 自述即如此（`_resume_hit` docstring：「第 2 道是**纵深防御**，不是契约……不承诺结构校验」）
- 三门（`_resume_hit`）：门 1 形状、门 2 非空、门 3 指纹
- **主屏障在上游，不在本门**：`distill_incremental_stream` 的失败 Map 片不落 checkpoint（抛异常即跳过 `on_chunk_done`），正常路径下这里不该出现空串候选。本门只挡「任何路径往 checkpoint 写入空结果」这一类错误
- Map 返回自由文本角色证据、不产 JSON —— 「半截 JSON」不是本门的场景（曾如此误写）
- **边界约束：`core/` 内任何位置（含注释与 docstring）不得出现 adapter 层异常类名**。描述边界用语义表达（「该层遇未完成终态即抛异常」），类名只留在 adapter 层
- 该约束由 `tests/test_chat_stream_error.py::test_no_exception_class_leaks_into_core_web_storage` 强制（**文本级 grep——注释与 docstring 也拦**）
- 门的**范围**由活断言钉住：`tests/test_distill_resume.py::TestResumeHitDoors::test_truncated_nonempty_result_passes_second_gate`（非空截断的自由文本必须穿过、不得被拦）——给本门加结构校验会让它变红

**4. 改原文后旧分片永不刷新** —— 状态：**已修**（commit `9611ce3`，2026-09-12）
- 根因：两个 store（`storage/sqlite_store.py:3507`、`storage/postgres_store.py:3081`）的 `save_distill_chunk` 都是 `INSERT ... ON CONFLICT (task_id, chunk_index) DO NOTHING`
- 现象：原文变 → 片指纹变 → 门 3（`_resume_hit` 第 3 道）拒绝复用 → 重跑该片 → 新结果想写回，撞 `DO NOTHING` → 库里仍是旧 `result` + 旧 fingerprint → 下次续跑门 3 再次拒绝 → **该片每次续跑都重跑，永不收敛到满复用**
- 影响面：只掉「省调用量」，不掉正确性——当前轮 reduce 吃的是内存里的新结果（`distill_incremental_stream` 内 `map_results.append` → `raw_analyses`），落库副本陈旧只影响下次续跑的复用判据
- 修法（**语义差异写进两处 docstring**）：`DO NOTHING` → `DO UPDATE SET result / chunk_fingerprint`（SQLite 用 `excluded.`，PG 用 `EXCLUDED.`）。**联合 PK 仍保证不重复行**（同一 `(task_id, chunk_index)` 恒只有一行），变的只是：同一片允许被更新的结果覆盖。`created_at` 不动，保留该片首次落库时间
- 验收锁：`tests/test_distill_resume.py::TestChangedChunkConverges`（走**真实 SQLite store**，不是内存 dict —— 病灶在落库语义不在门逻辑）：等长替换改原文 → 续跑一次（该片重跑并写回）→ 再续跑 **零 Map 调用**、产出与「直接全量跑改后原文」逐字节一致
- 契约锁：`test_storage.py` / `test_postgres_store.py` 各两条（`_last_write_wins` / `_replaces_same_index_no_duplicate_row`）——原为 first-write-wins，已随契约翻转
- 变异验证（实测红，SHA256 逐字节还原）：sqlite 改回 `DO NOTHING` → 三条齐红，且 `TestChangedChunkConverges` 红在**目标断言**上（`assert 1 == 0`，该片第二次续跑仍在重跑 = 永不收敛）
- **双 store 分别验**：SQLite 本地跑；PG 用一次性容器（`postgres:16-alpine`，本机空闲端口 + 运行期生成的一次性口令）建真库实跑 —— `tests/test_postgres_store.py` **45 passed**（本地无 `DATABASE_URL` 时这 45 条恒为 error）。验毕 `docker rm -fv` 连匿名卷一起删，`character-distill-postgres-1` 未受触碰

**5. sqlite 新库缺 `users.embedding_key` / `embedding_region`** —— 状态：**已修**（commit `dae4928`，2026-09-11）
- 病灶两层：(1) `storage/migrations/067_embedding_config.sql` 用 `ALTER TABLE users ADD COLUMN IF NOT EXISTS ...`——SQLite 无此语法，`executescript` 解析期即抛 `near "EXISTS": syntax error`；(2) 执行块的 `except` 只吞「duplicate column」，这条错误串不匹配 → 打一行 print 就放过。**报错真的发生了，被按字符串匹配漏掉，静默继续**
- 后果：新建 sqlite 库永远缺 `users.embedding_key` / `embedding_region` → `get_user_api_config` 的 `SELECT u.embedding_key` 抛 `OperationalError` → 500。生产是 PG（该语法合法）不受影响；受影响的是新开发环境与 `start_all.bat`
- 修法：067 的 SQLite 版去掉 `IF NOT EXISTS`（PG 侧 `storage/migrations_pg/002_embedding_config.sql` 独立、不动）；执行块改 `PRAGMA table_info(users)` **前置判断**——读出现有列，缺哪列 ALTER 哪列，不缺就跳过。**确定性执行取代猜错误串**；该块不再有 except，迁移真失败照常上抛，不再静默
- 回归锁：`tests/test_sqlite_fresh_schema.py`（建真库跑迁移，不是正则扫文件）：(a) 新库列齐 (b) `get_user_api_config` 不抛 (c) 同库两次 init (d) 建库 stdout 无失败行——一处断言兜住全部迁移文件 (e) 半成品库（一列有一列无）只补缺列
- **盲区说明**：`tests/test_schema_parity.py` 是正则扫 `.sql` 文本、「写了」就算「跑成了」，本缺陷正是该盲区的实例——补齐它需要真建库的用例（即上方回归锁）
- 迁移执行区曾有 **75 处「失败只 print、从不重抛」**（56 处靠错误串判断可否忽略、19 处裸吞），是本病灶的同族存量——**已于 2026-09-12 随缺陷 15 一并消除**（该区重写为单一应用器，不再有任何 except）

**6. `/api/admin/tasks` 读内存不读库** —— 状态：**已修**（commit `bac7133` / `254455c`，2026-09-11）
- `web/routers/admin.py` 的 `admin_tasks`：原 `from routers.distill import _tasks, _task_lock`；`{"task_id": tid, **task}` 把内存条目**原样展开**
- 泄漏内部字段：条目由 `web/routers/distill.py` 写入，含 `user_id` 与 `_db`（写入与判等都在该文件）
- 影响：重启后内存为空 → 管理员看不到 `interrupted` 行（那是开机 reconcile 的落库产物），且响应带 `user_id` / `_db`
- 修法：改读库 `list_distill_tasks()`（存储层 `ORDER BY updated_at DESC` 的有界列表，base + PG/SQLite 双实现），逐行走共享序列化器 `_task_response(row, mem)` —— 它本身就是逐键白名单，行里的 `user_id` 从构造上进不来；`mem` 只覆盖 `stage` / `message`，**永不覆盖** `status` / `progress_pct` / 存在性（红线由共享序列化器强制，不由本调用方自觉）。出参信封 `{tasks, total, truncated}`，`truncated = total > len(rows)` **实算**，不写死上限
- **200 条上限是显式上报的截断，不是静默截断** —— 见 §四「有上限的路径必须显式上报被裁过」
- 回归锁：`tests/test_admin_tasks_api.py`（`TestVisibleAfterRestart` 覆盖「重启后 interrupted 行可见」、`TestOutputWhitelist` 覆盖 `user_id` / `_db` 不出现、`TestEnvelope` 覆盖 `total` / `truncated`）

**7. `get_upload_task_status` 注入 user 但不校验归属** —— 状态：**已修**（归属 `a0a3a4d`，2026-09-10；拒绝码 404 于 `35e5794`）
- 原形态：`web/routers/text.py` 的 `get_upload_task_status` 签名取 `user: dict = Depends(get_current_user)`，函数体只 `_upload_tasks.get(task_id)`，**全程无归属比对**；且条目本身不含属主（写入的键只有 `status` / `progress_pct` / `message` / `text_id`）→ 想校验也无从校验
- **订正（2026-09-12）**：本条一直记「未修」，与代码事实不符 —— `a0a3a4d` 修的正是这一条，且条目的**两个症状都覆盖**：三处写入点补 `user_id`（`_run_upload_task` 正常/异常分支、`upload_text`），读取时 `task is None or task.get("user_id") != user["id"]` fail closed（条目缺 `user_id` 也拒，不因「无从校验」放行），返回前剥离 `user_id` 不上屏
- 回归锁：`tests/test_upload_task_ownership.py`（含变异验证：删掉校验 → 越权断言变红）
- 与缺陷 9 的 `text.py` 那条是**同一处端点的两个面**：本条是归属面（有没有校验），缺陷 9 是拒绝码面（403 还是 404）

**8. `_GEN_DEADLINE_S = 60.0` 写死，不可 env 覆盖** —— 状态：**已修**（2026-09-13）
- 原形态：`_GEN_DEADLINE_S`（`adapters/llm_adapter.py`）写死；同组的三个 ceiling `LLM_DECISION_ATTEMPT_S` / `LLM_GEN_ATTEMPT_S` / `LLM_STREAM_ATTEMPT_S` 都可 env 覆盖，deadline 仍无出口 → 「超时可调」名不副实（生成轮 60s 总墙钟烧死在代码里）
- **修法（同族一并，不只点名的那个）**：抽 `_env_timeout_s(name, default_s, floor_s)` 统一出口，**三个 deadline 与三个 ceiling 全部**改走它。floor 防「填 0 静默关超时」：ceiling 取 `_ATTEMPT_MIN_S`（防 `create(timeout=0)` 变 no-timeout），deadline 取 `_ATTEMPT_WINDOW_S`（deadline 撑不起一次有效窗 = 静默关掉全部 attempt）。默认值一字不变：deadline 6 / 60 / 8，ceiling 5 / 45 / 7
- **同族普查（硬要求，不允许「只修点名的那个」）**：全仓扫超时类常量 —— 适用本出口的只有这 6 个；`_*_ATTEMPTS` / `_*_BACKOFF_S` / `_ATTEMPT_MIN_S` / `_ATTEMPT_TIMEOUT_MARGIN_S` / `_RATE_LIMIT_ATTEMPTS` 是次数 / 退避 / 内部机制，不属「填 0 静默关超时」形态；`core/embeddings.py` 的 `_EMBED_ATTEMPT_S` 早已 env 化（floor 0.1）。**无第五个漏网同类**
- 回归锁：`tests/test_llm_adapter_retry.py` 三条 —— helper 的缺省 / 覆盖 / floor、六个常量真消费 env（`importlib.reload` 实跑，堵「只测 helper、常量没用它」的假绿）、默认值不变。变异：`_GEN_DEADLINE_S` 改回硬编码 60.0 → `test_timeout_family_constants_consume_env` 红
- 历史背景（仍成立）：关掉思考后（2026-09-10 实测，n=14）**0/14 超 60s，max 31.3s**；修复前单次 map 12.0–160.0s，>60s 11/14（`ev:thinking-maplen-before`）。**本轮只补出口、不动默认值** —— 生产是否仍有超时由运维按 `.env` 调

**9. 端点注入 `get_current_user` 却不引用 `user`（越权一类）** —— 状态：**已修**（2026-09-10）
- 形态：签名取 `user: dict = Depends(get_current_user)`，函数体从不引用它 → 无归属校验，任何登录用户拿 id 就能读他人资源
- 全仓 AST 扫描（`web/routers/*.py`）命中 **9 处**：
  - **3 处真越权，已修**：`text.py` 的 `get_upload_task_status`（上传任务：任意 task_id 读他人进度/错误）、`voice.py` 的 `preview_audio`（自定义音色文件：绕过私有的 `/list` 过滤与删除/上传的属主校验）、`voice.py` 的 `voice_synthesize`（`card_id` 参考音频：兄弟端点 `preview_ref_audio` 查卡主，唯独它不查）。统一 fail closed + 非属主 **404**（不用 403，避免靠状态码枚举资源是否存在）
  - **6 处良性**（只当登录门，读全局/公开数据）：`auth.py` 的 `get_announcement`、`market.py` 的 `get_card_versions` / `get_card_forks` / `list_post_comments`、`voice.py` 的 `voice_status` / `speech_to_text`
- 边界锁：`tests/test_auth_param_used.py` 固化这次 AST 扫描 + 白名单，将来新增同类端点自动变红，不必靠人再扫一遍
- 注：`voice.py` 内既有四处查属主返回 **403**（`get_ref_audio` / `preview_ref_audio` / `upload_ref_audio` / `delete_ref_audio`），与新修的 404 并存，口径待统一 —— **已于 2026-09-12 统一为 404**（缺陷 13）

**10. （已移出缺陷表 → 见下节「三之二、特性缺失 / 立项」条目 A）** —— 状态：**已移出（非缺陷）**：前提证伪、裁定「不做」（2026-09-12）。为保持编号连续、不改后续条号，此处只留占位；条目全文移入三之二。
- 移出理由：它**不是「有东西坏了」，是「有东西从来没建」** —— 混在缺陷表里让「还有几个真缺陷待修」这个数失真（用户 2026-09-14 裁定）。

**11. 越权读取的根因：storage 读取原语没有身份概念** —— 状态：**已修**（2026-09-11）
- 形态：`storage/base.py` 的读取原语 `get_text(id)` / `get_session(id)` 签名里没有 user——读取本身不带身份，于是**每个调用点都必须自己记得**补一次属主比对，忘一个漏一个，且漏了没有任何报警
- 命中 6 处真越权，同形：`web/routers/history.py` 的 `resume_session`（最重：读到他人全文 → 重建 RAG → 把引擎塞进共享 `sessions` → 补完全部消息与好感度 → 生成重逢问候）、`web/routers/distill.py` 的 `identify_by_text_id` / `distill_by_text_id` / `_distill_start_impl` / `reindex_rag` / `distill_stream`。`core/text_manager.py` 的 `get_or_distill` 另有同形陷阱：`user_id: str = ""` 默认值让「缺参数」静默退化成「按空属主查」（`save_distilled_card` / `distill_all` / `switch_character` 同形，一并去默认值；后两个方法已于 2026-09-22 整段删除 —— 全仓零调用方，见 86）
- 另查出第 7 个洞：`web/routers/chat.py` 的 `_ensure_session` 的 DB 重建分支缺属主校验——内存命中分支有
- 为什么逐处补 `if` 不算修：根因是原语无身份。补 6 个 `if` 只是把「忘一个漏一个」推迟到下一次
- 修法（能力拆分）：`get_text` / `get_session` 拆成 `*_unscoped`（显式无属主过滤）与 `*_owned(id, user_id)`（**SQL 里** `WHERE user_id=?`；拿不到行返回 None，不抛异常，由调用方定 404）。选 `*_owned` 就不可能漏过滤；剩下的 `*_unscoped` 每一处都必须有意为之
- **属主过滤必须在 SQL 里**，不能取回后在 Python 比对——Python 侧比对是将来一次顺手编辑就能删掉、且删了不报警的东西
- 过渡别名（`get_text` / `get_session` 委托到 `*_unscoped`）已删：别名在，调用点就仍可不选变体
- 边界锁：`tests/test_storage_scope_lock.py` 扫全仓 `*_unscoped` 调用点，必须在 `UNSCOPED_ALLOWLIST`（模块:函数 + 理由）里；名单外出现即红，名单里的条目不再命中亦红（防名单腐烂）。与 `tests/test_auth_param_used.py` 互补：那条查 router 层「注入了 user 有没有真用上」，这条查 storage 层「读取有没有绕过身份」
- 当前 `*_unscoped` 白名单 7 处：`core/chat_engine.py` 三处（引擎读自身 `session_id`；`ChatEngine` 无 user 语境，`self._user_id` 恒空）、`storage/sqlite_store.py` 与 `storage/postgres_store.py` 的 `export_session`（存储层导出原语签名无 user，属主校验由调用方端点 `history.py` 的 `export_session` 用 `*_owned` 完成）、`mcp_server/server.py` 的 `_toolkit_for`（stdio 通道无身份语境）、`web/routers/text.py` 的 `get_text_deletion_impact`（admin 跨属主查看的显式逃生口，受 `is_admin` 保护）
- 回归用例：`tests/test_security_authz.py::TestHoleOwnershipRegression` 逐洞一条「非属主 → 404」。两条 session 用例的夹具刻意让 A 的 card 指向 **B 的** text——`resume_session` / `_ensure_session` 是双门（session 属主门 + 下游 `get_text_owned`），让开下游门才测得出 session 门被去掉
- 注：`_distill_start_impl` / `distill_stream` 在读文本前先调 `get_user_api_config`，而新建 sqlite 库缺 `users.embedding_key`（缺陷 5）→ 用例夹具临时让该读返回空配置，避免缺陷 5 挡住属主门的验证
- 拒绝码：非属主与不存在同判 **404**，理由见 §四

**12. `start_session` 把属主 404 吞成 500** —— 状态：**已修**（2026-09-11）
- `web/routers/distill.py` 的 `start_session`：属主门 `get_text_owned` 及其 `raise HTTPException(404, ...)` 在 `try` 块内，同块的 `except Exception` 把它当普通异常重抛成 `HTTPException(500, "操作失败，请稍后重试")`
- 后果：非属主读他人文本被正确挡住（拿不到行），但客户端看到 500 而非 404——4xx 的客户端条件被报成服务端错误，并破坏 §四 的 404 口径。非属主与不存在仍是同一个码（都 500），故不是存在性枚举点，是错误分类与可观测性问题
- 同型：宽 `except` 把有语义的异常吞成通用错误——与 `CollectionUnusableError` 被 `IndexingService` 的宽 `except` 吞掉、`finish_reason` 被当成功返回同源
- 修法：宽 `except Exception` 之前加 `except HTTPException: raise`。宽 `except` 只兜真正意外的东西
- 回归锁：`tests/test_security_authz.py::TestHoleOwnershipRegression::test_17_distill_start_session_non_owner_404` 同时锁「状态码是 404」与「没为别人建成会话」——只锁后者不够，500 同样满足它

**13. 存量属主型 `HTTPException(403)` 按 §四 翻成 404** —— 状态：**已修**（2026-09-12）
- §四 口径：授权失败一律 404（防资源存在性枚举）。本轮把存量一次翻齐
- **实际计数**（现跑现数，`grep "403" web/routers/`）：含 "403" 的行 46 = **42 处 `HTTPException(403)` raise** + 4 处注释/docstring 提及。此前 AGENTS.md 记的「约 38」是估数，不准
- **42 处三分类**：A 归属型 **33**（资源不属于当前用户 → 翻 404）；B 权限型 **5**（非管理员访问管理端点、账号禁用 → 本就该 403，不动）；C 业务门 **4**（审核待审/fail-to-flag、geo base_url 白名单 → 不动）。裁决后 `message.py:retract_dm_message` 从 A 改判 **B**（见下），故最终 **A=32、B=6、C=4**
- **翻了 32 处**，分布在 `card.py`(4) / `distill.py`(5) / `group.py`(5) / `market.py`(7) / `memory.py`(5) / `message.py`(1, react) / `voice.py`(5)
- **0 处能下沉到 `*_owned`**：`*_owned` 变体只有 text / session 两类，fac1a3a 早已翻完；其余资源（card/group/voice/memory/message/distill_task/comment/dm）没有变体，而「不新造存储层变体」是本轮硬约束。故本轮形态只能是**路由层把两分支合并成一个 404**：`if not X: 404` + `if X.user_id != user: 403` → `if not X or X.get("user_id") != user["id"]: 404`
- **逐处改，不 sed**：每处的「不存在」与「非属主」文案必须合并——文案本身是第二条枚举信道（同一路径下非属主与不存在若文案不同，即便都 404 也能靠 detail 区分）。本轮只做到「逐端点两侧同文案」（裁决 4）；跨端点收敛到单一来源（现 404 文案中英混用）是更大的改动，未做
- **两道裁定的例外**：
  - `distill_task_status`（裁决 1）：前端 `useAppStore.js` 的轮询对 403 有专门分支（重试，注释写「可能 token 过期」），404 分支是「任务行已不存在 → 立即移除」。翻 404 是**行为变更**——非属主场景从「403 重试」变「404 立即移除」。裁决「改 404 并同步改前端」。该 403 分支仍有活的生产者（账号禁用经 `get_current_user` 返 403），不退化成死码
  - `message.py:retract_dm_message`（裁决 2）：**保留 403 当 B**。「仅发送者可撤回」是对外可公开的规则，不是资源归属；改 404 会让合法接收方收到「消息不存在」而非「只能撤回自己发送的消息」
- **验收**：`tests/test_ownership_404.py` 42 条——32 端点各一条「非属主 → 404 且显式 `!= 403`」+ 1 条「retract 仍 403 且 `!= 404`」+ 2 条 B 类（管理端点非管理员 → 403 且 `!= 404`）+ 7 条文案一致性（非属主 vs 不存在的 `detail` 逐字相等）
- **到达性已取证**（2026-09-12）：42 条各自实际命中的 raise 站点已用探针逐一核对，均为预期的属主 guard（非「被前置门拦下、返了巧合正确的码」）；核对同时查出并修掉两条 ambient 依赖（`deps.get_llm` 依赖本机 `.env` 凭据、`deps.get_memory_manager` 内联 import 绕过 `Depends` 构造真 chroma）。工具与产物见 §五「用例到达性证据档」，纪律见 §四
- **文案一致性仍是抽样**（7/32）：跨端点收敛到单一来源未做，本轮只做到逐端点两侧同文案，补齐 32 处另立一轮
- **变异已验证**：3 处改动点各自改回 403 → 对应用例必红；B 类 `market.py:delete_card_version` 改成 404 → B 类用例必红
- **既有测试一并订正**：`tests/test_security_authz.py` 的 `test_03/04/05`（card get/export、group history）与 `tests/test_distill_task_api.py` 的「他人任务」用例——它们原先断言 403，标签与断言都已翻成 404
- 残留 403 现共 **10 处 = B 6 + C 4**（`admin.py` require_admin；`auth.py` 三处账号禁用 + 一处 geo；`market.py` 三处审核门 + 一处仅管理员；`message.py` retract）；routers 之外另有 `web/deps.py` 的 geo 白名单与 `web/server.py` 的账号禁用（均 B/C）

**14. 已报数字指向 gitignored 产物（可追溯性缺口）** —— 状态：**已修**（2026-09-12）
- 形态：AGENTS.md / 会话记录引用的原始产物留在 `e2e/scratch/`（被 `.gitignore` 的 `/e2e/` 规则覆盖），读者按引用去查是空的 —— **引用等于没有出处**，而这正是证据档存在的意义
- **根因（不在那 8 处，在机制）**：仓里没有「证据产物」这个一等类别，于是① 探针默认落点全是 gitignored 目录（`map_len_probe` / `capfield_probe` 的 `PROBE_OUT_DIR or e2e/scratch`、`raise_probe` 的脚本同目录、`distill_orphan_matrix` 的 stdout 重定向 —— 三套约定）；② 「调试脚本不入库」与「文档数字必须可追溯」两条规则正面相撞，凡被引用的数字就要临时开一次例外；③ 脱敏靠记性（本档记着「入库产物已删 `preview` / `content_head`」，一次手工动作）。**不消灭「例外」这个动作，修完必然长回来** —— 所以本轮改的是机制，不是逐个 sed
- 处置（commit `8dd99d3` 立契约 / `e389fdc`+`ac11cfe` 落点迁移 / 本轮收编存量）：
  - **唯一写入出口** `tests/perf/evidence_writer.py`：落点固定 `docs/evidence/<id>.json`（**不提供路径参数、不读环境变量** —— 留口子就等于留回退路径）、按**白名单**在写入时脱敏（黑名单只挡已知字段名，探针加一个字段就漏）、同时 upsert 清单条目。七个探针全部接入，各自的 `OUT_DIR` / `OUT` / `PROBE_OUT_DIR` / `PROBE_OUT` 与 `json.dump` 已删
  - **清单即唯一真源** `docs/evidence/manifest.json`：三档 status（`verified` / `runtime-measured` / `unverifiable`）。正文引用改写字形 `ev:<id>`，文档里的数字与简历口径都是清单的**渲染**，不是第二份手写表
  - **双向满射锁** `tests/test_evidence_integrity.py`（43 条）：正文每个 `ev:` 引用必须解析到条目；条目按状态满足必填字段与产物存在性（`verified` 的 artifact 必须被 `git ls-files` 命中；另两档 artifact 必须为 `null`）；`script_role` 逐条表态
  - **指称闭合**（v3 补的方向）：锁原先只校验**形状**，字段值从不解析回仓库 —— `producer` 可以指向不存在的脚本而全绿。现补：`script` 在库、`reproduce` 路径逐个解析、`code_sha` 解得开 commit 或精确等于哨兵 `unknown(scratch)`、`notes`/`claim` 里的路径 tracked 或逐条声明进 `non_repo_paths`
  - **数值闭合**：`verified` 条目必填非空 `assertions`（`{"path","value"}` 严格相等；`{"path","len"}` 给计数），`claim` 里**每个数字**要么被覆盖、要么写进结构化 `derived`（`{"value","formula","refs"}`，`refs` 必须落在本条目 assertions 里且非恒等式）。这是简历数字与入库产物之间**唯一**的连接点 —— `claim` 在 v3 之前是自由文本，口径混用那次（算出 `6487 / 1205`）就是这么漏的
  - **`derived` 升为字段**（v4）：算出来的数字（合并中位数、按状态过滤的计数）原先写在 notes 的 `派生量：` 块里 —— **散文锁看不见**，实测 `派生量：9999=9999，77=77` 全绿。现要求「派生量只能从已绑定的量派生」：`refs` ⊆ 本条目 assertions 的 path，`value` 不得等于任何单个 ref 的值（恒等式即红），`formula` 非空。既绑不上产物、又说不出算法的数字，处置是**从 claim 里删掉它**，不是造一条 derived 糊过去
  - **`non_repo_paths` 升为字段**（v4）：非仓库路径原先靠「整条 blob 里找『未入库』等词」判定（`any(m in blob)`）—— 一条目两条路径只标一条也过，标注与它管的那条路径之间没有绑定。现改成逐条声明字段，**双向**核对：claim/notes 里解析不到仓库的 token 必须逐个声明；声明进来的必须**确实**不在库（tracked 的写进来即红）。前缀白名单（`_GITIGNORED_PREFIXES`）随之删除 —— 逐条声明之后前缀猜测就是多余的，而且它本身就是启发式
  - **提取器反空过**（v4 §二之二）：`_path_tokens` / `_numbers` 是唯一**不可能靠「声明」消除**的启发式 —— 没被提取到的东西既不会被查、也不会被要求声明。把两者的识别面钉成语料（反引号 / 行内代码 / 中文标点紧邻 / `AGENTS.md:349` 行号后缀 / `docs/evidence/<id>.json`；`n=14` / `p50 1245` / `8192` / `94%` / `12441/12413`），识别不到的形态记进 `_PATH_KNOWN_MISSES`。判据在测试文件里，不在 README 里
  - **变异矩阵** `tests/test_evidence_integrity_mutations.py`（36 行覆盖 23 个断言，另 18 条带理由豁免）：渲染新鲜度锁会因为**任何**清单变异而红，于是变异测试失去鉴别力（红的不是你以为是的那条）。矩阵对每个变异**只跑指定的那一条**断言，证明它有专属红源。「新增任何清单语义断言必须同时补一行」这条约定本身也已机器化（`TestMutationCoverage` **反射**全部 `Test*` 类求差集 —— 不列举类名，否则新开第五个类照样漏；豁免走带理由的 `_NO_MUTATION_NEEDED` dict）—— 此前它是文件头的一句散文，实测不补行/删行都是全绿
  - **止于何处**：「`producer` 脚本跑出来是不是**真**这份产物」「`derived.value` 是不是**真**由 `refs` 算出」静态皆不可判，查到「脚本在库、数字与产物相等、派生量可回溯到已绑定量、路径逐条表态」为止 —— 核不出来的部分靠 review，不靠再收一层正则。见 `docs/evidence/README.md`
- **收编的存量缺口**：缺陷 2 的 `out_v5.json` → `ev:incomplete-v5`（只留统计量，正文段 `sampleChunk` 按白名单挡在库外）；`chunk_size` 来历 → `ev:chunk-size-provenance`（**订正**：原文写「`git log -S` 全仓无 4500/12000 命中」过宽 —— 有命中，只是无一处当 chunk_size 用）；A2 接线变异实验 → `ev:a2-wiring-mutation`；`TECHNICAL_REPORT.md` 的图谱统计 → `ev:graphify-snapshot-2026-08-15`（`unverifiable`）。具名/正文类产物一律**脱字段不删文件**
- **不重跑顶替**（硬要求）：产物丢失时不得重跑生成一份新的顶上 —— 重跑得到的是今天的数字，文档写的是当时的结论，拿新数字填旧引用是把「无出处」伪装成「有出处」。产物的 `code_sha` 只在推断能落到唯一 commit 时才填，否则 `unknown(scratch)` + `notes` 交代依据不足
- **`script` 有两种语义，已升为字段**：`script_role: producer | corroborating`（`verified` 必填其一、其余两档必须 `null`；`corroborating` 还必须带 `notes` 交代产出脚本是谁、为什么没入库）。先例 `ev:incomplete-v5` —— 它的 `script` 指向仓内覆盖同一断言的用例，产出脚本实为未入库的 scratch；不加这个字段，区别就只活在散文里，下一个人照抄那个形态就会填出一条**真不可复现**的 `verified`
- **残余未收编**：`.claude/sessions/`（gitignored）仍被正文引用 **5 处**（`docs/engineering-evidence.md` L59/63/191/241/282，均已标 `⚠️ 待核`）。处置：机械收编成 `runtime-measured`，本档已裁定**做，但排在缺陷 15 之后**（价值中等，不急）
- 契约细节（三档 status 的必填字段、白名单上限、`ev:` 语法、commit hash 的保留例外）见 `docs/evidence/README.md`

**15. `034_post_enhancements` 在已建库上每次 init 都打一行假失败；迁移执行区 75 处吞错** —— 状态：**已修**（2026-09-12）
- 现象（修复前）：同一个库第二次 `_ensure_initialized` 稳定打 `[SQLiteStore] Post enhancements migration failed: duplicate column name: images`。034 段是 `except Exception: print` **连 duplicate column 都不吞**，故「已建库重跑」这一正常路径每次都报失败；真出错也只留一行 print 后继续
- 两层问题：(1) **假失败**——列已存在的正常情况被报成 failed，噪音会训练人忽略真失败；(2) **真失败被吞**——无重抛，034 真出错也静默继续。与缺陷 5 同病灶（「失败被吞成正常返回」谱系）
- 同族存量分类（**实测非目测**，AST 扫 `_ensure_initialized`）：77 个 handler = **74 吞错 + 3 重抛**。74 吞错 = **56 靠错误串判断**（`if "duplicate column" not in str(exc).lower()`）+ **18 裸吞**（`except: print` 后继续）；3 重抛里旧 L850（内联 CREATE INDEX）是「print + 不查错误串 + `raise`」，与 18 处裸吞同形态只差最后一步，故合起来就是立项时所称的 **75 处「失败只 print」家族**（56 猜错误串 + 19 裸吞）。全部落在迁移执行区；PG 侧 `_ensure_initialized` 实测仅 1 个 handler（外层，含 raise），**无同族病灶**
- 修法（**机制，非补丁**）：迁移执行区重写为单一应用器 `_apply_migration` + 显式次序表（`_MIGRATIONS_BEFORE_USER_REBUILD` / `_MIGRATIONS_AFTER_USER_REBUILD`，77/078 之间夹 users 表重建，次序有意义）。幂等靠**读现状**（`PRAGMA table_info`）：脚本内每条 `ALTER TABLE ... ADD COLUMN` 都已在 → 整脚本跳过；部分在 → 剔掉已在的那几条再执行其余。确定性执行取代猜错误串；**该区不再有任何 except**，迁移真失败照常上抛
- 顺带修掉一个潜伏 bug：旧逻辑「executescript 遇第一条 ALTER 撞 duplicate 即中断整脚本」，同一文件里后续未应用的列会**永不补上**（半成品库形态）——新写法逐列判断，只补缺的那些。`tests/test_sqlite_fresh_schema.py` (e) 专测这条判别力
- **形态锁（不变量）**：`tests/test_migration_dispatch.py` 两条 —— (i) `storage/migrations/*.sql` 每个文件要么在次序表、要么在带理由的 `_NOT_APPLIED`（**唯一豁免出口**），否则红；(ii) AST 扫 `_ensure_initialized`，任何不含 `raise` 的 except handler 即红
- **闸门恢复全强度**（本条的真正目的）：`tests/test_sqlite_fresh_schema.py` (c) 由「不含 067 的失败」改为同一库两次 init **整库零失败输出**；(d) 整类兜底同样覆盖第二次 init
- **变异验证（实测红，验后 sha256 逐字节还原）**：(1) 084 迁移追加非法 SQL + 在 AFTER 循环外包 `except: print` → (c)(d) 两条**断言**红 + AST 锁红（三红）；(2) 从 `_MIGRATIONS_AFTER_USER_REBUILD` 摘掉 `084` → **只有** dispatch 锁红，fresh-schema 5 条全绿 —— 正是缺陷 20 那种「静默掉一个文件」的形态
- 附带：5 处「因缺陷 5」的 `get_user_api_config` 空配置夹具绕过已删（`test_security_authz.py` 1 + `test_distill_task_api.py` 3 + `test_rag_unusable.py` 1），夹具注释同步订正；`web/routers/group.py:287` 对该读本就套了宽 `except: pass`，故那处 stub 双重多余

**16. `distill` 站点初次调用漏记 usage（三处站点口径不一致）** —— 状态：**已修**（2026-09-12）
- 三个站点各调一次 `_chat_initial`：`distill`（短文本截断模式）、`_distill_longcontext`（整本蒸）、`distill_incremental` 的收尾格式化。原状口径不一致：后两处各自紧跟一条 `_try_record_usage`，而 `distill` 那处**根本没有** → happy path（绝大多数情况）零记录，该 action 用量被系统性低估
- **「需先核」已核**：`web/` 层无任何补记（全仓 `record_usage` 调用点只有 `core/distiller.py` 与 `core/chat_engine.py` 两处），确系真漏记
- **修法不是三处各改各的**（那是补丁），是收敛到**唯一出口**：`_chat_initial` 加 `action` 参数，成功即恰记一条 —— 不变量 = 一次成功的 `_chat_initial` 调用恒对应一条记录
  - 该出口覆盖「正常 return」与「`length` 截断 return」两条路：截断那次 token 已烧、半截正文还要进重修环，同样是成功调用，照记；`raise` 那条路不记
  - 随之**删除**两处站点各自的外部补记（`_distill_longcontext` / `distill_incremental` 格式化站点）—— 否则出口 + 外部会双记
  - 重修调用是**另一条独立出口**（`_parse_json_with_retry` 的 `repair_stage` 2/3），它记的是另一笔真实调用，不动。故「初次坏 + 重修好」= 两条，正确
  - 流式两条链（`_distill_longcontext_stream`，以及 `distill_incremental_stream` 内联的 Phase 3）走 `chat_stream`、不经 `_chat_initial`，本就一调用一记，不动
- 形态锁：`tests/test_distill_usage_accounting.py`（5 例）——替换 `core.distiller.try_record_usage` 做同步计数，锁「出口发了几次、带什么 action」，不锁 storage 落库链路（那有独立测试）
- 变异已验证（4 个，全红后逐字节还原，`sha256 542c179a…`）：① 删出口记录 → `distill` 初次用例红；② 整本蒸外部补记加回 → 双记用例红；③ 截断分支改成早退不记 → 截断用例红；④ 格式化外部补记加回 → `distill_incremental` 格式化站点用例红

**17. 蒸馏失败上屏双重前缀 + 运维口径泄漏** —— 状态：**已修**（commit `396079f`，2026-09-12）
- 原形态是**三重**（不止双重前缀）：① 源头 `core/distiller.py` 的终态 `ValueError` 自带「蒸馏失败：」前缀，路由 `web/routers/distill.py` 又拼一次 →「蒸馏失败：蒸馏失败：…」；② 源头文案本身就是**运维口径**（原话「请提高生成角色卡调用的 max_tokens 上限，或精简角色卡内容」直接给终端用户）；③ 兜底分支还打 `type(exc).__name__`，流式两条链直接 `str(exc)` 上屏
- 根因是**错误上屏没有统一出口**，不是「蒸馏那句话写错了」——所以修法不是改文案，是收敛出口
- 出口：`adapters/llm_adapter.py`（既有边界模块）新增 `user_facing_error(exc, *, preserve_unknown=False)`。优先级：`llm_error_payload`（**复用** `_INCOMPLETE_USER_MESSAGES` 上屏表，未拆第二张消息表）→ `getattr(exc, "user_message")`（duck typing，**不 import 异常类**，守住 `tests/test_chat_stream_error.py` 的边界锁）→ 通用文案。`preserve_unknown` 只给 chat 的 SSE 帧，保住「未登记异常原样透出」既有契约；蒸馏路径一律不传
- 配套：`core/distiller.py` 新增 `DistillError(ValueError)`，`user_message`（上屏）与 `str()`（运维：`last_error` / 分片计数 / 处置建议）两口径分离；继承 `ValueError` 使路径上既有 `except ValueError` 语义不变
- **全仓 grep 覆盖清单**（「把异常转成用户可见文案」的地方，8 处全改）：
  1–2. `core/distiller.py` 9 处 `raise ValueError("蒸馏失败…")` → `DistillError`
  3–4. 同文件流式 Map / Reduce 两条 queue 支路：`q.put(("error", str(exc)))` → 改传异常本体，`yield {"error": f"…阶段失败：{item[1]}"}` → `user_facing_error`（阶段名是内部实现词，改为只进日志）
  5. `web/routers/distill.py` 后台任务兜底（旧：拼前缀 + `str(exc).split("\n")[0]` + 类名兜底）
  6. 同文件 SSE 流异常帧（旧：`str(exc)`）
  7. 同文件保存失败帧（旧：`f'保存角色卡失败：{exc}'`）
  8. `web/routers/chat.py` 的 `_stream_error_payload`（旧：`llm_error_payload(exc) or {"error": str(exc)}`）
  - 另附同文件同形态但**非异常来源**的两处：ValidationError 上屏（旧带字段名与输入值，可能含原文片段）、空格式输出（旧文案「请查看服务器日志」是运维口径）
- **本轮不改、记残留**（同源但属其它子系统，未验证其错误契约前不动）：`web/server.py` / `web/routers/auth.py` 的连接测试端点（响应本身就是给运维的诊断载荷）、`web/routers/group.py` 流式帧、`web/routers/text.py` 上传任务 `message`、`web/app.py` 的 `gr.Error(str(exc))`
- 不变量锁：`tests/test_error_user_facing.py`（9 例）——A 已知异常集合逐个过出口，断言上屏不含内部标识（异常类名 / `max_tokens` / `finish_reason` / 分片计数 / 内部路径）**且**运维细节确实留在 `str()`；B AST 锁（蒸馏失败的 raise 必须抛 `DistillError`；上屏字面量扫禁词，覆盖 raise 首参与 dict 的 `error`/`message` 值）；C 真实路径真跑 `Distiller.distill` 真抛 `DistillError`，断言上屏只有一个「蒸馏失败：」前缀
- 变异已验证（5 个，全红后还原）：`ValueError` 取代 `DistillError` → B 红；兜底改回 `type(exc).__name__` → A 红；往 `user_message` 塞 `max_tokens` → A/B/C + truncation 用例共 4 条红；删 truncated 的 `ops_detail` → truncation 用例红；往 dict `message` 塞 `finish_reason` → B 红
- **既有用例订正**（两处，都是缺陷 17 的**固化**——测的就是被修掉的那个行为）：
  - `tests/test_distiller_truncation_selfheal.py` 的上限用例：断言从「`max_tokens` 在文案里」翻成「`max_tokens` **不在** `user_message` 里、但在 `str()` 里」
  - `tests/test_distiller_routing.py` 的 3 条（`test_sync_429_bail` / `test_stream_429_bail` / `test_stream_generic_bail`，`adcb0a9`）：改锁方向相反的两条——上屏含「限流」/「重试」且**不含** 429 / 个分片 / `connection timeout`，而 `str()` 里那些必须**还在**（区分「分口径」与「删信息」）。`pytest.raises` 从 `ValueError` 收窄到 `DistillError`，锁住「必须带 `user_message`」
  - 注：断言用「个分片」而非裸「分片」——上屏文案「部分片段处理失败」是合法中文，裸词假阳（本文件 §四 禁词表同此处理）

**18. `IncompleteResponseError` 不可 pickle** —— 状态：**已修（2026-09-13）**
- 原形态：`__init__(self, finish_reason, where, content="")` 调 `super().__init__(<单条消息>)` → `args == (msg,)`。`BaseException.__reduce__` 在 `__dict__` 非空时返回 `(cls, self.args, self.__dict__)`，反序列化按 `args` 调构造器 → 缺 `where` → `TypeError`，**炸成另一个异常、掩盖真因**（跨进程 / 队列传异常时正是这个场景）
- **全仓普查（AST，硬要求）**：带自定义状态的异常类共 **7** 个（**配方**，随树变动 —— 现算来源 `tests/test_exception_pickle_lock.py::_census`，`2026-09-17` 复算仍为 7。锁只锁**集合相等**、不锁这个数：新增一个带自定义字段的类时锁会红，这个数不会，故它是说明值不是读数），逐个真 pickle 往返验证 —— 需修 3 处：`IncompleteResponseError`（补 `__reduce__` + 存 `where`）、`StoreError`（`args` 是格式化 message、`__init__` 要 `(op, exc)`，且原始 `exc` 未必可序列化 → 只带 `op`+message 过河，模块级 `_rebuild_store_error` 绕开再格式化）、测试替身 `_RateLimitError429` / `_RateLimit429` / `_BadRequest400`（参数与 `args` 对不上）。其余（`DistillError` / `CollectionUnusableError`）额外参数可默认，`cls(*args)` 本就成立、无需 `__reduce__`
- **形态锁**：`tests/test_exception_pickle_lock.py`，两层（§四）——①形态层：全仓带自定义状态的异常类集合 == 登记表，漏登 / 陈旧都红；②语义层：每类真 `dumps`/`loads`，断言类型 / `vars()` / `str()` 全不变。**判据有意用「带自定义字段即须往返通过」而非「无 `__reduce__` 即红」**——后者是代理指标，会误伤上述两个本就可序列化的类（锁症状，不锁代理）
- 变异（实测）：删 `IncompleteResponseError.__reduce__` → 语义层红（`TypeError: missing 'where'`，即病根症状本身）；新增一个带自定义字段的异常类 → 形态层红
- 扫描范围排除 vendored `services/gptsovits`（22738 个 `.py`、未入库）与构建 / 缓存产物 —— 第三方异常的序列化行为不归本仓管

**19. 边界锁只守 `*_unscoped` 命名，无身份读取原语裸奔** —— 状态：**已修（5 commit，2026-09-13）**
- 形态：`tests/test_storage_scope_lock.py` 用 AST 扫调用点，判据是 `_is_unscoped_call` → `attr.endswith("_unscoped")`。**只有名字以 `_unscoped` 结尾的调用**才进白名单校验
- 漏网：`get_card` / `get_group_session` / `get_dm_message` / `get_distill_task` / `get_card_author_id` 等**读取原语本身就无身份参数**，取回整行后由调用点在 Python 侧比对属主——名字里没有 `_unscoped`，锁看不见。删掉任一调用点的属主 `if`，锁全绿
- 为什么是缺陷 11 的同族：da0e3b3 只把 `text` / `session` 拆成 `_owned`/`_unscoped` 双变体，锁也只守这两类。其余资源从未拆分，于是「忘一个漏一个」的结构性风险仍在，只是没有报警
- **这正是缺陷 13 的 32 处只能手写路由层合并、且删一处不会红的原因**——本轮 `tests/test_ownership_404.py` 是补的语义用例（第二层防线），形态锁（第一层）仍缺
- **分类（SQL 事实判据，2026-09-13）**：命中 **46** 处 → **A 组 20**（SQL 本身已按身份收窄）+ **B1 12**（无属主过滤，真越权）+ **B2 8**（有意跨属主读：跨用户聚合/后台扫描/全局状态，无身份概念是有意的）+ **B3 6**（逐条裁定）。判据：属主表 = `migrations_pg/*.sql` 里沿 `_id` 边可达 `users` 的表（**29** 张）；读原语 = `SQLiteStore` 公开方法且 SQL 含 SELECT 无写语句；「已按身份收窄」= 签名有身份参数 **∨ WHERE 子句**（非 JOIN ON）含该表身份列谓词
- **JOIN 坑（41→46 来源）**：初版把 `JOIN users u ON c.user_id = u.id` 当作身份过滤，`get_card` 因此被判「已收窄」，命中数 **41**。**JOIN 条件不是过滤条件**——它只约束连接行配对，不筛掉非属主行。把「是否收窄」的判据限定到 **WHERE 子句**（在 GROUP BY/ORDER BY/LIMIT/HAVING 处截断后再测）后为 **46**，`get_card` 归位。见 §四纪律
- **裁决（用户，2026-09-13）**：A 组加「11 个管理原语的调用点必须全在 `web/routers/admin.py`」AST 断言——把「现在是这样」变成「必须是这样」（调用点事实 → 强制事实）；B1 里三处实锤（`get_characters` 先返缓存后校验 / `get_card_versions` 返回含卡全文的快照 / `get_text_comments` 无校验）**先修、单独 commit**，其余九个随后；B3 逐条：`get_card_author_id` 保留显式无身份但标注 **`identity_primitive`**（它是「校验的第一步」，输出供属主比对，与「有意跨属主读」区分开）、`get_reactions` 改 `_owned`、`get_latest_review_log`/`get_comment_reports` 归 A 组管理、`get_recent_card_session` **无调用点=死代码删掉**、`get_remote_card` 改私有 `_get_remote_card`
- **修法（根因，不是补校验）**：把无身份读取原语在 **storage 层**消灭——扩展缺陷 11 的 `_owned`/`_unscoped` 双变体范式，读属主表的原语要么 SQL 带身份、要么显式命名 `_unscoped` 并进白名单。**锁的判据 = 不存在「读属主表 ∧ WHERE 无身份列谓词 ∧ 无身份参数 ∧ 不叫 `_unscoped` ∨ 不在白名单」的原语**；名字只在**声明豁免**时起作用，真源是 SQL 事实（与缺陷 21「锁症状不锁代理」同范式）
- **夹具自效性（探针取证）**：既有 `test_12_distill_identify_non_owner_404` 对 `get_characters` 越权**恒绿**——它建的文本无缓存（`characters_json` 空），旧代码读缓存得 None 照样走 404，故旧代码下也绿。判别力要求**缓存已存在**（`test_23` 先 `save_characters` 再打非属主）。这是「探针自效性」的实例：夹具没走到那条分支，用例就是恒绿的
- **进度（2026-09-13）**：commit 1/5 `6d89bae`（三处实锤越权 → `*_owned`）；commit 2/5 `e3cf9e8`（B1 其余九原语补 `*_owned`，两后端 + 内部调用点 + 20 处外部调用点收窄 + 17 处白名单登记）；commit 3/5 `fc5f267`（B2 八原语：`get_session_affinity` / `load_affinity_state` / `get_reactions_after` / `get_group_reactions_after` / `get_unsynced_cross_border_cards` / `get_unsynced_cross_border_messages` 六个改名加 `_unscoped` 后缀，`get_session_unscoped` / `get_text_unscoped` 早已命名；4 处新调用点逐处写明「无身份语义」理由进白名单）
- **commit 4/5 `09742cc`（B3 裁决落地）**：`get_reactions(message_ids)`（旧签名收一组**调用方自拼的 message_id**，无身份谓词）**重键**为 `get_session_reactions_owned(session_id, user_id)`（属主谓词落 SQL，`JOIN sessions` + `WHERE m.session_id = ? AND s.user_id = ?`）+ 私有 `_get_group_reactions(group_id)`（群反应按 group_id，`char:<card_id>` 合成行保留）；据此删掉「任意 message_ids」这个越权面。为什么不能按 `mr.user_id` 收窄：群角色反应写法是 `toggle_reaction(mid, f"char:{card_id}", emoji)`（group.py），按 reactor 过滤会把角色反应一并滤掉 —— `TestDefect19Commit4B3Verdicts.test_36` 就是这条理由的红源。另：`get_recent_card_session` **无调用点=死代码，删**；`get_remote_card` 仅被 `upsert_remote_card` 内部调用 → **改私有 `_get_remote_card`**（私有方法不进公开原语普查，问题自然消失）；`get_card_author_id` **保留显式无身份**并标注 **`identity_primitive`**（它是校验的**第一步**，输出供属主比对，与「有意跨属主读」区分，改 `_owned` 反而成循环依赖）。**变异验证**：删 `AND s.user_id = ?` → `test_35` 红（「非属主拿到了他人会话的反应」）；改回 `mr.user_id = ?` → `test_36` 红（「角色反应被属主过滤掉了」）；各自独立红源，已字节还原
- **commit 4 未落的两条 B3 裁决 → commit 5 按事实重裁（2026-09-13）**：① `get_latest_review_log` 的「归 A 组管理」不成立 —— SQL 无身份谓词（按本项目 A 组判据就不是 A 组），唯一调用点在 `market._publish_preflight`（用户发布路径，非 admin），硬写断言当天即红；改法 **补 `_owned`**（`JOIN cards c ... WHERE r.card_id = ? AND c.user_id = ?`），按 SQL 事实自动成为「已收窄」，与 `get_reactions` 同型。② `get_comment_reports` **零调用点=死代码**，「调用点全在 admin.py」对空集恒真=断言空转（§四已收此案例）；按 `get_recent_card_session` 先例**删掉**。真 A 组断言落在 `get_comment_reports_grouped`（admin.py 调它）
- **commit 5/5（本 commit，两把锁）**：① **SQL 事实锁**（`test_no_owner_table_read_without_identity_or_declaration`）判据 = 不存在「读属主表 ∧ WHERE 无身份列谓词 ∧ 无身份参数 ∧ 不叫 `_unscoped` ∧ 不在 `OWNER_READ_ALLOWLIST`」的原语；属主表/身份列由 `storage/migrations_pg/*.sql` 现推（29 张），真源是 SQL 事实而非命名 —— 补上命名锁「改回原名就漏」的盲区。当前登记 **21** 处豁免：7 公开面（`get_card_forks` / `get_featured_cards` / `get_public_cards_by_text_id` / `list_public_cards` / `list_public_cards_total` / `search_public_cards` / `search_public_cards_total`）+ 11 管理面（`count_distill_tasks` / `get_all_usage_summary` / `get_card_reports_grouped` / `get_comment_reports_grouped` / `get_config_changelog` / `get_dashboard_stats` / `get_review_logs` / `get_usage_quality_stats` / `list_all_cards_admin` / `list_all_posts_admin` / `list_distill_tasks`）+ 1 凭据路径（`get_refresh_token`）+ 1 identity_primitive（`get_card_author_id`）。`get_user_by_email` 的条目在判据 v2 下已不再命中（`WHERE u.email = ?` 被认作身份谓词），见缺陷 25。② **管理面调用点断言**（`test_admin_management_primitives_are_called_only_from_admin`）：11 个管理原语中 `get_comment_reports` 已删，余 **10** 个的生产调用点必须全在 `web/routers/admin.py`（tests/ 豁免）；**并带非空守卫**——每个名字至少一个生产调用点，否则报「空转假绿」（对治上一条死代码教训）。**变异验证**：新增一个读 cards、无身份、不在白名单的原语 → SQL 事实锁红；把一个管理原语的调用点挪到 `market.py` → 调用点断言红；把一个零调用点名字塞进管理集合 → 非空守卫红。三条独立红源，已字节还原
- **收口（2026-09-13，`5ba2a7a`）**：commit 5 的 SQL 事实锁**只扫 sqlite 一边** —— 「方法集一致」（覆盖断言只比方法名）**≠**「SQL 体一致」，PG 版某原语少一个身份谓词，两把锁都看不见。修法：`_BACKENDS` 声明两后端，`_scan_class(path, cls_name, idc)` 同一判据、同一代码路径各扫一遍取并集（**不是**「加一条断言比对两边判定结果」——那是在锁外再套一层间接，真源仍只有 sqlite 一边）。另补 PG 侧**运行期**语义用例 `tests/test_postgres_store.py::TestPgOwnedIdentityIsolation`（非属主读 `get_text_owned` / `get_card_owned` 必须 None），并做 PG 侧变异实跑：删 PG 版 `get_card_owned` 的身份谓词 → 语义用例红；删谓词**同时**删参数 → 静态锁红（只删谓词不删参数时静态锁不红，见缺陷 25）
- 与 §四「形态锁与语义用例是两层防线，各管各的」直接相关：缺陷 11 的两层都齐，这一类只有第二层

**20. 蒸馏断点行的删除路径不对称 + 「删卡保留断点」的理由与代码事实相反**（会话文件里记作 **F**）—— 状态：**已修**（行清理 `6753f17`；线程停止 `bee9993`；注释订正 `53494ae`，2026-09-12）；删卡/解绑口径裁决为**不动**（另一件「加功能」已立项）

- **零外键**：`storage/migrations/084_distill_tasks.sql` 的 `distill_tasks` / `distill_chunks` **都没有 REFERENCES / ON DELETE**（PG 侧建表同样零外键）。所以级联指望不上，**每一处清理都必须显式删两张表**，顺序先父后子（父行一消失，`save_distill_chunk` 的 `WHERE EXISTS` 即失效，写路径随之关闭）
- **删除路径残留矩阵**（2026-09-12 现跑现测，sqlite 与 PG 逐格相同；脚本 `tests/perf/distill_orphan_matrix.py`，产物 `docs/evidence/distill-orphan-matrix.json`，证据：`ev:distill-orphan-matrix`）：

  | 删除路径 | distill_tasks | distill_chunks | 判定 |
  |---|---|---|---|
  | `delete_text`（软删） | 保留 | 保留 | 对：可 restore，断点该留 |
  | `hard_delete_text`（两个 `keep_cards` 分支） | 清 | 清 | 已清 |
  | `delete_card`（软删）/ `purge_card` / `detach_text_cards` | 保留 | 保留 | 裁决：不动（行身份与卡无关；断点跟文本走） |
  | `delete_user` | 清 | 清 | 已清（`6753f17`）+ 线程停止（`bee9993`） |

- **「删卡保留断点」的注释理由是错的**：`storage/sqlite_store.py` 的 `hard_delete_text` 里写着「不清理 `delete_card` / `purge_card` / `detach_text_cards`：蒸馏行身份是 (user, text, character)，card_id 只是产物反向指针；删卡重蒸是常规迭代，文本还在就不该清掉续跑断点（否则下次重蒸从头烧 API）」。但续跑发现 `find_interrupted_distill` **只匹配 `status = 'interrupted'`**。删卡时行的状态通常是 `running` / `done` / `error` —— **永远命不中**，断点留着也用不上；只有 boot reconcile 把 `running` 翻成 `interrupted` 之后才可达
  - 现跑现测（`tests/perf/distill_resume_reachability.py`，同产物文件）：`running` / `done` / `error` 三个状态删卡后**都命不中**（整批重跑），只有 `interrupted` 命中。「删卡保留」目前 = 纯占空间、零 API 收益
  - 即：注释宣称的保护在主流路径上不存在。要么承认它无用而连带清掉，要么改成「按 (user, text, character) 找最近一条可复用行」——后者是**加功能**，不是修 bug
- **残留行的 `card_id` 是死指针，但症状是静默 no-op 而非报错**：蒸馏状态查询端点（`web/routers/distill.py` 里构造状态响应体的那处）原样回 `row["card_id"]`；卡被 `purge_card` 之后该 id 已不存在。前端三处消费点**都做了存在性守卫**（`DistillTaskBar.jsx` 与 `DistillWorkbench.jsx` 的 `tryChat` 是 `if (card)`，导出按钮挂在 `view?.canChat` 下），所以不会 404、不会崩——**点了「打开 / 试聊当前版本」什么也不发生**。属「失败被吞成正常返回」同族，但等级低（无数据损坏，只是无反馈）。*（初判为「前端取卡必 404」，实读前端后订正）*
- **running 任务这一问已有实现**：`web/routers/text.py` 的软删与永久删**都在** `soft_delete` / `hard_delete` 之前调 `cancel_distill_tasks_by_text_id` —— 它同时置内存 `error`（bg 线程在 `distill_incremental_stream` 的消费循环里读这个当停止信号）与 DB `cancel_distills_by_text_id`。所以「删文本时有 running 任务怎么办」的答案是「删了让任务停」，且已落地，**无需再裁决**
- **真缺口：`admin.delete_user` 这条路没接**。`web/routers/admin.py` 的 `delete_user` 直接调 `storage.delete_user`，**不停在跑的蒸馏线程**；`storage/postgres_store.py` 的 `delete_user` 注释自己承认了这一点。行被清、线程还在烧 LLM 额度，且其 `save_distill_chunk` / `update_distill_task` 双双落空（`WHERE EXISTS` + UPDATE-only），结果是**不可观测的野线程**
- **野线程还能把刚清掉的东西再造回来**：`storage/migrations/001_init.sql` 的 `cards` 只在 `text_id` 上有 FK（`REFERENCES texts(id) ON DELETE CASCADE`），`cards.user_id` **没有** FK。所以 `delete_user` 清完行之后，那条仍在跑的蒸馏线程走到收尾的 `save_card` / `update_card`，会**给一个已删除的用户建出一张新卡**（孤儿行，且 `delete_user` 已经跑过去了，不会再有第二次清理）。这是本缺陷里唯一会**新增不可达数据**的路径，不只是「该清没清」
- **判据小结**：断点行本身的生命周期是**跟文本走**的（每条文本死亡路径都已清、且行随文本 soft-delete 一起停留），所以「删卡/解绑要级联清」这个前提不成立；真正要修的是**文本/用户死亡时仍在跑的那条线程**
- **处置（2026-09-12 完成）**：
  - **用户死亡路径已修**（`bee9993`）：`cancel_distill_tasks_by_user_id` 与按 text 的变体同族（命名对齐既有的 `cancel_distill_tasks_by_text_id`，未复制逻辑），`web/routers/admin.py` 的 `delete_user` 与 `batch_delete_users` **两处都**在 `storage.delete_user` **之前**调它。只置内存停止信号、不扫 DB —— DB 那半多余（同一请求紧接着就删掉该用户的全部蒸馏行），该判断已写进函数 docstring，免得后人以为漏了
  - **顺序不变量锁**：`tests/test_admin_user_delete_distill.py` —— 取证点选在 spy 的 `delete_user` **内部**读 `_tasks`，比「停止函数有没有被调用」强（能识破「先删库后发信号」这种顺序颠倒的假修）；batch 路径逐用户发信号，不是只发第一个
  - **注释订正**（`53494ae`）：两 store 的 `hard_delete_text` 注释改成真实口径（断点生命周期跟文本走；非 `interrupted` 的残留行重蒸命不中，是已知取舍），证据脚本 `tests/perf/distill_resume_reachability.py` 与 `distill_orphan_matrix.py` 及其产物一并入库
  - **删卡/解绑的清理口径：不动**（保守裁决）。删卡不产生孤儿行（行身份 `(user, text, character)` 与卡无关），清理由文本死亡路径负责；改成「按 `(user, text, character)` 找最近一条可复用行」是**加功能**，另立项
- **未接停止信号的其余删除点（本轮只记不修）**：`web/routers/card.py` 的卡删除路由（`hard_delete("card")` ×2 / `soft_delete("card")` ×1），以及 `web/routers/market.py` 的 `delete_market_card`（直接调 `storage.delete_card`）—— 要把在跑的任务对上被删的卡需要 `(text_id, character)`，而 `character` 在 identify 步解析出名字之前是空串，匹配不可靠；且与上面「删卡口径不动」是同一件事

**21. `079_remote_user_profiles.sql` 从未接线 —— SQLite 新库缺 `remote_user_profiles` 表** —— 状态：**已修（2026-09-13）**
- 事实（实测）：`storage/migrations/079_remote_user_profiles.sql` 躺在目录里，但既不在 SQLite 次序表，PG 侧也无对应（PG 用 `migrations_pg/012_remote_user_profiles.sql`，其 glob 驱动已接线）。故 SQLite 新库**没有**这张表
- 而 `storage/sqlite_store.py` 已在用它：`INSERT OR REPLACE INTO remote_user_profiles` / `SELECT ... FROM remote_user_profiles` / `LEFT JOIN remote_user_profiles` → 新库走到这几条即 `no such table` 抛错
- 影响面同缺陷 5：生产是 PG（不受影响），受影响的是新开发环境 / `start_all.bat` 建的 SQLite 库
- **生产实证（2026-09-14，SZ + SG 两台各一次，只读）**：把上面那句「生产是 PG（不受影响）」从**推断**升级为**证据**。方法：两台 SSH → `sudo docker exec character-distill-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d remote_user_profiles"'`（容器内 unix socket + trust，未涉及任何口令；全程只跑 `\dt` / `\d` / `SELECT`，未跑 DDL、未重启、未跑迁移、未改任何文件）。
  - **结论：两台都有表，缺陷 21 在生产无影响，本件结束。** SZ 与 SG 均 `public | remote_user_profiles | table | charsim`，列集与 `migrations_pg/012_remote_user_profiles.sql` 声明**逐列一致**（`id text PK NOT NULL` / `username text NOT NULL` / `home_region text NOT NULL DEFAULT ''` / `avatar_data text DEFAULT ''` / `created_at timestamp DEFAULT CURRENT_TIMESTAMP`），索引仅 `remote_user_profiles_pkey`。
  - 两台 PG 容器均为 `created 2026-07-01`，晚于 `15e5f3e0`（2026-06-28）—— 即 012 上盘后容器确有过重建，glob 迁移链已跑过。故本条**不需要**「未重启」分支的排查。
  - 影响面据此定稿：**生产（PG）无影响，缺陷 21 的实际损害只存在于 SQLite 侧**（本地 / 测试新库），与上一条一致。
- **定性订正（2026-09-13 排查）**：此条**不是**「静默不执行」而是「**带理由地不执行**」—— 分发形态锁 `tests/test_migration_dispatch.py` 早已存在（`c1a8131`），079 躺在它的 `_NOT_APPLIED` 豁免名单里，所以锁是绿的。真正的洞是**豁免出口没有闭环**：写下理由即永久放过，而理由里写的后果（「新库缺表、代码在用」）没有任何东西去验。原设想的「每个文件必须有明确归宿」形态锁**不必新做** —— 它已存在，且正是被绕过的那一个
- **处置（2026-09-13，四步，均只记/只验不越界）**：
  - **接线**（`4be3ecf`）：`079_remote_user_profiles.sql` 加进 SQLite 执行器次序元组，从 `_NOT_APPLIED` 移除。位次依据：它建独立表、无依赖，按编号自然位次排。验收：新库实跑后表存在，三个引用点在真新库上不再抛 `no such table`
  - **闭环锁**（`4a608f9`）：`tests/test_sqlite_fresh_schema.py::TestExemptionClosedLoop` 断言「**SQLite 新库实跑后的表集合 ⊇ `migrations_pg/` 声明的表集合**」（排除 `sqlite_sequence`）。锁的是**症状本身**，不是「文件有没有登记」这个可被合法豁免绕过的代理指标；真源选 PG 目录，因为 PG 执行器是 glob、目录即清单、无豁免出口 —— 「PG 目录里有的表」= 「应该存在的表」的可靠定义。与 `test_sqlite_fresh_schema` 同一套基础设施，CI 可行，不需要真 PG
  - **store 层同族形态全量收敛**（`2ab669a`）：本轮另一条线，见下面「第七个同族形态」
  - **未修**：本条目自身已闭环，无遗留
  - **后续（2026-09-13）**：豁免的事实源从测试移进执行器 —— `tests/test_migration_dispatch.py::_NOT_APPLIED` → `storage/sqlite_store.py::_MIGRATIONS_NOT_APPLIED`（与次序元组同一处，读执行器的人一眼看到「哪个迁移被有意略过」）；测试与 `tests/perf/migration_coverage_audit.py` 只读那一份，不再自持副本。不变量：**豁免只有一个源**
- 同族风险（已用锁兜住）：次序表是显式元组，**加文件忘登记不会有任何报警**（079 就是先例），故 `test_migration_dispatch` 有「目录 ↔ 次序表求差集」形态锁
- **本条目衍生出的第七个同族形态 —— store 层「失败与空结果不可区分」**：SQLite 新库缺 `remote_user_profiles` 表时，`storage/sqlite_store.py` 的 `get_conversations` 把 `OperationalError` 吞成空列表 → 私信收件箱**恒为空、不报错、不 500**，日志里只有一行 print。根因不是「那一处吞错了」，而是 store 层用**同一个返回值**同时表达「查到了，结果是空」与「查询失败了」两种互斥语义。这正是前六个同族形态（线程弃船 / 384 维度 / 截断响应 / `finish_reason` 缺失 / `$contains` 恒不命中 / `admin_tasks` 静默截断）**都因为「只修出问题那处」**才长出第七个的原因，故 `2ab669a` 全量收敛：A 类 176 处（基线 `4a608f9`，sqlite 88 / pg 88）全改，不变量「**store 层的空返回值只表示「无数据」，永不表示「失败」**」定于 `storage/base.py` 的 `StoreError` 单一定义，容忍策略上移到调用方并注明理由。普查全集与 A/B 分类见 `2ab669a` 的 commit message，可 `python tests/perf/store_swallow_census.py --ref 4a608f9` 逐字复算

**22. `async_chat` 的 usage 被丢弃 —— Map 阶段与代词消解的 token 完全没记账** —— 状态：**已修**（commit `ac2692f`，2026-09-14）
- 事实（实测）：`adapters/llm_adapter.py` 的 `async_chat` 返回 `(result, usage)`，且**不写 `self.last_usage`**（该文件 `_async_chat_span` 注释自己写明了）。四个调用点里三个是 `result, _ = await ...`，usage 就地丢弃：
  - `_run_map_concurrent` 的 `_one`（sync MapReduce 的 Map）
  - `distill_incremental_stream` 的 `_one`（SSE MapReduce 的 Map）
  - `coref_resolve` 的 `_resolve_chunk`（代词消解分片）
  - 唯一正确记账的是 `_single_reduce_async`（`result, usage = await ...` → `_try_record_usage("distill_reduce", usage)`）
- 后果比缺陷 16 更大：Map 是 MapReduce 里**最烧 token 的一段**（每片一次调用，片数几十到几百），而它**一条记录都没有**；reduce / 初次 / 重修 / 流式各自都记。于是 `distill_*` 系列 action 的用量被系统性低估，且低估幅度随文本变长而放大
- **不属缺陷 16 的同源修法**：16 的病灶是「记账出口分散 + 一处漏」，收敛到一个 `_chat_initial` 出口即解决；本条的病灶是**记账出口从未写**，`_chat_initial` 覆盖不到（Map 不走它）。修它要先裁决「N 次并发调用怎么记」——N 条独立记录（写放大）还是一条聚合（需新增聚合态），是**设计决策**不是补漏
- **收口（`ac2692f`，2026-09-14）**：**不是只修点名那三处** —— 把下列机制锁的判据对 HEAD 全量跑：**36 个 LLM 调用点、23 个不流向记账出口**，全部接进同一出口（修后同一判据 36 个调用点、**0 个不流向出口**）。粒度按已定裁决「整阶段汇总一条 + 分片数」：`chunk_count` 落 `usage_stats`（SQLite 085 / PG 018 新列），语义 = **本次真调了几次 LLM**，不是文本分片数（续跑命中/重试会让二者不一致，注释里写死）。失败分片按字符估算照记并标 `estimated` —— 只记成功会让成本统计**系统性偏低**，偏低的统计比没有更危险。异步/sync Map、`coref_resolve` 三处汇总放 `finally`（gather 半途炸掉时已完成的分片也是花掉的钱）。**出口不新增**（缺陷 16 刚收敛完出口分散，不并列第二个出口）。
- **机制锁 `tests/test_usage_accounting_lock.py`**：**存在「调用 LLM 但不流向记账出口」的调用点即红。** 判据全部从事实推出、**不含调用点清单**（藏在守卫与被守对象之间的第二份手工清单本身就是漂移点，与缺陷 21 的豁免名单、缺陷 25 的命名代理同谱系）；出口「全仓恰好一处」且落在 `core/utils.py`，多一处即红（防缺陷 16 复发）。
- **状态订正（2026-09-14）**：本行在修复后仍标「未修」一日 —— 原因是**只读了这张表的状态行、没核 `git log`**。见 §四「台账状态行不是事实，是上一轮的记录」。

**23. PG 侧无对称形态锁 —— 迁移的「声明」与「真库」之间无闭环** —— 状态：**已修（2026-09-13，`TestPgFreshSchemaClosure`）**
- **原立项措辞要订正两处**（立项时按「SQLite 独有 66 表」理解，实测不成立）：
  - **现存差集是「文件 83 vs 17」，不是「表 0 vs 66」**。`_objects_from_sql` 复算：两目录各声明 **41** 张表，**双向差集都是 0**。「66」是**文件数**差 —— PG `001_init.sql` 并掉了前 66 个 SQLite 迁移文件，故编号结构性不对齐（PG `012_` 对应 SQLite `079_` 是先例），**按编号比不可行，按表集合比才是可判定定义**。
  - **朴素形态（加了 SQLite 迁移忘了 PG）已被锁住**：`tests/test_schema_parity.py` 早已**双向**断言两目录的表 / 列集合相等（`only_pg` 与 `only_sqlite` 都报错）。所以「反方向无锁」在**表粒度**上不成立 —— 只对「**声明 vs 真库**」成立。
- **真缺口（已修）**：那三把锁（dispatch / parity / fresh-schema）都只看「文件里写了」，不看「真库建出来了」。SQLite 侧有 `TestExemptionClosedLoop` 兜着（真建库 ⊇ PG 声明，配合 text parity 即 SQLite 侧闭合）；**PG 侧此前一条都没有，而 PG 才是生产**。
- **锁法**（`tests/test_postgres_store.py::TestPgFreshSchemaClosure`，两条断言）：真 PG 库 ⊇ `migrations_pg/` 声明表 **∪** `postgres_store.py` 引用表。第二条直接锁 079 的症状形态（代码引用库里没有的表）。
- **零豁免、不需要豁免名单**：真源取「表集合」而非「文件编号」后，上线当天就是绿的（实测 41 声明 / 41 建出 / 引用 41，差集全 0）。**这正是与缺陷 21 的关键差别** —— 拿编号当真源会带一堆豁免，而豁免即永久放行（§四 纪律）。将来真出现 SQLite-only 的表也不在此豁免：它以「不在 `postgres_store.py` 引用集」被第二条断言直接验掉，不靠理由文本。
- **为什么不与缺陷 21 的锁合并成一条双向断言**：依赖不同（一条只需 SQLite、无 PG 也能跑；一条必须有真 PG —— 本地无 PG 时 PG 段是 skip/error，合并会让本地丢掉 sqlite 侧保护）；盲区不同（一条防本地 / 测试库缺表，一条防**生产**缺表）。两条 docstring 已互相交叉引用。**列级闭合原为记账，已于 2026-09-14 补上**（见下）。
- **前提**：库必须「新鲜」（CI 每次给新 postgres service 容器）。对长期存在的 dev 库跑会被残留表掩盖 —— 实测删掉 `012_remote_user_profiles.sql` 后若库里还留着该表，两条断言都绿。已写进 docstring。
- **变异（实测两次）**：删 `012_remote_user_profiles.sql`（代码仍引用）→ 只有「引用表」那条红；让一条迁移「声明了但建不出来」→ 只有「声明表」那条红。两条各抓一类，互不代偿。
- 原「反方向无锁」判断的正误：**形态判断对（确实缺生产侧闭环），但把模板当成了表集合**。立项时的推断「PG 目录声明的表集合 ⊆ SQLite 新库表集合」也不对 —— 该方向早已被 `TestExemptionClosedLoop` 覆盖，真正缺的是**反向的、且对着真 PG 库**。
- **列级闭合（2026-09-14 补）**：`tests/test_postgres_store.py::TestPgFreshSchemaClosure::test_fresh_sqlite_and_fresh_pg_have_the_same_columns` —— 两侧**真库**逐表列集合双向相等（SQLite 读 `PRAGMA table_info`、PG 读 `information_schema.columns`）。真源是**另一侧真库**，不是文本声明。
  - **故意不用上面这个「真库 ⊇ 文本声明」的形状**，理由是实测出来的不对称：`DROP TABLE` **0 处**、`DROP COLUMN` **4 处**。表级那个形状能成立，靠的是一个从没写下来的前提 —— **没有任何东西删过表**；列级这个前提不成立。而删列在本仓由**两套完全不同的机制**完成：PG 是 `migrations_pg/005_data_residency.sql` 的四条声明式 `DROP COLUMN IF EXISTS`，SQLite 是 `storage/sqlite_store.py` 的 Python 表重建（`.sql` 文本里根本没有这条语句）。`test_schema_parity` 的提取器只认 CREATE TABLE + `ALTER ... ADD COLUMN`，两个都看不见 → `users` 的**声明列**比真库多 4 个（`password_hash`/`api_key`/`base_url`/`model`）→ 套用表级形状会**当场红**，要它绿只能加豁免清单（豁免即永久放行，§四）。两侧盲区**互相抵消**，这正是文本 parity 一直绿着的原因。故列级比「另一侧真库」：不需要声明列标尺、不需要 SQL 解析器、不需要任何豁免清单。**别把两处统一成同一个形状** —— 该不对称证据已写进两处 docstring，防止将来有人「顺手统一」把洞重新打开。
  - **变异（实测，真库真改）**：对 throwaway SQLite 真跑 `ALTER`、真重读 catalog —— 单侧加列 → 红并点名 `[users] 列 ['mutation_probe'] 只在 SQLite 新库里，真 PG 缺这些列`；单侧删列 → 红并点名 `[users] 列 ['embedding_region'] 只在真 PG 里，SQLite 新库缺这些列`。改 SQLite 而非 PG，因为 PG 是共享库，在它上面加删列会污染同一次会话里后面所有用例。另有一条**不需要 PG** 的负控 `test_column_probe_has_teeth`（读空了与真无漂移都是 0 处，那是要挡的）。无 PG 时显式 skip 且原因可见，`REQUIRE_PG_TESTS=1` 时拒绝 skip（两向均已实测）。
  - **两条潜伏路径（记账，不修）**：① `test_schema_parity` 的 `_COL_TYPE_RE` 类型表覆盖不全（`VARCHAR`/`NUMERIC`/`JSONB`/`DATE`/`UUID` 等识别不到；实测现采列类型全部在表内 —— SQLite `{TEXT,INTEGER,TIMESTAMP,REAL}` / PG `{TEXT,INTEGER,SMALLINT,TIMESTAMPTZ,TIMESTAMP,DOUBLE PRECISION,BIGSERIAL}` —— 未触发）；② 裸 `ALTER TABLE ... ADD <col>`（PG 合法、SQLite 不合法；实测两侧 84 + 15 处**全部带 `COLUMN` 关键字**，未出现）。**real-vs-real 判据天然免疫这两条**（不依赖类型/语法解析），故是记账不是待修；但若有人把列级锁退回文本层，这两条会复活。
- 同轮另两条观察（**非缺陷，只记**）：
  - **豁免名单住在测试里，不在执行器里** —— **已修（2026-09-13）**：豁免的事实源移到执行器 `storage/sqlite_store.py::_MIGRATIONS_NOT_APPLIED`（与次序元组同一处，改执行器的人一眼看到「哪个迁移被有意略过、为什么」），其余三处读者（`tests/test_migration_dispatch.py` / `tests/perf/migration_coverage_audit.py::_exemptions` / `tests/test_sqlite_fresh_schema.py` 的报错文案）全部改从执行器读，**不再自持副本** —— 不变量：豁免只有一个源。新增 `test_exemption_has_one_source_in_the_executor`：往执行器声明塞一条指向不存在文件的假豁免，dispatch 锁必须相应变红（证明测试读的确实是执行器那份）。仓外变异：执行器加 `"999_ghost.sql"` → `test_every_migration_file_is_dispatched` 红（stale 分支）
  - **PG `001_init.sql` 保留了 SQLite 侧已删的 4 个遗留列**（`users` 的 `password_hash` / `api_key` / `base_url` / `model`，`migrations_pg/001_init.sql:93-98` 内联声明）。**已复核，运行期 schema 无漂移**：这 4 列由 `migrations_pg/005_data_residency.sql` 的 `DROP COLUMN IF EXISTS` 删掉；两侧逐表逐列求交集（`001_init` + 全部 `ALTER` − drop）**完全相同**（用 `tests/perf/migration_coverage_audit.py` 的 `_objects_from_sql` 复算）。漂移只在**文件层**：读 PG 的 `001_init.sql` 会看到一个最终库里并不存在的列清单（SQLite 侧这 4 列本就不在 `001_init` 声明、是后来 `ALTER` 加的，故它的 `001_init` 天然干净）。**非缺陷**，但会误导「按 bootstrap 文件推断 schema」的人 —— 与上面两条同属「文件的陈述与运行期事实不一致」

**24. 提交责任无归属 —— SQLite store 的写方法漏 `commit` 就静默丢数据，「失败被吞成正常返回」的第八次显形** —— 状态：**已修**（2026-09-13，`4a91868`）
- 事实（实测）：`_connect()` 用 `aiosqlite.connect()` 且全仓未设 `isolation_level=None` → legacy 事务模式，INSERT / UPDATE / DELETE / REPLACE 隐式开事务，不 commit 则 close 时被回滚；而 `_ConnectionContext.__aexit__` 当时**只 close 不 commit**。于是任何写方法自己忘了 `await conn.commit()`，就「写了、函数照常返回构造好的 dict / rowcount、数据不在、**连异常都没有**」。现场两处：`add_post_comment`（INSERT 后无 commit，仍返回 `{"id": ...}`，前端把评论显示出来、刷新即消失）、另一处 UPDATE 方法（UPDATE 后无 commit，仍返回真实 `cursor.rowcount`；该方法已在 2026-09-22 随缺陷 86 收尾整段删除，见条目 86）
- **普查（AST 全量，不是「报一处修一处」）**：扫 235 个「在 `_connect` 作用域内且有调用」的方法，得 **2 处**（`add_post_comment` + 上述 UPDATE 方法；后者已删）。报告只点名 1 处，全量扫出 2 处 —— 前七次同族形态都因「只修出问题那处」才长出下一次，故本轮先做全集普查再动手
- **同型定性**：与缺陷 21 的「豁免出口没有闭环」同型 —— **都是把正确性寄托在人的记忆上，没有机制兜底**。这是「失败被吞成正常返回」的第八次显形（前七次：线程弃船 / 384 维度 / 截断响应 / `finish_reason` 缺失 / `$contains` 恒不命中 / `admin_tasks` 静默截断 / store 层 `except: return <空值>`），且是**唯一一次连异常都没有的**
- **方案裁决（风险实测，不是偏好）**：
  - 方案 A（`_connect()` 设 `isolation_level=None` 自动提交）：风险 = 27 个「一个连接里做多步写」的方法**失去事务边界**，多步写中途失败会留半写状态 —— 实测把 `_connect` 改成 `isolation_level=None` 后，两个最长的多步写方法（`delete_user` / `hard_delete_text`）的原子性用例**双双变红**。这是把一个静默丢数据换成一个静默留半写，不可接受。（「多步写方法」口径：方法体内 ≥2 条 `execute` / `executemany` 写语句，共 27 个，`delete_user` 12 条 / `hard_delete_text` 8 条最长；可复算）
  - 方案 B（`_ConnectionContext.__aexit__` 无异常 commit、异常 rollback）：风险集**实测为空** —— 全仓 0 处显式 `rollback`、0 处「故意不提交」、只读作用域上 commit/rollback 是 no-op。**选 B**
- **改法（单点，不复制补丁）**：提交语义收敛到 `_ConnectionContext.__aexit__` 一处（先 commit/rollback、再 close），`_ConnectionContext` 类 docstring 写明「提交语义由本类承担（承重，勿改回只 close）」。**两处 offender 由机制吸收，不去各自补 commit**；调用方零改动、对外签名与返回语义不变。与 `2ab669a` 的 `StoreError` 单一出口同形（单点定义、调用点不各写各的），未引入第二种事务/错误管理风格
- **提交这一步本身不得再造同形态失败**：`__aexit__` 的 commit 失败必须上抛（吞掉 = 调用方以为写成功而库没有，正是本类要消灭的形态）；异常路径的 rollback 失败不得顶替调用方原始异常（沿用既有 close 段口径：print 可见、不上抛），两处均写 `# store-empty-ok:` 说明
- **两层防线（各管各的）**：`tests/test_store_connect_lock.py` —— **形态锁**：sqlite store 任何函数不得绕过 `_ConnectionContext` 直接 `aiosqlite.connect` / `sqlite3.connect`（白名单 `_connect` / `_ensure_initialized` 各附理由 + 名单腐烂检查）；`tests/test_store_commit_contract.py` —— **语义用例**：跨连接读回（不是同连接内的可见性假象）、两处 offender 写入后新连接读得到、多步写抽样（`delete_user` / `hard_delete_text` 在**最后一条写语句**处注入异常 → 全回滚无半写）、只读作用域 commit/rollback 是 no-op。**变异**：删掉 `__aexit__` 的 commit → 3 条语义用例红（形态锁仍绿）—— 证明「只锁形态」不够，两层都要有（§四「形态锁与语义用例是两层防线」）
- **PG 侧无需改**：asyncpg 语句默认自动提交，另有 18 处显式 `async with conn.transaction():`（原生 clean-exit 提交 / 异常回滚语义），缺陷 24 是 SQLite legacy 事务模式特有的
- 同轮范围核查（**已扫，无缺口**）：store 之外的直接 sqlite 连接全查过 —— 4 个带 `sqlite3.connect` 的运维/诊断脚本（`check_missing_card` / `diagnose_psyche` / `export_shiyu` / `integration_check`）全为只读，`rebuild_384_collections` 用 `mode=ro` URI，`migrate_sqlite_to_pg` 只读 SQLite、写 PG（asyncpg 自动提交）。无需处理

**25. SQL 事实锁的盲区 —— 签名留着身份参数、SQL 却不再用它过滤，静态锁看不见** —— 状态：**已修（判据 v2，2026-09-13）**
- 形态：缺陷 19 commit 5 的判据是「读属主表 ∧ WHERE 无身份列谓词 ∧ 无身份参数 ∧ 不叫 `_unscoped` ∧ 不在白名单」。其中**「签名有身份参数」单独足以**判为已收窄 —— 参数在 ≠ 参数用了。跟刚换掉的「名字后缀」是同一类代理指标，判据里还留了半个
- 实测（缺陷 19 收口时的 PG 侧变异）：删 PG 版 `get_card_owned` 的 `AND c.user_id = $2`、**保留签名参数** → 静态锁**绿**（只比方法名的覆盖断言也绿）；谓词与参数一起去掉才红。这一类当时只有运行期语义用例抓得住（sqlite `tests/test_security_authz.py`；PG `tests/test_postgres_store.py::TestPgOwnedIdentityIsolation`）
- **为什么当场修（推翻本条原先的「记账」处置）**：「当前无真漏」正是最危险的状态 —— 锁绿着、盲区在，下次谁删一行谓词就静默通过；而这个盲区是**变异实验**自己撞出来的（可复现、可被利用），不是理论担忧。验收标准就定为「那条变异必须红」
- **修法（判据 v2）：「已收窄」只认 WHERE 里的身份谓词这一条事实，签名参数不再单独充分**。三处提取器精度：①**F1 片段拼合** —— 动态 `append("s.user_id = ?")` 与 `f"WHERE {...}"` 分属不同常量，判据改在**拼合后的整段 SQL** 上找（docstring 剔除，它是散文不是 SQL）；②**F2 一表多列** —— 属主表可能多列指向 users（`user_follows` 的 `follower_id` + `following_id`），v1 每表只取第一列，`WHERE following_id = ?` 因此被判「无谓词」；③**F3 users 自身** —— `WHERE u.id` / `u.username_lower` / `u.email = ?` 也是身份谓词（须带 users 别名限定，否则 `WHERE s.id = ?` 会混淆）
- **v1 `idp` 盲区名单 8 处，逐条 SQL 依据**（两后端同形，仅 `?` vs `$n` 之差）：`get_user_by_id` `WHERE u.id = ?`；`get_user_api_config` `WHERE u.id = ?`；`get_user_by_username` `WHERE u.username_lower = ?`；`list_sessions` `where_clauses.append("s.user_id = ?")`（与 `f"WHERE {' AND '.join(...)}"` 分属两串 → F1）；`get_followers` `WHERE following_id = ?`（v1 的 idcol 只取 `follower_id` → F2）；`get_unread_count` `WHERE receiver_id = ?`（idcol 只取 `sender_id` → F2）；`get_liked_comment_ids` `f"... comment_id IN ({ph}) AND user_id = ?"`（谓词与 f-string 字面部分分属两串 → F1）；`get_text_deletion_impact` —— **唯一真无身份事实**的一处
- **`get_text_deletion_impact` 的裁定**：形参 `user_id` 是调用点统一签名留下的、SQL 不用它（sqlite 全按 `text_id`：`cards` / `sessions↔cards` / `messages.session_id IN (...)` / `distill_tasks`；PG 同形）。**不能**给它补身份谓词 —— 调用方 `web/routers/text.py` 先 `get_text_owned(text_id, user_id)`，拿不到且 `user.is_admin` 时才走到这里，算的是「删这篇文本会连带影响多少行」，加了过滤会把管理员看到的数字变成 0。故按「**有意跨属主**（admin 逃生口）」登记进 `OWNER_READ_ALLOWLIST` 并写明理由；同款 router 层逃生口早已在 `UNSCOPED_ALLOWLIST`
- **第二道断言**：`test_signature_param_never_substitutes_for_a_where_predicate`（「有身份参数 ∧ 无身份谓词 ∧ 未登记 → 红」）。v2 落地后只命中 `get_text_deletion_impact` 一处且已登记 —— 即**没有「判据没做全」的残留**
- **探针非空转**：`test_criterion_probe_is_not_vacuous` 负控 + 正控（合成「只有 `user_id` 形参、SQL 按 `texts.id` 读」的方法必须红；SQL 里加 `user_id = ?` 必须不红）—— v1 正是被一次「刚好绕过判据」的变异撞瞎的（§四）
- **变异验证（实测）**：删 PG `get_card_owned` 的 `AND c.user_id = $2`、保留签名参数 → 主锁 + 第二道均红；sqlite 同形同红；两处各自字节还原
- **副作用（名单收敛）**：删掉 `get_user_by_email` 的豁免条目 —— `WHERE u.email = ?` 现被认作身份谓词，它不再是「未收窄」的原语；陈旧条目由 `test_owner_read_allowlist_has_no_stale_entries` 逼出。豁免总数 21 → 21（去 1 增 1）
- **收口补记（2026-09-13，判据 v2 落地时 3 条红的定性）**：全量 3 条红逐条处置，**没有一条以「改动前既有」放行** —— ① `tests/test_rag_unusable.py::test_caller_group_rebuild_degrades_no_index`：被测对象在 `e3cf9e8` 改名（`get_group_session` → `get_group_session_owned`）而用例桩没跟，属**测试写法过期**，修测试；「为什么没人发现」的答案是 **CI 跑了也红了、只是没人看**（`ac6fc5b` / `9e899a2` 的 run 日志里它明确 FAILED），**不是**覆盖缺口，故不新增 CI 步骤。复发堵法见 §四第三条（`_stub()` 不抄清单）。② ③ `TestPgFreshSchemaClosure::test_every_declared_table_is_created` / `test_every_referenced_table_is_created`：只在本机无 PG 时红，CI 侧一直是绿的（`0 skipped`）—— 按「标准本地环境下恒红会训练人忽略红色」处置为**显式 skip + 拒跳声明**，见 §四第二条。三条各自的变异验证也记在该处

**26. 「全新库」与「重启过的库」是两个 schema —— users 删列的触发条件用了「删一次就再也不出现」的哨兵列** —— 状态：**已修（2026-09-14）**
- **形态**：`storage/sqlite_store.py` 的 users 删列块（数据居留：删 `password_hash`/`api_key`/`base_url`/`model`）原先的触发条件是 `if "password_hash" in all_cols`。而 `password_hash` 只在 **009** 建表时出现、首轮启动就被删掉、**再也不会回来**；`api_key`/`base_url`/`model` 却由 **018** 每轮重新 `ADD COLUMN`（执行器 `_apply_migration` 判「迁移有没有应用过」只看**列在不在**，没有「已应用」账本）。于是在**重启过的**库上：018 加回三列 → 哨兵没出现 → 整块跳过 → 残留态（25 列）**永久驻留**。同一个库第一轮 22 列、第二轮之后 25 列。
- **为什么它是缺陷 23 的直接产物**：缺陷 23 的列级锁比的是 **fresh SQLite ⟷ fresh PG**（两侧都只跑一轮 init），于是它**今天绿** —— 而**生产上跑的恰恰是「重启过的库」**，那个状态从来没有任何锁看过。锁的输入状态本身也是判据的一部分（教训入 §四）。
- **severity 低（不是数据丢失）**：那三列是**死列** —— `get_user_api_config` 读的是 `user_secrets` 的 `s.api_key`/`s.base_url`/`s.model`（JOIN 出来的），**从不读 users 上这三个**。危害只在 schema 漂移本身（两个后端列集不一致 → 任何「按表全列 SELECT/INSERT」的代码路径会炸 `no such column`）。
- **修法**：删除判据收敛到模块常量 `_USERS_LEGACY_COLUMNS`（单一事实源），触发条件 = 「**四列任一存在**」（不变量是「users 不得有这四列」，触发条件就该是这四列的任一）。**没有引入第二份手工清单** —— 判据与删除对象读的是同一个常量。
- **拉锯要不要消除的裁决：不消除，但把每轮代价从 O(数据量) 压到列删除**。018 **不能摘**（老库上没有这三列时，070 的 `INSERT ... SELECT u.api_key` 直接 `no such column` —— 而 070 靠 `_apply_migration` 的「home_region 已在 → 整份跳过」在新库上才不被重跑）；执行器又没有「已应用」账本，所以「018 照加、这里照删」每轮都会发生。**代价实测**（`e2e/scratch/time_users_rebuild.py`，全表拷贝口径）：4 行/180KB ≈ **20ms**、1000 行/200KB ≈ 2.7s、5000 行/200KB ≈ **18s** —— 每轮全表拷贝在生产体量上**不可忽略**，故 3.35+ 改走原生 `ALTER TABLE users DROP COLUMN`（省掉临时表 + `INSERT..SELECT` + 索引重建，且**不碰**其余列类型 —— 原重建的 `col_defs` 兜底会把未知列静默重定型为 `TEXT`），<3.35 回落原表重建并把这段 O(rows) 代价注明在该分支上。本机 3.49.1 走原生路径。**（订正 2026-09-23，随缺陷 82）**：最低版本定为 SQLite 3.35 —— `<3.35` **不再回落原表重建**，改为在 `_ensure_initialized` 开头直接抛 `RuntimeError` 退出，那条既不幂等又会留残骸的回落分支已**整体删除**；上面「每轮 O(rows) 代价」的实测口径随之只作历史读数保留，「拉锯每轮只付列删除」现在是无条件成立的。
- **验收锁（两把，都是新增）**：① `tests/test_sqlite_fresh_schema.py::test_second_init_adds_nothing_and_stays_silent` 补断言「**两次 init 后 users 列集相等**」—— 该用例名字里写着 "adds nothing"，此前只断言 embedding 两列 + 无失败输出；② `tests/test_postgres_store.py::TestPgFreshSchemaClosure::test_restarted_sqlite_matches_fresh_pg` —— **二次 init 后的 SQLite ⟷ fresh PG**（缺陷 23 的锁扩到「重启过的库」，这是本轮核心收益）。
- **变异（实测，sha256 逐字节还原）**：把触发条件改回旧语义（`if "password_hash" in all_cols` 才删四列）→ ②红（`[users] 列 ['api_key','base_url','model'] 只在 SQLite 新库里，真 PG 缺这些列`）、①红（第二次 init 新增 `['api_key','base_url','model']`），而缺陷 23 那条 **fresh ⟷ fresh 的锁仍绿**（1 passed）—— 这条绿正是「只守理想初态」的实证。
- **测试写法上的一处连带纠正**：`_sqlite_columns` 初版每次读都跑 `_ensure_initialized`，于是变异被「重跑又补回来」当场修复，变异永远红不了（红出来的反而是补列副作用）。拆成 `_build_fresh_sqlite`（建一次）+ `_sqlite_columns`（**只读，绝不重建**）—— 读事实的锁不许带写副作用。缺陷 26 正是这次拆分暴露出来的。

**27. `_retrieve_scenes` 的 `hasattr` 降级分支：注释描述的降级条件从来不是这段代码判的条件** —— 状态：**已修**（分支随 Evidence 2b 重构删除，`2a50c82`，2026-09-14）
- **形态**：旧代码 `if hasattr(self.rag, "query_with_emotion"): <情感加权检索> else: snippets = self.rag.query(...)`，注释写「优先用情感加权检索；**若集合无 emotion metadata（chunk 模式）则降级**」。但 `hasattr` 判的是**对象**有没有这个方法，与**集合**有没有 emotion metadata 毫无关系 —— 注释描述的降级条件从来不是这段代码判的条件。
- **该分支不可达**：全仓只有 `core/rag.py` 定义 `query_with_emotion`；无 `__getattr__` 代理、无 rag 包装类；生产 rag 取值只有 `RAGEngine`（`web/app.py` / `indexing_service.py` / `group.py` / `mcp_server/server.py`）或 `None`（`text_manager.py`，纯卡面无检索），而 `None` 在函数首行即被挡掉（`MagicMock` 也走不进去，`hasattr` 恒真）。
- **误导性**：那句注释至少误导了一轮判断（把「集合无 emotion metadata」当成真实的降级触发条件去推理）。**与缺陷 21 的注释、PG `001_init.sql` 的遗留列同族：文件的陈述与运行期事实不一致。**
- **删除而非补 loud-fail 分支的裁定（用户，2026-09-14）**：为一个不可达条件加告警分支 = 给死代码挂报警器（YAGNI）。删掉后行为反而更响：将来注入一个缺 `query_with_emotion_ex` 的 duck-typed rag → `AttributeError` → `status="failed"` + 打印；旧路径是**静默**走 `.query()`（返回无 meta 的字符串，零告警）。
- **删除是 2b 重构的强制后果，未单独立 commit**：Evidence 线硬要求「块字符串必须由 `EvidenceItem` 列表渲染」，而该 else 只返回字符串、构造不出 items —— 留着即构成第二份构造路径。

**28. `test_fails_open_when_llm_is_none` 不 hermetic —— 用例会真发一次外部审核请求** —— 状态：**已修**（2026-09-14）
- **形态**：用例传 `llm=None` 期望走 fail-open，但 `core/moderation/auto_review.py` 在 `llm is None` 时回落到 `deps.get_llm()`，而 `web/deps.py` 的 `get_llm()` 在 API 已配置时返回**真** `LLMAdapter` 单例 —— 于是该用例真的打一次外部审核请求。
- **为什么比偶发红更严重**：**打真实 API 的用例本身就是不可信绿**。结论取决于当时的网络与模型输出：网络失败被那条 `except` 兜成 fail-open → 假绿；模型真回一个 `pass: false` → 假红。实测在整套 948 例里出现过 1 次偶发红，单跑 / 单文件 / 重跑整套均绿 —— 「偶发」正是这条信道不可信的证据。
- **修法**：把 `deps.get_llm` 换成一个返回 None 的**普通替身函数** —— `patch("deps.get_llm", new=_unavailable_llm)`，**不是 MagicMock**（工厂的形态就是「无参调用」，一个真函数即可完整表达；用 mock 则方法名拼错也不报错，正是缺陷 29 那轮的教训）。并加一条**独立信号** `assert calls == [1]` 钉住「确实走了取全局 LLM 这条路并拿到 None」—— 少了它，用例会退化成「碰巧 `get_llm()` 也返回 None」的假绿（被测分支没走到，§四）。
- **断网 / 无凭据仍绿**：替身返回 None，`auto_review_card` 在第二个 `if llm is None` 处 fail open，全程零出网。
- **变异（实测，两刀各自红在有病灶名的那句上）**：① 生产的 fail-open 分支改成 `pass False` → `assert result["pass"] is True` 红（`assert False is True`）；② 摘掉用例里的替身（退回不 patch）→ `assert calls == [1]` 红（`assert [] == [1]`），且捕获 stdout 显示真走到了 `try_record_usage` —— **即真发了一次外部审核请求**。第二刀是**回归锁**：证明「有人把隔离拿掉、用例退回打真 API」会被抓住，否则这次修好的东西下次一次顺手编辑就长回来。脚本 `e2e/scratch/run_defect_28_mutations.py`（gitignored，先验基线 failed=0）。
- **全仓普查：第二个会真打外部请求的用例 —— 无。** `tests/test_*.py` 里出现真 `LLMAdapter()` 或 `urlopen` 的只有 `test_chat.py` / `test_distill.py` / `test_connection.py` / `test_integration.py` / `test_distill_progress.py` / `test_following_api.py` —— **全部是脚本式**（只有 `main()`、无 `test_*` 函数），pytest 收集不到，不在套件内；`tests/perf/*` 同理（非 `test_` 命名）。`web/routers/market.py:471` 那处真 `LLMAdapter()` 只被**属主**发布路径触达，而 `test_ownership_404.py` 的发布用例一律以**非属主**身份打、在 `get_card_owned` 的 404 处（`market.py:498-501`）即被拦下，到不了审核调用。`test_llm_none_flags_injection` 本就 `patch("deps.get_llm", return_value=None)`，已隔离。

**29. `agent_loop.py` dedup 分支：根因是 `steps` 的语义从未定义，`ok = True` 只是它的症状** —— 状态：**已修**（Evidence 收口，2026-09-14）
- **最初记的形态**（把症状当成了病）：`if dedup_key in executed:` 分支写 `result_content = ...` 后跟一句 `ok = True`；而 `ok` 的两处读取（`steps.append` 的 `"ok": ok`、`if ok and result_content != EMPTY_RESULT` 的 `retrieved.append`）都在 `else` 分支内、紧随它自己的 `ok = result.ok` 之后 —— 全文件无一处在该分支之外读它，看着就是**死存**。
- **真问题（用户核代码时指出）**：dedup 分支**整个不进 `steps`**，而模型确实收到了一条 tool 回填、确实消耗了一轮上下文。根因不是那一行死存，是 **`steps` 的语义没定义过**：它数的是「工具执行次数」还是「模型经历的轮次」？`MAX_STEPS` 数的是**决策轮次**（`for _ in range(MAX_STEPS)`），`steps` 数的却是**执行次数**（一轮多调用时条数 > 轮数）—— **同名不同义**，这才是病灶。
- **判定（依据：谁在读、读它干什么 —— 全仓普查）**：① 生产/路由层**零读者**（`core/chat_engine.py` 的 `_run_agent_phase` 只用 `messages`/`retrieved`/`evidence`/`degraded`；`web/` 全目录无一命中）；② 唯一非测试读者是 `scripts/run_agent_eval.py`，只读 `s["tool"]` 的**名字集合**与 `len(steps)`（chitchat 判 `== 0`）——**两个定义下它给出的结论完全相同**；③ 字段形态 `{tool, args, ok, elapsed_ms}` 本身就是执行台账（`elapsed_ms` 是执行耗时，dedup 没有执行；`ok` 取自 `result.ok`，dedup 没有结果）——给 dedup 造一行就得**发明**这两个值；④ commit 3 的同型先例：`timeout` 从引擎三态拆出去，因为它是**调用层**事实而非检索事实，dedup 同理是**编排层决策**；⑤ **决定性**：commit 3 已定死「dedup 不产新 trace」（同一查询没发生第二次检索）。若让 `steps` 记 dedup，**同一事件在两个台账里说法相反**（`steps` 说发生过、`evidence` 说没发生）。故 `steps` = **实际执行过的工具调用**，dedup 三处一起不记，只留 `messages`。
- **改法**：删 `ok = True`（症状随之消失，不再需要为一个「没发生」发明成功值）；`AgentLoopResult.steps` 的字段注释 + `run()` 的 docstring 写清语义与 **三个台账的分工**（`messages` 模型经历的 / `steps` 实际执行的 / `evidence` 检索发生的）；dedup 分支加注释说明为何不记步。
- **验收锁（两条新增，缺一不成）**：① 「模型确实收到了那条 dedup 回填」—— 断 `len(tool_contents) == assistant_calls == 5`（工具协议不变量：每个 `tool_call` 都要有回填）且 `tool_contents.count(_DEDUP_NOTE) == 1`；② 「`MAX_STEPS` 与 `steps` 不同义」—— 断言 `rounds == 2` 且 `len(res.steps) > rounds`，把这个不等变成**被验证的事实**而非注释里靠人读到的一句。两条都在 `test_evidence_accumulation_dedup_and_order`（复用该场景：2 轮 5 调用，其中 1 次去重 + 1 次未知工具 → `steps` 4 条）。
- **变异（实测，四条各自红在有病灶名的那句上）**：dedup 回填文案改一字 → `assert 0 == 1`（计数）；dedup 分支补一条 `steps.append` → `assert 5 == 4`；`steps` 改成按轮记（两刀：摘掉执行处 append、挪到 `tc` 循环外）→ `assert 2 > 2`；`EMPTY_RESULT` 改一字 → 字节基线两条红（`scene_empty: tool 消息变了`）。脚本 `e2e/scratch/run_defect_29_mutations.py`（gitignored，先验基线 failed=0）。
- **顺带修掉一处同源恒真**：`_EMPTY` 原先写作 `_EMPTY = EMPTY_RESULT`（**从生产 import**），于是「冻结字节基线」与生产值同源、改 `EMPTY_RESULT` 两边一起变 —— 正是 §四「等式类断言先问两边是不是同一个来源」的形态。改为字面量 `"未找到相关内容"`；上一条变异（M23）即证明它有牙。

**30. 群聊路径的「不渲染证据」只由前端兜底覆盖，后端无专门用例** —— 状态：**已修（`885735c`，2026-09-17）**（Evidence commit 5 自查，2026-09-14）
- **形态**：证据的**写入**只有 `web/routers/chat.py` 两个调用点（`_do_chat` / `_do_chat_stream`），**读取**只有 `web/routers/history.py` 的 GET 与 resume。群聊走的是另一套原语（`storage.save_group_message` / `get_group_messages`，签名里根本没有 evidence 参数）与另一条历史出口 —— 即群聊的**写侧恒不产证据、读侧恒不带证据**，两条都成立。
- **薄在哪**：这两条「成立」**没有任何后端用例守着**。把 `save_group_message` 将来改成能带证据、或群聊历史出口将来接上 `parse_evidence`，都不会有锁变红；「群聊不渲染」这个结论目前只由前端 `EvidenceRail` 的「evidence 缺失 → 返回 null」那一行覆盖。
- **原「为什么不现在补」的前提不成立（订正）**：原文写「起群聊要一整套新测试基建（群聊会话夹具 + 群聊路由器依赖），成本远超收益」——**那套基建早就有了**，`tests/test_group_affinity_list_api.py` 里就有 `store` / `user_id` / `client`（群聊路由器 + 两处 `dependency_overrides`）与 `_create_group(store, user_id, card_ids)`。本轮把夹具照搬到证据线那个文件里，没有新建任何框架、没有新文件。这条记账搁了两轮，代价就是「不知道基建已存在」。
- **已修（`885735c`）**：`tests/test_message_evidence.py` 新增 `TestGroupPathIsNotOnTheEvidenceLine`（文件是证据线的既有家族，两处新增都附在它末尾；`SQLiteStore` / `PostgresStore` / `FastAPI` / `TestClient` / `get_storage` / `get_current_user` 全部复用文件里已有的 import，只多引一个 `inspect` 与 `group_router`）：
  - ① **写侧形态锁**：`inspect.signature` 逐类断言 `save_group_message` 签名里没有 `evidence` 参数（同 `test_default_call_sites_are_unchanged` 的形态锁思路 —— 加参数必须在**改断言**时被意识到，而不是红了之后「顺手补上」）。
  - ② **读侧走接口**：`GET /api/group/{gid}/history` 返回的条目里没有 `evidence` 键。走接口而不是只看 store —— 出口才是前端拿到的东西。
- **变异验证（实测两臂，production 码还原后逐字节核过）**：臂 1 给 `SQLiteStore.save_group_message` 加 `evidence: str | None = None` → 用例 ① 红，失败信息点名 `SQLiteStore.save_group_message 多了 evidence 参数`（另一条绿，两臂互不牵连，证明是两条独立判据而不是一条测两遍）；臂 2 在 `group.py` 的 `/history` 出口加 `for _m in messages: _m["evidence"] = None` → 用例 ② 红，失败信息把**实际键集**整份打出来（`['content', 'created_at', 'evidence', 'group_id', 'id', 'reactions', 'reply_to_id', 'reply_to_preview', 'role', 'speaker', 'speaker_card_id']`），一眼看出多的是哪个。臂 2 用的是「加键但值恒 None」这种**最容易被当成无害**的形态 —— 它照样被抓，因为判据锁的是**键在不在**，不是值。
- **顺带修掉同文件一处过度声明**：`test_default_call_sites_are_unchanged` 的 docstring 原写「不传 evidence 的老调用点（用户消息 / 摘要 / **群聊**）」，而函数体只测了 `save_message(sid, "user", ...)` 一条 —— 群聊连 `save_message` 都不走。docstring 改为只留「用户消息 / 摘要」并指向本条的类。**这正是本缺陷的形态本身**（声称有覆盖、实际没有）在同一文件里的第二次显形。
- **跨层覆盖不算单点全包**：前端那行只保证「拿到不带 evidence 的条目时不崩」，不保证「后端不会开始产证据」—— 后者才是这条缺口的实质。
- **判据命令**：`git grep -n 'save_group_message\|get_group_messages'`（列两侧原语的全部引用点，与上面两条断言对齐）。
- **同源路径普查（全量，不是只测点名那处）**：`web/routers/group.py` 里 `save_group_message` 的**生产调用点 5 处**（:510 :514 两处系统消息、:574 用户消息、:596 :609 角色回复）与 `get_group_messages` 的 **4 处**（:173 重建会话、:500 :561 组装上下文、:708 历史出口）——**没有一处单独断言**，也**不需要**：签名里没有这个参数，5 处调用点就一个都传不进去。这是「形态锁 > 逐点断言」的地方：逐点写 5 条会在第 6 个调用点出现时漏掉，签名锁在参数出现的那一行就红。两个 store 各一处定义，`PostgresStore` 那条不在本机跑（无 PG），但 `inspect.signature` 是纯静态的，在 collect 期就成立 —— 不需要 PG 也守着。
- **不确定项（如实记）**：PG store 的那条断言在本机被 collect 并执行（`inspect.signature(PostgresStore.save_group_message)` 不建连接），所以不挂 `PG_ENV.skipif`；本轮全量实跑为 `1125 passed, 64 skipped`（改前基线 1123），增量正好是这两条。
- **全程生产代码零改动**：两臂变异都已还原，`git diff --stat` 仅 `tests/test_message_evidence.py`（75 insertions, 1 deletion）。

**31. `steps` 的三键（`args` / `ok` / `elapsed_ms`）全仓零读者 —— 表态：保留，定位为执行台账** —— 状态：**已裁定·保留（不删，定位为执行台账）**（Evidence 收口 2026-09-14；2026-09-15 从「记账」移出）
- **事实（普查，不是印象）**：读 `steps` 的只有 `s["tool"]`（`scripts/run_agent_eval.py` 取名字集合判「触发是否命中 expected」）与 `len(steps)`（同文件判 chitchat `== 0`；测试里计数）。**`args` / `ok` / `elapsed_ms` 三键零读者**。`elapsed_ms` 在别处也没有同义记录：`ToolResult.elapsed_ms` 只在 `tools.py` 的 print 里用；OTel 的 `agent.execute_tool` span 自带 duration，但 **`OTEL_ENABLED` 关时装饰器原样返回、根本没有 span**。
- **表态：保留。** 定位 = **执行台账**（「实际跑了哪几次、各花多久、块空不空」）。台账的价值在**多键关联**，不在于单键被谁第一时间读；删掉三键后 `steps` 退化成 `list[str]`，与 `messages` 里 assistant 的 `tool_calls` 完全重复。
- **但有一条硬要求**：**它们当前不参与任何判定** —— 谁要拿它们做判据，必须先在此登记「谁在读、读它干什么」；不登记就拿来当判据，就是下一个「写了没人读」。三键中 `ok` 是唯一没有现成替代的（span 不带 ok；`retrieved` 与 `evidence` 是两个不同口径的投影，都要两步推导才等价）。
- **将来二选一的判据（可观测性工作开始时定）**：要么把 `ok`/`elapsed_ms` 提成 `agent.execute_tool` span 的 attr（那里已有 `tool` attr）并**删掉三键**，要么明确 `steps` 就是导出给评测脚本的台账、把这句话写进字段注释。判据命令：`git grep -n 'elapsed_ms\|\["ok"\]'`。
- **为什么现在不删**：与缺陷 29 是两件事（那是**语义**，这是**去留**）。零读者字段的删除不产生行为差异，混进 29 的 commit 会让「哪行因哪个动因改动」说不清。

**32. `docker-compose.local.yml` 缺 env 变量时静默降级成空串 —— 「失败被吞成正常运行」的再一次显形** —— 状态：**已修**（2026-09-15，两个 compose 文件同批）
- **形态**：app 服务的 `DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}`，而 `.env` 里**没有** `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` 三个条目 → compose 只打一行 warning（`The "POSTGRES_USER" variable is not set. Defaulting to a blank string.`），然后**照常拼出一个空用户、空库名的 URL 并把容器起起来**。compose 全程不报错。
- **证据**：`.env` 的键清单只有 `ADMIN_INVITE_CODE` / `ALLOWED_ORIGINS` / `DASHSCOPE_API_KEY` / `DB_PATH` / `DEEPSEEK_API_KEY` / `JWT_SECRET` / `STORAGE_BACKEND`；`docker compose config` 打三行 warning（`up` 时又各一次）；容器内 `${DATABASE_URL##*@}` 为 `postgres:5432/` —— **库名为空**。
- **后果与「为什么本机看不见」**：本机无感的唯一原因是 `STORAGE_BACKEND=sqlite`，它让 `DATABASE_URL` 成了惰性项。**兜住这件事的是那个偶然配置，不是设计** —— 换一台 `STORAGE_BACKEND` 指向 PG 的机器（或有人把 sqlite 那一行删掉），同一条命令会静默起一个连不上库的 app，且没有任何报错指向这里。
- **定性**：与本仓「失败被吞成正常运行」的既有形态同族（线程弃船 / 384 维度不符 / 截断响应当成功 / `finish_reason` 缺失 / `$contains` 恒不命中 / 静默截断）。**它不抛异常，只是拼出一个坏值**——坏值比异常难查，因为它长得像正常配置。
- **与纪律相悖**：「凭据一律 env 化」的判据是**无默认值、缺变量即硬失败并点名变量**。这里是缺变量 → 静默空串，恰好相反。
- **处置方向（记在条目里，不实现）**：compose 原生支持 `${VAR:?<msg>}` —— 缺变量时在**解析期就失败并打印 msg**（把变量名写进 msg 即为「点名」），而不是 `:-` 那样给默认值。**判据：要的不是「补一份 `.env.example` 让人记得填」** —— 那又是靠人记得，与 §四「守卫与被守对象之间隔着第二份手工维护的清单」同病灶；要的是「缺了就跑不起来，且报错自己说清缺哪个」。
- **顺带结论**：本次 rebuild 加 `--no-deps` 的必要性正由此而来 —— 三个变量解析为空后 compose 会判定 postgres 配置变更并 **recreate** 它（与「只重建 app、不碰 postgres」的指令冲突）。实测加了 `--no-deps` 后 postgres 容器 id / `StartedAt` 均未变。
- **修法（2026-09-15）**：`docker-compose.local.yml` 与 `docker-compose.prod.yml` **同批**改 —— postgres 三个 env 由 `${VAR}` 改为 `${VAR:?<点名文案>}`。**同批的理由**：两文件逐字同形、同一处静默面，只修 local 就是「修了一处、同类的留着」，而且是留在生产端；prod 那侧是**惰性**的（不部署不生效），但一旦部署，缺键会在解析期直接拦住，比静默起一个连不上库的 app 好。
- **只加一处守卫，不逐处重复**：app 的 `DATABASE_URL` 与本 service 的 healthcheck 都用同一批变量，但 compose 插值是**文件级**的 —— 缺任何一个，整个文件在解析期就失败，故不再在 `DATABASE_URL` / healthcheck 处各写一遍 `:?`。报错自带位置与变量名：`error while interpolating services.postgres.environment.POSTGRES_USER: required variable POSTGRES_USER is missing a value: 缺 POSTGRES_USER：填进 .env（模板见 .env.example）`。
- **`prod` 部署前置条件（用户裁定，记为硬要求）**：**部署前在两台各跑一次 `docker compose -f docker-compose.prod.yml config`**。prod 的 `.env` 本机核不到，这条是把「本机核不到」变成「部署前一定有人核过」。
- **变异验证（实测，两文件各一次 —— 不是可选步骤）**：把 `.env` 三个键临时注释掉 → `config` **exit=1** 且报错点名变量（原文见上）；恢复后 exit=0、stderr **0 行**（改前是 3 行 `not set. Defaulting to a blank string.`）。**恢复时逐字节核对**（sha256 一致）才算数。
- **不加静态锁（用户裁定）**：若有人把 `:?` 改回 `${}`，**下一次 `config` 立刻重新打 warning** —— 那已经是运行时反馈；再加一层扫 YAML 的静态锁是在运行时门之上叠静态门，**重复守卫**。
- **同族第二处（已单独立项，见缺陷 41）**：本 service 的 healthcheck 就在这三行下面几行处，且**不由 `:?` 修好** —— 变量补齐后 `pg_isready` 也仍不会因凭据错而失败。
- **prod `config` 的另一条 warning（如实记，不修）**：本机对 `docker-compose.prod.yml` 跑 `config` 会打 `The "PEER_NODE_IP" variable is not set` —— 那是**本机 `.env` 没有该键**的产物（prod 机器上有），不是新缺陷；且 `.env.example` 已写明「为空时本机 inter-node 白名单仅 127.0.0.1」，**空是有定义的状态**，故不改。

**33. 凭据以「命令行前缀」形态传递，明文沉积进 `.claude/settings.local.json` 的权限白名单** —— 状态：**已修**（2026-09-15，与 32 同批）
- **症状**：`.claude/settings.local.json` 的 `permissions` 里存着 `Bash(POSTGRES_USER=…` 这条前缀，**明文**。该文件被 `.gitignore:228` 的 `.claude/` 覆盖，`git ls-files` 未命中 —— **未入库**。
- **根因（不是「这一条白名单里有口令」）**：**凭据被当成命令行参数传递**。只要还用 `POSTGRES_USER=… POSTGRES_PASSWORD=… docker compose up` 这种形态，同一份口令就会同时落在 **shell history / `ps` 输出 / 权限白名单 / 任何记录命令的地方**。「为了方便自动积累」的白名单只是其中一处。**删掉白名单里那一条 = 治症状**：下次再跑同样的命令，它会被重新写回去。
- **「没入库」不等于「没风险」**：它明文躺在磁盘上，而且是个**自动积累**的文件，不是人有意选定的凭据存放点。将来解除某个子路径的 ignore、或打包发送配置 / 日志 / 环境快照，它会跟着出去。
- **处置方向（记在条目里，不实现）**：凭据进一个 **gitignored 的 env 文件**，由 compose 的 `env_file` 读取，命令行不再出现凭据（`docker compose -f … up -d` 即可）。
- **与 32 是同一件事的两面**：32 管「缺变量不许静默降级」，33 管「变量从文件来、不从命令行来」。只做 33（补 `.env`）而不做 32 → 缺变量仍静默、凭据仍可能走命令行；只做 32（改成 `:?` 硬失败）而不做 33 → 把「必须显式传入」变成「必须每次打在命令行上」，**反而扩大凭据的扩散面**。**两条同轮才有闭环，单独改任一条都是补丁。**
- 判据命令：`git grep -n 'POSTGRES_USER'`（列引用点）、`git check-ignore -v .claude/settings.local.json`（确认 ignore 来源）
- **修法（2026-09-15，与 32 同批）**：三键搬进 `.env`（`.gitignore:151` 覆盖，`git ls-files` 未命中），**搬运全程不经过人眼**——由一个只进内存的脚本从白名单条目里取出值写入，**不回显、不进 shell history**；随后删掉白名单里那一条（删前后都 `json.loads` 验过，1500 → 1499 行）。命令行形态随之消失，那条白名单不会再被自动写回 —— 这才是闭环。
- **搬运途中查出的实情（**这条推翻了原计划的前提**，故此条已修但代价须记）**：白名单里那个值**根本不是这个卷的口令**。实测 `127.0.0.1:5432` → `FATAL: password authentication failed for user "charsim"`；卷 `character-distill_pg_data` 的 `CreatedAt=2026-06-25`，而容器建于 2026-08-17（日志首行 `directory appears to contain a database; Skipping initialization`）—— 即**初始化发生在更早的一次运行里**，白名单那条命令当时只打在了已存在的卷上，`POSTGRES_PASSWORD` 从未生效。真值最后从**两个已退出容器的 `.Config.Env`**（`charsim-pg` / `postgres`）里找回并验证通过。**一般化：白名单记录的是「有人这么打过这条命令」，不是「这条命令成功过」**，二者被同一份文本掩盖 —— 已作为 §四「台账状态行不是事实」的同族第三案例记入。
- **残留（如实记，不动）**：同一份明文还躺在**两个已退出容器的 `.Config.Env`** 里（本机、未入库，`docker inspect` 可读，含 `charsim-pg` / `postgres`）。威胁模型与白名单那条相同；处置需要删容器，属用户资产，**本轮不碰**，只记。
- **未新增静态锁**：`grep -c POSTGRES_PASSWORD .claude/settings.local.json` = 0 是**本轮的一次性验证**，不是常设守卫 —— 该文件是自动积累的，将来别处再引入命令行凭据同样不会被拦；这一条的守卫是「命令行形态本身消失」，不是「有人盯着这个文件」。

**34. Reduce 全部 batch 返回空仍产出卡片，用户拿到残次品而界面显示「成功」 —— 「失败被吞成正常返回」的第十次显形** —— 状态：**已修（`de924cd`）**（2026-09-14 记账，同日修；复现 429 时发现）
- **症状**：Map 大面积失败 + Reduce 两个 batch 全空，流水线照常走到 `Card saved`，落了卡、返回成功；用户**没有任何信号**知道这张卡是残缺的。日志里有 30 条 `Map chunk N failed`、2 条 `Reduce batch N returned empty, skipped`，界面上只有「成功」。
- **后果链（本次实测，原文见会话记录）**：128 片里 30 片全灭（27 个 429 打满 5 次、3 个 `Request timed out`）→ 失败率 23% < 50% 门控 → `within tolerance, continuing` → Reduce batch 1 被 `max_tokens` 截断（`finish_reason='length'`）、batch 0 `Request timed out` → 两个 batch 都 `returned empty, skipped` → **仍然落卡**（`card_id=6fc336cfcb23`，`text_id=0b353450811b`）。
- **根因（不是「429」）**：**Reduce 阶段没有失败门控，Map 有。** 两条不对称，可直接对照：
  - Map 侧有门：`core/distiller.py:1618` `if failed / total_chunks > 0.5:` → 1620–1628 打 `aborting stream` 并 `yield {"error": …}` + `return`，**中止整条流水线**。
  - Reduce 侧无对称门：`core/distiller.py:1699-1707` 收集 `batch_results` 时，空的只 `print(f"[distiller] Reduce batch {i} returned empty, skipped")`，**不计数、不判定、不中止**。
  - **且空输入会走进一条「凭空归并」路径**（这是本条的实质，比「缺少门控」更具体）：`core/distiller.py:1711` `if len(batch_results) <= self.SAFE_SINGLE_REDUCE:`，而 `SAFE_SINGLE_REDUCE = 80`（`core/distiller.py:223`）—— **空列表必然满足**，于是调用 `_single_reduce_stream([], character_name)`，即**对零条分析再发一次归并请求**。`_reduce_user_prompt([], name)`（`core/distiller.py:344`）产出的是「以下是从多段文本中提取的关于「X」的独立分析，请整合为一份完整的角色档案：\n\n」后面**一条分析都没有** —— 模型只能凭空产出。
  - **唯一那道兜底门此时不成立**：`core/distiller.py:1726` `if not profile_draft.strip():` → 凭空产出的那次输出**非空**，于是放行。
  - **精度订正（2026-09-14 复核）**：走进凭空归并的**空输入是 `batch_results`（Reduce 阶段的输入），不是 `raw_analyses`**。`raw_analyses` 为空是**有门**的 —— `core/distiller.py:1634-1636` `if not raw_analyses: yield {"error": "未能从任何片段中提取到角色信息"}; return`。本次 98 片成功 → `raw_analyses` 非空、>80 → 分两批 → **两批都失败**才把 `batch_results` 清空。所以这道口的形状是「**第一层输入有门、第二层输入无门**」，不是「完全没有门」。据此，修法也应当是**在 `batch_results` 这一层加输入门**（即 `:1711` 之前判空），而不是在 `:1726` 收紧输出门。
  - **非 stream 路径同病（同一形态的第二处）**：`distill_incremental`（`core/distiller.py:1232`，本仓只有 `/run` 走）里的 `_do_reduce` 递归同样会吃空列表 —— `core/distiller.py:1218` 过滤掉空 batch（`if r[1].strip()`）后 `merged` 可为 `[]`，`:1228` 直接 `self._do_reduce([], character_name)` → `:1207` `len([]) <= 80` 成立 → `_single_reduce([])`。`distill_incremental` 的 `:1366` 输出门（`if not profile_draft.strip()`）与 stream 版一样拦不住。**故本条不是 stream 独有，是 Reduce 这一级的输入未校验**；两处同改才算修完（§四「修复一处越权 ≠ 这一类修完」）。
- **与 429 的关系**：429 只是**本次的触发条件，不是根因**。429 是外部约束（DeepSeek 按余额给并发额度，当前上限 15，而 `map_concurrency=30` 一开跑就超一倍），充值即缓解；**「Reduce 全空仍落卡」是设计缺陷，下次以任何原因（超时 / 截断 / 网络 / 余额）导致 Reduce 失败，都会静默产出垃圾卡**。与 thinking 方言、与本轮任何改动均无关。
- **定性**：与「线程弃船 / 384 维度不符 / 截断响应当成功 / `finish_reason` 缺失 / `$contains` 恒不命中 / `admin_tasks` 静默截断 / store 层 `except: return <空值>`（缺陷 24 记为第八次）/ 缺陷 32 compose 静默空串（第九次）」同族 —— **第十次**。前九次都还有「值不对劲」可查，这次是**归并等于没跑，却输出了一张卡**。
- **可辨性缺口（与缺陷 2 同源）**：`finish_reason=None` 那条 WARN（`[llm] WARNING: chat_stream: 未识别的 finish_reason=None`，本次两条）本身就是「可能截断但被按正常处理」；它出现的位置恰在这次凭空归并与 format 两次调用上，**信号存在但没人把它接到「这张卡可信吗」上**。
- **处置方向（记在条目里，不实现）**：Reduce 全空应**显式失败并告诉用户原因**，而不是落一张空壳卡。参考口径 = Map 的失败率门控（`core/distiller.py:1618`）：空 `batch_results` 是 100% 失败，比 50% 阈值严重得多，**连 `_single_reduce_stream([])` 都不该发**。范围待裁：是「Reduce 全空即中止」还是「按 batch 失败率定阈值」，以及「已落卡后如何告知/回滚」。
- **判据命令**：`git grep -n 'returned empty, skipped\|SAFE_SINGLE_REDUCE\|total_chunks > 0.5' core/distiller.py`
- **一般化已入 §四**：「兜底门判『输出空不空』而上游能对非法输入产出非空结果 ⇒ 门结构性失效；判据要落在输入是否合法」。修 34 时按那条口径走（守卫前移到 `batch_results` 是否为空），不要只把 `:1726` 那道门收紧
- **修法落点（`de924cd`）**：守卫加在 **`core/distiller.py` 的 `_run_reduce_concurrent` 末尾**（`asyncio.gather` 结果之后）：`if not any(r[1].strip() for r in results): raise DistillError(...)`，上屏文案「蒸馏失败：归并阶段未能产出有效内容，请稍后重试」，运维口径带 `Reduce batch 全空：N/N 失败`。
  - **为什么不在两个调用点各加一行**（原计划是「两处同改」）：`_run_reduce_concurrent` 的调用点**只有两个**（`_do_reduce` 的 `:1217`、stream 的 `:1668`），它就是所有 reduce 批次结果的**唯一汇合点**；且非 stream 那一路的洞在 **`_do_reduce` 的递归**（`:1228` 拿 `merged` 再进 `:1207` 的空列表分支），守在 `:1228` 只堵住一次递归、堵不住语义。守在这里，两条路径**一处覆盖**，且不改变 `_do_reduce` 的形状。
  - **错误传播是现成的、两条都通**：stream 侧 `_reduce_thread` 的 `except` → `rq.put(("error", exc))` → 消费端 `:1692` `yield {"error": user_facing_error(exc)}`；非 stream 侧直接往上抛。**不需要在调用点加 try/except。**
  - **变异验证**（`tests/test_distiller_routing.py::TestReduceAllEmptyBails`，两条）：删掉守卫 → 两条双双变红（sync 落到 format 阶段、stream 不再产 error 帧），日志复现原形状 `Reduce batch 0/1 returned empty, skipped` → 第三次 reduce 调用（凭空归并）→ `distill_format` → 落卡。**用例的假件必须模拟「零条分析也产出非空」**，否则 `:1726` 那道门在用例里反而拦住了，测不到真缺口。
  - **同病第二处已一并覆盖**：`distill_incremental`（`/run` 走的那条）的 `_do_reduce` 递归同样吃空列表，见上方「非 stream 路径同病」条 —— 守卫落在汇合点后，这一处无需另改（递归传入的 `merged` 已被上游拦住）。

**35. `/start` 后台线程路径的 usage 记账全程空转 —— 缺陷 22 收口后的覆盖缺口** —— 状态：**已修**（`dc7b09f` + `34bf075`，2026-09-21；2026-09-14 首记）
> 下面 6 条是**修复前的现场记录**，行号与默认值都是当时的形态，保留原文以便对照。

- **事实**：本次运行 5 个 action 各打一条 `[Distiller] usage not recorded: storage/user_id missing (user=, action=…)` —— `distill_identify` / `distill_map` / `distill_reduce` / `distill_format` / `distill_autotag`。`user=` 为空即证据（`core/utils.py:64` 的 print 把 `user_id` 直接填进去）。
- **根因**：`/start`（`web/routers/distill.py:734`）→ `_distill_start_impl`（763）→ `_run_distill_task`（317）**后台线程**。该线程**收到了 `user_id` 参数，但只用它放并发槽**（`_release_user_slot`），**从不把 `distiller._storage` / `distiller._user_id` 接上去**。`Distiller.__init__` 的默认是 `self._user_id = ""`（`core/distiller.py:239`）。
- **对照（同一仓库里的正确写法）**：`/run_stream`（`web/routers/distill.py:1044`）在 1075–1076 显式写了 `distiller._storage = storage` / `distiller._user_id = user_id`。全仓 `distiller._user_id = …` / `distiller._storage = …` **只此一处**（`git grep -n 'distiller\._user_id\|distiller\._storage'`）→ **三道蒸馏入口里只有 `/run_stream` 一条接了**：`/start`（后台线程，`_run_distill_task`）不接；`/run`（`web/routers/distill.py:686` → `TextManager.get_or_distill`，`core/text_manager.py:407`）也不接 —— 它的 distiller 由 `web/deps.py:254` 现造（`Distiller(llm)`），同样落 `_user_id=""` / `_storage=None` 默认值。`Distiller.__init__` 的这两个默认值见 `core/distiller.py:238-239`，全仓 5 处 `Distiller(...)` 构造点（`web/app.py:48` / `web/deps.py:185,191,254,297`）无一传身份。
- **机制**：`core/utils.py:63` `if not storage or not user_id:` → print + `return`。整条链的记账出口是接上了的（缺陷 22 刚把 36 个 LLM 调用点全部接进该出口），**但这条路上入口的两个实参都是空的**，于是出口空转。
- **与缺陷 22 的关系（关键）**：22 修的是「**出口从未写**」；本条是「**出口写了、这条路从未注入**」。二者**不重叠**，但**机制锁看不见本条** —— `tests/test_usage_accounting_lock.py` 的判据是**静态调用链**（「存在调用 LLM 但不流向记账出口的调用点即红」），而本条的调用**在代码路径上确实流向出口**，只是运行期早退。**判据是调用图，不是实参** —— 这是该锁的又一类盲区（与缺陷 25「签名的代理代替 SQL 事实」同谱系：静态形态对，运行期事实错）。
- **后果**：除 `/run_stream` 外的蒸馏入口（`/start`、`/run`）上，distiller 内部那几笔 token 用量**一条都不入库**；而 `/start` 正是前端主要的蒸馏入口 —— 「蒸馏成本统计」在这些路上系统性偏低（偏低的统计比没有更危险，同缺陷 22 的措辞）。本次观测到的只是 `/start` 这一条（5 条 no-op 打点即其证据）；`/run` 的同一形态由静态读代码得出，**未实跑验证**。
- ~~**处置方向（记在条目里，不实现）**：① 让 `_run_distill_task` 注入 `storage` / `user_id`（照 `/run_stream` 的写法）；② 更根本的是**补一条运行期判据**……~~ **已按下文实现** —— 但**没有**照 ① 补注入：给 `/start` 补一次注入只是把「谁忘了写」从一条路挪到另一条，缺陷形态原样留着。改成身份**不再靠往实例上写属性传**。

**修法（`dc7b09f`，2026-09-21）** —— 身份收敛成**一个** contextvar：
- 身份 = `LLM_CALLER`（`core/request_context.py`）。**写口一处**：`AuthMiddleware` 在它唯一的 `call_next` 出口前设一次；**读口一处**：记账出口用 `current_user_id()` 读它。传播是白拿的 —— `core/concurrency` 的 `ctx_thread` / `ctx_submit` 本来就 `copy_context()`，派生线程自动带上。
- `storage` 是**依赖不是身份**：走构造注入，由**唯一生产装配出口** `web/deps.get_distiller` 注入（`Distiller(llm, storage=get_storage())`）。不往身份里夹带，也不为它在 `core/` 新建第二个模块。
- `/run_stream` 那两行逐路由写属性**删掉**：六条蒸馏路由共用同一个机制，路由侧一行都不用写。

**返工（`34bf075`）** 首版错在哪：在 `core/` 另建了一个只装 user_id 的 ContextVar，中间件于是同一个出口**连写两份同一个值** —— 正是 `web/llm_gate.py` docstring 禁的那件事（`web/` 没有 `__init__.py`，同名两个 ContextVar 各看各的，中间件设的值另一边读不到）。正确解法是把身份上下文**下沉到 core**：`git mv web/request_context.py core/request_context.py`（不留兼容转发），`current_user_id()` 读的就是门读的那一个 `LLM_CALLER`。

**回归锁（`tests/test_usage_identity_context.py`，10 条）** —— 分五层，各锁一维：
- 派生面传播 ×2：身份进 `ctx_thread` / `ctx_submit` 后读得到（写后**还原** —— 不还原会污染同进程后面「无上下文即 fail-closed」的门用例，实测踩过）；
- **写口唯一**：AST 现算 `LLM_CALLER.set` 的调用点只有两处（`AuthMiddleware.dispatch` 与 `system_llm_context`），判据是**接收者名 + 方法名** —— 换包装、换函数名、在路由里直接伪造身份都逃不掉；
- **身份 ContextVar 唯一**：AST 现算 `ContextVar(...)` 的定义只有 `LLM_CALLER` 与 `_EMBED_DEADLINE` 两处。白名单按 **`file:line 名字` 整串**钉死，**不按名字** —— 按名字放行挡不住「在另一个模块里再写一遍同名 `LLM_CALLER`」，而那正是要挡的形态；
- 形态 ×2：`Distiller` 无 `self._user_id`；`web/` 里没有对 distiller 实例写身份的赋值；
- 落库：真 app + 真中间件 + 真 `/start` 后台线程，`usage_stats` 恰 5 行、`user_id` 全是请求身份、action 面 == 5 个蒸馏动作。
- **负控 ×2**：合成第三处写口 / 合成第三个 `ContextVar(` 定义，扫描器都必须报得出来 —— 否则那两条锁是在假绿。

**变异（7 种，2026-09-21 现跑，各按「修复前的代码长什么样」改）**：A 身份退回实例属性 → 形态 + 落库红；B 删中间件那一次写 → 写口唯一 + 中间件 + 落库红；C 写口挪到 `call_next` 之后 → 中间件 + 落库红；D 多出第三处写口 → 写口唯一红；E `get_distiller` 不注 storage → 落库红；F `ctx_thread` 退回裸线程 → 传播 + 落库红；**G 再建一份携带 user_id 的 ContextVar → 只打红「ContextVar 唯一」那一条**。基线绿、复原绿，每个变异只红它该红的。

**读数（现跑，探针 `e2e/scratch/probe_start_usage_leak.py`，真 Distiller + 真后台线程）**：

| 入口 | 修复前 发出 / 落库 | 修复后 发出 / 落库 |
|---|---|---|
| `/start` | 5 / **0** | 5 / **5** |
| `/run_stream` | 4 / 4 | 4 / 4 |
| `/run` | 5 / **0** | 5 / **5** |

修复后「守卫拦下（日志）」= 0 —— 那行 `usage not recorded` 不再出现。

**全量**：SQLite **1392 passed / 69 skipped / 1 xfailed**（返工前后逐位相同，差量 0）；PG（一次性容器，`REQUIRE_PG_TESTS=1`）**1460 passed / 1 skipped / 1 xfailed、0 failed**。PG 是**返工前**的读数 —— 返工只动 import 路径与上下文归属、不碰存储层，但**未重跑**，属推断非实测。

**未覆盖（登记，不是遗漏）**：本条只修**蒸馏族**。同一形态在**聊天族**照旧（见 83 / 84），Gradio 路径同形态但**生产不可达**（见 85）。

**36. `/api/distill/start` 不读 `characters_json` 缓存（只有 `/identify` 读）** —— 状态：**已修**（`0605bdf` `00e0710` `d5bdbc3` `592cc28` `5088c29` `ef43b38`，2026-09-21；2026-09-14 首记，原状态「记账（不修，待裁范围）」）
- **事实**：`/identify`（`web/routers/distill.py:658`）在属主校验后读 `get_characters_owned`（678），命中即返回、**不发 LLM**；`/start`（734）这条全流水线**不读该缓存** —— 角色识别由流水线内的 `distiller.identify_characters`（`core/distiller.py:635`）直接跑。
- **它自己的那层缓存不是 `characters_json`**：`identify_characters` 用的是**进程内 TTL memo**（`core/distiller.py:28-32`，`IDENTIFY_CACHE_TTL_SECONDS = 600`），键 = `sha256(前 10000 字) + model`。寿命是**进程**，不是库。
- **实测**：本次 `/start` 之前该文本的 `characters_json` 已存 8 个角色（早先 `/identify` 的产物），流水线仍在 12:30:07 真发了 identify 调用（`action=distill_identify`）；日志里 `[distill] identify cache hit` **零命中** —— 因为本次 rebuild 刚重启过容器，进程内 memo 是冷的。
- **意义（解释了上一轮的一个推理为什么只在一条路上成立）**：上一轮判定「再点一次不可能复现，因为 `characters_json` 已缓存 8 个角色、命中即返回」—— 该推理**只在 `/identify` 上成立**。`/start` 不等价。
- **收口（2026-09-21）**：按**产品语义**裁，不是补一行 cache 读取 —— 名单是**作品的属性**，所以「读缓存 → 识别 → 落库」收敛成唯一入口 `core/character_roster.py`（S4 `5088c29`），所有**有 `text_id`** 的调用方（`/identify` `/run` `/run_stream` `/reindex`、`TextManager.get_or_distill` / `_build_all_characters`、`/start` 的 bg 线程）一律走它（S5 `ef43b38`；原先并列的 `TextManager.distill_all` 已于 2026-09-22 删除，见 86）；`refresh=True` 保留「用户显式点重新识别」这条语义，不再靠「调另一条路由」表达重跑（**2026-09-22 订正**：该参数无生产调用方、前端无入口，且真调用也会被进程内 memo 吃掉 —— 已删除，见 89）。legacy 的 `/api/identify`、`/api/distill` 与 `web/app.py:85`（Gradio）保持现场识别 —— 只有原文、没有 `text_id`，读不到也落不了这份缓存。
- **同轮一起落地的两处前提**：① 原 `identify_characters` 取 `text[:10000]`，红楼梦只覆盖头两章，残缺名单被当全书名单落库 —— 改为按 `_chunk_size` 全片识别再合并（S2 `00e0710`；合并输出上限另提 `d5bdbc3`）。② `characters_json` 没有版本，口径一改旧名单不会失效 —— 加 `texts.characters_version`（SQLite 087 / PG 020，S3 `592cc28`），版本号的唯一定义是 `Distiller.IDENTIFY_VERSION`，读回时版本不符即当无缓存。
- **判据命令**：`git grep -n 'get_characters_owned\|save_characters' -- web/ core/`（应**只剩** `core/character_roster.py`）、`git grep -n 'identify_characters' -- web/ core/`（应只剩 `core/distiller.py` 的定义、`core/character_roster.py` 的调用、`_do_identify` / `_resolve_character_name` 两条 legacy 纯文本路径、`web/app.py:85`）
- **同轮另记的观察（各自单列，均未修）**：见 86–90。

**37. （前提证伪）`get_distiller` 的单例被 `/run_stream` 原地改写 —— 该形态不可达，usage 不会记到别人名下** —— 状态：**证伪，不成立**（2026-09-14 当天记下、当天核掉）
- **原假设**：`get_distiller(llm=None)` 返回模块级单例 `_distiller`（`web/deps.py:182-192`），而 `/run_stream` 原地改写其 `_storage` / `_user_id`（`web/routers/distill.py:1075-1076`）→ 共享可变状态跨请求污染 → 之后某个没有 per-user key 的请求取回被污染的单例，usage 记到前一个用户名下。当时标注为「静态读出、未实跑验证」。
- **证伪：两个前提互斥，不可能同时成立。**
  - `/run_stream` 传的是 `get_distiller(llm=per_user_llm)`（`web/routers/distill.py:1057`），而 `per_user_llm = await get_user_llm(...)`，`get_user_llm` 在用户没配 key 时**不是返回 None，而是回落到 `get_llm()`**（`web/deps.py:118-119`：`# Fallback: global config / admin key` → `return get_llm()`）。故 **`per_user_llm is None` ⟺ `get_llm() is None`**。**（2026-09-19 订正：这条等式的定义域是「经 `get_user_llm` 走出来的那个变量」，不是「全仓任何一处 LLM 是否为 None」—— 见本条末段。）**
  - `get_distiller(None)` 只在 `_distiller is None` 时才去问 `get_llm()`，而 `get_llm()` 为 None 就直接 `return None`（`web/deps.py:187-190`）→ **单例从未被创建**。反向：单例一旦被创建（`deps.py:191`）就要求 `get_llm()` 非 None；且 `_llm` 是非 None 缓存，**全仓没有路径把它设回 None**（`get_llm` 只在成功时赋值；`reset_llm_and_dependents` 的 `_llm = LLMAdapter()` 抛异常时不赋值）。
  - 合起来：**单例存在 ⟹ `get_llm()` 非 None ⟹ `per_user_llm` 非 None ⟹ `get_distiller(llm=…)` 现造每请求新实例**（`web/deps.py:184-185`）→ 1075-1076 改写的是**请求级副本**。反向：`per_user_llm` 为 None ⟹ 单例不存在 ⟹ `get_distiller(None)` 返 None ⟹ **503 在 `distill.py:802` 先抛**，根本走不到 1075。
  - 另核写点：全仓 `distiller._user_id = …` / `distiller._storage = …` **只有 1075-1076 一处**（判据命令见下），不存在第二个把身份写进单例的地方。
- **连带证伪**：「503 门只判 `distiller is None`、拦不住被污染的单例」也不成立 —— 那道门拦的正是「拿不到 LLM」，而单例被污染的前提恰恰是「拿得到 LLM」，两者不共存。
- **为什么保留本条而不删**：它的**假设形态**（长生命周期对象持有请求级状态 → 跨请求错归）在本仓仍值得警惕，只是当前**没有**这条路径。**复活判据**：① 上条 grep 的写点数从 1 变 2；② `distill.py:1057` 的传参不再是 `llm=per_user_llm`（例如有人改成 `get_distiller()` 取单例）；③ 出现新的、能对单例写请求级字段的调用点。任一条成立，本条立即复活。
- **教训（落笔时没走完静态链）**：「静态读出」这个标注救不了**没追到源头**的静态链 —— 我只读到 `get_distiller` 自己的两个分支就落了笔，没跟到 `get_user_llm` 的 fallback，于是把**一对互斥条件**当成了可同时成立的组合。核一条静态结论，必须把**每个分支的入参从哪来**追到源头（此处就是那句 `return get_llm()`）；只读被判对象自己，等于只读了半条链。与 §四「别人给的 premise 与代码不符时，报更正」同族 —— 那次是别人的 premise 错，这次是**我自己顺着一个看起来自洽的 premise 往下推**。
- **判据命令**：`git grep -n 'distiller\._user_id\|distiller\._storage'`（写点数应为 1）、`git grep -n 'global _distiller\|_distiller = Distiller\|return get_llm()' web/deps.py`
- **连带变化（2026-09-18，收敛装配时把单例删了 —— 重跑的人必读）**：第二条判据命令的命中**行数从 4 掉到 1**。被删掉的三行**正是本条的对象本身**（`get_distiller` 的单例分支 + `reset_llm_and_dependents` 里造单例那行），故改前/改后要逐行对：
  | 改前（`9864608`） | 改后（`bb62311`） |
  |---|---|
  | `web/deps.py:184:    global _distiller` | **（已删）** |
  | `web/deps.py:189:        _distiller = Distiller(fallback)` | **（已删）** |
  | `web/deps.py:304:    _distiller = Distiller(_llm)` | **（已删）** |
  | `web/deps.py:116:    return get_llm()` | `web/deps.py:115:    return get_llm()`（行号 −1） |
  删掉的那段不只是「空转」——**那段 `llm is None` 路径就是本条证伪掉的那条路**（前提互斥使其不可达），故删除是收口不是新增风险，理由与证据强度见下方 C 条的 B′ 段。唯一存活的那行属于 `get_user_llm` 的 admin-key 回落（`deps.py:115`），**与本条无关**：它从前被这条命令顺带匹配到，是判据的噪声项。**故重跑的人不要对「4 行」**：今天应为 **1**，且那 1 行不是本条的对象；本条的真正对象（`global _distiller` / `_distiller = Distiller`）命中数应为 **0**，一旦不为 0 就是单例回来了。第一条判据命令（写点数）不受影响：仍是一处写点（`web/routers/distill.py:1079-1080`，两行同一处）。
- **订正（2026-09-19，C0–C5 门重构暴露）—— 本条的证伪结论不变，但上面那句等式的作用域要写死**：`per_user_llm is None ⟺ get_llm() is None` **只在「经 `get_user_llm` 解析的调用点」上成立**。把解析出口整条拆掉之后，两条**不走该出口**的路径立刻显形并有实测读数：`9864608`（装配收敛，2026-09-17）之后后台蒸馏线程首次报「请先配置 API Key」；`bb62311`（删单例分支，2026-09-18）之后 `/start` 在「用户无 key、全局可用」下由 200 变 503（两个 revision 各跑一次同一条件即可复现，读数即 spec 的 F1/F2）。当时据此判定「删除零风险」，错在**把某条路径上的等式当成了全仓不变量**。
  - **性质：§四「追入参到源头」的再次显形。** 上一轮（本条自身）是「只读到被判对象的两个分支、没追入参」；这一轮是「追到了入参，但没问别的调用点是否也走这条出口」。同一条纪律的第二种形态：**追到源头还不够，要问这条等式在别的调用点是否也成立** —— 不同调用点各有一套解析逻辑时（本案正是 F4「geo 策略三份分裂」），等式只在其中一份里成立。
  - **收口**：调用点全部收敛到唯一解析出口 `get_user_llm`（C4，`06efc80`），等式因此重新成为全仓不变量；后台线程改为由请求线程把**已解析的** `llm` 传进来（`is` 判等钉住，L2），并在入口断言 `llm is not None`。
  - **复活判据 +1**：若再出现第二个解析出口（`git grep -n 'get_llm()' web/ core/` 在解析层之外命中新的调用点），本条的作用域限制立刻重新生效。

**38. `/run` 把 `DistillError` 的运维口径 `str(exc)` 直接当 400 响应体上屏** —— 状态：**已修**（commit `8aec1a4`，2026-09-14；修 34 时顺带发现）
- **症状**：走 `/run` 这条蒸馏路径时，任何 `DistillError` 都会把「上屏口径｜运维口径」整串返回给用户。例：Map 失败率门控抛的那条会以 400 返回 `蒸馏失败：部分片段处理失败，请重试｜3/4 个分片失败；最后错误：connection timeout` —— 分片计数与上游原始错误**都是内部标识**，正是缺陷 17 明令不上屏的东西。
- **根因**：`web/routers/distill.py` 的 `/run` 异常处理写成 `except ValueError as exc: raise HTTPException(400, str(exc))`，而 `DistillError` **继承 `ValueError`**（`core/distiller.py`，为了不改路径上既有的 `except ValueError` 语义），且其 `str()` 被**刻意**定义成运维口径（`f"{user_message}｜{ops_detail}"`）。两条设计各自都对，撞在一起就成了泄漏。同一条路由里 Map 门控（`core/distiller.py:1347`、`:1351`）抛的 `DistillError` **早就在走这条路**，本条不是 34 的修复引入的 —— 34 的修复只是又加了一个走同一形状的入口。
- **为什么流式两条链没这个问题**：`/start` 的 `_run_distill_task` 与 `/run_stream` 的 error 帧都经 `user_facing_error(exc)`（`adapters/llm_adapter.py:374-376` 取 `exc.user_message`）**唯一出口**收敛；只有 `/run` 这条没走那个出口。
- **修法（不是「每个路由各调一次 `user_facing_error`」，是「一处注册处理器」）**：路由层只 `raise` 领域异常，**不碰文案、不碰状态码** —— 这两样本就不是它的职责。集中在 `web/server.py::register_domain_error_handlers` 定义「异常类型 → 码」：`_DOMAIN_ERROR_STATUS = {DistillError: 400}`，文案一律调 `adapters.llm_adapter.user_facing_error`，**不新建第二张消息表**（表里只有码）。这样「新增一种异常 = 表里加一行」，零路由改动。
  - **表搬家不是再造**：`web/routers/chat.py` 原先自己那张 `finish_reason → 状态码` 表（content_filter→400 / length→502 / 资源不足→503 / 未登记兜 502）整段搬到 `_INCOMPLETE_STATUS`；`chat._do_chat` 改为「`llm_error_payload(exc) is not None` 就 re-raise」，让异常走到出口再配码。
  - **`register_domain_error_handlers(target_app)` 抽成函数**：测试能在**自己的最小 app** 上装同一份注册 —— 测试与生产共用这一处，才拦得住「注册表被改空而测试没动」的变异（在测试里另抄一份注册表，注册表被改空测试照样绿）。
  - **不注册 `StoreError`**：`storage/base.py` 的类注释已把「记 traceback 并回 500」指派给下面的全局 `Exception` 处理器。在此注册会**丢掉 traceback** —— 那是降级，不是统一。
  - **边界锁的绕行（方案与原始设想不同，此处必须写明）**：`tests/test_chat_stream_error.py::test_no_exception_class_leaks_into_core_web_storage` 是**文本级**锁（`core/` `web/` `storage/` 下任何 `.py` 都不得出现 `IncompleteResponseError` 字符串，注释与 docstring 也拦）⇒ **`web/server.py` 根本不能 import 该类**，「注册处写 `isinstance` 不违反」这个设想不成立。解法：由 `adapters/llm_adapter.py` 发布 `llm_error_types()` 返回类型元组（adapters 不在扫描范围），`web/` 只循环注册**不透明的类对象**、不写类名。新增一种 LLM 失败 = 元组里加一个类，装配层零改动。
  - **Q4 判据（三种响应形状，接口隔离）**：非流式（响应头未发，handler 能配码）／真 SSE（异常发生时 **HTTP 200 + event-stream 已在线**，处理器产出的是**第二个响应**、Starlette 不会再发 —— **管不到，不是不让管**）／后台任务（连响应都没有，只能写任务状态行）。故**统一的是「文案出口」，码归各形状自己**；把 SSE / 任务硬塞进那张 `(status, text)` 表 = 给不消费 `status` 的调用方发它用不上的字段。那两条继续用 `user_facing_error` 取文案，共用同一份口径链。
  - **一期不动的那 53 处**：见 §四「加了全局处理器 ≠ 所有路径都走它」那条（会先于 handler 拦下 / 30 处文案逐字重复 / 两句通用文案应择一）。
- **范围边界（三类不迁，**代码里都留了注释**说明为什么不迁 —— 否则下一个人会「顺手统一」把它们一起收进去）**：
  - **A 类 5 处**（`web/routers/text.py` ×2、`web/routers/history.py` ×1、`web/routers/distill.py` ×2 的 `except ValueError → HTTPException(400, str(exc))`）：这些 raise 的**实参是本仓为人写的用户输入/属主校验文案**（如「文件编码无法识别，请另存为 UTF-8 后重新上传」），本就是上屏口径。它没有 `user_message`，走 `user_facing_error` 会落到 `_GENERIC_USER_ERROR`（「服务暂时不可用」）= **删信息**。**「用户输入校验失败」和「领域异常」是两类东西，不该为统一而统一。**
    - **判的对象是「这条 `raise` 的实参构成」，不是「异常类型是不是裸 `ValueError`」—— 表述已于 2026-09-15 窄化（缺陷 39）**。原表述按类型判，而缺陷 39 的 **DOCX 双包**证伪了它：`core/text_manager.py` 的 `_extract_docx` 里**同一个 `except Exception` 既接住本仓自己写的 `ValueError("DOCX 文件无有效文本内容")`、又接住 python-docx 的异常**，包完上屏成了 `"DOCX 解析失败: DOCX 文件无有效文本内容"`。同一个 `except ValueError` 后面站着的既有 A 类（人话）、也有 C 类（库原文）⇒ **按异常类型判必然判错**。判据本身（*这段文字是为谁写的*）不变，判定对象换成实参构成。
    - **影响面 = 0**：接收点行为一字未改（继续 `str(exc)` → 400），变的只有 `TextManager` **抛出点**的实参构成（改走 `core/text_failure.py` 的表）。
  - **B 类 8 处**（`web/routers/inter_node.py` 全部 `except Exception → HTTPException(500, f"...: {exc}")`）：本路由的「用户」是**对端部署的运维**，异常原文是跨节点排障的唯一线索。收到统一出口后对面只剩「操作失败，请稍后重试」，是把可排障变成不可排障。一般化：**上屏文案该不该收，看有没有正当消费者，不看它像不像泄漏。**
  - **C 类 8 处是真泄漏，本期已收**：`voice.py` ×2（ffmpeg `stderr` 含服务器路径）、`market.py` ×2（pydantic 字段级报错 / 上游原文）、`distill.py` 卡片校验 ×1（pydantic 报错）、`server.py` ×1（`Update config failed` 带 `{exc}`）——原文本改只进日志，上屏换为人话。
    - **补记（缺陷 39，2026-09-15）**：`voice.py` 的 ffmpeg `stderr` 形态**实为 3 处，本期只收了 2 处** —— `:111`/`:382` 收了，`:460`（ASR 转码）漏了。它**不在任何名单上**，是缺陷 39 的**判据（「这段文字是为谁写的」）扫出来的**。已在缺陷 39 批次一并收口。教训落在缺陷 39 条目里：按名单修会漏掉「同形态的第三个」。
- **回归锁**：`tests/test_domain_exception_exit.py`（A 注册面 / B 非流式 `/run` / **C 反向验收** / D 另两种形状）。**变异验证**：把 `DistillError` 从 `_DOMAIN_ERROR_STATUS` 删掉 → `test_b1_run_distill_error_is_400_...` 红在 **500 ≠ 400**（`raise_server_exceptions=False` 让「没被接住」表现为 500 而非炸成 error）。
- **反向验收（证明没顺手把 A 类一起迁走）**：`tests/test_security_authz.py::TestErrorSanitization::test_10_value_error_400` 仍绿 —— 它断言 `/export` 的 400 detail 里 `"xlsx" in detail`，A 类被迁则用户需要的信息消失、该条变红。
- **判据命令**：`git grep -n 'except ValueError as exc' web/routers/distill.py`、`git grep -n '_DOMAIN_ERROR_STATUS\|llm_error_types' web/server.py`

**39. 文本解析层把「异常诊断」和「用户文案」揉在一起** —— 状态：**已修**（2026-09-15，缺陷 38 收口时顺带普查）
- **症状**：`core/text_manager.py` 三处把库的异常原文拼进**用户可见**的 `ValueError` —— `_extract_pdf` 的 `pymupdf.open` / `pymupdf4llm.to_markdown`、`_extract_docx` 的 `Document`。实测（造件现跑）：损坏 PDF 上屏 `PDF 文件无法打开（可能已损坏或加密）：Failed to open file '\\tmp\\...'` —— **服务器路径直接给了用户**。
- **根因（不是「顺手多拼了个 `{str(e)}`」，是分层坏了）**：`text_manager.py` 既要知道**怎么解析文件**，又要决定**用户看到什么话** —— 后者不是它的职责。两者揉在一起时，一个 `except` 会同时接住「我们写的用户文案」和「库的异常原文」。
- **结构性证据 —— DOCX 双包**（决定性，同一缺陷的 A 类边界也因此重划）：
  ```python
  try:
      doc = Document(file_path)
      paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
      if not paragraphs:
          raise ValueError("DOCX 文件无有效文本内容")   # ← 本仓人话
      return "\n\n".join(paragraphs)
  except Exception as e:
      raise ValueError(f"DOCX 解析失败: {str(e)}") from e   # ← 把上面那句又包一层
  ```
  实测上屏 = `"DOCX 解析失败: DOCX 文件无有效文本内容"` —— **一个 `except` 同时抓「我们的语义」和「库的噪声」**，这就是「揉在一起」的字面证据。
- **判据（本条与缺陷 38 的 A/C/B 分界同用这一条，2026-09-14 用户要求升格为判据本身）**：**问「这段文字是为谁写的」，不问「它像不像泄漏」。**
  - 为人写的（本仓撰写的用户指令，如 A 类各处的 `str(exc)`）→ 上屏**是信息**，收走就是**删信息**。
  - 为机器 / 排障写的（第三方库的异常原文、pydantic 字段级报错、ffmpeg `stderr`、内部计数与上游原始码）→ 上屏**是泄漏**，原文只进日志。
  - 判据可判定：取那句文案，问「作者写下它时，读者是用户还是开发者」。答不上来即说明文案本身没想清楚，这本身就是缺陷。
- **全量普查（判据驱动，不是照名单改）**：两步 —— ① AST 全仓找「`except ... as E` 的绑定名被引用、且**不在 print / 日志调用内**」的点，**267 处**（排除 vendored `services/gptsovits`、`tests/`、`e2e/scratch`）—— **这个数是「记录」且已不可复算**：产出它的脚本当年落在 gitignore 覆盖的 `e2e/scratch/`，**从未入库**（`git log --all` 全空），`2026-09-17` 按本条自己的措辞重建四种读法都对不上（修前树 `8a2c065` 得 532 / 85 / 256，今天得 530 / 82 / 257），故**只能原样冻结、不得改写成任一重建值**。**结论不依赖它**：真泄漏 4 处、单独立项 2 处、保留 14 处、安全的其余各处，各自有后续 commit 与锁独立证实；不可复算的只是这个入口计数。**为何「不可复算」也算一条结论、处置与由此立的规矩，见 §四「引用一个数字之前，先问它是核过的还是抄来的」。** **②** 逐点判「这串最终会不会到**终端用户**眼前」，到得了的机制只有四种：HTTP 响应体 / SSE 错误帧 / 后台任务状态行 / admin 可读记录。结果：
  - **真泄漏、活代码 4 处**：`text_manager.py` ×3（本批收）、**`web/routers/voice.py` 的 ASR 转码 ×1**（本批收）。
    - `voice.py` 那处**不在任何名单上** —— 它**不是异常对象，是子进程 stderr**，「异常对象被格式化」类的扫法扫不到。同文件同形态的另两处（`:111`/`:382`）已由缺陷 38 收口，**`:460` 是判据找出来的第三个**。漏它就是「改了两处、第三处记账」。
  - **同判据、但决定面不同 → 单独立项，不塞进本批（2 处）**：`web/routers/group.py` 的 SSE 错误帧、`web/routers/text.py` 的后台任务状态行，都直接 `str(exc)`（可能是 `StoreError` → `storage operation '<op>' failed: <驱动原文>`）。**为什么不顺手收**：它们的「正确形态」`web/routers/chat.py::_stream_error_payload` 本身用 `user_facing_error(exc, preserve_unknown=True)` —— **故意把未登记异常的 `str()` 原样透出**（契约锁 `tests/test_chat_stream_error.py::test_other_errors_keep_original_shape`）。改 group/text 就等于动 `chat.py` 那条契约（三处一起的决定），属于另一个面。
    **订正（2026-09-22）**：上面这段「**不顺手收**」的结论**作废** —— 这三处已随缺陷 94 的**泄漏那半**一并修掉（`group.py` 的 SSE 帧 `36c81c0`、`text.py` 的任务状态行 `c3972ff`），而当时被当作「正确形态」的 `chat.py::_stream_error_payload` 自己那条「故意原样透出」的契约也一并收掉（`393debe` 删了 `preserve_unknown` 开关）。**当时判「另一个面」是误判**：三处的决定面其实是**同一个**（未登记异常的原文该不该上屏），同判据就该同批改。原文保留，仅作当时的判断快照。
  - **原文给「人」看、但读者是管理员 / 对端运维 → 保留（14 处）**：`inter_node.py` ×8（对端运维）、`web/server.py` 的 GPT-SoVITS / FunASR 连通性测试 ×2（admin 自点「测试连接」，原文就是他要的答案）、`web/routers/auth.py` 的 embedding key 自测 ×1、写 `review_log` 的 ×3（`market.py` + `card_guard.py` ×2 + `text_manager.py` 的开卡守卫）。
  - **到不了用户眼前 → 安全（其余 ≈ 245 处）**，机制三类：① 原文进 `ops_detail` 位、出口只取 `user_message`（`DistillError` 各处，缺陷 38 的设计）；② 被 catch 后只 print / 进内部线程队列 / **只做谓词**（`"locked" not in str(exc).lower()` 这类，看着像格式其实是判断）；③ 全局 `Exception` 处理器统一回固定文案 + `traceback.print_exc()`（`StoreError` ×176）。
  - **死代码（记一笔，不修）**：`web/app.py` ×8（Gradio，docstring 标 `.. deprecated::`）。已核 `Dockerfile` / `docker-compose*.yml` / `start_all.bat` / `.github/workflows/` **零引用**。是雷，但今天不可达 —— 记下来免得下次普查重新花时间。
- **修法（机制，不是改三行字符串）**：
  - 新建 **`core/text_failure.py`**：`TEXT_FAILURE_MESSAGES`（12 键）+ 文案。**只放常量、无逻辑**。不塞进 `adapters/llm_adapter.py` —— 那边是 **LLM 侧**失败的口径链（`_INCOMPLETE_USER_MESSAGES` / `user_facing_error`），文本解析失败跟 LLM 无关；共用一个家会让「新增一种 LLM 未完成终态」和「新增一种文件格式」互相牵动。
  - `text_manager.py` 的 **16 条 `raise ValueError`** 全改成 `_MSG[key]`（4 组同文案合并成 12 键）。**除 3 条泄漏点外文案逐字保留** —— 迁移是纯搬迁，行为按构造不变；3 条改成「删掉原文 + 补一句用户能做的动作」（如 `pdf_open_failed` → 「…请确认文件完整后重试」）。
  - **DOCX 双包的修法 = 把「我们自己的判定」移出 `except Exception` 网**（`if not paragraphs` 提到 try 外），不是加标记类型。
  - **原始诊断进日志**：3 处泄漏点 + DOCX 各加 `print(f"[TextManager] <stage> failed: {e!r}")`（该文件本就有 5 处同风格 print）。
  - **顺带**：`web/routers/auth.py` 发验证码的 `except Exception as exc` 绑了 `exc` 却不用、也不 print，**诊断整条丢弃**（用户只看到「邮件发送失败」，运维无从知道是 SMTP 还是 Resend 炸了）—— 与「线索不能丢」同族，一行 print 并进本批。
- **不建 `TextParseError` 的论证（写进条目，否则下次有人会再提一次）**：
  1. **概念确实缺失**（不是「为了方便」）：今天「失败原因」只能用中英文字符串表达，外层 `except` 分不出「我们的事实」与「库的噪声」—— DOCX 双包就是它的症状。这一半成立。
  2. **但载体不必是异常类型**：缺的是「**机器可辨的原因**」，一个闭集键就是它。`raise ValueError(_MSG[key])` 语义上已经只说「发生了什么」。
  3. **类带不来增量**：新类型的唯一独有能力是「结构化字段供多个消费者各自投影」，而今天消费者**只有 1 个**（A 类接收点要的那句人话）。字段 `format`/`stage` 零读者 = YAGNI。
  4. **建类有实际代价**：带自定义字段的异常类会触发 `tests/test_exception_pickle_lock.py::test_census_matches_registry` 的形态门（必须登记 + 提供可重建路径），而文本解析失败是**请求内短命对象、从不过进程边界** —— 为它付跨进程序列化的成本是**为锁而锁**。
  5. **不变量靠静态门强制**，与有没有类型无关（见下 L1）。用户摆在明处的「唯一正当建类理由」= 「类型强制 + 记日志自动化」，也已由 `print` + L3 `capsys` 覆盖，**不要**。
- **形态锁 `tests/test_text_failure_messages.py`（5 条，不维护点位白名单）**：
  - **L1（主判据，静态 AST）**：`core/text_manager.py` 每条 `raise` 的实参必须是 `_MSG[…]` 或 `_MSG[…].format(...)` —— 即**实参内不得出现任何字符串字面量，也不得引用任何 `except` 绑定名**。别名由文件里的 import 语句解析（派生，不是手工清单）。
  - **L2（闭集）**：`text_manager.py` 用到的键 ⊆ 表键，且表键 ⊆ 用到的键（无陈旧）。
  - **L3（端到端 + 线索不丢）**：坏 PDF / 空 DOCX / 超长走 `/api/text/upload` → 400 + `detail` **恰等于**表里那句 + 不含 `Failed to open file` / `Traceback` / `pymupdf`；**同时 `capsys` 里必须有原始诊断**。**超长的载体是文件上传**（缺陷 40 commit 二 把 `data={"text": …}` 那条 urlencoded 通道下掉后换的，命题不变）。
  - **L4（非空负控）**：L1 的扫描必须命中 **≥ 16** 条 `raise` —— 防「对空集断言恒真」的假绿（§四「凡『所有 X 都满足 P』形式的断言，先问 X 会不会是空集」；缺陷 25 min-width 那次就栽在这）。
  - **L5（缺陷 40 commit 二 加；2026-09-15 缺陷 42 第 3 步迁到框架账本层，结案这一步改为按 schema 判正文通道）**：**全仓每一条**声明了表单 content 的 operation，正文只能走**文件字段**，其余 Form 字段只能是元数据（`_FORM_METADATA` 是扁平表 `(path, method, field) → 理由`）。判据读的是 `route_facts.form_fields(...)`（`get_openapi` 出的文档 + `$ref` 解析）与 `route_facts.is_file_field(schema)`，**既不读 `Form(...)` 默认值的形状、也不看字段叫什么名字**。三处历史都写在这条上：① 旧判据扫 AST 找「默认值是不是名叫 Form 的调用」，同时漏掉三种等价写法（`Annotated[...]` / keyword-only / `fastapi.Form`），且只锁得住它硬编码那一处 `web/routers/text.py::upload_text` —— 覆盖面 1 条 → 4 条是**能力达到，不是扩面**（命题本来就适用于所有表单 op）；② 第 3b 步前 L5 自己读 `route_facts._FORM_CONTENT_TYPES` / `_METHODS` 重算覆盖面（跨层读私有常量 + 同一判定两份实现），现已整段删除，content-type 判定只留在事实层的 `_form_media` 一份；③ **第 3 步初版仍按字段名判断正文通道**（`_PAYLOAD_FIELD = "file"`），把 `file: UploadFile = File(...)` 改成 `file: str = Form(...)` 之后正判据与负控**全绿** —— 静默绕过，被 V9 照出（V10 是它的一对：判据退回按名字排除、保留同一改法，绿）。负控 `test_l5_scan_is_not_vacuous`（表单 op 集合非空 + **至少一条 op 带文件字段** + 现场非文件字段集合非空 —— 最后一条是主判据的假绿门）。**边界（如实写在 docstring 里）**：**哪些非文件字段算元数据**仍是策略不是事实（「这个字段是不是正文通道」没有事实层对应物，starlette 对所有文本字段一视同仁），失效方向是**响亮误伤**（签名多一个字段就红、红的信息点名该改哪一行）。**「响亮面故不配绕过型变异」这个说法在结案这一步被推翻**：响亮的是**策略表**那一侧，而「文件 / 非文件」那一侧当时是**静默**的（按名字判），恰恰要绕过型变异才照得出来。
  - **变异（实测）**：L1 —— 把 `_extract_pdf` 的 raise 改回 `f"…{str(e)}"` → 红在「引用了 except 绑定名」；L2 —— 表里删一个键 → 红；L3 —— 删掉那句 `print` → 红在「线索丢了」；**L5 —— 把 `text: str | None = Form(None)` 加回签名 → 红在「多出了 Form 字段：['text']」（`1 failed, 1 passed`，红的是正命题那条、负控仍绿，说明两者判的不是同一件事）；把路由改名 `upload_text_renamed` → L5 两条**都**红（`2 failed`，定位锚点消失）；L3（commit 二 换载体后复验）—— 把文件路径的 `max_chars` 上限 100 万改成 200 万 → 超长用例红。变异后两个文件均已还原并逐字节核对**。
  - **跨文件的通用锁（全仓「`except` 绑定名不得进 `HTTPException` 实参」）不做** —— 它需要一张豁免表（`inter_node.py` ×8 是 B 类）才不误伤，而**豁免表就是「守卫与被守对象之间的第二份手工清单」**（本轮已踩过两次）。如实记在此处，不假装有。
- **判据命令**：`python -W ignore -c "import ast,pathlib;..."`（AST 扫 `raise` 实参形态，见锁文件）、`git grep -n 'str(e)' core/text_manager.py`（应为 0）、`git grep -n 'stderr.decode' web/routers/voice.py`（应全部出现在 `print(` 行内）

**40. 依赖只有下界 ⇒ 上游发版能改本仓测试的结论，且它与「本仓写错了」共用一个红；同一端点「多大算太大」分散在三处** —— 状态：**已修**（commit 一 `53bed63`、commit 二 = 本条目所在提交，2026-09-15）
- **症状（CI 现红）**：`tests/test_text_failure_messages.py::test_l3_oversized_text_screens_table_wording` 在 CI 红、在本机绿。失败详情：`assert 'Field exceeded maximum size of 1024KB.' == '文本超过 100 万 字上限，请分卷上传'` —— **400 那条断言是过的**，只有文案断言过不去。CI run 的 `1 failed, 1071 passed` 与「本仓代码写错了」看起来一模一样。
- **根因 A —— 本仓没有「上游变了」这个信号，于是它与「我变了」共用一个红**（本轮的核心，判据见 §四）：`requirements.txt` / `requirements-dev.txt` 全是下界（`fastapi>=0.115.0` / `python-multipart>=0.0.9` …），CI 每次 `pip install -r` 装到的是**当天的最新版**。上游 2026 年某次发版把 `starlette` 送到 1.6.0：它给 **urlencoded 的 `FormParser`** 也加了 1MB 逐字段上限（1.0.0 时只有 `MultiPartParser` 有，消息是 `Part exceeded…`；新增的这条是 `Field exceeded…`），于是本仓 `max_chars` 判断**根本轮不到执行**。本机装的是 starlette 1.0.0 —— 同一条用例，两台机器两个结论，**没有任何一处告诉你「这是上游动的」**。
- **根因 B —— `/api/text/upload` 一个端点两条入口，「多大算太大」散在三处各自为政**：① 框架的 `max_part_size`（1MB，非文件字段，**本轮唯一真正生效的那处**）；② 应用自己的 `max_chars`（100 万字，`core/text_failure.py` 的 `too_long` 文案，**最后才生效**）；③ 文件路径自己的 `total_size` 累加。三处里只有 ② 是本仓口径，而它排在 ① 之后 —— **守卫与被守的失败形态错位**（同 §四「凡『输出为空即失败』形状的守卫，先问上游能不能对非法输入产出非空输出」）。
- **修法（commit 一：让两种红可区分）**：
  - `requirements.txt` / `requirements-dev.txt` **降级为产物**，人写的那份改名 `requirements.in`（下界照旧）。锁用 **uv** 生成：`uv pip compile requirements.in -o requirements.txt --universal --python-version 3.12`（dev 侧加 `-c requirements.txt` 把主锁当天花板）。**选 uv 的理由与代价**：0.5–6 秒重锁（pip-tools 在本仓要几分钟解析 134 个包），重锁便宜才谈得上「会不会腐烂」—— 用户明确担心的正是锁演化成「永不升级的死锁」；代价是维护者机器上要有 uv，而 uv 不能经 `pip` 声明 —— 缓解是 uv 会把**确切命令**写进锁文件头部，不需要人记。**锁钉的是 CI 当前已装到的新版，不往回钉旧版**（往回钉等于把这次查清的上游变化重新藏起来）。`Dockerfile` / CI / README 的 `requirements.txt` 路径不变，故零改动。
  - `build.yml` 拆成**两条独立红源**：`test`（gate，按锁装，命题「本仓有没有回归」，上游发版动不了它）与 `upstream-drift`（sentinel，按 `.in` 装最新，`continue-on-error: true`、不在 `needs` 链上、不挡合并，命题「上游有没有动」）。**一眼分辨不需要比对版本号**：跑红 = gate；跑绿但一个 job 打红叉 = 哨兵。两条 job 失败时各自往日志末尾打一条带 `title` 的 `::error` / `::warning` 注解，把「这条红意味着什么」写在失败输出自己身上；两边都上传 `pip freeze` 产物 —— 下次红时「当时装的是哪一版」是**可查的事实**，不是要重推一遍的推理。
  - 那条红在 commit 二 落地前标 **`xfail(strict=True)`**（不是裸 skip），reason 带本条编号、根因与「strict 为什么是承重的」。**`strict=True` 在这里干了三件事**：commit 二 修好后当场 XPASS 成红 → 逼着删标记，不会留一个腐烂的「永远 xfail」把真实回归一起吞；把锁退回旧 starlette 会当场变红（**本机实测：本地 venv 是 starlette 1.0.0，该用例 `XPASS(strict)` 红**）；上游若自行改了这行为，哨兵会红。
- **修法（commit 二：消灭「两条入口两套约束」）—— 已落，方向 A（下掉 `text` 字段）**：
  - **先查后定**（普查命令与结果见 commit message）：`text` / `filename` 两个 Form 字段的**生产调用方为零** —— 前端 `useAppStore.uploadText` 只 `append` 了 `file/title/description/text_type`，`TextPanel` 无粘贴入口；`mcp_server/` / `scripts/` / `docs/` 零命中。唯一两处调用方都是非生产形态：`tests/perf/e2e_otel.py`（性能脚手架，**是同一缺陷的第二个实例**，见下）与那条 xfail 用例自身。**射程经查是一个端点的两个字段** —— `TextManager.upload_text` 另有调用方 `web/routers/distill.py:1562`（legacy `/api/distill`，走 **JSON body**、不经 `FormParser`），其 100 万字判据是好的，故**领域层一个字节不动**。
  - **方向 B（约束前移）被排除，理由是硬的**：本仓上限的单位是**字**，starlette 是**字节**；合法上界 100 万汉字 ≈ 3MB > 1MB ⇒ **任何**前移到 `Content-Length` 的门都无法既放行全部合法输入、又不让库先炸 —— B 只能做到「更早报错」，做不到「传不出非法值」。已升格为 §四 判据（「先看两层的度量单位能不能换算」，两个分界：可换算 → 前移成立；不可换算 → 动通道本身）。
  - **改动**：路由签名去掉 `text` / `filename`，删 `elif text:` 分支，`:182` 的「二选一」文案改 `"Must provide file"`（**留在路由内，不进 `core/text_failure.py`** —— 它是「路由参数缺失」不是「文本解析失败」，进表会污染领域层的键闭集，L2 当场红）。**隔离判据（已核）**：`git diff --stat` 未出现 `core/text_manager.py` 与 `core/text_failure.py`。
  - **两处连带项同批**（不同批会留下自相矛盾的中间态）：① L3 超长用例**换载体不换命题** —— 从 `data={"text": …}` 换成上传 100 万零 1 字（≈3MB）的 `.txt`，`strict xfail` 当场删除转真跑；文件 part 不受 `max_part_size` 约束（starlette 的检查写在 `if self._current_part.file is None:` 里面，**读源码坐实**），故能走到本仓 `max_chars`。② `tests/perf/e2e_otel.py` 的 `_multipart` 加 file part、调用点改走 `file` —— **32 万汉字 ≈ 960KB = 1MB 上限的 92%**，从前只差 9% 就会撞上限报出库文案，是同源的第二处显形。
  - **防回归机制（L5 形态锁，沿用本文件 L1–L4 同一套，未新建扫描框架）**：`test_l5_upload_route_accepts_no_form_text_payload` 断言路由签名里除三个元数据字段外没有别的 `Form(...)` 字段（判据从**默认值的形状**推出，不读注解），配 `test_l5_scan_is_not_vacuous` 负控（扫描必须命中 `file/title/description/text_type`）。**锁的边界如实写进 docstring 原样保留**：「它只管这一个 route 的签名；有人在**另一条** route 上重开一个无界文本入参，它抓不到 —— 那要靠 §四 判据人自己问『这条红能告诉我是我变了还是世界变了』。」**不把它说成覆盖全仓。**
    - **2026-09-15 更新（缺陷 42 第 3 步，留档不删）**：上面这两个测试名**已不存在**，L5 已迁到框架账本层（见本节 L5 条与 §三 缺陷 42）。当时那句边界（「只管这一个 route」）**随之消失** —— 现在覆盖全仓四条表单 op。**旧边界成立过，故原文保留而不是改写**：它是「判据隔着几层」在当时的真实读数，改写会让后来的人看不出这次迁移补的是哪个洞。
  - **文档同步**：`README.md` 的 API 表由「文件或粘贴」改为「**仅文件**」—— 那是「靠它存在而非它的值」的最典型形态：**能力早没了，承诺还在**（同 §四「注释也是某一刻的记录」）。
  - **「多大算太大」还剩几处**：本端点上从三处收敛到两处（框架的 multipart 非文件字段上限已无字段可卡 + 本仓 `max_chars`），文件体积另由路由的 `MAX_FILE_SIZE`（30MB，`:143`）兜 —— 两者判的不是同一件事（体积 vs 字数），不是重复守卫。
- **判据命令**：`git grep -n 'Field exceeded' `（应为 0，出现即在泄漏框架文案）、`grep -c '==' requirements.txt requirements-dev.txt`、`grep -nE '^[A-Za-z][A-Za-z0-9._-]*(\[[^]]*\])?\s*[<>~!]' requirements.txt`（应为 0 —— 锁里出现区间就不再是锁）
- **未做 / 已知代价（如实记）**：
  - ① **本地 venv 与锁差 89/156 条**（2026-09-15 现跑现数：67 条版本相同、76 条版本不同、13 条锁里有而本地无；`openai` 2.24.0→3.13.0、`starlette` 1.0.0→1.6.0、`gradio` 6.14→6.27 …）。**影响：本机全量的绿/红都不可信** —— 与 §四「绿不可信」同族：环境不是锁的那套时，跑绿说明不了「锁的那套是绿的」，跑红也定位不到是本仓还是版本。**触发条件**：要拿本机结果当「按锁」的证据时（跑全量、复现 CI 红、判断某条红是不是上游所致）。**不修的理由**：照锁重装会把一个已知可用的环境换成一个已知会炸的 —— 锁里的 `onnxruntime==1.30.0` 在这台 Windows 上加载不了 DLL，重装后本地全套会在中途段错误（单变量反向对照见 §五 环境账）；**代价大于收益**，且这是 Windows 特有、与 CI（Linux）无关。**做法（不是建议）**：**本地要按锁验证，只用一次性 venv**（新建、按锁装、验完即弃），不碰日常那个 venv。
  - ①′ **`strict xfail` 曾兼任「环境差异报警器」，commit 二 删掉标记后这个能力没了** —— 从前本地跑全量会因 starlette 版本差异 XPASS 成红，等于免费提示「你的环境不是锁的那套」；现在那条用例两条版本下都通过（载体已换成文件上传，不再对 `FormParser` 的行为敏感），**环境差异从「会红」变成「静默」**。如实记：这是 commit 二 的一个真实代价，上面的「本机全量不可信」因此更依赖人的自觉，而不再有任何一条用例会替你喊。
  - ② **「`.in` 改了忘了重锁」目前没有门**：本可加 `uv pip compile … -c requirements.txt` 对账，但那个步骤要在 gate 里连网解析最新 —— 一旦解析侧出问题，gate 就会因为**世界的原因**变红，正好复犯本轮要修的错（两种成因共用一个信号），故**故意不加**；重锁命令已写进 `.in` 头部。**附带的不符（已记、未修）**：`.in` 头部注释里提到了 build.yml 的 `"Verify installed versions match the lock"` 步骤，而该步骤最终没有实现（理由同上）—— **注释与实现不符**，属本节「注释也是某一刻的记录」。用户裁定**不搭车**（2026-09-15）。
  - ③ **不建第三条 verdict job**：job 命名 + 注解 + 产物已构成契约。
  - ④ **「断言不是实测」的交代（用户认可，照记）**：`continue-on-error: true` 在 job 级的语义（job 显示红叉、整个 run 结论仍为 success）**本轮没有实测** —— 要实测得往 `main` 推一个故意让哨兵红的提交，代价不划算，故如实写明而不是含糊过去。**同时把依赖关系拆清楚**：commit 一 要的「两条失败可区分」这个命题**不依赖**那次未做的实测 —— 它由三条各自独立成立的机制共同保证：**job 名**（「gate / 本仓回归」vs「sentinel / 上游漂移」）、**失败注解**（`::error` vs `::warning`，各自把「这条红意味着什么」写在失败输出自己身上）、**两份具名产物**（`pip-freeze-gate` / `pip-freeze-sentinel`）。**拆清依赖关系比笼统说「应该没问题」强**：前者说明白了哪一条是承重的、哪一条只是锦上添花。

**41. postgres 的 healthcheck 是一个结构上无法失败的探针 —— `depends_on: service_healthy` 这道门一个半月来没验过凭据，且不可能因凭据问题红** —— 状态：**已修**（B 轮 `3da6156` `a3b51d4` `33aba7b` `0619d53` `809a78c` `90167f4` `bc072ea`；A 轮 `c08c8fc` `008874d`，判别力收尾 `e9cbdde`；**C 轮重建 `6e5398c` `5afecbf` `725761d` `b820f13` `d667db8`，锁 → `tests/test_pg_gate.py`，验收补 `486e573`**，2026-09-16）
- **形态**：`docker-compose.local.yml` 与 `docker-compose.prod.yml` 的 postgres 服务：
  `test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]`。变量为空时 shell 展开成 `pg_isready -U  -d `，`-d` 被当成用户名 —— 日志里于是每 10 秒一行 `FATAL: role "-d" does not exist`，**自 2026-08-17 起持续**（`docker logs character-distill-postgres-1` 首行之后即是）。
- **门看着在，实际什么都不守**：容器全程 `(healthy)`，而 `app` 的 `depends_on: postgres: condition: service_healthy` 正是靠这个结论放行。也就是说，这道被当作「库就绪」的保证，**一个半月里连一次凭据都没验过**，且没有任何一条路径会让它因凭据问题变红。
- **为什么 `:?` 修不好它（所以不是 32 的尾巴）**：即便变量补齐、`-U` / `-d` 拿到正确名字，**`pg_isready` 依然不会因凭据错而失败**。官方语义写得很清楚：*"It is not necessary to supply correct user name, password, or database name values to obtain the server status"* —— 它回答的是「服务器在不在应答」，不是「我连得上」。实测：`pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`（两变量为空）输出 `accepting connections` 且 **exit=0**。
- **定性：不是配错了，是选错了检查手段。** 用一个**结构上无法失败**的探针当门 —— 与缺陷 32/34 同族（失败被吞成正常），但**不同处**：32 是「缺变量静默降级」，34 是「输出非空即放行」，这一条是「**探针的命题与门的命题不是同一个**」：门的命题是「库可用」，探针的命题是「库在应答」。
- **可辨性缺口**：唯一的信号是 PG 自己日志里的 FATAL 行，而它**不在**任何被监控的地方；容器状态、`docker compose ps`、`config` 输出一律正常。与 §四「两种不同成因的失败若共用一个信号」同源 —— 这里更极端：**根本没有信号**。
- **与本批（32+33）分开的理由（用户裁定）**：本批命题是「缺变量不静默」+「凭据不走命令行」；这条修的是「健康检查不检查凭据」—— **同病不同处**，混进同一 commit 会让「哪行因哪个动因改动」说不清。
- **三层修法（各层的判据不是同一件事，故各锁各的）**：
  - **① pg 凭据探针**（A 轮 `c08c8fc`）：两个编排文件的 postgres healthcheck 改成**逐字相同**的一条 `psql -tAc 'select 1'`，`-h` 用**服务名** `postgres`（与 app 连库同一条路径、同一套 `pg_hba` 规则），口令以 `$$POSTGRES_PASSWORD` 在**容器内**展开（容器配置里存的是变量名，`docker inspect` 看不到值），`PGCONNECT_TIMEOUT=3` 小于该 healthcheck 的 `timeout=5s`（否则「连不上」与「连得慢」又共用一个信号），stderr 不重定向（认证失败原文进健康日志）。**为什么 `-h` 不能用环回**：官方镜像里 unix socket 与 127.0.0.1 都是 `trust`，只有非环回地址才走 scram —— 写成环回会让探针**又能「永远成功」**（实测）。
  - **② app 就绪端点**（B 轮 `3da6156` + `a3b51d4` + `0619d53`）：`GET /api/health/ready` 真调 `storage.ping()`；`StorageBase.ping()` 是「跑一条读语句」而不是「取到连接」，SQLite 那份读的是 `sqlite_master`（不读文件的语句不构成探测）。
  - **③ 部署两段门**（B 轮 `33aba7b` + `809a78c`）：两个区域各跑**存活**（`/api/health`，不碰库）→ **就绪**（`/api/health/ready`，真查库）两段；容器镜像的 `HEALTHCHECK` 同走就绪路径。
  - **各层的锁**：① `tests/test_pg_gate.py`（**C 轮重建**，10 条：两条负控 + **门的三个部分各一条封闭判据** + 时长解析器契约 + 豁免表四条对账；判据全部读 `compose_model.effective_model()` 的**有效模型**，不 grep、不自己解析 YAML —— postgres 服务集合由镜像派生，新增编排文件或改名服务自动进覆盖；负控「解析不出任何 postgres 服务」与「某个库的硬消费者**全靠 `depends_on`** 认出来」防各条断言空转；「所有此类服务的整个 healthcheck 逐字相同」那条兑现「多份定义」这个取舍）
      - **这 10 条里 7 条挂环境声明、3 条不挂**（验收补 `486e573`）。**病灶**：上一版一条 mark 都没挂，本机没有 `docker compose` 时那 7 条不是 skip 而是**红**（`ComposeFactError: 调不起 docker compose`）—— 「装没装 docker」与「门被破坏了」共用同一个信号，正是本条从头到尾要修的形态，却长在锁自己身上。**改法**：要真跑 `compose config` 的七条各挂 `compose_model.COMPOSE_ENV.skipif("门的锁")`；不碰 compose 的三条（隐式加载文件、时长解析器契约、豁免理由非空）**不挂** —— 没有 docker 的环境里它们照样真跑，判据多一条是一条。CI 侧由 `REQUIRE_COMPOSE_TESTS=1` 拒绝跳过，故挂 mark 不会让 CI 少跑一次门锁。**三环境实测**（Windows 有 docker）`35 passed` 0 skipped；（Linux 容器无 docker）`16 passed, 19 skipped, 0 failed`，exit 0；（Linux 容器 + `REQUIRE_COMPOSE_TESTS=1`）`16 failed` —— 那 19 条拒绝跳过、真跑并红，其余 16 条照旧通过。
      - **这条漏挂是承重的（照记）**：它说明「环境声明」这个机制**不会自动覆盖新写的用例** —— 新加一条依赖 docker 的判据时忘了挂 mark，在有 docker 的 CI 上完全看不出来，只有没装 docker 的机器会红，而红的样子又不指向「你忘了挂 mark」。这次是验收按 §6 要跑三环境才照出来的。
    ② `tests/test_storage_ping.py` + `tests/test_health_ready.py` ③ `tests/test_health_probe_targets.py`。
    - **第一版锁是开放式检查，已返工为封闭语法**（`8395827`）。**病灶**：它逐条断言「不含 `pg_isready`」「调了 `psql`」「`-h` 是服务名」「口令容器内展开」「超时关系对」—— 每条只证明**好的成分在**，证明不了**坏的成分不在**。审计实测四种改法全部绕过它（两个编排文件同步改，锁全绿）：前缀加 `PGHOSTADDR=127.0.0.1`、`-h postgres` 后加 `--host=127.0.0.1`、末尾加 `; exit 0`、末尾加 `|| true` —— 每种都让探针退化成「不验密码」或「永远成功」，而四条断言一条都不红。**改法**：`shlex` 切 token，整条命令必须**恰好**是 `PGPASSWORD=<容器内变量> PGCONNECT_TIMEOUT=<整数> psql -h <本服务键名> -U <容器内变量> -d <容器内变量> -tAc 'select 1' > /dev/null`，出现任何语法外的 token 就**点名它**；`2>` 单列成一条（吞掉 stderr 等于让红失去诊断价值）。**A-8～A-13** 是这六条新变异，A-1～A-7 结果不变。这是本条病灶的又一次显形（与缺陷 42 同族：**开放式判据只能证「像」，证不了「是」**），只是这次长在锁自己身上。
    - **第二轮审计：门还有两个部分没锁，`disable: true` 照样绕过**（`19a90b7`）。**「门」不是那条命令**，是三个部分：**探针命令** + **探针配置**（`healthcheck` 映射的其余键）+ **门的消费者**（谁在等这个服务的健康状态）。上一版只把第一部分管住了。审计实测：两个编排文件同步加一个 `disable: true`，锁**全绿** —— 它不是「多了一个无关的键」，它把这道门整个关掉。同理没锁的还有 `start_period`、`retries` 的取值，以及消费者侧的三条静默改法：`depends_on` 退成短式列表（不带 `condition`）、`condition` 改成 `service_started`、新服务连库却不声明依赖。**改法（判据 1/2/3）**：① healthcheck 键集合**恰好**为 `{test, interval, timeout, retries}`（多一个键就点名它），`test` 必须是恰好两元素的列表且首元素为 `CMD-SHELL`，`interval` / `timeout` 用同一个 `_duration_seconds` 解析（**解析不了就报错点名，不返回默认值** —— 「当 0」会把「没人看得懂」变成一个合法数字），`retries` 必须是正整数；② 一致性从「只比 `test`」升级为「比**整个 healthcheck 映射**」（只调一份的 `interval` 同样是漂移）；③ 消费者由**解析**得出（`depends_on` 与连接串主机段 `@<服务键>:` 两条路径的**并集**，列表与映射两种 `environment` 写法都认），且必须写成映射形式的 `condition: service_healthy`。**A-14～A-24** 是这十一条新变异（A-24 打的是 `_duration_seconds` 自己的契约），A-1～A-13 结果不变。识别依据会写进报错文案 —— 否则「删掉 `depends_on` 只留连接串」红出来的句子和「退成短式列表」一模一样，说不清是哪条路径命中的。
    - **C 轮重建：判据不再对着「代理」判**（`b820f13`，锁改名 `tests/test_pg_gate.py`）。**病灶**：上面两版（无论开放式还是封闭语法）都把模型交给两处**代理** —— 自己 `yaml.safe_load`，再按**字面量** `@<服务名>:` 认消费者。两处各有一条实测盲区（只改 `docker-compose.prod.yml` 一个文件，**旧锁 `5 passed` 全绿**）：
      - **Y-1**：`worker` 用 `extends: {service: app}` 继承到连接串、自己不声明 `depends_on`。**代理一**（原始 YAML）看不见 —— 原文里它没有 `environment`、没有 `depends_on`，干干净净。`<<` 合并键 `yaml.safe_load` 倒**会**展开，这正是代理最难察的地方：**它有时候是对的**。
      - **Y-2**：`worker` 只写 `PGHOST: postgres`（值**恰好等于**服务名）、无 `depends_on`；**Y-3**：连接串主机段写成 `@postgres/db`（斜杠，不是冒号）—— **代理二**（按 `@<服务名>:` 这个子串认）两条都穿过去了。
      三条都让一个服务连上了库却没人等门。**改法**：模型一律问 `tests/compose_model.py`（真跑 `docker compose config --no-env-resolution --format json`），锁只对**模型**判 —— `extends` / `<<` / 短式 `depends_on` / `${VAR}` 插值都已在 docker 那边展开；**两处代理一起删**。判据同时从「好的成分在」收敛为**封闭**（`shlex` 切 token、整条命令必须恰好是那个形状；healthcheck 键集合恰好四个）。
      **两条新变异是「读的是有效模型」的直接反证**：G-7 用 `<<` 从顶层锚点注入 `disable: true`（原始 YAML 里 healthcheck 下只有一个 `<<`）、G-16 用 `extends: {service: app}` 继承连接串 + `!reset` 覆盖 `depends_on`（原始 YAML 里既没有连接串、也没有「不等门」这两个事实）。**G-16 的写法是实测选的，不是猜的**：`extends` 对 `depends_on` 是**映射合并** —— 子服务另写短式列表、或另写一个映射条目，都只是**并进**父服务的 `postgres` 条目、照样等门；四种写法实测只有 `!reset []` 能真正清掉（`depends_on -> null`）。
      **判据 3 随之从「两条路径的并集」升级为「三条硬证据 + 一张豁免表」**：证据 = 连接串主机段（`@<服务名>:` **或** `/<服务名>/`）/ 某个环境变量值**恰好等于**服务名 / `depends_on` 点名；认不出来的服务**必须登记**，登记**必须有理由**，且表要与现场对账（未登记 / 陈旧 / 空理由 / 与识别器自相矛盾 —— 四条都走 `tests/policy_table.py` 的机械校验）。**闭包判据是这一轮真正的增量**：原来「新加一个服务却没人复核它连不连库」正是从判据底下溜过去的那条路。
      **豁免表全文（3 条，各条都经现场复核、不是猜的）**：`("docker-compose.local.yml", "jaeger")` —— OTel 收集器，只收 app 上报的 OTLP，无 `depends_on`、无连接串、无 `env_file`；`("docker-compose.prod.yml", "nginx")` —— 反向代理，`depends_on` 的是 `app`（应用层）、不直接连库；`("docker-compose.prod.yml", "fail2ban")` —— 只读 nginx 日志做封禁。`app` 在两份文件里都是**硬消费者**（连接串 + `depends_on`），故一律不进表。
  - **各层的实跑证据**：① `tests/perf/credtest_pg_healthcheck.py` —— 独立项目 `-p credtest` + 自有卷，healthcheck **从仓里那份解析读取**（不另抄一份，否则验的是脚本里的命令而不是仓里的命令）；实测三场景 postgres `healthy → unhealthy → healthy`，app 只在第一场景被放行。**场景 3 是本条最完整的一次显形**：换回 `pg_isready`、同一条错口令下 postgres 报 `healthy`、app 被放行，而它自己的 `/api/health/ready` 答 **503** —— 「门说可以，门后面的东西说不行」，门的命题与它要保证的命题不是同一个。② 由同一脚本的场景 1/3 顺带真跑（好凭据 200 / 坏凭据 503）。B 轮当时的证据是临时 docker 实跑，**未入库**（A 轮的 credtest 脚本是唯一入库的实跑产物）。
  - **变异矩阵**：**C 轮矩阵** `tests/perf/pg_gate_mutations.py` —— 一组共 25 条（G-1～G-25），**全 RED 且红在预期那句**（红源标记逐条比对，不是只看「有没有红」）。按不变量分组：**I1 探针命令封闭** G-1 退回 `pg_isready` / G-2 `-h` 改环回 / G-3 前缀加 `PGHOSTADDR=127.0.0.1` / G-4 末尾加 `|| true` / G-5 口令改成宿主机插值 `${POSTGRES_PASSWORD}`（由**哨兵值**识别）/ G-23 服务键改名而探针仍写旧名；**I2 探针配置封闭** G-6 加 `disable: true` / G-7 用 `<<` 从顶层锚点**注入** `disable: true` / G-8 `timeout` 小于 `PGCONNECT_TIMEOUT`；**I3 多份定义一致** G-9 只改 local 的 `interval`；**I4 消费者闭包** G-10 `depends_on` 退成短式列表 / G-11 `condition` 改 `service_started` / G-12~G-16 五种新服务写法（`@postgres/db` 斜杠连接串、`PGHOST: postgres`、只有 `env_file`、无任何配置、`extends` 继承 + `!reset` 覆盖依赖）/ G-17 豁免表加不存在的服务 / G-18 理由改成空串 / G-19 真消费者塞进豁免表 / G-24~G-25 **文件级**豁免表（`_NO_IN_FILE_CONSUMER`）加一条现场不存在的条目、把理由改成空串；**I5 无隐式自动加载** G-20 在仓库根目录新建空的 `docker-compose.override.yml`；**事实层自己的契约** G-21 求值失败时返回 `{}` / G-22 退回读原始 YAML（这两条的靶子是事实层自己的锁 `tests/test_compose_model.py`）。**驱动自己有四道门**：docker 前置门（没有可用的 `docker compose` 时**拒跑并明确打印「本环境无法运行此矩阵」，不记为通过** —— 一个什么都跑不出来的矩阵和全绿长得一样）、先验基线（两把锁全绿才开跑，否则「变异后红」说不清红源）、编号集合自检（docstring 声明的编号必须等于变异表现数，匹配不到也拒跑）、还原逐字节（收尾核 sha256；G-20 新建的文件单独清掉并点名残留）。A 轮矩阵（24 条，随旧锁一并删除）与 B 组 `tests/perf/ping_mutations.py`（B-1～B-14，15 条）仍在。三个驱动都**复用** `route_facts_mutations.py` 的 `_apply` / `_restore` / `_run`，不复制实现。
- **两个共享层的登记（本轮新增其一、复用其一；使用方各列全）**：
  - **`tests/compose_model.py`（compose 事实层，C 轮 Commit 3 `725761d` 新增）** —— 只回答「这份编排文件，compose 读成了什么」。**使用方**：`tests/test_pg_gate.py`（策略锁，判据全部建立在它上面）、`tests/test_compose_model.py`（本层自己的锁，13 条；含唯一一条真跑前提的用例）、`tests/test_compose_capability.py`（承重前提 P-a/P-b 的负控/正控，7 条，**默认不真跑 compose** —— 假 `_compose` 由 autouse fixture 装上）、`tests/perf/pg_gate_mutations.py`（docker 前置门用事实层的能力判定，缺陷 51 起叫 `cli_capable()`；G-21/G-22 把它当靶子）。**边界（硬，由 `test_fact_layer_names_no_repo_specifics` 强制）**：不出现本仓任何服务名、任何本仓编排文件名，也不 import 策略层 —— 依赖只能自上而下；唯一出现的一批文件名是 compose **工具自己**的默认自动加载名（那是工具的契约，不是本仓的约定）。**名单由仓库现状推导**（glob 出的编排文件名 ∪ 各文件里解析出的服务名），不是写死的一张表 —— 写死的话，新加一个服务时这条用例会继续绿，而事实层已经悄悄认识它了。**两个字面量承重**：`--no-env-resolution`（不加它 compose 会解析服务声明的 `env_file`：干净检出上是 exit=1，开发机上则把**真实凭据**原样灌进模型。**订正（审计 F4）**：由此说它「不吐秘密」是**无条件的**，错 —— 那是**有前提**的，只在满足 P-a/P-b 的 compose 上成立；v2.x 上 flag 在、效果没有，见「缺陷 51」）与哨兵占位值（策略层据此分辨「宿主机 `${VAR}` 插值结果」与「容器内 `$VAR`」）。
  - **`tests/policy_table.py`（通用豁免表机械校验，缺陷 42 结案时抽出，C 轮 Commit 2 `5afecbf` 更名）** —— `empty_reasons` / `stale_keys` / `unexpected`，表统一是扁平 `dict[Hashable, str]`（键 = 被豁免的东西，值 = 理由），**与领域无关**。**使用方**：`tests/test_text_failure_messages.py`（`_FORM_METADATA`）、`tests/test_auth_param_used.py`（`ALLOWLIST`）、`tests/test_pg_gate.py`（`_NOT_A_DB_CONSUMER`）、`tests/test_policy_table.py`（本层自己的锁），变异驱动见 `tests/perf/route_facts_mutations.py` 的 P 组。**更名的理由**：它原本的 docstring 用 L5 与 auth 锁的例子解释自己，看起来像路由专用层；compose 门锁要用同一套（服务有没有登记的豁免表）时，那个名字会误导人往本层里塞业务字段。函数体一字未改。
- **CI 声明（C 轮 Commit 5 `d667db8`）**：`build.yml` 里两个跑 pytest 的 job（gate 与 sentinel）都加了 `REQUIRE_COMPOSE_TESTS: "1"`。**理由**：门的锁判的是 compose 的有效模型，没有 CLI 时那批用例走的是**显式 skip**（不是红）—— 本地那样可以，CI 不行（CI 最该真跑门锁，跳过等于这道门在 CI 里不存在）。`ubuntu-latest` 自带 docker 与 compose v2 插件（实测 2.38.2；`config --no-env-resolution` 与 `--variables` 两个承重选项都在），故这条声明是真的。**订正（审计 F4）**：这次「实测」测的是**选项在不在**，不是这两条**行为** —— v2.x 上两个选项都在，而 P-a/P-b **都不成立**，故「这条声明是真的」当时**不成立**（正是缺陷 51 本身）。现在它的依据换成了：两个 job 各钉 compose `v5.5.1`（sha256 校验 + 版本号断言，判的是行为），`REQUIRE_COMPOSE_TESTS: "1"` 只在此之上声明「本环境保证有 compose」。**它与 `REQUIRE_PG_TESTS` 是同一个机制**：`tests/conftest.py::requirement()` 一处定义，`REQUIRE_*_TESTS` = 拒绝 skip，`SKIP_*_TESTS` = 显式关闭，两者同时置位即红。
- **已知取舍（用户认可，照记）**：
  - **500 与 503 不是同一件事**：`STORAGE_BACKEND` 未声明时 `get_storage()` 在 **Depends 阶段**抛，就绪端点答 **500**；凭据/连通性故障在端点内部被接住，答 **503**。两者共用一个信号的余地是**故意**留的（「配置没给」与「库连不上」要能分开），代价是调用方读状态码时要多认一种。
  - **一次认证配置错误会拦下整条启动链**：这是本次要的（从前是 app 静默起来、请求时才报），但代价就是**配置层故障会让 app 直接起不来**。
  - **健康检查命令是多份定义**（prod / local / test 各一份），由 `tests/test_pg_gate.py` 的「整个 healthcheck 映射逐字相同」那条强制一致。**不抽共享的原因**：部署只 scp `docker-compose.prod.yml` 这一个文件到服务器，抽成共享脚本或共享 compose 文件会改动两区部署流程并新增挂载依赖 —— 拿「多一层共享」换「少一处重复」，而那层共享正是能悄悄漂移的地方。**多份定义读的仍是同一个「有效模型」**：锁比的是 `json.dumps(healthcheck)` 而不是文件文本，故 `<<` / `extends` 写出来的漂移一样接得住。
- **记账（不修）**：
  - **本机全量的绿/红都不可信（§四旧账，非本条范围）**：Windows 单进程跑不完整套 —— 半途 `onnxruntime` 访问违例（`tests/test_ownership_404.py` 的注里早写着成因：chroma → fastembed → onnxruntime），**06ecabb 同样崩、崩在同一处**；临时容器（`python:3.12-slim`）全量的 17 条失败**全部是它没装 git**（`git cat-file` 一类拿不到），装上 git 后同一文件 `1 failed, 40 passed` 与 Windows 逐字相同、整仓 `1068 passed / 82 skipped / 0 failed`。故本条的验收证据一律取自**受控对比**（06ecabb vs HEAD、同一环境），不取本机全量的绝对绿。
  - **`web/server.py` 启动时连库失败仍为非致命**（有意的设计）：就绪端点与部署门是**事后**发现凭据问题的那一道，进程本身不因库不可用而退出。
  - **变异驱动还原时会覆盖期间对靶子文件的外部修改**：`_restore` 把 `TARGETS` 里每个文件按开跑那一刻的字节写回。跑矩阵期间编辑靶子文件 = 编辑被静默吞掉。这条写进了三个驱动的 docstring，属工具的固有形态（要保留「逐字节还原」这个不变量，就得接受它）。
  - **两处编排文件的注释仍指着旧锁名**（C 轮 Commit 4 把锁改名 `tests/test_pg_gate.py`，但这两个文件本轮**必须零 diff**，故注释没动）：`docker-compose.prod.yml:55` 与 `docker-compose.local.yml:46` 各有一句「两份的一致性由 tests/test_pg_healthcheck.py 强制保证」。**路径已不存在**，属 §四「注释也是某一刻的记录」。**不搭车修**：改了就不是零 diff，本轮的验收判据（`git diff 06ecabb..HEAD -- docker-compose*.yml` 必须为空）当场失效。要修的话单独一次改动、只动这两行注释。**已修（2026-09-24，与 `--wait` 文案同批）**：两行注释现写 `tests/test_pg_gate.py`，旧锁名在本仓只剩本条这段历史引述。
  - **`tests/route_policy.py` → `tests/policy_table.py` 的更名超出本缺陷范围（用户裁定：保留，不回滚）**：`5afecbf` 那一改是照本轮 spec 的 Commit 2 执行的，而用户砍掉该 Commit 在**执行之后** —— 时序问题在方案侧。**保留的理由是回滚成本高于收益**（要动改名 + 4 处调用点），**不是因为它属于缺陷 41** —— 本条的范围是 compose 门锁，改名只是搭了这一趟车。下一轮若有人问「这层为什么叫这个名」，答案在缺陷 42 结案那一节（它确实与路由无关），不在本条。
  - **覆盖闭合元锁首跑：169 条判别器只撞到 63 条（缺口 106），另有 6 条「空转」是假的**（2026-09-16，`tests/lock_coverage.py` + `tests/test_lock_coverage.py` 第一次跑全三个驱动；`③ ping 补产物` 让 `ping_mutations.py` 也写产物之后）。逐驱动：`pg_gate` |D|=64 / |E|=23（缺 41）、`ping` |D|=35 / |E|=14（缺 21）、`route_facts` |D|=70 / |E|=26（缺 44、空转 6）。**这个读数是本条最要紧的产物**：缺口是锁自己报出来的，不是人手工对表对出来的（C 轮那次手工只对了包装类 24 个调用点，`assert` 与 `raise` 两类从没对过）。缺口按类处置不同：
    - **① 补变异（约 101 条）** —— 绝大多数，按变异落点分四组：**打编排文件形态的非法 healthcheck**（`test_pg_gate.py` 的 `_assert_closed_grammar` / `_assert_healthcheck_config` / `_assert_consumer_waits` 共约 29 条分支，每条 = 一种具体的非法形状，机械可写 —— A 轮审计手工找到的那四种绕过只是其中四个）；**打事实层与锁自身实现**（`test_compose_model.py` 11、`test_policy_table.py` 8、`test_route_facts.py` 4）；**打 `core/text_manager.py` 与消息表**（`test_text_failure_messages.py` 的 L1/L2/L3 那批）；**打 `web/` `storage/` 生产代码**（`test_storage_ping.py` / `test_health_ready.py` / `test_health_probe_targets.py`）。**成本要如实看**：判据数随锁文件里的断言数（169）走，不随被守的行为数走 —— 补完是一百多条新变异、每轮矩阵的时长按分钟到小时计。
    - **② 定义**过宽**造成的假缺口（2 条，不是死判据）** —— **已收窄**：`tests/test_health_probe_targets.py:133` 与 `tests/test_health_ready.py:48` 的 `raise self._exc`。两条都不是判据，是**测试替身**的失败注入（`if self._exc is not None:` 之下，所以也不是纯抛错包装的体，原先各占 D 的一席）；而判别器定义 (b) 原先收的是**每一条 `ast.Raise`**，把机制当成了判别器。**不能按「死判据就删掉」处置**（删了替身就不工作了），正解是**收窄定义**：只收**就地构造或点名异常**的 raise（`raise X(...)` / `raise X`），不收**转抛外来异常**的（`raise self._exc` / 裸 `raise`）。`_pure_raisers` 里那把尺子**同一处一起换掉**（体是裸 raise / 转抛的函数不算包装）—— 两处不一致的话，下一个按 docstring 判断的人会用另一把尺子。**实测收窄后：`ping` |D|=35 → 33（正好是这两条），合计 169 → 167。**
    - **③ 本环境必然留下的缺口（3 条 →（方案 C 后）2 条）**：`tests/test_storage_ping.py:161`（撞它的 B-1/B-1b 在本机被 `pytest.skip`）、`:175`（PG 用例，本机无 PG）、`tests/test_text_failure_messages.py:477`（import 钩子那条 `raise _ParserRuntimeTouched(fullname)` —— 它是**就地构造**的，收窄后留在 D，只是本机 import onnxruntime 必崩、钩子根本走不到；**原先误记进 ②**，它跟替身那两条不同：替身是机制、它只是不可达）。**产物是在本机写的**，于是这三条会**永久**留在入库的那份产物里 —— 元锁读的是**产物**、不是当场重算，故在 Linux 上重跑也一样。要让覆盖闭合在 CI 成立，产物必须由 **CI 上那次真跑**生成并核对。
      - **2026-09-16 方案 C 落地：第三条整条消失（3 → 2）。** 那条 `raise` 所在的 import 钩子随 `pymupdf4llm` 一起被删（解析器运行时已不在仓里，「没触达」成了对**空集**的断言 = 假绿），换成入口面静态守卫 `test_l6_no_parser_runtime_in_source`。**故它是被删掉的判据，不是被撞到的判据** —— 两条剩余缺口（`test_storage_ping.py` 的两条环境性 skip）与本条无关，仍在。
  - **重写：探针判据从「一条判据一段控制流」改成「形状写在段表里」（2026-09-16）** —— `_assert_closed_grammar` 原本是 16 条判据 = 16 段控制流 = 16 个抛错点，而它真正要防的失效只有**一类**（「这条命令不是那个形状」），那 16 个只是它的 16 种表现。现在逐 token 比对由 `_scan` 一处做完（`_GRAMMAR` 五段：前缀赋值 5 条反例 / 命令体 2 / psql 参数 6 / 查询参数 2 / 结尾重定向 3），**合法定义与它的反例挂在同一段上**（共 18 条），抛错只剩 `_reject` 一处。实测（现跑现数）：`_assert_closed_grammar` |D| **23 → 7**（7 条全是 `_reject` 调用点，493/499/506/512/516/520/523），整文件 |D| **48 → 32**（正好少这 16 条，无连带）；**被删的 16 条原先一条都没被变异撞到**（`pg_gate` 未覆盖 41 → 25，恰好 -16）—— 即它们此前是**无人撞的死判据**，而这正是要消灭的形状。
    - **代价 ①（如实记）：形状没有消失，只是搬了家。** 净 gain 是「**16 条一次性跑过的证据 → 18 条挂在表上、由 `test_closed_grammar_rejects_every_listed_shape` 每次都跑的证据**」，**不是**「少写了 17 条」。换来的是元锁的规模不再随「写了多少种判法」膨胀：15 个语法形状 + 1 个 sink 搬进数据后，|D| 回到失效种类数。
    - **代价 ②：删掉表里的一行 = 那条反例跟着消失，而没有任何东西会响。** 唯一的警报是 `_MIN_VIOLATIONS = 18`（照 `_MIN_TABLE_RAISES = 16` 的先例；下界值 = 重写当刻实测的反例条数，不是拍的）。**表里加一条反例就要把它跟着往上调** —— 不调的话新加的那条日后被删掉照样无声。
    - **代价 ③：现行实现的报错在指错方向 —— 重写顺带修了，证据留档。** 旧实现在两条反例上报的是**真凶之外**的 token：`-tAc 'select 2'` 报 `'-tAc'`、`> /dev/stderr` 报 `'>'`（逐段判、先撞到哪条报哪条）；段表逐 token 比对后报的是真正不符的那一个（`'select 2'` / `'/dev/stderr'`）。§四「**失败信息自解释**」那条纪律在这里有了实证：重写不只降了 |D|，还修了报错质量。16 个原始拒绝点复跑**全部仍红**，且每条都点名真正的违规 token。
    - **重写后的复核（23 条 G 现跑现数，`tests/perf/pg_gate_red_lines.json`）**：`pg_gate` 未覆盖 41 → **25**、空转 0；`route_facts` 未覆盖 44（不变）、**空转 6 → 0**、controls 0 → **6**（② 生效）；`ping` 未覆盖 19（不变）。合计未覆盖 **104 → 88**。**两条硬约束逐条核过**：① 那 7 条策略/sink 行**各由一条变异独占**（493←G-5、499←G-1、506←G-4、512←G-3、516←G-8、520←G-2、523←G-23）；② 「无专属红源的变异」**改前改后是同一批 11 条**（G-6/7/10/11/12/13/14/15/16/17/21，逐条同名 —— 由 `540a202` 的 HEAD 工作树对照得出，不是推断），**即共享红源是改前就有的形态、不是本次引入**；被多条共享的行 13 → 12。
    - **方案 C 落地后的复核（2026-09-16 晚，全套矩阵现跑现数）**：`pg_gate` |D|=49 / |E|=23 / 未覆盖 **26**；`ping` |D|=33 / |E|=14 / 未覆盖 **19**；`route_facts` |D|=**67** / |E|=28 / 未覆盖 **39**。**合计 |D|=149、|E|=65、未覆盖 84、空转 0**（26+19+39）。
      - **88 / 89 / 84 这条漂移要如实记，否则下一次对不上账**：**88** 是上一条复核当下写的**快照**（848 行曾引用它并说「属缺陷 41 的 88 条缺口」）；**89** 是 C 之前的一次现测（|D|=152）—— 与它同期，CI 上的 `test_lock_coverage` 恰好红 3 条，对得上；**84** 是 C 落地后的现测值（|D|=149）。88 与 89 之间没有「谁对谁错」，是 |D| 中途变过（L6 收窄定义那一批改动）而缺口数没跟着重算 —— §四「台账状态行不是事实」的又一例。
      - **C 让缺口净减 5（`route_facts` 44 → 39，合计 89 → 84）**。两个方向同时发生，别只记净数：**减**——旧 L6 的实例（`pymupdf4llm` 的 import 钩子）整条删除，它原本在未覆盖名单里（③ 那格）；**增**——L6 改判据面时新增的入口面静态守卫（T-3）与新的非空负控（T-4）各由一条新变异撞到，把此前无人撞的行变成本次有红源的实例。
      - **判据数 |D| 149 这个口径下，「缺口」的绝对值不说明工作量** —— 缺口随锁文件里的断言数走，不随被守的行为数走（§三 缺陷 41 已有同款声明）。这条数字的用处只有一个：**它是锁自己报出来的，不是人手工对表对出来的。**
      - **名单式「只许减少」落地（2026-09-17，用户裁定 ④）** —— 命题从「全覆盖」换成「**只许减少**」，84 条**不清**。裁定的理由是这条锁自己的实测：**一直红的锁等于没有锁**（本仓的现成例子：生产 PDF 已坏 73 天，而 CI 一直是红的、也是绿的 —— 缺陷 52）。补 84 条变异是几天的工作量，而方向 ① 已经把「新长出来的判别器不许被豁免」原样继承了 ⇒ 换命题**不是**绕过 CI。清单住 `tests/lock_coverage_gaps.py`，按驱动三段 **pg_gate 26 / ping 19 / route_facts 39 = 84**，与 `test_lock_coverage.py` 的参数化同一份驱动名。除双向差集外另加三条机械判据：空理由（复用 `policy_table.empty_reasons`）、**占位语理由**、**孤儿驱动段**（方向 ② 在上一层：驱动没了却留着整段名单，而它不会被参数化跑到 ⇒ 既不红也不绿）。**「占位语」这条是③层字符串代理，落地当场就假红过一次**：只按「出现占位词」判，把一条**正确**理由里的「**占位符**」判成了占位语 —— 那是 Python 格式化占位符这个**领域词**。这类红比漏判更坏：它逼人改写正确的句子去迎合代理，代理就从「底线」退化成「措辞税」。故加长度下限 20 字（实测真理由最短 25 字、占位语都在 10 字内），并把这条假红钉进合成正控。代理的天花板照写：**一长段全是空话的理由照样混得过去**，真正的判据仍是复核。
      - **名单的键是判别器的文本，不是 `文件:行号` —— 这是落地当场被实测逼出来的，不是风格偏好。** `lock_coverage.gap_keys()` 把 `文件:行号` 映射成 `(文件, 判别器源码, 同文本第几处)`。行号是**坐标**，任何一次无关的插行/删行都会让整份名单平移；本仓实测已踩到：`224fd68` 只改了一段 docstring，L6 入口面静态守卫的 assert 就从 473 滑到 482，于是在 `339bea4` 生成的 `route_facts_red_lines.json` **当场陈旧** —— T-3 的红源落到一个不再是判别器的行号上：那条变异被记成**空转**（`vacuous == []` 当场红），同时 482 凭空算成一条「新缺口」（未覆盖 84 → 85）。**同一个形态当天出现两次**（另一次是 `test_the_gap_list_names_only_real_drivers` 拿 `Path.stem` 去比名单里的**路径**键，两边永远不等、判据恒红）—— 「拿剥过后缀的名字去比另一种形态的键」这个坑，`test_every_mutation_driver_has_an_artifact_and_vice_versa` 已经踩过一次，这是第二次。**同文本判别器用出现次序区分**（实测：`assert r.status_code == 400, r.text` 在一份文件里 4 次、`assert await store.ping() is None` 2 次），次序按**行号数值**排，不按 `"文件:行号"` 的字典序（跨过 99→100 时字符串序会把两处对调）。重跑矩阵刷新产物后回到 **26 / 19 / 39 = 84、空转 0**。
      - **元锁自己的新判据一律由合成输入承担，不写「撞元锁的变异」** —— 给 `test_lock_coverage.py` 写驱动就会把本文件的全部 assert 拉进覆盖域，一夜之间多出几十条缺口：那不是守卫，是洪水（递归终止的理由见 `lock_coverage` 模块 docstring）。本轮加的四条合成用例：键在插行下不变 / 同文本按行号次序 / **双向各一负控 + 全对上的正控**（只控一侧会让「恒空」永远绿）/ 占位语（含一条「正确理由里出现领域词不该被误判」的**假红回归**）与孤儿段各一正一负。
      - **原先那条「不设豁免名单」的老规矩**（在任何合法变异下都红不了的判别器 = 死判据，处置只有删掉它）**没有丢，由方向 ① 承担**；两侧的完整记录写在 `tests/lock_coverage.py` 的模块 docstring 里（含「哪一半被谁继承」）。
  - **元锁自身两处错，一处已修（同一次首跑照出）**：① `test_every_mutation_driver_has_an_artifact_and_vice_versa` 拿 `Path.stem` 去 `removesuffix("_red_lines.json")` —— `stem` 早剥掉了 `.json`，**永远删不掉**，两个集合恒不相等、这条锁**恒红**（已修：两边各去**自己的**尾巴。修前 `4 failed`、修后 `3 failed`，剩下三条都是真缺口不是工具坏）。② **产物没有「期望绿」的位置**：`route_facts` 的 6 条「红源唯一性」条目（V2/V4/V7/V10/X-5/I-3）本来就**该是绿的**（它们证的恰恰是「把判据退回去，同一条变异就红了」），却因为红源为空被元锁记成**空转变异**。产物格式要能标出这一类，否则「反证」与「空转」又共用一个信号。
  - **后果（推之前必须知道）**：`build.yml` 的 `build` job 是 `needs: test` —— 元锁红 ⇒ `test` job 红 ⇒ **镜像不构建、不发布**。故这三个红不能当「先记着」推上去。

- **运维注记**：B、A 两轮**尚未部署**。首次部署时，就绪检查门会**第一次**在生产凭据上做验证 —— 若生产 `.env` 与卷里的口令不一致，app 将**起不来**（那是本条修好之后才可能出现的红，不是新的故障面）。
- **判据命令**：`grep -rnE '^[[:space:]]*test:.*pg_isready' docker-compose.prod.yml docker-compose.local.yml`（应为 0，出现即在回退 —— **注意锚在 `test:` 字段上**：上面那四条注释里**故意**写着 `pg_isready` 这三个字（那段就是在讲它为什么不能用），只按词 grep 会把这四条解释文字当成回退，而「判据必须能区分机制与被删机制的描述」），`python tests/perf/pg_gate_mutations.py`（G-1～G-23，末行给结论；**无 docker 的环境会拒跑并打印原因，那不是通过**）、`python tests/perf/ping_mutations.py`、`python -m pytest tests/test_pg_gate.py tests/test_compose_model.py tests/test_storage_ping.py tests/test_health_ready.py tests/test_health_probe_targets.py -q -rs`；**历史现场复现**（已不再成立，留作对照）：`docker logs --tail 20 character-distill-postgres-1` 曾持续出现 `FATAL: role "-d" does not exist`，`docker exec character-distill-postgres-1 sh -c 'pg_isready -U  -d ; echo exit=$?'` 曾输出 `exit=0`。

**42. 判据读的是「看起来像」而不是事实 —— 同一形态四处显形：`Form(...)` 的 AST 形状 / 并列身份按模块名 / 按 `__file__` 字符串 / 正文通道按字段名** —— 状态：**已修**（第 1 步 `7009d77` + `3e2670d` + `2c9fee9`，第 3 步 `fbb9066`，第 3a 步 `efa36a6`，第 3b 步 `c959553`，结案 = 本条目所在提交，2026-09-15）
- **病灶的通用形态**：判据与被判事实之间**隔着的东西**，就是能骗人的地方。四处实例「隔的东西」各不同、失效方式**逐字一样**：**静默漏过** —— 该红的没红，且没有任何东西报错。已升格为 §四 通用判据（「判据不得建立在『看起来像』之上」），本条是它的立项来源。
- **第 4 处（结案这一步照出的）**：L5 迁上事实层后，**「哪个字段是正文通道」仍按字段名判断**（`_PAYLOAD_FIELD = "file"`）。把 `web/routers/voice.py` 的 `file: UploadFile = File(...)` 改成 `file: str = Form(...)` 之后，L5 与它的负控**全绿**，而那条 op 的正文通道事实上已经换成一条未登记的无界文本字段 —— 按名字判断身份，与前三处同一个病。V9/V10 是这一对：「先把文件字段改成同名的文本字段」（红）／「判据退回按名字排除、保留同一改法」（绿），红源由此唯一地钉在「判据读 schema」上。
- **第 1 步：抽出「路由框架事实」层** `tests/route_facts.py` —— 只回答「框架认为这条路由长什么样」，无任何本仓业务知识（哪些字段允许、哪些端点豁免是各把锁自己的策略）。取数入口：
  - `app()` **唯一**入口（`importlib.import_module("server").app`）；`enumerate_routes()` 从**模块级 router 对象**枚举（`pkgutil.iter_modules` 派生 + 追加入口模块，**不写手工清单** —— 手工名单就是守卫与被守对象之间的第二份副本，新增模块时锁不会红），键 `(path_format, method)`；`openapi()` 走 `get_openapi`，默认缓存、显式传 `routes=` 时**不写缓存**；`census_diff()` **只算不断言**（「两份账本都对得上」这件事本身要被测，而它只有在两份独立时才有信息量）；`form_operations(spec=None)` 给出「声明了表单 content 的 op」集合（与 `form_fields` 共用同一份 content-type 判定 `_form_media` —— 同一判定两份实现会各自漂移，故只留一份）；`form_fields(path, method)` 读 multipart + urlencoded **两分支**并解 `$ref`，operation 不存在时 **raise 不返回空**（空字典是「这条路由没有表单字段」的合法答案，两者不能共用返回值）；`is_file_field(schema)`；`injected_params(route, dep)` 判据是**依赖对象本身**（`d.call is dep`），与位置参数 / keyword-only / `Annotated` 写法无关。
  - **为什么不遍历 `app.routes`**：部分框架版本把 include 进来的路由包成**没有 `.routes` 的对象**，按 `isinstance(APIRoute)` 筛只能拿到很小一部分（实测 9 / 总数 228）**且不抛异常**，版本一换数量还变。故从**模块**枚举 + 用 OpenAPI 文档做第二份独立账本对账。**这条误写的可见性依赖 fastapi 版本**（本地 0.133.1 下 include 是内联的，228 == 228，**不红**；上锁版 0.141.1 才是 9 vs 228）—— 变异 F-3 因此单独一档，本地结果**不能**当作「这条判据没问题」的证据。
  - **入口模块的身份只能有一个**：`web/` 无 `__init__.py`（namespace package），conftest 把 `web/` 插进 `sys.path` ⇒ `server` 与 `web.server` 是**两个模块对象**（同一文件被执行两次、两个 app），`is` 与属性比对**静默为假**。
- **第 1 步的两条独立核对面**（`tests/test_route_facts.py`）：A **合成 app** —— 同一语义的**每一种等价写法**各一条路由（表单四种写法、依赖三种写法 + 装饰器形态），期望值写死在本文件里、与仓库路由变动解耦；B **真实 app 对账** —— `census_diff` **双向**都必须为空（模块枚举与文档是两份独立账本，两个方向的盲区病根不同）。外加**非空负控**（探测器塌成空集时「所有 X 都满足 P」恒真）。身份判据落在 `os.path.samefile` —— **文件系统身份**，不是模块名（②层），也不是 `__file__` 字符串相等（**同样是②层**：盘符大小写 / 分隔符 / 符号链接都能骗过；变异 X-5 把判据退回字符串相等后别名装载**全绿**，红源由此唯一地钉在那次改动上）。
- **第 3 步：两把锁迁上来**（`test_text_failure_messages.py` 的 L5、`test_auth_param_used.py`）—— 从 AST 形状层落到框架账本层。**两处「换不动」如实留档，不假装覆盖**：① L5 的**策略表**是③层（**意图不是事实**；失效方向是**响亮误伤**）—— 注意结案这一步澄清了**这不是整条 L5 的性质**：同一条锁里「文件 / 非文件」那一侧当时仍是按名字判的**静默**半边，已由 V9/V10 照出并改按 schema 判（见 L5 条与缺陷 42 第 4 处）；② auth 锁的「**用没用**」半边仍是②层，`route_facts` 无此能力 —— 注入 + 只 `str(user)` 不做归属校验，迁移前后**都绿**，**与缺陷 25 同形**。**这是本锁的边界，不是遗漏**：「user 是否被用于**归属校验**」的真值在 **SQL 谓词**那一层（缺陷 25 的解法），框架的账本只记「注入了什么」、不记「拿它做了什么」，路由层没有这个事实；要覆盖只能下沉到谓词层或上运行时探针。**识别**半边则落到了 0 层（依赖树 `d.call is get_current_user`，`Annotated[...]` 从静默漏过变成红）。豁免名单键从 `file:func` 改为 `(path, method)`：代码组织事实 → **框架契约事实**（文件位置与函数名可以改，路由路径是对外契约；`/api/settings/config` 因旧锁按目录 glob 而漏，新键天然覆盖，加进名单时写明它是「纯登录门、不做归属校验」）。
- **第 3a/3b 步：把「哪些 op 声明了表单 content」这件判定收回事实层一份**（`efa36a6` + 本提交，2026-09-15）。第 3 步迁完后，L5 的**覆盖面仍是它自己重算的**：读 `route_facts` 的**私有常量** `_FORM_CONTENT_TYPES` / `_METHODS`（跨层读私有），且「两个 content-type 分支 + 只认方法名」这段判定与 `form_fields` 里那份**各写了一遍**。两份实现会各自漂移，而漂移**不报错** —— 只让两处对同一条 op 给出不同答案（本形态的又一例：**同一判定两份实现**，与「手工清单」「台账状态行」同族，都是守卫与被守对象之间多隔了一层）。**第 3a 步**在事实层公开 `form_operations(spec=None)`，并把 content-type 判定收敛成 `_form_media(op)`（`form_operations` 与 `form_fields` 共用同一份；`form_fields` 行为不变，含「op 不存在就 raise」那条契约）；**第 3b 步**把 L5 的私有常量引用与那份重复实现整段删掉，改调 `route_facts.form_operations()`。**3b 零新增能力**：`form_operations` 是 3a 就位的，`route_facts.py` 一字未动（既定规则：若缺能力，**停下报，不顺手加**）。
  - **合成 app 侧的新锁（3a）**：`form_operations(_SYN_SPEC)` 必须**恰等于**写死在本文件里的合成表单路由集合 —— **一条**断言判两个方向（少一条 = 只认了某个 content-type 分支；多一条 = 根本没在看 content-type）。另写一条「且不含 `/synth-deps/*` 那四条 GET」的断言**故意不做**：它只能在相等断言已经失败时失败，是**重复红源、零增量**，而失败文案已把两侧差集分开点名（审计裁定：**多一条恒随另一条红的断言不是更严，是噪声** —— 红的时候人还要先判断哪条是真的）。
  - **变异 F-10/F-11/F-12**（只打 `form_operations` 自己的判据行，不碰 `_form_media` —— 那是 `form_fields` 共用的尺，F-5 已经打过）：F-10 漏 urlencoded 分支 → 合成用例红（一次丢 4 条纯 Form 路由，不是 1 条）；F-11 恒返回空集 → **合成用例与真实负控两条齐红**；F-12 不看 content-type → 合成用例红（混入的正是 `/synth-deps/*` 那四条 GET）。**F-11 是 §四「变异红了 ≠ 判别器起作用」第三个变体的立项案例**（该条的 marker 元组支持即为此而加：只验一条就等于容忍「只红一半」的变异体）。
  - **四条表单 op 的现测结果（2026-09-15 现跑现数）**：4 条 —— `POST /api/text/upload`（`file` + 已登记元数据 title/description/text_type）、`POST /api/voice/asr`（**仅** `file`，理想形态）、`POST /api/voice/ref-audio/upload`（`file` + card_id/ref_text）、`POST /api/voice/upload`（`file` + name）。四条**全部合规**（多出字段集合逐条为空、无陈旧元数据条目、每条都带 `file`）。
- **共享层的第二块：`tests/policy_table.py`（结案这一步抽出；当时那个名字带「路由」字样，缺陷 41 Commit 2 `5afecbf` 更名 —— 见缺陷 41「两个共享层的登记」）** —— L5 的 `_FORM_METADATA` 与 auth 锁的 `ALLOWLIST` 是**同一种东西**（「人声明的豁免 + 理由」），而两把锁**各写了一遍**「陈旧条目 / 空理由 / 现场未登记」这三套差集与判空。两份实现会各自漂移，且漂移**不报错** —— 与 3a/3b 收掉的是同一个病（同一判定两份实现），只是换了个住址。本层只放与业务无关的机械操作（`empty_reasons` / `stale_keys` / `unexpected`，表统一是扁平 `dict[Hashable, str]`：键 = 被豁免的东西，值 = 理由），**模块内不出现任何路径、字段名、依赖名** —— 与 `route_facts` 同一条原则（内容归各把锁，机械操作归这一层）。变异组 P 打本层自己的判据行：不 strip / `stale_keys` 参数方向写反 / `unexpected` 恒返回空集。**第三、第四个使用方**（更名之后加的）：`tests/test_pg_gate.py`（`_NOT_A_DB_CONSUMER`，服务有没有登记的豁免表）与 `tests/test_policy_table.py`（本层自己的锁）。
- **隔离判据 ① ② ③（已核，不是口头保证）**：① `git diff --stat` 无 `core/` `web/` `storage/` `adapters/` —— **生产代码零改动**；② `tests/route_facts.py` **一字不动** —— 本步是「让两把锁去用它」不是「改它来适配两把锁」；③ 两把锁**互不依赖** —— 移走任一把，另一把照常红**同一条**断言（红源带 marker 比对，不是只看「有没有红」），且两把锁交错调用后本层输出**指纹不变**。**关于共用缓存**：本层唯一的模块级可变状态是那格 spec 缓存 `_cache`，内容只由 app 决定、**无 setter**、显式传 `routes=` 时不写缓存、`enumerate_routes` 每次现算 ⇒ **不存在「一方污染、另一方读到」的通道**；这条由 `test_shared_layer_output_is_a_pure_function_of_the_app` 钉住（钉的是**可观测性质**，不是模块全局的形状 —— 数「有几个 dict」是代理指标）。**第 3b 步的连带对账（两处锚必须跟着改，否则变异脚本自己失效）**：① V7 的第 1 条锚原打在 L5 自备的 `_form_operations()` 函数体上，该函数随 3b 删除后锚会 `count == 0` —— `_apply` 当场 assert，形态是「变异没生效」的假绿；改锚到**调用点**（把 `route_facts.form_operations()` 换成写死的 `{("/api/text/upload", "post")}`，即迁移前那条硬编码 op 的等价形态）。**结案这一步这条锚又搬了一次**：L5 改成按 schema 判之后，旧形态＝「覆盖面写死一条 op」+「按字段名排除」，两半合起来退回才等价，故锚改到 `_observed_non_file_fields()` 的函数体上（见 `_L5_OLD_SHAPE` 上方注释）。② I-3 原先调 `L5._form_operations()`，改为调 **L5 自己的扫描入口**，**不能**直连 `route_facts.form_operations()` —— 直连绕开了 L5，这一步就不再是「L5 跑过之后本层输出有没有变」的检验，而是变成了「事实层自己跑一遍有没有变」。**结案这一步 L5 的入口从一条拆成三条**（主判据 / 策略表 / 负控），I-3 相应地调其中两条走完整条扫描路径。
- **变异矩阵入库**：`tests/perf/route_facts_mutations.py` —— 六组共 39 条（F/R/X 打事实层自己、V 打两把锁的判据、I 打隔离性、P 打策略表校验层），**不带参数即跑全部组**（`--group` 默认 `FRVXIP`：文档称本脚本为「全矩阵」，默认值必须与这个说法一致，否则「不带参数跑一遍」少跑一组却看不出来）；**X 组的别名路径跨平台** —— 用 `web/../web/server.py`（两个平台都「字符串不同、`samefile` 为真」）而不是翻盘符大小写（后者只在 Windows 成立：Linux 上首字符是 `/`，翻完不变，X-3 退化成 X-4、X-5 失去前提，而结论行照样打印「全部符合预期」），跑 X 组前由 `_alias_gate()` 断言该前提，不成立即拒跑；逐条还原并核对 sha256；**先验基线不绿就拒跑**（两种成因共用一个红时说不清红源）、**锚点非恰一命中即 assert**（锚点漂移会静默变成「变异没生效」的假绿）、`--with-container` 才跑 F-3。**为什么必须入库**：本条的每一条数字原先骑在仓外 `D:/Temp/*.py` 上 —— 追不到，三个月后复现不了（§四「文档引用的数字，其产数脚本与原始产物也要入库」）。**一处如实交代**：迁移前那两条「旧锁在同一变异下绿」的形态**没有留在本脚本里**（旧版锁只存在于迁移那一刻的工作树，抄回仓里当靶子是添加 over 删除）；配方留在一旁（V1 的 `Annotated` 注入变异），配上 `git show <迁移前 sha>:tests/test_auth_param_used.py` 即可复原，**V4/V7 是留在树里的等价形态**（把判据退回旧的 AST 形状，同一变异仍绿）。
- **判据命令**：`python tests/perf/route_facts_mutations.py`（全矩阵，末行给结论；`--group V` / `--group I` / `--group P` 单跑）、`python -m pytest tests/test_route_facts.py tests/test_policy_table.py tests/test_auth_param_used.py tests/test_text_failure_messages.py -q`、`git diff --stat`（应只有 `tests/` 下的锁与脚本 + `AGENTS.md`，无 `web/` `core/` `storage/` `adapters/` —— 本缺陷是锁的缺陷，不是业务缺陷）
- **收口一处：策略层理由判空改为按类型判定（Commit A）** —— `empty_reasons` 原先走「先字符串化再 strip」，于是理由写成 `None` 时被变成 `"None"`、判为**有内容**：把 `/api/voice/ref-audio/upload` 的 `ref_text` 理由改成 `None`，L5 **全绿**。这是本条病灶的第五次显形（判的是「转成字符串之后长不长」，不是「理由在不在」），只是长在了策略层自己身上。现在先判类型再判内容，非 `str` 一律算空；变异 P-4（把字符串化请回来 → `None` 用例红）与 P-5（只判类型、不 strip → 纯空白理由用例红）各打一个新分支。**原 P-1 退役**：它的变异是「`str()` 之后丢掉 strip」，而那段 `str()` 已删，新写法下与 P-5 逐字同形 —— 编号留空位不复用，免得台账里的旧编号改指别的事。

**43. `web/test_spa_fallback.py` 在 `tests/` 之外，pytest 从不收集它** —— 状态：**已修（`1060495`，2026-09-17）**（2026-09-15 缺陷 42 结案时顺带普查发现）
- 实测：该文件有 5 条 `def test_*`；`pytest.ini` 的 `testpaths = tests` ⇒ `pytest --collect-only` 里它 **0 命中**，全仓无任何 workflow / 脚本引用过它（`grep` 全仓 yml/ini/cfg/toml/sh 零命中）。
- 形态：该文件自带 `__main__` 运行器（`python web/test_spa_fallback.py` 才是它的既定用法），所以它不是「写坏的测试」，是**放错位置的测试** —— 覆盖的命题（静态资源优先于 catch-all / 中文文件名 / SPA 回退 / `/api/*` 不被回退吃掉 / 路径穿越被挡）全都有价值，只是**没有任何自动化在跑它**。属 §四 那条「一条恒 skip 的锁和没有锁是一回事」的邻居：**不被收集的测试与没有测试是一回事**。
- **用户裁定（2026-09-17）：不移动文件，加判据。** 理由：`_STATIC_DIR` 是 `web/server.py` 的模块级常量，而这个文件 `from server import app, _STATIC_DIR` —— **移文件要干净就得改生产代码**，为整理测试位置改生产代码是本末倒置。判据写在**那一类**上（「有文件长得像测试但不会被跑」），不写在这一个文件上。
- **判据的落点（`1060495`）**：新增 `tests/test_collection_surface_lock.py` —— 全仓「像个测试」的文件集合必须都在收集面内，差集**两向都查**（现场有而名单没有 → 点名；名单有而现场没有 → 逼删）。机械校验交给既有的 `tests/policy_table.py`（「人声明的豁免表」的单一出口），**没有另造一套**；豁免表 `_OUT_OF_SURFACE` 只有一条（本文件），理由写在表里。
- **两个事实都从 `pytestconfig` 现读，一个都不写死**：收集面 = `testpaths` 声明的目录树；「像个测试」= 文件名匹配 `python_files` **且**模块体里有匹配 `python_functions` / `python_classes` 的可收集条目（匹配规则照抄 `PyobjMixin._matches_prefix_or_glob_option`：先 `startswith`，含通配符才 `fnmatch` —— `python_functions` 的默认值是 `["test"]` 前缀，**不是** `test*`，拿 fnmatch 套会一个都不匹配）。
- **两个谓词都承重（四条合成输入各钉一个方向，全部实测出来的真反例）**：
  - `web/server.py` 里 `async def test_gptsovits_connection` / `test_funasr_connection` 与 `web/routers/auth.py` 的 `test_embedding` 是**路由处理器**，只有函数名对得上 ⇒ 光看函数名会逼人去登记一堆路由；
  - `scripts/test_distill.py`、`scripts/test_search_api.py`、`tests/test_chat.py` 一类是吃 `sys.argv` / 带 `main()` 的**手工脚本**，只有文件名对得上 ⇒ 光看文件名会凭空造缺口；
  - `tests/test_admin_tasks_api.py` 一类**整份只有 `class Test*`**（面内共 30+ 份）⇒ 只查模块级函数的普查会漏掉它们 —— **假阴性比假阳性坏，那是真的失守**；
  - 嵌在别的函数里的 `def test_*` 不可收集 ⇒ 收进普查是凭空造缺口。
- **遍历方式（已被 2026-09-21 的改动取代，如实记）**：`1060495` 时本锁用 `os.walk` **原地剪枝**，理由是「结果同、代价差一个数量级以上」（`os.walk` 剪枝 vs `rglob` 走完全树再筛；实测值与日期、以及为什么这类比值不能当指标，原写在 `_walk` 的 docstring 里）。**那个理由只对「怎么遍历目录树」成立，前提本身就错了** —— 目录树里躺着的并不都是本仓代码，`.claude/worktrees/` 下每个兄弟 worktree 都带着完整一份仓库副本。故 2026-09-21 把两条 census 锁的取文件方式一起换成问 `git`（`tests/repo_files.py::repo_py`），**`_walk` 与 `_PRUNED_DIRS` 已删**，本条目的性能取舍随之作废。来由与验收见 §三之二 D。既有那几把锁（扫 `core/` / `web/` 等**具名子目录**的）**仍不改**：它们不从仓库根起扫，兄弟 worktree 的副本进不了它们的面。
- **变异验证（两臂，全量已还原 —— `git status` 只剩新文件）**：
  - 臂 ①：造 `web/test_zz_probe.py`（含 `def test_*`、在收集面外）→ 只有「现场有、名单没有」那条红并点名该文件，另外四条不动（证明是一条独立判据）。
  - 臂 ②：`git mv web/test_spa_fallback.py tests/` → 该方向立刻变绿，而「名单只许减少」那条红并点名要删的行；**把那行删掉后五条全绿** —— 这正是验收里「挪回收集面 → 判据应变绿」的落点。判据不替你宣布「已修」，它只把陈旧的那一行指出来（两向表的既有语义，同 `lock_coverage_gaps` 的「名单只许减少」）。
- **另补一条自证**：普查必须扫到本文件自己（`test_the_census_finds_the_lock_itself`）—— 否则「整片扫不到」（`testpaths` 读空、prune 表误伤 `tests/`、AST 全解析失败）会让两条判据**恒绿**。自证不靠魔法数字，本文件永远存在、永远叫 `test_*.py`、永远有 `def test_*`。
- **这条锁看不见的漂移（如实记，不假装覆盖）**：① CI 打的是 `pytest tests/`（显式路径让 `testpaths` 不生效），今天与 `testpaths` 指向同一个目录，将来若改成 `pytest 别的目录/`，本判据看不见；② **逐案**的排除面（`--ignore` / 嵌套 `collect_ignore`）不在判据内 —— 本仓今天一处都没有（`git grep 'collect_ignore'` 零命中），真出现了它会以「文件在目录里却没被跑」的形态绕过。都不提前造机制。
- **同源普查的另一族（只记不修，本条目射程外）**：`tests/` 下有 **9 份**名字匹配 `python_files` 却在 pytest 下产出 **0 条** 的文件（`test_chat.py` / `test_connection.py` / `test_distill.py` / `test_distill_progress.py` / `test_following_api.py` / `test_integration.py` / `test_migrate_sqlite_to_pg.py` / `test_rag.py` / `smoke_test.py`），`scripts/` 下另有 2 份（`test_distill.py` / `test_search_api.py`）。它们是「带 `main()` + `if __name__ == "__main__"` 的手工脚本」，**被收集但不产出条目** —— 与本体是同一类病的另一个住址（「像个测试却不跑」），但形态与处置都不同（那是命名问题，不是位置问题），按「发现新问题只记不修」留在这里。
- 另一处形态（记下以便裁定）：`_setup()` 在**模块级**执行（import 时即往 `_STATIC_DIR` 写测试文件）—— 一旦将来有人 import 它，就会在收集期产生副作用。今天不可达，因为没人 import。**这也是不选「把 `web/` 加进 `testpaths`」这条路的理由**：那样收集期就会写进生产的静态目录，收集中途失败时 `_teardown()` 不会跑到，留下 `assets/x.js` 与 `中文.png`。
- 修法（待裁）：把文件移进 `tests/`（需处理 `web/` 的 `sys.path` 与 `_STATIC_DIR` 写入的位置）或加进 `testpaths`；两者都要顺带处理模块级 `_setup()`。**本轮不动**（缺陷 42 的命题是锁的判据，不是测试布局）。

**44. 镜像里没有 `onnxruntime`，而 PDF 解析在「包 import 期」就拉它 ⇒ 生产上「每一份」PDF 上传都失败** —— 状态：**已修**（2026-09-16，方案 C）
- **原标题（保留以便对上旧账）**：`44. 本地 app 镜像缺 onnxruntime，容器内 L3 坏 PDF 用例必红` —— 原状态**记账（不修）**（2026-09-15 缺陷 42 结案跑容器腿时发现）。**原标题把生产故障写成了测试问题**，射程订正见下一条。
- **射程订正（2026-09-16，本条最要紧的结论）**：不是「L3 那条用例必红」，是**生产功能故障**。`_extract_pdf` 里那句 `import pymupdf4llm` 在**包 import 期无条件**拉进 `onnxruntime`（链见下），镜像里那份又被 `Dockerfile:28` 主动卸掉 ⇒ **合法 PDF、非法 PDF、任何上传**都先撞上 `ModuleNotFoundError`，且**无人接住** → 500。**射程 = 全部生产、两个区**：`build.yml` 只从 `context: .` 建**一个** `character-distill-app` 镜像（同一个 Dockerfile，推 ghcr + 阿里云镜像站），两区的 `docker-compose.prod.yml` 拉的是同一个 `character-distill-app:${APP_IMAGE_TAG}` ⇒ 同一份镜像、同一条链，没有「SZ 好 SG 坏」这种可能。旧条目把它记成「本地镜像 / 测试腿」的问题，是把**观察到的第一个症状**当成了射程。
- **起于 2026-06-29，不是 06-27**：`86f8c814`（06-27）只是**卸掉 onnxruntime** 的那一步，它自己不成故障（那时没人拉它）；链要闭合得等上游把 `pymupdf4llm` 发到会拉 `pymupdf_layout` 的版本。**PyPI 实测**：`pymupdf4llm 1.28.0` 上传 `2026-06-29T09:05:11Z`（前一个 `1.27.2.3` 是 `2026-04-24`，它**不 import onnxruntime** —— 这正是本条末尾「旧结论为何错」那一格的由来）。而当时 `requirements.txt` 还是**不带钉的下界**（`pymupdf4llm>=0.0.10`；钉版本是 09-15 的 `53bed63`）⇒ **06-29 之后构建的每一个镜像**都解析到 1.28.x、都带着这条链。**06-27～06-29 之间是「卸了个用不上的包」，06-29 起才是故障。**
- **修法（用户裁定：方案 C，2026-09-16）**：`_extract_pdf` 从 `pymupdf4llm.to_markdown()` 换成 `pymupdf` 的文本层 `page.get_text()` —— **不再经过任何解析前端，链就不存在了**。方案 ①（去掉 `Dockerfile:28` 的卸载）与 ②（把 `pymupdf4llm` 降版本）都**不采**：① 等于拿镜像体积换一条我们**明确不用**的能力（`_extract_pdf` 的 docstring 自己写着 Does NOT OCR scanned PDFs），② 等于回退缺陷 40 刚钉的锁。**也不采**「接住 `ImportError` 兑现 400」那一条 —— 那是让一条已经坏的路由学会好好报错，不恢复能力。
- **隔离**：只动 `core/text_manager.py::_extract_pdf` 的**函数体与 docstring**；DOCX / TXT / `upload_text_from_file` 零改动；签名与 `_MSG` 逐字不变；不碰 `ALLOWED_EXTENSIONS` / 路由 / Dockerfile。依赖侧（`pymupdf4llm` → `pymupdf`）单独一次提交。
- **验收：V1 是本次唯一的真证据（容器内现跑）**。本机 `.venv` 仍装着 `pymupdf4llm`，本机的绿**证明不了**「镜像里没有它也能跑」。容器内挂当前源码跑真的 `_extract_pdf`：
  - 合法 PDF（3 页）→ 取到 66 字符、内容正确；**同一份 PDF** 走镜像**自带**的旧代码 → `ModuleNotFoundError: No module named 'onnxruntime'`（**反证**：C 修的是真实故障，不是推测出来的故障）
  - 非法字节 → `pdf_open_failed`（库原文含服务器路径，只进日志，缺陷 39 的行为保住）；空白文本层 → `pdf_no_text`；2001 页 → `pdf_page_limit`（带实际页数）
- **代价（唯一一条，实测，不是推断）**：输出是**纯文本不是 Markdown**，不再产 `#` 标题 ⇒ 前端 `detectChapters` 落到「独占一行的 1–4 位数字」那条规则，**页码会被切成章节**。实测入库：`ev:pagenum-chapter-rules`（`tests/perf/pagenum_chapter_probe.py` + `docs/evidence/pagenum-chapter-rules.json`，`58be15d`）。**不写成「影响很小」**：带页码、且没有中文章节词与 `Chapter N` 的 PDF 会多出一批以页码为界的假章节 —— 这是 C 引入的**真回归**，只是比「整条路由 100% 失败」小。
- **残余状态（照记，别让人以为 onnxruntime 这件事清了 —— 也别让人以为它可用）**：C 之后 `onnxruntime==1.30.0` **仍在 `requirements.txt` 里**（经 `chromadb` 进来），`Dockerfile:28` 也**仍然**卸它。两者都没动，也**不该**在这次动 —— C 拆的只是「文本解析」这条链，chromadb 那条链是另一件事（镜像内实测 `chromadb` 在**没有 onnxruntime 的情况下** import 正常，故它不是本故障的一部分）。**「锁里有它」≠「它可用」**：镜像里**仍然没有 onnxruntime**，任何代码只要能 import 到它、今天就还是一条坏路 —— 现在只是**没有任何生产路径需要它了**。将来谁要在 PDF 或向量这条路上用它，先回答「镜像里为什么卸了它、什么变了可以让它回来」。
- **修完仍不能发布（推之前必须知道）**：`build.yml` 的 `build` job 是 `needs: test`，而元锁 `tests/test_lock_coverage.py` 仍有 **3 条红**（缺陷 41 的覆盖缺口，本轮按裁定不动）⇒ `test` job 红 ⇒ **镜像不构建、不发布**。C 的代码已就位，发布路径另议（记账见缺陷 52）。**这条状态行已过期，见下条「已发布」—— 保留原文是因为它记录了当时的阻塞形状。**
- **已发布（2026-09-17，名单式落地后）** —— 上面那条阻塞由「只许减少」解开：`d781d86` 推上去后 CI 三个 job 全绿（`sentinel` / `gate` / `build`，run `35108664654`），镜像 tag = `d781d860870ef3a06404471432f1ccb141b80ac3`，deploy run `35110011977` 以 `build_run_id=35108664654` 显式 pin 部署 `both`，SZ/SG 两个 app 容器都起来且 healthy、`docker inspect` 的 `Config.Image` 都是这个 tag。**两台各跑一次合法 PDF（与 V1 同形：容器内现造 3 页最小合法 PDF → 真调 `_extract_pdf`）**：两台都 `TYPE=str / LEN=117`、「Page 1..3 of the production verification.」逐字正确，临时文件跑完即删。同一次实测里 `import pymupdf4llm` 在镜像内已是 `ModuleNotFoundError: No module named 'pymupdf4llm'`（包本身已不在锁里，不是「装了但缺 onnxruntime」），而 `chromadb` 在**没有 onnxruntime 的镜像里** import 正常（`1.5.9`）—— 与「chromadb 不是本故障的一部分」这条旧结论一致。**证据的边界要写清**：这一次验的是 `_extract_pdf`（缺陷 44 坏的就是这一层，也是 V1 的验收形状），**没有**走完整 HTTP 上传（那要带凭据 + 往库里写一条记录），所以「路由层端到端」这一格今天仍无生产实测。
- 事实（原样保留）：镜像内 `python -c "import pymupdf4llm"` → `ModuleNotFoundError: No module named 'onnxruntime'`（`pymupdf4llm/ocr/analyze_page.py` 顶层 import）。链路：`core/text_manager.py` 的 `_extract_pdf` 里 `import pymupdf4llm` → 该异常无人接住 → **坏 PDF 上传返回 500 而非 400**，`tests/test_text_failure_messages.py::test_l3_bad_pdf_screens_table_wording_and_logs_the_original` 在容器内必红；变异驱动的先验基线门因此拒跑。
- 证据命令：`docker run --rm -v <repo>:/app -w /app --entrypoint python character-distill-app:latest -c "import pymupdf4llm"`；或容器内 `pytest tests/test_text_failure_messages.py -q` → `assert 500 == 400`，栈底 `ModuleNotFoundError: No module named 'onnxruntime'`。
- **根因（2026-09-15 订正，替换原先的「重建镜像」结论）**：不是「镜像比锁旧」，是**镜像构建时主动把 onnxruntime 卸掉**。冲突链三段逐段核过：
  - `Dockerfile:28` 主动 `pip uninstall -y onnxruntime kubernetes 2>/dev/null || true`，由 `86f8c814`（2026-06-27）引入 —— 比钉锁那一步早 525 个 commit，**重建镜像不会改变它**。
  - `53bed63`（2026-09-15，缺陷 40 的「依赖按锁安装」）把 `pymupdf4llm` 钉到 `1.28.2`。该版本 `Requires-Dist: pymupdf_layout==1.28.2`，`pymupdf_layout` 的 `Requires-Dist` 里有 `onnxruntime` —— 装得上，随后被上面那句卸掉。
  - `.venv/Lib/site-packages/pymupdf4llm/ocr/analyze_page.py:5` 是顶层 `import onnxruntime as ort`，`import pymupdf4llm` 即触发。
  - 核过的命令：`git log -1 -S "pip uninstall -y onnxruntime" -- Dockerfile`（→ `86f8c814`）、`git log -1 -S "pymupdf4llm==1.28.2" -- requirements.txt`（→ `53bed63`）、`cat .venv/Lib/site-packages/pymupdf_layout-*.dist-info/METADATA | grep Requires-Dist`。
- **修法（三选一，待裁）**：① 去掉 `Dockerfile:28` 里的 `onnxruntime`（先核它当初为何被卸；若为镜像体积，等于拿体积换掉整条 PDF 链）；② 把 `pymupdf4llm` 降到不依赖 onnxruntime 的版本（等于回退缺陷 40 刚钉的锁）；③ 让 `core/text_manager.py:278` 那句 `import pymupdf4llm` 接住 `ImportError` 并兑现对外的 400 —— 只有这条同时兜住容器「缺包」与本机「被 System32 同名 DLL 抢先」两种症状，且不动依赖。
- **订正声明**：`e828fd3` 与 `7c07f36` 里写的「修法是重建镜像，不是改锁」由本条订正 —— 实测重建无效。
- **非缺陷 42 引入（已核）**：把树退到 `c959553`（本轮全部改动之前）复跑同一文件，**同一条同样红**（1 failed, 21 passed）。
- 附：镜像里**也没有 pytest**（`python -m pytest` → `No module named pytest`），跑容器腿得临时 `pip install -q pytest pytest-asyncio onnxruntime`。两者同源：镜像只装运行时，而「判据要求容器内复跑」这个用法还要开发依赖。
- **Windows 形态（2026-09-15 收口时补，同一根因的另一端）**：同一条导入链在**本机**也能踩到 —— 锁里 `pymupdf4llm==1.28.2` → `pymupdf-layout` 1.28.2 → 顶层 `import onnxruntime`。本机 `C:\Windows\System32\onnxruntime.dll` 是**微软随 Windows 装的 ONNX Runtime 1.17.260613**（`FileVersion 1.17.260613-0100.1.os-germanium…`），`System32` 在 DLL 搜索路径里压过 pip 包内的 1.30.0，ABI 不符 → **模块初始化时 access violation**，`pytest tests -q` 整个进程被杀（`exit=139`，无红无绿）。
  - 证据命令：`.venv/Scripts/python.exe -m pytest tests/test_text_failure_messages.py::test_l3_bad_pdf_screens_table_wording_and_logs_the_original -q` → `Windows fatal exception: access violation`，栈底 `onnxruntime/capi/_pybind_state.py:32`。
  - 与容器那一端的差别只在症状：容器内是**缺包**（`ModuleNotFoundError`，一条用例红），本机是**包在、被 System32 的同名 DLL 抢先**（进程级崩）。两者都源自 `_extract_pdf` 里那句顶层 `import pymupdf4llm` 无人接住。
- **订正一条旧结论**：此前「本机 Windows 装了 onnxruntime 故不复现」**是错的**。那台环境（harness venv）里 `pymupdf4llm` 是 1.27.2.3，**根本不 import onnxruntime**，所以旧基线 `pytest tests -q` 全绿里那条 L3 是**环境恰好绕开了**，不是「代码没问题」。换到锁里的 1.28.2 立刻崩 —— 与缺陷 42 同形：拿一个与锁不一致的环境跑出的绿当证据，而没有任何东西说它不一致。
- **本机全量测试的当前做法**：`--deselect tests/test_text_failure_messages.py::test_l3_bad_pdf_screens_table_wording_and_logs_the_original`，结果是 `1045 passed, 62 skipped, 1 deselected`。**这不是绿**，是「这条在本机跑不了」—— 上机复跑只能进容器。
- **2026-09-16 更新（缺陷 41 · ④ 动了 import 的位置，见缺陷 49）**：`_extract_pdf` 那句 `import pymupdf4llm` 已从**函数入口**挪到 `pymupdf.open()` 与页数校验**都通过之后**。于是**坏 PDF** 的路径不再 import 它，容器内「缺包 → 500」与**本机「被 System32 抢先 → 整个进程崩」**两条都**在坏 PDF 用例上**消掉了（本机 `test_text_failure_messages.py` 由「import 期即杀进程」变为可跑，27 passed）。**合法 PDF 那条没消**（本机仍崩 —— 缺陷 50），且**容器侧未在本机验证**。
  - **订正（2026-09-16 晚，方案 C 落地）：这一步是「报错改善」，不是「修好」—— 两者当时被记成了同一件事。** 挪 import 的位置只让**坏** PDF 不再付出加载代价；**合法** PDF 照旧在 import 期拉起 onnxruntime，容器侧照旧缺包、本机照旧崩。**这一步没有恢复任何能力**：改前改后，「上传一份能解析的 PDF」在两个环境里都是坏的（容器 500 / 本机 access violation）。真正的修好是**方案 C**（缺陷 44）—— 把 `pymupdf4llm` 整个从这条路上拿掉。**判据（写给下一次）：一次修复要回答「哪一个**能力**从坏变好了」，答不出来的（「这条用例不再红了」「报错文案对了」）都不是修好，是报错改善。** 本条的 ③ 负控与入口面静态守卫在 C 落地时改判据面（实例没了、命题仍在），见缺陷 41 · ④。

**45. 变异驱动的先验基线门红源不可辨：「基线跑不起来」与「基线绿、变异没红」同型** —— 状态：**已修（`d1d36de`，2026-09-17）**（2026-09-15 容器腿实测，原裁定「记账（不修）」，改判修复）
- 事实：镜像内没有 pytest 时，`_baseline_gate` 对四个锁文件的 pytest 调用全部**拿不到 summary**，却仍**放行**进入变异阶段；逐条变异拿到的同样是空 summary，最终表现为一片 `>> ?` 加一串 `MISMATCH ...：期望 RED 实得 green`。
- 形态：红是响了（**不是**静默假绿），但「基线**跑不起来**」与「基线绿、变异**没红**」两种成因共用同一种红 —— 正是 §四 那条「两种不同成因的失败若共用一个信号，就分不出是哪一种」。基线门存在的唯一理由就是让这两件事分开，它在「跑不起来」这一支上没有生效。
- 证据命令：镜像内（未补 pytest）`python tests/perf/route_facts_mutations.py --group XF --with-container` → 逐条 `实得=green`、`>> ?`；补装 pytest 后同一命令「全部符合预期」。
- **修法（已落地，`d1d36de`）**：`_baseline_gate` 要求每个基线目标都解析出 `N passed`，解析不到即判「基线不可用」并**以另一种退出原因**拒跑，与「基线有 failed」分开 —— 与原「待裁」那条同形，落点在 `tests/lock_coverage.py`（三个驱动共用的出口）而不是各驱动一份。
- **订正前提（2026-09-17，动手前实测）：上面「却仍**放行**进入变异阶段」今天已不成立。** `lock_coverage._run` 拿不到汇总行时回 `RUNAWAY`，而 `baseline_ok(RUNAWAY)` 为假、`_baseline_gate()` 对三个 ping 靶子全部回「坏」——**门是拒跑的**（仓外探针写死四行汇总行复核：`RUNAWAY` → 拒跑）。**门已经生效这一点，本条原先记反了。** 仍然成立、也是本次真正修掉的，是后半句：**成因不可辨** —— 拒跑了，但「跑不起来」与「跑起来了但红」共用一句文案（「基线不绿 …… 先修基线」）与一个退出码 2，于是拿着「基线不绿」去翻锁、而真实原因是 pytest 压根没装，**拒跑但指错方向**。
- **改了什么（机制面）**：判定、文案、退出码三样都收进 `lock_coverage.py` 的单一出口，没有各驱动一份（同一判定两份实现正是本仓反复栽的跟头）。`baseline_verdict(summary) -> str` 回**成因**（不可用回 `BASELINE_UNUSABLE` / `BASELINE_RED`，可用回 `""`）；`baseline_ok` 收成它的布尔投影，「可用吗」只剩一个实现；`refuse_on_baseline({靶子: 成因}) -> int` 按成因分组点名靶子、给下一步动作（先修环境 vs 先修基线），退出码 `3` / `2` 分档。**skip 仍算可用**这一侧没被写坏：`test_storage_ping.py` 基线本身就是「4 passed, 2 skipped」，判成不可用会让那个驱动**永远**开不了跑。两种成因同时出现按「不可用」退 —— 环境没修好之前，另一支的结论本来也拿不到。
- **判据与验证**：「新来的人不知此事也不会写错」的落点是 `lock_coverage.baseline_verdict` 的签名与返回值 —— 想拒跑就得回答「哪一种」，没有能省掉的第三个选项。变异/控两条补在 `tests/test_lock_coverage.py`：① `baseline_verdict` 四个可用汇总行（含 skip）→ `""`、三个红汇总行 → `BASELINE_RED`、`RUNAWAY` → `BASELINE_UNUSABLE`，且两常量不相等；② `refuse_on_baseline` 三种组合（只红 → 2 含「先修基线」；只不可用 → 3 含「先修环境」且**不含**「先修基线」；两者并存 → 3 且两靶子都点名）。失败信息点成因与靶子，不自解释的那侧（「只不可用」还断言反面文案不出现）也控了。
- 生产代码零改动（`git diff --stat` 只有 `tests/` 下五个文件）。全量 1132 passed / 64 skipped / 0 failed（改前 1130，增量正好这两条）。
- **退出码 `3` / `2` 拆分的下游普查（2026-09-17，现跑现数）**：拆之前 `2` 是「拒跑」的唯一码，拆之后 `2` 只剩「基线红」、`3` 归「跑不起来」—— 故须核有没有人按 `== 2` 认过这个码。命令与结果：
  - `grep -rn -e '-eq 2' -e '== 2' -e 'exit 2' -e '-ne 2' -e 'returncode == 2' .github/ scripts/` → **0 命中**（CI 只有两条 workflow，皆不调这三个驱动）。
  - 全仓（排除 `.venv`）搜 `-eq 2|-ne 2|exit 2|returncode == 2|returncode != 2|status == 2` → **1 命中**，`tests/perf/step4_seed_pg.py:177 assert r.status == 200`，是 HTTP 状态码，与退出码无关。
  - 三个驱动的名字（`ping_mutations` / `pg_gate_mutations` / `route_facts_mutations`）在 `.github/`、`scripts/` 里各 **0 命中** —— 它们只被人工直接调用（调用方式写在各自 `main()` 的注释里），没有任何自动化入口读它们的退出码。
  - **结论：无下游依赖，无需同步改。** `lock_coverage.EXIT_BASELINE_RED = 2` 与其余前置门（条数门 / docker 门 / 别名门）同为 `2` 是**刻意对齐**（都表示「拒绝开跑」），不是被谁依赖的契约；要改的是「成因可辨」，故只有不可用那一支分到 `3`。

**46. `tests/test_auth_tokens.py::TestLoginRefreshChain` 两条依赖本机 `.env` 的 `JWT_SECRET`，无 `.env` 的环境必红** —— 状态：**已修（`170d49a`，2026-09-17）**（2026-09-15 缺陷 42 收口时普查环境依赖发现）
- 事实：`web/routers/auth.py::get_jwt_secret` 在 `JWT_SECRET` 缺失或等于默认值时长直接 `raise RuntimeError`，而 `JWT_SECRET` 只可能来自工作树里那份 **gitignored `.env`** —— 该类的登录 + 刷新两条用例因此**只在这台机器上绿**。
- 证据命令（现跑现数）：`git archive 53bed63 | tar -x` 得到一棵**不含 `.env`** 的树（`.env` 未被跟踪，仓里只有 `.env.example`），容器内跑该类 → **2 failed**，栈底 `web/routers/auth.py:41 RuntimeError`。
- **非缺陷 42 引入（已核）**：`53bed63` 早于本轮全部改动，同样红。
- 形态：**ambient 依赖** —— 用例的通过条件不在仓库里，换一台机器 / 换一个干净检出，结果就变。同一个病还有一面：本机 `pytest tests -q` 全绿**不能**当作「这组用例没问题」的证据，它只证明**这台机器恰好有 `.env`**（与 §四「环境没配好与代码有问题，不能共用一个绿」同源）。
- **订正（用户裁定）**：上面「只在这台机器上绿」不准确 —— CI 的两个 job（gate / upstream-drift）都注入了 `JWT_SECRET`（`build.yml:116` / `:203`）。准确说法是**只在有 `.env` 的检出或 CI 里绿；干净 clone 直接跑就是 2 红**。另外范围是**这 2 条**，不是一类（全量普查验过）。
- **已修（`170d49a`）—— 两半，各堵一个洞**：
  - **(a) harness 供值**（`tests/conftest.py`）：autouse fixture 给整个会话设死一个仓库自带的 `TEST_JWT_SECRET`。这一半才是「下一个人写新用例照样读 `.env`」的闭合点 —— 用 autouse 而不是让用例各自声明，因为漏一次就滑回 ambient 依赖，而本缺陷的形态本来就是「没人注意到这个值从哪来」。沿用本文件已有的同一手法（`STORAGE_BACKEND` 用的 `setdefault`：值由仓库给，不由机器恰好有没有 `.env` 决定）。
  - **(b) 注入点**（`web/routers/auth.py`）：secret 收敛成 `Depends(get_jwt_secret)`，端点与依赖的签名因此**写着**「这里需要一个 secret」。这一半堵的是「签名里看不出来」—— 环境变量的存在性在任何签名里都看不见，只能靠跑一次干净检出发觉。配套一条守卫用例（`test_secret_is_injected_not_read_from_environment`）：用一个**与环境值不同**的 secret 覆盖依赖，走完 login（签发）→ `/me`（验签），任何一侧回去读环境都会 401。
- **同源路径普查（本轮补）**：`get_jwt_secret()` 全仓**5 个直接调用点**（`git grep -c` 现跑现数：auth.py 4 + server.py 1）都过了一遍 —— 签发侧 `_create_access_token`（被 register / login / refresh 两个分支共 4 处调用）与验签侧 `get_current_user` / `get_optional_user` 已收敛；`validate_jwt_secret()`（启动校验）与 `web/server.py:278`（ASGI 中间件）**仍直接读环境**，但前者职责就是校验环境、后者不是 FastAPI 依赖注不进去，两处都被 (a) 覆盖成确定值，故不再是 ambient 依赖（要单独测中间件那条路径才需要给它注入点）。另：`storage/{sqlite,postgres}_store.py` 也从 `JWT_SECRET` 派生 Fernet key（同名的另一个消费者，不是 auth token 路径），今天无用例走到，但 (a) 一并把它变成确定值。
- **订正（自己 commit message 里的数）**：`170d49a` 的正文写「`get_jwt_secret()` 是唯一取值点，7 个调用点」—— **7 是错的**，实测是 **5 个直接调用点**（auth.py 4 + server.py 1；其中 4 处是 `_create_access_token` 的调用者数量，与调用点数量不是一回事，当时把两者加在了一起）。口径以本条为准。
- **实测**：干净 clone 条件（`JWT_SECRET= pytest tests/test_auth_tokens.py`）修复前 **2 failed**（栈底 `auth.py:41 RuntimeError`）、修复后 **3 passed**；`JWT_SECRET= pytest tests/` → **1123 passed, 64 skipped, 0 failed**；守卫的变异验证：把 `get_current_user` 的 decode 改回 `get_jwt_secret()` → **只有新增那条守卫红**（1 failed, 2 passed），还原后 3 passed。

**47. `pymupdf` 的 `Document.__init__` 失败路径不释放 `fz_stream` —— 句柄只在那个异常对象被 GC 回收时才关** —— 状态：**已裁定·上游跟踪**（2026-09-22）—— pymupdf 上游缺陷，本仓无修复点（不引 monkey-patch / 不 fork），只跟踪上游版本。（2026-09-16 缺陷 41 · ④ 收口时逐层实测）
- 事实（`pymupdf 1.28.2`，`.venv/Lib/site-packages/pymupdf/__init__.py`）：`3012 fz_stream = mupdf.fz_open_file(filename)` → `3013 doc = mupdf.fz_open_document_with_stream_and_dir(...)` 抛 → `3016 raise FileDataError(...) from e`；**`3027 self.this = doc` 是成功路径才到得了的那一行**。`Document` 没有 `__del__`，`close()` 只把 `self.this` 置空 —— 失败路径上它**从没被赋值**，故**从外面关不掉**。
- **滞留者是谁（这是可查的事实，链已追全）**：不是我们的异常处理，是**异常对象的 traceback 链**：`Document ← __init__ 的帧 ← traceback ← FileDataError ← traceback ← concurrent.futures.thread.run() ← … ← _asyncio.Task`。
- **「是我们 `raise ... from e` 造成的」已被实测证伪**：四种写法（`from e` / `from None` / `del e` / 在 `with` 外 raise 且 `__context__` 为 None）失败形态**逐字相同**；`gc.collect()` 也放不掉（那是活引用，不是环）。故修法不在我们这一侧。
- **滞留界为 1、一个 event loop 迭代内自愈、不累积**（实测 5 次连续坏 PDF 请求：存活 `Document` 计数恒为 1；`await asyncio.sleep(0)` 后归 0、临时文件随即可删）。
- **Linux 同形、无后果**：对象图与引用链**逐字相同**，只是 Linux 允许 unlink 已打开的文件，于是这条永远不会响。**故它不是「Windows-only 的清理竞态」，是上游的释放时机问题，只在 Windows 上现形**（我们的边界见缺陷 48）。
- 修法：**没有本地修法** —— 失败分支既不 `close()` 也不赋 `self.this`，外部无从关闭。只能等上游，或在 `Document.__init__` 之外重试。

**48. Windows 上删临时文件的 `os.unlink` 会失败一次，且失败的清理顶上真响应（400 → 500）** —— 状态：**已修**（2026-09-16 缺陷 41 · ④ 收口）
- 现象：`web/routers/text.py` 上传路由的 `finally` 里是裸 `os.unlink(temp_path)` → 坏 PDF 的失败路径上抛 `PermissionError [WinError 32]`，把本该抛出的 `HTTPException(400)` **顶成 500**、上屏文案也丢了（`test_text_failure_messages.py` 的两条坏 PDF 用例因此从 400 变 500）。
- 根因在**上游**（缺陷 47），不在这一行；这一行自己的缺陷是**没有防线**：**清理失败与请求失败共用同一个异常通道**。
- 修法：unlink 包 `except OSError` 并**打日志点名路径** —— 不是静默吞掉（失败不顶掉真响应，但看得见）。日志里写明这是已知上游行为 + 指向缺陷 47/48，否则下一个人看到这行会当成新问题。
- **不加 `gc.collect()`**：靠 GC 释放句柄是不确定的，不是机制。
- 射程：**Windows 上失败一次、下一个 loop 迭代自愈、不累积**；Linux 同形但无后果 —— **这不是线上故障**。

**49. 校验失败的路径也要付出加载 45.2MB 解析运行时的代价 —— 「校验 ↔ 重依赖」没断** —— 状态：**已修**（2026-09-16 缺陷 41 · ④；标题原写 40MB，是估算，实测值见正文）
- 事实：`core/text_manager.py::_extract_pdf` 原先在**函数入口**就 `import pymupdf4llm`，而该包在**包 import 期无条件**拉进 `onnxruntime`（`pymupdf4llm/ocr/analyze_page.py:5`；本仓**没有一处** import onnxruntime，是它拉进来的）。全仓 **45.2 MB** 的推理运行时被拉起（实测：`du -sk .venv/Lib/site-packages/onnxruntime` = 45.2 MB，`onnxruntime 1.30.0`；本条原先写的「40MB」是估算，不是量出来的），而本函数的 docstring 自己写着 **「Does NOT OCR scanned PDFs」** —— **依赖的能力我们明确不用，成本却全付了**。后果是生产性的：**任何**（含非 PDF / 打不开的）上传都要先付这份加载代价。
- **定性**：不是「文案 ↔ 解析层」没断，是**「校验 ↔ 重依赖」没断** —— 真架构缺陷，有生产后果。
- 不变量（**判据落在位置，不落在重量** —— 不需要定义什么叫「重依赖」）：**校验失败的路径不得触达解析器**。可判定为**行序**：① 模块顶层零第三方 import；② 每个函数里「无守卫的第三方 import」（无守卫 = 行号在该函数第一条 `raise` 之前）至多一条 —— 第一条是该函数**自己的工具**，否则判据本身成死判据。失效方向是**响亮误伤**（多一条就红、红的信息点名哪一行），不是静默漏过。
- 「哪些算解析库」**从事实推出**（`requirements.txt` 的分发包名 ∩ `importlib.metadata.packages_distributions()` 反查顶层模块名），**不是名单**：实测 134 个分发包，`aiofiles` / `pymupdf` / `pymupdf4llm` / `docx` / `onnxruntime` / `chromadb` 全部解析为真。
- 锁 = `tests/test_text_failure_messages.py` 的 **L6**（三条：① 顶层、② 每个函数的无守卫 import、③ 负控「判据面不塌」）；修法 = `import pymupdf4llm` 挪进 `pymupdf.open()` 与页数校验**都通过之后**的内层 `try`。变异 **T-1**（挪回函数顶部）/ **T-2**（挪到模块顶层）各自点名该 import 与行号。
- **C 落地后 L6 的构成（命题不变、实例没了，故改的是判据面）**：
  - **① 不动。** 命题仍成立，只是今天没有实例。
  - **② 不动（判据本身），但它的失效方向要认清：** 剩下的那条「无守卫 import」现在是 `_extract_pdf` 里的 `import pymupdf`，它**就是该函数的第一件工具**，按 ② 的定义免责 —— 即 **② 今天在任何合法写法下都红不了**，撞它的是 T-1（插第二条 import）、T-2（挪到模块顶层）两条变异。判据留着是因为**命题还在**：将来有人给 PDF 加第二个重解析库时它立刻有齿。**退役等于因为「暂时没有违例」而拆掉规则，那是把偶然当成必然。**
  - **③ 负控换判据面（旧判据作废）**：原判据是「必须存在排在失败点**之后**的第三方 import」—— 全仓唯一那条就是 `import pymupdf4llm`，C 拿掉它之后该判据**恒红**（它在报告「我的命题失去对象了」，不是 bug）。换成「**扫描确实下钻进了函数体**」：判的是**扫描器有没有工作**，不是「今天恰好有违例」。**这个换法比原来更准** —— 原判据把「有违例」当成「扫描正常」，两件事本来就不同。**代价**：新判据在被撞之前是「死判据」，故补了变异 **T-4**（把三条函数级解析库 import 全提到模块顶层 → 函数体里一个都认不出来 → 负控红）给它一个撞得红的对象。
  - **入口面新增静态守卫**：`test_l6_no_parser_runtime_in_source` 断言 `"pymupdf4llm" not in text_manager.py 源码` —— 这条同时守住**「生产缺陷不得靠一次 import 回来」**，是净增保护。**代价（如实记）**：它是**字面子串**检查，故生产 docstring 里**不能出现这个库名**（写了就自红）；库名与整条链留在本条与缺陷 44 的台账里，不在生产代码里。撞它的是 **T-3**。
  - **T-1 的形态换过一次，如实记**：老 T-1 是「把 `import pymupdf4llm` 挪回函数顶部」，C 把 `pymupdf4llm` 整包删了之后**锚点不复存在**，那个变异体现在崩在 `assert hits == 1` 上（**锚点失效是崩，不是变红** —— 崩得响亮，不是静默假绿）。老 T-1 想守的对象（`pymupdf4llm` 回到源码里）**没丢**，改由 **T-3** 打静态守卫；**T-1 的对象从「挪回 pymupdf4llm」换成「插入第二条解析库 import」—— 形状变了，不是同一件事。**
- **验收（写死的这条）**：非法 PDF 走 `pdf_open_failed` 且**不触达 onnxruntime** —— 用 `sys.meta_path` 的 **import 钩子**断言「没触达」，**不许用「本机不崩了」当证据**（那在装了 onnxruntime 的机器上恒真，是假绿）。钩子抛的是 **`BaseException` 子类**：抛 `Exception` 子类会被被测代码的宽 `except Exception` 抓走再包一层，于是**钩子响了而判据仍然是绿的**。
- **残余接受（照记，别让人以为 PDF 上传修好了）**：真·有效 PDF 的解析路径**本机仍会崩** —— 本修复只消掉「测失败文案要拉起推理运行时」，**不修机器**。
- **对缺陷 44 的后果（未在本机验证）**：import 挪到校验之后，容器内「缺 onnxruntime → 坏 PDF 返回 500」那条应当随之消失（坏 PDF 在 `pymupdf.open()` 就 400）。**本机无法验证**（Windows 那条拒绝装 pytest 的容器腿要另跑）。

**50. 本机 `import onnxruntime` 一律崩 ⇒ 本地 PDF 上传在生产代码路径上就是坏的** —— 状态：**已裁定·结案**（2026-09-22）—— 生产后果已由缺陷 44 消除，剩余只是本机环境事实，**不属本仓缺陷**。（2026-09-16 缺陷 41 · ④ 收口时发现，**spec 外**；同日方案 C 落地，后果消失，见末条）
- 事实：本机锁版 `onnxruntime 1.30.0` 被 `C:\Windows\System32\onnxruntime.dll`（微软随系统装的 1.17.260613）抢先加载，ABI 不符 → **模块初始化时 access violation**。这条链在 `_extract_pdf` 里，是**生产代码路径**，所以本机**上传一份合法 PDF 也是坏的**。
- **射程写清：只影响本机开发，不影响生产。** 容器是 Linux（无此 System32 DLL），实测 `1068 passed / 82 skipped / 0 failed`。这不是测试问题，是**生产功能在开发机上不可用**。
- **原生层根因未查明** —— 照写「未查明」：已核的是「DLL 同名抢先 + ABI 不符」这一层，为什么 `System32` 那份能压过 pip 包内那份、以及为何换 `1.26.0` 就不崩，**没有查明**，不装懂。
- 与缺陷 44 的关系：44 记的是**容器**那一端（缺包 → 500），本条目记的是**本机开发**那一端（包在但被抢先 → 崩）。同一条 import 链的两个方向，修法不通用。
- **2026-09-16 晚（方案 C 落地后）：上面的「生产代码路径」那半句不再成立 —— 环境事实没变，是那条路不再碰它了。** 实测两条，都在本机 `.venv`：
  - `.venv/Scripts/python.exe -c "import onnxruntime"` → **仍然 `Segmentation fault`，exit=139**（System32 抢先那条链一字未动，这是**环境事实**，不是本仓能修的）
  - 但 `.venv/Scripts/python.exe` 里调真的 `TextManager._extract_pdf` 解析一份合法 PDF → **exit=0，取到 51 字符、内容正确**（C 之前这里是 access violation 整个进程被杀）
  - 即：本机**开发**这一端的 PDF 链修好了，不是因为 onnxruntime 修好了，而是因为 `pymupdf4llm` 从路上拿掉了。**这条订正同时说明缺陷 44 的「不是测试问题，是生产功能在开发机上不可用」这句话当时对、现在过期** —— 两句话的射程都写在各自条目里。
  - 附：本机跑全量的 `--deselect`（缺陷 44 那条）**从 C 起不必再挂**（`tests/test_text_failure_messages.py` 28 passed，含原先要 deselect 的 L3）。

**51. compose 事实层的两条承重前提只在 v5 上成立，却以散文形式存在 —— 那条「实测」测的是 flag 不是行为** —— 状态：**已修**（2026-09-16）
- 现象（CI run `35075782258`，`0cb193f`）：11 条红 = `test_pg_gate` 7 + `test_compose_model::test_fact_layer_names_no_repo_specifics` 1 + `test_lock_coverage` 3。**前 8 条的 stderr 是同一句**：`env file /home/runner/work/Character-distill/Character-distill/.env not found: stat …: no such file or directory` —— 报的是「你少了个文件」，而真凶是「本机 compose 不满足前置条件」。
- 根因：`tests/compose_model.py` 的模块 docstring 写着「加 `--no-env-resolution` 就**不读 service env_file、不吐秘密**」。这句话读起来是一条性质，实际是**两条**，而两条在 compose v2.x 上**都不成立**（引入点 `725761d`）。前提写在散文里就没人守：版本一换静默失效，失效的表现还是**另一件事**。
- **那次「实测」测的是 flag 存不存在，不是行为。** `build.yml` 的注释原文是「ubuntu-latest 自带 compose v2（实测 2.38.2，`config --no-env-resolution` 与 `--variables` 都在）」—— 逐字读，它核的是**两个 flag 在不在**。而 **v2.38.2 上两个 flag 都在、两条行为都不成立**。判据与命题不是同一个东西，这就是本条的全部。
- 版本表（命令形状与事实层一致；本会话复现，与引子里的实测逐格相符）：

| 前提 | v2.38.2 | v2.40.3 | v5.0.0 | v5.5.1 |
|---|---|---|---|---|
| P-a：`env_file` 指向不存在的文件时仍 exit 0 | ✗ | ✗ | ✓ | ✓ |
| P-b：`env_file` 存在时其值**不进**模型输出 | ✗ | ✗ | ✓ | ✓ |

- **P-b 的安全面（这条比红更值得记）**：v2 的**开发机**上，仓内那份**真实 `.env`** 会被 compose 原样灌进模型 —— 凭据进了「有效模型」这个中间产物。而这一支持**不会红**：P-a 只在**缺**文件时才炸，开发机上 `.env` 是**在**的。**红的那条路（CI，干净检出、没有 `.env`）与泄漏的那条路（开发机，有 `.env`）互斥** —— 于是 CI 以红掩盖、开发机以绿掩盖，两面都看不见。
- 修法两件，缺一不可：
  - **① 前置条件升成事实。** `tests/compose_model.py::capability_defect()`（`lru_cache(maxsize=1)`，进程内只探一次）：先跑 `compose version`（跑不起来 = 「无 CLI」，第四种成因），再在临时目录造**三份各自独立**的合成编排文件，按与 `_effective_model` **同一形状**的命令各真跑一次：**对照 C**（服务没有 `env_file`，只问「这条命令形状在本机跑得通吗」）、**P-a 探针**（`env_file` 只列一条不存在的文件）、**P-b 探针**（`env_file` 只列一条存在且内容是**每进程现生成**哨兵的文件）。C 不成立 = 探针自己不成立（第四种成因），就此打住、不再往下判；P-a 探针非零退出 = P-a 不成立；P-b 探针退出 0 而输出含哨兵 = P-b 不成立。**两条都要判完再返回**，都不成立时消息里**同时**带上两条。**四种成因各说各的话**，不共用一句。`effective_model()` / `sentinel_values()` 两个入口在**调 compose 之前**先过守卫（`_require_capability()`），不过就抛 `ComposeFactError`、**不给模型**。守卫**不放在 `_compose` 里** —— 探针自己也走 `_compose`，放那儿会自己递归自己。P-b 的消息里**绝不带哨兵值、也不带输出原文**：那正是要防的东西，而异常会进日志、进 CI 输出。
  - **② CI 钉版本。** `gate` job 装 compose **v5.5.1**（sha256 `db1889…aed576` 校验），装到 `$HOME/.docker/cli-plugins/`（用户级在 Docker CLI 的搜索序里**先于** runner 自带的系统插件），随后**断言** `docker compose version` 含 `v5.5.1` —— 不核这一句，「钉在 v5.5.1」就只是注释里的一个数字。`upstream-drift` 反过来装**最新 release**（tag 取 `releases/latest` 的跳转、sha256 取同一 release 的 `.sha256`），与 pip 下界同理：**它红 = compose 自己变了，不是本仓写错了**。
- **拒绝的三种做法（遇到诱惑就停下）**：
  - `env_file` 改成 `required: false`：改的是**生产语义** —— 缺 `.env` 时静默启动，把失败**吞成成功**。毛病出在「我们信了一个不成立的前提」，不是出在编排文件写得太严。
  - CI 里造一个空 `.env`：补丁，且**v2 上 P-b 照样泄漏**。治好的是症状（那条 `stat` 报错），前提原封不动。
  - 按版本号字符串判断：判的是**代理**，不是**行为**。v2.x 上 flag 都在，所以「版本号 < 5」与「前提不成立」今天恰好同真 —— 明天 compose 发个 5.x 改回去就静默错位。这与本层「问 docker 而不是自己 `yaml.safe_load`」是同一条道理。
- **本机核查（现跑现数）**：`docker compose version` → `Docker Compose version v5.1.3`；`capability_defect()` → `None`（两条前提都满足）。**故本机历史上没有把真实 `.env` 读进过模型**，那条「本机若是 v2 就立即停下报告」的停止条件**未触发**。
- **边界（如实记）：`--variables` 只列变量名，未纳入探针。** 事实层的 `_variable_names()` 走的是 `config --no-env-resolution --variables` 这条形状，而探针只真跑了 `--format json` 那条。依据是实测（v2.38.2 与 v5.5.1 各跑一遍，`env_file` 指向一份含**造出来的**占位值 `PROBE_SECRET=deadbeefcafe0123` 的文件）：

```
$ docker compose -f probe.yml --env-file placeholder.env config --no-env-resolution --variables
NAME                REQUIRED            DEFAULT VALUE       ALTERNATE VALUE
PROBE_IMAGE         false
退出码=0        含占位值的行数=0        ← 两个版本读数相同
```

  `--variables` 的输出是一张**名字表**（NAME / REQUIRED / DEFAULT / ALTERNATE），值根本不进这条路径；`env_file` 缺文件时它也 exit 0。**故它既不承载 P-a、也不承载 P-b，不必进探针** —— 这条边界是实测的，不是推断的。**反面证据一并留着**（同一个文件、两个版本，走 `--format json` 那条形状）：v2.38.2 上占位值被原样印进模型（`"PROBE_SECRET": "deadbeefcafe0123"`，exit 0），v5.5.1 上印不出来 —— 探针测的正是**有性质的那条形状**，不是「随便跑一条 compose 命令」。
- **验收（现跑现数）**：改前 `35075782258` 的 11 条红 → 改后 `35080483581` 的 `gate` job **恰好 3 failed**（全是 `test_lock_coverage` 那 3 条，属缺陷 41 的覆盖缺口，本条不动 —— **那里当时写的「88 条」是快照，现测 84，见缺陷 41 末尾的漂移说明**），`1175 passed`；新增的 6 条用例（T1–T5 + 一条「无 CLI 是第三种成因」）**全 PASSED**，含真跑那条 T5。`Pin docker compose v5.5.1` 那步在 CI 上如实打印 `/tmp/docker-compose: OK` 与 `Docker Compose version v5.5.1`。v2.38.2 + `REQUIRE_COMPOSE_TESTS=1` 的对照里，同一批红的报文**以 P-a 原因开头**，`env file … not found` 降级成附在后面的证据。
- **（本轮追加修复后，用例数与落点都变了：`test_compose_capability.py` 7 条 —— T1/T2/T2b/T6 + 无 CLI + 哨兵入口 + 正控，T5 移回 `test_compose_model.py`；本轮的闸门数字见下面「审计追加」末条。）**
- **同族判据（写给下一次）：**「实测」必须写明**测的是哪条性质**；**测 flag 存在 ≠ 测行为**。本条的引子就是一句没写性质的「实测 2.38.2」—— 它核的是 flag，读的人当成了行为，于是前提整整一轮没人守。同族：缺陷 40（依赖只有下界 → 上游发版能让本仓用例红，红的样子与本仓写错逐字相同）。

- **审计追加（F1–F4，2026-09-16）** —— 上面那条「已修」有四处没修干净。四处都不动命题，只动守卫的形状：
  - **F1 一次判两条，先失败的那条会遮住后面的。** 原先一次运行同时判 P-a 与 P-b：v2 在缺文件那一步先炸，于是**永远只报得出 P-a** —— 而**那台机器恰恰也在泄漏**（P-b 同样不成立），泄漏被前一条遮住。两种成因（缺文件 / 值进模型）共用同一个信号，等于少了一整条守卫。**实证**：v2.38.2 上改前返回值含 P-a、不含 P-b；改后含**两条**（v2.40.3 同）。改法：三条探针各用**各自独立**的合成文件，**P-a 与 P-b 都判完再返回**，都不成立时消息里**同时**带上两条。
  - **F2 非零退出 ≠ P-a。** 原先只要退出码非零就判 P-a —— 合成文件写错、flag 不被支持等**无关原因**会被误报成 P-a，两种成因又共用一个信号。改法：加**对照 C**（服务没有 `env_file`，只问命令形状跑不跑得通），它不成立就是第四种成因「探针自己不成立」，就此打住、不再往下判；C 已证命令形状可用之后，P-b 探针的非零退出若**不带哨兵**就说不清是什么，同样报「探针不成立」，**不猜成 P-a**。
  - **F3 用例落点跟着错信号走。** 唯一一条真跑前提的用例（T5）被放进「默认不真跑」的新文件、还**逐条**挂 mark —— 环境需求是**文件**的性质，模块级 mark 才承载得动；逐条清单是把 fail-safe 的默认换成一张靠人记得维护的表。改法：T5 移回 `test_compose_model.py`（模块级 mark 管），`test_compose_capability.py` 加 autouse fixture，默认把 `_compose` 换成**直接抛 `AssertionError`** 的假实现（各用例的伪实现在此之上覆盖）。**默认给假的，不靠用例自己记得换**：忘了换的用例会去调真 docker，「本机装了什么」就悄悄渗进本该是纯逻辑的判据。**实证（M6）**：守卫在场时，一条「不加伪实现、直接真调」的用例红在**守卫那句话**上；把守卫那行换成 `pass`，同一条用例转绿 —— 故那条红确实来自守卫，不是来自环境。全仓 `COMPOSE_ENV.skipif` 只剩两处**代码**落点（`test_compose_model.py` 模块级、`test_pg_gate.py` 的 `_COMPOSE`）。
  - **F4 两处注释说的与事实相反。** `build.yml` 原文两处写「事实层静默失效的状态下判绿」—— 事实是那轮 CI **红了**（运行 `35075782258` 实测 `11 failed, 1161 passed`），红的是**症状**：每条报 `env file …/.env not found`，看日志的人会去查「干净检出上为什么没有 `.env`」，而不是「本机 compose 够不够格」。**红得指错地方比静默判绿轻，但同样不是守卫** —— 两种成因（本机环境不对 / 本仓写错）共用同一个信号，而缺陷 40 修的正是这个形态。`compose_model.py` 模块 docstring 里那句无条件的「加了它：不读 service env_file、不吐秘密」也改成了带前提的说法（「**在满足 P-a/P-b 的 compose 上**如此」）。本条台账同步订正（686 / 688 两处旧说法）。
  - **同族判据（补一句给下一次）：一次探针只测一条性质；多条性质合在一起测，先失败的那条会遮住后面的。** F1 与 F2 是同一句话的两个方向：合并判 → 后面的被遮住；只看退出码 → 前面的被误认。**判据要能逐条点出「这条探针在测哪一条性质」**，点不出来的探针就不是一条判据。
  - **本轮闸门数字（现跑现数）**：变异 7 条（M1/M2/M3/M4/M5 + 新增 M7「恢复成合并探针」/ M8「删掉对照 C」）**全 RED，且红在预期的那条用例**。四组矩阵 —— 无 docker `3 failed, 38 passed, 20 skipped`（跳过的是要 compose 的用例，T5 `SKIPPED`）；v2.38.2 未设 REQUIRE `3 failed, 38 passed, 20 skipped`（同上）；v2.38.2 + `REQUIRE_COMPOSE_TESTS=1` `20 failed, 41 passed`（拒绝跳过、真跑并红 = `test_compose_model` 10 + `test_pg_gate` 7 + `test_lock_coverage` 3，**含 T5**）；v5.5.1 + `REQUIRE_COMPOSE_TESTS=1` `3 failed, 58 passed`（**只剩元锁那 3 条红**，T5 在 `test_compose_model.py` 里 `PASSED`）。各版本由 shim 把 `docker compose` 转发到对应 standalone 二进制，驱动脚本在仓外（一次性）。

**52. 一条 100% 失败的生产路由，在本仓「没有任何一条守卫」碰得到它 —— 全部 PDF 判据都长在「坏输入」那一侧** —— 状态：**已修（`62bd744`，2026-09-17；原裁定「记账（不修，处置方向不实现）」，改判修复）**（2026-09-16 方案 C 收口时发现，**spec 外**）
- **事实（现跑现数）**：全仓测试里出现的 PDF 字节只有**一处** —— `b"not a pdf at all"`（`tests/test_text_failure_messages.py:485` 与 `:525`，两条都判 400 + 文案）。**没有任何一条测试解析过一份合法 PDF**；全仓第一份「真造出来的合法 PDF」出现在 `tests/perf/pagenum_chapter_probe.py`（方案 C 为**量这次回归**才写的探针，`58be15d`），而它是探针不是用例，`pytest` 不收集。
- **后果的量级要写清**：这条路由从 2026-06-29 起**每一份上传都失败**，持续约 2.5 个月，而期间 CI 是**绿的** —— `1068 passed / 82 skipped / 0 failed`、缺陷 41 的 13 条 L 锁、外加一整套变异矩阵，**没有一条碰到它**。
- **为什么碰不到（这是本条真正的形态，不是「忘了写一条用例」）**：已有的 PDF 判据判的是**校验失败的路径**（坏输入 → 400 / 文案 / 日志），而坏输入的路径**在拉起解析器之前就 return 了** —— 锁的眼睛长在失败路径上。同一件事的另一面正好解释了缺陷 41 · ④ 为什么能让 L3 变绿而生产照样坏：**把 import 挪到校验之后，等于让锁看得见的那条路更早地避开了解析器**，锁于是更绿，而能力一点没回来。**「失败路径判得越细」与「成功路径有没有人守」是两件事，前者不会顺带覆盖后者。**
- **它与缺陷 41 的关系是互补不是重复**：41 管的是「守卫与被守对象之间隔着几层」，本条管的是**守卫长在哪一侧** —— 判据全在「该拒绝的拒绝了」，没有一条在「该接受的接受了」。**失败路径的判据再多，也不构成成功路径的守卫。**
- **原裁定（留档）**：处置方向是「一条『合法 PDF 解析出预期文本』的守门用例」，**不实现的原因**是「本轮是生产故障修复，这条属于测试覆盖债，与缺陷 41 的缺口分开走」。**改判（2026-09-17，用户裁定）**：这半条不是覆盖债 —— 缺陷 41 修的是「守卫与被守对象隔着几层」，本条修的是**守卫长在哪一侧**，41 收口了本条也照样在（事后复核：41 的 13 条 L 锁 + 全套矩阵，没有一条碰得到成功路径）。
- **改判后落地（`62bd744`）**：`tests/test_text_failure_messages.py` 新增 **L7** 两例（该文件原先自述「锁六件事」，全是失败侧，现为七件）。① 合法 PDF：用例内用 `pymupdf` 现造最小合法 PDF（一页一行，`fontname="china-s"` —— 内置中文字体**编在 pymupdf 二进制里**，不依赖可选的 `pymupdf-fonts` 包；无需入库二进制夹具），断言 `_extract_pdf` 返回的就是它里面的字；② 同源路径同批：合法 DOCX（两段）断言 `_extract_docx` 返回两段以空行相接。两条的失败信息**自带成因与下一步**（取到空串 = `page.get_text()` 那条路断了，就是缺陷 44 的形态；取到异常 = 不要在本机拿「崩没崩」当判据，那是缺陷 50）。
- **范围普查（命令与结果，2026-09-17 现跑现数）**：
  - `grep -rn --include="*.py" -E "def _extract_[a-z_]+" core/ web/ storage/ adapters/` → 全仓 **7 个** `_extract_*`。
  - 再对每个名字 `grep -rl "\b<名>\b" tests/` 看判据落在哪一侧：

    | 函数 | 位置 | 引用它的测试 | 判据在哪一侧 |
    |---|---|---|---|
    | `_extract_pdf` | `core/text_manager.py:271` | `test_text_failure_messages.py`（+ 探针/驱动） | **只坏输入** → 本轮补 |
    | `_extract_docx` | `core/text_manager.py:321` | `test_text_failure_messages.py` | **只失败结局** → 本轮补 |
    | `_extract_catchwords` | `core/chat_engine.py:1187` | `test_catchwords.py`（11 处，真文本 → 关键词） | 正向已有 |
    | `_extract_json` | `core/distiller.py:353` | `test_distiller_utils.py`（9 例，含 `'{"a": 1}'` 原样返回） | 正向已有 |
    | `_extract_content` | `adapters/llm_adapter.py:420` | 三个文件；`test_llm_adapter_finish_reason.py::test_stop_returns_content` 断言 `chat(...) == "完整"` | 正向已有 |
    | `_extract_quote_text` | `core/text_manager.py:112`（嵌在 `_parse_wechat_json` 内，调用点 `_parse_content` 的 `.json` 分支） | **无** | 两侧都没有 → 只记不修 |
    | `_extract_audio_from_video` | `web/routers/voice.py:99` | **无** | 两侧都没有（且要 ffmpeg）→ 只记不修 |

  - 「判据只长在坏输入一侧」的共 **2 个**，都在 `core/text_manager.py`，本轮同批补完；末两个是**另一条**（「一个会解析用户文件的生产函数两侧都没有判据」），**只记不修**，挂在这里等人立项 —— 它们不是「判据在坏输入侧」，别混进来。
- **变异验证（`D:/Temp/l7_mutation_verify.py`，仓外一次性脚本，每臂一个进程）**：三臂各改**被测函数自己的返回值/异常**，跑完逐字节还原（`git diff` 收尾为空）：
  - A `_extract_pdf` 的 `return pdf_text` → `return ""` ⇒ **红**，红源 `tests/test_text_failure_messages.py:617`，首行 `AssertionError: 合法 PDF 解析不出它自己的字。取到 ''（原文本 '阿朱抬起头，看着窗外的雨。'）——`。
  - B 同一行 → `raise ValueError(_MSG["pdf_parse_failed"])` ⇒ **红**，红源 `ValueError: PDF 解析失败，请确认文件完整后重试`，栈底指到 `_extract_pdf`。
  - C `_extract_docx` 的 `return "\n\n".join(paragraphs)` → `return ""` ⇒ **红**，红源 `:640`，`AssertionError: 合法 DOCX 解析不出它自己的段落。取到 '' —— 期望两段以空行相接。`
  - N **负控**：改一个与本命题无关的常量（`MAX_PDF_PAGES` 2000→1999）⇒ 两条**仍绿**。没有这一臂，「红」可能来自任意扰动而不是来自这条判据。
- **同一形态进矩阵当 S 组（为什么不是登记缺口）**：新判据加进覆盖域（`tests/test_text_failure_messages.py` 本就在 route_facts 域里）后，元锁当场按设计红，点名这两条「长出来了、名单里没有的缺口」，并给三条出路：补一条撞得到的变异 / 删判据 / 去 `lock_coverage_gaps.py` 登记。**登记被否掉的理由是判据 ② 本身**：登记条目对将来清名单的人说的话等于「以后记得补这条变异」，那是**换了个记忆点**，不算机制。故新立 **S 组**（`tests/perf/route_facts_mutations.py`）两条 ——S-1 打 `_extract_pdf` 回空串、S-2 打 `_extract_docx` 回空串，各指到 L7 那一条判据、各带红源 marker。形态取「解析成功之后回**空串**」而不是抛异常：空串正是缺陷 44 表面上最像的样子 —— 上传不报错、只是什么都没拿到，那种坏法日志里没有栈。**S 与 T 互补不是重复**：T 问「失败路径摸没摸到解析器」，S 问「摸到了以后拿到了什么」。
- **读数（现跑现数，与台账 717 行同口径）**：`pg_gate` |D|=49 / |E|=23 / 未覆盖 26；`ping` |D|=33 / |E|=14 / 未覆盖 19；`route_facts` |D|=**69** / |E|=**30** / 未覆盖 **39**（S 组加了 2 条判别器，**两条都被撞到**，故未覆盖**不变**）。合计 |D|=**151**、|E|=**67**、未覆盖 **84**、空转 0、unknown 0。元锁 `tests/test_lock_coverage.py` 25 passed。全量 **1135 passed / 64 skipped / 0 failed**（改前 1133，增量正好 L7 两条）。
- **顺带修的自检漂移点**：`route_facts_mutations._count_gate` 的文档统计正则原先把组数写死成「七组共 N 条」，加 S 组时它是**匹配不到**（报「分组的括号匹配不到」）而不是报「组数对不上」—— 拒跑拒了，但把看红的人引向括号。改成「共 N 组 M 条」，组数与条数都从 `GROUPS` 现算，加组时该说的是「文档说 7 组、现数 8 组」。
- **边界（写明，别当全覆盖）**：L7 两条锁的是**解析函数那一段**；路由那一段（`.pdf` → `_extract_pdf` → 落库）今天只有**负向**用例覆盖到「进得去解析器」，「解析成功之后落库」仍无判据。S 组同理只撞解析函数的返回值。另：夹具里的中文靠 pymupdf **内置**字体（`Font("china-s")` 取到 `Droid Sans Fallback Regular`，磁盘上无对应字体文件），不依赖可选包；若哪天它变回需要 `pymupdf-fonts`，L7 会红在造夹具那一步而不是解析那一步 —— 那条信息足够定位，故不为它单设判据。

**53. `requirements.in` 的注释说了一件**从没发生过**的事 —— 声称的那个 CI 步骤不存在** —— 状态：**已修（`bbbfa5e`，2026-09-17）**（2026-09-16 方案 C 重锁时发现，与白名单那次同族；当时记「记账（不修）」的理由见下面末条）
- **事实（grep 现跑）**：`requirements.in:9` 写着「不要手改 requirements.txt：… 且 CI 会红（build.yml 的 **"Verify installed versions match the lock"** 步骤当场核对）」。`grep -i "verify\|installed version\|match the lock" .github/workflows/build.yml` → **零命中**。真实情况是：gate job 按锁装（`pip install -r requirements.txt`）后跑一句 `python -m pip freeze --all > pip-freeze-gate.txt` **上传成 artifact** —— 那是**留证**，不是**核对**；全仓没有任何一步比对「装到的版本」与「锁里的版本」。
- **第二半（同一处注释的另一条，也是本轮撞上的）**：注释给的重锁配方是**不带约束**的那条 —— 照它逐字执行，本次会混进 **11 个包**的上游漂移（openai / sqlalchemy / urllib3 / yarl / resend / qdrant-client / posthog / propcache / filelock / pyproject-hooks，以及因 `pymupdf4llm` 消失而掉出的 networkx）。**配方本身会产出不可 review 的 diff**，而这正是本次要避免的（用户裁定：重锁用 `--constraint` 求最小 diff）。
- **族**：「文本说了一件没发生的事」—— 与缺陷 51（「实测 2.38.2」测的是 flag 不是行为）同族，也与 §四 第三案例（`.claude/settings.local.json` 里那条凭据前缀**从未生效过**）同族。**判据相同**：以「它是否真的成立」为准，不以「有那么一份文本」为准。
- **处置方向（留档，本轮不实现）**：① 删掉 CI 那句，或改成「CI 按锁装 + `pip freeze` 留证」的事实描述；② 把重锁配方写成**本轮实测可行**的形状（`uv pip compile requirements.in -o requirements.txt --universal --python-version 3.12 --constraint <上一把锁>`，并写明 `--constraint` 会往注解里写约束文件路径、生成后要去掉 —— 见缺陷 44 那次提交的记录）。**不搭车改的理由**：本轮 `requirements.in` 必须**只动一行**（`pymupdf4llm>=0.0.10` → `pymupdf>=1.28.2`），改注释就不是最小 diff，本轮「只删两个包 + 提升一个包」这个可核对的验收口径当场失效。
- **已修（`bbbfa5e`）**：上面 ①② 照处置方向做了，另加一处**本轮测量才暴露**的第三条 —— 原文那句「手改会被下次重锁冲掉」在 ② 这条配方下**反了**：以旧锁为约束时手改的那一版会被**保留**（实测把 `pyproject-hooks` 从 1.2.0 手改成 1.1.0，重锁后仍是 1.1.0）。所以手改的真实危险不是「被冲掉」而是「**生效且不留痕**」—— 锁里那一版凭空在那里，`.in` 解释不了它的来由。三处都改成事实。
- **同源路径普查（条目里没有，本轮补）**：`requirements-dev.in` 的注释本来就写着 `-c requirements.txt`，**无缺陷** —— 实测按它重跑与 `requirements-dev.txt` 逐字节相同（那把锁保留 49 条 `-c` 边，是它自己的产物形状，配方能原样复现它），故不动；`requirements.txt` 头部注记是 uv 按当时真跑的命令写的，对它自己成立，不动。
- **数字订正**：上面「11 个包」是那次（同时删掉了 `pymupdf4llm`）的数目；本轮同一条不带约束的命令实测是 **10 个**（131 个包，无增删，纯上移）。
- **残余（明写进注释里，不假装已机制化）**：注释挡不住不读注释的人。真机制化要在 CI 里跑一次重锁再比对，那要联网 + 新建一套东西，既超出本条范围也违背「不新建扫描框架」。本条做到的是：假记忆点删掉，配方自带验收口径（清理后应与上一把锁逐字节相同 —— 出现差异就说明动了不该动的版本）。

**54. 入库产物 `tests/perf/*_red_lines.json` 的坐标会陈旧，而陈旧时**红在错的地方**** —— 状态：**已修（`23fb813`，2026-09-17）**（2026-09-17 缺陷 41 名单式落地时撞上，与缺陷 45 同族）
- **事实**：产物里的 `mutations` 是**行号**（`文件:行号`），从变异后那次真跑取回、按出现次序搬回变异前坐标系。而**刷新它的唯一途径是人手跑变异驱动**：`build.yml` 的 `pytest tests/` 不跑三个驱动（缺陷 45 记的正是「先验基线不绿就拒跑」那一类前置条件），所以没有任何 CI 步骤会重新生成或核对它。改任何一个锁文件的行数（哪怕只动 docstring）都会让它与树脱钩。
- **实测（本轮两次同源）**：`224fd68` 改了 L6 守卫的 docstring ⇒ `route_facts_red_lines.json`（`339bea4` 生成）里 T-3 的 `:473` 落到一个不再是判别器的行号上。后果是**双重的、而且指错方向**：那条变异被记成**空转**（变异本身没错，错的是它的红源坐标过期了），同时现场凭空多出一条「新缺口」（482 是判别器的**新坐标**，不是新长出来的判别器）。未覆盖 84 → 85、空转 0 → 1。
- **它不是静默的**（这点与缺陷 45 的表述要分清）：陈旧会红，只是**红在错的地方** —— 报「这条变异空转 / 这里有一条没登记的缺口」，而真凶是「产物的坐标过期了」。诊断成本落在人身上，不在锁身上。
- **同一事实的正面余波（已做）**：名单的键因此改成**判别器文本 + 同文本第几处**（`lock_coverage.gap_keys()`），名单本身不再随插行平移 —— 但**产物**仍是行号坐标（当时只修了一半，产物这一半由下面「处置」那条补上）。
- **处置（已修，`23fb813`）—— 选文本身份，否掉指纹，理由记下来**：用户裁定 **「选 (b)，彻底存文本身份」**。否掉 ① 指纹的理由是**它不消除病、只改报错方向**：「坐标仍在，下次改行数照样脱钩，只是脱钩时红在自己名字上 —— 那是把「指错方向」改成「指对方向」，**不是消除**」。② 与 ③ 的成本账没变（② 每轮按分钟到小时计且要先满足 docker / 真服务 / 真 PG；③ 违背 §四「证据产物必须入库」）。裁定前记的判据是：**41 已经把名单那半边改成文本身份，产物这一半还是坐标 —— 同一个病只修了一半。**
- **修法：行号算出来，不存下来。** 产物里 `mutations` 的值改成判别器的**身份** `[文件, 判别器源码, 同文本第几处]`，与 `lock_coverage_gaps.ALLOWED_GAPS` 的键**同一套**（都由 `lock_coverage.discriminator_identities()` 算出，而它就是 `gap_keys()` 对**整个域**的一次调用 —— 沿用 41 已建的判据，没有第二套身份，也没有第二份手工清单）。两侧的 `nth` 都是**文件内全局**序号：逐条变异各算一遍会让同文本的两处塌成同一个身份，这是它必须一次收全的理由。行号仍在用，但只作为**当场算出的中间量**（`decode_recorded` 把产物身份翻回当前树的行号，`gaps()` 一行没动）。
- **新不变量：产物失效只剩一种方式 —— 判别器自己的源码文本变了 / 被删了。行数变了不算。** 残余失效也**单独报**，不混进旧的那两个假象：新增的断言排在 `gaps()` 之前，红源是「产物里这些判别器身份在当前树里找不到（判别器被删了、或它的源码文本改了 —— 产物只在这两种情况下失效，行数变了不算）＋ 点名身份 ＋ `重跑一次驱动刷新产物：python <驱动>`」。**若把它丢给 `gaps()` 去翻，同一次陈旧会同时造出「空转」与「新缺口」** —— 那正是本条要消除的那副「两个成因共用信号」的面孔，故顺序本身是判据的一部分。
- **重跑三个矩阵（一次性成本，裁定接受）后逐项核过覆盖闭合没有漂移**：原始帧 → 判别器命中 ping 24→14、pg_gate 61→23、route_facts 45→28，与 |E|=14/23/28 逐项相等；未覆盖 **19/26/39 = 84** 与改前**逐条相同**、空转两侧同为 0、未知身份 0。条目少了的是**非判别器帧**（中间帧、包装内部那条 `raise`）—— `gaps()` 读的时候本来就会滤掉，写入侧现在提前做了这一步，故这类帧**不再入库**（代价如实记：产物里少了「它红在一个非判别器的行号上」这一层原始信息，而那一层从来只是噪声）。
- **变异两臂（仓外脚本，跑完逐字节还原；验的是「那条路不存在了」而不是「代码看起来对」）**：① 给 `tests/test_route_facts.py` **头部插一行**（只改行数、不动判别器文本）→ **绿**，且不含「空转变异」也不含「长出了名单上没有的缺口」—— 旧设计下这一臂正是上面「实测」那条记下的两个假象，**这条路现在不存在**；② 把一条**被撞到的**判别器 `assert len(ops) > 0, "…"` 改成 `assert len(ops) >= 1, "…"`（语义等价的改写，断言照样通过）→ **红**，红源是新增那条、**不含**那两个旧假象。臂 ② 顺带确立残余边界的**真实口径**：连语义等价的改写都要重跑矩阵 —— 因为产物记的是「被撞的是哪条判据」的**文本**；而纯排版（换行、缩进、多余空格）**不算**改动，判别器摘要走 `" ".join(seg.split())` 归一化（臂 ② 第一版就是插了个空格，当场全绿）。
- **元锁自己的新判据由合成输入承担**（它不在覆盖域里，给元锁写驱动 = 把本文件全部 assert 拉进域，那不是守卫是洪水）：整段前移 20 行后身份不变、`decode_recorded` 翻回**新**行号、未知身份单独报出来 —— 一条用例三件事一起控，任一处塌陷就红。全量 1133 passed / 64 skipped / 0 failed（改前 1132，增量正是这条）。
- **仍未解决的（如实留着，不在本条射程）**：产物仍靠**人手跑驱动**刷新（② 那条的成本没变），所以「判别器文本变了而没人重跑」这件事今天只能靠**本元锁在 CI 里红**来发现 —— 红得住，但发现得晚（推到 CI 才知道）。这一格与本条修的是两件事：本条消除的是「无关改动让产物脱钩」，那一格是「产物本身需要重跑而没人跑」。
- **同源普查（本轮补，防「改点名的那处、同源的记账」）**：全仓 JSON 产物里的 `文件:行号` 现跑现数 —— `docs/evidence/*.json` 与 `tests/perf/*.json` 共 14 份，**只有两份带坐标**：`ownership-reachability.json` 与其 `PROBE_NO_KEY=1` 孪生（各 49 处，形如 `web/routers/card.py:38:get_card_avatar`）。**它们不是同源未修项，理由是判据键不是行号**：`tests/perf/check_reachability.py` 比的是 `site` 里的**函数名**（`EXPECTED[用例短名] == (期望函数名, 期望状态码)`），行号只是随行的上下文，陈旧不改变任何判定；且它没有变异矩阵、没有缺口名单、没有与树对账的锁，故也没有本条那种「闭合被坐标骗了」的机理。**只记不修**（三个 `*_red_lines.json` 已是 0 处坐标）。

**55. `_create_session` 按位置传参 → 用户的 embedding key 落进 `user_role`（凭据进 prompt + 明文落库）** —— 状态：**已修（`f69dfad`，2026-09-17）**（评审代号 G；实跑确认 2026-09-17）
- **缺陷本体**：`web/routers/history.py` 调 `text_manager._create_session` 时按**位置**传 8 个实参，而签名是 `(text, card, all_characters=None, rag=None, card_id="", user_id="", user_role="", embedding_key="", embedding_region="")` —— 第 7 位是 `user_role`，于是 `user_role := emb_key`、`embedding_key := emb_region`、`embedding_region := ""`（静默降级为默认值）。
- **为什么五处只有一处翻车 —— 这是修法落在签名上、不落在调用点上的理由**：签名有 7 个可位置传的可选参数，**全是 str**，错位不报错。五个调用点里 `core/text_manager.py:522`、`:584`、`web/routers/chat.py:187`、`web/routers/distill.py:1416` 四处**恰好停在第 6 个参数**（`user_id`）为止。`distill.py:1416` 是最清楚的证据：**6 个位置 + 1 个关键字**，位置到 `user_id` 为止刚好没错位、多传一个就错。**「今天对」是边界巧合，不是设计** —— 调用点会增加、会重排，所以判据只能写在签名上。
- **后果链（实跑确认，非推断）**：`user_role` 进 `ChatEngine` → 进 prompt（`chat_engine.py:793`、`:1373`）→ `_compute_initial_affinity` 是**纯本地函数、不调 LLM**，其 `reason` 由 f-string 直接插 `{user}` → 赋给 `_affinity_reason` → `chat_engine.py:701` 的 `_evaluate_affinity` **无条件**调 `_save_affinity_state()` → `affinity_state.reason` 落库。**确定性成立**，不靠 LLM 回显。
- **实跑证据（`D:/Temp/g_verify.py`，仓外一次性脚本）**：夹具走真路由（真 `history_router` / `chat_router`、真 `TextManager`、真 `SQLiteStore` + 临时库），只 stub `get_user_llm`（假 LLM，不发网络）/ `get_storage` / `IndexingService.get_rag_for_session → None`；**key 用假的 `sk-TESTKEY-…`，真凭据全程不碰**。
  - A（绕开路由，逐字复刻 `history.py:251` 的位置实参）→ `engine.user_role = 'sk-TESTKEY-…'`、`_affinity_reason = '测试角色 不认识sk-TESTKEY-…，态度谨慎'`；关键字对照组 `user_role='读者'`，key 不出现。
  - B `POST /api/history/{sid}/resume` → 200，`engine.user_role` = 假 key，**0 条 LLM prompt**（resume 自身不调 LLM）；此后库里仍干净 —— 因为 `history.py:315` 是在 `load_affinity`（`:310`）**之后**才设 `engine._storage`，`_save_affinity_state` 空转。
  - C 再发一条 `POST /api/chat/send` → 200 → `sessions.affinity_state` 的 `reason` 含明文 key、`affinity_initialized=1`，**整个 DB 文件字节里含明文 key**（而 `users.embedding_key` 那一列是 Fernet 密文）；3 条 LLM prompt 里 **2 条含 key**（一条「你正在和「sk-TESTKEY-…」对话」，一条「对话者身份：sk-TESTKEY-…」）。落库那条 reason 是在评估失败之后留下的（`EVAL FAILED … no JSON object found`）—— 正是那句无条件保存把它落了盘。
- **射程（实测划出的边界，不是推测）**：**只在该 session 存的 `user_role` 为空/falsy 时触发**。第二组用 `user_role="朋友"` 重跑：resume 后 `user_role='朋友'`、reason 用真角色重算、**0/3 条 prompt 含 key**、库里无 key —— 因为 `history.py:299-300` 会用 DB 里的 `user_role` 覆盖回去（那个兜底本身另立第 57 条）。**而 `user_role` 为空恰恰是默认态**（`/start_session` 建成的会话、更早的会话都可能没写角色），故射程不小。
- **这条路径存在多久**：`git blame` 于修复前 → 第 258/259 行那两个多余的实参是 `db8d1de8`（2026-06-24，换成阿里云 text-embedding-v4 那笔）加进来的；在此之前实参个数正好是 6、与签名前 6 位对齐。即错位窗口 ＝ **2026-06-24 → 2026-09-17（约 85 天）**。
- **生产落地排查（2026-09-17，只读，两台）**：在 app 容器内跑扫描（脚本 `D:/Temp/g_leak_scan.py`）。判据**不用 key 前缀猜** —— 把每个 `users.embedding_key` 在内存里解密，检查其明文（及去 `sk-` 前缀后的片段）是否出现在 `sessions.affinity_state` **与 `sessions.affinity_reason`** 里。**SZ 0 命中 / SG 0 命中**（SZ：10 用户 / 2 个配了 embedding_key / 4 条非空列会话；SG：11 / 2 / 13）。**扫描口径本身是判据的一部分**：key 不出容器、不打印、不落文件，命中只报 session_id —— 将来再做同类扫描照这个口径。
- **但 0 命中不是强证据（写下防误读）**：它是「**没踩上**」，不是「**踩不上**」。样本小 —— 两台各只有 2 个用户配了 key、非空列会话 4 / 13 条。**降级为「未遂」是基于运气，不是基于防护**：不清理、不轮换，但结论是「没发生」，不是「这条路安全」。
- **修法（根除，不是改一行）**：`core/text_manager.py` 的 7 个可选参数全部移到 `*` 之后（keyword-only），五处调用点逐个改关键字。**新来的人按位置传就直接报错，不需要知道这段历史** —— 机制而非记忆点。`_create_session` 的行为逻辑一行未动。
- **五处调用点全集**：`core/text_manager.py:522`、`core/text_manager.py:584`、`web/routers/chat.py:187`、`web/routers/distill.py:1416`、`web/routers/history.py:251`。`tests/` 里的同名符号都是测试自己的局部 helper 或 `_StubTextManager` 方法（签名 `*_a, **_kw`），不是本函数的调用点，不受影响。
- **锁与变异（`tests/test_create_session_kwonly_lock.py`，3 条）**：① 签名里第 3 个及以后的参数 kind 只能是 `KEYWORD_ONLY`；② 位置越界调用必 `TypeError`；③ 正控 —— 关键字绑定按名字对号（钉住 `user_role` 收角色、`embedding_key` 收凭据，正是当年串位的那两格）。**变异实测**：删掉签名里的 `*` 分隔符 ⇒ ①②**双双变红**（`FAILED … test_optional_params_are_keyword_only` / `… test_positional_optional_arg_raises_typeerror`），③ 保持绿；逐字节还原后三条全绿。全量 **1144 passed / 64 skipped / 0 failed**。
- **隔离（机械核）**：生产代码只动 `core/text_manager.py` + 三个 router，`git diff --stat` = 26 insertions / 13 deletions；`chat_engine` / `affinity_service` 一行未动。
- **顺带发现（只记不改）**：`embedding_key` / `embedding_region` 在 `_create_session` **函数体里从未被使用** —— 死参数。它们存在的唯一效果是给位置传参多留两格错位空间（当年 `history.py` 正是多传了这两个）。删掉能进一步缩小表面，但那超出本条批准的范围，故只记。

**56. 「哪些列该加密」在本仓没有守卫 —— 该加密的加了，泄漏的那份是明文** —— 状态：**已修**（`c826ed8`，2026-09-23）
- **事实**：`users.embedding_key` 落库前过 Fernet（`storage/sqlite_store.py:2755`、`storage/postgres_store.py:2341`），而它泄漏出去的那一份落在 `sessions.affinity_state` —— 一个 **明文 TEXT 列**（`storage/migrations_pg/015_affinity_state.sql`；SQLite 侧同）。
- **不是「忘了加密某一列」，是「没有任何东西规定哪些列该加密」**：加密与否只在每个写入口手写一次 `_get_fernet().encrypt(...)`，既没有敏感列的名单，也没有「新增敏感列必须登记」的判据。**今天是 `affinity_state` 撞上，明天可能是别的列。**
- **与 55 的关系**：同一次事件的两个面 —— 55 是**值走错了格子**，56 是**正确的值落在没有保护的格子里**。只修 55 不会让 56 消失（下次凭据从别的路径进别的列，同样没人拦）。
- **本条的处置方向被 spec 改写（对原方向的推翻）**：原文提的「敏感列名单 + 写入路径守卫」**被 spec §2.5 明确否决**（「**不建"敏感列名单"或注册表**」）。理由成立：名单是**第二份手工清单**，与它要守的对象之间就是漂移点 —— 与缺陷 21 的豁免名单、25 的命名代理、24 的调用点清单同谱系，本仓已有成堆先例。改为**直接守住结果**：一条金丝雀走完「保存配置 → 建会话 → 重建 → 评估落库」整条链路，扫**全库所有文本列**（SQLite 另扫文件字节）断言明文一次都没出现；漏了哪一列它就当场红，不需要任何人去名单里补一行。
- **落地**：加解密的两份实现（两个 store 各自一份 `_get_fernet` + 各自的解密闭包）收进 `storage/secret_box.py`（`encrypt_secret` / `decrypt_secret`）。派生规则原样保留：`FERNET_KEY` 优先，否则由 `JWT_SECRET` 经 sha256 派生，两者都没有则**硬失败并点名变量**，不退到内置常量。
- **判据（spec §5 行 56）**：`git grep -n "Fernet(" -- storage` **只命中共享模块** `storage/secret_box.py`（本文件自己的 docstring 里那处引用也在这个文件内，计数=2 行、文件集=1）。**变异**：在任一 store 里恢复自己的 Fernet 实例化 → grep 当场多出该 store 的命中。**已实测**（把一段 `_get_fernet` 塞回 `sqlite_store.py` → `storage/sqlite_store.py:6006: return Fernet(b"x")` 出现；恢复后回到只剩共享模块）。
- **金丝雀红源（走值流，不是走名单）**：`tests/test_secret_never_plaintext.py`，SQLite / PG 各一条，同一个事件循环内直驱生产函数（**不经 HTTP** —— HTTP 链上那个跨事件循环用连接池的问题就是缺陷 123 的根因，不在本 spec 射程内）。**扫描前先断言 `affinity_state` 确实非空**：夹具哪一步没跑起来时「全绿」只说明没东西可泄漏，那是最难发现的假绿。**变异**：按 55 的形态把凭据塞进 `user_role`（`_create_session(..., user_role=emb_key)`）→ 两条同时红，且**红在 55 的那一列上**（SQLite 报 `表 sessions.affinity_state × 1` + `库文件字节`，PG 报 `表 sessions × 1`）。变不红就停下报告 —— 已实测两条都红。
- **量尺草稿作废**：`e2e/scratch/census_sensitive_columns.py`（2026-09-20 的本地草稿，判据 (a) 有两处已知缺陷、读数本就不可信）随本条处置方向被推翻而**整体作废**，不再是任何后续工作的起点。那句「启动 56 时按值流重写判据后再入库」也随之失效 —— **值流判据已经以金丝雀测试的形态入库**（上一条），不必再回来写解析器。

**57. `history.py:299-300` / `chat.py:205-206` 的 `user_role` 兜底：靠它才没漏，但没人知道它为什么存在** —— 状态：**已修**（`8580286`，2026-09-23）
- **它承担什么**：会话重建走 `_create_session` 时 `user_role` 归一为空串，这两处**从 DB 行把它覆盖回来**（`if db_session.get("user_role"): engine.user_role = db_session["user_role"]`）。缺陷 55 的射程边界（「有角色的会话不漏」）**正是由它划出来的** —— 实测 `user_role="朋友"` 的会话 0 泄漏。
- **为什么必须记账**：它长得像一句可有可无的冗余赋值，实际是**当前唯一挡在凭据泄漏前的东西**。这类「没人知道为什么存在的兜底」将来会被顺手删掉；删掉它，55 的射程当场从「空角色会话」扩到「所有会话」。**写明它现在承担什么，就是为了让它不至于变成那种没人敢动、也没人知道为什么在的代码。**
- **处置**：不修、不动。55 修完后它是否仍必要是**另一个问题** —— 它现在还兼着「重建后角色不丢」的正常职责，不只是安全兜底，所以「55 已修 → 可以删它」是错的推论。
- **本轮只做两件事（代码一行未改）**：① 两处兜底旁各加一句注释，写明它把角色从库里恢复回来、**同时是缺陷 55 射程的边界**（进程内的旧会话一没，脏角色值只能经这里流回引擎、再随 prompt 进模型）；② 各配一条专属测试。
- **为什么两条而不是一条**：`_ensure_session`（发消息时懒重建）与 `resume_session`（显式重连）是**两条独立的重建路径**，触发入口不同、谁先跑取决于客户端行为，一条测试覆盖不了另一条。`tests/test_session_user_role_restore.py::TestEnsureSessionRestoresUserRole` 与 `::TestResumeRestoresUserRole`，断言都是「重建后 `engine.user_role` == 库里那一份」。
- **变异 / 红源**：删掉任一处兜底 → 对应的那条红（引擎角色停在初值 `""`，不是库里的值）；**已实测两条同时删时两条都红**。另有 `::TestEmptyRoleStaysEmpty`（库里角色为空时不许把引擎既有值抹成空串）锁住 `if` 的真值判断那一半 —— 它守的是「判断」不是「赋值」，删赋值它不红、删判断它红。

**58. 顺带查：还有哪些「多参数按位置传」的高风险函数** —— 状态：**已修**（2026-09-23，全部 9 个函数的可选参数加 `*`、调用点改关键字传参，见「本次处置」段；2026-09-17 缺陷 55 同批普查；同日重核，其中的 `save_message` / `create_user` 两处**已修**，见「重核」段）
- **判据（用户给的）**：可位置传的可选参数 ≥3 个，**且**有 ≥2 个调用点按位置传（位置实参数 > 必填形参数数）。
- **扫法**：`D:/Temp/positional_risk_scan.py`（仓外一次性脚本，AST，**按函数名匹配调用点**）。名字匹配是**启发式，会跨文件误配同名函数** —— 所以下面每条都**逐条核过定义与调用点是不是同一个函数**，核不上的（`list_cards`、`send_message`、`_req`、`app_api`、`_ScriptedCollection.query` 等）已剔除；`e2e/scratch`、`data/eval_scratch` 的一次性脚本也不入表。初扫 20 条候选 → 核后 **8 条**。
- **已核的全集（生产代码）**：

  | 函数 | 可位置传的可选参数 | 按位置传的调用点 | 现状 |
  |---|---|---|---|
  | `_do_chat`（`web/routers/chat.py`） | 10 | 2（一处传满 10 个；一处传 8 个 + 4 个关键字） | 对 |
  | `save_text`（`storage/base.py` + 两个实现） | 5（两个实现另有 2 个尾部参数） | 2（`core/text_manager.py` 两处，各传 8 个位置实参） | 对（契约不完整，见「重核」段） |
  | `save_message`（`storage/base.py` + 两个实现） | 4 | 2（`chat.py` 两处，各传 6 个） | **已修**（`882d094`；原是 `base` 声明顺序与两个实现不一致，见下） |
  | `Distiller.distill_incremental_stream`（`core/distiller.py`） | 4 | 2（`web/routers/distill.py` 两处） | 对 |
  | `try_record_usage`（`core/utils.py`） | 3 | 11 处，各多传 1 个 | 对 |
  | `IndexingService.get_rag_for_session`（`core/indexing_service.py`） | 3 | 2（`chat.py`、`history.py` 各一处，各传 5 个） | 对 |
  | `IndexingService.schedule_scene_index`（`core/indexing_service.py`） | 3 | 3 处，各多传 1 个（`embedding_*` 走关键字） | 对 |
  | `save_group_message`（两个实现，base 无声明） | 3 | 5（`web/routers/group.py`） | 对 |
  | `create_distill_task`（`storage/base.py` + 两个实现） | 9 | 1（`web/routers/distill.py`；另 20 处为测试调用点，全仓共 21） | 对（**漏记**，2026-09-23 补） |

  > 表内一律用符号名不用行号（§四：行号随改动漂移且无测试报警）。本批的改动**当场把上一版抄下的行号全部打死**，故一并改写 —— 这正是那条规矩要防的形态。
  > **`create_distill_task` 一行是 2026-09-23 补记的漏项**：它与表内 8 行同判据、同形态（4 位必填后的 9 个可选参数全可位置传，21 个调用点把 `character` 按第 4 位传），2026-09-17 那次普查按函数名匹配扫到了 `_do_chat` 一族却把它漏下了表 —— 不是判据没覆盖，是**表是人工抄的**（同段末「这张表靠人重跑脚本维持」的边界）。本次一并加 `*`。

- **头号发现（新，与 55 同族 —— 一份会误导人的签名）**：`StorageBase.save_message` 声明的是 `(…, rag_context, retracted=False, reply_to_id=None, reply_to_preview="", evidence=None)`，而两个实现都是 `(…, rag_context, reply_to_id=None, reply_to_preview="", retracted=False, evidence=None)` —— **第 5/6 位互换**。今天没有调用点受影响（调用点走的是具体实现，两个实现彼此一致，所以是「两份声明不一致」而不是「两种行为」）；**但将来照 base 写第三个实现，`chat.py` 那两处 6 个位置实参会静默错位**（`reply_to_id` 落进 `retracted`）。这正是 55 的形态在另一处待着。
- **不修的理由**：本批的批准范围是「55 的签名 + 五个调用点」。这 8 条今天**全部正确**，改它们是 8 处重组而不是修 bug；而「要不要全面禁止多参数按位置传」是个跨全仓的取舍（像 `_do_chat` 那种 10 个可选参数的签名，改 keyword-only 会让两个调用点各长十几行），要单独裁定。**边界如实写明：这张表是「今天核过的事实」，不是判据** —— 它靠人重跑脚本维持，代码一变就滞后（同 §四「报数字前现跑现数」的处境）。**注意这 8 行的「对」与「重核」段修掉的两处不是同一件事**：8 行说的是「按位置传这件事本身今天没出事」，重核修的是「base 声明与实现不一致」（`save_message` / `create_user`）—— 后者是**会静默的契约缺陷**，与前者的「今天正确」并存不矛盾。

- **本次处置（2026-09-23，用户）—— 上表全部 9 个函数的可选参数加 `*`，调用点改关键字传参。**
  - **范围与判据**：表内 8 行 + 漏记的 `create_distill_task` = 9 个函数；`save_message` 上一批已修（`882d094`），故实际动手 **8 个**。判据照旧（可位置传的可选参数 ≥3 **且** ≥2 个调用点按位置传），**不新增条目、不新增测试**。
  - **改法**：只在必填参数之后插 `*`，可选参数一律 keyword-only；**签名面 base 与两个实现逐格同改**（`test_storage_contract_shape.py` 按 `(名字, 种类, 有无默认值, 默认值)` 逐格比，`*` 会把 `kind` 变成 `KEYWORD_ONLY`，漏改一侧即红）。调用点按位置传的改关键字：`try_record_usage` 11 处、`save_text` 2 处（`core/text_manager.py`）、`schedule_scene_index` 3 处、`get_rag_for_session` 2 处、`_do_chat` 2 处、`distill_incremental_stream` 6 处（2 处生产 + 4 处测试）、`save_group_message` 6 处（5 处生产 + 1 处测试转发）、`create_distill_task` 21 处。
  - **边界（如实写明）**：`web/routers/chat.py::_do_chat_stream` **未改**。它与 `_do_chat` 同形（必填 4 / 可选 10），但全仓只有 **1** 个调用点按位置传，**不满足 ≥2 的中心判据** —— 不扩大范围，此处只登记。
  - **验收**：AST 普查（按函数名 + 逐条核定义与调用点是不是同一个函数，剔同名桩）在合并后代码上重跑，9 个函数的「位置实参数 > 必填形参数数」命中 **0**；每个定义 `posonlyargs + args` 的默认值数 **0**（即可选参数零可位置传）；干净形态全量 pytest 绿。

- **重核（2026-09-17，本批动手前）—— 范围从「这 8 行」放开到 `StorageBase` 全部 91 个 `@abstractmethod`。** 判据从事实推出（抽象方法集取 `StorageBase.__abstractmethods__`，`ABCMeta` 现算，**不维护函数名单**），逐个比 base 与两个实现的形参表。结果：**不一致的只有 3 个函数**（「只在部分实现里存在」= 0，「两个实现都没有」= 0）。重核要找的是两类**今天看起来是对的**的形态 —— 「base 与实现不一致」与「死参数留出错位空间」，它们比单纯的「多参数按位置传」危险：

  | 函数 | 形态 | 今天会静默吗 | 处置 |
  |---|---|---|---|
  | `save_message` | 第 5/6 位互换 | 会（触发条件未到） | **已修** `882d094` |
  | `create_user` | 实现第 5 位插入 `email`，base 没有这一格 | **会**（已有人在传那一格） | **已修** `baf9719` |
  | `save_text` | base 少声明 2 个尾部参数 | 不会（尾部 → TypeError） | 记表不修（锁内 `strict xfail` 入册） |

- **`create_user` 比 `save_message` 更近一步 —— 这一族不是理论风险。** `save_message` 还停在「**如果有**第三个实现」，`create_user` **已经有人在按位置传那一格**：`tests/perf/distill_resume_reachability.py:53` 与 `tests/perf/distill_orphan_matrix.py:72` 都是 `create_user(uid, uid, "x", f"{uid}@t.local")` —— 按实现是 `email`，按 base 是 `home_region`。**今天对，是实现赢了，不是写法对**；两个脚本虽在 `tests/perf/` 但是真调用点，一并改了关键字。
- **这一族有了真判据：** `tests/test_storage_contract_shape.py` —— 抽象方法集取 `__abstractmethods__`（不维护名单），逐格比 `(名字, 种类, 有无默认值, 默认值)`。选「形参表逐格相同」而不是「可选参数 keyword-only」：**后者只防调用点错位，防不了契约漂移**；前者两者一起管住（种类里已含 `KEYWORD_ONLY`）。变异已验：顺序漂移 → 红并点名到第几格；撤掉 `*` → 红并点名种类差异。**本表「靠人重跑」的边界因此收窄一格** —— 表本身仍要人重跑，但「base 与实现有没有漂移」已由锁日常看着。
- **`save_text` 为何不搭车（是判据，不是省事）**：它是**纯尾部追加**，前缀逐格相同 —— base 派调用点能到的位置**没有一个**改变含义（不像 `save_message` 的互换、`create_user` 的中间插入）。**是契约不完整，不是契约漂移，会响不会哑**；且补齐 base 声明要连带处置两处 8 位置传参，是独立一件事。锁里以 `strict xfail` 入册（`PRE_EXISTING_GAPS`），**补上后 XPASS 变红、逼人出册** —— 该机制在 `create_user` 上已生效一次（`baf9719` 同时删掉它的册条）。
- **`_create_session` 的第三个死参数 `text`（第 1 位必填）** —— 与 55 那两个死参数同函数、同形态，但**必填参数不会被静默错填**（少传即 `TypeError`），且删它要动 4 个调用点。**记表不修**。§四 已记「死参数为错位留出空间 —— 先问这个参数有没有人用，再问怎么传」。
- **死参数普查的边界（不冒充通用判据）**：全仓 189 处「形参在函数体里零引用」，**绝大多数是 FastAPI 注入的 `request` / 依赖参数与 `__aexit__` 这类协议参数**，且**抽象方法的参数必然零引用**（体只有 docstring）—— 上一版没排除这类，把 `StorageBase.save_text` / `save_message` 全判成死参数。可判定的组合是**三者同时**：零引用 **+** 可位置传 **+** 与相邻参数名字/类型相近。故普查只作**改签名时的自查**，未做成锁（同 §四「『全仓写死的实测数字』这件事做不出通用判据」的处境）。

**75. `DELETE /api/text/{text_id}` 的副作用跑在鉴权之前 —— 已登录用户能对**他人**的文本落一次持久写** —— 状态：**已修**（`af78195` `2ff7872` `2844d43` `fde2841`，2026-09-20）
- **事实**：`web/routers/text.py` 的两个删除端点（`delete_text` / `permanent_delete_text`）里，副作用先于授权执行。顺序是 `cancel_upload_tasks_by_text_id` → `routers.distill.cancel_distill_tasks_by_text_id` →（`keep_cards=true` 时）`storage.detach_text_cards` → **最后**才是 `core.trash_service.soft_delete` / `hard_delete`。属主校验只在后两个函数**内部**做，故「非属主 404」发生在副作用全部落定**之后**。
- **为何是越权写、不是无效操作**：`detach_text_cards` 的 SQL 谓词里**没有 `user_id`** —— SQLite 侧 `UPDATE cards SET text_id = '' WHERE text_id = ?`，PG 侧 `UPDATE cards SET text_id = NULL WHERE text_id = $1`。故 `DELETE /api/text/{他人的 text_id}?keep_cards=true` 会真把对方的卡从文本上摘掉（`text_id` 置空、卡与文本解绑），且 `cancel_*` 会掐掉对方在途的蒸馏/上传任务。
- **可达性**：`get_current_user` 只保证「已登录」，路由自己不校验属主。故它是**已登录的跨用户写**，不是匿名写，也不是「只错在状态码」那一类。
- **两处同形态**：`delete_text` 与 `permanent_delete_text` 各一份，`cancel_*` 那两行两份逐字相同 —— 修一处不改另一处**不会报错**（§四「同一个行为被复制多遍」）。
- **与缺陷 19 / 25 同族，但落在写侧**：那两条管的是**读**原语没有身份概念（判据读命名后缀 / 签名参数）；这条是**写**原语 `detach_text_cards` 无身份过滤，且非法窗口开在**鉴权之前** —— 不是判据写错，是**顺序**写错。故 `tests/test_storage_scope_lock.py` 那类锁看不见它（它既不叫 `*_unscoped`，也不在带身份的仓储入口上）。
- **本次处置（用户，2026-09-20）**：先只立项；同日方案审定后按下面四个 commit 修复。
- **修复（四个 commit，`7a588e4` 之上）**：
  - `af78195` **先红**：`tests/test_text_delete_side_effects.py` 9 例 —— 非属主两个端点 ×（带/不带 keep_cards）→ 404 且**三样副作用零发生**（卡片 text_id 不变、`_upload_tasks` 仍 parsing、内存 `_tasks` 与 `distill_tasks` 行仍 running）；属主与 admin 正控**三样都发生**（无正控则「什么都不做」也全绿）；未知 id 404 不崩。实测先红 **4 failed / 5 passed**，每条红的断言正落在其红源上。
  - `2ff7872` **storage**：`UPDATE cards SET text_id` 原先每个 store 写两遍（公有 `detach_text_cards` 与 `hard_delete_text` 内联），收敛成私有 `_detach_text_cards_tx(conn, id)`（不 commit，事务边界归调用方）；`delete_text` 增加 `keep_cards`，True 时在同一事务内**先断开再软删**（异常整体回滚）。SQLite 侧 PRAGMA OFF 必须落在事务开始之前，故顺序保持原样。实测（`e2e/scratch/probe_delete_text_keep_cards.py`）：卡片 `text_id=''`、无 FK 报错、文本 `deleted_at` 非空。
  - `2844d43` **core**：`soft_delete` / `hard_delete` 增加 `before_mutation` 钩子（在 `_fetch` 与 hard 的 `deleted_at` 校验**之后**、第一条存储写**之前** await）与 `**op_kwargs` 透传；顺带删掉 `hard_delete` 里 `if entity_type == "text"` 的特判 —— core 不必知道哪个实体有 `keep_cards`。`restore` 不加（无副作用）。
  - `fde2841` **route**：两个端点的副作用收敛成 `_cancel_text_tasks(record)`（内含 `cancel_upload` + `cancel_distill`），经 `before_mutation` 注入；`detach_text_cards` 不再由路由调用（改由 `delete_text(keep_cards=True)` 在事务内做）。步骤 1 的 9 例转绿。
- **变异验证**：把 `before_mutation` 的调用从 `_fetch` 之后挪到之前 → 恰打红那 4 条非属主用例（正控 5 条不受影响），逐字节还原（sha256 `7e9cca68…b2fb`）。
- **判据 grep（修后）**：`web/routers/text.py` 的 `detach_text_cards` = **0**；`cancel_*` 的调用只出现在 `_cancel_text_tasks` 内（各 1 处）；`core/trash_service.py` 的 `entity_type == "text"` = **0**；每个 store 的 `UPDATE cards SET text_id` 字面量 = **1**。
- **全量**：1362 passed / 64 skipped / 1 xfailed = **1427 collected**（基线 1416 + 缺陷 75 用例 9 + `tests/test_storage.py` 新增的 `delete_text` keep_cards 分支 2）。
- **为何钩子不能挂在路由层「鉴权先跑一次 `fetch_for_actor`」**：那正是本条的形态来源 —— 路由判完属主再自己跑副作用，副作用与删除之间又隔着一次取数，`detach_text_cards` 与软删仍不在同一事务。钩子放在 `_fetch` 之后、存储写之前，顺序由**一个**地方保证，两个端点不会再各写一份。
- **PG 实跑（一次性容器，`postgres:16-alpine`，环回 + trust，不进生产库）**：新增 `tests/test_postgres_store.py::test_delete_text_keep_cards_detaches_in_same_tx`（断言 `text_id is None`）与 `..._default_keeps_card_attached`（正控），两文件合跑 **57 passed**（原 55 + 2），`-k delete_text` 3 passed。故 PG 分支**不是静态推断，是实跑读数**；本机默认无 `DATABASE_URL` 时这些用例照旧 skip，CI 的 `REQUIRE_PG_TESTS=1` 会真跑。
- **产品级（活实例 `127.0.0.1:7861`，两个账号 A=`testadmin` / B=`testuser`，全程应用自己的接口）**：B 对 A 的 text 发 `DELETE ?keep_cards=true` 与 `DELETE /permanent?keep_cards=true` → 两个 **404**，A 的卡片 `text_id` 不变、text 仍在；A 自己 keep_cards 软删 → 回收站可见 → `POST restore` → 卡片已断开（`text_id=''`）且独立存活。**注意**：本地这套 compose 的 `STORAGE_BACKEND=sqlite`（虽配了 `DATABASE_URL`），故本条走的是 SQLite store，PG 的凭据是上一条的一次性容器。
- **残余风险**：无已知功能性残留。若要挑刺：`before_mutation` 的**类型**只是 `Callable[[dict], Awaitable[None]]`，传一个同步函数进去要到 `await` 那一刻才炸（`TypeError`），没有静态拦截 —— 与 `capture` 那类「运行时才响」同形，但调用点只有一处、由 9 条用例覆盖。
- **同族**：与缺陷 76 同源于这次验收；两者都不是判据写错，而是**顺序 / 间接层**写错 —— 76 是名字成了数据，75 是副作用早于授权。

**76. 回收站四类实体全灭 —— ENTITY_MAP 按名取数，改名漏改后字符串悬空、静默 500** —— 状态：**已修**（`4ecd58e`…`9ce0cf9` + `cae6c37`，2026-09-20）
- **现象**：`DELETE /api/text/{id}` 返回 **500** `{"detail":"Storage missing method: get_text"}`；`soft_delete` / `restore` / `hard_delete` 对 **card / session / text / group 四类实体全部不可用**（SQLite 与 PG 同 —— 两个 store 都只剩 `*_unscoped` 名）。回收站整套失效。
- **根因**：`core/trash_service.py` 的 `ENTITY_MAP` 把 storage 侧方法名**当字符串存**（`get="get_text"` …），再由按名反射解析调用。**名字一旦是数据，改名就与调用点脱钩** —— AST 锁判的是名字**长什么样**（调用 / 属性引用 / 字符串常量三种形态），而这里名字在 dict 里、取数在别处，改名漏改既不报错也不打红，直到运行时才 500。静态检查同理。
- **时间线（逐 commit 核过，非转述）**：
  - `fbf4077`（2026-06-26）建 `TrashService`，`ENTITY_MAP` 用当时有效的旧名（`get_text` / `get_session` / `get_card` / `get_group_session`）。
  - `da0e3b3`（2026-09-11）`get_text` / `get_session` 能力拆分，**同时在 `storage/base.py` 留了过渡别名**（`async def get_text(self, id)` → 转发 `get_text_unscoped`，随继承生效）—— 此时 `trash_service` 尚能解析，**未坏**。
  - **`f7bd92a`（2026-09-11）删掉过渡别名**（`storage/base.py` −8 行）→ `get_text` / `get_session` 悬空，**text / session 两类失效**。
  - **`e3cf9e8`（2026-09-13）B1 补完其余读原语的 `*_owned`** → `get_card` / `get_group_session` 消失，**card / group 两类失效**。
  - 中间 `core/trash_service.py` **一次都没被改过**（`git log -- core/trash_service.py`：`fbf4077` 之后直接跳到 `c881aa5`）。
  - 2026-09-20 验收清理时撞到：text/session 已坏 **9 天**、card/group **7 天**。
- **为何 9 天无人发现（两处同时缺位）**：① `core/trash_service.py` 的**派发层零测试覆盖** —— `tests/` 下无一条用例碰 `trash_service`；② 当时唯一的形态锁 `tests/test_storage_scope_lock.py` 的判据是 `isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)`，**只认调用形态**，名字存在 dict 里、在别处按名取数，它一个字也看不见。
- **修复（两轮共 7 个 commit）**：
  - 第一轮 `4ecd58e`…`9ce0cf9`：补 `tests/test_trash_service.py`（服务层公开口，四实体 × soft/restore/hard，**先红 44 条**）；scope lock 扩到**三种形态**；新增原语 `core/authz.py::fetch_for_actor` 收敛「属主优先、admin 放行」；补 `get_group_session_unscoped`（base + sqlite + pg）；`ENTITY_MAP` 换显式五槽 + 走原语；三处手写 admin 分支迁到原语（`9ce0cf9`，超出修复范围、单独 commit）。
  - 第二轮 `cae6c37`：**槽位从方法名字符串改为属性引用**（`lambda s: s.get_x_owned`），按名反射解析删除 —— 名字不再是数据，同一处改名不会再静默悬空。**但「改名会被发现」靠的不是 `AttributeError` 的即时性，是 `tests/test_trash_service.py`**：lambda 体要到**被调用时**才解析属性，`storage` 又是 duck-typed（形参标 `Any`，静态检查看不见），导入 / 定义 / 取槽都不报错；真正响的是那套用例把 **ENTITY_MAP 的 20 个槽全求值过一遍** —— `owned` / `unscoped` 是 `core.authz.fetch_for_actor` 的两个实参，**每次取数都求值**（连 non-admin 路径也求值，`fetch_for_actor` 不调用不等于不构造）；`soft` / `restore` / `hard` 在各自成功路径上被调用。**变异抽查四槽**（逐字节还原、sha 一致）：`card.owned` → 该实体 11 条全红；`group.unscoped` → 该实体 11 条全红；`session.restore` → **1** 条红；`card.hard` → **2** 条红。后两者只红该槽真正被调用的那几条 —— 覆盖是逐槽的，不是「有测试就行」。字符串形态的扫描**保留作兜底**（挡这类写法再出现）。
- **残余风险**：`get_group_session_unscoped` 的 **PG 实现从未在真 PG 上执行过**。运行时唯一调用点是 `tests/test_trash_service.py` 里那条自证原语的用例，用的是 `SQLiteStore`；`tests/test_postgres_store.py` 只调 `get_group_session_owned`。本机 PG 用例整体**正式 skip**（无 `DATABASE_URL`）；CI 的 `build.yml` 设了 `REQUIRE_PG_TESTS=1` 会真跑 PG 用例，但**没有任何一条**调到这个新方法。它目前只有**静态**覆盖（方法集镜像锁 + SQL 事实锁扫它的 SQL 文本）。SQL 与 sqlite 侧同形（去掉 `AND user_id`）故风险低，但**如实记为未实跑**。
- **同族**：缺陷 19（判据读命名后缀）、缺陷 25（签名参数替代不了 WHERE 谓词）、本条（判据读字符串表里的名字）—— 三条问的是同一件事：**判据读的是「名字 / 签名 / 字符串」，还是真事实**。§四「判据不得建立在『看起来像』之上」的第三处显形。

**77. `soft_delete` 缺「未删除」前置条件 —— 已进回收站的 text 再软删返回 404，卡却已被摘掉** —— 状态：**已修**（`b7dfc95` `2562c37`，2026-09-20）
- **现状读数（修复前，pre-fix worktree `e6d4c63` 上实跑四端点，`e2e/scratch/probe_defect77_status.py`）**：对**已在回收站**的实体再发一次软删 —— text **404** / card **200** / session **404** / group **200**。这个码**不是「因为已在回收站而拒」**：`soft_delete` 里没有任何一处看过 `deleted_at`。404 来自 `delete_text` / `delete_session` 的 `WHERE deleted_at IS NULL` 打不中 → `rowcount = 0` → 路由把 `False` 翻成 404（`web/routers/text.py` 的 `if not ok: raise HTTPException(404)`）；200 是 card / group 存储方法的空转。
- **病灶比 404 更深**：`SQLiteStore.delete_text` 是**先断开、后 UPDATE、无条件 commit、最后才 `return rowcount > 0`**。故第二次调用虽然返回 404，**断开那一步已经执行并提交**。探针的第二段就是为这个读数写的 —— 先软删一次把 text 送进回收站，**事后新挂一张卡上去**，再发一次带 `keep_cards=true` 的软删：返回 **404**，而卡片的 `text_id` 从 `txt_624f85…` 变成 `''`。**「已删记录上的操作被拒了」与「副作用还是落了」同时成立。**
- **与缺陷 75 的分工**：75 管副作用跑在**鉴权之前**，本条管副作用跑在**前置条件之前**。同一族（`before_mutation` 的时序），两个不同的门。
- **后果**：用户手滑点第二次删除 → 上屏 404（看起来什么都没发生）→ 但他在第一次删除之后新挂上去的卡又被摘了一次，**无提示、不可逆**；之后 `POST restore` 也拿不回那批卡。
- **修法（一个谓词、两处相反的用法）**：`core/trash_service.py` 抽出 `_in_trash(record)`（= `bool(record.get("deleted_at"))`）。`soft_delete` 要求**不在**回收站，`hard_delete` 要求**在**（后者原是函数体内手写的 `if not record.get("deleted_at") → 400`，换用同一谓词，**行为不变**）。两处判定都在 `before_mutation` **之前** —— 副作用不可逆，穿过了前置条件就会作用在「本来不该发生这次操作」的对象上。
- **状态码为什么是 404**：按约束「沿用现状、不自创」。四类现状是 404/200/404/200，**统一到 404** —— 对「软删」这个动作，已删记录就是「没有了」，且与 text / session 由 rowcount 得到的 404 一致（card / group 的空转 200 是四类里唯一的异类，收敛掉）。与全仓「非属主与不存在同判 404，防 ID 枚举」同向。
- **测试**（`tests/test_trash_service.py`，本轮 +13 条）：四类实体 × `TestAlreadyInTrash` —— 对已在回收站的记录再软删一律 404、**`before_mutation` 零调用**、**零存储写**；另有一条正控（首次软删必须调钩子并写库 —— 没有它，一个「永远拒绝」的实现也能让三条全绿）、一条「恢复后重新可删」（前置条件是「在不在回收站」，不是「删过几次」）、以及 `test_repeat_soft_delete_with_keep_cards_leaves_card_attached`（只对 text 有可观测的持久副作用，故不参数化）。
- **变异验证（两次，均实测）**：① 把 `soft_delete` 的谓词判定挪到 `before_mutation` **之后** → 恰打红 4 条（四类实体的 `test_repeat_soft_delete_is_rejected`，报错行正是 `assert calls == []`），54 passed；② 把前置条件**整个删掉**（= 修复前形态）→ 5 failed（上面 4 条 + keep_cards 那条，报错 `assert None == 404`）。**第二次还单独核过 keep_cards 那条的断言有分辨力**：同一形态下直跑，卡片 `text_id` 从 `txt_1465ec86…` 变成 `None` —— 「被拒了还是把卡摘了」当场复现。两次均逐字节还原（`git diff` 空）。
- **判据 grep（修后）**：`core/trash_service.py` 里 `deleted_at` 的出现只在 `_in_trash` 内（另两处在 docstring 与注释里描述该谓词）。
- **顺带发现（只记不改）**：`hard_delete` 对**不在**回收站的记录返回 **400**（`请先移入回收站再永久删除`），而 `soft_delete` 对**已在**回收站的记录返回 **404**。不对称是刻意的（前者是「顺序错了，先移进去」的可操作提示，后者是「它已经不在了」），但两个方向的门用了不同的码 —— 若将来要统一，先想清楚「谁该看到什么」，别只看对称性。

**78. SQLite 的 `cards.text_id` 是 `NOT NULL`、PG 是可空 —— 「断开」只能写 `''`、写 `''` 只能关外键、关了就回不来，永久删除因此留孤儿行** —— 状态：**已修**（`0c83e3a` `ce218ec` `5baf582` `91263fe` `aaf54b9`，2026-09-20）
- **事实**：`storage/migrations/001_init.sql` 声明 `cards.text_id TEXT NOT NULL`，`storage/migrations_pg/001_init.sql` 声明 `cards.text_id TEXT`（可空）。两侧的 `cards.text_id` 都有 `REFERENCES texts(id) ON DELETE CASCADE`。
- **根因是一条链，不是一处笔误**：① SQLite 写不进 NULL → 「卡未关联文本」**只能**用 `''` 表示；② `''` 不指向任何 `texts.id` → 外键直接拒 → ③ 为了写进去，`_detach_text_cards_tx` 只能发 `PRAGMA foreign_keys = OFF` 绕过；④ 该指令**在事务内是 no-op**（缺陷 75 已实测），关不回来 → ⑤ 同一连接随后的 `DELETE FROM texts` **既不级联也不报错** → 孤儿行。链条的每一环都「看起来能用」，直到最后一环才露馅。
- **实测（`e2e/scratch/probe_pragma_fk.py`）**：`text_id` 置 `''` 之后同一连接 `DELETE FROM texts`，`text_comments` 留下孤儿行。
- **三处同源，一次修完**：① 断开写 `''`（`_detach_text_cards_tx`，两个 store 各一份）；② `fork_card` 把前端送来的 `text_id: ''` 原样插库 → **既有 500** `FOREIGN KEY constraint failed`（`AuthorPage` 送的就是 `''`，语义 = 要一张独立卡）；③ `tests/test_schema_parity.py` 的锁漏掉了这条可空性，所以两侧分叉了 9 个月没人知道。
- **迁移（`ce218ec`）**：新库由 `001_init.sql` 直接按新规格声明；旧库走**一次性重建块**（判据 = `PRAGMA table_info(cards)` 里 `text_id` 还挂着 `NOT NULL`，跑过一次即永久跳过），保留全部 17 列 / 3 个索引 / 外键子句，存量 `''` 归一成 NULL，并按**现算**的 FK 子表处置孤儿行（`_text_fk_children` 从 `PRAGMA foreign_key_list` 现取，**不维护名单** —— 名单会随迁移腐烂）；未登记处置的 texts 子表直接**上抛**，逼新增表时回来选一个处置，而不是静默留孤儿。
- **为什么必须真的关 FK（实测，不是推断）**：`DROP TABLE cards` 在 FK 打开时会做一次隐式 `DELETE FROM`，`ON DELETE CASCADE` 随之触发 —— `e2e/scratch/probe_cards_rebuild.py` 实测 **8 张引用 cards 的子表被清空 6 张**（`sessions` / `card_likes` / `card_comments` / `card_versions` / `card_comment_reports` / `card_reports` 各 1 → 0；`wechat_users` 与 `featured_cards` 不动），且进入时 `foreign_keys` 读数确认为 1。`PRAGMA defer_foreign_keys = ON` **挡不住** —— 它推迟的是**约束违例检查**，而 CASCADE 是 **FK 动作**，不是违例。故只能走官方流程：**事务外**关 FK → 重建 → `finally` 复位（`probe_drop_cascade.py` 的 A 段复现该流程：8 张子表行数全不变，重建后 FK 复位为 1）。
- **本地库副本实跑读数（`e2e/scratch/probe_migrate_local_copy.py`，副本用 `sqlite3.backup()` 生成以免丢 WAL）**：`cards` 行数 **23 → 23**；`text_id` 的 `NOT NULL` 位 **1 → 0**；`''` **1 → 0**；`IS NULL` **0 → 3**；孤儿 `cards.text_id` **3 → 0**、`text_comments.text_id` **0 → 0**；8 张引用 `cards` 的子表行数**全部不变**（`sessions` 18 / `card_comments` 10 / `card_versions` 5 / `card_reports` 2）；3 个索引原样回来；**二次初始化各读数不变**（幂等）。当日复测本地库（迁移已完成态）：`cards` 23 / `NOT NULL` 0 / `''` 0 / `IS NULL` 3 / 两个孤儿位 0 —— 与迁移后读数一致。
- **`''` 判断清单（逐一核对后再改，`5baf582`）**：`_detach_text_cards_tx` → NULL；`fork_card` 两侧 → 归一成 NULL 且去重查询改 **NULL 安全比较**（SQLite `IS ?` / PG `IS NOT DISTINCT FROM` —— `= NULL` 恒不成立，会让独立卡每次 fork 都新插一张）；`list_standalone_cards` 两侧的 `(text_id IS NULL OR text_id = '')` **已同时覆盖 NULL，保留**；`market.get_book_versions` 的 `or ""` 是只读真值门、不落库，保留；`chat.py` / `group.py` 的 `get_text_owned(text_id)` 两值都查不到、同走 404/continue（**PG 生产一直喂的就是 NULL，这些分支早就在跑**）；`scripts/rebuild_384_collections.py` 的 `text_id <> ''` 读数不变；前端 `TextPanel.jsx` 对 NULL 的判定与对 `''` 一致，不改前端。
- **parity 锁为什么漏了它（读代码得出，非推测；`91263fe` 结项）**：模块 docstring 写着「every column exists in both schemas with **matching nullability**」，但实现从未比较可空性 —— `_extract_column_names` 返回 `set[str]`（只有列名），`_extract_schema` 存的是 `{表: {列名}}`，比较是**纯集合差集**。可空性在抽取的第一步就被丢掉，**数据里根本没有这个维度可比**，`NOT NULL` 与可空在锁眼里完全同形。docstring 的承诺是「应有的行为」，不是「实际的行为」。修法：`_COL_TYPE_RE` 多捕一组余下修饰 → `_extract_columns` 返回 `{列名: 是否显式 NOT NULL}` → 新增可空性比较分支；**只认显式声明，不推算 PRIMARY KEY 的隐式非空**（PG 隐式、SQLite 的 legacy quirk 允许 NULL，按它推算只会造假差异）。变异：把 `001_init.sql` 的 `text_id TEXT` 改回 `NOT NULL` → 锁红并点名 `[cards.text_id]`；还原后绿。
- **顺带揪出一条此前不可见的真实分叉（登记不修）**：`dm_reactions.created_at`（SQLite 069 写 `TEXT NOT NULL DEFAULT (datetime('now'))`，PG 004 写 `TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP`）。两侧都有默认值、写入路径不显式给 NULL，暂无实际分叉；改任一侧历史迁移都会让「全新库」与「已建库」变成两个 schema（缺陷 26 的教训）。登记方式不是静默放行：`_NULLABILITY_EXEMPT` 每条带理由，且**反向核对每条豁免是否仍然成立，一旦不再分叉即报红**（缺陷 21 / 079「豁免即永久放行」的教训）。变异：去掉 069 的 `NOT NULL` 使两侧一致 → 锁红要求删豁免；还原后绿。
- **顺带发现（只记不改）**：`users` 的既有重建先例用的是 `defer_foreign_keys` 而非 FK OFF，而 `refresh_tokens.user_id` / `user_secrets.user_id` 是 `ON DELETE CASCADE`（`probe_drop_cascade.py` 的 B 段现算）—— **那块重建一旦真跑会清空这两张表**。触发条件是 SQLite < 3.35（本机 3.49.1，未触发）。与缺陷 78 同族（「用错了关外键的手段」），未纳入本轮。
- **残余风险**：`_repair_text_orphans` 对**未登记处置的 texts 子表上抛** —— 这是刻意的（逼人回来选处置而不是静默留孤儿），代价如实写明：**将来新增一张 FK 引用 `texts` 的表，旧库启动会直接失败**，直到在 `_TEXT_FK_ORPHAN_TREATMENT` 里登记处置。
- **全量**：1382 passed / 0 failed / 69 skipped / 1 xfailed = **1452 collected**（基线 1429 collected，在独立 worktree `e6d4c63` 上现跑）—— 逐 id 比对 **+23 / 消失 0**：`test_trash_service.py` +13（缺陷 77）、`test_storage.py` +7（78 的断开/孤儿 5 + fork 2）、`test_postgres_store.py` +3（skip +2 是新增的 PG fork 用例在本机无 `DATABASE_URL` 时正式 skip，passed +2 是 SQLite 侧的两条 fork 用例 —— 故 1380→1382 与 67→69 两处都能对上）。PG 侧在一次性容器 `cd-pg-defect78`（`REQUIRE_PG_TESTS=1`）实跑 **58 passed / 0 skipped**。

**79. 两处表重建各写一遍「关外键」的时序 —— users 那块只用了 `defer_foreign_keys`（等于没关），真跑会按 CASCADE 清空 `refresh_tokens` 与 `user_secrets`** —— 状态：**已修**（`8adecf0`，2026-09-21）
- **病灶**：`DROP TABLE` 在 FK 打开时会先做一次隐式 `DELETE FROM`，触发 `ON DELETE CASCADE`。78 已为 `cards` 重建实测过这一条，但 **users 的回落重建**（SQLite < 3.35 才进得去）仍用 `PRAGMA defer_foreign_keys = ON` —— 它推迟的是**约束违例检查**，而 CASCADE 是 **FK 动作**，挡不住。
- **静默丢行臂（实测）**：CASCADE 子表有行、非 CASCADE 子表为空时 —— 造 users + 3 行 `refresh_tokens` + 1 行 `user_secrets`，跑回落重建 → **`refresh_tokens` 3 → 0、`user_secrets` 1 → 0**，且 **init 报成功**。`user_secrets` 存的是加密 API 凭据，**属数据丢失，不是普通记账项**。现场命令：`tests/test_sqlite_rebuild_cascade.py::test_users_rebuild_keeps_cascade_children`（修复前跑即红，报错行 `{'refresh_tokens': 0} != {'refresh_tokens': 3}`）。
- **启动崩臂（实测）**：真实库形状 —— 活库 `usage_stats.user_id` 是 `NO ACTION` 且有 **502** 行，autocommit 下 `DROP TABLE users` 立即触发违例 → `IntegrityError: FOREIGN KEY constraint failed`，**应用直接起不来**。命令：活库副本 + 把 `sqlite3.sqlite_version_info` 按到 `(3, 34, 0)` 跑 `SQLiteStore._ensure_initialized()`。
- **落点：两处重建共用 `_fk_disabled`**（`storage/sqlite_store.py`）—— `await conn.commit()`（开关必须发在**事务外**，事务内是 no-op，缺陷 75 实测）→ `PRAGMA foreign_keys = OFF` → `finally` 复位；异常路径先 `rollback()` 再上抛，否则 finally 那句复位同样是 no-op、外键会一直关到连接结束。病因 docstring **只写在这一处**，`_rebuild_cards_nullable_text_id` 与 users 回落分支各留一行指过去。**不是打补丁**：没有在 users 那块再补一行 `PRAGMA`；users 块原来的 `PRAGMA defer_foreign_keys = ON;` 已删。
- **红源**（`tests/test_sqlite_rebuild_cascade.py`，2 条，各锁一个消费者）：`test_users_rebuild_keeps_cascade_children` 锁 users 回落分支 —— 真建库、插 user + 1 行 `user_secrets` + 3 行 `refresh_tokens`，再用 `monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))` **把回落分支逼出来**（本机 SQLite 3.49 走原生 `DROP COLUMN`，不按版本号进不去），断言两表行数不变；`test_cards_rebuild_keeps_cascade_children` 锁 cards 重建（直接调 `_rebuild_cards_nullable_text_id`，本机造不出 `text_id NOT NULL` 的老库），断言 `sessions` 行数不变 —— 这条**修复前后都绿**，它是给共用件第二个消费者留的判别面，保证共用件不退化成「只服务一处」。**（订正 2026-09-23，随缺陷 82）**：`test_users_rebuild_keeps_cascade_children` 已删除 —— users 的回落重建整体不存在了，`_fk_disabled` 现存两个消费者都是 cards 那两条重建；本段其余内容是 `8adecf0` 当时的事实，保留存档。
- **变异（驱动 `e2e/scratch/mutate_fk_disabled.py`，本轮现跑）**：① 去掉 `PRAGMA foreign_keys = OFF` → **2 failed**；② 把开关挪进事务内（先 `BEGIN` 再发）→ **2 failed**；两种坏法都把**两条**用例打红 —— 共用件在两个调用点上都吃劲。逐字节还原后 **2 passed**。
- **真实库副本读数**（只读副本，活库未动）：`refresh_tokens` **926 → 926**、`user_secrets` **4 → 4**、`users` 4、`cards` 23、`sessions` 18 全部不变；重建后 `users` 残留 legacy 列 **0**、`users_mig` 残留 **0**。（副本上需先清掉下述 82 条的残骸，否则卡在 `table users_mig already exists`。）
- **全量**：SQLite 形态 **1384 passed / 69 skipped / 1 xfailed = 1454 collected**；PG 形态（一次性 `postgres:16-alpine` 起在 127.0.0.1:5455，跑完即删）**1451 passed / 2 skipped / 1 xfailed = 1454 collected**。基线 `e41b6a6` 现跑 `--collect-only` = **1452**（worktree 现测，用后即删），差量 **+2 / 消失 0**（新文件恰 2 条）。

**80. 活库 25 行 `refresh_tokens` 孤儿 —— 用户已删、token 还在** —— 状态：**已裁定·不修（SQLite 专属）**（2026-09-24 裁定；PG 不可达：外键级联 + 同事务显式删除）
- **读数（2026-09-21，本地 SQLite）**：`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe e2e/scratch/probe_residue_readings.py` 的 §2 遍历全库每张表的每个 FK 子句，唯一的孤儿类是 `refresh_tokens.user_id → users.id：25 行孤儿`；同库 `users` 4 行、`refresh_tokens` 926 行。§1 另证四个验收 id 在这张表里没有残留。
- **PG 不可达（基线 `cc9f231` 核实，两条独立保证）**：① `migrations_pg/001_init.sql` 给 `refresh_tokens.user_id` 建了外键 `REFERENCES users(id) ON DELETE CASCADE`，PG 总是强制外键 —— 删用户时这些行由库自己级联清掉；② 即便没有级联，`postgres_store.py:2718` 的 `delete_user` 在**同一个事务**里显式执行了 `DELETE FROM refresh_tokens WHERE user_id = $1`。**线上用 PG，故这是 SQLite 专属现象**，按主次规则（PG 为准、SQLite 只保接口一致）不修、不补测试。
- **生产只读核查**（预期 0）：`SELECT count(*) FROM refresh_tokens r LEFT JOIN users u ON u.id = r.user_id WHERE u.id IS NULL;`

**81. `cross_border_delete_outbox` 有指向已删 fork 的行，且该表没有任何应用清理入口** —— 状态：**已修**（Spec 80/81 修问题 1–3，2026-09-23；Spec 81b 补修问题 4「恢复卡片不回撤跨境删除」；两轮都与代码 / 测试同一个 commit）
- **读数（2026-09-21）**：`e2e/scratch/probe_residue_readings.py` §1 → `0c1779b40d15 → [('cross_border_delete_outbox', 1)]`；直读该行 = `(1, 'card_delete', '0c1779b40d15', '', 0, '2026-09-20 03:43:25')` → **`synced = 0`，一条仍待同步的删除传播**。
- **本地那一行卡住的原因 —— 环境，不是缺陷**：删除转发只在配置了 `PEER_NODE_URL` 时才执行（`web/cross_border_sync.py` 单轮入口开头即返回），本地没配 → 永远不转发。对端接收端点是幂等的（`web/routers/inter_node.py`，目标不存在也返回 200），只要配了对端，这一行就能正常传播出去。
- **根因（读代码在 PG 上发现的 4 个真问题：1–3 由 Spec 80/81 修，4 由 Spec 81b 补修）**：
  1. **删除补发被嵌在卡片补发的 `else` 分支里** —— 卡片查询一抛异常，本轮删除补发整段被跳过，两件互不相关的事被绑死。
  2. **已传播的行永不回收**：对端确认后只 `UPDATE ... SET synced = 1`，而全仓唯一读这张表的地方只读 `synced = 0` 的行 —— `synced = 1` 没有任何读者，表无界增长。
  3. **失败静默**：`forward_delete_to_peer` 非 200 或异常时直接返回 False，不留状态码 / 异常，线上无从排查。
  4. **恢复卡片不回撤跨境删除**：`restore_card` 只 `SET deleted_at = NULL` —— 既不撤销还在排队的 `card_delete`（补发 60 秒一轮，下一轮照发，对端把用户刚恢复的卡硬删掉），也不把 `cross_border_synced` 归零（补发查询只挑 `= 0`，这张卡永远选不中，对端副本删了就再也建不回来）。
- **处置**：① 单轮逻辑抽成 `_resync_once(storage)`，`_cross_border_resync_loop` 只管「sleep 60 秒再调它」；DM / 卡片 / 删除三段各自独立捕获异常、互不影响，删除段从卡片的 `else` 里移出。② 对端确认后**直接删行**，不再标 `synced = 1`：`mark_delete_propagated` 改为删除语义并改名 `remove_delete_propagation(id)`，`storage/base.py` 抽象签名 + SQLite 同步改（SQLite 只求接口一致、能跑，不单测）。**不写迁移、不停用 `synced` 列** —— 存量里已是 `synced = 1` 的行从此不会再被读到，要不要清由 Shiyu 手动决定。③ 失败路径打印 `op_type` / `target_id` + 状态码或异常，沿用该文件既有 `print` 风格，不引日志框架、不加计数。④ **恢复卡片时同事务回撤**（Spec 81b）：`restore_card` 在同一事务里 `SET deleted_at = NULL` + `DELETE FROM cross_border_delete_outbox WHERE op_type = 'card_delete' AND target_id = $1` + `SET cross_border_synced = 0`。不加「是否公开」判断（私有卡本就没有排队的删除；归零也无害，补发查询只挑公开卡，少一个分支行为不变）；不新增接口、不新增操作类型（对端收卡走 `upsert_remote_card`，重发即重建）。**只改 PG**：SQLite 是单节点、没有对端，不存在这个问题。
- **守它的测试**（`tests/test_cross_border_sync.py`）：问题 3 → `test_forward_delete_to_peer_logs_status_on_non_200` / `test_forward_delete_to_peer_logs_exception`；问题 1 → `test_delete_resync_survives_card_query_failure`；问题 2 → `test_delete_resync_removes_row_after_ack` / `test_delete_resync_keeps_row_without_ack`；问题 4 → `test_restore_card_drops_queued_delete` / `test_restore_card_requeues_card_for_forwarding` / `test_delete_requeues_after_ack_and_restore`（走真 PG，一次性 `postgres:16-alpine`）。
- **生产只读核查**：`SELECT synced, count(*) FROM cross_border_delete_outbox GROUP BY synced;`（看积压与历史行各多少）；若决定清理历史行，再 `DELETE FROM cross_border_delete_outbox WHERE synced = 1;`。

**82. 活库残留一张 `users_mig`（4 行，DDL 无 `nickname` / `username_lower`）—— 数据丢失风险** —— 状态：**已修**（2026-09-23，Spec 82；与代码 / 测试同属一个 commit）
- **根因**：`users_mig` **只可能**由 `sqlite_store.py` 里 SQLite < 3.35 的「原表重建」回落分支产生 —— 那段建表语句**没带 `IF NOT EXISTS`，不幂等**：中途失败一次即在库里留下残骸，此后每次启动都在这句上报 `table users_mig already exists`，init 失败、**应用直接起不来**（成功路径会把该表改名回 `users`，不留残骸）。3.35+ 走原生 `DROP COLUMN`，根本不产生这张表。
- **处置：整条回落分支已删，不是给它补幂等。** 最低版本定为 SQLite 3.35，版本检查**只放一处** —— `_ensure_initialized` 开头，`< (3, 35)` 时抛 `RuntimeError`（消息点名所需版本与当前版本），**直接失败退出、不做任何降级**（全部表结构操作都从这个方法进，构造函数保持轻量）。同时删掉 `if sqlite3.sqlite_version_info >= (3, 25):` 那条已恒真的护栏（两条去重 DELETE 原样保留、缩进上提；去重本身改成一次性迁移原挂在蒸馏线 90 / 99 名下，两条已于 2026-09-24 结案）。
- **红源改写**：`tests/test_sqlite_rebuild_cascade.py` 里按 `(3, 34, 0)` 逼回落分支的 `test_users_rebuild_keeps_cascade_children` 删除，换成 `test_sqlite_below_minimum_version_fails_loudly`（断言 `RuntimeError` 且消息含 `3.35`）；同文件的 cards 重建用例保留。
- **不写清理迁移**：分支删掉后这张表再也不会产生，现存残留只是**一张孤立表，不会再让启动失败**；那 4 行是不是别处没有的用户数据只能由人判断，故清理交 Shiyu 手动执行（SQL 见本轮交付报告，**不入库**）。
- **残留读数（2026-09-21，只读副本）**：`users_mig` 存在、**4 行**，id 与 `users` 完全一致（`3996bd7f23a34311` / `f4b7650ba30e446c` / `f46432a6a92e4ae7` / `ebaaac2677c84e3d`）；其 DDL 的列清单没有 `nickname` / `username_lower` → 由 migration **077 之前**的代码版本创建。**它当时反而挡着丢数据**（建表语句排在 `DROP TABLE users` 之前，表已存在即报错、`DROP` 不执行，78 / 79 说的 CASCADE 清空因此没发生）—— 巧合，不是设计。
- **原「处置方向」作废**：`DROP TABLE IF EXISTS users_mig` 那种「给回落分支补幂等」的写法，已被「整条分支删除」取代 —— 分支不存在了，无从补起。

**83. 聊天族的身份与依赖全靠「往引擎实例上写属性」传 —— 同形 11 行散在 4 个文件，且写的字段集互不一致** —— 状态：**已修**（`15c6a6a` + `8efb67c` + `b259e82`，2026-09-22）
- **病灶（与缺陷 35 修复前同形）**：`ChatEngine.__init__`（`core/chat_engine.py:123-124`）持有 `self._storage = None` / `self._user_id: str = ""`，路由在构造**之后**才逐个把值写上去。缺陷 35 已把**蒸馏族**从这条路上摘掉（身份走 `LLM_CALLER`），**聊天族没动**。
- **读数（现跑）**：`git grep -n "\._user_id = \|\._storage = " -- web/ core/` → 引擎侧写点 **11 行、4 个文件**：`web/routers/chat.py` 7 行（212 / 309 / 310 / 434 / 435 / 466 / 467）、`web/routers/group.py` 1 行（219）、`web/routers/history.py` 1 行（313）、`core/group_session.py` 2 行（163 / 353）。
- **字段集不一致**：`chat.py:212` **只写 `_user_id`**、不写 `_storage`；`group.py:219` / `history.py:313` / `group_session.py:163,353` **只写 `_storage`**、不写 `_user_id` —— 后四处的路径上 `_user_id` 保持初值 `""`，与缺陷 35 修复前 `Distiller` 的默认值逐字相同。「得记得写」在同一个仓里已经兑现成了「写的字段都不一样」。
- **代价已有化石证据**：`core/context_engine.py:215-220` 的 `_record_usage` docstring 明写「ChatEngine 的这两个字段在构造之后才被路由绑定，构造时取值会拿到 None」—— 为绕开这个时序，`ContextEngine` 只好接受一个**延迟取值回调** `usage_ctx`（`core/chat_engine.py:171` 传 `lambda: (self._storage, self._user_id)`）。属性注入这条路已经贵到要在下游多养一个间接层。
- **性质**：与 35 同一个病，只是在聊天族上还没发作（症状形态不同，故不在 35 的范围内）。
- **判据命令**：`git grep -n "\._user_id = \|\._storage = " -- web/ core/`（引擎侧写点数现为 11）、`git grep -n "usage_ctx" core/`（回调还在 ⟹ 时序问题还在）
- **处置方向**：照 35 的形态收敛（身份走 `core/request_context`、`storage` 走构造注入）。**本轮不做**：改它会碰 `ChatEngine` / `ContextEngine` / 4 个路由及其测试，超出本案 2 个门的硬范围。
- **收口（2026-09-22，照上面那个方向做的）**：`15c6a6a` 把 `storage` 改成构造注入（路由不再构造后写 `_storage`），`8efb67c` 把记账身份收敛到出口自读 `current_user_id()`（`_user_id` 实例字段随之消失、`ContextEngine` 那个延迟取值回调 `usage_ctx` 也一并删掉），`b259e82` 上锁。判据现跑：`git grep -n "\._user_id = \|\._storage = " -- web/ core/` → 引擎侧**外部写点 0**（余 7 处 `self._storage = storage` 全部落在各自 `__init__` 内，是构造注入本身）；`git grep -n "usage_ctx" core/` → **零命中**（回调已删 ⟹ 那个时序问题不再存在）。**注意本条只收掉了「依赖」**：靠实例字段传的**会话状态**（`_session_id` / `_group_id` / `_user_tz`）没动，另记 96。

**84. `AgentLoop` 的记账身份取自 `ChatEngine` 的实例字段 —— 83 的下游，不是独立病灶** —— 状态：**已修**（随 `8efb67c`，2026-09-22）
- **形态**：`core/chat_engine.py:302` 造 `AgentLoop(self.llm, toolkit, storage=self._storage, user_id=self._user_id)`；`core/agent/agent_loop.py:64-65` 在构造里存下这两个值，`:116` 用它们调记账出口（`try_record_usage(..., "chat_agent_route", source="AgentLoop")`）。
- **为什么单列一条**：它的**注入形态本身是对的**（构造注入，与 35 里 `storage` 的走法一致），坏的是**喂进来的值**来自 83 的那两个实例字段 —— 路由不写 `engine._user_id`，`AgentLoop` 拿到的就是 `""`。单列是为了标明**依赖方向**（84 只能随 83 一起修），不是又多一处病灶。
- **判据命令**：`git grep -n "AgentLoop(" -- core/ web/`（构造点现为 1 处，全在 `chat_engine.py:302`）
- **收口（2026-09-22，随 83 的 `8efb67c` 一并，不是单独一次改动）**：身份不再由 `ChatEngine` 实例字段喂进来，故本条要标的那个依赖方向已经没有下游了。判据现跑：`AgentLoop(` 构造点仍 **1 处**（现 `core/chat_engine.py:308`，行号随 83 的重构位移），**实参只剩 `storage=self._storage`、`user_id=` 已去掉** —— 这正是「喂进来的值」那一半被摘掉的读数。

**85. `web/app.py:48` 的 `Distiller(_llm)` 是缺陷 35 的同形残留 —— 生产不可达** —— 状态：**已修**（`238610d` + `680eafe`，2026-09-22）
- **形态**：`web/app.py:48` 只传 llm、不传 storage 就造 `Distiller` —— 与 35 修复前的默认值同形。它没被 35 的修复覆盖，是因为它是 **Gradio** 入口，不在 `web/routers/` 那一族的装配路径上。
- **生产不可达（实测）**：`Dockerfile:52` 是 `CMD ["python", "-m", "web.server"]`；`git grep -rn "web\.app\b"` **零命中** —— 全仓无任何模块 import 它。它只在有人手工拉起 Gradio demo 时才会被加载。
- **判据命令**：`git grep -n "Distiller(" -- web/app.py`、`git grep -rn "web\.app\b" -- .`（应零命中）
- **处置方向**：先判这个 Gradio 入口是否还有人用 —— 已废弃就随它退役，还在用就照 83 收敛。**不先改**。
- **收口（2026-09-22，按「已废弃就随它退役」那一支处置）**：`238610d` 删掉整个 `web/app.py`（那个 `Distiller(_llm)` 随之消失），`680eafe` 把随它进场的 `gradio` 从 dev 锁（`requirements-dev.in` / `.txt`）里摘掉 —— 删代码不删依赖会留一个没人 import 的重量级包。判据现跑：两条都**零命中**（`git grep -n "Distiller(" -- web/app.py` 与 `git grep -rn "web\.app\b" -- .`；文件本身已不存在，故第一条是对着一个不再存在的路径取的零）。

**86. `_run_distill_task` 取名单失败时 `except Exception: chars = []` —— 错因被吞，故障以「输入有问题」的口径上报** —— 状态：**已修**（见本线 commit `823325f`，2026-09-22）
- **形态**：`web/routers/distill.py:371-375` 的 bg 线程里，`resolve_characters` 的任何异常都被宽捕获吞成空名单 —— 不打印、不分类。缺陷 36 收口把这里从 `distiller.identify_characters(content)` 换成 `submit_to_main_loop(resolve_characters(...))`，**原样保留了这个形状**（「发现 spec 外问题先报告，不顺手修」）。
- **可达，且后果不是静默成功而是错因混淆**：`/start` 的 `DistillTaskRequest.character_name` 默认 `""`（`:245`），bg 线程对空名字走 `if not name:` 分支（`:376-383`）—— 名单空则任务以 **error** 收口、message 为 `"No characters identified"`。于是**上游故障（网络超时、DB 不可用、额度耗尽）与「这本书里确实没有角色」在用户与排查者眼里是同一句话**，日志里一个字都没有。这是缺陷 38 的同族（客户端条件与上游故障共用一条上屏文案），只是这里连运维口径都没留下。
- **点名蒸馏也受影响**（2026-09-22 订正；原写「点名蒸馏不受影响」）：`name` 非空时名单只用来取别名，**名单成功取回但为空**时确实不受影响（`aliases_for` 返回 `[]` → 蒸馏照常产卡）。但**取回本身失败**时原先与空名单走同一条宽捕获，点名的 `/start` 与 `/run_stream` 也照样把故障降级成「空别名继续蒸馏」。**只有「这本书真的没有具名角色」才是不受影响的**；故障不再降级 —— 这正是本次的行为变化。
- **判据命令**：`git grep -n "chars = \[\]" -- web/routers/distill.py`（**修复前实测 2 处** —— bg 线程与 `_event_gen` 各一；台账原写「1 处」漏计了 SSE 那条。修复后 **0 处**）
- **处置方向**：把宽捕获改成 `except Exception as exc: print(...)` + 以 **500 口径**（「操作失败，请稍后重试」）收口，把「这本没有角色」留给真正的空结果分支。**先不改**：要连带定「识别失败该报 5xx 还是可重试」，属缺陷 38 那张表的范围。
- **收口（2026-09-22，见本线 commit `823325f`；没照上面的「500 口径」走，理由见下）**：三条一起做才成 —— ① 识别层只留两种结果：`_identify_single_call` 两次解析均失败由 `print` + 返回 `None` 改为**抛 `DistillError`**，`_identify_over_chunks` 的 `if not parts` 由**抛**「未能从任何片段中识别到角色」改为**返回 `[]`**（原先它正是把真空名单当故障的那处）；② 「挑不出目标角色」的判据下沉到 `core/character_roster.py`（`NoTargetCharacter(DistillError)` + `target_character_name`），三份手抄的判据合一；③ 三通道只渲染：bg 去掉宽捕获、SSE 就地 `yield` 错误帧、`/run` 的**空名字那一支**冒泡到 `web/server.py` 的领域异常出口（点名那一支根本不走路由层的识别，见下面「未做的第二处」）。
  - **没走 500 口径**：识别失败本来就是 `DistillError`（缺陷 38 那张表已定它走 400 + `user_message`），而本条的真问题是**「故障 vs 真空名单」不可辨**，不是「码配错了」。给 500 等于另开一条上屏口径，反而把 38 已经统一的那条拆开。所以修的是**分类**：故障抛异常、真空名单返回 `[]`，码沿用已有的领域异常出口。
  - **判据现跑**：`git grep -n "chars = \[\]" -- web/routers/distill.py` → **0 处**（修复前 2 处）；`git grep -rn "_first_character_name" -- '*.py'` → 生产代码零命中（余 1 处在新测试的文档字符串里，描述被删的机制）；中文文案只定义在 `core/character_roster.py:41-42` 一处。
  - **行为变化（须让上游知道）**：点名的 `/start` 与 `/run_stream`，识别失败由「降级为空别名继续蒸馏」变为**任务失败**；HTTP 400 的 detail 由英文变为中文（三通道统一）。锁在 `tests/test_identify_failure_channels.py`（5 条，三通道 + 单源判据），登记在 `tests/test_exception_pickle_lock.py`。
  - **失败码不对称（有意保留，不是残留）**：HTTP 400 / bg 任务态 / SSE 错误帧，三条通道的载体不同（同步响应 / 任务行 / 事件流），同一个异常各自只能有各自的形状；可辨性已由「故障抛异常、真空名单返回 `[]`」这一层保证，码的一致不是这里的判据。
  - **清理空卡（2026-09-22 已删，§H 第 2 项）**：本改动把识别失败新引入了 `_run_distill_task` 外层 `except` 里的 `cleanup_empty_cards(text_id, user_id)`，而它的键是 (text_id、属主)、**不是任务** —— 会误伤同一属主在同一部作品下的其他卡片。既已确认无生产空卡来源（全仓 `save_card` 的 4 个写点都写 `card.model_dump_json()`；判据 `git grep -n "save_card(" -- '*.py'`），整段删除，不留「按任务键重写」的版本。删除面：`storage/base.py` 的声明、`storage/sqlite_store.py` / `storage/postgres_store.py` 的实现、`web/routers/distill.py` 外层 `except` 里的调用、`tests/test_e2e_flow.py::test_06_cleanup_empty_cards`、`tests/test_store_commit_contract.py` 的同名用例、`storage/sqlite_store.py` 注释里的引用。判据现跑：`git grep -n "cleanup_empty_cards" -- '*.py'` → 生产代码零命中。
  - **点名 `/run` 的别名段（2026-09-22 已修，§H 第 1 项）**：`core/text_manager.py` 的 `get_or_distill` 里，别名那段原为 `except Exception as exc: print("Identify aliases failed, using empty")` 后**带着空 `aliases` 继续蒸馏**。`/run` 在 `character_name` 非空时**跳过**路由层的 `resolve_characters`（`web/routers/distill.py` 的 `if not char_name:`），识别的唯一入口就落在这里 —— 于是点名 `/run` 的识别失败**照旧降级**，与 `/run_stream`、bg 两条（无条件先 resolve）不同。**净删宽捕获**（不新增捕获 / 映射），`DistillError` 冒泡到调用方已有的 `except DistillError: raise` → `web/server.py` 统一出口。
    - **三处调用方的行为变化（须让上游知道）**：① `POST /api/distill/run`（点名）识别失败由「降级为空别名、蒸馏照常成功」变为 **400 + `user_message`**；② legacy `POST /api/distill`（点名分支）同上；③ `TextManager.switch_character` 随之内联生效 —— 它**当前无生产调用方**（`git grep -n "switch_character" -- .` 只有定义与台账），故本条为零波及，记明以免被当成已覆盖的活路径（**同日订正**：该方法已整段删除，见下面「别名缓存的读失败 + 两个死方法」一条）。另：原先被一并吞掉的**存储层异常**（`StoreError` 等，非 `DistillError`）现在也不再降级，走路由的 `except Exception` → 500，属预期（真故障就该现形）。
    - **锁与红源**：`tests/test_identify_failure_channels.py::TestNamedRunKeepsIdentifyFailure::test_named_run_identify_failure_is_400`（真 `TextManager` + 识别即抛的桩，断言 400 + `user_message`）。**变异 = 把宽捕获加回 `core/text_manager.py` 的别名段 → 该用例红（1 failed, 8 passed）**：失败被吞后代码继续往下走（桩上没有 `distill_incremental`）→ 500，与本用例的 400 不符。
- **HTTP 侧收尾（2026-09-22）：识别族三条路由不再吞 `DistillError` —— 已修，不是残留** —— `_do_identify`（`POST /api/identify`）、`_resolve_character_name`（`POST /api/distill` 的**不点名那一支**）、`reindex_rag`（`POST /api/distill/reindex/{text_id}`）原先各自 `except Exception as exc: print(...); raise HTTPException(500, "操作失败，请稍后重试")`。这条就地捕获**把 `DistillError` 也拦下了** —— 单分片解析失败从 **400 变 500**，用户看不到「名单无法解析 / 上游限流」这类真实原因（缺陷 38 同族：客户端条件与上游故障共用一条上屏文案）。
  - **修法（净删，不新增捕获 / 映射 / helper）**：三处捕获全删，异常冒泡到 `web/server.py` 已有的三条出口 —— `DistillError` 及子类 → `_DOMAIN_ERROR_STATUS`（400 + `user_message`）、LLM 侧已知失败 → `_llm_error_handler`、其余意外异常 → `_global_exception_handler`（500 + traceback）。原先那两行 `print` 随之删掉：traceback 由全局出口打。同文件的 `/identify`（**带 text_id** 那条）本来就没有 try，是现成先例。
  - **判据现跑**：`git grep -n "操作失败，请稍后重试" -- web/routers/distill.py` → 仅剩 **6 处，全部落在名单段之外**（本文件其余 `except Exception` 归缺陷 71，按裁定一律不动）；三条名单段的路由函数体内已零命中。
  - **锁**：`tests/test_identify_failure_channels.py::TestIdentifyFamilyRoutesKeepDistillError` 三条（`POST /api/identify` / `POST /api/distill` / `POST /api/distill/reindex/{id}` 各一条，断言 400 + 真实 `user_message`）。每条各配一条变异：**把 `except Exception → HTTPException(500)` 加回该处即红**（实测逐条只打红它自己那条）。测试 app 走 `server.register_domain_error_handlers(app)`，装的是**生产同一份**注册。
  - **对测试的影响**：`tests/test_integration.py:262` / `:267` 按 URL 调了这两条 legacy 路由，但走的是**成功路径**（真 LLM、有 `sample_text`），不断言 500 也不断言文案；该文件无 `test_*` 函数，**pytest 收集为 0**，要真起服务才跑得起来（见交付报告）。其余零依赖：`git grep "操作失败，请稍后重试" -- tests/ web/frontend/src` 无命中，前端不调这三条。
- **别名缓存的读失败 + 两个死方法（2026-09-22，本条目同族遗留清尾）**：`_build_all_characters`（`core/text_manager.py:612`）原自带 `except Exception as exc: print(...)`，把**存储 / 序列化的读失败**降级成「这些角色没有别名」—— 会话照常建起来，用户只看到别名缺失，运维一个字都收不到，两类结果（真没别名 / 读挂了）不可辨。**净删宽捕获**（不新增捕获 / 映射），`cached_characters` 的异常直接冒泡。
  - **调用方一行未改（行为变化须让上游知道）**：① `TextManager.get_or_distill`（`core/text_manager.py:517`，**此时卡已落库**、会话尚未建）—— 异常由它自己已有的 `except Exception: print + raise` 收口，形态不变；② `TextManager.save_distilled_card`（`:579`）—— 就地无捕获，冒泡给调用方；③ `web/routers/distill.py::start_session`（`:1351`）—— 落在外层 `except Exception` → **500「操作失败，请稍后重试」**（原先 200 + 空别名）；④ `web/routers/chat.py::_ensure_session`（`:173`，重启后自动恢复会话那一支）—— 就地无捕获，冒泡到 `/send`（含流式）`/reset` `/revoke` 各自的收口。四处一律是「空别名继续」→「故障现形」，与 §H 第 1 项同口径。
  - **同时删除两个零调用方的方法**：`TextManager.distill_all`（`:373`，46 行）与 `TextManager.switch_character`（`:600`，9 行）。删前全仓复核（含 `web/frontend`、`scripts`、`tests`）：`distill_all` 的引用只有台账 2 处 + `core/character_roster.py:18` 的一句历史叙述，**生产调用方零**；`switch_character` 的引用只有台账 2 处（越权条目 + §H 第 1 项那条「当前无生产调用方」的记录），**生产调用方零**。前者是按整本作品批量出卡的旧入口（`web/routers/` 无调用、前端无入口），后者是 `get_or_distill` 的一行转发。上述三处叙述性引用随本次一并改成带日期的指针（否则删掉的方法名会一直留在「现役」句式里）；`scripts/rebuild_384_collections.py:140` 说的是「复刻 `_build_all_characters`」，那个方法仍在，不动。
  - **锁与红源**：`tests/test_alias_cache_failure.py::test_alias_cache_read_failure_propagates`（打桩 `core.text_manager.cached_characters` 抛错 → 断言 `_build_all_characters` 抛出）。**变异 = 把 `except Exception: print(...)` 加回 `_build_all_characters` → 该用例红（1 failed，`DID NOT RAISE RuntimeError`，stdout 里出现被吞的那句 `Alias cache merge failed: storage exploded`）**，且只红它自己那条。
  - **判据现跑**：`git grep -n "Alias cache merge failed" -- .` → **零命中**；`git grep -n "distill_all\|switch_character" -- '*.py'` → 生产代码零命中（余 1 处是 `core/character_roster.py:18` 的历史叙述，已带删除日期）。

**87. 名著模式的「深度预处理」整条支线从没接线 —— `coref_resolve` 与 `update_text_resolved` 零调用点** —— 状态：**已修**（2026-09-23；2026-09-21 首记，原状态「记账（不修）」）
- **事实（修复前，现跑）**：`core/distiller.py` `coref_resolve` 有定义、**全仓零生产调用点**；`update_text_resolved`（SQLite / PG 两个 store）同样零调用点。写侧唯一入口是 `save_text(..., content_resolved="", coref_resolved=0)`，两个调用点（`core/text_manager.py`）都没传 → 两列恒为 `''` / `0`。
- **读侧唯一消费者**：`web/routers/distill.py` `_get_distill_content` —— 仅当 `DISTILL_USE_COREF=1` 时取 `content_resolved`，而它恒空，`if resolved and ...` 恒假 → 与 `=0` 逐字等价。
- **性质**：不是「坏了」（没有正确行为可对照），是**整条支线从没接线**。按「三之二」的口径它本属「立项」；留在缺陷表是因为它**以可用特性的形态存在**（`DISTILL_USE_COREF` 开关 + `.env.example` 的有注释条目 + 两列 schema：SQLite `056_coref_resolved.sql`、PG 内联在 `001_init.sql`），读代码的人会以为它可以打开。
- **裁定（2026-09-23）**：**删，不接线**。设计 A —— 迁移执行器补一条与 ADD 对称的 DROP 支；不带 < 3.35 回退（老 SQLite 在启动时响亮失败）。
- **修法（净删，不新增捕获 / 映射 / helper）**：
  - **删函数与方法**：`core/distiller.py::coref_resolve` 整条（含它的 `ctx_submit` 派生点与 `distill_coref` 记账）；`web/routers/distill.py::_get_distill_content`（5 个调用点改回 `text_rec["content"]`）；两个 store 的 `update_text_resolved`。
  - **删字段**：`save_text` 的两个尾部参数从两侧实现删净（INSERT 列 / `ON CONFLICT SET` / `EXCLUDED` / SELECT 列表全部同步）；`storage/base.py` 本就只声明到 `user_id` —— 删完两侧实现与 base **逐格相同**，故 `tests/test_storage_contract_shape.py` 的 `PRE_EXISTING_GAPS["save_text"]` 出册（册子恢复为空，机制留在原地）。
  - **两条新迁移**：SQLite `storage/migrations/094_retire_coref_columns.sql`（两条裸 `DROP COLUMN`，登记在 AFTER 段 —— 必须晚于 BEFORE 段的 056）+ PG `storage/migrations_pg/027_retire_coref_columns.sql`（`DROP COLUMN IF EXISTS` ×2，与 025 同形）。PG `001_init.sql` 是历史文件，一个字没动。
  - **056 改为空操作**（删掉两条 `ADD COLUMN`，**文件与编号保留**，只留一行注释）：**根因是缺迁移账本 —— 台账 90 / 99**。没有「已应用」记录表，`_ensure_initialized` 每次启动都重跑全部迁移，于是「056 每轮把列加回来、094 每轮再删掉」这个净效果为零的循环，代价是**每一次启动都重写整张 `texts`**（`DROP COLUMN` 在 SQLite 里是「建新表 + 拷数据 + 换名」，而 `texts.content` 存的是全文）。056 停掉 ADD 后：新库从没建过这两列 ⇒（列都已不在）⇒ 094 的**整份跳过**当场成立 ⇒ 重启不再碰这张表；老库仍靠 094 的那一次 DROP 退役。**迁移账本落地后此事自然成立** —— 账本本身归 90/99，两条已于 2026-09-24 结案；056 / 094 的删除属 SQLite 侧（`storage/migrations/` 已冻结，`77f0179`）的独立清理，本条不碰。
  - **执行器补对称支**：`_apply_migration` 新增 `_DROP_COLUMN_RE`，与 ADD 支**共用同一套 PRAGMA 读现状**；「已生效」的判据相反（ADD 列已在 / DROP 列已不在）；整份跳过的条件是两种形态**同时**满足。docstring 里「`ADD COLUMN` 是迁移脚本里唯一「重复执行即报错」的形态」随之改写成两种形态。前提已写进 094 头部注释：`DROP COLUMN` 要 SQLite ≥ 3.35，更老的版本启动即**响亮失败**（执行器没有 except，不会静默留列）。
  - **残留清扫**：`core/scene_indexer.py` 幂等段 docstring、`.env.example` 的开关条目、`web/frontend/src/store/useAppStore.js` 注释、`web/routers/text.py` 四处注释（`_run_upload_task` 早已不跑 coref，注释是历史残留）、`tests/census_llm_call_contexts.py`（`ctx_submit` 4 行 / 3 处 → **3 行 / 2 处**，删 `Distiller.coref_resolve` 那一行）、`tests/test_migrate_sqlite_to_pg.py` fixture 的两列、`docs/specs/llm-access-gate.md` F7（**原文保留**，表下追加带日期的订正行）。
- **行为变化**：蒸馏正文一律取 `texts.content`。原先的默认路径也是原文（开关与消解列恒空），故这是**净删，不是语义变更**。
- **锁与红源（两条，各锁一半）**：
  - `tests/test_sqlite_fresh_schema.py::TestFreshSqliteSchema::test_retired_texts_columns_stay_retired_after_restart` —— 两次 init 后 `texts` 都无这两列、两次列集相等，**且重启时 094 整份跳过**（`monkeypatch` 监视 `aiosqlite.Connection.executescript`，断言没有任何带 `DROP COLUMN` 的脚本被发给 SQLite —— SQLite 侧只有 094 是 `.sql` 里的 `DROP COLUMN`，其余退役走 Python 重建）。两条缺一不可：只比列集会漏掉「每轮加回来又删掉」——那种形态下列集照样相等，而表每轮被重写。
    **变异（实测，本轮）**：把 056 恢复成两条 `ADD COLUMN` → **只红这一条**（12 passed / 1 failed，列集断言仍绿）—— 这正是加这条断言的判别力所在。
    **另一条变异（实测）**：把 094 从 `_MIGRATIONS_AFTER_USER_REBUILD` 摘掉 → 红三条（本条 + `test_every_migration_file_is_dispatched` + 依附它的 `test_exemption_has_one_source_in_the_executor`，后两条是「文件在盘上没人应用」的既有判据）。
  - `tests/test_migration_dispatch.py::test_already_satisfied_drop_column_is_stripped` —— 执行器级：造一份**两句 DROP、其中一句已生效**的脚本，已生效那句必须被剥、未生效那句必须真跑，连跑两次都不抛。**变异 = 删掉 `_apply_migration` 里 `_DROP_COLUMN_RE.sub(...)` 那一句 → 红**（`no such column: gone_col`，实测只红它自己）。
  - ⚠ **红源挂错了地方会假绿（S0 计划在此处偏了，当场改）**：计划写「1 条锁，变异抽掉 DROP 支 → 红」。实测抽掉 DROP 支**打不红**端到端那条 —— 当时 056 每轮已先把列加回来，没有 DROP 支时 094 的裸 DROP 照样执行成功。故拆成两条，DROP 支自己的判别力由执行器级那条承担。（056 空操作之后，端到端那条的判别力已换成「094 是否整份跳过」，见上。）
- **第三处改动：`tests/test_schema_parity.py` 收窄为「表集合 + 共有列可空性」**。056 停掉 ADD 后这条文本锁当场红（`[texts] columns in PG but missing in SQLite: ['content_resolved','coref_resolved']`）：SQLite 侧那两列**只**由 056 提供，而 PG 侧是 `001_init.sql` 的 `CREATE TABLE` 内联声明，且该提取器**看不见 `DROP`**。顺手的修法（提取器改成 DROP 感知）**实测是陷阱**：红点从 `texts` 平移到 `users` 的 5 列（`password_hash` / `api_key` / `base_url` / `model` / `is_admin`）—— PG 用 `.sql` 的 `DROP COLUMN` 退役这批列、SQLite 用 `_USERS_RETIRED_COLUMNS` 的 Python 重建，两个后端的退役手段**不同构**，文本扫描器永远读不到 Python 那一半。故把列集合判据交给**真库闭环**（`TestPgFreshSchemaClosure::test_fresh_sqlite_and_fresh_pg_have_the_same_columns`；CI 起真 PG 且 `REQUIRE_PG_TESTS=1`，`.github/workflows/build.yml` 两个 job 都有 —— 每次 CI 强制跑），本锁只留它读得准的两件事。**收窄后仍验过有牙**：表集合两侧不等 → 红；共有列可空性翻面 → 红（两条变异实测）。
- **判据现跑**：`git grep -n "coref_resolve\|update_text_resolved" -- '*.py'` → 除**迁移文件名**（`"056_coref_resolved.sql"` 这个登记项，文件已空操作但编号保留）与四处**说明性注释**（056/094/027 头部、census 的新读数段、contract_shape 的出册说明）外零命中；`git grep -n "DISTILL_USE_COREF" -- .` → 仅 AGENTS.md 本条 + 094/027 的注释。`content_resolved` / `coref_resolved` 在**可执行代码**里零命中（`storage/sqlite_store.py` 那一处是迁移文件名，不是列引用）。
- **验收**：三态（新库 / 老库 / 重启态）`texts` 都无这两列，且重启时 094 不再执行 DROP；干净形态全量 + 一次性 `postgres:16-alpine` 容器跑 `TestPgFreshSchemaClosure` 与重启态闭环（口令 / 端口只在运行期，不写 `.env`，跑完 `docker rm -fv`）。读数见交付报告。
**88. `distill_incremental_stream` 内联了一份 Map 循环的副本** —— 状态：**已修**（2026-09-22；2026-09-21 首记，原状态「记账（不修）」）
- **形态（修复前）**：`core/distiller.py:1820-1879` 的 `_map_with_progress` 自己重写了一遍 semaphore / `done_count` / `lock` / `failures` / `usages` / `_one` / `gather` / `aggregate_usage`，与 `_run_map_concurrent`（`:1407-`）同形。S1（`0605bdf`）已把 Map 参数化成 `build_prompt` + `usage_action`，**识别侧复用了它，流式蒸馏侧没有**；连「建 client → 跑 → 关」那一段的清理语义也是第二份手抄。
- **首记的「是设计活不是替换」对了一半**：流式侧确有三样独占 —— 续跑命中短路（`_resume_hit`）、逐片 checkpoint 落库（`save_distill_chunk` + `ON CONFLICT DO NOTHING` 的指纹语义）、进度经 `queue.Queue` 倒回生成器。但**通道本来就存在**（生成器自 S1 起就是线程 + 队列），且命中片可以**在调原语之前**就摘出去 —— 收进预扫之后，「原语只看未命中片」是唯一真正需要设计的一处，其余全是收口。
- **修法（2026-09-22）**：
  - 原语 `_run_map_concurrent`（`:1459`）只加 `ok` 标志：成功片 `ok = True`（`:1488`）、失败片 `ok = False`（`:1501`），回调签名改 3 参 `on_chunk_done(index, result, ok)`（`:1506`），docstring（`:1473-1476`）改述「`ok=False` 的片结果是空串且已计入 failures，调用方据此决定落不落 checkpoint」。同步侧 `_on_done`（`:1683`）同步改签名（标志在同步路径不参与计数，忽略）。
  - 流式 Map 相整段替换：预扫（`:1872-1886`，命中片零 LLM 调用经 `_resume_hit` 直接并入 `map_results`，并就地补发其进度事件）→ `_on_chunk` 闭包（`:1893-1897`，**全函数唯一一处**把原语侧未命中片下标 `j` 映射回 `relevant` 的原始下标；指纹与 checkpoint 用的都是原始下标）→ `_thread_run`（`:1899-1911`，调 `_run_map_with_client`，即收口后全仓唯一建/关 Map client 处）→ 队列排空循环（`:1916-1943`，成功片回调落 checkpoint、失败片点名不落且打印）。
  - **全命中也走原语**（不分叉）：`miss_indices` 为空时原语收到空列表 —— `_run_map_concurrent` 对空 `usages` 的 `aggregate_usage` 返回 None、不写零成本假账、零 LLM 调用。
- **行为变化**：
  - client 关闭语义收敛到 `_run_map_with_client`（`:1431`）一处；流式侧原先自己 hand-rolled 的建/关副本删除。
  - 回调契约由「2 参、指纹在回调内算」改为「3 参；指纹仍由本侧从 `relevant[idx]` 现算」（只有生成器侧拿得到原文）。
  - 失败率**分母不变**，仍是全书相关片数 `total_chunks = len(relevant)`（流式 `:1952`、同步 `:1712`）—— 命中片不进原语但仍计入分母，否则续跑会让失败率虚高、越线整批 bail。该口径已补注释锁在 `:1950-1951`。
  - 文档陈旧行号随本步修正：`_resume_hit` docstring（`:237`）改述主屏障在流式侧（失败 Map 片不落 checkpoint，`ok=False` 即不回调落库）。
- **判据命令**：`git grep -n "Semaphore(self._map_concurrency)" -- core/` → **1** 处（`core/distiller.py:1478`；修复前 2 处）；`git grep -n "_map_with_progress" -- core/` → 零命中（余 1 处在本条「形态（修复前）」的叙述里）。
- **锁与红源**：先按「现有测试能打红的变异不新增锁」把 L1–L4 逐条打到既有用例上，每条变异跑完把 `core/distiller.py` 按字节还原（逐字节回到原状，无半改残留）。各条变异**改了什么**与读数：
  - **L1｜预扫直通**：把预扫改成命中片也进 `miss_indices`（先 append 再判 `hit is None` 才 continue），即整条 `relevant` 交给原语 → 红 **8** 条，全在 `tests/test_distill_resume.py`；代表读数 `assert 12 == 0`「全命中不该再发任何 Map 调用」。
  - **L2｜回调 `ok` 恒真**：`on_chunk_done(i, result, ok)` → `on_chunk_done(i, result, True)`，失败片被当成功 → 红 **2** 条：`TestFailedChunkNotCheckpointed::test_failed_chunk_is_not_checkpointed`（失败片落了 checkpoint）、新锁 `TestFailureRateDenominator::test_hits_still_count_in_the_denominator`。
  - **L3｜生成器里加回整阶段落账**：在 `_thread_run` 调完原语后补一句 `self._try_record_usage("distill_map", aggregate_usage([...], 1))`（重构前那份内联记账的形态）→ 红 **1** 条：`tests/test_usage_identity_context.py::test_start_route_lands_usage_rows`，读数 `assert 6 == 5`（多落一行 `distill_map`，与出口 5 笔对不上）。
  - **L4｜分母改成未命中片数**：`total_chunks = len(relevant)` → `len(miss_indices)`，以及带保护的 `max(1, len(miss_indices))` 形态 → 红 **5** 条，含下面的新锁；其余 4 条**全部**来自全命中退化路径的 `ZeroDivisionError`（`core/distiller.py:266` 的 `failed / total`，`total == 0`）—— 退化解，钉不住口径本身，故必须补锁。
- **L4 专属锁（本步新增，补上后已删去上版台账的「残留覆盖缺口」）**：`tests/test_distill_resume.py::TestFailureRateDenominator::test_hits_still_count_in_the_denominator` —— 12 片里 9 片命中（不进原语）、3 片未命中且其中 2 片失败：`2/12 ≤ 1/2` 在容忍内，`2/3 > 1/2` 越线。断言：不中止（无 `error` 事件、有 format token）+ 打印 `2/12 map chunks failed (within tolerance), continuing`（一行同钉分子与分母）+ 3 片未命中里只有活下的那 1 片落 checkpoint。分母换成未命中片数时走 bail 分支、该行根本不打印 → 必红 —— **`len(miss_indices)` 与 `max(1, len(miss_indices))` 两种形态实测均红**。
- **不加 L5（显式声明）**：进度事件的**顺序**（命中片是否仍早于未命中片上屏）**不是契约** —— 它只影响进度条抖动，不改变任何落库 / 计费 / 续跑结果，故不为它立锁。

**89. 同一份「识别结果」存了两遍：进程内 TTL memo + 库里的 `characters_json`** —— 状态：**已修**（见本线 commit `d565a5a`，2026-09-22；2026-09-21 首记，原状态「记账（不修）」）
- **两份的键不同**：进程内 memo（`core/distiller.py:884`）键 = `text_fingerprint(text) + ":" + model`，TTL 600s、上限 100 条、寿命是**进程**；库缓存键 = (text_id, 属主, `IDENTIFY_VERSION`)，寿命是**库**。
- **两者的失效条件不一致**：`characters_version` 一改，库缓存立刻当无缓存重算；而 memo 原先不认版本号 —— `identify_characters` 的注释（`core/distiller.py:870-871`）明写「版本判定在 `core/character_roster.py` 那一层，这里不管版本」。于是版本变更后 roster 会调一次识别、**拿到 memo 里的旧口径名单**、再以**新版本号**写回库 —— 旧名单被洗成新名单，S3 的版本门形同虚设。
- **当前不可达（判据不是推论，2026-09-22 S0 复核现跑）**：`IDENTIFY_VERSION` 是类常量（`core/distiller.py:321`，唯一定义），改它必改代码，改代码必重启进程，重启则 memo 为空。复核命令与读数：`git grep -n "IDENTIFY_VERSION" -- .` → 生产命中只有 `core/distiller.py:321`（定义）+ `core/character_roster.py:92/:115`（唯二读取处），`CONFIG*` / `docs/` 零命中；`git grep -n "os.environ\|getenv" -- core/distiller.py` → 零命中。窗口要打开需要**一个不改代码就能变版本号的入口**（读 env / config），现无。
- **判据命令**：`git grep -n "IDENTIFY_CACHE" -- core/`、`git grep -n "IDENTIFY_VERSION" -- core/ CONFIG* docs/ 2>/dev/null`（版本号若出现在 env/config 读取处，窗口即已打开）
- **修法（2026-09-22）**：版本号并进 memo 的键 —— `core/distiller.py::identify_characters` 的 `key = f"{text_fingerprint(text)}:{self._llm.model}:{self.IDENTIFY_VERSION}"`，并把「这里不管版本」那段 docstring 按事实改写为「键必须覆盖全部决定输入」。库缓存的版本判定仍在 `core/character_roster.py` 那一层（两道缓存各守各的版本）。
- **锁与红源（形态锁，不是缺陷复现）**：`tests/test_character_roster.py::TestMemoKeyCoversIdentifyVersion::test_version_change_is_a_memo_miss` —— 灌 memo → `monkeypatch Distiller.IDENTIFY_VERSION` → 再 `resolve_characters`，断言 LLM 被再调一次、落库的是新名单且版本号是新的。**变异 = 从键里去掉版本号 → 该用例红（1 failed, 6 passed）**。它复现的是**生产当前不存在的时序**（常量变更必经重启），故锁的是**「缓存键覆盖全部决定输入」这一形态**，不是可达缺陷 —— 交付里已显式声明。
- **随修收口的一条**：`resolve_characters` 的 `refresh` 参数已删除（生产零调用方、前端无入口，且真调用也会被 memo 吃掉 ⇒ 原先 docstring 说的「用户显式点重新识别」不成立）；`tests/test_character_roster.py` 里对应用例一并删除。

**90. 两个后端每次启动都重跑全部迁移，且没有「已应用」记录表** —— 状态：**已修（PG 侧 `5148ab6`，2026-09-23）**（原记「不修」是因为本条是「两个后端」的共性问题、只修一侧不算修完；2026-09-24 给 SQLite 侧定了结论，两半都有归属，故状态升。**SQLite 侧判为不修**：SQLite 已正式放下（`77f0179`，2026-09-24：备用后端、不测试），生产与本地开发只跑 PG；按主次规则（PG 为准、SQLite 只保接口一致），SQLite 专属问题只记录、不修）
- **事实（2026-09-21 首记读数；PG 半 2026-09-23 已改，见下；SQLite 面按本条判据不修，保持原样）**：当轮全仓无 `schema_migrations` 一类记录表。SQLite 的幂等靠**读现状**（`storage/sqlite_store.py:97` / `:195` / `:249` 的 `PRAGMA table_info`，逐列比对后再决定加不加 —— 这一面**至今未变**）；PG 靠 `ADD COLUMN IF NOT EXISTS` / `DROP COLUMN IF EXISTS`。启动时**每一份** `.sql` 都会被解析并执行一遍（PG 侧今已改为「只跑没记过账的」）。
- **后果（缺陷 21 / 23 已各自从一侧记过，这里记共同的前提）**：① 迁移文件**不能重写** —— 改一个已应用过的迁移，对已有库是 no-op，而对新库生效，于是「文件里的第 N 号迁移」与「这个库实际经历过的变更」没有对应关系；② 排查时无法回答「这个库跑过哪些迁移」；③ 每次启动都要把整批文件重新解析、逐条试探性 DDL。
- **为什么当时只记不修**（2026-09-21 的口径；2026-09-23 本轮已把 PG 半立项落地，见下条；SQLite 半按本条判据不修，不再是待办）：加记录表要处理「已有库首次见到这张表时如何回填」——回填本身就是一次不可验证的猜测（拿什么当真源？文件列表？），且会与缺陷 21 的豁免清单、缺陷 23 的 fresh-schema 锁互相纠缠。属于基础设施改造，得单独立项。**裁定（2026-09-23 S0）：真源不猜 —— 存量库首次 = 账本表为空 → 无 skip、全量重放一遍并逐份记账。**
- **PG 半已落地（2026-09-23，见本线 commit `5148ab6`）**：共用判定抽成 `storage/migration_ledger.py` —— `pending_files(已记录, 磁盘文件, 顺序)` 与 `file_sha256` 两个纯函数，无 IO（读盘 / 连库 / 记日志仍归各自执行器，因为两执行器的**判据**必须同口径，分开写会各自漂移，而漂移只在某一侧表现为「迁移没跑」）。`PostgresStore._run_migrations` **就地建** `schema_migrations(filename, sha256, applied_at)`（表本身不能写成迁移文件，否则先有鸡还是先有蛋），每份迁移用显式 `conn.transaction()` 包住「正文 + 记账一行」，已记账的文件跳过，已记账但 sha256 不符 → 一条 `logger.error` 后继续启动（复用现有 ERROR 邮件告警；静默等于没有判据）。`file_sha256` 对**文本**取而不是对原始字节：`Path.read_text` 已把换行归一成 `\n`，同一份文件按 autocrlf 检出成 CRLF 不会算出不同摘要 —— 判的是「内容改没改」，不是「这台机器的检出风格」。
- **豁免清单与守卫（原为过渡态，今为终态）**：`tests/test_postgres_store.py` 的 `_ENGINE_INTERNAL_TABLES = frozenset({"schema_migrations"})`、它在 `_pg_columns` 里的过滤、以及 `test_column_probe_has_teeth` 里那条「名单里的名字不得出现在任一迁移目录」的守卫 —— 三处都是为「账本表只在 PG 侧存在」这个**过渡态**加的，原计划等 SQLite 半落地、两侧都有这张表后连同名单一起删。**SQLite 侧已定不修（见本条状态），此过渡态即终态**：PG 的 `schema_migrations` 是执行器内部表、不为任何迁移文件所声明，豁免与守卫都留住，且**名单里只许有这一个名字** —— 加第二个就是把列级闭环重新开洞。
- **本轮实测的代价（要一并记住，别当成已解决）**：账本只回答「这份文件跑过没有」，**不**回答「库里的东西是不是文件里写的那个」。两条口子：① 已记账文件内容被改 → 永不重跑，只报一条 ERROR（改**既有**约束仍须 `DROP`+`ADD`，见 99）；② 库被账本之外的手段改回去（如测试夹具删列）→ 文件没变，账本照旧跳过、什么都不报。
- **锁与红源**：`tests/test_postgres_store.py::TestPgMigrationLedger` 三条 —— ① 重启不再执行已记账文件（用 `asyncpg.Connection.execute` 探针数正文被执行几次，并先断一次「第一次确实跑了」，免得用例恒绿）；② 迁移中途失败 → 账本无该行且正文建的表回滚掉；③ 改动已记账文件内容 → 恰记一条 ERROR 且启动成功、账本摘要不被改写。**变异（实测红，验后已还原，跑在一次性 PG 容器上）**：① `pending_files` 忽略 `recorded` → 红；② 记账挪到正文之前、脱离同一事务 → 红；③ 去掉内容不符的报错 → 红；④ 内容不符时把账本摘要改写成新的 → 红。**未入库变异驱动**：`tests/test_postgres_store.py` 不属任何 `tests/perf/*_red_lines.json` 的 domain，给它挂驱动会把整文件近百条判据拖进元锁（`tests/test_lock_coverage.py`）的覆盖域，故按 75 / 101 的惯例在本条逐条记录。
- **判据命令**（读数 2026-09-23 现跑）：`git grep -n "schema_migrations" -- storage/` → **3 行，全在 `storage/postgres_store.py`**（`:28` 就地 DDL、`:184` 读、`:195` 写）—— `storage/migration_ledger.py` **零命中**是对的，它是纯函数，账本内容由参数传进、不认表名；`git grep -n "pending_files\|file_sha256" -- storage/postgres_store.py` → **4 行 = import 1（`:18`）+ 调用点 3**（`pending_files` `:190`、`file_sha256` `:196` 与 `:203`）；`git grep -n "schema_migrations" -- storage/sqlite_store.py` → **零命中**（SQLite 半判为不修，故这条读数**应当**保持零命中，不是待补的缺口）。旧判据「`git grep -n "schema_migrations\|migration_history\|_applied\b" -- storage/` 应零命中」**已作废**（PG 半落地后必然命中）。**坑**：`git grep -c` 对零命中路径**静默不打印**且退出码 1，别把「没打印」当成「没跑」。
- **并发边界（只记不做）**：账本主键是 `filename`，两个 worker / 多副本**同时首启**会在同一份迁移上撞 `unique_violation`（`CREATE TABLE IF NOT EXISTS` 本身也有竞态）。**当前生产单 worker，不可达。** 将来上多 worker 时，给 `_run_migrations` 加一把 PG advisory 锁（`pg_advisory_xact_lock(<常量>)` 包住整段）即可 —— 现在不做：为一个不可达的形态加锁是纯增复杂度。

**91. 非流式截断那一笔记账是空转 —— 出口拿到 `usage=None`，一条也不落库** —— 状态：**已修**（2026-09-21，记账收敛那一轮的下一项）

- **事实**：`_chat_accounted` 非流式支在 `length` 截断的返回路径上调 `self._try_record_usage(action)` —— **载荷为 `None`**。出口 `try_record_usage` 见此走 `if usage is None: usage = llm.last_usage`（`core/utils.py:74`），而 `adapters/llm_adapter.py` 的 `chat()` 进本轮就先 `self.last_usage = None`（`:634`），`_extract_content` 的抛出点又在 usage 回写（`:651`）**之前** ⇒ `if not usage:` 命中，打印一行 `[Distiller] usage not recorded: no usage data` 后 `return`，**一条不落库**。
- **与流式支的关系**：**同一形态，只修了一半**。`_collect_stream` 那条截断路已在上一轮（记账收进调用原语）按 `estimate_usage_from_chars` 补记，非流式支没跟上 —— 于是「截断烧掉的 token」在**长输出走流式**的路上有账、在**短输出走非流式**的路上无声无息（生产口径：系统性偏低，且低于哪一段取决于输出长短）。
- **为什么既有测试看不见（关键）**：`tests/test_distill_usage_accounting.py` 的 `usage` 探针**只收 `action`、丢掉载荷** —— `test_truncated_initial_call_is_still_recorded` 断言的 `["distill", "distill"]` 在「记了一条空载荷」与「记了一条估算」两种实现下**同样成立**。**判据读的是动作序列，不是载荷**，与缺陷 25「签名的代理代替 SQL 事实」同谱系：形态对、事实错。（同一文件的流式用例之所以能判，是因为它用的是连载荷一起收的 `records` 探针。）
- **修法**：非流式支把截断那条路的 `usage` 置为 `estimate_usage_from_chars(self._prompt_chars(system_prompt, messages), len(reply))`；两条截断路共用新抽的 `Distiller._prompt_chars`（原先这个字符数只在 `_collect_stream` 里现算）—— 「按字符估算」只留一个口径。正常返回那条路**不动**（`usage=None` ⇒ 出口回落 `last_usage`，那才是真实读数，不是估的）。
- **锁**：`tests/test_distill_usage_accounting.py::TestNonStreamTruncationAccounting::test_truncated_chat_records_one_estimated_entry`（断言 `[a for a,_ in records] == ["distill"]`、载荷 `== estimate_usage_from_chars(两侧已见字符)`、`estimated is True`）。**红源已钉**：用例先写、先跑 —— 红在 `assert payload is not None`（`1 failed, 6 passed`，同文件其余用例不受影响）；改完 `26 passed`（连同 `test_distiller_truncation_selfheal` / `test_usage_accounting_lock` / `test_usage_identity_context`）。
- **判据命令**：`git grep -n "_prompt_chars" core/distiller.py` —— 应恰三处：定义一处 + 两条截断路各一处。**口径的唯一性靠这条**，不靠数 `estimate_usage_from_chars` 的命中（那个字符串全仓 6 处，另有 map 失败片 `1472` / `1894` 与压缩 `1724`，故它对「两条截断路共用同一口径」没有判别力 —— 本条初稿写的「应恰三处」是**错的**，落笔时按想当然写、没现跑，当场订正）。
- **顺带**（同一文件内的重复）：造截断异常的 `_Msg` / `_Choice` 三件套原先内联在 `test_truncated_initial_call_is_still_recorded` 体内，新用例要用第二遍 —— 提为模块级 `_truncated_exc(where)`，那条老用例改调它（净删 8 行，断言不变）。

**92. 非流式硬失败一条账都不记 —— 同一个事实在流式/非流式两侧记出两个数** —— 状态：**已修**（2026-09-21，缺陷 91 的下一项）
- **事实**：`_chat_accounted` 非流式支的硬失败分支（`info is None or info[0] != "length" or not info[1]`）打印一行后直接 `raise`，**记账出口一次都没走到**。而流式支 `_collect_stream` 的 `except` 支在同一个条件下先 `estimate_usage_from_chars(prompt_chars, len(text))` 补记、再 `raise`。于是**同一件事**（这次调用没产出正文，但 token 花了 —— 重试墙下正是空烧）在长输出（走流式）有账、在短输出（走非流式）无账。
- **为什么它是缺陷而不是设计（找的是可判定的理由，不是「像不像」）**：旧口径只写在对它的描述里 —— `core/distiller.py` 的「``raise`` 那条路不记」与 `tests/test_distill_usage_accounting.py` 模块头的同一句。但**说不出判据**：凭什么叫截断该记、硬失败不该记？两者都是「token 已花、正文没拿到」，都拿不到 `last_usage`（`chat()` 进本轮清空，`_extract_content` 的抛出点在 usage 回写之前）。**理由不改口径就是遗留，不是决策**（§四「理由要升格成判据」）。同一取向本仓早有先例：map 失败分片与档案压缩那两处的注释就写着「失败重试墙下空烧，只记成功 = 统计系统性偏低」。
- **修法**：硬失败支在 `raise` 之前补记 `estimate_usage_from_chars(self._prompt_chars(system_prompt, messages))` —— completion 侧按 **0** 算（没有产出任何正文，这正是它与截断支传 `len(reply)` 的唯一差别）。两处 docstring 改写成新不变量：**一次调用恰记一条 usage，与它结果如何无关**。
- **锁**：`tests/test_distill_usage_accounting.py::TestNonStreamHardFailureAccounting::test_hard_failure_records_one_estimated_entry`（断言 `[a for a,_ in records] == ["distill"]`、载荷 `== estimate_usage_from_chars(两侧字符, 0)`、`estimated is True`）。**红源已钉**：用例先写先跑，红在 `assert [] == ['distill']`；改完 17 passed（连同 `test_usage_accounting_lock` / `test_distiller_truncation_selfheal`）。
- **形状锁不受牵连**：`test_usage_accounting_lock._audit()` 的配平是 `records >= need`，且互斥分支合并计一组 —— `_collect_stream` 早已是同形（try 一支 + except 一支），本条只是让非流式支对齐它，没有改写配平口径。
- **判据命令**：`git grep -n "estimate_usage_from_chars" core/distiller.py` —— 全文 **6** 处：`_collect_stream` 的 except 支 1 处、`_chat_accounted` 体内**恰 2** 处（硬失败支 + 截断支）、余 3 处是 map 失败分片与档案压缩（与本条无关）。**注意这条判据与缺陷 91 那条不是同一条**：91 要的是「两条截断路**共用同一个口径**」，判据是 `_prompt_chars` 恰三处；本条要的是「两条非流式出口**各自**按字符估算」，才数 `estimate_usage_from_chars`。

**93. 用量写库失败只留一行 print 就被吞 —— SG 全员用量为 0 直到人工发现** —— 状态：**已修（可见性）**（2026-09-22；机制根因 2026-09-21 已修，见下「修法」两条）
- **链路**：记账出口是 `core/utils.try_record_usage`（`core/utils.py:51`），以 `submit_to_main_loop(_write(), wait=False)`（`:98`）投递 —— 写库跑在别处，请求线程不等它。内层 `_write` 的 except 只 `print(f"[{source}] Record usage failed (non-fatal): {exc}")`（`:96`），**不重抛、不置标志、不影响响应**。写侧 `PostgresStore.record_usage`（`storage/postgres_store.py:3014`）自己 print 一行后抛 `StoreError`（`:3022`）—— 这一抛正落进上面那层 except 里。
- **实害（SG 2026-09-21 只读取证）**：`usage_stats` 的 `max(id)=104` 而序列 `last_value=2`，每条 INSERT 都撞 `usage_stats_pkey`；表里最后一行 `created_at = 2026-06-26 19:10:28+00`（当天 0 行、近 7 天 0 行）；全部 104 行 `action='chat'`，从无 `distill_*` 行。设置页「我的用量」与 admin 用量页都是 0 —— **展示层是忠实的**（`web/routers/auth.py:577` → `get_usage_stats`；`web/routers/admin.py:397` → `get_all_usage_summary`），它读的就是这张冻住的表。
- **修法（两段，别混着读）**：
  - **机制根因**（2026-09-21）：新增 `storage/pg_identity_sync.py`，启动时与导入脚本末尾对齐全部 identity 序列 —— 让写入**能**成功。这一修不改「失败会怎样」，只去掉「必然失败」这个前提。
  - **可见性**（2026-09-22）：吞异常那一层不再只 print，改为上报后台日志面板。新增唯一构造 `core/nonfatal.nonfatal(source, what)`（异步上下文管理器：吞掉块内异常并以 logging ERROR 上报，`exc_info=True`；`CancelledError` / `KeyboardInterrupt` / `SystemExit` 照常上抛）—— 面板 `RingBufferHandler` 只收 WARNING+ 且只存 `getMessage()`，故异常类型由本构造拼进消息文本。`core/utils.try_record_usage` 的 `_write` 是改点之一。
  - **仍未定的那半**：「账写不进去」要不要进一步升级（对用户报错 / 健康检查置红 / 计数告警）是**产品口径决策** —— 用量算不算必须送达的账，得先定。本轮只解决「无声」。
  - **2026-09-22 补记：主动告警已上线**（与缺陷 98 同轮做，不在本条的修法内）。新增 `core/alerting.AlertHandler`：ERROR 级日志按 `(logger 名, 异常类型)` 节流后投递到 `ALERT_EMAIL`（同键 1 小时一封，窗口后那封附上被压下的次数），在 `web/server.py` 的 lifespan 里与 `install_log_collector()` 同处挂到 root logger。本条要的其实是**两跳**：`print` → 面板（可见）是缺陷 93 修的那一跳，「面板 → 人」是这一跳 —— 面板得有人打开才看得见，SG 的「数月无人察觉」正卡在后一跳。上面「仍未定的那半」里的「计数告警」至此**机制已具备、口径仍未定**：要不要让用量写库失败真的发信仍是产品决策，现在只是有了通道（`ALERT_EMAIL` 为空时整条不安装，见 `core/alerting.py`）。
- **判据命令**：`git grep -n "Record usage failed" core/utils.py storage/postgres_store.py` —— 现为 **1** 处（`storage/postgres_store.py:3022`，`print + raise`，异常还没到终点、在那里记会重复）。`core/utils.py` 那侧**应为 0**：它已改为 `async with nonfatal("usage", ...)`。可见性的锁在 `tests/test_nonfatal.py::test_try_record_usage_reports_write_failure`（断言面板收到恰好 1 条 ERROR 且含异常消息）。

**94. 同一个消息保存失败，群里摊给用户、一对一静默丢** —— 状态：**已修**（口径统一，2026-09-22；可见性 2026-09-22 收口 → 泄漏那半 2026-09-22 收口 → 口径统一 2026-09-22 收口 → 消息真正落库 2026-09-23 收口）。**覆盖范围：一对一聊天（非流式 + 流式）、群聊广播、群聊非流式 `/send`（121）、开场白与重逢问候（122）** —— 72 线之前的两处范围缺口已并进来，见下「机制」。
- **事实**：一对一路径**全部吞** —— 非流式三笔（用户 / 角色 / 摘要）共用一个 try（`web/routers/chat.py:339-375`，except 只 print `Dual-write messages failed (non-fatal)`）；流式三笔各自 try + print（`:449-456` / `:459-505` / `:520-525`）。群聊非流式同样吞（`web/routers/group.py:509-520`），但**流式**把整个生成器包在一个 try 里（`:558`），`except Exception as exc`（`:633`）把异常当 SSE 事件发给用户（`:634`，`{'error': str(exc)}`）；三条保存点（用户 `:575`、助手 `:597` / `:610`）都在那个 try 之内且各自没有兜底 —— 保存失败会中断本轮回复，并把内部异常文本摊到用户面前。
- **为什么这是缺陷而不是设计**：两边各自都说得通，但**同一件事**（PG 拒绝写入）在两处给出相反的可见性，而判据（该不该让用户看见）从没被写下来过。真要在两边做不同选择，就得同时说清「群里为什么该看见、一对一为什么不该」—— 说不出就是遗留（§四「理由要升格成判据」）。另外把 `str(exc)` 直接回给前端，本身还是一条信息泄漏面。
- **已做的（可见性，2026-09-22）**：一对一那侧**每一条吞错点**（非流式三笔共用的一个 `except`、非流式的摘要一笔、流式的用户 / 助手 / 摘要三笔）连同 `group.py` 非流式的两笔保存、`web/routers/distill.py` 的开场白保存、`web/routers/history.py` 的重逢问候，全部改为 `async with nonfatal(source, what)` —— 失败从「一行 stdout」变成「后台日志面板一条 ERROR」。**行为不变，仍是吞**。
- **仍未定的那半（要产品决定）**：群的**流式**那条把 `str(exc)` 当 SSE 事件摊给用户（`group.py:632` 的 `except Exception as exc` → `:633` 的 `yield ... {'error': str(exc)}`）—— 本轮**没动**。它与一对一的「吞」是两套口径，而要统一就得先答「群里为什么该看见、一对一为什么不该」（说不出就是遗留，§四）。另外把内部异常文本直接回给前端本身还是一条信息泄漏面。
- **泄漏那半已修（2026-09-22，分支 `worktree-session-cred`）**：**同根三处**一起收口 —— 上屏文案一律走 `adapters/llm_adapter.py` 的 `user_facing_error` 唯一出口，上游原文只进日志：
  - 群聊 SSE 错误帧 `web/routers/group.py`（`36c81c0`）。
  - 一对一 chat SSE 错误帧 `web/routers/chat.py::_stream_error_payload`（`393debe`）—— **它当时正是「故意原样透出」的那处契约**，`user_facing_error` 的 `preserve_unknown` 开关就是为它设的；本轮把开关**删掉**（全仓唯一调用方就是它）。
  - 上传任务状态行 `web/routers/text.py::_run_upload_task`（`c3972ff`）。
  - 另加 `UpstreamFailure`（`2205b12`）：重试耗尽时抛的异常自带 `user_message`，上游已知状态码（401/403 key、402 余额、429 限流）给出能指导下一步的中文提示，未登记的状态码落通用文案；`str()` 逐字不变，日志与 core 侧既有的 `failed after N attempts` / `rate limited (429)` 判据继续命中。
  - **这三处不新开台账号**：它们与 94 是同判据、同一个决定面（§四「理由要升格成判据」），一并收口才自洽；**可见性那半原样未动**（群聊流式仍把保存失败当 error 事件发出去，只是帧里的值换了）。
- **判据命令（泄漏那半，2026-09-22 现跑）**：`git grep -n "preserve_unknown" -- ':!AGENTS.md'` → **空**（开关已删；AGENTS.md 里那句是历史快照，故排除）；`git grep -nE "'error': str\(exc\)|\"message\": str\(exc\)" -- web/routers/chat.py web/routers/group.py web/routers/text.py` → **空**。**上面那条「判据命令」（可见性那半）不变**。
- **同轮例外（2026-09-22，`9dc4118`）**：`web/routers/auth.py::test_embedding` 是**连通性测试**，用户点它就是为了查自己的 key 哪里不通，故**有意**把上游原话（`detail`）连同中文提示一并返回。判定只按 `status_code` / `code` / 异常类型（`core/embeddings.py::describe_embedding_failure`），不匹配报错文本。它**不在本条「一律通用文案」的口径内** —— 与上面三处（日常路径，原文只算泄漏面）性质不同，审计时不算漏修。
- **判据命令**：`git grep -n "save_group_message" web/routers/group.py` 与 `git grep -n "save_message" web/routers/chat.py`，逐个数「这个保存点的最近一层 except 是 `nonfatal` 还是 `yield` 一条 error 事件」。读数（2026-09-22 现跑）：`group.py:574/596/609` 三条的最近一层 except 仍是 `:632`（yield error，未动）；`chat.py` 的每一条都已是 `nonfatal` 包裹（`:340` / `:366` / `:446` / `:492` / `:513`），`Save user message failed` / `Save assistant message failed` / `Save summary failed` / `Dual-write messages failed` 那四行 print 全部应为 0 命中。
- **行号漂移**：上面正文里 2026-09-21 的读数是**改动前**的坐标（`chat.py:339-375` 等）。本轮把 9 处吞错点换成 `async with nonfatal(...)` 后，`group.py` 现存段上移 2 行、`chat.py` 上移若干 —— 现读以「判据命令」那条为准，别照抄正文里的旧行号。
- **口径统一已修（2026-09-22，分支 `worktree-session-cred`，commit `fc18018` / `e17b6aa` / `60fec7e` / `bf6a50b` / `6c042d5` / `38c8428` / `09818fe`）**：产品口径定为四条 ——
  1. 聊天**不因保存失败中断**；存失败的那一条旁标「未保存，刷新后会丢失」。
  2. 摘要存失败**不提示**，只记日志。
  3. 一对一与群聊的**用户可见行为一致**（不要求共用代码路径）。
  4. **不自动重试、不弹窗**；连续多条失败也**不**升级为弹窗或汇总提示。
- **机制（`fc18018` 立口径；`cd756a6` 换成队列实现，2026-09-23）**：口径没变，换的是「存失败之后怎么办」—— 从「吞掉并标记」换成「**留在队里、找机会真写进去**」。
  - **不变量**：每条消息入队时拿一个幂等键（`client_key` 列 + `(session_id, client_key)` / `(group_id, client_key)` 上的部分唯一索引），**队尾入队、队头补写、写成功才出队**（`core/message_outbox.py`）。补写发生在**本轮写入之前**，故补上的行 id 必定小于本轮新写的行 id —— 读回来的先后与用户看到的一致，不会出现「补写的摘要插到了那条消息前面」。
  - **两个边界**（`flush` 是模块里唯一的判断分支）：写失败后探一次 `ping` —— **库不可达** → 全队保留，这条是 `pending`（写不进去是所有消息的共同处境，继续往下写只会把后面每条一条条判死）；**库可达、这一条有问题** → 重试**一次**，仍失败才丢掉（`failed`）并继续下一条。
  - **四个补写时机**：下一次写入（`outbox.write` 自带，故 121 的 `/send` 只要走它就自动有一条路）、两个重试接口（`POST /api/chat/{sid}/flush`、`POST /api/group/{gid}/flush`，前端「重试」按钮打的）、空闲清理**出队之前**（`deps._session_cleanup_loop` —— 出队之后队列跟着没了）、进程关停（`web/server.py::_lifespan`，排在「取消清理循环并等它退出」之后，反过来两边会同时 flush 同一把队列）。异常退出（崩溃）丢队列是已知边界。
  - **字段**：帧 / 返回体带 `save`（`SaveState.as_json()`；**落库时整个字段为 `None`**，不是 `{"state": "saved"}`）与本次补写的 `flushed` / `dropped`。前端按 key 在后续 `flushed` 里认领真 id。
  - **队列不持有存储实例**（缺陷 117 的教训）：`ping` 每次调用时传入，补写出口 `deps.flush_outboxes` 现取 `get_storage()`。持有的话，存储被换掉之后补写会一直问那个作废的库。
  - `core/nonfatal.py::nonfatal` 那半仍在（`NonFatalOutcome.failed`）：队列内部每一步失败都过它，只记一条 ERROR，不打断本轮。`fc18018` 的逐块拆分（摘要块把 `get_messages` 也包进去、`bf6a50b` 复原 silent 帧守卫、`09818fe` 包住 `toggle_reaction`、`6c042d5` 先置 `msg_id=None`）改由队列承接，那些取舍仍然成立。LLM 失败仍走原 error 帧。
- **前端（`60fec7e` 立；`4c9b6df` 2026-09-23 改判据源）**：判定与文案仍各只写一份 —— `web/frontend/src/utils/withSaveResult.js` 与 `web/frontend/src/components/common/UnsavedHint.jsx`。变化是**判据的来源字段**：不再看后端布尔 `saved`，改看 `save`（`SaveState.as_json()`）——判「有没有」而不是「真不真」：`save` 缺席 = 已落库（hidden 用户消息、摘要帧、旧接口都不带它），拿 falsy 当失败会给用户标出满屏「未保存」。`withSaveResult(msg, save)` 只在 `save` 存在时多出 `saveState` / `saveKey`；`applyFlushReport` 把 `flushed` 里认领到的换成真 id、把 `dropped` 里的翻成 `failed`。**两态两种提示**：`pending` → 「未保存，刷新后会丢失」+ 重试按钮（打对应会话的 `/flush`），`failed` → 「保存失败，刷新后会丢失」不给重试。落点仍是四处（非流式发送、流式 done、撤回提示 `_sendRevokeNotice`、群聊帧 `payload.save`）。失败时 `msg_id` 为 `null`，落点保留乐观 id（`?? prev.id`）—— 撤回与反应都拿 `msg.id` 当参数。
- **未保存消息不提供需要 id 的操作（2026-09-22 补，commit `38c8428`）**：保留乐观 id 的代价是它会被当成服务端 id 用出去 —— 引用回复它时下一条消息带 `reply_to_id: "optimistic-…"`，后端 `int` 校验 422，**用户下一条就发不出去**；点反应同样 422，只在控制台报错。四个调用点（`ChatArea.jsx` 一对一、`GroupChatPage.jsx` 用户气泡与角色气泡）在 `m.saveState` 时不传 `onReact` / `onReply`，复用 `MessageReactions` 既有约定（没传 handler 就不渲染对应入口，组件本身没改）。一对一的 `if (!msg.id) return` 保留不动。判据：`UnsavedNoActions.test.jsx`（V4 群聊两处气泡、V5 一对一），各自有独立红源（逐个调用点去掉条件都会红）。
- **订正（2026-09-22）**：本条 S0 原写「无 id 的消息前端本来就有（流式途中），反应已有 `if (!msg.id) return` 兜底」—— 实读该守卫**只在一对一 `ChatArea.jsx` 里有**，群聊 `reactToMessage` 没有，是 spec 描述有误；据此删去的「未保存条不给操作入口」一步已按上面补回。
- **残留风险（`e17b6aa`，第 2 步）**：`storage/sqlite_store.py::save_message` 的读回 `SELECT` 已挪到 `commit()` **之前**、同一连接同一事务，故本地库上「异常 ⇔ 没入库」成立。**唯一无法区分的是 COMMIT 应答在网络上丢失**（事务其实已提交，调用方却收到异常）—— 不处理，只在此记录。PG 侧本就在事务内读回，不涉及。
- **判据命令（2026-09-23 现跑，队列实现后）**：
  - `git grep -c "outbox.write(" -- web/routers/chat.py web/routers/group.py web/routers/distill.py web/routers/history.py` → **6 / 5 / 1 / 1**，合 **13** 处 —— 每个「消息落库」点都从队列走，没有裸露的 `save_message` / `save_group_message`。
  - `git grep -n "nonfatal(" -- web/routers/chat.py web/routers/group.py`（2026-09-22 读数，`fc18018` 那轮）：一对一 **6** 处逐笔、群聊 **5** 处（含 `:522` 的共用块，见 121）。**121 已修后**该共用块不再存在，逐笔/共用之分不再是判据 —— 队列把「哪一条没存上」变成了 `save` 字段，见下。
  - `git grep -n "saveState\s*:" -- 'web/frontend/src' ':!*.test.*'` → **2** 处，都在 `web/frontend/src/utils/withSaveResult.js`（`:18` 代码、`:43` 代码）—— 没有任何落点手写这个字段。锁在 `withSaveResult.test.js` 的 V2（扫全部产品源码，任何第二份 `saveState:` 判断或第二份文案都判红）。
  - `git grep -n "未保存，刷新后会丢失" -- 'web/frontend/src'` → 产品代码只 **1** 处（`components/common/UnsavedHint.jsx:16`），其余命中都是测试文件与 CSS 注释。同一个 V2 锁同时扫第二份文案。
  - 端到端锁：`tests/test_message_backfill.py`（C1–C9 一对一 / G1–G2 群聊 / O6 队列不持有存储）—— 每条都有一条自身的变异能把它打红（变异脚本与逐条读数见该轮交付报告）。
- **不做的事（口径第 4 条，无行为可变异故不设锁）**：不重试、不弹窗、不汇总、不改操作入口。

**95. 适配器的 `Depends(get_jwt_secret)` 在无凭据路径照样取 secret** —— 状态：**已修**（`c417827`，2026-09-23）
- **事实**：`web/routers/auth.py` 的 `get_current_user` / `get_optional_user` 都带 `secret: str = Depends(get_jwt_secret)`。FastAPI 在**进端点函数体之前**解析整棵依赖树，`Depends` 参数的求值与「这次请求带没带凭据」**无关** —— 一次**匿名**请求也会把 secret 读一遍。`get_jwt_secret()` 未配置 / 用默认值 / 短于 32 字符时抛 `RuntimeError`。
- **后果**：一条**公开**且依赖树里挂着 `get_optional_user` 的路由（`web/routers/market.py` 的 `GET /api/market/card/{card_id}`、评论列表），在 `JWT_SECRET` 未配置时，一次本该正常的**匿名读**变成 **500**（异常从 `solve_dependencies` → `run_in_threadpool` 抛出）；secret 配好时同一请求是 **404「角色不存在」**（正常走到函数体）。即：**配置缺失把一个公开读变成了 500，而它既不是鉴权失败也不是业务失败**。
- **与缺陷 46 的关系**：46 收敛了「**显式直接读环境**」的 5 处调用点（auth.py 4 + server.py 1）。本条是同一缺陷的**另一种形态** —— 没有显式调用，是框架按依赖树代劳；46 的判据命令（`git grep` 直接读环境）**看不见它**。所以它既不在 46 的修复面里，也不在 46 的判据覆盖里。
- **与本线 `1fc61ab` 的关系**：那次只消除了「同一请求内身份判两次」（重复的 `get_user_by_id`），**没动 `Depends(get_jwt_secret)` 的急切求值时机** —— 复用入口 `resolve_request_identity` 收的是 `secret_source` 取值函数，但适配器自身这个 `Depends` 参数仍在进函数体前被求值。本条是那次改动**明确留下的残留**（合并时由用户点名登记）。
- **证据（现跑现数，2026-09-21）**：产数脚本 `scripts/probe_eager_jwt_secret.py`（已入库），三行对照 ——
  - `JWT_SECRET` 已配置：`GET /api/market/card/nope` → **404**、`GET /api/market/tags` → **200**、`GET /api/history/list` → **401**
  - `JWT_SECRET` 未配置：`GET /api/market/card/nope` → **500**、`GET /api/market/tags` → **200**、`GET /api/history/list` → **401**
  - 读法：`card/nope` 是「公开 + 依赖树里有适配器」→ 唯一随 secret 配置变色的那条；`tags` 无适配器 → 不受影响；`history/list` 受保护 → **中间件在路由前就拦下**（401），secret 根本轮不到读。三条缺一不可：只有第一条会红的话，无法排除「所有公开路由都 500」这种更宽的病灶。
- **为什么只记不修**：修它要把适配器的 `secret` 从**值**改成**取值函数**（`secret_source`），与 `storage` 一并由解析器统一控制取值时机（有凭据才取 secret、解码成功才取 storage）。那是**接口变更**：牵动四个入口的调用形态与既有 `Depends(get_jwt_secret)` 注入契约（缺陷 46 的测试正是靠 `app.dependency_overrides[get_jwt_secret]` 显式给值）。本轮是合 main 的登记动作，不在范围内。
- **修法**：新增 `jwt_secret_source()` —— 它返回 `get_jwt_secret` **取值函数本身**（不取值），交给 `resolve_identity`，secret 只在身份判定给出 MISSING 之后才取。适配器不再挂 `Depends(get_jwt_secret)`，于是「这次请求带没带凭据」重新决定「要不要读 secret」。**签发侧**（`login` / `register` / `refresh`）确实需要值，保留 `Depends(get_jwt_secret)` 不动。
- **判据命令（修后）**：`.venv/Scripts/python.exe scripts/probe_eager_jwt_secret.py` —— 两段读数必须**一致**：`card/nope` 两段都是 **404**、`tags` 都是 **200**、`history/list` 都是 **401**。**修复前**未配置段是 `card/nope -> 500`（已配置段 404），那次 500 的消失就是本条被修掉的唯一证据。（本条**没有** `git grep` 型判据：事实是行为差异，不是文本事实 —— 与 91 / 92 那两条靠 `git grep` 数命中不同，别照抄。）
- **判据 / 红源（测试侧）**：`tests/test_jwt_secret_lazy.py` —— `TestNoSecretConfigured::test_public_read_in_an_adapter_dep_tree_is_404_not_500`（核心：未配置 secret 时匿名公开读是 404 不是 500）、`::test_public_read_without_an_adapter_is_unaffected`（`tags` 恒 200）、`::test_protected_path_without_credentials_is_401`（受保护路径仍 401），`TestWithSecretConfigured` 两条守「修完没有把正常鉴权弄坏」。**另有 1 个既有的 `dependency_overrides[get_jwt_secret]` 测试文件随之改为覆盖 `jwt_secret_source`**（不新增抽象，只是把打桩点挪到新的取值入口）。

**96. `ChatEngine` 构造后由外部写入的会话状态字段（`_session_id` / `_group_id` / `_user_tz`）—— 是状态不是依赖，能否构造注入待调研** —— 状态：**已修**（Spec 96 `96-session-identity-and-timezone`，代码 2026-09-24；见文末「收口」）
- **事实（现跑）**：`ChatEngine.__init__` 只给初值 —— `self._session_id: str = ""`（`core/chat_engine.py:131`）、`self._group_id: str = ""`（`:132`）、`self._user_tz: str = ""`（`:133`）；三个字段的**实际值全部由构造之后的外部写点给出**：`web/routers/chat.py` 5 处（`_session_id` `:211` / `:308` / `:431`，`_user_tz` `:303` / `:427`）、`web/routers/history.py` 2 处（`_session_id` `:301`，`_user_tz` `:314`）、`core/group_session.py` 2 处（`_group_id` `:163` / `:352`）、`scripts/run_agent_eval.py` 3 处（`_session_id` `:177` / `:331` / `:370`）—— 合计 **12 行 / 4 文件**（测试里的同形赋值另计）。
- **与 83 的区别（这就是为什么不并入 83）**：83 修的是**依赖**（`storage`、记账身份）被实例字段传递 —— 依赖在构造那一刻**已经存在**，只是传晚了一步，所以构造注入是纯粹的改进。这三条不是依赖，是**有生命周期的会话状态**，且**在构造时刻根本还没有值**：`_session_id` 会在 auto-resume 时被换成另一个 id（`web/routers/chat.py:194-196` 把引擎整个搬到原 `session_id` 名下、`:211` 再把字段改成新值 —— **同一个引擎实例会被贴上不同的 session id**），`_user_tz` 随客户端每次请求刷新（`:303` / `:427`），`_group_id` 要到群聊装配时才定（`core/group_session.py:163`）。构造注入只是把赋值挪个地方，**不解决「同一实例跨多个 session id」这件事**。
- **与 `_unscoped` 读无耦合（现跑核实）**：引擎里那三处 `self._storage.get_session_unscoped(self._session_id)`（`core/chat_engine.py:651` / `:1017` / `:1358`）用的是字段的**值**，不是字段的**来法** —— 这三个字段将来无论改成构造注入还是保持构造后赋值，那三处的行为逐字不变。本条的处置因此**不牵动** ownership / 裁决那一族逻辑，别把它和 `_unscoped` 的归属议题绑在一起。
- **判据命令**：`git grep -n "\._session_id = \|\._group_id = \|\._user_tz = " -- web/ core/ scripts/` —— 现为 **12 行 / 4 个文件**（分布见上）。**本条没有「应为 0」这种验收值**：它是**待调研**，不是待修 —— 先要判「能不能构造注入」，再谈写点该不该清零。
- **处置方向**：先调研「引擎实例的生命周期与 session 的对应关系」—— 若一个 `ChatEngine` 实例确实只服务一个 session，则可构造注入；若确实要跨 session 复用（auto-resume 那条路已经这么用了），那这份状态本来就不该挂在实例上，得先定承载物。**不先改**。
- **移出 72 线**（2026-09-23）：本条曾被挂在 72 线名下，但 72 线第 3 步（活会话换连接，`91c0a8b` + `df01bd2`）**不碰 `ChatEngine` 字段**（spec §3.4 明确划走）—— 换连接只动 `llm` 与预算，与「这三个会话状态字段该由谁注入」无关。故本条**不再归属 72 线**，留待独立调研，编号不变、状态不变。
- **收口（2026-09-24，Spec 96）—— 调研结论：能构造注入，而且构造注入恰好消灭了本条的根因**：上面「与 83 的区别」里那句「同一个引擎实例会被贴上不同的 session id」不是**不能**构造注入的理由，反而是**病灶本身** —— auto-resume 先按新 id 造引擎、再把整个引擎搬进 `sessions[原 id]` 名下（`sessions.pop(新 id)`），于是「引擎属于哪个会话」在构造后的一小段时间里是**错的**。改成原 id 直接进构造函数后，引擎一出生就在自己的 id 名下，中间态消失，跨 id 复用这件事也就不存在了。三个字段的裁定各不相同：`_session_id` 与 `_group_id` **构造注入**；`_user_tz` **整个删除**（时区改由请求入口写 ContextVar、`UserClock` 自读，不再有「随客户端每次请求刷新」这个动作）。做法（本段 commit subject，**未合入 main 前不写 sha**）：
  - `feat(db): add users.timezone with a SQLite twin (defect 96)` —— `users.timezone` 列：PG `storage/migrations_pg/028_users_timezone.sql` + SQLite 孪生 `storage/migrations/095_users_timezone.sql`（列集闭锁逼出来的，同 commit 订正 `storage/migrations/README.md` 与 `AGENTS.md:52` 的「不为它做迁移」口径）。
  - `feat(core): resolve the request timezone from a ContextVar (defect 96)` + `feat(web): resolve the request timezone at the entry point (defect 96)` + `test(core): admit the request-timezone ContextVar to the identity lock (defect 96)` —— 时区从「实例字段」改为「请求级 ContextVar」。
  - `refactor: drop the client_tz / _user_tz plumbing (defect 96)` —— 清掉 `client_tz` / `_user_tz` 旧通道；`_user_tz` 字段至此不存在。
  - `feat(core): inject session/group identity at engine construction (defect 96)` —— `session_id` / `group_id` / `is_new_session` 成为 `ChatEngine.__init__` 的**无默认值**入参（对齐 `new_session_entry` 先例：漏传要响亮，不要静默）；5 处外部写点全部删除。
- **判据命令（收口后现跑）**：`git grep -n "\._session_id = \|\._group_id = \|\._user_tz = " -- web core scripts` → **零命中**；`git grep -n "sessions.pop(new" -- web` → **零命中**。上面「现为 12 行 / 4 个文件」及 `chat.py:211` / `:194-196` 等处行号是 **2026-09-22 的快照**，不再维护。
- **锁**：`tests/test_session_identity_injection.py` 六条（新建/重建单聊、新建/续接好感度、新建/重建群引擎）。变异读数：去掉 `session_id=` **红 2 条**；恢复 `new_id` + `sessions.pop` 改名**恰好红「重建」那条**；用「`session_id` 空否」推 `is_new_session` **恰好红「新建好感度」那条**（配对的「续接」条保持绿 —— 这正是必须让新建用例用**非空** id 的原因，否则两个判据巧合一致、变异假绿）；去掉 `/create` 处的 `group_id=` **恰好红「新建群」那条**。

**97. `Login to Aliyun CR` 无 `continue-on-error` 且排在 GHCR 构建之前 —— 阿里云链路一断，GHCR 镜像也构建不出来** —— 状态：**已修**（2026-09-22，分支 `fix/registry-fallback`，commit `5525b46`）
- **裁定（用户，2026-09-22）**：阿里云对 SZ **必需**、对 SG **只是加速**。同一条原则贯穿全程 —— **「部署用哪个镜像」只取自「实际拉取成功」的那个仓库**，不预设、不写死。据此：登录/推送的 `continue-on-error` 保留（阿里云断链不得阻塞 GHCR 产出与 digest），同时把它的挂死时长收紧。
- **已修三处**：
  - `build.yml` `Login to Aliyun CR` 加 `continue-on-error: true`，与两个推送步同一条判据。**选「加标记」而不是「把登录挪到 GHCR 那两步之后」**：登录的消费者只有紧跟其后的两个阿里云推送步，挪位会把 app 的阿里云推送从「nginx 的 GHCR 构建**之前**」变成「**之后**」—— 而 timeout 的采样正是按现有序列取的，不动序列。
  - `build.yml` `Push app image to Aliyun CR` timeout `89` → `10` 分钟。依据：近 20 次成功 build 的本步耗时 p50 = 62s、19/20 ≤ 107s（最快 55s、最慢 101s）；唯一的 1762s 是**离群值**，不作基准 —— 取 max×3 会让一个正常几十秒的**可选**步骤，每次挂死白占 runner 89 分钟（本次实发正是如此）。nginx 那步本就是 3 分钟，未动。
  - `deploy.yml`（SZ）新增**单一** `pull_image()`（`:200`）：拉一个镜像，把**实际命中的仓库**写进全局 `PULLED_REGISTRY`。四处调用点（app `:214` / nginx `:219` / postgres `:228` / fail2ban `:233`）**只导出这个结果**（`:217/:222/:231/:236`），原先无条件写死阿里云的四处 `export` 已删；`SZ_rollback`（`:266`）改走同一函数。
    - postgres / fail2ban 一并纳入（原先被写死成阿里云）：`8213668`（2026-07-01）是为「Docker Hub 从 SZ 拉超时」才改的，所以阿里云**留在首选位**；但「写死」正是本次要拆的东西 —— 拉不到就该退到 Docker Hub，两边都拉不到则显式 `exit 1`，**不继续 `compose up`**（写死一个没拉到的仓库，compose 会自己再去拉一次，失败信息埋在 compose 里）。
    - **范围**：只动 SZ 那条脚本。SG 本来就是 GHCR-only 且 tag 正确，未改。
- **两条路径的最终镜像引用（读码自证）**：
  - **阿里云拉取成功**：`:202` 命中 → `:203` `PULLED_REGISTRY="${ALIYUN_REPO_PREFIX}"` → `:217` `export APP_IMAGE_REGISTRY` → `:240` `docker compose ... up -d` → `docker-compose.prod.yml:78` `image: ${APP_IMAGE_REGISTRY:-ghcr.io/verse-shiyu}/character-distill-app:${APP_IMAGE_TAG:-latest}`，而 `:238` 把 `APP_IMAGE_TAG=${COMMIT_SHA}` —— 最终引用 = **`<ALIYUN_REGISTRY>/verse-shiyu/character-distill-app:<COMMIT_SHA>`**。nginx 同形（`:219`–`:222` → `docker-compose.prod.yml:107`）。
  - **阿里云拉取失败、只剩 GHCR**：`:202` 未命中 → `:204` 按 digest 拉 `ghcr.io/verse-shiyu/character-distill-app@${APP_DIGEST}` → `:205` `docker tag` 成 `:${COMMIT_SHA}`（digest 落地的镜像本地没有 tag，而 compose 的 `image:` 只能是 repo:tag）→ `:206` `PULLED_REGISTRY="${2%/*}"` = `ghcr.io/verse-shiyu` → `:217` export → `:240` `compose up` —— 最终引用 = **`ghcr.io/verse-shiyu/character-distill-app:<COMMIT_SHA>`**。（digest 也失败则 `:207` 退到 `ghcr.io/...:${COMMIT_SHA}`，仓库仍取 `:208` 的 `ghcr.io/verse-shiyu`。）两个仓库都失败 → `:210` `return 1` → `:216` `exit 1`，**`compose up` 不执行**。
- **验证**：actionlint（`rhysd/actionlint:latest`，对两个文件）—— **0 条 `[actionlint]` 报错**；20 条 shellcheck `SC2086:info` / `SC2129:style`，与 `main` 基线**逐条同集**（唯一差异是 `build.yml` 那条的行号因插入注释 333→347 平移；全部落在既有的 `run:` 块上，不在本次新增代码里）。**真正的拉取/部署实测不在本轮**（用户裁定：实测放在部署那一步做，先部署 SG）。
- **事实（修前形态；行号为修前的 `build.yml`）**：`build` job 步骤次序是 `Login to GHCR`（`:248`）→ `Login to Aliyun CR`（`:255`）→ `Build and push app image`（`:262`，**推 GHCR**）→ `Push app image to Aliyun CR`（`:279`）→ `Build and push nginx image`（`:289`，**推 GHCR**）→ `Push nginx image to Aliyun CR`（`:302`）。两个推送步都标着 `continue-on-error: true` 并各带 step 级 `timeout-minutes`（`89` / `3`）—— **`continue-on-error` 的全部含义就是「这步失败不算 build 失败」，即「阿里云是可选」**。可它们前面那个 `Login to Aliyun CR` **没有**这个标记：登录失败 = 本步 failure = 后续步全部 skipped，**连推 GHCR 的那两个 build/push 步一起带走**。
- **证据（run `35586099467`，2026-09-21 重跑那次）**：`Login to Aliyun CR | failure`（`read: connection reset by peer`，对 `dockerauth.cn-hangzhou.aliyuncs.com`），紧接着 `Build and push app image | skipped`、`Push app image to Aliyun CR | skipped`、`Push nginx image to Aliyun CR | skipped` —— 那次**一个镜像都没推出去**（GHCR 那两步也是 skipped），而 gate / sentinel 两条 test job 都是 success。
- **为什么这是矛盾而不是设计（要可判定的判据，不是「像不像」）**：同一份文件对「阿里云挂了会怎样」给出了两种口径 —— 推送步说「可选，失败就跳过」，登录步说「必需，失败就整条 build 停」。**两种口径都写在同一段里，而谁也没解释为什么登录比推送更必需** —— 登录只是推送的**前置**，它自己失败没有独立后果（不推就不推）。说不出「为什么」，就是遗留（§四「理由要升格成判据」）。
- **当时的待裁定（已由上面的裁定结项）**：阿里云到底是**必需**还是**可选**？—— 两者都不是单选题：**对 SZ 的部署必需、对 build 的 GHCR 产出可选**。原先的问法把两件事混成一件，所以怎么答都别扭。拆开后即得修法：**产出侧**（`build.yml`）阿里云可选，断链不许带走 GHCR；**部署侧**（`deploy.yml`）阿里云是 SZ 的首选，但用哪个仓库由**实际拉取结果**决定，而不是写死。
- **判据命令**：`git grep -n "continue-on-error\|name: Login to\|name: Build and push\|name: Push .* to Aliyun CR" .github/workflows/build.yml` —— 逐个数「这一步失败会不会带走后面所有步」。读数（2026-09-22，`5525b46` 之后）：`Login to Aliyun CR` **有**标记（不会带走）、两个 `Push * to Aliyun CR` 有标记（不会）—— **三处口径已一致**。

**98. 保存失败被「non-fatal」吞掉后 `user_rec` / `char_rec` 未绑定，同一函数后面照样解引用它 —— 保存失败被转译成 `UnboundLocalError`** —— 状态：**已修**（2026-09-22，当日记账当日修）
- **形态**：`web/routers/chat.py` 两个入口都是「`*_msg_id = None` 有初值，`*_rec`（`save_message` 的返回值）**没有初值**」，保存又都套在「non-fatal」的 `except`（只 print）里 —— **`except` 一吞，那个 `*_rec` 就是未绑定**，而函数后面照样对它 `.get()`。
- **两处的解引用点与失败形态（现跑）**：
  - `_do_chat`（非流式）：`user_rec` 绑在 `:341`（在 `if not hidden:` 内）、`char_rec` 绑在 `:348`；解引用在 `:380`（`user_rec.get("created_at", "") if not hidden else ""`）与 `:381`（`char_rec.get("created_at", "")`）。这个 dict 构造**不在任何 try 里**，异常直接冒出 `_do_chat` → 路由 `send_message`（`:607` 是裸 `return await _do_chat(...)`，无兜底）→ **500**。
  - `_do_chat_stream`（流式）：`user_rec` 绑在 `:451`、`char_rec` 在 `:500`；解引用在同一个 `done` payload 的 `:532` / `:533`。这两行**在大 try 内**（`try` 起 `:459`、`except` 在 `:549`）→ 失败形态**不是 500 而是**：正文已整段流给用户，末尾 `done` 帧却变成 `{"error": ...}`（`:564`），客户端拿不到 `user_msg_id` / `char_msg_id` / `user_created_at`；且 `:554` 的回滚条件 `user_msg_id is not None and not tokens` **两半都为假**（前者是 `None`、`tokens` 非空）→ **回滚也不触发**（库里留下助手消息、没有配对的用户消息）。
- **触发条件正是 94 描述的那个「吞」**：`save_message` 抛任何异常（PG 拒写、identity 序列落后撞主键 —— 93 号那类）都在 `:456` / `:374` / `:505` 被吞成一行 print，**执行继续往下走**，然后在上面那几行解引用未绑定的名字。
- **与缺陷 94 的关系**：同族，但**不是同一件事**，故不并入 94 作补充、另开本条。94 记的是「保存失败的**可见性**在群聊与一对一之间不一致」（吞 vs 摊给用户），判据是「这个保存点的最近一层 except 是 print 还是 yield」；本条记的是「**吞**这个动作让一个名字没被绑定、而代码后面要用它」—— 是 `except` 的**副作用**，不是可见性口径。修法也不同：94 要先定口径，本条只要给 `user_rec` / `char_rec` 一个初值（`None`）并把解引用改成 `(user_rec or {}).get(...)`（或把赋值挪到 try 之外）。
- **顺带订正 94 的一处读数**：94 把 `_do_chat_stream` 读成「三笔各自 try + print，一对一全部吞」。按本条现跑，`:532` / `:533` 的解引用**在大 try（`:459-549`）之内**，那处失败**不吞** —— 会以 error 帧摊到用户面前。即一对一**流式**这条路也有一处把异常摊给用户，94 的「一对一全部吞」在此不完全成立。
- **判据命令**：`git grep -n "user_rec\|char_rec" web/routers/chat.py` —— 数「赋值点在不在 try 内 / 解引用点在不在同一个 try 内」。读数（2026-09-22）：赋值 **4** 处（`:341` / `:348` / `:451` / `:500`）、解引用 **4** 处（`:380` / `:381` / `:532` / `:533`），其中 `:380` / `:381` 在**任何 try 之外**。
- **修法（三件，`web/routers/chat.py`）**：
  - 两个入口在 `nonfatal` 块**之前**给 `user_rec` / `char_rec` 初值 `None`（`_do_chat` `:358`–`359`、`_do_chat_stream` `:471`–`472`）—— 与既有的 `user_msg_id` / `char_msg_id` 对齐（那两个本来就有初值，`*_rec` 是同一件事的漏网）。同时给 `user_created_at` / `char_created_at` 初值 `""`。
  - 新增私有 `_msg_fields(rec)`（`:266`）：`save_message` 的返回值 → `(msg_id, created_at)`；`rec is None` → `(None, "")`。**四处取值点全部改调它**（`:368` / `:375` / `:481` / `:528`），不在各处写 `(rec or {}).get(...)` —— 判据只写一份，漏一处就是又一次本条。存在的理由是「保存**可能**失败」这件事得在代码里有个地方表达。
  - 保存失败时 `created_at` 给 `""` 而非 `None`：与 `hidden` 时原本给 `""` 同口径（对外它就是「这一条的时间戳」，没有这条消息时给空串）。原先 `:380` 的 `user_rec.get("created_at", "") if not hidden else ""` 里那个 `if not hidden` 因此变冗余（`hidden` 时 `_msg_fields(None)` 已给 `""`），删除。
- **验证（`tests/test_nonfatal.py`，9 passed）**：
  - `test_do_chat_returns_normally_when_save_fails`：**strict xfail 翻正**（原标记即「已知本条未修」）。断言接口正常返回、`reply == "回复"`、两个 id 为 `None`、两个 `created_at` 为 `""`。
  - 新增流式等价 `test_do_chat_stream_finishes_with_a_done_frame_when_save_fails`：保存失败 → **`errors == []`**、恰好一个 `done` 帧、两个 id 为 `None`、两个 `created_at` 为 `""`、正文照常流出（`"回复"`）。这条钉的正是原记账里「done 帧变 error 帧」的形态。
  - `test_do_chat_save_failure_reaches_the_panel`：**不在 spec 点名的用例里，一并翻正**。它原以 `pytest.raises(Exception)` 包住调用 —— 依赖的就是本条的 `UnboundLocalError`，修完后不再抛，不翻正必红。同因一并删掉的还有非流式入口里 `user_created_at` 上那个冗余的 `if not hidden else ""`（`hidden` 时 `_msg_fields(None)` 已给 `""`）。
  - **变异验证**：`git checkout -- web/routers/chat.py` 回退到修前形态（先备份修后文件），上述三条用例**全红**；恢复后 9 passed。
- **判据命令**：`git grep -n "user_rec\|char_rec\|_msg_fields" web/routers/chat.py` —— 数「每个赋值点是否在 `nonfatal` 之前有初值」「每个解引用点是否经 `_msg_fields`」。读数（2026-09-22 修后）：`*_rec` 初值 **4** 处（`:358`/`:359`/`:471`/`:472`）、`_msg_fields` 调用 **4** 处（`:368`/`:375`/`:481`/`:528`）、裸 `*_rec.get(` / `*_rec[` **0** 处。
- **未修（另记，只报告不改）**：流式「助手消息已存、用户消息未存」时，`_do_chat_stream` 的回滚条件 `user_msg_id is not None and not tokens` 两半仍为假（前者是 `None`、`tokens` 非空）→ 回滚不触发，库里留下无配对的助手消息。同类：非流式 `ids_to_add = [user_msg_id, char_msg_id]`（`:376`）**只在两笔都成功时才算**，若用户消息写成功、助手消息写失败，用户行已落库却不在 `session["message_ids"]` 里。两者都是「配对/一致性」问题，不是本条要修的解引用崩溃 —— 待单独立项。
- **行号漂移（2026-09-22 现跑，缺陷 93/94 那轮 `try/except + print` → `async with nonfatal(...)` 之后）**：赋值点 **4** 处仍在 —— `:342` / `:349`（`_do_chat`）、`:448` / `:495`（`_do_chat_stream`）；解引用点仍是 4 处 —— `:377` / `:378`（在任何 `try` 之外，仍 500）、`:523` / `:524`（在大 try 之内，其 `try` 起 `:454`、`except` 在 `:545`，回滚条件在 `:550`、error 帧在 `:555`）。**结论（哪几处在 try 之外）未变**，变的只是坐标。另注：吞错点的**形态**变了（不再是 `except` 里 print，而是 `async with nonfatal(...)` 吞掉），但「吞掉 ⇒ 名字未绑定」这条因果不受影响 —— 换构造没有、也不该消掉本条。

**99. PG 侧没有迁移账本 —— 每轮 init 全量重放，改约束定义只对新建库生效** —— 状态：**已修**（见本线 commit `5148ab6`，2026-09-23；原「记账（不修，2026-09-21）」。**本条只覆盖 PG 侧** —— PG 已有 applied 表；SQLite 侧没有对应机制，那一面归缺陷 90，90 已于 2026-09-24 结案为「PG 侧已修、SQLite 侧不修」）
- **与缺陷 90 同源，不重复记账**：90 记的是「两个后端都没有已应用记录表」这个共同前提（含只记不修的理由）；本条只补**本案在 PG 侧实测到的那条具体代价**，以及它在 `published_from` 一案里的适用边界。两条都留 —— 90 是前提，本条是该前提在一个改动上的后果。
- **形态（2026-09-23 前的读数 · 保留为成因证据）**：`PostgresStore._ensure_initialized` 每轮 init 走 `sorted(migrations_dir.glob("*.sql"))` 全部执行（旧坐标 `storage/postgres_store.py:94` / `:112` —— 那段循环今已拆进 `_run_migrations`），**没有** applied-migrations 表 —— 当轮 `git grep -n "schema_migrations\|applied_migrations\|migrations_applied" -- storage/ web/ core/` 零命中。幂等全靠每个文件自己手写（`ADD COLUMN IF NOT EXISTS` / `DO $$ ... EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL; END $$;`）。行号会漂，故本条下面一律用方法名指位。
- **代价（本轮实测）**：021 把 `cards_published_from_fkey` 从 `ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED` 改成 `ON DELETE SET NULL (published_from)`，在**已存在的库**上不生效 —— `DO` 块把 `duplicate_object` 吞掉，约束保持旧定义。验证必须**删库重建**才看得到新定义。**021 尚未合入 main、未上生产，本案不受影响** —— 这条代价现在只作用于「今后改**既有**约束的迁移」：那些迁移要么显式写「只对新建库生效」，要么自带 DROP + ADD。**（2026-09-23：PG 半落地后成因换了一层 —— 存量库里不再是「`DO` 块吞掉 `duplicate_object`」，而是「账本已记过账 ⇒ 整份跳过」，改文件内容只多一条 ERROR；代价与上面那条要求不变。）**
- **为什么这条要紧**：它让「迁移文件是唯一真源」在 PG 侧**不成立** —— 文件写什么不代表库里是什么，两者只能靠重建对齐。任何改**既有**表/约束的迁移，都必须显式声明「只对新建库生效 + 存量库的处置」。
- **判据命令**（读数 2026-09-23 现跑）：`git grep -n "glob(\"\*.sql\")" -- storage/postgres_store.py` → **1 处**（`:188`，落在 `_run_migrations` 内）；`git grep -n "pending_files\|file_sha256" -- storage/postgres_store.py storage/sqlite_store.py` → **只打印 `storage/postgres_store.py:4`**（import 1 + 调用 3），SQLite 零命中 —— 而 `git grep` 对零命中路径**静默不打印**且退出码 1，「SQLite 那半没做」这条正是从那个静默里读出来的，故报数时必须写明「路径没打印 ≠ 没跑」。旧判据「上一条账本 grep 应零命中」**已作废**：那是本条另一面（缺账本）的反向读数，PG 半落地后必然命中，改由 90 的判据承担。
- **修法（2026-09-23，见本线 commit `5148ab6`）**：PG 侧补 applied 表 —— `schema_migrations(filename, sha256, applied_at)`，由 `PostgresStore._run_migrations` 就地建、每份迁移一事务地记账（正文 + 记账同一事务）、已记账的文件跳过、摘要不符只记 ERROR 不重跑；共用判定在 `storage/migration_ledger.py`（纯函数、无 IO）。**细节、代价与三条锁见 90 条，此处不重复。**
- **修后仍成立的约束（本条已修，下面这条要求不因修而消失）**：账本只回答「这份文件跑过没有」，不回答「库里是不是文件里写的那个」。已记账文件内容被改（或库被账本之外的手段改回去）时，账本一律跳过、至多记一条 ERROR —— 于是「改**既有**表/约束的迁移，要么自带 `DROP` + `ADD`，要么显式声明『只对新建库生效 + 存量库处置』」这条要求**照旧成立**，反而更硬（改文件定义在新库里生效、在存量库里连提醒都只是日志）。**SQLite 侧那一面判为不修**：身份线 82 虽已合入 main（`0378d4b`），本线不为 SQLite 补账本 —— SQLite 已正式放下（`77f0179`，2026-09-24：备用后端、不测试），生产与本地开发只跑 PG，故按 90 条那条判据（主次规则）只记录、不修；90 已于 2026-09-24 结案。

**100. `publish_card` 先查后插、无唯一约束 —— 同一草稿可能并存两张发布副本** —— 状态：**已修**（`e8906f6` `0ec13ce` `8942c3f`，2026-09-21）
- **病灶**：`publish_card`（`storage/postgres_store.py:4696` / `storage/sqlite_store.py:5591`）第一步 `SELECT` 找「调用者自己的发布副本」，查不到才 `INSERT`。两步之间没有事务、也没有约束 —— PG 侧是 asyncpg autocommit，两个并发请求可以都查不到、各插一张。
- **为什么原有约束拦不住**：`cards_id_user_id_key` 是 `UNIQUE (id, user_id)`，管的是「行不重复」，管不了「同一 `published_from` 只能有一行」；`published_from` 上没有任何唯一约束。
- **后果**：两张并存后，判据对两行都成立，取哪张取决于查询顺序 —— 头像同步只落到其中一张，另一张逐渐与草稿脱节。
- **落点：把「至多一张」交给库**，不再靠调用点记得查。部分唯一索引 `cards_published_from_live_uniq ON cards(published_from) WHERE deleted_at IS NULL`（`090` / `023` 各一份）+ `publish_card` 合成**一条** `INSERT … ON CONFLICT (published_from) WHERE deleted_at IS NULL DO UPDATE … RETURNING id`（更新列清单取自草稿的**同一次读**，`EXCLUDED` 即本行 VALUES）。唯一性不看 `visibility` —— 见 88 的裁定。（索引原先与加列同写在 088 / 021、SQLite 侧另在执行器尾部补建一次；两份定义的成因与拆开见 102，编号排到回填之后见 103。）
- **红源**（`tests/test_published_copy_relation.py::TestUnpublishIsWithdrawingTheRelease` 与 PG 侧同名类，各 3 条）：其中 `test_a_second_live_copy_of_the_same_draft_is_rejected_by_the_database` 越过 store 裸插第二张存活副本，断言必须被数据库拒（判据是 `"UNIQUE"` 出现在异常里，不是随便什么错）。**刻意不走 `publish_card`**：upsert 的 `ON CONFLICT` 推断的目标就是这条索引，索引不在时 publish 自己先报「no unique or exclusion constraint matching the ON CONFLICT specification」，红在 publish 上而非本条要锁的库约束。
- **变异（实测红，验后已还原，两引擎各跑一遍）**：去掉部分唯一索引（删掉 `090` / `023` 的 CREATE 并把测试库索引 `DROP` 掉；当时那句还在 088 与执行器尾部）→ 上述那条红在 `DID NOT RAISE`。**同时另外两条也红**：同一条索引是 `ON CONFLICT` 的推断目标，索引没了 publish 直接报错 —— 实测到的耦合，不是判据不具分辨力。
- **判据命令**：`git grep -n "ON CONFLICT (published_from)" -- storage/`（两处）、`git grep -n "async def publish_card" -- storage/postgres_store.py storage/sqlite_store.py`（两处，均已是单条 upsert；`base.py` 另有一处抽象声明）

**101. 下架与「是不是发布副本」写进同一个谓词 —— 下架后再发布会并存第二张副本，旧副本仍指向草稿** —— 状态：**已修**（`2c7b815` `e8906f6` `0ec13ce` `8942c3f`，2026-09-21）
- **病灶**：`_PUBLISHED_COPY_OF` 原先的判据含 `visibility = 'public'`，把**关系**（副本属于哪张草稿）与**状态**（在不在架）压进一个谓词。作者把已发布的副本**下架**（`update_card_visibility(id, 'private')`）后再点发布，旧副本因不再 public 而查不到 → 建**第二张**副本。
- **修复前读数（SQLite 真库）**：发布 → 下架 → 再发布，两次返回的 id 不同（`c5b0e1107b3c` / `73764e5f142a`）；直读 `cards` 里 `published_from = <草稿 id>` 的行 **= 2 行**（一张 private、一张 public）。
- **裁定（本条的真正内容）：下架 = 撤回发布** —— 副本行仍在（关系不断），只是不再在架；重发**复用同一行**，内容取草稿当前值，点赞 / 他人 fork / 版本历史都留在那一行上。于是唯一性不看 `visibility`，只看「未删」。
- **落点：一条关系、一条在架，且在架由关系组合而来**。`storage/{sqlite,postgres}_store.py` 顶部：`_PUBLISHED_COPY_OF` 去掉 `visibility`；新增 `_live_published_copy_of` = `(关系) AND copy.visibility = 'public'` —— `visibility` 这个状态字面量因此只出现**一处**。调用点分派：`published_id` 三处（`get_card_unscoped` / `get_card_owned` / `list_cards`）用**在架**；头像同步两处（`save_card_avatar` 上/下）用**关系**（下架不该把草稿与副本的头像同步断开）。
- **红源**（两引擎各 3 条，同上）：`test_unpublish_leaves_no_published_id_but_keeps_the_row` 锁「下架后 `published_id` 为空（`get_card_owned` + `list_cards` 各一次）、副本行仍在」；`test_republish_reuses_the_row_and_takes_the_drafts_current_values` 锁重发语义 —— id 不变、`card_json` 取草稿当前值、发布字段更新、拨回在架、**点赞保留**（`toggle_like` 挂的）、该草稿存活副本行数 = 1、版本号 `MAX+1` 到 2。
- **变异（实测红，验后已还原，两引擎各跑一遍）**：把三处 `published_id` 的 `_live_published_copy_of` 换回关系谓词 `_published_copy_of` → 第 1 条红在 `assert '7291dc30492d' is None`（正是要钉的那句）。
- **判据命令**：`git grep -n "_PUBLISHED_COPY_OF = " -- storage/`（两处定义，两行都不含 `visibility`）、`git grep -c "_live_published_copy_of('" -- storage/`（两个 store 文件各 3 处调用）。**不要**用 `git grep -n "visibility = 'public'"`：那条会命中 market 查询等十余处正常用法，判据不可辨。

**102. 同一份索引 DDL 两个来源 —— `_apply_migration` 的「列都在即整份跳过」静默吞掉尾部语句** —— 状态：**已修**（`d14fe29`，2026-09-21）
- **形态**：`cards_published_from_live_uniq` 同时定义在 `storage/migrations/088_published_from.sql` 与 `storage/sqlite_store.py` 的 `_CARDS_LIVE_PUBLISHED_UNIQ_INDEX`（由 `_ensure_initialized` 尾部无条件执行）。前者注释自陈「因为会被跳过所以这里再补一句」—— 两处定义是**同一成因的产物**，不是两次疏忽。PG 侧只有 021 一份，无此问题。
- **成因（实测，`scripts/probe_published_index_sources.py` A 段）**：判据是 `_apply_migration` 的「脚本里每个 ADD COLUMN 的列都已存在 → 整份跳过」（`storage/sqlite_store.py` 的 `_apply_migration`）。088 正是加列那份，列一旦存在（088 跑过之后就是）整份被跳。读数：全新库决策 `APPLY 088` → 索引在；「列已存在」的库决策 `SKIP 088` → 索引**仍在**（来自执行器尾部那句）；把尾部那句钝化 → 索引**没了**（该形态下尾部是唯一来源）；自造 [ADD COLUMN + CREATE INDEX] 同文件，首次 APPLY 建了索引、列已在再跑 → 返回 OK 但索引不在（**静默吞掉**）；只含 CREATE INDEX 的文件连跑两次 = APPLY / APPLY。
- **同一处那条触发器的成因**（用户要求一并判）：**不同源、不同处置**。它不在任何 `.sql` 里（`git grep -rn TRIGGER storage/migrations storage/migrations_pg` 零命中），唯一来源就是执行器尾部那句；重建表前后各量一次 sqlite_master：索引**在**（`_rebuild_cards_published_from` 按 sqlite_master 重放）、触发器**没了**（不重放）。即它的成因是「重建丢」，必须留在 Python 侧，**不动**。
- **落点**：索引拆成不含 ADD COLUMN 的独立迁移文件 `090` / `023`，执行器的常量与那句无条件执行一并删除 —— DDL 单一来源。
- **记账（本线不改执行器，用户指定只报）**：该跳过规则**确有吞语句风险**，且吞的不只是 DDL —— 同一文件里它后面的**任何**语句都会被吞。本线只把 DDL 挪到不受该判据影响的文件，执行器语义原样保留。**遗留风险**：下次有人把必须生效的语句写在含 ADD COLUMN 的文件里，同样会静默不生效。
- **红源**（`tests/test_migration_dispatch.py::test_live_published_uniq_index_has_one_unskippable_source`）：① 定义文件不含 ADD COLUMN；② 执行器里不再有可执行的 `CREATE UNIQUE INDEX`（注释不算，用 AST 取字符串常量）；③ 索引登记在 088 之后。
- **变异（实测红，验后已还原）**：① 往 090 塞一句 ADD COLUMN → 红在「出现了 ADD COLUMN」；② 执行器里再写一份索引定义 → 红在「还有 1 处可执行的 CREATE UNIQUE INDEX」；③ 把 090 的登记挪到 088 之前（两条都仍在名单里，只换次序）→ 红在 `assert 9 > 10`。③ 第一版是直接删掉 088 那条登记，红因变成 `ValueError: … is not in list` —— 测的不是顺序断言本身，已改成只换次序重跑。
- **判据命令**：`git grep -c "CREATE UNIQUE INDEX IF NOT EXISTS cards_published_from_live_uniq" -- storage/`（现给 `090` / `023` 各 1，恰好 2）、`git grep -nE "ALTER[[:space:]]+TABLE[[:space:]]+cards[[:space:]]+ADD[[:space:]]+COLUMN" -- storage/migrations/090_published_from_live_uniq.sql storage/migrations_pg/023_published_from_live_uniq.sql`（应零命中）。**不要**用裸 `ADD COLUMN` 做这条判据：这两份文件的注释里就写着「本文件不含 ADD COLUMN」（解释成因时必然引到这个词），实测会命中注释 —— 判据必须是语句形。

**103. 唯一索引排在回填之前 —— 存量库里回填与建索引互相撞，两种顺序都让 init 失败** —— 状态：**已修**（`d14fe29`，2026-09-21）
- **形态**：索引原与加列同写在 088，而回填必须排在它前面。存量库里同一草稿可能有多张存活副本（缺陷 100 的旧语义：每发布一次新建一行），回填把它们的 `published_from` 都写成同一草稿 id → 撞唯一索引。
- **实测复现（`scripts/probe_published_index_sources.py` B 段，含两张同草稿存活副本的库）**：
  - B1 索引先建、再回填 → 建索引 `OK`（`published_from` 全为 NULL，唯一索引不互撞），回填 `IntegrityError: UNIQUE constraint failed: cards.published_from`
  - B2 回填先、再建索引 → 回填 `OK`，建索引同一个 `IntegrityError`
  - B3 回填 → 收敛（多余副本软删）→ 建索引 → **三步全 OK，存活副本 1 张**
- **结论：光换顺序不解决** —— 两种顺序都让 init 失败，只是炸在不同语句；让引脚建得上的是**收敛**。
- **落点**：顺序排成 加列(`088` / `021`) → 回填并收敛 → 建唯一索引(`090` / `023`)，SQLite 与 PG 两侧同序。索引因此与加列解耦，顺带解掉 102 的重复来源。
- **订正（2026-09-22）**：回填并收敛**没有**独立的 `089` / `022` 文件 —— 它写在加列那一份里（SQLite 088 尾部、PG 021 的同一个 DO 块），因为「只跑一次」的谓词只能是「列已存在则整份跳过」，另开文件就得引入迁移账本。逐条见缺陷 104。
- **判据命令**：`ls storage/migrations | grep published_from`（应给 `088_` `090_` 两条）、`ls storage/migrations_pg | grep published_from`（应给 `021_` `023_` 两条）

**104. 回填每次启动重跑会把合法自我 fork 改判成发布副本 —— 回填必须绑在加列那一刻** —— 状态：**已修**（`04dc4ef`，2026-09-22）
- **形态**：PG 侧没有「已应用」账本，每轮 init 全量重放每个文件（缺陷 99）；SQLite 侧 `_apply_migration` 的跳过判据是「本文件每个 ADD COLUMN 的列都已存在 → **整份**跳过」。回填若写成独立文件（原定的 `089` / `022`），这两个机制都拦不住它重跑。
- **为什么重跑会出事**：fork 路由允许 fork 自己的公开卡（`web/routers/market.py` 的 `POST /{card_id}/fork`），而 `fork_card` 只写 `forked_from`、从不碰 `published_from` —— 新代码会写出一条**同属主 `forked_from`** 的合法行。回填谓词正是「同属主」，重跑就把它改判成发布副本（语义被写坏）；同一张副本被 fork 两次再撞 023/090 的唯一索引 → 启动失败。
- **落点（不引入迁移账本、不改执行器）**：PG `021` 整份包进一个 `DO $$ IF NOT EXISTS (information_schema.columns.published_from) THEN … END $$`，块内依次 加列 → 复合 FK → 回填 → 收敛（同一事务），列已存在则整块跳过；SQLite `088` 把回填并收敛放在 `ADD COLUMN` 那句之后作尾段，与加列共用同一个跳过谓词。两侧同形；`090` / `023`（唯一索引）不变，排在其后。**没有独立的 `089` / `022` 文件。**
- **谓词**：与代码里唯一那份关系谓词同义 —— `_PUBLISHED_COPY_OF`（`storage/sqlite_store.py` 顶部，PG 侧 `storage/postgres_store.py` 同形）= `published_from = D.id AND deleted_at IS NULL`。SQL 里写不出函数，回填段写成 `c.forked_from = d.id AND c.user_id = d.user_id`（属主相同）**并带 `c.deleted_at IS NULL`**。**不看 `visibility`**（下架 = 撤回发布，副本行仍存活、仍是发布副本）。
- **偏离声明**：用户裁定原文是「不看 visibility、**不看 deleted_at**」；实现**保留了 `deleted_at IS NULL`**，取「与 `_PUBLISHED_COPY_OF` 逐字同义」这一读法（去掉它会让回填把已软删副本也认成发布副本，而代码不认 —— 两边分叉且无测试会红）。等价锁就是钉这一条的。
- **收敛**：同一草稿多张存活副本时，按 `(likes desc, 被 fork 数 desc, created_at desc, id desc)` 排名取第一张写 `published_from` 并清 `forked_from`；**落选的不动**（`forked_from` 原样，成为普通 fork）。不 DELETE、不改 visibility。
- **④⑤ 读数（存量两库，2026-09-21 22:44–22:53 只读现跑，SG + SZ 生产库）**：④ 疑似自 fork **0 行**、⑤ `user_id IS NULL` **0 行**（两地明细 SELECT 也 0 行）；⑥ 同草稿多张存活副本 **0 行**。两地基线非空（SZ 17 张卡 / 3 张有 `forked_from` / 5 条版本行；SG 34 / 4 / 6），故 0 是「真的没有」而非「库是空的」。
- **假设（两地均 0 ⇒ 不特判）**：存量两库上本段是空操作，回填与收敛都不为它们开特判分支；对其它形态的库（旧 `pg_dump` 备份恢复）本段按其谓词自行成立。**复跑查询**：`scripts/audit_published_from_prebackfill.sql`（只读，**必须在回填前**跑）。
- **锁（红源各自独立）**：`tests/test_published_from_backfill.py`（SQLite：T1 / T2 / T3 + 等价锁）与 `tests/test_postgres_store.py::TestPgPublishedFromBackfill`（PG：T1 / T2 / T3 + T4 双侧逐行比）。
  - T1 旧形态库（草稿 + 同属主副本）启动后：副本 `published_from` 指向草稿、`forked_from` 清空。
  - **T2（本案的根本命题）** 回填后新建一条「同属主自我 fork」，再次初始化：其 `forked_from` 不变、`published_from` 仍为空。
  - T3 同一草稿 3 张存活副本 → 恰一张拿到 `published_from`，另两张 `forked_from` 不变、行数不变。
  - T4 同一组夹具（id 用 prefix 定死，两侧行同名）在 SQLite 与 PG 上逐行比读数一致。
  - 等价锁：同一批旧形态数据分别问 SQL 回填谓词与 Python `_PUBLISHED_COPY_OF`，比选中行集。
- **旧形态夹具怎么造**：SQLite 侧 `ALTER TABLE cards DROP COLUMN published_from` 被表级复合外键挡住（`unknown column … in foreign key definition`）→ 只能重建表（`_revert_cards_to_pre_088`）；PG 侧直接 `DROP COLUMN`（先删 FK 与 `cards_id_user_id_key`）。行仍由 store 正常写入，只把 `forked_from` / `likes` / `visibility` 三列改回旧语义。**读数一律走裸 `sqlite3`**，不走 `SQLiteStore._connect()`（后者会先跑 init，把夹具前提和断言一起改掉 → 测试恒绿）。
- **变异（实测红，验后已还原；各条红因不同）**：① 拆成独立文件 → 只有 T2 红（`assert '' == 'card_…'`）；② SQLite 去收敛 → T3 红（`UNIQUE constraint failed: cards.published_from`）；③ 整段去掉回填 → 4 条全红；④ PG 回填脱出 `IF` → 新库上 T2 红；⑤ PG 去收敛 → T3 红（`could not create unique index … Key (published_from)=(t3_draft) is duplicated`）。**前两版不忠实已换掉**：`if False` 去掉整份跳过 → 红在别的数据迁移（`no such column: u.password_hash`）；拆文件第一版忘了把 088 截断 → 回填写两遍撞唯一索引。判据按「修复前的代码长什么样」改，不按「哪里能动」改。
- **判据命令**：`ls storage/migrations | grep published_from`（两条）、`ls storage/migrations_pg | grep published_from`（两条）；`git grep -c "rn = 1" -- storage/migrations/088_published_from.sql storage/migrations_pg/021_published_from.sql`（各 1 = 收敛只在一处）。

**105. 发布到市场失败只 `console.error` —— 对所有用户静默，403 也不例外** —— 状态：**已修**（commit `9fae212`，2026-09-22）
- **归属**：Spec 2（用户身份统一为 `users.role`）—— 游客点「确认发布」会被门禁 403，而这条路径把 403 一并吞掉，正是「拒绝文案有没有上屏」那一问的另一半。
- **形态（修复前）**：`web/frontend/src/components/CharCard.jsx:1007` 的 `catch (err) { console.error('Publish failed:', err) }`。`POST|PUT /api/market/{card_id}/publish` 的响应**从不查 `res.ok`**，只 `const data = await res.json()` 再看 `if (data.card_id)`；4xx/5xx 的正文是 `{detail}`，没有 `card_id`，于是客户端表现是「按钮从『发布中…』变回原文案、什么都没发生、也没有任何提示」。**不是游客专有**：任何用户的任何失败都走这条路。
- **发现于**：2026-09-22 补报「游客点非白名单写操作，前端是否显示拒绝文案」时顺带读出的（distill 那两条入口有 `.error-box`，这条没有）。
- **修法**：成功判据定为**响应里拿到 `card_id`**（原先只是「没抛异常」，而这条路径从不抛）—— 拿不到就 `throw new Error(data.detail || '发布失败：服务端未返回 card_id')`，与网络错误**走同一个 `catch`**，不另开分支。`catch` 把 `err.message`（即后端 `detail`）写进新增的 `publishError` 状态，由发布弹窗内的 `<ErrorBox>` 上屏；失败时弹窗**保持打开**、按钮文案**不回退成「已分享」**。修的是**所有用户**的这条路径，不是给游客单开一条。
- **回归锁**：`web/frontend/src/components/__tests__/CharCardPublishError.test.jsx`（3 条）：① `fetchWithTimeout` reject 403 → `.error-box` 含 detail、弹窗仍在、按钮仍为「分享到市场」；② 200 但正文无 `card_id` → 同样上屏（这条锁的是「成功判据是 `card_id` 而不是 HTTP 码」）；③ 有 `card_id` → 弹窗关闭、按钮变「已分享」。
- **变异（实测红，验后已还原）**：把 catch 改回 `console.error` + 去掉 `card_id` 守卫（即**修复前的代码形态**）→ ①② 同时红，③ 仍绿。
- **判据命令**：`git grep -n "Publish failed" -- web/frontend/src/components/CharCard.jsx`（**零命中**）；`git grep -n "!data.card_id" -- web/frontend/src/components/CharCard.jsx`（应给 1023 一行 —— 成功判据的唯一落点）。

**106. 取消蒸馏任务 `.catch(() => {})` —— 服务端没删成，本地列表照样把它移走** —— 状态：**已修**（commit `de49658`，2026-09-22）
- **归属**：蒸馏线。
- **形态（修复前）**：`web/frontend/src/components/DistillTaskBar.jsx:69` 的 `fetchWithTimeout('/api/distill/task/{id}', { method: 'DELETE' }).catch(() => {})`，紧接第 71 行 `removeDistillTask(task.id)` **无条件**执行 —— DELETE 失败（403 / 404 / 500）也把任务从本地列表移走，界面与后端分叉，且无任何提示。同一段逻辑在 `web/frontend/src/components/DistillWorkbench.jsx` 里**逐字重复了第二份**。
- **与缺陷 105 的差别（本条更重一档）**：105 是「失败了没人告诉用户」；本条在失败之外还**改了本地状态**，用户以为取消成功（下次拉取任务时它可能又回来，或后台仍在跑）。
- **修法**：`await` DELETE，**成功才** `removeDistillTask`；失败把 `err.message` 抛给调用方。两份逐字重复的代码收敛成 store 里一个 action `cancelDistillTask(task)`（`web/frontend/src/store/useAppStore.js:1008`，就放在 `removeDistillTask` 旁边）：`actions` 含 `cancel` 才发 DELETE，**没有 `cancel` 动作**（已终结 / 刚建还没拿到第一次响应）走纯本地移除、一个请求都不发。两个组件各自 `try/catch` 该 action 并把 detail 交给 `<ErrorBox>`；`catch(() => {})` 删除。
- **回归锁**：`web/frontend/src/store/cancelDistillTask.test.js`（3 条，锁 store 层：成功移出且请求为 `DELETE /api/distill/task/t1` / 失败上抛且任务**仍在列表** / 无 `cancel` 动作不发请求）；`web/frontend/src/components/__tests__/DistillCancelError.test.jsx`（4 条，锁两个**落点**各自绑定到了这个 action：任务条与工作台各一组「失败留任务 + `.error-box` 含 detail / 成功移出」）。**两个落点分开测是有意的** —— 只测一份，另一份以后再把 DELETE 吞掉也不会红。
- **变异（实测红，验后已还原；2026-09-22 现跑现数）**：把 store action 改回 `.catch(() => {})` + 无条件移除（即**修复前的代码形态**）→ `DistillCancelError` 4 条里红 2 条（任务条 / 工作台各自的「失败留任务」那条）、`cancelDistillTask.test.js` 3 条里红 1 条（「失败上抛且任务仍在列表」）—— 3 红 4 绿，正好是各自断言里负责失败路径的那几条；单独把工作台的 `<ErrorBox>` 摘掉（其余不动）→ `DistillCancelError` **只红 1 条**（工作台那条），另 3 条仍绿，证明两个落点各有一条独立的锁、不共用。
- **判据命令**：`git grep -n "catch(() => {})" -- web/frontend/src/components/DistillTaskBar.jsx`（**零命中**）；`git grep -n "cancelDistillTask" -- web/frontend/src/store/useAppStore.js web/frontend/src/components/DistillTaskBar.jsx web/frontend/src/components/DistillWorkbench.jsx`（三个文件各 1 处定义/绑定 —— 写法只剩一种）。

**107. LLM 全不可用时 `/api/distill/start_session` 回 500「操作失败，请稍后重试」—— 兄弟端点早就回 503 并说清原因** —— 状态：**已修**（`dbdbf9c` + `7a5992a`）
- **归属**：D 组。
- **收口**：由 72 线第 1 步的 **503 门**收口 —— `start_session` 在建会话之前统一判 `text_manager is None` → `503 请先在设置页配置 API Key`，与同文件兄弟端点同码同文案。该门同时消掉了「独立卡片分支 200 建出一个 llm=None 的死会话」这一半（见缺陷 108–109）。测试见 `tests/test_ownership_404.py::TestStartSessionApiKeyGate` 的 T7 / T8（`7a5992a`），`no_api_key` 夹具把 `deps.get_user_llm` 钉成返 `None`。
- **两条分支两种状态码（原 110 的内容，2026-09-22 并入）**：没配 key 时 `get_text_manager(llm=None)` 返 `None`（`web/deps.py:290` 的 `llm is None ⟺ None`）。**独立卡片分支**手搓的引擎拿 `llm=None` 照样 200，**建出一个聊不了的会话**；**文本分支**撞上 `NoneType` 才 500。同一个前置条件、两条分支两种码 —— 门槛提到两条分支之前后，两种码归一为 503。红源：T7 / T8。
- **形态（修前）**：`web/routers/distill.py` 的 `text_manager = get_text_manager(llm=per_user_llm)`，而 `get_text_manager` 在 `llm is None` 时**返回 None**（`web/deps.py:290`）。`start_session` 不查 None，`text_manager._build_all_characters(...)` 直接 `AttributeError`，被宽 `except Exception` 兜成 `HTTPException(500, "操作失败，请稍后重试")`。
- **实测（修前）**：2026-09-22 本机把后端起在「无任何 LLM 凭据」下（既无用户 key 也无全局 key），客户端点「+ 新建存档」→ `POST /api/distill/start_session -> 500`；服务端日志 `[deps] LLMAdapter init failed (API not configured?): missing API key …` + `[distill] Create session for card … failed: 'NoneType' object has no attribute '_build_all_characters'`。
- **为什么算缺陷而不是「配置错了活该」**：同一文件的兄弟端点（`text_manager is None` → 503「请先在设置页配置 API Key」）有正解；且本端点开场白那一段原先自己就用 `if per_user_llm is not None:` 表达过「LLM 可以缺席」。缺席是被预期的状态，只是早退那一步没跟上。**500 把「没配 key」说成「服务端故障」**：用户据此会去重试，而正确的下一步是去设置页配 key。**（该 `if per_user_llm is not None:` 已于 `926d870` 删除**：有了 503 门它恒真、else 不可达，留着只是死分支。）
- **触发面（不是「游客必然中招」）**：`deps.get_user_llm` 在用户没配 key 时**回落全局**（`web/deps.py:107` 的 `resolve_llm(config, build_user=…, get_global=get_llm)`），故配了全局 key 的生产不触发；触发条件是**两份都缺**（自托管未配、或本机开发环境）。
- **判据命令**：`git grep -n "text_manager is None" -- web/routers/distill.py` → **4 行**：`bare` 形式两处（`/run` 的 :685 与 `start_session` 的 :1354，正是本条要的两处），另两处是 `or` 组合形态（`text_manager is None or distiller is None` / `distiller is None or text_manager is None`）被同一子串顺带匹配到。判据要的是**裸形式两处**，不是子串命中数。

**114. 写失败被静默吞掉 —— 105 / 106 修好后仍在的同类落点** —— 状态：**已修**（`841b113`，2026-09-23）
- **归属**：Spec 3（紧接本份）。**说明（spec 补充 5）**：记 **114** 时，在条目里注明：「Spec 3 的 S0 普查把 105、106 两处也纳入，与其余 9 处统一迁移到同一套写失败处理机制，全仓只留一种写法。」（补充 5 原文写的是「记 108 时」—— 该编号与本条一起按 main 现最大号顺延为 114。）
- **坐标（按 spec 补充 3 #7 记录）**：`MinePage.jsx:37`（改可见性，PATCH）、`:413`（关注，POST）、`:611`（改简介）、`:963`（发动态）、`AuthorPage.jsx:197`（给帖子点赞，POST）、`MarketCardDetail.jsx:189`（给角色卡点赞，POST）、`PostCard.jsx:106`（发评论，POST）、`PrivateMessageChat.jsx:254`（撤回消息，POST）、`GroupChatPage.jsx:1358`。
- **形态**：写请求（POST/PUT/PATCH/DELETE）后面接一个静默 `catch {}`（或 `catch {} finally { … }`）—— 失败（含门禁 403）被吞掉，界面既不提示也不回滚，用户以为成功。**与 105 / 106 同形**，105 / 106 是其中被点名先修的两处。
- **复核附注（2026-09-22，现跑现数，**未改**本条目按 spec 定下的范围）**：按「写调用后 25 行内出现静默 catch」重扫 `web/frontend/src/**/*.jsx`，得 16 处原始命中。与上面 9 处逐一对：
  - **1 处复核为误判**：`GroupChatPage.jsx:1358` 是 `parseCardJson` 的 **JSON 解析兜底**，不是写调用 —— 该文件所有静默 catch 只覆盖 `GET /api/distill/cards/by-text/*` 与 `JSON.parse`，**没有写路径**。
  - **7 处同形态未在这 9 处内**：`MarketCardDetail.jsx:229`（发评论）、`:248`（使用角色 / fork）、`FeedPage.jsx:119`（给帖子点赞）、`PrivateMessageChat.jsx:276`（同意 consent 后重发）、`ChatArea.jsx:744`（给消息加表情）、`TextPanel.jsx:821`（编辑角色卡，PUT）、`BookReader.jsx:305`（阅读进度，POST —— 这一条在门禁白名单里，对游客不触发 403，但网络失败同样被吞）。另有 `PrivateMessageChat.jsx:265` 是剪贴板 `catch {}`，**不是网络写**，已排除。
  - **口径**：本条目正文仍按 spec 记录的 9 个坐标为准；上述偏差**只作复核留痕**，Spec 3 立项时按「重扫一遍再定坐标」而非照抄本条目。
- **收口（Spec 3 · S2，`841b113`）**：S0 按「写方法 + 静默 catch 出现在 25 行窗口内」重扫（80 文件 / 123 处静默或仅 console 的 catch / 49 处在窗口内），逐条读上下文定案后接线。规则（spec §2 + 补充 1 的裁定）：
  - **错误落在动作发生的那个组件**的本地 `error` + 既有 `ErrorBox`，**不借 store 的全局 `error`**（`CharPanelBody:98/:120`、`TextPanel:67` 会读它，借了就会串页）；`FeedPage` 手写的错误条换成 `ErrorBox`（全仓只留一种错误展示）。
  - **乐观更新的失败要回滚**：`HistoryPanel` 批量删除只摘掉真正删掉的 id，失败的留在列表里并报错；`PrivateMessageChat` 的表情回应失败回滚到服务端真值。
  - **弹窗自持错误**：`EditCardModal` 自己的本地 error + ErrorBox，保存失败**不关窗**（用户改的内容不能丢）；三个调用点（`TextPanel.jsx:804`、`CharCard.jsx:950`、`MarketCardDetail.jsx:1120`）原先各自的错误处理已删除，免得报两遍。
  - **两处例外**（spec §2.5：「自动发出、幂等的后台写，失败只留痕」）：`BookReader.jsx` 的翻页进度自动保存、`PrivateMessageChat.jsx` 的 markRead —— 均 `console.warn`，不出错误条。
  - 本份额外收进的两处（补充 1 #5）：`MinePage` 的取关（`:1109`）与删动态（`:1156`）原先**连 catch 都没有**，现同样接线；状态更新一律挪到 await 成功之后。
  - **例外清单之外、仍未接线的同类落点（S0 复扫发现，不在 S2 范围）**：`VoicePanel.jsx:134/216/590/607` 的注释写着「store handles」，但 `useAppStore.js` 的 `deleteCustomVoice`（`:497`）与 `deleteVoiceRef`（`:441`）**没有任何错误处理** —— 用户点删除失败是静默的；`TextPanel.jsx:700/768` 的 `startChat`（进聊天的前置写）与 `ChatArea.jsx:895/933` 的 `alert('添加失败'/'更新失败')` 同理。`HomePage.jsx:152` 的 `resumeSession` 经查**合规**（store 里 `set({ error: err.message })` 后 rethrow）。**用户裁定：除 `HomePage.jsx:152`（合规、不动）外全部并入 Spec 3 本份**，已在 S5（`015cae9`）接线；其中 VoicePanel 那四处读上下文时发现根因是**重复删除**（确认框形同虚设），另记 **124**。
- **本 commit 自身引入又在本份修掉的一处（订正 2026-09-23）**：`841b113` 把 `TextPanel.jsx` 内层组件 `CharacterManagement` 的 `console.error('Delete card failed:', err)` 改成了 `setLocalError(err.message)`，而 `localError` 只声明在外层 `TextPanel` —— 删卡失败会抛 `ReferenceError: setLocalError is not defined`（错误既不显示、还以未捕获异常丢掉）。已在 S5 `015cae9` 修（内层组件自带 `error` + `ErrorBox`），红源与变异见 **124**。
- **红源**：`web/frontend/src/components/__tests__/WriteFailureSurfacing.test.jsx`（3 条，各守一种形态）：① 非回滚落点 —— `PostCard` 发评论失败，文案可见且输入框内容不丢；② 回滚落点 —— `HistoryPanel` 批量删除部分失败，成功的走掉、失败的原位留着并报错；③ 弹窗落点 —— `EditCardModal` 的 `onSave` 抛错时错误在弹窗内、弹窗不关、按钮回到「保存」。**变异已验**：① 还原静默 `catch {}` → ①**红**；② 把「只摘真正删掉的」改回「按选中集合摘」 → ②**红**；③ 去掉弹窗内的 try/catch → ③**红**；还原后 3/3 绿。
  - **偏差（须声明）**：对账表第 2 行写的「选一个点赞类落点」，实际**全仓没有乐观更新的点赞**（点赞都用响应回填 state），故改用真正的回滚落点 `HistoryPanel` 批量删除 + `PrivateMessageChat` 表情回应。
- **判据命令（修复后）**：`git grep -nE "catch \{\}|\.catch\(\(\) => \{\}\)" --` 上列 16 个改过的组件文件 —— 剩余的每一处都要读上下文确认是**读请求 / `JSON.parse` / 剪贴板**（`PrivateMessageChat.jsx:271` 的剪贴板按本条原口径排除）；两个写路径例外就是上面点名的 `BookReader` / `PrivateMessageChat` markRead，形态是 `console.warn` 而非空块。**同上，命中数多于 0 属正常，逐条按上下文定案，不按命中数定案。**

**115. `ErrorBox` 渲染出字面 `??` 与 `?` —— 图标字形丢在了文件里** —— 状态：**已修**（`bd685e2`，2026-09-22）
- **归属**：前端通用组件（不分线）。
- **形态（修复前）**：`web/frontend/src/components/common/ErrorBox.jsx:6` 是 `<span style={{ flex: 1 }}>?? {message}</span>`，第 19 行关闭按钮的内容是一个 `?`。按 `ErrorBox` 的用途（错误提示条）推断，原意应是 ⚠️ 与 ✕，写进文件时丢成了 `?`。**是文件里的字面 `?`，不是终端编码或字体显示问题**（读源码即可见）。
- **根因（2026-09-22 查清）**：字形**从未进过仓库**。`git log --follow` 显示该文件**只有过一次提交** `ae47a216`，那一次的 blob 里就已经是 `??`；`LC_ALL=C` 数非 ASCII 字符得 **0**（全文件纯 ASCII），而同目录 / 同批的组件里 ⚠️ / ✕ / 中文都在。即：字符是在**写文件的那一刻**被替换掉的（ASCII 化），不是入库后编码损坏、也不是终端字体显示不出。**所以「找回」找回不了 —— 没有可找回的原物，只能重画**。
- **影响面**：所有用 `ErrorBox` 的界面都会带上这个前缀与关闭按钮，**本份新做的 105 / 106 两处上屏也在这条路径上**（拒绝文案本身完整，前缀是多余的 `??`）。功能不受影响 —— `.error-box` 与文案都在 —— 属观感 / 可读性缺陷。
- **修法（已做，`bd685e2`）**：改用本仓自绘的图标集 `web/frontend/src/components/common/Icon.jsx` —— 前缀 `<AlertTriangle size={14} />`、关闭按钮 `<Close size={12} />`，并给按钮补 `aria-label="关闭"`（SVG 自身没有可读名称，只画不给名等于把按钮变成哑的）。**选 SVG 而不是补一个真字形字符，是冲根因去的**：画出来的图形是代码，不是码位，同一种「作者端 ASCII 化」的丢失不可能重演。
- **回归锁**：`web/frontend/src/components/__tests__/ErrorBox.test.jsx`（3 条）。断言写成**渲染出的文案不含单个 `?`** 而非只查 `??` —— 变异只把一处换回 `?` 时，只查 `??` 的断言不会红（实测：还原缺陷原形 M1 → 3/3 红；只把前缀换成单个 `?` 的 M2 → 相关 2 条红）。
- **判据命令（修复后）**：`git grep -c '?' -- web/frontend/src/components/common/ErrorBox.jsx`（应给 **0** —— 全文件不含字面 `?`）；`git grep -n 'AlertTriangle\|Close size' -- web/frontend/src/components/common/ErrorBox.jsx`（应给 3 行）。修复前那条 `git grep -n '?? {message}' …（应给 1 行）` 已失效（现给 0 行），此处替换而非并列保留。
- **改前核过的耦合**：11 个调用方都不依赖这个前缀；两处既有断言（`CharCardPublishError.test.jsx`、`DistillCancelError.test.jsx`）用的是 `textContent` + `toContain('…detail…')`，前缀无关，未受影响。

**116. `DistillWorkbench` 拉卡片的 effect 自激 —— text 列表为空时无限发请求** —— 状态：**已修**（`4950936`，2026-09-23）
- **归属**：**Spec 3**（2026-09-22 改判 —— 原记「蒸馏线」，用户把它划给 Spec 3 承接）。
- **形态**：`web/frontend/src/components/DistillWorkbench.jsx:110` 的 effect 依赖 `[texts, loadTexts]`，函数体第 91 行是 `if (texts.length === 0) { loadTexts(); return }`。而 `web/frontend/src/store/useAppStore.js:656` 的 `loadTexts` 每次都执行 `set({ texts: data })`（第 661 行）—— **不论 `data` 是不是空数组都换一个新引用**，`texts` 的引用必然变化 → effect 重跑 → 仍然是空 → 再调 `loadTexts()`。闭环成立，**与网络是否失败无关**，触发条件只是「文本列表为空」。
- **实测**：2026-09-22 的 S4 浏览器点检与写 106 的组件测试时都撞到。vitest 侧的表现是该用例文件**挂住不退出**（需手动 kill；那次留下过 3 个挂死的 vitest 进程，清掉后全量才恢复正常）。测试侧的规避写法是别给空列表 —— `web/frontend/src/components/__tests__/DistillCancelError.test.jsx` 的 `beforeEach` 就为此塞了 `texts: [{ id: 'x1', filename: 'a.txt' }]`。
- **为什么算缺陷**：空文本列表是**正常状态**（新注册账号、清空之后），不该引发请求风暴；而且触发条件是「数据为空」不是「出错」，用户看不到任何提示，只会觉得页面卡。
- **落点比记账时多一处（Spec 3 · S0 重扫）**：同一段代码在 **两个**地方各有一份 —— `DistillWorkbench.jsx` 与 `TextPanel.jsx` 的 `CharacterManagement` 子组件。记账时只点了前者。
- **修法（Spec 3 · S3，`4950936`）**：按本条留的方向走「挪到挂载时只做一次」，两处同修 ——
  - 文本列表改成**挂载时拉一次**（`DistillWorkbench` 新加 `useEffect(() => { if (texts.length === 0) loadTexts() }, [])`；`TextPanel` 的父组件本来就有这一个，只是删掉了子组件里那次），卡片 effect 只依赖 `[texts]`，**不再调 `loadTexts`**。
  - 两份逐字重复的「逐文本 + 独立卡片」聚合抽到 `web/frontend/src/api/cards.js` 的 `fetchAllCards(texts)`（`_textInfo` / `_source` 只在那里挂一次）；端点字符串也收进该模块，组件不再手写 URL。
  - **没有**按本条「要避免」的那条走：不加长度判断绕，也没给 `loadTexts` 加重入门（`loadTexts` 的语义不变，其它调用点不受影响）。
- **连带影响（必须说清）**：修前「文本列表为空」会在卡片 effect 里**早返回**，于是**独立卡片（`/api/distill/cards/standalone`）根本没被拉** —— 只有市场卡、没有上传过文本的用户，角色管理页与工作台的「已验收」区是**空的**。修后独立卡片照常返回，这类用户第一次看到自己的卡。**这是行为变化，不是纯内部重构。**
- **红源**：`web/frontend/src/components/__tests__/DistillCancelError.test.jsx` 新增 3 条 —— ① 蒸馏工作台、② 创作页角色管理，在 `texts: []` 下断言 `/api/text/list` **恰好被调一次**（原先该文件的 `beforeEach` 是为了躲这个 bug 才硬塞了一条假文本，现已改回空数组并注明）；③ `fetchAllCards([])` 直接返回独立卡片（连带项的单元锁）。**变异已验**：把 `loadTexts()` 放回卡片 effect → ① ② **红**（worker 在无界请求循环里被拖死，不是断言失败）；在 `fetchAllCards` 里加回 `if (texts.length === 0) return []` → ③**红**；还原后 7/7 绿。
- **判据命令（修复后）**：`git grep -n "cards/by-text/" -- web/frontend/src/components`（应给 **0 行** —— 端点字符串只剩 `web/frontend/src/api/cards.js` 一处与 `store/useAppStore.js` 三处）；`git grep -n "loadTexts()" -- web/frontend/src/components/DistillWorkbench.jsx web/frontend/src/components/TextPanel.jsx`（应**各给 1 行**：`DistillWorkbench.jsx:91` 与 `TextPanel.jsx:106`，且两处都在**依赖为空数组的挂载效应**里——`DistillWorkbench.jsx:92` / `TextPanel.jsx:107` 的 `}, [])` 是行内第二段证据；修复前两处的调用点在**依赖含 `texts` 的卡片效应**里）。只数 `loadTexts` 子串不行：注释里也提到它，命中数会虚高。

**117. main 的 CI 在 `Run tests` 这一步挂死（不是失败，是挂着不动）—— 已发生四次** —— 状态：**已修**（`70cdba1` + `23ac407`，2026-09-23）
- **归属**：CI / 测试基础设施（不分线）。
- **根因（2026-09-23 定位并修复）**：两个成因叠加，缺一不成——
  - **① 库有了两个来源（`dbdbf9c` 引入）**。那个提交把独立卡片分支的 `ChatEngine` 指到 `TextManager` **构造时捕获**的那份库上，而那份是 `deps.get_storage()` —— **进程级单例**（`web/deps.py:130`）。**测试换库走的是 `app.dependency_overrides[get_storage]`，只覆盖 `Depends(get_storage)` 那条路**，`TextManager` / `Distiller` 内部都是直接调 `get_storage()`，override 管不到。于是同一场测试里两条路读**两个库**：请求路径读 sqlite（被测的那份），引擎攥着的是**真库**（CI 里是 postgres）。
  - **② 跨事件循环的报错被静默吞掉（见新条目 123）**。真库那份在 `TestClient` 下还叠了一层：没有 app lifespan ⇒ `core.scheduling` 的 loop submitter 从未注册 ⇒ `submit_to_main_loop(..., wait=True)` 走回退 `asyncio.run(coro)` **新建一个 loop**，而全局 `PostgresStore` 的连接池绑在另一个 loop 上 ⇒ asyncpg 抛 `got Future <Future pending> attached to a different loop`。SQL 已经写进 socket，服务端那笔事务留着不结算，连接既回不了池也关不掉（`Release connection failed: cannot perform operation: another operation is in progress`）。这条异常被 `core/chat_engine.py:637/655` 的宽 `except Exception` 当「非致命」打一行日志 —— **用例照样 PASSED，没有任何东西变红**。
  - **挂死的链条**：那笔没结算的事务持 `cards` 上的 `AccessShareLock`（`migrations_pg/007_card_sync.sql` 的 `ALTER TABLE cards ADD COLUMN` 要 `AccessExclusive`，两者冲突）→ 下一个建 store 的用例（`tests/test_pg_identity_sync.py::test_store_startup_runs_the_alignment`）经 `_ensure_initialized` 重放全部迁移 → **永远等下去**。挂点因此落在那条用例上，但**它只是受害者不是元凶**（与本条下面「二分定位」的弱推论一致：它及其被测代码在区间内零改动）。
  - **为什么本地绿、CI 挂**：本地 `.env` 是 `STORAGE_BACKEND=sqlite`，两个来源**指向同一个文件**，分叉看不出来；CI 是 postgres，「请求路径 sqlite vs 引擎真 PG」才显形。**差异确实在环境，但根因在代码** —— 本条下面那句「差异不在代码而在环境」是当时的判断，现按实测订正。
  - **生产侧的同一处（`storage/postgres_store.py` 的 `_PoolContext.__aexit__`）**：那条卡住的连接 `pool.release()` 会抛，而原先的实现**只打一行 `Release connection failed` 就放手**（`self.conn = None`），槽位从此不回收 —— 池被一格一格吃光，之后**所有**请求一起挂住。这是「连接卡住」这条路上真正的生产风险，与测试能不能跑无关。修法与红源另见下面「同一处的生产修复」。
- **生产未受影响（用户要求查清，2026-09-23 现读代码）**：`get_storage()` 返回的就是 `deps._storage` 这个模块级单例，由 `get_store()` 在 `web/deps.py:130-135` 建一次、此后一直缓存；生产是单进程单 loop，且 `set_main_loop(loop)` 在 lifespan 里跑（`web/server.py:118-119`）。**修复前 TextManager 捕获的那份与 `Depends(get_storage)` 返的是同一个对象** —— 生产只有**一个**来源，也不存在跨 loop。分叉只在「一处换、一处不换」的消费者上发生，实际就是测试。
- **修复（单一来源，`70cdba1`）**：`TextManager` 不在构造时捕获 store，改收 `get_storage` 这个**取库方式**，用到处现取（`_storage` 成了只读 property，不缓存）；装配点 `web/deps.py::_assemble_text_manager` 传**函数**不传返回值。**不给 `_create_session` 加 `storage` 参数**（那是把两个来源写进签名，正好是要消除的东西）。换 `deps._storage` 一处，两条路同时覆盖。
- **改测试的一处（超出字面指令，须声明）**：`tests/test_ownership_404.py` 的 autouse 夹具 `_no_ambient_state` 增加 `monkeypatch.setattr(deps, "_storage", store)`。**这不是随手加的**：只做 TextManager 侧的修复、不动这个夹具，在复现环境里**照样挂死**（`e6b05e8` + 仅 TextManager 改动 → `test_store_startup_runs_the_alignment` 被 `timeout 900` 杀掉，exit 124）—— 因为这个文件里的用例仍只 override `Depends` 那条路，引擎照样拿到真库。两处**同轮**才闭环。
- **同一处的生产修复（`postgres_store.py` 的 `_PoolContext.__aexit__`）**：归还失败时改为 `self.conn.terminate()` —— asyncpg 侧那是「不等收尾、直接断连」的原语，且它会走 `_release_on_close()` 把 holder 交还池，故代价是**重建一条连接**，不是**丢一个槽位**。红源 `tests/test_postgres_store.py::TestReleaseFailureDoesNotLeakTheSlot`（3 条，全桩不碰 PG，故在没跑 PG 的机器上也真跑）：① 归还失败必须 terminate；② 正控 —— 归还成功**不得**断连（否则每个请求都在重建连接）；③ 归还失败不许顶替调用方真正的异常（原有语义）。**变异已验**：删掉 `terminate()` → ①**红**、②③仍绿。
- **红源**：`tests/test_ownership_404.py::TestStorageSingleSource` 两条 —— ① `test_text_manager_resolves_the_store_at_use_time`（装配后换单例，用的时候必须取到新的那份）；② `test_independent_card_session_binds_the_single_source`（端到端：独立卡片建出的引擎必须绑本次用例那份库）。**变异已验**：`__init__` 改回构造时捕获 → ①**红**；② 仍绿（引擎的库在引擎构造时就定了，这条不具分辨力，**如实记**）。
- **证伪（用户指定的前置实验，验后已撤）**：把独立卡片会话的 storage 换回注入的那份 → `e6b05e8 + falsify.patch` 在复现环境跑 `tests/test_ownership_404.py tests/test_pg_identity_sync.py` **56 passed / 165s**，那条用例 `PASSED [100%]`，挂起消失。
- **四次挂起的 sha 与时间**（落笔时仍未定位，现已定：见上「根因」）：
  - **第一次**：main @ **`842f3e0`**，run [`35703112463`](https://github.com/VERSE-SHIYU/Character-distill/actions/runs/35703112463)，**2026-09-22T08:07:36Z** 触发。`gate` 的 `Run tests` 自 `08:08:48Z` 起挂住，`gate` / `sentinel` 各占 runner **8360s / 8362s**（≈ 2h19m），最终**由人手工 cancel 收场**（conclusion = `cancelled`）。
  - **第二次**：main @ **`da497b1`**，run **`35716206496`**，**2026-09-22T10:28:52Z** 触发（**紧接在第一次被 cancel 之后**，不是同日稍后的偶发）。`gate` 的 `Run tests` 自 **`10:30:08Z`**、`sentinel` 自 **`10:30:01Z`** 起一直是 `in_progress`，**到落笔时已 >1h17m 仍无终态**；两个 job 的后续步骤（`红的时候说清楚…` / `upload-artifact` / `Stop containers`）全部 `pending`。
  - **第三次**：main @ **`a19058d`**，run **`35747175886`**，**2026-09-22T15:24:58Z** 触发，`Run tests` 自 **`15:26:06Z`** 起挂，**16:05:19Z** 收场。
  - **第四次**：main @ **`9aba202`**，run **`35747536487`**，**2026-09-22T15:27:57Z** 触发，`Run tests` 自 **`15:29:04Z`** 起挂，**16:08:21Z** 收场。
  - **第三、四次是怎么收场的**：**不是人工 cancel，是被 `timeout-minutes: 40`（`776c733`）掐掉的** —— 两次 `Run tests` 各活 **39m10s**，conclusion 记 **`cancelled`**（不是 `failure`）。即**护栏按设计生效**：挂死不再烧满 runner。这一点还**反证了那是真挂起而非「跑得慢」** —— 若只是慢，40 分钟里进度会往前走、也不会两次都精确停在同一个时长上。
- **形态**：四次都是**两个 job 同时挂住、停在同一步骤 `Run tests`**。**挂死不等于失败** —— pytest 进程一直活着，只是不往前走了，所以既不会自己报红、也不会自己退出；**唯一能让它收场的是人工 cancel**（第一次就是这么结束的）。这也正是它能挡住镜像构建的原因：`build` job `needs` 这两个 job，挂住 = 永远不开始。
- **四次之间没有共同改动**：`842f3e0` → `da497b1` 隔了 7 个提交（后端 + 测试 + 台账），**共同项只剩「main 的 CI 环境」本身**；第 3、4 次同理（`a19058d` 与 `9aba202` 相隔 3 分钟、内容几乎同一批）。这把归因从「某个 commit 引入」推向「环境 / 测试基础设施」，但**仍未定位** —— 后来定位到的根因（单一来源被破坏 + 跨 loop 异常被吞）恰好落在**长期存在**的代码里，与「区间内某个 commit」无关，故此处当时的推论方向是对的。
- **停在哪（第一次）**：最后一条**带终态百分比**的输出落在 **54%**，位置在 `tests/test_pg_identity_sync.py` 附近；取消时 runner 仍在收拾 pytest（`Terminate orphan process: pid (2935) (pytest)`）。**读挂点只认带 `[ NN%]` 的行**：pytest 用 `-v` 时在 `logstart` 就写节点 id 但**不带换行**，重定向日志的最后一行是「写了一半的行」，它既不是挂点也不能当进度 —— 拿它定位会指向一个其实已经跑过去的用例（本条的第一次误判就是这么来的）。第二次的 `Run tests` 日志**在步骤结束前取不到**，所以第二次连「停在哪」都不知道。
- **本地不可复现（2026-09-22 当天现跑）**：进程内单跑那条最近的用例，带 `.env` / 不带 `.env` 分别 2.04s / 0.37s，**都是绿的**；按 CI 口径整跑全量得 **1685 passed / 3 failed / 997s（0:16:36）**，**没有挂住**。即：同一天、同一个 commit、同一套依赖，本地走完、CI 停死，**差异不在代码而在环境**。
- **当时的嫌疑（已排除）**：**112 那一族**（全量里约 20 条 `PytestUnhandledThreadExceptionWarning`、aiosqlite `Event loop is closed`，条数与命中用例每次不同）。当时的理由是没有证据把 117 钉到 112 上（117 是「停住」，112 是「抛异常后继续」）。**2026-09-23 定位后确认：确实不是 112 —— 112 是 aiosqlite 侧，本条是 asyncpg 侧；两者都源于「跨 loop」，但不是同一处代码。**
- **二分定位（2026-09-22，只报告不修）**：按「main 上最后一次 `Run tests` 成功」到「第一次挂起」取区间。
  - 最后一次**成功**的 run = `35690413574`，sha **`e1cd661`**（2026-09-22T05:21:55Z 触发，`gate` 的 `Run tests` 263s `success`）；第一次**挂起**的 run = `35703112463`，sha **`842f3e0`**（08:07:36Z 触发）。两者**相邻**，中间没有别的 main run。
  - 区间内 `e1cd661..842f3e0` 共 **11 个提交**，改了 **10 个文件**：`AGENTS.md`、`core/group_session.py`、`core/text_manager.py`、`web/routers/{chat,distill,group,history}.py`、`tests/test_create_session_kwonly_lock.py`、`tests/test_llm_access_gate.py`、`tests/test_ownership_404.py`。
  - **重点核查结论**：`tests/test_pg_identity_sync.py` **在区间内零改动**；它的被测代码也**零改动** —— 整个 `storage/` 目录在区间内**一个文件都没动**（`git diff --stat e1cd661 842f3e0 -- storage/` 为空；`storage/postgres_store.py` 与 `storage/pg_identity_sync.py` 最后一次改动都是 `100fae67`，**在 `e1cd661` 之前**）。`tests/` 目录也没有增删文件（两端各 158 个）。
  - **推论（弱）**：既然那条用例及其被测代码在区间内根本没动，「停在 54% / 近 `test_pg_identity_sync.py`」**大概率是上面那条「未刷新的半行」读法造成的假信号，不是真定位**；本条不把它当作指向该用例的证据。区间内真正可能碰并发的改动只有 `core/group_session.py`（新增 keyword-only 的 `user_id`）、`core/text_manager.py`（删掉 `_create_session` 的死参数 `text`）与 `web/routers/chat.py` 的 `_ensure_session` 属主收紧（`session.get("user_id") != user_id`，未登记即 404）—— **这三处是「嫌疑范围」不是「已定位」，没有任何证据把它们钉上。**
- **验收对照（2026-09-23，现跑现数）**：修复后的 run [`35750857228`](https://github.com/VERSE-SHIYU/Character-distill/actions/runs/35750857228) @ **`a73b476`**（= `9aba202` + 两项修复）**全绿**：`gate` 的 `Run tests` **284s**、`sentinel` **249s**，均落在历史 p50（235s）附近；`build` 成功，app + nginx 两个镜像已推 GHCR 与阿里云 CR。**同一条 main 线上「改前挂、改后 284s」，是干净的对照**（且这一次用的是与挂起时同一套 CI 环境，故能排除「环境变了」）。
- **防护（已加，`776c733`）**：`timeout-minutes: 40` 加到 `gate` 与 `sentinel` 两个 job 上 —— 到点即收场（第 3、4 次就是这么结束的，conclusion 记 `cancelled`），「挂住」从此不再白占 runner 到撞 360 分钟上限（同形先例：`docker push` 那次挂死 2h40m）。**这个防护只让挂死可见，不消除挂死** —— 挂死本身由 `70cdba1` 消除（根因见上）。防护保留：它是「再挂一次也落进红」的底线，与本条是否是根因无关。
- **统计 p50 / max 的原样命令**（取值依据 = 最近 **46 次**成功 run 的本步耗时：p50 = **235s**、最大 = **714s**，其余落在 169–321s；**40 分钟 = p50 的 10.2 倍、最大值的 3.4 倍**）：
  ```
  gh run list --workflow=build.yml --branch=main --limit 25 --json databaseId,conclusion,createdAt
  gh run view <run-id> --json jobs
  ```
  第二条取每个 job 的 `steps[]` 里 `name == "Run tests"` 的 `startedAt` / `completedAt` 相减；`completedAt` 为 `0001-01-01T00:00:00Z` 的表示该步没跑完（**这次统计就靠这条把两个挂起 run 摘出去的**）。**只点名命令、不入库数据文件** —— 样本随时可用上面两条重取。
- **判据命令**：`git grep -n 'timeout-minutes: 40' -- .github/workflows/build.yml`（应给 2 行，分别在 `test` 与 `upstream-drift` job 上）。复现证据走 CI 侧：`gh run view 35703112463`（第一次）、`gh run view 35716206496`（第二次）、`gh run view 35747175886`（第三次）、`gh run view 35747536487`（第四次）、`gh run view 35750857228`（修复后的对照，全绿）—— 本仓 CI 不出产物，**run id 就是证据坐标**。

**123. `chat_engine.py:637/655` 把「不同事件循环」的报错当非致命吞掉 —— 故障被静默掩盖** —— 状态：**已修**（2026-09-24，Spec 123：`1e6be37` 生产侧 + `33984c4` / `12268e0` 测试侧；`eee0fa1` 补一条过期 docstring）
- **归属**：D 组。
- **坐标**：`core/chat_engine.py:637`（`except Exception as exc: print(f"[Affinity] Fetch reactions failed (non-fatal): {exc}")`）与 `:655`（`except Exception: pass`，整条 `pass`）。
- **形态**：两处都是**宽 `except Exception` + 只打日志 / 什么都不做**。这正是 117 那两个成因里 ② 的落点：`submit_to_main_loop` 回退 `asyncio.run` 建新 loop、asyncpg 报 `got Future <Future pending> attached to a different loop`，异常被这两处吃掉，**调用方看不到、用例照样 PASSED**。
- **危害（比「少取了一次点赞」重得多）**：吞掉的不是「这次数据没拿到」，是「**这个库现在状态不对**」。SQL 已写进 socket、服务端那笔事务没人结算、连接既回不了池也关不掉 —— 这是个**会持续恶化**的故障（后续任何用该库的代码都受影响，117 就是这么挂住的），却被降级成一行 `non-fatal` 日志。**「非致命」这个判断在跨 loop 这类错误上不成立**：真正的可忽略错误是「这次读没读到」，跨 loop 是「连接坏了」。
- **为什么记而不修**：修法不是把这两处收窄（那是治症状）—— 要拿掉的是**回退到新建 loop** 这条路本身（`core/scheduling.py` 在没有注册 submitter 时 `asyncio.run`），而它服务于 `TestClient` 无 lifespan 这一整套测试装配。改动面超出 117 的修复范围，117 已用「让库只有一个来源」把这条路径从生产测试里**绕开**（不再有跨 loop 的真库调用）。**收窄 `except` 单独做没有意义**：不新建 loop 了，这两处自然不再撞；新建 loop，收窄了也只是把静默挂死换成显式报错 —— 那是**另一件事**（「失败可见」），要单独立项。
- **与 112 的分工**：112 是 aiosqlite 侧的同类形态（`Event loop is closed`，抛了但继续），本条是 asyncpg 侧（被吞掉）。**同源不同落点**，两条都还在。
- **判据命令**：`git grep -n "non-fatal" -- core/chat_engine.py`（应给 2 行 —— `:638` 的 `[Affinity] Fetch reactions failed` 与另一条 save state 的；本条的 `:655` 那处是裸 `pass`，不带这个串，须按行号读）。收窄后本条应改为**在跨 loop 错误上显式抛 / 记成 ERROR**，判据也随之改成「跨 loop 的异常不再走这两条路」。
- **收口（2026-09-24，Spec 123）**：修法与上面「为什么记而不修」预告的路径完全一致 —— 拿掉的是**回退到新建 loop** 这条路本身，不是把这两处 `except` 收窄。`core/scheduling.py::submit_to_main_loop` 未注册时抛 `RuntimeError`（`1e6be37`：删掉 `asyncio.run` / `create_task` 两条分支与那条 `warnings.warn`）；`web/deps.py::_submit_to_main_loop` 在主 loop 线程上 `wait=True` 时于**投递之前**抛 `RuntimeError`（同 commit，治的是 `chat.py:221` / `history.py:313` 那两处「主 loop 等自己」卡满 15 秒、「保存静默不生效」）；这两处路由改走 `await asyncio.to_thread(...)`。测试侧不再借退路过关：引擎单测的存储改 `None` / `AsyncMock` + 显式注册测试投递实现（`33984c4`），进程级注册收敛到一个会还原的入口（`12268e0`）。
- **这两处 `except` 按原裁定不动**（`core/chat_engine.py` 的 `_save_affinity_state` 与 `fetch reactions` 两条）。理由不是「改不动了」，而是**本条的成立条件已经不存在**：跨 loop 报错唯一的产生机制是「回退新建一个 loop」，该机制已删；此后能流经这两处的，只剩「这一次读/写没成功」这类真的可以非致命的错误。
- **判据命令订正**（原判据已随 119 的 `print` → `logging` 落地而失效：那两处现在是 `logger.warning(..., exc_info=True)`，不再是 `print`）：
  - `git grep -nE "asyncio\.run\(coro\)|\.create_task\(coro\)" -- core/scheduling.py` → **0**（本条的红源：退路一旦被恢复，这条路就会重新在本线程造 loop）。**判据只认调用形态，不认裸标识符**：`asyncio.run` / `create_task` 这两个名字仍在该模块 docstring 里各出现一次（描述**被删掉的**旧退路），按名字查会命中注释而与代码现状无关 —— 与本仓「历史注释要描述被删机制、判据 grep 覆盖注释」那条同形；
  - `git grep -n "No loop submitter" -- core` → **0**（退路那条 `warnings.warn` 的文案已随 `1e6be37` 删除；全仓仅 `AGENTS.md` 与本 spec 引述它）；
  - `git grep -c "主 loop 线程上不得阻塞等主 loop" -- web/deps.py` → **1**。
- **分支 CI 对照（现跑，2026-09-24）**：基线 run `35941780534`（`990b7dcc`）→ 本分支 run `35993735059`（`12268e0`），同一 job 口径：`No loop submitter` **每 job 1 → 0**（退路的 `warnings.warn` 随分支删除而消失）、`never awaited` **每 job 8 → 4**。两个 job 都绿。

**118. `importlib.reload` 替换异常类对象 —— 按「retry → error → chat」的文件顺序跑，三个文件红 4 条** —— 状态：**已修**（2026-09-22，`6c8d805`，分支 `worktree-session-cred`）
- **归属**：72 线（94 泄漏那半的收尾）。
- **形态**：`tests/test_llm_adapter_retry.py::test_timeout_family_constants_consume_env` 用 `importlib.reload(adapters.llm_adapter)` 读「六个超时常量确由 env 派生」，以证明「改 env 真的改行为」。`reload` 把模块属性**整体换新**，`IncompleteResponseError` 等异常**类对象**也跟着换了一份新类 —— 别的测试文件在**收集期** import 到的是旧类，此后它的 `pytest.raises(旧类)` 抓不到新抛的类。
- **引入**：`3d1d171`（该用例本身）。
- **触发面**：只有文件顺序落到「retry → error → chat」才显形（pytest-randomly 打乱时常绿），故长期未被发现。现跑：`pytest -p no:randomly tests/test_llm_adapter_retry.py tests/test_error_user_facing.py tests/test_chat_stream_error.py` 红 **4** 条 —— `test_error_user_facing.py::test_known_failures_screen_exact_text_and_no_internals` / `::test_known_failures_keep_ops_detail_out_of_screen`、`test_chat_stream_error.py::test_incomplete_payload_is_identifiable` / `::test_boundary_maps_known_failure_and_rejects_others`。
- **修法**：不再 reload `sys.modules` 里那一份。改为 `importlib.util.spec_from_file_location` 以**另一个模块名**（`_llm_adapter_env_probe`）加载一份**私有副本**读常量；副本**不写进 `sys.modules`**（注册了等于又替了一份全局单例，别处再 `import adapters.llm_adapter` 会拿到带 env 的版本）。测试命题（常量确由 env 派生）不变。
- **变异（现跑，2026-09-22）**：把 `_load_env_probe()` 改回 `return importlib.reload(M)` → 上面那条三文件命令**红 4 条**，与修前逐条同集；还原后 28 passed。
- **判据命令**：`git grep -n "importlib.reload" tests/test_llm_adapter_retry.py` → **0**（本条没有「应为 0」以外的验收值）。

**119. 全站没有「`print` 型失败」的告警 —— 失败只到容器 stdout，owner 不翻 `docker logs` 就无从得知** —— 状态：**已修**（2026-09-24 首段；rebase 到分叉点 `9bb2d97` 后，修法 = `e4c6c5cb` + `222ff7ed` + `6ece957e` + `7b056113` + `a8a07557`，约定与登记 = `c817c647`，判据与产数 = `f8dc09da` + `fee547b2`，rebase 收尾 = `e6e73463`；**2026-09-25 残留结案** = `17db7e4` + `597aa1f` + `f99dad6` + `7367b9a`）
- **归属**：身份线（第 3 步的失败日志正好落在这一格的盲区里，顺手记账；与蒸馏线在 `core/distiller.py` / `web/routers/distill.py` 上共线，那两处的逻辑归蒸馏线）。
- **形态**：`core/alerting.AlertHandler`（`3fb3dbf`）挂的是 **root logger** 的 ERROR 级 handler，投递到 `ALERT_EMAIL`；后台日志面板（`core/log_collector.install_log_collector`）同样只收 **logging 记录**。而路线里大量失败处理是 `print(f"[xxx] ... failed ...")` 形态 —— `print` 写 fd 1，**不进 logging 管道**，于是既不上面板、也不进告警。
- **本轮落点**：第 3 步给 `web/routers/auth.py::test_embedding` 加的失败日志正是 `print` 形态（与其余路由同风格，spec 明确点名），所以它**看得见但不会告警** —— 这条日志的可见性止于容器输出。
- **修法（一个落点 + 两条口径 + 约定）**：
  1. **往上抛的不动**：**290** 处「print 文本含 `fail`、下一条语句必然 `raise`」（`storage` 241 / `web` 40 / `core` 9）保留，由 `web/server.py` 的全局异常处理器（`222ff7ed`）统一记一条带堆栈、带 `METHOD path` 的 ERROR —— 一处覆盖整条路，`AlertHandler` 由此收到那些失败。响应体**一字不改**（仍是 `{"detail": "服务器内部错误，请稍后重试"}`）。
  2. **吞掉的改走 logging**：`core/nonfatal.nonfatal` 加了 keyword-only 的 `level`（`e4c6c5cb`，默认仍是 `logging.ERROR`，现有调用行为不变），其余用模块 `logger`。级别口径：**ERROR = 数据没有存进去，或者用户的请求失败了；WARNING = 已经兜底、不影响结果的后台动作**（好感度评估、阅读进度、预热、缓存）。落点：`storage/postgres_store.py` 5 处（`6ece957e`）+ `web` / `core` 其余 24 个文件（`7b056113`）。`sqlite_store.py` 不动（SQLite 已退役，见 80/112）。
  3. **断言 stdout 的用例改 `caplog`**（与所在落点同 commit）—— 只断言「有日志」会让 ERROR 被写成 WARNING 也照绿，而那一档之差就是「发不发邮件」。
  4. **约定入 `AGENTS.md`**（「开发工作流约束」末条）：吞掉错误必须写日志、不许只 `print`；往上抛的由全局处理器统一记录。
- **读数（2026-09-24，rebase 后现跑）**：区间 `git diff -U0 9bb2d97..HEAD -- core web storage` 删掉 **124** 行 `print(`（同区间新增的 `print(` 为 **0**），新增失败记录调用 **123** 处（`logger.error` 39 + `logger.warning` 79 + `nonfatal(` 5）。按级别：**ERROR（发邮件）43 处、WARNING（只上面板）80 处**，跨 **25** 个文件。产数脚本 `tests/perf/alerting_level_census.py` —— `--end <sha>` 可回放本批各 commit 的量；它按 (文件, 档) 逐条打印，排除 `tests/` 与 `core/nonfatal.py` 的理由写在 docstring 里（后者的 `async def nonfatal(` 会被误收成一个落点）。
  - **本读数的 `BASE` 是「本分支与 `origin/main` 的分叉点」，不是某个固定的老 main**：本分支 rebase 过两次（原 base `1b09328` → `e6b1698` → 现 `9bb2d97`）。以旧 main 作 base 时同口径读数是 130 / 129 / 45 / 84，差在 main 自己带进来的落点被算成「本批新增」（如 `storage/postgres_store.py` 那条「迁移文件已记账但内容不符」的 `logger.error`）。`e6b1698` 与 `9bb2d97` 两个 base 下同一个 HEAD 读到同一组数（124 / 123 / 43 / 80）—— 之间被跳过的 main 提交没动过本批落点。分叉点用 `git merge-base origin/main HEAD` 重算，rebase 后必须同步 `BASE` 这一个字面量。
  - 130 → 124、129 → 123、45 → 43、84 → 80 的另一半原因在 rebase 期间 main 的重写：`core/text_manager.py` 那两处本批改成的 ERROR 落点被 main 重写成 `print` + `raise`（归 §2.2，本批不动，故不再计入本批落点），另 3 处 WARNING 落点被 main 重写。
- **判据命令**：`pytest tests/test_failure_alerting.py::test_no_print_swallowed_failures_left_in_production_code` —— 扫 `storage/postgres_store.py` / `web` / `core`，「`print` 文本含 `fail` 且下一条语句不必然 `raise`」的结果必须为 **0**（现跑 **0**）；该判据按「必然抛出」精化，不靠行号例外清单（`core/distiller.py` 那处 print 后跟 `if truncated: raise … / raise …`，判为必然抛出，属 ①，不动）。**2026-09-25 起判据换了口径**：同一句命令，扫描面加上 `adapters/`，判据变成「`print` 落在最近的 `except` 分支内（或文本含 `fail`）**且**此后不必然把失败交给日志」，结果必须**等于**登记豁免 `_KEPT_PRINTS`（现跑只剩 `core/alerting.py::_main` 一处；一个相等比较同时拦住新增违规与豁免失效）。两处就地修正：`raise HTTPException(...)` 不算交给日志（走不到全局处理器），`return` / `break` / `continue` 算不抛出；找语句所在的列表遍历父节点所有列表字段（`body` / `orelse` / `finalbody` / `handlers`）。
- **rebase 收尾（`e6e73463`）修了一处冲突解错**：`web/routers/distill.py` 的模块 `logger` 定义落在冲突区里，被解到 HEAD 侧丢掉，于是该文件 21 处 `logger.*` 全成 `NameError`（后台蒸馏线程里才炸）。扫描判据只看源码文本，**看不见**这一处（它扫的是「`print` 还在不在」，不是「`logger` 有没有定义」），是受影响的蒸馏用例抓到的 —— 故「以分支 CI 为准」不是形式：判据绿不等于这条改动能跑。
- **红源**：`tests/test_failure_alerting.py` 四组 —— 全局处理器（改动前那条是 `traceback.print_exc()`，无 logging 记录、无告警）；`nonfatal(level=WARNING)` 只记 WARNING 且不发信、默认档仍发信（`e4c6c5cb` 前根本没有 `level` 参数）；吞错落点的级别；以及上面那条扫描判据。变异表与产物见 `tests/perf/alerting_mutations.py` / `alerting_red_lines.json`（每个判别器都有专属变异撞过，由 `tests/test_lock_coverage.py` 核闭合）。
- **残留（2026-09-25 结案：四处各有 commit）**：本条原先只做到「把 `print` 换成日志」，三项都要求判据从**文本匹配**改成**按结构判定**，当时（2026-09-24）留作身份线 71 批统一处理。**2026-09-25 这一批（spec `119-residual-structural-scan`，spec 与产数脚本不入库）把四处一并收口：**
  1. **判据按文本匹配小写 `fail`，漏掉 `except` 里的吞错**（下面 ①）→ **`17db7e4`** 把残余的 8 处换成模块 logger；**`597aa1f`** 把判据改成按结构判定（`print` 落在最近的 `ExceptHandler` 内即算，不再靠 `fail` 这个词）。
  2. **扫描器只在父节点 `.body` 里找语句，`else` / `finally` / `handlers` 被静默跳过**（下面 ②）→ **`597aa1f`**（遍历父节点所有列表字段）+ **`7367b9a`**（M11：往 `else:` 里插一句 print 必须变红）。
  3. **`print` 后紧接 `raise HTTPException` 不经全局处理器**（下面 ③ 的残余 4 处：`web/routers/distill.py:833`、`:1219`，`web/routers/voice.py:114`、`:461`）→ **`17db7e4`**（换 logger，级别按 ③ 的场景）；判据侧由 `597aa1f` 承认「`raise HTTPException` 不算交给日志」、`7367b9a` 的 M12 钉住。
  4. **路由锁 `_raises_5xx` 只看位置参数，关键字写法 `HTTPException(status_code=5xx, …)` 能绕过去**（本批 S0 顺着 ③ 查出的姊妹盲区）→ **`f99dad6`**；红源是仓外一次性变异（给 `web/routers/history.py::list_trash` 加一处关键字兜底 500 → 收集函数变红 → 按字节还原）。
  另：约束 7 那两条断言 stdout 的用例（`tests/test_rag_unusable.py::test_caller_group_rebuild_degrades_no_index`、`tests/test_message_evidence.py::test_parse_failure_is_visible_not_silent`）随 `17db7e4` 改成 `caplog`（后者断言的是文案**片段**，按完整文案 grep 扫不到）。
  **读数（2026-09-25 现跑）**：规则命中 **17 → 1**（`c3ea722` 上 = 16 违约 + 1 豁免；HEAD 上只剩豁免那 1 处）；本批改动的 `print` 语句 **19** 条（spec 的「18 处」是它自己的表格口径把 `adapters/llm_adapter.py:208`/`:210` 合算一行；按语句数实测 19 —— 改动面与表格逐行一致，无多余无遗漏）；路由锁现场 **1 处 == 登记**；变异 **13** 条全 RED、覆盖域 **12** 条判别器全部被打到，`tests/test_lock_coverage.py` **28 passed**、缺口名单无新增。
- **下面三项是本条原先登记的现场（行号是当时的，不复算）**：本条只做到「把 `print` 换成日志」，下面三项都不在本条的能力范围内，且都要求判据从**文本匹配**改成**按结构判定**，故留作身份线 71 批的统一做：
  1. **现有判据按文本匹配小写「fail」，漏掉 except 块里的吞错** —— 行号以 rebase 后的现场重新核对后为 **11** 处：`core/chat_engine.py:407`、`:460`、`core/rag.py:570`、`:598`、`core/schema.py:298`、`web/routers/text.py:188`、`web/routers/group.py:142`、`:324`、`web/routers/market.py:516`、`core/text_manager.py:108`、`core/agent/agent_loop.py:108`。核对时的两处修正：① 原列的 `web/routers/group.py:141` / `:322` 现址是 `:142`（print）/ `:324`（print 之后还隔一条 `traceback.print_exc()` 才 `raise HTTPException`）；② 原列的 `core/distiller.py:967` 已由蒸馏线改成 `raise DistillError`，不再是该形态，故名单由 12 处减为 11 处。另 `core/chat_engine.py:407` / `:460` 两处 print 之后仍 `raise`，本不属「吞掉」；把判据改成按结构判定时要注意 `_always_raises` 对「无 `else` 的 `if`」判为不必然抛出，会把它误报，得一并处理。登记这两种形态的差别比登记行号重要。
     合理例外一处：`core/alerting.py:194` —— `AlertHandler` 的 `--test` 自检自身发信失败，只能写 stderr 并 `return 1`（它就在投递链末端，没有更上一层可托）。
     另有 `core/distiller.py:1007`（角色识别数组某项不是对象、静默跳过）不在 except 块内，故不在上面那张名单里，一并交给身份线 71 批判。
  2. **扫描器只在父节点的 `.body` 里找语句**（`getattr(parent.get(stmt), "body", None)`），`else:` / `finally:` 分支里的会被**静默跳过**（不是判为合规，是根本没判）。现跑 **0** 处；改成按结构判定时要把 `orelse` / `finalbody` 一并走。
  3. **print 后紧接 `raise HTTPException` 的 41 处**（其中 40 处 print 文本含小写 `fail`）**不经全局处理器** —— `ExceptionMiddleware` 就地消化 `HTTPException`，它到不了 `@app.exception_handler(Exception)`（已实测：挂一条抛 `HTTPException` 的路由，全局处理器一次都没记）。故 ① 的覆盖对它们不成立、失败仍只到 stdout。按文件：`web/routers/history.py` 12、`web/routers/distill.py` 11、`web/routers/voice.py` 4、`web/routers/admin.py` 3、`web/routers/text.py` 3、`web/routers/auth.py` 2、`web/routers/market.py` 2、`web/server.py` 2、`web/routers/chat.py` 1、`web/routers/message.py` 1。另加 `adapters/llm_adapter.py:595`（rebase 前是 `:569`；`adapters/` 不在三个扫描根内，判据从不看它）。修法需 Shiyu 定夺（逐处记一条，或加一个按异常链 `__cause__` / `__context__` 判定的 HTTPException 处理器），本条未做。
     - 读数 44 → 41 的原因：rebase 期间 main 重写了 `web/routers/distill.py`，该文件的这一形态由 14 处减为 11 处，其余文件不变。
     - **读数 41 → 18（2026-09-24，Spec 71 A-2 首段之后）**：本批次删掉的正是这一形态的大头（`print(…fail…)` 紧接 `raise HTTPException(500…)` 的包装），并把 503 契约那处的 `print` 换成了 `logger.error(…, exc_info=True)`（见 71 的账）。判据与上面同一句（`print` 语句的下一条语句是 `raise HTTPException`），按文件现跑：`history.py` 12、`voice.py` 2、`server.py` 2、`chat.py` 1、`distill.py` 1 = **18**（`admin` / `auth` / `market` / `message` / `text` 五处已清零）。其中 **13 处（`chat.py:724` 1 + `history.py` 12）随本条的 ① / ② 一起归身份线 71 批** —— 12 处属缺陷 96 的改动面，`chat.py` 那处属 71 尾段。**其余 5 处不在本批改动面内，非本批漏删**：`web/server.py` 2 处（`Read/Update config failed`，本批只动 `web/routers/`，统一出口所在的 `web/server.py` 未动）；`voice.py` 2 处与 `distill.py:1220` 1 处本就是 **400**（ffmpeg 转码失败 / 角色卡校验失败），本批只删 5xx 包装。
     - **读数 18 → 4（2026-09-24，Spec 71 A-2 尾段之后）**：尾段删掉其中 **15 处**（`chat.py` 1、`history.py` 12、`web/server.py` 2），剩下 **4 处** —— `web/routers/distill.py:833`、`:1219`，`web/routers/voice.py:114`、`:461`，**留给本条残留那一份处理**。**两个口径差 1 处，并列写明**：上面「41 / 18」那串读数用的是**字面形态**（`print` 的下一条语句是 `raise HTTPException`）；若再加「print 文本含小写 `fail`」这层过滤（本条 ① 的口径），差额就是 `distill.py:833`（它的 print 文本是 `Resume row … gone before restamp; refusing to start`，不含 `fail`）。同一棵树上现跑：字面形态 **19 → 4**（删 15）、含 `fail` 形态 **18 → 3**；步骤 4 说的 15 / 4 是**字面形态**那一式，别再与 `fail` 式混算。

**120. 保存设置不校验 `embedding_region` —— 存进一个构造器不认识的地域，该用户此后走嵌入的路径全线 `KeyError`** —— 状态：**已修**（`2af4ee8`，第 3 步，2026-09-23）
- **归属**：72 线（S0 第 3 条查实）。
- **形态**：`web/routers/auth.py:151` 的 `ApiConfigRequest.embedding_region: str = ""` **无取值校验**，`update_user_api_config` 原样落库。该值随后被 `core/rag.py` 读作 `region=config.get("embedding_region", "cn")` → `core/embeddings.py` `DashScopeEmbedding(api_key, region, ...)` → `DASHSCOPE_BASE_URLS[region]`。
- **后果**：保存一个未知地域（如 `"us"`）后，该用户此后**所有**走嵌入的路径（RAG 检索、索引、蒸馏建会话）都在构造客户端那一刻 `KeyError`，而保存那一刻毫无提示、返回 `{"ok": True}`。
- **修法**：给 `ApiConfigRequest.embedding_region` 加 `field_validator`（`_known_region_or_blank`）—— 空串或 `DASHSCOPE_BASE_URLS` 的键放行，其余抛 `ValueError` → FastAPI 422，**在落库之前**拦下。空串放行不是漏检：仓储层对空字段一律不写（`update_user_api_config`），放行空串 = 「这次不改这个字段」。校验**查同一张表** `DASHSCOPE_BASE_URLS`，不另写 `{"cn", "intl"}` 字面量（与已改的试连端点 `test_embedding` 同口径）。导入放在函数内 —— `core.embeddings` 顶层 import chromadb，路由模块不为一次字段校验背它。
- **另一面（同族，早先已修）**：连通性测试端点 `test_embedding` 的前置校验（不在表里返回「地域只能选 cn 或 intl」，`9dc4118`）管的是**试连那一刻**；本条管的是**保存那一刻**。两处不是同一点，本条修的不是那条。
- **判据命令**：`git grep -n "embedding_region" web/routers/auth.py` —— 数「取值处有没有集合判断」。读数（2026-09-23 修后）：`ApiConfigRequest` 的 `if v not in DASHSCOPE_BASE_URLS`（`:169`）**1** 处；`test_embedding` 的 `if region not in DASHSCOPE_BASE_URLS`（`:687`）**1** 处；`update_api_config` 落库**0** 处（校验已前移到模型层，落库点无需再判）。
- **红源**：`tests/test_embedding_test_endpoint.py` 的 T11（空串 / cn / intl 放行）与 T11b（未知地域 422 且**不落库**）；T12 钉「查的是同一张表」（`monkeypatch.setitem(DASHSCOPE_BASE_URLS, "us", ...)` 后 `"us"` 变放行）。

**121. 群聊非流式 `/send` 的两笔保存共用一个 `nonfatal` —— 用户那笔失败时助手那笔整条不执行** —— 状态：**已修**（`cd756a6`，2026-09-23；72 线 · 消息真正落库）
- **归属**：72 线 · 94 口径统一（S0 第 5 条查实）。
- **形态**：`web/routers/group.py:522` 的 `async with nonfatal("group", "save messages")` 块内连出两条 `save_group_message`（`:523` 用户、`:527` 助手）。
- **后果**：用户那笔抛错时块内后续（助手那笔）整条不执行，而调用方只拿到一个 `failed` —— 与非流式一对一改前的「三笔共用一个块」是**同一形态**（那半边已在 `fc18018` 拆掉），同样分不出是哪条没存上。
- **修法**：共用块整个删掉，两笔各自 `await group.outbox.write(...)`（`web/routers/group.py:539` / `:541`），各自返回 `user_save` / `char_save` 与本次补写报告 —— 与 `broadcast` 同一口径。「用户那笔失败 → 助手那笔不执行」从机制上不再可能：两次 `write` 是并列语句，第一条的成败不构成第二条的前提。
- **判据**：`tests/test_message_backfill.py::test_G1_group_user_save_failure_still_enqueues_the_reply` —— 用户那笔存失败时断言 `char_save.state == "pending"`（**入了队**，不是没执行），恢复后两笔按原顺序补上（键都在 `flushed` 里且 `id` 递增）。变异 = 把 `/send` 改回「用户那笔没存上就跳过助手那笔」，该用例变红。

**122. 两处角色消息保存点未纳入 94 口径 —— 开场白与重逢问候存失败时无标记、无提示** —— 状态：**已修**（`cd756a6` + `3d0c07a`，2026-09-23；72 线 · 消息真正落库）
- **归属**：72 线 · 94 口径统一（2026-09-23 审计复查时查出的范围缺口；94 的「已修」只覆盖一对一聊天与群聊广播）。
- **形态一**：`web/routers/distill.py:1467` 的 `async with nonfatal("start_session", "save opening message")` 块内含三件事 —— 开场白落库、`engine_obj.history` 追加、`message_ids` 登记。
- **后果一**：存失败被吞掉后，`opening` 仍作为 `result["first_message"]` 返回（同文件 `:1478`），前端**照常显示**这条开场白，但它不带任何「未保存」标记（该接口没有对应字段，94 只给一对一 chat 与群聊 broadcast 的返回体加了 `*_saved` / `saved`），也不会进 `engine.history` —— 角色下一轮不记得自己说过这句。
- **形态二**：`web/routers/history.py:323` 的 `async with nonfatal("history", "reunion greeting")` 把**生成**（`engine.generate_reunion_greeting`）与**保存**两件事包在同一块里。
- **后果二**：存失败时 `greeting_data` 保持 `None`，整条问候从返回体里消失 —— 用户看不到问候，也看不到任何提示，只当今天没有重逢问候。附带一处：同文件 `:338` 的 `_visit_count >= 3 and not greeting_data` 会把「存失败」读成「没生成」，于是转而走按到访次数觉察那条路。
- **修法**：两处的落库都改走会话队列，并把**「进记忆」与「落库」拆开** ——
  - `distill.py`：开场白 `outbox.write(_save_opening, ping=storage.ping)`，返回体带 `first_message_save`（`save_field`，落库时为 `None`）；`engine_obj.history.append(...)` **移出** `if opening_rows:` 守卫（`3d0c07a`）。
  - `history.py`：问候 `outbox.write(_save_greeting, ...)`，`messages` 末条带 `save`；`engine.history.append(...)` 同样移出 `if greeting_rows:` 守卫（`3d0c07a`）。
  - 为什么必须拆：绑在守卫上时，库一抖动这条就「没说过」—— 开场白刷新后消失；问候不在记忆里，下一轮 LLM 不知道刚打过招呼会再打一次。`message_ids` 与时间戳仍只在落库成功后登记（未落库的还没有行 id，不能塞一个假 id 进去）。
- **判据**：`tests/test_message_backfill.py::test_C5_...` / `test_C6_...` —— 断言**两件事分开**：记忆里有这条（本轮体验完整），且返回体带 `save` / `first_message_save` 声明它会丢。C6 数记忆里 assistant 的**条数**（=2）而不是看末条：假 LLM 下开场白与问候文本相同，只看 `history[-1]` 时「问候没进记忆」也照样绿。变异 = 把 `history.append` 挪回 `if *_rows:` 守卫内，两条各自变红。



**124. VoicePanel 的删除是「删两次」—— 确认框形同虚设，第二次必 404** —— 状态：**已修**（`015cae9`，2026-09-23）
- **归属**：Spec 3（S5）。记账缘由：114 的「例外清单之外、仍未接线」里点了这四个坐标（`VoicePanel.jsx:134/216/590/607`），读上下文时发现根因不只是「没接错误处理」。
- **形态（修前）**：`handleCustomDelete`（`:124`）/ `handleRefDelete`（`:212`）**自己 await 了删除**，同时又 `setDeleteVoiceConfirm` / `setDeleteRefConfirm` 打开确认框；确认框的 `onConfirm`（`:585` / `:607`）**再删一次**。即：点一下按钮就删了（确认框出现在删除已经发出之后），用户再点「确定」是**第二次** DELETE。
- **根因**：`5445431c`（「replace window.confirm with ConfirmModal across 3 files」）把 `if (!window.confirm(...)) return` 换成 `setDeleteVoiceConfirm(...)` 时，**只换了判断、没删掉紧随其后的删除调用** —— 确认从同步门变成了状态位，后面那段照旧执行。`git log -S "setDeleteVoiceConfirm(voiceId)"` 一条命中即可定位。
- **为什么今天才看得见**：第二次 DELETE 打的是已经删掉的资源，后端不是幂等的 —— `voice.py:178` 对自定义音色回 404「音色不存在」、`voice.py:411` 对参考音频回 404「角色卡不存在」。原代码把这两处都 `catch { /* store handles */ }` 吞了（而 store 里根本没有那句注释所说的处理），所以**把错误接上屏的第一步，就会在正常删除路径上弹出一个假错误** —— 这才是本条必须与 114 的接线同时处理的理由。
- **修法（`015cae9`）**：确认框成为**唯一**删除落点 —— 两个 handler 只留 `setXxxError('') + setDeleteXxxConfirm(...)`（`deletingId` 的置位/复位跟着落到 `onConfirm`），删除、成功文案、失败 `setCustomError` / `setRefError` 全在 `onConfirm` 里各做一次。四句 `/* store handles */` 假注释随之删净（全文件 `git grep -n "store handles"` → 0 行）。
- **行为变化（不是纯内部重构）**：删自定义音色、删参考音频**现在真的需要点确认**。这正是确认框本来的意图，但用户此前感知不到（点下去就已经删了）。
- **是不是乐观更新 / 要不要回滚**：**都不是、不需要**。两个 store action（`useAppStore.js` 的 `deleteVoiceRef:441` / `deleteCustomVoice:497`）结构是 `await fetchWithTimeout(DELETE)` → 再向服务端重拉（`loadVoiceRef` / `loadVoices`）；本地状态**没有任何先行删除**，失败时列表压根没动过，无物可回滚。改动只是把这个「失败抛出」的契约写成注释钉住（两处各一行），并且**没有**给它加重试或假默认值。
- **本份同时接线（114 点名的其余坐标，同一 commit）**：`TextPanel.jsx` 的两个 `startChat` 落点（分组卡菜单 `:684`、版本行菜单 `:753`）改为 `setError(err.message)`；`ChatArea.jsx` 记忆面板的 `alert('添加失败'/'更新失败')` 改为面板自己的 `memoryError` + `ErrorBox`（**面板是 `position: fixed; z-index: 1000` 的覆盖层，ChatView 顶层那个 ErrorBox 会被它盖住，所以必须在面板内**）；`MinePage.jsx:91` 的 `exportCard(...).catch(alert)` 是最后一处「用 alert 报请求失败」，同改 `setError`。
- **本份产生并当场修掉的问题（自我披露）**：S2（`841b113`）把 `TextPanel.jsx` 内层组件 `CharacterManagement` 的 `console.error('Delete card failed:', err)` 改成了 `setLocalError(err.message)`，**而 `localError` 只在外层 `TextPanel` 上声明** —— 删卡失败时抛 `ReferenceError: setLocalError is not defined`，错误既不显示、还以未捕获异常丢掉。S5 给内层组件加了自己的 `error` + `ErrorBox` 并改回本层状态（`015cae9`）。红源见下第 ② 条，变异正是**改回 `setLocalError`**（实测报文逐字为 `ReferenceError: setLocalError is not defined`）。
- **红源（3 个新文件，10 条）**：
  - `web/frontend/src/components/__tests__/VoicePanelDeleteConfirm.test.jsx`（6 条）：① 点删除只开确认框、**不发请求**；② 点确认后 DELETE **恰好 1 次**；③ 删除失败时 `customError` 上屏且音色仍在列表；④ 参考音频同形；⑤⑥ store 层 `deleteCustomVoice` / `deleteVoiceRef` 失败时 **reject**。
  - `web/frontend/src/components/__tests__/TextPanelDeleteError.test.jsx`（3 条）：删卡失败错误上屏（②的锁）、分组卡 `startChat` 失败上屏、**版本行** `startChat` 失败上屏（两个落点分别锁，避免只锁一处的仪器假绿）。
  - `web/frontend/src/components/__tests__/ChatAreaMemoryError.test.jsx`（1 条）：记忆「添加」失败 → 面板**内**出现 `.error-box`，且 `window.alert` **未被调用**。
- **变异实测**：① 把 `await deleteCustomVoice(voiceId)` 放回 `handleCustomDelete` ⇒ 「不发请求」与「只 1 次」**双双红**（实测 0 vs 1、1 vs 2）；② 给 `deleteCustomVoice` 包回静默 try/catch ⇒ store 两条**红**（`promise resolved "undefined" instead of rejecting`）且「失败上屏」红；③ 改回外层 `setLocalError` ⇒ TextPanel 删卡用例**红**（`ReferenceError: setLocalError is not defined`）；④ 记忆「添加」的 catch 改回 `alert('添加失败')` ⇒ ChatArea 用例**红**（alert 被调用）。逐字节还原后 **10/10 绿**。
- **判据命令（修复后）**：`git grep -n "store handles" -- web/frontend/src`（应给 **0 行**）；`git grep -n "deleteCustomVoice\|deleteVoiceRef" -- web/frontend/src/components/VoicePanel.jsx`（应给 **3 行/3 行**形态：handler 里已无调用，只剩 `useAppStore` 选取与 `onConfirm` 里那一次）；`git grep -n "alert(" -- web/frontend/src` 剩余 **6 行**，逐条读上下文全部是**提交前校验 / 浏览器能力提示**，没有一处报请求失败 —— `EditCardModal.jsx:109/115/122`（角色卡字段行数/字数/关系条数上限）、`GroupChatPage.jsx:222`（没 @ 到角色）、`MinePage.jsx:183/219`（浏览器不支持定位 / 定位权限被拒）。**命中数不是判据，「还有没有报请求失败的」才是。**

**125. 识别层解析失败的两套语义（单分片吞成空名单 / 多分片把真空当失败）** —— 状态：**已修**（见本线 commit `823325f`，2026-09-22；与缺陷 86 的识别层那一半同一 commit）
- **归属**：蒸馏线（缺陷 86 收口时拆出）。86 记的是三通道「故障 vs 真空名单」在**上屏文案**上不可辨；本条记的是**同一个区分在识别层被表达成两套相反的规则** —— 上屏那一层修好了也没用，只要这两条规则还在，被送上来的东西本身就分错了类。
- **形态（修前，`core/distiller.py`）**：`_identify_single_call` 两次解析均失败时 `return None`；`_identify_over_chunks` 末尾 `if not parts: raise DistillError("识别失败：未能从任何片段中识别到角色")`。同一个「空名单」在两条路径上各被当成另一种东西 ——
  - **单分片**：故障（网络超时 / 限流 / 模型回的不是 JSON）→ 返回 `None` → 被 `web/routers/distill.py` 的 `except Exception: chars = []` 吞成空名单 → 与「这本书真的没有具名角色」渲染成同一句。
  - **多分片**：每一片都解析成空（＝这本书真的没有具名角色）→ **抛** `DistillError` → 一个**合法结果**被当成故障上报，用户被告知「请重试」，而重试永远不会成功。
- **为什么是缺陷**：两套语义各自都在**染色**一类结果 —— 不是「两个 bug」，而是**一个区分在两个方向上各错一半**：空名单是结果还是故障，取决于走了哪条路径；而正确口径与路径无关。判据命令：`git grep -n "未能从任何片段中识别到角色" -- '*.py'`（修后应 **0 行**）。
- **处置（见本线 commit `823325f`，净删宽分支、不新增配置 / 不新增捕获）**：两条路径统一成「**失败抛 `DistillError`、空名单返回 `[]`**」—— `_identify_single_call` 的 `return None` 改抛；`_identify_over_chunks` 的 `if not parts` 改 `return []`。空名单是**合法结果**，照常落 memo；失败走异常，天然不进缓存（`core/distiller.py` 只缓存成功结果那一段据此成立）。
- **锁**：`tests/test_identify_whole_book.py::TestEmptyRosterIsNotFailure` 三条 —— `test_single_chunk_empty_array_is_an_empty_roster`（单分片回合法的 `[]` → 空名单、不抛）、`test_all_chunks_empty_array_is_an_empty_roster`（多分片每片都回 `[]` → 空名单、不抛、且不走到合并：断言 `chat_stream.call_count == 0`）、`test_empty_roster_is_cached_like_any_other_result`（空名单照样进 memo）。**变异对象（该测试自己记的）= 恢复 `_identify_over_chunks` 末尾的 `if not parts: raise` → 多分片那条红**；单分片那条守的是相反方向 —— 不把合法空数组当解析失败。
- **与 86 的分界**：本条修「结果如何分类」，86 修「分类结果如何上屏」。同一 commit 落地，守它们的用例不同（本条在 `tests/test_identify_whole_book.py`，86 在 `tests/test_identify_failure_channels.py`），互不覆盖。

### 三之二、特性缺失 / 立项（非缺陷）

> 与「缺陷」分开记账：**缺陷 = 有东西坏了**（有正确行为可对照）；**立项 = 有东西从来没建**（没有可对照的现状，做它就是加功能）。混在一起会让缺陷清单虚高、也让「还有几个真缺陷待修」失真。三、里的编号 10 只留占位，指向本节。

**A. post / card 评论点赞特性整体缺失 → 已补（2026-09-25）**（原缺陷 10，2026-09-12 重记并移出）
- **原记账被证伪**：原条目称「同类端点 `text.py` 的 `get_text_comments` 标了 `liked_by_me`、`list_post_comments` 没标，按 `text.py` 的写法对齐即可」。实读后前提不成立 —— 照做只会写死一个恒 `False` 的**假默认值**（本仓明令禁止，见 §四）
- 证据（2026-09-12 现跑现查）：
  - **无表**：全仓没有 `post_comment_likes` / 卡评论点赞表；`_likes` 家族只有 `text_comment_likes`（`storage/migrations/031_text_comments.sql` 及 PG 等价物）与 `post_likes`（点赞**帖子**本身，`web/routers/market.py` 的 `like_post` → `toggle_post_like`）
  - **无路由**：`toggle_post_comment_like` 全仓零命中 —— post 评论根本没有点赞入口
  - **无原语**：`storage/sqlite_store.py` 的 `get_liked_comment_ids` **硬编码** `text_comment_likes`（PG 侧同），拿 post 评论 id 去查恒返空集
  - **无消费**：前端 `web/frontend/src/components/common/PostCard.jsx` 渲染 post 评论（头像 / 用户名 / IP 属地 / 时间 / 正文）**没有点赞按钮**，从不读该字段；`post.liked_by_me` 是**帖子**的赞，不是评论的
- 即：真缺口是**「post / card 评论点赞」这个特性从来不存在**，不是「某端点漏标一个字段」。对齐写法 ≠ 修 bug，是**加功能**（建表 + 双方言 migration + toggle 路由 + `get_liked_post_comment_ids` 原语 + 前端按钮），且要新增一张表
- 处置裁定（用户，2026-09-12）：**不做**。`list_post_comments` 保持现状 —— 不返回该字段，比返回一个恒 `False` 更有信息量（**2026-09-25 用户改判为「已做」，见下**）
- **收口（用户，2026-09-25）：已做** —— 按「新功能」走完整流程（spec `docs/specs/spec-comment-likes.md`），四个 commit 依次落地：`00afba5` 迁移 → `43651dc` 共用存储函数 → `23b01af` 路由 → `97b3cd6` 前端按钮。
- **新表 / 新列**：`post_comment_likes`、`card_comment_likes`（主键 `(comment_id, user_id)`；`comment_id` 外键 + `ON DELETE CASCADE`，`user_id` 不加外键 —— 删用户不删评论，带了级联会让「评论还在、赞没了、计数没减」漂掉）；`post_comments` / `card_comments` 各加 `likes INTEGER NOT NULL DEFAULT 0`。PG `029_comment_likes.sql` + SQLite 孪生 `096_comment_likes.sql`（编号按合并时 main 的最大号；2026-09-25 合并前复核 main 仍停在 PG `028` / SQLite `095`，故不变）。
- **判据（区别于「对齐写法」）**：三处列表的 `liked_by_me` 由真表查询得出，`get_liked_comment_ids(kind, …)` 按评论类型选表 —— 写死一张表或写死 `False` 都会让 `tests/test_comment_likes.py` 的列表用例变红；游客侧是「不显示按钮」而非「返回假 `False`」。
- 注：它仍留在第 9 条的扫描名单里（读 user 与否在该端点曾表现为「功能与否」而非「越权与否」），该扫描不受本条影响
- 立项与否 = **产品决策**，不是待办欠账；将来要做，按「新功能」走完整流程，不挂在缺陷表下

**B. `character_arc`（角色弧线）能填不能看 → 已补（2026-09-14，两处同补）**
- **事实**：字段是 `list[str]`（`core/schema.py`，每阶段一句话），`EditCardModal` 有编辑入口、存进 `card_json`、前端也拿得到；但**两处卡片详情都不渲染它**。同节其余字段（`personality_traits` / `values` / `key_memories` / `inner_tensions` / `relationships`）两处都渲染了
- **性质**：功能缺失，不是缺陷 —— 没有「该显示却显示错」的可对照现状，是**从来没有这块展示**，故不挂在缺陷表下
- **两处就是全集**：卡片详情渲染器全仓只有两个 —— `MarketCardDetail`（集市卡）与 `CharCard`（自建卡，`App.jsx` 的 `character` 视图）。普查当时 `character_arc` 在 `web/frontend/src` 下**只命中 `EditCardModal`**，展示侧零命中。只补一处会得到「看别人的卡有弧线、看自己的卡没有」—— 同一缺口的两处显形必须同补
- **处置（用户，2026-09-14）**：**已补，两处同补**
- **形态**：`<ol>` + 序号徽章 + 纵向序列（体现阶段先后），区别于 `values` 的并列 chip；外壳与折叠各自复用**本文件既有机制**（`MarketCardDetail` 走 `collapsedSections` / `toggleSection`，`CharCard` 本文件无折叠机制故不加）；空值 `?.length > 0` 整节降级。渲染层不抽共享组件（两处外壳本就不同：`CardSection` vs `card-section--wide`+`<h3>`），但 **CSS 共用同一组类名** —— `global.css` 的 `card-arc-list` / `card-arc-item` / `card-arc-index`，全仓只此一处
- **覆盖证据**：`web/frontend/src/components/__tests__/` 下 `MarketCardDetailCharacterArc.test.jsx` 与 `CharCardCharacterArc.test.jsx`（各两条：有 / 无），外加一条「CSS 只落一处」断言（三条类名定义在 `global.css`、不出现在 `adm-theme.css`）。变异矩阵见会话记录（守卫改恒假 → 两文件各自的「有」用例红；「无」载荷改带弧线 → 「无」用例红）

**C. 重构前置检查项：`/start` 的 scene index 曾只靠打开卡片时的 `/start_session` 补偿 —— 踩断它不会报错**（2026-09-14 记账，**2026-09-17 已收口**）
- **收口时的规格开篇 · 红线先说死**：**「只补调度不做幂等」比不做更坏** —— 它把「静默不索引」换成「**静默双倍索引 + `delete_collection` 删掉此刻在服务的集合**」。`core/scene_indexer.py` 的 `index_scenes` 原先无条件 `delete_collection` → `create_collection`，中间那段**空窗**里正在读这个集合的会话查询恒空；而 `rag.collection` 是**共享的**（`IndexingService._text_rag_cache` 把同一个 `RAGEngine` 交给后续每一轮对话）。**去重键挡不住第二拍**：`_scene_index_in_flight` 是**并发窗口**去重，任务一结束就 `discard`，而两处调度者之间隔着**人操作时间**（点完蒸馏、过一会儿才点开卡），窗口早关了 —— **只有被索引对象自己幂等才挡得住**。故两半必须同一次做完。
- **性质**：原记账不是缺陷（当时行为正确），是一条**靠巧合成立的隐性契约**。巧合有两层：`/start` 落卡那条 `TextManager` 不调度，而打开卡片时的 `/start_session` 补上了；补偿又依赖「`list_cards` 不投影 `session_id`」使前端每次都调 `/start_session`。**两层断了都不报错**，只表现为场景预索引停摆、聊天 RAG 召回悄悄变差。
- **修法（两半，同一次落地）**：① **装配出口唯一** —— `_save_card` 改走 `deps.get_text_manager()`。原先它在 `_run_distill_task` 后台线程里就地拼 `TextManager`，**是全仓唯一一处不经 deps 的构造**，漏传 `indexing_service` → `core/text_manager.py` 的 `if self._indexing_service:` 为假。② **`index_scenes` 幂等** —— 幂等键取**正文指纹**（`scene_indexer._FINGERPRINT_KEY`），存在集合自己的 metadata 里；正文没变即复用，指纹不同、或旧集合维度与当前 embedder 不符才重建。**不另存「已索引清单」**：那是第二份手工状态，会漂（§四「守卫与被守对象之间若隔着第二份手工维护的清单」）。保留 `/start_session` 的补偿调度（它同时是老卡的补建入口），幂等之后重复调度无害；`list_cards` 的投影不动。
- **锁**：`tests/test_scene_index_dispatch.py`（正控：同正文第二次不 delete/create/add；负控一：正文变了必须重建；负控二：维度不符必须重建；另加一条构造点唯一）。**变异已验**：撤掉幂等检查 → 正控红；指纹取常量 → 负控一红；维度不符当复用 → 负控二红；在 `distill.py` 就地把 `TextManager` 拼回去 → 构造点那条红并点名 file:line。
- **已知盲区（写在锁的 docstring 里，不假装覆盖）**：构造点那条读的是 AST 里的**名字**，`from core.text_manager import TextManager as TM` 这类改名会溜过（**已实测**：别名绕过确实不红）。失效方向是**响亮误伤**（就地构造会多红一次并点名），不是静默漏过 —— 照 §四 ②层代理的处置记在此处。
- **本项原正文（记账时的事实链 / 踩断方式 / 重构时检查动作 / 判据命令）已随收口删除** —— 它描述的是收口前的代码状态，留着就是 §四「台账状态行不是事实」的又一个滞后副本。**连带失效**：原判据命令 `git grep -n 'session_id' storage/sqlite_store.py`（`list_cards` 投影里应没它）不再承重 —— `/start` 这条路已自己调度，前端是否调 `/start_session` 不再决定场景索引是否发生。
- **构造点那把锁的两条盲区，本轮重新处置（2026-09-18）**：
  - **① 「文件粒度豁免」曾是真盲区，已由 `len(sites) == 1` 关闭 —— 是修，不是记账。** 原先判据只断言「所有 `TextManager(...)` 构造点都在 `web/deps.py` 里」，而 `web/deps.py` **整文件豁免**，故**在 deps.py 内部**再写一处装配（漏传依赖）照样绿 —— 这是**「豁免的粒度」这个设计的固有代价**，不是判据写漏了。处置不是记账而是修：加一条 `assert len(sites) == 1`。**变异已验**：在 deps.py 里插一处 `TextManager(get_storage(), None, None, get_sessions())` → 该条红并点名 file:line（M1）。**这条一度被列为「记账」，落地时发现它关得掉 —— 关得掉就不是记账，是本轮修掉**：一件事写两遍（先记「盲区」再改「已修」）没有意义。
  - **② 扫描面是 `web/**`，`core/` / `mcp_server/` 里就地拼不会被抓 —— 这一条换不动，才是真记账。** 判据读的是 AST 里 **`web/` 下**构造点的位置与数量；要覆盖任意包，就得自己维护一张「哪些包该扫」的清单 = §四 ③层手工清单。**今日 `web/` 之外零处是实测，不是设计保障**（2026-09-18 实测：全仓 `TextManager(` 的非测试命中**只有 `web/deps.py` 的构造一处** —— `core/text_manager.py` 的类定义是 `class TextManager:`，无括号，故不进这条 grep）。它变了锁不会报 —— 照 §四 ②层代理的处置写在此处，不假装覆盖。
- **B′. 三处工厂手抄同一份装配 → 收敛为一处（2026-09-18，与上面 ① 是同一个病的两个显形）**
  - **病灶**：`TextManager.__init__` 有 7 个形参，而 `web/deps.py` 里**三个工厂各自手抄一遍这份装配**。加一个形参要改三处、漏改一处照样绿（构造点锁看的是**文件**，不是**格子**）—— 这**就是缺陷 35 的核心**（同一份装配抄多遍），只是这次显形在 `deps.py` **内部**。旁证：`IndexingService(get_storage(), _rag_config)` 原本在 `reset_llm_and_dependents` 与 `get_indexing_service` 里已经抄了两遍。
  - **收敛后的形状**：一处装配出口 `_assemble_text_manager(distiller, llm)` —— 调用方**传实例**，该函数**不取单例、不缓存**。这是缺陷 37 那条互斥关系的底线：统一的是**装配流程**，不是**实例**（per-user 每次新造正是 LLM 隔离的实现方式，统一成单例会把 A 的 LLM 给 B）。`IndexingService(get_storage(), _rag_config)` 的重复收进 `_make_indexing_service()`，但**不合成单例 getter** —— 两者共用的是**构造表达式**，不是**生命周期**（`get_indexing_service` 懒建，`reset_llm_and_dependents` 必须重建）。
  - **删掉的死参数**：`TextManager.__init__` 的 `rag_config` 与 `summary_threshold`（类体内**唯一出现就是赋值那一行**，零读取）。**形态要写下来 —— 它们是「看着活的参数」**：名字对、类型对、**旁边真有消费者**，只是**消费者不在这个类里** —— `_rag_config` 在 `IndexingService` 是活的（拿去建 `RAGEngine`），`summary_threshold` 的概念也是活的（`web/server.py` 在设置接口暴露）。**这比「全仓没人用」危险得多**，读代码的人会以为它有用（§四 第四个同型例即此）。第三个性质让删除变成**必须**：删前 `__init__` 的位置 4/5 是**相邻同型对**（`rag_config` dict / `sessions` dict），转置**不报错、现有测试也抓不到** —— `self._sessions` 变成 config dict 后下游照样成功（往 config 里塞键），真正的 `_sessions` 模块字典**永远不被填**，表现是「会话不持久」、成因指向别处。**这是缺陷 G 的翻版，且更隐蔽**（G 至少让凭据出现在 prompt 里，可见）。
  - **删完为何不必再补 keyword-only**：剩下 4 个必填参数类型两两不同（`StorageBase` / `Distiller` / `LLMAdapter` / `dict`），**而真正会静默的那一对（两个 dict，第 4/5 位相邻）正是被删掉的那一对**；另核过 `Distiller` 与 `LLMAdapter` 的公开方法名零重叠 ⇒ 错位会 `AttributeError` **响亮失败**。**判据从事实推出，不是照搬 55/58 的形式**。唯一剩下的可选参数 `indexing_service` 本就是 keyword-only（`*` 之后）—— **变异已验**：改回位置传 → `TypeError: TextManager.__init__() takes 5 positional arguments but 6 were given`（M2，**机制拦下，不是测试红**）。
  - **证据强度（将来谁怀疑就重跑；①③ 于 2026-09-19 就地订正）**：① 死分支判定是**静态推出**，两个前提均为缺陷 37 已确立并有判据命令者（`llm is None ⟺ get_llm() is None`）—— **该等式只对经 `get_user_llm` 解析的调用点成立**（定义域与回归链见缺陷 37 条目末段「订正（2026-09-19）」）；本条的调用面恰好全落在定义域内，故此处结论不变；② 调用面**实测** —— `get_distiller(` 在 `web/` 下 9 处调用**全部传 `llm=`**，零处不传参；③ **原判「验不了」撤回** —— 原文写「那个条件（全仓无任何 API key）在本机结构上**复现不了** ⇒ 不是『还没验』，是**『验不了』**」，**此论断作废**：前提是「某个函数返回什么」时，一个 `monkeypatch` 就能构造，本案 C4 返工正是这么做出正反两态的（`tests/test_distill_task_api.py` 的 `_install_user_llm` / `_assert_premise_resolves`）。**「本机结构上复现不了」说的是环境，不是命题** —— 判据该落在**被替换的符号**上，不该落在环境的偶然状态上（§四「用例不得依赖测试机的 ambient 状态」的镜像：那条拦「靠环境恰好有」，这条拦「说环境恰好没有」）。删除的正当性由图 ① ② 承重，不再由 ③ 承重；删除**可逆**（git 有记录），留着一段读起来像活的死代码才是持续成本。
  - **删除的边界是量出来的，不是「我觉得难」**：死参数普查命中 14 处候选，其中**至少 1 处是结构性误判** —— `core/embeddings.py` 的 `Mem0BridgeEmbedder._dimensions` 被 `core/rag.py` 以 `getattr(self._embedding_function, "_dimensions", None)` **外部读取**，而那正是 `CollectionUnusableError` 的维度判据（本次幂等负控二依赖的机制）。**`getattr` 形式的读取对任何 AST 形状扫描都是隐形的**（没有 `.` 前导、也不在同一个类里）；要排掉它就得挂一张豁免表 —— 那正是「守卫与被守对象之间的第二份手工清单」（§四 ③层）。故**只取数、不建锁**：取数工具 `tests/census_dead_attrs.py` **已入库**（模块名不以 `test_` 开头，pytest 不收集，`--collect-only` 核过），**它不是判据，别把它做成锁**。**可复算读数**：`git ls-files` 的 95 个 `.py`（去掉 `tests/`）→ **9 处命中**；同一判据在本次改动**前**（`06ecabb` / `8457271`）是 **11** 处，差正好是本次删掉的两个 `TextManager` 死参数（`_rag_config` / `_summary_threshold`）—— 逐条对得上。**一条撤回**：本轮中途我报过一个「**14 处**」，那是**手边 scratch 脚本跑的、判据没钉下来**；现在用入库的 `census_dead_attrs.py` 在任何一种能构造出的扫描面上都复现不出 14（入库去 `tests/` = 11；入库含 `tests/` = 17；gitignore 盘面 = 1；把「读」收紧成「排除 `__init__` 体内的读」= 26），故**那个数作废，以 9（改后）/ 11（改前）为准**。这正是本节「被台账引用的数字，产出它的脚本与原始产物必须入库」那条的现场实例 —— 脚本不入库，数字就只剩「记录」而没有「配方」，**四读法重建也救不回来**（同缺陷 38 的「267 处」，那条的教训就是上一轮重建仍无定论）。脚本已入库，故这两个数这次可复算。（另须注意：gitignore 的 scratch 盘面**确实存在**同类命中，实测只有 1 处 —— `e2e/scratch_3c_resume.py` 的 `FakeLLM._make_async_client`，不在上面 95 个入库文件里，故不影响读数，也不该被算进分母。）
  - **变异结论（如实：没有锁报它）**：把 `_distiller` 单例放回去并接上线（恢复 `get_distiller` 的单例分支 + 让 `reset_llm_and_dependents` 重建它），跑 7 个受影响用例集 —— **没有任何锁变红**。与 M3 同处置：**边界属实**。构造点唯一那把锁管的是 `TextManager` 的**装配处数**，不管 `Distiller` 的**实例化**；收敛后本已无第二个 `Distiller` 装配点，锁的对象消失，故不为此新建锁。**「没有锁报它」是实跑确认的，不是「做不出」** —— 此二者在台账里必须分开写。7 个用例集里的 6 处 `deps.get_distiller` monkeypatch 点也逐个核过：**收敛后仍可 patch**（`test_distill_task_api.py` ×4、`test_domain_exception_exit.py` ×1、`test_security_authz.py` ×1；子集实测 172 passed / 2 skipped）。
  - **缺陷 37 的连带变化**：见缺陷 37 条目的「连带变化（2026-09-18）」段 —— 那条判据命令命中行数 4 → 1，改前/改后逐行对照写在那里。

**D. 两条 census 锁的扫描面不能再遍历目录树**（2026-09-21 立项，同日**已做** —— 本次提交，见 `git log -- tests/repo_files.py`）
- **事实**：`.claude/worktrees/<name>/` 是 git worktree 的挂载点（`.gitignore:228` 忽略、不入库），每个都带一份完整的仓库副本。`tests/test_collection_surface_lock.py` 的收集面与 `tests/test_exception_pickle_lock.py` 的异常类普查原先都取「仓库根以下」，于是兄弟 worktree 的 `tests/` 与 `core/` / `adapters/` / `storage/` 进了分母 —— 表现是这两条锁在本机**恒红**，且红的报文长度随活着的 worktree 个数增长。现证与反向对照见 §四「测试结论也不许依赖测试机的工作目录布局」。
- **为什么它不是缺陷而是一件事**：锁的**命题**没错（本仓确实不该有收集面外的测试文件；带自定义状态的异常类确实该全登记），错的是扫描面的分母混进了**别的工作树**。故处置是换扫描面，不是改命题 —— 改了命题就是把真判据也一起放宽。
- **处置（用户裁定，2026-09-21）：扫描面改成问 `git`（`git ls-files --cached --others --exclude-standard '*.py'`）；明令不得加 `.claude/` 等路径排除规则。** 理由：路径黑名单是又一条静默通道 —— 名单漏一格就少扫一片；而「第三方 vendored 代码 / 构建缓存 / 兄弟 worktree / 本地一次性脚本」本来就不是「豁免」，是**不在仓库里**，该由 `.gitignore` 说。排除规则因此只剩一份（`.gitignore`），两条锁里那两份逐字重复的 `_PRUNED_DIRS` 一并删掉。
- **落点**：新增 `tests/repo_files.py::repo_py(root)`（唯一取法，docstring 写清为什么不是 `os.walk`/`rglob`、以及 `--others` 为什么不能省），两条 census 锁改为调它。取向与 `tests/test_llm_access_gate.py::_production_py` 一致（那边 2026-09-17 已踩过「只认 `--cached` 时新写的文件对锁隐形」：L12 读成 3/2、真值 1/1）。
- **实测差集（改动前现跑，2026-09-21）**：旧面 **1084** 条 vs 新面 **238** 条。旧面独有 846 条，其中非 worktree 的还有 140 条本地产物（`e2e/scratch/**`、`data/eval_scratch/**`、`scripts/{import,export}_shiyu.py` —— 后两个在 `.gitignore:282-283` 被**点名**忽略）。新面 ⊂ 旧面，**新面独有 0 条**。
- **这三个数**是那一刻**快照**，别拿去当判据或对表 —— 本条目落进提交时就已变成 240：本改动自己新增了两个 `tests/repo_files*.py`，而新面是「问 `git`」，**未 `git add` 的新文件本来就在面里**（这正是 `--others` 那半在起作用，是特性不是漂移）。**可复跑的判据只有两条**：新面 `git ls-files --cached --others --exclude-standard '*.py' | wc -l`（读数随文件增减走）；旧面得把 `repo_py` 变异回 `os.walk` 再跑锁（见下一条验收②）—— 旧面那个 1084 依赖一份**已删除**的 `_PRUNED_DIRS`，没有等价的单行命令，**所以别引用它当判据**。
- **验收（两个方向，均已现跑）**：① 仓库根下三个活 worktree 时，两条 census 锁 + 新锁 **16 passed**；② **反向对照**：把 `repo_py` 变异回 `os.walk` → **3 failed** —— 两条 census 锁各自点名 `.claude/worktrees/<各 worktree>/tests/test_*.py`，新加的合成反例锁（`tests/test_repo_files.py`）点名 `junk/hidden.py`；恢复后 16 passed。第二条是关键：合成反例**只在临时小仓库里造一个被 `.gitignore` 覆盖的 `.py`**，故它在干净 clone（CI）上同样可判定 —— 只靠「本机有兄弟 worktree」才红的锁，CI 上恒绿，等于没有。

### 三之三、LLM 访问门 + core 反向依赖（C0–C5，2026-09-17 起）

> 规格在库：`docs/specs/llm-access-gate.md`（v5）。分步 C0 → C1 → C1′ → C2a → C2b → C3 → C4 → C5，
> 每步把 `tests/test_llm_access_gate.py` 的一组锁由红转绿（L1–L15，red-first + `xfail(strict=True)`）。
> **本节只记台账侧的事实**：步骤内容、锁的命题、逐步变异清单都在 spec 里，不在这里重抄
> （§四「同一个理由不要落在三个地方」）。判据**一律写符号名与命令**，不写行号（§四）。

#### A. 订正三条

> 订正**改的是原条目**，正文就在原处，本节只留索引（§四「同一个理由不要落在三个地方」）。三条的目标分别是：缺陷 37 条目、§三之二 C 条 B′ 段、`core/concurrency.py` 的模块 docstring。

- **A1 → 缺陷 37 条目末段「订正（2026-09-19）」**。要点：`per_user_llm is None ⟺ get_llm() is None` 的定义域是「经 `get_user_llm` 解析出来的那个变量」，不是全仓不变量；回归链 `9864608`（打断后台蒸馏线程）→ `bb62311`（打断 `/start`）各有实测读数，判据命令随条目给出。
- **A2 → §三之二 C 条 B′ 段的第 ③ 条**。要点：撤回「那个条件在本机结构上复现不了 ⇒ 验不了」的论断 —— 前提是「某函数返回什么」时一个 `monkeypatch` 就能构造，本案 C4 返工即以此做出正反两态。
- **A3（正文见下）** 的目标是一处**代码 docstring**（`core/telemetry.py` 的原判词，随 C2b 迁移时已在 `core/concurrency.py` 订正），台账里没有第二条副本，故正文留在此处。

**A3（正文）. telemetry 的「会抛异常」按实测订正 —— 判据从「不抛异常」改成结构判据**
- 原判词（`66f0363` 加入 `core/telemetry.py`）：「`detach` 会因 token 属另一 Context 抛 `ValueError`（实测）」。
- **实测（2026-09-19，本机 Python 3.12.10 + `opentelemetry`）**：原生 `ContextVar.reset(token)` 确实抛（`ValueError: ... was created in a different Context`）；但 **OTel 的 `detach` 把同一个 `ValueError` 自己吞掉**，只在 `opentelemetry.context` 这个 logger 上留一条 `ERROR ... Failed to detach context` + traceback，**不向调用方抛**。复算命令（`PYTHONPATH` 用仓内 `.venv`）：在 `contextvars.copy_context().run(...)` 内 `attach()`、在 `ctx.run` 之外 `detach(token)`，观察「有无异常」与那条日志。
- **真实后果因此不是「抛异常」，而是「token 一直没被释放、父上下文挂在拷贝出的 Context 里」**：异常被吞掉意味着**没有任何东西会报错**，泄漏是静默的。
- **判据随之改形**：「不抛异常」是个**恒真**的判据（OTel 永远不抛），拿来当锁等于没有锁。改成**结构判据**：`restore` / 执行 / `release` 三者必须同处一个 `ctx.run`（`core/concurrency.py` 的形态），由 L15 钉住。**这是 §四「豁免即静默放行」的镜像：失败被上游吞掉时，判据必须落在「结构对不对」，不能落在「有没有报错」。**

#### B. 新增缺陷

**59. `get_user_llm` 缓存命中时跳过 geo 检查（F3）** —— 状态：**已修**（C4，`06efc80`）
- 现象：用户实例进了 `_user_llm_cache` 之后，后续请求拿到的是缓存实例，**解析层此后再不判 geo** —— 换个被拦的 IP 来调用，照样放行。
- 修法：geo 判定**整体移出解析层**（§2.8 删掉 `client_ip` 参数），落到**调用点门**（`web/llm_gate.py` 的 `geo_call_guard`，出站前每次必判）。缓存命中路径仍要过 `llm.preflight()`（缓存的是**实例**，不是**这一次的判定**）。
- 锁 L3：缓存热时换成被拦 IP 调 `get_user_llm` → 拒绝，判据是 `kind == "call_refused"`。**变异**：缓存命中时跳过 `preflight()` → L3 红。

**60. geo 策略有三份且互相矛盾（F4）** —— 状态：**已修**（C4，`06efc80`）
- 三份：`get_user_llm`（解析层判一次）、`_distill_start_impl`（手抄一遍解析）、`_run_distill_task`（线程内再判一次）。三份的实现不同 → 同一个请求在三处可以得到三种结论。
- 修法：**策略收敛成一份谓词**，且**判解析结果、不判下游派生物**（`if llm is None: 503`）。原写法判的是 `get_distiller(llm) is None` —— 生产里两者恰好等价，测试一打桩就分叉，门形同虚设而线程侧照样断言炸（C4 返工就是被这一条咬的）。
- 锁 L1（`/start` 无 key + 全局可用 → 200，且后台拿到全局实例）、L12（`check_api_allowed(` 全仓恰 1 处且在 `geo_refusal` 内）。**变异**：恢复 `/start` 的手抄解析 → L1 与 L12 同批红。

**61. 聊天与群聊会话常驻 LLM 实例，命中后不再做任何 geo 检查（F6）** —— 状态：**已修**（C3）
- 现象：解析出口只在**建会话那一次**被判过；会话把它持有的实例留在内存里，此后每一轮对话都用它出站，**永远不会再判**。用户换 IP、或管理员改了策略，会话里的旧实例照跑。判「解析出口」拦不住它 —— 要拦的是**出站那一刻**。
- 修法：门移到一个 IP 看不见的地方 —— adapter 出站前调用注入的守卫（`_before_call`），身份经 contextvar（`LLM_CALLER`）传递，门在**调用点**判。
- 锁 L4：活会话持有非白名单实例、用被拦 IP 发 `/send` → 403，且毒客户端未被触碰。**变异**：门判 `distiller` 而不是 `llm`（或缓存命中不判）→ L4 红。

**62. core 有 10 处 `from deps import`（F8）** —— 状态：**已修**（C2a，`704996b`）
- 分布：`run_on_main_loop` 7 处（`chat_engine` 5、`evaluation_pipeline` 2）、`get_llm` 2 处（`auto_review`）、`get_memory_manager` 1 处（`TextManager` 的会话装配）。方向是 **core → web 的反向依赖**，与 §2.1 的分层箭头相反。
- 修法：改为**向下注册 + 注入** —— 投递走 `core/scheduling.py`（web 在启动时注册实现），`auto_review` 的 `llm` 变必填、由调用方注入，`TextManager` 的 `memory_manager` 由 `deps._assemble_text_manager` 注入。**不留别名、不做 re-export**（否则依赖只是换了个名字）。
- 锁 L13：`core/` 与 `adapters/` 里不 import `deps` / `web` / `routers`，**含函数体内的局部 import**（这 10 处正是局部 import）。**变异**：`core/chat_engine.py` 恢复一行 `from deps import get_llm` → L13 红。

**63. 投递写法有三种（F9）** —— 状态：**已修**（C2a `704996b` + C3）
- 三种：`deps.run_on_main_loop`（core 7 处 + web 7 处真实调用，spec §1.1 订正过口径）、`core/utils.try_record_usage` 里**自建 loop 起线程**写库（与前者 docstring 自述的语义相矛盾）、审计投递各写各的。同一件事三份实现 ⇒ 未注册时的回退语义、异常归宿、是否阻塞三处各有一个答案。
- 修法：收敛为 `core/scheduling.py` 的 `submit_to_main_loop(coro, *, wait, timeout)`；**未注册时的回退只定义一次**（`wait=True` 阻塞并**响亮**警告；`wait=False` 当前线程有运行中的 loop 就 `create_task`，否则 `asyncio.run`）。删 `deps.run_on_main_loop`，14 处调用点改指新符号。记账出口改用 `wait=False` 投递（异常交给 done-callback 落日志 —— 「记账不该拖住请求」）。
- 锁 L14（① 已注册时委托；② 未注册时两种 wait 的回退语义；③ `try_record_usage` 经它投递）与 L9（审计投递一次、抛错不影响拒绝也不阻塞）。**变异**：① 出口恢复自建 loop 的线程 → L14 红；② 未注册时把 `wait=False` 做成阻塞 → L14 红；③ 审计不再投递 / 审计抛错向上传播 → L9 红。
- **订正（C2a 落地时被实测逼出）**：L14 的 `wait=False` 判据原先只断言「协程跑了」—— **阻塞型错回退照样绿**，即 spec 那条变异根本打不红。改成**记序**（`["RETURNED", "CORO"]`）后两种错法各钉一半。这是把该锁自己声称的命题补成可判定的，不是扩范围。
- **`test_usage_accounting_lock` 的出口识别随之调整**（spec §3 要求记账）：`_has_record_usage_call` 的判据是「node 内下潜后出现 `.record_usage(`」，而出口的形态从「把闭包交给线程」变成「把协程交给投递原语」——**判据本身（找出口）没变，下潜的理由写了新的**。不下潜就找不到出口，出口集变空集，全仓站点集体假红。**变异证据**：把下潜去掉 → 该锁全站点假红。
- **L14 的还原口径**（C2a 记）：投递测试的还原一律按**原值**（`_SetSubmitter`），**不置 `None``** —— 生产 lifespan 一注册，同一进程里后续用例会被 `None` 静默拆掉注册。

#### C. 迁移记录（改名字 / 换位置，都是「调用点必跟」类）

- **`ctx_thread` / `ctx_submit`：`core/telemetry.py` → `core/concurrency.py`**（C2b，`639331a`）。调用点全部改 import，**不做 re-export**（§2.3）。同时把「载体」从 OTel 私有形态抽成 `register_context_carrier(carrier)` 协议（`capture / restore / release`），并补配对读口 `set_context_carrier` / `get_context_carriers` —— 原先载体注册没有读口，L15 的后半「OTEL 开启时已注册载体」**没有观测点**，是 spec §1.1 裁定「补配对读口」的那条。`core/concurrency.py` 内**不出现 `opentelemetry`**（L15），OTEL 开关只决定已登记的载体是不是 no-op。
- **投递：`deps.run_on_main_loop` → `core/scheduling.submit_to_main_loop`**（C2a）。删旧名不留别名；`scripts/` 里两处伪造 `deps` 模块的 shim 改用**公开注册口**（原先伪造模块，改名后它们会静默失去拦截 —— 仍绿，但拦不到东西，spec §1.1 预警过这一点）。
- **`install_llm_gate(app)` 放 `_lifespan` 的**第一行**（C3）**：门在**启动动作的最前面**装，因为启动过程本身若有 LLM 调用，门必须在那一刻已经成立。**不放 import 期**是另一半理由：`import web.server` 改变进程级策略，会让同进程里任何不启 app 的代码（适配器单测、脚本）凭空被门管住，并引入「谁先 import 决定谁被拦」的序依赖。注册是**装配**的一部分，就写在装配处。判据是 L11（锚点在启动那一刻，不在 import 那一刻）。
- **`_llm_error_handler` 写成同步**（C3）：它一个 `await` 也没有，只组装一个响应，没必要占一个事件循环任务（同仓 `_domain_error_handler` 保持 async 各有各的理）。**审计不在这个出口**：出口不唯一（chat 的兜底、SSE 错误帧、蒸馏的 `user_facing_error` 都不经过它），挂在出口等于「有的拒绝记、有的不记」；审计挂在**门**那一侧（`geo_call_guard` 拿到理由就记），那里是每个拒绝的必经之处。

#### D. 本案只报告项（逐条建条目，标状态）

**64. `_snapshot` 还原器自己也需要一把锁 —— 含「入场值为 `None`」那一格** —— 状态：**已修**（2026-09-22 补可判定的一格；2026-09-24 余项收口）
- `tests/test_llm_access_gate.py` 的 `_Restored` / `_snapshot` / `_set_var` 是四条进程级状态（守卫、投递器、载体登记表、`LLM_CALLER`）的还原器，而**它自己没有反向的守卫**：谁在 `with` 里 `set` 了又被别的路径覆盖，还原写回的是**入场快照**，不是「本该是什么」（这条是**设计如此**：块内跑 lifespan 装生产守卫、退出还原入场值，是本文件的定式，L4/L9/L11 都靠它）。
- 修法：**只补可判定的那一格**。插桩实测（单文件一次跑 37 次退出）：入场非 `None`/装非 `None` 14 次；入场非 `None` 且块内被别的路径改过 3 次；**入场 `None`/装 `None`/块内被装上值 6 次**；入场 `None` 且退出仍 `None` 3 次；ContextVar 11 次。
- 「入场值为 `None`」这一格分两种，**一种可判定、一种不可判定**：
  - **普通全局：不可判定** —— `write(None)` 与「本来就没装」是**同一次写入**，两种写法可观测后果完全相同（连变异都造不出差别）。故**不造锁**，造出来只能是假锁。（`write(prev)` 把 `None` 写回去会拆掉块内那一份，这是上面说的定式，不是缺陷。）
  - **ContextVar：可判定** —— 全局的「没装」就是 `None`，ContextVar 的「没设过」**不是** `None`（`get()` 抛 `LookupError`，只有 `get(None)` 返回 `None`）。补锁 `test_set_var_restores_the_unset_fact_not_a_none_value`：在空 `contextvars.Context()` 里自建前提（入场必然是「没设过」），块前块后 `LLM_CALLER.get()` 都必须抛 `LookupError`。
- **变异读数**（单文件基线 48 passed）：撤销写成 `write(None)` → 3 红；写成 `write(value)`（不还原）→ 1 红；撤销改成 `pass`（完全不还原）→ 2 红 —— 这三种**已有用例间接打红**（红在后面用例上、与成因无关），按「已被打红的不新增锁」不补。`_set_var` 改成快照式 `var.set(None)` → **0 红**，即上面那一格此前无人看住；补锁后同变异 → 恰好本锁红（1 failed / 48 passed）。
- **裁定·不做**：不给 `_Restored.__exit__` 加还原后自校验（`assert read() == prev`）。探针实测对现有 48 条零误伤（载体读口返回副本，故必须 `==` 不能 `is`），但它的判据「还原已生效」当前**没有可复现的缺陷形态**（还原是一次普通赋值），红源只能杜撰 —— 按项目尺度不加固。
- **普查**：同机制另一处 `tests/test_usage_identity_context.py::_LoopSubmitter`（快照 `get_loop_submitter` + 退出写回，入场可为 `None` 的格相同），**裁定不动**（不在 ContextVar 侧、不走 token）。不同形：`tests/test_alerting.py::clean_root` 是**增量摘除**（把自己挂的摘掉，不写回快照）；`core/embeddings.py` / `core/request_context.py` 与 `test_usage_identity_context.py` 的 `set`/`reset` 站点**都用 token** 还原，是正确形态。
- **余项收口（2026-09-24，Spec 123 步骤 3，`12268e0`）**：上面「裁定·不做」里那句「判据『还原已生效』没有可复现的缺陷形态、红源只能杜撰」**被本 spec 推翻**。要补的那一格不是「还原后读回 prev」（那确实是普通赋值），而是**写回之前**先核「此刻读到的仍是我装进去的那个」—— 这一格有真实形态：`_snapshot` 的读口可能返回**副本**，且块内**允许别人先改**（L4 的 `install_llm_gate`、L15 的载体登记表都这么用）。旧写法静默 `write(prev)`，会把别人的改动一并擦掉 —— 那正是「轮到本用例时全局是什么」不再由本用例决定（见条目开头那句）。补上后单文件当场 **7 处**报红，逐条判明是**误伤而非真缺陷**，分两类修正：
  - **读口返回副本**（L15 ×3）：容器语义如此，故比对由 `is` 改为 `==`（函数 / `None` 这类标量上两者等价，放宽只影响副本容器）；
  - **作用域指望别人来装**（L4 / `_boom`）：入场值由 `None` 改为「装配将要装的那一个」（`gate.geo_call_guard`）。若装配装的是**别的**函数，退出时当场报错 —— 这正是这一格的分辨力。
- **红源（现跑，2026-09-24）**：`test_snapshot_refuses_to_restore_a_value_someone_else_changed` —— 删掉 `_snapshot._restore` 里那条断言 → 恰好该锁红（**1 failed / 50 passed**），其余用例不察觉；该锁补前无人看住。**本载体不适用于「本作用域指望别人来装」的那种**：那类作用域归 `conftest.registered_globals()`（见 113 的收敛）。

**65. L12 扫描面尖角：只认 `git ls-files`，未入库的新文件看不见** —— 状态：**已修**（本步 C5）
- 现象（实测）：`web/llm_gate.py` 尚未 `git add` 时，L12 读数是 **3/2**（真值 1/1），而当时锁看着是**绿的** —— 扫描面看不见的文件等于豁免。
- 修法：扫描面改用工作区事实 `git ls-files --cached --others --exclude-standard '*.py'`，L10 / L12 / L13 **共用一个构造函数** `_production_py`（原先各写一份，其中 L10 用 `rglob`、L12/L13 用 `git ls-files`）。未跟踪文件的处置是**显式的**：`--others` 一律纳入，要排除就得写进 `.gitignore`（与全仓其他工具同一份声明），而不是在锁里悄悄跳过一个「git 不认」的文件。**不用 rglob 全盘**的理由也写进了 docstring：那会把 `.venv/`（11 964 个 `.py`）连同 `services/` 一起当生产代码扫。
- 判据：`test_scan_face_sees_untracked_workspace_files`（用例自带一个真的未跟踪 `.py`，`finally` 清掉）—— 这条不能靠读实现代替：`--others` 一旦被删回去，L10 的**主断言照样绿**（它只数已入库的两处）。**变异**：在 `web/` 放一个未跟踪的 `.py` 写 `LLMAdapter(api_key=...)` → L10 红（实测：命中 `('web/_stray_untracked.py', 3, '<module>')`）。

**66. retry 与 `finish_reason` 之间的 8 条序依赖** —— 状态：**已修**（2026-09-22，范围 B）
- 形态：`_parse_json_with_retry` 的截断自愈环（缺陷 2 的接回）里，「重修触发」「`truncated` 初值」「上限计数」「环内 `except` 的前置分支」之间靠**行序**成立，共 8 条；改任何一处的顺序都不报错，只改变行为（例如把 `truncated` 初值的那行提到环外，自愈会静默失效）。
- 原先不建锁的理由是「只能锁实现的行序（打桩形状）」。**本步订正这条理由的适用范围**：8 条里只有 O7 真的只有行序可锁，其余 7 条都有**可观测后果**（调用次数、第 2 次的 prompt 文案、最终异常类型与分档）—— 锁后果即可，不必锁行序。
- 修法（判据收口）：新增 `Distiller._truncation_evidence(exc, partial="") -> str | None`，全仓唯一一处判「是不是截断」（**`length` 且正文非空**，两个条件都必要），`_collect_stream` / `_chat_accounted` 两处改调它；环内两处 `except` 从「任何未完成终态都吞成截断」改成「确定性终态**原样上抛**」，配码与上屏交给 `web/server.py` 既有的 finish_reason 分档表（分档不在 core 里判）。收口顺带解决 `_collect_stream`（允许空正文）与 `_chat_accounted`（要求非空）的口径不一，取「`length` 且正文非空」—— 空正文没有可修的东西。
- 8 条的去向：
  - **O1** `truncated = upstream_truncated` 预置（Attempt 1 之前）→ `test_upstream_length_signal_heals_and_calls_llm_twice`：不预置则第 2 次退化成通用 prompt，红。
  - **O2** `_looks_truncated` 兜底（Attempt 1 的 `except` 内）先于 Attempt 2 的 prompt 选择 → `test_no_signal_still_guesses_from_text_shape`。
  - **O3**「合法 JSON 缺字段」支先于「截断」支 → **本步补锁** `test_shape_branch_beats_truncation_branch_when_both_signals_present`（上游 length + 合法 JSON 缺字段时，第 2 次必须是 schema 支）；变异「给前一支加 `and not truncated`」→ 该锁红，既有用例无一红。
  - **O4 / O5** `fix_truncated` / `retry_truncated` 先于解析 → `test_repair_cap_raises_truncation_error_not_format_error`（「超长」不能被退化成「格式异常」）。
  - **O6** 环内 `except` 的 incomplete 前置分支 → **随判据收口消除**：这一支原先自己判「是不是截断」并写 `truncated` / `last_error`，与 O4/O5 抢顺序；收口后它只做「确定性终态原样上抛」的早退，不再参与截断记账，序依赖消失。剩下的可观测后果（不白烧 Attempt 3、错误按 `content_filter` 分档）由本步新增的 `test_non_length_terminal_state_is_not_swallowed_as_truncation` 看住；变异「改回 `info is not None` + 吞成截断」→ 该锁红。
  - **O7** `set_current_attr("repair_stage", N)` 先于 return → **裁定不锁**：该值只进遥测，没有任何生产分支读它，锁它只能锁打桩形状（同条目 70 的裁定：没有专属红源就不造锁）。
  - **O8** 每次 try 前 `attempts += 1` → 同 `test_repair_cap_raises_truncation_error_not_format_error`（上限仍 3 次尝试、不多烧第 4 次）。
- 收口带来的**行为差**（非缺陷）：`_collect_stream` 原先「`length` 但累积正文为空」返回 `("", True)`，环内归到「超长被截断」→ `DistillError` **400**；现在按失败原样上抛 → 统一出口 `incomplete:length` → **502**。受影响调用方是全部走 `stream=True` 的 `_chat_accounted`（环内 `_repair` 与长输出合并支）；现有测试无覆盖该格（`test_distill_usage_accounting.py::TestStreamChannelAccounting::test_truncated_stream_records_one_estimated_entry` 喂的是非空片段，不受影响）。
- 「首调 502 / 环内 400」**保留**的理由：分档按**结论的来源**而不是文案关键词配码。首调上抛的是上游自己的终态（`kind=incomplete:length`），属上游档 502；环内是**本域**跑完 3 次后的结论（这份输入 + 这份配置就是装不下），抛 `DistillError` 走域档 400，文案是给用户的处置。两者不合并。

**67. `install_log_collector` 的守卫与 3.12 `addHandler` 的去重重复** —— 状态：**已修**（2026-09-22）
- `core/log_collector.py` 的 `if _handler not in root.handlers: root.addHandler(_handler)` —— 本机实测（Python 3.12.10）`logging.Logger.addHandler` 自身的实现就是 `if not (hdlr in self.handlers)`，**守卫是多余的**，删掉行为不变。复算命令：打印 `inspect.getsource(logging.Logger.addHandler)`。
- **修法**：两行 → `logging.getLogger().addHandler(_handler)` 一行，注释写明去重由 `Logger.addHandler`（含它自己的 `_acquireLock()`）保证。不只是「等价」：我们的守卫在锁**外**，两个线程可以同时通过，去重实际一直靠 stdlib 兜住 —— 删掉后这一段才是原子的。
- **红源读数**：把 `addHandler` 绕成 `root.handlers.append(_handler)`（去重不再成立这一形态）→ `tests/test_log_collector.py::test_install_log_collector_idempotent` **红**（1 failed / 6 passed）；不绕（= 本步修法）→ 7 passed。既有锁对**这条性质**有分辨力（分辨不了「谁在去重」，本就不必分辨），故**不新增锁**。
- **普查**：`core/alerting.py:173` 形状相同但**机制不同** —— 它按**类**去重（`any(isinstance(h, AlertHandler))`，防「换个收件人再挂一个」），`addHandler` 按**身份**去重，删了会真挂两个 → 不并入。`web/deps.py:15` 与 `web/server.py:24,28` 的 `sys.path` 守卫，`list.insert` 不去重，守卫**承重** → 不同形。

**68. `update_user_api_config` 在无用户行时静默不写** —— 状态：**已修**（`f7fd0b6`，2026-09-23）
- 形态：`storage/sqlite_store.py`（PG 侧同）里写的是 `UPDATE user_secrets SET ... WHERE user_id = ?` / `UPDATE users SET ... WHERE id = ?`，**不检查受影响行数**；用户行不存在时 0 行匹配，`await conn.execute` 不报错、函数正常返回，调用方 `update_api_config` 回 `{"ok": True}`。
- **这是凭据路径上的「失败吞成成功」**：用户以保存成功为信号，而 key 从未落库（下一轮请求照旧回落全局 key 或 503）。当前的「空白字段不写」语义是**刻意的**，但「整行不存在也不写」不是 —— 两者共用同一个静默出口。
- **修法**：照抄 `set_user_role` 的现成先例 —— **执行过、却匹配 0 行**的语句 `raise ValueError`，路由层把它翻成 **404**（不是泛化的 500：这是「资源不存在」，不是「服务出错」）。用户注册时必建 `user_secrets` 行，故 0 行 ⇔ 用户不存在，不需要额外查询。
- **判据 / 红源**：`tests/test_api_config_unknown_user.py::test_unknown_user_raises`（store 层）与 `::test_unknown_user_is_404_not_ok`（接口层）；变异「去掉 0 行检查」→ 两条同时红。**「空白字段不写」的原语义由 `::test_blank_fields_write_nothing_and_do_not_raise` 与 `::test_blank_fields_on_unknown_user_also_do_not_raise` 反向锁住** —— 「语句没执行」与「执行了 0 行」的区别正是这次修复的落点，只锁前者会漏掉本条。

**69. 管理员保存无 key 配置返回 200、全站转 503、响应无信号** —— 状态：**已修**（`17626d6`，2026-09-23）
- 形态：`reset_llm_and_dependents` 未配置时把 `_llm` 置 `None`（口径统一，是 C4 的裁定）—— 但保存接口照旧回 200，而**全站随后转 503**，响应体里没有任何提示。运维看到的是一次成功保存 + 随后一片 503。
- 不处置的理由：本案射程是门与解析，这条属于**保存接口的可用性反馈**，另开议题（与 68 同一族：**失败/降级的信号没有传到调用方**）。
- **修法**：保存响应体加 `llm_available: get_llm() is not None`（用 `deps.py` 里现成的 `get_llm()`，不新增抽象）。**保存本身不阻断** —— `None` 是 C4 已裁定的既定结局，管理员可能正是有意清空 key。
- **判据 / 红源**：`tests/test_settings_config_llm_available.py::test_no_global_llm_reports_false_and_still_saves`（无 key → 200 且 `llm_available == false`）、`::test_global_llm_present_reports_true`（有 key → true）；变异「字段恒 `True`」→ 红。
- **前端半作废（对 spec 的订正）**：spec §2.3 / §5 原要求前端也消费这个信号；实查 `POST /api/settings/config` **无前端调用方**（管理员直接调接口），用户裁定「不新增调用方」，故前端用例作废、只留后端锁。交付报告里已声明这是对 spec 的订正。

**70. 门判据收敛（判 `llm` 不判 `distiller`）没有专属红源** —— 状态：**已裁定·不设锁**（2026-09-19）
- C4 返工把 `/start` 的门从 `get_distiller(llm) is None` 改成 `llm is None`。这是**生产行为等价**的收敛（`get_distiller(None) is None`），它的价值是**消除分叉的可能**，不是修一个可观测的错。故没有变异能只打红它 —— 能打红它的变异（把 `get_distiller` 换掉）打红的是 L1/L2。
- **裁定：不为它造锁。** 造出来只能锁**打桩形状**（「测试里把 `get_distiller` 换成别的」），那是测试命题不是生产命题。**靠 review 不靠锁**，此条即该裁定的留痕。

#### E. §5 越界项（各记一条，**另开议题**，本案不处置）

**71. web/routers 下就地 `except Exception` 只让 LLM 失败的两处放行，其余拒绝变 500** —— 状态：**已修**（2026-09-24，Spec 71 A-2 两段：首段 `1ca2329` + `97c2934`；尾段 `7dad544` + `5af404b` + `d5b0c92`）
- 其余路由里的拒绝会变成 **500**：调用照样被挡住（门在出站前），但状态码错。与缺陷 38 同族。
- **旧读数「147 处」是陈旧数**（对不上任何现算口径）；按下面这条判据在**本批动的基线**上现跑＝ **129 处**。
- **判据：字面串 `except Exception` 的出现次数**，不是「文件计数之和」—— 后者随文件数变化，读数就换了含义（§四「聚合统计必须写明口径」）。命令：`git grep -o 'except Exception' origin/main -- 'web/routers/*.py' | wc -l` → **129**；同一条命令去掉 `origin/main`（量本分支）→ **95**。
- **本批处置（33 处）**：删掉那些 `except Exception` 且 body 里 `raise HTTPException(500..599)` 的包装，失败交给 `web/server.py` 的统一出口配码取文案。**503 那 1 处有意保留** —— `web/routers/distill.py::_distill_start_impl` 落库失败拒绝启动是**契约**（`tests/test_distill_task_api.py::TestCStartRefusesOnDBFailure` 钉着），只把 `print` 换成 `logger.error(..., exc_info=True)`（`HTTPException` 不经过全局处理器，不记就只剩 stdout）。
- **两类宽 except 不删**（删了是回归，不是「统一」）：`except ValueError → 400`（实参是**本仓为人写的**用户/属主校验文案，没有 `user_message` 可回落，塞进统一文案会把用户要的信息删掉）；`except DistillError: raise`（**必须排在 `except ValueError` 之前** —— `DistillError` 是 `ValueError` 的子类（`core/distiller.py:190`），删了会被后者接住、把 `user_message｜ops_detail` 整条上屏，缺陷 38 当场复现）。
- **尾段已把余量收净（2026-09-24）**：`chat.py`（3）+ `history.py`（12）+ `web/server.py`（2）＝ **17 处**一次删净（措辞统一为「服务器内部错误，请稍后重试」），`web/**` 现跑「宽 `except` 分支体内改抛 5xx」＝ **1 处**，即登记的 `_distill_start_impl` 503 豁免。**route lock 已就位**：`tests/test_router_unified_exits.py::test_no_catch_all_5xx_wrapper_in_web` —— 扫遍 `web/`（不只 `web/routers`），按 **(文件, 函数名)** 列表**相等**比较（不用行号，行号会漂），故「新长出来的包装」「豁免失效」「在豁免函数里又加第二处」三种都被拦。上面那句「本批没有 route lock」说的是首段，尾段补上了。
- **红源**：`tests/test_router_unified_exits.py` 两条（`at_reply` 门拒绝 → 403 + 门给的原话；`inter_node` 失败不上屏原文、留一条带堆栈的 ERROR），各配一条变异（把包装包回去）逐条打红；`TestCStartRefusesOnDBFailure` 补的 caplog 断言由「改回 `print`」打红。

**72. 活会话持有旧 key 实例：`clear_user_llm_cache` 不重建会话** —— 状态：**已修**（`91c0a8b` + `df01bd2`，第 3 步，2026-09-23）
- 形态：用户换 key / 换 model / 换 base_url 后，新请求解析到新实例，但**内存里已有的会话**仍拿着旧实例（`get_sessions()` 里的 engine 持 `llm`）。
- **这不是安全缺口**（原状态行称「撤销场景下是安全缺口」，已就地订正）：`update_user_api_config` 的 docstring 明写「空字段不写入」（`storage/sqlite_store.py`），即**用户根本没法通过 API 清掉 key**；「换 key」是把该用户的旧 key 换成他自己的新 key，旧实例拿的不是别人的凭据，也不存在「撤销」这个动作。真实后果是**功能性的**：换 key / 换 model 之后，已有的活会话**不生效**，要等会话重建才切过去。
- 修法三半：**（1）换连接要连预算一起换** —— `ContextEngine.set_llm(llm)` 按新模型重算预算（抽出 `_apply_budgets(model)`，构造与换 LLM 同一处口径），`ChatEngine.set_llm(llm)` 同时换 `self.llm` 与上下文引擎；**（2）保存即换活会话** —— `web/deps.py::refresh_user_llm(user_id)` 找出该用户在一对一 / 群聊两张内存表里的活引擎逐个 `set_llm`，`web/routers/auth.py::update_api_config` 存完库调它；不踢会话、不重建 RAG。换连接失败**不改写**这次保存的结果（配置已落库、缓存已清，下个请求自会取到新实例，活会话只晚一轮）；**（3）在飞的那一轮归发起它的连接** —— 出站与记账取同一个局部 `llm`（`_try_record_usage(action, *, llm)` 参数必填，调用点先绑 `llm = self.llm` / `engine.llm` 再 `await`），`await` 期间换了连接也不会把这轮的用量记到新实例头上。
- **单进程前提**（写进 `refresh_user_llm` 的 docstring）：`_sessions` / `_group_sessions` 是本进程的内存表（`web/server.py` 的 `uvicorn.run` 不带 `workers`），故「活会话」就是这两张表。**将来要多 worker**，得改成「按配置版本号每轮重新解析」—— 跨进程换不了别人手里的实例。
- 与缺陷 61（F6）是同一形态的两半：61 修的是「geo 判定不跟着会话走」，这条修的是「**凭据**不跟着会话走」。`ChatEngine` 构造后由外部写入的会话状态字段另记 **96**（不属本条，见该条）。
- 红源：`tests/test_live_llm_swap.py` 的 T1 / T2 / T3a / T3b（换连接与在飞记账）与 `tests/test_live_llm_refresh.py` 的 T4–T10（缓存口径、活会话、群聊、保存失败不撤销、保存不过 preflight、热重载只换全局用户）。每条断言各有一条专属变异（逐条真跑、跑完还原，逐条打红了它点名的那条用例）。

**73. `core/embeddings.py` 与 mem0 的出站不经过 adapter，是否需要 geo 约束待定** —— 状态：**已修**（与代码/测试同属一个 commit，2026-09-23）
- 这些出站拿的是 `base_url` 之外的嵌入端点（DashScope），**没有**经过 `adapters/llm_adapter.py`，故**不受调用点门约束**。D3 说「请求内所有 LLM 使用都受 geo 门约束」，实现在 adapter 上 ⇒ 这类出站落在门的射程外。
- 是「门该不该覆盖嵌入」还是「嵌入另有一套策略」，是**决策**不是修法，故另开议题。
- **本次处置（2026-09-23，用户裁定「修机制不修实例」）**：
  - **不把嵌入搬进 adapter**（那要造第二套 client / 会话），而是让它走**同一个判定**：`core/embeddings.py::_call_api` 出站前调 `adapters.llm_adapter.check_outbound_guard(self._base_url)` —— 与 `LLMAdapter._before_call` 共用的**唯一**一处实现。门没注册（独立进程）就放行，行为与改前一致。
  - **判的是「生效的 base_url」**（含 `EMBEDDING_BASE_URL` 覆盖），与 LLM 侧判「用户配置里那个 URL」同口径：判真出门的东西，不是「本该是什么」。故判定放在 `_call_api` 而**不是** `__init__` —— 实例是被缓存的，缓存的是实例、不是这一次的判定（同 `preflight()`「缓存命中也判」）。
  - **保存侧同判**：`update_api_config`（`web/routers/auth.py`）现在也把 `DASHSCOPE_BASE_URLS[req.embedding_region]` 加进判定目标，用的是**同一个** `geo_refusal` + `emit_geo_block_audit`（不另写策略）。空串 = 「这次不改这个字段」，不判。只堵出站不堵保存，用户会「存得下、跑不通」，在保存那一刻看不到理由。
  - **请求外的两处显式声明**：`mcp_server/server.py` 的 `call_tool`、`scripts/rebuild_384_collections.py` 的重建循环各包一层 `with system_llm_context():` —— 这两处没有请求身份，不声明就 fail-closed。
  - **类型不可被吞**（同一步的机制修补）：`_embed_impl` 原本把 `_call_api` 的一切非审核类异常包成 `RuntimeError("百炼 embedding 失败…")`，门拦下的会被**吃掉类型** —— `/test-embedding` 的 403 路径与 MCP 的 fail-closed 都会失真。故在宽 except 前加一句放行。放行用的是新基类 `adapters.llm_adapter.OutboundRefused`（`LLMCallRefused` 与 `web.llm_gate.LLMCallerMissing` 的共同父类，理由是两者同属「这不是一次失败的请求，是一次没发生的请求」）—— 单一基类是为了让 core 用一句 except 覆盖两种拦法**而不 import web**（L13）。`/test-embedding` 的宽 except 前同理放行 `LLMCallRefused`。
  - **红源**：`tests/test_embeddings.py::TestOutboundCallGuard` 两条（① 每次判 + 判的是生效 URL；② 无身份 fail-closed + `system_llm_context()` 放行）、`tests/test_embedding_test_endpoint.py::test_T13_saving_intl_is_judged_by_the_same_geo_rule`（保存 intl：境内 403 + 一条审计、境外放行）。
  - **变异（逐条真跑、跑完按字节还原）**：① 删 `_call_api` 的门调用；② 门挪回 `__init__`；③ 判定改看 region 表；④ `_embed_impl` 恢复宽 except；⑤ 保存侧关掉整段 geo 检查；⑥ 保存侧只判 `base_url`、不判 `embedding_region`；⑦ 保存侧被拦不记审计 —— 7 条全部打红，无幸存。③ 只由「判的是哪个 URL」那条钉（`system_llm_context()` 在 `geo_refusal` 之前就 return，选哪个 URL 都不影响身份的两种走向）。
  - **未做/边界**：`AGENTS.md` 旧条目 26 里的常量名（`:446`）是历史记录，不改；`tests/census_llm_call_contexts.py` 的归属结论已随本条反转，就地加 v4 订正（不改 §二/§三 的日期快照表）。
- **2026-09-23 查实的事实（缺陷 74 的 S0 交付，供本条决策；判定依据已被上面的「本次处置」收编，此处保留作引用出处）**：
  - 嵌入端点**可以**被配成国际站：`core/embeddings.py` 的 `DASHSCOPE_BASE_URLS` 里 `"intl"` → `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`，由 per-user 的 `users.embedding_region`（默认 `'cn'`）选；另有一条全局 env 覆盖 `EMBEDDING_BASE_URL`（`core/embeddings.py`，优先于地域）。出处：阿里云 Model Studio 文档「OpenAI compatible - Embedding」与「错误码」—— 国际站 key 与中国站 key **不能跨地域混用**，配错返 401 `invalid_api_key`（正是 `_EMBED_FAILURE_HINTS[401]` 那句「或与所选地域不匹配」的出处）。
  - **门看不见这条路**，三处可核：① 该出站在 `core/embeddings.py` 自建 OpenAI client，不经 `adapters/llm_adapter.py`，`geo_call_guard` / `geo_refusal` 都不在链上；② `_DOMESTIC_LLM_HOSTS`（`web/geo_guard.py`）只列 `dashscope.aliyuncs.com`，**不含** `dashscope-intl.aliyuncs.com`，而白名单是后缀匹配、`intl` 那条配不上；③ `update_api_config`（`web/routers/auth.py`）只 geo 检查 `req.base_url`，`req.embedding_region` **原样落库**，`/test-embedding` 连 geo 检查都没有（只校验 `region in DASHSCOPE_BASE_URLS`）。
  - 即：国内用户把 `embedding_region` 存成 `intl`，嵌入出站即到新加坡，门的任何一层都不拦。

**74. embedding 配置读取有 5 处完全相同的写法（含变体约 8 处）** —— 状态：**已修**（2026-09-23，收敛成唯一出口 `resolve_embedding`，见「本次处置」段）
- 同一份「读 `embedding_key` / `embedding_region` 并给默认值」的逻辑散在多处，与缺陷 35/62 同族（同一件事抄多遍，漏改一处是静默的）。本轮的解析出口收敛（`get_user_llm`）只覆盖了 LLM 那一条，**embedding 那条没并入**。
- **本次处置（2026-09-23，用户）**：
  - **普查更正**：逐处数下来是 **11 处**（不是标题里的 5 / 约 8），分三族 —— A 族 5 处（`chat.py`、`history.py`、`group.py` ×2、`distill.py` 的会话段；形态 `if key: … get(region, "cn")`）、B 族 4 处（`distill.py` 的四个蒸馏端点；形态 `.get(k, "")`）、C 族 2 处（`mcp_server/server.py`、`scripts/rebuild_384_collections.py`；走 env）。**判据**：`git grep -n 'embedding_key\|embedding_region'` 取「读配置并给默认值」的写法，排除写完就走的写入面。
  - **出口**：`web/llm_resolution.py` 新增纯函数 `resolve_embedding(config, *, env_key="", env_region="cn") -> EmbeddingResolution`（`source` ∈ `USER` / `GLOBAL_ENV` / `UNAVAILABLE`）。与 `resolve_llm` **同一文件、同一套形状**：不缓存、不做 geo、不做 IO；进程环境由调用方以实参给（`resolve_llm` 的 `build_user`/`get_global` 同理），故该模块仍不 import `os`。不新建文件、不新建注册表。
  - **默认值统一取 `"cn"`，不是 `""`**：`DASHSCOPE_BASE_URLS` 里没有空键（`""` → KeyError）；`DashScopeEmbedding.__init__(region="cn")`、两个 store 的读侧 `row[4] or "cn"`、建表默认 `users.embedding_region TEXT DEFAULT 'cn'` 都已经把 `"cn"` 当作这个字段的缺省。B 族那个 `""` 是**隐性不一致**、不是活缺陷 —— store 读侧先兜了一层，故平时打不响；一旦真流到 `core/rag.py` 的 `config.get("embedding_region", "cn")`（键**在**而值为空，`.get` 不给默认），就是 `DASHSCOPE_BASE_URLS[""]` 当场 KeyError。
  - 同批删掉 `web/deps.py::get_rag_config` 的两个死参数（`embedding_key` / `embedding_region`）：从加进来起**没有一个生产调用点传过值**，三个调用点都裸调再自己手搓合并 —— 出口形同虚设，与缺陷 111 的 `text` 同形。现在它只回 `dict(_rag_config)`。
  - **边界**：`core/rag.py` 的最终消费点、`web/routers/auth.py` 的 `/me` 展示 与 `/test-embedding` 排障面**未并入** —— 前者是消费不是分发；后者 region 以请求实参优先，语义不同。
  - **红源**：`tests/test_embedding_config_single_exit.py::test_resolve_embedding_contract` —— 出口契约表：空 config → `UNAVAILABLE`/`("", "cn")`；用户 key → `USER` 且 region 缺省 `"cn"`；**`embedding_region: ""` 必须归一到 `"cn"`**（病灶本尊）；`env_key` → `GLOBAL_ENV`；用户 key 压过 env key 且 region 归用户侧缺省。**先跑变异再定锁**：把 `group.py` 的注入点改回手搓并给错默认值 `"intl"`，相关的 7 个测试文件 **204 passed**（含 `test_rag_unusable` / `test_ownership_404` / `test_group_save_failure` / `test_llm_access_gate` 等）—— 现有测试打不红，故补此锁。
  - **审计删减（2026-09-23，用户）**：初版另带「判据 1 源扫描（手搓取值只许落在三处白名单）+ 变异自测」，**已删**。理由：锁写法不锁行为 —— 某处改回手搓但默认值写对**不是缺陷**（写错由上面的契约与出口兜住），而扫源码位置要求维护一份白名单，白名单自身还有盲区（单引号写法、下标读取漏得掉），维护成本压在判据可信度上。现锁只钉出口的行为。

**108. 独立卡片会话不登记属主，`_ensure_session` 的属主门整段被跳过** —— 状态：**已修**（`dbdbf9c` + `b728ca8`）
- `/api/distill/start_session` 不带 `text_id` 的「独立卡片」分支原先手搓 `ChatEngine(...)` 并直接写 `sessions[session_id] = {...}`，条目里**没有 `user_id`**。而 `_ensure_session` 的命中校验写成 `if session.get("user_id") and session["user_id"] != user_id` —— 「没登记」使整段短路放行，**任何登录用户拿到该 `session_id` 都能用**。
- 修法两半：`dbdbf9c` 让该分支改走**唯一**的建会话点 `TextManager._create_session(rag=None)`，条目自带 `user_id`；`b728ca8` 把命中校验的前置 `session.get("user_id") and` 删掉 —— 条目没登记属主时 `None != user_id` 判「不是你的」。**失败即关**：将来再冒出一个忘了登记的构造点，后果是**属主本人 404**（响亮、当场暴露），而不是所有人放行（静默）。
- 红源：`tests/test_ownership_404.py::TestOneToOneSessionOwnership::test_T6_unregistered_entry_fails_closed`（T6 是补写的 —— T1 在「所有构造点都已登记」之后**打不红**，见 §四「每条断言要有专属红源」）。

**109. 群聊内存命中只判「删没删」，不判「是不是你的」** —— 状态：**已修**（`dbdbf9c`）
- `/{group_id}/send` 与 `/{group_id}/broadcast` 各写了一遍「内存取 → 取不到就重建 → 还取不到 404」。内存命中后 `get_group_session_owned` 对非属主返回 `None`，而调用处只判 `if session_rec and session_rec.get("deleted_at")` —— `None` 直接放行。
- 修法：`GroupSession` 增加 **keyword-only、无默认值**的 `user_id`；两处读取合成一个 `_get_owned_group(group_id, user_id, storage)`（内存命中也要 `group.user_id == user_id`，否则按属主重建）。非属主与「不存在」因此**同码同文案**（`群聊会话已过期，请重新创建`）。`deleted_at` → 410 的判断不动（管的是「已删除」，与属主无关）。
- 红源：`tests/test_ownership_404.py::TestGroupSessionOwnership` 的 T3（`/send`）与 T4（`/broadcast`）。

**110. `start_session` 无 key：独立卡片 200 建出一个死会话、文本分支 500** —— 状态：**并入 107**
- 同一事实（没配 key 时 `start_session` 的行为）、同一修复提交（`dbdbf9c` + `7a5992a`），内容已整体并入 **107**，此处不再单独维护 —— 同一个事实记两条，改状态必然只改到一处。

**111. `_create_session` 死参数 `text`** —— 状态：**已修**（`5947d18`）
- `text` 是**第一个位置参数**，函数体从未引用（正文经 `card` 与调用方的 RAG 进引擎）。**死参数为错位留出空间** —— 与缺陷 G 那次的落脚点同形：调用点按位置多传一个实参就有地方可落、静默装错值。删掉后 `card` 成为唯一的位置参数，多传的位置实参当场 `TypeError`。
- 6 个调用点同步去掉首个位置实参；`tests/test_create_session_kwonly_lock.py` 的 `_POSITIONAL_OK` 收成 `("self", "card")`，并把 `text` 加进「死参数不许回来」的签名断言（**不放松锁的命题**）。
- 红源：把 `text` 加回签名 → `test_dead_params_that_gave_the_overrun_somewhere_to_land_stay_deleted` / `test_optional_params_are_keyword_only` / `test_keyword_call_binds_each_name_to_its_own_value` 三条红。

**112. 全量里约 20 条 `PytestUnhandledThreadExceptionWarning`（aiosqlite `Event loop is closed`），条数与命中用例每次不同** —— 状态：**已裁定·不修（SQLite 专属）**（2026-09-24 裁定）
- 本轮未引入、本轮不修：需要单独查线程 / loop 生命周期，与本轮「会话属主」议题**无共同成因**。记在此处，以免下次当成新问题重查。
- **补充（2026-09-24，Spec T1）**：`aiosqlite` 只在 SQLite 后端下起线程；T1 之后测试一律连 PG，这条警告在常规全量里应当不再出现（只有直接构造 `SQLiteStore` 的那几个 SQLite 专属用例仍走 aiosqlite）。再出现就说明有新代码把测试引回了 SQLite，按新病例查，不是本条复发。
- **分支 CI 实数（2026-09-24，Spec 123 步骤 4，`12268e0`，run `35993735059`）**：本条**没有消失**，按现数如实记 —— `Event loop is closed` 共 108 行：**gate job 40 行（20 处）/ sentinel job 68 行（34 处）**。来源逐条归到 `tests/test_demo_gate.py`（`PytestUnhandledThreadExceptionWarning: Exception in thread Thread-N (_connection_worker_thread)`），该文件在 `:121` 直接构造 `SQLiteStore` —— 正是上面「直接构造 SQLiteStore 的 SQLite 专属用例」那一类留门，**不是**「新代码把测试引回了 SQLite」。两个 job 的条数不同（20 / 34），与条目开头「条数与命中用例每次不同」的形态一致。该文件不在 Spec 123 的改动面内，故**只记不修**，状态维持「SQLite 专属，按主次不修」。
  - **对照读数**（同口径、上一个分支 run `35941780534`，`990b7dcc`）：`Event loop is closed` gate **52** 行 / sentinel **24** 行。两组数同量级且互有高低（与本 spec 无关的正负抖动），故不把任何一侧的差值归给本次改动。

**113. 全量跑时 T2 额外报 `coroutine ... was never awaited`（只跑 `tests/test_ownership_404.py` 不出现）** —— 状态：**已修**（`8dfb1f8`；2026-09-25 生产侧配对注销收口，见末条）
- 成因：`tests/test_llm_access_gate.py::test_l11_app_installs_the_production_guard` 用 `with TestClient(server.app)` 触发真实 lifespan，以证明「生产经 lifespan 装上了门」。**启动同时也注册了 `core.scheduling` 的投递器**（`set_main_loop`），而该用例只还原了守卫 —— 退出后 loop 已被 `TestClient` 关掉，注册却还指着它。
- 之后第一个走 `submit_to_main_loop` 的用例把协程投到死 loop 上；`core/chat_engine.py` 的宽 `except` 吞掉异常，于是它以 `coroutine ... was never awaited` 的形式飘在**别的**用例头上（最难查的那种串味）。
- 修法：把启动那一段包进本文件既有的 `_snapshot(S.get_loop_submitter, S.set_loop_submitter, before)` 惯用法，并在块后断言已还原。**不新建全局夹具** —— 还原责任归泄漏用例自己（与 `_Restored` docstring 的「还原，而不是置 None」同理由）。
- 红源：撤掉那层 `_snapshot` 包装 → `test_l11_app_installs_the_production_guard` 的最后一条断言红。
- **已知边界（2026-09-22 审计）**：**lifespan 注册的守卫与投递器没有配对注销** —— 生产关停路径不撤销这两处注册。目前全仓**只有 `test_l11` 会启动生产 lifespan**，两个注册都由测试侧的 `_snapshot` 隔离，故影响面就这一处；测试侧隔离进程级注册也正是本仓既定机制。
- **收敛时机**：**出现第二个启动 lifespan 的用例时**，再把这套还原收敛成统一机制。在那之前不动 —— 为一个假想的用例去给 `deps` 加注销接口、改生产关停流程，规模不相称；且「关停后仍在跑的线程再投递，该走死 loop 报错还是走 `core.scheduling` 的回退语义」需要单独调研，不能夹在收尾里。
- **收敛（2026-09-24，Spec 123 步骤 3，`12268e0`）**：上面写明的触发条件**已成立**，本条因此复发并收敛。触发者是 `tests/test_message_backfill.py::test_C9_shutdown_backfills_the_queues`（`a275beb` 那条触发记录）—— 第二个启动 lifespan 的用例，而且它按旧写法（`async with server_mod._lifespan(...)`，注册后不还原）**恰好复现了本条**。还原收敛为 `tests/conftest.py::registered_globals()` 这一个入口：进入时快照「主 loop / 投递实现 / 调用守卫」三者并把两处注册清成未注册，退出时**先核再还原**（一处都没装上 = 这段没走到装配那一步，当场报错，不留假锁）。两种用法共用它 —— 包住生产 `_lifespan`（C9、l11），或作测试 app 的 lifespan、在其中 `deps.set_main_loop(当前运行 loop)`（`test_message_backfill._client`、`test_ownership_404._make_client`）。l11 自己那层 `_snapshot` 随之删掉；「lifespan 注册的守卫与投递器在生产关停路径没有配对注销」这条已知边界**不变**（本步收敛的是测试侧还原）。
- **复现命令（现跑，2026-09-24）**：`pytest "tests/test_message_backfill.py::test_C9_shutdown_backfills_the_queues" "tests/test_ownership_404.py::TestOneToOneSessionOwnership::test_T2_independent_session_owner_can_chat"` → `never awaited` **0**（改前 **4**）。分支 CI 侧同一形态：基线 run `35941780534`（`990b7dcc`）里 `tests/test_ownership_404.py::…test_T2_independent_session_owner_can_chat` 挂着 **never awaited**，run `35993735059`（`12268e0`）里它已消失。
- **红源**：把 C9 改回直接 `async with server_mod._lifespan(...)` → `test_C9_shutdown_backfills_the_queues` 的最后一条断言（`_registrations() == before`）红（1 failed / 14 passed）。
- **生产侧配对注销收口（2026-09-25，Spec observability E 段步骤 9）**：上一条收敛只落在**测试侧**，「生产关停路径不撤销注册」这条已知边界当时写明「不变」。本步把它关掉：每个 `install_*` / `set_main_loop` 返回自己的撤销函数，`web/server.py::_lifespan` 用 `contextlib.AsyncExitStack` 收集，`yield` 之后、既有停机关停动作（取消后台任务、flush 队列）**之后**逆序执行 —— 那些动作要用投递器，故不能排在撤销之后。
  - **撤销语义统一为「写回装配前的状态」**：`set_main_loop` 写回**装配前那一个**（loop 与投递实现一起写回），守卫（`install_llm_gate`）写回装配前的守卫 —— 生产上装配前就是未注册，故两者这时都等价于「撤成未注册」。一律写 None 会把**外层**那份注册抹掉，而测试里生产 lifespan 确实会被套在测试 app 的 lifespan 之内跑（C9）。
  - **`init_error_reporting` 的撤销是 `ExitStack.push` 的退出函数，不是 `callback`**（唯一一处）：回调拿不到正在冒出的异常，`validate_*` 一抛就变成「先 flush + close，异常这才冒出 lifespan」，而这条出口排首位正是为了收到启动期自身抛的错。退出函数先 `capture_exception`（`CancelledError` / `GeneratorExit` 除外 —— 关停与收尾不是故障）再 flush + close，不吞异常。红源：把 `push` 换回 `callback`（撤销不带异常信息）→ `test_startup_failure_is_reported_before_the_client_closes` 红（1 failed / 5 passed）。
  - **测试侧随之收窄**：`conftest.registered_globals()` 不再包住生产 lifespan（C9、l11 改掉，改由生产 lifespan 自己还原；C9 保留 `_registrations() == before` 作为回归锁），只剩「测试 app 的 lifespan」这一种用法。`test_error_reporting.py` / `test_stdout_logging.py` 里为绕开本条而临时加的 `restore_app_lifespan_globals` 夹具同批删除 —— 它与 `registered_globals()` 补的是同一个根因。
  - **红源（现跑）**：去掉任意一项撤销 → `tests/test_lifespan_undo.py::test_lifespan_undoes_every_process_wide_registration`、`tests/test_message_backfill.py::test_C9_shutdown_backfills_the_queues`、`tests/test_llm_access_gate.py::test_l11_app_installs_the_production_guard` 三处齐红（实测 **3 failed / 1 error**）；还原后三处齐绿（**3 passed**）。变异取两处：`deps` 的撤销写死 None、`install_llm_gate` 的撤销换成空操作。

### 四、验证纪律

- **基线数字现跑现取**（测试通过数、函数签名）：禁止引用上一轮结果或凭记忆。引用代码一律用符号名（函数/常量/测试名），不写行号——行号随改动漂移且无测试报警
- **台账状态行不是事实，是上一轮的记录** —— 引用「某条未修 / 仍是 X」之前必须核 commit 历史。`git log` / `git show` 是权威；缺陷表、清单、README 的状态行只是**写下的那一刻**的快照，会滞后于实际。案例：缺陷 22 已由 `ac2692f` 修掉，状态行却仍标「未修」，被当作遗留报了出去 —— 漏记的动作只有一个：**只读了状态行，没核 `git log`**。这是上一条的**同源反面**：上一条说「别引用上一轮跑出来的**数字**」，这一条说「**台账本身也是上一轮的结果**」。判据：任何「某条仍未修 / 仍是某形态」的结论，落笔前跑一次 `git log --grep=<关键词>` / `git show <sha>` 核验；核不到、或状态行与历史打架时，**以历史为准并就地订正台账**（订正要在条目里写明「原状态行滞后」及原因，否则下一个人会再踩一次）。**同一动作也适用于本文件的其它清单**（如 §五 工具现状）：清单是索引，不是证据。同族第二案例：`docker-compose.local.yml` 头部自称「这份文件不要提交 git！」，实则**早已入库**（`git ls-files` 命中，`4e69615` 引入）—— **注释也是写下的那一刻的记录**，与状态行同理；判据相同：以 `git ls-files` / `git log` 为准，不以文件自己怎么说为准。已记账，用户裁定**不修**（2026-09-14）。**同族第三案例**：`.claude/settings.local.json` 白名单里那条凭据前缀 —— 它记录的是「**有人这么打过这条命令**」，不是「**这条命令成功过**」。修 33 时实测那个口令**从未生效过**（`FATAL: password authentication failed for user "charsim"`；卷 `character-distill_pg_data` 建于 2026-06-25，容器建于 2026-08-17 且日志显示 init 被跳过，即那条命令打在了一个已存在的卷上），真值最后从两个已退出容器的 `.Config.Env` 里找回（详见缺陷 33）。判据与前两案例相同：**以「它是否真的成立」为准，不以「有那么一份文本」为准** —— 且这一例更阴：前两例是「文本说错了」，这一例是「文本说了一件从没发生过的事」
- **引用一个数字之前，先问它是核过的还是抄来的 —— 「未复算」永远不是结案理由**（上一条在**数字**上的实例，两条并列）。判据：**文本里出现的数字，有没有对应的现算来源？** 有现算来源（命令 / 配方 / 判据）的是**配方**，它必须**当下为真**；只有一段带日期的读数、没有产出它的方法的，是**记录**，它只对**写下它的那棵树**为真。处置只有三种：① **核过且仍相符** → 数字不动，但**标出它属哪一类**（免得下一个人重新判）；② **已变且属记录** → 它本就该冻结，补一句「这是某次普查的读数」；③ **已变且属配方** → **它真的烂了，当场改掉** —— 保留结论，把实测值与日期移进括号作为观测（口径见缺陷 43 的遍历方式那条）。**「没核过」本身要写进条目，不许悄悄归进「只记不修」** —— 未复算不是「不该修」的理由，是「还没查」。本文件两个实例：缺陷 18 的「带自定义状态的异常类共 7 个」是**配方**（现算来源 `tests/test_exception_pickle_lock.py::_census`，2026-09-17 复算仍为 7）；缺陷 38 的「267 处」是**记录且已不可复算**（产出它的脚本从未入库，按它自己写的判据重建四种读法都对不上）。**由此立一条配套规矩：被台账引用的数字，产出它的脚本与原始产物必须入库**（§五「证据产物一等化」同此理由）—— 否则下一个人拿到的不是「数字过期了」，而是「这个数永远核不了」
- **「全仓写死的实测数字」这件事做不出通用判据 —— 承认边界，好过凑一份自己会腐烂的清单**（与上一条同一句判据，落在这件事上的结论是「做不出」）。实测（2026-09-17，`AGENTS.md` + `tests/**/*.py`，四种划法各自误伤多少）：① 形状划法「数字 + 单位（个 / 条 / 组 / 桶 / 倍 / × / passed / s / ms…）」命中 **812** 处；② 再排除「该行带日期」**721** 处；③ 再排除「该行带现算来源标记（`git grep` / 现跑现数 / 判据命令 / `python` / `pytest`）」**638** 处（相对 ① 只收窄 21% —— 形状划法收不动）；④ 只认「与某个现算来源**数值恰好相同**」—— **不可用**，绝大多数数字根本没有对应的现算输出可比。**误伤是当场发生的，不是推演**：把缺陷 13 的「含 `403` 的行 46 处」判成「已烂」（今天 `grep -rn "403" web/routers/` = 48），而它是 403→404 翻齐**之前**的读数、本就该冻结 —— **形状划法分不出「记录」与「配方」**，这正是它不可用的原因（同本节「判据不得建立在『看起来像』之上」，②层代理）。故本轮处置是**逐条问、不是全仓扫**：台账里成篇引用的写死数字只有几处，而每条都要判「归谁、谁会先变」，全仓扫的成本远高于收益。**同类边界并列（四例同型 —— 都是「试图建通用锁 → 被实测否掉 → 如实退到边界」，值得点破；但第 ④ 例与前三个的**区别**要看清楚）**：① `tests/test_ledger_substitution_markers.py` 的占位符天花板；② 本条（全仓写死的实测数字）；③ 死参数（见本节「死参数为错位留出空间」，普查 189 处，**绝大多数是框架注入与协议参数，它们本该零引用**）；④ **「看着活的参数」**（见本节「死参数为错位留出空间」末段与缺陷 C 条的 B′ 段：`rag_config` / `summary_threshold` 在本类体内零引用，但**同名在别处是活的** —— `_rag_config` 被 `IndexingService` 拿去建 `RAGEngine`，`summary_threshold` 的概念在 `web/server.py` 有接口）—— ①②③④ 都是**形状同构、只能列举模式而不能从事实推出**，故都**明写留白**，不假装覆盖。**④ 值得单列的理由**：前三例的共同形态是**「文本与事实脱节」** —— 一份文本（占位符、写死的数字、签名里的形参）说了件在被判对象自己身上就能看破的事；④ 是**「参数与消费者脱节」** —— 名字、类型、甚至「附近有个同名消费者」**全是活的**，**只是那个消费者不在这个类里**，故**肉眼审不出来、只 grep 这一个文件也审不出来**。③ 与 ④ 的分界正在此处：③ 零引用且全仓无人用（`grep` 一遍即可确认）；④ 零引用但同名物在他处活跃，**判据必须落到「消费者在哪个类」这一层，而不是「这个名字全仓出现过没有」**
- **旧账已记的结论不要重验 —— 验之前先查台账。** 动手做对比/复现之前，先问一句「这个结论台账里有没有」：已经写下并立住结论的（含「本机全量的绿/红都不可信」「容器里 N 条红 = 缺 git」这类**环境性**结论），直接引用，不重跑；台账里没有的才动手。案例：落缺陷 41 时，「容器 17 failed = 缺 git」早已记在 §四，仍把两小时花在 Windows↔容器逐文件对比上 —— 那不是新证据，是**已记结论的重验**，等于把同一个结论重新买了一遍。本条与上一条同源（都在管「已写下的记录」），方向相反：上一条防「拿台账当事实」，本条防「不信台账而重买事实」。判据：**做对比/复现前先 grep 台账，命中且已立住就停**
- **判据不得建立在「看起来像」之上，必须落在「是不是」那一层事实 —— 判据与被判事实之间隔着的东西，就是能骗人的地方。** 可判定的操作（写锁前做三步）：① 写下这条判据**读的是什么** —— 一个名字？一个字符串？一份清单？一段文本？还是**被判对象本身 / 它的一次真实运行结果**？② 问「**有没有一条路径，能让这个『读的东西』与被判的事实脱钩，而两边都不报错？**」—— 举得出，它就是代理指标；举不出不算答（须给出「为什么不存在这种路径」），因为「想不出」与「不存在」是两回事。③ 举得出时换成读事实那一层的形态；**换不动就写明退到了哪一层、为什么退**（见本节「症状在本环境结构性测不到时，退到最近的可验证事实」那条）。**本条的②层实例**：`except` 能接住什么，是对**失败模式**的假设（隔着 1 层），「实测抛的是什么」才是事实（0 层）—— 实测 `os.path.samefile(None, x)` 抛的是 `TypeError` 而非 `OSError`，故 `None` 必须在调用前挡掉（缺陷 42 第 1 步收尾）；「我以为 except 能接住」与下面谱系里那些缺陷是同一形态。三个互不相同的分界，按「隔着几层」排：
  - **① 隔着 0 层：判据读的就是事实本身。** SQL 谓词原文、文件系统身份（`os.path.samefile`）、运行时实测的命中站点、从 DDL / 源码事实推出的集合。改动即红，没有误报面，可直接用。
  - **② 隔着 1 层「同源派生量」：与事实通常同向，但存在可构造的脱钩路径。** 典型：AST 形状、签名的参数是否存在、`__file__` 字符串相等、参数的**存在**（而非**被用**）。**这一层不是没用，是「有已知盲区」**——留着它就必须**同时**给一条「为绕过它而设计」的变异去撞（见本节「变异实验会暴露判据本身的盲区，不只是实现的缺陷」）；撞不红，说明代理层还留着半个。盲区的形态往往与正常错误**长得一样**（都报绿），所以只有这种变异照得出来。
  - **③ 隔着 ≥2 层，或中间那一层由人维护：两边不存在任何自动同向关系。** 手工清单（`_stubs` 那种假守卫、豁免名单 `_NOT_APPLIED`、allowlist、`# type: ignore`）、文本记录（台账状态行、注释、docstring、白名单）。事实变了它不跟着变，它变了也不代表事实变了，**脱钩时两边都不报错**。必须换成从事实推出的形态，或退成运行时探针。**但③层不是一律要变异 —— 要看失效方向。** 策略表（`_FORM_METADATA` 那几个字段算元数据、`ALLOWLIST` 哪条路由豁免）是**意图不是事实**，没有事实层对应物；它们脱钩的方向是**响亮误伤**（新增一个 Form 字段就红、红的信息点名该改哪一行），不是静默漏过 —— **响亮误伤**与**静默漏过**是两回事，前者的漏法只是「多红了一次」，红本身把该动的地方说清了。判据：**问它脱钩时是「该红没红」还是「不该红却红了」** —— 前者是静默面、必须配绕过型变异；后者是响亮面、人看得见即可。实例：缺陷 42 第 3 步两处「换不动」的降层 —— L5 的元数据表落在**后者**（响亮误伤，不配变异），auth 锁的「用没用」落在**前者**（静默漏过，写进 docstring 当已知盲区 + 与缺陷 25 同形）。
  - **案例谱系（同一病灶，「隔的东西」不同）**：缺陷 19 初版——判据是**命名后缀** `*_unscoped`，真值层是 SQL 事实（WHERE 谓词 / 调用点集合）；缺陷 25——判据是**签名里有身份参数**，真值层是 WHERE 谓词（变异「删 `AND c.user_id = $2`、保留签名参数」照理该红却**绿**，盲区由此照出，改判据后才红）；缺陷 39/40 的 L5——判据是 **`Form(...)` 默认值的 AST 形状**，真值层是框架自己的账本（`get_dependant` / `get_openapi`），`Annotated[...]` / keyword-only / `fastapi.Form` 三种等价写法**静默漏过**；缺陷 42 第 1 步——判据从**模块名**（`web.server`）改到 **`__file__` 字符串相等**，仍是②层代理（盘符大小写、符号链接、`..`、大小写不敏感文件系统都能骗过），最终落在 `os.path.samefile`（同一个 app：`E:\…` 与 `e:\…` 字符串不等而 `samefile` 为真；变异 X-5 把判据退回字符串相等后别名装载**全绿**，红源由此唯一地钉在那次改动上）；`test_rag_unusable.py` 的 `_stubs` **手工清单** → `_stub(storage, name, **kw)` **赋值即声明**（清单是人抄的副本，赋值是事实）；缺陷 42 第 3 步——上述两把锁**已落到**那一层：L5 的覆盖面由 `Form(...)` 形状改为「`get_openapi` 里声明了表单 content 的 op」（1 条 → 4 条是**能力达到不是扩面**），auth 锁的识别半边由 `d.call is get_current_user` 取代形状扫描（`Annotated[...]` 从静默漏过**变成红**：同一个注入变异在新判据下红（V1）、把判据退回旧的 AST 形状后绿（V4）—— 这一对是证明迁移真的降了层的唯一证据）；**缺陷 42 结案**——L5 的第 3 步初版虽然读的是框架账本，却仍拿**字段名**（`_PAYLOAD_FIELD = "file"`）判断谁是正文通道，把文件字段改成同名的文本 Form 字段后正判据与负控**全绿**（V9 照出、V10 用「退回按名字排除 + 同一改法」把红源钉死在判据本身）；这是本形态的**第四次**显形（AST 形状 → 并列身份按模块名 → 按 `__file__` 字符串 → 按字段名判身份），真值层仍在上一次已经找到的那层（schema），只是**有一半边没跟着一起下来**；两把锁各留一处**换不动**的半边，按本条在 docstring 里写明而不是假装覆盖；**缺陷 51**——判据是 **flag 存不存在**（`cli_available()` 只跑一次 `compose version` 看退出码，`build.yml` 的注释核的是「两个 flag 都在」），真值层是 compose 的**两条行为** P-a / P-b，而 **v2.x 上 flag 都在、两条行为都不成立**：代理答「成立」，事实层已经死了整整一轮 —— 那轮 CI 其实**红了**（运行 `35075782258` 实测 `11 failed`），只是红报的是**症状**（`env file …/.env not found`）而不是成因（订正见缺陷 51 的「审计追加 F4」）。修法正是本条要求的那一条 —— **退成运行时探针**（`capability_defect()` 每次都真跑一遍合成编排文件）；引子是那句没写性质的「实测 2.38.2」，故这一例的判据写成：**「实测」必须写明测的是哪条性质，测 flag 存在 ≠ 测行为**。**本条与本节「台账状态行不是事实」、以及「凡『用来防误用』的守卫，其核对的名字必须从被防的事实推出，不能另抄一份清单」是父母与子女的关系**——那两条各只说了一个面（文本记录 / 手工清单），本条给的是它们的共同判据：**问它隔着几层能骗人的东西**。五次撞上同一形态（原锁 AST 形状 → 并列身份按模块名 → 按 `__file__` 字符串 → 正文通道按字段名 → flag 存不存在）就不是巧合，这也是本条立为通用判据的由来
- **两种不同成因的失败若共用一个信号，等于两种都没有守卫。** 可判定的操作：**拿到一条红，问「它能告诉我是我变了还是世界变了」；答不出就是缺红源，不是缺修复。** 这条之所以是判据而不是格言 —— 它能推出**三个互不相同**的分界（满足下一条的门槛 ①），且每一处的检查动作都是同一句话：
  - **依赖只有下界** → 上游发版能让本仓用例红，红的样子与「本仓代码写错了」逐字相同。案例：缺陷 40，`test_l3_oversized_text_screens_table_wording` 在 CI 红、本机绿，两台机器两个结论而**没有任何一处告知是上游动的**。修法是拆成两条红源（按锁的 gate / 装最新的 sentinel），并让**跑红 vs 跑绿但一个 job 打红叉**本身成为分辨手段 —— 不是靠人去比对版本号。
  - **同一行为被复制多遍** → 那两个 500 逐字相同（缺陷 38 顺带记的账：53 处就地 `except Exception → HTTPException(500, "操作失败，请稍后重试")` 与全局处理器的「服务器内部错误，请稍后重试」）。看到「服务器错误」分不出是**就地构造**的还是**兜底来的** —— 想改口径时，改了一处而另一处照旧，且不会有任何东西报错。
  - **崩溃与正常共用一个退出码** → §五 环境账：`pymupdf4llm` 在 Windows 下 import 即崩守护线程（onnxruntime access violation），而 `pytest` 报绿、`exit=0`。吞它的是解释器不是本仓代码，故**没有任何一条本仓的断言会红** —— 记下来之前，它连「有人见过」都没有。
  - 与本条同族的是上面「凡『输出为空即失败』形状的守卫」：那条问的是**守卫的判据与被守的失败形态是不是同一个**；本条问的是**两个失败源是不是共用了同一个信号**。两者都落在「守卫绿不代表没失败」上。
- **判「约束前移」可不可行，先看两层的度量单位能不能换算 —— 单位不同（字 vs 字节、逻辑行 vs 物理行、条目 vs 字节）时，前移只能做到「更早报错」，做不到「传不出非法值」。** 可判定的操作：把要前移的那条判据的**单位**写下来，与框架/上游那条上限的**单位**并排对齐；两者之间若有**非常数**的换算系数（字符 → 字节取决于编码，汉字 3 字节 / 其它 1–4 字节；逻辑行 → 物理行取决于折行宽度），则不存在任何一个阈值能同时「放行全部合法输入」与「拦住框架先炸」。案例：缺陷 40 commit 二 —— 本仓上限是 100 万**字**，starlette `FormParser` 是 1MB **字节**，汉字 UTF-8 三字节 ⇒ 合法上界 ≈3MB > 1MB，**任何**前移到 `Content-Length` 的门都只能二选一。两个互不相同的分界：① **单位可换算**（同一度量，如秒 vs 毫秒、字节 vs KiB）→ 前移成立，换算后落一个阈值即可；② **单位不可换算**（字符 vs 字节）→ 前移不成立，要动的是**通道本身**（下掉那条无界入口），不是校验的位置。与本节「凡『输出为空即失败』形状的守卫」同族：都在问**守卫的判据与被守的失败形态是不是同一个** —— 那条问**形状**，这条问**度量**。
- **删掉一条会红的用例时，先问它顺带承担了什么信号 —— 「本职」是它断言的那条命题，「副业」是别的东西靠它变红。副业不会写进名字里，所以删的时候不会有人提醒。** 可判定的操作：**把那条用例的失败条件列出来，逐条问「这一条除了被测性质，还有什么能让它红？」**——列不出来的（「跑绿就说明没事」）不算答。两个互不相同的分界：① **那条红只由被测性质产生**（输入全部在用例内部构造、结果只依赖被测代码）→ 它是纯守卫，删掉或换载体**无代价**，只需保住命题本身；② **那条红还能被外部条件独立触发**（上游/框架版本、并发时序、机器差异）→ 它是**隐性信号源**，删前必须先把那个外部条件**另找住处**（新的哨兵用例 / 具名 gate），或**显式记账放弃**——两者都不做就是引入一个静默面，而静默正是本表几十条缺陷的共同病根。案例：缺陷 40 commit 二，`test_l3_oversized_text_screens_table_wording` 原带 `strict xfail`，**本职**是守「超长 → 400 + 表里那句」，**副业**是当环境差异报警器（本机 venv 的 starlette 与 CI 锁的版本不同时 XPASS 成红，免费提示「你的环境不是锁的那套」）。换载体（urlencoded `text` 字段 → file part）保住了本职，**副业没了**，这个代价当场记进了缺陷 40 的已知代价①′。一般化：**删用例和改代码在这一点上同形 —— 删掉的东西不会报错，所以「它原来还兼着什么」只能靠问出来。** 与本节「两种不同成因的失败若共用一个信号」是同一条纪律的两面：那条管**新增信号时**别把两件事挤进一个红，这条管**移除信号时**别连带撤走一个没写进名字的报警器
- **「理由」升格成「判据」有门槛 —— 不设门槛，升格会反噬成台账通货膨胀**。升格的价值在于把台账从**记录**变成**可复用的判据集**：记录只在你翻到那一条时有用，判据在下一个没见过那条的人手里也有用。但只解释一个个案的理由若硬升格，下一个人照着套会**套错，而且套错了不会报错**。资格两条，缺一不可：
  - **① 能推出至少两个互不相同的分界。** 案例：缺陷 39 那条「这段文字是为谁写的」同时推出 A / C / B / 39 四处分界 → 够格；只覆盖一个个案的不够格。
  - **② 给得出可判定的操作。** 案例：「取那句文案，问作者写下它时读者是用户还是开发者」可判定；「注意泄漏风险」不可判定。**这一条正是判据与格言的分界。**
- **同一个理由不要落在三个地方 —— 测试说「验什么」，台账说「为什么会有这条」**。判据：一条改动的**历史成因**（「原来是这么错的、所以现在这么改」）只该有一个权威住所，就是台账（本文件条目 / commit message）；测试的文档串只说**这个用例验什么、为什么这样隔离**，不复述成因。案例：缺陷 28 收口时，同一段「原用例为什么不 hermetic」同时写进了测试 docstring（12 行）、commit message、本文件缺陷 28 条目——三处各一份，将来改一处忘两处，是**注定漂移**的重复。当时改动成本高于收益，故只记不改。一般化：**判「该不该重复」先问「谁会先变」**——若三份副本里有任一份会随实现演进先变，就是漂移点，收成一份（判据：一份是事实源、其余是引用）。这与上一条同源：都在问「这段文字是**证据**，还是**某一刻的记录**」——是记录就别抄到需要证据的地方
- **并发/事务/锁的结论不给「应该如此」，只给「要验什么、怎么验」**。案例：MVCC 下 `WHERE EXISTS` 是快照读、不加锁，挡不住「父行正在被删、还没提交」——必须补 `FOR SHARE`。本仓已成文于 `storage/postgres_store.py` 的 `save_distill_chunk`（含死锁无环分析）
- **SQLite 全绿不构成并发命题的证据**：SQLite 写是库级序列化，窗口从根上不存在（`storage/sqlite_store.py` 的 `save_distill_chunk` 自述），生产是 PG。同一段逻辑在 sqlite 上验不出 PG 的行锁语义
- **mock 的形态 ≠ 被测对象的形态**。案例：mock 回 canned JSON，据此误判 map 输出是 JSON——实际是自然语言（prompt 见 `core/distiller.py` 的 `_map_system_prompt` / `_map_user_prompt`；消费侧只判「非空且 ≠『无』」，见 `distill_incremental_stream` 内 `raw_analyses`）。据此设计的 JSON 校验会否掉所有真实 map 结果
- **测试通过 ≠ 命题成立**，可能只是那条路径根本没被走到。案例：分片续跑逻辑落地后长期未真实执行——短文本恒走长上下文路径（分流在 `distill_incremental_stream`），从不进分片。要验分片路径必须显式强制（见第五节）。**同族：对空集的断言恒真**——缺陷 19 的 A 组调用点断言「这 N 个管理原语的调用点必须全在 admin.py」，若把零调用点的死代码原语（`get_comment_reports`）纳进来，命题对空集恒真 = **断言空转假绿**，且不会有任何告警。修法：断言里同时要求「每个名字至少有一个生产调用点」，把「非空」变成断言的一部分（`test_admin_management_primitives_are_called_only_from_admin` 的 `vacuous` 守卫）。一般化：**凡「所有 X 都满足 P」形式的断言，先问 X 会不会是空集**
- **等式类断言先问「两边是不是同一个来源」—— 同源即恒真，判据为零**（上一条的孪生：上一条是**空集**让断言恒真，这一条是**同一个来源**让断言恒真）。判据：写「A 的数量 == B 的数量」这类一致性断言前，先追一遍两个量的产出路径；只要它们**在同一处、由同一份数据**产出，等式对任何输入都成立，**连变异都红不了**（改动会让两边一起变）。案例：Evidence commit 6 的 spec 要求「条目数与注入 prompt 的片段数一致」，而 `core/context_engine.py` 的 `_retrieve_via` 里 `body is None` 时块体正是**由那份 items 当场渲染**的（`"\n".join(f"{line_prefix}{it.text}")`）—— 少产一条 item，块里也少一段，等式照旧成立；web 更直接：块体是 LLM 改写结果（按设计有损），该等式结构性不适用。**有判别力的形态是钉绝对量，且该量从独立的来源推出**（此处从假件语料推出 —— scene 复用生产的 `filter_by_characters`、memory 直接问假件、web 读 payload —— 再与既有冻结字面量 `_HIT_SHAPES` 交叉对齐：**两个独立来源对上，才敢拿它当判据**）。等式的半边保留，但降级为「钉渲染处没丢结构」，不承担判别力。一般化：**「所有 X 都满足 P」先问 X 会不会是空集；「A == B」先问 A 和 B 是不是同一个来源**
- **症状在本环境结构性测不到时，退到最近的可验证事实 —— 不写恒真的假象**。判据：落笔前先问「这条断言在**这个**环境（jsdom / SQLite / 无凭据机）里，会不会对**任何**输入都成立」；会，它就是假绿，且**比没有断言更糟**——它提供虚假覆盖感，还永远不会红。做法：把命题降级到该环境**能判别**的那一层。案例：`card-arc-text` 的 `min-width: 0` 溢出防护 —— 溢出/垂直居中/折行都是布局症状，jsdom 不做布局（`scrollWidth` / `offsetWidth` 恒为 0），写「不溢出」的像素断言**恒真**；退到**源级断言**「那条消除症状的声明确实在」（删掉即红，变异 M29 已证），比只留注释强、又不假装测了布局。一般化：**「锁症状」与「锁代理」的边界** —— 症状可测就锁症状；症状在本环境**结构性**不可测时，锁「消除症状的那条事实」，并**在注释里写明为什么退到这一层**（否则后人以为漏测，会补一条恒真断言回来）。与上两条同族，都在问同件事：**这条断言有没有可红性**——永远不会红的断言不是防线，是伪装成防线的空白

- **修复必须做变异验证**：改坏它，指定测试必须变红。案例：把兜底调用从 `finally` 里整行删掉，**原 14 个测试仍全绿** → 接线没被覆盖；随后补 `TestA2Wiring` 两条（commit `9d2a9e4`；变异实验本身与扫描口径见 `ev:a2-wiring-mutation`。补齐动作可 `git show 9d2a9e4` 直查，但「删掉后仍全绿」这个**运行期观测**只记在未入库的会话文件里，故它单独入了清单）
- **变异必须可判定：要让它「红」，不能让它「挂死」**。判据：设计用例时先想清楚「这条用例被改坏后会怎样」——若会死循环/长时间阻塞，那条变异就**不可判定**（跑不出结果，等于没验）。做法：把输入设计成**有限且末尾通向成功**（如「上限 N 次截断、第 N+1 次成功」），于是「上限改成无限」的变异会走到第 N+1 次并成功返回，与断言的「应当抛出」立刻冲突 → 红。案例：`tests/test_distiller_truncation_selfheal.py::test_repair_cap_raises_truncation_error_not_format_error`
- **测「某字段是承重的」时，被删的字段必须经真实产生产出**。判据：若测试自己直接构造了那个对象/异常，删掉字段的**传递链**不会红——因为测试根本没走那条链。案例：截断自愈用例若直接 `IncompleteResponseError(..., content=X)`，则「去掉 `_extract_content` 的 content 传递」只红 adapter 用例、正向自愈用例全绿；改成经真实 `_extract_content` 产出后才 3 条齐红
- **「X 消失了」不足以证明 Y 修好了——要找独立、可交叉验证的指标**。案例：关掉思考后「空正文片数 3→0」，但空正文消失本身也可能只是采样波动，用它证明「思考关掉了」是同义反复。真正的实证是 **tokens / 正文字符 3.85 → 0.62**，与本仓自己的 `_estimate_tokens = int(len*0.6)` 吻合——两个互相独立的量对上，结论才立得住
- **别人给的 premise 与代码不符时，报更正、只修真缺口，不去实现那个不存在的修复**。案例：SSE「截断会硬断流，因为 `_next_piece` 只捕 `StopIteration`」——实读 `chat.py` 外层 `except` 早已 `yield {"error": ...}`、`client.js` 早已渲染，连接不会掉。真缺口是**可识别性**（帧里没有 `code`/`finish_reason`）与**文案漏内部标识**，修的是这两样。按错前提动手会改出一段无人需要、还掩盖真问题的代码
- **兜底门若判「输出空不空」，而上游能对非法输入产出非空结果，这道门就是结构性失效的 —— 判据要落在「输入是否合法」，不是「输出是否为空」**。案例：缺陷 34 的 Reduce。`if not profile_draft.strip()`（`core/distiller.py:1726`）看着是「归并没产出就报错」的兜底，但上游 `len(batch_results) <= self.SAFE_SINGLE_REDUCE`（`:1711`，阈值 `80`，`:223`）在 `batch_results` **为空时必然成立** → 对**零条分析**再发一次归并请求（`_single_reduce_stream([])`）→ 模型对空输入照样能产出非空内容 → 兜底门**在结构上不可能拦住**。要点：这道门的命题是「输出为空 ⇒ 失败」，而真实的失败形态是「**输入非法但输出非空**」—— 两者不是同一个集合。一般化：**凡「输出为空即失败」形状的守卫，先问「上游能不能对空/非法输入产出非空输出」**；能，则守卫必须**前移到输入校验**（此处：`batch_results` 全空即应中止，那一次调用连发都不该发）。与本节的「凡『所有 X 都满足 P』先问 X 会不会是空集」同族 —— 两条都在问**守卫的判据与被守的失败形态是不是同一个**；判据与被守对象错位时，守卫绿不代表没失败，只代表**失败长得不像它要找的样子**
- **「加了一个集中的出口」≠「所有路径都走这个出口」—— 更靠内的 `except` 先拦下，处理器永远看不到那些异常**。判据：改「异常 → 响应」的口径前，先数**有多少处自己 `except` 并就地构造响应**；那些点不经处理器，口径改不到它们。案例（2026-09-14，缺陷 38 收口，AST 现数）：`web/` 28 个文件里 79 处 `raise` 落在 `except` 块内，其中 **53 处是 `except Exception → HTTPException(500, ...)`**。它们与全局处理器**产出同一个 500**（`web/server.py:219` 的「服务器内部错误，请稍后重试」），故第一期**零行为变化**就放过了 —— 但它们的性质是**冗余，不是被替代**：异常在那里就停了，全局处理器只兜住剩下的。两个顺手记的账：① **同一行为被复制 30 遍**（30 处文案逐字相同「操作失败，请稍后重试」），将来改这句要动 30 处；② **两句通用文案表示同一件事**（「操作失败，请稍后重试」vs 全局的「服务器内部错误，请稍后重试」），统一时应择一。一般化：**「集中」是装配动作，「覆盖」是路径计数** —— 前者做完就看得见（注册一行），后者要真的数，且数出来的缺口**不会自己报错**（这个 500 和那个 500 长得一模一样）。
- **修复一处越权 ≠ 这一类修完**。案例：`text.py` 上报的越权与 `voice.py` 两处同形（都因「注入 `user` 却不引用」）；用 AST 扫一遍全仓同类形态，才把 9 处一次分清（3 真越权 / 6 良性），并把扫描固化成测试（见缺陷 9）。逐处手工排查会漏，且下次照旧
- **判「这条 SQL 有没有按身份收窄」时，JOIN 条件不算数 —— 只有 WHERE 筛得掉非属主行**。`JOIN users u ON c.user_id = u.id` 只约束连接行怎么配对，不排除任何行；把它当成身份过滤，会让「无过滤」的原语看起来已收窄。案例：缺陷 19 的普查初版据此把 `get_card` 误判为「已收窄」，命中数 **41**；把判据限定到 WHERE 子句（先在 `GROUP BY`/`ORDER BY`/`LIMIT`/`HAVING` 处截断，再测身份列谓词）后为 **46**。一般化：**判定一个条件是否构成「过滤」，必须问「它能否独立地排除目标行」，而不是「它提到了这个列」**——同一个列名出现在 JOIN ON、ORDER BY、SELECT 列表里，都不构成过滤。**同族推论：签名里有身份参数，也不证明那个参数参与了筛选** —— 静态锁把「有身份参数」单独当作已收窄的充分条件，于是「参数还在、SQL 不再用它」隐形。该盲区已由缺陷 25 的判据 v2 修掉（只认 WHERE 谓词事实），运行期语义用例仍作第二层兜底（§四「锁绿 ≠ 读取安全」）
- **变异实验会暴露判据本身的盲区，不只是实现的缺陷**。判据要**故意构造「刚好绕过它」的形态**去撞，而不是只撞「明显的错」。案例：缺陷 19 收口时对 PG 版 `get_card_owned` 的变异是「删 `AND c.user_id = $2`、**保留签名参数**」——照理该红却**绿**；暴露的不是 PG 实现有问题，是**判据把「签名有身份参数」当成了「已收窄」的充分条件**（代理指标还留了半个）。据此把判据改成只认 WHERE 谓词事实（缺陷 25），并把那次变异固化成 `test_storage_scope_lock.py::test_criterion_probe_is_not_vacuous` 的负控。一般化：**一条锁的盲区，只有「为绕过它而设计」的变异才照得出来** —— 设计变异时先问「什么样的错误形态能从这个判据下溜走」，再照着造
- **任何「新建/添加/引入」类动作，先确认它是否已存在**（仓库里多半已有同形实现或同名字段）
- **授权失败一律 404，不用 403**：403 说「资源存在但你没权限」，泄漏存在性；404 让「非属主」与「不存在」不可区分。攻击者拿一批 id 扫描时，403/404 的差异就是存在性枚举的预言机。新增端点沿用此口径；已下沉到 storage `*_owned` 原语的端点天然如此（拿不到行即 404）。判据与理由落在 `tests/test_security_authz.py::TestReadAuthorization` 的文档串里。**B 权限型（非管理员/账号禁用）与 C 业务门（审核待审/geo 白名单）仍用 403**——它们答的是「你这个人不能做这事」，与资源是否存在无关，不构成枚举信道。存量 32 处属主型 403 已于 2026-09-12 翻齐（缺陷 13）
- **同一判定的两条分支必须同判一个拒绝码**。案例：`web/routers/chat.py` 的 `_ensure_session`，内存命中分支对非属主判 403、DB 重建分支判 404。状态码在分支间不一致，攻击者反复请求、靠命中/未命中的差异就能推断资源是否存在——内存路径把 DB 路径的防枚举漏掉了。已统一为 404（含同一条文案），由 `tests/test_security_authz.py::TestHoleOwnershipRegression::test_19_chat_memory_hit_non_owner_404` 锁住
- **形态锁与语义用例是两层防线，各管各的，缺一不可**。案例：`tests/test_storage_scope_lock.py` 扫的是调用形态，看不到 SQL 体——把 `storage/sqlite_store.py` 的 `get_text_owned` 里 `AND user_id=?` 去掉，**六条语义用例全红而边界锁全绿**；反过来把调用点改回 `*_unscoped`，锁红。所以「锁绿」不等于读取安全，两层都要有
- **用例恒绿可能是双门互相兜底，不是命题成立**。案例：`resume_session` / `_ensure_session` 是双门（session 属主门 + 下游 `get_text_owned`），只把 session 门改回 `*_unscoped`，下游门兜住、用例仍绿。要让单门暴露，夹具必须刻意让开另一道门（`TestHoleOwnershipRegression` 里让 A 的 card 指向 B 的 text），否则这条用例锁的是「两道门都没了」，而不是「这一道门在」
- **定性一个环境缺陷前先做反向对照，把可疑变量逐个摘掉**。案例：本机 chroma 段错误（0xC0000005）一度被定性为「跨平台读容器写的数据会崩」（可疑变量=数据来源）；反向对照——临时目录内本机自建集合并 `add` 两个向量，同样段错误——证明与数据来源无关，真实范围是「任何非空集合的任何操作」（空集合 `count()`、`get_collection` 正常）。范围写窄了，后人会按错误的边界做决策
- **有上限 / 截断 / 采样的返回路径必须显式上报「被裁过」**。判据：写任何 LIMIT / cap / 采样 / 分页路径时，**在同一次改动里**就决定截断如何上报（`total` / `truncated` 这类显式信号），确实报不了就明说理由，别默认静默。案例：`admin_tasks` 的 200 条上限若不报 `total` / `truncated`，被裁掉的任务在管理页上完全不可见 —— 与 §三 缺陷 2 的「截断响应当成功返回」同病灶，是「失败被吞成正常返回」的**第六次形态**（前五次：线程弃船 / 384 维度不符 / 截断响应 / `finish_reason` 缺失 / `$contains` 恒不命中）。**修 A 时若自己埋下同形态的 B，当场修掉，不许记账放过**。回归锁：`tests/test_admin_tasks_api.py::TestEnvelope`
- **聚合统计必须写明口径**（分组前 / 后、上中位 / 下中位、含不含空样本）。判据：**产物里的字段名与文档表格里的名字不一致时必须显式对照** —— 同一份数据用两种口径能算出两个数，复算时看着像「数字对不上」，被追问时最致命。案例：thinking 证据表的 `out_tokens` p50 是 14 条**合并后**的 nearest-rank 上中位（`sorted(outs)[n//2]` → 8191 / 1245），产物 `summary[].out_tokens_p50` 却是按档（5000 / 6000 字符）**分组**、`int(round(0.5*(n-1)))` 取的**下**中位（8192 / 6079 → 1446 / 1177）；两者各自自洽，混用则得 6487 / 1205，像造假。复算口径成文于 `docs/evidence/thinking_budget_evidence.md` 第 4 节
- **用例绿 ≠ 命题成立，必须证明它真的走到了断言点**。判据：只断言返回码不够 —— 请求可能在更早的守卫（配置门 / 参数门 / 会话门）就被拦下，返回了一个**与断言恰好相同**的码。这类「凑巧过的断言」比失败更危险：它的成立取决于测试机状态，换台机器（或换台机器的凭据）就红。做法：用探针记录**实际命中的 raise 站点**（`file:line:func`），逐条比对期望 handler —— 不是目视，是产物。工具见 §五。案例：`test_create_group_with_foreign_card_404` 断言 404，实际在**有 key 的机器**上到达属主判定（绿）、在**无 key 的机器**上被 `create_group` 的 503「请先在设置页配置 API Key」拦下（红），从未验到它声称要验的东西
- **测试用例不得依赖测试机的 ambient 状态**（凭据 / `data/` / 全局单例）。判据：写完夹具先问「换台干净的机器，这条还成立吗」。两类实测踩坑：① `deps.get_user_llm` 在用户没配 key 时 fallback 到 `get_llm()`（读本机 `.env` / `config.yaml`）——测试机有没有 `DEEPSEEK_API_KEY` 直接决定门开不开；② 路由内的**内联** `from deps import get_memory_manager` **不走 `Depends`**，`dependency_overrides` 管不到它，于是构造了真 MemoryManager（chroma → fastembed → onnxruntime → 本机 access violation）。修法是把这些入口在夹具里钉死，而不是让用例去适应本机
- **monkeypatch 之前先确认打的模块对象就是被测代码用的那一个**。判据：patch 完必须用「把被 patch 的东西真的弄坏」的方式验证（而不是「改完跑绿了」）。案例：`import web.deps` 与 `import deps` 在本仓是**两个不同的模块对象**（同一文件、两份 globals，因为 `web/` 没有 `__init__.py` 且 `web/` 在 `sys.path` 上）——patch 前者完全打空，套件照样绿，真门还开着
- **验证方法本身可能静默没生效 —— 先确认「探针真的在探」，再采信它的结论**。判据：① 确认那条命令真的产出了你假设的环境，而不是「它没报错」；② 再用一个**独立信号**交叉确认，别让同一次动作既当操作又当证据。案例：为复现 CI 浅克隆跑 `git clone --depth 1 . <dir>`，git 打印 `warning: --depth is ignored in local clones; use file:// instead` 后**静默给了完整历史**（1202 个 commit），六个待核 sha 全部 resolve —— 探针什么都没证（本地 clone 走硬链接优化，`--depth` 被忽略）。改用 `file://` 才拿到真浅克隆（1 个 commit + 存在 `.git/shallow`），六个 sha 齐报 `fatal: Not a valid object name`。独立信号 = 与 CI 失败日志**逐字节比对**（同 11 条、同序、同断言行），确认修的是真问题而不是像的问题。**「我验过了」不构成证据，除非能说清那次验证的方法本身就成立**
- **变异红了 ≠ 判别器起作用，可能只是环境让所有输入都红**。判据：变异验证只在**基线为绿**的环境里构成证据；看到「变异也红了」先反问「这次改动**之前**，那个目标用例是绿的吗」。基线本身就红时，变异红可能与被改坏的那行毫无关系。案例：CI 浅克隆下**任何**非哨兵 `code_sha` 都 `fatal`，于是 `tests/test_evidence_integrity.py::test_mutation_reds_its_own_assertion[fabricated_code_sha]` 的「必须变红」被**环境**满足而非被判别器满足 —— 该变异锁在 CI 里一直没有判别力，只是碰巧红着，且不会有任何告警（它被 `test_code_sha_resolves` 同时报红掩盖）。修法是修环境而不是修变异：`build.yml` 的 test job 显式 `fetch-depth: 0`（commit `62fad5c`）。**这条比它附带的那次修复值钱：适用于本仓证据锁的全部变异用例**
- **变异工具自身失效时，红和绿都不可信**（上一条的新变体：上一轮是环境让所有输入都红，这一轮是**判别器根本没跑**）。判据：变异矩阵的**第一步必须是「基线为绿」且这句基线本身要能区分「真绿」与「根本没跑」** —— 只看「有没有红」的脚本，在工具启动即死时会把**零输出读成零失败**。案例（Evidence commit 5 前端变异，两条自查）：① `npx vitest run --reporter=basic`，而 **vitest 4 已无 `basic` reporter** → 整个 run 在启动期就死、只打印 `Startup Error` / `Failed to load url`，脚本按「找 `×` 开头的行」统计 → **red=0**，四条前端变异**全部是假的**，而结论会写成「前端有锁」；② 同一文件被改两刀（M15 先摘帧、再把帧补到别处）时 `originals.append((path, src))` 逐刀记，第二刀记下的「原件」其实是**第一刀之后的中间态**，`finally` 按它还原 → 帧被整段抹掉，**基线跑出 3 红**（发现方式正是那句基线断言）。处置：变异脚本先跑基线并**显式断言 failed=0**；`originals` 改成按路径只留第一次读到的原件；`run_js` 显式拦 `Startup Error` / `Failed to load url` 并 `raise SystemExit`（跑不起来 = 证据无效，不是「没红」）。一般化：**「没红」与「判别器在跑但没红」是两回事** —— 任何自动化的红/绿统计，都要有一条独立的「我确实跑了」的信号（基线断言 / 覆盖率 / 命中计数），否则它只是个安静的空白
- **变异「红了」还不够 —— 要「该红的都红了」；只红一半 = 判别器只走完了一半，而没有任何东西会看出来**（同族第三个变体：一个是**环境**替判别器满足了红，一个是**判别器根本没跑**，这一条是**判别器跑了、但该被它打中的另一半没红**）。判据：一条变异常常同时打中**两条**断言（正命题 + 负控、或两个方向），而「有没有红」只是**一个比特** —— 必须把**该红的用例逐条点名**，全部命中才算这次变异符合预期；只验一条，等于默认容忍「判据只坏了一半」的变异体。案例（缺陷 42 第 3a 步）：把 `form_operations` 改成恒返回空集，合成用例（覆盖面恰等于声明集合）**与**真实负控（表单 op 非空 + 条条字段非空）**都该红**；变异驱动的 marker 因此支持**元组**（全部命中才判符合）。一般化：**变异验证的断言对象是「红源集合」，不是「红 / 绿」这一个比特**
- **豁免机制本身会成为新的静默通道 —— 一把锁允许「写理由即可跳过」，就必须有**另一样东西**去验那条理由声明的后果**。判据：给任何锁加「豁免出口」时，**同一次改动里**必须同时给出验证该后果的独立断言；否则豁免 = 永久放行。案例：`tests/test_migration_dispatch.py::_NOT_APPLIED` 允许以理由豁免迁移接线，`079_remote_user_profiles.sql` 就躺在里面 —— 三把锁（`test_migration_dispatch` / `test_sqlite_fresh_schema` / 分发形态锁）**各自尽职、全部绿**，而 SQLite 新库仍然缺表；更刺眼的是 079 的豁免理由里**白纸黑字写着**「新库缺表、代码在用」，却没有任何东西去验这句话。理由本身不是闭环，它只是把「未修」从代码搬进了注释。修法是**补一条验后果的锁**（`TestExemptionClosedLoop`：新库表集合 ⊇ `migrations_pg/` 声明的表集合，锁症状本身），**不是**再加一条「文件有没有登记」的形态锁 —— 后者正是被那个合法豁免绕过的那一个。推论：`skip` / `xfail` / allowlist / 豁免名单 / `# type: ignore` 这类机制都应按此自查「谁在验我这条理由」；本轮的 `# store-empty-ok:` 标记同理，其后果由 `tests/test_store_failure_visibility.py` 的动态探针（真的把库打坏，看失败有没有消失）来验，而不是靠标记本身。**首选形态：语言 / 框架自带的自清机制 —— 「已知缺口」这类豁免，先看有没有现成的，再考虑自造校验。** `pytest.mark.xfail(strict=True)` 就是：缺口补上后该条 **XPASS 转红**，逼着人把册条删掉，**册子不会变成新债的收容所**。它与 §四「双向校验」的名单同原理，但用的是语言自带的机制而不是自造校验 —— 复用的正确形态。**已实证走通一次**：`create_user` 在 `tests/test_storage_contract_shape.py` 的 `PRE_EXISTING_GAPS` 入册（`882d094`），缺口补上后**只有同时删掉册条那条才没红**（`baf9719`）—— 这比论证硬

- **「改动前既有」不是一种处置 —— 一条恒红的锁和没有锁是一回事，一条恒 skip 的锁同理**。判据：收口时若全量存在红色用例，必须逐条定性并落到**根因修** 或 **显式 skip（原因可见且写明怎么让它真跑）** 二者之一，不允许「环境性、不管」过去；「改动前就已红」只说明责任更早，不说明可接受。案例：缺陷 25 收口时本地全量 `1 failed`，是 `tests/test_rag_unusable.py::test_caller_group_rebuild_degrades_no_index` —— 缺陷 19 的 `e3cf9e8` 把 `group.py` 的 `get_group_session` 改名 `get_group_session_owned`，用例桩没跟着改，`await MagicMock()` 报「不是 awaitable」，指不到根因。**先答「为什么没人发现」再动手**：`gh run view <run> --log-failed` 显示 CI 明确 FAILED（run `34743380519`：`1 failed, 935 passed, 0 skipped`）—— 即 **CI 跑了、也红了，只是红的 CI 没人看**；缺口不在覆盖，故不新增 CI 步骤，只订正为「收口必须看 CI 结果」。修测试侧同时把复发堵掉：桩名改由「实际要桩什么」推出（`_stub()` 赋值即声明并核对真实 store 有同名方法），**不再另存一份硬编码桩名清单** —— 清单自己会漂，原写法正是「用代理（清单）代替事实（赋值）」，与缺陷 25 同病灶
- **skip 也是豁免，就必须有人在「声明的环境」里验它没被滥用**（上一条在 skip 上的实例）。本地无 PG 时 PG 用例走 `skipif`，在标准本地环境下**恒 skip**，会把红训练成背景噪声、最终连真失败一起忽略。处置是双向的：① PG 不可达时**显式 skip 且原因写清怎么让它真跑**（`tests/conftest.py::pg_skip_reason`），判据是**真连一次**（`pg_reachable`）而非「读 `STORAGE_BACKEND` 猜」——后者是代理指标，变量写成 postgres 而库连不上时用例会以连接错恒红；② 环境**声明有 PG 时拒绝 skip**（`build.yml` 的 test job 设 `REQUIRE_PG_TESTS=1`，`pg_required()` 是唯一认它的地方），并由 `test_pg_suite_is_not_silently_disabled_where_pg_is_required` 对账「在声明的环境里被静默跳过、或被 `SKIP_PG_TESTS` 关掉」**本身判红**。**变异验证**：`REQUIRE_PG_TESTS=1` 但无 PG → 两条 PG schema 锁 + 该元断言**三条齐红**（不是 skip）；`REQUIRE_PG_TESTS=1` 与 `SKIP_PG_TESTS=1` 同置 → 元断言在「两边都要」那条上红。`@_pg` 挂**类**不挂模块级 `pytestmark`，否则会把不需要 PG 的纯提取器负控（`test_lock_has_teeth`）一起 skip 掉 —— 用不相干的理由关掉一条本地跑得动的锁，正是要消灭的静默通道
- **凡「用来防误用」的守卫，其核对的名字必须从被防的事实推出，不能另抄一份清单**。案例：`test_rag_unusable.py` 修复桩漂移时，初版守卫是硬编码 `_stubs = (…4 个名字…)` 再逐个 `hasattr` —— 看起来在防桩漂移，实测把桩名改回旧名**它不报**（仍只报「MagicMock 不是 awaitable」），因为它核的是那份**清单**而非**实际桩了什么**。改成 `_stub(storage, name, **kw)`（赋值即声明，赋值时核对）后，把桩名回退成 `get_group_session` 立刻红在「桩 'get_group_session' 在真实 store 上不存在 —— 桩漂移了」。一般化：**守卫与被守对象之间若隔着第二份手工维护的清单，清单就是新的漂移点**（同 §四「判据要问它能否独立排除目标行，而不是它提到了这个列」）
- **死参数为错位留出空间 —— 先问这个参数有没有人用，再问怎么传。** 判据：写或改签名时，对每个可选参数问一句「**函数体里引用过它吗**」——引用为零，它就是纯粹的**错位落脚点**。它与「可选参数 keyword-only」是两层，后者更浅：keyword-only 管**怎么传**（调用点层，多传一个就报错），死参数管**有没有地方可落**（签名层，删掉即无处可落）。**两层都要，但顺序是先删后锁** —— 只锁不删，等于把落脚点留着、只在门口加了道栏杆。两个互不相同的分界：① 参数**被引用**、但与相邻参数名字/类型相近（同为 `str`、同在尾部）⇒ keyword-only 是解；② 参数**零引用** ⇒ 删掉是解（动源头），keyword-only 只是兜底。案例：缺陷 G 的两个参数（嵌入凭据 / 所在区域）在 `_create_session` 函数体里从未引用 —— `history.py` 按位置多传的两个实参正落在它们身上，凭据被静默装进 `user_role`（→ prompt → 明文落库）。**没有这两格，那 8 个位置实参会直接 `TypeError`，根本不会有静默错位**（2026-09 删，锁见 `tests/test_create_session_kwonly_lock.py`）。**边界（诚实写清）**：「死参数」单独**不是可用的全仓判据** —— 普查 189 处，绝大多数是 FastAPI 注入的 `request` / 依赖参数与 `__aexit__` 这类协议参数，而**抽象方法的参数必然「零引用」**（体只有 docstring），上一版没排除这类就把 `StorageBase.save_text` 全判成死参数。**「零引用」在本仓是常态不是异常** —— 这个事实本身就是这条边界成立的依据：绝大多数命中是框架注入与协议参数，它们**本该**零引用，故「零引用」单独没有判别力。可判定的组合是**三者同时**：零引用 **+** 可位置传 **+** 与相邻参数名字/类型相近；故本条只作**改签名时的自查**，不做全仓锁。**同族的静默面**：`_create_session` 还有第三个死参数 `text`（第 1 位必填），删它要动 4 个调用点，本轮**记表不修**（必填参数不会被静默错填，风险不同）
- **锁的输入状态本身也是判据的一部分，只守理想初态的锁会漏掉生产实际运行的那个状态**。判据：写一条比对/闭环锁时，先问「**生产上跑的是哪个状态**」，再问「我的锁跑的是哪个状态」——两者不是同一个时，锁绿不构成结论。案例：缺陷 23 的列级锁比的是 **fresh SQLite ⟷ fresh PG**（各跑一轮 init），而生产 / 长期开发库跑的是**重启过若干轮**的库：第二轮起 018 会把 `api_key`/`base_url`/`model` 加回 users，删列块却因为哨兵 `password_hash` 早已被删而整块跳过 → 25 ≠ 22 列的漂移**只在第二次 init 后出现**，fresh 锁**一直绿**（变异实测：改回旧触发条件后，fresh 那条仍绿、只有新加的「二次 init ⟷ fresh PG」红）。一般化：**状态是判据的输入，不是背景** —— 「初始态」「空库」「首次运行」这类理想态最容易让锁自证成立；凡是描述「稳态」的断言，都要说清是哪一个稳态（首轮 / 第 N 轮 / 已收敛）。**推论（同族）**：读事实的锁不许带写副作用 —— `_sqlite_columns` 初版每次读都跑 `_ensure_initialized`，把变异当场「修好」，于是变异永远红不了；锁的读取路径与写路径必须分开。

- **排查前先确认那条日志出自哪个 revision —— 出处不明的日志比没有日志更危险**。判据：拿一段日志当证据之前，先在**产出它的那份代码**里定位那行字符串（`git log --all -S '<原文>'`；再到容器 stdout / 宿主机日志里 grep 一遍）。全空即说明它不属于任何已知版本，**在它上面做出的每一条推断都是空中楼阁**——没有日志至少知道自己不知道，来源不明的日志会让人以为自己在推理。案例（2026-09-14）：「识别角色卡住」的诊断建立在日志 `调用失败 (尝试 1/3)，2.0s 后重试` 上，据此推出「`_GEN_ATTEMPT_S` 默认 45 秒不够」；而该常量与那行日志在本仓**任何 revision 都不存在**（`git log --all -S` 全空，`a41fcfd` 删掉的是英文 `[LLMAdapter] Attempt {n} failed: ... retrying in {w}s...`），容器 stdout 18651 行、宿主机 `logs/` 亦零命中；观测到的间隔（11s / 5s / 5s）本来就对不上 45s ceiling，否决它的证据当时就在。**同族推论**：日志的**格式**比它的**内容**更能定版本 —— 先问「这行是谁打印的、现码里还有没有这个格式」
- **版本抽样要选有区分度的样本 —— 没变更过的文件认不出版本**。判据：核对「镜像 / 容器 / 部署跑的是哪个 commit」时，样本必须是**在该区间内真的改过的文件**；拿一个横跨区间未动过的文件去比，会把版本误判到更早的 commit 上，而且**看不出任何异常**。案例（2026-09-14）：用 `adapters/llm_adapter.py` 比对得 `0403d407`（09-08），实际容器是 `b561f5e`（09-08 17:43:28+1000）——该文件在 `0403d407..b561f5e` 之间逐字节未变，真正钉死版本的是 `web/routers/market.py`（含 `_publish_preflight`、尚无 `get_card_unscoped`）。做法：**取 3 个以上区间内有变更的文件互相交叉**，或把镜像创建时间与相邻 commit 时间对齐（本次 `b561f5e` 提交 2026-09-08 17:43:28+1000 → 镜像 `2026-09-08T07:44:45Z`，差 73 秒）。**推论**：新版比对同理 —— 核「rebuild 有没有真生效」也要挑有区分度的标记（本次：`_RetryBudget` / `_THINKING_DISABLED` / `_GEN_DEADLINE_S` 在、且启动日志里 `[SQLiteStore]` 迁移告警由 3 条变 0 条）
- **核静态结论前，先把每个分支的入参追到源头 —— 只读被判对象自己，等于只读了半条链**。判据：从一条静态链推出「某分支必然 / 不可能发生」之前，把该分支的**每个入参**追到它的产生处。只读到被判对象自己的分支（`if` 的两边）而没读两个分支的**输入从哪来**，就会把一对**互斥**条件当成可同时成立。案例（2026-09-14，缺陷 37）：据 `get_distiller`（`web/deps.py:182-192`）的两个分支断言「`/run_stream` 原地改写模块级单例、跨请求污染」——`llm is not None` 走 `return Distiller(llm)`（新实例）、`llm is None` 走模块单例，看着两条都可能。追入参才发现：调用点传的 `per_user_llm` 来自 `get_user_llm`，而它**没 key 时回落 `get_llm()`**（`deps.py:118-119`）⇒ `per_user_llm is None ⟺ get_llm() is None`；而单例**仅在 `get_llm()` 非 None 时被创建** ⇒ 「单例存在」与「入参为 None」**互斥**，实际永远走新实例那条，改的是请求级副本。**这个互斥只有追到 fallback 那一行才看得见**。连带修正：同一条里「503 门拦不住」也随之证伪 —— 那道门（`distill.py:1059`）拦的正是「拿不到 LLM」，而污染的前提恰恰是「拿得到」，两者不可能同时成立。教训：标注「静态读出」**救不了没走完的静态链** —— 标注只声明了方法的局限，不构成结论成立的充分条件。**第二种形态（2026-09-19，C0–C5 门重构）：追到了入参也会骗人 —— 要把「某条路径上的等式」与「全仓不变量」分开。** 缺陷 37 的证伪正是追到了源头才写出的那句 `per_user_llm is None ⟺ get_llm() is None`（源头 = `get_user_llm` 的 fallback），但它的定义域只是「经 `get_user_llm` 解析出来的那个变量」。把这句话当全仓不变量用，就得到「拆掉解析出口零风险」的结论，直到 `9864608`（打断后台蒸馏线程）与 `bb62311`（打断 `/start`）各自显形。故判据补一半：**追到入参之后，再问「这条等式在别的调用点是否也成立」** —— 同一件事有几套解析逻辑时（本案即 F4「策略三份分裂」），等式只在其中一份里成立。识别形态：**同一条链上存在两个以上各自解析一次的入口**。订正正文见缺陷 37 条目末段。

- **判据的定义不全，会同时造出「假空转」和「假覆盖」—— 两个方向相反的假象，同一个成因。** 判据：一条「判别器 ↔ 变异」的覆盖闭合锁（`tests/lock_coverage.py` + `tests/test_lock_coverage.py`）报告某个集合对不上时，先问**判别器的定义收全了没有**，再问变异写了没有。案例（缺陷 41 · C 轮收口）：定义原先只收 `assert` / `raise` / 纯抛错包装的调用点三类，于是「`pytest.raises` 该抛而没抛」这一类分支**在两边都不存在** —— 它既让某些判别器**从没被任何变异撞过**（假覆盖），又让某个变异看起来**一条判别器都没撞到**（假空转）。补上第四类 (d) 之后，那条原先「空转」的 **G-21 当场红在 125/142** —— 「唯一一条空转是假象」的判断由此成立，缺的正是 (d)。**反过来也要防**：(d) 的行号取法（`with` 取 `ast.With` 的行号、`context_expr` 那个调用不单独计数）是**实测定的** —— 按调用取会与 `with` 差一行，凭空多出一条**永远没人撞**的判别器，那正是本锁要防的另一种空转。**故「定义收不收得全」和「定义怎么落地」两处都会制造假象，两处都要实测。**

  **镜像的一条：定义**过宽**，会造出「假缺口」。** 判据：闭合锁报「某条判别器没有任何变异能撞到」时，除了问「变异写没写」，还要问**它是不是判别器** —— 定义把**机制**当成了判据，就会把机制记成缺口，而**删代码不是处置**（删了机制就不工作了）。案例（缺陷 41 · ① 收窄）：判别器定义 (b) 原收**每一条 `ast.Raise`**，于是测试替身的失败注入（`raise self._exc`，`test_health_probe_targets.py:133` / `test_health_ready.py:48`）进了 D —— 它们是**让替身工作的动作**，不是**判断被守对象的**分支，任何合法变异都撞不到。正解是**收窄定义**：只收**就地构造或点名异常**的 raise（`raise X(...)` / `raise X`），不收**转抛外来异常**的（`raise self._exc` / 裸 `raise`）。收窄后 `ping` |D| 35 → 33，正好是那两条。**收窄这类定义时，同一把尺子在模块里出现几处就要换几处** —— `_pure_raisers`（「纯抛错包装」的体）里也收 `ast.Raise`，不一起换，下一个照 docstring 判断的人就会用上另一把尺子。两条合起来：**定义不全 → 假空转 + 假覆盖；定义过宽 → 假缺口。三个方向，一个成因：定义与「要防的失效」之间没有对齐。**

**同族三条：只看了链条的一段就下结论。** 上面各条中，「**台账状态行不是事实**」（读了**记录**，没核它指向的 `git log` 源头）、「**变异工具自身失效时，红和绿都不可信**」（验了**结果**，没验**工具本身跑没跑**）、本条的「**只读被判对象自己**」（读了**函数**，没追它的**入参**）是同一病灶落在三个对象上的实例：**结论所依赖的那一环，和结论本身之间隔着一段没人读的链路**。三条的检查动作也同构 —— 落笔前问一句「**这段是我读到的，还是我从别处推的？推的那一环我读了吗？**」：读记录就问源头、读结果就问工具、读函数就问入参。三条不并成一条（对象不同、修法不同），但**看到其中任何一条的形态，去另外两条上各扫一眼**。

- **「失败吞成成功」只有一种合法形态：吞了，但留了痕 —— 且痕得能被下游读到。** 判据两步，缺一不可：① **问「这次降级在哪个字段里留了痕？」** —— 答不出就是普通吞错（不合法）；答得出才进第二步。② **问「这个痕有人消费吗？」** —— 只写给人看的痕不算闭环：真正的留痕得有**下游读它**（进响应体 / 落库 / 被某个判定分支消费），否则这次降级对**程序**而言仍然是静默的。案例（D2）：`web/llm_resolution.py::resolve_llm` —— per-user key 构造失败**回落全局 key**（不抛给调用方，这是 D2 定的策略，不是请求失败），但 `Resolution.reason` **非空即「有账要记」**，由调用方 `deps.get_user_llm` 打日志。这是本族**唯一**被允许的吞法，理由写在该函数 docstring 里。**反例（同族，不合法）**：缺陷 68 —— `update_user_api_config` 无用户行时 0 行匹配仍回 `{"ok": True}`，没有痕，用户以保存成功为信号而 key 从未落库。**如实标注本轮的边界**：`reason` 目前只到 `print`（进日志收集器），**满足 ① 不满足 ②** —— 故 D2 today 的准确口径是「吞了但只留了给人看的痕」，够格登记，**不够格算闭环**；要闭环就得让 `reason` 进到某个下游真读它的地方。

- **进程级状态：谁改谁还原，且断言者必须自建它断言的前提 —— 两件事都别信「碰巧在」。** 判据：动手改之前问「**这条断言依赖的进程级前提是谁建的？**」—— 答「进来时就在 / 上一条用例留下的」就是依赖 ambient 状态，必须自己建；动过任何进程级状态，退出时必须还原成**入场原值**。两个分界：① **`None` / 空集不等于「还原」** —— 本案四处状态（守卫、投递器、载体登记表、`LLM_CALLER`）没有一处是用例私有的，生产 lifespan 一跑就注册了：置 `None` 是把**生产那一份**拆掉，后续用例轻则静默改走回退分支，重则凭空被门管住、报 `LLMCallerMissing` 而与自己的断言无关；② **ContextVar 与普通全局的还原方式不同** —— `var.set` 返回的 **token** 连「本来没设过」这个事实一并还原，快照式的「写回 `None`」做不到（`tests/test_llm_access_gate.py` 的 `_set_var` / `_snapshot` 两个载体共用 `_Restored` 那一对 `__enter__` / `__exit__`）。**「自建前提」侧的实例**：`tests/test_log_collector.py::test_install_log_collector_idempotent` 原按 `count_before + 1` 断言，偷带的前提是「入场时没人装过」—— 只有看哪条用例先跑才成立；命题本是**幂等**（装几次都恰好一个 handler），改成「装一次断言 == 1、再装一次仍 == 1」，前提就由用例自己建出来了。

- **用例的通过条件不许依赖测试机的凭据 —— 判定标准是「干净克隆 + 无 `.env` + 无 `config.yaml` + 无任何 API key」时全量全绿。** 判据：新增/改动任何用例之后，用**空 key 环境**再跑一遍全量。本仓的构造法是 `DEEPSEEK_API_KEY=`（**空串**，不是 unset）—— `load_dotenv(override=False)` 按**存在性**判断，已存在的空串压过 `.env` 里的值。**这条踩过两次**：L4（活会话那道）与 `TestFPerUserGate` 的初版各自都只在**本机有 key** 时成立，表现是「本机绿、干净克隆红」，而红的样子指向用例自己的断言、与成因无关。与上一条同源：上一条管**同一进程内的相邻用例**，这条管**同一份代码换一台机器**；共同判据是同一句 —— **把「它凭什么会绿」逐条列出来，凡答不出「用例自己建的」就是外部条件。**

- **测试结论也不许依赖测试机的工作目录布局 —— 仓库根下挂着兄弟 worktree 时，两条 census 锁必红，红的信息里的路径前缀就是判据。** 事实：`tests/test_collection_surface_lock.py::test_every_test_shaped_file_is_inside_the_collection_surface` 与 `tests/test_exception_pickle_lock.py::test_census_matches_registry` 的扫描面都是「仓库根以下」（前者 `os.walk`、后者 `REPO_ROOT.rglob`），而 `.claude/worktrees/<name>/` 下每个兄弟 worktree 都带着完整一份仓库副本 —— 于是它们的 `tests/` 与 `core/` / `adapters/` / `storage/` 被算成了本仓「收集面外的测试文件」与「未登记的异常类」。**证据（2026-09-21，`1036c65` + 记账收敛改动，现跑）**：全量 `2 failed, 1413 passed, 69 skipped, 1 xfailed`，两条即此二者；两条**单独重跑仍红**（`2 failed in 43.46s`），命中项**逐条都在 `.claude/worktrees/` 下**（`{avatar-owner, chat-identity, demo-readonly}` × 8 个已知异常类 = 24 条）。**反向对照**（照本节「定性一个环境缺陷前先做反向对照，把可疑变量逐个摘掉」）：同一 commit `git clone` 到仓库**外**（clone 里没有 `.claude/`，`git worktree list` 只有它自己），两条锁所在的**整个文件共 14 passed 全绿** —— 红源由此唯一地钉在「根下有没有兄弟 worktree」这一个变量上。**定性：环境问题，非编号缺陷**（判据命令：看红的信息里点名的路径是否**全部**带 `.claude/worktrees/` 前缀，是即属本条，**别去改代码**）。**根治已做（2026-09-21，§三之二 D）：扫描面从「遍历目录树」换成「问 `git`」，两条锁不再依赖工作目录布局；同日的反向对照是把它变异回 `os.walk` 后 3 failed、恢复后 16 passed。** 本条保留的是**方法**，不是待办：与上一条同族且互补 —— 那条管**凭据 / `data/` / 单例**这类 ambient 状态，这条管**工作目录的物理布局**；判据是同一句的镜像 —— **把「它凭什么会红」逐条列出来，凡答不出「本仓代码变了」就是外部条件**，而外部条件里凡是**能由本仓声明掉的**（`.gitignore` 就能声明「这不是本仓代码」），就该换成依赖那份声明，别靠读锁的人当场判读。**留个反例做量尺**：新加的 `tests/test_repo_files.py` 只在临时小仓库里合成一个被忽略的 `.py`，故它在**干净 clone 上也能判**；只靠「本机有兄弟 worktree」才红的断言，CI 上恒绿 —— 那是伪装成防线的空白。

### 五、验证工具现状

- `tests/perf/mock_llm_server.py`：mock LLM，挂 `/chat/completions` + `/embeddings`，另有 `/admin/set` 控制面
- **思考参数证据档**：`docs/evidence/thinking_budget_evidence.md` + `tests/perf/map_len_probe.py` / `tests/perf/capfield_probe.py` + 原始产物 `docs/evidence/thinking-maplen-before.json` / `thinking-maplen-after.json` / `thinking-capfield.json`。2026-09-11 自 `e2e/scratch/` 提入库、2026-09-12 随「证据产物一等化」迁入 `docs/evidence/`（这批数字被 AGENTS.md 正文引用，产物不入库就无从追溯；此类产物走 `tests/perf/evidence_writer.py` 落盘并登记清单，不是「调试脚本不入库」的例外而是唯一路径）；入库产物已删 `preview` / `content_head`（会逐字带出原文对话），只留统计量
- **证据清单渲染**：`tests/perf/render_evidence.py` → `docs/evidence/resume-numbers.md`（「哪些数字能写进简历」+ 不可写清单 + 出处索引）。渲染产物勿手改，锁会重渲染比对（清单改了不重跑即红）
- **用例到达性证据档**：`tests/perf/raise_probe.py`（pytest 插件，包住 `HTTPException.__init__` 记录实际命中的 `file:line:func`）+ `tests/perf/check_reachability.py`（逐条比对期望 handler）+ 原始产物 `docs/evidence/ownership-reachability.json` / `ownership-reachability-nokey.json`，叙述见 `docs/evidence/raise_sites_evidence.md`。用于证明「每条属主用例真的走到了属主判定」以及「夹具已与测试机凭据无关」（`PROBE_NO_KEY=1` 模拟无 key 机器，须仍全绿）
- `e2e/scratch/`：一次性探针。gitignore 覆盖见 `.gitignore` 的 `/e2e/` 规则；注意另有 `web/frontend/e2e/` 的一组规则，勿混。**调试脚本不入库**（被正文引用数字的探针产物不算例外，走 `tests/perf/evidence_writer.py` 落 `docs/evidence/` 并登记清单 —— 见「开发工作流约束」的「调试脚本不入 main」条）
- **强制走分片路径**：配置里把 `longctx_threshold` 调到 1（另可把 `chunk_size` 调小、`map_concurrency` 调 1 以确定性截杀），**不要改源码**。该做法跑通的证据是 `ev:incomplete-v5`（产物 `docs/evidence/incomplete-v5.json`）。原引的 `.claude/sessions/2026-09-10-distill-dbtruth-closeout.md` 未入库（`.gitignore` 覆盖 `.claude/`），本行不再以它为唯一出处 —— 上面三行配置项是自足的，跑完该分片路径是否真被走到由那份产物判定
- **PG 验证用 throwaway 容器**：`scripts/restore_verify.sh` 里的 `docker run -d --rm` / `docker rm -f`。本仓惯例见会话记录（「PG throwaway（55433）N passed，随后 `docker rm -f`」等）
- **`e2e/helpers.cjs` 路径更正**：根 `e2e/` 下无此文件，实际在 **`web/frontend/e2e/helpers.cjs`**，`BASE = 'http://localhost:7861'`。本机实测 7861 端口**现有 3 条 LISTENING**（PID 21116 占 `0.0.0.0:7861` 与 `[::]:7861`，PID 11408 占 `[::1]:7861`），与「四重监听」不符，以现测为准。**待改成 `127.0.0.1`**；「会连到 Docker 里的旧 build」一说**待验证**

- **环境账（2026-09-15）：`pymupdf4llm` 在 Windows + pytest 下 import 即崩一个守护线程（onnxruntime `access violation`），而 pytest 报绿、`exit=0`**。现象：任何走到 PDF 解析路径的用例（`core/text_manager.py::_extract_pdf` 顶部的 `import pymupdf4llm`）都会在解释器退出时打印 `Windows fatal exception: access violation`，崩的栈是 `concurrent.futures.thread._worker` → `pymupdf.layout.onnx.BoxRFDGNN` → import `onnxruntime`。**已定性到「与本仓改动无关」**：只含 `import pymupdf4llm` 的单测即触发。这是「失败被吞成正常返回」的又一形态，只不过**这次吞它的是解释器** —— 不记，哪天它变成偶发红就没人记得见过。首次暴露于缺陷 39 的形态锁（此前无用例走 PDF 路径）。**已定性（2026-09-15 补，缺陷 40 commit 一 期间）**：变量就一个 —— **`onnxruntime` 的版本**。反向对照（单变量）：在按锁装的独立 venv 里只把 `onnxruntime` 从 `1.30.0` 退回 `1.26.0`，其余 155 条一字不动 → **进程不再崩、正常出汇总**。`1.30.0` 在本机的失败形态有两个，且都很难认成「环境问题」：① 主线程 `import onnxruntime` 直接抛 `ImportError: DLL load failed while importing onnxruntime_pybind11_state: 动态链接库(DLL)初始化例程失败。`；② 守护线程里的 `access violation`（同一条）。① 从 `core/text_manager.py::_extract_pdf` 的 `import pymupdf4llm`（那个 import **在 try 之外**）直接穿到 HTTP 层，于是 `test_l3_bad_pdf_screens_table_wording_and_logs_the_original` 从 **400 变 500**，**看起来完全像本仓写错了**；② 让 pytest 进程中途 `Segmentation fault`、`exit=139`、**连汇总都打不出来**（旧版是「跑完、exit=0、退出时崩个守护线程」）。**范围**：本机 Windows 特有 —— CI 是 Linux，且**早就装的是同一批版本**（「按锁装」= CI 今天解析到的那一套），Linux 上 `import onnxruntime` 正常、1071 例照过。**故它不是缺陷 40 的一部分，也不该靠钉旧版解决**（往回钉等于把这个上游信号重新藏起来）。**对本地开发的后果（如实记）**：本机 venv 与锁差 86/156 条，**在弄清 onnxruntime 为何在这台机器上加载不了之前，不要照锁重装本地 venv** —— 重装后本地全套会在中途段错误。同机另一处已被定性的 onnxruntime 崩见 §四「测试用例不得依赖测试机的 ambient 状态」

来源（外部文档，非本仓代码）：DeepSeek 思考模式——参数为 `thinking: {"type": "disabled"}`，思考默认开启且 effort=high，思考模式下 `temperature` / `presence_penalty` 被忽略：<https://api-docs.deepseek.com/guides/thinking_mode/>
