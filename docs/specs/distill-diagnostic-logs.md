# ⑤b 诊断日志补全：出问题一下定位到哪一步

> v2（10-19 13:55，对照 rework-lessons 自查后修订，替换 13:45 的 v1）：distiller / phase_locate / log_skip 行号改对；章号换算 helper 改为吃原文坐标；G1 只看任务范围内的行；G5、G6 的新文本先放进目标检查文件，提交 3 再改旧测试；蒸馏一步的写法给出代码。

基线：main `9d8a4562`（含 #185、#186、#187）。分支 `feat/distill-diagnostic-logs`，新 worktree。
目的：蒸馏 / 草稿重转出问题时，按任务 ID 过滤一次日志，就能看到每一步做了什么、花了多久、模型为什么这么判。
范围：**只补日志，不改任何业务行为**（卡片内容、状态、返回值、写库都不变）。

依据（总文档二表第 8 行）：逐步记录 + 关联 ID + 每步耗时与错误（AgentDebugX arXiv 2607.18754；OpenTelemetry GenAI 约定）；真正出错常在上游、症状在下游，要记下每步用了哪个上游结果（arXiv 2607.07989；2610.11600：去掉上游依赖信息后步骤级定位 32.76% → 1.72%）。

## 零、约束

- 不改 `web/frontend/`、`tests/conftest.py`。
- 生产 handler 级别仍是 WARNING（`core/stdout_logging.py:33`），不改。本 PR 新增的诊断行都打 WARNING，与仓库现有监测行同口径（如 `core/card_draft.py:511`）。
- `/run_stream` 前端不用（前端只调 `/api/distill/start`：`web/frontend/src/store/useAppStore.js:873`），本 PR 对它只绑任务 ID，不加分步计时与合计。

## 一、目标检查（第 1 个提交只放这些；全部标 `xfail(strict=True)`，之后每个提交都跑）

新建 `tests/test_distill_diagnostic_logs_goal.py`。后台任务替身复用 `tests/test_distill_draft_persist.py` 的 `_DraftStub`、`_install`、`_run_start`、`_seed_text`；redraft 照 `tests/test_card_redraft_goal.py` 的写法。日志用真 handler 取：照 `tests/test_stdout_logging.py:58-81` 装上 `install_stdout_logging()`，用 `capsys` 读 stdout。

- **G1 每行带任务 ID，开始和结束各一行**：跑一次 `/start`，拿到 `task_id`。stdout 里从「任务开始」那行到「任务结束」那行之间（含这两行）的每一行都含 `[<task_id>]`（请求线程里、任务开始之前的行如 `_distill_start_impl` 的续跑判断不在范围内，显示 `[-]` 是对的）；有且只有一行含 `任务开始` 且含 `来源=蒸馏`，有且只有一行含 `任务结束`。再跑一次 redraft：开始行含 `来源=草稿重转`。
- **G2 分步计时**：同一次 `/start`，按顺序出现 `步骤 名单 完成`、`步骤 回忆区 完成`、`步骤 蒸馏 完成`、`步骤 重定位 完成`、`步骤 转卡 完成`、`步骤 后置 完成`、`步骤 打标签 完成`、`步骤 落卡 完成`、`步骤 待补 完成`、`步骤 苏醒台词 完成`，每行带耗时（正则 `耗时 \d+\.\d+s`）。redraft 没有 `步骤 蒸馏`。另造一个让 `card_from_draft` 抛异常的草稿：出现 `步骤 转卡 失败`，且含异常类名。
- **G3 token 合计**：在 `task_log.task_scope("t1", "测试")` 里调两次 `core.utils.try_record_usage`（假 storage + `usage={"prompt_tokens":10,"completion_tokens":2,...}`；身份照 `tests/test_distiller_routing.py:257` 的写法 `LLM_CALLER.set(Caller(ip=None, user_id=...))`；`submit_to_main_loop` 照 `tests/test_distill_draft_persist.py` 的 `_install` 打桩）；结束行含 `调用 2 次`、输入 20、输出 4，并按 `action` 分列。scope 外调用不累加、不报错。
- **G4 每阶段起点每次都打**：照 `tests/test_anchor_skip_diagnostics_goal.py` 的 `_draft` / `_messages` 造一张**核对通过**的卡：每个阶段一行，以 `阶段起点（角色）阶段 k「label」` 开头，含 `第N章`、规范化位置、回忆区判定、`前文：`。行里不含「整卡跳过」（该文件 :93 的断言保持成立）。整卡跳过时的原有明细逐字不变（该文件 G1–G3 不改、保持绿）。
- **G5 改挂行带章号**：照 `tests/test_phase_anchoring.py:192-203` 的用例在本文件里写一条：改挂行全文 = 原文本末尾加 `（摘录在第N章）`（N 以用例原文实际章号为准；原文没有章节标题时为 `无章节标题`，换不回原文坐标时为 `章号未知`）。`tests/test_phase_anchoring.py:202-203` 那条旧断言在提交 3 里改成同样的新文本，不放宽；该文件其余断言不改。
- **G6 重定位记下每个候选章的判断**：照 `tests/test_phase_locate_goal.py:146-147` 的用例在本文件里写一条，替身判断回复里带 `why`。每个候选章一行：`[phase_locate] card=… 阶段 k 候选 第N章：是 / 否；理由：…；句子：…`（否时句子为空）；原有两行文本末尾加 `；理由：<所选章的 why>`。另一条：核对通过、不重新定位时打一行 `[phase_locate] card=… 锚点核对通过，用模型锚点`。`tests/test_phase_locate_goal.py:146-147` 的旧断言在提交 3 里改成新文本，不放宽。
- **G7 链路上不再有 print**：AST 扫描第四节表里列出的函数，函数体里没有 `print(` 调用。
- **G8 「标 0 = 故事开始前」线上可见**：触发 `core/card_draft.py:271` 那一行，`caplog` 在 WARNING 级能抓到。

## 二、S0 开工前核对（任何一条对不上就停下报告）

先用 `Test-Path docs/specs/distill-diagnostic-logs.md` 确认文件在。

| 位置 | 应看到 |
|---|---|
| `core/stdout_logging.py:33`、`:40-43` | handler 级别 WARNING；Formatter `"%(asctime)s %(levelname)s %(name)s: %(message)s"` |
| `core/concurrency.py:68-100` | `_derive_body` 用 `contextvars.copy_context()`；`ctx_thread`、`ctx_submit` 都经它 |
| `core/distiller.py:1129`、`core/relationship_batch.py:85` | 线程池提交都用 `C.ctx_submit`（任务 ID 能传进工作线程） |
| `core/utils.py:72-130` | `try_record_usage` 是记账唯一出口；:100、:105 两处 `print`；:107-113 取 pt / ct / cache_hit / cache_miss |
| `web/routers/distill.py:489-490` | `@T.spanned("distill.task", wf="distill")` 下面是 `def _run_distill_task(` |
| `web/routers/distill.py:546-654` | 后台任务各步：名单 :546-550、回忆区 :553-554、蒸馏 :558-561、重定位 :564、转卡 :566-573、后置 :577、打标签 :581-587、落卡 :612-620、待补 :625-630、苏醒台词 :633-643 |
| `web/routers/distill.py:1107-1114` | `async def distill_stream(`，函数体第一句 `user_id = user["id"]` |
| `core/phase_anchoring.py:134-146` | `class PhaseAnchors(NamedTuple)`，字段 n / source_norm / ranges / reason / retro |
| `core/phase_anchoring.py:148-162` | `build_anchors` 手里有 `source_text` |
| `core/phase_anchoring.py:170-206` | `log_skip`：:177 `if not anchors.reason: return`；:184 一次 `raw_offsets`；:198-203 用 `chapter_starts` + bisect 从原文坐标算章号，原文坐标还用来取前文 |
| `core/phase_anchoring.py:297-309` | 去掉 / 改挂 / 未定位三行 warning；改挂行在 :300 |
| `core/card_draft.py:442` | `phase_anchoring.log_skip(phases, source_text, anchors, draft.name)`，全仓唯一调用方 |
| `core/card_draft.py:271` | `logger.info("[card_draft] %s：%s", _ZERO_MEANINGS[layer], _row_label(item))` |
| `core/phase_locate.py:123-127`、`:145-151` | `Verdict(happens, sent, why)`（frozen）；`PhaseResult(phase, chapter, anchor, yes, why)` |
| `core/phase_locate.py:186-219` | :197 `verdicts` 字典按 (阶段, 章) 存每个候选的判断；:203-218 逐阶段选起点，只把所选章的 `why` 放进 `PhaseResult`；`TOP_K = 25`（:36） |
| `core/distiller.py:1075-1114` | `locate_phase_starts`：:1088 阶段不足 2 个直接 return；:1090-1091 核对通过直接 `return`（不打日志）；:1095-1100 两种日志行不含 why；:1110-1111 换上新锚点后不打日志 |
| `core/quotes.py:59-65` | `normalized_starts` 对每个点都规范化一次前缀（O(N×K)），**不要**拿它换算几百个章节起点 |
| `core/quotes.py:130-` | `raw_offsets` 一次调用一趟 `paragraph_bounds`，适合少量位置批量换算 |

全量扫描原始输出（基线 9d8a4562）：
```
$ grep -n "print(" web/routers/distill.py
312:    print(f"[distill] Persisted awakening_message to card {card_id}")
396:                print(f"[distill] Stream error for {name}: {piece['error']}")
518:        print(f"[distill] 并发蒸馏达上限，任务超时: {char_name}")
621:        print(f"[distill] Card saved: card_id=... name={name} text_id={text_id} user_id={user_id}")
892:            print(f"[distill] Resume {task_id} rejected: 切分参数变更 "
895:            print(f"[distill] Resume {task_id} rejected: 原文变更（text_fingerprint 不符），整批重跑")
902:            print(f"[distill] Resume {task_id}: {len(resume_candidates)} 片候选，任务级门通过")
1500 / 1679 / 1713（list_cards、start_session，不在蒸馏链路上，不改）

$ grep -n "print(" core/distiller.py   （蒸馏链路上的）
1013  _collect_stream            调用 LLM 失败
1056  fill_relationships         关系分批失败
1186  _chat_accounted            调用 LLM 失败
1410  _parse_json_with_retry     JSON 重试用尽（多行）
1458  identify_characters        identify cache hit
1488 / 1500 / 1508 / 1526 / 1538  identify_characters  识别解析与重试失败
1676 / 1677  _judge_groups       识别合并结果
2698 / 2796  distill_incremental_stream  map / reduce 中止
（482、766–774、1781–1906、2532–2557 是 schema 生成 / 配置读取 / 非流式旧路径，不在 /start 链路上，不改）

$ grep -n "print(" core/text_manager.py core/utils.py
core/text_manager.py:614   save_distilled_card  card-guard flagged（:513 是旧的 save_card 路径，不改）
core/utils.py:100 / :105   try_record_usage     usage not recorded
```

依赖日志文本的现有测试（本 PR 会动到的只有这三处，断言改成新文本，不放宽）：
```
tests/test_phase_anchoring.py:202-203        改挂行全文
tests/test_phase_locate_goal.py:146-147      重定位两行全文
tests/test_anchor_skip_diagnostics_goal.py   整卡跳过明细全文（本 PR 不改其文本，必须保持绿）；:93 不跳过时不得有含「整卡跳过」的行
```

## 三、行为变化

业务行为无变化。日志格式变化一条：生产 stdout 每行在级别后多一段 `[任务ID]`，不在任务里的行显示 `[-]`。

## 四、做法（按提交）

### 提交 2：任务 ID、分步计时、token 合计（新模块 `core/task_log.py`）

`core/task_log.py` 只管这三件事，不认识 web 层：

```python
TASK_ID: ContextVar[str]          # 默认 "-"
class TaskIdFilter(logging.Filter):  # record.task = TASK_ID.get()；返回 True
@contextmanager
def task_scope(task_id: str, source: str): ...
    # 设 TASK_ID 与本任务累加器；进入时 warning「任务开始 来源=<source>」；
    # 退出（含异常）时 warning「任务结束 耗时 X.XXs；调用 N 次；输入 P（缓存命中 H / 未命中 M）输出 C；按用途 a:n/p/c, ...」；还原 contextvar
def scoped(source_of: Callable[..., tuple[str, str]]): ...
    # 装饰器：用 source_of(*args, **kwargs) 取 (task_id, source)，在 task_scope 里调原函数；用 functools.wraps 保留原函数签名（外层还有 @T.spanned）
@contextmanager
def step(name: str): ...
    # 正常退出 warning「步骤 <name> 完成 耗时 X.XXs」；异常 warning「步骤 <name> 失败 耗时 X.XXs：<异常类名>」后原样再抛
def add_usage(action, prompt_tokens, completion_tokens, cache_hit, cache_miss) -> None: ...
    # 有累加器就加（加锁，工作线程会并发调），没有就什么都不做
```

接入点（每处一行或一层 `with`）：
1. `core/stdout_logging.py`：`_handler.addFilter(TaskIdFilter())`；Formatter 改为 `"%(asctime)s %(levelname)s [%(task)s] %(name)s: %(message)s"`。
2. `web/routers/distill.py:489-490`：在 `@T.spanned(...)` 和 `def` 之间加
   `@task_log.scoped(lambda task_id, *_a, draft=None, **_k: (task_id, "草稿重转" if draft is not None else "蒸馏"))`。
   函数体不缩进。:518 信号量超时那行也在 scope 里。
3. `web/routers/distill.py` `distill_stream` 函数体第一句前：`task_log.TASK_ID.set(f"s-{_uuid.uuid4().hex[:12]}")`。只绑 ID。
4. `core/utils.py` `try_record_usage`：在 :113 取完 cache_miss 之后、`_write` 之前调 `task_log.add_usage(action, pt, ct, cache_hit, cache_miss)`。
5. 分步计时：在 `_run_distill_task` 第二节表列出的 10 步各包一层 `with task_log.step("<步骤名>"):`，步骤名照 G2。**有 try/except 的步骤，`with` 放在 try 里面、只包住那次调用**：转卡写成 `try:` → `with step("转卡"): card = card_from_draft(...)` → `except`；落卡同理只包 `submit_to_main_loop(_save_card(), timeout=120)`。这样失败会先记「失败」再进原来的 except。蒸馏一步只在 `draft is None` 时包（草稿重转没有这一步），:558-559 写成：
   ```python
   if draft is None:
       with task_log.step("蒸馏"):
           data = _distill_to_draft(task_id, name, content, text_type, aliases, resume_candidates, distiller)
   else:
       data = draft
   ```

### 提交 3：阶段起点、改挂章号、重定位理由、标 0 升级

1. **章号换算只写一处**：`core/phase_anchoring.py` 新增
   `def chapter_labels(source_text: str, raw_positions: Sequence[int | None]) -> list[str]`：输入已经换算好的原文坐标（`raw_offsets` 的结果），对 `chapter_starts` 二分，返回「第N章」/「首章之前」/「无章节标题」/「章号未知」（坐标为 None），口径照 :198-202 现有写法。`log_skip` 的 :198-202 改调它；:184 的 `raw_offsets` 与 :203 取前文不变。调用方各自只调一次 `raw_offsets`。
2. **`PhaseAnchors` 加字段 `source_text: str = ""`**（放最后、带默认值），`build_anchors` :162 传入。`verify` 用它算章号。
3. **每阶段起点每次都打**：`log_skip` 改名 `log_phase_starts`，去掉 :177 的提前 return。跳过时原样打现在的两种行（文本逐字不变）；不跳过时每阶段打一行，开头 `阶段起点（{name}）阶段 {k}「{label}」锚点「{anchor}」→ `，后半与跳过明细同格式（章、规范化位置、回忆区、前文）。`card_draft.py:442` 改调新名。
4. **改挂行带章号**：`verify` 循环里不再当场打改挂行，先把（kind、label、p、targets、该标注落点里的第一个规范化位置）存进列表；循环结束后对这些位置调一次 `raw_offsets(anchors.source_text, anchors.source_norm, 位置)`、一次 `chapter_labels`，再逐条打 `改挂：…（摘录在第N章）`。这样每次 `verify` 只多一趟 `raw_offsets`，没有改挂时一趟都不多。去掉 / 未定位两行不变。
5. **重定位理由**：
   - `core/phase_locate.py` `PhaseResult` 加字段 `judged: list[tuple[int, bool, str, str]] = field(default_factory=list)`（章、是否、why、所选句子；否时句子为空串），`locate` 里按 `verdicts` 和 `sents` 填好。
   - `core/distiller.py` `locate_phase_starts`：:1091 那个 return 前打 `[phase_locate] card=%s 锚点核对通过，用模型锚点`；:1093-1100 每个阶段先打每个候选一行（G6 格式），再打原有那行，末尾加 `；理由：%s`（`row.why`）；:1110-1111 换上新锚点之后打 `[phase_locate] card=%s 已换成重定位锚点`。
6. `core/card_draft.py:271` 的 `logger.info` 改为 `logger.warning`。

### 提交 4：链路上的 print 改成 logger

第二节扫描列出的每一处 `print(...)` 改成同文案的 `logger.warning(...)`（失败类）或 `logger.error(...)`（:1410 JSON 重试用尽、:2698 / :2796 map / reduce 中止）。文案保留，f-string 改成 `%s` 参数。所在模块没有 `logger` 的照文件现有写法 `logger = logging.getLogger(__name__)`。

G7 扫描的函数：`web/routers/distill.py` 的 `_persist_awakening`、`_distill_to_draft`、`_run_distill_task`、`_distill_start_impl`；`core/distiller.py` 的 `_collect_stream`、`fill_relationships`、`_chat_accounted`、`_parse_json_with_retry`、`identify_characters`、`_judge_groups`、`distill_incremental_stream`；`core/text_manager.py` 的 `save_distilled_card`；`core/utils.py` 的 `try_record_usage`。

## 五、已有机制与规模

| 项 | 现状 | 本 PR 后 |
|---|---|---|
| contextvar 进工作线程 | 后台线程、线程池、主 loop 投递都拷上下文（第二节表） | 任务 ID 与累加器随之可见，不另做传递 |
| 生产日志级别 | WARNING | 不变 |
| 每张卡新增行数 | — | 开始 / 结束 2 行 + 分步 ≤10 行 + 阶段起点 n 行（n = 阶段数）；重定位只在锚点核对不过时发生，候选行 ≤ 25 ×（n−1） |
| 额外计算 | — | 每次 `verify` 有改挂时多一趟 `raw_offsets`（一张卡 4 次以内）；不调用 `normalized_starts` 换算章节 |
| 记账 | `usage_stats` 只有 user_id | 表不变；合计只在日志里 |

## 六、结构自检

| 问题 | 改后 |
|---|---|
| 任务 ID 从哪里加到日志上 | 1 处（`TaskIdFilter`），所有现有 logger 行自动带上 |
| 任务开始 / 结束 | 1 处（`task_scope`，经 `scoped` 装饰器挂在 `_run_distill_task` 上），首次蒸馏与草稿重转共用 |
| token 合计 | 1 处（记账唯一出口 `try_record_usage` 调 `add_usage`） |
| 章号换算 | 1 处（`chapter_labels`），跳过明细、起点行、改挂行共用 |
| 以后加一个步骤 | 在 `_run_distill_task` 里多包一层 `with task_log.step("…")` |

## 七、验证

本地（Docker PG），只跑受影响的文件：
```
pytest -q tests/test_distill_diagnostic_logs_goal.py tests/test_stdout_logging.py tests/test_anchor_skip_diagnostics_goal.py tests/test_phase_anchoring.py tests/test_phase_locate_goal.py tests/test_card_redraft_goal.py tests/test_distill_draft_persist.py tests/test_distill_task_api.py tests/test_lifespan_undo.py tests/test_arc_phase_fields_unit.py tests/test_arc_phase_fields_locks.py tests/test_card_draft.py
```
全量回归交给分支 CI，gate / 前端 / sentinel 全绿才算过。

报告：每个提交的 `git show --stat`；提交 1 的红色输出、最后的绿色输出；上面命令的汇总行；**再贴一段 G1 那次 `/start` 的真实 stdout 原文**（让人一眼看到新日志长什么样）；CI 结果。末尾附「已核实 / 未核实」。范围内的问题直接修，碰到冲突或需要决定的事才停下。

## 八、本 spec 的已核实 / 未核实（作者在 9d8a4562 上读过的行）

已核实：第二节表全部行号与内容；`_derive_body` 拷 contextvar，蒸馏链路上的线程与线程池都经它；`try_record_usage` 是 Distiller、审核（`card_guard`）、苏醒台词共用的记账出口；`normalized_starts` 是 O(N×K)、`raw_offsets` 一趟 `paragraph_bounds`；`log_skip` 全仓唯一调用方在 card_draft.py:442；前端只调 `/start`；依赖日志文本的测试只有第二节列出的三处；`tests/test_stdout_logging.py` 只断言「含」时间、级别、logger 名，不断言整行格式。

未核实：生产 stdout 是否有外部系统按旧格式解析（仓库里没有）；新增日志行对单张卡耗时的实际影响（按第五节估算可忽略）。

## 补充 1（10-19 14:00）：单值字段撞车写明保留谁、丢了谁

来源：二表第 5 行调查（单值字段同阶段多条只留第一条）。现在被丢的条目只打一行「X 的阶段 N 有多条取值，保留第一条」，不写丢的是哪条、留的是哪条、是模型写了两条还是改挂撞车，所以用草稿重转也分不清是哪条路。本项只补日志，不改保留规则（仍按模型输出顺序留第一条）。

S0 追加核对：

| 位置 | 应看到 |
|---|---|
| `core/card_layers.py:69-71` | `decision_style`、`speaking_style.tone`、`speaking_style.sentence_pattern` 是 `("state", "scalar", …)` |
| `core/card_draft.py:295-298` | `def dispatch(final_phases, count, *, layer, kind, label, unlocated, zero_only)` |
| `core/card_draft.py:331-341` | 格子里的条目按 `final_phases` 的下标顺序（即模型输出顺序）放入；`kind == "scalar"` 时顶层和各阶段格子都过 `_cap_slot` |
| `core/card_draft.py:344-348` | `_cap_slot` 只留 `slot[:1]`，丢掉的每条打一行 `"[card_draft] %s 的%s有多条取值，保留第一条"` |
| `core/card_draft.py:464-473` | 字段循环：`verify` 不传 `placement`（默认改挂，不是态度用的 `KEEP_TAGGED`）；`dispatch(..., kind=spec.kind, label=path, ...)` |
| `core/phase_anchoring.py:47-57` | `Verification.tag_targets[i][p]` = 模型标的阶段 p 按摘录最终落到的阶段 |
| `tests/` | 没有测试断言「有多条取值」这行文本（`grep -rn "有多条取值" tests` 无结果） |

目标检查（追加到 `tests/test_distill_diagnostic_logs_goal.py`，同样 xfail strict）：
- **G9 单值字段撞车写明来龙去脉**：两个阶段的卡，`decision_style` 写两条：先写 B（标阶段 1，摘录落在阶段 2 的原文里），再写 A（标阶段 2，摘录在阶段 2）。转卡后：卡上阶段 2 的 `decision_style` 仍是 B（行为不变）；日志有一行
  `[card_draft] decision_style 的阶段 2 有多条取值，保留「B」（由阶段 1 改挂进来），丢弃「A」（原标此阶段）`。
  再测一个两条都原标阶段 2 的情形：两条都写「原标此阶段」。

做法（并进提交 3）：
1. `dispatch` 加关键字参数 `describe: Callable[[int, int], str] | None = None`，原样传给 `_cap_slot`；顶层格子传阶段 0。
2. `_cap_slot(slot, label, where, phase, describe)`：`describe` 为 None 时行为与文本都同今天；有 `describe` 时每条丢弃打
   `"[card_draft] %s 的%s有多条取值，保留%s，丢弃%s"`，两处 `%s` 都是 `describe(i, phase)`。
3. 字段循环（:472）传 `describe`：取 `rows[i].value`，再用 `res.tag_targets[i]` 判来源 —— 标注里有 phase 且它落回 phase → 「原标此阶段」；否则找落点含 phase 的那个标注 q → 「由阶段 q 改挂进来」；phase 为 0（顶层）→ 「全程」。整卡跳过时 `tag_targets` 是 `{p: [p]}`，自然都判「原标此阶段」。这个函数写在字段循环旁边，`dispatch` / `_cap_slot` 不认识草稿结构。
4. 做法、记忆、关系三处 `dispatch` 调用不传 `describe`，不变。

结构自检追加：「单值字段撞车怎么记」只写一处（`_cap_slot`），来源怎么判只写一处（字段循环里的 `describe`）。

## 补充 2（10-19 14:05）：任务开始行写明是否重新蒸馏、是否续跑（来自 F，二表第 6 行）

目的：线上每次蒸馏都能看出是第一次蒸、重新蒸、续跑还是草稿重转。F 之后会给同一行再加 `entry` 字段（前端哪个按钮发起），本 PR 不管。

S0 追加核对：

| 位置 | 应看到 |
|---|---|
| `web/routers/distill.py:250-253` | `DistillTaskRequest.force: bool = False` |
| `web/routers/distill.py:878-902` | `resume_candidates` 默认 None；:880 `if not req.force:` 才找可续跑任务；只有任务级门通过时 :898 才赋值（可能是空字典） |
| `web/routers/distill.py`（`_distill_start_impl` 起线程处） | `args=(task_id, req.text_id, req.character_name, req.force, user_id, content, text_type, llm, emb.key, emb.region, resume_candidates)`，`kwargs={"draft": draft}` |

改法（并进提交 2，替换第四节里 `scoped` 和 `task_scope` 的签名）：
1. `task_scope(task_id: str, **fields)`：开始行写成 `任务开始 ` + 按传入顺序的 `键=值`，布尔写 `true` / `false`。例：`任务开始 来源=蒸馏 force=false resumed=false`。
2. `scoped(fields_of)`：装饰器先用 `inspect.signature(fn).bind(*args, **kwargs)` 加 `apply_defaults()` 得到按参数名取值的字典，再调 `fields_of(bound) -> (task_id, dict)`。**不按位置下标取参数**（`_run_distill_task` 有 11 个位置参数，按下标取以后加参数就错位）。
3. `_run_distill_task` 上的装饰写成：
   ```python
   @task_log.scoped(lambda b: (b["task_id"], {
       "来源": "草稿重转" if b["draft"] is not None else "蒸馏",
       "force": b["force"],
       "resumed": b["resume_candidates"] is not None,
   }))
   ```
4. G1 追加断言：`/start`（force=False、无可续跑任务）开始行含 `force=false resumed=false`；redraft 开始行含 `来源=草稿重转 force=true resumed=false`。再加一条：先造一条 error 状态、任务级门能通过的任务行（照 `tests/test_distill_task_api.py:873` `test_error_task_resumes_and_skips_checkpointed_chunks` 的造法），再 `/start`，开始行含 `resumed=true`。

`card_draft.py:271` 升 WARNING 已在第四节提交 3 第 6 条，不重复。
