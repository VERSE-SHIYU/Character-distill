# 模型调用一律用用户自己的 key（主对话 + RAG + 长期记忆）

> 状态：**草案 v1（设计评审用，未发执行）**。第 7 节的变异要在实现之后实跑，实跑有存活的变异就不发。
> 日期：2026-09-30　分支：`claude/mem0-disabled-nodes-mu8qhi`　基线：`cae5510`

## 1. 目的与验收

| # | 目的 | 可核对的判据 |
|---|---|---|
| G1 | 长期记忆真正工作起来（现在两台生产的 `add called` 都是 0） | 配好两把 key 的账号聊几轮后，日志出现 `add OK`，记忆面板能看到条目，新开会话后角色能引用之前的内容 |
| G2 | 所有面向用户的模型调用（主对话 LLM、RAG embedding、Mem0 提炼 LLM + embedding）都用该用户自己的 key，服务器不出钱 | 全局 key 可用、用户没配时：对话接口 503，出站调用数为 0；两个用户交替使用，key 不串用 |
| G3 | 没配 key 的用户明确知道原因 | 对话：503「请先在设置页配置 API Key」；记忆接口：`memory_unconfigured` 加中文提示；已有记忆仍可查看、删除 |
| G4 | 用户改 key 后立即生效 | 保存设置后，同一个活会话的下一轮就改用新 key（LLM、RAG、记忆三样都换） |
| G5 | 出问题时看得见 | 禁用或跳过走 logging；`PYTHONUNBUFFERED=1` |

## 2. 已定口径（用户裁定，2026-09-30）

1. 主对话、RAG、Mem0 都**只用用户自己的 key**，没有全局回落；没配就不提供对应服务。
2. Mem0 必须 LLM key 和 embedding key **两把都配**，缺一把就不提供记忆，对话照常。
3. **发布审核保留平台 key**（`web/routers/market.py:484` 的 `get_llm()`）。审核是平台职责，不是提供给用户的服务。
4. 版本用锁里的稳定版：mem0ai 2.0.20、qdrant-client 1.19.0，**不升级**。
5. 方案必须是成熟的、贴合本项目的、对照现有代码核实过的。
6. 不在范围：`mcp_server/`、`scripts/`（运维工具，没有用户身份，继续读 env）。

## 3. 方案选型（规则 1）

参照物是仓库里已有的「按用户 key 做 embedding」做法，即 RAG：
- 按「资源 + key 指纹」缓存，每个用户一个实例（`core/indexing_service.py::_get_or_build_rag`，`cache_key = f"{text_id}:{key_fingerprint(embedding_key)}"`）；
- 会话构建路由先 `resolve_embedding(user_cfg)`，再把结果传进去。

Mem0 照这个做。唯一的差别在向量库层：chroma 允许多个客户端同开一个目录，本地 qdrant 不允许（见 §4.3 实测），因此用 mem0 **官方公开参数**共享同一个客户端。

| 方案 | 版本 / 许可证 | 结论 |
|---|---|---|
| **A. mem0 官方 `vector_store.config.client` 共享 `QdrantClient`，每个用户一个 `Memory.from_config` 实例** | mem0ai 2.0.20（Apache-2.0，METADATA `License-Expression`）；qdrant-client 1.19.0（Apache-2.0）；都已在锁里 | **采用** |
| B. Qdrant 服务端容器（qdrant/qdrant，Apache-2.0） | 原生支持多客户端 | 两个区各加一个容器，改 deploy，还要迁移数据；A 实测可行，所以不用 |
| C. mem0 托管平台（MemoryClient） | SaaS | 数据出境，与「用户自付」模式不对应，否决 |
| ~~D. 浅拷贝 Memory、替换内部属性~~ | — | 自造方案，依赖 mem0 内部结构，**作废** |

注入方式照旧：`Mem0BridgeEmbedder`（`core/embeddings.py`）与 `AdapterLLM`（`core/mem0_llm.py`）都是现有代码，生产已在使用，只是改成传入用户自己的 key 和 LLM。

## 4. 已查实事实（原始输出）

### 4.1 Mem0 被禁用的根因
`core/memory_manager.py::MemoryManager.__init__`：`:169` `os.getenv("DEEPSEEK_API_KEY")`、`:175` `os.getenv("DASHSCOPE_API_KEY")`，缺任一个就 `_enabled=False`。`docker-compose.prod.yml:116` 有 `env_file: .env`，下发通道是有的，只是两台服务器的 `.env` 里没有这两个键。`DEPLOY.md:125` 明文写着「用户可在 Web 设置页自行填写，无需服务端统一配置」，文档口径和代码行为自相矛盾。

### 4.2 mem0 源码（mem0ai 2.0.20，逐项核对）
- `mem0/configs/vector_stores/qdrant.py:13`：`client: Optional[QdrantClient] = Field(None, description="Existing Qdrant client instance")`
- `mem0/vector_stores/qdrant.py:60-62`：`if client: self.client = client; self.is_local = False`
  - `is_local=False` 的两处影响：`:169` 会对本地库尝试建 payload 索引（实测只打一条 `UserWarning: Payload indexes have no effect in the local Qdrant`，无功能影响）；`:599` 的 `reset()` 会跳过一段 Windows/NFS 清理（生产是 Linux，且仓库不调 `reset`）。
- 所有模型调用都走实例属性 `self.embedding_model` / `self.llm`（`mem0/memory/main.py`：`:902`、`:925`、`:956`、`:994`、`:1638`、`:1863`、`:1966`、`:2014`、`:2071` 等）。search 内部会起 `ThreadPoolExecutor`（`:1778`），所以**不能用 ContextVar 传凭据**。
- 历史库：`history_db_path` 默认是 `os.path.join(mem0_dir, "history.db")`，`mem0_dir = MEM0_DIR or ~/.mem0`（`mem0/configs/base.py:13, :42-44`）。提炼时会读 `self.db.get_last_messages(session_scope, limit=10)`（`main.py:920`）。
- 哪些操作需要 key：`add` 要 LLM + embedding；`search` / `add_manual`（`infer=False`）/ `update` 要 embedding；`reflect` 要 LLM + embedding；`get_all` / `delete` / `delete_all` 两把都不要。

### 4.3 实测（沙箱，qdrant-client 1.19.0 / mem0ai 2.0.20）
```
# 同一目录开第二个本地客户端
second client: RuntimeError Storage folder db_probe is already accessed by another instance of Qdrant client. If you require concurrent access, use Qdrant server instead.

# 两个 mem0 Qdrant 向量库对象传入同一个 client
b sees a's row: ([Record(id='00000000-0000-0000-0000-000000000001', payload={'user_id': 'cardA'}, ...)], None)

# 4 个 SQLiteManager 实例指向同一个文件，8 个线程各写 200 行
expected 1600 rows 1600 errors 0
```

### 4.4 主对话 LLM：唯一出口与 15 个调用点
回落只发生在一处：`web/deps.py::_resolve_user_llm`，`resolve_llm(config, build_user=_make_user_llm, get_global=get_llm)`。

扫描命令：`git grep -n "get_user_llm(" -- '*.py' ':!tests' | grep -v "def get_user_llm"`，共 15 处，逐一核对「LLM 为 None 时的可观测输出」：

| 调用点 | 函数 | None 时（现状） | 本次动作 |
|---|---|---|---|
| chat.py:158 | `_ensure_session` | 503「请先在设置页配置 API Key」 | 不动 |
| chat.py:671 | `send_message` | 503 | 不动 |
| distill.py:650 | `identify_by_text_id` | 503 | 不动 |
| distill.py:673 | `distill_by_text_id` | 503 | 不动 |
| distill.py:762 | `_distill_start_impl` | 503 | 不动 |
| distill.py:1035 | `distill_stream` | 503 | 不动 |
| distill.py:1200 | `reindex_rag` | 503 | 不动 |
| distill.py:1343 | `start_session` | 503 | 不动 |
| distill.py:1494 | `legacy_identify` | 503 | 不动 |
| distill.py:1510 | `legacy_distill` | 503 | 不动 |
| group.py:89 | `_rebuild_group_session` | 返回 None，调用方报 **404「群聊会话已过期，请重新创建」**（group.py:541-542） | **改为 503**：用户没配 key 会变成常见情况，404 文案会误导 |
| group.py:291 | `create_group` | 503 | 不动 |
| history.py:183 | `resume_session` | 503 | 不动 |
| market.py:906 | `at_reply` | **没有判 None**，`llm.chat` 抛 `AttributeError` → 500 | **补 503** |
| text.py:137 | `upload_text` | 503 | 不动 |

另有两处全局 `get_llm()`：`market.py:484`（发布审核，口径 3 保留）、`server.py:609`（管理员的 `llm_available`，保留）。

### 4.5 RAG
扫描命令：`git grep -n "resolve_embedding(" -- '*.py' ':!tests'` 和 `git grep -n "env_key=" -- '*.py' ':!tests'`。web 侧 9 处都**没传** `env_key`（默认 `""`），所以本来就不回落；传 env 的只有 `mcp_server/server.py:90`、`scripts/rebuild_384_collections.py:278`（不在范围）。

现有问题：解析结果为不可用时仍拿空 key 建 `RAGEngine`，失败后在 `core/indexing_service.py::get_rag_for_session` 被降级成 `None`，只留一条 warning。`web/routers/chat.py:180` 的注释写「会静默用全局 key」，与事实不符。

### 4.6 引擎侧已有「记忆为 None 即跳过」的分支（引擎不改）
`core/context_engine.py:507`、`core/chat_engine.py:535` / `:1525`、`core/reflection_service.py:56`、`core/event_service.py:37` / `:106`。

### 4.7 改 key 的现有钩子
`web/deps.py::refresh_user_llm`：保存设置时由 `update_api_config` 调用，遍历一对一与群聊的活引擎，只执行 `engine.set_llm(llm)`；docstring 写明「不重建 RAG」。

## 5. 改动清单

| # | 文件 | 改动 |
|---|---|---|
| C1 | `web/deps.py::_resolve_user_llm` | 去掉 `get_global=get_llm`，只接受用户自己的 LLM（沿用 `resolve_llm`，全局那一侧恒返回 None） |
| C2 | `web/routers/group.py::_rebuild_group_session` 及调用方 | 没有 LLM 时报 503，不再落到 404 |
| C3 | `web/routers/market.py::at_reply` | LLM 为 None 时报 503 |
| C4 | 会话构建处（chat / history / group / distill / TextManager） | embedding 不可用时直接 `rag=None`，不再用空 key 试错；修正 `chat.py:180` 的注释 |
| C5 | `core/memory_manager.py` | 改成工厂：构造时不需要 key，只建一个共享的 `QdrantClient`（`data/mem0_db`）；`for_user(user_id, llm, embedding_key, embedding_region)` 用官方 `Memory.from_config` 传入 `vector_store.config.client` 建出用户视图，缓存键为「用户 + LLM key 指纹 + embedding key 指纹」（与 RAG 同一写法）；视图对外的方法与现在一致，方法体搬过去不重写；缺 key 时返回 None 并记 WARNING |
| C6 | 同上 | 显式配置 `history_db_path = data/mem0_db/history.db`（官方配置项）：默认路径在容器的 `~/.mem0`，不在数据卷里，每次部署都会被清空 |
| C7 | 会话构建处 | 与 C4 同一处取记忆视图，缺 key 时传 `memory_manager=None`（复用 §4.6 的现有分支） |
| C8 | `web/deps.py::refresh_user_llm` | 同时换 RAG 与记忆视图；在 `ChatEngine` / `ContextEngine` 上照 `set_llm` 的形态加对应的 setter |
| C9 | `web/routers/memory.py` | `get_all` / `delete` / `clear` 用不需要 key 的底层实例；检索 / 手动添加 / 编辑在未配置时返回 `memory_unconfigured` |
| C10 | `Dockerfile` | `ENV PYTHONUNBUFFERED=1` |
| C11 | `DEPLOY.md` / `.env.example` | 删掉「DEEPSEEK_API_KEY 用于记忆」的暗示，写明三类服务都用用户自己的 key，服务器的 DEEPSEEK key 只用于发布审核 |

## 6. 测试

### 6.1 新增用例（先写失败用例）
| # | 前提 | 断言 | 锁住 |
|---|---|---|---|
| T1 | 全局 LLM 可用、用户没配 | `/api/chat/send` 等返回 503，全局实例的出站调用数为 0 | C1 |
| T2 | 同上 | `at_reply` 返回 503（不是 500） | C3 |
| T3 | 同上，群聊会话已被逐出内存 | 发送返回 503（不是 404） | C2 |
| T4 | 用户只有 LLM key | 会话 `rag is None`，DashScope 出站调用数为 0 | C4 |
| T5 | 两把 key 齐全 | `for_user` 返回视图，embedder 的 key 就是该用户的 | C5 |
| T6 | 用户 A、B 交替 add | 各自的调用用各自的 key | C5 缓存键 |
| T7 | 缺任一把 | 会话 `memory_manager is None`，零模型调用，恰好一条 WARNING | C5/C7 |
| T8 | 未配置 | `memory/clear` 成功；`memory/add` 返回 `memory_unconfigured` | C9 |
| T9 | 活会话中保存新 key | 下一轮 LLM、RAG、记忆都用新 key | C8 |
| T10 | 历史库路径 | `Memory` 的 `history_db_path` 位于 `data/` 下 | C6 |

### 6.2 要改写的既有用例（C4 行为反转）
扫描命令：`git grep -ln "resolve_llm\|get_llm" -- tests | xargs grep -ln "get_global\|_install_user_llm\|deps.get_llm\|\"get_llm\""`，共 9 个文件：
`test_auto_review.py`、`test_default_model.py`、`test_group_save_failure.py`、`test_live_llm_refresh.py`、`test_llm_access_gate.py`、`test_message_backfill.py`、`test_ownership_404.py`、`test_session_identity_injection.py`、`test_settings_config_llm_available.py`。

明确要翻转的：`test_llm_access_gate.py::test_l1_start_falls_back_to_global_instance`、`::test_l2_background_thread_gets_the_resolved_instance`（前提「无 key + 全局可用」）。`resolve_llm` 纯函数契约（`:557-567`）不变。其余 7 个文件实现时逐个判定：是否依赖回落；依赖的话，改成给用户配 key，或者改断言 503。

## 7. 变异清单（实现后实跑，结果回填；有存活就不发）
| # | 变异 | 应该红的用例 |
|---|---|---|
| M1 | C1 恢复 `get_global=get_llm` | T1 |
| M2 | 删掉 `at_reply` 的 None 判断 | T2 |
| M3 | 群聊重建缺 key 时退回 404 | T3 |
| M4 | C4 恢复用空 key 建 RAG | T4 |
| M5 | 缓存键去掉用户 key 指纹 | T6 |
| M6 | 缺一把 key 时仍返回视图 | T7 |
| M7 | `clear` 改走需要 key 的视图 | T8 |
| M8 | `refresh_user_llm` 只换 LLM | T9 |
| M9 | 去掉 `history_db_path` 配置 | T10 |

## 8. 已查实约束（规则 8）
- 测试库：`docker-compose.test.yml` 端口 **55432**，库名 `charsim_test`（`tests/conftest.py` 会核对库名以 `_test` 结尾）。
- 本沙箱：`/usr/bin/docker` 存在，但 **daemon 未运行**（`/var/run/docker.sock` 不存在）；装有本地 **PostgreSQL 16**（`/usr/lib/postgresql/16`），可以用它在 55432 起测试库。7860、7861、55432 当前都没被占用。
- 生产 Mem0 向量目录：`./data/mem0_db`（挂载 `./data:/app/data`）。两台 Mem0 一直是禁用状态，预计没有存量向量；上线前只读核对一次目录是否为空。
- 生产 `.env` 本仓核不到；DEEPSEEK key 口径改为「只用于发布审核」。

## 9. 待定
- 前端（设置页 / 记忆面板）要不要加引导文案：待用户定。只做后端时，前端能收到 503 文案和 `memory_unconfigured` 错误码，但界面上暂时没有引导。
- 运维：GlitchTip postgres 口令需要轮换（与本 spec 无关，只作提醒）。
