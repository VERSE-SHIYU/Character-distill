### 22:00 七步修复：history 过滤统一 + stream rollback + done_count 竞态 + 任务 TTL + msg_ids + 锚点时序 + 默认 budget
- **做了什么**：
  1. chat.py: 提取 `_rebuild_history_from_db()` 共享函数，统一用白名单 `role in ("user", "char")` 过滤，替换 `_ensure_session` 和 `revoke` 两处实现
  2. chat.py: `_event_generator` except 块加 `engine.history.pop()` 同步回滚后的 memory 与 DB
  3. distiller.py: `_map_with_progress` 和 `_run_reduce_concurrent` 中 `done_count[0]` 读取移入 `async with lock:` 块内
  4. distill.py: `distill_task_status` 改 5min TTL 替代即时 `pop`，避免前端轮询 404
  5. chat.py: `_event_generator` 用 `user_msg_id`/`char_msg_id` 命名变量替代 `msg_ids[0]/[1]` 位置索引，修复 `hidden=True` 时的取值错误
  6. context_engine.py: `_build_history` 中 anchors 搜集后加 `anchors.reverse()` 恢复时间正序
  7. context_engine.py: `_compute_budgets` 未知模型默认值 8000→32000，加 warning 日志
- **影响范围**：web/routers/chat.py, core/distiller.py, web/routers/distill.py, core/context_engine.py

### 23:00 七项 UI/交互修复
- **做了什么**：
  1. VoicePanel: 补充 `const setView = useAppStore((s) => s.setView)` 修复返回键崩溃
  2. ProfilePage: `togglePanel()` 互斥展开 + expand 时 `scrollIntoView` 定位到底部卡片
  3. Market delete 后端: `admin_delete_post()` 方法 + `delete_post` 端点检查 admin 身份
  4. MarketPage 前端: `handleDeletePost()` + 🗑 按钮（admin/owner 可见）
  5. Sidebar: 底部用户区改为可点击的头像+用户名链接，跳转 profile 页
  6. global.css: `.sidebar-user-link` hover 样式
  7. Market delete 路由: 后端 admin 分支 + 前端确认弹窗 + 列表即时刷新
- **为何分批**：上一轮核心架构变动处理后，本轮做 UI 层扫尾，7 项独立小改动一批提交
- **影响范围**：web/frontend/src/components/VoicePanel.jsx, ProfilePage.jsx, MarketPage.jsx, Sidebar.jsx, web/frontend/src/styles/global.css, web/routers/market.py, storage/sqlite_store.py
