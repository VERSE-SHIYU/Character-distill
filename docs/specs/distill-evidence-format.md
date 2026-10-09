# 字段正文的依据写法统一、口癖只写原话（蒸馏遗留③）

> 基线：`origin/main` @ `b9b8cd2`。交付：本文件 + `tests/test_evidence_format_goal.py`（目标检查，V1–V9，共 21 条）。
> 目标检查在 main 上 **20 红 1 绿**。绿的是 V7，回归守卫。
> 只改 `core/distiller.py` 的提示词常量，外加 `tests/test_arc_phase_fields_locks.py` 的 S4 锁名单。与「草稿阶段号」那一段（只改 `core/card_draft.py`）互不依赖。

## 0. 结论与方案对比（返工经验第 1、3 条）

目标有三条：
- 依据（原话或场景）保留，写法统一；
- 不再出现章节号、「原文出处」这类核对不了的位置标签；
- 口癖不再因为被套上描述模板而被整条撤回。

| 方案 | 做法 | 能否达成目标 | 结论 |
|---|---|---|---|
| A 删依据 | 取值只写结论 | **不能**：93 条核对通过的原话会丢，卡片变空 | 否 |
| B 依据另存 | 正文只写结论，依据存进新字段 | 能，但没有任何代码或页面读这个新字段（§3.3），等于为一个不存在的需求改卡片结构 | 否 |
| C 渲染时删括号 | 拼提示词时用正则去掉括号 | 勉强能；括号写法一变就漏，属于打补丁 | 否 |
| **D 统一规则** | 依据写法只定义一处；模板按「原话类 / 描述类」由逐字清单派生 | **能**：内容保留、写法统一、口癖不再被误删 | **采用** |

D 的依据（第 28 条）：
- 和仓库铁律 5/6 同源（`core/distiller.py:162`，以及 `:169–171` 的注释），都来自 Anthropic 官方指南 [Reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)：用逐字引文作依据，并且事后逐条核对。仓库已经有事后核对这一步，即 `retract_unverified`。
- 「哪些字段是原话类」的唯一来源已经存在，即 `card_quotes._VERBATIM_TOP`，直接复用。

## 1. 问题（已查实；数据来自 6 张现卡的全量核对，本地报告 `③-卡内出处核对-报告.md`，2026-10-09）

| 发现 | 数据 | 根因 |
|---|---|---|
| 括号里的依据是有用的内容 | 157 组：93 条带引号的原话，核对 93/93 通过；24 条原话没加引号；40 条场景转述 | — |
| 口癖被整条撤回 | 蒸馏日志共撤回 20 条，其中 16 条是口癖；抽查 6 条，里面的原话全都在原文里 | 口癖套的是描述类模板（`core/distiller.py:278` 用 `_TIMED_TPL`），可口癖规定整条值逐字是原文（`core/card_quotes.py:53–57`）。同一个模板套在了性质不同的两类字段上 |
| 写法四种并存 | 带引号原话 37/37（孔乙己）；不带引号 24（掌柜）；只写章节号 8 条，章节号共 44 组（阿Q）；完全不写（我、双喜） | 「出处用括号附句尾」（`:267`）等要求只规定「要写」，没规定「写成什么样」 |
| 章节号核对不了 | §3.2：`core/` 里没有任何章节号核对 | 位置标签是模型自己报的 |

## 2. 设计

1. **`_EVIDENCE_RULE` 只定义一次**。描述类取值写成「结论（依据）」：依据优先用原文里的原话，写成「原话」，引号里逐字照抄；找不到原话，就写一句场景转述，不加引号。依据里只写原话或场景，不写章节号，也不写注明来源位置的标签。
   - 它进 `_FORMAT_OUTPUT_RULES`，替换 `:267` 那句。输出规则每个分组都带，所以所有提示词都有这条。
   - 示例统一用 `_EVIDENCE_EXAMPLE = '（「原话」）'`：描述类写成 `'结论' + _EVIDENCE_EXAMPLE`，记忆写成 `'关键经历' + _EVIDENCE_EXAMPLE`。
2. **原话类和描述类由逐字清单派生**。常量 `_TIMED_TPL` 改成函数 `_timed_tpl(path)`：路径在 `card_quotes.VERBATIM_FIELDS` 里，就用原话模板（取值就是原话本身，不加描述、括号和引号）；不在，就用描述模板。维度 C 的口癖改用新常量 `_PHASE_VERBATIM_RULE`。
3. **稳定项不附依据**。`_STABLE_FIELD_RULE` 改成「只写取值本身，不附依据」；文化程度那一句由「原文证据：引一句原文中角色说的话作为判断依据」改成「依据原文中角色说的话判断」。依据有两条：现卡里稳定项本来就不带依据；`chat_engine` 直接把它拼成「你的文化程度：{…}」（`core/chat_engine.py:1019`）。
4. **维度总标题**（`:176`）由「每个维度必须给出原文证据」改成「每条结论都要以原文为依据」，否则和第 3 条矛盾。

以后加东西要改几处（第 6 条）：
- **新的原话类字段**：只在 `card_quotes._VERBATIM_TOP` 登记一处。
- **新的按阶段描述类字段**：登记表加一条，模板里写一行 `_timed_tpl("<登记路径>")`；路径写错由 V9 拦住。
- **改依据写法**：只改 `_EVIDENCE_RULE` 和 `_EVIDENCE_EXAMPLE`。

不改的地方（第 28 条，逐条附理由）：
- **铁律第 6 条**（`:162`，「引号里必须是原文逐字摘录……做不到就用转述，不加引号」）：和本设计一致。
- **map 阶段的提示词**（`:770–778`，「原话完整保留」「必须有原文证据」）：它只产出中间笔记，不进卡片字段。
- **map-reduce 格式化时的用户消息**（`:2396–2400`，「口癖必须是真实口癖」「性格附场景证据」）：和本设计一致。
- **把「原话类」这个属性从 `card_quotes` 挪进字段登记表**：内聚会更好，但这是跨模块的重构，不属于本次修复（第 9 条）；现有的唯一来源已经足够，不会出错。

## 3. 已查实的约束（基线 `b9b8cd2`）

### 3.1 全量扫描：提示词里所有要求写出处或证据的地方（第 8 条）

```
$ grep -n "出处\|原文证据\|_TIMED_TPL" core/distiller.py      # 已去掉注释行
176:_FORMAT_DIMS_HEADING = '## 分析维度（每个维度必须给出原文证据）'
187:    '取值按阶段给，写成 {"value": "描述（含原文出处）", "occurrences": '
193:    '这类字段全程不变：只写一次，不按阶段拆，出处写在括号里。'
196:_TIMED_TPL = '{"value": "描述（原文出处）", "occurrences": [{"phase": 1, "quote": "该阶段原文摘录"}]}'
240:        '   - education_level（文化程度）：…原文证据：引一句原文中角色说的话作为判断依据。稳定项，…
267:    '严格按以下 JSON 格式输出，…把结论和关键依据（含出处）浓缩进一句话，出处用括号附句尾。…
273 276 277 278 283 284 295 298 299 322 323 324 326 327 328 338 339：模板里 17 处 + _TIMED_TPL
287:        '    {"memory": "关键经历（原文出处）", "occurrences": [...]}\n'
775:            f"- 性格特质和行为模式（必须有原文证据）\n"      ← map 阶段，不改（见 §2）
```

另外把所有提示词实际生成出来逐句扫了一遍（完整提示词、6 个分组、共享前缀），没有发现上面以外的出处要求。

### 3.2 代码里没有章节号核对

```
$ grep -rn "第.\{0,3\}[章回]" core/*.py      # 已去掉注释
命中的只有「第一句」「第几条」「第一处」这类无关字样，没有任何章节号的解析或核对
```

### 3.3 谁会读字段正文（不采用方案 B 的依据）

读字段正文的有：聊天提示词（`context_engine` / `chat_engine`）、开场白（`core/opening.py:31`）、市场页提示（`web/routers/market.py:843`）、内容审核（`core/moderation/card_text.py`）、引文核对（`core/card_quotes.py`）。**没有一处去解析括号里的依据**。场景索引切的是原文，不读卡片（`core/scene_indexer.py:98`）。

### 3.4 调用点矩阵（第 12 条）

| 发提示词的地方 | 用到的常量 | 目标检查覆盖 |
|---|---|---|
| 一次读完 / 流式 | `DISTILL_PROMPT_AFTER_NAME`（`:1659`、`:1692`） | V1–V9 的 `full` |
| map-reduce 格式化 | 同上（`:2387`） | 同上 |
| 按组并行 | `format_prompt_after(group)`（`:2703`） | V1、V2 对每个分组都跑 |
| 关系分批的前缀 | `format_prompt_shared()`（`:2410`、`:2767`） | 不含改动的常量；改前改后逐字一致（§3.5） |

### 3.5 规模与缓存（第 11 条，Claude 沙箱实测）

- **token 数**（`core.tokens.count_tokens`，官方 tokenizer）：完整提示词 4366 → 4551（+185）；各分组 G1 +52、G2 +70、G3 +145、G4 +72、G5 +52、G6 +54。
- **长度预算只按原文计**：`core/distiller.py:1744` 是 `fits_one_pass(count_tokens(text), …)`，`core/length_budget.py:65` 是 `count_tokens(text)`，不受影响。
- **前缀缓存**：`format_prompt_shared("X")` 改前改后用 `==` 比较为 True，不受影响。

### 3.6 现有测试

现有测试都没有钉住旧写法。Claude 用原型跑了所有引用 `distiller`、`format_prompt`、`Distiller`、`card_quotes` 的测试文件，共 43 个：1007 passed，deselect 了已知稳定红的那一条。

### 3.7 环境冲突（第 16 条）

- **测试库**：本段单元测试用 `docker compose -f docker-compose.test.yml up -d --wait` 起的测试库（55432）。并行的 ④ 改用它自己的一次性 PG（不同端口），两边不会互相干扰。
- **本地栈**：§6 重蒸要重建本地应用镜像，同一时间只能有一个任务在用本地栈。重建前先确认「对话候选调查」（①②）已经交了报告，还没交就等它。

## 4. 参考实现（Claude 沙箱原型；示意用，执行方按 §2 独立实现，不要求逐字一致）

```python
from core.card_quotes import VERBATIM_FIELDS, retract_unverified

_FORMAT_DIMS_HEADING = '## 分析维度（每条结论都要以原文为依据）'
_EVIDENCE_RULE = (
    '描述类取值写成「结论（依据）」：依据优先用原文里的原话，写成「原话」，引号里逐字照抄；'
    '找不到原话就写一句场景转述，不加引号。依据里只写原话或场景，不写章节号，也不写注明来源位置的标签。'
)
_EVIDENCE_EXAMPLE = '（「原话」）'
_DESC_VALUE = '结论' + _EVIDENCE_EXAMPLE
_VERBATIM_VALUE = '原文里的原话，逐字照抄'
_PHASE_DESCRIPTION_RULE = (
    '取值按阶段给，写成 {"value": "' + _DESC_VALUE + '", "occurrences": '
    '[{"phase": 1, "quote": "该阶段原文摘录"}]} —— 一条取值只写一次，'
    '标出它确实成立的阶段；从头到尾都成立时标全部阶段。'
)
_PHASE_VERBATIM_RULE = (
    '取值就是原话本身：写成 {"value": "' + _VERBATIM_VALUE + '", "occurrences": '
    '[{"phase": 1, "quote": "该阶段原文摘录"}]}，value 里不加描述、括号和引号；'
    '一条取值只写一次，标出它确实成立的阶段。'
)
_STABLE_FIELD_RULE = '这类字段全程不变：只写一次，不按阶段拆，只写取值本身，不附依据。'

def _timed_tpl(path: str) -> str:
    value = _VERBATIM_VALUE if f"{path}[]" in VERBATIM_FIELDS else _DESC_VALUE
    return '{"value": "' + value + '", "occurrences": [{"phase": 1, "quote": "该阶段原文摘录"}]}'
```

其余改动：
- 维度 C 改为「语气/句式按阶段给，」+ `_PHASE_DESCRIPTION_RULE` +「口癖按阶段给，」+ `_PHASE_VERBATIM_RULE`；
- `_FORMAT_OUTPUT_RULES` 里那一句换成「每条取值浓缩成一句话。」+ `_EVIDENCE_RULE`；
- 记忆模板换成 `'关键经历' + _EVIDENCE_EXAMPLE`；
- 模板里 17 处 `_TIMED_TPL` 换成 `_timed_tpl("<登记路径>")`，路径以 `core/card_layers.py` 的 `REGISTRY` 为准；
- S4 锁（`tests/test_arc_phase_fields_locks.py:119`）的名单里加上 `_EVIDENCE_RULE` 和 `_PHASE_VERBATIM_RULE`。

## 5. 测试（第 15 条）

- 目标检查 `tests/test_evidence_format_goal.py`，V1–V9 共 21 条，全部要绿。
- 只跑受影响的文件：目标检查、`tests/test_arc_phase_fields_locks.py`、`tests/test_personality_distill_goal.py`、`tests/test_card_quotes.py`、`tests/test_distiller_*.py`。库用 docker PG，并且 `--deselect tests/test_arc_phase_fields_unit.py::test_relationship_batch_splits_and_merges`。
- 合并门是分支 CI。**执行方不跑变异**（第 14 条）：对账表由审计方逐条验证，审计方还要自己补变异。

## 6. 合并前验收：重蒸对照（不达标就不合并）

提示词改了，模型实际会写成什么样，只能真跑才知道。用 `deepseek-flash`，在本分支的镜像上重蒸 6 张卡：孔乙己、阿Q、赵太爷、掌柜、我、双喜。
- **基线**：先用本次调查留下的同一套统计脚本（`e2e/scratch/accept3/cite_audit.py` 等），在**改前**的 6 张现卡上重跑一遍，得出基线数字；
- **对照**：再对重蒸后的 6 张卡跑同一套脚本，逐项比较。

| 指标 | 要求 |
|---|---|
| 口癖被核对撤回的条数（蒸馏日志 `[card_quotes] 撤回`） | 明显少于基线（调查时为 16） |
| 取值里出现章节号、「原文出处」字样的条数 | 0 |
| 带引号原话的核对通过率 | 不低于基线（调查时为 93/93） |
| 描述类取值中带依据的比例 | 不低于基线；逐卡列出 |
| `[phase_anchoring]` 的改挂、进未定位区、挂最后阶段计数 | 不变差；逐卡列出 |
| `usage_stats.model` | 只能是 `deepseek-flash` |

另外每张卡贴 3 条新旧对照：性格 1 条、口癖 1 条、三档关系做法 1 条。

## 7. 对账表（复核方清单，第 14 条：执行方不跑，审计方逐条验并自补变异）

| 行为 | 守它的测试 | 应让它变红的变异 | 改后能触发的状态 |
|---|---|---|---|
| 不再要求位置标签 | V1 | 把「出处用括号附句尾」加回输出规则 | 任一提示词 |
| 依据规则进所有提示词 | V2 | `_EVIDENCE_RULE` 只放进维度说明、不进输出规则 | 分组提示词 |
| 口癖只写原话 | V3、V6 | `_timed_tpl` 忽略逐字清单，一律用描述模板 | 完整提示词与 G3 |
| 描述类示例统一 | V4、V6 | `_DESC_VALUE` 改回「描述（原文出处）」 | 同上 |
| 稳定项不附依据 | V5 | `_STABLE_FIELD_RULE` 改回「出处写在括号里」 | G1、G3、G4 |
| 可选值不变 | V7 | 同时改掉维度说明（`:240`）和模板里文化程度的可选值（只改一处时另一处仍含原文字，V7 不会红，属于回归守卫的已知范围） | — |
| 原话类由清单决定 | V8 | `_timed_tpl` 里写死 `path == "speaking_style.catchphrases"` | 逐字清单新增字段 |
| 模板路径来自登记表 | V9 | 把一处 `_timed_tpl` 的路径写成不存在的路径 | 模板新增字段 |
| 规则只定义一次 | S4 锁 | 复制一份 `_EVIDENCE_RULE` | — |

## 8. 执行步骤

- **S0**：先进 plan mode 报计划。逐条复核 §3 的事实，再审 §7 对账表，有一条不成立就停下报告（第 18 条）。
- **S0-1**：在独立 worktree 里，以 `origin/main`（`b9b8cd2`）为基线开分支 `fix/distill-evidence-format`。先用 `git rev-parse HEAD` 确认基线；不对就执行 `git checkout -B fix/distill-evidence-format origin/main`。放入本文件（`docs/specs/distill-evidence-format.md`）和目标检查，用 `test -f` 确认两个文件都在。跑目标检查，应为 **20 failed、1 passed**，不符就停下。
- **S0-2**：第一个提交只放这两个文件，提交信息 `test(distill-evidence-format): add spec and goal check`。推送，CI 应为红。
- **S1**：按 §2、§4 实现，并扩 S4 锁。提交信息 `fix(distiller): one evidence rule for descriptive values; verbatim fields keep only the line`。
- **S2**：跑 §5 的测试，推送，等分支 CI 变绿。
- **S3**：按 §6 做重蒸对照，报告写到 `e2e/scratch/evidence/report.md`（gitignored）。**不开 PR、不合并**，报告交给 Shiyu，由 Claude 审计。

## Skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 复用 `VERBATIM_FIELDS`、`REGISTRY` 和调查留下的统计脚本，不重写 |
| S1 | `@test-driven-development` | 目标检查先红，再实现到绿 |
| S2、S3 | `@verification-before-completion` | 测试结果和重蒸数字现跑现贴 |

## 附 A：判断清单（第 7b 条，每条都对过读过的行）

| 判断 | 读过的行 |
|---|---|
| 口癖用的是描述模板 | `core/distiller.py:278`：`'    "catchphrases": [' + _TIMED_TPL + '],\n'` |
| 口癖要求整条值逐字 | `core/card_quotes.py:53–57`：`_VERBATIM_TOP` 含 `"speaking_style.catchphrases[]"`；`:92` 由它派生 `VERBATIM_FIELDS` |
| distiller 已导入 card_quotes | `core/distiller.py:30`：`from core.card_quotes import retract_unverified` |
| 铁律第 6 条 | `core/distiller.py:162` |
| map 阶段提示词 | `core/distiller.py:770–778`（`_map_user_prompt`） |
| 出处要求的全部位置 | §3.1 的原始输出 |
| 稳定项直接拼进聊天 | `core/chat_engine.py:1019`：`f"你的文化程度：{cog.education_level}。"` |
| 长度预算只计原文 | `core/distiller.py:1744`；`core/length_budget.py:65` |
| 前缀与分组的调用点 | `core/distiller.py:2410`、`:2767`（`format_prompt_shared`）；`:2703`（`format_prompt_after(group)`） |
| knowledge_scope 属于经历类 | `REGISTRY` 实测 `layer == "experience"`，所以 V9 同时允许 state 和 experience |
| 现卡数据 | 本地报告 `③-卡内出处核对-报告.md` 的汇总表 |

## 附 B：返工经验逐条对照

| # | 本 spec 怎么做到，或为什么不适用 |
|---|---|
| 1 | §0 方案对比，每个方案都写了「能否达成目标」 |
| 2 | 目标检查先行：原型上 21/21 通过，main 上 20 红 |
| 3 | §0 依据是官方指南和仓库现有的铁律；方向由 Shiyu 10-09 确认「开始做」 |
| 4 | 不适用：只改提示词文本，不引入库 |
| 5 | 不适用：没有界面控件 |
| 6 | §2「以后加东西要改几处」，每种情况都是一处；由 V9 和 S4 锁守住 |
| 7 / 7a / 7b | 每条代码判断都带基线行号；见附 A 判断清单 |
| 7c | 参考实现在原型上跑过；没有写未核实的设计想法 |
| 8 | §3.1、§3.2 贴了扫描的原始输出；现卡数据是 6 张全量，不是抽样 |
| 9 | 只做依据写法这一件事；④ 另开 PR |
| 10 | 提示词影响所有蒸馏路径，属于高风险：配了锁（V9、S4）和真跑验收，不另加全量扫描 |
| 11 | §3.5：token 增量、长度预算、前缀缓存都核对过 |
| 12 | §3.4 调用点矩阵 |
| 13 | 不适用 |
| 14 | §7 对账表是复核方清单；执行方不跑变异 |
| 15 | §5 按固定写法 |
| 16 | §3.7：测试库端口和 ④ 错开；重蒸前确认本地栈没有别的任务在用 |
| 17 | 本文件放进 `docs/specs/`，用 `test -f` 确认（等同于 `Test-Path`） |
| 18 | S0 逐条复核 §3 |
| 19 | 不适用：没有界面 |
| 20–24 | 审计时执行：先跑目标检查；逐文件写结论；自补变异；每个问题写根因；发现写回本文件的「补充」一节 |
| 25 / 26 | §0 否决了打补丁的方案 C；规则一处定义并加锁 |
| 27 | 执行方卡住时，先把它的输出贴给 Claude |
| 28 | §0 和 §2「不改的地方」都附了依据 |
| 29 | 没有转给 Shiyu 的待定项 |
| 30 | **写和验没有完全分开**：spec 和原型是 Claude 写的，审计也由 Claude 做（Shiyu 10-08 定）。为了减少自己验自己的偏差：参考实现只作示意，由执行方独立实现；审计时 Claude 必须自补放宽、过严两个方向的变异 |
| 31 | 对照了今天的返工：行号写错（已逐条重核）、凭样卡推测（已改用 6 张卡的全量数据）、变异改后看不出（对账表每条都实测能打红） |

## 补充（审计后，2026-10-09）

审计方自补变异中两个未打红，属 spec 设计漏洞（实现无偏离）：
1. 维度 C 的口癖口径无测试 —— 「口癖只写原话」落在模板（由逐字清单派生）与维度说明（手写）两处，目标检查只守模板。补 V10：维度 C 必须有「口癖按阶段给，取值就是原话本身」，且不得再有「语气/句式/口癖按阶段给」。
2. 依据规则的退路无测试 —— 删掉「找不到原话就写一句场景转述，不加引号」测试仍绿，缺它模型会为凑引号编原话。补 V11：每个提示词都含这一句。
目标检查由 21 条增至 29 条（V11 每个提示词各一条）；main 上 28 红 1 绿。
