# 工程证据档 — Character-distill（2026-09-08 ~ 09-10）

> 用途：简历撰写与面试问答的**唯一事实源**。
> 规则：每条都必须能追到仓库里的代码、commit 或文档。凭印象的一律标 `⚠️ 待核`。
> 落库位置：本文件（`docs/engineering-evidence.md`）。

## 来源标注口径

| 标记 | 含义 |
|---|---|
| `✅ 代码核实` | 本人直接读过仓库代码确认 |
| `✅ 仓库文档` | 在仓库内文档中（含 commit hash 与结果文件引用），未自行复跑 |
| `⚠️ 待核` | 只有对话印象，仓库中无支撑，**不得写进简历** |

### 入库核对说明（2026-09-11）

本文自用户手上的 `ev.md` 提入库（`docs/`，非 `tests/perf/`）。入库时只做三件事，**未改任何数字、未去掉任何标记**：

1. **逐数字对出处**。每节末尾补 `**数字核对**` 块：数字 → 入库文件 / commit / 符号名；无法在仓库内落地的标 `⚠️ 待核`，与实测矛盾的标 `✗ 不符`。
2. **行号 → 符号名**。原文引用的 `AGENTS.md` 行号已随改动漂移（入库时逐条复核更正，见 §三 核对块）。本仓纪律是「引用代码一律用符号名」（`AGENTS.md` §四），本文照办。
3. **去标识**：与 `tests/perf/` 同口径（角色占位符 / 作品名移除 / `text_id` 保留）。**实测本文原本不含作品名、测试语料身份或任何 `text_id` / `card_id` 取值**——此条为零改动；账号名那一处见「入库时发现」第 3 条（后续修订时处理）。

> **后续修订（2026-09-11，同日）**：入库后按「证据档是简历唯一事实源，宁可少一个数字，不可留一个说不出出处的数字」再走一轮——**§五删去一组全仓无出处的具体数字**（影响面总数 + 重建成本），**§六的 1 处 `✗ 不符` 修复代码后转正**。两处均在本文件内留了处置说明；其余 `⚠️ 待核` 标记**一个未动**。

## 一、重试嵌套 — 上游故障被乘数放大成重试墙

| 项 | 内容 |
|---|---|
| **怎么发现** | 故障注入压测中出现**矛盾信号**：注入率从 10% 涨到 30%（2×），TTFT p95 却从 10.4s 涨到 62s（6×）。`raise` 是立即返回 500，理论上应让 ReAct 更快走到降级、延迟**下降**才对。矛盾本身触发了归因。 |
| **怎么定位** | 从 Jaeger 拉 p95 附近的 trace 看 span 耗时分布：62.3s 中 59.4s 落在**单个** `llm.chat_with_tools` span 内（`error=True`）。据此排除两个竞争解释——① 非多步空转（degraded trace 只有一个 `chat_with_tools`，无多次短 span）；② 非并发排队（span 间无 gap，PG 池峰值仅 17/30 从不封顶）。再以 C=1 零并发隔离复现：单次 26.06s，与并发无关。 |
| **根因** | `adapters/llm_adapter.py` 外层重试预算 3 次（非 429 退避 5s+10s）× OpenAI SDK 内置 `max_retries=2` = 单请求最多 **9 次 HTTP**。降级只在重试全部耗尽后触发。 |
| **修复** | 对齐 Google SRE 分层重试预算：SDK 归零消除嵌套乘法；抽 `_RetryBudget` 把三份重复重试循环收敛为单一控制点（次数 × 总时限双维，先到先弃）；退避夹逼 deadline；429 独立计数上限；per-attempt `create(timeout=min(ceiling, 剩余−margin))` 让 deadline 在 attempt 内也生效。 |
| **量化** | raise30 p95 **62.1s → 5.3s**；hang8 p95 **100.4s → 8.1s**；blackhole（零字节挂 30s）**斩在 ~6.5s**，ok 6/6；超 15s 阈值请求 **14 → 0**。 |
| **来源** | `✅ 仓库文档` `tests/perf/phase24_closure.md` §1 全表<br>`✅ 代码核实` `a41fcfd` `a609e62` `2bc3658` `8ad5f05` |

**面试要点**：这条的价值不在数字，在**排除法**。被问"你怎么确定不是并发排队"，答案是 PG 池峰值 17/30 从不封顶 + span 间无 gap + C=1 零并发下仍复现 26s。

**数字核对**

| 数字 | 出处 | 判定 |
|---|---|---|
| raise30 p95 62.1s → 5.3s | `tests/perf/phase24_closure.md` §1：`fb2_raise_30` p95 `62062` → `d1b_raise_30` p95 `5297` | ✅ |
| hang8 p95 100.4s → 8.1s | 同表：`hg_hang_30` `100438` → `d1b2_hang8000_30` `8077` | ✅ |
| blackhole 斩在 ~6.5s、ok 6/6 | 同表：`d1b2_blackhole_100` p50/p95 `6547` / `7436`，`rec=ok=6` | ✅ |
| 超 15s 阈值 14 → 0 | 同表 `over15s` 列：`fb2_raise_30` = 14 → D1a/D1b = 0 | ✅ |
| PG 池峰值 17/30 | `tests/perf/phase24_closure.md` §3 | ✅ |
| 重试预算 3 次 / SDK `max_retries=2` / 最多 9 次 HTTP | `adapters/llm_adapter.py` `_RetryBudget` 及模块上方注释（修复前/后对照就写在注释里） | ✅ 代码核实 |
| TTFT p95 10.4s（rate=10 档）、6× 增幅 | `.claude/sessions/2026-09-09-step4-press.md`（**gitignored**）。仓库内文档只有双峰结论与各档 p95，无 10.4s 这个点 | ⚠️ 待核 |
| 62.3s / 59.4s span 分解、26.06s C=1 隔离复现 | 同上 gitignored 会话（trace id、单发隔离实验记录都在那里） | ⚠️ 待核 |
| commits `a41fcfd` `a609e62` `2bc3658` `8ad5f05` | `git log` 逐个存在、标题与描述一致 | ✅ |

> `⚠️ 待核` 的成因是**这一档（缺陷 14 类）**：实验记录在 gitignored 的 `.claude/sessions/` 里。就「第三方能按引用查到」的标准，它等同于无出处——但**数字本身是当时实测的，不是编的**，故不删，只标。要转正需把该会话提到的 trace 快照 / 复现脚本提入库。

---

## 二、工具执行器线程弃船 — `fut.result(timeout)` 杀不掉已启动线程

| 项 | 内容 |
|---|---|
| **怎么发现** | 原本计划用「hang 时长 > 工具超时」的压测档来验证线程泄漏。**执行前先读代码验证前提，发现该 instrument 打不到目标**：故障注入打在决策轮 HTTP 上，重试耗尽后 `AgentLoop.run()` 直接返回 `degraded=True`，**根本进不到 `tools.execute`**；且该链路用的是 `search_memory`（`MEMORY_TIMEOUT=5s`）而非 `web_search`（15s）。按原计划跑会产出误导性的"未泄漏"。改用**阻塞工具探针**：让一次真实请求的 handler 阻塞超过其超时，直接观测线程。 |
| **怎么验证** | Jaeger span 父子关系 + 线程曲线 + app 日志三方互证。 |
| **根因** | `core/agent/tools.py` 外层 `fut.result(timeout=5)` 超时后 `shutdown(cancel_futures=True)` 只能取消**未启动**的 future；已启动的 handler 线程 Python 无法终止，继续挂到底层 embed client 自行放弃。超时不等式反了：内层 embed `timeout=8.0, max_retries=2` ≈ 最坏 24s > 外层工具超时 5s。 |
| **修复** | 对齐 gRPC deadline propagation：不靠上层强杀，改为**把超时预算逐层传到底层**，让它在外层超时前自行返回。实现上因中间隔着 mem0/chroma 两个第三方库（签名传不进去），收敛为 `ContextVar` scope + `_call_api` 单一收口点读取——比改四层函数签名覆盖更全。 |
| **量化** | 线程残留 **16.5s → 0.8s**；span 由 `execute_tool=5012ms` / 子 `embed.api=17540ms`（**子超父 12.5s**）收敛为 **3908 / 3907ms**（子在父内结束）。 |
| **读表警告** | 父 span 5012→3908ms **不是 embed 变快**：5012 是外层弃船时刻（精确命中 5s 超时），3908 是 embed 在 `timeout−1s=4s` 预算内自行收手的返回点。两次测的都是"工具调用何时解套"，不是吞吐提升。 |
| **来源** | `✅ 仓库文档` `tests/perf/phase24_closure.md` §1<br>`✅ 代码核实` `7834d6c` `e05da8b` `0a3eb2c` |

**面试要点**：`3908 / 3907` 这一对（子比父少 1ms）是"弃船彻底消失"的最强证据——不是"改善了"，是"没有了"。

**数字核对**

| 数字 | 出处 | 判定 |
|---|---|---|
| `MEMORY_TIMEOUT = 5` / `WEB_TIMEOUT = 15` | `core/agent/tools.py` 模块常量 | ✅ 代码核实 |
| `fut.result(timeout=5)` / `shutdown(cancel_futures=True)` | `core/agent/tools.py` `execute` 路径 | ✅ 代码核实 |
| 内层 embed `timeout=8.0, max_retries=2`（≈24s 上限） | `core/embeddings.py`：`AsyncOpenAI(..., timeout=8.0, max_retries=2)` | ✅ 代码核实 |
| 线程残留 16.5s → 0.8s | `tests/perf/phase24_closure.md` §1 D2 表（探针 = `leak_probe`，mock `search_memory`） | ✅ |
| `5012 / 17540` → `3908 / 3907` | 同表 `Jaeger execute_tool / 子 embed.api` 列 | ✅ |
| commits `7834d6c` `e05da8b` `0a3eb2c` | `git log` 逐个存在 | ✅ |

> 与 §一 不同，**本节数字全部落在入库文件里**——`phase24_closure.md` 是 tracked 的先例档。

---

## 三、`thinking` 参数写错 — 明确要求关思考，实际一直按 high effort 烧

| 项 | 内容 |
|---|---|
| **怎么发现** | **不是排查出来的，是测量方法本身暴露的**。原目标是回答"`max_tokens: 4096` 够不够"，为此建测量闸统计单片 `out_tokens`。结果测出 `out_tokens` **顶满上限而 `content` 为空**——正常情况不该出现这种组合。 |
| **根因** | `adapters/llm_adapter.py` 四处硬编码 `extra_body={"enable_thinking": False}`，那是 **Qwen 方言**；DeepSeek 不认该字段、静默忽略，而 DeepSeek-V4-Pro 思考模式**默认开启**。推理内容吃光 `max_tokens` 预算 → `content` 为空 + `finish_reason='length'` → 落空串。 |
| **修复** | 建**供应商方言层**：由 `base_url`/`model` 解析供应商，表达的是**意图**（"关闭思考"）而非 payload；未知供应商不发任何 `extra_body`（安全默认）。四处调用点改为调同一解析器，加第三个供应商只改一张表。 |
| **量化（n=14，同语料同提示词前后对照）** | `out_tokens` p50 **8191 → 1245**；单次耗时 **12.0–160.0s → 1.4–31.3s**；超生产 deadline(60s) **11/14 → 0/14**；空正文片 **3 → 0**；tokens/正文字符 **3.85 → 0.62** |
| **最硬的一条** | `tok/char 3.85 → 0.62` 与本仓自己的 `_estimate_tokens = int(len*0.6)` 吻合。**"空正文消失"不足以证明思考关掉了（可能只是采样波动），两个互相独立的量对上才立得住。** |
| **来源** | `✅ 仓库文档` `AGENTS.md` §二基线表（`### 二、模型与参数现值` 下基线表）、§四纪律案例（`### 四、验证纪律` 下「『X 消失了』不足以证明 Y 修好了」条）<br>`✅ 代码核实` `f2dfd23` |

**数字核对**

| 数字 | 出处 | 判定 |
|---|---|---|
| 全套 before/after（p50、max、撞上限、空正文、耗时、tok/char） | `tests/perf/out_maplen.prefix.json`（修复前）/ `out_maplen.json`（修复后）——**已入库**，可复算；复现口径见 `tests/perf/thinking_budget_evidence.md` §4 | ✅ |
| `_estimate_tokens = int(len*0.6)` | `core/distiller.py` `Distiller._estimate_tokens` | ✅ 代码核实 |
| 四处 Qwen 方言硬编码（修复前形态） | 现为 `adapters/llm_adapter.py` `_THINKING_DISABLED` / `_DIALECT_QWEN`；修复前是该字面量的四处硬编码 | ✅ 代码核实 |
| commit `f2dfd23` | `git log` 存在 | ✅ |

> **口径显式对照（本仓纪律要求，`AGENTS.md` §四「聚合统计必须写明口径」）**
> 本节表格里的 `out_tokens` **p50** 是 14 条**合并后**的 nearest-rank **上**中位（`sorted(outs)[n//2]`，n=14 → 下标 7）→ **8191 / 1245**。
> 产物 JSON 里**同名但不是同一个东西**的字段是 `summary[].out_tokens_p50` —— 它按档（5000 / 6000 字符）**分组**、用 `int(round(0.5*(n-1)))` 取**下**中位，合并前 **8192 / 6079**、合并后 **1446 / 1177**。
> **文档表格名「`out_tokens` p50」≠ 产物字段名 `summary[].out_tokens_p50`**；混用两者会算出 6487 / 1205，**看着像数字对不上**。复算时认准本节这一行，出处见 `tests/perf/thinking_budget_evidence.md` §4 与 `AGENTS.md` §四。
>
> 另两处易混口径同此理：`tokens/正文字符` = `sum(out_tokens)/sum(out_chars)` 且**仅计 `out_chars>0`** 的记录（不是逐条比值再平均）；`撞上限 7 → 0` = `count(clipped_by_probe_cap)`。

---

## 四、`finish_reason` 从不检查 — 截断响应被当成功返回

| 项 | 内容 |
|---|---|
| **怎么发现** | 全仓 grep：`finish_reason` **只在 `tests/perf/mock_llm_server.py` 里出现，生产代码零引用**。四个响应提取点各自 `content or ""` 直接返回。 |
| **根因** | 缺响应校验层。截断（`length`）、内容过滤（`content_filter`）、资源不足（`insufficient_system_resource`）三类未完成终态，全部被当成功。 |
| **准确表述（重要）** | 后果是**截断响应被当成功返回、静默降质**。<br>❌ 不要写成"半截结果落库被复用"——截断片落的是**空串**，续跑第二道门（非空校验）拦得住，不会被复用。这个说法一问就破。 |
| **修复** | `_check_finish_reason` 单一裁决点：三类未完成终态抛 `IncompleteResponseError`（带 `finish_reason` 字段，三类处置可辨：抬预算 / 改输入 / 可重试）；陌生值 WARN 放行（供应商语义不一）。错误边界收敛为 `llm_error_payload(exc)`，路由层不 import 异常类；HTTP 状态码按 `finish_reason` 分（`content_filter`→400、`length`→502、资源不足→503），不再一律 500。 |
| **后续（2026-09-11）** | 本层引入过一个**回归**并同轮修掉：`chat` 对 `length` 先抛后，`core/distiller.py` 里那条**截断感知的重修自愈环**主触发路径不可达（半截文本永远到不了 `_parse_json_with_retry`）。修法是让异常**携带**已生成正文（`content` 属性，不进 message）并新增边界出口 `incomplete_response_info`（与 `llm_error_payload` 同构，core 侧仍不 import 异常类）——**边界零泄漏不变**。这不是新增失败，是把"静默降质"换成"显式报错"后，在保持诚实的前提下把可用性拿回来；细节见 `AGENTS.md` 缺陷 2 的接回段。 |
| **量化** | 属"缺陷消除"类，无 before/after 数字。**别硬凑。**可讲的是设计：三类可辨 + 边界零泄漏（`core/` `web/` `storage/` 对 `IncompleteResponseError` 零命中，且有源文件级边界锁测试，重现即红）。 |
| **来源** | `✅ 代码核实` `64d2d14` `d068242` `b77c0d5` `e949a94` `2cac39c` |

**数字核对**

| 断言 | 出处 | 判定 |
|---|---|---|
| 修复前生产代码对 `finish_reason` 零引用 | `git grep finish_reason 64d2d14^ -- .`，除 `tests/perf/mock_llm_server.py` 外**零命中** | ✅ 代码核实 |
| `core/` `web/` `storage/` 对 `IncompleteResponseError` 零命中 | `git grep -l IncompleteResponseError` 命中 `adapters/llm_adapter.py` + 4 个测试（`test_llm_adapter_finish_reason` / `test_chat_stream_error` / `test_chat_http_status` / `test_distiller_truncation_selfheal`）+ `AGENTS.md` + 本档自身；**`core/` `web/` `storage/` 实测 0** | ✅ 代码核实 |
| 边界锁测试（重现即红） | `tests/test_llm_adapter_finish_reason.py`（另有 `test_chat_http_status.py` / `test_chat_stream_error.py` 锁路由层状态码） | ✅ |
| `content_filter`→400 / `length`→502 / 资源不足→503 | `web/routers/chat.py` `_INCOMPLETE_HTTP_STATUS` 映射表 | ✅ 代码核实 |
| `_check_finish_reason` / `IncompleteResponseError` / `llm_error_payload` | `adapters/llm_adapter.py` 三符号均存在 | ✅ 代码核实 |
| commits `64d2d14` `d068242` `b77c0d5` `e949a94` `2cac39c` | `git log` 逐个存在 | ✅ |

---

## 五、向量索引升维后静默失效 — 加载成功但检索恒空

| 项 | 内容 |
|---|---|
| **怎么发现** | MCP 开发中验证检索时发现 scene 返回空，追查是"真无匹配"还是别的原因。 |
| **根因** | embedder 从 384 维升到 1024 维后，`core/rag.py` `load_existing` **只判 `count()>0` 不验维度**，旧集合被正常"加载成功"；查询时维度不符报错，又被**两层宽 except** 吞成 `[]`。结果：加载成功、查询恒空、日志无痕、永不自动恢复。与"真的没匹配到"不可区分。 |
| **影响面** | **12 个 `text_` 集合**已确认是旧维度（384 dim）且有存活卡引用 → **19 张卡 / 2 个用户**受影响；web 单卡 chat 与 MCP 检索走同一条加载路径，故两处同受影响。全库集合总数与其中 384 维的个数：**当时实测过，但原始产物未入库，本档不引用这两个数字**。 |
| **修复** | `load_existing` 三分支语义：无数据→`False`；真错误→记日志+`False`（不再静默 pass）；维度不符→抛 `CollectionUnusableError`（**绝不返回 True 制造"已装载但恒空"**）。`query`/`query_with_emotion` 区分"失败"与"真无匹配"（空 documents 本就不抛，仍返 `[]`；真异常才抛）。四个调用点统一**降级不自动重建**（捕获顺序必须在宽 `except` 之前——它是 `RuntimeError` 子类）。 |
| **数据修复** | 用可重跑脚本重建了上面那 **12 个**旧维度集合（**脚本在库**：`scripts/rebuild_384_collections.py`，`a1982f0`，plan/rebuild 双模式 + 断点续跑）。分片写入量、真实 embed 次数、缓存命中数、耗时与成本：**当时实测，但其运行产物落在 gitignored 的 `data/eval_scratch/rebuild_384/run_*.jsonl`，本档不引用具体数字**（唯一的例外是集合数 12，它由 `docs/384-dim-stale-collections.md` 支撑）。 |
| **来源** | `✅ 代码核实` `4a971d1` `1d19beb` `a1982f0` `65950e1` |

**面试要点**：这条和上面的线程弃船、`finish_reason` 是**同一类病的三次显形——失败被吞成正常返回**。能把三个不同模块的问题归到同一个模式，比修好三个 bug 更有说服力。

**数字核对**

| 数字 | 出处 | 判定 |
|---|---|---|
| 12 个 `text_id` → **19 张卡 / 2 用户** | `docs/384-dim-stale-collections.md`（tracked） | ✅ 仓库文档 |
| `load_existing` 三分支 / `CollectionUnusableError` / `_peek_dimension` | `core/rag.py` 三符号均存在 | ✅ 代码核实 |
| 四个调用点统一降级不自动重建 | `web/routers/group.py` ×2（`_rebuild_group_session` / 建群）+ `mcp_server/server.py` `_rag_for_text_id` 各有 `except CollectionUnusableError`；`core/indexing_service.py` 经 `RAG build failed (degraded)`→None | ✅ 代码核实 |
| commits `4a971d1` `1d19beb` `a1982f0` `65950e1` | `git log` 逐个存在 | ✅ |

> **本节已按「宁可少一个数字，不可留一个说不出出处的数字」处置（2026-09-11）。**
>
> 原列出的两组数字来源不同，分开说：
>
> - **影响面总数**（全库 chroma 集合总数、其中 384 维的个数）——**产数脚本（只读审计）未入库**，
>   仓库里连它的名字都没有。这组数字**任何地方都查不到**，只能重跑审计。
> - **重建成本数字**（重建集合数 / 分片写入量 / 真实 embed 次数 / 缓存命中数 / 耗时 / 金额）——
>   **重建脚本本身是在库的**（`scripts/rebuild_384_collections.py`，`a1982f0`，§五 来源行已引），
>   缺的是**运行产物**：每次结果 append 到 `data/eval_scratch/rebuild_384/run_*.jsonl`，而该路径
>   **gitignored**。所以成本数字属缺陷 14 类（数字指向 gitignored 产物），补入库产物即可转正。
>
> 两组都按「宁可少一个数字，不可留一个说不出出处的数字」**从本节删除**，改为定性表述并注明
> 「当时实测，原始产物未入库」。**只保留**有 tracked 兜底的「12 → 19」
> （`docs/384-dim-stale-collections.md`）。
>
> **与其余 `⚠️ 待核` 的区别**：§一（TTFT 10.4s、62.3s/59.4s span 分解）、§七（「六条」计数
> 标签）、§九（judge κ≈0）这几条的 `⚠️ 待核` **保持不动**——它们在 tracked 仓库里同样无支撑，
> 但**至少在 gitignored 的 `.claude/sessions/` 里有据可查**，性质是「一手材料没入库」，补入库
> 即可转正。本节被删的两组里，**重建成本数字**同属这一类（产物在 gitignored 的 `run_*.jsonl`）；
> 只有**影响面总数**更彻底——连 gitignored 记录都没有，且产数脚本从未入库。

---

## 六、三次越权 — 从一条已知项 grep 出一类模式

| 项 | 内容 |
|---|---|
| **怎么发现** | **不是撞上的，是扫出来的。** 从缺陷表一条已知项（`web/routers/text.py` 上传任务状态注入 `user` 却全函数未使用）出发，用 AST 扫 `web/routers/*`：找出所有 `Depends(get_current_user)` 注入但函数体从不引用 `user` 的端点，共 9 处，逐个判定真越权还是良性（只是登录门 + 读全局/公开数据）。 |
| **为什么静态检查抓不到** | `Depends` 消费了该参数，IDE 的"未使用参数"告警被绕过。 |
| **扫出的真越权** | ① `text.py` 上传任务状态：任何登录用户拿到 `task_id` 即可读他人任务；② `voice.py` `voice_synthesize`：`card_id` → 参考音频全程无归属校验（**铁证**：同文件兄弟端点 `preview_ref_audio` 对同一资源是查 `user_id` 的）；③ `voice.py` `preview_audio`：自定义音色分支直接从磁盘取文件返回（而 `/list` 只列本人、删除/上传都查属主 → 按设计是私有的）。 |
| **修复口径** | fail closed（无法确定归属即拒，不因"无从校验"放行）；返回 **404 而非 403**——403 泄漏资源存在性，等于给攻击者一个枚举预言机；返回前剥离内部字段。全部配变异验证（删校验 → 越权断言必须变红）。 |
| **量化** | 判断质量类，**无数字，别凑**。可讲的是方法：一条已知项 → 一类模式 → 9 处候选 → 3 处真越权 + 6 处判定良性。 |
| **来源** | `✅ 代码核实` `a0a3a4d` `cdea9e5` `45895f4` |

**数字核对**

| 断言 | 出处 | 判定 |
|---|---|---|
| 9 处候选 → 3 真越权 + 6 良性（含 6 处良性端点名单） | `AGENTS.md` 缺陷 9；边界锁 `tests/test_auth_param_used.py`（固化这次 AST 扫描 + 白名单，新增同类端点自动变红） | ✅ 仓库文档 |
| 「返回 **404** 而非 403」（三端点统一） | `voice.py` `voice_synthesize` / `preview_audio` 为 404；`text.py` `get_upload_task_status` 的 403 已于 2026-09-11 改为 404（与「不存在」同判 404、同一条文案），边界锁 `tests/test_upload_task_ownership.py` | ✅（2026-09-11 修复后转正） |
| 「无数字，别凑」 | 与实测一致：本节无前后对照数字 | ✅ |
| commits `a0a3a4d` `cdea9e5` `45895f4` | `git log` 逐个存在 | ✅ |

> **`✗ 不符` 已修复（2026-09-11）**：入库核对时发现的那处矛盾已按 `AGENTS.md` §四 口径（授权失败一律 404）改齐。
> - `voice.py` 两处（`voice_synthesize` / `preview_audio`）→ **404**（原本即 404）
> - `text.py` 的 `get_upload_task_status` → **403 改为 404**（`a0a3a4d` 引入的 403，不再是漏网）。与「任务不存在」同判 404 且**同一条文案**，两条分支不可区分，不靠状态码枚举 `task_id`；`user_id` 缺省也拒（fail closed）。回归锁：`tests/test_upload_task_ownership.py`（含「非属主与不存在同码同文案」一条），变异验证：改回 403 即 3 条用例转红。
>
> 附带扫过 `a0a3a4d` 之后引入的归属校验点：**无其它 403 漏网**（全仓 `HTTPException(403)` 逐行 blame，最新的一处归属型 403 早于 `a0a3a4d`）。但 `web/routers/distill.py` 有三个同型端点（`get_distill_task_status` L940 / `cancel_distill_task` L969 / `distill_task_params` L997：404「Task not found」+ 403「无权…此任务」）与 `core/trash_service.py` 三处（L75/98/127），形状与本次修的完全一致、**不在缺陷 13 记的「存量约 38 处」名单内**——**只列不修**，交缺陷 13 批量翻新时一并裁定。

---

## 七、断点续跑与任务持久化

| 项 | 内容 |
|---|---|
| **问题** | 蒸馏 MapReduce 的 map 中间结果只在内存，任务状态是内存 dict。进程崩溃 → 结果全丢、从零重跑（用户自带 key，白付已消耗的调用）、前端轮询到不存在的 `task_id`。 |
| **设计要点（比"实现了续跑"值钱）** | ① **DB 为唯一真相源**，内存只作写缓存；启动 reconcile 把孤儿 `running` 置 `interrupted`——否则"没有内存记录"与"任务不存在"不可区分。<br>② **两级校验防错位复用**：任务级比对切分参数与全文指纹（不符整批作废，不进分片门）；分片级三重门（行存在 + 形状合法 + 分片原文 sha256 匹配）。没有指纹这道，用户改稿或调 `chunk_size` 会**静默复用错位分片，产出看似正常实则错乱的角色卡**——比重烧钱严重得多且不可见。<br>③ **reduce 整体重跑不分批**：分批只省最后一两次调用，却引入"部分 reduce 如何合并"的一致性问题。权衡写进注释，标明不是遗漏。<br>④ **手动触发不自动续跑**：开机自动续跑会把所有 interrupted 任务同时推给刚恢复的上游。<br>⑤ **按用户并发闸**：进程内同步预留位挡 TOCTOU（DB 查+插隔着 `await` 会交错，单靠 DB 复核挡不住）+ 带时效窗的 DB 复核挡跨重启幽灵行。天花板写进注释：多 worker 时必须升级为部分唯一索引。 |
| **量化** | 续跑命中片 **零 LLM 调用**；六条验收断言 + 两条防"空过"对照（正向：门全过时分片行真被读成候选）。<br>**注意**：分流阈值 15 万 token（≈25 万字符）以下走长上下文单次路径，**断点续跑只在分片路径生效**。 |
| **来源** | `✅ 代码核实` `19ed51f` `43b621f` `59472f6` `fe0ec16` `e5bb4c6` `101ec3f` `afd11a3` `de9d1f4` `fa7e95d` |

**数字核对**

| 断言 | 出处 | 判定 |
|---|---|---|
| DB 为唯一真相源 + 启动 reconcile 置 `interrupted` | `storage/base.py` `mark_interrupted_distills`（boot reconcile）/ `find_interrupted_distill`（续跑发现） | ✅ 代码核实 |
| 分片级 sha256 指纹门（防错位复用） | `core/distiller.py` `text_fingerprint`（任务级与分片级共用，避免两份漂移）+ `_resume_hit`（三重门） | ✅ 代码核实 |
| 续跑命中片**零 LLM 调用** | `tests/test_distill_resume.py::TestResumeSavesCalls::test_full_hit_zero_map_calls`（tracked） | ✅ |
| 六条验收断言 + 两条防"空过"对照 | 断言落在 `tests/test_distill_resume.py`（tracked：`TestResumeHitDoors` 三条 + `TestResumeSavesCalls` / `TestResumeIdempotent` / `TestSecondGateRerunsBadChunk` / `TestFailedChunkNotCheckpointed` / `TestMainPathUnchanged` 各一条，共 **8 个用例**）；「六条」这个**计数标签**出自 `.claude/sessions/2026-09-10-distill-dbtruth-closeout.md`（**gitignored**） | ✅（用例）/ ⚠️ 待核（"六条"的计数口径） |
| 分流阈值 15 万 token ≈ 25 万字符 | `config.yaml` `longctx_threshold: 150000`；25 万字符 = 150000 / 0.6，与 `_estimate_tokens = int(len*0.6)` 同口径 | ✅ 代码核实 |
| commits（10 个） | `git log` 逐个存在 | ✅ |

> 「六条」与 tracked 测试文件的 8 个用例对不上，是因为**验收是人工六步、测试是落地后的等价覆盖**（会话档 L205 明说「已用单测覆盖其逻辑等价」）。数字本身没错，错的是把它读成"六条断言在测试里"。

---

## 八、MCP Server

| 项 | 内容 |
|---|---|
| **内容** | 把 `AgentToolkit` 三个检索工具暴露为标准 MCP server（stdio 传输）。 |
| **设计要点** | `card_id` **只注入在 MCP 协议适配层**（对 `get_schemas()` 返回值深拷贝后注入），**不动 `core/agent/tools.py`**——因为同一份 schema 被生产 ReAct 循环喂给路由器 LLM 做 function calling，加一个 required 的 `card_id` 会让路由器每次编造它，轻则调用失败率上升，重则退化到 degraded 路径。 |
| **安全口径** | 不开 HTTP/SSE：stdio 下进程边界即鉴权；在 HTTP 上挂静态共享 token 反而更弱（无调用方身份、不能吊销、不做资源归属校验）。**被问"怎么鉴权"时这是威胁建模，不是"我加了个 token"。** |
| **踩到的坑** | `mcp` 1.x stdio 子进程 env 只取白名单、不继承父环境，客户端须 `env=dict(os.environ)` 显式透传。 |
| **来源** | `✅ 代码核实` `3be4a30` `cb60ba6` `12ed8e8` `3f3d32f` `a6db31d` |

**数字核对**

| 断言 | 出处 | 判定 |
|---|---|---|
| `card_id` 在协议适配层 deepcopy 后注入、`core/agent/tools.py` 不动 | `mcp_server/server.py` `_tool_specs` / `call_tool` / `_toolkit_for`；该次改动 diff 只碰 `mcp_server/` | ✅ 代码核实 |
| stdio env 白名单坑 | `mcp_server/client_demo.py` `StdioServerParameters(..., env=dict(os.environ))` | ✅ 代码核实 |
| commits `3be4a30` `cb60ba6` `12ed8e8` `3f3d32f` `a6db31d` | `git log` 逐个存在 | ✅ |

---

## 九、评测资产

| 项 | 内容 | 来源 |
|---|---|---|
| 注入对抗样本集 | **60 条**（`tests/eval/injection/`：upload / card / chat 三链路各 20 条） | `✅ 代码核实`（本人计数） |
| Agent 工具调用评测集 | **40 条**（`tests/eval/agent_eval_cases.jsonl`） | `✅ 代码核实`（本人计数） |
| LLM-as-judge 证伪 | κ≈0、位次偏见、跨代模型不可复现 → 判定不可作质量代理 | `⚠️ 待核` |

> **`⚠️ 待核` 说明**：judge 证伪这条在本仓库中**找不到支撑**（grep `judge`/`kappa` 只命中 `card_guard` 等无关模块）。它可能属于另一条工作线或只存在于对话中。**在补到一手材料之前，不要写进简历**——这正是本档存在的理由。
> （入库核对补注：`.claude/sessions/2026-09-08-judge-anchors-v1.md` 里确有 qwen-max 主臂 κ≈0 的记录，但该目录 **gitignored**，且这只是本人当时的过程记录、**不是**用户所说的「一手材料」。**标记不动，判据不变**。）

**数字核对**

| 数字 | 出处 | 判定 |
|---|---|---|
| 注入集 **60 条**（20 / 20 / 20） | 入库时实测：`tests/eval/injection/upload_corpus_samples.json` **20**、`card_market_samples.json` **20**、`chat_conv_samples.json` **20**。（档里写的「upload / card / chat 三链路」指的是这三个文件的链路，**不是三个子目录**——该目录下无子目录） | ✅ |
| Agent 评测集 **40 条** | 入库时实测：`tests/eval/agent_eval_cases.jsonl` = **40** 行 | ✅ |
| judge κ≈0 | 无仓库内支撑 | `⚠️ 待核`（保留） |

---

## 十、如实记录的空线索（不是失败，是诚实）

压测**推翻了自己的先验假设**：

原设想"连接池 30 vs 线程池 200 配置错配"是主瓶颈。实测：PG 池峰值 **17/30 从不封顶**、内存 **276MiB**（上限 768MB）、线程 **241→257**，全程无资源绑定。**瓶颈是延迟不是资源。**

另一条方法论结论：**TTFT 呈双峰分布**——健康峰 3.6–4.7s / 降级峰（修复前）26–133s。聚合 P95 是两峰混叠的误导值，取决于故障混合比例而非系统真实水位，**必须分峰报**。修复后三类故障 p95 全部收敛到 5.3–8.1s，与健康峰拉到同量级，跨峰聚合才有意义。

`✅ 仓库文档` `tests/perf/phase24_closure.md` §2 §3

**数字核对**

| 数字 | 出处 | 判定 |
|---|---|---|
| PG 池峰值 17/30、内存 276 MiB（limit 768m）、线程 241→257 | `tests/perf/phase24_closure.md` §3「空线索如实写」表 | ✅ |
| 双峰：健康峰 3.6–4.7s / 降级峰 26–133s | `tests/perf/phase24_closure.md` §2 | ✅ |
| 修复后三类故障 p95 收敛到 5.3–8.1s | 同 §2（对应 §1 表：raise30 5297 / hang8 8077 / blackhole 7436） | ✅ |

---

## 十一、贯穿全程的方法论（面试最该讲的）

1. **失败必须可见，还要可辨。** 三次同类缺陷（线程弃船、维度不符、截断响应）都是"失败被吞成正常返回"。修完一处不够——`CollectionUnusableError` 被上层宽 `except` 吞成通用错误时，"确定性不可用"和"瞬时故障"混成一条日志，看日志分不出该重建还是该等。
2. **修复必须做变异验证。** 改坏它，指定测试必须变红。案例：兜底调用从 `finally` 删掉，14 个测试仍全绿 = 那条接线根本没被覆盖。
3. **测试通过 ≠ 命题成立**，可能只是那条路径没被走到。案例：分片续跑逻辑落地后，因短文本恒走长上下文路径，一直未被真实执行。
4. **"X 消失了"不足以证明"Y 修好了"**，要找独立可交叉验证的指标。案例见 §三。
5. **先读代码验证前提，再决定要不要跑。** 案例见 §二——按原计划跑会产出误导性的"未泄漏"。
6. **mock 的形态 ≠ 被测对象的形态。** 案例：mock 回 canned JSON，导致误判 map 输出是 JSON，实际是自然语言，进而把"结构合法校验"写进了 spec（实际只能做非空校验）。
7. **基线数字现跑现取**，禁止引用上一轮或凭记忆。案例：本档整理时差点把已测完的 thinking 数据当成缺口去重测。

**数字核对**

| 条目 | 出处 | 判定 |
|---|---|---|
| 案例 2（`finally` 删掉、14 个测试仍全绿） | `AGENTS.md` §四「修复必须做变异验证」条，含后续补 `TestA2Wiring` 的 commit `9d2a9e4` | ✅ 仓库文档 |
| 案例 3（分片续跑长期未被走到） | `AGENTS.md` §四「测试通过 ≠ 命题成立」条 | ✅ 仓库文档 |
| 案例 6（mock canned JSON 误判 map 输出形态） | `AGENTS.md` §四「mock 的形态 ≠ 被测对象的形态」条（并指出真判据在 `core/distiller.py` 的 `_map_system_prompt` / `_map_user_prompt` 消费侧） | ✅ 仓库文档 |
| 案例 1 / 4 / 5 | 分别指 §三 / §一 / §二，判定同上 | ✅ |
| 案例 7 | 过程自述，无数字 | — |

---

## 入库时发现（只列不修）

本次入库核对过程中发现、**不属于本次改动范围**的问题。按本仓约束「发现未覆盖的问题：只列不修，停下来报」，此处只记录、未动代码与台账。

1. ~~**`text.py` 越权修复用了 403，与档/台账的「统一 404」口径矛盾**（详见 §六 `✗ 不符`）。~~
   → **已处置（2026-09-11）**：`a0a3a4d` 给 `get_upload_task_status` 加的是 `HTTPException(403)`；`AGENTS.md` 缺陷 9 正文却写「统一 fail closed + 非属主 **404**」。当时**只列不修**，后续单独立项把代码改成 404（与「不存在」同判、同一条文案），并补回归；档内 §六 该行随之转 ✅。
   **本条以外还扫到同型未修的**：`web/routers/distill.py` 三处（L940 / L969 / L997）与 `core/trash_service.py` 三处（L75/98/127）—— 归属型 403、与本次修的完全同形，**均早于 `a0a3a4d` 引入**，故不在本次范围内；但**也都不在缺陷 13 的「存量约 38 处」名单里**，属漏网存量，**只列不修**，交缺陷 13 一并处理。

2. ~~**§五「数据修复」的整组数字全仓无出处**，重建脚本也不在仓库。~~
   → **已处置（2026-09-11）**：两组数字来源不同（详见 §五 尾注）——**影响面总数**的审计脚本未入库、数字任何地方都查不到；**重建成本数字**的脚本在库（`scripts/rebuild_384_collections.py`）但运行产物 gitignored。已从 §五 **删除具体数字**、改为定性表述，只保留有 `docs/384-dim-stale-collections.md` 兜底的「12 → 19」。要恢复：前者重跑审计，后者补入库 `run_*.jsonl`。

3. **去标识核对结果**：入库时实测本文**不含**真人同人作品名或 `text_id` / `card_id` 取值（逐项 grep 零命中），故入库轮为零改动。`tests/perf/` 那套占位符（`角色A`…`角色D`）不适用于本文——它谈的是代码路径与统计量，不涉及测试语料身份。
   → **补一处（2026-09-11）**：§五 那行原带「（主账号 15 全 2026-05 + testadmin 4）」的**账号名拆解**，改这一行时已**去掉账号名、只留「2 个用户」**。其源头 `docs/384-dim-stale-collections.md` 带同一账号名——已在续轮一并去标识（见下方第 5 条）。

4. **行号引用已漂移**：原文引用的 `AGENTS.md` §二（"L103–109"）现为 `### 二、模型与参数现值` 下的基线表（数据行仍是 103–109，表头在其上两行）；「§四纪律案例（L195）」**已不在该行**——该案例现位于 `### 四、验证纪律`（`AGENTS.md` §四「『X 消失了』不足以证明 Y 修好了」条，现约 L232）。已按本仓纪律（引用用符号名）改指小节名。**未改任何数字，只改了指针。**

5. **同型未覆盖（2026-09-11 续轮：已处置）**：
   - `tests/test_card_guard.py` 里还有**另一个语料角色名**（与本轮点名的两个同属作品人名）——仍**只列不修**，待裁定。
   - **账号名（本名）散在 8 个 tracked 文件**，另 `.gitignore` 有两行以它为名的本地脚本文件名。续轮处置：文档/注释/打印标签**去标识**；功能字面量（`scripts/e2e_verify.py` 的登录名、`scripts/migrate_data.py` 的 SQL 查询名）**改走环境变量**（缺失即 `SystemExit`，不留默认值）。`.gitignore` 两行**不动**——改模式会解除对本地数据导出脚本的 ignore，风险高于收益。复扫后 tracked 文件里非品牌命中**仅剩 `.gitignore` 那两行**。
   - **固定测试口令字面量原散在 13 个 tracked 文件**（`scripts/`、`web/frontend/e2e/`、`tests/perf/`）。续轮**全 13 处一律 env 化**（统一变量名 `TEST_PASSWORD`，Python `os.environ[...]` / JS `process.env.TEST_PASSWORD`，缺变量即 `SystemExit` / `throw`，不留默认值；`scripts/e2e_verify.py` 的登录名走 `TEST_USERNAME`、`scripts/migrate_data.py` 的目标账号名走 `TARGET_USERNAME`）——**复扫口令字面量零命中**。变量名清单见 `.env.example` 末尾「本地验证脚本用」段（只有变量名、无值）。该口令在 git 历史中自 2026-06-25 起出现于 12 个 commit——**改文件不等于清除**，历史处置方案见 session 记录（待裁决；倾向「作废/轮换口令、历史不动」）。

---

## 待办

- [ ] judge 证伪补一手材料，或从简历中移除（`⚠️ 待核` 保持）
- [x] ~~`e2e/scratch/` 是 gitignored，thinking 的原始 JSON 不在仓库里——已摘要进 `AGENTS.md` §二，但原始数据无代码支撑，考虑把探针脚本入库（与 `leak_probe.py` 同处理）~~ → **已处置**（2026-09-11）：探针脚本 + 原始产物已提入库，落点 `tests/perf/`（commit `5ba7b9e`，语料去标识与占位符硬门见 `bc19511`），索引见 `AGENTS.md` §五「思考参数证据档」
- [ ] 若要恢复 §五 被删的数字：**影响面总数**需重跑审计并把审计脚本入库（该脚本从未入库）；**重建成本**只需补入库 `data/eval_scratch/rebuild_384/run_*.jsonl`（重建脚本 `scripts/rebuild_384_collections.py` 已在库）。根因是「产物没入库」，只补数字不算转正
- [x] ~~§六 的 403/404 口径矛盾裁定（改代码 or 改台账）~~ → **已裁定并修复**（2026-09-11）：改代码（`text.py` 403→404，与 voice.py 同口径），补回归 + 变异验证
