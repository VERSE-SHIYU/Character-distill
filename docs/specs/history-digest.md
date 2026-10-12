# 历史摘要块：窗口外的对话按块写成摘要（history-digest）

- 日期：2026-10-12。作者：Claude。目标由 Tracy 给的 spec「P13」定。
- 基线：`main` @ `46d34ca`（PR #150 合并后）。下面所有坐标都是这个提交上的。
- 分支：`feat/history-digest`（worktree `.claude/worktrees/history-digest`）。跟踪：④ 段 1 跟踪文档「P13」一节。
- 前置：**P12 合并后再做实现**。本分支当前只落 spec + 目标检查（在 main 上红）；实现待 P12 合 main 后 rebase 再写。
- 参考来源：
  - SillyTavern-MessageSummarize（<https://github.com/qvink/SillyTavern-MessageSummarize>）：逐块摘要、挂在原文上、写完不让模型重写、短期记忆按 token 上限滚动。理由「LLM 反复重写摘要会越写越差」。
  - Claude Code 压缩：不带工具、先写 `<analysis>` 草稿再写 `<summary>` 正文、只留 summary；失败不阻断，连续 3 次失败停止自动重试。
  - 范本模块：`core/session_record.py`（本模块与它同构：一个模块管全部规则、不做 IO、模型调用由调用方以 `call` 传入）。

## 0. 要解决什么

历史窗口挤掉的旧轮现在直接丢（`core/chat_engine.py` 的 `_build_llm_messages` 只从最新往前保留 `MAX_HISTORY` token，更早的不发）。上一场 139 轮验收里，窗口外的情节只剩「本场记录」一条路兜（`search_memory` 全场 0 次）。本段把窗口外的旧轮按块写成摘要，写完不再重写，总量设 token 上限，注入在历史窗口之前，让角色在长对话里接得上情节。

不改：历史窗口算法本身、本场记录、好感、Mem0、前端、蒸馏。

## 1. 目标检查

文件 `tests/test_history_digest_goal.py`，走真实代码（`core/history_digest` 的纯函数 + `ChatEngine._post_turn` 的触发路径）。**main 上没有 `core/history_digest.py`，整个文件在 main 上是收集错误（红）**；实现后按下表的「实现后」列绿。

| 编号 | 检查什么 | main | 实现后 |
|---|---|---|---|
| D1 | 窗口外满 6 轮才产出一块（`next_block`：5 轮返回 None，6 轮返回那 6 轮），不足不调用 | 红 | 绿 |
| D2 | 已摘要的轮不再重摘（`pending_turns` 排除 `digests.covered_turns()` 之前的轮） | 红 | 绿 |
| D3 | `render_block` 超 token 上限丢最旧、保最新；空 → `""` | 红 | 绿 |
| D4 | `parse` 剥掉 `<analysis>`，取 `<summary>`；缺 `<summary>` 抛错 | 红 | 绿 |
| D5 | 调用失败 / 回复不可解析：`digests` 不变、对话不受影响；连续 3 次失败后本会话不再调用 | 红 | 绿 |
| D6 | 结构锁：`core/history_digest.py` 不 import `chat_engine` / `storage`（同 session_record 的写法） | 红 | 绿 |

## 2. 已查实的约束（S0 逐条核对 @ `46d34ca`）

| # | 事实 | 坐标 @ 46d34ca | S0 核对 |
|---|---|---|---|
| 1 | 历史窗口：从最新往前成对保留，攒满 `MAX_HISTORY` 即停，更早的直接不发；无摘要 | `core/chat_engine.py:547–587` `_build_llm_messages`；`core/context_engine.py:242` `MAX_HISTORY = budgets["history"]` | ✅ 与原文一致（实为 547–587） |
| 2 | 旧摘要是 1584bdd 加、9c40ce3 去的 | `1584bdd6`（2026-05-18）加 role=`summary` + 阈值 40→50；`9c40ce32`（2026-05-20）删掉**生成器**（`summary_threshold` 形参、`_summary_lock`、摘要提示词、`history=[summary_message,…]`），只留字段 | ✅ 实查两提交 |
| 3 | 残留：`last_summary`（无生成器，只从库里恢复回填）、恢复时跳过 role=summary、`summary_threshold` 引擎不用 | `chat_engine.py:225`；`web/routers/history.py:261–273`、`web/routers/chat.py:216–221`（`_rebuild_history_from_db` 白名单见 `:66–85`）；`config.example.yaml:20`(=100)、`web/server.py:523/565` | ✅ 且**另有读者**：`summary_threshold` 还被 `ApiConfigPanel.jsx:32/47/390`、其单测、7 个 e2e mock、`README.md:544`、`AGENTS.md` 读；`last_summary` 经 `result["summary"]`→`useAppStore.js:1464`→`ChatArea.jsx:699` 渲染成前端气泡 |
| 4 | 范本 `session_record.py`：一个模块管全部规则、不做 IO、`call` 由调用方传；单独一次低温调用；失败只丢本轮 | `core/session_record.py`；`core/evaluation_pipeline.py:163–197` `_run_session_record`（`llm.derive(temperature=0.0)` + `try_record_usage`，失败 `return ctx.session_record`） | ✅ 与原文一致 |
| 5 | 写 role=summary 的现有函数 | **无 summary 专用函数**；有通用 `storage.save_message(session_id, role, content, rag_context, *, client_key=…)`（`storage/base.py:497`），现有 role=`summary` 写走它（`web/routers/chat.py:406–407`、`:581–582`），`get_messages` 已返回 summary 行 | ✅ 判据见 §9 决策一 |

## 3. 设计

### 3.1 模块 `core/history_digest.py`（与 session_record 同构）

不做 IO、不认识 chat_engine / storage。数据、提示词、解析、注入块、序列化都在这里；模型调用由调用方以 `call` 传入。

```python
BLOCK_TURNS = 6                 # 一块多少轮（MOOM 打包粒度）
BUDGET_TOKENS = 768             # 摘要总量上限，与本场记录注入块同量级（见 §9 决策三）
BLOCK_TITLE = "【更早的经过】"
MAX_CONSECUTIVE_FAILURES = 3    # 连续失败这么多次，本会话停止自动摘要

@dataclass(frozen=True)
class Digest:
    from_turn: int              # 含，0 起
    to_turn: int                # 不含
    text: str

@dataclass(frozen=True)
class Digests:
    items: tuple[Digest, ...] = ()
    def covered_turns(self) -> int                 # BLOCK_TURNS * len(items)
    def to_persist(self) -> str                    # 序列化（存档列的唯一读写格式）
    @staticmethod
    def from_persist(raw: str | None) -> "Digests" # 坏数据不抛，当空

def pending_turns(history, window_start, digests) -> list[list[dict]]
    # 窗口外、尚未摘要的轮，旧→新；每轮 = [user_msg, assistant_msg]
def next_block(pending) -> list[list[dict]] | None
    # 满 BLOCK_TURNS 返回最旧那一块，不足返回 None
def render_prompt(turns, char_name) -> tuple[str, str]
    # 一块（BLOCK_TURNS 轮）→ (system, user)；正文第三人称，记事件、情绪变化、约定，
    # 不重复本场记录已有的用户画像
def parse(reply) -> str
    # 剥 <analysis>…</analysis>，取 <summary>…</summary>；缺 <summary> 抛 ValueError
def render_block(digests, budget=BUDGET_TOKENS) -> str
    # 从新到旧取，累计超 budget 丢最旧；空 → ""
def extract(digests, turns, *, char_name, call) -> Digests
    # render_prompt → call(system, user) → parse → 追加一个 Digest
    #   (from_turn=covered_turns(), to_turn=from_turn+len(turns))。解析失败抛错，digests 不动
```

- 块边界：块从会话最开始连续切，`[0,6)`、`[6,12)`…；只切整块（不足不切）。故 `covered_turns()` 直接由块数推出，`pending_turns` 只返回 `covered_turns()` 之后的窗口外轮。
- 注入块的标题 `【更早的经过】`（对齐 session_record 的 `【这个存档里发生过的事】`）。
- 抽取 system 提示词首行固定为 `You are a conversation summarizer`（目标检查按它给替身分流，同 session_record 的 `_RECORD_SYS`）。
- 常量都在模块顶，注明出处。

### 3.2 `chat_engine`：只在两处调用

1. **注入**：新增 `_build_history_digest_block()`（同 `_build_session_record_block`），拼进 `_build_all_enhancements` 的 `persona`（`core/chat_engine.py:1109`）：`persona = self._build_affinity_persona_block() + self._build_session_record_block() + self._build_history_digest_block()`。放 system prompt 里、本场记录块旁——因为历史窗口在 messages 数组里，而 spec 要求「不在 `_build_llm_messages` 里写业务逻辑」（§9 决策二）。空 / 群聊 / 无落库身份 → `""`。
2. **触发**：`_post_turn`（`core/chat_engine.py:593`）里，若有满一块的窗口外轮，**交评估流水线同一套方式**单独调一次：走 `EvaluationPipeline`，一个新增的 side-effect（`_run_history_digest`，与 `_run_session_record` 同一写法：`_record_llm_for` 派生低温适配器 + `try_record_usage` + 自己的 try）。失败只记 warning、本轮不写。连续 `MAX_CONSECUTIVE_FAILURES` 次失败后本会话不再调用（计数器挂在 `ChatEngine`）。

   **已知边界**：`affinity_enabled=False` 时整条评估流水线不跑，摘要随之停 —— 与本场记录一致（它同为流水线的 side-effect），另开 PR 解开。

- 窗口起点只算一处：把 `_build_llm_messages` 里「从末尾成对攒到 budget 为止」的起点抽成 `ChatEngine._window_start(history) -> int`，`_build_llm_messages` 与摘要触发共用（避免窗口规则写两遍）。**懒算**：注入给流水线的是解析器 `self._window_start` 本身（`EvalContext.resolve_window_start`），只在真要抽摘要时才算 —— 好感评估每轮都跑，但多数轮没有满一块的窗口外轮；若在组装 `EvalContext` 时就算，会让好感路径每轮都依赖 `_ctx_engine.MAX_HISTORY`（无意义且拖累）。
- 引擎状态：`self._digests: Digests`、`self._digest_failures: int`；`load_history_digest(digests)` 供恢复用（同 `load_session_record`）；`_build_history_digest_block()` 供注入。

### 3.3 存储（§9 决策一）

`Digests` 存会话行的一列 JSON，与 `session_record` 同一写法（`sessions.history_digest`，`TEXT`，NULL=还没有）：

- 迁移：PG `storage/migrations_pg/040_history_digest.sql` ↔ SQLite `storage/migrations/105_history_digest.sql`（**双端注册**，列集锁要求两侧相等；加入 `storage/sqlite_store.py` 的迁移名单与本端 PG 名单）。
- 方法：`save_history_digest(session_id, json)` / `load_history_digest_unscoped(session_id)`，`storage/base.py` 起、sqlite + postgres 各实现一份（未实现 `NotImplementedError`，同 session_record）。内存锁 `test_storage_scope_lock` **无需改动**：`restore_engine_state` 已在 `UNSCOPED_ALLOWLIST`，且两端都实现这两个方法、无 PG-only 漂移。
- 恢复：`core/session_restore.py` 里 `restore_engine_state` 加一段（各自 try，失败只 warning），同本场记录。群聊不涉及（本段只做一对一）。

**为什么不复用消息表 role=summary**：见 §9 决策一。

## 4. 改动

| 改哪里 | 改什么 |
|---|---|
| `core/history_digest.py` | 新增（§3.1） |
| `core/chat_engine.py` | `_window_start` 抽出并被 `_build_llm_messages` 复用；`_build_history_digest_block`；`_build_all_enhancements` 拼一行；`_post_turn` 触发；`load_history_digest`（恢复用）；失败计数器 |
| `core/evaluation_pipeline.py` | 新增 `_run_history_digest`（side-effect，与 `_run_session_record` 同写法）；`EvalContext` 加 `history`、`resolve_window_start`（窗口起点解析器，懒算）、`digests` 字段；`EvalResult` 回传新 `digests` |
| `core/session_restore.py` | `restore_engine_state` 恢复 `history_digest` |
| `storage/base.py`、`storage/sqlite_store.py`、`storage/postgres_store.py` | `save_history_digest` / `load_history_digest_unscoped` |
| `storage/migrations_pg/040_history_digest.sql`、`storage/migrations/105_history_digest.sql` | 新列 |
| `tests/test_history_digest_goal.py` | 新增目标检查（§1） |
| `tests/test_departure_notice.py` | `_make_engine` 的替身引擎补 `_digests` / `_digest_failures`（真 `__init__` 新增的两项，test 用 `ChatEngine.__new__` 手搭替身） |
| `tests/test_session_record_goal.py` / 结构锁测试 | 若列集锁测试枚举列，加新列（按实际） |

## 5. 对账表

变异 → 预期红的测试。**由独立复核窗口在交付的分支上实跑，有存活不发**（SPEC-STANDARD 三）。

| 变异 | 预期红 |
|---|---|
| 块大小 6 → 5 | D1 |
| 不足 6 轮也切块（`>=` 写成 `>`） | D1 |
| `pending_turns` 不减去 `covered_turns()`（重摘） | D2 |
| `render_block` 超上限丢最新、保最旧 | D3 |
| `parse` 不剥 `<analysis>` | D4 |
| `parse` 缺 `<summary>` 时返回空串而不抛 | D4 |
| 失败后把 `digests` 也改了 | D5 |
| 连续失败计数不生效（第 4 次仍调用） | D5 |
| 模块 import `chat_engine` / `storage` | D6 |

## 6. 测试

本地只跑受影响文件：

```
python -m pytest -q tests/test_history_digest_goal.py tests/test_length_budget.py tests/test_session_record_goal.py tests/test_storage_scope_lock.py tests/test_storage_contract_shape.py tests/test_migration_dispatch.py
```

（`test_session_restore.py` 不存在 —— 恢复路径实由 `test_session_record_goal.py` 覆盖；聊天窗口与注入由 `test_length_budget.py` 覆盖。存储相关的检查只在 PG 上跑，同 session_record 目标检查。）合并门是分支 CI；合并只做 git。报告里不写本地全量数字。

## 7. 上线和回滚

- 新列 NULL 兼容旧数据；回滚 = 回退这个合并提交（新列留着无害，视为 unknown column 不读）。
- 不新增常驻进程 / 服务 / 向量库（服务器内存紧）。
- 部署：合并进 main 不自动上线（deploy 手动触发）。

## 8. S0（执行方开工前逐条复核，有一条不成立就停下报告）

1. `git rev-parse --short origin/main` 是 `46d34ca`。不是的话报新提交，并报 `git diff 46d34ca origin/main --stat -- core/chat_engine.py core/context_engine.py core/session_record.py core/evaluation_pipeline.py core/session_restore.py web/routers/chat.py web/routers/history.py web/server.py config.example.yaml storage/`。
2. §2 表第 1–5 条逐条打开对应行核对原文。
3. 全量扫描重跑：`storage` 层写 role=summary 的调用点仍只有 `web/routers/chat.py:406–407`、`:581–582`；`save_message` 签名仍如 §2 第 5 条。
4. PG 迁移最新号仍是 039（`storage/migrations_pg/`）；SQLite 名单最新仍是 104。已被占则顺延并报。
5. 结构锁测试（`test_storage_scope_lock`）当前允许的方法名单里有没有 `save_session_record`，新方法按同样的加。

## 9. 决策记录（S0 拍板，2026-10-12）

**决策一：存会话行 JSON，不复用消息表 role=summary。** spec S0 要求二选一。查实：通用写入路径 `storage.save_message` 存在、可用，但 role=`summary` 的行**会被前端渲染成可见气泡**（`ChatArea.jsx:699`、`HistoryPanel.jsx:1016`、`useAppStore.js:1464`），与本模块「内部、不给用户看」（同 session_record）冲突；且 `save_message` 无结构化列装 `from_turn/to_turn`（只有 content/rag_context/evidence 文本列）。存会话行 JSON：不进消息/UI 路、能结构化存轮区间、正合「与 session_record 同构」、追加一块是原子操作。代价（新列+迁移+双端方法+scope 名单）有现成模板。

**决策二：注入在 system prompt、本场记录块旁。** spec 写「历史窗口前、本场记录块旁」，但本场记录块在 system prompt（`_build_all_enhancements`），历史窗口在 messages 数组（`_build_llm_messages`）——是两处。且 spec 明令「不在 `_build_llm_messages` 里写业务逻辑」。故注入落在 system prompt，与 `_build_session_record_block` 相邻；「在历史窗口前」由 system prompt 恒在 messages 之前满足。

**决策三：总量上限 768 token。** 与本场记录注入块同量级（session_record 每栏 `COLUMN_TOKEN_WARN=256`、四栏；验收判定线「139 轮后有效内容 ≤ 768 token」）。常量放模块顶。

**决策四：残留清理（`last_summary` + `summary_threshold`）挪到独立 PR，本段不碰。** S0 查实另有读者（前端气泡 + 设置页字段 + e2e + 文档），范围比 spec 设想的「配置 + 设置接口字段」大；且与摘要块功能无耦合。本段不删。

## 10. 补充（待写）

- 真模型验收（P12 合入、实现完成后）：一场 ≥140 轮长对话，窗口外的情节题（非用户画像题）能答对；本场记录相关判定线不退化；记录每块调用次数与花费。

## 11. P14 修改：取轮改按角色配对（2026-10-12）

### 现象

P13 验收探针（存档 `bc01a19d51ee`，产物 `e2e/scratch/p13-probe/`）产出的 8 块摘要里，第 2、4、5 块把提问方/作答方写反。例：第 4 块写「孔乙己接连问对方……小时候模样」，而第 19–24 轮实际是**用户问、孔乙己答**。角色自己的事（欠掌柜十九个钱、写字、读过书没进学、长衫）被写成了对方的事。这段内容注入给角色，角色会把孔乙己的事当成用户的事用。

### 根因

`pending_turns` 按**位置**取轮（`[[history[2*t], history[2*t+1]] …]`），默认历史以 user 开头；实际历史以**角色开场白**开头。

证据（存档 dump `e2e/scratch/p13-probe/db_dump.json`，本段副本 `e2e/scratch/p14-probe/db_dump.json`）：

- `bc01a19d51ee` 共 **279** 条消息，角色分布 `{char: 140, user: 139}`，**第 0 条 `role=char`（开场白）**，其后 `user/char` 严格交替。

有开场白时，按位置取到的每对变成「角色上一句 + 用户下一句」，整体错位一格；`render_prompt` 再把一对里的第 1 条标成「对方」、第 2 条标成角色名 —— 于是 8 块送进模型的输入**说话人全部标反**，模型照着标签写，摘要就写反了。

`_window_start` 不受影响：它从末尾往前成对保留（末尾是 assistant，往前步步落在 user 上），返回的仍是 user 下标。只是它的 docstring 写成「历史以 user 开头、返回偶数下标」，与线上不符（有开场白时是奇数）。

### 修法（只动取轮逻辑；提示词、存储、调用链不动）

- 抽出 `history_digest.turn_spans(history)`：一轮 = 一条 `user` 消息配它**后面紧跟**的 `assistant` 消息；开场白（前面没有 user）不属于任何一轮，user 后没有 assistant 的半个轮也不算。**取轮规则只此一处**。
- `pending_turns` 改用它：`turns = [[history[u], history[a]] for u, a in turn_spans(history[:window_start])]`，再减去 `covered_turns()`。窗口外的轮 = **整轮**都落在 `window_start` 之前（不再用 `window_start // 2`）。
- `ChatEngine._window_start` 复用同一个 `turn_spans`（结构锁 D6 要求 `history_digest` 不 import chat_engine，故函数放 `history_digest`、chat_engine 反向引用；chat_engine 早已 import 它）。返回值的**语义不变**（仍是「最早保留消息的下标」，即某个 user 下标）：成对历史上与旧位置实现逐位相同，穷举 12 组历史 × 58 组预算 = **696 次比对、0 不一致**（`artifacts/p14_window_start_equivalence.py` + `.out.txt`）。docstring 一并改正。
- 另一处同类按位置数轮（`core/chat_engine.py:1411` 沉默冷却的 `current_turn = len(self.history) // 2`）改成按角色计数（`sum(1 for m in … if role == "user")`），**单独一个提交**；两种历史下值都不变，但计数规则自此只剩一种（`:546` 早已是角色计数）。
- 目标检查 **D8**：历史以角色开场白开头时，每轮都是 `[user, assistant]`、开场白不属于任何一轮；边界「只有开场白、还没有用户发言」返回空。

### 为什么测试替身没抓到

D1–D6 的测试历史都由 `_flat`/`_turns` 造，一律**从 user 开头、无开场白**。在这种历史里「按位置配对」与「按角色配对」完全等价，旧实现照样全绿。线上存档有开场白，这个形态测试替身没覆盖。

### 保留不改的两处

- `scripts/probe_session_record.py:116` 的 `len(history) // 2`：离线探针脚本、非生产路径；且 `(2t+1)//2 == (2t)//2 == t`，有开场白时数值不变，不构成活 bug。probe 是验收工具，不在本段改动面内。
- `docs/specs/artifacts/arc_behavior_inject_mutations.py:102` 的变异串 `prior = len(self.history) // 2`：这是**历史记录**（当时 `_window_start` 的旧写法）。保留原文以留痕；它指向的源码早已是角色计数（`:546`），该变异据此失真 —— 改它只会抹掉历史。

### 验收（真模型，8 次调用，< $0.01）

离线用新取轮逻辑把该存档第 1–48 轮（8 块）重写一次（`e2e/scratch/p14-probe/rewrite.py`，产物 `blocks.json`）。逐块三项判定：

1. 谁问、谁答、谁去金陵，方向对不对；
2. 角色自己的事（欠钱、写字、长衫、读书）有没有被写成对方的事；
3. 送进模型的每块输入里，每一行的说话人标签是否与原消息的角色一致 —— **第 ③ 项由代码按字节重建断言，不人工看**。

结果：8 块**三项全部通过**（③ 8/8 `labels_match_roles=True`）。
