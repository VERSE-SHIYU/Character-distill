# 时间事件「已问过」写在事件自己身上（P9）

基线：main `dc7cd3ca`。所有行号都是这一版上实际读过的。跟踪文档：「④ 段 1 本场记录：问题与修复跟踪」P9。

## 0. 目标

角色问过一件到期的时间事件后，这件事在这个存档里就不再被当成没问过，**重启后也一样**；不因此多出一条记忆；标记失败时日志如实报失败。

## 1. 目标检查（第 1 个提交就带上）

文件：`tests/test_time_event_asked_goal.py`。替身只替换真正出网的那一步（`DashScopeEmbedding._call_api`），生产的向量化逻辑照常执行（见 §4.3）。

| 检查 | 守什么 | main 上 |
|---|---|---|
| T1 | 问过以后新建一个引擎（模拟重启），`get_due_event` 不再返回这件事 | 红 |
| T2 | 标记生效（重启后不再问），且这个存档的记忆条数不变（不另存一条） | 红 |
| T3 | 标记写不进去时打 warning，不打「Marked … as asked」 | 红 |
| T4 | 这件事被别的存档的引擎标记，不会改到它（归属核对） | 红（没有这个方法） |
| T5（结构锁） | `core/` 里不再出现 `time_event_asked`；`EventService` 里没有进程内的「已问」集合 | 红 |

已有的 `tests/test_memory_archive_scope_goal.py::test_G1a_asked_marker_follows_the_archive` 和 `tests/test_memory_read_all_goal.py::test_R1_every_reader_sees_the_whole_archive` 换到共享替身后，在 main 上也应当变红（它们原来是在替身上绿的，见 §2 第 3 条）。

## 2. 已查实的约束

1. **「已问过」另存成一条空文本记忆。** `core/event_service.py:107–110`：

   ```python
   self._memory.add_manual(
       "",
       metadata={"type": "time_event_asked", "event_id": eid},
   )
   ```

   读的时候要先把这类记录扫成一个集合，再拿去过滤事件（`:43–47`）。
2. **空文本过不了向量化。** `core/embeddings.py:304–308` 过滤空串，全空返回 `[]`；`Mem0BridgeEmbedder.embed`（`:423`）取 `[0]` → `IndexError`。所以第 1 条的写入基本不会成功。
3. **测试替身替换在错误的位置。** 三个测试文件各自重写了整个 `Mem0BridgeEmbedder`，空串都返回一个全零向量照样入库：`tests/test_memory_read_all_goal.py:34–52`、`tests/test_memory_archive_scope_goal.py:61–84`、`tests/test_memory_delete_goal.py:36–52`。第 2 条的生产逻辑在测试里从没被执行过，G1a 和 R1 替一个生产上不成立的行为作了证。
   - `tests/test_mem0_llm.py:74–81` 也有一个嵌入替身，但它直接赋给裸 mem0 对象的 `embedding_model`（`:141`），测的是 LLM 注入，不经过我们的 `Mem0BridgeEmbedder`。不在本次范围。
4. **失败被吞掉。** `MemoryView.add_manual` 出错时记 error、返回 `False`（`core/memory_manager.py:492–508`）；`mark_asked` 不看返回值，接着打印「Marked event … as asked」（`core/event_service.py:107–111`）。
5. **同一进程里有内存集合挡着。** `_asked_event_ids`（`:31`、`:58`、`:104`）让持久化失败在同一个进程里看不出来，只有重启后才露出。
6. **一件事有两个 id。** 事件写入时另生成 `event_id = uuid4().hex[:16]` 放进元数据（`core/evaluation_pipeline.py:193–198`）；mem0 本身给每条记忆一个 id（`get_all` 结果的 `id`）。
7. **mem0 2.0.20 可以只改元数据。** `Memory.update(memory_id, text=None, metadata=...)`（`mem0/memory/main.py:1815–1866`）；`_update_memory`（`:2038–2098`）在 `text` 为 None 时沿用原文本，把元数据合进原 payload（`new_metadata.update(...)`），身份键 `user_id` / `run_id` 不会被改（`_strip_identity_keys`）。
   - 但它会**用原文本再调一次向量化**（`:2068–2073`：`existing_embeddings` 为空时 `self.embedding_model.embed(data, "update")`），并写一条历史（`self.db.add_history`）。
8. **mem0 的 `get(memory_id)`**（`:1208–1250`）返回的字典带 `user_id`、`run_id`，可用来核对归属。
9. `MemoryView.update(memory_id, text)`（`core/memory_manager.py:567–576`）只能改文本，不核对归属。
10. **线上有没有 `time_event_asked` 旧记录：未核实。** 由于第 2 条，基本不会有；要在两台服务器上查（§7）。

## 3. 根因

| # | 根因 | 对应约束 |
|---|---|---|
| 1 | 「已问过」是事件自己的状态，却被建成另一条记忆：多了一条要向量化的空文本、一条假记忆，读的时候还要跨两类记录拼 | 1、2 |
| 2 | 同一件事的状态有三份：另存的记录、进程内集合、（以后的）元数据；身份也有两份（`event_id` 和记忆 id） | 5、6 |
| 3 | 写入结果没人看，失败被报成成功 | 4 |
| 4 | 测试替身换在整个嵌入器上，生产的输入契约被替身各自重写了一遍 | 3 |

## 4. 设计

### 4.1 结构自检（写代码之前填）

| 问 | 现在 | 改后 |
|---|---|---|
| 同一条规则写了几处？ | 「这件事问过没有」3 处：另存记录（`event_service.py:107–110`）、读时拼集合（`:43–47`）、进程内集合（`:31/:58/:104`）。事件身份 2 处（`event_id`、记忆 id）。「空串怎么向量化」：生产 1 处 + 测试替身 3 处，各写各的 | 「问过没有」1 处：事件元数据 `asked`。身份 1 处：记忆 id。嵌入契约 1 处（生产），测试只替换出网那一步 |
| 调用方是否被迫知道不该知道的事？ | `EventService` 要知道「已问」是另一类记录、要自己维护进程内缓存 | `EventService` 只读写事件自己的元数据；归属核对在 `MemoryView` 里，调用方不碰 |
| 加一种情况要改几处？ | 给事件加一个状态（如「对方已回答」）：再造一类记录 + 读侧再拼一个集合 | 元数据里加一个字段，`update_metadata` 一个调用 |

### 4.2 改动

| 文件 | 改什么 | 消掉的根因 |
|---|---|---|
| `core/memory_manager.py` | `MemoryView.update_metadata(memory_id, metadata, scope) -> bool`：先 `get` 核对这条记忆的 `user_id` / `run_id` 与 `scope.filters()` 一致，不一致返回 False 并记 warning；再 `self._mem.update(memory_id=..., metadata=...)`。出错记 warning、返回 False | 1、3 |
| `core/memory_scope.py` | `ScopedMemory.update_metadata(memory_id, metadata) -> bool`，转给视图（和 `add_manual` 同一个写法） | 1 |
| `core/event_service.py` | 事件身份改用记忆 id；「问过没有」只看事件元数据 `asked`；删掉 `time_event_asked` 的读写和 `_asked_event_ids`；`mark_asked` 调 `update_metadata(eid, {"asked": True})`，按返回值打成功日志或 warning | 1、2、3 |
| `core/evaluation_pipeline.py` | `_persist_time_event` 不再生成、写入 `event_id`（身份就是记忆 id）；日志改为打事件和时间 | 2 |
| `tests/conftest.py` | 新增夹具 `fake_embedding_api`：只替换 `DashScopeEmbedding._call_api`，返回确定性的字符 + 二元组袋向量（取自 `test_memory_archive_scope_goal.py:73–78` 那一版，余弦能区分相似度）；前后清空嵌入模块的共享缓存 `_SHARED_CACHE`（`core/embeddings.py:151–165`），免得跨测试串 | 4 |
| `tests/conftest.py` | 新增 `persist_time_event(scoped, event, due_at)`：调生产的 `EvaluationPipeline._persist_time_event` 写事件。测试不手写事件元数据（见 §4.5） | 4 |
| `tests/test_memory_read_all_goal.py`、`tests/test_memory_archive_scope_goal.py`、`tests/test_memory_delete_goal.py` | 删掉各自的嵌入替身，改为请求 `fake_embedding_api`；R1 的两件事件改用 `persist_time_event` 写，「体检」走生产的注入 → 标记流程问过一次，断言按事件名 | 4 |
| `tests/test_time_event_asked_goal.py` | 新增 T1–T5 | — |

### 4.3 为什么替换 `_call_api`

`_call_api`（`core/embeddings.py:246`）是嵌入器里唯一出网的一步。替换它，空串过滤（`:304–308`）、截断、缓存、`Mem0BridgeEmbedder.embed` 取 `[0]`（`:423`）都走生产代码，测试和生产对同一个输入的反应一致，以后也不会再各写一份。

### 4.5 测试不手写数据格式

写目标检查时先手写了事件元数据（不带 `event_id`），main 的 `get_due_event` 直接跳过这类事件（`core/event_service.py:57–58`），T1–T3 红在「没有到期事件」这个前提上，没测到标记。R1 原来的种子同样是手写的。这和 §2 第 3 条是同一类问题：测试自己写一份格式，和生产分叉。所以事件一律用生产的写入函数造，已问一律走生产的注入 → 标记流程。

### 4.4 机制与规模（第 11 条）

| 项 | 现状 | 改后 |
|---|---|---|
| `mark_asked` 调用时机 | 每轮收尾都调（`chat_engine.py:597`），只有本轮注入过事件才真正写 | 不变 |
| 每次标记的外部调用 | 1 次 mem0 `add` → 1 次向量化（空串直接失败） | 1 次 `get` + 1 次 `update` → 1 次向量化（原文本），1 条历史记录。每件事只在第一次问完时发生一次 |
| 标记失败时 | 进程内集合挡住，本进程不再问；重启后再问 | 下一轮还会再注入、再标记，日志每次都有 warning。失败可见，不被盖住 |
| `get_due_event` | 每轮 `get_all` 读一遍，拼两类记录 | 每轮 `get_all` 读一遍，只看事件本身 |

## 5. 不做

- 不改 `test_mem0_llm.py` 的替身（不经过我们的嵌入器，见 §2 第 3 条）。
- 不在运行时保留读取 `time_event_asked` 的兼容代码。旧记录按 §7 的查询结果处理：没有就到此为止；有就写一次性脚本删掉，不改运行时。
- 不改 `Mem0BridgeEmbedder.embed` 遇到空串的行为：改完后不再有调用方送空串。

## 6. 步骤

1. 第 1 个提交：spec + `fake_embedding_api` + 三个测试文件换用它 + T1–T5。在 main 上跑：T1–T5、G1a、R1 应当红，其余受影响测试绿。
2. 第 2 个提交：§4.2 的四个 `core/` 文件。受影响测试全绿。

## 7. 线上旧记录（未核实，由 Tracy 在两台服务器上查）

只读查询命令见交付的本地提示词。结果为 0：不需要任何处理。不为 0：另写一次性清理脚本，结果记在本文件「补充」一节。

## 8. 对账表（复核方必须验的清单；变异由复核方跑并自己再补）

| 改坏的方式 | 应当拦住它的检查 |
|---|---|
| `mark_asked` 改回另存一条记录 | T2、T5 |
| 恢复进程内「已问」集合 | T5（结构锁）；T1 在集合存在时依然要靠持久化 |
| `update_metadata` 不核对归属 | T4 |
| `mark_asked` 不看返回值，无条件打成功日志 | T3 |
| 测试改回整个替换 `Mem0BridgeEmbedder` | T1（替身对空串宽容时可能放过，复核方要验） |
| `get_due_event` 不看 `asked` | T1、R1 |

## 9. 测试

本地只跑 `tests/test_time_event_asked_goal.py`、`tests/test_memory_read_all_goal.py`、`tests/test_memory_archive_scope_goal.py`、`tests/test_memory_delete_goal.py`、`tests/test_memory_manager.py`、`tests/test_embeddings.py`、`tests/test_departure_notice.py`、`tests/test_user_own_keys.py`，以及 `npm test`。库用 Docker 起的 PG。合并前提是分支 CI 绿和独立复核。

## 补充
