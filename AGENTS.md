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

> 本节所有 `file:line` 于 2026-09-10 现读现取。核不到的已标「待验证」，未凭记忆写成断言。

### 一、两条路由（最容易被误解的事）

`core/distiller.py:1379` 按 `_estimate_tokens(text)` 与 `self._longctx_threshold` 分流：

- **低于阈值 → 长上下文单次调用**：`distiller.py:1384` 直接 `_distill_longcontext_stream`，全文喂一次。**不分片、不写 `distill_chunks`**，中途崩了整本重来（docstring 自述见 `distiller.py:1383-1385`）
- **达到阈值 → MapReduce 分片**：`distiller.py:1389` 起走 `effective_chunk_size` + `_split_chunks`
- 同一分流的非流式版本在 `distiller.py:1158-1169`
- `_estimate_tokens` = `int(len(text) * 0.6)`（`distiller.py:897-899`）。配合 `longctx_threshold: 150000`（`config.yaml:3`）→ **约 25 万字符以下的文本全部走整本单次调用**。杀破狼 652990 字符 → 估算 ~39 万 > 15 万，才进分片

**结论**：断点续跑（`distill_chunks` 落库 + 三重门复用）**只在分片路径生效**；短文本从来不进这条路，也就永远不产生分片检查点。

### 二、模型与参数现值

config.yaml 现值（现读，非转述）：

- `distill.chunk_size: 5000`（`config.yaml:2`）
- `distill.longctx_threshold: 150000`（`config.yaml:3`）
- `llm.max_tokens: 4096`（`config.yaml:7`）
- `llm.model: deepseek-v4-pro`（`config.yaml:8`）
- `llm.temperature: 0.7`（`config.yaml:10`）
- 另有 `rag.chunk_size: 500`（`config.yaml:18`）—— **是 RAG 检索切块，与蒸馏分片无关，勿混**
- `map_concurrency` 不在 config.yaml → 走代码默认 **30**（`core/distiller.py:235`）
- classic 档分片强制放大到 ≥6000：`effective_chunk_size`（`core/distiller.py:247-253`）

**模型规格**：deepseek-v4-pro 上下文 1M token / 最大输出 384K —— **待验证**（代码与仓库内无此声明，来自口述的「官方 GA 0813」，未找到出处）。

**chunk_size 的来历**：**待验证·无记载**。「实测对比 3000/4500/6000/12000 四档、以卡片质量对标整本喂、12000 太慢 3000 太碎」——`git log -S` 全仓无 `4500` / `12000` 命中，`docs/` 与 `.claude/sessions/` 亦无记录。可查到的只有：3000 起于 2026-05-19（commit `5aee0609`；`.claude/sessions/2026-05-19-incremental-distill.md:10`），现值 5000，classic 地板 6000（`distiller.py:253`）。结论：**暂定值，无统一标准**。

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

**口径**：n=14、单模型（deepseek-v4-pro）、单供应商、`temperature` 取生产默认 0.7。探针在 scratch 内把 `_GEN_ATTEMPT_S` / `_GEN_DEADLINE_S` 抬到 120 / 240 以测「模型自然输出」，故表中 45s / 60s 两行是**按生产口径回算**的，不是探针真实超时。修复前数据存于 `e2e/scratch/out_maplen.prefix.json`（`out_maplen.json` 已被修复后的同批数据覆盖）。

**4096 维持结论**：`max_tokens=4096` 不动 —— 0/14 达到其 80%（≥3277），max 2097，约 2× 余量；且已有环境变量出口（`_resolve_max_tokens`，`adapters/llm_adapter.py:332`，阶梯=显式 arg > `LLM_MAX_TOKENS` > config.yaml > 4096），要动不用发版。另：3 片 `out_chars=1` 是模型按提示词答「无」（U+65E0），**属正常应答，不是缺陷**。

### 三、已知缺陷

**1. thinking 参数写错（方言不对）** —— 状态：**已修**（commit `f2dfd23`，2026-09-10）
- 现象（实测）：单片吐 `reasoning_content` 8735–12441 字符、`content` **0** 字符、`finish_reason='length'`，耗时 122.4–161.9s（`e2e/scratch/out_capfield.prefix.json`，不入库）
- 根因（修复前）：四处调用点（旧行号 `:327` / `:370` / `:412` / `:479`）都传 `extra_body={"enable_thinking": False}`——那是 Qwen 方言，DeepSeek 不认、静默忽略。DeepSeek 写法是 `extra_body={"thinking": {"type": "disabled"}}`（外部文档，见本节末来源）
- 后果链：思考默认开启（effort=high）→ 思考与正文**共享** `max_tokens` 预算 → 思考吃光预算 → `content` 为空 + `finish_reason='length'` → 落一条空串行 → 前端显示「本片无信息」。且思考模式下 `temperature` / `presence_penalty` 被忽略（同来源，修复前 `:366` / `:368` 传的值不起作用）
- 连锁：`llm.max_tokens=4096`，而修复前实测思考单项就 >8k token → 大分片的失败形态不只是空 content，还大概率直接撞 `_GEN_DEADLINE_S` 超时（见缺陷 8）
- 修法：收敛为单一控制点。`_THINKING_DISABLED`（`adapters/llm_adapter.py:238`）一张表表达「意图 → 方言 payload」，`_detect_dialect`（`:245`）由 `base_url`（优先）/`model` 解析供应商，四个调用点只调 `_request_options()`（`:432`；调用点 `:465` / `:510` / `:554` / `:632`）。**未知供应商不传 extra_body**（安全默认：宁可开着思考，也不发一个可能被 400 拒的未知字段）。`base_url` 用户可配这点按方言判断处理，没有一刀切
- 回归锁：`tests/test_llm_adapter_thinking.py` 断言三种 base_url 的 wire format + 四个调用点都接上控制点（修复前该文件断言的恰是**错的**方言）
- 行号读取于 2026-09-10

**2. 适配器从不读 `finish_reason`** —— 状态：**已修**（commit `64d2d14`，2026-09-10；`content_filter` 等未完成值收严于 `d068242`）
- 证据（修复前）：全仓生产代码零命中，仅测试 mock 出现（`tests/perf/mock_llm_server.py:263` / `:291` / `:295`）
- 后果（修复前）：截断/饿死的响应被当成功返回并落库
- 实测证据：`e2e/scratch/out_v5.json`——52 字节半截内容落满 6 片、二次续跑 map 调用 = 0（第二道门判「非空」即复用）
- 修法：`_check_finish_reason`（`adapters/llm_adapter.py:311`）为唯一裁决点。`_INCOMPLETE_FINISH_REASONS`（`:269`）= `{length, content_filter, insufficient_system_resource}` → 抛 `IncompleteResponseError`（`:293`，带 `finish_reason` 字段）；`stop` / `tool_calls` 放行；**真正陌生的值**与缺失点名 WARN 后放行（供应商语义确实不一，不阻断——但 `content_filter` / `insufficient_system_resource` 不是陌生值，是已知未完成终态，放行等于当成功）
- 四个提取点收敛：`chat` / `async_chat` → `_extract_content`（`:327`），`chat_with_tools` → `_checked_message`（`:321`），`chat_stream` 每 chunk 先校验再吐、流尽仍无终态判缺失
- 可辨性：截断（`IncompleteResponseError`）≠ 网络失败（`RuntimeError`）≠ 空内容（放行但返回 `""`）
- 处置建议分两张表：`_INCOMPLETE_ACTIONS`（`:277`，运维口径：抬预算 / 改输入 / 可重试）与 `_INCOMPLETE_USER_MESSAGES`（`:285`，上屏口径，经 `IncompleteResponseError.user_message` `:306` 取）
- SSE 侧：`web/routers/chat.py:30` `_stream_error_payload` 给未完成终态附 `code=incomplete_response` + `finish_reason`，前端 `web/frontend/src/api/client.js:231` 透传为 `AppError.code`
- 注：截断**不会**让 SSE 硬断流——外层 `except` 早已 yield 错误帧，缺的是可识别性而非连接存活性
- 回归锁：`tests/test_llm_adapter_finish_reason.py`（含变异验证）、`tests/test_chat_stream_error.py`
- 行号读取于 2026-09-10

**3. 第二道门是「非空」门，不是「结构合法」门** —— 状态：纵深防御（主屏障已上移）
- `core/distiller.py:194`：`if not (isinstance(cand["result"], str) and cand["result"].strip()): return None`
- 只判非空，挡不住非空的截断文本；docstring 自述即如此（`distiller.py:182`：「第 2 道是**纵深防御**，不是契约……不承诺结构校验」）
- 三门位置：门 1 形状 `:192`、门 2 非空 `:194`、门 3 指纹 `:196`
- **主屏障在上游，不在本门**：`distill_incremental_stream` 的失败 Map 片不落 checkpoint（抛异常即跳过 `on_chunk_done`），正常路径下这里不该出现空串候选。本门只挡「任何路径往 checkpoint 写入空结果」这一类错误
- Map 返回自由文本角色证据、不产 JSON —— 「半截 JSON」不是本门的场景（曾如此误写）
- **边界约束：`core/` 内任何位置（含注释与 docstring）不得出现 adapter 层异常类名**。描述边界用语义表达（「该层遇未完成终态即抛异常」），类名只留在 adapter 层
- 该约束由 `tests/test_chat_stream_error.py::test_no_exception_class_leaks_into_core_web_storage` 强制（**文本级 grep——注释与 docstring 也拦**）
- 门的**范围**由活断言钉住：`tests/test_distill_resume.py::TestResumeHitDoors::test_truncated_nonempty_result_passes_second_gate`（非空截断的自由文本必须穿过、不得被拦）——给本门加结构校验会让它变红
- 行号读取于 2026-09-10

**4. 改原文后旧分片永不刷新**
- 根因：两个 store 的 `save_distill_chunk` 都是 `INSERT ... ON CONFLICT (task_id, chunk_index) DO NOTHING`（`storage/sqlite_store.py:4088-4092`、`storage/postgres_store.py:3001-3005`）
- 现象：原文变 → 片指纹变 → 门 3（`distiller.py:189`）拒绝复用 → 重跑该片 → 新结果想写回，撞 `DO NOTHING` → 库里仍是旧 `result` + 旧 fingerprint → 下次续跑门 3 再次拒绝 → **该片每次续跑都重跑，永不收敛到满复用**
- 影响面：只掉「省调用量」，不掉正确性——当前轮 reduce 吃的是内存里的新结果（`core/distiller.py:1480` `map_results.append` → `:1507-1509` `raw_analyses`），落库副本陈旧只影响下次续跑的复用判据
- 状态：未修

**5. sqlite 新库缺 `users.embedding_key` / `embedding_region`**
- `storage/migrations/067_embedding_config.sql` 用 `ALTER TABLE users ADD COLUMN IF NOT EXISTS ...`——SQLite 不支持该语法
- 根因：`executescript` 解析期抛 syntax error，被 `storage/sqlite_store.py:684-689` 的 `except` 捕获后只 print（「duplicate column」之外都打印、不重抛）→ 被吞
- 作者已知：`sqlite_store.py:782` 注释明写「migration 067 uses IF NOT EXISTS (SQLite syntax error)」，并在 `:783-784` 用动态列过滤绕行
- PG 不受影响：`storage/migrations_pg/002_embedding_config.sql`（PG 支持该语法）
- 影响面：**仅新建的 sqlite 库**；生产是 PG。状态：未修

**6. `/api/admin/tasks` 读内存不读库**
- `web/routers/admin.py:571-584`：`:578` `from routers.distill import _tasks, _task_lock`，`:581` `{"task_id": tid, **task}` 把内存条目**原样展开**
- 泄漏内部字段：条目含 `user_id`（`web/routers/distill.py:189` / `:220`）与 `_db`（`:157` 写、`:186` / `:217` 判等）
- 影响：重启后内存为空 → 管理员看不到 `interrupted` 行（那是开机 reconcile 的落库产物），且响应带 `user_id` / `_db`。状态：未修

**7. `get_upload_task_status` 注入 user 但不校验归属**
- `web/routers/text.py:203-221`：签名取 `user: dict = Depends(get_current_user)`（`:206`），函数体只 `_upload_tasks.get(task_id)`（`:210`），**全程无归属比对**
- 且条目本身不含属主：写入的键只有 `status` / `progress_pct` / `message` / `text_id`（`text.py:59` / `:78` / `:84` / `:199`）→ 想校验也无从校验。状态：未修

**8. `_GEN_DEADLINE_S = 60.0` 写死，不可 env 覆盖** —— 状态：**保留记账**
- `adapters/llm_adapter.py:62`；同组的三个 ceiling 都可 env 覆盖（`:85-87`：`LLM_DECISION_ATTEMPT_S` / `LLM_GEN_ATTEMPT_S` / `LLM_STREAM_ATTEMPT_S`），deadline 仍无出口
- 关掉思考后（2026-09-10 实测，n=14）：**0/14 超 60s，max 31.3s**（>45s 也是 0/14）→ 当前工作负载已不再顶它，见 §二基线表
- 历史（修复前，思考未真正关闭）：单次 map 调用 12.0–160.0s，>45s 12/14、>60s 11/14（`e2e/scratch/out_maplen.prefix.json`）→ 当时超过 60s 的调用在生产上必然失败
- 生产是否仍有超时：**待验证**（受本机吞吐、网络、prod `.env` 覆盖值影响）。常量仍写死不可 env 覆盖，保留记账

**9. 端点注入 `get_current_user` 却不引用 `user`（越权一类）** —— 状态：**已修**（2026-09-10）
- 形态：签名取 `user: dict = Depends(get_current_user)`，函数体从不引用它 → 无归属校验，任何登录用户拿 id 就能读他人资源
- 全仓 AST 扫描（`web/routers/*.py`）命中 **9 处**：
  - **3 处真越权，已修**：`text.py:204`（上传任务：任意 task_id 读他人进度/错误）、`voice.py:189`（自定义音色文件：绕过私有的 `/list` 过滤与删除/上传的属主校验）、`voice.py:213`（`card_id` 参考音频：兄弟端点 `preview_ref_audio:272` 查卡主，唯独它不查）。统一 fail closed + 非属主 **404**（不用 403，避免靠状态码枚举资源是否存在）
  - **6 处良性**（只当登录门，读全局/公开数据）：`auth.py:get_announcement`、`market.py:get_card_versions` / `get_card_forks` / `list_post_comments`、`voice.py:voice_status` / `speech_to_text`
- 边界锁：`tests/test_auth_param_used.py` 固化这次 AST 扫描 + 白名单，将来新增同类端点自动变红，不必靠人再扫一遍
- 注：`voice.py` 内既有四处查属主返回 **403**（`get_ref_audio` / `preview_ref_audio` / `upload_ref_audio` / `delete_ref_audio`），与新修的 404 并存，口径待统一

**10. `market.py:697 list_post_comments` 不标 `liked_by_me`** —— 状态：未修
- 同类端点 `text.py:449 get_text_comments` 用 `user["id"]` 标了 `liked_by_me`，这个没标——它也因此进了上面第 9 条的扫描名单（读 user 与否在这里是「功能与否」，不是「越权与否」）
- 属**功能缺口，非安全问题**

### 四、验证纪律

- **基线数字现跑现取**（测试通过数、行号、函数签名）：禁止引用上一轮结果或凭记忆。行号随改动漂移，写进文档的都要标读取日期
- **并发/事务/锁的结论不给「应该如此」，只给「要验什么、怎么验」**。案例：MVCC 下 `WHERE EXISTS` 是快照读、不加锁，挡不住「父行正在被删、还没提交」——必须补 `FOR SHARE`。本仓已成文于 `storage/postgres_store.py:2984-2998`（含死锁无环分析），SQL 见 `:3003`
- **SQLite 全绿不构成并发命题的证据**：SQLite 写是库级序列化，窗口从根上不存在（`storage/sqlite_store.py:4081-4084` 自述），生产是 PG。同一段逻辑在 sqlite 上验不出 PG 的行锁语义
- **mock 的形态 ≠ 被测对象的形态**。案例：mock 回 canned JSON，据此误判 map 输出是 JSON——实际是自然语言（prompt 见 `core/distiller.py:257-304`；消费侧只判「非空且 ≠『无』」，`distiller.py:1507-1509`）。据此设计的 JSON 校验会否掉所有真实 map 结果
- **测试通过 ≠ 命题成立**，可能只是那条路径根本没被走到。案例：分片续跑逻辑落地后长期未真实执行——短文本恒走长上下文路径（分流在 `distiller.py:1379`），从不进分片。要验分片路径必须显式强制（见第五节）
- **修复必须做变异验证**：改坏它，指定测试必须变红。案例：把兜底调用从 `finally` 里整行删掉，**原 14 个测试仍全绿** → 接线没被覆盖；随后补 `TestA2Wiring` 两条（commit `9d2a9e4`，`.claude/sessions/2026-09-10-distill-dbtruth-closeout.md:29`）
- **「X 消失了」不足以证明 Y 修好了——要找独立、可交叉验证的指标**。案例：关掉思考后「空正文片数 3→0」，但空正文消失本身也可能只是采样波动，用它证明「思考关掉了」是同义反复。真正的实证是 **tokens / 正文字符 3.85 → 0.62**，与本仓自己的 `_estimate_tokens = int(len*0.6)`（`core/distiller.py:897`）吻合——两个互相独立的量对上，结论才立得住
- **别人给的 premise 与代码不符时，报更正、只修真缺口，不去实现那个不存在的修复**。案例：SSE「截断会硬断流，因为 `_next_piece` 只捕 `StopIteration`」——实读 `chat.py` 外层 `except` 早已 `yield {"error": ...}`、`client.js:228` 早已渲染，连接不会掉。真缺口是**可识别性**（帧里没有 `code`/`finish_reason`）与**文案漏内部标识**，修的是这两样。按错前提动手会改出一段无人需要、还掩盖真问题的代码
- **修复一处越权 ≠ 这一类修完**。案例：`text.py` 上报的越权与 `voice.py` 两处同形（都因「注入 `user` 却不引用」）；用 AST 扫一遍全仓同类形态，才把 9 处一次分清（3 真越权 / 6 良性），并把扫描固化成测试（见缺陷 9）。逐处手工排查会漏，且下次照旧
- **任何「新建/添加/引入」类动作，先确认它是否已存在**（仓库里多半已有同形实现或同名字段）

### 五、验证工具现状

- `tests/perf/mock_llm_server.py`：mock LLM，挂 `/chat/completions`（`:218`）+ `/embeddings`（`:220`），另有 `/admin/set` 控制面（`:201`）
- `e2e/scratch/`：一次性探针。gitignore 覆盖见 `.gitignore:279` 的 `/e2e/`；注意 `:256-267` 是 `web/frontend/e2e/` 的另一组规则，勿混。**调试脚本不入库**
- **强制走分片路径**：配置里把 `longctx_threshold` 调到 1（另可把 `chunk_size` 调小、`map_concurrency` 调 1 以确定性截杀），**不要改源码**。做法记录于 `.claude/sessions/2026-09-10-distill-dbtruth-closeout.md:262`
- **PG 验证用 throwaway 容器**：`scripts/restore_verify.sh:67` `docker run -d --rm`，`:77` `docker rm -f`。本仓惯例见会话记录（「PG throwaway（55433）N passed，随后 `docker rm -f`」，`.claude/sessions/2026-09-10-distill-dbtruth-closeout.md:51` 等）
- **`e2e/helpers.cjs` 路径更正**：根 `e2e/` 下无此文件，实际在 **`web/frontend/e2e/helpers.cjs`**，`BASE = 'http://localhost:7861'`（`:11`）。本机实测 7861 端口**现有 3 条 LISTENING**（PID 21116 占 `0.0.0.0:7861` 与 `[::]:7861`，PID 11408 占 `[::1]:7861`），与「四重监听」不符，以现测为准。**待改成 `127.0.0.1`**；「会连到 Docker 里的旧 build」一说**待验证**

来源（外部文档，非本仓代码）：DeepSeek 思考模式——参数为 `thinking: {"type": "disabled"}`，思考默认开启且 effort=high，思考模式下 `temperature` / `presence_penalty` 被忽略：<https://api-docs.deepseek.com/guides/thinking_mode/>
