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

- **调试脚本不入 main**：一次性复现/调试脚本必须留在 `.gitignore` 覆盖的本地目录（如 `scripts/` 或 `e2e/scratch/`），绝不 `git add` 入库
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

**chunk_size 的来历**：**待验证·无记载**。「实测对比 3000/4500/6000/12000 四档、以卡片质量对标整本喂、12000 太慢 3000 太碎」——`git log -S` 全仓无 `4500` / `12000` 命中，`docs/` 与 `.claude/sessions/` 亦无记录。可查到的只有：3000 起于 2026-05-19（commit `5aee0609`；见 `.claude/sessions/2026-05-19-incremental-distill.md`），现值 5000，classic 地板 6000（`effective_chunk_size`）。结论：**暂定值，无统一标准**。

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

同一批 14 次 map 调用，真实语料 + 生产提示词，同一份探针（`e2e/scratch/map_len_probe.py`；探针内把 `max_tokens` 抬到 8192，测的是**自然输出长度**而非被截断后的长度）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `out_tokens` p50 / max | 8191 / 8192 | **1245 / 2097** |
| `out_chars == 0`（空正文） | 3 | **0** |
| 撞 8192 探针上限 | 7 | **0** |
| 单次耗时 min–max | 12.0–160.0s | **1.4–31.3s** |
| 耗时 > 45s（生产单次 ceiling） | 12/14 | **0/14** |
| 耗时 > 60s（生产 deadline） | 11/14 | **0/14** |
| tokens / 正文字符 | 3.85 | **0.62** |

**口径**：n=14、单模型（deepseek-v4-pro）、单供应商、`temperature` 取生产默认 0.7。探针在 scratch 内把 `_GEN_ATTEMPT_S` / `_GEN_DEADLINE_S` 抬到 120 / 240 以测「模型自然输出」，故表中 45s / 60s 两行是**按生产口径回算**的，不是探针真实超时。修复前 / 修复后原始产物见 `tests/perf/out_maplen.prefix.json` / `tests/perf/out_maplen.json`（2026-09-11 自 `e2e/scratch/` 提入库），脚本、前置配置与逐行复算口径见 `tests/perf/thinking_budget_evidence.md`。

**4096 维持结论**：`max_tokens=4096` 不动 —— 0/14 达到其 80%（≥3277），max 2097，约 2× 余量；且已有环境变量出口（`_resolve_max_tokens`，`adapters/llm_adapter.py`，阶梯=显式 arg > `LLM_MAX_TOKENS` > config.yaml > 4096），要动不用发版。另：3 片 `out_chars=1` 是模型按提示词答「无」（U+65E0），**属正常应答，不是缺陷**。

### 三、已知缺陷

**1. thinking 参数写错（方言不对）** —— 状态：**已修**（commit `f2dfd23`，2026-09-10）
- 现象（实测）：3 条探针里 2 条吐 `reasoning_content` 12441 / 12413 字符、`content` **0** 字符、`finish_reason='length'`，耗时 161.9s / 122.4s（第 3 条 reasoning 8735、`content` 2060、`stop`、131.5s）。产物见 `tests/perf/out_capfield.json`（2026-09-11 自 `e2e/scratch/` 提入库）
- 根因（修复前）：四处调用点都传 `extra_body={"enable_thinking": False}`——那是 Qwen 方言，DeepSeek 不认、静默忽略。DeepSeek 写法是 `extra_body={"thinking": {"type": "disabled"}}`（外部文档，见本节末来源）
- 后果链：思考默认开启（effort=high）→ 思考与正文**共享** `max_tokens` 预算 → 思考吃光预算 → `content` 为空 + `finish_reason='length'` → 落一条空串行 → 前端显示「本片无信息」。且思考模式下 `temperature` / `presence_penalty` 被忽略（同来源，修复前传的值不起作用）
- 连锁：`llm.max_tokens=4096`，而修复前实测思考单项就 >8k token → 大分片的失败形态不只是空 content，还大概率直接撞 `_GEN_DEADLINE_S` 超时（见缺陷 8）
- 修法：收敛为单一控制点。`_THINKING_DISABLED` 一张表表达「意图 → 方言 payload」，`_detect_dialect` 由 `base_url`（优先）/`model` 解析供应商，四个调用点只调 `_request_options()`。**未知供应商不传 extra_body**（安全默认：宁可开着思考，也不发一个可能被 400 拒的未知字段）。`base_url` 用户可配这点按方言判断处理，没有一刀切
- 回归锁：`tests/test_llm_adapter_thinking.py` 断言三种 base_url 的 wire format + 四个调用点都接上控制点（修复前该文件断言的恰是**错的**方言）

**2. 适配器从不读 `finish_reason`** —— 状态：**已修**（commit `64d2d14`，2026-09-10；`content_filter` 等未完成值收严于 `d068242`）
- 证据（修复前）：全仓生产代码零命中，仅测试 mock 出现（`tests/perf/mock_llm_server.py` 的 mock 分支）
- 后果（修复前）：截断/饿死的响应被当成功返回并落库
- 实测证据：`e2e/scratch/out_v5.json`——52 字节半截内容落满 6 片、二次续跑 map 调用 = 0（第二道门判「非空」即复用）
- 修法：`_check_finish_reason` 为唯一裁决点。`_INCOMPLETE_FINISH_REASONS` = `{length, content_filter, insufficient_system_resource}` → 抛 `IncompleteResponseError`（带 `finish_reason` 字段）；`stop` / `tool_calls` 放行；**真正陌生的值**与缺失点名 WARN 后放行（供应商语义确实不一，不阻断——但 `content_filter` / `insufficient_system_resource` 不是陌生值，是已知未完成终态，放行等于当成功）
- 四个提取点收敛：`chat` / `async_chat` → `_extract_content`，`chat_with_tools` → `_checked_message`，`chat_stream` 每 chunk 先校验再吐、流尽仍无终态判缺失
- 可辨性：截断（`IncompleteResponseError`）≠ 网络失败（`RuntimeError`）≠ 空内容（放行但返回 `""`）
- 处置建议分两张表：`_INCOMPLETE_ACTIONS`（运维口径：抬预算 / 改输入 / 可重试）与 `_INCOMPLETE_USER_MESSAGES`（上屏口径，经 `IncompleteResponseError.user_message` 取）
- SSE 侧：`web/routers/chat.py` 的 `_stream_error_payload` 给未完成终态附 `code=incomplete_response` + `finish_reason`，前端 `web/frontend/src/api/client.js` 透传为 `AppError.code`
- 注：截断**不会**让 SSE 硬断流——外层 `except` 早已 yield 错误帧，缺的是可识别性而非连接存活性
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

**4. 改原文后旧分片永不刷新**
- 根因：两个 store（`storage/sqlite_store.py`、`storage/postgres_store.py`）的 `save_distill_chunk` 都是 `INSERT ... ON CONFLICT (task_id, chunk_index) DO NOTHING`
- 现象：原文变 → 片指纹变 → 门 3（`_resume_hit` 第 3 道）拒绝复用 → 重跑该片 → 新结果想写回，撞 `DO NOTHING` → 库里仍是旧 `result` + 旧 fingerprint → 下次续跑门 3 再次拒绝 → **该片每次续跑都重跑，永不收敛到满复用**
- 影响面：只掉「省调用量」，不掉正确性——当前轮 reduce 吃的是内存里的新结果（`distill_incremental_stream` 内 `map_results.append` → `raw_analyses`），落库副本陈旧只影响下次续跑的复用判据
- 状态：未修

**5. sqlite 新库缺 `users.embedding_key` / `embedding_region`** —— 状态：**已修**（2026-09-11）
- 病灶两层：(1) `storage/migrations/067_embedding_config.sql` 用 `ALTER TABLE users ADD COLUMN IF NOT EXISTS ...`——SQLite 无此语法，`executescript` 解析期即抛 `near "EXISTS": syntax error`；(2) 执行块的 `except` 只吞「duplicate column」，这条错误串不匹配 → 打一行 print 就放过。**报错真的发生了，被按字符串匹配漏掉，静默继续**
- 后果：新建 sqlite 库永远缺 `users.embedding_key` / `embedding_region` → `get_user_api_config` 的 `SELECT u.embedding_key` 抛 `OperationalError` → 500。生产是 PG（该语法合法）不受影响；受影响的是新开发环境与 `start_all.bat`
- 修法：067 的 SQLite 版去掉 `IF NOT EXISTS`（PG 侧 `storage/migrations_pg/002_embedding_config.sql` 独立、不动）；执行块改 `PRAGMA table_info(users)` **前置判断**——读出现有列，缺哪列 ALTER 哪列，不缺就跳过。**确定性执行取代猜错误串**；该块不再有 except，迁移真失败照常上抛，不再静默
- 回归锁：`tests/test_sqlite_fresh_schema.py`（建真库跑迁移，不是正则扫文件）：(a) 新库列齐 (b) `get_user_api_config` 不抛 (c) 同库两次 init (d) 建库 stdout 无失败行——一处断言兜住全部迁移文件 (e) 半成品库（一列有一列无）只补缺列
- **盲区说明**：`tests/test_schema_parity.py` 是正则扫 `.sql` 文本、「写了」就算「跑成了」，本缺陷正是该盲区的实例——补齐它需要真建库的用例（即上方回归锁）
- 迁移执行区仍有 **75 处「失败只 print、从不重抛」**（56 处靠错误串判断可否忽略、19 处裸吞），是本病灶的同族存量，见缺陷 15

**6. `/api/admin/tasks` 读内存不读库** —— 状态：**已修**（commit `bac7133` / `254455c`，2026-09-11）
- `web/routers/admin.py` 的 `admin_tasks`：原 `from routers.distill import _tasks, _task_lock`；`{"task_id": tid, **task}` 把内存条目**原样展开**
- 泄漏内部字段：条目由 `web/routers/distill.py` 写入，含 `user_id` 与 `_db`（写入与判等都在该文件）
- 影响：重启后内存为空 → 管理员看不到 `interrupted` 行（那是开机 reconcile 的落库产物），且响应带 `user_id` / `_db`
- 修法：改读库 `list_distill_tasks()`（存储层 `ORDER BY updated_at DESC` 的有界列表，base + PG/SQLite 双实现），逐行走共享序列化器 `_task_response(row, mem)` —— 它本身就是逐键白名单，行里的 `user_id` 从构造上进不来；`mem` 只覆盖 `stage` / `message`，**永不覆盖** `status` / `progress_pct` / 存在性（红线由共享序列化器强制，不由本调用方自觉）。出参信封 `{tasks, total, truncated}`，`truncated = total > len(rows)` **实算**，不写死上限
- **200 条上限是显式上报的截断，不是静默截断** —— 见 §四「有上限的路径必须显式上报被裁过」
- 回归锁：`tests/test_admin_tasks_api.py`（`TestVisibleAfterRestart` 覆盖「重启后 interrupted 行可见」、`TestOutputWhitelist` 覆盖 `user_id` / `_db` 不出现、`TestEnvelope` 覆盖 `total` / `truncated`）

**7. `get_upload_task_status` 注入 user 但不校验归属**
- `web/routers/text.py` 的 `get_upload_task_status`：签名取 `user: dict = Depends(get_current_user)`，函数体只 `_upload_tasks.get(task_id)`，**全程无归属比对**
- 且条目本身不含属主：写入的键只有 `status` / `progress_pct` / `message` / `text_id` → 想校验也无从校验。状态：未修

**8. `_GEN_DEADLINE_S = 60.0` 写死，不可 env 覆盖** —— 状态：**保留记账**
- `_GEN_DEADLINE_S`（`adapters/llm_adapter.py`）；同组的三个 ceiling `LLM_DECISION_ATTEMPT_S` / `LLM_GEN_ATTEMPT_S` / `LLM_STREAM_ATTEMPT_S` 都可 env 覆盖，deadline 仍无出口
- 关掉思考后（2026-09-10 实测，n=14）：**0/14 超 60s，max 31.3s**（>45s 也是 0/14）→ 当前工作负载已不再顶它，见 §二基线表
- 历史（修复前，思考未真正关闭）：单次 map 调用 12.0–160.0s，>45s 12/14、>60s 11/14（`tests/perf/out_maplen.prefix.json`）→ 当时超过 60s 的调用在生产上必然失败
- 生产是否仍有超时：**待验证**（受本机吞吐、网络、prod `.env` 覆盖值影响）。常量仍写死不可 env 覆盖，保留记账

**9. 端点注入 `get_current_user` 却不引用 `user`（越权一类）** —— 状态：**已修**（2026-09-10）
- 形态：签名取 `user: dict = Depends(get_current_user)`，函数体从不引用它 → 无归属校验，任何登录用户拿 id 就能读他人资源
- 全仓 AST 扫描（`web/routers/*.py`）命中 **9 处**：
  - **3 处真越权，已修**：`text.py` 的 `get_upload_task_status`（上传任务：任意 task_id 读他人进度/错误）、`voice.py` 的 `preview_audio`（自定义音色文件：绕过私有的 `/list` 过滤与删除/上传的属主校验）、`voice.py` 的 `voice_synthesize`（`card_id` 参考音频：兄弟端点 `preview_ref_audio` 查卡主，唯独它不查）。统一 fail closed + 非属主 **404**（不用 403，避免靠状态码枚举资源是否存在）
  - **6 处良性**（只当登录门，读全局/公开数据）：`auth.py` 的 `get_announcement`、`market.py` 的 `get_card_versions` / `get_card_forks` / `list_post_comments`、`voice.py` 的 `voice_status` / `speech_to_text`
- 边界锁：`tests/test_auth_param_used.py` 固化这次 AST 扫描 + 白名单，将来新增同类端点自动变红，不必靠人再扫一遍
- 注：`voice.py` 内既有四处查属主返回 **403**（`get_ref_audio` / `preview_ref_audio` / `upload_ref_audio` / `delete_ref_audio`），与新修的 404 并存，口径待统一

**10. `market.py` 的 `list_post_comments` 不标 `liked_by_me`** —— 状态：未修
- 同类端点 `text.py` 的 `get_text_comments` 用 `user["id"]` 标了 `liked_by_me`，这个没标——它也因此进了上面第 9 条的扫描名单（读 user 与否在这里是「功能与否」，不是「越权与否」）
- 属**功能缺口，非安全问题**

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

**13. 存量约 38 处属主型 `HTTPException(403)` 待按 §四 翻成 404** —— 状态：未修（已立项）
- §四 已定口径：授权失败一律 404，理由是防资源存在性枚举。**新增端点一律按此办**
- 存量仍有约 38 处属主型 403，分布在 `web/routers/` 的 `card.py` / `group.py` / `memory.py` / `market.py` / `voice.py` / `message.py` 等
- 与缺陷 11/12 分开立项的理由：这批**语义一致**（都是 403），不存在缺陷 11 的「原语无身份」或缺陷 12 的「同函数两语义」那种不一致缺陷——它是一次性口径大扫，不是逐处排错
- `tests/test_security_authz.py` 的 `test_03/04/05`（card、group）断言 403，对应的就是这批；批量翻新时它们要一起改
- 翻新前先核前端：`web/frontend/src/api/client.js` / `useAppStore.js` 里按 403 分支的逻辑落在 `web/routers/distill.py` 的 task 端点（`/api/distill/task/{taskId}`）与 `web/frontend/src/pages/BookReader.jsx` 的 `!r.ok` 通用分支上；批量翻新会动到它们，需先确认依赖

**14. 已报数字指向 gitignored 产物（可追溯性缺口）** —— 状态：未修（已立项）
- 形态：AGENTS.md / 会话记录引用的原始产物留在 `e2e/scratch/`（被 `.gitignore` 的 `/e2e/` 规则覆盖），读者按引用去查是空的 —— **引用等于没有出处**，而这正是证据档存在的意义
- 已知一例：缺陷 2 引用的 `e2e/scratch/out_v5.json`。**全仓清点尚未做**，同类可能还有其它处
- 处置方向：逐个确认「产数脚本 + 原始产物」是否入库（落点沿用 `tests/perf/` 先例）；产物含正文或凭据的**删字段不删文件**
- 已处置可参照：缺陷 1 / 缺陷 8 与 §二 基线表的产物已于 2026-09-11 提入库，见 §五「思考参数证据档」

**15. `034_post_enhancements` 在已建库上每次 init 都打一行假失败；另有 19 处裸吞** —— 状态：未修（已立项）
- 修 067（缺陷 5）时顺带实测到：同一个库第二次 `_ensure_initialized` 会打 `[SQLiteStore] Post enhancements migration failed: duplicate column name: images`。该块（`storage/sqlite_store.py` 的 034 段）是 `except Exception: print` **连 duplicate column 都不吞**，故「已建库重跑」这一正常路径每次都报失败
- 两层问题：(1) **假失败**——列已存在的正常情况被报成 failed，噪音会训练人忽略真失败；(2) **真失败被吞**——该块无重抛，034 真出错也只留一行 print 后继续
- 同族存量（迁移执行区实测）：75 处「失败只 print、从不重抛」，其中 56 处靠错误串判断可否忽略、19 处裸吞。与缺陷 5 同病灶（「失败被吞成正常返回」谱系）
- **未同轮修的理由**：067 spec 的硬要求是「发现未覆盖的问题只列不修、停下报告」，不自行扩大范围。此外**该缺陷直接影响验收口径**：`tests/test_sqlite_fresh_schema.py` 的 (c)「同库两次 init 无失败输出」因此只能把断言限定在 067；(d) 的「整类兜底」也只覆盖首次建库——修掉 034 后两者都可升级为全强度
- 附带：缺陷 5 修好后，`tests/test_security_authz.py` / `test_distill_task_api.py`（3 处）/ `test_rag_unusable.py` 里那 5 处「让 `get_user_api_config` 返回空配置」的夹具绕过已成多余（保留无害，但注释里「因缺陷 5」的理由已过期）

### 四、验证纪律

- **基线数字现跑现取**（测试通过数、函数签名）：禁止引用上一轮结果或凭记忆。引用代码一律用符号名（函数/常量/测试名），不写行号——行号随改动漂移且无测试报警
- **并发/事务/锁的结论不给「应该如此」，只给「要验什么、怎么验」**。案例：MVCC 下 `WHERE EXISTS` 是快照读、不加锁，挡不住「父行正在被删、还没提交」——必须补 `FOR SHARE`。本仓已成文于 `storage/postgres_store.py` 的 `save_distill_chunk`（含死锁无环分析）
- **SQLite 全绿不构成并发命题的证据**：SQLite 写是库级序列化，窗口从根上不存在（`storage/sqlite_store.py` 的 `save_distill_chunk` 自述），生产是 PG。同一段逻辑在 sqlite 上验不出 PG 的行锁语义
- **mock 的形态 ≠ 被测对象的形态**。案例：mock 回 canned JSON，据此误判 map 输出是 JSON——实际是自然语言（prompt 见 `core/distiller.py` 的 `_map_system_prompt` / `_map_user_prompt`；消费侧只判「非空且 ≠『无』」，见 `distill_incremental_stream` 内 `raw_analyses`）。据此设计的 JSON 校验会否掉所有真实 map 结果
- **测试通过 ≠ 命题成立**，可能只是那条路径根本没被走到。案例：分片续跑逻辑落地后长期未真实执行——短文本恒走长上下文路径（分流在 `distill_incremental_stream`），从不进分片。要验分片路径必须显式强制（见第五节）
- **修复必须做变异验证**：改坏它，指定测试必须变红。案例：把兜底调用从 `finally` 里整行删掉，**原 14 个测试仍全绿** → 接线没被覆盖；随后补 `TestA2Wiring` 两条（commit `9d2a9e4`，见 `.claude/sessions/2026-09-10-distill-dbtruth-closeout.md`）
- **「X 消失了」不足以证明 Y 修好了——要找独立、可交叉验证的指标**。案例：关掉思考后「空正文片数 3→0」，但空正文消失本身也可能只是采样波动，用它证明「思考关掉了」是同义反复。真正的实证是 **tokens / 正文字符 3.85 → 0.62**，与本仓自己的 `_estimate_tokens = int(len*0.6)` 吻合——两个互相独立的量对上，结论才立得住
- **别人给的 premise 与代码不符时，报更正、只修真缺口，不去实现那个不存在的修复**。案例：SSE「截断会硬断流，因为 `_next_piece` 只捕 `StopIteration`」——实读 `chat.py` 外层 `except` 早已 `yield {"error": ...}`、`client.js` 早已渲染，连接不会掉。真缺口是**可识别性**（帧里没有 `code`/`finish_reason`）与**文案漏内部标识**，修的是这两样。按错前提动手会改出一段无人需要、还掩盖真问题的代码
- **修复一处越权 ≠ 这一类修完**。案例：`text.py` 上报的越权与 `voice.py` 两处同形（都因「注入 `user` 却不引用」）；用 AST 扫一遍全仓同类形态，才把 9 处一次分清（3 真越权 / 6 良性），并把扫描固化成测试（见缺陷 9）。逐处手工排查会漏，且下次照旧
- **任何「新建/添加/引入」类动作，先确认它是否已存在**（仓库里多半已有同形实现或同名字段）
- **授权失败一律 404，不用 403**：403 说「资源存在但你没权限」，泄漏存在性；404 让「非属主」与「不存在」不可区分。攻击者拿一批 id 扫描时，403/404 的差异就是存在性枚举的预言机。新增端点沿用此口径；已下沉到 storage `*_owned` 原语的端点天然如此（拿不到行即 404）。判据与理由落在 `tests/test_security_authz.py::TestReadAuthorization` 的文档串里（存量未翻新的 38 处见缺陷 13）
- **同一判定的两条分支必须同判一个拒绝码**。案例：`web/routers/chat.py` 的 `_ensure_session`，内存命中分支对非属主判 403、DB 重建分支判 404。状态码在分支间不一致，攻击者反复请求、靠命中/未命中的差异就能推断资源是否存在——内存路径把 DB 路径的防枚举漏掉了。已统一为 404（含同一条文案），由 `tests/test_security_authz.py::TestHoleOwnershipRegression::test_19_chat_memory_hit_non_owner_404` 锁住
- **形态锁与语义用例是两层防线，各管各的，缺一不可**。案例：`tests/test_storage_scope_lock.py` 扫的是调用形态，看不到 SQL 体——把 `storage/sqlite_store.py` 的 `get_text_owned` 里 `AND user_id=?` 去掉，**六条语义用例全红而边界锁全绿**；反过来把调用点改回 `*_unscoped`，锁红。所以「锁绿」不等于读取安全，两层都要有
- **用例恒绿可能是双门互相兜底，不是命题成立**。案例：`resume_session` / `_ensure_session` 是双门（session 属主门 + 下游 `get_text_owned`），只把 session 门改回 `*_unscoped`，下游门兜住、用例仍绿。要让单门暴露，夹具必须刻意让开另一道门（`TestHoleOwnershipRegression` 里让 A 的 card 指向 B 的 text），否则这条用例锁的是「两道门都没了」，而不是「这一道门在」
- **定性一个环境缺陷前先做反向对照，把可疑变量逐个摘掉**。案例：本机 chroma 段错误（0xC0000005）一度被定性为「跨平台读容器写的数据会崩」（可疑变量=数据来源）；反向对照——临时目录内本机自建集合并 `add` 两个向量，同样段错误——证明与数据来源无关，真实范围是「任何非空集合的任何操作」（空集合 `count()`、`get_collection` 正常）。范围写窄了，后人会按错误的边界做决策
- **有上限 / 截断 / 采样的返回路径必须显式上报「被裁过」**。判据：写任何 LIMIT / cap / 采样 / 分页路径时，**在同一次改动里**就决定截断如何上报（`total` / `truncated` 这类显式信号），确实报不了就明说理由，别默认静默。案例：`admin_tasks` 的 200 条上限若不报 `total` / `truncated`，被裁掉的任务在管理页上完全不可见 —— 与 §三 缺陷 2 的「截断响应当成功返回」同病灶，是「失败被吞成正常返回」的**第六次形态**（前五次：线程弃船 / 384 维度不符 / 截断响应 / `finish_reason` 缺失 / `$contains` 恒不命中）。**修 A 时若自己埋下同形态的 B，当场修掉，不许记账放过**。回归锁：`tests/test_admin_tasks_api.py::TestEnvelope`
- **聚合统计必须写明口径**（分组前 / 后、上中位 / 下中位、含不含空样本）。判据：**产物里的字段名与文档表格里的名字不一致时必须显式对照** —— 同一份数据用两种口径能算出两个数，复算时看着像「数字对不上」，被追问时最致命。案例：thinking 证据表的 `out_tokens` p50 是 14 条**合并后**的 nearest-rank 上中位（`sorted(outs)[n//2]` → 8191 / 1245），产物 `summary[].out_tokens_p50` 却是按档（5000 / 6000 字符）**分组**、`int(round(0.5*(n-1)))` 取的**下**中位（8192 / 6079 → 1446 / 1177）；两者各自自洽，混用则得 6487 / 1205，像造假。复算口径成文于 `tests/perf/thinking_budget_evidence.md` 第 4 节

### 五、验证工具现状

- `tests/perf/mock_llm_server.py`：mock LLM，挂 `/chat/completions` + `/embeddings`，另有 `/admin/set` 控制面
- **思考参数证据档**：`tests/perf/thinking_budget_evidence.md` + `map_len_probe.py` / `capfield_probe.py` + 原始产物 `out_maplen.prefix.json` / `out_maplen.json` / `out_capfield.json`。2026-09-11 自 `e2e/scratch/` 提入库（「调试脚本不入库」的例外：这批数字被 AGENTS.md 正文引用，产物不入库就无从追溯）；入库产物已删 `preview` / `content_head`（会逐字带出原文对话），只留统计量
- `e2e/scratch/`：一次性探针。gitignore 覆盖见 `.gitignore` 的 `/e2e/` 规则；注意另有 `web/frontend/e2e/` 的一组规则，勿混。**调试脚本不入库**
- **强制走分片路径**：配置里把 `longctx_threshold` 调到 1（另可把 `chunk_size` 调小、`map_concurrency` 调 1 以确定性截杀），**不要改源码**。做法记录于 `.claude/sessions/2026-09-10-distill-dbtruth-closeout.md`
- **PG 验证用 throwaway 容器**：`scripts/restore_verify.sh` 里的 `docker run -d --rm` / `docker rm -f`。本仓惯例见会话记录（「PG throwaway（55433）N passed，随后 `docker rm -f`」等）
- **`e2e/helpers.cjs` 路径更正**：根 `e2e/` 下无此文件，实际在 **`web/frontend/e2e/helpers.cjs`**，`BASE = 'http://localhost:7861'`。本机实测 7861 端口**现有 3 条 LISTENING**（PID 21116 占 `0.0.0.0:7861` 与 `[::]:7861`，PID 11408 占 `[::1]:7861`），与「四重监听」不符，以现测为准。**待改成 `127.0.0.1`**；「会连到 Docker 里的旧 build」一说**待验证**

来源（外部文档，非本仓代码）：DeepSeek 思考模式——参数为 `thinking: {"type": "disabled"}`，思考默认开启且 effort=high，思考模式下 `temperature` / `presence_penalty` 被忽略：<https://api-docs.deepseek.com/guides/thinking_mode/>
