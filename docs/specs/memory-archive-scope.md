# ④ 段 0a：长期记忆按存档隔离

状态：**第 1 步（目标检查 + 最小原型）**。本文件现在只有第 1 步要用的内容；正式改动、对账表在第 2 步补进同一文件。

- 基线：main `24df878`（2026-10-11 读）。
- 分支：从 main 开 `proto/memory-archive-scope`。
- 放置：本文件放进 worktree 的 `docs/specs/memory-archive-scope.md`，随第 1 个提交入库。
- 以代码为准：§3 的事实有一条对不上，就停下报告，不要按本文件硬做。

## 1. 目标

每个存档的记忆是它自己的。用户能感觉到的就是三件事：

1. 同一张卡开两个存档，在 A 里告诉角色一件事：回 A，他记得；去 B，他不知道。
2. 群聊里说过的事，这张卡的一对一存档里不知道；反过来也一样。
3. 升级前留下的旧记忆（没有存档标记）不会串进任何存档或群。

这三条不成立，别的测试全绿也算没做成。

**这一步不做**：旧记忆归回各自存档（迁移）、删除存档时清记忆、记忆面板接口和前端、`scripts/` 下的脚本、本场记录。旧记忆迁移是 0a 的下一步，要先看线上真实数据再定规则。

## 2. 第 1 步要交付的

两个提交，推到 `proto/memory-archive-scope`，不开 PR。

### 提交 1：目标检查（只加测试，不改实现）

文件：`tests/test_memory_archive_scope_goal.py`。

**必须是真的**

- 读写都经过真实的 `ChatEngine` 及它带的评估管道、事件服务、反思服务、`ContextEngine`。不能直接调 `MemoryView` 的方法来代替引擎。
- 隔离由真的 `mem0ai==2.0.20` 加本地 qdrant（临时目录）来过滤。不能用自己写的假过滤器证明隔离。仓库里已有真 mem0 的搭法可以参考：`tests/test_mem0_llm.py:119–142`。

**可以用替身的**：聊天模型、评估模型、embedding、mem0 提炼用的 LLM。不联网，不调付费模型。

**要验的**

| # | 检查 | 通过侧 | 拒绝侧 |
|---|---|---|---|
| G1 | 同卡两个存档 A、B | A 里产生的记忆，A 读得到 | B 读不到 |
| G2 | 群聊与一对一 | 群里产生的记忆，同一个群读得到 | 这张卡的一对一存档读不到；一对一产生的，群里读不到 |
| G3 | 没有存档标记的旧记忆 | — | A、B、群都读不到 |
| G4 | 换记忆视图（`set_memory`）之后 | 仍读得到本存档换之前写的记忆 | 仍读不到别的存档的 |

G1、G2 要覆盖 §3-F2 的全部 8 个读写点：4 个写入点各产生一条记忆，4 个读取点各读一次。漏一个点，那个点就可能串。

**在 main 上跑**：拒绝侧应当红。报告里逐条列出红绿；main 上是绿的，逐条写明为什么它本来就该绿（防巧合的绿）。

### 提交 2：最小原型（让目标检查全绿）

只做到 G1–G4 全绿为止。结构要求只有这几条，怎么实现由你按实际代码定：

- 「这条记忆属于哪个存档（或哪个群）」只定义一处。8 个读写点拿来用，不各自拼「卡 + 存档」。
- 这个范围不能存在记忆视图上（§3-F3：视图是按用户缓存、多个存档共用的）。
- 换视图后范围还在（§3-F4）。
- 不加特判分支来区分单聊和群聊的读写逻辑；两者只是范围不同。

原型不求完整，但不要留下「先这样、以后再整理」的写法。做不到上面某一条，停下报告原因，不要绕过去。

### 这一步不要做

- 不跑变异、不加结构锁（复核方在后面的步骤做）。
- 本地不跑全量，只跑受影响的测试文件。
- 不顺手修 §3-F8 这类已有行为。

## 3. 已查实的事实（main `24df878`；第 0 步逐条复核）

**F1 记忆现在只按卡读写。** `core/memory_manager.py`：

- `:369` `query, filters={"user_id": card_id}, limit=self._search_top_k`
- `:454` `kwargs = {"user_id": card_id}`
- `:476` `results = self._mem.get_all(filters={"user_id": card_id})`
- `:492` `kwargs = {"user_id": card_id, "infer": False}`
- `:549` `ok = self.add_manual(` （反思洞察，在 `reflect` 里）
- `:586` `self._mem.delete_all(user_id=card_id)`

**F2 引擎侧读写点共 8 处，都只传卡 id。**

| 读 / 写 | 位置 | 做什么 |
|---|---|---|
| 写 | `core/chat_engine.py:583` | 每轮对话入库 |
| 写 | `core/evaluation_pipeline.py:159` | 时间事件 |
| 写 | `core/event_service.py:108` | 时间事件「已问过」的标记（空文本，`metadata={"type": "time_event_asked", ...}`） |
| 写 | `core/reflection_service.py:108` | 反思洞察 |
| 读 | `core/context_engine.py:567` | 检索进 prompt（agent 模式的 `search_memory` 工具也走这里，`core/agent/tools.py:152–153`） |
| 读 | `core/chat_engine.py:1557` | 重逢问候取记忆 |
| 读 | `core/event_service.py:40` | 取到期事件 |
| 读 | `core/reflection_service.py:65` | 反思取近期记忆 |

扫描命令（在仓库根目录）：

```
grep -rnE '\.(add_manual|get_all|delete_all|reflect)\(' --include=*.py core web scripts | grep -v '^core/memory_manager.py'
grep -rnE '(mem[a-z_]*|view|_memory)\.(search|add|update|delete)\(' --include=*.py core web scripts | grep -v '^core/memory_manager.py'
grep -rnE 'to_thread\(\s*[A-Za-z_\.]*\.(get_all|add_manual|add|search|update|delete|delete_all|reflect)\b' --include=*.py core web scripts
```

原始输出（2026-10-11，`24df878`）：

```
core/evaluation_pipeline.py:159:            ctx.memory.add_manual(
core/event_service.py:25:    memory : 只读调用 memory.get_all() / memory.add_manual()
core/event_service.py:40:            memories = self._memory.get_all(self._card_id)
core/event_service.py:108:                self._memory.add_manual(
core/chat_engine.py:1557:            all_memories = self._memory.get_all(self._card_id)
core/reflection_service.py:24:    memory : 只读调用 memory.get_all() / 写调用 memory.reflect()
core/reflection_service.py:65:            all_memories = self._memory.get_all(self._card_id)
core/reflection_service.py:108:        self._memory.reflect(self._card_id, llm, recent, card_name,
web/routers/admin.py:318:                memory_manager.delete_all(cid)
web/routers/admin.py:373:                    memory_manager.delete_all(cid)
core/context_engine.py:567:        memories = self.memory.search(query, self.card_id, current_mood=current_mood)
core/chat_engine.py:583:            self._memory.add(
scripts/run_agent_eval.py:144:                    await mem_mgr.add(messages, card_id, metadata={"importance": 10, "eval_seed": True})
scripts/run_agent_eval.py:146:                    mem_mgr.add(messages, card_id, metadata={"importance": 10, "eval_seed": True})
web/routers/memory.py:92:    memories = await asyncio.to_thread(memory_manager.get_all, card_id)
web/routers/memory.py:113:    ok = await asyncio.to_thread(view.add_manual, body.text.strip(), card_id)
web/routers/memory.py:137:    ok = await asyncio.to_thread(view.update, memory_id, body.text.strip())
web/routers/memory.py:157:    ok = await asyncio.to_thread(memory_manager.delete, memory_id)
web/routers/memory.py:176:    ok = await asyncio.to_thread(memory_manager.delete_all, card_id)
```

除上面 8 处外的都不在这一步：记忆面板（`web/routers/memory.py` 5 处）、删用户（`web/routers/admin.py` 2 处）、评测脚本（`scripts/run_agent_eval.py` 2 处）；`event_service.py:25`、`reflection_service.py:24` 是文档字符串。这三条命令按变量名和方法名匹配，换了变量名的调用可能扫不到，你第 0 步发现漏的就报告。

**F3 记忆视图按用户缓存，同一用户的所有存档共用一个视图。** `core/memory_manager.py:301–306`：

```
cache_key = (
    f"{user_id}:{llm.credential_fingerprint()}:"
    f"{key_fingerprint(embedding_key)}:{embedding_region}"
)
with self._lock:
    view = self._views.get(cache_key)
```

**F4 引擎里记忆的引用有四份，运行中会被整体换掉。** `core/chat_engine.py:231–240`：

```
def set_memory(self, memory: Any) -> None:
    ...
    self._memory = memory
    self._ctx_engine.memory = memory
    self._reflection_service._memory = memory
    self._event_service._memory = memory
```

调用方：`web/deps.py:212`（用户换 key 后）。

**F5 引擎知道自己是哪个存档或哪个群。**

- 一对一：`core/chat_engine.py:169` `self._session_id: str = session_id`；新会话的 id 在建引擎之前就生成了（`core/text_manager.py:715–717`）。
- 群聊：`core/chat_engine.py:170` `self._group_id: str = group_id`；群聊建引擎时 `session_id=""`、`group_id=group_id`（`web/routers/group.py:178–179`、`:377–378`）。
- 存档 id 和群 id 都是 `uuid.uuid4().hex[:12]`（`core/text_manager.py:717`、`web/routers/group.py:341`），光看值分不出是存档还是群。

**F6 每轮对话入库是后台线程，并且会调 mem0 的提炼 LLM。** `core/memory_manager.py:452–470`：`_do_add` 里 `self._mem.add(messages, **kwargs)`（没有传 `infer=False`），由 `C.ctx_thread(_do_add, daemon=True).start()` 启动。目标检查要等这条线程写完再读。

**F7 `mem0ai==2.0.20` 的 `run_id` 行为（用真 mem0 加本地 qdrant 加假 embedder 实跑，脚本放在 `docs/specs/artifacts/memory-archive-scope-probe.py`，可以直接重跑）。**

| 操作 | 结果 |
|---|---|
| 写入时带 `run_id`，读取 `filters` 里带同一个 `run_id` | 只读到这个 `run_id` 的 |
| 读取只带 `user_id` | 这张卡全部都读到，含没有 `run_id` 的旧记忆 |
| 读取带 `run_id` | 读不到没有 `run_id` 的旧记忆 |
| `delete_all(user_id=卡, run_id=X)` | 只删 X 的；别的 `run_id`、别的卡不受影响 |
| `delete_all(user_id=卡)` | 这张卡全部删掉 |

原始输出：

```
1 get_all card only      : ['A 存档里说的事', 'B 存档里说的事', '旧记忆：没有存档标记', '群里说的事']
2 get_all card+sessA     : ['A 存档里说的事']
3 get_all card+sessB     : ['B 存档里说的事']
4 search card+sessA      : ['A 存档里说的事']
5 search card+sessB q=A  : ['B 存档里说的事']
6 search card only  q=A  : ['A 存档里说的事', 'B 存档里说的事']
11 after delete sessA    : ['B 存档里说的事', '群里说的事']
11b card2 untouched      : ['别的卡的事']
12 after delete card     : [] | card2: ['别的卡的事']
```

接口形状（mem0 `memory/main.py`）：`add(..., user_id=, run_id=)` 是顶层参数（`:760–766`）；`get_all` 只接受 `filters={...}`，顶层传会被拒（`:1281–1282` `_reject_top_level_entity_params(kwargs, "get_all")`）；`search` 的文档写的是同样的规矩（`:800–801`），拒绝的那一行我没读，按实跑结果是用 `filters` 传。

实跑环境是 `qdrant-client 1.19.1`，仓库钉的是 `1.19.0`（`requirements.txt:306`），差一个补丁版本，请在你的环境里用仓库版本确认一遍。

**F8 已有行为，这一步不修，只别踩。** `get_all` 不传 `top_k` 时最多返回 20 条（mem0 `memory/main.py:1259` `top_k: int = 20`；实跑写入 30 条只回 20 条）。仓库在 `core/memory_manager.py:476` 就是这样调的。目标检查的断言不要依赖「`get_all` 返回全部」。

**F9 可参考的现成搭法。** `tests/test_mem0_llm.py:119–142` 的 `_build_memory` 用真 `Memory.from_config` 加本地 qdrant；它的 `_StubEmbedder`（`:74–81`）返回全零向量，相似度没有区分度。需要能区分的向量时，用确定性的假 embedder（上面那个探测脚本里有一个）。

**F10 测试环境。** 测试库是 `docker-compose.test.yml` 的 PG，端口 55432（`tests/conftest.py:31–33`）。起之前先查端口和容器名有没有被占，被占就另起一个一次性的，跑完删掉，不动别人的容器。

**未核实**（你按实际代码处理，报告里写明结论）

- 4 个写入点在一轮对话里各自的触发条件（比如反思要累计到阈值才触发），目标检查里怎么稳定地触发它们。
- 群聊引擎的评估、事件、反思是否和一对一走完全相同的代码路径。

## 4. 报告里要有的

1. 第 0 步：§3 的 F1–F10 逐条复核结果。
2. main 上目标检查的红绿清单，绿的逐条说明理由。
3. 原型上目标检查全绿的原始输出。
4. 原型改了哪些文件，每个文件一句话说明改了什么。
5. 回答：以后新增一个记忆读写点，要改几处？
6. §3「未核实」两条的结论。
7. 做的过程中发现、但不属于这一步的问题，只列出，不修。
