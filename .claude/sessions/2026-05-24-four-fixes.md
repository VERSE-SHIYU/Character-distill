### 14:30 四合一修复：表格对齐 + 情感持久化 + 情感开关拆分
- **做了什么**：
  1. CSS: admin-table td 加 vertical-align:middle 和 white-space:nowrap，admin-actions-cell 加 align-items:center 和 flex-wrap:nowrap
  2. start_session 路由恢复上次同角色会话的情感数据（get_recent_card_session → get_session_affinity → engine.load_affinity）
  3. ChatRequest 加 affinity_enabled 字段，_do_chat/_do_chat_stream 传给 engine，engine._evaluate_affinity 前检查 self.affinity_enabled
  4. 前端 sendMessage/sendMessageStream 传 affinity_enabled; ChatArea 在 affinityEnabled=false 时不显示面板; SettingsPanel 新增"情感系统"开关
- **为什么**：表格行高不一致；情感状态每次"开始对话"重置；情感面板只有一个显示开关但后端总在跑评估
- **影响范围**：web/frontend/src/styles/global.css, web/routers/distill.py, web/routers/chat.py, core/chat_engine.py, useAppStore.js, ChatArea.jsx, SettingsPanel.jsx

### 18:20 删除角色 + 市场 Fork 隔离
- **做了什么**：
  1. web/routers/card.py: 新增 DELETE /{card_id} 路由，校验 owner 后硬删除
  2. CharCard.jsx CharSidebar: 卡片 hover 显示删除按钮(🗑)，点击弹出确认 modal，删除后刷新卡片列表 + 独立卡片列表
  3. storage/sqlite_store.py fork_card: 空 text_id 不再回退到原卡 text_id，支持独立卡片
  4. storage/sqlite_store.py: 新增 list_standalone_cards(user_id) 方法查 text_id 为空的卡片
  5. web/routers/distill.py: 新增 GET /cards/standalone 端点
  6. useAppStore.js: 新增 standaloneCards 状态和 loadStandaloneCards 动作
  7. MarketPage.jsx: 有 currentTextId 时 fork 前弹 modal（挂载到当前文本 / 新建独立空间）；无文本时直接 fork 为独立卡片
  8. CharCard.jsx CharSidebar: 新增"来自市场"分组，渲染 standaloneCards，支持删除
  9. global.css: 新增 .char-delete-btn 样式（hover 显示，红色高亮）
- **影响范围**：web/routers/card.py, CharCard.jsx, sqlite_store.py, distill.py, useAppStore.js, MarketPage.jsx, global.css

### 18:50 群聊异步化 + Token 截断
- **做了什么**：
  1. adapters/llm_adapter.py: 新增 async achat() 方法（异步非流式）
  2. core/group_session.py: send() 改为 async def，使用 await engine.llm.achat() 避免阻塞事件循环
  3. web/routers/group.py: 移除 asyncio.to_thread() 包装，直接 await group.send()
  4. core/group_session.py: _convert_history() 末尾加 token 截断（MAX_HISTORY_TOKENS=2000），从头部丢弃最旧消息
- **为什么**：群聊 LLM 调用阻塞事件循环；长对话 token 无上限会撑爆 context
- **影响范围**：adapters/llm_adapter.py, core/group_session.py, web/routers/group.py

### 19:40 角色卡回收站（软删除）
- **做了什么**：
  1. storage/migrations/029_soft_delete_cards.sql: cards 表加 deleted_at 列
  2. sqlite_store.py: delete_card 改为 UPDATE SET deleted_at; 新增 restore_card、purge_card、list_deleted_cards 方法
  3. list_cards / list_standalone_cards 查询加 AND deleted_at IS NULL 过滤
  4. web/routers/card.py: 新增 GET /trash、POST /{id}/restore、DELETE /{id}/purge 路由
  5. CharCard.jsx CharSidebar: 新增回收站模式（🗑 按钮切换），显示已删角色，支持恢复和彻底删除，底部有清空回收站
  6. 删除确认弹窗文字改为"移入回收站"
- **为什么**：硬删除不可恢复，缺少回收站机制
- **影响范围**：029_soft_delete_cards.sql, sqlite_store.py, card.py, CharCard.jsx
