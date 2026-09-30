# Step4 raise 档重跑 + trace 快照（2026-09-30）

补 `docs/engineering-evidence.md` §一「怎么定位」的可复现件。**全程不调用真实 LLM**
——app 容器内 LLM / embedding / mem0 全指向 host mock（compose 把 DASHSCOPE/DEEPSEEK key
盖成哑值）。

## 环境

| 件 | 值 |
|---|---|
| app | `66f03631`（= D1a `a41fcfd` 的父提交，即修复前），Docker 从该 commit 的 worktree build |
| mock | `tests/perf/mock_llm_server.py`（与 README 数字所据的 `ec370ae` **逐字节相同**） |
| PG | `data/eval_scratch/s4/.env.s4` 的一次性 rig 库（127.0.0.1:5435） |
| Jaeger | all-in-one，UI `127.0.0.1:16686` / OTLP `127.0.0.1:4318`（内存存储） |

## 两档参数与结果

| 文件 | mock | C | samples | ok | dur_s | p50 | p95 | p99 | over15s |
|---|---|---|---|---|---|---|---|---|---|
| `rerun_raise_30.json` | raise rate30，tool=search_memory | 8 | 40 | 40 | 85.7 | 3921 | 28733 | 28858 | 4 |
| `rerun_raise_100_c1.json` | raise rate100，tool=search_memory | 1 | 5 | 5 | 174.2 | 28608 | 29500 | 29500 | 5 |

命令（B 档同形，改 `--concurrency 1 --samples 5` 与 `--out`；B 档前 mock 经
`POST /admin/set {"decision_fail_rate":100}` 热改，档间按 README 前置 5 drain 到线程数
连续两次差 ≤3）：

```
HOST=0.0.0.0 PORT=60800 PRESET=fast MOCK_TOOL=search_memory \
  MOCK_DECISION_FAIL_RATE=30 MOCK_DECISION_FAIL_MODE=raise python tests/perf/mock_llm_server.py
python tests/perf/step4_load.py provision --count 16 --prefix rr0930
python tests/perf/step4_load.py warmup
python tests/perf/step4_load.py level --preset fast --concurrency 8 --samples 40 \
    --ttft-ms 15000 --out data/eval_scratch/s4/results/rerun_raise_30.json --prefix rr0930
```

## trace 快照（`GET /api/traces/<id>` 的完整响应）

挑选条件：窗口内 `service=character-distill` 的 `chat.invoke_agent` 根，按 `app.ttft_ms`
取 **p95 附近（最慢）**、**第二慢**、**p50 附近（健康对照）**；B 档取 1 条样本。
「空隙」= 根时长 − 全部子 span 时间并集。

| 文件 | trace id | 根 ms | `agent.plan` ms | `llm.chat_with_tools` ms | error | 空隙 ms | root ttft ms |
|---|---|---|---|---|---|---|---|
| `trace_A1_slow_p95.json` | `69fd1a8d063fe55538a6145e2c0a2ffd` | 29125.6 | 27146.3 | **27145.9** | **True** | 40.6 | 27574 |
| `trace_A2_slow_2nd.json` | `fd072422319fd79536772911e447a0d7` | 28969.4 | 26897.3 | **26897.1** | **True** | 62.4 | 27406 |
| `trace_A3_healthy_p50.json` | `032b0a08e8bd5382cf2557b44ff25814` | 3940.7 | 1779.1 | 912.5 / 861.4 | – | 348.0 | 2108 |
| `trace_B1_c1_rate100.json` | `2926737c2caba816c49aaa942de0d356` | 28389.5 | 26589.9 | **26589.7** | **True** | 38.6 | 26874 |

慢条：单个决策轮 span 占根 **93%**，其后 legacy 回退约 **1.9s**（= 根 − `agent.plan`），
子 span 串接基本覆盖根 → 几乎无空隙。健康对照有**两个**短的 `chat_with_tools`（两次
决策轮成功），慢条只有**一个**（首轮失败后即降级）。

## 降级归因（本 commit 无 `agent.degraded` 埋点）

`66f03631` 早于 `agent.degraded` / `repair_stage` 埋点（`0b31fb2`），故 Jaeger 全部根 span
查不到 `agent.degraded` 属性（本窗口 63 条根全为 `None`）。降级归属改由两条证据互证：

1. 慢条 trace 里单个 `llm.chat_with_tools` `error=True` 且吃掉根 93% —— 决策轮重试堆叠；
2. `degraded_log.txt` = `docker logs -t cdload-app-1` 中 `agent degraded → legacy context
   injection` 的全部行（20 条，含时间戳）。

## 与 09-09 已报数字的关系

- 09-09 `fb2_raise_30`（同 commit、同 mock）p95 `62062`ms；本次同参数只得到 `28733`ms：
  40 条里 4 条 degraded，并发重叠少 → 停在「单条降级固有代价 ~27s」。62s 是**并发 8 下
  多条降级重叠**的尾部（见会话记录 Task 1 的 6×/2× 双峰解释）。本目录**不引用**
  62.3s / 59.4s / 26.06s。
- C=1 零并发（B 档）仍复现 ~28.6s → 与并发无关。
