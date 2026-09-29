# Spec：蒸馏 / 识别整批失败时的上屏文案——四处裁决收口到一个出口，用适配层给的 user_message

基线：main `360c66bb`。只改 `core/distiller.py` 与 `tests/test_distiller_routing.py`；不改适配层、路由、阈值。
在 `fix-llm-402` 这个 worktree 上从最新 `origin/main` 新开分支 `fix/map-failure-message`（旧分支已合并，不在其上提交）。
**本文件为第 2 版，取代同名第 1 版**（第 1 版漏了判定阶段第 4 个调用点、缺出处表与执行上下文表、变异未预跑）。

## 目标

识别 / 判定 / 蒸馏阶段整批失败时，用户看到上游失败的真实原因（余额不足、key 无效、请求过于频繁），不再一律说「部分片段处理失败」或「判定未能取得一致结论」；不再因报错文本里偶然出现 "429" 而误判成限流。

## 复用与选型（规则 1、2）

- **不引入库，也不自写新机制**：复用适配层已有的唯一文案来源 `UpstreamFailure.user_message`（`adapters/llm_adapter.py` `_upstream_user_message`）。distiller 只负责「拿来用」，不再自己判断上游错误。本问题是仓内两层之间的数据丢失，没有第三方库对应，不存在选型。
- 不涉及原生控件替换（规则 2 不适用）。

## S0（先做，任一不过即停）

0. 对 `.claude/worktrees/fix-llm-402/docs/specs/fix-map-failure-message.md` 做 `Test-Path` + 与源文件 hash 比对；确认是第 2 版（首段有「取代同名第 1 版」）。
1. 逐条复核「已查实的约束」C1–C10。
2. 环境冲突（见 C10）：不用 55432；起一次性 PG 前先查所选端口、容器名是否被占用。

## 已查实的约束（基线 `360c66bb`）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 整批失败文案在 4 处各自生成：识别、判定、同步 Map、流式 Map（全量扫描见②） | `core/distiller.py:1292-1303`、`:1373-1380`、`:2147-2158`、`:2394-2406` |
| C2 | 其中 3 处用 `"429"` 子串自判限流；判定阶段不看失败原因，统一说「未能取得一致结论」 | 同上 |
| C3 | 失败阈值唯一判据 `_map_failure_exceeds_tolerance`，新出口放它旁边 | `core/distiller.py:385-393` |
| C4 | `DistillError(user_message, ops_detail)`：前者上屏，后者只进日志 | `core/distiller.py:297-313` |
| C5 | 单片失败只追加进 `failures`（加锁，按完成先后），不中止整批 | `core/distiller.py:1904-1920` |
| C6 | `core/distiller.py` 已从 `adapters.llm_adapter` 导入，加 `UpstreamFailure` 不产生新的层间依赖 | `core/distiller.py:24` |
| C7 | 现有测试用纯文本 `RuntimeError("rate limited (429)…")` 冒充限流，锁的是要删掉的子串行为，需改成真实 `UpstreamFailure` | `tests/test_distiller_routing.py:463-473`、`:507-525`、`:544-559` |
| C8 | 以下测试只手工构造 `DistillError("…上游接口限流…")` 测出口，不依赖 distiller 输出，**不改** | `tests/test_error_user_facing.py:86-104`、`tests/test_identify_failure_channels.py:59,207`、`tests/test_domain_exception_exit.py:233` |
| C9 | 判定阶段：执行器失败与「样本不合法 / 不一致」共用一个结论。只有存在失败且最后一个失败带上屏文案时才改说上游原因；纯不一致时文案不变 | `core/distiller.py:1344-1380` |
| C10 | 环境：`character-distill-test-postgres-1`（孤儿，占 55432，项目名 `character-distill-test`）在跑且不属本线，**不碰**；测试库用一次性容器 + 空闲端口，库名须以 `_test` 结尾（conftest 强制） | 窗口 B 报告；`tests/conftest.py` 的库名检查 |

## 样本范围与样本外行为

- **「取最后一个失败」的前提**：账户级失败（401/402/403）每片都一样，取哪一个都对——这是本 spec 实测的样本（全部失败同一状态码）。
- **样本外**：
  - 混合失败（如先 429 后 402）：取完成最晚的那个。`failures` 按完成先后追加，并发下哪片最后完成不确定，所以文案可能是其中任一种上游原因。可接受：每种都是真实原因；预跑用例锁了「列表最后一个」这一规则本身。
  - 零个失败对象（识别阶段只有解析失败）：给通用文案。
  - 最后一个失败不是 `UpstreamFailure`（超时之外的本地异常）或 `user_message` 为空（未登记状态码，如 500）：给通用文案（或判定阶段自己的文案）。

## 方案

在 `_map_failure_exceeds_tolerance` 旁加一个纯函数，作为 4 处的**唯一**文案出口：入参为阶段前缀、`failures` 列表、可选的兜底文案（默认「部分片段处理失败，请重试」）；最后一个失败是 `UpstreamFailure` 且 `user_message` 非空 → `「{前缀}：{user_message}」`，否则 `「{前缀}：{兜底}」`。
- 4 处改为调用它；判定阶段传自己的兜底「全书角色分组判定未能取得一致结论，请重试」。
- ops_detail 保持现有口径（含最后错误原文），删掉「API 429；…」专用分支。
- 限流文案从「上游接口限流，请稍后重试」变为适配层的「请求过于频繁，请稍后再试」——有意统一，文案只在适配层一处定义。

## ① 出处对照表（照抄仓内权威来源：`adapters/llm_adapter.py` `_UPSTREAM_USER_MESSAGES` 与 `_upstream_user_message`）

| 权威条目 | 本 spec 行为 |
|---|---|
| 401 → 「API Key 无效或无权限，请到设置页检查」 | B1：前缀 + 该文案 |
| 403 → 同上 | B1 |
| 402 → 「账户余额不足，请充值后重试」 | B1 |
| 429 → 「请求过于频繁，请稍后再试」 | B1（替代原「上游接口限流」） |
| 传输层失败 → 「模型服务暂时不可用，请稍后重试」 | B1（同样经 `user_message` 带过来） |
| 未登记状态码 → `""` | B2：兜底文案 |
| 状态码只看 `status_code`，不看报错文本（适配层已删子串匹配） | B3：distiller 也不再看文本 |

## ② 全量扫描原文

```
$ grep -rn -E "\"429\" in|'429' in|rate limited \(429\)\" in" --include=*.py . | grep -v "^./tests/"
./core/distiller.py:1295:            if "429" in last_error:
./core/distiller.py:2150:            if "rate limited (429)" in err_text or "429" in err_text:
./core/distiller.py:2397:            if "rate limited (429)" in err_text or "429" in err_text:
./adapters/llm_adapter.py:36:    （注释）

$ grep -n -E "_run_map_with_client\(|_run_map_concurrent\(|def _run_map" core/distiller.py
1272:        map_results, failures = self._run_map_with_client(      ← 识别
1340:        results, failures = self._run_map_with_client(           ← 判定
1835:    def _run_map_with_client(
1852:                return await self._run_map_concurrent(
1862:    async def _run_map_concurrent(
2134:            map_results, map_failures = self._run_map_with_client(   ← 同步 Map
2341:                _results, failures = self._run_map_with_client(    ← 流式 Map

$ grep -n -E "failures\[|map_failures\[|failures\)|len\((map_)?failures\)" core/distiller.py
1292:        failed = len(failures) + parse_failed
1294:            last_error = str(failures[-1][1]) if failures else "分片结果无法解析为角色数组"
1378:                f"（执行器失败 {len(failures)} 次），不足 {self.IDENTIFY_JUDGE_QUORUM} 份",
2147:        failed = len(map_failures)
2149:            err_text = str(map_failures[-1][1])
2157:                f"{failed}/{total_chunks} 个分片失败；最后错误：{map_failures[-1][1]}",
2350:            q.put(("done", failures))
2394:        failed = len(map_failures)
2396:            err_text = str(map_failures[-1][1])
2403:                    failed, total_chunks, map_failures[-1][1],
```
结论：`_run_map_with_client` 的 4 个调用者 = 4 个裁决点，无遗漏；`2350` 只是把 `failures` 经队列交回流式裁决点。

## ③ 规模表

| 数据源 | 上限 | 策略 |
|---|---|---|
| `failures` 列表 | ≤ 本阶段分片数（Map）或 `IDENTIFY_JUDGE_SAMPLES = 5`（判定，`:487`） | 只读最后一个，O(1)，不遍历 |
| 上屏文案 | 单条字符串 | 不拼接分片数、原文、request_id |

## 通道 × 执行上下文 × 守它的测试

| 通道 | Map 在哪跑 | 裁决在哪跑 | 守它的测试 |
|---|---|---|---|
| 识别 `:1292` | `_run_map_with_client` → `_run_async_in_ctx_thread`（工作线程里的 asyncio 协程） | 调用方同步线程 | `test_identify_402_shows_balance` |
| 判定 `:1373` | 同上 | 调用方同步线程 | `test_judge_402_shows_balance`、`test_judge_disagreement_keeps_own_message` |
| 同步 Map `:2147` | 同上 | 调用方同步线程 | `test_sync_402_bail_shows_balance`、`test_sync_429_bail`（改写）、`test_sync_generic_bail`（现有） |
| 流式 Map `:2394` | `C.ctx_thread` 工作线程 → `_run_map_with_client`，`failures` 经 `queue.Queue` 交回 | 生成器所在的消费线程 | `test_stream_402_bail_shows_balance`、`test_stream_429_bail`（改写）、`test_stream_generic_bail`（现有） |

出口是纯函数，不依赖执行上下文；表的作用是保证每条通道都至少有一条行为用例证明「确实调了出口」。执行方若发现第 5 个调用者，**先补这张表再写代码**。

## ④ 调用点矩阵（行 = 调用点，列 = 可观测输出，格 = 测试名）

| 调用点 | 402 → 余额不足 | 429 → 请求过于频繁 | 非 Upstream / 未登记 → 兜底 | 文本含 "429" 但状态码 402 | 零失败对象 | 自带兜底文案 |
|---|---|---|---|---|---|---|
| 出口纯函数 | `test_map_failure_message_table` | 同左 | 同左 | 同左 | 同左 | 由判定行覆盖 |
| 识别 | `test_identify_402_shows_balance` | 表覆盖 | 表覆盖 | 表覆盖 | 表覆盖 | — |
| 判定 | `test_judge_402_shows_balance` | 表覆盖 | `test_judge_disagreement_keeps_own_message` | 表覆盖 | 同左 | 同左 |
| 同步 Map | `test_sync_402_bail_shows_balance` | `test_sync_429_bail`（改写） | `test_sync_generic_bail`（现有） | 表覆盖 | — | — |
| 流式 Map | `test_stream_402_bail_shows_balance` | `test_stream_429_bail`（改写） | `test_stream_generic_bail`（现有） | 表覆盖 | — | — |

出口表的参数行：401、403、402、429、402 且文本含 "429"、500（未登记）、纯文本 `RuntimeError("rate limited (429)")`、空列表、`[429, 402]` 混合（锁「取最后一个」）。所有行为用例断言上屏文案**等于**期望值（不是「包含」），且不含 `个分片`、request_id。
新用例放进 `tests/test_distiller_routing.py`；**不要继承** `TestMapPhaseFailureHandling`（会把父类用例重复跑一遍），共用其 `_make_distiller` 的方式照搬或抽成模块级辅助函数。

## 对账（变异）——**已在沙箱原型上预跑，零存活**

预跑方式：我在沙箱按本 spec 写了原型实现与上表用例，一次性 PG，逐个变异、每次清 `__pycache__`（同尺寸改动会命中过期字节码）。基线 52 passed；新用例在未修复的 main 上 18 红（证明能抓到真 bug）。

| 变异 | 预跑结果（杀死者） |
|---|---|
| X1 出口忽略 `user_message` | 红：表、4 个 `*_402_*`、两个 `*_429_bail` |
| X2 出口内恢复 `"429"` 子串 | 红：表、`*_402_*`、`*_429_bail` |
| X3a 识别不调出口 | 红：`test_identify_402_shows_balance` |
| X3b 判定不调出口 | 红：`test_judge_402_shows_balance` |
| X3c 同步不调出口 | 红：`test_sync_402_bail_shows_balance`、`test_sync_429_bail` |
| X3d 流式不调出口 | 红：`test_stream_402_bail_shows_balance`、`test_stream_429_bail` |
| X4 识别前缀写成「蒸馏失败」 | 红：`test_identify_402_shows_balance` |
| X5 取第一个失败而非最后 | 红：表的混合行 |
| X6 忽略兜底参数 | 红：`test_judge_disagreement_keeps_own_message` |

执行端在分支上按同一张表复跑，存活即不合并。

## 约束

- 不改适配层文案表；distiller 不再写任何上游错误文案。
- 不改阈值、不改「单片失败不中止」。
- 测试替身用真实 `UpstreamFailure(…, user_message=…)`，不保留对纯文本 "429" 的兼容。
- 不新增模块、不新增抽象层；出口是一个函数。

## 步骤（每步独立 commit）

1. [core] 新增出口函数（`_map_failure_exceeds_tolerance` 旁），4 处改为调用它，删掉 3 处子串匹配与「API 429」分支。
2. [测试] 改写 C7 的替身与断言；按矩阵新增用例。
3. [docs] 本 spec 自成一个 commit。

## 不在本段范围（附理由）

- 402 失败分片仍按字符估算 token（`:1911-1913`），统计偏高：只影响内部统计且已标 `estimated`；要修须让 core 读适配层的失败分类，改动面超出本段。
- 402 后其余分片仍各发 1 次请求：适配层修复后每片 1 次、0 退避，代价小。

## 测试

- 本地只跑 `tests/test_distiller_routing.py`、`tests/test_error_user_facing.py`、`tests/test_identify_failure_channels.py`（一次性 PG，见 C10）。
- 合并门槛是分支 CI；合并只做 git 操作，不跑测试、不等 CI；执行报告不出现本地全量数字。

## 交付

- 报告开头先列本段改动文件清单。
- 新发现：属于本段改动面的直接修；只有会撞车或需拍板时才停下报告，不自行记账。

## 补充（审计发现当场记录在此）

- 2026-09-29 按「成本与规模相称」删去 `test_map_failure_message_fallback_param`：兜底参数已由 `test_judge_disagreement_keeps_own_message` 通过真实调用点守住（预跑中 X6 两条都红），单测重复。

- 2026-09-29 第 1 版自审：漏了判定阶段（`:1340`/`:1373`）这第 4 个调用点——当时只按 "429" 子串扫，没按「`_run_map_with_client` 的全部调用者」扫。第 2 版改为按调用者全量扫描（见②）。
- 2026-09-29 预跑中发现：变异脚本若不清 `__pycache__`，同尺寸改动（如调换两个同长单词）会被过期字节码掩盖，出现假绿 / 假红。执行端复跑变异时每次都要清。

### 2026-09-29 执行端 CI 红：C6 结论有误、C7 漏扫 `tests/`（均已修，改为走边界出口）

PR #64 分支 CI 两条红，都在本 spec 声明的改动面内，根因是 spec 自身写错。

**C6「加 `UpstreamFailure` 不产生新的层间依赖」—— 错。** 仓内早有一条边界锁
`tests/test_chat_stream_error.py::test_no_exception_class_leaks_into_core_web_storage`：AST 扫
core/web/storage 源码，凡出现 `llm_error_types()`（`adapters/llm_adapter.py:591`，元组为
`(IncompleteResponseError, LLMCallRefused, UpstreamFailure)`）里的**类名**（`ImportFrom` 别名 /
`ast.Name` / `ast.Attribute`）即红。CI 原文：

```
assert ['core/distiller.py:24: UpstreamFailure', 'core/distiller.py:420: UpstreamFailure'] == []
```

改法（不开白名单、不新增适配层 API、不用 `getattr` 鸭子类型）：走适配层**已有**的边界出口
`adapters/llm_adapter.py:564 llm_error_payload` —— 它把 `UpstreamFailure` 翻成
`{"code":"upstream","error": user_message or _GENERIC_USER_ERROR,"kind":"upstream"}`，说明里
已写明「供 core/web 用、**无需 import 异常类**」，`web/routers/chat.py:45`、`auth.py:725` 已在用。
出口改为：

```python
payload = llm_error_payload(failures[-1][1])
if payload is not None and payload["kind"] == "upstream":
    return f"{prefix}：{payload['error']}"
```

**裁定（未登记状态码）**：`user_message` 为空（未登记状态码如 500、裸传输层故障）时，由适配层在
payload 里落 `_GENERIC_USER_ERROR`（`adapters/llm_adapter.py:601`「服务暂时不可用，请稍后重试」），
统一上屏这一句 —— core 不再自己兜底，也不另立措辞。**这是「服务暂时不可用」唯一上屏的场合**：它是
适配层给上游失败用的通用文案，与出口自己的阶段兜底（「部分片段处理失败，请重试」）不是同一句。

**同族失败的副作用（白拿）**：`kind` 只有 `"upstream"` 才算上游原因；`incomplete:<finish_reason>`
（截断）与 `call_refused`（调用点拒绝）自动落本阶段兜底，不另设分支。原先「最后一个失败不是
`UpstreamFailure`」的判别，由 `llm_error_payload` 给 `None` 覆盖。

**C7 漏了第 4 个文件。** C7 是手写清单；②的全量扫描那一行是 `grep … | grep -v "^./tests/"`，
**明确把 `tests/` 排除了**，于是漏了
`tests/test_identify_whole_book.py::TestChunkFailurePolicy::test_429_gets_its_own_message`
（替身是纯文本 `RuntimeError("API 429 rate limited")`，锁的正是要删掉的子串自判）。CI 原文：

```
AssertionError: assert '限流' in '识别失败：部分片段处理失败，请重试'
```

已按同一口径改写：替身改抛真 `UpstreamFailure("rate limited (429) after 5 attempts",
user_message="请求过于频繁，请稍后再试")`，断言改为 `user_message == "识别失败：请求过于频繁，请稍后再试"`，
`str()` 仍带 429 原文（ops_detail 侧口径不变）。

### 执行端全量扫描原始输出（分支头 `e8860665`，已排除 `__pycache__`）

```
$ grep -rn '"429" in\|429" in \|rate limited (429)" in' core web storage
core/distiller.py:408:    （docstring 里提到旧写法，非代码）
$ grep -n "_map_failure_message(" core/distiller.py
401: def   1331: 识别   1407: 判定   2184: 同步 Map   2428: 流式 Map
$ grep -rn "UpstreamFailure" core web storage
core/distiller.py:26（import）、:420（isinstance）、:410/:415（docstring）  ← C6 违约，已删
$ grep -rln "上游接口限流\|部分片段处理失败\|API 429 rate limited" tests
tests/test_distiller_routing.py / tests/test_identify_whole_book.py（锁旧行为，已改）
tests/test_domain_exception_exit.py / tests/test_identify_failure_channels.py /
tests/test_error_user_facing.py（直接构造 DistillError 的通道测试，不经过被改代码，不改）
```

### 对账（变异）—— 执行端在最终状态实跑：基线 20 passed，8 行逐行单独施加、各自还原，零存活

每个变异只施加一次、跑完即还原；每次先清 `__pycache__`（同尺寸改动会命中过期字节码）。
空 `SURVIVORS: none`。

| 变异 | 必须红的用例 | 实跑 |
|---|---|---|
| 去掉 `kind` 判断 | `test_truncation_as_last_failure_falls_back` | KILLED（1 红） |
| 改用 `user_facing_error(failures[-1][1])` | `test_plain_text_429_without_an_upstream_failure_is_not_limiting`、`test_truncation_as_last_failure_falls_back` | KILLED（2 红） |
| 取 `failures[0]` | `test_mixed_failures_take_the_last` | KILLED（1 红） |
| 加回 `UpstreamFailure` import | `test_no_exception_class_leaks_into_core_web_storage` | KILLED（1 红） |
| `:1331` 识别调用点写死兜底 | `test_identify_402_shows_balance`、`test_429_gets_its_own_message` | KILLED（2 红） |
| `:1407` 判定调用点写死兜底 | `test_judge_402_shows_balance` | KILLED（1 红） |
| `:2184` 同步 Map 调用点写死兜底 | `test_sync_429_bail`、`test_sync_402_bail_shows_balance` | KILLED（2 红） |
| `:2428` 流式 Map 调用点写死兜底 | `test_stream_429_bail`、`test_stream_402_bail_shows_balance` | KILLED（2 红） |

失败原文（每个变异实际红的用例，与上表逐行一致，无多余红）：

```
M1_drop_kind_check: KILLED
    FAILED test_truncation_as_last_failure_falls_back
M2_use_user_facing_error: KILLED
    FAILED test_plain_text_429_without_an_upstream_failure_is_not_limiting
    FAILED test_truncation_as_last_failure_falls_back
M3_take_first: KILLED
    FAILED test_mixed_failures_take_the_last
M4_reimport_class: KILLED
    FAILED test_no_exception_class_leaks_into_core_web_storage
M5_identify_hardcoded: KILLED
    FAILED test_identify_402_shows_balance
    FAILED test_429_gets_its_own_message
M6_judge_hardcoded: KILLED
    FAILED test_judge_402_shows_balance
M7_sync_hardcoded: KILLED
    FAILED test_sync_429_bail
    FAILED test_sync_402_bail_shows_balance
M8_stream_hardcoded: KILLED
    FAILED test_stream_429_bail
    FAILED test_stream_402_bail_shows_balance
SURVIVORS: none
```

判据：零存活即合并门槛达成；任一存活即不合并。全部命中、无多余红，说明每条判据都有分辨力且互不重叠。

**经验（写给下一条线）**：②那份「全量扫描」用了 `grep -v "^./tests/"`，把 `tests/` 整个排掉了 ——
于是「链条上还有谁锁着旧行为」这一问只覆盖了生产代码。全量扫描若要支撑「改动面之外无人锁旧行为」
这类结论，必须连 `tests/` 一起扫（或明确声明「测试侧未扫」，由 C7 之类的手写清单兜底并标注其来源）。
