# 一对一存档的本场记录（④ 段 1）

放到 `docs/specs/session-record.md`。基线 main `bc3e1dfe`（#139 合并后）。分支 `feat/session-record`，开 PR。

设计出处：④ 调研记录「段 1 设计」（三栏、两种操作、R1–R5、验收判定线，Tracy 10-08 同意、10-11 定存哪、群聊不做）。本文件只写怎么落到代码上。参考实现：复核方在沙箱里的原型，改动文件 `session-record.diff` 随本文件交付（交付方式见 §11）。

## 1. 目标

每个一对一存档维护一份「本场记录」，分三栏：他对你的了解；你们关系的变化；发生过的事和承诺。每轮并进现有的评估调用抽一次，有效条目整份注入提示词的动态区，不显示给用户。

1. 存档 A 里发生的事写进 A；同一张卡的存档 B 读不到；A 重新打开后角色仍接得上。
2. 改口（这一轮明确推翻旧条目）后，旧条目不再进提示词，但仍留在存档里。
3. 读好感的接口、返回前端的会话行都不带本场记录。
4. 群聊不受影响：评估提示词和现在逐字相同，不抽、不存、不注入。
5. 好感部分和本场记录部分各自校验：一部分坏了只丢这一部分。

**本 PR 顺带收掉一处已有问题**（改动面内，返工经验第 18 条）：一对一每轮好感存了两次（§2-F6）。

不在本 PR：群聊的本场记录（⑤）；定期压缩（段 2，只监测）；付费验收（合并后跑一次，§10）；SQLite 退役（⑤ 之后单独做）。

## 2. 已查实的约束（main `bc3e1dfe`）

| # | 事实 | 坐标 |
|---|---|---|
| F1 | 评估提示词由 `AffinityService.build_evaluation_prompt` 一处生成；规则段落在 `"输出严格JSON格式"` 之前，JSON 段在最后，好感字段由 `affinity_protocol.render_json_fields()` 生成 | `core/affinity_service.py:205–301`（JSON 段 `:287–300`）；`core/affinity_protocol.py:61–70` |
| F2 | 评估只收到这一轮的一问一答 | `core/affinity_service.py:242–243` |
| F3 | 生产里评估流水线只有一个调用点；它先 `apply_evaluation`（好感），再做副作用 | `core/chat_engine.py:771`、`:788`；`core/evaluation_pipeline.py:76`、`:89–90` |
| F4 | 好感校验已经是独立的：事件不合法只丢好感，其余字段照常生效 | `core/affinity_service.py:360–368` |
| F5 | 群聊和一对一共用同一条流水线：群聊引擎建时 `session_id=""`、带 `group_id`（`web/routers/group.py:179–180`）；群聊调增强块 3 处 | `core/evaluation_pipeline.py:185–216`；`core/group_session.py:159`、`:326`、`:335` |
| F6 | **已有问题**：一对一每轮好感存两次 —— 流水线单聊分支 `core/evaluation_pipeline.py:202–216` 存一次，回到引擎后 `core/chat_engine.py:804` 又存一次（注释写「单一保存点」） | 同左 |
| F7 | 增强块在预算拼装之后接上，预算逻辑管不到它 | `core/chat_engine.py:349–352`、`:1083–1101` |
| F8 | 恢复存档时读好感的代码有两份一样的写法 | `web/routers/chat.py:222–233`、`web/routers/history.py:278–289` |
| F9 | 读好感接口只返回 `resolve_session_affinity` 的结果（来自 `affinity_state` 列）；会话读取方法都写明列名 | `web/routers/chat.py:774`；`storage/postgres_store.py:1417`、`:1434` |
| F10 | `save_affinity_state` 会改 `updated_at`；离开提示用 `updated_at` 判断上次聊天时间 | `storage/postgres_store.py:3486–3495`；`core/chat_engine.py:740–748` |
| F11 | 存储层抽象方法 PG、SQLite 都要实现；契约锁按基类扫描 | `storage/base.py:777–790`；`tests/test_storage_contract_shape.py:158` |
| F12 | 加列必须补 SQLite 孪生迁移并登记进次序表；列集锁不许开豁免；SQLite「只同步到接口还能跑，不为它写用例」 | `AGENTS.md:53`；`storage/migrations/README.md`；`storage/sqlite_store.py:158–162`；`tests/test_postgres_store.py:1342` |
| F13 | 用 `*_unscoped` 方法的函数要在边界锁登记理由 | `tests/test_storage_scope_lock.py:32`、`:46–49` |
| F14 | 「重置会话」只清内存里的对话历史，好感、长期记忆、库里的消息都不动 | `web/routers/chat.py:650–659`；`core/chat_engine.py:1344–1346` |
| F15 | 逐字核对和 token 计数都有现成函数 | `core/quotes.py:verbatim_in`；`core/tokens.py:38` |

全量扫描原文（`bc3e1dfe`，在仓库根目录）：

```
$ git grep -n -E 'build_evaluation_prompt\(|EvalContext\(|EvaluationPipeline\(|\._pipeline\.run\(|apply_evaluation\(' -- core web scripts | grep -v "def "
core/chat_engine.py:198:        self._pipeline = EvaluationPipeline()
core/chat_engine.py:771:        ctx = EvalContext(
core/chat_engine.py:788:        result = self._pipeline.run(ctx)
core/evaluation_pipeline.py:58:    pipeline = EvaluationPipeline()
core/evaluation_pipeline.py:76:        importance = ctx.affinity_service.apply_evaluation(data, ctx.old_stage, ctx.card.psyche)
core/evaluation_pipeline.py:111:            prompt = ctx.affinity_service.build_evaluation_prompt(
scripts/integration_check.py:197:        ctx = EvalContext(
scripts/integration_check.py:213:        pipeline = EvaluationPipeline()
scripts/integration_check.py:423:    pipeline = EvaluationPipeline()
scripts/integration_check.py:468:        ctx = EvalContext(
scripts/smoke_eval_e2e.py:147:    ctx = EvalContext(
scripts/smoke_eval_e2e.py:164:    pipeline = EvaluationPipeline()
scripts/smoke_eval_e2e.py:202:    ctx = EvalContext(
scripts/smoke_eval_e2e.py:219:    pipeline = EvaluationPipeline()
scripts/smoke_eval_e2e.py:264:    ctx = EvalContext(
scripts/smoke_eval_e2e.py:281:    pipeline = EvaluationPipeline()

$ git grep -n -E '_build_all_enhancements\(' -- core web
core/chat_engine.py:352:        system_prompt += self._build_all_enhancements()
core/chat_engine.py:1083:    def _build_all_enhancements(self) -> str:
core/chat_engine.py:1486:            system_prompt += self._build_all_enhancements()
core/group_session.py:159:        system_prompt += engine._build_all_enhancements()
core/group_session.py:326:            system_prompt += engine._build_all_enhancements()
core/group_session.py:335:            system_prompt += engine._build_all_enhancements()

$ git grep -n -E 'save_affinity_state\(|load_affinity_state_unscoped\(' -- core web storage
core/affinity_service.py:405:    state_json, initialized = await storage.load_affinity_state_unscoped(session_id)
core/chat_engine.py:614:        self._save_affinity_state()
core/chat_engine.py:653:            self._save_affinity_state()
core/chat_engine.py:668:                self._save_affinity_state()
core/chat_engine.py:681:    def _save_affinity_state(self) -> None:
core/chat_engine.py:688:                self._storage.save_affinity_state(
core/chat_engine.py:804:        self._save_affinity_state()
core/evaluation_pipeline.py:208:                    ctx.storage.save_affinity_state(
storage/base.py:777:    async def save_affinity_state(self, session_id: str, state_json: str) -> None:
storage/base.py:781:    async def load_affinity_state_unscoped(self, session_id: str) -> tuple[str, bool]:
storage/postgres_store.py:3486:    async def save_affinity_state(self, session_id: str, state_json: str) -> None:
storage/postgres_store.py:3756:    async def load_affinity_state_unscoped(self, session_id: str) -> tuple[str, bool]:
storage/sqlite_store.py:4219:    async def save_affinity_state(self, session_id: str, state_json: str) -> None:
storage/sqlite_store.py:4493:    async def load_affinity_state_unscoped(self, session_id: str) -> tuple[str, bool]:

$ git grep -n -E 'read_persisted_affinity\(|resolve_session_affinity\(' -- core web
core/affinity_service.py:394:async def read_persisted_affinity(session_id: str, storage) -> tuple[dict[str, Any] | None, str | None]:
core/affinity_service.py:417:async def resolve_session_affinity(session_id: str, storage, sessions: dict) -> dict[str, Any] | None:
core/affinity_service.py:422:    data, source = await read_persisted_affinity(session_id, storage)
web/routers/chat.py:224:        data, source = await read_persisted_affinity(session_id, storage)
web/routers/chat.py:774:    aff = await resolve_session_affinity(session_id, storage, sessions)
web/routers/history.py:280:        data, source = await read_persisted_affinity(session_id, storage)

$ git grep -n -E 'ChatEngine\.__new__' -- tests
tests/test_departure_notice.py:41:        engine = ChatEngine.__new__(ChatEngine)
tests/test_length_budget.py:348:    engine = ChatEngine.__new__(ChatEngine)
tests/test_memory_read_all_goal.py:128:    eng = ChatEngine.__new__(ChatEngine)
```

两个脚本建 `EvalContext` 不带本场记录（新字段默认 `None`，按「没有本场记录」处理），不用改。三处 `ChatEngine.__new__` 里只有 `test_departure_notice.py` 走到评估，要补一行属性。

## 3. 结构

| 方案 | 做法 | 取舍 |
|---|---|---|
| A. 堆进 `build_evaluation_prompt` 和 `apply_evaluation` | 说明、字段、校验都写在好感服务里 | 好感和本场记录混在一处，违背「各自校验」，以后改一边要读另一边 |
| **B. 照好感协议另立一个模块** | `core/session_record.py` 管本场记录的全部规则：给模型看的说明、JSON 字段、校验、应用、注入块、序列化；评估流水线把它和好感并列编排 | 选这个。和 `core/affinity_protocol.py` 同一写法；好感服务只多两个可选字符串参数 |

B 的具体形状：

- `core/session_record.py`：`SessionRecord`（不可变，条目元组）、`Entry`、`render_rules(record)`、`render_json_fields()`、`apply(record, data, user_message, assistant_reply) -> (record, warnings)`、`render_block(record)`、`to_persist` / `from_persist`。
- **一对一的判定只有一处**：`ChatEngine._has_session_record`（有 `session_id`、没有 `group_id`）。它决定交不交给评估（`EvalContext.session_record` 为 `None` 就是不抽不存）、注不注入。
- **一对一每轮的保存点只有一处**：评估流水线，好感和本场记录在同一步存；引擎评估完不再另存（收掉 F6）。本场记录只在这一轮有变化时存。
- **恢复只有一处**：`core/session_restore.restore_engine_state`，两个恢复路径都调它（收掉 F8 的重复）。

### 规则怎么落到代码（模型输出是信任边界）

| 规则 | 代码怎么判 |
|---|---|
| 必须有依据 | 每条操作带 `evidence`，必须逐字出现在这一轮的一问一答里（`verbatim_in`）；不在就丢 |
| R1 关系一栏不和③抢 | 关系条目同样要依据，只有结论的给不出原话 → 丢；本场记录不进 `relational_tier`，不碰好感字段；关系一栏按先后记成轨迹，对它的「取代」按新增处理 |
| R2 取代核对编号 | 编号必须是本记录里存在且仍有效的条目（同一轮里第二次取代同一条也算已失效）；否则按新增，打 warning；跨栏取代也按新增 |
| R3 超长只监测 | 注入时某栏超过 256 token 打 warning，不截 |
| R4 取代必须有明确推翻 | 取代的依据也必须是这一轮的原话；「没再提」给不出原话 → 丢，旧条目仍有效 |
| R5 来源字段 | 来源由代码定：依据在用户那句里 = 用户，只在角色回复里 = 角色（两边都有算用户）；「他对你的了解」只收用户来源；模型自称的来源不看 |
| 每轮输出保持短 | 一轮最多收 6 条操作，多的丢掉并打 warning |

存储：`sessions.session_record TEXT`（PG `037`、SQLite `102`）；`save_session_record` 不改 `updated_at`（F10）；会话读取方法不选这一列（F9）。重置会话不清本场记录（F14：和好感、长期记忆一样属于存档状态）。

以后要改几处：本场记录的规则 → 只改 `core/session_record.py`；新增一个恢复入口 → 调 `restore_engine_state`；一对一的判定 → 只改 `_has_session_record`。

## 4. 改什么

| 文件 | 改什么 |
|---|---|
| `core/session_record.py`（新） | §3 的全部规则 |
| `core/session_restore.py`（新） | `restore_engine_state(engine, session_id, storage)`：好感 + 本场记录，各自失败只打 warning |
| `core/affinity_service.py` | `build_evaluation_prompt` 加 `record_rules=""`、`record_fields=""` 两个参数，分别接在 `"输出严格JSON格式"` 前和 JSON 段末尾；为空时提示词与现在逐字相同 |
| `core/evaluation_pipeline.py` | `EvalContext.session_record`、`EvalResult.session_record`；`_record_prompt_parts`；`_apply_session_record`（自己的 try，出错只丢本场记录）；`_persist_session_record`（只在有变化时）；单聊好感分支注明「唯一保存点」 |
| `core/chat_engine.py` | `_session_record`、`_has_session_record`、`load_session_record`、`_build_session_record_block`（接在好感块后面，两种顺序都在）；评估时交出、拿回；删掉 `:804` 那次重复保存 |
| `web/routers/chat.py`、`web/routers/history.py` | 两处恢复好感的代码换成 `await restore_engine_state(engine, session_id, storage)` |
| `storage/base.py`、`storage/postgres_store.py`、`storage/sqlite_store.py` | `save_session_record`、`load_session_record_unscoped`；SQLite 次序表登记 `102` |
| `storage/migrations_pg/037_session_record.sql`、`storage/migrations/102_session_record.sql`（新） | 加列 |
| `tests/test_storage_scope_lock.py` | 登记 `core/session_restore.py:restore_engine_state` |
| `tests/test_departure_notice.py` | `__new__` 拼装的引擎补 `_session_record` |
| `tests/test_session_record_goal.py`（新） | §5 |

## 5. 目标检查

文件 `tests/test_session_record_goal.py`，21 条。存储相关的只在 PG 上跑（`PostgresStore(TEST_DATABASE_URL)` + `main_loop_registered()`，引擎的一轮放进 `asyncio.to_thread`，写法同 `tests/test_secret_never_plaintext.py:115–126`）；规则和流水线层不碰数据库。

| 检查 | 要成立的 |
|---|---|
| R0 | 依据不在这一轮里 → 丢 |
| R1 ×2 | 关系条目没依据 → 丢；关系一栏是轨迹，对它的取代按新增 |
| R2 ×2 | 编号有效 → 旧失效新生效；不存在 / 类型不对 / 空 / 同轮第二次 / 别的存档的编号 → 按新增、旧的不动、有 warning |
| R3 | 超线有 warning、完整注入；未超线没有 warning |
| R4 | 取代的依据不是这一轮的原话 → 丢，旧的仍有效 |
| R5 | 来源由代码定；角色的话进不了「了解」栏，模型自称用户来源也不行 |
| R6 | 被取代的不注入，但仍在存档里 |
| R7 | 没有操作 → 记录不变、没有 warning |
| R8 | 序列化往返；坏数据当空记录 |
| R9 | 一轮最多 6 条 |
| P1 | 一对一的评估提示词有说明、当前条目、完整 JSON 字段；群聊的和不带本场记录时逐字相同 |
| P2 | 好感坏了本场记录照常；本场记录坏了（格式错、或这一步抛异常）好感照常且和不带本场记录时完全一样 |
| P3 | 带不带本场记录操作，好感和关系状态一样 |
| P4 | 有变化时好感、本场记录各存一次；没变化只存好感；群聊只存群好感 |
| E1 | 存档 A 写进 A、B 读不到；本引擎这一轮后就有；A 恢复后注入块里有，B 没有 |
| E2 | 读好感接口、属主会话行、无属主会话行都不带 |
| E3 | 一轮：`save_affinity_state` 一次、`save_session_record` 一次 |
| E4 | 群引擎：评估提示词没有、注入块没有、不存 |
| E5 | 存本场记录不改 `updated_at` |

沙箱结果：main `bc3e1dfe` 上整个文件在导入时就红（`core/session_record` 不存在）；按第 4 节改完 21 passed。

## 6. 对账表（复核方在原型上实跑；放宽、过严两个方向）

| # | 变异 | 预期红 | 实跑 |
|---|---|---|---|
| M1 | 角色回复里的依据也算用户来源 | R5 | 红 |
| M2 | 去掉「了解栏只收用户」 | R5 | 红 |
| M3 | 去掉依据核对 | R0、R1、R4 | 红 |
| M4 | 已失效的也能被取代 | R2 | 红 |
| M5 | 编号不对时丢弃而不是新增 | R2 ×2 | 红 |
| M6 | 关系一栏可以被覆盖 | R1 轨迹 | 红 |
| M7 | 注入块含已失效条目 | R6 | 红 |
| M8 | 超线就截断 | R3 | 红 |
| M9 | 超线不报警 | R3 | 红 |
| M10 | 操作数不设上限 | R9 | 红 |
| M11 | 群聊也加本场记录说明 | P1 | 红 |
| M12 | 本场记录这一步的异常往外抛 | P2 | 红 |
| M13 | 没变化也存 | P4 | 红 |
| M14 | 一对一好感不存 | P4 | 红 |
| M15 | 提示词不列当前条目 | P1 | 红 |
| M16 | 评估提示词漏掉 JSON 字段 | P1 | 红 |
| E1 | 评估后引擎又存一次好感 | E3 | 红 |
| E2 | 群聊也算一对一 | E4 | 红 |
| E3 | 恢复时不读本场记录 | E1 | 红 |
| E4 | PG 保存不带 `WHERE` | E1 | 红 |
| E5 | 会话读取带出这一列 | E2 | 红 |
| E6 | PG 保存顺带改 `updated_at` | E5 | 红 |
| E8 | 注入块没接进提示词 | E1 | 红 |
| E9 | 引擎没把记录交给评估 | E1、E2、E3 | 红 |
| E10 | 引擎没拿回本轮结果 | E1 | 红 |

第一轮存活 3 条，都已处理：M12（P2 用的坏数据走不到异常分支 → 补了「这一步抛异常」的检查）；M16（P1 查的字段名在说明里也出现 → 改成查整段字段）；E7「群引擎也能装进记录」是等价变异 —— 用不用由 `_has_session_record` 在使用处判，`load_session_record` 里再判一次是同一条规则写了两遍，已删掉那一遍。

## 7. 规模

- 每轮评估提示词多出：说明约 300 字 + 当前有效条目 + 两行字段。条目整份注入、不检索（设计「注入什么」，StateMem：600 轮规模下有效状态仍然够小）。
- 一轮最多 6 条操作；每栏超过 256 token 打 warning（段 2 只监测、不压缩）。
- 存储：一列 TEXT，一轮至多一次 UPDATE；没有新增模型调用。

## 8. 提交顺序

1. 本文件 + 目标检查（在 main 上整个文件导入即红）。
2. `core/session_record.py`（R* 全绿）。
3. 存储：两份迁移、基类、PG、SQLite、次序表登记。
4. 评估流水线 + 好感服务 + 引擎 + 恢复入口 + 两个路由；收掉 F6；边界锁登记；`test_departure_notice.py` 补属性（21 条全绿）。

## 9. 测试

本地只跑受影响的文件加 `npm test`，库用 Docker 起的 PG；全量由复核方在沙箱跑并和 main 对比；合并门是分支 CI。受影响的文件：

```
tests/test_session_record_goal.py
tests/test_evaluation_pipeline.py
tests/test_departure_notice.py
tests/test_storage_scope_lock.py
tests/test_storage_contract_shape.py
tests/test_postgres_store.py
tests/test_schema_parity.py
tests/test_sqlite_fresh_schema.py
tests/test_initial_affinity.py
tests/test_affinity_read_api.py
tests/test_personality_goal.py
```

复核方沙箱：原型全量 3513 passed、0 failed；main `bc3e1dfe` 干净基线 3488 passed、0 failed。多出的 25 条 = 目标检查 21 条 + 两个新存储方法在 PG、SQLite 各一条签名契约用例（`tests/test_storage_contract_shape.py:158` 按基类自动参数化）。

## 10. 上线与验收

- 迁移只加一列、可空，旧代码不读它，向后兼容；回滚代码不需要回滚迁移。
- **付费验收**（Tracy 10-11 20:27 同意花钱）：合并后用真模型在演示卡上按设计的「验收判定线」跑一次 —— 聊到最早几轮被挤出历史窗口之后（超过 12800 token）问 10 题早先的事，接不上 3 题及以上不合格；设 2 处改口，用了旧值不合格。不合格就把抽取拆成单独一次调用再验。跑之前先把费用算清楚报给 Tracy。

## 11. 交付方式

按 SPEC-STANDARD 第 4 条，参考实现应以分支提交交付。复核方这次没有推送权限（默认不往仓库推，要推先问），所以交改动文件 `session-record.diff`（基于 `bc3e1dfe`，`git apply --check` 干净）。Tracy 同意的话也可以改由复核方推一个参考分支。

## 12. 报告要写的

1. 第 2 节 F1–F15 逐条复核，有一条不成立就停下报告。
2. 第 1 个提交和最后一个提交上目标检查的最后一行真实输出。
3. 受影响文件的结果；`npm test` 结果。
4. 这两条的真实输出：
   ```
   git grep -n "_save_affinity_state()" -- core/chat_engine.py     # 不应再有评估之后那一处
   git grep -n -E "read_persisted_affinity\(" -- web               # 应为空（都走 restore_engine_state）
   ```
5. 和本文件不一致的地方；面外发现只列不修。
