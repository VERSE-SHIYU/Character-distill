# 一个进程一个 chroma 客户端（P8）

- 基线：main `5df7052c`。方向：Tracy 10-20 11:16 定「嵌入式、一个进程一个客户端」；不走服务端模式（SZ 共 2GB 内存、已用 1GB 多，官方单机文档写「少于 2GB 不建议部署 Chroma」）。这是偏离官方生产建议的已知取舍。
- 跟踪：「祈灼卡修复 · 当前问题」P8 行。
- 实现 skill：`@tdd`。

## 0. 问题与根因

10-19 重转后两行非致命失败：`Text reindex failed: 'RustBindingsAPI' object has no attribute 'bindings'`、`Scene index failed: './data/chroma_db'`。失败后不自动重试，集合不存在或缺位置时相关会话的检索为空。

根因：chromadb 1.5.9 按持久化路径缓存 System，**创建时不加锁，且在 `start()` 之前就放进缓存**；初始化失败时把 System 弹出并停掉。我们在存卡汇合点背靠背调度两个后台作业，各自在工作线程里 `RAGEngine()` → 各自 `PersistentClient(...)`。进程里还没有客户端时，两个线程同时第一次构造 → 后到的拿到没启动完的 System（`bindings`），它失败时把 System 停掉 → 先到的读缓存 `KeyError: '<路径>'`。

## 1. 目标检查（第一个提交，`tests/test_chroma_shared_client_goal.py`，`xfail(strict=True)`）

| # | 检查 | 测的是目标 |
|---|---|---|
| C1 | 新起一个子进程（chroma 的缓存是类级的，必须新进程），两个线程用 `threading.Barrier` 同时 `RAGEngine(cfg, chroma_path=<新临时目录>)`（`cfg` 带假 key，见 F7），重复 3 个子进程，全部无异常 | 并发首次构造不再失败 |
| C2 | 同一路径（含相对 / 绝对两种写法）两次构造的 `RAGEngine._client` 是同一个对象；不同路径是不同对象 | 一个进程一个路径一个客户端；测试注入临时目录照常可用 |
| C3 | 第一次构造抛异常（monkeypatch `chromadb.PersistentClient` 第一次 raise）→ 不缓存；第二次构造成功 | 失败不被永久缓存 |
| C4 | 结构锁：生产代码（`core` `web` `mcp_server` `scripts`）里 `chromadb.PersistentClient(` 只出现一次，在共享入口函数里 | 以后不会又冒出第二个构造处 |

## 2. 已查实的约束（main `5df7052c` + chromadb 1.5.9 源码，本轮读过）

| # | 事实 | 坐标 |
|---|---|---|
| F1 | chromadb 竞态：`_create_system_if_not_exists` 无锁；先 `cls._identifier_to_system[identifier] = new_system`，后 `new_system.start()`；只有 `_refcount_lock` 管引用计数 | chromadb 1.5.9 `api/shared_system_client.py:14-48` |
| F2 | 客户端初始化失败时释放引用（会把 System 弹出、停掉） | 同版本 `api/client.py:106-114` |
| F3 | 生产里唯一的构造处：`RAGEngine.__init__` → `chromadb.PersistentClient(path=chroma_path or "./data/chroma_db")`；失败时 print 的文字写成了「EphemeralClient」（错） | `core/rag.py:201-205` |
| F4 | 另一处直接构造：一次性运维脚本 `collection_states` | `scripts/rebuild_384_collections.py:171`（同脚本 :301、:358 还用 `RAGEngine` 开同一路径） |
| F5 | `RAGEngine` 的构造方：索引服务每次新造（**有意不缓存引擎**：引擎持有「当前指向哪个集合」的可变状态），文档串声称「chroma 客户端按持久化路径共用一个 System」——共用的正是有竞态的那层 | `core/indexing_service.py:156-168` |
| F6 | 存卡汇合点背靠背调度场景索引与原文补位置；两个作业各自 `asyncio.to_thread` 跑 | `core/text_manager.py:637-655`；`core/indexing_service.py:274-298`、`:377-381` |
| F7 | 构造 `RAGEngine` 需要非空 embedding key（否则在客户端之后报「请先配置 API Key」）；假 key 不联网即可构造 | 本轮沙箱实跑 |
| F8 | 仓库从不改客户端状态：没有 `set_tenant` / `set_database` / 客户端 `close()`；`RAGEngine` 只调 `get/create/delete_collection` | `grep` 结果；`core/rag.py:309`、`:316`、`:632`、`:649`、`:682`；Client 的可变状态只在这三个方法里（chromadb `api/client.py:580-616`） |
| F9 | 测试多数绕过 `__init__`（`object.__new__(RAGEngine)` 后直接设 `_client`），不受影响；真构造的只有 `tests/test_chat.py:27`、`tests/test_rag.py:11` | `tests/test_session_rag_binding.py:120-127` 等 |
| F10 | 同仓库现成写法：mem0 的 Qdrant 客户端一个进程只开一个、首次用到时在锁内创建并共用 | `core/memory_manager.py:203-220` |
| F11 | MCP 服务是独立进程，自己 `RAGEngine(...)`，同样走共享入口即可 | `mcp_server/server.py:155` |

**本轮沙箱实测**（chromadb 1.5.9，`/var/tmp/venv`）：main 上两线程同时构造，3/3 进程都出现与线上一字不差的两条报错（`'RustBindingsAPI' object has no attribute 'bindings'`、`KeyError: '/tmp/…'`）；把构造换成「锁内按路径缓存」后 3/3 正常。

**依据**

| 设计点 | 出处 |
|---|---|
| 一个进程只建一个客户端并共用 | open-webui main 模块级 `VECTOR_DB_CLIENT`；mem0 接受注入的 client；本仓库 F10（P8 调查，10-20） |
| 上游未修 | chroma-core/chroma#7778 给这段加锁，未合并；PyPI 最新 1.5.9（P8 调查，10-20 11:30） |

## 3. 改动

1. `core/rag.py` 新增模块级 `chroma_client(path: str | None = None)`：路径缺省 `./data/chroma_db`，按 `os.path.abspath` 作键，`threading.Lock` 内「没有就建、有就返回」；构造失败直接抛出、不写入缓存。**`chromadb.PersistentClient(` 全仓只在这里。**
2. `RAGEngine.__init__`：`self._client = chroma_client(chroma_path)`；改正 print 文字（打印后 `raise` 的 print 按 project.md 保留）。
3. `scripts/rebuild_384_collections.py:171` 改用 `chroma_client(...)`（同一进程同一路径本来就共享 System，这里只是收口到唯一构造处）。
4. `core/indexing_service.py:156-168` 文档串改为「客户端由 `core.rag.chroma_client` 按路径共用一个」。
5. 引擎仍每次新造（F5 的理由不变）；不改 `IndexingService` / `text_manager` 的调度。

### 结构自检

| 三问 | 现在 | 本 PR 后 |
|---|---|---|
| 同一规则写几处 | 「怎么拿 chroma 客户端」2 处（F3、F4），都直接构造 | 1 处 `chroma_client` |
| 调用方是否被迫知道不该知道的 | 调用方不知道构造有竞态 | 调用方只给路径 |
| 以后加一个用 chroma 的地方 | 再写一次构造，又有竞态 | 调 `chroma_client`；C4 锁守 |

## 4. 不做

- 服务端模式（Tracy 已定不采用，理由见头部）。
- 失败后的自动重试、已坏集合的修复：根因消掉后，下次存卡会照常调度重建。祈灼卡现在两个集合是否可用，见 `claude/handoff/P8-server-check.md` 的线上只读核查（结果未回）；P11 重蒸会存卡、触发重建。

## 5. 对账表（复核方必验的清单，并自补变异）

| 变异 | 方向 | 应红 |
|---|---|---|
| 去掉锁 | 放宽 | C1 |
| 不按路径区分（全进程一个） | 过严 | C2 |
| 失败也写进缓存 | 放宽 | C3 |
| 键不做 `abspath` | 放宽 | C2 |
| `RAGEngine` 恢复直接 `PersistentClient(` | 结构 | C4、C1 |

## 6. 测试

只跑：`tests/test_chroma_shared_client_goal.py tests/test_rag.py tests/test_chat.py tests/test_session_rag_binding.py tests/test_scene_index_dispatch.py tests/test_rag_unusable.py`（库用 55432 的测试库）。全量看分支 CI。不改任何旧断言；要改就停下报告。

## 7. 上线后核对

部署重启后第一次存卡（P11 重蒸正是这个顺序）：`docker compose -f docker-compose.prod.yml logs app` 里该任务没有 `Scene index failed` / `Text reindex failed`，且有 `Scene index … done`。

## 8. 未核实

- C1 在 CI 机器上是否同样稳定复现（沙箱 3/3）：第一个提交推上去后看 CI 里 C1 是否为 XFAIL（不是 XPASS）。
