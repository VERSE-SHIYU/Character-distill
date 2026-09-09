# 阶段 ②④ 收口报告 — 压测与容量归因

- 日期：2026-09-09
- 范围：决策轮故障注入 rig（D1a/D1b）+ 工具执行器线程残留（D2）的发现 / 修复 / 复压
- 代码支撑：`tests/perf/`（rig + mock + 探针，入库）；结果 JSON 与编排器在 `data/eval_scratch/s4/`（gitignored）
- 可复现：`tests/perf/README.md` 前置 + 档参全表

---

## 1. 主线发现 + before/after 全表

故障注入只在 mock 决策轮（tools-bearing `chat_with_tools`）打，mock 裁决按 messages-body key 记忆，压测会话号内嵌消息防撞裁决。客户端锚点：TTFT p50/p95，超阈 15s。

**发现一：重试嵌套 — 上游故障被乘数放大成重试墙。**
D1a 前同一决策轮在 SDK 层与 adapter 层叠加重试，一次上游故障放大成多次 HTTP（hang8 档实测单决策 ~9 attempts × 8s ≈ 72s+，直接解释 p95 100s / p99 133s）。D1a 撤 SDK 嵌套重试（`a41fcfd`），`_RetryBudget` 在 adapter 单点封顶，退避夹逼 deadline、429 独立上限（`a609e62`）。

**发现二：线程 abandon — `fut.result(timeout)` 只能放弃 future，杀不掉已启动的线程。**
D2 before：被放弃的工具 handler 线程继续挂到底层 embed client（读超时 8s×~3）自行放弃为止，请求 done 后线程保持 baseline+1 长达 16.5s（Jaeger：execute_tool=5012ms，其子 embed.api=17540ms，子超父 ~12.5s）。D2 把 embed deadline scope 开进 executor worker（`7834d6c` + `e05da8b`），库内 embed 在 `fut.result(timeout)` 放弃它之前自行结束。

### D1 决策轮故障 — before / D1a / D1b

| 档（mock） | 阶段 | rec | ok | p50 | p95 | p99 | over15s |
|----|------|----|----|-----|-----|-----|--------|
| raise rate30 | **before** `fb2_raise_30` | 40 | 40 | 4438 | **62062** | 62093 | 14 |
| raise rate30 | D1a `d1a_raise_30` | 60 | 60 | 4358 | **5452** | 5483 | 0 |
| raise rate30 | D1b `d1b_raise_30` | 60 | 60 | 4014 | **5297** | 5593 | 0 |
| hang 8s rate30 | **before** `hg_hang_30` | 60 | 60 | 3702 | **100438** | 133468 | 18 |
| hang 8s rate30 | D1a `d1a_hang8000_30` | 58 | 50 | 3735 | **68813** | 76172 | 6 |
| hang 8s rate30 | D1b `d1b2_hang8000_30` | 60 | 60 | 4782 | **8077** | 8390 | 0 |
| hang 20s rate30 | D1a `d1a_hang20000_30` | 30 | 30 | 3766 | **23125** | 23156 | 7 |
| blackhole rate100 | D1b `d1b2_blackhole_100` | 6 | 6 | 6547 | **7436** | 7436 | 0 |

- **raise**：快速失败（attempt1 立即 500 → 退避 1s → attempt2 立即 500 → non429 cap），degrade 成本 ~1s。p95 62s → 5.3s，D1a→D1b 不回归。
- **hang8**：D1a 只能把重试次数封顶，单次 create 阻塞仍以 HTTP 返回为界 → p95 仍 68.8s、8 个 ttft_timeout。**D1b per-attempt `create(timeout=min(ceiling, 剩余−margin))`（`2bc3658`）才是收尾**：决策 5s ceiling 生效，p95 8.1s、over 0、ok 60/60。注：attempts=2 在超时类故障下不生效（首 attempt 吃满 5s 后剩余 ~1s < 窗 1.25s 直接耗尽）——deadline 优先于次数是正确设计。
- **blackhole（D1b 决定性证据）**：mock 零字节挂 30s，只有 per-attempt socket 读超时能把单次阻塞斩在 5s → degrade ≈6.5s、ok 6/6。无 D1b 此档每请求阻塞 30s → ttft_timeout。
- 单次 degrade 成本探针 `pb_probe_rate100`：C=1 rate100 raise，5 样本全 ~27.5s（旧 adapter 决策轮 raise 固定成本 ~26s）→ D1a 后 ~1s。

### D2 工具执行器泄漏 — before / after（leak_probe，mock `search_memory`）

| 探针 | 请求 done | done 后线程超基线长窗 | Jaeger execute_tool / 子 embed.api |
|------|----------|----------------------|-------------------------------------|
| **before** | 8.3s | **baseline+1 持续 16.5s**（t≈8.5→24.8s） | **5012 / 17540** → 子超父 ~12.5s |
| **after** | 7.4s | **0.8s**（settle 8.2s 回基线） | **3908 / 3907** → 子在父内收敛 |

after：`search_memory` handler 内 embed 在 deadline 前自行放弃 → execute 3.9s 正常返回（**非 5s force-abandon**），线程随 handler 结束即回落，无 baseline+1 长窗。生产遇慢 provider 反复超时不再累积线程。

**读表提示：execute_tool 5012→3908ms 不是 embed 变快。** before 的 5012ms 是外层 `fut.result(timeout=5s)` 的弃船时刻（MEMORY_TIMEOUT=5s 精确命中）；after 的 3908ms 是 embed deadline 预算（`timeout−1s`=4s）内 bounded attempt 自行收手的返回点。差来自「弃船点从 5s 提前到 4s 预算」+「handler 在预算内自断」——两次测的都是工具调用何时解套，不是 embed 吞吐/延迟提升，别误读成性能收益。

### 相关 commit

- D1a `a41fcfd` 撤 SDK 嵌套重试 · `a609e62` 退避夹 deadline + 429 独立上限 · D1b `2bc3658` per-attempt timeout · `8ad5f05` ceiling 三常量 env 可覆盖
- D2 `7834d6c` embed deadline scope 分路 · `e05da8b` execute 预算开进 worker + 回归(a) · `0a3eb2c` build 不开 scope 的理由注释 · `a9d679e` 非 429 退避 uniform(0,1) jitter
- rig `ec370ae` 决策故障注入 + 泄漏探针 · 落表 `d34ba10`

---

## 2. 双峰分布结论（分峰报，单一 P95 会误导）

TTFT 不是单峰。上游健康与上游故障各有一个模式：

- **健康峰**：rate=0（fast preset）任意并发档 p50 收敛在 **3.6–4.7s**（fast_c01→c64 p50 3500–4358，C=16 实测 3672/4547/4719 最贴，除 C=32 样本抖动 8313）。这是 agent 决策轮 + 64-token 生成的固有成本，与并发无关（C=64 仍 over 0）。
- **降级峰（before）**：上游故障把请求推入 **26–133s** 带 —— raise30 p95 62s / hang8 p95 100s / hang8 p99 133s。峰的位置由故障类决定：raise 收敛于重试墙次数，hang/blackhole 收敛于「单次阻塞 × 嵌套 attempts」。
- **修复后收敛**：三个故障类的 p95 全部落到 **5.3–8.1s**（raise 5.3 / hang8 8.1 / blackhole 7.4），即「一次 bounded attempt + degrade 回退」的代价，与健康峰拉开到同量级。

**结论**：只要压测混入故障档（或线上确有瞬时上游故障），聚合 P95 就是两峰混叠的可误导值——它取决于 raise/hang 的混合比例而非系统真实水位。**必须按峰分报**；跨峰聚合 P95 只有在两峰都收敛到同一量级（即修复后）才有意义。slow 档（外部真慢 2s/轮）p50≈20.9s 守恒是另一回事，与故障无关，不算降级峰。

---

## 3. 空线索如实写（假设不成立，别让报告替数据说话）

归因阶段原设想「连接池规模错配（池 30 vs 200）是容量天花板」，**数据不支持**。采样点时序（线程 / PG active-total / mem，三采样点/档）：

| 指标 | 观测 | 结论 |
|------|------|------|
| PG 连接池 | active **峰值 17/30** | 池从未打满，非瓶颈 |
| 容器内存 | 峰 **276 MiB**（mem_limit 768m） | 远未触顶，无 OOM 压力 |
| 线程 | 241 → **257** | 负载下温和增长 ~16，且随慢工具调用结束即 drain（D2 修复后无长窗残留） |

真正的容量语义：健康容量本身撑得住（fast_c01→c64 全部 over≤2，C=64 over 0）；系统在「上游慢/故障」下的代价是**延迟**（重试墙 + 线程残留），不是**吞吐/资源耗竭**。阶段内压测的归因对象一直是延迟分布，不是容量 cliff。

---

## 4. 行业对齐

- **D1 = Google SRE 分层重试预算（layered retry budget）**：重试只应在紧邻被拒层之上那一层做；每层都重试会乘数放大（层 × 次数）。我们观测的正是这个病：SDK 层与 adapter 层嵌套，把一次故障放大成 ~9 次 HTTP。修法即 SRE 结论——`_RetryBudget` 在 adapter 单点管总次数/总时长，429（honor Retry-After）、5xx（退避）、连接超时（读超时斩零字节）分别归类，非重试类（400/401/403）立即 raise。
- **D2 = gRPC deadline propagation**：被放弃的请求不该继续消耗下游资源。gRPC 靠元数据传 deadline、对端自停；Python 杀不掉已启动线程，所以把「弃船」换成「传播 deadline」——外层 `fut.result(timeout)` 是预算上限，内层 embed deadline 在 executor worker 内开 scope（ContextVar），让库内请求在 deadline 前自行结束。语义等价于 deadline propagation，机制受限于线程模型。**build() 动态区检索刻意不开 scope**（无 timeout → 无弃船 → 没有残留线程问题），已在代码注释说明（`0a3eb2c`）。

---

## 5. 已识别未实现（6 条，记账不做）

1. **重试比例预算**（client retry ratio budget）——现在每请求 bounded，无 fleet 级「重试次数 / 总请求」比例闸门；上游持续半故障时仍会在预算内逐请求重试。SRE 完全体需要比例门。
2. **熔断 / adaptive throttling**——上游错误率跨阈值后 fail fast + 冷却窗，或 Google 自适应限流。D1/D2 假定外部（DeepSeek/embed provider）会恢复；熔断是在「持续故障」下的下一道闸。
3. **ceiling 调优**——决策 5s ceiling 是估值（prod 无 OTel sink、rig 延迟全来自 mock，真实 DeepSeek `chat_with_tools` 耗时分布无源可报）。三常量已 env 可覆盖（`8ad5f05`，默认值不变）；生产发现太紧/太松改 `LLM_DECISION_ATTEMPT_S` 等即可，需真实分布后再调。
4. **web_search LLM filter overrun**——`ContextEngine._search_web` 第二步角色过滤器是独立 LLM chat 调用，超 15s 仍会被调用方 timeout 弃船 → 与 D2 同类（线程孤儿），但机制在 **llm_adapter 的 chat 预算**而非 embed 的 deadline scope，是独立一块。已识别，未实现。
5. **execute() 复用 ThreadPoolExecutor（不做）**——D2 预算下传消除弃船后 executor 不再泄漏（线程残留 16.5s→0.8s，span 子超父 12.5s→1ms）。剩余的"每次 execute() 新建 ThreadPoolExecutor"只是创建开销，压测实测噪声级（线程 241→257、PG 池 17/30、mem 276MiB，全程无资源绑定）。且换共享池会引入新失败模式：被占住的 worker 会拖死整个池，比每次新建更糟。判定为性能优化而非缺陷，不做。
6. **成本模型漏洞——embedding 走平台 key、零配额（记账不做）**——LLM 走用户自带 key（平台零成本），但 embedding 走平台 key（core/embeddings.py，dashscope:region:key 单例 + 模块级共享 512 LRU），**无任何调用配额或限流**——[embed-stats] 计数器纯观测不拦截，LRU 只去重不设闸。用户每次蒸馏都在烧平台的钱，开放注册会放大。单本 30 万字 ≈ 1–2 毛 / 整库 pass，但多角色/多轮重建 scenes 按 pass 数累乘。候选对策：每文本/每日 token 上限、或 embedding 改走用户自带 key。当前邀请制量小未暴露，记账不做。
