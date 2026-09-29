# Spec：LLM 适配层——确定性失败不重试，按 key 来源分级记日志

基线：main `dd9f673`。只改 `adapters/llm_adapter.py` 与其 key 来源的传入点，不动重试预算、超时、闸的结构。

**设计决定（待 Shiyu 拍板，未确认前执行端不动）**
- D1 确定性失败的范围：**采用**「只重试 408/409/429/≥500，其余状态码（含 400/401/402/403/422）一律不重试」。两个权威来源一致：openai SDK `_should_retry`（下表）与 DeepSeek 官方错误码页（`platform.deepseek.com/api-docs/quick_start/error_codes`，已拉取）——后者明确 400/401/402/422 要先修请求、密钥、余额、参数，429 放慢节奏，500/503「稍等后重试」。仓库里 `chat_with_tools` 也已有「400 不重试」先例，一个口径最省维护。窄口方案（只认 401/402/403）不采用。
- D2 key 来源的传入方式：**推荐 A** 构造适配器时传入 `Source`，日志级别按来源分（用户 key → warning，全局 key → error）；B 不传、一律降 warning（全局 key 余额耗尽会静默，不推荐）。
- D3 批量 Map 阶段遇到 402 是否整批中止：本 spec **不改**，S0 只报告现状（见下），由 Shiyu 决定。

## 目标

401/402/403 等确定性失败立即返回带上屏文案的 `UpstreamFailure`，不白等退避；用户自带 key 的失败不再以 error 级别进告警，全局 key 仍是 error。

## S0（先做，不过就停）

0. 在 worktree 路径 `.claude/worktrees/<name>/docs/specs/fix-upstream-nonretryable.md` 上 `Test-Path` + 与源文件 hash 比对，不通过即停。
1. 逐条复核下表。
2. 读 `LLMAdapter.__init__`（`llm_adapter.py:710-770`）、`web/deps.py` 的 `_make_user_llm`、`get_llm`，写明 key 来源在哪一处传入最自然（我未读这三处）。
3. 定位「with_tools 400 不重试」在代码里的实现位置（`test_llm_adapter_retry.py:289` 守着它，`llm_adapter.py:21` 导入了 `BadRequestError`），写明它与新判定的关系；两者重叠就并到同一处，不并存两套。
4. 读 `core/distiller.py` Map 阶段收到 `UpstreamFailure` 后的行为（继续其余分片 / 整批中止），原始代码贴回，供 D3 决策。

## 已查实的约束（基线 `dd9f673`）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 重试判定只分「429 / 其他」；402 落入「其他」，按非 429 上限重试（`_GEN_ATTEMPTS=3`） | `llm_adapter.py:32-51`、`:205-213`、`:79` |
| C2 | 429 判定含 `"429" in str(exc)` 子串匹配；同一判定在 `_upstream_user_message` 又写了一遍 | `:39`、`:495-497` |
| C3 | 耗尽后一律 `logger.error`；402 文案已登记，用户侧文案无需改 | `:243`、`:453` |
| C4 | 线上证据（Sentry 事件 `01a0e792…`、`01a0e7929…`，2026-09-28 20:30-20:32 UTC+10，完整面包屑已读）：`[LLMAdapter async]` = `async_chat` 批量 Map 路径；用户自带 key（Shiyu 已确认）。时间线：20:30:41 约 60 路同时 429（attempt 1）→ 20:31:37 起出现 402：`Attempt 3 failed … retrying in 15.3s`、`Attempt 4 … 20.8s`、`Attempt 4 … 20.5s`、`Attempt 5 … 25.3s` → 20:32:18 `All 5 attempts failed`，随后 `core.distiller: Map chunk 94 failed`。**退避 15.3/20.8/25.3s 与公式 `5×_total+U(0,1)` 逐点吻合**（`_total` 含 429 次数，故 402 的等待被 429 放大）。「6 次」= 3 次 429 + 3 次 402、「5 次」= 2 次 429 + 3 次 402，恰为非 429 上限 3 次耗尽 | Sentry；`:882-960`、`:205-213` |
| C5 | key 来源已在解析出口算好：`Source.USER` / `Source.GLOBAL` | `web/llm_resolution.py:26-36`、`:52-80`；`web/deps.py:110-119` |
| C6 | `_classify_retry` 被 `on_failure`（`:205`）与闸下调（`:940`）两处消费；`_upstream_user_message` 被 `on_failure`（`:235`）与流式路径（`:1064`）两处消费 | 见扫描原文 |
| C7 | `tests/test_failure_alerting.py:401` 把 `_classify_retry` 登记为「函数内恰好 1 处宽 except」的豁免锁——**函数名与那处 except 不能动**，否则同一提交里改锁 | `tests/test_failure_alerting.py:396-406` |
| C8 | SDK 已设 `max_retries=0`，重试只由 `_RetryBudget` 负责 | `tests/test_llm_adapter_retry.py:143` |

## 出处对照表（权威：`openai==3.13.0`，Apache-2.0，已拉源码）

`openai/_base_client.py:815-857` `_should_retry`，`requirements.txt:185`。

| SDK 条目 | 本 spec 行为 | 决定 |
|---|---|---|
| `Retry-After` 超过 `MAX_RETRY_AFTER_DELAY` → 不重试（817-823） | 适配器自己把 `Retry-After` 夹到 60s（`:46-48`），口径不同 | 不动，保持现状（超范围） |
| DeepSeek 官方错误码页：400/401/402/422 不重试；429、500、503 重试 | 同 B1–B4，与 SDK 一致 | 已核对，无分歧 |
| `x-should-retry: true/false` 头 → 服从（826-834） | — | **不采纳**：YAGNI，DeepSeek 不发该头，日后再加 |
| 408 → 重试（837-839） | B2 保持重试（按非 429 计） | 现状即如此 |
| 409 → 重试（841-844） | B2 同上 | 现状 |
| 429 → 重试（846-849） | B3 走独立 429 预算，判定只看状态码 | 去掉子串匹配 |
| ≥500 → 重试（851-854） | B4 现状不变 | 现状 |
| 其余 → 不重试（856-857） | **B1 立即 `UpstreamFailure`，不 sleep** | 新增（D1） |
| —（无状态码：超时、连接失败） | B5 现状不变，仍重试 | 现状 |

新增行为编号：B1 确定性失败立即抛；B6 日志级别按 key 来源；B7 确定性失败不通知闸（不调 `on_rate_limited`）。

## 已有路径上的机制（往里改判定前列出，逐个核对前提）

| 机制 | 计时/计数从哪开始 | 依赖的前提 | 本次是否改变 |
|---|---|---|---|
| `_total` / `_non429` / `_rate_limited` 三个计数器 | `on_failure` 每次失败累加 | 非 429 上限 = `attempts`，429 上限 = 5 | 不变；B1 直接走 `cap_hit=True` 分支 |
| 非 429 退避 `backoff_mult × _total + U(0,1)` | 用的是 **`_total`**（含 429 次数），不是 `_non429` | 与 429 交错时退避被放大（如 `_total=4` → 20-21s） | 不变；B1 不 sleep，此放大对确定性失败消失 |
| 总墙钟 `deadline` | 预算构造时起算；`async_chat` 进闸后用 `extend_deadline` 顺延排队时间 | 批量默认 `_GEN_BATCH_DEADLINE_S` | 不变 |
| 单次超时 `attempt_timeout()` / `_next_to` | 每次 create 前取；`on_failure` 预计算下一次 | 剩余窗 ≥ margin+min | B1 不再发下一次，不触及 |
| `AdaptiveGate` | `_classify_retry(exc)[0]` 为真才 `on_rate_limited(gen)`（`:940`）；成功才 `on_success`；退避睡眠在闸外 | 只有 429 下调并发上限 | B1 不属 429，闸不受影响（B7）；去掉子串匹配后，闸的输入只剩真 429 |
| `_upstream_user_message` 的流式消费 | `:1056-1064`，在预算之外 | 与 `_classify_retry` 的状态码口径一致 | 二者改为共用同一取状态码函数（一处定义） |

## 规模表（真实规模的数字）

| 路径 | 预算 | 数字 |
|---|---|---|
| `async_chat` 批量（Map） | `_GEN_BATCH_DEADLINE_S` = 3×45 + (5·1+1) + (5·2+1) + 1 | **153s**；非 429 最多 3 次尝试 |
| 交互式 `achat` / `chat` | `_GEN_DEADLINE_S` | 60s，3 次 |
| 决策轮 `chat_with_tools` | `_DECISION_DEADLINE_S` | 6s，2 次，退避 1s |
| 流式 `_stream` | `_STREAM_DEADLINE_S` | 8s，2 次，退避 1s |

**线上实测：一条 402 链在耗尽前白等约 45 秒以上（20.8+25.3 加前面的 429 退避），且每片独立重复。**

**402 落在 Map 上的比例（代码注释里的生产规模：242 片、账号并发上限 18）**：
- 现状：每片最多 3 次请求，退避 (5+1)+(10+1)=约 15-17s → 最多 **726 次**请求。
- 改后：每片 1 次 → **242 次**，0 秒退避。
- 与 429 交错时，现状的退避随 `_total` 放大，改后同样降为 0。
- 测试要按这个比例复刻（60 路并发，见矩阵），不只验证「不重试」。

## 全量扫描原文

命令：`grep -rn -E "_classify_retry|_upstream_user_message|_UPSTREAM_USER_MESSAGES" --include=*.py .`（去掉 def 行）
```
adapters/llm_adapter.py:205   is_429, retry_after = _classify_retry(exc)
adapters/llm_adapter.py:235   user_message = _upstream_user_message(exc)
adapters/llm_adapter.py:450   _UPSTREAM_USER_MESSAGES: dict[int, str] = {
adapters/llm_adapter.py:498   return _UPSTREAM_USER_MESSAGES.get(code, "")
adapters/llm_adapter.py:940   if gate is not None and _classify_retry(exc)[0]:
adapters/llm_adapter.py:1064  user_message=_upstream_user_message(exc),
tests/test_failure_alerting.py:401        ("adapters/llm_adapter.py", "_classify_retry")   ← 豁免锁
tests/test_llm_adapter_retry.py:123,241,256   假异常驱动 _classify_retry / _UPSTREAM_USER_MESSAGES
tests/test_distiller_routing.py:883           429 带 Retry-After:0 走 _classify_retry
tests/test_chat_stream_error.py:294           变异⑥ 针对 _upstream_user_message
```
`_RetryBudget(` 使用点：`:847`（chat）、`:904`（async_chat）、`:990`（_stream）、`:1175`（with_tools 决策）、`:1227`（with_tools 生成）。

## 约束

- 判定口径来自上表，不自造；代码注释写明 SDK 文件与函数名。
- `_classify_retry` 与 `_upstream_user_message` 共用同一个取状态码的函数；删掉两处子串匹配；**`_classify_retry` 函数名与其 Retry-After 的那一处 except 保持不变**（C7）。
- 无状态码的异常仍按可重试处理，行为不变。
- 不改 `_UPSTREAM_USER_MESSAGES`、预算常量、超时、闸。
- S0-3 若发现 400 不重试已有实现，并到同一处，不留两套。

## 步骤（每步独立 commit）

1. [适配层] 抽出统一的状态码提取 + 「是否确定性失败」判定（对齐 SDK）；`_classify_retry`、`_upstream_user_message` 改用它。
2. [适配层] `on_failure`：确定性失败进入耗尽分支，不 sleep；日志级别按 key 来源（USER → warning，GLOBAL → error）；日志模板保持 `%s` 占位，GlitchTip 才能同模板归并。
3. [解析出口] 构造适配器时带入 key 来源（位置以 S0-2 为准）。
4. [测试] 见矩阵。

## 通道 × 执行上下文 × 守它的测试

| 通道 | 上下文 | 预算 | 现有守卫 |
|---|---|---|---|
| `chat` `:847` | 同步线程，`time.sleep` | GEN | 待 S0 补测试名（我未在测试文件里对应到） |
| `async_chat` `:904` | asyncio + 闸，`asyncio.sleep` | GEN 批量 | `test_async_chat_500_storm_capped :324`、`test_async_chat_retry_not_starved_by_total_deadline :360` |
| `_stream` `:990` | 同步生成器 | STREAM | `test_chat_stream_create_storm_capped :399` |
| with_tools 决策 `:1175` | 同步 | DECISION | `test_decision_500_storm_raises_fast_not_5s_10s_wall :150`、`test_with_tools_400_no_retry_preserved :289` |
| with_tools 生成 `:1227` | 同步 | GEN | 待 S0 补测试名 |

五条通道共用 `on_failure` 这一个汇合点，B1 落在那里，一处覆盖；测试按五条通道各验一次。

## 调用点矩阵（行 = 通道，列 = 可观测输出，格 = 测试名；新增用例参数化覆盖五条通道）

| 通道 | 402：0 次 sleep、只发 1 次 | 402：`user_message`=余额不足 | 日志级别 USER/GLOBAL | 429 行为不变 | 500 行为不变 |
|---|---|---|---|---|---|
| chat | `test_deterministic_status_no_retry[chat]` | `test_exhausted_upstream_failure_carries_user_message :270`（现有） | `test_deterministic_log_level_by_key_source` | 现有 `:238`、`:199` | 现有 `:324` 同族 |
| async_chat | `[async_chat]` + `test_async_chat_402_batch_no_retry_ratio` | 同上 | 同上 | 现有 | `:324` |
| _stream | `[stream]` | 同上 | 同上 | 现有 | `:399` |
| with_tools 决策 | `[with_tools_decision]` | 同上 | 同上 | `:238` | `:150` |
| with_tools 生成 | `[with_tools_gen]` | 同上 | 同上 | 现有 | 同 chat |

另加：
- `test_async_chat_402_batch_no_retry_ratio`：60 路并发 + 闸，假上游全回 402 → 断言上游被调恰好 60 次、0 次 sleep、闸上限不变；对照用例 429 风暴仍触发闸下调（现有行为）。
- `test_status_402_with_429_text_not_rate_limited`：状态码 402 而文本含 "429" → 不当限流、闸不下调。
- 沿用现有假异常与 sleep 打桩方式（`test_llm_adapter_retry.py:123`、`:256`）。

## 对账（变异；执行端在分支上逐个实跑，存活即不合并）

| 变异 | 期望被谁杀死 |
|---|---|
| 判定改回「一律重试」 | `test_deterministic_status_no_retry[*]` |
| 恢复 `"429" in str(exc)` | `test_status_402_with_429_text_not_rate_limited` |
| B1 后仍调 `gate.on_rate_limited` | `test_async_chat_402_batch_no_retry_ratio` |
| USER/GLOBAL 日志级别对调 | `test_deterministic_log_level_by_key_source` |

## 测试

- 本地只跑 `tests/test_llm_adapter_retry.py`、`tests/test_failure_alerting.py`、新增用例所在文件（均不依赖数据库）；不涉及前端。
- 合并门槛是分支 CI；**合并只做 git 操作，不跑测试、不等 CI**；执行报告不出现本地全量数字。

## 交付

- 报告开头先列本段改动的文件清单；S0-4 的现状原始代码贴回。
- 新发现：属于本段改动面的直接修；只有会撞车或需拍板时才停下报告，不自行记账。
