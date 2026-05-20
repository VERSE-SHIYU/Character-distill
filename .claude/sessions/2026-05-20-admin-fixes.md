# 2026-05-20 管理员功能 + UI 修复

### Bug 1 修复: 管理后台入口不显示
- **做了什么**：auth.py _user_response() 和 UserResponse 模型加了 is_admin/is_disabled 字段
- **为什么**：DB 查询返回了 is_admin，但 _user_response 没透传，前端 authUser.is_admin 永远是 undefined → 侧边栏管理后台按钮不渲染
- **影响范围**：`web/routers/auth.py`

### feat: 历史记录回收站（软删除）
- **做了什么**：sessions 加 deleted_at 字段，DELETE/clear-all 改为软删除，新增 trash/restore/purge 路由，前端 HistoryPanel 加回收站 toggle + 恢复/彻底删除按钮
- **影响范围**：`storage/migrations/014_sessions_deleted_at.sql`、`storage/sqlite_store.py`、`storage/base.py`、`web/routers/history.py`、`HistoryPanel.jsx`、`global.css`

### Step 1: 管理员密码重置
- **做了什么**：PATCH /api/admin/users/{user_id}/reset-password（is_admin 守卫 + 新密码≥6位校验），AdminPanel UsersTab 每行加"重置密码"按钮 + 弹窗
- **影响范围**：`storage/base.py`、`storage/sqlite_store.py`、`web/routers/admin.py`、`client.js`、`AdminPanel.jsx`

### Step 2: 头像裁剪功能恢复
- **做了什么**：确认 ImageCropModal 完整存在（拖拽平移、clampOffset 边界约束、触摸支持、缩放滑块、圆形导出），CharCard 和 ChatArea 都正确引用。无需恢复。
- **影响范围**：无代码变更

### Step 3: 进入对话速度优化
- **做了什么**：startChat 中 currentView='chat' 移至 await start_session 之前，用户点击"开始对话"后立即进入聊天页显示"…"占位，不再白屏等待
- **影响范围**：`useAppStore.js`

### Step 4: 历史对话页面样式对齐
- **做了什么**：HistoryDetail 消息渲染加 chat-bubble-text span，移除 history-readonly-msg 多余 max-width，与 ChatArea MessageBubble 完全一致的 CSS class
- **影响范围**：`HistoryPanel.jsx`、`global.css`

### Sidebar toggle 按钮修复
- **做了什么**：侧边栏折叠时加可见的 ▶ 按钮（sidebar-toggle-btn），替换原有不可见的 sidebar-trigger hover 区域
- **为什么**：侧边栏折叠后通过 transform translateX 移出屏幕，原有的 sidebar-collapse-btn 在 SidebarHeader 内不可见
- **影响范围**：`App.jsx`、`global.css`
