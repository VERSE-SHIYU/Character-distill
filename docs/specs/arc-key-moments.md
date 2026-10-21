# 弧线阶段新增 `key_moments`，重定位只按它找起点（方案 B）

仓库 `VERSE-SHIYU/Character-distill`（不是 neylo-python），基线 main `f555a7bf`。新分支 `fix/arc-key-moments`，草稿 PR，标题 / 描述 / 提交信息英文，描述首句「Not reviewed, do not merge」。
第一个提交：把本提示词原样存为 `docs/specs/arc-key-moments.md`（返工经验第 17 条）。

## 0. 目标与根因

**目标**：重定位找阶段起点时，判断的是「标志这个阶段开始的那件事」，而不是阶段描述里随便哪件事。验收：小重放祈灼、谢凛羽各 2 次，起点都落在真值内（第 34 条，见 §8）。

**根因（已核，一条规则两处、前提相反，第 6 条）**：
- 弧线组规定 state「句首用书里的具体事件或时段点明这是什么时候……再写此时的心态与行事方式」（`core/distiller.py:296`，自 a03296bc 10-02）。
- 重定位判断提示词「这个阶段描述里有一件标志阶段开始的事（描述里有几件事时，取最后发生的那一件）」（`core/phase_locate.py:114`，自 #185 b187a499 10-19）。
- 实例（10-22 小重放产物，本机 `e2e/scratch/replay-20261021/`）：谢凛羽 run2 阶段 3 state「揽月台见她受伤后彻底认清心意，从此……甘愿被她扇巴掌、被她拿捏」→ 判断取最后一件「扇巴掌」，在第 424 章采信「云绮扇楚翊一巴掌」（被扇的人都不是谢凛羽），真值 460–461。谢凛羽 run1 阶段 2 state「被蒙面少女吸引后念念不忘，撞破是云绮时又气又乱」→ 取「撞破」，候选章里都只是回忆它，75 章只 1 章答是，重定位失败、整卡跳过。

### 出处表（第 32 条；标「转述」的是读原文网页时的转述，不是逐字原文，执行方写 spec 前去原文页逐字核对后再填）

| 来源 | 原文 | 用在哪 |
|---|---|---|
| ArcANE（arXiv 2606.05553，2026-06）附录 C（字段名为原文） | trajectory 每个 phase 对象四个字段：`phase`、`chapter_range`、`position_description`、`key_moments` | 状态描述与关键事件**分成两个字段**；字段名沿用 `key_moments`（列表） |
| ArcANE §3.1（转述） | 阶段由章节范围、状态描述、用于定位的关键事件三部分描述 | key_moments 的作用就是给阶段定位（anchoring） |
| Too Long, Didn't Model（LaTeCH-CLfL 2026） | 「none of seven tested frontier LLMs retain stable understanding beyond 64k tokens」 | 本书 81.7 万 token：模型凭记忆写的位置不可信，保留代码侧重定位，不改这一设计 |

**自拟（无文献）**：「key_moments 第一件 = 标志阶段开始的事」「必须是此人本人做出或亲身经历的事」——前者把 ArcANE 的列表收成一个可定位的起点，后者来自本项目 P2 ⑤ 实例（上面谢凛羽 run2）。

## 1. 环境硬规则（违反即停，原样报告）

1. 不动 Docker Desktop / WSL / 任何容器。先 `docker ps`：测试库（character-distill-test，55432）已 healthy 就不再 up；没有才在仓库根目录执行一次 `docker compose -f docker-compose.test.yml up -d --wait`（第 16 条）。
2. 不用 cmd / powershell / start 启动程序；不重启电脑。
3. 不调真模型、不读不设任何 key。
4. 每个测试文件单独跑，套 `timeout 300`；超过 2 分钟或内存持续上涨：Ctrl+C，停。全量看分支 CI。本 PR 不改任何调用方式（同步 / 流式不变），判断替身是非流式 `chat` 返回字符串、组替身沿用已有有终点的 `chat_stream_long`，故不需先进沙箱（第 40 条的前提不成立，理由在此写明）。
5. 退出码非 0/1 或超时：停，不重试，贴原始输出。

## 2. Skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 逐条复核 §3 K1–K10（贴行），有一条不成立就停 |
| 目标检查 | `@tdd` | 第一个实现前提交 `xfail(strict=True)` |
| 实现 | `@incremental-implementation` | 每项一个提交 |
| 收尾 | `@verification-before-completion` | 每个「绿」附原始输出 |

## 3. 已查实的约束（@ `f555a7bf`，本轮读过）

| # | 事实 | 行 |
|---|---|---|
| K1 | 草稿里的阶段：`DraftPhase(PhaseState)` 有 `label`、`state`、`anchor`、`boundary_examples` | `core/card_draft.py:145-148`；`PhaseState` 在 `core/schema.py:154-157` |
| K2 | 发给模型的 G6 结构由 `CardDraft.model_json_schema()` 派生，`DraftPhase` 加字段会自动进 G6 schema | `core/card_draft.py:205-221` |
| K3 | 卡片里的阶段 `ArcPhase(PhaseState)` 没有 `anchor`，另有 behaviors / overlay / start / boundary_examples | `core/schema.py:172-180` |
| K4 | 弧线组维度 L 的文字规则（含 state、anchor 规则） | `core/distiller.py:291-301`；JSON 模板示例 `:370`；形态规则 `_FORMAT_IMPORTANCE` G6 `:418` |
| K5 | 重定位：BM25 检索只用 state；判断提示词用 `phase_query` = label + state | `core/phase_locate.py:106-107`、`:110-120`、`:195-196` |
| K6 | 既有测试直接涉及判断提示词 / 重定位 | `tests/test_phase_locate_goal.py`、`tests/test_phase1_anchor_not_checked_goal.py`、`tests/test_distill_diagnostic_logs_goal.py`、`tests/test_replay_distill_goal.py`（grep `phase_locate\|judge_prompt\|取最后发生`） |
| K7 | 转卡是 `card = draft.model_dump()` → `CharacterCard.model_validate(card)`；`ArcPhase` / `CharacterCard` 未设 `extra`（pydantic 默认忽略）。实测 `ArcPhase.model_validate({...,'anchor':'x','key_moments':['k']}).model_dump()` 的键为 behaviors / boundary_examples / label / overlay / start / state —— **key_moments 与 anchor 一样被丢掉，不进卡** | `core/card_draft.py:491`、`:577`；本轮在 `f555a7bf` 实跑 |
| K8 | 依赖组看到的阶段列表只渲染 编号 · label · state · 起始原句 | `core/distiller.py:510-522` `_phase_list_note` |
| K9 | 重新出卡（`/card/{id}/redraft`）用存下的旧草稿走同一条链，含 `locate_phase_starts` | `web/routers/distill.py:1444-1465` → `_run_distill_task` `:556-567` |
| K10 | 受影响的既有测试（另 grep `tests/perf/*.py`：没有变异锚点落在 `phase_locate.py` 或维度 L 文字上；`docs/evidence` 无引用） | 本轮 grep |
| K10' | 明细：受影响的既有测试（grep `L. 角色弧线`、`phase_locate`、`draft_schema`）：`tests/test_card_arc_behaviors.py:74-86`、`tests/test_longctx_focused_calls_goal.py:271` 与 fixture `tests/fixtures/longctx_focused_calls_f1_baseline.json`（G6 system 逐字比较，必须重录）、`tests/test_card_draft.py:140-146`（DraftPhase 字段断言）、`tests/test_phase_tagged_goal.py:98`、`tests/test_phase_locate_goal.py`（替身 `_Judge` 用正则 `角色弧线的一个阶段：(.+?)：` 取 label、`_PHASES` 无 key_moments）、`tests/test_phase1_anchor_not_checked_goal.py`、`tests/test_distill_diagnostic_logs_goal.py`、`tests/test_replay_distill_goal.py` | 本轮 grep |

**已定（不交执行方选，理由附后）**：
- D1 `_phase_list_note` **不带** key_moments：依赖组只需要阶段号与心态（K8），多给事件会诱导它按事件而非按摘录位置标阶段。
- D2 `key_moments` 在发给模型的 schema 里**必填**（照 `core/card_draft.py:72-77` `_phase_stays_required` 的写法），模型类里默认 `[]`、解析宽容；阶段 1 允许空列表。
- D3 **旧草稿没有 key_moments**（K9 重新出卡会遇到）：该阶段按「找不到起点」处理，不回落到 state。后果与 #185 之前相同：锚点核对不过就整卡跳过位置核对（D7）。不回落的理由：回落就是把「取哪件事」的第二套规则留在代码里（第 6 条）。旧卡要修本来就靠重蒸（P11）。
- D5 **F1 fixture 重录方法**：录制脚本不在仓库（`tests/test_longctx_focused_calls_goal.py` 头注释写明 `e2e/scratch/record_f1_calls.py` 不入库）。用该文件里的 `_run_f1()`（:114）在新代码上产出 `group_calls` / `tail_frame_kinds` / `draft` 写回 fixture；`baseline_commit` 填重录所用提交。报告里贴新旧 fixture 的 diff，**差异只允许来自** G6 system 里维度 L 新增的 key_moments 规则与 JSON 模板、draft 里阶段多出的 `key_moments`；其余组一字不变。
- D6 **规模（第 11 条）**：10-22 小重放 G6 每次输出 522–692 token；每阶段加 1–3 句关键事件，约多 100–200 token，远低于每组 8192 上限。判断提示词每次约 1,800 token（同一产物 `usage`），只是把一段话换成一句，长度基本不变。
- D4 K10 里依赖 `_PHASES` / 草稿夹具的测试，夹具补上 key_moments，`_Judge` 的取 label 正则若因提示词改写失效，改成从新提示词里取，**守的东西不变**；逐个写进 spec 的「失效的锁与夹具」表。

## 4. 改动

1. **字段**：`DraftPhase` 加 `key_moments: list[str] = []`。只在草稿里，不进 `ArcPhase`、不进卡、不存库。
2. **弧线组规则（维度 L）**：加一条 key_moments 规则——按发生顺序列 1–3 件此阶段的关键事件，每件一句、是此人本人做出或亲身经历的具体事；**第一件是标志这一阶段开始的那件事**；阶段 1 可留空。state 规则**不改**（下游聊天仍用它点明时期）。
3. **一处定义（第 6、26 条）**：「标志阶段开始的事 = key_moments 第一件」这句定义只写一处（一个常量），弧线组规则与判断提示词都引用它；结构锁守「全仓只此一处」。
4. **判断提示词**：删掉「描述里有几件事时，取最后发生的那一件」；改为给出 key_moments 第一件，问它是否**由「{name}」本人做出或亲身经历**、并在本章正文当下发生。label + state 仍作为背景给出。
5. **检索不改**：BM25 仍只用 state（`phase_locate.py:195` 注释：与 10-18 离线覆盖测试同口径，前 25 章 5/5）。改检索不在本 PR。
6. **key_moments 为空的阶段（2..n）**：见 D3。

## 5. 不做

- 不改 BM25 检索、不改「取最早答是」聚合规则、不改 ArcPhase / 卡片 schema、不改 ② 分段提示词、不处理阶段名退化。
- 不跑真模型（合并部署后由 §8 小重放验）。

## 6. 目标检查（第一个提交，`xfail(strict=True)`）

| # | 检查 | 测的目标 |
|---|---|---|
| G1 | `draft_schema("G6")` 的阶段对象含 `key_moments`（数组）；维度 L 文字含 key_moments 规则 | 模型被要求单独写标志事件 |
| G2 | `judge_prompt` 输出含 key_moments 第一件原文与「{name}」本人做出或亲身经历的约束；不含「取最后发生的那一件」 | 判断只看标志事件、查主体 |
| G3 | 「标志阶段开始的事」定义常量全仓只出现一处（AST / 源码锁），弧线组规则与判断提示词都由它生成 | 一条规则一处 |
| G4 | 用替身跑 `card_from_draft`：草稿阶段带 key_moments，产出的卡与存库 JSON 中没有 key_moments；ArcPhase 字段集与基线相同 | 不污染卡片 |
| G5 | **端到端复刻谢凛羽 run2**：小文本里第 A 章当下写「揽月台见她受伤」、更晚的第 B 章当下写「她扇了别人一巴掌」；草稿阶段 state 末尾写「甘愿被她扇巴掌」、key_moments[0] 写揽月台那件。替身判断**按提示词里要判的那件事**作答（揽月台那件 → 只在 A 答是；巴掌那件 → 只在 B 答是）。跑真实 `locate_phase_starts` → 该阶段起点 = A。改前（取最后一件）应落到 B，所以先红。另：key_moments 为空 → 该阶段找不到起点 | 根因场景：起点落对，不只是提示词文字对 |

替身照生产写，流有终点（第 35、40 条）。

## 7. 测试

本 PR 新增目标检查 + K10 列出的全部文件 + `test_arc_view`、`test_lock_coverage`、`test_mutation_anchors`，逐个跑；变异驱动 `tests/perf/card_draft_mutations.py`、`tests/perf/arc_phase_anchoring_mutations.py` 各跑一次、`git diff --stat` 为空或说明差异。推送前预检钩子要过。

## 8. 真模型验收（合并部署后另行执行，不在本 PR）

小重放工具按起点章匹配真值（另一个小改，见跟踪文档 10-22），祈灼、谢凛羽各 2 次，约 $0.2；两人两次都过才算过。

## 9. 报告

**顺序**：S0 复核 → 在 spec 里填结构自检表（第 36 条，写代码之前）→ 目标检查先红 → 实现。

结论先行；S0（K1–K10 复核，均贴行）；结构自检三问（同一规则几处、调用方是否被迫知道不该知道的事、加一个阶段字段要改几处）附行；目标检查先红后绿原始输出；K10 每处改写理由（守什么、为什么失效、怎么改、守的东西不变）；`git diff --stat f555a7bf HEAD`；PR 链接。不宣称通过。

---

## 返工经验对照（发出前逐条核过）

| 条目 | 依据 |
|---|---|
| 1 先定目标 | §0 目标 + §8 验收线 |
| 2 / 20 测达成目标 | G5 端到端跑真实 `locate_phase_starts`，断言起点落对（不只是断言提示词文字）；真模型验收另列 |
| 3 / 32 / 13 文献当年、原文照抄 | 出处表全为 2026 文献；字段名与 Too Long 一句为原文，§3.1 标「转述」并要求执行方逐字核对；自拟部分单列 |
| 6 / 26 一条规则一处 | §4 第 3 条 + G3 结构锁 |
| 7 / 7a 判断附读过的行 | §3 K1–K10 本轮读过或实跑；无「未核实」项 |
| 7c / 29 能自己查的不推给执行方 | 原 U1–U4 已由我读代码定成 D1–D4，附理由 |
| 9 / 37 一个 PR 一件事 | §5 不做清单；工具改动另走 |
| 11 往已有路径加东西先列机制、算规模 | K7（转卡丢字段）、K9（重新出卡路径）、K10（受影响测试 / fixture / 变异锚点，含 perf 与 evidence 的扫描）；D6 规模 |
| 8 全量扫描 | K10 的 grep 覆盖 tests、tests/perf、docs/evidence |
| 16 起环境前查冲突 | §1 第 1 条先 `docker ps` |
| 36 结构自检先于代码 | §9 顺序 |
| 40 碰替身先沙箱 | §1 第 4 条写明前提不成立的理由 |
| 17 / 18 / 36 spec 入库、S0、结构自检 | 开头 + §9 |
| 34 靠模型的行为两次都过 | §8 |
| 35 替身照生产、有终点 | §6 末；G5 替身按提示词里那件事作答，和生产判断同一口径 |
| 39 环境硬规则 | §1 |
| 43 根因清楚才给修法 | §0 根因两处代码 + 两个实例 |

---

## 10. S0 复核与结构自检（写代码之前）

### 10.1 K1–K10 复核（@ `f555a7bf`，逐条贴读到的行）

| # | 结论 | 依据（本轮读到的行 / 命令） |
|---|---|---|
| K1 | 成立 | `card_draft.py:145-148` `class DraftPhase(PhaseState)` + `anchor` / `boundary_examples`；`schema.py:154-157` `label` / `state` |
| K2 | 成立 | `card_draft.py:211` `full = CardDraft.model_json_schema()`；`:215-221` 按组裁剪，`$defs` 整体带上 |
| K3 | 成立 | `schema.py:172-180` `ArcPhase` 字段 behaviors / overlay / start / boundary_examples，无 anchor |
| K4 | 成立 | `distiller.py:291-301` 维度 L（state 规则 `:296`、anchor `:297`）；模板 `:370`；`_FORMAT_IMPORTANCE` G6 `:418` |
| K5 | 成立 | `phase_locate.py:106-107` `phase_query` = label + state；`:113` 提示词用它；`:196` `index.rank(state)` |
| K6 | 成立 | 上列 4 个测试文件均存在，grep `phase_locate\|judge_prompt\|取最后发生` 命中 |
| K7 | 成立（实跑） | `card_draft.py:491` `card = draft.model_dump()`；`:577` `CharacterCard.model_validate(card)`；实跑 `ArcPhase.model_validate({...,'anchor':'x','key_moments':['k']}).model_dump()` 的键 = behaviors / boundary_examples / label / overlay / start / state |
| K8 | 成立 | `distiller.py:510-522` `_phase_list_note` 只渲染 编号 · label · state · 起始原句 |
| K9 | 成立 | `distill.py:1444-1465` redraft 读存档草稿 → `_distill_start_impl(draft=...)`；`:556-567` `draft is None` 分支跳过蒸馏但同样走 `locate_phase_starts` |
| K10 | 成立 | grep `tests/perf/*.py` 无 `phase_locate` / 维度 L 文字锚点（唯一相关是 `card_draft_mutations.py:147` 的 `class DraftPhase(PhaseState)` 基类替换，不受加字段影响）；`docs/evidence` 无命中 |
| K10' | 成立，另多出 4 个文件 | `test_arc_phase_fields_unit.py:1017`（只查 DraftOccurrence/Attitude 的 phase 类型）、`test_arc_phase_select.py:99`（key_memories `$ref`）、`test_distiller_routing.py:779`（组顶层字段集）、`test_card_draft.py:140-146`（只断言 anchor 在、behaviors 不在）——均不依赖被改的字面量，纳入回归运行 |

`origin/main = f555a7bf`（本地 `main` 停在 `58215653`，落后 71 个提交）；工作树从 `origin/main` 同 sha 新建。

### 10.2 结构自检三问

1. **同一规则几处？** 改前两处、前提相反：维度 L 的 state 规则（`distiller.py:296`）让 state 句首写事件；判断提示词（`phase_locate.py:114`）让模型「取最后发生的那一件」。改后该判定只由 `core/phase_locate.py` 的常量 `KEY_MOMENT_START` 定义一次；维度 L 规则与 `judge_prompt` 都拼接它，G3 源码锁守全仓 `.py` 只出现一次。
2. **调用方是否被迫知道不该知道的事？** 否。`locate()` 的入参仍是草稿阶段 dict，只新增读取 `key_moments[0]`；签名不变。依赖组的 `_phase_list_note`（`distiller.py:510-522`）不渲染 key_moments（D1）；`ArcPhase` / 卡 / 存库 JSON 仍看不到它（K7 实跑，G4 锁）。`judge_prompt` 签名不变，label + state 仍作背景。
3. **加一个阶段字段要改几处？** 4 处生产代码 + 夹具：① `card_draft.py` `DraftPhase` 字段与 `json_schema_extra`（必填）；② `distiller.py` 维度 L 规则；③ `distiller.py` JSON 模板 + `_FORMAT_IMPORTANCE` G6 形态规则；④ `phase_locate.py` 常量、提示词、空列表跳过。夹具：`test_phase_locate_goal._PHASES`、`test_replay_distill_goal._ARC`、`test_longctx_focused_calls_goal._ARC`、F1 fixture（重录）。转卡无需改（K7）。

### 10.3 失效的锁与夹具（D4）

| 文件 | 守什么 | 为什么失效 | 怎么改（守的东西不变） |
|---|---|---|---|
| `tests/test_phase_locate_goal.py` `_PHASES` | 阶段 2..n 用它定位 | 新契约下无 key_moments 的阶段不再回落 state | 给阶段 2/3 补 `key_moments`（取该阶段起点事件） |
| 同上 `_Judge` 正则 | 从提示词取 label 作替身键 | 提示词仍保留 `角色弧线的一个阶段：{label}：{state}` 一行，**不失效** | 不改 |
| `tests/test_replay_distill_goal.py` `_ARC` | 重定位按阶段起点章走 | 同上，无 key_moments 不重定位 | 给阶段 2–4 补 key_moments |
| `tests/test_longctx_focused_calls_goal.py` `_ARC` + fixture | G6 system 逐字与组调用形态 | G6 规则 / 模板新增 key_moments 行 | 补 `_ARC` 的 key_moments，按 D5 重录 fixture |
| `tests/test_phase1_anchor_not_checked_goal.py` | 阶段 1 锚点不参与核对 | 直接用上面的 `_PHASES` | 随 `_PHASES` 更新，无需改代码 |
| `tests/test_distill_diagnostic_logs_goal.py` | 候选明细日志 | 同上（import PL） | 随 `_PHASES` 更新，无需改代码 |
