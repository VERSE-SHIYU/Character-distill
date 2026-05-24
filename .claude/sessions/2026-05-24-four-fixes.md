### 14:30 四合一修复：表格对齐 + 情感持久化 + 情感开关拆分
- **做了什么**：
  1. CSS: admin-table td 加 vertical-align:middle 和 white-space:nowrap，admin-actions-cell 加 align-items:center 和 flex-wrap:nowrap
  2. start_session 路由恢复上次同角色会话的情感数据（get_recent_card_session → get_session_affinity → engine.load_affinity）
  3. ChatRequest 加 affinity_enabled 字段，_do_chat/_do_chat_stream 传给 engine，engine._evaluate_affinity 前检查 self.affinity_enabled
  4. 前端 sendMessage/sendMessageStream 传 affinity_enabled; ChatArea 在 affinityEnabled=false 时不显示面板; SettingsPanel 新增"情感系统"开关
- **为什么**：表格行高不一致；情感状态每次"开始对话"重置；情感面板只有一个显示开关但后端总在跑评估
- **影响范围**：web/frontend/src/styles/global.css, web/routers/distill.py, web/routers/chat.py, core/chat_engine.py, useAppStore.js, ChatArea.jsx, SettingsPanel.jsx
