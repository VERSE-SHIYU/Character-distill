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

**3. 第二道门是「非空」门，不是「结构合法」门** —— 状态：纵深防御（主屏障已上移）
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

**8. `_GEN_DEADLINE_S = 60.0` 写死，不可 env 覆盖** —— 状态：**保留记账**
- `_GEN_DEADLINE_S`（`adapters/llm_adapter.py`）；同组的三个 ceiling `LLM_DECISION_ATTEMPT_S` / `LLM_GEN_ATTEMPT_S` / `LLM_STREAM_ATTEMPT_S` 都可 env 覆盖，deadline 仍无出口
- 关掉思考后（2026-09-10 实测，n=14）：**0/14 超 60s，max 31.3s**（>45s 也是 0/14）→ 当前工作负载已不再顶它，见 §二基线表
- 历史（修复前，思考未真正关闭）：单次 map 调用 12.0–160.0s，>45s 12/14、>60s 11/14（证据：`ev:thinking-maplen-before`）→ 当时超过 60s 的调用在生产上必然失败
- 生产是否仍有超时：**待验证**（受本机吞吐、网络、prod `.env` 覆盖值影响）。常量仍写死不可 env 覆盖，保留记账

**9. 端点注入 `get_current_user` 却不引用 `user`（越权一类）** —— 状态：**已修**（2026-09-10）
- 形态：签名取 `user: dict = Depends(get_current_user)`，函数体从不引用它 → 无归属校验，任何登录用户拿 id 就能读他人资源
- 全仓 AST 扫描（`web/routers/*.py`）命中 **9 处**：
  - **3 处真越权，已修**：`text.py` 的 `get_upload_task_status`（上传任务：任意 task_id 读他人进度/错误）、`voice.py` 的 `preview_audio`（自定义音色文件：绕过私有的 `/list` 过滤与删除/上传的属主校验）、`voice.py` 的 `voice_synthesize`（`card_id` 参考音频：兄弟端点 `preview_ref_audio` 查卡主，唯独它不查）。统一 fail closed + 非属主 **404**（不用 403，避免靠状态码枚举资源是否存在）
  - **6 处良性**（只当登录门，读全局/公开数据）：`auth.py` 的 `get_announcement`、`market.py` 的 `get_card_versions` / `get_card_forks` / `list_post_comments`、`voice.py` 的 `voice_status` / `speech_to_text`
- 边界锁：`tests/test_auth_param_used.py` 固化这次 AST 扫描 + 白名单，将来新增同类端点自动变红，不必靠人再扫一遍
- 注：`voice.py` 内既有四处查属主返回 **403**（`get_ref_audio` / `preview_ref_audio` / `upload_ref_audio` / `delete_ref_audio`），与新修的 404 并存，口径待统一 —— **已于 2026-09-12 统一为 404**（缺陷 13）

**10. post / card 评论点赞特性整体缺失（原记为「`list_post_comments` 不标 `liked_by_me`」）** —— 状态：未修（**重记**，2026-09-12 裁定「不做」）
- **原记账被证伪**：原条目称「同类端点 `text.py` 的 `get_text_comments` 标了 `liked_by_me`、`list_post_comments` 没标，按 `text.py` 的写法对齐即可」。实读后前提不成立 —— 照做只会写死一个恒 `False` 的**假默认值**（本仓明令禁止，见 §四）
- 证据（2026-09-12 现跑现查）：
  - **无表**：全仓没有 `post_comment_likes` / 卡评论点赞表；`_likes` 家族只有 `text_comment_likes`（`storage/migrations/031_text_comments.sql` 及 PG 等价物）与 `post_likes`（点赞**帖子**本身，`web/routers/market.py` 的 `like_post` → `toggle_post_like`）
  - **无路由**：`toggle_post_comment_like` 全仓零命中 —— post 评论根本没有点赞入口
  - **无原语**：`storage/sqlite_store.py` 的 `get_liked_comment_ids` **硬编码** `text_comment_likes`（PG 侧同），拿 post 评论 id 去查恒返空集
  - **无消费**：前端 `web/frontend/src/components/common/PostCard.jsx` 渲染 post 评论（头像 / 用户名 / IP 属地 / 时间 / 正文）**没有点赞按钮**，从不读该字段；`post.liked_by_me` 是**帖子**的赞，不是评论的
- 即：真缺口是**「post / card 评论点赞」这个特性从来不存在**，不是「某端点漏标一个字段」。对齐写法 ≠ 修 bug，是**加功能**（建表 + 双方言 migration + toggle 路由 + `get_liked_post_comment_ids` 原语 + 前端按钮），且要新增一张表
- 处置裁定（用户，2026-09-12）：**不做，重记缺陷**。`list_post_comments` 保持现状 —— 不返回该字段，比返回一个恒 `False` 更有信息量
- 注：它仍留在第 9 条的扫描名单里（读 user 与否在该端点曾表现为「功能与否」而非「越权与否」），该扫描不受本条影响

**11. 越权读取的根因：storage 读取原语没有身份概念** —— 状态：**已修**（2026-09-11）
- 形态：`storage/base.py` 的读取原语 `get_text(id)` / `get_session(id)` 签名里没有 user——读取本身不带身份，于是**每个调用点都必须自己记得**补一次属主比对，忘一个漏一个，且漏了没有任何报警
- 命中 6 处真越权，同形：`web/routers/history.py` 的 `resume_session`（最重：读到他人全文 → 重建 RAG → 把引擎塞进共享 `sessions` → 补完全部消息与好感度 → 生成重逢问候）、`web/routers/distill.py` 的 `identify_by_text_id` / `distill_by_text_id` / `_distill_start_impl` / `reindex_rag` / `distill_stream`。`core/text_manager.py` 的 `get_or_distill` 另有同形陷阱：`user_id: str = ""` 默认值让「缺参数」静默退化成「按空属主查」（`save_distilled_card` / `distill_all` / `switch_character` 同形，一并去默认值）
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

**18. `IncompleteResponseError` 不可 pickle** —— 状态：未修（记账）
- `__init__(self, finish_reason, where, content="")` 调 `super().__init__(<单条消息>)` → `args == (msg,)`。pickle 还原时按 `args` 调构造器 → 缺 `where` → `TypeError`
- 现无跨进程传递（异常在同一进程的线程内上抛与捕获，map 的 `failures` 列表也不出进程），故未动
- **将来若上多进程部署（进程池 / 队列传异常），这是个会突然炸的点**——届时改成让 `args` 载全部构造参数，或在边界层先翻成可序列化结构

**19. 边界锁只守 `*_unscoped` 命名，无身份读取原语裸奔** —— 状态：**修（进行中，commit 1/5，2026-09-13）**
- 形态：`tests/test_storage_scope_lock.py` 用 AST 扫调用点，判据是 `_is_unscoped_call` → `attr.endswith("_unscoped")`。**只有名字以 `_unscoped` 结尾的调用**才进白名单校验
- 漏网：`get_card` / `get_group_session` / `get_dm_message` / `get_distill_task` / `get_card_author_id` 等**读取原语本身就无身份参数**，取回整行后由调用点在 Python 侧比对属主——名字里没有 `_unscoped`，锁看不见。删掉任一调用点的属主 `if`，锁全绿
- 为什么是缺陷 11 的同族：da0e3b3 只把 `text` / `session` 拆成 `_owned`/`_unscoped` 双变体，锁也只守这两类。其余资源从未拆分，于是「忘一个漏一个」的结构性风险仍在，只是没有报警
- **这正是缺陷 13 的 32 处只能手写路由层合并、且删一处不会红的原因**——本轮 `tests/test_ownership_404.py` 是补的语义用例（第二层防线），形态锁（第一层）仍缺
- **分类（SQL 事实判据，2026-09-13）**：命中 **46** 处 → **A 组 20**（SQL 本身已按身份收窄）+ **B1 12**（无属主过滤，真越权）+ **B2 8**（有意跨属主读：跨用户聚合/后台扫描/全局状态，无身份概念是有意的）+ **B3 6**（逐条裁定）。判据：属主表 = `migrations_pg/*.sql` 里沿 `_id` 边可达 `users` 的表（**29** 张）；读原语 = `SQLiteStore` 公开方法且 SQL 含 SELECT 无写语句；「已按身份收窄」= 签名有身份参数 **∨ WHERE 子句**（非 JOIN ON）含该表身份列谓词
- **JOIN 坑（41→46 来源）**：初版把 `JOIN users u ON c.user_id = u.id` 当作身份过滤，`get_card` 因此被判「已收窄」，命中数 **41**。**JOIN 条件不是过滤条件**——它只约束连接行配对，不筛掉非属主行。把「是否收窄」的判据限定到 **WHERE 子句**（在 GROUP BY/ORDER BY/LIMIT/HAVING 处截断后再测）后为 **46**，`get_card` 归位。见 §四纪律
- **裁决（用户，2026-09-13）**：A 组加「11 个管理原语的调用点必须全在 `web/routers/admin.py`」AST 断言——把「现在是这样」变成「必须是这样」（调用点事实 → 强制事实）；B1 里三处实锤（`get_characters` 先返缓存后校验 / `get_card_versions` 返回含卡全文的快照 / `get_text_comments` 无校验）**先修、单独 commit**，其余九个随后；B3 逐条：`get_card_author_id` 保留显式无身份但标注 **`identity_primitive`**（它是「校验的第一步」，输出供属主比对，与「有意跨属主读」区分开）、`get_reactions` 改 `_owned`、`get_latest_review_log`/`get_comment_reports` 归 A 组管理、`get_recent_card_session` **无调用点=死代码删掉**、`get_remote_card` 改私有 `_get_remote_card`
- **修法（根因，不是补校验）**：把无身份读取原语在 **storage 层**消灭——扩展缺陷 11 的 `_owned`/`_unscoped` 双变体范式，读属主表的原语要么 SQL 带身份、要么显式命名 `_unscoped` 并进白名单。**锁的判据 = 不存在「读属主表 ∧ WHERE 无身份列谓词 ∧ 无身份参数 ∧ 不叫 `_unscoped` ∨ 不在白名单」的原语**；名字只在**声明豁免**时起作用，真源是 SQL 事实（与缺陷 21「锁症状不锁代理」同范式）
- **夹具自效性（探针取证）**：既有 `test_12_distill_identify_non_owner_404` 对 `get_characters` 越权**恒绿**——它建的文本无缓存（`characters_json` 空），旧代码读缓存得 None 照样走 404，故旧代码下也绿。判别力要求**缓存已存在**（`test_23` 先 `save_characters` 再打非属主）。这是「探针自效性」的实例：夹具没走到那条分支，用例就是恒绿的
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
- **定性订正（2026-09-13 排查）**：此条**不是**「静默不执行」而是「**带理由地不执行**」—— 分发形态锁 `tests/test_migration_dispatch.py` 早已存在（`c1a8131`），079 躺在它的 `_NOT_APPLIED` 豁免名单里，所以锁是绿的。真正的洞是**豁免出口没有闭环**：写下理由即永久放过，而理由里写的后果（「新库缺表、代码在用」）没有任何东西去验。原设想的「每个文件必须有明确归宿」形态锁**不必新做** —— 它已存在，且正是被绕过的那一个
- **处置（2026-09-13，四步，均只记/只验不越界）**：
  - **接线**（`4be3ecf`）：`079_remote_user_profiles.sql` 加进 SQLite 执行器次序元组，从 `_NOT_APPLIED` 移除。位次依据：它建独立表、无依赖，按编号自然位次排。验收：新库实跑后表存在，三个引用点在真新库上不再抛 `no such table`
  - **闭环锁**（`4a608f9`）：`tests/test_sqlite_fresh_schema.py::TestExemptionClosedLoop` 断言「**SQLite 新库实跑后的表集合 ⊇ `migrations_pg/` 声明的表集合**」（排除 `sqlite_sequence`）。锁的是**症状本身**，不是「文件有没有登记」这个可被合法豁免绕过的代理指标；真源选 PG 目录，因为 PG 执行器是 glob、目录即清单、无豁免出口 —— 「PG 目录里有的表」= 「应该存在的表」的可靠定义。与 `test_sqlite_fresh_schema` 同一套基础设施，CI 可行，不需要真 PG
  - **store 层同族形态全量收敛**（`2ab669a`）：本轮另一条线，见下面「第七个同族形态」
  - **未修**：本条目自身已闭环，无遗留
- 同族风险（已用锁兜住）：次序表是显式元组，**加文件忘登记不会有任何报警**（079 就是先例），故 `test_migration_dispatch` 有「目录 ↔ 次序表求差集」形态锁
- **本条目衍生出的第七个同族形态 —— store 层「失败与空结果不可区分」**：SQLite 新库缺 `remote_user_profiles` 表时，`storage/sqlite_store.py` 的 `get_conversations` 把 `OperationalError` 吞成空列表 → 私信收件箱**恒为空、不报错、不 500**，日志里只有一行 print。根因不是「那一处吞错了」，而是 store 层用**同一个返回值**同时表达「查到了，结果是空」与「查询失败了」两种互斥语义。这正是前六个同族形态（线程弃船 / 384 维度 / 截断响应 / `finish_reason` 缺失 / `$contains` 恒不命中 / `admin_tasks` 静默截断）**都因为「只修出问题那处」**才长出第七个的原因，故 `2ab669a` 全量收敛：A 类 176 处（基线 `4a608f9`，sqlite 88 / pg 88）全改，不变量「**store 层的空返回值只表示「无数据」，永不表示「失败」**」定于 `storage/base.py` 的 `StoreError` 单一定义，容忍策略上移到调用方并注明理由。普查全集与 A/B 分类见 `2ab669a` 的 commit message，可 `python tests/perf/store_swallow_census.py --ref 4a608f9` 逐字复算

**22. `async_chat` 的 usage 被丢弃 —— Map 阶段与代词消解的 token 完全没记账** —— 状态：未修（记账，2026-09-12 查缺陷 16 时发现）
- 事实（实测）：`adapters/llm_adapter.py` 的 `async_chat` 返回 `(result, usage)`，且**不写 `self.last_usage`**（该文件 `_async_chat_span` 注释自己写明了）。四个调用点里三个是 `result, _ = await ...`，usage 就地丢弃：
  - `_run_map_concurrent` 的 `_one`（sync MapReduce 的 Map）
  - `distill_incremental_stream` 的 `_one`（SSE MapReduce 的 Map）
  - `coref_resolve` 的 `_resolve_chunk`（代词消解分片）
  - 唯一正确记账的是 `_single_reduce_async`（`result, usage = await ...` → `_try_record_usage("distill_reduce", usage)`）
- 后果比缺陷 16 更大：Map 是 MapReduce 里**最烧 token 的一段**（每片一次调用，片数几十到几百），而它**一条记录都没有**；reduce / 初次 / 重修 / 流式各自都记。于是 `distill_*` 系列 action 的用量被系统性低估，且低估幅度随文本变长而放大
- **不属缺陷 16 的同源修法**：16 的病灶是「记账出口分散 + 一处漏」，收敛到一个 `_chat_initial` 出口即解决；本条的病灶是**记账出口从未写**，`_chat_initial` 覆盖不到（Map 不走它）。修它要先裁决「N 次并发调用怎么记」——N 条独立记录（写放大）还是一条聚合（需新增聚合态），是**设计决策**不是补漏
- 未同轮修的理由：铁律 3「新发现只记缺陷表，不当场修」；且本条改动会显著改变用量/计费口径，属**行为变更**，须单独一轮带验收锁

**23. PG 侧无对称形态锁 —— 迁移的「声明」与「真库」之间无闭环** —— 状态：**已修（2026-09-13，`TestPgFreshSchemaClosure`）**
- **原立项措辞要订正两处**（立项时按「SQLite 独有 66 表」理解，实测不成立）：
  - **现存差集是「文件 83 vs 17」，不是「表 0 vs 66」**。`_objects_from_sql` 复算：两目录各声明 **41** 张表，**双向差集都是 0**。「66」是**文件数**差 —— PG `001_init.sql` 并掉了前 66 个 SQLite 迁移文件，故编号结构性不对齐（PG `012_` 对应 SQLite `079_` 是先例），**按编号比不可行，按表集合比才是可判定定义**。
  - **朴素形态（加了 SQLite 迁移忘了 PG）已被锁住**：`tests/test_schema_parity.py` 早已**双向**断言两目录的表 / 列集合相等（`only_pg` 与 `only_sqlite` 都报错）。所以「反方向无锁」在**表粒度**上不成立 —— 只对「**声明 vs 真库**」成立。
- **真缺口（已修）**：那三把锁（dispatch / parity / fresh-schema）都只看「文件里写了」，不看「真库建出来了」。SQLite 侧有 `TestExemptionClosedLoop` 兜着（真建库 ⊇ PG 声明，配合 text parity 即 SQLite 侧闭合）；**PG 侧此前一条都没有，而 PG 才是生产**。
- **锁法**（`tests/test_postgres_store.py::TestPgFreshSchemaClosure`，两条断言）：真 PG 库 ⊇ `migrations_pg/` 声明表 **∪** `postgres_store.py` 引用表。第二条直接锁 079 的症状形态（代码引用库里没有的表）。
- **零豁免、不需要豁免名单**：真源取「表集合」而非「文件编号」后，上线当天就是绿的（实测 41 声明 / 41 建出 / 引用 41，差集全 0）。**这正是与缺陷 21 的关键差别** —— 拿编号当真源会带一堆豁免，而豁免即永久放行（§四 纪律）。将来真出现 SQLite-only 的表也不在此豁免：它以「不在 `postgres_store.py` 引用集」被第二条断言直接验掉，不靠理由文本。
- **为什么不与缺陷 21 的锁合并成一条双向断言**：依赖不同（一条只需 SQLite、无 PG 也能跑；一条必须有真 PG —— 本地无 PG 时 PG 段是 skip/error，合并会让本地丢掉 sqlite 侧保护）；盲区不同（一条防本地 / 测试库缺表，一条防**生产**缺表）。两条 docstring 已互相交叉引用。**列级闭合未做**（text parity 已在文件层做列级；运行期列级是新工作量），**记账**。
- **前提**：库必须「新鲜」（CI 每次给新 postgres service 容器）。对长期存在的 dev 库跑会被残留表掩盖 —— 实测删掉 `012_remote_user_profiles.sql` 后若库里还留着该表，两条断言都绿。已写进 docstring。
- **变异（实测两次）**：删 `012_remote_user_profiles.sql`（代码仍引用）→ 只有「引用表」那条红；让一条迁移「声明了但建不出来」→ 只有「声明表」那条红。两条各抓一类，互不代偿。
- 原「反方向无锁」判断的正误：**形态判断对（确实缺生产侧闭环），但把模板当成了表集合**。立项时的推断「PG 目录声明的表集合 ⊆ SQLite 新库表集合」也不对 —— 该方向早已被 `TestExemptionClosedLoop` 覆盖，真正缺的是**反向的、且对着真 PG 库**。
- 同轮另两条观察（**非缺陷，只记**）：
  - **豁免名单住在测试里，不在执行器里**：`tests/test_migration_dispatch.py::_NOT_APPLIED` 是唯一记录「哪个迁移被豁免、为什么」的地方，而读 `storage/sqlite_store.py` 的人只看到次序元组，**看不到「本应在这却没在」**。可观性问题，非正确性缺陷；若要改，方向是把豁免登记移进执行器模块或让它可被执行器导入
  - **PG `001_init.sql` 保留了 SQLite 侧已删的 4 个遗留列**（`users` 的 `password_hash` / `api_key` / `base_url` / `model`，`migrations_pg/001_init.sql:93-98` 内联声明）。**已复核，运行期 schema 无漂移**：这 4 列由 `migrations_pg/005_data_residency.sql` 的 `DROP COLUMN IF EXISTS` 删掉；两侧逐表逐列求交集（`001_init` + 全部 `ALTER` − drop）**完全相同**（用 `tests/perf/migration_coverage_audit.py` 的 `_objects_from_sql` 复算）。漂移只在**文件层**：读 PG 的 `001_init.sql` 会看到一个最终库里并不存在的列清单（SQLite 侧这 4 列本就不在 `001_init` 声明、是后来 `ALTER` 加的，故它的 `001_init` 天然干净）。**非缺陷**，但会误导「按 bootstrap 文件推断 schema」的人 —— 与上面两条同属「文件的陈述与运行期事实不一致」

**24. 提交责任无归属 —— SQLite store 的写方法漏 `commit` 就静默丢数据，「失败被吞成正常返回」的第八次显形** —— 状态：已修（2026-09-13，`4a91868`）
- 事实（实测）：`_connect()` 用 `aiosqlite.connect()` 且全仓未设 `isolation_level=None` → legacy 事务模式，INSERT / UPDATE / DELETE / REPLACE 隐式开事务，不 commit 则 close 时被回滚；而 `_ConnectionContext.__aexit__` 当时**只 close 不 commit**。于是任何写方法自己忘了 `await conn.commit()`，就「写了、函数照常返回构造好的 dict / rowcount、数据不在、**连异常都没有**」。现场两处：`add_post_comment`（INSERT 后无 commit，仍返回 `{"id": ...}`，前端把评论显示出来、刷新即消失）、`cleanup_empty_cards`（UPDATE 后无 commit，仍返回真实 `cursor.rowcount`）
- **普查（AST 全量，不是「报一处修一处」）**：扫 235 个「在 `_connect` 作用域内且有调用」的方法，得 **2 处**（`add_post_comment` / `cleanup_empty_cards`）。报告只点名 1 处，全量扫出 2 处 —— 前七次同族形态都因「只修出问题那处」才长出下一次，故本轮先做全集普查再动手
- **同型定性**：与缺陷 21 的「豁免出口没有闭环」同型 —— **都是把正确性寄托在人的记忆上，没有机制兜底**。这是「失败被吞成正常返回」的第八次显形（前七次：线程弃船 / 384 维度 / 截断响应 / `finish_reason` 缺失 / `$contains` 恒不命中 / `admin_tasks` 静默截断 / store 层 `except: return <空值>`），且是**唯一一次连异常都没有的**
- **方案裁决（风险实测，不是偏好）**：
  - 方案 A（`_connect()` 设 `isolation_level=None` 自动提交）：风险 = 27 个「一个连接里做多步写」的方法**失去事务边界**，多步写中途失败会留半写状态 —— 实测把 `_connect` 改成 `isolation_level=None` 后，两个最长的多步写方法（`delete_user` / `hard_delete_text`）的原子性用例**双双变红**。这是把一个静默丢数据换成一个静默留半写，不可接受。（「多步写方法」口径：方法体内 ≥2 条 `execute` / `executemany` 写语句，共 27 个，`delete_user` 12 条 / `hard_delete_text` 8 条最长；可复算）
  - 方案 B（`_ConnectionContext.__aexit__` 无异常 commit、异常 rollback）：风险集**实测为空** —— 全仓 0 处显式 `rollback`、0 处「故意不提交」、只读作用域上 commit/rollback 是 no-op。**选 B**
- **改法（单点，不复制补丁）**：提交语义收敛到 `_ConnectionContext.__aexit__` 一处（先 commit/rollback、再 close），`_ConnectionContext` 类 docstring 写明「提交语义由本类承担（承重，勿改回只 close）」。**两处 offender 由机制吸收，不去各自补 commit**；调用方零改动、对外签名与返回语义不变。与 `2ab669a` 的 `StoreError` 单一出口同形（单点定义、调用点不各写各的），未引入第二种事务/错误管理风格
- **提交这一步本身不得再造同形态失败**：`__aexit__` 的 commit 失败必须上抛（吞掉 = 调用方以为写成功而库没有，正是本类要消灭的形态）；异常路径的 rollback 失败不得顶替调用方原始异常（沿用既有 close 段口径：print 可见、不上抛），两处均写 `# store-empty-ok:` 说明
- **两层防线（各管各的）**：`tests/test_store_connect_lock.py` —— **形态锁**：sqlite store 任何函数不得绕过 `_ConnectionContext` 直接 `aiosqlite.connect` / `sqlite3.connect`（白名单 `_connect` / `_ensure_initialized` 各附理由 + 名单腐烂检查）；`tests/test_store_commit_contract.py` —— **语义用例**：跨连接读回（不是同连接内的可见性假象）、两处 offender 写入后新连接读得到、多步写抽样（`delete_user` / `hard_delete_text` 在**最后一条写语句**处注入异常 → 全回滚无半写）、只读作用域 commit/rollback 是 no-op。**变异**：删掉 `__aexit__` 的 commit → 3 条语义用例红（形态锁仍绿）—— 证明「只锁形态」不够，两层都要有（§四「形态锁与语义用例是两层防线」）
- **PG 侧无需改**：asyncpg 语句默认自动提交，另有 18 处显式 `async with conn.transaction():`（原生 clean-exit 提交 / 异常回滚语义），缺陷 24 是 SQLite legacy 事务模式特有的
- 同轮范围核查（**已扫，无缺口**）：store 之外的直接 sqlite 连接全查过 —— 4 个带 `sqlite3.connect` 的运维/诊断脚本（`check_missing_card` / `diagnose_psyche` / `export_shiyu` / `integration_check`）全为只读，`rebuild_384_collections` 用 `mode=ro` URI，`migrate_sqlite_to_pg` 只读 SQLite、写 PG（asyncpg 自动提交）。无需处理

### 四、验证纪律

- **基线数字现跑现取**（测试通过数、函数签名）：禁止引用上一轮结果或凭记忆。引用代码一律用符号名（函数/常量/测试名），不写行号——行号随改动漂移且无测试报警
- **并发/事务/锁的结论不给「应该如此」，只给「要验什么、怎么验」**。案例：MVCC 下 `WHERE EXISTS` 是快照读、不加锁，挡不住「父行正在被删、还没提交」——必须补 `FOR SHARE`。本仓已成文于 `storage/postgres_store.py` 的 `save_distill_chunk`（含死锁无环分析）
- **SQLite 全绿不构成并发命题的证据**：SQLite 写是库级序列化，窗口从根上不存在（`storage/sqlite_store.py` 的 `save_distill_chunk` 自述），生产是 PG。同一段逻辑在 sqlite 上验不出 PG 的行锁语义
- **mock 的形态 ≠ 被测对象的形态**。案例：mock 回 canned JSON，据此误判 map 输出是 JSON——实际是自然语言（prompt 见 `core/distiller.py` 的 `_map_system_prompt` / `_map_user_prompt`；消费侧只判「非空且 ≠『无』」，见 `distill_incremental_stream` 内 `raw_analyses`）。据此设计的 JSON 校验会否掉所有真实 map 结果
- **测试通过 ≠ 命题成立**，可能只是那条路径根本没被走到。案例：分片续跑逻辑落地后长期未真实执行——短文本恒走长上下文路径（分流在 `distill_incremental_stream`），从不进分片。要验分片路径必须显式强制（见第五节）
- **修复必须做变异验证**：改坏它，指定测试必须变红。案例：把兜底调用从 `finally` 里整行删掉，**原 14 个测试仍全绿** → 接线没被覆盖；随后补 `TestA2Wiring` 两条（commit `9d2a9e4`；变异实验本身与扫描口径见 `ev:a2-wiring-mutation`。补齐动作可 `git show 9d2a9e4` 直查，但「删掉后仍全绿」这个**运行期观测**只记在未入库的会话文件里，故它单独入了清单）
- **变异必须可判定：要让它「红」，不能让它「挂死」**。判据：设计用例时先想清楚「这条用例被改坏后会怎样」——若会死循环/长时间阻塞，那条变异就**不可判定**（跑不出结果，等于没验）。做法：把输入设计成**有限且末尾通向成功**（如「上限 N 次截断、第 N+1 次成功」），于是「上限改成无限」的变异会走到第 N+1 次并成功返回，与断言的「应当抛出」立刻冲突 → 红。案例：`tests/test_distiller_truncation_selfheal.py::test_repair_cap_raises_truncation_error_not_format_error`
- **测「某字段是承重的」时，被删的字段必须经真实产生产出**。判据：若测试自己直接构造了那个对象/异常，删掉字段的**传递链**不会红——因为测试根本没走那条链。案例：截断自愈用例若直接 `IncompleteResponseError(..., content=X)`，则「去掉 `_extract_content` 的 content 传递」只红 adapter 用例、正向自愈用例全绿；改成经真实 `_extract_content` 产出后才 3 条齐红
- **「X 消失了」不足以证明 Y 修好了——要找独立、可交叉验证的指标**。案例：关掉思考后「空正文片数 3→0」，但空正文消失本身也可能只是采样波动，用它证明「思考关掉了」是同义反复。真正的实证是 **tokens / 正文字符 3.85 → 0.62**，与本仓自己的 `_estimate_tokens = int(len*0.6)` 吻合——两个互相独立的量对上，结论才立得住
- **别人给的 premise 与代码不符时，报更正、只修真缺口，不去实现那个不存在的修复**。案例：SSE「截断会硬断流，因为 `_next_piece` 只捕 `StopIteration`」——实读 `chat.py` 外层 `except` 早已 `yield {"error": ...}`、`client.js` 早已渲染，连接不会掉。真缺口是**可识别性**（帧里没有 `code`/`finish_reason`）与**文案漏内部标识**，修的是这两样。按错前提动手会改出一段无人需要、还掩盖真问题的代码
- **修复一处越权 ≠ 这一类修完**。案例：`text.py` 上报的越权与 `voice.py` 两处同形（都因「注入 `user` 却不引用」）；用 AST 扫一遍全仓同类形态，才把 9 处一次分清（3 真越权 / 6 良性），并把扫描固化成测试（见缺陷 9）。逐处手工排查会漏，且下次照旧
- **判「这条 SQL 有没有按身份收窄」时，JOIN 条件不算数 —— 只有 WHERE 筛得掉非属主行**。`JOIN users u ON c.user_id = u.id` 只约束连接行怎么配对，不排除任何行；把它当成身份过滤，会让「无过滤」的原语看起来已收窄。案例：缺陷 19 的普查初版据此把 `get_card` 误判为「已收窄」，命中数 **41**；把判据限定到 WHERE 子句（先在 `GROUP BY`/`ORDER BY`/`LIMIT`/`HAVING` 处截断，再测身份列谓词）后为 **46**。一般化：**判定一个条件是否构成「过滤」，必须问「它能否独立地排除目标行」，而不是「它提到了这个列」**——同一个列名出现在 JOIN ON、ORDER BY、SELECT 列表里，都不构成过滤
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
- **豁免机制本身会成为新的静默通道 —— 一把锁允许「写理由即可跳过」，就必须有**另一样东西**去验那条理由声明的后果**。判据：给任何锁加「豁免出口」时，**同一次改动里**必须同时给出验证该后果的独立断言；否则豁免 = 永久放行。案例：`tests/test_migration_dispatch.py::_NOT_APPLIED` 允许以理由豁免迁移接线，`079_remote_user_profiles.sql` 就躺在里面 —— 三把锁（`test_migration_dispatch` / `test_sqlite_fresh_schema` / 分发形态锁）**各自尽职、全部绿**，而 SQLite 新库仍然缺表；更刺眼的是 079 的豁免理由里**白纸黑字写着**「新库缺表、代码在用」，却没有任何东西去验这句话。理由本身不是闭环，它只是把「未修」从代码搬进了注释。修法是**补一条验后果的锁**（`TestExemptionClosedLoop`：新库表集合 ⊇ `migrations_pg/` 声明的表集合，锁症状本身），**不是**再加一条「文件有没有登记」的形态锁 —— 后者正是被那个合法豁免绕过的那一个。推论：`skip` / `xfail` / allowlist / 豁免名单 / `# type: ignore` 这类机制都应按此自查「谁在验我这条理由」；本轮的 `# store-empty-ok:` 标记同理，其后果由 `tests/test_store_failure_visibility.py` 的动态探针（真的把库打坏，看失败有没有消失）来验，而不是靠标记本身

### 五、验证工具现状

- `tests/perf/mock_llm_server.py`：mock LLM，挂 `/chat/completions` + `/embeddings`，另有 `/admin/set` 控制面
- **思考参数证据档**：`docs/evidence/thinking_budget_evidence.md` + `tests/perf/map_len_probe.py` / `tests/perf/capfield_probe.py` + 原始产物 `docs/evidence/thinking-maplen-before.json` / `thinking-maplen-after.json` / `thinking-capfield.json`。2026-09-11 自 `e2e/scratch/` 提入库、2026-09-12 随「证据产物一等化」迁入 `docs/evidence/`（这批数字被 AGENTS.md 正文引用，产物不入库就无从追溯；此类产物走 `tests/perf/evidence_writer.py` 落盘并登记清单，不是「调试脚本不入库」的例外而是唯一路径）；入库产物已删 `preview` / `content_head`（会逐字带出原文对话），只留统计量
- **证据清单渲染**：`tests/perf/render_evidence.py` → `docs/evidence/resume-numbers.md`（「哪些数字能写进简历」+ 不可写清单 + 出处索引）。渲染产物勿手改，锁会重渲染比对（清单改了不重跑即红）
- **用例到达性证据档**：`tests/perf/raise_probe.py`（pytest 插件，包住 `HTTPException.__init__` 记录实际命中的 `file:line:func`）+ `tests/perf/check_reachability.py`（逐条比对期望 handler）+ 原始产物 `docs/evidence/ownership-reachability.json` / `ownership-reachability-nokey.json`，叙述见 `docs/evidence/raise_sites_evidence.md`。用于证明「每条属主用例真的走到了属主判定」以及「夹具已与测试机凭据无关」（`PROBE_NO_KEY=1` 模拟无 key 机器，须仍全绿）
- `e2e/scratch/`：一次性探针。gitignore 覆盖见 `.gitignore` 的 `/e2e/` 规则；注意另有 `web/frontend/e2e/` 的一组规则，勿混。**调试脚本不入库**（被正文引用数字的探针产物不算例外，走 `tests/perf/evidence_writer.py` 落 `docs/evidence/` 并登记清单 —— 见「开发工作流约束」的「调试脚本不入 main」条）
- **强制走分片路径**：配置里把 `longctx_threshold` 调到 1（另可把 `chunk_size` 调小、`map_concurrency` 调 1 以确定性截杀），**不要改源码**。该做法跑通的证据是 `ev:incomplete-v5`（产物 `docs/evidence/incomplete-v5.json`）。原引的 `.claude/sessions/2026-09-10-distill-dbtruth-closeout.md` 未入库（`.gitignore` 覆盖 `.claude/`），本行不再以它为唯一出处 —— 上面三行配置项是自足的，跑完该分片路径是否真被走到由那份产物判定
- **PG 验证用 throwaway 容器**：`scripts/restore_verify.sh` 里的 `docker run -d --rm` / `docker rm -f`。本仓惯例见会话记录（「PG throwaway（55433）N passed，随后 `docker rm -f`」等）
- **`e2e/helpers.cjs` 路径更正**：根 `e2e/` 下无此文件，实际在 **`web/frontend/e2e/helpers.cjs`**，`BASE = 'http://localhost:7861'`。本机实测 7861 端口**现有 3 条 LISTENING**（PID 21116 占 `0.0.0.0:7861` 与 `[::]:7861`，PID 11408 占 `[::1]:7861`），与「四重监听」不符，以现测为准。**待改成 `127.0.0.1`**；「会连到 Docker 里的旧 build」一说**待验证**

来源（外部文档，非本仓代码）：DeepSeek 思考模式——参数为 `thinking: {"type": "disabled"}`，思考默认开启且 effort=high，思考模式下 `temperature` / `presence_penalty` 被忽略：<https://api-docs.deepseek.com/guides/thinking_mode/>
