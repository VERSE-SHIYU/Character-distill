# 本场记录：抽取改为单独一次调用（④ 段 1 修复）

放到 `docs/specs/session-record-extract.md`。基线 main `9b0052af`（#140 合并后）。分支 `fix/session-record-extract`，一个 PR。

- 前置 spec：`docs/specs/session-record.md`（#140）。本文件**取代**它里面「抽取并进评估调用」的部分；存储、恢复、注入位置、一对一判定、R1–R5 校验都沿用。
- 问题出处：验收第 1 场（session `48cad3b35abd`，孔乙己 `d5c9bf10ed78`，139 轮）不合格。问题、原因、证据见共享文档「④ 段 1 本场记录：问题与修复跟踪」P1–P8。
- 参考实现：Claude 沙箱原型，基线 `9b0052af`，改动随本文件交付为 `session-record-extract.diff`。Claude 推不了分支，所以按 #139 / #140 的做法交付 diff。

## 0. 目标

本场记录由每轮单独一次中立的抽取调用更新，不再和好感评估共用一次调用。抽取提示词逐句照抄文献，不转述。好感评估的提示词恢复到 #140 之前，逐字相同。

修的是：

| # | 问题 | 怎么消掉 |
|---|---|---|
| P1 | 抽取被放在「角色本人写内心独白」那次调用里，独白的腔调串进了记录 | 拆成单独一次调用，用分析员口吻 |
| P2 | 栏名里的「你」指反了 | 抽取时用第三人称、写名字；只有注入给角色的块才用「你」 |
| P3 | 抽取说明是转述的，漏了「只记有用的」「已有的不重复记」 | 照抄 StateMem 附录 E.1 原句 |
| P4 / P5 | 改口没取代成；记录无限变长 | 由 P1–P3 解决；判定线见 §6 |

## 1. 目标检查（第 1 个提交就带上）

文件：`tests/test_session_record_goal.py`。原有 21 条中的 P 组和 E 组改写，新增 8 条，共 29 条（第四轮修改后；见文末「补充」）。

| 检查 | 守什么 | main `9b0052af` 上 | 原型上 |
|---|---|---|---|
| P1 | 一对一每轮两次调用；好感评估的提示词和不带本场记录时逐字相同；抽取调用的提示词里没有「你现在就是 / 内心 / inner_voice / 好感」；群聊没有抽取调用 | 红 | 绿 |
| P1b | `build_evaluation_prompt` 没有 `record*` 参数 | 红 | 绿 |
| P2 | 好感评估失败时，本场记录照常更新并落库；抽取调用抛错、回非 JSON、操作格式坏时，好感照常，记录原样 | 红 | 绿 |
| P3 | 抽出什么都不改好感判定（沿用） | 绿 | 绿 |
| P4 | 每轮保存点：有变化时 `affinity`、`record` 各一次；没变化不存；群聊只存 `group` | 红 | 绿 |
| P5 | 抽取用量记在 `chat_session_record` 下 | 红 | 绿 |
| P6 | 低温适配器每个源适配器只派生一次，换了源适配器才重新派生 | 红 | 绿 |
| X1 | 照抄的 11 句原文在提示词里一字不差（只允许 unit→entry） | 红 | 绿 |
| X1b | 示范用另编的人物和句子，不含验收那场对话里的人名、物件、地名 | 红 | 绿 |
| R0b | 跨两句拼起来的依据会被丢掉；提示词把「依据只能来自某一句」写给了模型 | 红 | 绿 |
| R5b | 对方自己说的条目，只能由对方自己的话取代；角色复述的话取代它会被丢掉 | 红 | 绿 |
| X2 | 抽取提示词里栏名用角色名、没有「他对你」；注入块用「你知道的关于对方的事」 | 红 | 绿 |
| E1–E4 | 真引擎 + 真 PG：存档隔离、恢复后接得上、用户看不到、一轮各存一次、群聊不带（沿用，改为从抽取调用拿操作） | E1–E3 红，E4 绿 | 绿 |
| R0–R9、E5 | 校验规则、序列化、`updated_at`（沿用，不改） | 绿 | 绿 |

原始输出（沙箱，PG16）：main 上收集即报错（`core.session_record` 没有 `FIELD_OPS`），整文件红；原型上 `29 passed`。第一轮原型全量 `3518 passed, 1 skipped, 0 failed`；第二轮只改了 `core/session_record.py`、本测试文件和重放脚本，受影响的三个测试文件 `52 passed`（第三轮后目标检查 28 条，三个文件 53 passed）。

真模型的检查不在单元测试里，在 §6（重放）、§7（受控探针）、§8（整场验收）。

## 2. 已查实的约束（main `9b0052af`，每条都读过所引的行）

| # | 事实 | 坐标 |
|---|---|---|
| F1 | 评估提示词把本场记录的说明插在 JSON 段前（`+ record_rules`），把两个字段接在 JSON 段最后（`+ record_fields`）；JSON 段里 `"inner_voice": "你的第一人称内心独白2-3句"` 在前，所以模型先写独白、再写记录 | `core/affinity_service.py:213–214`、`:293`、`:300`、`:307` |
| F2 | 这几行是 #140 加的：`git diff bc3e1dfe 9b0052af -- core/affinity_service.py` 只有这 12 行变化，回退到 `bc3e1dfe` 的版本就是 #140 之前的提示词 | 同上 |
| F3 | 好感评估失败时整个 `run` 提前返回（`if data is None: … return EvalResult(importance=5, applied=False)`），本场记录也跟着不更新 | `core/evaluation_pipeline.py:76–79` |
| F4 | 本场记录现在从评估回复的 `data` 里取操作 | `core/evaluation_pipeline.py:92`、`:158–178` |
| F5 | 每个引擎只建一个流水线；评估用的是引擎的 `self.llm` | `core/chat_engine.py:200`、`:799` |
| F6 | 一对一判定只在一处：`return bool(self._session_id) and not self._group_id`；群聊传进流水线的是 `None` | `core/chat_engine.py:701–703`、`:802` |
| F7 | 注入块接在好感块后面 | `core/chat_engine.py:1106`、`:1120–1124` |
| F8 | `LLMAdapter.chat` 自带重试预算（「3 次非429 / 总墙钟 60s」）；没有按次传温度的参数 | `adapters/llm_adapter.py:940–946` |
| F9 | `derive(temperature=…)` 返回新实例，会新建 `OpenAI` 客户端，不能每轮建。mem0 那边是建一次缓存起来用 | `adapters/llm_adapter.py:867–890`、`:829`；`core/memory_manager.py:311` |
| F10 | 用量记账不阻塞调用方；`action` 是自由字符串，全仓没有枚举表（`git grep` 只在 `core/evaluation_pipeline.py:136` 一处出现 `chat_affinity_eval`） | `core/utils.py:49–79` |
| F11 | 全仓解析模型 JSON 的统一出口是 `loads_llm_json` | `core/llm_json.py:1–5`、`:90` |
| F12 | 评估在回复的 done 帧之后跑：`await asyncio.to_thread(engine.post_stream_process, …)` → `_post_turn` 依次是 评估 → 标记已问 → 记忆入库 → 反思 | `web/routers/chat.py:607`；`core/chat_engine.py:593–610` |
| F13 | 下一条消息会不会等这段收尾跑完 | **未核实**（前端行为） |

全量扫描（命令和原始输出，main `9b0052af`）：

```
$ git grep -n -E "record_rules|record_fields|render_rules|render_json_fields|_record_prompt_parts|_apply_session_record|FIELD_ADD|FIELD_REPLACE|COLUMNS|render_block|BLOCK_TITLE" -- core web scripts tests
（只列和本场记录有关的行；affinity_protocol、context_engine._render_block、migrate 脚本、前端的同名常量与本改动无关）
core/affinity_service.py:213 / 214 / 218 / 293 / 307
core/chat_engine.py:1124   return record_protocol.render_block(self._session_record)
core/evaluation_pipeline.py:92 / 125 / 158 / 162 / 163 / 166
core/session_record.py:26 / 33 / 40 / 88 / 90 / 108 / 110 / 112 / 113 / 136 / 167 / 212 / 217 / 218
tests/test_session_record_goal.py:82 / 116 / 124 / 154 / 161 / 237 / 249 / 259 / 269 / 280 / 286 / 295 / 352 / 365 / 377 / 398 / 414 / 420
```

`scripts/integration_check.py`、`scripts/smoke_eval_e2e.py` 建 `EvalContext` 时不带 `session_record`，默认 `None`，不受影响。

## 3. 设计

### 3.1 模块边界

| 模块 | 管什么 | 不管什么 |
|---|---|---|
| `core/session_record.py` | 栏的定义（`Column`，栏名只写一次）、抽取提示词、`extract`（拼提示词 → `call` → 解析 → `apply`）、校验、注入块、序列化 | 不碰模型和存储：模型调用由调用方以 `call(system, user) -> str` 传进来 |
| `core/evaluation_pipeline.py` | 两个各自独立的步骤：`_run_affinity`（原 `run` 的好感部分，原样搬过去）、`_run_session_record`（真正发起调用、记用量、兜失败、有变化才落库）；低温适配器的缓存 | 不拼提示词，不判规则 |
| `core/affinity_service.py` | 回退到 `bc3e1dfe` 的版本 | — |
| `core/chat_engine.py` | 不改 | — |
| `scripts/replay_session_record.py`（新） | 用真模型重放一场录下的对话，只跑抽取，按 §6 出结论 | 和生产共用 `extract`，不另写一份提示词 |

为什么 `extract` 收一个 `call`：生产路径和重放脚本要用同一份提示词、解析和校验，模型调用的方式又不一样（生产要记用量、要缓存适配器），所以只把「怎么调模型」交给调用方，其余只写一处。

### 3.2 流水线

```python
def run(self, ctx):
    result = self._run_affinity(ctx)                       # 原逻辑，不再碰本场记录
    result.session_record = self._run_session_record(ctx)  # 一对一才调；自己的失败域
    return result
```

- 顺序：先好感、后本场记录。保存顺序仍是 `affinity`、`record`（P4）。
- 好感评估失败，本场记录照常（消掉 F3 的耦合）。抽取失败（调用、解析、校验任一处抛错），记录原样，打 warning，好感不受影响。
- 重试只用适配器自己的预算（F8），不再套一层。
- 用量 `try_record_usage(..., action="chat_session_record")`。
- 低温适配器：`self._record_llm_for(ctx.llm)`，按源适配器缓存，换了源适配器才重新派生（F9）。派生放在 `call` 里，所以替身没有 `derive` 时也只是这一轮不更新，不往外抛。
- 落库仍用 `_persist_session_record`，只在条目有变化时存。

### 3.3 抽取提示词与出处

渲染出来的提示词（第三轮）：角色名「孔乙己」，当前已有 1 条。

```
You are a character state analyst. After each conversation turn, you update the tracked state of one save between the character 「孔乙己」 and the user, who is referred to as 「对方」.

## CRITICAL RULES
1. **USE ONLY DIALOGUE EVIDENCE.** Every entry must be grounded in something explicitly said in the latest turn. Do NOT fabricate events or emotions not supported by the text. Each entry carries "evidence": ONE continuous excerpt copied verbatim from ONE line of the latest turn (either 对方's line or 「孔乙己」's line). Never join pieces of different lines into one evidence.
2. **THIRD-PERSON PERSPECTIVE.** You MUST write ALL entries in the THIRD PERSON: call the character 「孔乙己」 and the user 「对方」. Never use 「我」 or 「你」.
3. Only create entries for information RELEVANT to future decisions.
4. Do NOT duplicate information already in an existing entry -- check the current state first.
5. Keep content concise -- one clear sentence per entry.
6. SUPERSEDE an existing entry when new information contradicts or updates it. Include the ID of the entry being superseded. Look ACTIVELY for supersession patterns: "actually", "instead", "no longer", "switched to", "cancelled", "replaced X with Y", "now Y not X" （中文如「改主意了」「不…了」「改成」「换成」「其实」「取消」）. An entry that has merely not been mentioned recently is NOT contradicted: do not supersede it.
7. NO CHANGE: If the turn contains no new state-relevant information, or if the state already captures everything, return an empty list.
8. **你必须用中文撰写所有输出内容。**

## Columns
- known「孔乙己知道的关于对方的事」：对方明确说出的、关于对方本人的事实：身份、经历、身体状况、明说的喜好、身边的人。依据只能是对方的原话；对方提的问题不是关于对方的事实。
- relation「孔乙己和对方关系的变化」：只在这一轮改变了两人之间的义务、冲突、相互期待或以后能否联系上时才记（原文：a recurring person mention becomes a relationship only when it changes obligations, conflicts, mutual expectations, or later reachability）。
- plans「还没兑现的约定和计划」：任何一方做出的、还没兑现的承诺、计划或决定，包括对方说的行程和安排。孔乙己讲自己的经历、看法、习惯，或劝对方做什么，都不记。

## Output format
Output ONLY a valid JSON object, no other text. One item per fact; a fact that supersedes an existing entry is ONE item carrying "supersedes":
{"record_ops": [{"col": "known|relation|plans", "text": "...", "evidence": "...", "supersedes": <ID of the entry being superseded, or null>}]}

## Few shot examples
(Character 「老陈」 below is only an example, not part of the real conversation.)

Current state: （还没有）
对方：你平时喜欢喝什么茶？
老陈：龙井吧，清淡。
Output: {"record_ops": []}

Current state: （还没有）
对方：我在杭州读研究生，下个月要去北京面试。
老陈：那可得好好准备。
Output: {"record_ops": [{"col": "known", "text": "对方在杭州读研究生。", "evidence": "我在杭州读研究生", "supersedes": null}, {"col": "plans", "text": "对方下个月要去北京面试。", "evidence": "下个月要去北京面试", "supersedes": null}]}

Current state: #2 [plans] 对方下个月要去北京面试。
对方：面试改到上海了，不去北京了。
老陈：上海也好。
Output: {"record_ops": [{"col": "plans", "text": "对方下个月的面试改在上海，不去北京了。", "evidence": "面试改到上海了，不去北京了", "supersedes": 2}]}

Current state: （还没有）
对方：说好了，你教我钓鱼。
老陈：行，下周三来。
Output: {"record_ops": [{"col": "plans", "text": "老陈答应下周三教对方钓鱼。", "evidence": "行，下周三来", "supersedes": null}]}

Do not return anything from the custom few shot example prompts provided above.
-----（以上 system，约 952 token；以下 user）-----
## Current state
#1 [known] 对方自称林小满，苏州人

## Latest turn
对方：说来惭愧，我从小就怕猫，见了就躲。
孔乙己：怕猫？这倒奇了。
```

逐句出处。PHASE-Tree 指 `src/tree_pipeline/update_session_moment.py`，commit `0be378f`；StateMem 指 arXiv 2608.19652v1 附录 E.1，p.14–15。

| 句子 | 出处 | 照抄 / 改动 | 改动的理由 |
|---|---|---|---|
| 第一句（角色状态分析员） | PHASE-Tree `:68`；StateMem E.1 首句 | 合两句，加角色名和「对方」 | 写明双方怎么称呼（P2）；「对方」照 PHASE-Tree `:108` 的中文示例 |
| 规则 1 | PHASE-Tree `:81–84` 规则 3 | 去掉「or clearly implied」；「in the dialogue」改为「in the latest turn」；加 evidence 的要求：只能是这一轮**某一句**里连续的一段原话，不许把两句拼起来 | 代码要求依据是这一轮的原话（`_source_of`：要么在对方那句里，要么在角色那句里），「暗示」给不出原话；提示词把代码的这条约定原样写给模型（第三轮补，见「补充」） |
| 规则 2 | PHASE-Tree `:96–101` 规则 7 | 换成具体称呼 | P2 |
| 规则 3、4、5 | StateMem E.1 Rules 前三条 | 照抄，unit→entry | — |
| 规则 6 | StateMem E.1 第 2 项 SUPERSEDE | 照抄，unit→entry；括号里的中文例子是 Claude 加的；末句沿用 R4 | 原文例子都是英文；R4 来自 #140 已定的设计 |
| 规则 7 | StateMem E.1 第 5 项 NO CHANGE | 照抄，「return an empty list」对应我们的输出格式 | — |
| 规则 8 | PHASE-Tree `:155` | 照抄 | — |
| known 栏定义 | StateMem E.1「From USER statements: explicit requests, preferences, corrections」；mem0 `configs/prompts.py:116`「Create the facts based on the user messages only」；R5 | 「明确说出的」对应 explicit；「对方提的问题不是关于对方的事实」是范围的反面写明 | 第一轮丢了 explicit，重放里 107 条「对方问起 X」 |
| relation 栏定义 | AutoPersonas arXiv 2607.08252v1 p.45 | 括号里照抄原句，前面是中文对应 | — |
| plans 栏定义（原 events） | StateMem E.1「constraints, commitments, or facts」「binding commitments, remaining obligations」；PHASE-Tree `:114` commitments「Promises or decisions」；mem0 `prompts.py:21`「Track Plans and Intentions … any plans the user has shared」；PHASE-Tree `:77–79`「Only track what THIS character learned, felt, or committed to」 | 只记双方还没兑现的承诺、计划、决定，含对方的行程；角色自述和劝告不记 | 第一轮写成「发生过的事」，重放里 228 条角色自述和「再次劝去歇息」；用户计划归栏不一 |
| 输出格式 | PHASE-Tree `:132`「Output ONLY a valid JSON object」；Mem0 §2.1（arXiv 2504.19413 p.4）每条候选事实对应一个操作 | 一个列表 `record_ops`，每条事实一项，取代就在这一项上带 `supersedes` | 第一轮分两个列表，第 16 轮同一件事写了两遍 |
| 示范（4 个，含 1 个「不用记」） | mem0 `prompts.py:27–44`（示范含「Hi.」→ 空列表）；`:51` 照抄末句 | 人物和句子另编（「老陈」），不取验收那场的内容，由 X1b 守 | 提高 A–D 的服从度，又不让重放变成对答案 |
| user 段（当前有效条目 + 这一轮） | StateMem E.1 的 `{current_state}`、`{latest_turn}` | — | — |

从旧说明里删掉的：「每条写完整，带上必要的前因」。上一场的条目正是因为这句写成了长段解读；它和 E.1 的「one clear sentence per unit」冲突，以照抄的原文为准。

采样温度：`EXTRACT_TEMPERATURE = 0.0`，照 PHASE-Tree `:281`。只在 `session_record.py` 写一处，生产和重放脚本都读它。

### 3.4 不变的部分

依据必须是这一轮的原话；来源由代码判定；「了解」栏只收用户的原话；取代核对编号；关系栏只追加不覆盖；每轮最多 6 条；注入整份、不截、超 256 token 打 warning；存储、恢复、一对一判定都不变。

## 4. 机制与规模

| 项 | 现状 | 改后 |
|---|---|---|
| 一对一每轮模型调用 | 评估 1 次（带记录，上一场到第 139 轮输入涨到 13,051 token） | 评估 1 次（回到不带记录；上一场第 1 轮光是带着空记录的说明就有 2,323 token）+ 抽取 1 次 |
| 抽取输入 | — | system 约 540 token，加当前记录（判定线 ≤ 768），加这一轮（约 50–300 token），合计约 0.6–1.6K |
| 群聊 | 评估 1 次 | 不变，没有抽取调用 |
| 时延 | 评估在 done 帧之后跑（F12） | 收尾多一次调用，串行在评估之后；重放脚本记录每次调用的耗时（均值、p95）。下一条消息会不会等收尾跑完未核实（F13），两次调用要不要并行，等实测数字再定 |
| 重试 | 评估外层 1 次 + 适配器预算 | 抽取只用适配器预算 |

## 5. 改动

| 文件 | 改什么 |
|---|---|
| `core/session_record.py` | `Column` 和 `COLUMNS`（栏名带 `{who}`）；`render_extraction_prompt`、`extract`；删掉 `render_rules`、`render_json_fields`；`render_block` 用 `who="你"`；`EXTRACT_TEMPERATURE` |
| `core/evaluation_pipeline.py` | `run` 拆成 `_run_affinity` 和 `_run_session_record`；删掉 `_record_prompt_parts`、`_apply_session_record`；加 `_record_llm_for` |
| `core/affinity_service.py` | 回退到 `bc3e1dfe` |
| `tests/test_session_record_goal.py` | 见 §1 |
| `scripts/replay_session_record.py` | 新文件 |
| `scripts/probe_session_record.py` | 新文件（§7.2） |
| `tests/test_probe_session_record_script.py` | 新文件：探针脚本的离线检查（替身模型），守住它借用的两个引擎方法 |
| `docs/specs/session-record.md` | 首行标「抽取部分已被本文件取代」 |

## 6. 步骤

0. **[核对]** 执行方逐条复核 §2 的事实，有一条不成立就停下报告。
1. **[测试，提交 1]** 只提交 `tests/test_session_record_goal.py`。在 main 上应是收集即报错（没有 `FIELD_OPS`）。
2. **[core + 脚本，提交 2]** 提交其余改动。目标检查 29 条全绿，受影响测试和 `npm test` 通过。
3. **[本地，真模型]** 先小重放（前 30 轮，§7.1），过了再全量重放，**全量连跑两次都要全过**（温度 0 也不保证每次输出一样，见「补充」）。然后跑受控探针（§7.2）。都过了才推分支、开 PR。不过就停下，把 `summary.json` 和 `replay.json` 交给 Claude，**不自己改提示词**。
4. **[PR]** Claude 拉分支复核：逐文件看、全量和 main 对比、自补变异（见 §9）。Tracy 合并。
5. **[本地，真模型]** 受控探针（§7.2）在开 PR 之前跑；合并后再跑整场验收（§7.3）。

## 7. 真模型检查

### 7.1 重放（第 3 步；小重放约 $0.01，全量约 $0.03，峰时翻倍）

小重放先加 `--limit 30`（含 8 条种子、2 处改口、21 轮填充），判定线同下；过了再去掉 `--limit` 跑全量。

```
python scripts/replay_session_record.py --transcript <上一场的 transcript.json> --card-name 孔乙己 --out e2e/scratch/replay-session-record
```

判定线写在脚本顶部，和这里一致：

| 线 | 为什么 |
|---|---|
| 8 条种子都有记录 | 记得住的前提 |
| 2 处改口都用取代完成 | P4 |
| 改口那一轮没有取代之外的重复条目（同栏或同一句依据） | 上一场 #14、#15 |
| 119 轮填充里「了解」栏新增 ≤ 3 条 | P2；填充轮全是用户问角色的问题，0 轮提到「我」 |
| 没有带「不设防」「嘴上」的条目 | P1 |
| 139 轮后注入块 ≤ 768 token | P5；3 栏 × 256 token（BOOKMARKS 的每栏设置） |

判定线本身的检验：把上一场的记录套进同一套判定，结果是 5 条不过（种子 8/8 通过；改口 1/2；第 16 轮重复；填充「了解」103 条；独白腔多条；注入块 9,552 token）。说明这套判定抓得住上一场的问题。

Claude 另外逐条读 `replay.json` 里的记录，看内容对不对，不只看数。

### 7.2 受控探针（全量重放过了之后、开 PR 之前，约 $0.02）

脚本 `scripts/probe_session_record.py`。

- **历史**：只放最后一处种子 / 改口之后、第 1 题之前的对话（第 17–129 轮，11,632 token，在 12,800 的窗口之内）。种子和改口的原话一定不在发出去的内容里，脚本逐题核对，出现了就中止。
- **记录**：从全量重放的 `replay.json` 还原到第 129 轮结束时。
- **提示词**：真引擎拼，`_compose_system_prompt(include_dynamic=False)` 加 `_build_llm_messages`，和线上同一条路。但不走路由器、不检索 Mem0，也不跑每轮收尾。
- **分两组**：同样 10 题，每题各问两次，一组带记录，一组不带记录。
- **判定**：
  - 9 道有标准答案的题：带记录 ≥ 8 道答对；不带记录 ≤ 3 道（历史里角色自己反复提过「脚上那伤」，蒙对几道是正常的）。
  - 第 139 题（态度）没有标准答案，由 Claude 读答案判。
  - 不带记录那组超线，说明还有别的路在兜底，探针作废。

```
python scripts/probe_session_record.py --transcript <transcript.json> --replay <全量 replay.json> \
  --card-json <cards.card_json 导出的文件> --user-role "<会话 48cad3b35abd 的 user_role>" --out e2e/scratch/probe-session-record
```

限制：好感状态用的是默认值，没有恢复成那场的数值；注入块之外的增强块（好感档位等）可能和线上略有差别。这些不影响「记录在不在、角色读不读」这件事。

### 7.3 整场验收（第 5 步，≤ $1）

- 停机规则：用和 `_build_llm_messages` 相同的算法实时算，最后一条种子或改口之后累计超过 12,800 token，才开始提问。
- 抓下第 1 题发给模型的全部消息，证明种子和改口都已不在里面。
- 日志里记下每题有没有调 `search_memory`。
- 判定线照 ④ 文档「验收判定线」：接不上 3 题及以上不合格；改口用了旧值不合格。

## 8. 不做（面外）

- P9（时间事件「已问过」标记存不进去）：另开一个 PR。
- Mem0 在回忆题上没被检索：只在验收里记录，不在本 PR 处理。
- 两次调用并行：等 §4 的时延实测数字再定。

## 9. 对账表（复核方必须验的清单；变异由复核方跑并自己再补，见返工经验第 14 条）

| 变异 | 预期红的检查 |
|---|---|
| 评估提示词里重新接上本场记录的内容 | P1、P1b |
| `_run_session_record` 放到好感失败的提前返回之后 | P2 |
| 抽取异常不兜，往外抛 | P2 |
| 抽取用 `ctx.llm`，不派生低温适配器 | P1（derived） |
| 每轮都派生，不缓存 | P6 |
| 不记用量，或 `action` 写错 | P5 |
| 群聊也发抽取调用 | P1、E4 |
| 没变化也落库 | P4 |
| 改写任一照抄的原句（如去掉「-- check the current state first」） | X1 |
| 栏名改回「他对你的了解」，或注入块用角色名 | X2 |
| `extract` 不经 `apply`，直接收模型给的条目 | R0–R5（经 P、E 组） |
| `supersedes` 为 null 时也打 warning，或当作取代 | R2 |
| 示范里换成验收那场的句子 | X1b |
| 去掉「角色的话不能取代对方说的条目」 | R5b |

## 10. 测试

本地只跑 `tests/test_session_record_goal.py`、`tests/test_probe_session_record_script.py`，加上受影响的 `tests/test_departure_notice.py`、`tests/test_storage_scope_lock.py`，以及 `npm test`。库用 Docker 起的 PG。合并前提是分支 CI 绿。全量由 Claude 在沙箱跑并和 main 对比。

## 11. 判断清单（发出前逐条对照过读过的行）

§2 的 F1–F12 都附了坐标，F13 标了未核实。§3.3 每句提示词都有出处，原文页码对照过 PDF。§1 的红绿数字和 §7.1 的「上一场 5 条不过」是沙箱实跑结果。

## 补充

### 重放第 1 次（10-12 01:34，本地，`2cb1152d`）

判定线过 3 条、不过 3 条：种子 8/8、改口取代 2/2、独白腔 0 都过；第 16 轮重复记、填充轮新增「了解」107 条、注入块 9,156 token 不过。逐轮看 `replay.json`：

- 填充轮每轮固定三条：「对方问起孔乙己 X」进 known；角色自述进 events；「再次劝对方去歇息」进 events。后两类 228 条，来源全是角色。
- 带《论语》进了 events，去京城进了 known，用户的计划归栏不一。
- 第 16 轮取代 #9 之后，又新增了同一件事。

原因是第一轮的栏定义和文献对不上：known 丢了 explicit；events 写成了「发生过的事」；用户计划没定归栏；输出分两个列表。

### 第二轮修改（10-12 09:10，Tracy 同意按此修）

| 改什么 | 出处 |
|---|---|
| known：只记对方明确说出的关于自己的事实，对方提的问题不算 | StateMem E.1 explicit；mem0 `prompts.py:116` |
| events → plans（还没兑现的约定和计划），含对方的行程；角色自述和劝告不记 | StateMem E.1 commitments；PHASE-Tree `:114`、`:77–79`；mem0 `prompts.py:21` |
| 输出合成一个列表 `record_ops`，取代带 `supersedes` | Mem0 §2.1 p.4 |
| 加 4 个示范，人物另编 | mem0 `prompts.py:27–44`、`:51` |

代码校验规则（原话依据、来源判定、R5、编号核对、关系栏只追加、每轮 6 条上限）不变。

如果小重放还压不住 known 栏，下一步换 Mem0 的两步法（§2.1：先抽候选，再逐条和已有条目比，可选「不动」），每轮多一次调用；不再只改措辞。

### 重放第 2 次（10-12 09:39，本地，第二轮 + docstring，`6cc6fe2e`）

小重放 30 轮 6/6 全过。全量 139 轮 5/6：「每条种子都记到」7/8。其余全过：填充轮新增「了解」0 条，注入块 154 token，两处改口都用取代完成、没有重复。

- 第 7 轮（用户请孔乙己回来教下棋）模型给的依据是「你答应我，等我回来教我下棋，好不好？……教棋的事，回来再说。」，把对方那句和角色那句拼在了一起。代码的 `_source_of` 只认「在对方那句里」或「在角色那句里」，所以丢掉了。
- 同样的前 7 轮，小重放那次这一条是记下来的。说明温度 0 下 DeepSeek 的输出也会变，单跑一次的结果不够。

### 第三轮修改（10-12 09:55）

| 改什么 | 理由 |
|---|---|
| 规则 1 写明：依据只能是这一轮**某一句**里连续的一段原话，不许把两句拼起来 | 这是代码早就有的约定（`_source_of`），提示词原来只写了「这一轮的原话」，没写清楚。提示词照代码的约定写，代码不放宽 |
| 新增目标检查 R0b：拼接依据会被丢掉，而且提示词里写了这条 | 守住提示词和代码这条约定的一致 |
| 门槛：全量重放连跑两次都要 6/6 | 应对同一提示词跑两次结果不同 |
| 新增 `tests/test_probe_session_record_script.py` | 探针脚本借用 `ChatEngine._compose_system_prompt`、`_build_llm_messages` 两个私有方法；引擎改了它们，这条离线检查先红，不等花钱跑探针时才发现。没有为此给引擎加公开接口 |

已知、这次不动：低温派生适配器的缓存在流水线一份（本场记录）、`core/memory_manager.py:311` 一份（Mem0 提炼），概念重复。按一个 PR 一件事，记为以后的候选。

### 重放第 3 次与探针（10-12 10:15，本地，`41f1a736`）

全量重放两次都是 6/6。A 种子 8/8，注入块 162 token；B 种子 8/8，注入块 143 token。探针带记录 9/9、不带记录 1/9，判 PASS。Claude 逐条读了两份 `replay.json` 和 `probe.json`：

- 8 条种子在两次重放里内容都对，依据都是对方那句的原话。两处改口都是一项取代完成。填充轮除下面一条外都是空的。
- 探针带记录的回答，细节和记录对得上，例如「我只说回来再说」；不带记录的答不上。不带记录那组第 130 题答出了名字，因为 user_role「旅人林小满」本来就在提示词里，但没答出「苏州」，所以不算。第 135 题带记录那组多编了一句「去看妹妹」，是聊天模型自己的发挥，与记录无关。
- 两处小问题：
  1. 重放 A 第 21 轮记了「对方明日要赶路」，依据是角色自己说的「明日还要赶路呢」，是角色的臆测，记成了对方的计划。只有 1 条，不改。
  2. 重放 B 第 136 轮（提问轮），角色复述「你上回说半月后回来请我教你下棋」，把对方原话那条 #7 取代了，丢了「孔乙己没明确答应」这层意思。违反 R5「对方说的和角色复述冲突时以对方为准」。

### 第四轮修改（10-12 10:25）

| 改什么 | 理由 |
|---|---|
| `apply`：对方自己说的条目，只能由对方自己的话取代；角色的话来取代它就丢掉并打 warning | R5（Infini Memory：同一事实用户说的和 AI 复述的冲突时以用户为准），只在 `apply` 这一处收口。只会让取代更严，不改提示词，所以不用重跑重放：A 没有这种取代；B 只有第 136 轮那一条，现在会被丢掉，#7 保留 |
| 新增目标检查 R5b | 守住这条规则 |
