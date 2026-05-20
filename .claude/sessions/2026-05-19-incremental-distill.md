### 15:00 蒸馏增量更新重构 — 移除RAG依赖，支持100万字文本

- **做了什么**：按用户指定方案，将蒸馏从"关键词截取+RAG一次蒸馏"改为"逐块增量更新"策略，分5个切片完成
- **为什么**：
  1. 问题1（截断丢失）：原 max_input_chars=30000 对100万字丢失97%内容；增量更新逐块处理，每块仅含角色相关内容
  2. 问题3（RAG瓶颈）：蒸馏阶段移除RAG依赖，RAG仅保留在对话阶段（chat_engine.py不动）
  3. 问题4（进度不透明）：SSE推送逐块进度，前端显示"正在分析第X/Y段"
  4. 问题2（Codified Profile）留待第二期
- **影响范围**：
  - `config.yaml` — distill.max_input_chars删除，新增chunk_size:3000 + max_profile_len:2000
  - `core/distiller.py` — 删除`_extract_character_paragraphs`和RAG导入；简化`distill()`/`distill_stream()`（移除rag参数）；新增`_split_chunks()`、`distill_incremental()`、`distill_incremental_stream()`
  - `core/text_manager.py` — `get_or_distill()`移除rag/all_chars参数，改用distill_incremental；`save_distilled_card()`移除rag参数；`distill_all()`改用distill_incremental
  - `web/routers/distill.py` — 删除RAG构建段；SSE路由改用distill_incremental_stream，推送进度事件；移除未使用的RAGEngine/get_rag_config导入
  - `web/frontend/src/api/client.js` — streamSSE中onStatus改为传递完整payload（支持进度事件携带current/total）
  - `web/frontend/src/store/useAppStore.js` — distillCharacter的onStatus增加analyzing进度处理

### 验证结果

| 检查项 | 期望 | 实际 |
|--------|------|------|
| max_input_chars引用 | 0 | 0 |
| _extract_character_paragraphs引用 | 0 | 0 |
| RAGEngine导入（distill.py） | 0 | 0 |
| get_rag_config导入（distill.py） | 0 | 0 |
| get_or_distill含rag参数调用 | 0 | 0 |
| save_distilled_card含rag参数调用 | 0 | 0 |
| Python语法检查 | 通过 | ✅ |
| 前端build | 成功 | ✅ 283ms |

### 15:30 Bug修复：增量流阻塞 + 硬编码消除

- **做了什么**：
  1. `distill_incremental_stream` 增量阶段 `chat()` 同步阻塞 10-30s → 改用 `chat_stream()`，token 标记 `phase` 由 SSE route 判断不累积到 card JSON
  2. `distill()` / `distill_stream()` 中 `text[:30000]` → `text[: self._chunk_size * 10]`
  3. 前端新增 `compressing` 状态显示
- **为什么**：`chat()` 阻塞期间 `_next_piece` 线程无输出，SSE 连接可能超时
- **影响范围**：`core/distiller.py`、`web/frontend/src/store/useAppStore.js`

### 验证结果

| 检查项 | 期望 | 实际 |
|--------|------|------|
| chat() in distill_incremental_stream | 0 | 0 |
| text[:30000] | 0 | 0 |
| Python语法检查 | 通过 | ✅ |
| 前端build | 成功 | ✅ 140ms |

### 18:30 Bug修复：413 Request Entity Too Large — 微信JSON蒸馏失败

- **做了什么**：
  1. `_split_chunks` 加三级 fallback（`\n\n` → `\n` → 强制按 chunk_size 字符切），防止无空行文本全变成单 chunk
  2. 新增 `_parse_wechat_json()` — 微信JSON导出预清洗：只取 `renderType` 为 text/quote 的消息，提取 senderDisplayName/senderUsername + 日期 + 内容，quote 类型拼入引用内容，其余 system/emoji/image/voice/video/voip/link/file/transfer/redPacket 全部丢弃
  3. `_parse_content` 中检测 `schemaVersion + messages` 格式自动触发微信清洗
  4. RAG 文本级缓存（`_text_rag_cache`）— 同一 text 首次建索引后缓存，后续秒开
  5. 前端 distillIncrementalActive — 增量阶段 onToken 不累加 tokenCount
  6. `profile_draft = new_draft` 加空字符串保护
- **为什么**：
  - 微信 1.1亿字符 JSON 中实际对话文本不到 1%（879K），预清洗后可直接蒸馏
  - `_split_chunks` 原本只在 `\n\n` 处切段，单行 JSON 无空行 → 整本变成 1 个 chunk → 413
  - `_create_session` 每次新建 RAGEngine 全文 embedding（30-60s），同文本切角色/恢复会话重复浪费
- **影响范围**：`core/distiller.py`、`core/text_manager.py`、`web/routers/distill.py`、`web/routers/history.py`、`web/frontend/src/store/useAppStore.js`
- **经验**：
  - 微信JSON预清洗必须在**上传/解析阶段**做，不能等蒸馏时处理脏数据
  - 1.1亿字 JSON → 879K 字纯对话，缩减 99.2%，蒸馏速度和成功率大幅提升
  - `_split_chunks` 不能假设输入文本有段落分隔符，必须有硬字符切 fallback

### 19:00 前端3个UX修复 + 微信JSON第二格式支持

- **做了什么**：
  1. **Problem 1 — resumeLoading 传递修复**：`HistoryPanel.jsx` 中 `HistoryDetail` 未接收 `resumeLoading` prop，导致"继续对话"按钮无 loading 状态。修复：props 解构加 `resumeLoading`，父组件传 `resumeLoading={resumeLoading}`
  2. **Problem 2 — ChatArea 加载动画**：`selectCard` 设 `resumeLoading: true` 但 ChatArea 未消费，切换卡顿无反馈。修复：ChatArea 加 `resumeLoading` 读取，true 时显示 `<Loading text="正在加载会话…" />`
  3. **Problem 3 — RoleSetupModal 提升到 ChatArea**：`RoleSetupModal` 仅在 `CharCard.jsx` 的"开始对话"按钮触发，历史恢复/其他路径进入聊天时缺失。修复：`ChatView` 挂载时若 `!userRole` 自动弹出 `RoleSetupModal`
  4. **微信JSON第二格式**：`_parse_content` 新增 `"messages" + "conversation"` 格式检测（MemoTrace 变体），在 `isinstance(data, dict)` 之前处理
- **影响范围**：`ChatArea.jsx`、`HistoryPanel.jsx`、`core/text_manager.py`

### 19:30 MapReduce 并发蒸馏架构完整实现

- **做了什么**：按用户最终规格，完整重写 distiller.py + 更新 config.yaml + 更新前端 store
- **为什么**：替代之前的半成品 MapReduce，新增共享方法、类常量、自动分批 Reduce
- **影响范围**：
  - `config.yaml` — max_profile_len: 4000 → 8000
  - `core/distiller.py` — 新增类常量 SAFE_SINGLE_REDUCE=120, MAP_CONCURRENCY=10；新增静态 prompt 方法 `_map_system_prompt`, `_map_user_prompt`, `_reduce_system_prompt`, `_reduce_user_prompt`；新增共享异步 Map 方法 `_run_map_concurrent`；新增 `_single_reduce`, `_single_reduce_stream`, `_do_reduce`；完全重写 `distill_incremental` 和 `distill_incremental_stream`
  - `web/frontend/src/store/useAppStore.js` — SSE 状态处理更新：analyzing(并发/进度), merging(整合), formatting(生成JSON) 三层状态
  - `web/routers/distill.py` — 无需修改（协议兼容）
- **关键改动**：
  - 删除了 MAX_RELEVANT=80 采样截断 — 现在通过 MapReduce 并发直接处理全部相关片段
  - `_do_reduce` 自动分批：超过 120 个分析时自动切成多批，递归合并
  - `distill_incremental_stream` 中 Map 使用 as_completed 实时推送每 chunk 完成进度
  - 新增 `formatting` 状态让前端区分"整合中"和"生成JSON中"

### 验证结果

| 检查项 | 期望 | 实际 |
|--------|------|------|
| Python语法检查 | 通过 | ✅ |
| Distiller导入+类常量 | SAFE_SINGLE_REDUCE=120, MAP_CONCURRENCY=10 | ✅ |
| 静态prompt方法 | 4个 | ✅ |
| 前端build | 成功 | ✅ 279ms |

### 20:00 改动5: 撤销消息逻辑修正

- **做了什么**：重写 revokeMessage，仅撤回最后一轮（最后一条user+后续char），3秒冷却期，撤回后发送hidden系统提示触发角色反应
- **为什么**：原来按index撤回会误删消息，需精确到最后一轮；冷却防重复点击；hidden消息注入LLM上下文触发角色对撤回的反应
- **影响范围**：`web/routers/chat.py`（ChatRequest加hidden字段）、`useAppStore.js`（revokeMessage重写+新增_sendRevokeNotice）、`ChatArea.jsx`（revokeCooldown传递+MessageBubble签名更新）、`global.css`（revoke-cooldown样式）

| 检查项 | 期望 | 实际 |
|--------|------|------|
| 前端build | 成功 | ✅ 185ms |

### 20:15 改动6: 头像框统一圆形

- **做了什么**：角色和用户消息气泡头像统一为40px圆形，border-radius 50%，object-fit cover，首字占位
- **为什么**：用户头像border-radius 4px方形，角色头像66px，尺寸和形状不一致
- **影响范围**：`ChatArea.jsx`（MessageBubble中Avatar size 66→40）、`global.css`（.chat-msg-avatar尺寸66→40px、.user-avatar-circle 66→40px + border-radius 4px→50% + 增加overflow/border/shadow匹配Avatar组件）

| 检查项 | 期望 | 实际 |
|--------|------|------|
| 前端build | 成功 | ✅ 215ms |

### 21:30 Mem0 长期记忆系统集成

- **做了什么**：集成 Mem0 长期记忆，DeepSeek v4 Pro 做 LLM，sentence-transformers/all-MiniLM-L6-v2 做本地 embedding，Qdrant 本地向量存储；ChatEngine 移除旧的摘要机制，改为每次对话异步写入 Mem0，构建 system prompt 时按最新用户消息检索记忆
- **为什么**：
  1. 旧的 `_summarize_if_needed` 在同步调用 LLM 做摘要，阻塞 SSE 流式输出
  2. 摘要丢失细节，Mem0 向量检索保留完整语义
  3. 跨会话记忆持久化 — 同一角色不同对话能记住之前的事
- **关键决策**：
  - 使用 DeepSeek v4 Pro 而非 OpenAI（用户无 OpenAI Key）
  - 使用本地 embedding 而非 API（免费 + 隐私）
  - `add()` 用 daemon thread 异步写入，不阻塞对话
  - 统一 `context_window` 控制 LLM 历史条数，30 条
- **影响范围**：
  - `core/memory_manager.py`（新增）— Mem0 封装，lazy import，优雅降级
  - `core/chat_engine.py` — 删除 `_summarize_if_needed` / `summary_threshold` / `_summary_lock`；新增 `memory_manager` / `card_id` / 长期记忆注入 / 异步 add
  - `core/text_manager.py` — `_create_session` 加 `card_id` 参数
  - `web/deps.py` — MemoryManager 单例 + hot-reload
  - `web/routers/history.py` — resume 传 card_id
  - `web/app.py` — 移除 legacy ChatEngine 的 summary_threshold 参数
  - `config.yaml` — 新增 `memory` 段 + `max_tokens: 4096→1024`
  - `requirements.txt` — 新增 `mem0ai>=2.0.0`
- **验证**：同步 add/search 通过；异步 daemon thread add/search 通过；delete_all 通过
- **已知问题（已修复）**：spaCy 和 fastembed 已安装，BM25 关键词搜索已启用

### 2026-05-20 蒸馏超时修复 + Reduce 极速优化

- **做了什么**：三步蒸馏性能优化，每步独立 commit
- **为什么**：LLM 单次调用可能超 300s 限制；Reduce 阶段无心跳 SSE 连接断开；SAFE_SINGLE_REDUCE=120 单批过大；批合并串行执行慢
- **影响范围**：
  - `adapters/llm_adapter.py` — timeout 300→600（sync + async client）
  - `core/distiller.py` — SSE heartbeat 每 50 token；SAFE_SINGLE_REDUCE 120→60；新增 `_single_reduce_async` / `_run_reduce_concurrent`；`_do_reduce` 并发；`distill_incremental_stream` batch Reduce 改为 thread+queue 并发
  - `web/frontend/src/api/client.js` — streamSSE 跳过 heartbeat 事件

### 2026-05-20 用量统计完成（Step 4 剩余部分）

- **做了什么**：完成用量统计全链路 — LLM token计数传出、record_usage 调用、前后端 API、前端展示
- **为什么**：之前仅完成 DB 层（migration + storage methods），缺少 LLM 侧计数和前后端展示
- **影响范围**：
  - `adapters/llm_adapter.py` — `last_usage` dict；chat/async_chat/chat_stream 三个方法从 `completion.usage` 取精确 token 数；chat_stream 加 `stream_options={"include_usage": True}`
  - `core/chat_engine.py` — `_storage`/`_user_id` 字段；`_try_record_usage` helper；chat/chat_stream 后调用
  - `core/distiller.py` — `_storage`/`_user_id` 字段；`_try_record_usage` helper；`_single_reduce`/`_single_reduce_async`/`_single_reduce_stream`/distill_incremental/distill_incremental_stream 关键 LLM 调用点记录
  - `web/routers/auth.py` — `GET /api/auth/usage` 用户自用量
  - `web/routers/admin.py` — `GET /api/admin/usage` 管理员全量
  - `web/routers/chat.py` — 注入 `engine._storage`/`engine._user_id`
  - `web/routers/distill.py` — 注入 `distiller._storage`/`distiller._user_id`
  - `web/frontend/src/api/client.js` — `getMyUsage()` + `adminAPI.getAllUsage()`
  - `web/frontend/src/components/SettingsPanel.jsx` — "我的用量"卡片（总调用/输入/输出 + 按 action 明细）
  - `web/frontend/src/components/AdminPanel.jsx` — "用户用量"tab（按用户汇总表格）
  - `web/frontend/src/styles/global.css` — usage-stats/usage-action 样式
