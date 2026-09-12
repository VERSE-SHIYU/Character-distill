# thinking 参数修复 — 证据档（LLM token 预算实测）

- 日期：2026-09-10 测量，2026-09-11 入库；2026-09-12 迁入 `docs/evidence/`
- 范围：`thinking` 方言写错导致「思考吃光 `max_tokens` 预算 → 正文为空」的前后对照
- 代码支撑：`tests/perf/map_len_probe.py` / `tests/perf/capfield_probe.py` + 原始产物 JSON
  （`thinking-maplen-before.json` / `thinking-maplen-after.json` / `thinking-capfield.json`，本目录）
- 结论去向：`AGENTS.md` §二（基线表）、§三 缺陷 1 / 缺陷 2 / 缺陷 8

**为什么要入库**：这组数字是本项目最硬的一组前后对照，但产它的脚本与原始 JSON 原在
`e2e/scratch/`（gitignored）。后果是「换个会话就说不清怎么测的」，且已实际发生过一次
（把已有的数字当成缺口去猜）。2026-09-11 先提到 `tests/perf/`，2026-09-12 随「证据产物
一等化」迁到 `docs/evidence/` —— 探针脚本留 `tests/perf/`（可执行验证工具），产物与结论文档
归此目录；落点与脱敏由 `tests/perf/evidence_writer.py` 强制，契约见本目录 `README.md`。

---

## 1. 两个脚本各回答什么

| 脚本 | 回答的问题 | 产物 |
|------|-----------|------|
| `map_len_probe.py` | 生产 `map` 提示词下，模型**自然输出**多长？`max_tokens=4096` 够不够？ | `thinking-maplen-before.json`（修复前）/ `thinking-maplen-after.json`（修复后） |
| `capfield_probe.py` | 输出顶到 8192 上限时，token 花在哪？content 被截断，还是 content 为空、预算被别处吃掉？ | `thinking-capfield.json` |

`capfield_probe.py` 的答案是后者：**思考（`reasoning_content`）吃光了共享预算**。
它**故意**发修复前那套错方言 `extra_body={"enable_thinking": False}`（Qwen 方言，DeepSeek
静默忽略）——这是「修复前」的可复现演示，不是待修代码。生产侧正确方言见
`adapters/llm_adapter.py` 的 `_THINKING_DISABLED`。

## 2. 前置（当时的取值，全部可核）

`config.yaml` 自 2026-09-09 起无提交，故下表即测量当时的取值：

| 项 | 值 | 来源 |
|----|----|------|
| `llm.model` | `deepseek-v4-pro` | `config.yaml`；亦记在每份产物 JSON 的 `model` 字段 |
| `llm.max_tokens`（生产 cap） | 4096 | `config.yaml`；产物 `prod_max_tokens` |
| 探针 cap | 8192 | 脚本常量 `MEASURE_MAX_TOKENS`；产物 `measure_max_tokens` |
| `llm.temperature` | 0.7 | `config.yaml`（生产默认） |
| `distill.chunk_size` | 5000 | `config.yaml`（story 档） |
| classic 档片长 | 6000 | `core/distiller.py`：classic 强制 `max(chunk_size, 6000)` |
| `distill.longctx_threshold` | 150000 | `config.yaml`。**本组探针不走分流**（直接调 `Distiller._split_chunks` + map 提示词），故该值不进入测量，仅记上下文 |
| `_GEN_ATTEMPT_S` / `_GEN_DEADLINE_S` | 抬到 120 / 240 | 脚本内模块常量改写（生产 45 / 60）。测的是「模型自然输出多长」，不是「60s 内能吐多少」；生产口径的 45s / 60s 两行是**按 `elapsed_s` 回算**的，不是探针真实超时 |

## 3. 怎么跑

```bash
# 依赖：本机 data/character_sim.db（真实语料）+ .env 里的供应商凭据
PROBE_DB=data/character_sim.db PROBE_EVIDENCE_ID=thinking-maplen-after \
  python tests/perf/map_len_probe.py
PROBE_DB=data/character_sim.db PROBE_EVIDENCE_ID=thinking-capfield \
  python tests/perf/capfield_probe.py

# 换机器/换语料：替换 PLAN / CASES 里的 text_id（本机库行 id）
# 落点由 PROBE_EVIDENCE_ID 决定（一律 docs/evidence/<id>.json），无路径参数可改 —— 见 README.md
```

- 产物一律写 `docs/evidence/<PROBE_EVIDENCE_ID>.json`，**覆盖同名文件**；入库的那些是冻结快照，
  要重跑对照请换一个 id（如 `thinking-maplen-after-2026xx`），别顶替已入库的那份。
- 两个脚本都不是 `test_*.py`，pytest 不会收集，不进 CI。
- **重跑不会得到相同数字**（LLM 采样、`temperature=0.7`）。可复现的是**结论**与量级，不是逐条数值。
- 需要本机持有 `data/character_sim.db`（含真实语料，不入库、不可分发）——脚本本身可读可跑，
  但换人复现必须自备同形语料，并替换 `text_id` **与角色名占位符**（`角色A`…`角色D`）。
  未替换时脚本**拒绝运行**（`PLACEHOLDER_CHARS`）——否则 `sample()` 匹配不到任何片会静默退回前三片，
  跑出来的就不是同一个测量了。

## 4. 数字 → 文件映射（AGENTS.md §二 基线表逐行）

| 指标 | 修复前 | 修复后 | 口径（可从产物复算） |
|------|--------|--------|---------------------|
| `out_tokens` p50 | 8191 | 1245 | 14 条合并后 `sorted(outs)[n//2]`（n=14 → 下标 7） |
| `out_tokens` max | 8192 | 2097 | `max(records[].out_tokens)` |
| 撞 8192 探针上限 | 7 | 0 | `count(clipped_by_probe_cap)` |
| `out_chars == 0`（空正文） | 3 | 0 | `count(out_chars == 0)` |
| `out_chars == 1`（模型答「无」） | 0 | 3 | `count(out_chars == 1)`；正常应答，非缺陷 |
| 单次耗时 min–max | 12.0–160.0s | 1.4–31.3s | `min/max(records[].elapsed_s)` |
| 耗时 > 45s | 12/14 | 0/14 | `count(elapsed_s > 45)` |
| 耗时 > 60s | 11/14 | 0/14 | `count(elapsed_s > 60)` |
| tokens / 正文字符 | 3.85 | 0.62 | `sum(out_tokens)/sum(out_chars)`，仅 `out_chars > 0` 的记录 |

修复前 = `thinking-maplen-before.json`，修复后 = `thinking-maplen-after.json`。

**p50 口径**：表里的 p50 是**14 条合并后的 nearest-rank 上中位**（`sorted(outs)[7]`），
**不是**产物里 `summary[].out_tokens_p50`（那是按档（5000 / 6000 字符）分组的、
用 `int(round(0.5*(n-1)))` 取的下中位，合并前为 8192 / 6079，合并后 1446 / 1177）。
两者都自洽，但混用会得出 6487 / 1205 这种对不上的数 —— 复算时认准这一行。
**同一份数据两种口径 = 看起来像造假**，判据已成文于 `AGENTS.md` §四「聚合统计必须写明口径」。

`capfield` 侧（`thinking-capfield.json`）：3 条里 2 条 `content_chars=0` + `finish_reason='length'`
+ `reasoning_content_chars` 12441 / 12413 + 耗时 161.9s / 122.4s；第 3 条 `content_chars=2060`
+ reasoning 8735 + `stop` + 131.5s。即缺陷 1 的实测形态。

## 5. 入库时对内容做了什么

- **凭据**：入库文件（两个脚本 + 三份 JSON）逐个 grep 过 API key / DSN / 密码 / ssh 主机与用户名，
  **零命中**。脚本只打印 `llm.model`（模型名），不打印 `base_url` / 凭据；产物无 base_url 字段。
- **版权语料**：原始产物的 `preview`（map 输出前 200 字符）/ `content_head`（content 前 150 字符）
  **已删除**。map 规则要求「原文对话原句必须完整保留」，这些字段逐字带出真实小说正文
  （实测 14 条中 11 条含引号、含作品角色名）。入库产物**只留统计量与 id，不留正文**。
  两个脚本也已同步**不再落这两个字段**，以免重跑时又写出正文。
- **去标识**：`char` 的真实角色名（真人姓名）与脚本注释里的作品名（真人同人）**全部移除**，
  改为 `角色A`…`角色D` 占位；`text_id` **保留** —— 它是不可读 hex，既能追溯又不透露语料身份。
  本仓是公开作品集，语料是哪几部作品对证据结论毫无价值，不留在台面上。三个脚本 / 三份 JSON 同口径。
- `out_capfield.prefix.json` 与 `out_capfield.json` 逐字节相同（同一次运行的副本），
  故只入库一份，即本目录 `thinking-capfield.json`。**同源同数据、未单独入库** ——
  清单是唯一真源，给同一批数字再开一条条目只是把一份证据记两遍。
  （两者都曾是 `e2e/scratch/` 下的 scratch 文件，2026-09-12 迁移时前者已在本地丢失、
  后者经证据出口入库，故本条只留这句记载，不留产物。）

## 6. 待核 / 未入库的相关证据

- **`docs/engineering-evidence.md`**：截至 2026-09-11 **不在仓库内，也从未被 git 跟踪过**
  （`git log --all -- '*engineering-evidence*'` 无输出）。本批证据与它无依赖关系，先落本目录。
- 缺陷 2（`finish_reason`）引用的 `out_v5.json`（scratch）**已于 2026-09-12 经证据出口迁移入库**，
  即 `incomplete-v5.json` —— 只留统计量（52 字节落满 6 片、二次续跑 map=0），
  正文段 `sampleChunk` 与装饰文案 `finalMessage` 按白名单挡在库外。见 `ev:incomplete-v5`。
- 生产是否仍有 >60s 调用（缺陷 8 的「待验证」）**仍未验证**，本批数据不回答它。
