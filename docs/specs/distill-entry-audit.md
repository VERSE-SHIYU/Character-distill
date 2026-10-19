# F2：蒸馏任务记下「是否重新蒸馏」和「从哪个入口发起」

基线：main `cb883e1c`（含 ⑤b #189），**并且 #188（F1 重新蒸馏确认）必须先合并**：两边都改 `useAppStore.js` 里的 `distillCharacter`。分支：`feat/distill-entry-audit`。
目标：以后再出现「点 A 却蒸了 B」，查数据库就能看到当时发起的是不是重新蒸馏、从哪个按钮发起；线上任务开始那行日志里也有这两项。现有功能不变。
方案依据：总文档第五节 10-19 14:10 条（Tracy 定 F2 = B：distill_tasks 加两列，不只写日志；日志超过 30MB 就轮转覆盖，docker-compose.prod.yml:75-76）。

## 一、目标检查（第 1 个提交只放检查文件，标成预期失败；之后每个提交都跑）

两个检查文件我已经写好，并在 main cb883e1c 上跑过，都是红的（原始输出见第八节）。直接放进对应位置：

| 文件 | 放到 | 检查项 |
|---|---|---|
| `test_distill_entry_goal.py` | `tests/` | E1 新任务：`/start` 带 force、entry，任务行里记下同样的值（参数化 3 组）；E2 续跑复用出错的旧行：行里改成这次请求的 force=False、entry=task_bar；E3 草稿重转：行里 entry=redraft、force=True；E4 entry 只收约定的几种，未知值返回 422，不传时存空串；E5 entry 随线程参数传下去，任务开始那行日志里有 `entry=workbench_card` |
| `distillEntry.test.js` | `web/frontend/src/store/` | E6 `DISTILL_ENTRY` 取值表与后端约定一致；`distillCharacter(..., entry)` 的请求体里带上 entry |

第 1 个提交：pytest 文件里每条测试加 `@pytest.mark.xfail(strict=True)`，vitest 文件里每条 `it(` 改成 `it.fails(`；实现提交里再去掉。

另外两项检查由执行方补写（写法参照现有测试）：
- **E7**：任务栏和工作台任务详情里的「重新蒸馏 / 继续蒸馏」，`distillCharacter` 第 4 个参数分别是 `task_bar`、`workbench_task`。测试参照 `components/__tests__/CharCardHeader.test.jsx` 的写法：mock 整个 store，点按钮后断言调用参数。
- **E8**：#188 已有的 `web/frontend/e2e/redistill-confirm.spec.js` 已经会收集 `/api/distill/start` 的请求体，在它的断言 2（角色页确认）、3（第一次蒸馏）、4（工作台卡片详情）里，各补一句断言 `entry` 分别是 `character_page`、`character_page`、`workbench_card`。

## 二、S0 开工前核对（任何一条对不上就停下报告）

先用 `Test-Path docs/specs/distill-entry-audit.md` 确认文件存在；再确认 `git log origin/main` 里已经有 #188 的合并提交。没有就停下。

| 位置（main cb883e1c） | 应看到 |
|---|---|
| `web/routers/distill.py:251-254` | `class DistillTaskRequest(BaseModel)`：`text_id`、`character_name=""`、`force=False`，没有 entry |
| `web/routers/distill.py:941-945` | 续跑复用旧行：`storage.update_distill_task(task_id, status=..., chunk_size=..., text_fingerprint=...)` |
| `web/routers/distill.py:954-958` | 新任务：`storage.create_distill_task(task_id, user_id, req.text_id, character=..., ...)` |
| `web/routers/distill.py:972-976` | `C.ctx_thread(_run_distill_task, args=(... resume_candidates), kwargs={"draft": draft}, ...)`。`resume_candidates` 必须留在 args 的最后一位，`tests/test_distill_task_api.py` 的 `_capture_start` 取的是 `args[-1]` |
| `web/routers/distill.py:489-494` | `@task_log.scoped(lambda b: (b["task_id"], {"来源": ..., "force": b["force"], "resumed": ...}))` |
| `web/routers/distill.py:1477-1478` | 草稿重转：`DistillTaskRequest(text_id=..., character_name=..., force=True)` |
| `storage/postgres_store.py:3515`、`:3635` | `create_distill_task`、`update_distill_task` 的签名（sqlite_store.py:4263、:4389，base.py:988、:1065 是同一组签名） |
| `storage/migrations/README.md` | PG 新增列时，同时写一份 SQLite 孪生迁移，并登记进 `sqlite_store.py` 的迁移次序表（:165-178，最后一条是 108） |
| `storage/migrations_pg/` | 最后一份是 `043_text_retro_regions.sql` |
| `web/routers/distill.py:1021-1033` | `/task/{id}` 的返回逐个字段写明，不会带出新增的两列 |
| 前端 4 个调用点（以 #188 合并后的行号为准，用 grep 定位） | `CharCard.jsx` 的 `handleDistill`、`DistillWorkbench.jsx` 的 `restartTask` 和 `redistill`、`DistillTaskBar.jsx` 的 `handleRestart` |

## 三、改法

**迁移**：
- `storage/migrations_pg/044_distill_task_entry.sql`：`ALTER TABLE distill_tasks ADD COLUMN IF NOT EXISTS force BOOLEAN NOT NULL DEFAULT FALSE;` 和 `ADD COLUMN IF NOT EXISTS entry TEXT NOT NULL DEFAULT '';`。文件头注释写明用途，写法照 042。
- `storage/migrations/109_distill_task_entry.sql`：SQLite 孪生（BOOLEAN 写成 `INTEGER NOT NULL DEFAULT 0`），登记进 `sqlite_store.py` 的迁移次序表，注释写「109：PG 044 新增列的孪生」。
- 列集锁 `TestPgFreshSchemaClosure` 应该照常通过。

**存储**：`create_distill_task` 和 `update_distill_task` 各加 `force`、`entry` 两个关键字参数，base / PG / SQLite 三处签名一致。create 的默认值是 `False`、`""`；update 的默认值是 `None`（不改）。读取方法不改，新列只供在数据库里查。

**后端**：
- `DistillTaskRequest` 加 `entry: Literal["", "character_page", "workbench_card", "workbench_task", "task_bar", "redraft"] = ""`。**合法取值只在这一处定义**。
- `_distill_start_impl`：两处写库都传 `force=req.force, entry=req.entry`（续跑复用旧行时，记录改成最近这次请求的值）。
- 线程参数：`kwargs` 加 `"entry": req.entry`，不要放进 args（原因见 S0）。`_run_distill_task` 签名末尾加 `entry: str = ""`；装饰器的字段里加 `"entry": b["entry"]`。
- 草稿重转（:1477）构造请求时加 `entry="redraft"`。

**前端**：
- `useAppStore.js` 导出 `DISTILL_ENTRY = { characterPage: 'character_page', workbenchCard: 'workbench_card', workbenchTask: 'workbench_task', taskBar: 'task_bar' }`。`distillCharacter(textId, characterName, force = false, entry = '')`，请求体加 `entry`。#188 加的确认逻辑保持不变。
- 4 个调用点各传对应的常量：CharCard 传 `characterPage`；工作台卡片详情传 `workbenchCard`；工作台任务详情传 `workbenchTask`；任务栏传 `taskBar`。

## 四、行为对照

| 项 | 改前 | 改后 |
|---|---|---|
| 各按钮的蒸馏行为、确认弹窗 | — | 不变 |
| `/start` 请求 | `text_id, character_name, force` | 多一个 `entry`（不传也能用，存空串） |
| `/task/{id}` 返回 | — | 不变（不带出新列） |
| 任务开始日志行 | `来源 force resumed` | 多一个 `entry=…` |
| 数据库 `distill_tasks` | 没有 force、entry | 有；续跑复用旧行时改成最近一次请求的值；书或用户被删时随行一起删，和现在一样 |

## 五、结构自检

| 问题 | 答案 |
|---|---|
| 合法入口取值写了几处 | 后端 1 处（`DistillTaskRequest.entry` 的 Literal）；前端 1 处（`DISTILL_ENTRY`）。前后端各一份，是一份约定的两端，由 E6 和 E4 分别守住 |
| 写库写了几处 | 2 处：新建、续跑复用。这是两种不同的操作，都在 `_distill_start_impl` 里，一处请求一次写入 |
| 以后加一个入口要改几处 | 后端 Literal 加 1 项，前端 `DISTILL_ENTRY` 加 1 项，新调用点传常量 |
| 调用方是否被迫知道不该知道的事 | 调用方只传一个常量，不知道它怎么存、怎么打日志 |

## 六、机制核对（返工经验第 11 条）

这条路径上已有的机制：并发闸 `_gated_start`、续跑任务级门（切分参数 / 全文指纹）、建行失败返回 503、线程参数顺序（测试取 `args[-1]`）、⑤b 的 contextvar 任务 ID。本 PR 只给写库和日志多加两个字段，**不改任何判断和顺序**。线程参数用 kwargs 传，就是为了不碰 `args[-1]` 这个约定。

## 七、验证

```
pytest -q tests/test_distill_entry_goal.py tests/test_distill_task_api.py tests/test_card_redraft_goal.py tests/test_postgres_store.py tests/test_migration_dispatch.py
cd web/frontend && npx vitest run src/store/distillEntry.test.js src/store/redistillConfirm.test.js && npm test
cd web/frontend && npx playwright test e2e/redistill-confirm.spec.js
```
库用 Docker 起的 PG。本地只跑这些受影响的文件，全量回归交给分支 CI。

报告内容：每个提交的 `git show --stat`；第 1 个提交里 E1–E6 的红色原始输出，以及最后一个提交的绿色输出；E7、E8 的输出；上面命令的最后汇总行；CI 结果。末尾附「已核实 / 未核实」。范围内的问题直接修，碰到冲突或需要决定的事项才停下。

## 八、已查实（我在沙箱 main cb883e1c 上跑过；只写了检查，没有写实现）

| 文件 | sha256 前 16 位 |
|---|---|
| `test_distill_entry_goal.py` | `a1ed1d7931ce7be0` |
| `distillEntry.test.js` | `58a533d11b2cfad1` |

```
$ pytest -q tests/test_distill_entry_goal.py --tb=line
E1×3、E2、E3：sqlite3.OperationalError: no such column: force   （请求本身成功；E2 已确认复用了出错行，E3 已确认草稿重转接口返回 200）
E4：assert 200 == 422
E5：kwargs 里只有 {'draft': None}，没有 entry
7 failed
$ npx vitest run src/store/distillEntry.test.js
× 取值表与后端约定一致   × 请求体带 entry      Tests 2 failed (2)
```
E5 的写法另外验证过：用 `llm=None` 调 `_run_distill_task`，装饰器会先打出开始行（实测 `任务开始 来源=蒸馏 force=true resumed=false`），然后才撞上函数体开头的硬门，所以断言能看到这一行。

## 九、不在本 PR

- 没有界面可以查看这些记录：真遇到问题时直接查数据库。
- 不加「只追加、不覆盖」的审计表：方案 C，Tracy 没选。
