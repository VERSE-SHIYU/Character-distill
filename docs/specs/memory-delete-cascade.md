# 永久删除存档 / 卡 / 群 / 书时，长期记忆跟着删（④ 的 0b）

放到 `docs/specs/memory-delete-cascade.md`。基线 main `33c5173`（#138 合并后）。分支 `feat/memory-delete-cascade`，开 PR。

## 1. 目标

现在永久删除任何东西都不清长期记忆，记忆留在库里成了没人能找到的孤儿。改成（规则由 Tracy 10-11 定）：

| 永久删除的是 | 跟着清掉的记忆 | 不能动的 |
|---|---|---|
| 一个存档 | 只有这个存档的 | 同一张卡别的存档、群里的 |
| 清空回收站 | 回收站里每个存档的 | 没删的存档的 |
| 一张卡 | 这张卡的全部（所有存档、所有群、没有归属的旧记忆） | 别的卡的 |
| 一个群 | 每个成员在这个群里的 | 成员的一对一存档的、别的群的 |
| 一本书（不留卡） | 会被级联删掉的每张卡的全部 | 别的书的卡的 |
| 一本书（留卡） | 不清 | — |

另外两条：

- **进回收站不清**。回收站里的东西还能恢复，恢复后记忆得在。只有永久删除才清。
- **清不掉就不删**。先清记忆、再删行；记忆清失败就返回 500，行留在回收站，用户可以重试。反过来（先删行）的话，行没了而记忆没清掉，就再也没有线索找到它们。

不在本 PR：管理员删用户（已经在清，`web/routers/admin.py:319`、`:374`）；从群里移除一个成员时它在这个群里的记忆；线上已有的旧记忆（Tracy 10-11 定：不找回，留着不管）。

## 2. 已查实的事实（main `33c5173`）

| # | 事实 | 坐标 |
|---|---|---|
| F1 | 四类东西的永久删除都走同一个函数 `hard_delete(entity_type, entity_id, user, storage, *, before_mutation=None, **op_kwargs)`。它先鉴权取记录、确认已在回收站、跑 `before_mutation`，然后调存储删行 | `core/trash_service.py:143–170`（`:166` 跑钩子，`:169` 删行） |
| F2 | 调它的地方共 6 处：存档 2 处、卡 2 处、群 1 处、书 1 处 | `web/routers/history.py:385`、`:405`；`web/routers/card.py:172`、`:188`；`web/routers/group.py:922`；`web/routers/text.py:430` |
| F3 | 清空回收站不走 `hard_delete`，直接一条 SQL 批量删：`count = await storage.purge_trash(user_id)` | `web/routers/history.py:79`；`storage/sqlite_store.py:2208`、`storage/postgres_store.py:1661` |
| F4 | `hard_delete` 取到的记录里：存档有 `card_id`；群有 `card_ids`（成员列表，已解析成数组） | `storage/sqlite_store.py:1958`（`get_session_owned`，SELECT 里有 `card_id`）、`:2450`（`get_group_session_owned`）、`:2462`（`d["card_ids"] = json.loads(d["card_ids"])`） |
| F5 | 永久删书（不留卡）级联删的是「这本书名下全部的卡」，不看卡在不在回收站、不看属主：`DELETE FROM cards WHERE text_id = ?` | `storage/sqlite_store.py:1132–1135`、`storage/postgres_store.py:604–607` |
| F6 | 书进回收站时只给书这一行打删除标记，它的卡不动（还挂在书上、还是正常状态）。所以永久删书时，名下的卡可能是正常的，也可能是用户另外放进回收站的 | `storage/sqlite_store.py` 的 `delete_text`（只有一条 `UPDATE texts SET deleted_at`）；`tests/test_trash_service.py:233–234` |
| F7 | 现有的 `list_cards(text_id, …)` 只返回没删的卡（`deleted_at IS NULL`），拿不到回收站里的；没有「一本书全部卡 id」的方法。有一个同类先例 `get_user_card_ids(user_id)`，注释就是 `for Mem0 cleanup` | `storage/base.py:180`、`:697` |
| F8 | 清记忆用 `MemoryManager.delete_all(scope)`：同步、不需要用户的 key、失败时吞掉异常返回 `False` | `core/memory_manager.py:275–277`、`:589–598` |
| F9 | mem0 的 `delete_all` 自己分批删到空，不受「20 条」影响；带 `run_id` 时只删那个范围，只带 `user_id` 时删这张卡的全部（含没有 `run_id` 的旧记忆） | `mem0/memory/main.py:1918–1935`；0a 的探针实跑过 |
| F10 | 「整卡范围只许在登记处用」这把锁的登记表现在是 `{"memory_scope.py", "memory_manager.py"}`，本 PR 要在 `core/trash_service.py` 用整卡范围 | `tests/test_memory_archive_scope_goal.py:566` |
| F11 | 存储层有一把锁：读属主表的公开方法，SQL 里必须带身份条件，否则要登记到 `OWNER_READ_ALLOWLIST` 并写理由 | `tests/test_storage_scope_lock.py:281` |
| F12 | `tests/test_trash_service.py` 有 4 处直接调 `hard_delete` | `:116`、`:121`、`:149`、`:255` |

F2、F3 的全量扫描（main `33c5173`，命令和原始输出）：

```
$ git grep -n -E 'hard_delete\(|storage\.purge_trash\(' -- web core | grep -v 'async def'
web/routers/card.py:172:    ok = await hard_delete("card", card_id, user, storage)
web/routers/card.py:188:    ok = await hard_delete("card", card_id, user, storage)
web/routers/group.py:922:    await hard_delete("group", group_id, user, storage)
web/routers/history.py:79:    count = await storage.purge_trash(user_id)
web/routers/history.py:385:        ok = await hard_delete("session", session_id, user, storage)
web/routers/history.py:405:    ok = await hard_delete("session", session_id, user, storage)
web/routers/text.py:430:    ok = await hard_delete("text", text_id, user, storage,
```

这条路径上已有的机制，本 PR 都不改变：

| 已有机制 | 在哪 | 和清记忆的先后 |
|---|---|---|
| 鉴权取记录、确认已在回收站 | `core/trash_service.py:155–163` | 在清记忆之前。没权限或不在回收站的，记忆一条不动 |
| `before_mutation` 钩子（只有书在用：取消这本书在途的蒸馏任务） | `core/trash_service.py:166`；`web/routers/text.py:430` | 在清记忆之前。记忆清失败时任务已经取消了；书此时已在回收站，取消是对的，重试不受影响 |
| 群的内存会话在删行之后弹出 | `web/routers/group.py:923` | 在 `hard_delete` 返回之后，不受影响；清记忆失败抛 500 时不会走到这一行 |

未核实：`remote_cards`（对端同步过来的卡副本，`web/routers/inter_node.py:129`、`:235` 删它们）有没有本机的长期记忆。本 PR 不处理，报告里如果查到请列出来。

## 3. 方案

| 方案 | 能否达成目标 | 取舍 |
|---|---|---|
| A. 在 6 个接口里各自加一段清记忆 | 能，但同一条规则写 6 遍；以后加第 7 个永久删除的入口，忘了加就又是孤儿 | 不选 |
| **B. 清记忆放进 `hard_delete` 里，「删什么清什么」的对应关系在 `core/trash_service.py` 里只写一处；清空回收站改成逐个走 `hard_delete`** | 能。以后新增一个永久删除的入口，只要调 `hard_delete` 就自动带上，不传记忆管理器会直接报错 | 选这个 |
| C. 放进存储层的删除方法里 | 能，但存储层要反过来依赖记忆库，两套后端各写一遍 | 不选 |

（对照返工经验第 6、25、26 条：一条规则只写一处；以后加东西只改一处。）

## 4. 改什么

### 4.1 `core/trash_service.py`

- 新增一张对应表：每类东西 → 一个函数，输入「存储、取到的记录、删除参数」，输出要清的记忆范围列表。四个函数分别是：存档 → `MemoryScope.archive(记录的 card_id, 记录的 id)`；群 → 每个成员一个 `MemoryScope.group(成员卡 id, 群 id)`；卡 → `MemoryScope.whole_card(卡 id)`；书 → 留卡时空列表，否则这本书每张卡一个 `whole_card`。
- `hard_delete` 多一个**必填、没有默认值**的关键字参数 `memory`（记忆管理器，可以是 `None`）。没有默认值是故意的：以后新加调用点忘了传，当场 `TypeError`，而不是悄悄不清。
- `hard_delete` 里，在 `before_mutation` 之后、删行之前清记忆：记忆管理器是 `None` 或没启用 → 跳过；否则逐个范围调 `delete_all`（放进 `asyncio.to_thread`，它是同步的）；有一个返回 `False` → 打 ERROR 日志并抛 500，不往下删行。
- `soft_delete`、`restore` 不动。

### 4.2 存储层

新增 `get_text_card_ids(text_id) -> list[str]`：这本书名下全部卡的 id，含回收站里的（`SELECT id FROM cards WHERE text_id = ?`）。三个文件：`storage/base.py`、`storage/sqlite_store.py`、`storage/postgres_store.py`，写法照 `get_user_card_ids`。

它故意不带身份条件：取的必须正好是 F5 那条级联删除会删掉的那一批，而那条 `DELETE` 也不带身份；鉴权已经在 `hard_delete` 取书的记录时做过。所以按 F11 登记到 `OWNER_READ_ALLOWLIST`，理由写这两句。

### 4.3 接口

- F2 的 6 处：各自加依赖 `memory_manager=Depends(get_memory_manager)`，调用时传 `memory=memory_manager`。
- 清空回收站（F3）：改成取 `storage.list_trash_sessions(user_id)`，对每个存档调 `hard_delete("session", …, memory=memory_manager)`，返回的 `purged` 是处理的个数。这样「永久删一个存档」只有一条路。`storage.purge_trash` 之后没有线上调用方了，**本 PR 不删它**（另一件事），报告里列为面外。

### 4.4 连带的测试

- F10：登记表加上 `"trash_service.py"`。
- F12：4 处调用加 `memory=None`。

### 以后要改几处

新增一类能永久删除的东西：对应表里加一行。新增一个永久删除的入口：调 `hard_delete`，0 处额外改动。

## 5. 目标检查

文件 `tests/test_memory_delete_goal.py`。随 spec 附了我在沙箱跑通的原型，可以直接用，也可以改；改了要在报告里说明。

做法：真接口（`TestClient`）+ 真存储（sqlite）+ 真 mem0 加本地 qdrant，embedding 用确定性替身，不调模型。每条都从用户真正点的那个接口进去。

| 检查 | 接口 | 要成立的 |
|---|---|---|
| D1（两条路由各一次） | `DELETE /api/history/{id}/permanent`、`DELETE /api/history/{id}?permanent=true` | 进回收站后记忆还在；恢复后还在；永久删除后这个存档的记忆为 0；同卡另一个存档、群里的不变 |
| D2 | `DELETE /api/history/trash/purge` | 回收站里两个存档的记忆都为 0、行都没了；没删的那个存档记忆和行都在 |
| D3（两条路由各一次） | `DELETE /api/cards/{id}/permanent`、`DELETE /api/cards/{id}/purge` | 进回收站后还在；永久删除后这张卡的存档、群、旧记忆全为 0；别的卡（含同一个群里的）不变 |
| D4 | `DELETE /api/group/{id}/permanent` | 进回收站后还在；永久删除后两个成员在这个群里的都为 0；同一张卡在另一个群的、它的一对一存档的不变 |
| D5 | `DELETE /api/text/{id}/permanent` | 书下面一张正常的卡、一张已在回收站的卡，记忆都为 0；别的书的卡不变 |
| D5b | 同上，带 `keep_cards=true` | 卡还在，记忆不变 |
| D6 | 存档永久删除，记忆库这一刻删不动 | 返回 500；存档行还在回收站；记忆没少；恢复正常后重试成功 |
| D7 | 存档永久删除，记忆系统没开 | 照样删成功 |

调用点 × 检查：F2 的 6 处和 F3 的 1 处，每处至少被一条检查从接口打到（存档两处 D1，卡两处 D3，群 D4，书 D5/D5b，清空回收站 D2）。

沙箱结果：未改代码 **8 failed, 2 passed**（D5b、D7 本来就该绿）；按第 4 节改完 **10 passed**。

## 6. 规模

沙箱实测清一个范围的耗时（真 mem0 + 本地 qdrant，磁盘库）：20 条 0.06 秒，200 条 0.5 秒，1000 条 2.4 秒（约每条 2.4 毫秒）。永久删除是用户点一下、等一次的操作，在线程里跑，不堵别的请求。线上现在全部记忆是几十条的量级。

## 7. 提交顺序

1. spec + 目标检查（跑出 8 failed, 2 passed）。
2. 存储层新方法 + `trash_service` + 7 处接口 + 第 4.4 节的两处测试（跑出 10 passed）。

## 8. 测试

本地只跑受影响的测试文件加 `npm test`，库用 Docker 起的 PG；全量由复核方在沙箱跑并与 main 对比，合并门是分支 CI。受影响的文件：

```
tests/test_memory_delete_goal.py
tests/test_trash_service.py
tests/test_storage_scope_lock.py
tests/test_memory_archive_scope_goal.py
tests/test_ownership_404.py
```

不跑变异（复核方做，并且自己补）；不调付费模型。

沙箱里按第 4 节改完后的全量：只有上面 F10、F11、F12 对应的测试需要改（不改会红 17 条：`test_trash_service.py` 16 条参数化用例、`test_storage_scope_lock.py` 1 条），改完后与 main 的失败名单无差别。

## 9. 报告要写的

1. 第 2 节 F1–F12 逐条复核，有一条不成立就停下报告。「未核实」那条查到什么写什么。
2. 两个提交上目标检查的最后一行真实输出。
3. 这两条的真实输出：
   ```
   git grep -n -E 'hard_delete\(' -- web core          # 每一处都应带 memory=
   git grep -n -E 'storage\.purge_trash\(' -- web core   # 应为空
   ```
4. 和 spec 不一致的地方；面外发现只列不修。
