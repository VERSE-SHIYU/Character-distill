# tests/perf — Step 4 压测 rig 复现手册

本目录是一次性「②④ 压测与容量归因」rig 的可执行代码落库（commit 0b31fb2+）。
**原因**：rig app 镜像从工作区 build、不吃 git，D1a/D2 报过的 degraded 数字一度骑在
未提交代码上。这里把产数脚本 + mock 全量 + 泄漏探针收进 git，使每个已报数字有
「脚本 + 参数 + 前置」的代码支撑，三个月后仍可复现。结果 JSON / 编排器 / .env.s4
在 `data/eval_scratch/s4/`（gitignored，那是数据不是代码）。

**阶段 ②④ 收口报告**：`docs/evidence/phase24_closure.md`（D1a/D1b/D2 主线发现、before/after 全表、
双峰结论、空线索、行业对齐、未实现清单）。

## 拓扑

| 件 | 指向 | 说明 |
|----|------|------|
| compose | `data/eval_scratch/s4/docker-compose.s4.yml`（gitignored） | project `cdload`，全独立 PG/网络/卷 |
| app | `127.0.0.1:7862`（容器 `cdload-app-1`，PID1，mem_limit 768m） | 从工作区 `Dockerfile` build |
| PG | `127.0.0.1:5435`（凭据在 `.env.s4`，gitignored） | postgres 16，max_connections=50 |
| mock | `127.0.0.1:60950` | 本目录 `mock_llm_server.py`（绑 0.0.0.0，容器经 host.docker.internal） |
| Jaeger | UI `127.0.0.1:16686`，OTLP `127.0.0.1:4318` | app 侧 OTEL_ENABLED=1 → host.docker.internal:4318 |

app 在容器内把 LLM/embed/mem0 全指向 host mock（.env.s4 + compose env）。

## 前置（每轮复压前）

1. **镜像与 git 一致**：`docker compose -f data/eval_scratch/s4/docker-compose.s4.yml up -d --build app`
   —— 镜像吃工作区；改过生产/脚本代码后必须先 build 再压，否则数字骑在脏树上。
   确认 `git status` clean + 容器起的是新 commit。
2. **seed**（只拷最小 fixture 闭包：testadmin→texts→cards→sessions→messages）：
   ```
   python tests/perf/step4_seed_pg.py --sqlite data/character_sim.db \
       --s4env data/eval_scratch/s4/.env.s4
   ```
   app 必须先 up（/api/health 门禁）。幂等可重跑。
3. **mock 启动**（决策注入档要求 `MOCK_TOOL=search_memory`，否则 AgentLoop 决策轮不路由工具）：
   ```
   HOST=0.0.0.0 PORT=60950 PRESET=fast MOCK_TOOL=search_memory \
     python tests/perf/mock_llm_server.py
   ```
   `PRESET=fast|slow` 两档延迟（见 mock 头注释）。阶段 A 健康容量用 `fast`。
4. **load 编排**：
   ```
   python tests/perf/step4_load.py provision --prefix <stage> --count 96   # 每档全新会话前缀
   python tests/perf/step4_load.py warmup                                  # 1 次丢弃请求（pool/迁移预热）
   python tests/perf/step4_load.py level --preset fast --concurrency 8 --samples 60 \
       --ttft-ms 15000 --out data/eval_scratch/s4/results/<stage>.json --prefix <stage>
   ```
   单档模板：`level --preset P --concurrency C --samples N --ttft-ms 15000 --out <file>`。
   丢首样本（会话首请求引擎重建）；结果含三采样点时序（线程/PG active-total/mem）。
5. **档间 drain 协议**：worker join 后等 ≥15s，再每 5s 采 `/proc/1/task`，连续两次差 ≤3
   判 settled 才进下一档（防跨档线程累积污染归因）。编排器样板见 gitignored
   `data/eval_scratch/s4/run_*.py`（`run_hang_matrix.py` / `run_d1a_repress.py`）。
6. **Jaeger 归并**（degraded 只在服务端可见，客户端 done 事件不带标记）：
   ```
   python tests/perf/step4_jaeger.py roots --lookback 30m
   python tests/perf/step4_jaeger.py bucket --dir data/eval_scratch/s4/results --glob '*.json'
   ```

## 故障注入档速查（mock /admin/set 热改，或启动 env）

| 参数 | 值 | 作用 |
|------|----|------|
| `decision_fail_rate` | 0–100 | 决策轮（tools-bearing chat_with_tools）故障概率 |
| `decision_fail_mode` | `raise`/`hang`/`blackhole` | raise=HTTP 500；hang=先挂 N ms 再 500；blackhole=不回字节只挂连接 |
| `decision_fail_hang_ms` | ms | hang 档挂起时长 |
| `embed_hang_ms` | ms | /v1/embeddings 先挂 N ms（tools.py 阻塞探针用） |
| `tool_query` | 文本 | 覆盖决策轮 canned search query → 探针按文本 keyed 的 embed LRU 必 miss |

裁决按 messages-body key 记忆（同轮 SDK/adapter 重试共享同一裁决）；压测各会话消息内嵌
会话号（step4_load worker 已做），防 trajectory 相同撞裁决。

## 已报数字锚点（截至 2026-09-09，app=git HEAD，mock=commit 2 全量）

结果 JSON 均落 `data/eval_scratch/s4/results/`（gitignored）。下表 TTFT 单位 ms，
客户端锚点 p50/p95，超阈 15s。完整逐请求明细在对应 JSON。

### A. 健康容量（mock preset=fast，rate=0；旧驱动，driver 前有 agent_mode=None）

| 档 | C | rec | dur_s | p50 | p95 | p99 | over |
|----|---|-----|-------|-----|-----|-----|------|
| fast_c01 | 1 | 60 | 234 | 3500 | 4375 | 14327 | 0 |
| fast_c02 | 2 | 60 | 178 | 3500 | 4438 | 70188 | 2 |
| fast_c04 | 4 | 100 | 132 | 3531 | 15141 | 37889 | 5 |
| fast_c08 | 8 | 100 | 99 | 3562 | 14358 | 38000 | 4 |
| fast_c16 | 16 | 100 | 35 | 3672 | 4547 | 4719 | 0 |
| fast_c32 | 32 | 100 | 56 | 8313 | 11858 | 15983 | 2 |
| fast_c64 | 64 | 100 | 78 | 4266 | 10233 | 10375 | 0 |
| slow_c16 | 16 | 40 | 193 | 20969 | 26141 | 28109 | 40 |

slow_c01/02/04/08 均 ok=100%、p50≈20.9s（≈slow 预设 TTFT 2s×agent 多轮 + 64 token），
TTFT 恒超 15s 阈——故 slow 看的是「外部慢时是否守恒」，不是阈值达标。

### B/D1. 故障注入 — raise/hang before → D1a repress（mock tool=search_memory）

| 档 | mock | rec | ok | p50 | p95 | p99 | over | 意义 |
|----|------|-----|----|-----|-----|-----|------|------|
| fb2_raise_30 | raise rate30（before） | 40 | 40 | 4438 | 62062 | 62093 | 14 | 5s+10s 重试墙残 |
| **d1a_raise_30** | raise rate30（D1a 后） | 60 | 60 | 4358 | **5452** | 5483 | 0 | 墙消失 |
| hg_hang_30 | hang rate30 hang8s（before） | 60 | 60 | 3702 | 100438 | 133468 | 18 | 8s hang×~9 attempts |
| **d1a_hang8000_30** | hang rate30 hang8s（D1a 后） | 58 | 50 | 3735 | **68813** | 76172 | 6 | 8 个 ttft_timeout，单 attempt 以 create 返回为界 |
| **d1a_hang20000_30** | hang rate30 hang20s（D1a 后） | 30 | 30 | 3766 | **23125** | 23156 | 7 | = 1 次 20s 阻塞 + legacy 回退 |
| **d1b_raise_30** | raise rate30（D1b 后） | 60 | 60 | 4014 | **5297** | 5593 | 0 | vs D1a 5452 不回归 |
| **d1b2_hang8000_30** | hang rate30 hang8s（D1b 后） | 60 | 60 | 4782 | **8077** | 8390 | 0 | over 0 vs D1a 6；ok 60 vs 50+8 timeout |
| **d1b2_blackhole_100** | blackhole rate100（D1b 后） | 6 | 6 | 6547 | **7436** | 7436 | 0 | per-attempt 读超时斩零字节阻塞 → degrade |

> D1a 复压档参编排器 `run_d1a_repress.py`（raise30/hang8/hang20 各自 fresh 前缀，档间 drain
> 断言回落，drain 记录见 results/d1a_repress_drain.txt）。hang8 的 p95 68.8s 仍重：单次
> create 阻塞以 HTTP 返回为界、deadline 打不断已发出的请求 → 由 **D1b per-attempt
> `create(timeout=min(ceiling, 剩余−margin))`** 收尾（commit 3，单测在
> `test_llm_adapter_retry.py`；ceiling 三常量 env 可覆盖，commit 8ad5f05）。
>
> D1b 复压三档（编排器 `run_d1b_repress.py` + `run_d1b_repress2.py`，结果 JSON
> d1b_raise_30 / d1b2_hang8000_30 / d1b2_blackhole_100；**round-1 的 d1b_blackhole_100
> 与 d1b_hang8000_30 作废**——旧 mock 进程缺 commit-2 blackhole 分支，round-1 blackhole 跑成了
> raise 档、hang8 带 2 个旧 mock 连接抖动 error_event；round-2 重启 commit-2 mock 后重跑）：
> - **raise30 不回归**：p95 5297 ≈ D1a 5452。raise 档 degrade 决策成本 ~1s（attempt1 立即 500 →
>   退避 1s → attempt2 立即 500 → non429 cap）——快速失败用得上 attempts=2。
> - **hang8**：决策 5s ceiling 生效（app 日志全 "All 1 attempts failed: Request timed out"）。
>   **attempts=2 在超时类故障下不生效**：首 attempt 吃满 5s 后剩余 ~1s < 窗 1.25s 直接耗尽，
>   deadline 优先于次数（正确设计，别对不上）。degrade 总 TTFT ≈6.5s = 决策 5s + legacy 上下文
>   重建 ~1.3s + 生成 0.2s（C=1 rate100 探针 `probe_hang_degrade.py` 实测，无并发干扰）。
> - **blackhole（THE D1b 证据）**：mock 零字节挂 30s，只有 per-attempt socket 读超时能把单次
>   阻塞斩在 5s → degrade ≈6.5s、ok 6/6。若无 D1b，此档每请求阻塞 30s → ttft_timeout。
>
> 注：决策 5s ceiling 为**估值**——prod 无 OTel sink、rig 延迟全来自 mock，真实 DeepSeek
> chat_with_tools 耗时分布无源可报；生产发现太紧改 `LLM_DECISION_ATTEMPT_S` 即可。

单次 degrade 成本探针：`pb_probe_rate100.json`（C=1 rate100 raise，5 样本全 ~27.5s）
——旧 adapter 决策轮 raise 单轮固定成本 ~26s，D1a 后降为 ~1s。

### D2. tools.py 执行器泄漏 — before 基线（工具层未修，复现=当前树）

```
python tests/perf/leak_probe.py --hang-ms 20000 --prefix probe   # 前置：mock 须 MOCK_TOOL=search_memory
```
实测（app cdload-app-1，`/proc/1/task`，baseline 241）：
- 请求 done 8.3s；此后线程 **baseline+1 持续 16.5s**（t≈8.5→24.8s 在 242，24.5s 回 241）。
- 日志：`search_memory(leakprobe-…) ok=False 5002ms`（MEMORY_TIMEOUT=5s 精确命中）。
- Jaeger trace `a693634c…`：**execute_tool=5012ms，其子 embed.api=17540ms** → 子超父 ~12.5s
  → `shutdown(cancel_futures)` 杀不掉已启动的 handler 线程，挂到 embed client（读超时 8s×~3）放弃。

**结论**：每次 timed-out 工具调用泄漏 1 线程至底层调用结束（本 rig ~24s，受 embed client
8s 超时界）。生产遇慢 provider 反复超时会累积。修复后复跑本探针应断言：请求 done 后线程
随 handler 结束即回落（无 baseline+1 长窗），且 Jaeger 无「子 span 超父」。
