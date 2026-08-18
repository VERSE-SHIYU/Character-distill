# Character-distill 技术报告

> 依据 graphify 知识图谱（`graphify-out/graph.json`，2026-08-15 快照）重建的架构技术报告。图谱由 tree-sitter 静态提取 + DeepSeek 语义标注生成；本文所有结构结论均来自图数据，可追溯回代码。

## 1. 项目概览

角色模拟器（Character Simulator）——上传任意文本（小说 / 聊天记录 / 人物描写），自动蒸馏出角色人格后进行沉浸式对话。FastAPI + React 全栈，支持 SQLite / PostgreSQL 双存储后端、双区域（深圳 / 新加坡）独立部署。

## 2. 图谱统计（快照事实）

| 指标 | 值 |
|------|-----|
| 语料 | 369 文件 · ~65 万词 |
| 节点 | 4686 |
| 边 | 9606 |
| 社区 | 434（报告展示 227，207 个薄社区省略） |
| 语义标注 | 94% EXTRACTED · 6% INFERRED（603 边，平均置信 0.56） |
| 导入环 | **无** |
| 构建 commit | `eb72a3bd` |

**图的新鲜度**：代码改动后用 `graphify update .`（纯本地、0 API 成本）增量重建；社区名用 `graphify label . --backend=deepseek` 刷新（~$0.05/次）。

## 3. 核心架构中枢（God Nodes）

图的全连通度排序即本项目的核心抽象——存储抽象与对话引擎是两个架构重心：

| 节点 | 边数 | 角色 |
|------|-----|------|
| `StorageBase` | 372 | 存储抽象基类，业务层唯一依赖的存储接口 |
| `SQLiteStore` | 276 | SQLite 实现（本地 / 测试） |
| `PostgresStore` | 255 | PostgreSQL 实现（生产） |
| `ChatEngine` | 152 | 沉浸对话引擎核心（core/chat_engine.py，65.7K） |
| `CharacterCard` | 99 | 角色卡领域模型（蒸馏产物） |
| `useAppStore` | 91 | 前端全局状态（Zustand） |
| `AffinityService` | 89 | 情感亲和度评分（core/affinity_service.py） |
| `LLMAdapter` | 84 | LLM 统一适配层（adapters/llm_adapter.py） |
| `Distiller` | 75 | 角色蒸馏引擎（core/distiller.py，69.5K） |
| `fetchWithTimeout()` | 66 | 前端 API 客户端请求封装 |
| `MemoryManager` | 43 | Mem0 长期记忆（core/memory_manager.py） |
| `RAGEngine` | 42 | 情感感知检索（core/rag.py） |
| `DashScopeEmbedding` | 41 | Embedding 提供方 |
| `TextManager` | 34 | 文本/段落管理（core/text_manager.py） |

**解读**：God nodes 集中在「存储抽象 + 对话/蒸馏引擎」，说明架构把数据访问收敛在单一抽象（`StorageBase`）背后，业务模块通过它切换 SQLite/PG 无需改调用方；对话引擎是全系统边最多的业务节点，符合"沉浸对话"产品定位。

## 4. 模块地图（由社区划分还原）

### 4.1 存储层 `storage/`

- `base.py` — `StorageBase` 抽象接口（14.2K）
- `sqlite_store.py` — SQLite 实现（266.7K，节点最多的存储实现）
- `postgres_store.py` — PG 实现（203.5K）
- `migrations_pg/`、`migrations/` — 两套 schema 迁移；`migrate_sqlite_to_pg.py` 提供升级路径
- `__init__.py` — `get_store()` 工厂：`STORAGE_BACKEND` 必须显式设置（postgres/sqlite），未设置则 fail-fast 拒绝启动，防写错库

### 4.2 核心业务层 `core/`

对话与人格两大引擎 + 一组领域服务：

- **人格引擎**：`distiller.py`（蒸馏）、`chat_preprocessor.py`（对话前身份/上下文预处理）
- **对话引擎**：`chat_engine.py`、`context_engine.py`（上下文构建）、`group_session.py`（群聊会话）
- **记忆 / 检索**：`memory_manager.py`（Mem0 长期记忆）、`rag.py`（情感感知 RAG）、`embeddings.py`（DashScopeEmbedding）、`indexing_service.py` / `scene_indexer.py`（场景级向量索引）
- **情感模拟**：`affinity_service.py`（亲和度）、`reflection_service.py`（反思/人格一致性）
- **运营服务**：`text_manager.py`、`evaluation_pipeline.py`（蒸馏评估）、`moderation/`（内容审核决策）、`agent/`（Agent 工具调用）、`reaction_service.py`、`event_service.py`、`trash_service.py`、`email_service.py`、`clock.py`、`export.py`、`schema.py`、`log_collector.py`

### 4.3 API 路由层 `web/routers/`

`auth.py`（JWT + 邀请码）、`chat.py`、`distill.py`（45.6K，最大路由）、`card.py`、`group.py`、`market.py`（角色市场）、`history.py`、`text.py`、`voice.py`、`memory.py`、`message.py`、`admin.py`、`inter_node.py`（节点间通信）、`wechat.py` / `wechat_utils.py`（企业微信）

### 4.4 基础设施 `web/`

- `server.py` — 应用装配 / SPA fallback
- `deps.py` — FastAPI 依赖注入（含存储全局单例）
- `cross_border_sync.py` — 跨区域同步（`forward_card_to_peer` / `forward_user_profile_to_peer` 等）
- `inter_node_auth.py`、`geo_guard.py`（Geo API 守卫）、`limiter.py`（限流）、`legal_versions.py`

### 4.5 外部适配

- `adapters/llm_adapter.py` — LLM 供应商统一适配
- `speech/`、`services/gptsovits/`、`voice.py` — 语音链路：FunASR（转写）、EdgeTTS / GPT-SoVITS / VoiceClone（合成）

### 4.6 前端 `web/frontend/src/`

- `store/useAppStore` — Zustand 全局状态（91 边，前端第一大 hub）
- `api/client.js` — `fetchWithTimeout` 请求封装（66 边）
- 组件：`ChatArea`、`GroupChatPage`（74.3K）、`PrivateMessageChat`、`CharCard`（43K）、`AdminPanel`（87.4K）、`MinePage`、`MarketPage` / `MarketCardDetail`、`HistoryPanel`、`TextPanel`、`BookReader`、`VoicePanel` 等
- 工具：`utils/card.js`（card_json 统一解析）、`time.js`、`moodTypingSpeed.js`（心情驱动打字速度）

## 5. 关键数据流（图路径验证）

- **对话流**：`ChatEngine` ──uses──▶ `LLMAdapter`（LLM 调用）＋ `RAGEngine`（情感感知检索）＋ `MemoryManager`（长期记忆）。图路径实证：`ChatEngine --uses--> LLMAdapter <--uses-- Distiller`，对话与蒸馏共用同一 LLM 适配层。
- **蒸馏流**：文本 ─▶ `Distiller` ─▶ `CharacterCard` ─▶ 场景索引（`SceneIndexer`/`IndexingService`）── 产物同时进入市场/历史。
- **存储抽象流**：业务层 ─▶ `StorageBase` ─▶ `SQLiteStore` / `PostgresStore`（工厂切换，无 import 环）。
- **跨区域同步**：`web/cross_border_sync.py` 的 `backfill()` ──calls──▶ `forward_user_profile_to_peer()`（图：INFERRED 边，scripts/backfill_users.py → web/cross_border_sync.py）。

## 6. 跨切面关注点（Hyperedges）

- **CI/CD**：build.yml 测试+构建 → deploy.yml 解析 digest → SZ/SG 部署 → 镜像清理（EXT 0.90）
- **双区域部署**：SZ（深圳）/ SG（新加坡）各自独立 PostgreSQL，无主从同步；docker-compose.prod.yml 5 服务栈（postgres / app / nginx / fail2ban）
- **合规**：跨区域同步需披露（Privacy v3 / Terms v5 / inter-node sync / 跨境数据）
- **已知缺陷簇**：前端布局溢出（sidebar offcanvas / ocean bg / chat placeholder / px tester）——来自 e2e 审计产物

## 7. 架构质量结论

1. **无 import 环**：图检测零环，模块依赖是 DAG，改动影响可静态推演。
2. **抽象收敛合理**：`StorageBase` 372 边是设计上正确的 hub（存储可插拔）；`SQLiteStore`/`PostgresStore` 两个实现并列而非继承堆叠。
3. **两个超大文件**：`chat_engine.py`（65.7K）与 `distiller.py`（69.5K）承担了过多职责，是后续重构候选。
4. **6% INFERRED 边**：集中在脚本→生产代码的调用（如 backfill 脚本），属于一次性运维路径，可接受。

## 8. 维护方式

```bash
# 代码改动后增量重建（纯本地，0 成本）
graphify update .

# 刷新社区语义名（需 DEEPSEEK_API_KEY，~$0.05）
graphify label . --backend=deepseek

# 随时查询
graphify explain "ChatEngine"        # 单节点调用链
graphify path "A" "B" --undirected   # 两概念连接桥
graphify affected "X"                # 改动影响面
graphify god-nodes                   # 架构中枢
```
