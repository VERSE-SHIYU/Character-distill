# embedding-key-gate：没配百炼 key 就提示配置（召回升级 PR A）

> v2（10-25 11:15）：按 Shiyu 要求重做分层——凭据门独立成 `web/user_keys.py`，不放进 `deps.py`；门原位替换；reindex 的「服务未装配」拆成 503。v1 未交给执行方，直接以本版为准。

- 基线：main `ab4082f7`（2026-10-25 读）。下文行号均指此提交。
- 来源决定：Shiyu 10-22「每个用户用自己的配置；不配置就不能运行，不做无 key 降级」；10-25「用户没有就提示他配置即可」。共享文档：召回做法调研 · 2026 →「Key 规则与拦截」「S0 核实与新决定（10-25）」。
- 本 PR 只做一件事：面向用户的入口在没配百炼 key 时返回 400 + 配置提示。不碰检索、重排、蒸馏逻辑。

## 1. 目标与目标检查（第一个提交就带上，之后每个提交都跑）

**目标**：用户没配百炼 key（`embedding_key` 为空）时，下表 §4 的每个入口都返回 `400 {"detail": EMBEDDING_KEY_REQUIRED}`，并且在返回前**不花一次模型调用、不建会话、不起后台任务**；配了 key 的用户行为不变。

**目标检查**：新文件 `tests/test_embedding_key_gate.py`，参数化覆盖 §4 矩阵的每一行：

- 用户配了自己的 LLM key、`embedding_key` 为空 → 断言 `status_code == 400` 且 `resp.json() == {"detail": EMBEDDING_KEY_REQUIRED}`（整句相等，不是包含）；
- 同一请求里断言副作用为零：没有调用 LLM / distiller、没有登记新会话（`sessions` / 群会话表无新增）、没有起后台线程或调度索引；
- 用户 LLM key 也没配 → 仍是原来的 503（顺序：先 LLM 503，再百炼 400）。

写法照抄现有先例 `tests/test_session_rag_binding.py:724-760`（`test_x5_reindex_without_embedding_key_spends_no_identify_call`：`_NoKeyStore.get_user_api_config` 返回 `{}`，断言 400 + 整句 detail + 识别次数为 0）。

## 2. 已查实的事实（每条附读过的行）

| # | 事实 | 坐标与关键代码 |
|---|---|---|
| F1 | 提示文案已有唯一出处 | `core/embeddings.py:431-432`：`EMBEDDING_KEY_REQUIRED = "请先在设置页配置阿里云百炼 API Key，配置后才能使用向量检索"` |
| F2 | 现有唯一一处 400 拦截（先例） | `web/routers/distill.py:1319-1322`：`emb = resolve_embedding(...)`；`if not emb.key or indexing_service is None: raise HTTPException(400, EMBEDDING_KEY_REQUIRED)` |
| F3 | 其余入口没 key 时**静默降级**：照常建会话，只是不检索、不记忆 | 例 `web/group_engines.py:43-47`：`emb = resolve_embedding(user_cfg)` 后注释「用户自己的两把 key 缺一把就是 None —— …即『不提供记忆』」；`web/routers/chat.py:193-198` 把空 key 直接传给 `get_rag_for_session` / `memory_for` |
| F4 | 「读配置失败 → 按没配处理」的 try/except 抄了 4 份 | `chat.py:183-192`、`history.py:217-226`、`group_engines.py:34-42`、`distill.py:1610-1618`，四处都是 `except Exception: user_cfg = {}` + `logger.warning(... treating as unconfigured ...)` |
| F5 | **更正共享文档 10-25 的一条**：web 侧没有任何调用回落服务器 key。`resolve_embedding` 的 `env_key` 只有 `mcp_server/server.py:91`、`scripts/rebuild_384_collections.py:281` 传（扫描见 §8 S0-3）；web 侧调用全用默认 `env_key=""`。所以**不删** `GLOBAL_ENV`（MCP 与运维脚本在用），本 PR 不改 `web/llm_resolution.py` | `web/llm_resolution.py:121-122`：`if env_key: return EmbeddingResolution(Source.GLOBAL_ENV, env_key, env_region or "cn")`；同结论已写在 `docs/specs/user-own-keys.md:94` |
| F6 | LLM 侧同样不回落全局 | `web/deps.py:125`：`resolve_llm(config, build_user=_make_user_llm, get_global=_no_global_llm)` |
| F7 | 前端会把 400 的 detail 原样显示，无需改前端 | `web/frontend/src/api/client.js:154-160`（普通请求：`if (body.detail) detail = body.detail` → `throw new AppError(detail, res.status)`）、`:201-222`（SSE 同理 → `onError(new AppError(detail, res.status))` 在 `:222`）；store 写入 `error: err.message`（如 `store/useAppStore.js:1310`），聊天里显示为 `[Error] ${err.message}`（`:1492`） |
| F8 | 全局异常处理器：未捕获异常记堆栈、回 500 | `web/server.py:296-304` `_global_exception_handler` |
| F9 | 以前的规格写的是「缺百炼 key 对话照常」，本 PR 翻转它 | `docs/specs/user-own-keys.md:20`：「Mem0 必须 LLM key 和 embedding key 两把都配，缺一把就不提供记忆，对话照常。」 |
| F10 | `_ensure_session` 只在会话不在内存时才读配置；内存命中直接返回 | `chat.py:134-150`（`if session is not None: ... return session`），读配置在 `:183` 之后的重建分支 |

## 3. 设计

一条规则只写一处（返工经验第 6 条）。职责按层分开，各层只做自己的事：

| 层 | 文件 | 职责 | 本 PR |
|---|---|---|---|
| 策略（纯函数） | `web/llm_resolution.py` | 配置 → 用哪对凭据；不 import 任何 IO / HTTP（模块头 `:1-17` 明写） | 不改 |
| 实例与缓存 | `web/deps.py` | 单例、按用户缓存、刷新活会话；不判 HTTP 状态码 | 不改 |
| **请求边界：用户凭据门** | **新增 `web/user_keys.py`** | 「这个请求要用用户自己的百炼 key」：读配置 → 归一 → 没有就 400 | 新增 |
| 路由 | `web/routers/*`、`web/group_engines.py` | 只调门、拿结果、往下传 | 改调用 |

**`web/user_keys.py`（整个模块就一个公开函数）**：

```python
async def require_embedding(user_id: str, storage: StorageBase) -> EmbeddingResolution:
    """面向用户请求的唯一入口：没配自己的百炼 key → 400 + EMBEDDING_KEY_REQUIRED。"""
    emb = resolve_embedding(await storage.get_user_api_config(user_id) or {})  # 读库不包 try：失败走全局 500
    if not emb.key:
        raise HTTPException(400, EMBEDDING_KEY_REQUIRED)
    return emb
```

- 依赖只有四样：`StorageBase`（接口）、`resolve_embedding`、`EMBEDDING_KEY_REQUIRED`（`core/embeddings.py:432`，复用不复制）、`fastapi.HTTPException`。不 import `deps`，避免和实例层互相引用。
- 为什么不放进 `deps.py`：`deps.py` 是实例与缓存层，目前不 import fastapi、不判状态码；把 HTTP 400 放进去，实例层就和请求层耦合了。为什么不放进 `llm_resolution.py`：它按设计是纯函数、零 IO（模块头 `:11-17`）。
- 为什么不做成 FastAPI `Depends`：依赖在函数体之前执行，会跑到各入口原有的 LLM 503 判断前面，改变「先 503 后 400」的顺序（§7 里 T2、T3、`test_l1` 锁着这个顺序）。
- §4 每个入口把「读配置 + `resolve_embedding`」整段**原位**换成 `emb = await require_embedding(user_id, storage)`（就在现在 `resolve_embedding` 那一行）。所以它和原有 LLM 503、属主 404 的先后关系与现在完全相同；这些位置都在花钱或建会话之前（§4 每行已核）。F4 的 4 份 try/except 随之删除。400 只取决于请求者自己的配置，排在属主 404 之前也不暴露资源是否存在（与 `group.py:111-116` 注释同一口径）。
- `group_engine_deps`（`group_engines.py:29`）本来就是群引擎依赖的唯一准备处，门放在它里面，E3、E4 两个路由不各写一遍。
- `distill.py:1319-1322`（reindex）也改调它；`indexing_service is None` 拆成独立一行，回 503（那是服务没装配，不是用户没配置——原先和「没 key」合成一个 400，两种原因混在同一个提示里）。

**以后要改什么、改几处**：换提示文案 → `core/embeddings.py:432` 一处；改「没 key 怎么办」→ `user_keys.py` 一处；加一个面向用户的入口 → 调一次 `require_embedding`。

**读配置失败为什么不再吞掉**（技术题，我定，对照第 29 条）：吞掉后会给用户「请先配置」，而他其实配过了，是我们这边读库失败——提示是错的。不吞则走 F8 的全局处理器：记堆栈、回 500「服务器内部错误，请稍后重试」，这才是真实情况。

**不改**：`web/llm_resolution.py`（F5）；`web/routers/memory.py`（查看、删除已有记忆本来就允许不配 key，`user-own-keys.md` C9 的设计，且已有自己的 409 `MEMORY_UNCONFIGURED`）；`web/deps.py:209` 后台刷新（不是用户请求）；`mcp_server/`、`scripts/`；前端（F7）。

**同步更新旧规格**：在 `docs/specs/user-own-keys.md:20` 那一条末尾加一句：「（2026-10-25 起由 `embedding-key-gate.md` 取代：没配百炼 key 时对话与蒸馏入口一律 400 提示配置。）」

## 4. 调用点矩阵（入口 × 可观测输出 → 测试）

| # | 入口（路由 → 函数） | 改动坐标 | 现状（无百炼 key） | 改后 | 目标检查用例 |
|---|---|---|---|---|---|
| E1 | 聊天各接口在会话不在内存时 → `_ensure_session` | `chat.py:183-193` | 建会话，不检索不记忆 | 400 | `test_gate[chat_rebuild]`（经 `/api/chat/send`，会话只在库里） |
| E2 | `POST /api/history/{id}/resume` → `resume_session` | `history.py:217-227` | 同上 | 400 | `test_gate[history_resume]` |
| E3 | `POST /api/group/create` → `create_group` → `group_engine_deps` | `group_engines.py:34-43` | 建群，不检索不记忆 | 400 | `test_gate[group_create]` |
| E4 | `POST /api/group/{id}/restore` 等重建 → `_rebuild_group_session` → `group_engine_deps` | 同上 | 同上 | 400 | `test_gate[group_rebuild]` |
| E5 | `POST /api/distill/run` → `distill_by_text_id` | `distill.py:764-765` | 照常蒸馏，存卡后不建索引 | 400，不调 LLM | `test_gate[distill_run]` |
| E6 | `POST /api/distill/start`（及 `:1465` 用草稿续跑）→ `_distill_start_impl` | `distill.py:867-868` | 同上，起后台线程 | 400，不起线程 | `test_gate[distill_start]` |
| E7 | `POST /api/distill/run_stream` → `distill_stream` | `distill.py:1140-1141` | 同上 | 400（在流开始前抛） | `test_gate[distill_stream]` |
| E8 | `POST /api/distill/start_session` → `start_session` | `distill.py:1610-1619` | 建会话，不检索不记忆 | 400 | `test_gate[start_session]` |
| E9 | `POST /api/distill`（旧接口）→ `legacy_distill` | `distill.py:1767-1768` | 照常 | 400，不上传原文、不调 LLM | `test_gate[legacy_distill]` |
| E10 | `POST /api/distill/reindex/{text_id}` → `reindex_rag` | `distill.py:1319-1322` | 没 key 或服务未装配都回 400 | 没 key 400（走公用函数）；服务未装配 503 | 现有 `test_x5_...` 保持绿；目标检查加一条「服务未装配 → 503」 |

S0 要逐行核对：每个入口的 LLM 503 判断确实在改动坐标之前（已读到：`distill.py:752-760`、`:846-862`、`:1137-1139`、`:1597-1601`、`:1764`、`group.py:111-116`；`history.py`、`group.py:create_group` 未逐行核，标**未核实**）。

## 5. 结构自检（第 36 条）

| 问题 | 改前 | 改后 |
|---|---|---|
| 同一条规则写了几处 | 「读配置 + 归一 + 没 key 怎么办」散在 10 处，其中 9 处静默降级、1 处 400；吞读库失败的 try/except 4 份 | 1 处：`web/user_keys.py::require_embedding` |
| 调用方是否被迫知道不该知道的事 | 每个路由自己决定「没 key 降不降级」「读失败吞不吞」 | 路由只拿结果 |
| 以后加一个面向用户的入口要改几处 | 抄一段 try/except + 判断 | 调一次函数 |

## 6. 已有机制核对（第 11 条）

这些路径上原有的机制：鉴权依赖、限流装饰器（`@limiter.limit` 在函数体之前生效）、LLM 503 判断、属主 404 判断。新判断放在 LLM 503 之后、函数体其余部分之前，不改变它们的先后和计数。没有新增重试、超时、缓存；`get_user_api_config` 每个请求本来就读一次，调用次数不变。规模无变化。

## 7. 受影响的测试（扫描得出，S0 跑一遍确认）

需**有意改写**（锁的是被翻转的旧行为）：

- `tests/test_user_own_keys.py:173` `test_T4c_group_without_embedding_key_builds_no_rag`：改为断言 400 + 整句 detail + 不建群。
- `tests/test_card_draft.py:337`（`_FakeStore.get_user_api_config` 返回 `{}`）与 `:354` `test_run_stream_converts_draft`、`tests/test_phase_anchoring.py:526`、`:547`：这些用例测的是蒸馏转换，不是 key；把桩改为返回 `{"embedding_key": "k", "embedding_region": "cn"}`，断言不动。

可能受影响、按「不测 key 的补桩」处理：`tests/test_security_authz.py`、`tests/test_ownership_404.py`（测属主 404；若夹具用户没配百炼 key，`start_session` 等入口会先返回 400——给夹具补上 key，断言不动）、`tests/test_distill_task_api.py`。

应保持绿（顺序：LLM 503 先于百炼 400）：`test_user_own_keys.py` T2、T3；`test_llm_access_gate.py:299` `test_l1_...`；`test_session_rag_binding.py:724` `test_x5_...`；`test_identify_failure_channels.py:369`。

其余引用 `get_user_api_config` 的测试文件（`test_rag_unusable.py`、`test_live_llm_refresh.py`、`test_arc_positions.py`、`test_session_rag_binding.py`）的桩已返回带 key 的配置，预计不受影响——S0 跑一遍确认，红了按本节规则处理（测 key 行为的改断言，不测 key 的补桩）。

## 8. S0（执行方开工前逐条复核，有一条不成立就停下报告）

1. `git rev-parse HEAD` 基于 `ab4082f7` 或其后代；`git diff ab4082f7 -- web/routers/chat.py web/routers/history.py web/routers/distill.py web/group_engines.py web/deps.py core/embeddings.py` 为空。不为空则按新行号重核 §2、§4 并报告差异。
2. 逐条核对 §2 的 F1–F10 与 §4 的改动坐标。
3. 运行并贴原始输出：`git grep -n "resolve_embedding(" -- '*.py' ':!tests'` 与 `git grep -n "env_key=" -- '*.py' ':!tests'`。基线时的输出（我已跑）：

```
web/routers/history.py:227:    emb = resolve_embedding(user_cfg)
web/routers/chat.py:193:    emb = resolve_embedding(user_cfg)
web/routers/memory.py:57:    emb = resolve_embedding(await _user_api_config(user_id, storage))
web/routers/memory.py:106:    configured = bool(cfg.get("api_key")) and bool(resolve_embedding(cfg).key)
web/routers/distill.py:765:    emb = resolve_embedding(_api_config)
web/routers/distill.py:868:    emb = resolve_embedding(api_config)
web/routers/distill.py:1141:    emb = resolve_embedding(_api_config)
web/routers/distill.py:1319:    emb = resolve_embedding(await storage.get_user_api_config(user_id) or {})
web/routers/distill.py:1619:    emb = resolve_embedding(user_cfg)
web/routers/distill.py:1768:    emb = resolve_embedding(_api_config)
web/group_engines.py:43:    emb = resolve_embedding(user_cfg)
web/deps.py:209:    emb = resolve_embedding(await storage.get_user_api_config(user_id) or {})
---
mcp_server/server.py:91:        env_key=os.getenv("EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "",
scripts/rebuild_384_collections.py:281:        env_key=(os.getenv("EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "").strip(),
```

   改完后第一条命令在 web 侧应只剩：`web/user_keys.py` 一处、`web/deps.py:209` 后台刷新一处、`web/routers/memory.py` 两处。
4. 核对 §4 末尾标「未核实」的两处 LLM 503 顺序。

执行中发现的问题：属于本 PR 改动面的直接修；需要拍板的停下报告；不自行记账。

## 9. 测试与执行

- 本地只跑受影响的文件：`tests/test_embedding_key_gate.py`、§7 列出的文件；数据库用已起好的 Docker PG。不跑全量；合并门是分支 CI；合并只做 git 操作。
- 提交顺序：① 目标检查（红，`xfail(strict=True)`）；② 新增 `web/user_keys.py` + 各入口改调 + §7 改写（绿）；③ 更新 `docs/specs/user-own-keys.md:20`。
- Skill：本 PR 是纯后端小改动，不需要文档、前端类 skill。本地若有测试或代码审查类 skill，每个阶段最多用一个（写目标检查一个、提交前自查一个），不叠加。
- 新增发现目标外的问题（例如 16 处重复的 LLM 503 文案「请先在设置页配置 API Key」）：写进报告，不并进本 PR。

## 10. 不做

- 不改检索、重排、蒸馏逻辑（后续 PR）。
- 不处理「会话还活着时用户删掉了 key」（`deps.py:209` 刷新路径另有处理，不在本 PR）。
- 不改前端（F7）。
- 不删 `GLOBAL_ENV`（F5）。

## 发出前判断清单（第 7b 条）

| 判断 | 依据 |
|---|---|
| 提示文案唯一出处 | F1 读过 `:431-432` |
| 9 个入口现状静默降级 | §4 每行改动坐标都读过；E3/E4 读过 `group_engines.py:43-47` 注释 |
| web 侧不回落服务器 key | §8 S0-3 两条扫描原始输出 |
| 前端显示 detail | F7 读过 `client.js:154-160`、`:201-222`，`useAppStore.js:1310`、`:1492` |
| 读库失败会得到 500 | F8 读过 `server.py:296-304` |
| `_ensure_session` 内存命中不读配置 | F10 读过 `chat.py:134-150` |
| §7 受影响测试 | grep 得出 + 读过 T4c、`test_card_draft.py:330-375`、`test_phase_anchoring.py:520-547` 的桩；其余文件**未逐个运行**，交 S0 |
| history / create_group 的 LLM 503 在前 | **未核实**，交 S0-4 |
