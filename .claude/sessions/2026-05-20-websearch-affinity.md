# 2026-05-20 现实增强搜索 + 好感度系统

### Step 1: 现实增强搜索（两步分离法）
- **做了什么**：ContextEngine._search_web 从预留接口改为两步分离法实现（DuckDuckGo API 搜索 → LLM 角色知识过滤器）
- **为什么**：搜索结果不直接塞进角色 prompt，先经过角色过滤器判断哪些信息角色"可能知道"并用其语言习惯重表达
- **影响范围**：
  - `core/context_engine.py` — __init__ 加 llm 参数 + _search_web 完整实现
  - `core/chat_engine.py` — 传递 llm 给 ContextEngine
  - `web/routers/chat.py` — ChatRequest 加 web_search 字段，_do_chat/_do_chat_stream 接线
  - `web/frontend/src/components/ChatArea.jsx` — 顶部工具栏 🌐 开关
  - `web/frontend/src/store/useAppStore.js` — webSearchEnabled 状态 + SSE 请求传参
  - `web/frontend/src/styles/global.css` — .web-search-toggle.active 样式

### Step 2: 四维好感度系统
- **做了什么**：sessions 表加 4 列 + ChatEngine 后台异步 LLM 评估 + API 端点 + 前端可折叠面板
- **为什么**：每次对话后自动评估角色对用户的情感状态变化，单次±10约束，支持持久化和服务重启恢复
- **影响范围**：
  - `storage/migrations/017_affinity.sql` — sessions 加 affinity/trust/mood/guard 列
  - `storage/base.py` — update_session_affinity / get_session_affinity 抽象方法
  - `storage/sqlite_store.py` — 迁移注册 + 两个新方法实现
  - `core/chat_engine.py` — 四维属性 + _evaluate_affinity（daemon thread/+ get_affinity + load_affinity
  - `web/routers/chat.py` — GET /api/chat/affinity/{session_id} + _session_id 设置 + 恢复时加载好感度
  - `web/frontend/src/components/ChatArea.jsx` — AffinityItem/AffinityInline 组件 + 可折叠面板
  - `web/frontend/src/store/useAppStore.js` — affinity 状态 + fetchAffinity + 每次回复后调用
  - `web/frontend/src/styles/global.css` — .affinity-bar 面板样式 + 颜色编码

### 验证
- ✅ Python 语法检查通过（4 个文件）
- ✅ npm run build 成功
- ✅ 两次 commit 完成
