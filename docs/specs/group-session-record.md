# 群聊本场记录（⑤ 最后一项）

放到 `docs/specs/group-session-record.md`。基线 main `c688eac4`（#142 合并后）。分支 `feat/group-session-record`，开 PR。

## 1. 目标

群里每个 AI 人物在这个群里有一份自己的本场记录，效果和一对一一致：

1. 隔离：A 的记录不进同群的 B，也不进同一张卡的一对一存档。
2. 恢复：服务重启后群引擎是新造的，发消息前装回，提示词里有它（单发、广播两条路）。
3. 用户看不到：读群好感的接口不带它；也不能因为存了记录就伪造好感 50。
4. 删群跟着删（硬删）。
5. 一对一行为不变。

抽取、校验、注入全部复用 `core/session_record.py`（#142），本 spec 不改它的规则、提示词和栏目。
「对方」= 用户：沿用 #142（抽取提示词 `the user, who is referred to as 「对方」`，来源只分 user / character）。

目标检查：`tests/test_group_session_record_goal.py` G1–G6，外加 `tests/test_session_record_goal.py` 的 E4 改为新目标。main 上 7 条全红（G3 在 main 上只因存储方法不存在而红：main 上群聊本来不抽，G3 守的是新代码）。

## 2. 已查实的约束（main `c688eac4`，均为读过的行）

| # | 事实 | 坐标 |
|---|---|---|
| F1 | 群聊评估只传用户这句和这个人物的回复：`asyncio.to_thread(engine._evaluate_affinity, message, response)`；其他人物的话不在这一轮里 | `core/group_session.py:183`、`:373` |
| F2 | 点赞路径评估时回复是空串：`asyncio.to_thread(engine._evaluate_affinity, user_message, "")`，每次发消息都跑 | `web/routers/group.py:259`；调用 `:524`、`:700` |
| F3 | 一对一判定只有一处：`return bool(self._session_id) and not self._group_id`；用处只有交给评估、注入两处 | `core/chat_engine.py:703`、`:802`、`:1122` |
| F4 | 群聊拼提示词三处都走 `_build_all_enhancements`，本场记录块已在里面（`persona = ... + self._build_session_record_block()`） | `core/chat_engine.py:1106`；`core/group_session.py` 3 处 |
| F5 | 落库只认 `session_id`：`if not (ctx.storage and ctx.session_id): return` | `core/evaluation_pipeline.py:278` |
| F6 | 群好感恢复两份同样写法（`prev = await self._storage.get_group_affinity(...)` → `load_affinity(prev)`） | `core/group_session.py:137–143`、`:236–243` |
| F7 | 一对一恢复入口 `restore_engine_state` | `core/session_restore.py` |
| F8 | 成员标签靠「`group_affinity` 有没有这一行」判断评估过没有：`if not row: continue  # 该角色在本群从未被评估…不伪造 50` | `web/routers/group.py:729–731` |
| F9 | 硬删群只删 `group_messages`、`group_sessions` | `storage/postgres_store.py:1926–1931`、`storage/sqlite_store.py:2536–2537` |
| F10 | `update_group_affinity` 冲突时只更新好感五列；`get_group_affinity` 写明列名 | `storage/postgres_store.py:3802–3809`、`:3820` |
| F11 | 下一个迁移号：PG 039，SQLite 104（登记进次序表） | `storage/migrations_pg/`、`storage/sqlite_store.py:164–166` |

## 3. 存在哪（按目标定）

| 方案 | 达成目标？ |
|---|---|
| A. `group_affinity` 加一列 | ✗ 先建行会伪造 50（F8）；删群不删（F9） |
| B. `group_sessions` 加 JSON 列（卡 → 记录） | 广播时几个人物并发写同一行，要原子更新；列群读整行有漏给前端的风险 |
| **C. 新表 `group_session_records`（群, 人物）主键** | ✓ 主键 upsert，不互相覆盖；和好感互不牵扯；硬删群加一行 DELETE |

选 C。

## 4. 结构自检（三问）

| 规则 | 写在几处 | 加一种情况改几处 |
|---|---|---|
| 谁有本场记录 | `ChatEngine._has_session_record` 一处：`bool(self._session_id or self._group_id)` | 1 |
| 没有回复不抽 | `EvaluationPipeline._run_session_record` 开头一处（依据只能取自这一轮的一问一答） | 1 |
| 存到哪 | `_persist_session_record` 按 `group_id` 分两支，与 `_persist_affinity` 同一分法；不顺手重构好感 | 1 |
| 恢复 | `core/session_restore.restore_group_engine_state` 一处；F6 两份合成 `GroupSession._restore_members` 调它 | 1 |
| 删群 | `hard_delete_group_session`（两个 store 各一行，同一事务） | 1 |

## 5. 改什么

| 文件 | 改什么 |
|---|---|
| `storage/migrations_pg/039_group_session_records.sql`、`storage/migrations/104_group_session_records.sql` | 新表（SQLite 孪生只为列集锁，按 AGENTS.md 只补表） |
| `storage/base.py`、`postgres_store.py`、`sqlite_store.py` | `save_group_session_record`（upsert）、`load_group_session_record_unscoped`；硬删群一起删 |
| `core/chat_engine.py` | `_has_session_record` 放开群聊 |
| `core/evaluation_pipeline.py` | 空回复不抽；落库按群 / 存档分 |
| `core/session_restore.py` | `restore_group_engine_state` |
| `core/group_session.py` | `_restore_members`，单发、广播共用 |
| `tests/test_storage_scope_lock.py` | 登记 `restore_group_engine_state` 读 `*_unscoped` 的理由 |

## 6. 测试

本地只跑受影响的文件（库用 docker PG）：

```
pytest -q tests/test_group_session_record_goal.py tests/test_session_record_goal.py tests/test_storage_scope_lock.py tests/test_postgres_store.py tests/test_sqlite_fresh_schema.py tests/test_migration_dispatch.py tests/test_group_affinity_list_api.py tests/test_group_members.py tests/test_trash_service.py
```

全量交给分支 CI。沙箱（PG 16）结果：目标检查 + 一对一本场记录 + 边界锁 50 passed；触及改动的 29 个测试文件 907 passed、1 skipped。

真模型验收：合并后由 Tracy 定做不做。
