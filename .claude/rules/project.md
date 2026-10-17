# Character-distill 生产运维

## 部署拓扑

双区独立部署，各自独立 PostgreSQL（无主从同步），各备各的。

- **SZ（深圳）**：`ssh admin@47.107.42.111`，项目路径 `/opt/character-distill`，异地备份目标 = 阿里云 OSS，节点专属域名 `sz.bookecho-shiyu.cn`
- **SG（新加坡）**：`ssh ubuntu@43.134.55.201`，项目路径 `/home/ubuntu/Character-distill`，异地备份目标 = 腾讯云 COS，节点专属域名 `sg.bookecho-shiyu.cn`
- 节点间通信只用 `sz.bookecho-shiyu.cn` / `sg.bookecho-shiyu.cn`（只配默认线路、证书 SAN 已包含）；不能用主域名 `bookecho-shiyu.cn`——它按线路分流，两台各自解析回本机
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
- **执行中新发现的问题**（2026-09-30 起，取代原「发现 spec 外 bug 先报告」）：属于本段改动面的，直接修，报告里写明修了什么、对应哪条测试；会与其他线撞车、或需要 Shiyu 拍板的，停下报告；不许自行新增记账条目。标准见 `docs/specs/SPEC-STANDARD.md` 第 6 条
- **存储改动只保证 PG（2026-09-24 起）**：新的存储改动只保证 PG 正确；SQLite 只同步到「接口还能跑」为止，不为它写用例。SQLite 自 2026-09-24 起不再测试、不再维护，计划择期退役。**迁移上有一条例外**：PG 新增**列**时，`storage/migrations/` 里补一份同语义的孪生迁移并登记进 `sqlite_store.py` 的迁移次序表，此外不加。理由是冻结后第一次加列暴露了「接口还能跑」与「不做迁移」互斥 —— 新列没有那个列就谈不上接口能跑，而两侧真库的列集锁（`tests/test_postgres_store.py::TestPgFreshSchemaClosure::test_fresh_sqlite_and_fresh_pg_have_the_same_columns`）要求列集相等且不许开豁免，**列集锁是二者的仲裁**
- **跑测试前先起测试库**：本地一律 `docker compose -f docker-compose.test.yml up -d --wait`，测试连它的 `charsim_test`（55432）。`tests/conftest.py` 会话开始时核一次库名，不以 `_test` 结尾即整场中止 —— 开发库 `charsim` 不再有任何被测试碰到的路径
- **推送前预检（2026-10-17 起）**：克隆后执行一次 `git config core.hooksPath .githooks`。推送前测试库要已启动（`docker compose -f docker-compose.test.yml up -d --wait`），否则 pytest 会话开始就连不上库、整场中止；`git push --no-verify` 只用于验收「CI 失败摘要」，平时不用。钩子跑 `scripts/check.sh preflight`（6 个元锁 + 前端 eslint，全量只在 CI），解释器由脚本解析（`PYTHON` → 主仓库根 `.venv` → `python`），不依赖 PATH 上的 pytest；改动全在 `docs/` 或 `*.md` 时直接放行
- **失败必须进 logging，不许只 `print`**：`print` 写 fd 1，绕过整条 logging 管道 —— 既不进 GlitchTip（Sentry 的 LoggingIntegration，ERROR 生成事件），也不进告警邮件（`core/alerting.py` 的 `AlertHandler`，`ALERT_LEVEL = logging.ERROR`）。只 `print` 的失败等于只有翻容器 stdout 才看得见。
  - **往上抛的错误**不必逐处改：`web/server.py` 的全局异常处理器统一记一条带堆栈的 ERROR。**打印后 `raise` 的 `print` 保留不动。**
  - **吞掉的错误**必须走 `core/nonfatal.nonfatal`（异步、整块可失败后继续）或模块 `logger`（同步，或需要给调用方一个兜底返回值）。级别就是「发不发邮件」：**ERROR = 数据没有存进去，或者用户的请求失败了；WARNING = 已经兜底、不影响结果的后台动作**（好感度评估、阅读进度、预热、缓存）。
  - 判据是 `tests/test_failure_alerting.py::test_no_print_swallowed_failures_left_in_production_code`（扫 `storage/postgres_store.py` / `web` / `core`：「`print` 文本含 `fail` 且下一条语句不必然 `raise`」的结果必须为 0）。断言这类日志的用例走 `caplog`，**不要断言 stdout** —— 只断言「有日志」会让 ERROR 写成 WARNING 也照绿，而那一档之差就是发不发邮件
  - 例外不在名单里，在判据里：`core/distiller.py` 那处 print 后跟 `if truncated: raise … / raise …`，属「必然抛出」，由全局处理器接住

