# 2026-05-24 群聊删除按钮 + ProfilePage 滚动修复

## 背景
侧边栏"我的"入口及下属 ProfilePage/SettingsPanel 可见性问题已通过 dist 重建解决。本次任务在两个新需求：群聊历史记录删除按钮 和 ProfilePage 可滚动。

## 改动

### 19:07 群聊删除后端 API
- **做了什么**: 在 `web/routers/group.py` 末尾添加 `DELETE /api/group/{group_id}` 路由
- **为什么**: 前端需要 API 删除群聊会话及所有消息
- **影响范围**: `web/routers/group.py` — 新增 ~20 行

### 19:07 ProfilePage 滚动修复
- **做了什么**: `.view-transition` 的 `overflow: hidden` → `overflow-y: auto`
- **为什么**: ProfilePage 内容超出一屏时无法滚动，底部"设置"按钮不可见
- **影响范围**: `web/frontend/src/styles/global.css` — 1 行
- **注意**: `.main-panel` 的 `overflow: hidden` 保持不变（防横向溢出）

### 19:07 群聊列表删除按钮
- **做了什么**: HistoryPanel 群聊 tab 每项包裹 `history-swipe-wrapper`，增加 hover 显示删除按钮 + ConfirmModal 确认弹窗
- **为什么**: 用户需要删除群聊历史记录
- **影响范围**: `web/frontend/src/components/HistoryPanel.jsx` — 新增 `deleteGroupId` state、`handleDeleteGroup` handler、ConfirmModal
