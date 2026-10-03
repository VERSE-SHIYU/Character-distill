# arc-behaviors-draft：模型输出契约（草稿）与唯一出口

> 基线：`feat/arc-behaviors-s1` @ `5b2e1c62`。本分支：`feat/arc-behaviors-draft`（第一个提交即全部生产代码改动，执行方**不重写**，只按 §4 补测试、按 §5 跑变异、按 §7 跑小样）。
> 进度写在本文件 §8，补充写在 §9，不留在聊天里。

## 0. 目的

第 3 步注入时「第 k 阶段该给模型哪些做法」必须答对：当前阶段的做法 + 从头到尾都成立的做法，不能混进别的阶段专属的做法。5b2e1c6 上的缺陷是顶层「通用做法」里混入了阶段专属做法（孔乙己最后阶段仍拿到「被逼急就争辩」）。

根因：让模型判断「这个做法是否从头到尾都成立」（概括题），并给它两个可写的位置，它两边都写；同时发给模型的 JSON 结构取自存卡模型 `CharacterCard`，结构本身就写着「阶段下有 behaviors」。

## 1. 已拍板的决定

| # | 决定 |
|---|---|
| D1 | 模型写**草稿**：阶段只写 label/state；做法写成一张 `situation_behaviors`，每条只写一次，带 `phases`（维度 L 的阶段编号，从 1 开始）。「通用」由代码算：所有阶段都标了 → 顶层；否则挂到标了的阶段下；没有阶段 → 全部顶层。 |
| D2 | 草稿是独立的输出契约（`core/card_draft.py` 的 `CardDraft`），不在存卡模型上加校验器（方案 A 已否：发给模型的结构会与提示词自相矛盾）。 |
| D3 | 两处唯一出处：发给模型的结构只从 `draft_schema()` 取；模型输出转卡只经 `card_from_draft()`。`distill_incremental_stream` 的每条路径都交出草稿，转换只在消费方。 |
| D4 | 阶段编号不合法（越界、空）：沿用 `core.card_quotes.retract_unverified` 对模型输出的口径——对不上的撤回、打 warning，卡照常落；一条做法的编号全部作废则整条撤回。 |
| D5 | 存卡结构、PG、详情页、编辑弹窗、摘录核对都不动；不新增依赖、不碰 SQLite。 |

## 2. 已查实约束（坐标为本分支第一个提交上的行号；S0 由执行方逐条复核，任一不成立即停下报告）

C1. 发给模型的 JSON 结构有 5 个来源，全部改为 `draft_schema`：`core/distiller.py` 1471、1502、1572、2178（整张），2487（分组）。原 `core/schema.py` 的 `format_group_schema` 已移入 `draft_schema(group)`，仓内无其他使用者（tests 仅 `test_distiller_routing.py` 一处，已同步）。

C2. 模型输出转卡的入口共 5 处，全部改为 `card_from_draft`：
- 同步：`core/distiller.py` 1491（`distill`）、1598（`_distill_longcontext`）、2201（`distill_incremental` 格式化）；
- 流式消费：`web/routers/distill.py` 507（`/start` 的后台任务 `_run_distill_task`）、1126（`/run_stream`）。

C3. `distill_incremental_stream` 有两条交出成品的路径：长上下文一次读完（`_distill_longcontext_stream`，交出模型原文 token，即草稿）；分片后按组格式化（2535 起，原先校验成 `CharacterCard` 再 dump，现改为 `CardDraft.model_validate` 后 dump 草稿，保留本路径原有的 error 帧口径）。两条都交草稿，消费方统一转换。

C4. 同步的 `distill` / `distill_stream` / `distill_incremental` 在生产代码里没有调用方，只被测试使用；仍改走 `card_from_draft`（同一件事只有一处）。

C5. 前端只调 `POST /api/distill/start`（`web/frontend/src/store/useAppStore.js:846`）；`/run_stream`（`web/routers/distill.py:1025`）前端未用但仍是线上接口，一并接入、一并测。

C6. 其余 `CharacterCard.model_validate*` 调用（`card_quotes.py:153`、`card_relationships.py:51`、`distiller.py:1760`/`2213`、`routers/distill.py:523`/`1251`、`market.py:837`、各处读库的 `model_validate_json`）读写的都是**存卡形态**，不涉及模型输出，不改。

C7. `CharacterCard` 与其子模型未设 `model_config`（Pydantic 默认丢弃未知键）。`card_from_draft` 依赖这一点把草稿里的 `phases` 在落存卡时丢掉；`CardDraft` 只设了 `title="CharacterCard"`（让发给模型的结构标题不变）。

C8. 旧形态容错（弧线为字符串列表 / 阶段为字符串）移到共用底 `ArcAxis` / `PhaseState`（`core/schema.py`），存卡与草稿复用。理由：`tests/perf/mock_llm_server.py:101` 输出 `"character_arc": []`，不复用的话草稿校验会直接报错。

C9. Pydantic 版本 `2.13.5`（`requirements.txt:259`）。

C10. 环境冲突：测试库由 `docker-compose.test.yml` 起，端口 55432、库名 `charsim_test`、项目名 `character-distill-test`（顶层 `name:` 让它与 `docker-compose.local.yml` 的 `cd-role-postgres-1` 不撞名，见该文件第 13–17 行）。数据目录是 tmpfs，容器一停即清空。执行方起库前先 `docker ps` 确认 55432 没有被别的容器占用，占用就停下报告，不要去停别人的容器。

### 2.1 路径机制表（通道 × 执行上下文 × 守它的测试）

本次只改「模型输出 → 卡」这一步，不改任何线程、协程、计时、记账；表里列出每条通道上这一步跑在哪里，确认转换函数是纯计算（无 IO、无 loop 依赖），放在哪个上下文里都成立。

| 通道 | 转换发生处 | 执行上下文 | 守它的测试 |
|---|---|---|---|
| `/start` 后台任务 | routers/distill.py:507 | `C.ctx_thread` 派生的同步线程（:869） | R1 |
| `/run_stream` | routers/distill.py:1126 | 请求 loop 上的 async 生成器（流由 `asyncio.to_thread(_next_piece)` 驱动，:1085；转换本身在 loop 上同步执行，纯计算、无 IO） | R2 |
| 流式·一次读完 | 不转换，交草稿 | 同上两条的驱动方 | E4 |
| 流式·分组 | 不转换，`CardDraft.model_validate` 后交草稿（:2535） | 各组在 `C.ctx_thread` 线程里生成（:2515），合并与校验在生成器所在线程 | E5 |
| 同步三入口 | distiller.py:1491 / 1598 / 2201 | 调用方线程（生产无调用方，见 C4） | E1–E3 |

### 2.2 规模表

| 数据 | 上限与来源 | 展示/处理策略 |
|---|---|---|
| 阶段数 | 提示词「通常 2-3 个、最多 4 个」（distiller.py 维度 L）；schema 不设硬上限 | 前端 `common/ArcList` 原样列出（本次不改） |
| 做法条数 | 提示词「6-12 条」（维度 O）；schema 不设硬上限 | 分发后挂在阶段下或顶层，前端 `common/BehaviorList` 原样列出（本次不改） |
| 每条的 `phases` | 元素须在 1..阶段数；去重后计数 | 越界撤回 + warning（D4）；全阶段 → 顶层 |
| G6 组输出 | 与其他组同用 `CARD_MAX_TOKENS` 档（distiller.py 分组格式化），本次不改 | 草稿比旧形态每条做法多一个短数组，阶段下少一层嵌套，总量相当 |

### 2.3 出处对照表

| 依据 | 条目 | 本 spec 对应 |
|---|---|---|
| Pydantic 文档 Validators（[v2.9 concepts/validators](https://pydantic.dev/docs/validation/2.9/concepts/validators/)），本仓锁定 2.13.5 | before 校验器在内部解析之前运行，用于输入形态归一 | C8（`PhaseState` / `ArcAxis` 的旧形态容错） |
| 结构化输出的通行做法：交给模型的 Pydantic 模型即输出契约，字段名、描述、类型本身就在引导模型（[Stop Parsing JSON by Hand, DEV](https://dev.to/klement_gunndu/stop-parsing-json-by-hand-structured-llm-outputs-with-pydantic-1pg0)） | 发给模型的结构与模型被要求写的形态必须是同一个模型 | D2、D3、C1、U8 |
| 本仓先例 `core/card_quotes.py` 的 `retract_unverified` | 模型输出对不上：撤回 + warning，卡照常落 | D4、U3、U4 |
| 本仓先例 `core/distiller.py` 的 `finalize_card`（「三条产卡通道各接一次 → 合成一个入口」） | 同一件事只有一个出口，避免某条通道漏掉一步而成品看不出来 | D3、S1 |

全量扫描命令与输出（第一个提交上运行）：

```
$ git grep -n -E "CharacterCard\.model_validate|model_json_schema|draft_schema|card_from_draft|CardDraft" -- core web mcp_server scripts adapters storage
$ git grep -n -E "\.distill\(|\.distill_stream\(|\.distill_incremental\(|distill_incremental_stream\(" -- core web mcp_server scripts
$ git grep -n -E "/api/distill/(run_stream|start)\b" -- web/frontend/src
```

（输出见 §9 附录 A；执行方 S0 重跑一遍，与附录逐行比对。）

## 3. 本分支已做的改动（执行方不重写）

| 文件 | 改动 |
|---|---|
| `core/card_draft.py`（新） | `DraftBehavior`、`DraftArc`、`CardDraft`、`draft_schema()`、`card_from_draft()` |
| `core/schema.py` | 抽出 `PhaseState`、`ArcAxis` 两个共用底；删除 `format_group_schema`（移入 `draft_schema`）；存卡结构不变 |
| `core/distiller.py` | 维度 L/O 提示词、G6 模板与格式说明改为草稿形态；5 处 schema 来源、3 处同步转换、分组路径交草稿 |
| `web/routers/distill.py` | 两个消费点改为 `card_from_draft` |
| `tests/test_card_arc_behaviors.py`、`tests/test_distiller_routing.py` | 两条旧断言随契约更新 |
| `AGENTS.md` | 记录本次契约 |

沙箱基线：§6 的受影响测试在 `5b2e1c62` 上 235 passed，在本分支第一个提交上 235 passed（PG 16，55432）。

## 4. 测试计划（执行方写，先红后绿）

新文件 `tests/test_card_draft.py`；路由两项放进 `tests/test_distill_task_api.py`（复用该文件的 `_run_to_card` / `_build_app`）。**路由测试不用 `SQLiteStore`**：store 用该文件已有的内存 `_FakeStore`，`routers.distill.resolve_characters` 打桩成返回名单的协程，TextManager 用桩记下收到的卡。

共用夹具：一份孔乙己草稿 `KONG_DRAFT`——两个阶段；做法 A「被取笑→争辩」`phases=[1]`，做法 B「讨酒→排出九文」`phases=[1, 2]`，做法 C「被问断腿→不分辩」`phases=[2]`。期望卡 `KONG_CARD`：顶层只有 B；阶段 1 下只有 A；阶段 2 下只有 C；任何做法里都没有 `phases` 键。

### 4.1 单元（`card_from_draft` / `draft_schema`）

| 编号 | 用例 | 断言 |
|---|---|---|
| U1 | 所有阶段都标了 | 留在顶层，阶段下没有 |
| U2 | 只标部分阶段 | 只挂到标了的阶段，顺序保持 |
| U3 | 部分编号越界（如 `[1, 9]`） | 合法的留下；一条 warning（caplog 断言含「阶段编号不合法」） |
| U4 | 编号全不合法 / 空数组（有阶段时） | 整条撤回；一条 warning |
| U5 | 没有阶段的卡 | 全部顶层，`phases` 不看，无 warning |
| U6 | 旧形态：`character_arc: []`、字符串列表 | 草稿照常转卡（mock_llm_server 的输出形态） |
| U7 | 形态不对（如 `situation_behaviors` 是字符串） | 抛 `pydantic.ValidationError` |
| U8 | `draft_schema()` / `draft_schema("G6")` | `DraftBehavior` 有 `phases`；`PhaseState` 无 `behaviors`；整张的 title 为 `CharacterCard`；分组只含本组字段 |
| U9 | 落卡后 `model_dump()` | 任何做法里没有 `phases` 键；`CharacterCard.model_validate(dump).model_dump() == dump` |

### 4.2 调用点矩阵（每个入口喂 `KONG_DRAFT`）

| 入口 | 坐标 | 观测 a：产物 | 观测 b：形态不对时 | 测试名 |
|---|---|---|---|---|
| E1 `Distiller.distill` | distiller.py:1491 | == `KONG_CARD` | `DistillError` | `TestEntries::test_distill_sync` |
| E2 `_distill_longcontext`（经 `distill_incremental`，阈值调大） | :1598 | == `KONG_CARD` | `DistillError` | `TestEntries::test_longcontext_sync` |
| E3 `distill_incremental` 格式化（阈值 0） | :2201 | == `KONG_CARD` | `DistillError` | `TestEntries::test_incremental_format_sync` |
| E4 流式·一次读完 | `_distill_longcontext_stream` | 交出的 str 拼起来按 `CardDraft` 解析，`phases` 原样保留（未被转换） | — | `TestEntries::test_stream_longcontext_yields_draft` |
| E5 流式·分组 | :2535 | 同 E4 | 交出 error 帧，不交 str | `TestEntries::test_stream_grouped_yields_draft` |
| R1 `/start` 后台任务 | routers/distill.py:507 | TextManager 收到的卡 == `KONG_CARD`（`finalize_card` 用桩原样返回） | 任务 error，消息「蒸馏失败：数据校验错误，请重试」 | `TestDraftConversion::test_bg_task_converts_draft` |
| R2 `/run_stream` | :1126 | 同 R1 | SSE error 帧同上 | `TestDraftConversion::test_run_stream_converts_draft` |

E1–E3 的 LLM 打桩方式照 `tests/test_distiller_routing.py` 现成写法（`_map_distiller`、`_chat_accounted` 返回 `(json, False)`）；E5 照该文件 `TestFormatGroupsRunInParallel` 的按组回包写法。

### 4.3 结构与落库

| 编号 | 断言 |
|---|---|
| S1 | `git grep` 断言：`model_json_schema(` 在 `core/ web/` 里只出现在 `core/card_draft.py`；`card_from_draft(` 的调用点集合恰为 C2 的 5 处。以后新增入口没走它，这条红 |
| P1 | PG 往返：`KONG_CARD.model_dump_json()` 经 `PostgresStore.save_card` 存、`get_card_unscoped` 读，`CharacterCard.model_validate_json` 后与原卡相等（建用户/文本照 `tests/test_comment_likes.py` 的写法） |

**先红后绿**：§4 的新测试在 `5b2e1c62` 上应失败（`core.card_draft` 不存在 / 产物不对），在本分支上全绿。两边结果都贴进 §8。

## 5. 变异清单（执行方逐条跑，每条必须让所列测试变红；有存活的停下报告）

驱动脚本：`tests/perf/card_draft_mutations.py`（照 tests/perf 既有约定：先验基线全绿才开跑、锚点恰一命中、逐字节还原并核 sha256）。我已在沙箱 PG 16 上实跑，结果见 §8；执行方 S0 复跑一遍核对，不必另找漏洞。

| 编号 | 变异 | 应红 |
|---|---|---|
| M1 | `card_from_draft`：`len(valid) == count` → `len(valid) >= 1` | U2、E1 |
| M2 | warning 条件去掉 `or not valid`（空数组不再告警） | U4 |
| M3 | 范围判断 `1 <= p <= count` → `0 <= p <= count` | U3 |
| M4 | 删掉 warning 那一行 | U3、U4 |
| M5 | routers/distill.py:507 改回 `CharacterCard.model_validate(data)` | R1、S1 |
| M6 | routers/distill.py:1126 改回 `CharacterCard.model_validate(data)` | R2、S1 |
| M7 | distiller.py:1491 改回 `CharacterCard.model_validate(data)` | E1、S1 |
| M8 | distiller.py:2535 改回 `card_from_draft(merged)` 并 dump 卡 | E5 |
| M9 | distiller.py:1471 改回 `CharacterCard.model_json_schema()` | S1 |
| M10 | 删掉 `PhaseState._from_legacy_string` | U6 |
| M11 | 删掉 `ArcAxis._from_legacy_list` | U6 |
| M12 | G6 模板去掉 `"phases": [1, 2]` | `test_card_arc_behaviors::test_g6_prompt_carries_arc_and_behaviors_together`（执行方补一句断言：G6 片段含 `"phases": [1, 2]`） |

## 6. 本地命令（只跑受影响的文件；合并门是分支 CI）

```powershell
docker compose -f docker-compose.test.yml up -d
python -m pytest -q tests/test_card_draft.py tests/test_card_arc_behaviors.py tests/test_card_quotes.py tests/test_distill.py tests/test_distill_output_bounds.py tests/test_distill_progress.py tests/test_distill_resume.py tests/test_distill_task_api.py tests/test_distill_usage_accounting.py tests/test_distiller_dialogue_pick.py tests/test_distiller_routing.py tests/test_distiller_truncation_selfheal.py tests/test_distiller_utils.py tests/test_schema_parity.py tests/test_router_unified_exits.py tests/test_identify_failure_channels.py tests/test_error_user_facing.py tests/test_admin_user_delete_distill.py
cd web/frontend; npm test
```

不跑本地全量；报告里不出现全量数字。合并只做 git 操作。

## 7. 真实模型小样闸（只有本地能跑）

在本地用自己的 key、deepseek-flash，经 `/api/distill/start` 蒸下列角色（文本均为鲁迅作品，已进入公有领域，取自维基文库；原文只放本地临时目录，不入库）。每张卡把落库的 `card_json` 导出成文件，用 `tests/perf/arc_draft_sample_check.py` 判定，**不手判**：

| 样本 | 测什么 | 命令 |
|---|---|---|
| 《孔乙己》孔乙己 | 早期做法不落到最后阶段（本缺陷的原始用例） | `python tests/perf/arc_draft_sample_check.py 孔乙己.json --expect-phases 2+ --forbid-late 争辩 --log <蒸馏期间后端日志>` |
| 《阿Q正传》阿Q | 阶段多时也能按阶段归位 | `... 阿Q.json --expect-phases 3+ --log <日志>` |
| 《孔乙己》掌柜 | 没有变化的角色不硬分阶段，做法全在顶层 | `... 掌柜.json --expect-phases 0 --log <日志>` |
| 《阿Q正传》赵太爷 | 原文里有真实变化（第七章「革命」改口叫「老Q」）的角色，模型能分出阶段 | `... 赵太爷.json --expect-phases 2+ --log <日志>` |

判据（脚本内同号）：

| 判据 | 内容 |
|---|---|
| G1 | 日志里 `[card_draft] 做法的阶段编号不合法` 出现 0 次 |
| G2 | 做法条数 ≥ 3（按 situation 去重） |
| G3 | 更早阶段里含 `--forbid-late` 词的做法存在，**且**这些做法的 situation 不出现在「最后阶段 ∪ 顶层」里（按条目认，不按词搜后面的阶段：后期做法常写成「不再争辩」，按词搜会误报） |
| G4 | 期望无弧线：没有阶段，做法全在顶层 |
| G5 | 每个阶段、顶层各自内部没有两条相同 `situation` |
| G6 | 期望 N+ 个阶段：阶段数 ≥ N，且至少一个阶段下挂着只属于它的做法 |

说明：
- 赵太爷原先被当作「无弧线」样本，蒸出来分了阶段。核对原文，他确有态度变化，错在样本选择而不在模型；现改为检验「模型能识别真实变化」。
- G3 用关键词找早期条目，模型若换了说法（如「辩解」）会找不到而判不过；四张卡的 `character_arc` 与 `situation_behaviors` 仍原样贴进 §8，由审计方对照原文复核。
- 掌柜素材少，做法条数若低于 3（G2 不过），照实记录条数，不改提示词。
- 判定脚本自身已验过：造好的好卡全过、早期做法混进顶层的坏卡 G3 不过、有阶段的卡按无弧线判 G4 不过；另用审计方对照原文写的四张参考卡（`docs/specs/arc-behaviors-draft-reference.json`）跑过，四张全过。


**参考卡（审计用）**：`docs/specs/arc-behaviors-draft-reference.json` 是审计方对照原文、按草稿格式手写的四个角色（摘录已逐条核对为原文逐字子串）。它不是模型产物，只用于两件事：验证判定脚本本身不误判；审计时对照模型卡的阶段划分与做法归属。模型卡不要求与它逐字一致。

四个样本全部判定通过才开 PR；任一不过就停下报告，**不自行改提示词**（属于设计问题）。

## 8. 进度

**我已完成（沙箱，PG 16 @ 55432，提交见分支历史）**

- [x] §4 新测试已写：`tests/test_card_draft.py`（21 条）+ `tests/test_distill_task_api.py::TestDraftConversion`（2 条）。路由两条用内存 `_FakeStore`，不碰 SQLite；P1 连 PG。
- [x] 先红后绿：在 `5b2e1c62` 上收集即失败（`ModuleNotFoundError: core.card_draft`）；在本分支上全绿。
- [x] §6 受影响测试 + 新测试：**258 passed**（基线 235 + 新增 23）。
- [x] §5 变异 M1–M12：**全部被打红**，还原后 4 个文件 sha256 与开跑前一致。首轮 M2（删掉 `if not valid: continue`）存活 —— 证明那两行是多余分支（`valid` 为空时既到不了顶层也挂不到阶段，本来就整条撤回），已删掉，M2 改为打 warning 条件。
- [x] 前端未改动（`git diff 5b2e1c62 -- web/frontend` 为空），`npm test` 未在沙箱跑，由执行方跑。

**执行方待做**

- [ ] 交接：`Test-Path docs/specs/arc-behaviors-draft.md` 为 True；`git log --oneline -3` 与远端分支一致
- [ ] S0：C1–C10 逐条复核；附录 A 重跑比对（行号以本分支最新提交为准，若与附录有出入，逐条写明）
- [ ] §6 受影响测试（应为 258 passed）+ `npm test`
- [ ] 复跑 `python tests/perf/card_draft_mutations.py`，贴结论行
- [ ] §7 小样四张卡：`arc_draft_sample_check.py` 的输出原样贴入，卡片内容原样贴入
- [ ] 推分支，分支 CI 号

## 9. 补充

本段改动面内新发现的问题直接修，写进这里；会撞车或需要拍板的停下报告，不自行记账。

### 附录 A：全量扫描输出

（见下一节，由第一个提交上运行生成。）

```
$ git grep -n -E "CharacterCard\.model_validate|model_json_schema|draft_schema|card_from_draft|CardDraft" -- core web mcp_server scripts adapters storage
core/card_quotes.py:153:    return CharacterCard.model_validate(dump), retracted
core/card_relationships.py:51:    return CharacterCard.model_validate(dump), dropped
core/distiller.py:53:from core.card_draft import CardDraft, card_from_draft, draft_schema
core/distiller.py:1471:            schema_obj = draft_schema()
core/distiller.py:1491:            return card_from_draft(data)
core/distiller.py:1502:            schema_obj = draft_schema()
core/distiller.py:1572:                draft_schema(), ensure_ascii=False, indent=2)
core/distiller.py:1598:            return card_from_draft(data)
core/distiller.py:1760:        return CharacterCard.model_validate(card_dict)
core/distiller.py:2178:            schema_obj = draft_schema()
core/distiller.py:2201:            card = card_from_draft(data)
core/distiller.py:2213:                card = CharacterCard.model_validate(card_dict)
core/distiller.py:2476:        # 在发起调用的那一级记账）。合并 → 按草稿校验（`CardDraft.model_validate`）→ **一个**
core/distiller.py:2478:        # 调 `card_from_draft` 一处（两条消费路径都从累加串里 parse，见 web/routers/distill.py）。
core/distiller.py:2487:                    draft_schema(group), ensure_ascii=False, indent=2
core/distiller.py:2535:            draft = CardDraft.model_validate(merged)
core/schema.py:74:    选，由 `core.card_draft.card_from_draft` 按模型标的阶段分发。
core/text_manager.py:452:                        card = CharacterCard.model_validate_json(c["card_json"])
mcp_server/server.py:168:            card = CharacterCard.model_validate_json(card_rec["card_json"])
scripts/diag_tool_use.py:48:    return CharacterCard.model_validate_json(rec["card_json"])
scripts/run_agent_eval.py:618:    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/chat.py:174:    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/distill.py:29:from core.card_draft import card_from_draft
web/routers/distill.py:506:            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。
web/routers/distill.py:507:            card = card_from_draft(data)
web/routers/distill.py:523:                card = CharacterCard.model_validate(card_dict)
web/routers/distill.py:1125:            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。
web/routers/distill.py:1126:            card = card_from_draft(data)
web/routers/distill.py:1251:        validated = CharacterCard.model_validate(req.card_json)
web/routers/distill.py:1302:    card = CharacterCard.model_validate_json(record["card_json"])
web/routers/distill.py:1358:    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/group.py:144:                    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/group.py:159:            card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/group.py:359:        card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/history.py:206:    card = CharacterCard.model_validate_json(card_rec["card_json"])
web/routers/market.py:837:    char = CharacterCard.model_validate(_json.loads(card_json_str))

$ git grep -n -E "\.distill\(|\.distill_stream\(|\.distill_incremental\(|distill_incremental_stream\(" -- core web mcp_server scripts
core/distiller.py:2219:    def distill_incremental_stream(
web/routers/distill.py:407:        stream = distiller.distill_incremental_stream(
web/routers/distill.py:1081:        stream = distiller.distill_incremental_stream(

$ git grep -n -E "/api/distill/(run_stream|start)" -- web/frontend/src
web/frontend/src/components/DistillTaskBar.jsx:81:  // resume 与 retry 打同一个端点（POST /api/distill/start），续跑 vs 整批重跑由后端
web/frontend/src/store/useAppStore.js:846:      const data = await postJSON('/api/distill/start', { text_id: textId, character_name: characterName, force })

$ git grep -n "@router.post(\"/run_stream\")\|@router.post(\"/start\")" -- web/routers/distill.py
web/routers/distill.py:723:@router.post("/start")
web/routers/distill.py:1025:@router.post("/run_stream")

$ git grep -n "character_arc" -- tests/perf/mock_llm_server.py
tests/perf/mock_llm_server.py:101:    "character_arc": [], "tags": [],

$ git grep -n -E "model_config|extra=" -- core/schema.py core/card_draft.py
```
