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

## 补充（复核）

独立复核用 detached worktree（`be1dffc0`），基线 `5df7052c`。结论：**通过**。

### 判定

1. **C2 加 `monkeypatch.chdir(tmp_path)` 不是放宽**。三条断言未动，改动只解决 Windows 跨盘符 `relpath` 不可用。变异「键不做 abspath」仍使 C2 红（`a._client is b._client` 在 `tests/test_chroma_shared_client_goal.py:120` 失败），检测力保留。
2. **abspath 只改 chroma 的进程内标识，不改磁盘目录**。chromadb 1.5.9：`settings.persist_directory = str(path)`（`chromadb/__init__.py:219-228`），identifier 即 `persist_directory`（`api/shared_system_client.py:52-63`），只作 `_identifier_to_system` 的字典键（同文件 15-16、24-26），不落盘。容器 `WORKDIR /app`（`Dockerfile:23`）、`./data:/app/data`（`docker-compose.prod.yml` app volumes）、`mkdir -p /app/data/chroma_db`（`Dockerfile:48`），故 `./data/chroma_db` 与 `/app/data/chroma_db` 同目录。本地实测：在临时 cwd 下用相对写法，sqlite 生成在 `cwd/data/chroma_db/chroma.sqlite3`，且与 abspath 写法返回同一对象。
3. **无需 close**。仓库无 `set_tenant` / `set_database` / `close` 调用（grep）；`RAGEngine` 只调 `get/create/delete_collection`（`core/rag.py:331`、`:338`、`:654`、`:671`、`:704`）。旧实现每个引擎各造一个 Client 且从不 close（refcount 只增），新缓存把对象数从 N 降到 1，严格更省。测试中没有「删临时目录后同路径再用」的路径：C1 用 `mkdtemp`（不删、子进程），C2/C3 与 `test_session_rag_binding` 用 per-test `tmp_path`。缓存按唯一路径只增不淘汰是已知上限；生产与脚本各只有一个路径，不构成缺陷。
4. **C4 覆盖 MCP 与脚本**。扫描目录含 `mcp_server`、`scripts`；MCP 经 `RAGEngine`（`mcp_server/server.py:155`）间接走 `chroma_client`，脚本 `collection_states` 直接调 `chroma_client`（`scripts/rebuild_384_collections.py:169`），同脚本 RAGEngine 调用传同一 `str(chroma_path)`（`:299`、`:356`）。全仓其余直接构造只在 `tests/`（`test_session_rag_binding.py:782` 子进程、`:817` 手工引擎），用绝对临时路径。

### 变异实测（每条只跑目标文件，跑完已复原）

| 变异 | 方向 | 结果 |
|---|---|---|
| 去掉锁 | 放宽 | C1 红（1 failed, 3 passed） |
| 全进程一个客户端 | 过严 | C2 红；C3 连带红（C2 的全局缓存使 C3 的 monkeypatch 未被调用） |
| 失败也写进缓存 | 放宽 | C3 红（1 failed, 3 passed） |
| 键不做 abspath（原样字符串） | 放宽 | C2 红（line 120） |
| RAGEngine 恢复直接 `PersistentClient(` | 结构 | C1/C2/C4 红，C3 绿 |
| 锁只在函数内新建（线程间不共享） | 放宽（自补） | C1 红 |
| 模块级 `from chromadb import PersistentClient` | 结构（自补） | C3 红（`DID NOT RAISE`）+ C4 红；函数内 import 则 C3 绿、仅 C4 红 |
| 锁只包住读取、不包住创建 | 放宽（自补） | C1 红 |

### 目标检查与回归

- `d7d10512` 本地：`4 xfailed in 9.48s`；`--runxfail` 跑 C1 的报错与线上一致：`AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'` + `KeyError: 'D:\\Temp\\chroma-race-…'`，不是导入错误。同提交 CI gate 日志：`3780 passed, 4 xfailed, 36 warnings in 387.93s`（§8 成立）。
- `be1dffc0` 本地：`4 passed in 28.05s`。
- 第 6 节文件：`55 passed in 36.99s`（55432 测试库）。
- CI（PR #194，head `be1dffc0`）：gate pass 8m51s、前端 pass 1m35s、sentinel pass 9m36s；build skipped（push 触发）。

### 实现偏离（非缺陷）

- C2 增加 `monkeypatch.chdir(tmp_path)`：spec 未写，属测试可行性修正；检测力已用变异验证未削弱。

### 非阻塞观察

- C1 的 `subprocess.run(..., text=True)` 在中文 Windows 按 GBK 解码子进程输出，失败时可能触发 `PytestUnhandledThreadExceptionWarning`（`UnicodeDecodeError`）。Linux CI 不受影响且不影响断言；要消掉可加 `encoding="utf-8", errors="replace"`。
- C4 锁的是「四个目录 + `chromadb.PersistentClient(` 这一种调用形态」。当前全仓无其它 chromadb 构造（grep 证据），够用；将来在 `adapters/`、`storage/` 等目录新增构造不会被它发现。
- 缓存键用 `abspath`：不解析符号链接、Windows 下大小写不归一。生产 Linux 单路径，不构成问题。
