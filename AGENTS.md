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

> **全表状态口径（2026-09-15 现跑现数）**：1–46 共 46 条 —— **已修 34**（含 32、33；40：commit 一 `53bed63` + commit 二；42：`7009d77` → `3e2670d` → `2c9fee9` → `fbb9066` → `efa36a6` → `c959553` → 结案四提交 → 收口一提交）/ **记账 7**（35、36、41、43、44、45、46）/ **记账待补 1**（30）/ **已裁定 1**（31）/ **证伪 1**（37）/ 其余 2 条（3 纵深防御、10 已移出「三之二」）。**待办 = 记账 + 记账待补 = 8**。
> 引用任何「还剩几条 / 某条什么状态」之前**现数一遍**：取所有 `^\*\*(\d+)\. ` 的标题行，抽出 `状态：\*\*(.+?)\*\*`。**分组按主词，不按字面值** —— 「已修（commit `x`）」「已修（2026-09-13）」属同一个「已修」桶，括号里的是附注不是类别；照字面值分组会得到 16 个组，**与头部声明的 6 个桶对不上**，那时先怀疑分组口径而不是台账。**格式不变式：每条标题行必须带 `状态：`、且状态值用 `**` 加粗、`N.` 后带空格** —— 否则该条会从这次统计里**静默消失**（字段缺失不报错，正是 §四 那条「缺口不会自己报错」）。**禁用「已修 1–33」这类区间表述** —— 30–33 全在记账桶里，一个区间就把整桶抹掉；**摘要与台账不一致比缺陷本身贵**：照摘要决定下一步，会直接漏掉四条。

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

**3. 第二道门是「非空」门，不是「结构合法」门** —— 状态：**纵深防御（主屏障已上移）**
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

**18. `IncompleteResponseError` 不可 pickle** —— 状态：**已修（2026-09-13）**
- 原形态：`__init__(self, finish_reason, where, content="")` 调 `super().__init__(<单条消息>)` → `args == (msg,)`。`BaseException.__reduce__` 在 `__dict__` 非空时返回 `(cls, self.args, self.__dict__)`，反序列化按 `args` 调构造器 → 缺 `where` → `TypeError`，**炸成另一个异常、掩盖真因**（跨进程 / 队列传异常时正是这个场景）
- **全仓普查（AST，硬要求）**：带自定义状态的异常类共 **7** 个，逐个真 pickle 往返验证 —— 需修 3 处：`IncompleteResponseError`（补 `__reduce__` + 存 `where`）、`StoreError`（`args` 是格式化 message、`__init__` 要 `(op, exc)`，且原始 `exc` 未必可序列化 → 只带 `op`+message 过河，模块级 `_rebuild_store_error` 绕开再格式化）、测试替身 `_RateLimitError429` / `_RateLimit429` / `_BadRequest400`（参数与 `args` 对不上）。其余（`DistillError` / `CollectionUnusableError`）额外参数可默认，`cls(*args)` 本就成立、无需 `__reduce__`
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
- **拉锯要不要消除的裁决：不消除，但把每轮代价从 O(数据量) 压到列删除**。018 **不能摘**（老库上没有这三列时，070 的 `INSERT ... SELECT u.api_key` 直接 `no such column` —— 而 070 靠 `_apply_migration` 的「home_region 已在 → 整份跳过」在新库上才不被重跑）；执行器又没有「已应用」账本，所以「018 照加、这里照删」每轮都会发生。**代价实测**（`e2e/scratch/time_users_rebuild.py`，全表拷贝口径）：4 行/180KB ≈ **20ms**、1000 行/200KB ≈ 2.7s、5000 行/200KB ≈ **18s** —— 每轮全表拷贝在生产体量上**不可忽略**，故 3.35+ 改走原生 `ALTER TABLE users DROP COLUMN`（省掉临时表 + `INSERT..SELECT` + 索引重建，且**不碰**其余列类型 —— 原重建的 `col_defs` 兜底会把未知列静默重定型为 `TEXT`），<3.35 回落原表重建并把这段 O(rows) 代价注明在该分支上。本机 3.49.1 走原生路径。
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

**30. 群聊路径的「不渲染证据」只由前端兜底覆盖，后端无专门用例** —— 状态：**记账待补**（Evidence commit 5 自查，2026-09-14）
- **形态**：证据的**写入**只有 `web/routers/chat.py` 两个调用点（`_do_chat` / `_do_chat_stream`），**读取**只有 `web/routers/history.py` 的 GET 与 resume。群聊走的是另一套原语（`storage.save_group_message` / `get_group_messages`，签名里根本没有 evidence 参数）与另一条历史出口 —— 即群聊的**写侧恒不产证据、读侧恒不带证据**，两条都成立。
- **薄在哪**：这两条「成立」**没有任何后端用例守着**。把 `save_group_message` 将来改成能带证据、或群聊历史出口将来接上 `parse_evidence`，都不会有锁变红；「群聊不渲染」这个结论目前只由前端 `EvidenceRail` 的「evidence 缺失 → 返回 null」那一行覆盖。
- **为什么不现在补**：起群聊要一整套新测试基建（群聊会话夹具 + 群聊路由器依赖），成本远超收益。
- **待补（将来有群聊夹具时）**：后端各一条 —— ① 群聊写侧调用点不带 evidence（形态锁，同 `test_default_call_sites_are_unchanged`）；② 群聊历史出口读回的条目里没有 evidence 键。判据命令：`git grep -n 'save_group_message\|get_group_messages'`。
- **跨层覆盖不算单点全包**：前端那行只保证「拿到不带 evidence 的条目时不崩」，不保证「后端不会开始产证据」—— 后者才是这条缺口的实质。

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

**35. `/start` 后台线程路径的 usage 记账全程空转 —— 缺陷 22 收口后的覆盖缺口** —— 状态：**记账（不修，待裁范围）**（2026-09-14）
- **事实**：本次运行 5 个 action 各打一条 `[Distiller] usage not recorded: storage/user_id missing (user=, action=…)` —— `distill_identify` / `distill_map` / `distill_reduce` / `distill_format` / `distill_autotag`。`user=` 为空即证据（`core/utils.py:64` 的 print 把 `user_id` 直接填进去）。
- **根因**：`/start`（`web/routers/distill.py:734`）→ `_distill_start_impl`（763）→ `_run_distill_task`（317）**后台线程**。该线程**收到了 `user_id` 参数，但只用它放并发槽**（`_release_user_slot`），**从不把 `distiller._storage` / `distiller._user_id` 接上去**。`Distiller.__init__` 的默认是 `self._user_id = ""`（`core/distiller.py:239`）。
- **对照（同一仓库里的正确写法）**：`/run_stream`（`web/routers/distill.py:1044`）在 1075–1076 显式写了 `distiller._storage = storage` / `distiller._user_id = user_id`。全仓 `distiller._user_id = …` / `distiller._storage = …` **只此一处**（`git grep -n 'distiller\._user_id\|distiller\._storage'`）→ **三道蒸馏入口里只有 `/run_stream` 一条接了**：`/start`（后台线程，`_run_distill_task`）不接；`/run`（`web/routers/distill.py:686` → `TextManager.get_or_distill`，`core/text_manager.py:407`）也不接 —— 它的 distiller 由 `web/deps.py:254` 现造（`Distiller(llm)`），同样落 `_user_id=""` / `_storage=None` 默认值。`Distiller.__init__` 的这两个默认值见 `core/distiller.py:238-239`，全仓 5 处 `Distiller(...)` 构造点（`web/app.py:48` / `web/deps.py:185,191,254,297`）无一传身份。
- **机制**：`core/utils.py:63` `if not storage or not user_id:` → print + `return`。整条链的记账出口是接上了的（缺陷 22 刚把 36 个 LLM 调用点全部接进该出口），**但这条路上入口的两个实参都是空的**，于是出口空转。
- **与缺陷 22 的关系（关键）**：22 修的是「**出口从未写**」；本条是「**出口写了、这条路从未注入**」。二者**不重叠**，但**机制锁看不见本条** —— `tests/test_usage_accounting_lock.py` 的判据是**静态调用链**（「存在调用 LLM 但不流向记账出口的调用点即红」），而本条的调用**在代码路径上确实流向出口**，只是运行期早退。**判据是调用图，不是实参** —— 这是该锁的又一类盲区（与缺陷 25「签名的代理代替 SQL 事实」同谱系：静态形态对，运行期事实错）。
- **后果**：除 `/run_stream` 外的蒸馏入口（`/start`、`/run`）上，distiller 内部那几笔 token 用量**一条都不入库**；而 `/start` 正是前端主要的蒸馏入口 —— 「蒸馏成本统计」在这些路上系统性偏低（偏低的统计比没有更危险，同缺陷 22 的措辞）。本次观测到的只是 `/start` 这一条（5 条 no-op 打点即其证据）；`/run` 的同一形态由静态读代码得出，**未实跑验证**。
- **处置方向（记在条目里，不实现）**：① 让 `_run_distill_task` 注入 `storage` / `user_id`（照 `/run_stream` 的写法）；② 更根本的是**补一条运行期判据** —— 蒸馏路径上出现「记账出口空转」不应只是 `print` 一行淹没在 429 风暴里。范围待裁：注入点、以及是否把静态锁扩到「实参非空」这一层（若是，需要先想清怎么在不跑流水线的前提下判定）。

**36. `/api/distill/start` 不读 `characters_json` 缓存（只有 `/identify` 读）** —— 状态：**记账（不修，待裁范围）**（2026-09-14）
- **事实**：`/identify`（`web/routers/distill.py:658`）在属主校验后读 `get_characters_owned`（678），命中即返回、**不发 LLM**；`/start`（734）这条全流水线**不读该缓存** —— 角色识别由流水线内的 `distiller.identify_characters`（`core/distiller.py:635`）直接跑。
- **它自己的那层缓存不是 `characters_json`**：`identify_characters` 用的是**进程内 TTL memo**（`core/distiller.py:28-32`，`IDENTIFY_CACHE_TTL_SECONDS = 600`），键 = `sha256(前 10000 字) + model`。寿命是**进程**，不是库。
- **实测**：本次 `/start` 之前该文本的 `characters_json` 已存 8 个角色（早先 `/identify` 的产物），流水线仍在 12:30:07 真发了 identify 调用（`action=distill_identify`）；日志里 `[distill] identify cache hit` **零命中** —— 因为本次 rebuild 刚重启过容器，进程内 memo 是冷的。
- **意义（解释了上一轮的一个推理为什么只在一条路上成立）**：上一轮判定「再点一次不可能复现，因为 `characters_json` 已缓存 8 个角色、命中即返回」—— 该推理**只在 `/identify` 上成立**。`/start` 不等价。
- **处置方向（记在条目里，不实现）**：**不一定要「对齐」** —— 两条路由的语义本就不同（`/identify` = 只看角色，`/start` = 全量重跑），`/start` 复用库缓存会让「重跑」不再重跑。要裁的是**产品语义**：「`/start` 该不该复用已有识别结果」，而不是「补一行 cache 读取」。
- **判据命令**：`git grep -n 'get_characters_owned\|identify_characters' web/routers/distill.py core/distiller.py`

**37. （前提证伪）`get_distiller` 的单例被 `/run_stream` 原地改写 —— 该形态不可达，usage 不会记到别人名下** —— 状态：**证伪，不成立**（2026-09-14 当天记下、当天核掉）
- **原假设**：`get_distiller(llm=None)` 返回模块级单例 `_distiller`（`web/deps.py:182-192`），而 `/run_stream` 原地改写其 `_storage` / `_user_id`（`web/routers/distill.py:1075-1076`）→ 共享可变状态跨请求污染 → 之后某个没有 per-user key 的请求取回被污染的单例，usage 记到前一个用户名下。当时标注为「静态读出、未实跑验证」。
- **证伪：两个前提互斥，不可能同时成立。**
  - `/run_stream` 传的是 `get_distiller(llm=per_user_llm)`（`web/routers/distill.py:1057`），而 `per_user_llm = await get_user_llm(...)`，`get_user_llm` 在用户没配 key 时**不是返回 None，而是回落到 `get_llm()`**（`web/deps.py:118-119`：`# Fallback: global config / admin key` → `return get_llm()`）。故 **`per_user_llm is None` ⟺ `get_llm() is None`**。
  - `get_distiller(None)` 只在 `_distiller is None` 时才去问 `get_llm()`，而 `get_llm()` 为 None 就直接 `return None`（`web/deps.py:187-190`）→ **单例从未被创建**。反向：单例一旦被创建（`deps.py:191`）就要求 `get_llm()` 非 None；且 `_llm` 是非 None 缓存，**全仓没有路径把它设回 None**（`get_llm` 只在成功时赋值；`reset_llm_and_dependents` 的 `_llm = LLMAdapter()` 抛异常时不赋值）。
  - 合起来：**单例存在 ⟹ `get_llm()` 非 None ⟹ `per_user_llm` 非 None ⟹ `get_distiller(llm=…)` 现造每请求新实例**（`web/deps.py:184-185`）→ 1075-1076 改写的是**请求级副本**。反向：`per_user_llm` 为 None ⟹ 单例不存在 ⟹ `get_distiller(None)` 返 None ⟹ **503 在 `distill.py:802` 先抛**，根本走不到 1075。
  - 另核写点：全仓 `distiller._user_id = …` / `distiller._storage = …` **只有 1075-1076 一处**（判据命令见下），不存在第二个把身份写进单例的地方。
- **连带证伪**：「503 门只判 `distiller is None`、拦不住被污染的单例」也不成立 —— 那道门拦的正是「拿不到 LLM」，而单例被污染的前提恰恰是「拿得到 LLM」，两者不共存。
- **为什么保留本条而不删**：它的**假设形态**（长生命周期对象持有请求级状态 → 跨请求错归）在本仓仍值得警惕，只是当前**没有**这条路径。**复活判据**：① 上条 grep 的写点数从 1 变 2；② `distill.py:1057` 的传参不再是 `llm=per_user_llm`（例如有人改成 `get_distiller()` 取单例）；③ 出现新的、能对单例写请求级字段的调用点。任一条成立，本条立即复活。
- **教训（落笔时没走完静态链）**：「静态读出」这个标注救不了**没追到源头**的静态链 —— 我只读到 `get_distiller` 自己的两个分支就落了笔，没跟到 `get_user_llm` 的 fallback，于是把**一对互斥条件**当成了可同时成立的组合。核一条静态结论，必须把**每个分支的入参从哪来**追到源头（此处就是那句 `return get_llm()`）；只读被判对象自己，等于只读了半条链。与 §四「别人给的 premise 与代码不符时，报更正」同族 —— 那次是别人的 premise 错，这次是**我自己顺着一个看起来自洽的 premise 往下推**。
- **判据命令**：`git grep -n 'distiller\._user_id\|distiller\._storage'`（写点数应为 1）、`git grep -n 'global _distiller\|_distiller = Distiller\|return get_llm()' web/deps.py`

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
- **全量普查（判据驱动，不是照名单改）**：两步 —— ① AST 全仓找「`except ... as E` 的绑定名被引用、且**不在 print / 日志调用内**」的点，**267 处**（排除 vendored `services/gptsovits`、`tests/`、`e2e/scratch`）；② 逐点判「这串最终会不会到**终端用户**眼前」，到得了的机制只有四种：HTTP 响应体 / SSE 错误帧 / 后台任务状态行 / admin 可读记录。结果：
  - **真泄漏、活代码 4 处**：`text_manager.py` ×3（本批收）、**`web/routers/voice.py` 的 ASR 转码 ×1**（本批收）。
    - `voice.py` 那处**不在任何名单上** —— 它**不是异常对象，是子进程 stderr**，「异常对象被格式化」类的扫法扫不到。同文件同形态的另两处（`:111`/`:382`）已由缺陷 38 收口，**`:460` 是判据找出来的第三个**。漏它就是「改了两处、第三处记账」。
  - **同判据、但决定面不同 → 单独立项，不塞进本批（2 处）**：`web/routers/group.py` 的 SSE 错误帧、`web/routers/text.py` 的后台任务状态行，都直接 `str(exc)`（可能是 `StoreError` → `storage operation '<op>' failed: <驱动原文>`）。**为什么不顺手收**：它们的「正确形态」`web/routers/chat.py::_stream_error_payload` 本身用 `user_facing_error(exc, preserve_unknown=True)` —— **故意把未登记异常的 `str()` 原样透出**（契约锁 `tests/test_chat_stream_error.py::test_other_errors_keep_original_shape`）。改 group/text 就等于动 `chat.py` 那条契约（三处一起的决定），属于另一个面。
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

**41. postgres 的 healthcheck 是一个结构上无法失败的探针 —— `depends_on: service_healthy` 这道门一个半月来没验过凭据，且不可能因凭据问题红** —— 状态：**记账（不修，待裁）**（2026-09-15，修 32 时在同一文件同几行下发现）
- **形态**：`docker-compose.local.yml` 与 `docker-compose.prod.yml` 的 postgres 服务：
  `test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]`。变量为空时 shell 展开成 `pg_isready -U  -d `，`-d` 被当成用户名 —— 日志里于是每 10 秒一行 `FATAL: role "-d" does not exist`，**自 2026-08-17 起持续**（`docker logs character-distill-postgres-1` 首行之后即是）。
- **门看着在，实际什么都不守**：容器全程 `(healthy)`，而 `app` 的 `depends_on: postgres: condition: service_healthy` 正是靠这个结论放行。也就是说，这道被当作「库就绪」的保证，**一个半月里连一次凭据都没验过**，且没有任何一条路径会让它因凭据问题变红。
- **为什么 `:?` 修不好它（所以不是 32 的尾巴）**：即便变量补齐、`-U` / `-d` 拿到正确名字，**`pg_isready` 依然不会因凭据错而失败**。官方语义写得很清楚：*"It is not necessary to supply correct user name, password, or database name values to obtain the server status"* —— 它回答的是「服务器在不在应答」，不是「我连得上」。实测：`pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`（两变量为空）输出 `accepting connections` 且 **exit=0**。
- **定性：不是配错了，是选错了检查手段。** 用一个**结构上无法失败**的探针当门 —— 与缺陷 32/34 同族（失败被吞成正常），但**不同处**：32 是「缺变量静默降级」，34 是「输出非空即放行」，这一条是「**探针的命题与门的命题不是同一个**」：门的命题是「库可用」，探针的命题是「库在应答」。
- **可辨性缺口**：唯一的信号是 PG 自己日志里的 FATAL 行，而它**不在**任何被监控的地方；容器状态、`docker compose ps`、`config` 输出一律正常。与 §四「两种不同成因的失败若共用一个信号」同源 —— 这里更极端：**根本没有信号**。
- **处置方向（记在条目里，不实现）**：把探针换成能因凭据失败而失败的形态，例如 `CMD-SHELL: psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c 'select 1'`（`postgres` 镜像自带 `psql`；认证失败 → 非零退出 → 容器 `unhealthy` → `app` 不放行）。**代价必须一起裁**：这会让「库连不上」从「app 静默起来、请求时才报」变成「app 直接起不来」—— 那是**故意**的（正是本次要的），但它同时意味着**一次认证配置错误会拦下整条启动链**，是否接受要用户定；两个文件同形，须同批。
- **与本批（32+33）分开的理由（用户裁定）**：本批命题是「缺变量不静默」+「凭据不走命令行」；这条修的是「健康检查不检查凭据」—— **同病不同处**，混进同一 commit 会让「哪行因哪个动因改动」说不清。
- **判据命令**：`grep -n 'pg_isready' docker-compose.local.yml docker-compose.prod.yml`；复现 `docker logs --tail 20 character-distill-postgres-1`（应持续出现 `FATAL: role "-d" does not exist`）；探针语义自证 `docker exec character-distill-postgres-1 sh -c 'pg_isready -U  -d ; echo exit=$?'`（**输出 exit=0** 即本条成立）

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
- **共享层的第二块：`tests/route_policy.py`（结案这一步抽出）** —— L5 的 `_FORM_METADATA` 与 auth 锁的 `ALLOWLIST` 是**同一种东西**（「人声明的豁免 + 理由」），而两把锁**各写了一遍**「陈旧条目 / 空理由 / 现场未登记」这三套差集与判空。两份实现会各自漂移，且漂移**不报错** —— 与 3a/3b 收掉的是同一个病（同一判定两份实现），只是换了个住址。本层只放与业务无关的机械操作（`empty_reasons` / `stale_keys` / `unexpected`，表统一是扁平 `dict[Hashable, str]`：键 = 被豁免的东西，值 = 理由），**模块内不出现任何路径、字段名、依赖名** —— 与 `route_facts` 同一条原则（内容归各把锁，机械操作归这一层）。变异组 P 打本层自己的判据行：不 strip / `stale_keys` 参数方向写反 / `unexpected` 恒返回空集。
- **隔离判据 ① ② ③（已核，不是口头保证）**：① `git diff --stat` 无 `core/` `web/` `storage/` `adapters/` —— **生产代码零改动**；② `tests/route_facts.py` **一字不动** —— 本步是「让两把锁去用它」不是「改它来适配两把锁」；③ 两把锁**互不依赖** —— 移走任一把，另一把照常红**同一条**断言（红源带 marker 比对，不是只看「有没有红」），且两把锁交错调用后本层输出**指纹不变**。**关于共用缓存**：本层唯一的模块级可变状态是那格 spec 缓存 `_cache`，内容只由 app 决定、**无 setter**、显式传 `routes=` 时不写缓存、`enumerate_routes` 每次现算 ⇒ **不存在「一方污染、另一方读到」的通道**；这条由 `test_shared_layer_output_is_a_pure_function_of_the_app` 钉住（钉的是**可观测性质**，不是模块全局的形状 —— 数「有几个 dict」是代理指标）。**第 3b 步的连带对账（两处锚必须跟着改，否则变异脚本自己失效）**：① V7 的第 1 条锚原打在 L5 自备的 `_form_operations()` 函数体上，该函数随 3b 删除后锚会 `count == 0` —— `_apply` 当场 assert，形态是「变异没生效」的假绿；改锚到**调用点**（把 `route_facts.form_operations()` 换成写死的 `{("/api/text/upload", "post")}`，即迁移前那条硬编码 op 的等价形态）。**结案这一步这条锚又搬了一次**：L5 改成按 schema 判之后，旧形态＝「覆盖面写死一条 op」+「按字段名排除」，两半合起来退回才等价，故锚改到 `_observed_non_file_fields()` 的函数体上（见 `_L5_OLD_SHAPE` 上方注释）。② I-3 原先调 `L5._form_operations()`，改为调 **L5 自己的扫描入口**，**不能**直连 `route_facts.form_operations()` —— 直连绕开了 L5，这一步就不再是「L5 跑过之后本层输出有没有变」的检验，而是变成了「事实层自己跑一遍有没有变」。**结案这一步 L5 的入口从一条拆成三条**（主判据 / 策略表 / 负控），I-3 相应地调其中两条走完整条扫描路径。
- **变异矩阵入库**：`tests/perf/route_facts_mutations.py` —— 六组共 39 条（F/R/X 打事实层自己、V 打两把锁的判据、I 打隔离性、P 打策略表校验层），**不带参数即跑全部组**（`--group` 默认 `FRVXIP`：文档称本脚本为「全矩阵」，默认值必须与这个说法一致，否则「不带参数跑一遍」少跑一组却看不出来）；**X 组的别名路径跨平台** —— 用 `web/../web/server.py`（两个平台都「字符串不同、`samefile` 为真」）而不是翻盘符大小写（后者只在 Windows 成立：Linux 上首字符是 `/`，翻完不变，X-3 退化成 X-4、X-5 失去前提，而结论行照样打印「全部符合预期」），跑 X 组前由 `_alias_gate()` 断言该前提，不成立即拒跑；逐条还原并核对 sha256；**先验基线不绿就拒跑**（两种成因共用一个红时说不清红源）、**锚点非恰一命中即 assert**（锚点漂移会静默变成「变异没生效」的假绿）、`--with-container` 才跑 F-3。**为什么必须入库**：本条的每一条数字原先骑在仓外 `D:/Temp/*.py` 上 —— 追不到，三个月后复现不了（§四「文档引用的数字，其产数脚本与原始产物也要入库」）。**一处如实交代**：迁移前那两条「旧锁在同一变异下绿」的形态**没有留在本脚本里**（旧版锁只存在于迁移那一刻的工作树，抄回仓里当靶子是添加 over 删除）；配方留在一旁（V1 的 `Annotated` 注入变异），配上 `git show <迁移前 sha>:tests/test_auth_param_used.py` 即可复原，**V4/V7 是留在树里的等价形态**（把判据退回旧的 AST 形状，同一变异仍绿）。
- **判据命令**：`python tests/perf/route_facts_mutations.py`（全矩阵，末行给结论；`--group V` / `--group I` / `--group P` 单跑）、`python -m pytest tests/test_route_facts.py tests/test_route_policy.py tests/test_auth_param_used.py tests/test_text_failure_messages.py -q`、`git diff --stat`（应只有 `tests/` 下的锁与脚本 + `AGENTS.md`，无 `web/` `core/` `storage/` `adapters/` —— 本缺陷是锁的缺陷，不是业务缺陷）
- **收口一处：策略层理由判空改为按类型判定（Commit A）** —— `empty_reasons` 原先走「先字符串化再 strip」，于是理由写成 `None` 时被变成 `"None"`、判为**有内容**：把 `/api/voice/ref-audio/upload` 的 `ref_text` 理由改成 `None`，L5 **全绿**。这是本条病灶的第五次显形（判的是「转成字符串之后长不长」，不是「理由在不在」），只是长在了策略层自己身上。现在先判类型再判内容，非 `str` 一律算空；变异 P-4（把字符串化请回来 → `None` 用例红）与 P-5（只判类型、不 strip → 纯空白理由用例红）各打一个新分支。**原 P-1 退役**：它的变异是「`str()` 之后丢掉 strip」，而那段 `str()` 已删，新写法下与 P-5 逐字同形 —— 编号留空位不复用，免得台账里的旧编号改指别的事。

**43. `web/test_spa_fallback.py` 在 `tests/` 之外，pytest 从不收集它** —— 状态：**记账（不修，待裁）**（2026-09-15 缺陷 42 结案时顺带普查发现）
- 实测：该文件有 5 条 `def test_*`；`pytest.ini` 的 `testpaths = tests` ⇒ `pytest --collect-only` 里它 **0 命中**，全仓无任何 workflow / 脚本引用过它（`grep` 全仓 yml/ini/cfg/toml/sh 零命中）。
- 形态：该文件自带 `__main__` 运行器（`python web/test_spa_fallback.py` 才是它的既定用法），所以它不是「写坏的测试」，是**放错位置的测试** —— 覆盖的命题（静态资源优先于 catch-all / 中文文件名 / SPA 回退 / `/api/*` 不被回退吃掉 / 路径穿越被挡）全都有价值，只是**没有任何自动化在跑它**。属 §四 那条「一条恒 skip 的锁和没有锁是一回事」的邻居：**不被收集的测试与没有测试是一回事**。
- 另一处形态（记下以便裁定）：`_setup()` 在**模块级**执行（import 时即往 `_STATIC_DIR` 写测试文件）—— 一旦将来有人 import 它，就会在收集期产生副作用。今天不可达，因为没人 import。
- 修法（待裁）：把文件移进 `tests/`（需处理 `web/` 的 `sys.path` 与 `_STATIC_DIR` 写入的位置）或加进 `testpaths`；两者都要顺带处理模块级 `_setup()`。**本轮不动**（缺陷 42 的命题是锁的判据，不是测试布局）。

**44. 本地 app 镜像缺 `onnxruntime`，容器内 L3 坏 PDF 用例必红** —— 状态：**记账（不修）**（2026-09-15 缺陷 42 结案跑容器腿时发现）
- 事实：镜像内 `python -c "import pymupdf4llm"` → `ModuleNotFoundError: No module named 'onnxruntime'`（`pymupdf4llm/ocr/analyze_page.py` 顶层 import）。链路：`core/text_manager.py` 的 `_extract_pdf` 里 `import pymupdf4llm` → 该异常无人接住 → **坏 PDF 上传返回 500 而非 400**，`tests/test_text_failure_messages.py::test_l3_bad_pdf_screens_table_wording_and_logs_the_original` 在容器内必红；变异驱动的先验基线门因此拒跑。
- 证据命令：`docker run --rm -v <repo>:/app -w /app --entrypoint python character-distill-app:latest -c "import pymupdf4llm"`；或容器内 `pytest tests/test_text_failure_messages.py -q` → `assert 500 == 400`，栈底 `ModuleNotFoundError: No module named 'onnxruntime'`。
- **不是「锁里漏了这个依赖」**：`requirements.txt` 里**有** `onnxruntime==1.30.0`，它正是 `53bed63`（缺陷 40 的「依赖按锁安装」）那一步加进去的；而本机镜像 `character-distill-app:latest` 构建于该 commit **之前**（21 小时前）。故这是**镜像比锁旧**，修法是重建镜像，不是改锁。
- **非缺陷 42 引入（已核）**：把树退到 `c959553`（本轮全部改动之前）复跑同一文件，**同一条同样红**（1 failed, 21 passed）。
- 附：镜像里**也没有 pytest**（`python -m pytest` → `No module named pytest`），跑容器腿得临时 `pip install -q pytest pytest-asyncio onnxruntime`。两者同源：镜像只装运行时，而「判据要求容器内复跑」这个用法还要开发依赖。
- **Windows 形态（2026-09-15 收口时补，同一根因的另一端）**：同一条导入链在**本机**也能踩到 —— 锁里 `pymupdf4llm==1.28.2` → `pymupdf-layout` 1.28.2 → 顶层 `import onnxruntime`。本机 `C:\Windows\System32\onnxruntime.dll` 是**微软随 Windows 装的 ONNX Runtime 1.17.260613**（`FileVersion 1.17.260613-0100.1.os-germanium…`），`System32` 在 DLL 搜索路径里压过 pip 包内的 1.30.0，ABI 不符 → **模块初始化时 access violation**，`pytest tests -q` 整个进程被杀（`exit=139`，无红无绿）。
  - 证据命令：`.venv/Scripts/python.exe -m pytest tests/test_text_failure_messages.py::test_l3_bad_pdf_screens_table_wording_and_logs_the_original -q` → `Windows fatal exception: access violation`，栈底 `onnxruntime/capi/_pybind_state.py:32`。
  - 与容器那一端的差别只在症状：容器内是**缺包**（`ModuleNotFoundError`，一条用例红），本机是**包在、被 System32 的同名 DLL 抢先**（进程级崩）。两者都源自 `_extract_pdf` 里那句顶层 `import pymupdf4llm` 无人接住。
- **订正一条旧结论**：此前「本机 Windows 装了 onnxruntime 故不复现」**是错的**。那台环境（harness venv）里 `pymupdf4llm` 是 1.27.2.3，**根本不 import onnxruntime**，所以旧基线 `pytest tests -q` 全绿里那条 L3 是**环境恰好绕开了**，不是「代码没问题」。换到锁里的 1.28.2 立刻崩 —— 与缺陷 42 同形：拿一个与锁不一致的环境跑出的绿当证据，而没有任何东西说它不一致。
- **本机全量测试的当前做法**：`--deselect tests/test_text_failure_messages.py::test_l3_bad_pdf_screens_table_wording_and_logs_the_original`，结果是 `1045 passed, 62 skipped, 1 deselected`。**这不是绿**，是「这条在本机跑不了」—— 上机复跑只能进容器。

**45. 变异驱动的先验基线门红源不可辨：「基线跑不起来」与「基线绿、变异没红」同型** —— 状态：**记账（不修）**（2026-09-15 容器腿实测）
- 事实：镜像内没有 pytest 时，`_baseline_gate` 对四个锁文件的 pytest 调用全部**拿不到 summary**，却仍**放行**进入变异阶段；逐条变异拿到的同样是空 summary，最终表现为一片 `>> ?` 加一串 `MISMATCH ...：期望 RED 实得 green`。
- 形态：红是响了（**不是**静默假绿），但「基线**跑不起来**」与「基线绿、变异**没红**」两种成因共用同一种红 —— 正是 §四 那条「两种不同成因的失败若共用一个信号，就分不出是哪一种」。基线门存在的唯一理由就是让这两件事分开，它在「跑不起来」这一支上没有生效。
- 证据命令：镜像内（未补 pytest）`python tests/perf/route_facts_mutations.py --group XF --with-container` → 逐条 `实得=green`、`>> ?`；补装 pytest 后同一命令「全部符合预期」。
- 修法（待裁）：`_baseline_gate` 要求每个基线目标都解析出 `N passed`，解析不到即判「基线不可用」并**以另一种退出原因**拒跑，与「基线有 failed」分开。

**46. `tests/test_auth_tokens.py::TestLoginRefreshChain` 两条依赖本机 `.env` 的 `JWT_SECRET`，无 `.env` 的环境必红** —— 状态：**记账（不修）**（2026-09-15 缺陷 42 收口时普查环境依赖发现）
- 事实：`web/routers/auth.py::get_jwt_secret` 在 `JWT_SECRET` 缺失或等于默认值时长直接 `raise RuntimeError`，而 `JWT_SECRET` 只可能来自工作树里那份 **gitignored `.env`** —— 该类的登录 + 刷新两条用例因此**只在这台机器上绿**。
- 证据命令（现跑现数）：`git archive 53bed63 | tar -x` 得到一棵**不含 `.env`** 的树（`.env` 未被跟踪，仓里只有 `.env.example`），容器内跑该类 → **2 failed**，栈底 `web/routers/auth.py:41 RuntimeError`。
- **非缺陷 42 引入（已核）**：`53bed63` 早于本轮全部改动，同样红。
- 形态：**ambient 依赖** —— 用例的通过条件不在仓库里，换一台机器 / 换一个干净检出，结果就变。同一个病还有一面：本机 `pytest tests -q` 全绿**不能**当作「这组用例没问题」的证据，它只证明**这台机器恰好有 `.env`**（与 §四「环境没配好与代码有问题，不能共用一个绿」同源）。
- 修法（待裁）：`conftest.py` 里为该类注入一个测试用 `JWT_SECRET`（`monkeypatch.setenv`），或把 secret 取值收敛成可注入的依赖。

### 三之二、特性缺失 / 立项（非缺陷）

> 与「缺陷」分开记账：**缺陷 = 有东西坏了**（有正确行为可对照）；**立项 = 有东西从来没建**（没有可对照的现状，做它就是加功能）。混在一起会让缺陷清单虚高、也让「还有几个真缺陷待修」失真。三、里的编号 10 只留占位，指向本节。

**A. post / card 评论点赞特性整体缺失**（原缺陷 10，2026-09-12 重记并移出）
- **原记账被证伪**：原条目称「同类端点 `text.py` 的 `get_text_comments` 标了 `liked_by_me`、`list_post_comments` 没标，按 `text.py` 的写法对齐即可」。实读后前提不成立 —— 照做只会写死一个恒 `False` 的**假默认值**（本仓明令禁止，见 §四）
- 证据（2026-09-12 现跑现查）：
  - **无表**：全仓没有 `post_comment_likes` / 卡评论点赞表；`_likes` 家族只有 `text_comment_likes`（`storage/migrations/031_text_comments.sql` 及 PG 等价物）与 `post_likes`（点赞**帖子**本身，`web/routers/market.py` 的 `like_post` → `toggle_post_like`）
  - **无路由**：`toggle_post_comment_like` 全仓零命中 —— post 评论根本没有点赞入口
  - **无原语**：`storage/sqlite_store.py` 的 `get_liked_comment_ids` **硬编码** `text_comment_likes`（PG 侧同），拿 post 评论 id 去查恒返空集
  - **无消费**：前端 `web/frontend/src/components/common/PostCard.jsx` 渲染 post 评论（头像 / 用户名 / IP 属地 / 时间 / 正文）**没有点赞按钮**，从不读该字段；`post.liked_by_me` 是**帖子**的赞，不是评论的
- 即：真缺口是**「post / card 评论点赞」这个特性从来不存在**，不是「某端点漏标一个字段」。对齐写法 ≠ 修 bug，是**加功能**（建表 + 双方言 migration + toggle 路由 + `get_liked_post_comment_ids` 原语 + 前端按钮），且要新增一张表
- 处置裁定（用户，2026-09-12）：**不做**。`list_post_comments` 保持现状 —— 不返回该字段，比返回一个恒 `False` 更有信息量
- 注：它仍留在第 9 条的扫描名单里（读 user 与否在该端点曾表现为「功能与否」而非「越权与否」），该扫描不受本条影响
- 立项与否 = **产品决策**，不是待办欠账；将来要做，按「新功能」走完整流程，不挂在缺陷表下

**B. `character_arc`（角色弧线）能填不能看 → 已补（2026-09-14，两处同补）**
- **事实**：字段是 `list[str]`（`core/schema.py`，每阶段一句话），`EditCardModal` 有编辑入口、存进 `card_json`、前端也拿得到；但**两处卡片详情都不渲染它**。同节其余字段（`personality_traits` / `values` / `key_memories` / `inner_tensions` / `relationships`）两处都渲染了
- **性质**：功能缺失，不是缺陷 —— 没有「该显示却显示错」的可对照现状，是**从来没有这块展示**，故不挂在缺陷表下
- **两处就是全集**：卡片详情渲染器全仓只有两个 —— `MarketCardDetail`（集市卡）与 `CharCard`（自建卡，`App.jsx` 的 `character` 视图）。普查当时 `character_arc` 在 `web/frontend/src` 下**只命中 `EditCardModal`**，展示侧零命中。只补一处会得到「看别人的卡有弧线、看自己的卡没有」—— 同一缺口的两处显形必须同补
- **处置（用户，2026-09-14）**：**已补，两处同补**
- **形态**：`<ol>` + 序号徽章 + 纵向序列（体现阶段先后），区别于 `values` 的并列 chip；外壳与折叠各自复用**本文件既有机制**（`MarketCardDetail` 走 `collapsedSections` / `toggleSection`，`CharCard` 本文件无折叠机制故不加）；空值 `?.length > 0` 整节降级。渲染层不抽共享组件（两处外壳本就不同：`CardSection` vs `card-section--wide`+`<h3>`），但 **CSS 共用同一组类名** —— `global.css` 的 `card-arc-list` / `card-arc-item` / `card-arc-index`，全仓只此一处
- **覆盖证据**：`web/frontend/src/components/__tests__/` 下 `MarketCardDetailCharacterArc.test.jsx` 与 `CharCardCharacterArc.test.jsx`（各两条：有 / 无），外加一条「CSS 只落一处」断言（三条类名定义在 `global.css`、不出现在 `adm-theme.css`）。变异矩阵见会话记录（守卫改恒假 → 两文件各自的「有」用例红；「无」载荷改带弧线 → 「无」用例红）

**C. 重构前置检查项：`/start` 的 scene index 由打开卡片时的 `/start_session` 补偿 —— 踩断它不会报错**（2026-09-14 记账，**不修**）
- **性质**：**不是缺陷**（当前行为正确），也**不是待建功能**。它是一条**靠巧合成立的隐性契约** —— 统一构造出口那轮重构（三道路由 / deps 两个工厂 / TextManager 两份构造）一动就会断，且**断了不报错**：只表现为「场景预索引不再发生」，聊天的 RAG 召回悄悄变差。故单列，作为**重构前置检查项**。
- **事实链**：① `/start` 落卡走的是它自己就地拼的 `TextManager`（`web/routers/distill.py` 的 `_save_card`），**没传 `indexing_service`** → `core/text_manager.py:580` 的 `if self._indexing_service:` 为假 → **不调度** `schedule_scene_index`（对照：`/run`、`/run_stream` 都调度，见本文件 §三 缺陷 35 附近的差异全集）。② 补偿发生在**打开卡片**时：`/start_session`（`web/routers/distill.py:1410`）会调度。③ 前端只在 `card.session_id` 为假时才调 `/start_session`（`web/frontend/src/store/useAppStore.js` 的 `selectCard` / `startChat`）。④ 而 `storage/sqlite_store.py` 的 `list_cards` **不投影 `session_id`** → 前端拿到的卡**永远是假值** → 必然调 `/start_session` → 补偿成立。
- **踩断方式（重构时最容易顺手做的那件事）**：把 `session_id` 加进 `list_cards` 的投影字段。届时前端看到真值 → 不再调 `/start_session` → `/start` 那一侧又不调度 → **scene index 静默停摆**，没有报错、没有日志、用例也不红（该投影无锁）。
- **重构时的检查动作**：动 `list_cards` 投影、动 `/start_session` 的调度、或动 `_save_card` 的 `TextManager` 构造（补齐 `indexing_service`）时，三处任一处改完都要回答「这一改之后，`/start` 落下的卡由谁保证 scene index 被调度」。最干净的收口是**在 `_save_card` 补齐 `indexing_service`**（让三条路一致），那时本项作废、可连同正文一起删。
- **判据命令**：`git grep -n 'session_id' storage/sqlite_store.py`（`list_cards` 的投影里应**没有**它）、`git grep -n 'schedule_scene_index' web/routers/distill.py core/text_manager.py`

### 四、验证纪律

- **基线数字现跑现取**（测试通过数、函数签名）：禁止引用上一轮结果或凭记忆。引用代码一律用符号名（函数/常量/测试名），不写行号——行号随改动漂移且无测试报警
- **台账状态行不是事实，是上一轮的记录** —— 引用「某条未修 / 仍是 X」之前必须核 commit 历史。`git log` / `git show` 是权威；缺陷表、清单、README 的状态行只是**写下的那一刻**的快照，会滞后于实际。案例：缺陷 22 已由 `ac2692f` 修掉，状态行却仍标「未修」，被当作遗留报了出去 —— 漏记的动作只有一个：**只读了状态行，没核 `git log`**。这是上一条的**同源反面**：上一条说「别引用上一轮跑出来的**数字**」，这一条说「**台账本身也是上一轮的结果**」。判据：任何「某条仍未修 / 仍是某形态」的结论，落笔前跑一次 `git log --grep=<关键词>` / `git show <sha>` 核验；核不到、或状态行与历史打架时，**以历史为准并就地订正台账**（订正要在条目里写明「原状态行滞后」及原因，否则下一个人会再踩一次）。**同一动作也适用于本文件的其它清单**（如 §五 工具现状）：清单是索引，不是证据。同族第二案例：`docker-compose.local.yml` 头部自称「这份文件不要提交 git！」，实则**早已入库**（`git ls-files` 命中，`4e69615` 引入）—— **注释也是写下的那一刻的记录**，与状态行同理；判据相同：以 `git ls-files` / `git log` 为准，不以文件自己怎么说为准。已记账，用户裁定**不修**（2026-09-14）。**同族第三案例**：`.claude/settings.local.json` 白名单里那条凭据前缀 —— 它记录的是「**有人这么打过这条命令**」，不是「**这条命令成功过**」。修 33 时实测那个口令**从未生效过**（`FATAL: password authentication failed for user "charsim"`；卷 `character-distill_pg_data` 建于 2026-06-25，容器建于 2026-08-17 且日志显示 init 被跳过，即那条命令打在了一个已存在的卷上），真值最后从两个已退出容器的 `.Config.Env` 里找回（详见缺陷 33）。判据与前两案例相同：**以「它是否真的成立」为准，不以「有那么一份文本」为准** —— 且这一例更阴：前两例是「文本说错了」，这一例是「文本说了一件从没发生过的事」
- **判据不得建立在「看起来像」之上，必须落在「是不是」那一层事实 —— 判据与被判事实之间隔着的东西，就是能骗人的地方。** 可判定的操作（写锁前做三步）：① 写下这条判据**读的是什么** —— 一个名字？一个字符串？一份清单？一段文本？还是**被判对象本身 / 它的一次真实运行结果**？② 问「**有没有一条路径，能让这个『读的东西』与被判的事实脱钩，而两边都不报错？**」—— 举得出，它就是代理指标；举不出不算答（须给出「为什么不存在这种路径」），因为「想不出」与「不存在」是两回事。③ 举得出时换成读事实那一层的形态；**换不动就写明退到了哪一层、为什么退**（见本节「症状在本环境结构性测不到时，退到最近的可验证事实」那条）。**本条的②层实例**：`except` 能接住什么，是对**失败模式**的假设（隔着 1 层），「实测抛的是什么」才是事实（0 层）—— 实测 `os.path.samefile(None, x)` 抛的是 `TypeError` 而非 `OSError`，故 `None` 必须在调用前挡掉（缺陷 42 第 1 步收尾）；「我以为 except 能接住」与下面谱系里那些缺陷是同一形态。三个互不相同的分界，按「隔着几层」排：
  - **① 隔着 0 层：判据读的就是事实本身。** SQL 谓词原文、文件系统身份（`os.path.samefile`）、运行时实测的命中站点、从 DDL / 源码事实推出的集合。改动即红，没有误报面，可直接用。
  - **② 隔着 1 层「同源派生量」：与事实通常同向，但存在可构造的脱钩路径。** 典型：AST 形状、签名的参数是否存在、`__file__` 字符串相等、参数的**存在**（而非**被用**）。**这一层不是没用，是「有已知盲区」**——留着它就必须**同时**给一条「为绕过它而设计」的变异去撞（见本节「变异实验会暴露判据本身的盲区，不只是实现的缺陷」）；撞不红，说明代理层还留着半个。盲区的形态往往与正常错误**长得一样**（都报绿），所以只有这种变异照得出来。
  - **③ 隔着 ≥2 层，或中间那一层由人维护：两边不存在任何自动同向关系。** 手工清单（`_stubs` 那种假守卫、豁免名单 `_NOT_APPLIED`、allowlist、`# type: ignore`）、文本记录（台账状态行、注释、docstring、白名单）。事实变了它不跟着变，它变了也不代表事实变了，**脱钩时两边都不报错**。必须换成从事实推出的形态，或退成运行时探针。**但③层不是一律要变异 —— 要看失效方向。** 策略表（`_FORM_METADATA` 那几个字段算元数据、`ALLOWLIST` 哪条路由豁免）是**意图不是事实**，没有事实层对应物；它们脱钩的方向是**响亮误伤**（新增一个 Form 字段就红、红的信息点名该改哪一行），不是静默漏过 —— **响亮误伤**与**静默漏过**是两回事，前者的漏法只是「多红了一次」，红本身把该动的地方说清了。判据：**问它脱钩时是「该红没红」还是「不该红却红了」** —— 前者是静默面、必须配绕过型变异；后者是响亮面、人看得见即可。实例：缺陷 42 第 3 步两处「换不动」的降层 —— L5 的元数据表落在**后者**（响亮误伤，不配变异），auth 锁的「用没用」落在**前者**（静默漏过，写进 docstring 当已知盲区 + 与缺陷 25 同形）。
  - **案例谱系（同一病灶，「隔的东西」不同）**：缺陷 19 初版——判据是**命名后缀** `*_unscoped`，真值层是 SQL 事实（WHERE 谓词 / 调用点集合）；缺陷 25——判据是**签名里有身份参数**，真值层是 WHERE 谓词（变异「删 `AND c.user_id = $2`、保留签名参数」照理该红却**绿**，盲区由此照出，改判据后才红）；缺陷 39/40 的 L5——判据是 **`Form(...)` 默认值的 AST 形状**，真值层是框架自己的账本（`get_dependant` / `get_openapi`），`Annotated[...]` / keyword-only / `fastapi.Form` 三种等价写法**静默漏过**；缺陷 42 第 1 步——判据从**模块名**（`web.server`）改到 **`__file__` 字符串相等**，仍是②层代理（盘符大小写、符号链接、`..`、大小写不敏感文件系统都能骗过），最终落在 `os.path.samefile`（同一个 app：`E:\…` 与 `e:\…` 字符串不等而 `samefile` 为真；变异 X-5 把判据退回字符串相等后别名装载**全绿**，红源由此唯一地钉在那次改动上）；`test_rag_unusable.py` 的 `_stubs` **手工清单** → `_stub(storage, name, **kw)` **赋值即声明**（清单是人抄的副本，赋值是事实）；缺陷 42 第 3 步——上述两把锁**已落到**那一层：L5 的覆盖面由 `Form(...)` 形状改为「`get_openapi` 里声明了表单 content 的 op」（1 条 → 4 条是**能力达到不是扩面**），auth 锁的识别半边由 `d.call is get_current_user` 取代形状扫描（`Annotated[...]` 从静默漏过**变成红**：同一个注入变异在新判据下红（V1）、把判据退回旧的 AST 形状后绿（V4）—— 这一对是证明迁移真的降了层的唯一证据）；**缺陷 42 结案**——L5 的第 3 步初版虽然读的是框架账本，却仍拿**字段名**（`_PAYLOAD_FIELD = "file"`）判断谁是正文通道，把文件字段改成同名的文本 Form 字段后正判据与负控**全绿**（V9 照出、V10 用「退回按名字排除 + 同一改法」把红源钉死在判据本身）；这是本形态的**第四次**显形（AST 形状 → 并列身份按模块名 → 按 `__file__` 字符串 → 按字段名判身份），真值层仍在上一次已经找到的那层（schema），只是**有一半边没跟着一起下来**；两把锁各留一处**换不动**的半边，按本条在 docstring 里写明而不是假装覆盖。**本条与本节「台账状态行不是事实」、以及「凡『用来防误用』的守卫，其核对的名字必须从被防的事实推出，不能另抄一份清单」是父母与子女的关系**——那两条各只说了一个面（文本记录 / 手工清单），本条给的是它们的共同判据：**问它隔着几层能骗人的东西**。四次撞上同一形态（原锁 AST 形状 → 并列身份按模块名 → 按 `__file__` 字符串 → 正文通道按字段名）就不是巧合，这也是本条立为通用判据的由来
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
- **豁免机制本身会成为新的静默通道 —— 一把锁允许「写理由即可跳过」，就必须有**另一样东西**去验那条理由声明的后果**。判据：给任何锁加「豁免出口」时，**同一次改动里**必须同时给出验证该后果的独立断言；否则豁免 = 永久放行。案例：`tests/test_migration_dispatch.py::_NOT_APPLIED` 允许以理由豁免迁移接线，`079_remote_user_profiles.sql` 就躺在里面 —— 三把锁（`test_migration_dispatch` / `test_sqlite_fresh_schema` / 分发形态锁）**各自尽职、全部绿**，而 SQLite 新库仍然缺表；更刺眼的是 079 的豁免理由里**白纸黑字写着**「新库缺表、代码在用」，却没有任何东西去验这句话。理由本身不是闭环，它只是把「未修」从代码搬进了注释。修法是**补一条验后果的锁**（`TestExemptionClosedLoop`：新库表集合 ⊇ `migrations_pg/` 声明的表集合，锁症状本身），**不是**再加一条「文件有没有登记」的形态锁 —— 后者正是被那个合法豁免绕过的那一个。推论：`skip` / `xfail` / allowlist / 豁免名单 / `# type: ignore` 这类机制都应按此自查「谁在验我这条理由」；本轮的 `# store-empty-ok:` 标记同理，其后果由 `tests/test_store_failure_visibility.py` 的动态探针（真的把库打坏，看失败有没有消失）来验，而不是靠标记本身

- **「改动前既有」不是一种处置 —— 一条恒红的锁和没有锁是一回事，一条恒 skip 的锁同理**。判据：收口时若全量存在红色用例，必须逐条定性并落到**根因修** 或 **显式 skip（原因可见且写明怎么让它真跑）** 二者之一，不允许「环境性、不管」过去；「改动前就已红」只说明责任更早，不说明可接受。案例：缺陷 25 收口时本地全量 `1 failed`，是 `tests/test_rag_unusable.py::test_caller_group_rebuild_degrades_no_index` —— 缺陷 19 的 `e3cf9e8` 把 `group.py` 的 `get_group_session` 改名 `get_group_session_owned`，用例桩没跟着改，`await MagicMock()` 报「不是 awaitable」，指不到根因。**先答「为什么没人发现」再动手**：`gh run view <run> --log-failed` 显示 CI 明确 FAILED（run `34743380519`：`1 failed, 935 passed, 0 skipped`）—— 即 **CI 跑了、也红了，只是红的 CI 没人看**；缺口不在覆盖，故不新增 CI 步骤，只订正为「收口必须看 CI 结果」。修测试侧同时把复发堵掉：桩名改由「实际要桩什么」推出（`_stub()` 赋值即声明并核对真实 store 有同名方法），**不再另存一份硬编码桩名清单** —— 清单自己会漂，原写法正是「用代理（清单）代替事实（赋值）」，与缺陷 25 同病灶
- **skip 也是豁免，就必须有人在「声明的环境」里验它没被滥用**（上一条在 skip 上的实例）。本地无 PG 时 PG 用例走 `skipif`，在标准本地环境下**恒 skip**，会把红训练成背景噪声、最终连真失败一起忽略。处置是双向的：① PG 不可达时**显式 skip 且原因写清怎么让它真跑**（`tests/conftest.py::pg_skip_reason`），判据是**真连一次**（`pg_reachable`）而非「读 `STORAGE_BACKEND` 猜」——后者是代理指标，变量写成 postgres 而库连不上时用例会以连接错恒红；② 环境**声明有 PG 时拒绝 skip**（`build.yml` 的 test job 设 `REQUIRE_PG_TESTS=1`，`pg_required()` 是唯一认它的地方），并由 `test_pg_suite_is_not_silently_disabled_where_pg_is_required` 对账「在声明的环境里被静默跳过、或被 `SKIP_PG_TESTS` 关掉」**本身判红**。**变异验证**：`REQUIRE_PG_TESTS=1` 但无 PG → 两条 PG schema 锁 + 该元断言**三条齐红**（不是 skip）；`REQUIRE_PG_TESTS=1` 与 `SKIP_PG_TESTS=1` 同置 → 元断言在「两边都要」那条上红。`@_pg` 挂**类**不挂模块级 `pytestmark`，否则会把不需要 PG 的纯提取器负控（`test_lock_has_teeth`）一起 skip 掉 —— 用不相干的理由关掉一条本地跑得动的锁，正是要消灭的静默通道
- **凡「用来防误用」的守卫，其核对的名字必须从被防的事实推出，不能另抄一份清单**。案例：`test_rag_unusable.py` 修复桩漂移时，初版守卫是硬编码 `_stubs = (…4 个名字…)` 再逐个 `hasattr` —— 看起来在防桩漂移，实测把桩名改回旧名**它不报**（仍只报「MagicMock 不是 awaitable」），因为它核的是那份**清单**而非**实际桩了什么**。改成 `_stub(storage, name, **kw)`（赋值即声明，赋值时核对）后，把桩名回退成 `get_group_session` 立刻红在「桩 'get_group_session' 在真实 store 上不存在 —— 桩漂移了」。一般化：**守卫与被守对象之间若隔着第二份手工维护的清单，清单就是新的漂移点**（同 §四「判据要问它能否独立排除目标行，而不是它提到了这个列」）
- **锁的输入状态本身也是判据的一部分，只守理想初态的锁会漏掉生产实际运行的那个状态**。判据：写一条比对/闭环锁时，先问「**生产上跑的是哪个状态**」，再问「我的锁跑的是哪个状态」——两者不是同一个时，锁绿不构成结论。案例：缺陷 23 的列级锁比的是 **fresh SQLite ⟷ fresh PG**（各跑一轮 init），而生产 / 长期开发库跑的是**重启过若干轮**的库：第二轮起 018 会把 `api_key`/`base_url`/`model` 加回 users，删列块却因为哨兵 `password_hash` 早已被删而整块跳过 → 25 ≠ 22 列的漂移**只在第二次 init 后出现**，fresh 锁**一直绿**（变异实测：改回旧触发条件后，fresh 那条仍绿、只有新加的「二次 init ⟷ fresh PG」红）。一般化：**状态是判据的输入，不是背景** —— 「初始态」「空库」「首次运行」这类理想态最容易让锁自证成立；凡是描述「稳态」的断言，都要说清是哪一个稳态（首轮 / 第 N 轮 / 已收敛）。**推论（同族）**：读事实的锁不许带写副作用 —— `_sqlite_columns` 初版每次读都跑 `_ensure_initialized`，把变异当场「修好」，于是变异永远红不了；锁的读取路径与写路径必须分开。

- **排查前先确认那条日志出自哪个 revision —— 出处不明的日志比没有日志更危险**。判据：拿一段日志当证据之前，先在**产出它的那份代码**里定位那行字符串（`git log --all -S '<原文>'`；再到容器 stdout / 宿主机日志里 grep 一遍）。全空即说明它不属于任何已知版本，**在它上面做出的每一条推断都是空中楼阁**——没有日志至少知道自己不知道，来源不明的日志会让人以为自己在推理。案例（2026-09-14）：「识别角色卡住」的诊断建立在日志 `调用失败 (尝试 1/3)，2.0s 后重试` 上，据此推出「`_GEN_ATTEMPT_S` 默认 45 秒不够」；而该常量与那行日志在本仓**任何 revision 都不存在**（`git log --all -S` 全空，`a41fcfd` 删掉的是英文 `[LLMAdapter] Attempt {n} failed: ... retrying in {w}s...`），容器 stdout 18651 行、宿主机 `logs/` 亦零命中；观测到的间隔（11s / 5s / 5s）本来就对不上 45s ceiling，否决它的证据当时就在。**同族推论**：日志的**格式**比它的**内容**更能定版本 —— 先问「这行是谁打印的、现码里还有没有这个格式」
- **版本抽样要选有区分度的样本 —— 没变更过的文件认不出版本**。判据：核对「镜像 / 容器 / 部署跑的是哪个 commit」时，样本必须是**在该区间内真的改过的文件**；拿一个横跨区间未动过的文件去比，会把版本误判到更早的 commit 上，而且**看不出任何异常**。案例（2026-09-14）：用 `adapters/llm_adapter.py` 比对得 `0403d407`（09-08），实际容器是 `b561f5e`（09-08 17:43:28+1000）——该文件在 `0403d407..b561f5e` 之间逐字节未变，真正钉死版本的是 `web/routers/market.py`（含 `_publish_preflight`、尚无 `get_card_unscoped`）。做法：**取 3 个以上区间内有变更的文件互相交叉**，或把镜像创建时间与相邻 commit 时间对齐（本次 `b561f5e` 提交 2026-09-08 17:43:28+1000 → 镜像 `2026-09-08T07:44:45Z`，差 73 秒）。**推论**：新版比对同理 —— 核「rebuild 有没有真生效」也要挑有区分度的标记（本次：`_RetryBudget` / `_THINKING_DISABLED` / `_GEN_DEADLINE_S` 在、且启动日志里 `[SQLiteStore]` 迁移告警由 3 条变 0 条）
- **核静态结论前，先把每个分支的入参追到源头 —— 只读被判对象自己，等于只读了半条链**。判据：从一条静态链推出「某分支必然 / 不可能发生」之前，把该分支的**每个入参**追到它的产生处。只读到被判对象自己的分支（`if` 的两边）而没读两个分支的**输入从哪来**，就会把一对**互斥**条件当成可同时成立。案例（2026-09-14，缺陷 37）：据 `get_distiller`（`web/deps.py:182-192`）的两个分支断言「`/run_stream` 原地改写模块级单例、跨请求污染」——`llm is not None` 走 `return Distiller(llm)`（新实例）、`llm is None` 走模块单例，看着两条都可能。追入参才发现：调用点传的 `per_user_llm` 来自 `get_user_llm`，而它**没 key 时回落 `get_llm()`**（`deps.py:118-119`）⇒ `per_user_llm is None ⟺ get_llm() is None`；而单例**仅在 `get_llm()` 非 None 时被创建** ⇒ 「单例存在」与「入参为 None」**互斥**，实际永远走新实例那条，改的是请求级副本。**这个互斥只有追到 fallback 那一行才看得见**。连带修正：同一条里「503 门拦不住」也随之证伪 —— 那道门（`distill.py:1059`）拦的正是「拿不到 LLM」，而污染的前提恰恰是「拿得到」，两者不可能同时成立。教训：标注「静态读出」**救不了没走完的静态链** —— 标注只声明了方法的局限，不构成结论成立的充分条件。

**同族三条：只看了链条的一段就下结论。** 上面各条中，「**台账状态行不是事实**」（读了**记录**，没核它指向的 `git log` 源头）、「**变异工具自身失效时，红和绿都不可信**」（验了**结果**，没验**工具本身跑没跑**）、本条的「**只读被判对象自己**」（读了**函数**，没追它的**入参**）是同一病灶落在三个对象上的实例：**结论所依赖的那一环，和结论本身之间隔着一段没人读的链路**。三条的检查动作也同构 —— 落笔前问一句「**这段是我读到的，还是我从别处推的？推的那一环我读了吗？**」：读记录就问源头、读结果就问工具、读函数就问入参。三条不并成一条（对象不同、修法不同），但**看到其中任何一条的形态，去另外两条上各扫一眼**。

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
