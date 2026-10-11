# ④ 段 0a：长期记忆按存档隔离

状态：**第 2 步（正式实现）**。第 1 步（目标检查 + 最小原型）已完成并审过，记录在 §10。

- 基线：main `6a10ab7`（2026-10-11 读）。本文件引用的文件与 `24df878` 相比没有变化。
- 分支：从最新 main 开 `feat/memory-archive-scope`，先 `git cherry-pick 953ae71`（带来目标检查、本文件旧版、探测脚本；2026-10-11 试过，无冲突），再用本文件覆盖 `docs/specs/memory-archive-scope.md`。
- 原型分支 `proto/memory-archive-scope`（`45f61c5`）只作参考，不合并，不在它上面接着改。
- 以代码为准：§4 的事实有一条对不上，就停下报告。

## 1. 目标

每个存档的记忆是它自己的。

1. 同一张卡开两个存档，在 A 里告诉角色一件事：回 A，他记得；去 B，他不知道。
2. 群聊里说过的事，这张卡的一对一存档里不知道；反过来也一样。
3. 升级前留下的旧记忆（没有存档标记）不会串进任何存档或群。
4. 记忆面板看到的、加的、清空的，都只是当前这个存档的记忆。

**这个 PR 只做「按存档隔离」这一件事。** 不在这里的：

| 事 | 放在哪 |
|---|---|
| 每个范围最多只读到 20 条（§4-F9） | 本 PR 合并后紧接着的一个小 PR |
| 旧记忆归回各自存档 | 再下一个 PR，另出 spec |
| 删存档、删卡、删群时清记忆 | 0b |
| 本场记录 | 段 1 |

## 2. 结构：三个方案和选择

要解决的问题：存档范围要到达 8 个读写点（§4-F2）。

| 方案 | 做法 | 能否达成目标 | 漏传一处会怎样 | 新增一个读写点要改几处 | 换视图要改几处 |
|---|---|---|---|---|---|
| A 原型的写法 | 范围是带默认值的可选参数，一路传到 8 个点 | 能 | 不报错，悄悄退回整张卡 | 1 处，漏了没人知道 | 4 处（现状） |
| B 参数改成必填 | 同 A，去掉默认值 | 能 | 第一次调用时报错 | 1 处 | 4 处 |
| **C 绑成一个对象** | 「视图 + 卡 + 范围」绑成一个对象，引擎建一次，各处共用；调用点不传卡和范围 | 能 | 不存在「漏传」这件事 | 0 处 | 1 处 |

**选 C。** 依据：

- A 的问题是实跑出来的：第 1 步审查时逐个去掉范围试了 21 种写法，3 种没被发现（§10），都是漏传后悄悄退回整张卡。
- 记忆和卡 id 现在就分别存在四个地方，换视图要四处各赋值一次，代码注释自己写着「漏换一处，那一处就还用旧 key 出站」（`core/chat_engine.py:231–240`）。A 和 B 是在这四份上再加一份范围；C 把四份收成一份。
- C 已在沙箱原型上跑通：第 1 步的目标检查文件一字不改，5 passed；核心改动是 6 个文件各增删 47 行，外加一个约 65 行的新模块。原型只为验证结构，不交付。

### C 的具体形状

新增模块 `core/memory_scope.py`，只放两样东西：

**`MemoryScope`：这条记忆属于谁。** 不可变的值对象。

| 构造 | 含义 | 对应 mem0 |
|---|---|---|
| `MemoryScope.archive(card_id, session_id)` | 一对一存档 | `user_id=card_id`，`run_id="session:<session_id>"` |
| `MemoryScope.group(card_id, group_id)` | 群聊 | `user_id=card_id`，`run_id="group:<group_id>"` |
| `MemoryScope.whole_card(card_id)` | 整张卡，只给删除用 | 只有 `user_id=card_id` |

- 范围的值带类型前缀，因为存档 id 和群 id 是同一种格式（§4-F5），不带前缀分不出来。
- 把范围变成 mem0 的过滤条件，只在这个类里做一处。

**`ScopedMemory`：这个引擎的记忆。** 把「记忆视图 + `MemoryScope`」绑在一起。

- 对外的方法不带卡 id、不带范围：`search(query, current_mood=None)`、`add(messages, metadata=None)`、`get_all()`、`add_manual(text, metadata=None)`、`reflect(llm, recent, char_name, storage=None)`。
- `enabled`：视图存在且可用、并且有范围时为真。现在 7 处手写的 `self._memory and self._memory.enabled and self._card_id` 判断（守着 8 个调用点）收成这一处。
- `rebind(view)`：换视图。`ChatEngine.set_memory` 只调它一次。

**各处怎么用**

| 谁 | 现在 | 改成 |
|---|---|---|
| `MemoryView` 的 `search` / `add` / `get_all` / `add_manual` / `reflect` / `delete_all` | 收 `card_id: str` | 收 `MemoryScope`，必填，没有默认值 |
| `MemoryManager` 的 `get_all` / `delete_all`（`core/memory_manager.py:265–275`） | 收 `card_id` | 收 `MemoryScope` |
| `ChatEngine` | 存视图和卡 id，分给三个服务 | 建一个 `ScopedMemory`，分给检索、事件、反思和评估上下文。有群 id 用群范围，否则有存档 id 用存档范围 |
| `ContextEngine`、`EventService`、`ReflectionService`、`EvalContext` | 各存一份视图和卡 id | 只存 `ScopedMemory`，记忆调用不再传卡 id |
| 引擎既没有存档 id 也没有群 id | 按整张卡读写 | 这个引擎没有记忆（`enabled` 为假），不读不写，打一行 warning |
| 记忆面板 | 按卡 | 按当前存档（§6） |
| 删用户（`web/routers/admin.py:318`、`:373`） | `delete_all(cid)` | `delete_all(MemoryScope.whole_card(cid))`，行为不变 |

- `EvalContext.card_id` 保留：群好感落库还用它（`core/evaluation_pipeline.py:193`）。
- `ContextEngine` 的 `card_id` 改完后可能没有读者了：2026-10-11 在 main 上 `grep -n 'self\.card_id' core/context_engine.py` 只有 `:233`、`:565`、`:567` 三处，后两处都是记忆调用。没有读者就一并去掉；有别的读者就留着并在报告里写明。

## 3. 这条路上已有的机制，隔离之后各自怎样

| 机制 | 现在 | 隔离之后 | 依据 |
|---|---|---|---|
| mem0 提炼新记忆时，先找已有的相近记忆来合并 | 在整张卡里找 | 只在同一个存档里找 | mem0 `memory/main.py:923–931`：查已有记忆用的过滤条件取自 `user_id` / `run_id` |
| mem0 提炼时带上最近 10 条消息作上下文 | 按整张卡取 | 按存档取 | mem0 `memory/main.py:919–920` |
| 反思触发：累计重要性 ≥ 30、距上次 ≥ 8 轮、高质量素材 ≥ 3 条 | 素材从整张卡里数 | 素材只从本存档里数，所以新存档要聊得更久才触发第一次反思 | 阈值 `core/memory_manager.py:26–28`；取素材 `core/reflection_service.py:65` |
| 反思的累计器 | 每个引擎一份，只在内存 | 不变 | `core/reflection_service.py:36–37` |
| 到期事件和「已问过」标记 | 从整张卡里找 | 只从本存档里找：A 里说的「明天面试」不会在 B 里被问起 | `core/event_service.py:40–60`、`:108` |
| 重逢问候取最重要的 2 条 | 从整张卡里取 | 只从本存档里取 | `core/chat_engine.py:1557–1569` |
| 检索取前 10 条、记忆块的 token 预算 | — | 不变 | `core/memory_manager.py:197`；`core/context_engine.py:245` |
| 记忆视图按用户缓存 | — | 不变，范围不放在视图上 | `core/memory_manager.py:301–306` |
| 列出记忆最多返回 20 条 | 每张卡 20 条 | 变成每个存档 20 条。问题还在，下一个小 PR 修 | §4-F9 |

规模（线上，2026-10-11 对备份只读统计）：新加坡节点 1 张卡、21 条记忆，都没有存档标记。

## 4. 已查实的事实（main `6a10ab7`；第 0 步逐条复核）

**F1 记忆现在只按卡读写。** `core/memory_manager.py`：`:369` `query, filters={"user_id": card_id}, limit=self._search_top_k`；`:454` `kwargs = {"user_id": card_id}`；`:476` `results = self._mem.get_all(filters={"user_id": card_id})`；`:492` `kwargs = {"user_id": card_id, "infer": False}`；`:549` 反思洞察经 `self.add_manual(` 写回；`:586` `self._mem.delete_all(user_id=card_id)`。

**F2 引擎侧读写点 8 处。**

| 代号 | 读 / 写 | 位置 | 做什么 |
|---|---|---|---|
| W1 | 写 | `core/chat_engine.py:583` | 每轮对话入库 |
| W2 | 写 | `core/evaluation_pipeline.py:159` | 时间事件 |
| W3 | 写 | `core/event_service.py:108` | 时间事件「已问过」的标记 |
| W4 | 写 | `core/reflection_service.py:108` | 反思洞察 |
| R1 | 读 | `core/context_engine.py:567` | 检索进 prompt（agent 的 `search_memory` 也走这里，`core/agent/tools.py:152–153`） |
| R2 | 读 | `core/chat_engine.py:1557` | 重逢问候取记忆 |
| R3 | 读 | `core/event_service.py:40` | 取到期事件 |
| R4 | 读 | `core/reflection_service.py:65` | 反思取近期记忆 |

扫描命令和原始输出见 §10。

**F3 记忆视图按用户缓存，同一用户的所有存档共用。** `core/memory_manager.py:301–306`、`:327`。

**F4 记忆的引用和卡 id 分别存在四处；手写判断 7 处。**

- 持有处：`core/chat_engine.py:165–166`、`:180–181`、`:210–211`；`core/context_engine.py:232–233`；`core/event_service.py:30–31`；`core/reflection_service.py:34–35`。
- 换视图时四处各赋值一次：`core/chat_engine.py:231–240`，调用方 `web/deps.py:212`。
- 手写判断：`core/chat_engine.py:582`、`:1554`；`core/context_engine.py:565`；`core/evaluation_pipeline.py:152`；`core/event_service.py:37`、`:106`；`core/reflection_service.py:56`。

**F5 引擎知道自己是哪个存档或哪个群；两种 id 同一格式。** `core/chat_engine.py:169–170`；群聊建引擎 `web/routers/group.py:178–179`、`:377–378`（`session_id=""`、`group_id=group_id`）；两种 id 都是 `uuid.uuid4().hex[:12]`（`core/text_manager.py:717`、`web/routers/group.py:341`）。

**F6 线上建引擎只有三处，都有存档 id 或群 id。** `core/text_manager.py:718`、`web/routers/group.py:173`、`:372`。`scripts/` 下另有 4 处（`run_agent_eval.py:169`、`:331`、`:372`，`diag_tool_use.py:62`）。

**F7 每轮对话入库是后台线程。** `core/memory_manager.py:452–470`。

**F8 mem0 `2.0.20` 的行为（实跑，脚本 `docs/specs/artifacts/memory-archive-scope-probe.py`；第 1 步本地已用仓库钉的 `qdrant-client 1.19.0` 重跑一致）。**

- 带 `run_id` 读写互相隔离；带 `run_id` 读不到没有 `run_id` 的旧记忆。
- `delete_all(user_id=卡, run_id=X)` 只删 X 的；`delete_all(user_id=卡)` 删这张卡全部。
- `run_id` 只要求非空、不含空白（mem0 `memory/main.py:175–209`）；带冒号的值可用（探测脚本里的 `group:g1`、沙箱原型里的 `session:…` 都实跑过）。

**F9 列出记忆最多返回 20 条（本 PR 不修，只别踩）。** `core/memory_manager.py:476` 调 `get_all` 没有传数量；mem0 `memory/main.py:1259` 是 `top_k: int = 20`。实跑：写入 30 条只返回 20 条，固定是 id 最小的 20 条。目标检查的断言不要依赖「一个范围里超过 20 条还能读全」。

**F10 记忆面板。**

- 接口 `web/routers/memory.py`：列表 `:73–93`、添加 `:96–116`、清空 `:163–179` 都只按卡；修改 `:119–140`、删除 `:143–160` 按记忆 id。归属校验只查卡的属主（`_check_owner`，`:66–70`）。
- 前端只有一对一聊天页有面板：`web/frontend/src/components/ChatArea.jsx:442`（列表）、`:866`（清空）、`:899`（添加）、`:943`（修改）、`:966`（删除）。组件里有当前存档 id（`:139`）；没有存档 id 时外层组件只渲染占位、不渲染聊天区（`:62–78`）。群聊是另一个组件 `GroupChatPage.jsx`，里面没有记忆接口的调用。
- 查存档归属的现成方法：`storage.get_session_owned(id, user_id)`，返回里有 `card_id`（`storage/postgres_store.py:1428–1440`、`storage/sqlite_store.py:1958–1970`）。

**F11 测试环境。** 测试库是 `docker-compose.test.yml` 的 PG，端口 55432（`tests/conftest.py:31–33`）。起之前先查端口和容器名，被占就另起一个一次性的，跑完删掉。

**未核实**（按实际代码处理，报告里写明结论）

- 哪些现有测试要跟着改。扫描命令和 2026-10-11 的输出见 §10，共 20 个文件，但没有逐个打开看。
- 面板的「添加」要用户自己的两把 key 才有视图（`web/routers/memory.py:51–63`），目标检查里怎么安排这两把 key 没有核实。
- `scripts/run_agent_eval.py:136–146` 播种记忆的写法改成新接口后，脚本原来的用意还成不成立。

## 5. 目标检查

文件 `tests/test_memory_archive_scope_goal.py`（第 1 步已有 G1–G4）。保持「真引擎 + 真 mem0 + 本地 qdrant」，替身只用在聊天模型、评估模型、embedding、mem0 提炼 LLM。

| # | 检查 | 通过侧 | 拒绝侧 |
|---|---|---|---|
| G1 | 同卡两个存档 A、B（已有） | A 里产生的记忆 A 读得到 | B 读不到 |
| G1a | 「已问过」标记跟存档走（新） | A 里问过的事，在 A 上新建一个引擎后不再被当成没问过 | — |
| G1b | 反思洞察跟存档走（新） | A 里写下的洞察 A 读得到 | B 读不到 |
| G2a、G2b | 群聊与一对一（已有） | 各自读得到自己的 | 互相读不到 |
| G2c | 存档 id 和群 id 的值恰好相同（新） | — | 仍然互相读不到 |
| G3 | 没有存档标记的旧记忆（已有） | — | 存档和群都读不到 |
| G4 | 换记忆视图之后范围还在（已有） | 仍读得到本存档的 | 仍读不到别的存档的 |
| G4a | 换视图是真的换了（新） | 换完之后的读写走的是新视图，不再碰旧视图 | — |
| G5 | 记忆面板（新） | 在 A 添加的，A 的列表里有；清空 A 后 A 为空 | B 的列表里没有；清空 A 后 B 不变；别人的存档、存档与卡不匹配，都是 404 |
| G6 | 没有存档 id 也没有群 id 的引擎（新） | — | 不读也不写，不会退回整张卡 |
| G7 | 整卡范围只在登记的地方用（新，唯一一把结构锁） | 登记处（本 PR 只有 `web/routers/admin.py`）可以用 | `core/` 里除 `memory_scope.py`、`memory_manager.py` 外任何地方出现就红 |

**8 个读写点各由哪条检查守着**（复核方按这张表逐格验，每格都要能被打红）

| 读写点 | 写错了会怎样 | 守它的检查 |
|---|---|---|
| W1 每轮入库 | 落到整张卡：本存档读不到 | G1 通过侧（检索、重逢） |
| W2 时间事件 | 同上 | G1 通过侧（到期事件） |
| W3 「已问过」标记 | 重新进入后同一件事再问一次 | G1a |
| W4 反思洞察 | 洞察哪个存档都读不到 | G1b |
| R1 检索 | 读到别的存档的 | G1、G2 拒绝侧 |
| R2 重逢问候 | 同上 | G1、G2 拒绝侧 |
| R3 到期事件 | 同上 | G1、G2 拒绝侧 |
| R4 反思取素材 | 同上 | G1、G2 拒绝侧 |

说明：

- G1a、G1b、G2c、G4a 四条都对应实跑时没被抓到的写法（§10）。
- G5 走真实的接口，不直接调视图。仓库里面板归属的现有用例在 `tests/test_ownership_404.py:417–441`，它用的是假的记忆管理器，只能参考怎么搭接口和属主，不能照搬。
- 前端：给 `ChatArea` 的现有组件测试加断言，列表、添加、清空三个请求的地址里带着当前存档 id。
- 第 1 个提交只带「本文件 + 新增的目标检查」，先在未改实现的代码上跑，报告红绿；之后每个提交都跑。

## 6. 改动

| 位置 | 改什么 |
|---|---|
| `core/memory_scope.py`（新） | `MemoryScope`、`ScopedMemory`，见 §2 |
| `core/memory_manager.py` | `MemoryView` 和 `MemoryManager` 的读写方法改收 `MemoryScope` |
| `core/chat_engine.py` | 建一个 `ScopedMemory` 分给各处；`set_memory` 只调 `rebind`；两个调用点去掉卡 id 和手写判断 |
| `core/context_engine.py`、`core/event_service.py`、`core/reflection_service.py`、`core/evaluation_pipeline.py` | 只持有 `ScopedMemory`；6 个调用点去掉卡 id 和手写判断 |
| `web/routers/memory.py` | 列表、添加、清空三个接口加必填的 `session_id` 查询参数。「存档属于当前用户、且它的 `card_id` 等于路径里的卡，否则 404，然后得到 `MemoryScope.archive`」这一段只写一个函数，三个接口共用。修改、删除两个接口不动 |
| `web/routers/admin.py:318`、`:373` | 改用 `MemoryScope.whole_card`，行为不变 |
| `web/frontend/src/components/ChatArea.jsx` | 列表、清空、添加三处请求带上 `session_id`；`loadMemories` 的依赖加上 `sessionId` |
| `scripts/` 下用到记忆接口的脚本 | 跟着新接口改，保持脚本原来的用意 |
| 受影响的测试和测试替身 | 跟着新接口改，测试的意图不变；改了哪些、各改了什么，报告里逐个列出 |

规模估计：核心 7 个文件、接口和前端 3 个、脚本 2–3 个，测试最多 20 个左右。测试那部分是没逐个核实的估计（§4「未核实」）；实际明显超出，或者发现要改测试的意图，停下报告。

**不做**

- 不给范围留默认值，不保留「不传就是整张卡」的口子。
- 不在读取处给旧记忆开后门（比如「没有存档标记的也读进来」）。
- 不修 F9，不改面板的修改、删除接口，不改 mem0 版本，不动蒸馏。

## 7. 提交顺序

1. 本文件 + 新增的目标检查（在未改实现的代码上跑，报红绿）。
2. `core/memory_scope.py` 和引擎侧的结构（G1–G4a、G6、G7 绿）。
3. 记忆面板接口和前端（G5 绿）。
4. 脚本和剩余的测试适配。

一个提交只做一件事；每个提交后都跑目标检查。

## 8. 测试

改了接口或属性名的 PR，本地跑全量。下游改动（本次是记忆接口 + 引擎属性 `_memory`→`_scoped`、`ContextEngine.card_id` 删除）要跑全量测试加 `npm test`，库用 Docker 起的 PG；合并门是分支 CI；不跑变异（复核方做，并且自己补，放宽和过严两个方向都要有）；不调付费模型。

## 9. 上线

本 PR 合并后、「旧记忆归回存档」那个 PR 合并前，**不要部署**：这段时间里线上的旧记忆谁都读不到（G3 的行为）。

## 10. 第 1 步的记录（2026-10-11）

分支 `proto/memory-archive-scope`：`953ae71` 目标检查，`45f61c5` 原型。

**本地报告**：事实逐条复核通过；main 上 5 条目标检查全红，都红在拒绝侧；原型上全绿。

**复核（Claude 在沙箱实跑；Python 3.13、`qdrant-client 1.19.1`、`--noconftest` 只跑目标检查这一个文件）**

- `953ae71` 上 5 failed；`45f61c5` 上 5 passed。
- 在原型上逐个去掉存档范围共 21 种写法，18 种被抓到，3 种没抓到：

| 没抓到的写法 | 后果 | 补的检查 |
|---|---|---|
| `core/event_service.py` 写「已问过」标记时不带范围 | 重新进入后同一件事会再问一次 | G1a |
| `core/reflection_service.py` 调 `reflect` 时不带范围 | 洞察哪个存档都读不到 | G1b |
| `core/memory_manager.py` 的 `reflect` 里写洞察时不带范围 | 同上 | G1b |

- 在方案 C 的沙箱原型上又试了 10 种写错的方式，7 种被抓到，3 种没抓到：

| 没抓到的写法 | 补的检查 |
|---|---|
| 存档和群用了同一个前缀 | G2c |
| `rebind` 什么都不做（换视图没换成） | G4a |
| 反思洞察按整张卡写 | G1b |

**扫描命令和原始输出**（在仓库根目录，`24df878`；这些文件到 `6a10ab7` 没有变化）

```
grep -rnE '\.(add_manual|get_all|delete_all|reflect)\(' --include=*.py core web scripts | grep -v '^core/memory_manager.py'
grep -rnE '(mem[a-z_]*|view|_memory)\.(search|add|update|delete)\(' --include=*.py core web scripts | grep -v '^core/memory_manager.py'
grep -rnE 'to_thread\(\s*[A-Za-z_\.]*\.(get_all|add_manual|add|search|update|delete|delete_all|reflect)\b' --include=*.py core web scripts
```

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

这三条命令按变量名和方法名匹配，换了变量名的调用可能扫不到；第 0 步发现漏的就报告。

受影响测试的扫描（`6a10ab7`，只列了文件，没有逐个打开）：

```
grep -rlE 'EventService\(|ReflectionService\(|EvalContext\(|memory_manager=|set_memory\(|\.get_all\(|add_manual\(|MemoryView\(' tests | sort
```

```
tests/evidence_fakes.py
tests/test_agent_evidence.py
tests/test_alias_cache_failure.py
tests/test_arc_phase_fields_readers.py
tests/test_arc_phase_select.py
tests/test_arc_positions.py
tests/test_catchwords.py
tests/test_dialogue_fallback_goal.py
tests/test_evaluation_pipeline.py
tests/test_examples_pending_goal.py
tests/test_identify_failure_channels.py
tests/test_length_budget.py
tests/test_memory_manager.py
tests/test_ownership_404.py
tests/test_reflection_service.py
tests/test_secret_never_plaintext.py
tests/test_session_identity_injection.py
tests/test_session_rag_binding.py
tests/test_text_failure_messages.py
tests/test_user_own_keys.py
```

## 11. 报告里要有的

1. 第 0 步：§4 的 F1–F11 逐条复核结果，「未核实」三条的结论。
2. 第 1 个提交在未改实现的代码上的红绿清单；绿的逐条说明为什么本来就该绿。
3. 最终分支上目标检查全绿的原始输出，受影响测试的结果，`npm test` 的结果。
4. 改了哪些文件，每个文件一句话；测试和替身改了什么。
5. 回答两个问题：以后新增一个记忆读写点要改几处；以后换记忆视图要改几处。
6. 和本文件不一致的地方（做不到、或者发现本文件写错了），逐条写明，不要悄悄绕过去。
7. 过程中发现、但不属于这个 PR 的问题，只列出，不修。
