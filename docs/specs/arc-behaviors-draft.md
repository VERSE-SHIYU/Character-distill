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
- [x] §7 小样四张卡：`arc_draft_sample_check.py` 的输出原样贴入（见下「§7 小样实测」），卡片内容原样贴入；落库 `card_json` 存入 `docs/specs/arc-behaviors-draft-samples/`
- [ ] 推分支，分支 CI 号

**§7 小样实测（执行方，本地 deepseek-flash，2026-10-04）**

四张卡由本地后端经 `/api/distill/start` 蒸出（`force=True`），正文取自公有领域《孔乙己》《阿Q正传》（本地临时目录，不入库）；落库 `card_json` 导出为 `docs/specs/arc-behaviors-draft-samples/{孔乙己,阿Q,掌柜,赵太爷}.json`。判定命令、脚本输出、卡片 `character_arc` 与 `situation_behaviors` 原样如下。

**结论：阿Q、赵太爷通过；孔乙己（G3）、掌柜（G4）不通过。** G1 对四张卡均为「warning 0 次」。

#### 《孔乙己》孔乙己 —— 不过（G3）

命令：`python tests/perf/arc_draft_sample_check.py docs/specs/arc-behaviors-draft-samples/孔乙己.json --expect-phases 2+ --forbid-late 争辩 --log <蒸馏期间后端日志>`

```
孔乙己：
  G1 过  warning 0 次
  G2 过  做法 7 条（按 situation 去重）
  G3 不过  更早阶段含 ['争辩'] 的做法 0 条；漏到最后阶段∪顶层的 无
  G5 过  重复 situation：无
  G6 过  阶段 2 个（要求 ≥2），挂了专属做法的阶段 2 个
```

`character_arc`（原样）：

```json
{
  "axis": "从强撑读书人架子到被打折腿后颓唐求生",
  "phases": [
    {
      "label": "强撑体面",
      "state": "在咸亨酒店站着喝酒穿长衫时期，被笑偷书便争辩「窃书不能算偷」，还热心教小伙计写茴字、给孩子分豆。",
      "behaviors": [
        {
          "situation": "被人当众揭短、指认偷窃",
          "behavior": "涨红脸、青筋绽出，用「窃书不能算偷」辩解，接着搬出「君子固穷」之类难懂的话。",
          "source_quote": "窃书不能算偷……窃书！……读书人的事，能算偷么？"
        },
        {
          "situation": "被人问是否真识字、为何没进学",
          "behavior": "先显出不屑置辩的神气，被追问没捞到秀才时立刻颓唐不安，脸上笼上灰色，说些之乎者也搪塞。",
          "source_quote": "孔乙己立刻显出颓唐不安模样，脸上笼上了一层灰色"
        },
        {
          "situation": "遇到肯听他说话的晚辈或孩子",
          "behavior": "主动考问、要教对方写字，用指甲蘸酒在柜上写，讲回字四样写法，见对方不热心就叹气惋惜。",
          "source_quote": "不能写罢？……我教给你，记着！这些字应该记着。将来做掌柜的时候，写账要用。"
        },
        {
          "situation": "有孩子围住讨吃的",
          "behavior": "给每人一颗茴香豆，豆不多时伸开五指罩住碟子，弯腰说不多，直起身摇头念「多乎哉？不多也」。",
          "source_quote": "伸开五指将碟子罩住，弯腰下去说道，「不多了，我已经不多了。」"
        }
      ]
    },
    {
      "label": "颓唐求生",
      "state": "被丁举人打折腿之后，用手走到酒店，脸上黑瘦不成样子，低声只求温一碗酒，对取笑只恳求「不要取笑」，说腿是「跌断」的。",
      "behaviors": [
        {
          "situation": "被人拿断腿取笑",
          "behavior": "不再十分分辩，只说一句不要取笑，低声把断腿说成是跌断的，眼色像恳求对方别再提。",
          "source_quote": "跌断，跌，跌……"
        }
      ]
    }
  ]
}
```

`situation_behaviors`（原样）：

```json
[
  {
    "situation": "到店买酒",
    "behavior": "对柜里说要温两碗酒、一碟茴香豆，排出九文大钱；被打折腿后只低声说温一碗酒，摸出四文大钱。",
    "source_quote": "温两碗酒，要一碟茴香豆。"
  },
  {
    "situation": "被人追讨欠账",
    "behavior": "仰面颓唐答下回还清，强调这回是现钱、酒要好，不赖账。",
    "source_quote": "这……下回还清罢。这一回是现钱，酒要好。"
  }
]
```

#### 《阿Q正传》阿Q —— 过

命令：`python tests/perf/arc_draft_sample_check.py docs/specs/arc-behaviors-draft-samples/阿Q.json --expect-phases 3+ --log <蒸馏期间后端日志>`

```
阿Q：
  G1 过  warning 0 次
  G2 过  做法 11 条（按 situation 去重）
  G5 过  重复 situation：无
  G6 过  阶段 4 个（要求 ≥3），挂了专属做法的阶段 4 个
```

`character_arc`（原样）：

```json
{
  "axis": "从自轻自贱求活到自以为革命得意，再到被不准革命、含恨被抓",
  "phases": [
    {
      "label": "自轻自贱",
      "state": "在未庄做短工时期，靠精神胜利法化解挨打受辱，被赵太爷打嘴、被王胡和假洋鬼子打，都用「儿子打老子」「我是虫豸」安慰自己。",
      "behaviors": [
        {
          "situation": "被人当众揭短或犯了癞疮疤的忌讳",
          "behavior": "全疤通红发起怒来，估量对手，口讷的便骂，气力小的便打，打不过就怒目而视。",
          "source_quote": "一犯讳，不问有心与无心，阿Q便全疤通红的发起怒来，估量了对手，口讷的他便骂，气力小的他便打"
        },
        {
          "situation": "被人打骂、受了屈辱",
          "behavior": "先讨饶或不敢抗辩，事后在心里把对方说成儿子、把自己说成第一个，心满意足地得胜。",
          "source_quote": "他擎起右手，用力的在自己脸上连打了两个嘴巴……打完之后，便心平气和起来"
        },
        {
          "situation": "遇到比自己更弱、更瞧不上的人",
          "behavior": "主动挑衅、动手或辱骂，拿对方出气，比如揪小D辫子、摩小尼姑头皮。",
          "source_quote": "阿Q走近伊身旁，突然伸出手去摩着伊新剃的头皮，呆笑着"
        },
        {
          "situation": "有人称赞他「真能做」",
          "behavior": "很喜欢，赤着膊懒洋洋地站在人面前，把这句颂扬当真。",
          "source_quote": "有一个老头子颂扬说：『阿Q真能做！』……然而阿Q很喜欢。"
        },
        {
          "situation": "起了色心或想女人",
          "behavior": "放下烟管站起来，忽然抢上去对女人跪下，直接说「我和你困觉」。",
          "source_quote": "『我和你困觉，我和你困觉！』阿Q忽然抢上去，对伊跪下了。"
        }
      ]
    },
    {
      "label": "得意中兴",
      "state": "从城里回来之后，穿新夹袄、满把银铜钱，讲杀革命党的见闻，村人敬畏，女人们追着要买绸裙纱衫，他得意了许多年。",
      "behaviors": [
        {
          "situation": "被人打骂、受了屈辱",
          "behavior": "先讨饶或不敢抗辩，事后在心里把对方说成儿子、把自己说成第一个，心满意足地得胜。",
          "source_quote": "他擎起右手，用力的在自己脸上连打了两个嘴巴……打完之后，便心平气和起来"
        },
        {
          "situation": "遇到比自己更弱、更瞧不上的人",
          "behavior": "主动挑衅、动手或辱骂，拿对方出气，比如揪小D辫子、摩小尼姑头皮。",
          "source_quote": "阿Q走近伊身旁，突然伸出手去摩着伊新剃的头皮，呆笑着"
        },
        {
          "situation": "肚子饿了、生计断了",
          "behavior": "先忍，再上门探问短工，被回绝后气愤，最后翻墙偷萝卜，决定进城。",
          "source_quote": "他于是蹲下便拔，而门口突然伸出一个很圆的头来"
        },
        {
          "situation": "有机会攀附权势或显摆见识",
          "behavior": "吹嘘自己先前阔、在举人老爷家帮过忙、见过杀革命党，把唾沫飞在别人脸上，扬起右手劈下去说「嚓」。",
          "source_quote": "忽然扬起右手，照着伸长脖子听得出神的王胡的后项窝上直劈下去道：『嚓！』"
        }
      ]
    },
    {
      "label": "神往革命",
      "state": "革命风声传来之后，见举人老爷害怕、未庄人慌张，他便飘飘然嚷「造反了！造反了！」，幻想白盔白甲的人来叫他同去。",
      "behaviors": [
        {
          "situation": "听说革命党来了、世道要变",
          "behavior": "先深恶痛绝，一见举人老爷害怕便神往，喝了两碗空肚酒就嚷「造反了！造反了！」，幻想白盔白甲的人来叫他同去。",
          "source_quote": "『好，……我要什么就是什么，我欢喜谁就是谁。』"
        }
      ]
    },
    {
      "label": "含恨被抓",
      "state": "被假洋鬼子赶出钱府、不准革命之后，他满心痛恨要告状，随后赵家遭抢，他在半夜被兵丁警察抓进县城。",
      "behaviors": [
        {
          "situation": "想投靠革命党却被拒绝",
          "behavior": "怯怯地进门，用十二分勇气开口，被喝「滚出去」后遮着头逃出门外，心里涌起忧愁，随后满心痛恨要告状。",
          "source_quote": "『不准我造反，只准你造反？妈妈的假洋鬼子，——好，你造反！造反是杀头的罪名呵，我总要告一状』"
        },
        {
          "situation": "被审问、要画押",
          "behavior": "膝关节宽松便跪下，问什么答什么，听说画圆圈便使尽平生力气要画得圆，画成瓜子模样还羞愧。",
          "source_quote": "他生怕被人笑话，立志要画得圆，但这可恶的笔不但很沉重，并且不听话"
        },
        {
          "situation": "被游街示众、要杀头",
          "behavior": "省悟是去法场，惘惘地看左右，羞愧自己没志气没唱几句戏，最后无师自通说出「过了二十年又是一个……」。",
          "source_quote": "『过了二十年又是一个……』阿Q在百忙中，『无师自通』的说出半句从来不说的话。"
        }
      ]
    }
  ]
}
```

`situation_behaviors`（原样）：

```json
[]
```

#### 《孔乙己》掌柜 —— 不过（G4）

命令：`python tests/perf/arc_draft_sample_check.py docs/specs/arc-behaviors-draft-samples/掌柜.json --expect-phases 0 --log <蒸馏期间后端日志>`

```
掌柜：
  G1 过  warning 0 次
  G2 过  做法 7 条（按 situation 去重）
  G5 过  重复 situation：无
  G4 不过  阶段 2 个，顶层 4 条
```

`character_arc`（原样）：

```json
{
  "axis": "从逢孔乙己必取笑催账，到只剩念叨欠账、终不再提",
  "phases": [
    {
      "label": "取笑催账",
      "state": "孔乙己还常来喝酒时，掌柜见了每每故意问他识字与否、是否又偷东西，引人发笑，也当众催他还欠的十九个钱（原文中段）",
      "behaviors": [
        {
          "situation": "伙计附和着笑客人",
          "behavior": "决不责备，自己也每每这样问客人，引人发笑",
          "source_quote": "我可以附和着笑，掌柜是决不责备的。"
        }
      ]
    },
    {
      "label": "念叨欠账",
      "state": "孔乙己长久不来之后，中秋前结账取粉板说他还欠十九个钱，年关、第二年端午又反复提起，到中秋却不再说（原文末段）",
      "behaviors": [
        {
          "situation": "常客欠账不来",
          "behavior": "取下粉板念叨欠账数目，年节反复提起，直到不再说",
          "source_quote": "孔乙己还欠十九个钱呢！"
        },
        {
          "situation": "结账对账",
          "behavior": "慢慢结账，取下粉板查看赊欠，再慢慢算他的账",
          "source_quote": "掌柜正在慢慢的结账，取下粉板"
        }
      ]
    }
  ]
}
```

`situation_behaviors`（原样）：

```json
[
  {
    "situation": "欠账的熟客又上门",
    "behavior": "伸出头去先催旧账，再照常笑着招呼，问酒要好不要好",
    "source_quote": "孔乙己么？你还欠十九个钱呢！"
  },
  {
    "situation": "熟客被人取笑偷东西",
    "behavior": "跟着众人一起笑，还当面点破他又偷了东西",
    "source_quote": "孔乙己，你又偷了东西了！"
  },
  {
    "situation": "伙计做事不合意",
    "behavior": "嫌样子傻、怕侍候不了体面主顾，先派到外面，又改派专管温酒",
    "source_quote": "样子太傻，怕侍候不了长衫主顾，就在外面做点事罢。"
  },
  {
    "situation": "客人不同身份",
    "behavior": "按衣着分等：长衫的请进隔壁慢慢坐喝，短衣的只让靠柜外站着喝",
    "source_quote": "只有穿长衫的，才踱进店面隔壁的房子里，要酒要菜，慢慢地坐喝。"
  }
]
```

#### 《阿Q正传》赵太爷 —— 过

命令：`python tests/perf/arc_draft_sample_check.py docs/specs/arc-behaviors-draft-samples/赵太爷.json --expect-phases 2+ --log <蒸馏期间后端日志>`

```
赵太爷：
  G1 过  warning 0 次
  G2 过  做法 8 条（按 situation 去重）
  G5 过  重复 situation：无
  G6 过  阶段 3 个（要求 ≥2），挂了专属做法的阶段 3 个
```

`character_arc`（原样）：

```json
{
  "axis": "从仗势欺人到见风转舵",
  "phases": [
    {
      "label": "仗势欺人",
      "state": "儿子进秀才、阿Q自称同宗时，他满脸溅朱喝骂动手，视阿Q如草芥，不容冒犯门第。",
      "behaviors": [
        {
          "situation": "有人自称与他同宗、攀附门第",
          "behavior": "当场翻脸喝骂，动手打对方嘴巴，逼问对方配不配姓赵",
          "source_quote": "「你怎么会姓赵！——你那里配姓赵！」"
        },
        {
          "situation": "家中女仆被人调戏",
          "behavior": "不亲自出面，叫地保去训斥对方，罚酒钱、定条件，自己不出头",
          "source_quote": "「阿Q，你的妈妈的！你连赵家的用人都调戏起来，简直是造反。」"
        }
      ]
    },
    {
      "label": "惊慌留后路",
      "state": "革命党进城消息传来，他藏举人老爷的箱子、盘算于己无坏处，又怯怯叫阿Q「老Q」。",
      "behaviors": [
        {
          "situation": "时局动荡、有权势者来寄存财物",
          "behavior": "先盘算于己有无坏处，觉得没坏处便收下箱子藏起来，留作后路",
          "source_quote": "赵太爷肚里一轮，觉得于他总不会有坏处，便将箱子留下了"
        },
        {
          "situation": "革命党得势、对方喊造反",
          "behavior": "放下架子怯怯地改口叫对方「老Q」，低声试探口风",
          "source_quote": "「老Q，……现在……」赵太爷却又没有话"
        },
        {
          "situation": "本家亲眷盘起辫子闹革命",
          "behavior": "与两位真本家站在大门口论革命，随大势而动",
          "source_quote": "赵府上的两位男人和两个真本家，也正站在大门口论革命"
        }
      ]
    },
    {
      "label": "攀附保势",
      "state": "假洋鬼子带回银桃子后，他骤然大阔、目空一切，托人给儿子绍介进自由党，见阿Q更不放在眼里。",
      "behaviors": [
        {
          "situation": "想从对方手里买便宜旧货",
          "behavior": "托邻居去请人，点灯等候，当面打量对方全身，开口问有没有旧东西可看",
          "source_quote": ""
        },
        {
          "situation": "对方不肯拿货来、态度懒散",
          "behavior": "很失望气愤，担心，但主张不必驱逐，怕结怨，只叮嘱邻居别外传",
          "source_quote": "说这也怕要结怨，况且做这路生意的大概是「老鹰不吃窝下食」"
        },
        {
          "situation": "儿子想进革命党、需人绍介",
          "behavior": "托假洋鬼子带信上城，给儿子绍介绍介进自由党",
          "source_quote": "他写了一封「黄伞格」的信，托假洋鬼子带上城，而且托他给自己绍介绍介，去进自由党"
        }
      ]
    }
  ]
}
```

`situation_behaviors`（原样）：

```json
[]
```

注：蒸《孔乙己》掌柜时，本地后端在**卡片已落库之后**发生 Windows access violation（chroma/mem0 原生线程，`PYTHONFAULTHANDLER=1` 无 Python 栈），与本次纯 Python 改动无关；重启后端后按 `card_id` 从库中取回卡片（`id=93bffbdd0838`）。

## 9. 补充

本段改动面内新发现的问题直接修，写进这里；会撞车或需要拍板的停下报告，不自行记账。

### 9.1 《祝福》两个角色蒸不出卡 —— 改动面之外，待定

§7 小样里「无弧线」「阶段较多」两个角色取自《祝福》，都蒸不出卡，卡在**本次改动面之外**的一步（`core/quotes.py` 不在本分支 diff 内，`git diff 5b2e1c62...HEAD -- core/quotes.py` 为空），按规矩不自行修，待 Shiyu 决定。

- **现象**：祥林嫂、魯四老爺经 `/api/distill/start` 都在 `finalize_card → attach_dialogue_examples → pick_dialogue_examples` 抛 `DistillError: 挑选对话示例失败：模型没有选出可用的编号`（祥林嫂重试一次仍失败）。鲁四老爷若传**简体**名「鲁四老爷」则更早失败——`aliases_for` 按名**精确**匹配名单（`core/character_roster.py:118`），名单与原文都是**繁体** `魯四老爺`，故 aliases 为空、`extract_candidates` 零命中，报「原文里找不到…的对话句」。
- **根因**：候选门在 `core/quotes.py:166-169` —— `if not any(name in lead for name in wanted): continue`；其中 `lead` 由 `_lead_before`（`core/quotes.py:181-191`）取**引号之前、最后一个句读之后**的那一截。《祝福》的对话写法是 `「台词」说者说。`（说话人在引号**后**），名字不在引号前，故抽不到该说话人的对话句；少数抽到的只是旁白里提起这个名字的句子（模型判为「不是本人在说话」，全部拒收 → 无可用编号）。
- **候选数**（本地 `extract_candidates` 复算，未花 LLM）：孔乙己 **9**、祥林嫂 **1**、魯四老爺 **0**（该角色在篇中多称「四叔」，「四叔」2）；对照《阿Q正传》阿Q **38**、趙太爺 **5**，《故乡》楊二嫂 **1**、閏土 **0**。孔乙己之所以能过，是因为它的对话写作 `孔乙己…爭辯道：「…」`（名字在引号**前**）。

### 9.2 §7 孔乙己样本 G3 不通过 —— 模型换了说法（§7 已预告该形态）

孔乙己阶段一原句写作「…用「窃书不能算偷」**辩解**…」，未出现 `--forbid-late` 的关键词「争辩」。判定脚本 G3 先用关键词在更早阶段找条目（`any(w in _text(r) for w in forbid_late)`），找不到即 `bool(early)` 为假而判不过。§7 说明第 2 条已预告此形态（「模型若换了说法（如「辩解」）会找不到而判不过」），并要求四张卡原样贴入 §8 交审计方对照原文复核。原文（公有领域）确有「争辩道」，语义一致，只是措辞不同。不自行改提示词。

### 9.3 §7 掌柜样本 G4 不通过 —— 模型给他分了阶段（同赵太爷的样本选择问题）

`--expect-phases 0` 期望掌柜无弧线、做法全在顶层。实测模型输出 `character_arc.axis = 「从逢孔乙己必取笑催账，到只剩念叨欠账、终不再提」`，分出 2 个阶段（阶段一「取笑催账」1 条、阶段二「念叨欠账」2 条），顶层另有 4 条，故 G4 不过。形态与赵太爷当初被当作「无弧线」样本、模型却分出阶段相同（§7 说明第 1 条）。§7 对掌柜只预告了 G2（条数 <3）风险，未预告会出现阶段。是否把掌柜同样改判为「有弧线」，或另换一个真无弧线样本，待 Shiyu 决定；不自行改提示词。

### 9.4 分支 CI 红 —— card_draft 变异驱动缺产物

CI 红：`card_draft_mutations.py` 缺 `card_draft_red_lines.json`，作者修复中。红源为 `tests/test_lock_coverage.py::test_every_mutation_driver_has_an_artifact_and_vice_versa`（「有驱动却没产物：`['card_draft']`」）——新驱动 `tests/perf/card_draft_mutations.py` 没有像 `alerting_mutations.py` 那样末尾调用 `lock_coverage.write_artifact(...)` 写产物，故元锁恒红、卡住合并门（本分支 `f81c0010` / `bbbae01d` / `98f419f5` 三次 gate 均红）。属作者疏漏，由作者修复并推送到同一分支。

**已关闭**：`card_draft_mutations.py` 重写并接入共享执行框架 `tests/perf/mutation_framework.py`（执行原语/主循环收口一份，产物经 `run_matrix` 写出）；38 条变异全 RED、44/44 覆盖、0 空转，`tests/perf/card_draft_red_lines.json` 入库，元锁 `test_lock_coverage.py` 29 passed。收口过程见 `docs/specs/card-draft-mutation-framework.md`（§11 进度）。

### 9.5 变异产物在本机（Windows）重生为 CRLF —— 按内容接受，根因属改动面之外

复核（`cab4f829`）跑 alerting / pg_gate 两个驱动：均 exit 0、0 mismatch，产物与入库**内容完全相同**（`git diff --ignore-cr-at-eol` 零行差异），但 `cmp` 逐字节不同（char 2 line 1）。

- **根因**：`tests/lock_coverage.py` 的 `write_artifact`（约 :440）`pathlib.Path(path).write_text(..., encoding="utf-8")` **没传 `newline=""`**，Windows 文本模式把 `\n` 翻成 `\r\n`；入库 blob 是 LF（`.gitattributes` `* text=auto eol=lf`，`git add` 时归一化），故工作区重生的文件是 CRLF。
- **判定**：**按内容接受**。`eol=lf` 保证入库一律 LF，Windows 上的 CRLF 只是工作区差异（`git checkout` 即还原为 LF）；逐字节相同只在 Linux（审计沙箱、CI）成立 —— 与 §6 第 1 条「入库产物在 Windows 上生成、环境不同本来就会不同」同源。
- **归属**：根因在 `tests/lock_coverage.py` —— 该文件是 `card-draft-mutation-framework` spec §1 列的**禁区**（本段不改），故这是**改动面之外**的已知缺陷，另行处理。`tests/test_line_endings.py`（7 条）不覆盖 `tests/perf/*.json`，CRLF 产物不会让它变红。

### 9.6 审计方结论：§7 四张小样卡（基于 0a6de0e6，对照原文与参考卡）

**结论：通过，可开 PR。** 本段要修的缺陷——早期阶段专属的做法漏进最后阶段或顶层——四张卡里都没有出现。

| 卡 | 本段缺陷（早期做法漏到后面） | 说明 |
|---|---|---|
| 孔乙己 | 无 | 用 `--forbid-late 争辩 辩解` 补判 G3：过（「辩解」那条只在阶段 1）。9.2 是关键词没对上，不是缺陷复现 |
| 阿Q | 无 | 标了 `[1,2]` 的两条（精神胜利、欺负弱者）没进最后阶段 |
| 赵太爷 | 无 | 阶段 1 的做法没有漏到后面 |
| 掌柜 | 不适用 | 见下方观察 2 |

**观察（不阻塞本段，转入后续工作）**

1. **做法被归到时间上更晚的阶段**（「晚的漏到早的」或「早的放进晚的」，方向与本段缺陷不同）。按原文位置（全文占比）核对：
   - 孔乙己顶层「被追讨欠账 → 下回还清」：原文在 0.87，打折腿在 0.73，只属于阶段 2，却被标成全部阶段而进了顶层；
   - 阿Q 阶段 2「得意中兴」下的「生计断了 → 偷萝卜、决定进城」：原文在 0.49，第六章（中兴）始于 0.51，应属阶段 1；
   - 赵太爷阶段 3「攀附保势」下的两条买旧货（原文 0.61，第六章）：早于革命（0.63）和银桃子（0.79），应属阶段 1–2 之间。

   模型在阶段边界和章节不对齐时，按时间归位不准。计划第三步「证据注入」给证据带片段序号，正是为这一点；按 Shiyu 的规矩，不另做小样，届时以文献为据设计。
2. **掌柜被分出两个阶段**（取笑催账 → 念叨欠账）：变的是处境（孔乙己不来了），不是他的心态，按维度 L「无明显变化则 phases 为空」应判为无弧线。危害小：默认最后阶段时，他拿到的仍是「念叨欠账」加顶层四条，符合原文。记为模型倾向（对次要人物按情节分段），不阻塞本段。

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
