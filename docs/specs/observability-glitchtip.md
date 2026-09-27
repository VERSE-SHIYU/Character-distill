@incremental-implementation @search-first @tdd @source-driven-development

# 可观测性线：前后端报错统一进自托管 GlitchTip

## 已定决策（Shiyu 拍板）
- 收集端：**GlitchTip 自托管**（MIT，兼容 Sentry SDK），只装一份，装在 **SZ（47.107.42.111）**。SZ、SG 两台的前后端都上报到这一份
- 子域名：`errors.bookecho-shiyu.cn`（A 记录由 Shiyu 在 DNSPod 添加，指向 SZ）
- SDK：后端用官方 `sentry-sdk`，前端用 `@sentry/react`，不自写收集逻辑
- 自建日志面板（`core/log_collector`）与邮件告警（`core/alerting`）**在本线最后一段**、GlitchTip 线上验证可用之后才退役；在此之前两者并存
- D1 前端 DSN：**运行期下发**，不在构建期注入。沿用仓库现成的两条机制：
  - 配置经「仓库变量 → deploy.yml → compose environment」下发（先例：`ALERT_EMAIL`）
  - 前端经公开 GET 接口 + `PUBLIC_PATHS` 取服务端配置（先例：`/api/announcement/active`）
  - 同一个环境变量同时生成 CSP 的放行项，只有一处来源
- D2 SZ 专属文件：文件放进仓库留版本，**由执行器一次性 scp 到 SZ**，不进部署流水线。先例：`/opt/ssl-renew` 这类基础设施也不走发版

## 已查实的约束（代码基线 origin/main `4b8a2be`；服务器事实来自 2026-09-24 只读勘查）
1. **日志现状**：`web/server.py:96 _lifespan`，`:107 install_log_collector()`，`:110 install_alert_handler()`
   - 全仓（除 tests）没有 `basicConfig` / `dictConfig` / `setLevel`，根日志器保持默认 WARNING
   - 根日志器挂了 handler 之后，Python 的 lastResort 就不再生效，所以 `logger.error/warning` 不进 stdout，也就不进 `docker logs`
2. **异常汇合点**：`web/server.py:267-272 _global_exception_handler` 用 `logger.exception` 记录所有未捕获异常。后端的报错全部汇合到根日志器
3. **import 期装配的禁令**：`web/server.py:97-102` 注释写明，进程级装配只写在 lifespan 里，不放在 import 期
4. **前端入口**：
   - `web/frontend/src/main.jsx:7/14` 用自写的 `ErrorBoundary`（`componentDidCatch` 只 `console.error`；另有 `handleReset` 清 `nav_*` 这组 localStorage 键）
   - 全仓只有这一处引用 `ErrorBoundary`
   - React `^19.2.6`；`createRoot` 在 `main.jsx:13`
5. **前端没有构建期环境变量**：全仓 `import.meta.env` 只有 `DEV`。前端在 `Dockerfile` stage 1 里构建，打进同一个 app 镜像，由 `build.yml` 构建一次，SZ、SG 共用
6. **CSP**：`web/security.py:20-28`，其中 `connect-src 'self' https://api.deepseek.com`
7. **公开接口的先例**：
   - `web/server.py:305 PUBLIC_PATHS`
   - `:436-447 _announce_router`（`GET /api/announcement/active`，无需登录）
   - `web/demo_gate.py:63` 只读方法一律放行
   - `public/sw.js:14` 对 `/api/` 不缓存
8. **配置下发的先例**：
   - `deploy.yml:49-53` 顶层 `ALERT_EMAIL: ${{ vars.ALERT_EMAIL }}`（注释写明「不写在服务器 .env」），`:169/:363` 进 `envs`
   - `docker-compose.prod.yml:91` 进 `environment`
   - `DEPLOY.md:151-157`「由部署下发的配置」表
9. **节点身份已有**：`web/routers/auth.py:436 NODE_REGION`（缺省 `cn-shenzhen`），来源是各节点的 `.env`。不另起 `NODE_NAME`
10. **release 已有**：`deploy.yml:243/:393` 在 `compose up` 之前 `export APP_IMAGE_TAG=${COMMIT_SHA}`，回滚路径（`:285/:425`）是 `PREV_SHA`
11. **会删掉 GlitchTip 的写法**：
    - `deploy.yml:245/:395` 用 `docker compose -f docker-compose.prod.yml up -d --remove-orphans`，同一 project 里不在该文件中的服务会被当作孤儿删除
    - deploy 只 scp `docker-compose.prod.yml` 一个文件（`:153-160`）
12. **网络与 nginx**：
    - compose project 名为 `character-distill`（现役容器 `character-distill-nginx-1`、卷 `character-distill_ssl_certs`），业务网络实名 `character-distill_prod`（`docker-compose.prod.yml:156-157`）
    - nginx 挂 `./nginx/conf.d:/etc/nginx/conf.d:ro`（`:118`），主配置末尾 `include /etc/nginx/conf.d/*.conf`（`nginx/nginx.conf:164`）
    - 两个 server 都是 `server_name _`
    - 仓库里 `nginx/conf.d/` 只有 `.gitkeep`
13. **依赖锁**：`requirements.txt` 头部写明生成方式：`uv pip compile requirements.in -o requirements.txt --universal --python-version 3.12`
14. **CI 不跑 vitest**：`build.yml` 的 `frontend-lint` job 只跑 eslint；vitest 配置在 `vite.config.js:77-82`，测试放在 `src/**/__tests__/`
15. **SZ 服务器**：
    - 可用内存 810Mi，swap 2Gi 未用，磁盘余 25G
    - PG 16.14（容器 `character-distill-postgres-1`，库和用户都是 `charsim`）
    - 证书只含 `bookecho-shiyu.cn` 和 `www`；由 `ssl-renew.timer → acme.sh --cron → /opt/ssl-renew/acme-deploy` 签发并原子切换，DNS-01 验证（`dns_tencent`）
    - `/root/.acme.sh` 只有 root 能读，读取要 `sudo -n sh -c '…'`
16. **GlitchTip 官方要求**：
    - PG 14+；all-in-one 最低 256MB，推荐 512MB
    - `VALKEY_URL=""` 时为纯 PG 模式
    - 镜像 `glitchtip/glitchtip:6`
    - Sentry SDK 要设 `auto_session_tracking=False`
17. **React 19 官方接法**：在 `createRoot` 的 `onUncaughtError / onCaughtError / onRecoverableError` 三个钩子上挂 `Sentry.reactErrorHandler()`，要求 `@sentry/react` ≥ 8.6.0

### S0（只核坐标与现状，几分钟，全程只读）
- **读代码**：逐条复核 1–14
- **SZ**：
  - `sudo -n` 读 `/opt/ssl-renew/acme-deploy` 与 acme.sh 的域名配置，报告现状，不改
  - `docker network ls | grep prod`，核实网络名
- **两台**：报告 `/opt/character-distill/.env` 里 `NODE_REGION` 的值（这不是凭据，可以贴）。**SG 若缺这一项或值为 `cn-shenzhen`，停下报告**：那是注册时 `home_region` 写错的既有缺陷，不在本线顺手修
- 任何一条不成立，或 grep 返回 rc=2（报错不等于零命中），就停下报告

## 约束
- **DSN 为空时不启用**：前后端的 SDK 都不初始化，CSP 不变，行为与现在完全一致。A 段可以先合 main，不依赖 GlitchTip 就绪
- **不上报正文与用户输入**：
  - 后端 `send_default_pii=False`，`before_send` 删掉 `request.data` / `request.cookies`
  - 前端不带表单或正文
- **只做报错**：`traces_sample_rate=0`，`auto_session_tracking=False`
- **后端只走日志汇合点**（约束 2）：`auto_enabling_integrations=False`，保留默认的 LoggingIntegration；**不装** `[fastapi]` extra。否则 Starlette 集成会对同一个异常再报一次
- **SDK 在 lifespan 里初始化**（约束 3），放在首位
- **新配置一律走部署下发**（约束 8），不写进服务器 `.env`。唯一例外：GlitchTip 自身的密钥放在 SZ 的 `/opt/glitchtip/.env`，不经过 GitHub
- **GlitchTip 与主站生命周期隔离**：
  - 使用独立 compose project `glitchtip`，接入外部网络 `character-distill_prod`
  - 用独立的库和角色，不碰 `charsim`
  - nginx vhost 的上游用运行期解析：`resolver 127.0.0.11` 加变量 `proxy_pass`。**GlitchTip 容器不在时，主站 nginx 也必须能启动和 reload**
- **SZ 专属的东西不进 SG**：vhost 只拷进 SZ 的 `nginx/conf.d/`
- **改动范围**：属本段改动面的新发现直接修；需要拍板或会与其它线撞车的，停下报告；不自行记账

## A 段：代码（仓库内，先合 main）

### 步骤 1：[web] 日志进 stdout
- 在 lifespan 里、`install_log_collector()` 同处，给根日志器挂 `StreamHandler(sys.stdout)`
- 级别 WARNING，与环形缓冲同级，不改根日志器级别
- 格式带时间、级别、logger 名，`exc_info` 的堆栈完整输出
- 先写失败用例：lifespan 起来后 `logger.error(..., exc_info=True)` 的消息和堆栈都出现在 stdout（capsys）；再实现

commit：`fix(logging): write WARNING+ records to stdout so docker logs keeps them`

### 步骤 2：[web] 后端接 sentry-sdk
- `requirements.in` 加 `sentry-sdk`（不带 extra），按约束 13 的命令重新锁定
- lifespan **首行**调用 `init_error_reporting()`（新模块 `core/error_reporting.py`）
  - `SENTRY_DSN` 为空就直接返回
  - `release`、`environment` 由 SDK 自己读 `SENTRY_RELEASE` / `SENTRY_ENVIRONMENT`，缺省为 production
  - 设 tag `region = NODE_REGION`
  - 其余参数按上面的约束
- 测试（截获 transport，不发网络）：
  - DSN 空 → 不初始化
  - DSN 非空 → `logger.error(..., exc_info=True)` 产生一条带堆栈、带 `region` tag 的事件
  - 未捕获的路由异常只产生**一条**事件，且事件里没有 request body

commit：`feat(observability): report backend errors through sentry-sdk`

### 步骤 3：[web] 前端配置的单一来源：公开接口 + CSP
- 新模块 `web/client_config.py`：唯一读取 `SENTRY_FRONTEND_DSN` 的地方，配置、接口和 origin 解析都在这里。它提供三样东西：
  - `sentry_origin()`：从 DSN 解析出 `scheme://host`，DSN 空则 `None`
  - `router`：`GET /api/client-config`，返回 `{sentry_dsn, release, region}`，DSN 空则 `{}`（写法照 `_announce_router`）
  - `release` 读 `SENTRY_RELEASE`，与后端同一来源
- `web/server.py` 只加一行 `include_router` 和一条 `PUBLIC_PATHS`，不在 server.py 里写逻辑（71 线也在改这个文件）
- `web/security.py` 的 `connect-src`：`sentry_origin()` 非空时追加它，否则保持原值
- 测试：
  - DSN 空 → 接口返回 `{}`，CSP 与现在逐字相同
  - DSN 非空 → 接口返回 DSN，CSP 含该 origin
  - 未登录可访问
  - demo 账号可访问

commit：`feat(observability): serve the frontend reporting config from one source`

### 步骤 4：[frontend] 前端接 @sentry/react
- `package.json` 加 `@sentry/react`（≥ 8.6.0）
- 初始化逻辑收进新模块 `src/observability.js`，只导出两样：`initErrorReporting()` 和 `reactErrorHooks()`。`main.jsx` 只负责调用
- `main.jsx` 的启动链：
  1. 页面加载 → `fetch('/api/client-config')`
  2. 拿到 `sentry_dsn` 就 `Sentry.init(...)`（`tracesSampleRate: 0`、`sendDefaultPii: false`；`release`、`region` 取接口返回值）
  3. 然后 `createRoot(root, { onUncaughtError, onCaughtError, onRecoverableError })`，三个钩子都挂 `Sentry.reactErrorHandler()`（约束 17）
  4. 最后 render
  - fetch 失败或 DSN 空 → 不初始化，照常 render
- 自写的 `ErrorBoundary` **保留不改**：界面与重置逻辑不动，它捕获的错误经 `onCaughtError` 上报
- 全局异常和未处理的 Promise 拒绝由 SDK 的默认集成捕获，不另写监听
- 测试（vitest）：
  - 配置为空 → 不调用 `Sentry.init`，照常渲染
  - 配置非空、子组件渲染时抛错 → 现有 fallback 出现，上报被调用一次
- **需要 Shiyu 手测确认**：生产构建下首屏没有可感知的延迟（配置请求是同源的一次 GET）

commit：`feat(observability): report frontend errors through @sentry/react`

### 步骤 5：[ci/deploy] 进门与下发
- `build.yml` 的 `frontend-lint` job，在 eslint 之后加一步 `npm test`
- `deploy.yml` 顶层 env 加两行，写法照 `ALERT_EMAIL`：
  - `SENTRY_DSN: ${{ vars.SENTRY_DSN }}`
  - `SENTRY_FRONTEND_DSN: ${{ vars.SENTRY_FRONTEND_DSN }}`
- `deploy.yml` 两个 job 的 `envs` 列表加上这两个变量
- `docker-compose.prod.yml` app 的 `environment` 加：
  - `SENTRY_DSN=${SENTRY_DSN:-}`
  - `SENTRY_FRONTEND_DSN=${SENTRY_FRONTEND_DSN:-}`
  - `SENTRY_RELEASE=${APP_IMAGE_TAG:-}`
- `DEPLOY.md` 的「由部署下发的配置」表补两行
- `DEPLOY.md:11/:242` 的「阿里云云解析」改为 DNSPod（2026-09-25 实测 NS 为 `save.dnspod.net` / `hill.dnspod.net`；acme.sh 的 `dns_tencent` 签发成功也依赖 NS 在 DNSPod）

commit：`ci(observability): gate frontend tests and deliver reporting DSNs via repo vars`

### A 段验证
- 本地只跑本段新增或改动的测试文件（后端用 docker PG；前端 `npx vitest run <文件>`），不跑任何全量；合并门是分支 CI
- 分支 CI 若因 main 上既有的 vitest 失败而红，停下报告，不在本线修
- 报告：各 commit、测试末行、CI 链接；报告里不出现本地全量的数字
- A 段审计通过后合 main；合并只做 git 操作，不跑测试

## B 段：SZ 部署（服务器写操作，Shiyu 已授权；A 段合入后做）

**前置**：`dig +short errors.bookecho-shiyu.cn` 返回 `47.107.42.111`，否则停下。该记录只加「默认」线路、不加「境外」线路：SG 的后端要连到 SZ。

**HTTPS 是硬前提，不能先跑 HTTP**：主站下发了 `Strict-Transport-Security: includeSubDomains`（`nginx/nginx.conf:81`、`web/security.py:17-18`），访问过主站的浏览器会强制用 HTTPS 访问 `errors.`。所以步骤 7 的证书必须在步骤 8 之前完成。

### 步骤 6：[repo] SZ 专属文件（进仓库，不进任何流水线）
- `deploy/sz/glitchtip/docker-compose.yml`：
  - 顶层 `name: glitchtip`
  - 服务 `glitchtip`，镜像 `glitchtip/glitchtip:6`，`SERVER_ROLE=all_in_one`，`VALKEY_URL=""`
  - `mem_limit: 384m`，`restart: unless-stopped`，日志轮转与主站一致
  - 网络：外部网络 `character-distill_prod`
  - `DATABASE_URL` 指向主机 `postgres` 上的独立库 `glitchtip`
  - `GLITCHTIP_DOMAIN=https://errors.bookecho-shiyu.cn`，`ENABLE_USER_REGISTRATION=False`
  - `SECRET_KEY`、`EMAIL_URL` 从同目录 `.env` 读
  - 以上变量名按 GlitchTip 官方文档核一遍
- `deploy/sz/nginx/glitchtip.conf`：
  - `server_name errors.bookecho-shiyu.cn` 的 443 server，另有 80 → 443 跳转
  - `resolver 127.0.0.11 valid=30s;`，`set $glitchtip http://glitchtip:8000; proxy_pass $glitchtip;`
  - 按官方 nginx 示例带 Host / X-Forwarded-* 头
- `deploy/sz/README.md`：一段话写明这两个文件在服务器上的落点，以及它们为什么不走发版

commit：`feat(deploy): SZ-only GlitchTip stack and vhost`

### 步骤 7：[SZ] 证书
复用现有签发管道，把 `errors.bookecho-shiyu.cn` 加进现有证书的 SAN，不另起第二张证书或第二条部署链。
1. 先读清 acme.sh 当前的验证方式，以及 `acme-deploy` 的切换和回滚逻辑
2. 按它的方式重签，走它的部署
3. 验证：`openssl s_client` 显示新证书含三个 SAN，主站正常

管道不支持加 SAN 的话，停下报告，不自行另建方案。

### 步骤 8：[SZ] 上线
1. 在 PG 里建角色和库 `glitchtip`。口令只写进 `/opt/glitchtip/.env`，不回显
2. scp `deploy/sz/glitchtip/` 到 `/opt/glitchtip/`，执行 `docker compose -p glitchtip up -d`，等到健康
3. 拷 `glitchtip.conf` 进 `/opt/character-distill/nginx/conf.d/`，`docker exec character-distill-nginx-1 openresty -t -c /etc/nginx/nginx.conf` 通过后 reload
4. **隔离验证**：`docker compose -p glitchtip stop` → `openresty -t` 与 reload 仍然通过、主站正常 → 再 `start`
5. 验证：`https://errors.bookecho-shiyu.cn` 可访问；`docker stats` 里 glitchtip 在上限内；主站正常
6. 在 GlitchTip 里：
   - 建管理员（邮箱用 `ALERT_EMAIL` 的值）、组织、两个项目（backend、frontend），拿到两个 DSN
   - 配置「新问题 → 邮件」告警
7. 把两个 DSN 交给 Shiyu，由他填进仓库变量 `SENTRY_DSN` / `SENTRY_FRONTEND_DSN`，再跑一次 deploy（`both`）
8. **冒烟**：两台各触发一次后端测试错误、一次前端测试错误。GlitchTip 各出现一条，`region` 分别是两台的值，邮件收到

### B 段报告
每步贴原始输出；凭据一律不贴；任何失败即停，不做计划外变更。

## E 段：lifespan 注册的配对撤销（123 已合入，A 段合入后做；与 B 段互不阻塞）

### 已查实的约束（基线 origin/main `2d79423`，123 已合入）
1. **注册现状**：`web/server.py::_lifespan` 里的进程级注册全部没有撤销：
   - `install_llm_gate(app)` → `set_call_guard(geo_call_guard)`（`web/llm_gate.py:103`；读写口 `adapters/llm_adapter.py:225/231`）
   - `install_log_collector()`、`install_alert_handler()`，以及 A 段新增的 `install_stdout_logging()` 与 `init_error_reporting()`
   - `set_main_loop(loop)` → `scheduling.set_loop_submitter(...)`
2. **123 定下的语义**：`core/scheduling.py:48-54`，未注册时 `submit_to_main_loop` 当场抛 `RuntimeError`，没有退路。所以关停后撤销投递器，迟到的投递会明确报错，不会投到已关闭的 loop 上。这正是想要的行为
3. **两个 handler 的去重方式不同**：
   - `AlertHandler` 按类去重（`core/alerting.py:173`）
   - 环形缓冲和 stdout handler 靠 `addHandler` 按对象身份去重
4. **执行器不在范围内**：`loop.set_default_executor(...)` 不需要本仓撤销。`python -m web.server` 走 `uvicorn.run`，最终由 `asyncio.run` 执行；按 Python 文档，`asyncio.run` 收尾时会调用 `loop.shutdown_default_executor()`。S0 核对 uvicorn 版本确实走的是 `asyncio.run`，不是就停下报告
5. **`install_demo_gate(app)` 不动**（`web/server.py:155-157`）：它必须在导入期、路由登记之后执行，放进 lifespan 就会变成静默的空操作
6. **测试侧**：
   - `tests/conftest.py::registered_globals()`（:246）退出时会断言「作用域内至少有一处注册还在」。它现在有两种用法：一是包住生产 `_lifespan`（`test_message_backfill.py::test_C9_shutdown_backfills_the_queues`、`test_llm_access_gate.py:1054` 的 l11），二是作测试 app 的 lifespan
   - 生产 lifespan 自己撤销之后，第一种用法退出时两处注册都已清空，这条断言必然红
   - C9 的最后一条断言 `_registrations() == before` 本身就是「生产关停还原了注册」的锁
7. **台账 113** 写着「已知边界：生产关停路径不撤销，此条不变」，本段做完要改写这一条

### 约束
- **撤销与注册写在同一处**：每个 `install_*` 返回自己的撤销函数。lifespan 用 `contextlib.AsyncExitStack` 收集这些撤销，关停时按注册的逆序执行。不另起一套「注销清单」
- 没有真正装上的（比如 `ALERT_EMAIL` 为空，或者按类去重时发现已经装过），返回空操作。**只撤销自己装上的**，按各自原有的去重方式判断
- `deps` 的主 loop 与投递器一起撤销：`set_main_loop` 返回的撤销函数把两者都还原成未注册
- `init_error_reporting()` 的撤销是 flush 后关闭 client。它最先注册，按逆序最后撤销，这样关停过程中产生的 ERROR 仍能被上报
- 现有的关停动作（取消后台任务、flush 队列）保持原样，也保持先后顺序；ExitStack 的撤销在这些动作之后执行（它们要用到投递器）

### 步骤 9：[web] lifespan 配对撤销
- 各个 `install_*` 和 `set_main_loop` 改为返回撤销函数；`_lifespan` 用 `AsyncExitStack` 串起来
- `:97-102` 那段注释补一句：注册与撤销成对出现，逆序撤销
- 先写失败用例：用 `TestClient(server.app)` 启动再退出，之后调用守卫、投递器、主 loop 都是未注册，根日志器上不剩本仓的 handler
- 测试侧跟着调整：
  - C9、l11 不再用 `registered_globals()` 包住生产 lifespan，由生产 lifespan 自己还原。C9 的 `_registrations() == before` 就是回归锁
  - `registered_globals()` 只保留「测试 app 的 lifespan」这一种用法，文档注释同步改
  - `tests/test_usage_identity_context.py::_LoopSubmitter` 若因此失效就改，没失效不动
- 鉴别力自测：去掉任意一项撤销 → 新用例或 C9 必须变红
- 台账 113：「已知边界」改为已收敛，写上本 commit

commit：`fix(lifespan): pair every process-wide registration with its undo`

### E 段验证
只跑 `tests/test_message_backfill.py`、`tests/test_llm_access_gate.py`、`tests/test_usage_identity_context.py`、`tests/test_stdout_logging.py`，以及本步新增的用例；合并门是分支 CI。

## D 段：静默吞错补日志（70 处待判），并加回归锁（123、119 均已合入；B 段已上线）

### 已查实的约束（基线 origin/main `91e8b0b`）
1. **扫描口径与锁的判据一致**：新锁用 `_always_raises` 判断是否必然抛出，所以统计也按同一口径做，不能用「分支里出现过 `raise` 就排除」这种更宽的口径，否则统计出来的数目和锁找到的对不上。
   - 扫描范围：`storage/postgres_store.py`、`web/`、`core/`、`adapters/`
   - 只看宽 `except`：`Exception`、`BaseException`、裸 `except`，或元组里含这两者之一
   - 排除：`_always_raises(分支体)` 为真（必然抛出；注意 `raise HTTPException` 不算抛出）；分支里有 `print`（归第 3 节 119 的锁管，新锁跳过这类分支，避免同一处被两把锁重复判）；调用了 logger 的任一级别，或用了 `nonfatal`
   - **在 `91e8b0b` 上的结果：共 70 处要处理。** 行号会漂，以开工时重跑为准：
   - (a) 分支里完全没有 `raise`，也没用到异常变量，**51 处**：
     - `storage/postgres_store.py`：update_published_card:4993
     - `web/cross_border_sync.py`：forward_dm_to_peer:54、forward_card_to_peer:77、forward_card_to_peer:104、forward_invite_code_to_peer:201、forward_invite_code_delete_to_peer:229、forward_user_profile_to_peer:263
     - `web/geo_guard.py`：is_whitelisted_base_url:189
     - `web/routers/admin.py`：list_users_federated:131、list_users:55、list_users_federated:86、admin_user_detail:668、list_users_federated:123
     - `web/routers/auth.py`：test_embedding:698、get_user_online_status:916
     - `web/routers/card.py`：export_card:84
     - `web/routers/chat.py`：_decide_retraction:117、_ensure_session:179
     - `web/routers/distill.py`：start_session:1340
     - `web/routers/group.py`：_rebuild_group_session:97、create_group:309、_rebuild_group_session:130、_filter_valid_card_ids:432、cleanup_orphan_card_ids:476、send_message:529、_rebuild_group_session:121、_rebuild_group_session:149、event_generator:636
     - `web/routers/history.py`：resume_session:215
     - `web/routers/market.py`：_get_ip_location:44、_card_json_obj:429、publish_card:543、get_author:247、publish_card:520
     - `web/routers/voice.py`：voice_status:65、voice_status:71、upload_custom_voice:157
     - `web/server.py`：<module>:20
     - `core/alerting.py`：emit:111
     - `core/chat_engine.py`：_should_retract:1370、generate_reunion_greeting:1489、load_affinity:624、_evaluate_affinity:697、_build_time_awareness_block:1084
     - `core/clock.py`：_safe_zone:25、is_valid_timezone:41
     - `core/rag.py`：index:275、_peek_dimension:546
     - `core/scene_indexer.py`：index_scenes:90
     - `adapters/llm_adapter.py`：_classify_retry:44、aclose:685
   - (b) 分支里有 `raise`，但不必然抛出，**5 处**。身份线原先的口径把它们漏掉了；其中后两处是 `raise HTTPException`，失败只剩给用户的 500，服务端不留痕：
     - `core/distiller.py`：_parse_json_with_retry:955、_parse_json_with_retry:998
     - `core/embeddings.py`：_call_api_bounded:296
     - `web/routers/history.py`：export_session:108
     - `web/routers/voice.py`：voice_synthesize:232
   - (c) 用到了异常变量，疑似「交给下游」，**14 处**。要逐处确认交给了谁：
     - `web/llm_resolution.py`：resolve_llm:75
     - `core/agent/tools.py`：execute:207
     - `core/alerting.py`：_send:119
     - `core/distiller.py`：_thread_run:372、_thread_run:1983、_format_one_group:2171、_reduce_thread:2092
     - `core/moderation/card_guard.py`：judge_card:160、judge_card:158、_run:145
     - `adapters/llm_adapter.py`：chat:769、async_chat:816、_stream:868、chat_with_tools:999
2. **现成的锁**：`tests/test_failure_alerting.py` 第 3 节，负责「`print` 后吞掉」这一种形态。
   - 可复用的部分：`_SCAN_ROOTS`、`_always_raises`、`_is_http_exception`、`_enclosing_scope`、父节点表的构造、`_scan_files`
   - 豁免表 `_KEPT_PRINTS` 以 `(路径, 函数名)` 为键、理由跟着条目走，找到的结果与豁免表做**相等**比较（新增违规和豁免失效都会变红）
3. **现成的「吞掉但留痕」构造**：`core/nonfatal.py::nonfatal(source, what, level=...)`，模块注释自称是这个意图的「唯一定义」。仓内已有 16 处在用。**它只有 async 版本**（`@asynccontextmanager`）
4. **身份线读过代码、确认有意吞掉的**：
   - `core/alerting.py::emit`：告警处理器自身出错不能写日志，否则递归
   - `web/server.py` 模块级：启动时重设 stdout 编码，日志系统还没装好
   - `core/clock.py::is_valid_timezone`：校验函数，异常本身就是「不合法」这个结果
   - `adapters/llm_adapter.py::_classify_retry`：尽力解析 `Retry-After`，解析不出就用默认退避
   - `adapters/llm_adapter.py::aclose`：尽力关闭客户端
5. **可能有意、但需要读代码判断的**：`web/geo_guard.py::is_whitelisted_base_url`、`core/clock.py::_safe_zone`（身份线建议补 WARNING：它意味着库里存了坏数据）、`core/rag.py::_peek_dimension`、`web/routers/market.py::_get_ip_location`
6. **与 C 段的关系**：`core/alerting.py` 在 C 段会整个退役，它的两个豁免条目届时一并删除
7. **与 distill 线可能撞车**：`core/distiller.py` 有 6 处在本段范围内（(b) 2 处、(c) 4 处），而 distill 线正在频繁修改这个文件。锁的豁免表按函数名登记，那边一旦改名或挪动函数，本锁就会变红

### 级别口径（已定）
- **ERROR**：用户拿到的结果是错的，或者数据没写进去 / 没同步过去，而且不会自愈。例如跨节点同步失败导致两地数据不一致。这类会进 GlitchTip 并发邮件
- **WARNING**：有兜底，用户拿到的结果仍然正确，或者下次会自愈；只是降级。这类只进 stdout 和日志面板，在 GlitchTip 里只作为面包屑
- **豁免**：异常本身就是返回结果（校验函数）；位于日志链自身；发生在日志系统装好之前。每一条都要写明理由
- 日志消息里写 `source` / `what` 和定位信息（session_id、card_id、路径），**不写正文和用户输入**

### 约束
- **锁只有一套判据**：在 `tests/test_failure_alerting.py` 里新增一个测试，复用上面第 2 条列出的那些函数，不另起新文件、不另写一份扫描器
- **新锁的规则**：宽 `except` 如果不必然抛出（按 `_always_raises` 判），分支里就必须调用 logger 或 `nonfatal`；否则必须登记在新的豁免表里，写明理由
- 「交给下游」的 14 处**也要登记**进豁免表，理由写清交给了谁（队列、返回值、重试预算……）。不用「用到了异常变量」来自动放行，那等于给以后的静默吞错留了一个口子
- 同一个函数里有多处的，豁免表按处数登记，比较时要能区分数量
- 补日志时优先用 `nonfatal`，不在各处手写格式。同步函数里 `nonfatal` 用不了，这一处见步骤 10 的 S1
- **补日志不是唯一修法，先判断这个宽 `except` 该不该存在**，按下面顺序选，前面的能用就不用后面的：
  1. **能窄化就窄化**：捕获的其实是可预期的输入错误，就只捕获那个具体类型，不再是宽 `except`，也就不用补日志。例：`voice.py::voice_synthesize` 捕获的是请求体不是合法 JSON，应只捕获 JSON 解析错误，返回 400 是对的
  2. **能去掉就去掉**：捕获后只是把真故障包装成别的响应，就删掉这个 `try`，交给全局异常处理器（`server.py` 的 `_global_exception_handler`，已经会记日志、返回 500）。例：`history.py::export_session` 把数据库故障伪装成 404「会话不存在」，用户和服务端都被误导；`get_session_owned` 找不到时本来就返回空，不需要这个 `try`
  3. 以上都不适用，确实要吞掉继续跑，才补 `nonfatal` 或 logger
  - S1 的分类表加一列「修法：窄化 / 去掉 / 补日志 / 豁免」
- 只改这 70 处，其它代码不动；每个文件的改动都要能说清是哪一类、为什么
- 开工前（S1）先查 distill 线有没有未合并的分支改到了 `core/distiller.py` 里上述函数（`git log origin/main..<distill 分支> -- core/distiller.py`，并看 diff 是否碰到这几个函数）。碰到了就在 S1 报告里列出来，由我协调顺序；不自行给这几处开临时豁免，也不在锁里排除 distiller.py
- distiller 的 4 处 (c) 只需要登记豁免、不改 distiller.py 的代码，不会与 distill 线的代码冲突；以后那边改名或挪动这些函数时锁会变红，这正是锁该做的事。所以锁的失败信息要写清「去豁免表里改登记，并写明理由」，让任何一条线看到红都知道怎么处理

### 步骤 10：[core/web/adapters/storage] 静默吞错补日志 + 回归锁

**S1（只读，出分类表，停下等审计）**
- 在当时的 main 上用上面的口径重跑扫描，报 (a)(b)(c) 三类的数目，以及与第 1 条的逐项差异
- 逐处读代码，给出一张表，每行：`路径 · 函数 · 修法（窄化 / 去掉 / 补日志 / 豁免）· 级别（补日志时填 ERROR 或 WARNING）· 一句理由`
- 统计需要补日志的**同步函数**有几处，并给出建议：是给 `nonfatal` 加一个同步版本（两者共用同一个上报函数，格式只有一份），还是同步处直接用模块 logger。给出理由，由我裁决
- S1 不改任何文件

**S2（审计通过后实施）**
- 先写锁：新测试 `test_no_silent_broad_except_left_in_production_code`，此时应该是红的，红的内容正好是 S1 表里的全部条目
- 再按分类表逐文件改，每改完一个文件跑一次锁，看剩余列表在减少
- 豁免表最终的条目 = 分类表里的「豁免」+「交给下游」
- 鉴别力自测：随便挑一处已补的日志删掉 → 锁变红；随便删掉一条豁免 → 锁变红

commit：先 `test(failure-alerting): lock silent broad excepts`（锁 + 豁免表，此时红），再按文件分组若干个 `fix(<模块>): log the failures that were swallowed silently`，最后一个 commit 让锁变绿。中间 commit 锁为红是预期的，所以全部改完再一次推送，推送后分支 CI 必须全绿

### D 段验证
- 只跑 `tests/test_failure_alerting.py`，加上被改文件对应的已有测试文件；不跑全量；合并门是分支 CI；合并只做 git 操作，不跑测试
- 合并后：GlitchTip 里观察一天，确认没有被新的 ERROR 刷屏。如果刷屏，说明哪一处的级别定错了，回来改级别，不是去关告警

## C 段：退役自建组件（分两步：C1 现在做，C2 等 GlitchTip 官方修复后做）

### 已定决策（Shiyu 拍板，2026-09-26）
- **日志面板整个删掉**，不留跳转链接：报错只在 GlitchTip 一处看，GlitchTip 的地址收藏在浏览器里即可
- **`ALERT_EMAIL` 下发链路和旧邮件告警一起删**，但推迟到 C2：
  - 查实：我们镜像里的 `django-vtasks` 是 3.1.0，**不含**官方修复 !27（调度器遇到一次瞬时的数据库错误就会永久退出，之后报错照收、告警不发，外面看不出异常）
  - 在官方修复装上之前，旧邮件告警是两台服务器各自独立发信、不经过 GlitchTip 的兜底渠道
- **GlitchTip 自身的监控用轻量方案，不在深圳新增任何常驻组件**（深圳可用内存约 609MB）：
  - 网站和 GlitchTip 能否访问 → 阿里云站点监控（外部探测，深圳零内存），报警走短信 **〔2026-09-27 修订：改走邮件，见 W 段〕**
  - 调度器 bug → 靠官方修复根治：先固定镜像版本，官方发布含修复的版本后再升级
  - 不做「监控系统本身的死人开关」（Prometheus、采集代理、边车容器）：内存和规模都不值得。已知的剩余风险：官方修复没有覆盖「嵌入式运行时进程半死不活」这一边角情况，接受 **〔2026-09-27 修订：该剩余风险不再接受，改由 W 段的宿主机看门狗覆盖；Prometheus、采集代理、边车容器仍然不做〕**
- **GlitchTip 通知的用法**：修好的问题在 GlitchTip 里标「已解决」，复发时会再通知（查实：复发时会删掉旧的通知记录，所以同一条规则会重新发）；已知但暂时不修的，标「忽略」，不要标「已解决」

### 已查实的约束（基线：observability 分支 `fc39f0c`，即 D 段头部；已与最新 main `d226a20` 比对：自 D 段分叉点 `91e8b0b` 以来，main 没有改动本段涉及的任何文件）
1. **日志面板（C1 要删）**：
   - `core/log_collector.py`：`install_log_collector`（:41）、`get_recent_logs`（:58）
   - `web/server.py:78` import，`:125` `stack.callback(install_log_collector())`
   - `web/routers/admin.py:22` import，`:602` 路由 `GET /api/admin/logs`（:609 调用）
   - 前端：`web/frontend/src/api/client.js:383` `getLogs`；`components/AdminPanel.jsx` 的 `SystemLogTab`（约 :1622）用 `Promise.all` 同时加载日志和蒸馏任务，两者共用 loading 和错误提示，标题是「系统日志与任务」。只删日志部分，**蒸馏任务保留**，加载函数和状态要跟着拆干净，标题改为「蒸馏任务」
   - 测试：`tests/test_log_collector.py` 整个删；`tests/test_nonfatal.py:26/34/39` 靠 `get_recent_logs` 判断「失败留下了痕迹」，改用 `caplog` 判断同一件事；`tests/test_lifespan_undo.py:21` 引用 `RingBufferHandler`，去掉相应断言
   - 注释：`core/nonfatal.py:3`、`core/stdout_logging.py:7`、`core/alerting.py:4/14/161` 提到面板的句子要改写
   - 我查过，前端没有针对这一页的测试
2. **GlitchTip 镜像（C1 要固定版本）**：`deploy/sz/glitchtip/docker-compose.yml:16` 是浮动标签 `glitchtip/glitchtip:6`。Docker 官方文档说明，标签可变，发布者可以把同一个标签指向新镜像。执行方之前读过运行中镜像的源码，版本是 6.2.6
3. **旧邮件告警（C2 要删）**：
   - `core/alerting.py`：`AlertHandler`、`install_alert_handler`，读 `ALERT_EMAIL`（:38）；`web/server.py:80` import，`:128` 起挂载
   - 下发链路：`deploy.yml:49-53`（注释和 `ALERT_EMAIL: ${{ vars.ALERT_EMAIL }}`）、`:176` 和 `:370` 的 `envs`；`deploy.yml:54` 的注释写着「机制同 ALERT_EMAIL」，要改成独立说明；`docker-compose.prod.yml:91`、`DEPLOY.md:153`
   - 注释：`core/email_service.py:72` 把告警列为发信的使用方
   - 测试：`tests/test_alerting.py` 整个删；`tests/test_failure_alerting.py` 的 `alerts` 夹具（:31）装的是真的 `AlertHandler`；`tests/perf/alerting_mutations.py:140` 那条变异改的是 `alerting.ALERT_LEVEL`；`tests/test_message_backfill.py` 引用了 alerting
   - 现成的替代接收端：`tests/test_error_reporting.py:58` 的 `recorder` 夹具（把录制用的 transport 注入 `sentry_sdk.init`，全程不出网）
   - D 段豁免表里 `core/alerting.py::emit`、`::_send` 两条
   - **不改**：`tests/perf/alerting_level_census.py`。它是按 git 历史回放、复现台账 119 数字的产数脚本，不 import 要删的模块；改了它，历史读数就复现不出来了
4. **日志路径上的现有机制（C1、C2 都是从这条路径上拿掉组件，逐个核对前提）**：
   - 根日志器级别：全仓没有 `setLevel` / `basicConfig`，保持默认 WARNING，INFO 记录在进入 handler 之前就被丢弃
   - 根上现有的出口：环形缓冲（WARNING+，C1 删）、stdout（WARNING+，保留）、`AlertHandler`（ERROR，按「出错模块 + 异常类型」每小时最多一封，C2 删）、Sentry 的 LoggingIntegration（ERROR 生成事件，保留）
   - **前提一**：根上只要还挂着至少一个 handler，Python 的 lastResort 就不生效（A 段步骤 1 的依据）。C1、C2 之后根上仍有 stdout handler（A 段审计过，它是独立模块，不依赖环形缓冲），这个前提不变
   - **前提二**：E 段的 `AsyncExitStack` 按注册的逆序撤销，各项撤销互不依赖。去掉 `install_log_collector` 的那一项不改变其余项的顺序关系；`test_lifespan_undo.py` 里涉及它的断言按约束 1 一并去掉
   - **前提三**：Sentry 的 LoggingIntegration 是 sentry-sdk 自带的，不知道也不依赖我们的环形缓冲或 `AlertHandler`
5. **规模与时限**：
   - C2 升级 GlitchTip：当前实际占用 173–198MiB，硬上限 384MB；深圳可用约 609MB。升级后三个数字要重新实测
   - 阿里云站点监控：5 分钟探测一次，两个探测点都失败才报警，所以从出事到收到短信**最长约 10 分钟**。这个延迟可以接受，因为它防的是「几小时甚至几天没人发现」
6. **其余文档**：`README.md`、`AGENTS.md`、`docs/TECHNICAL_REPORT.md`、`DEPLOY.md` 中描述日志面板或邮件告警的段落，按 C1、C2 各自删掉的内容分别更新

### 约束（C1、C2 共用）
- 只删本段列出的东西，其余不动；新发现属于改动面的直接修；只有会与其它线撞车、或需要 Shiyu 拍板时才停下报告；不自行记账
- 删掉的是机制，不是日志：stdout 和 Sentry 两个出口不动

### 步骤 11（C1）：删除日志面板 + 固定 GlitchTip 镜像版本
**S0（只读，几分钟）**：在分支头部复核约束 1、2 的坐标；服务器上用 `docker inspect` 读出 GlitchTip 容器实际运行的镜像版本，确认是 6.2.6。任何一条不成立就停下报告。

- 分支：从 D 段头部另开 `worktree-observability-retire`，在同一个 worktree 里切过去做。**合并顺序：D 先合，C1 后合**
- **删日志面板**，按约束 1 的清单：
  - 先改测试：`test_nonfatal.py` 改用 `caplog`、`test_lifespan_undo.py` 去掉 `RingBufferHandler` 的断言，这时应该依然全绿
  - 再删 `core/log_collector.py`、lifespan 里的挂载、`/api/admin/logs`、前端的日志部分和 `getLogs`，删 `test_log_collector.py`，改注释和文档
- **固定版本**：`deploy/sz/glitchtip/docker-compose.yml:16` 改成 S0 读到的完整版本号（`glitchtip/glitchtip:6.2.6`），不做升级
  - 服务器上按 `deploy/sz/README.md` 的「scp 覆盖 + diff 验证」流程同步
  - 因为版本号没变，这一步**不需要**重建 GlitchTip 容器；报告里贴出 diff 与仓库一致的证据即可
- 残留检查：`git grep -n "log_collector\|get_recent_logs\|RingBufferHandler\|getLogs"`，除 `docs/specs/` 和台账的历史记录外零命中

commit：`test: check nonfatal traces via caplog`、`refactor(observability): retire the ring-buffer log panel`、`chore(glitchtip): pin the image to the running version`

**C1 验证**：本地只跑本段改动或删除涉及的测试文件（库用 docker 起的 PG），加上 `npm test` 里 AdminPanel 相关的文件；不跑全量；合并门是分支 CI；合并只做 git 操作，不跑测试。上线后确认管理后台只剩「蒸馏任务」，页面没有报错。

### Shiyu 的手动步骤（C1 之后做，阿里云控制台，不涉及服务器）
在阿里云「云监控 → 站点监控」建两个 HTTPS 探测任务，**2 个国内探测点、每 5 分钟一次，两个点都失败才报警**，报警联系人设为你的手机号 **〔2026-09-27 修订：通知方式改为邮件 `bookecho@163.com`，见 W 段〕**：
1. `https://errors.bookecho-shiyu.cn/`：GlitchTip 是否能访问
2. ~~`https://bookecho-shiyu.cn/health`~~ → **`https://bookecho-shiyu.cn/api/health`**：主站存活 **〔S0 后修订：`/health` 被 `nginx/nginx.conf:92-96` 限为 127.0.0.1，公网必 403，见 W 段「S0 后定稿」〕**
按阿里云的计费，国内探测点每 1 万次 10 元，两个任务合计约 35 元/月。

### 步骤 12（C2）：删除旧邮件告警与 `ALERT_EMAIL` 链路（等官方修复）
**开工条件**：GlitchTip 发布了包含 vtasks 修复的版本。判断办法：拉取新版本镜像，在里面 `grep -n "Scheduler loop failed" .../django_vtasks/scheduler.py`，有命中才算包含修复。

**S0**：复核约束 3 的坐标，行号以届时为准。

1. **先升级 GlitchTip**：
   - `deploy/sz/glitchtip/docker-compose.yml` 改为新的完整版本号，scp 覆盖 + diff 验证，然后 `docker compose -p glitchtip up -d`
   - 升级后用 `docker stats` 实测：GlitchTip 仍在 384MB 上限内，深圳可用内存没有明显下降；否则回退到旧版本并报告
   - 容器里复查：告警调度任务的上次运行时间在 2 分钟以内
2. **合并前闸门**（开 PR 之前做，结果写进报告）：
   - 在生产**两台**各触发一条后端测试错误、一条前端测试错误
   - 核对 GlitchTip 里出现了对应事件，region 分别是 `cn-shenzhen` 和 `sg-singapore`；`bookecho@163.com` 收到了 GlitchTip 发的告警邮件（由 Shiyu 确认）
   - 两台都通过才开 PR；任何一台没接住就停下报告
3. **删除**，按约束 3 的清单：
   - 先改测试：把 `recorder` 从 `test_error_reporting.py` 移到 `tests/conftest.py`，两个文件共用；`test_failure_alerting.py` 的 `alerts` 夹具改为录制 Sentry 事件。各条断言的含义不变（ERROR 产生一个事件，WARNING 不产生；未捕获的 500 产生一个事件），这时应该依然全绿
   - 变异 `alerting_mutations.py:140` 的目标会消失：在 `init_error_reporting` 里把 LoggingIntegration 的事件门槛显式写出来，复用 `core/nonfatal.py` 的 `DEFAULT_LEVEL`，让这条变异改为针对它
   - 再删 `core/alerting.py`、lifespan 里的挂载、`ALERT_EMAIL` 下发链路、D 段豁免表里 alerting 的两条、`test_alerting.py`，改注释和文档
   - 重新生成 `alerting_red_lines.json`，贴出 lock_coverage 的通过输出
   - 残留检查：`git grep -n "AlertHandler\|install_alert_handler\|ALERT_EMAIL"`，除 `docs/specs/` 和台账的历史记录外零命中

commit：`chore(glitchtip): upgrade to <版本> with the scheduler fix`、`test: record alerts as GlitchTip events`、`refactor(observability): retire email alerting`、`chore(deploy): drop the ALERT_EMAIL delivery`

**C2 验证**：同 C1。上线后两台 app 的启动日志里没有 `ALERT_EMAIL` 相关的 WARNING。GitHub 上的仓库变量 `ALERT_EMAIL` 由 Shiyu 自行删除或保留，不影响运行。

## W 段：GlitchTip 看门狗（宿主机 cron + 与 GlitchTip 互为心跳，全部走邮件）

> 提示词开头带上：`@search-first @source-driven-development @incremental-implementation @tdd`
> 本段分两步：**步骤 13-S0（只读，产出报告文件）→ 我把 S0 结果补进本节「待 S0 定」的参数 → Shiyu 拍板 → 步骤 13 实现**。设计已定，S0 只定参数，不重新选方案。

### 已定决策（Shiyu 拍板，2026-09-27）
- **目的**：GlitchTip 半死（报错照收但不通知，或收了不显示）时，要么自动恢复，要么邮件通知 Shiyu。监控系统本身不能是静默失灵的单点。
- **选型依据**（调研后定，不再推翻）：
  - 业界主流是心跳监控（dead man's switch）：定期 ping，缺席即报警。有出处：Healthchecks.io 文档、2026 年多篇 cron 监控横评
  - 上游 vtasks #6 的报告者遇到同一问题（3.5 天无报警），他们的解法就是外部读 `vtasks_last_run:<task>` 的秒龄并在过期时自动重启。有出处：gitlab.com/glitchtip/django-vtasks #6
  - 官方修复 !27 自述**不覆盖**：嵌入式模式的半死、broker 卡住（stall）、#7 的永久卡死原因未明。有出处：MR !27 描述。所以即使将来升到含修复的版本，看门狗仍有用
  - 不选：Prometheus（常驻，否决过）、自建镜像换 vtasks 3.2.0（约 700 行未经官方配套测试的改动，且仍不覆盖嵌入式半死）、第三方心跳服务（多一个外部依赖）、短信（Shiyu 定：邮件足够）
- **组成**（深圳不新增常驻组件、不新增容器）：
  1. **看门狗**：宿主机 cron **每 1 分钟**跑一个脚本 〔S0 后修订，见「S0 后定稿」〕，只读查询两个数：调度器心跳秒龄、最老可执行 QUEUED 任务秒龄
  2. **自愈**：任一超阈值 → `docker restart` GlitchTip 容器一次；冷却期内再次超阈值 → 不再重启，发邮件给 `bookecho@163.com` 〔S0 后修订：发信复用 GlitchTip 容器自己的邮件配置，见「S0 后定稿」〕
  3. **反向心跳**：两个数都正常 → POST 一次 GlitchTip 自带 Heartbeat 监控的地址；收不到 → GlitchTip 发邮件（发现看门狗或 cron 自身失效）
  4. **整机宕机**：阿里云站点监控，通知改邮件（C 段 Shiyu 手动步骤同步修订）
- **已知剩余风险（接受）**：GlitchTip 半死与看门狗失效**同时**发生时无人察觉（两个互不相关故障的叠加）
- **不变**：C2 开工条件与内容不变；看门狗在 C2 之后保留

### 沙箱演练结论（2026-09-27，PG 16 + Django 6.0.8 + vtasks 3.1.0，DB 后端、嵌入式，调度间隔缩到 5 秒）
- PG 停 15 秒再起 → **调度器永久停摆、零日志、无 `Scheduler stopped`**；worker 报 17 次错后自愈、手动入队照常执行。DB 后端同样中招（上游报告都是 Valkey）
- **只在进程层面重启会失败**：旧进程收 SIGTERM 不退出、仍持有 `/tmp/django_vtasks_scheduler.lock`，新进程 `Could not acquire local scheduler lock` → 无调度器。**必须重启整个容器**（`/tmp` 随容器重置）
- 整体冷启动后心跳恢复；worker 停时入队的任务，重启后全部消化，无丢失
- 单次检查（su + psql 一条查询）：35ms，峰值约 11.6MB，跑完退出
- 演练用的检查 SQL（DB 后端表名以 S0 实读为准）：
  ```sql
  select coalesce((select round(extract(epoch from now()) - value::float)
                   from db_vtaskmetadata where key='vtasks_last_run:send-alert-notifications'), -1),
         coalesce((select round(extract(epoch from now()-min(created_at)))
                   from db_queuedtask where status='QUEUED' and (run_after is null or run_after<=now())), 0)
  ```

### 已查实的约束（基线 origin/main `5f11e57`；vtasks 源码取自 PyPI `django-vtasks==3.1.0` sdist）
1. **GlitchTip 运行形态**（`deploy/sz/glitchtip/docker-compose.yml`）：`:18` 按 digest 钉 6.2.6；`:22` `SERVER_ROLE=all_in_one`（web + worker + migrate 同进程，注释 `:20-21`）；`:24` `VALKEY_URL=` 置空 → 队列走 PG；`:27` 库在主站 `postgres` 容器、库名和角色均为 `glitchtip`；`:32` `ALLOWED_HOSTS=errors.bookecho-shiyu.cn`；`:36` `EMAIL_URL` 在 `/opt/glitchtip/.env`；`:40-41` `mem_limit 384m`、`mem_reservation 192m`；`:47` `restart: unless-stopped`；`:57` 外部网络 `character-distill_prod`
2. **SZ 专属文件的落盘方式**（`deploy/sz/README.md`）：`:3` 不参与 build/deploy 流水线；`:20-42` 改动后必须「scp 覆盖 + diff 验证」；`:47` `/opt/glitchtip/.env` 不入库、`chmod 600`
3. **宿主机 cron 先例**：`scripts/backup.sh:12-15`（口令存 `/root/.backup_key`，`chmod 600`，不在 crontab 明文、不在仓库）；`DEPLOY.md:233-240`
4. **反向代理**：`deploy/sz/nginx/glitchtip.conf:14-15` 443 上的 `errors.bookecho-shiyu.cn`；`:34-38` 经 docker DNS 把请求转给容器名 `glitchtip`
5. **vtasks 3.1.0 的相关机制**：
   - `scheduler.py`：循环顶部的 `acquire_lock("vtasks_scheduler_lock", ttl=15)` 与 `get_metadata` **不在 try 内**；`vtasks_last_run:<task>` 在**入队时**写入，值为 epoch 秒字符串
   - `asgi.py`：嵌入式模式下 `asyncio.create_task(scheduler_instance.run())` 不留引用、无回调 → 死了不留日志；另有 `/tmp` 下的 fcntl 本地锁，每个进程启动时只尝试一次
   - `worker.py` `consume_queue`：整个循环体有 `except Exception` 兜底，会自愈
   - `db/models.py`：`QueuedTask`（状态 QUEUED / PROCESSING / FAILED，`created_at`、`run_after`）与 `VTaskMetadata`（`key`、`value`、`expires_at`）；app 为 `django_vtasks.db`
6. **本路径上已有的机制，逐个核对看门狗会不会改变其前提**：

   | 已有机制 | 计时 / 前提 | 看门狗的影响 |
   |---|---|---|
   | `restart: unless-stopped` | 容器退出才拉起，不看 health | 不变；看门狗补的正是「进程在、功能死」这一块 |
   | `all_in_one` 启动跑 migrate | 每次启动都跑 | 每次重启多一次 migrate；耗时和内存峰值 → S0 实测 |
   | vtasks DB 锁 ttl 15s | 从上次续期起算 | 容器重启后，新进程最多等 15 秒接手 |
   | vtasks `/tmp` fcntl 锁 | 进程启动时尝试一次 | 只能容器级重启（演练实证） |
   | vtasks 对 PROCESSING 任务的救援 | 新 worker 启动时 | 重启不丢任务（演练 + 官方博客） |
   | GlitchTip 通知只在首次发 | 按问题 | 不变 |
   | 阿里云站点监控 | 每 5 分钟、两点都失败才报 | GlitchTip 重启耗时须明显短于 5 分钟，否则会误报 → S0 实测 |
   | GlitchTip 自带 Heartbeat 监控 | 依赖 GlitchTip 自己的调度器评估 | GlitchTip 重启窗口里可能漏一次 ping；宽限期要覆盖一次重启 → S0 查语义 |
   | 主站 PG | 两库共用一个实例 | 看门狗只做一条只读查询，35ms |

7. **真实规模的数字**（带「待 S0」的以实读为准）：
   - ~~告警调度间隔 60 秒；心跳阈值 300 秒；最迟约 10 分钟触发重启~~ 〔S0 后修订，见「S0 后定稿」〕
   - 冷却期 1 小时：1 小时内第二次异常就发邮件，不再重启 → 最多 1 小时一次重启，不会反复重启
   - 看门狗每次约 35ms / 11.6MB，无常驻（次数见「S0 后定稿」）

### 约束（S0 后定稿版）
- 不改 GlitchTip 镜像、不改主站代码与发版流水线；新增文件全部放 `deploy/sz/`，落盘走 README 的「scp 覆盖 + diff 验证」
- 看门狗对 PG **只读**
- **凭据**：宿主机上只有一项——心跳地址，存 `/root/.glitchtip_watchdog`（`chmod 600`，仿 `/root/.backup_key`）。发信复用 GlitchTip 容器已有的邮件配置，宿主机**不解析、不复制**任何发信凭据。不入库、不打印；比对只打 sha256 前 12 位
- 重启只用容器级命令（`docker restart <GlitchTip 容器>`）
- 新发现属于本段改动面的直接修；只有会撞车或需要拍板时才停下报告；不自行记账

### 步骤 13-S0：只读调查（已完成，报告见 `docs/specs/observability-w-s0.md`）
逐条查实，**结果写成 `docs/specs/observability-w-s0.md` 上传**；任何一条与上文「已查实的约束」不符，就停下报告。
1. 在最新 main 上复核约束 1–4 的坐标
2. 深圳容器名：GlitchTip 容器、主站 PG 容器（`docker ps --format`）
3. 在 GlitchTip 6.2.6 容器里读 `VTASKS_SCHEDULE`：`send-alert-notifications` 的真实间隔；有没有更适合作心跳的任务
4. 在容器里读源码：停摆期间产生的事件，重启后还会不会触发通知（报警评估的时间窗口）。结论写明文件和行号
5. 在深圳用 `docker exec <PG 容器> psql` 免密只读执行上面那条 SQL，贴输出；确认表名 `db_vtaskmetadata`、`db_queuedtask` 在 `glitchtip` 库里
6. 取样：连续 30 分钟每 5 分钟跑一次那条 SQL，报两个秒龄的最大值（确认 300 / 600 秒的阈值有余量）
7. **经 Shiyu 同意后**做一次 `docker restart`：计时到 GlitchTip 首页可访问、心跳秒龄回到 60 秒以内，并用 `docker stats` 记内存峰值；重启前后各跑一次第 5 条的 SQL
8. GlitchTip 6.2.6 的 Heartbeat 监控：心跳地址格式、间隔与宽限期怎么设、超时是否走邮件（读源码或在网页上建一个测试监控）
9. 深圳到 Resend 的线路：GlitchTip 的 `EMAIL_URL` 是 SMTP 还是 API（只报类型和主机，不打印密钥）；从宿主机 `curl` 连 `api.resend.com:443` 能否握手（不发信）
10. 从宿主机经本机 nginx 访问 GlitchTip：`curl --resolve errors.bookecho-shiyu.cn:443:127.0.0.1 -so /dev/null -w '%{http_code}' https://errors.bookecho-shiyu.cn/`；以及宿主机上 cron 在不在跑（`systemctl is-active cron`，`crontab -l` 只报有几行，不贴内容）

### S0 后定稿（2026-09-27，Shiyu 拍板；依据 `docs/specs/observability-w-s0.md`，下称「报告」）
与上文冲突处一律以本节为准。

**S0 查实的新事实**（GlitchTip 6.2.6 容器内坐标，报告有逐行引用）：
1. 调度表 `/code/glitchtip/settings.py:971-1000`：`send-alert-notifications` 60 秒；`uptime-dispatch-checks` **1 秒**，本机已启用
2. 报警评估 `/code/apps/alerts/tasks.py:53-67`：只看最近 `timespan_minutes` 内的事件，本机两条告警均为 **5 分钟**；已通知过的不重发（`:65`）。→ **出事到恢复必须 < 5 分钟**，否则停摆早期报错的通知永久丢失
3. 重启实测（报告第 7 节）：首页 13.4 秒恢复；`memory.peak` 230.6 / 384 MiB；主站不受影响；`RestartCount` 不记手动重启；重启后只看秒龄会误判，**要看值有没有前进**
4. Heartbeat 监控（`/code/apps/uptime/utils.py:70-87`、`api.py:189-191`、`tasks.py:273-279`）：宽限期 == `interval`；心跳地址 `auth=None`，URL 即凭据；邮件需要项目上有 `uptime=true` 的告警与 email 收件人——**目前三样都没有**
5. 线路（报告第 9、10 节）：GlitchTip 的 `EMAIL_URL` 是 Resend SMTP，宿主机到 `smtp.resend.com:465` 通；经本机 nginx 访问 GlitchTip 200；cron 在跑，只有备份 1 行
6. `/health` 公网 403（`nginx/nginx.conf:92-96`）；`/api/health` 在 `PUBLIC_PATHS`（`web/server.py:331`），只看存活、不查库（`:482-490`）

**设计原则**（Shiyu 定：不过度工业化、不打补丁、隔离、抽象、复用、可维护）：
- **判定与动作分离**：一个纯函数 `decide(读数, 状态, 现在) → 动作`，不碰 IO；执行动作的外壳很薄，外部调用经一个可注入的执行器，测试替换它
- **复用现成的，不自己造**：
  - 发信 → `docker exec` 进 GlitchTip 容器，用它自己的 Django 邮件配置（`EMAIL_URL`、`DEFAULT_FROM_EMAIL` 已在 compose `:36`、`:39`）调 `send_mail`。Django 发信不需要连库，PG 挂了也能发
  - 防叠跑 → crontab 行里用系统自带的 `flock -n`，脚本里不写锁
  - 重启 → `docker restart`；通知去重 → GlitchTip 自己管，看门狗只做最粗的限流
- **能不要的状态就不要**：状态文件只存两个时间戳（上次自动重启、上次发信）

**每分钟一次的流程**：
1. 读数：`docker exec <PG 容器> psql` 跑一条只读 SQL，得到心跳秒龄与队列秒龄。**失败** → 通知「PG 不可用」，结束
2. 两项都正常 → POST 心跳地址，结束
3. 异常，且 1 小时内已经自动重启过 → 通知「重启后仍异常」，结束（不再重启）
4. 异常，1 小时内没重启过 → `docker restart`，记下时间；**在同一次运行里**每 5 秒读一次心跳值，最多等 90 秒：值前进 → 通知「已自动重启并恢复」；不前进 → 通知「重启未恢复」
5. 所有通知共用限流：1 小时最多 1 封

没有单独的「静默期」和「失败计数」：重启后的确认在同一次运行里完成（按「值前进」判定，对应事实 3），下一次运行看到的已经是新值。

**参数与真实规模**：

| 项 | 值 | 依据 / 计算 |
|---|---|---|
| cron | 每 1 分钟，`flock -n` | 最坏发现 = 60 秒阈值 + 60 秒 cron；加 13.4 秒重启 ≈ 2.3 分钟 < 5 分钟窗口（事实 2） |
| 心跳判据 | `vtasks_last_run:uptime-dispatch-checks` 秒龄 > 60 | 事实 1；报告第 6 节正常恒为 0–1 |
| 队列判据 | 最老可执行 QUEUED > 600 秒 | 报告第 6 节日常恒为 0 |
| 重启后确认 | 最多 90 秒，每 5 秒读一次 | 13.4 秒恢复 + 15 秒调度锁 ttl，留 3 倍余量 |
| 自动重启上限 | 1 小时 1 次 | 原定 |
| 通知限流 | 1 小时 1 封 | 每分钟跑，持续故障不能每分钟一封 |
| 反向心跳 | 正常时每次都 ping；GlitchTip 监控 `interval` 10 分钟 | 事实 4；10 分钟 > 一次最长运行 + 漏跑几次 |
| 外呼超时 | `curl --max-time 10`；发信的 `docker exec` 限 30 秒 | 单次最坏 ≈ 11 秒重启 + 90 秒确认 + 30 秒发信 ≈ 131 秒 → `flock -n` 让随后 2 次 cron 直接退出，不叠跑 |
| 次数与开销 | 每天 1440 次；正常一次约 35ms 查询 + 38ms ping | 演练与报告第 10 节 |

**依赖的外部兜底**（本段不做，写明而已）：GlitchTip 容器完全退出时 `docker exec` 发不了信 → 由 `restart: unless-stopped`（compose `:47`）拉起；持续起不来则首页不可达 → 阿里云站点监控发邮件。

**Shiyu 在 GlitchTip 网页上建（执行方不碰 Shiyu 账户）**：
1. `backend` 项目下新建 Heartbeat 监控，`interval` 10 分钟
2. `backend` 项目新建一条勾选 uptime 的告警，收件方式 email：`bookecho@163.com`
3. 心跳地址存本机文件，告诉执行方路径

**站点监控**（C 段手动步骤同步修订）：探测 `https://errors.bookecho-shiyu.cn/` 与 `https://bookecho-shiyu.cn/api/health`，通知走邮件，不用短信。

**实现前复核**（有一条不成立就停下报告）：
- 本节引用的 `nginx/nginx.conf:92-96`、`web/server.py:331`、`:482-490`、`deploy/sz/glitchtip/docker-compose.yml:36`、`:39`、`:47` 在最新 main 上逐条复核
- 公网 `curl https://bookecho-shiyu.cn/api/health` 返回 200
- GlitchTip 对 `MonitorCheck` 有没有定期清理（文件和行号）；没有就停下报告（每天 1440 行）
- 宿主机 `python3 --version`（脚本只用标准库）

### 步骤 13：实现（参数以「S0 后定稿」为准，Shiyu 已拍板）
> 提示词开头带上：`@tdd @incremental-implementation`

- 分支 `worktree-observability-watchdog`，基于最新 main
- 文件：
  - `deploy/sz/glitchtip/watchdog.py`：Python 3 标准库，一个文件。结构：`decide()` 纯函数；读数、重启、ping、发信四个动作经一个注入的执行器；`main()` 只负责把它们接起来
  - `deploy/sz/README.md`：落盘、心跳地址文件、crontab 行（含 `flock -n`）、回滚办法（删掉那一行 crontab）
- 测试 `tests/test_glitchtip_watchdog.py`，先写：
  - `decide()`，按比例复刻真实时间关系：心跳秒龄取阈值的 0.5 / 1 / 5 倍；队列同理；1 小时重启上限的内与外；限流窗口的内与外；PG 读数失败
  - 重启确认，用假执行器加注入的时钟：心跳值前进 → 通知「已恢复」；90 秒内不前进 → 通知「未恢复」；两种情况都写状态
  - 不测 `flock`、`docker` 本身（系统工具，不是我们的代码）
- commit：`feat(glitchtip): host watchdog restarts a stalled GlitchTip`、`docs(sz): install and roll back the GlitchTip watchdog`

**W 段验证**：本地只跑 `tests/test_glitchtip_watchdog.py`（本段不改前端，不跑 `npm test`；不连 PG，全用假执行器）；合并门是分支 CI；合并只做 git 操作。上线后验收：
1. 手动跑一次，把心跳阈值临时覆盖为 0（命令行参数，不改文件）：确认执行了容器级重启、90 秒内心跳值前进、写了状态、`bookecho@163.com` 收到「已自动重启并恢复」（同时证明发信复用路径可用）
2. 临时注释掉 crontab 那一行：约 10 分钟内收到 GlitchTip 的心跳缺席邮件，然后恢复
