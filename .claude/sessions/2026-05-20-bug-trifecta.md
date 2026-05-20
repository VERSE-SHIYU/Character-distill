# 2026-05-20 三连修 Bug

### 10:00 Bug 1 修复: last_summary AttributeError
- **做了什么**：在 `core/chat_engine.py` ChatEngine.__init__ 中添加 `self.last_summary: str | None = None`
- **为什么**：Mem0 集成时移除了旧的摘要机制（`_summarize_if_needed`），但 `chat.py` 多处引用 `engine.last_summary`，导致 AttributeError
- **影响范围**：`core/chat_engine.py`

### 10:15 Bug 3 修复: 角色卡页面自动创建聊天
- **做了什么**：ChatArea.jsx 的 useEffect 中增加 `currentView === 'chat'` 守卫
- **为什么**：`viewCard` 导航到角色详情页时，currentCard 已设置但 sessionId 为空，useEffect 触发 startChat 创建了不需要的会话
- **影响范围**：`web/frontend/src/components/ChatArea.jsx`

### 10:30 Bug 2 修复: 服务重启后旧会话无法回复
- **做了什么**：
  1. 新增 `_ensure_session()` helper — 当 session 不在内存时自动从 DB 重建 ChatEngine、加载历史
  2. 在 `_do_chat`、`_do_chat_stream`、`_do_reset`、`revoke_messages` 中替换 `sessions.get()` 为 `await _ensure_session()`
- **为什么**：服务重启后 `_sessions` 内存字典为空，`sessions.get(session_id)` 返回 None → 404。`_ensure_session` 参考了 `resume_session` 的重建逻辑，实现透明恢复
- **影响范围**：`web/routers/chat.py`（+75/-14）

## Part 2: 聊天记录预处理管线

### 11:00 2.2 后端 storage — texts表加text_type列
- **做了什么**：007_text_type migration + save_text/get_text支持text_type + upload API接收参数透传
- **为什么**：区分故事和聊天记录，蒸馏时采用不同策略
- **影响范围**：`storage/migrations/007_text_type.sql`、`storage/sqlite_store.py`、`storage/base.py`、`core/text_manager.py`、`web/routers/text.py`

### 11:40 2.3 core/chat_preprocessor.py
- **做了什么**：ChatPreprocessor三层清洗管线（Layer 0: 日期去重 + 系统消息/媒体标记/纯标点过滤；Layer 1: 保留≥5字/观点词/Q&A对 + 剔除事务消息；Layer 2: 目标角色发言 + 上下文窗口）
- **为什么**：微信聊天记录含大量噪音，直接蒸馏会污染角色档案
- **影响范围**：`core/chat_preprocessor.py`（新文件，269行）

### 11:55 2.4+2.5 distiller.py + distill路由
- **做了什么**：distill_incremental_stream新增text_type；chat类型→预处理+专用Map提示词（说话习惯/态度/情感反应）+ 按对话轮次分块；distill路由读取text_type透传
- **为什么**：聊天记录角色分析重点和小说不同——侧重说话风格/口头禅/人际关系模式
- **影响范围**：`core/distiller.py`（+91/-4）、`web/routers/distill.py`

### 12:30 2.1 前端上传组件 + 字数限制
- **做了什么**：上传弹窗文本类型选择 + 字数颜色编码 + 时间预估 + uploadText透传text_type + 后端1M字上限校验
- **为什么**：用户需在上传时明确类型，字数预估管理蒸馏耗时预期
- **影响范围**：`TextPanel.jsx`、`useAppStore.js`、`global.css`、`core/text_manager.py`

## Part 3: 登录系统 + 用户数据隔离

### 14:00 Step 1: users 表 + DB 方法
- **做了什么**：创建 009_users.sql migration + sqlite_store.py 中 create_user/get_user_by_username/get_user_by_id
- **为什么**：JWT 认证需要用户持久化存储
- **影响范围**：`storage/migrations/009_users.sql`、`storage/sqlite_store.py`、`storage/base.py`

### 14:30 Step 2: Auth 路由 + JWT 中间件
- **做了什么**：web/routers/auth.py（register/login/me + get_current_user）+ server.py AuthMiddleware 拦截所有 /api/* 路径
- **为什么**：JWT Bearer Token 认证 + request.state.user 注入用户上下文
- **影响范围**：`web/routers/auth.py`（新文件）、`web/server.py`

### 15:00 Step 3: user_id 数据隔离
- **做了什么**：texts/cards/sessions 表加 user_id 列（migration 010-012）+ 所有存储方法加 user_id 参数 + 所有 web 路由从 request.state.user 读取 user_id 传入
- **为什么**：每个用户只能看到自己的数据，实现多用户数据隔离
- **影响范围**：`storage/migrations/010-012_*.sql`、`storage/sqlite_store.py`（6个方法）、`storage/base.py`（6个抽象方法）、`core/text_manager.py`（6个方法）、`web/routers/text.py`、`web/routers/distill.py`、`web/routers/chat.py`、`web/routers/history.py`

### 16:00 Step 4: 前端登录页
- **做了什么**：LoginPage.jsx（登录/注册切换）+ api/client.js 自动附加 Authorization header + 401 自动清除 token + Sidebar 退出按钮 + App.jsx 认证守卫
- **为什么**：用户通过 JWT 登录后，所有 API 调用自动携带 token，token 过期/无效时自动登出
- **影响范围**：`LoginPage.jsx`（新文件）、`api/client.js`、`App.jsx`、`Sidebar.jsx`、`useAppStore.js`、`global.css`

## Part 4: 管理员系统

### 17:00 Task 1: 数据迁移
- **做了什么**：创建 migrate_data.py，将 texts/cards/sessions 中无 user_id 的历史数据归属到 Shiyu 用户
- **影响范围**：`migrate_data.py`（新文件），迁移了 4 texts + 5 cards + 10 sessions

### 17:30 Task 2 Step 1: Admin DB
- **做了什么**：013_admin migration（is_admin/is_disabled 列 + invite_codes 表）+ 7个 admin DB 方法 + SET is_admin=1 FOR Shiyu
- **影响范围**：`storage/migrations/013_admin.sql`、`storage/sqlite_store.py`、`storage/base.py`

### 18:00 Task 2 Step 2: Admin 后端路由
- **做了什么**：web/routers/admin.py（require_admin 依赖 + 用户管理 + 邀请码生成/列表）+ auth.py 注册加 invite_code 校验 + AuthMiddleware 加 is_disabled 检查
- **影响范围**：`web/routers/admin.py`（新文件）、`web/server.py`、`web/routers/auth.py`

### 18:30 Task 2 Step 3: Admin 前端
- **做了什么**：AdminPanel.jsx（用户管理/邀请码两个Tab）+ Sidebar 管理员入口 + LoginPage 邀请码输入 + client.js adminAPI
- **影响范围**：`AdminPanel.jsx`（新文件）、`client.js`、`Sidebar.jsx`、`App.jsx`、`LoginPage.jsx`、`useAppStore.js`、`global.css`

### 19:00 Task 2 Step 4: 验证（12项全部通过）
- ✅ Shiyu(admin) → GET /api/admin/users → 200，用户列表
- ✅ Alice(非admin) → 403 "需要管理员权限"
- ✅ Bob(非admin) → 403 "需要管理员权限"
- ✅ 生成邀请码 → 200，返回2个码
- ✅ 有效邀请码注册 → 200，成功
- ✅ 重复使用邀请码 → 400 "邀请码已被使用"
- ✅ 无效邀请码 → 400 "邀请码无效"
- ✅ 无邀请码注册 → 400 "需要邀请码才能注册"
- ✅ 禁用 bob → 200
- ✅ 被禁用用户访问API → 403 "账号已被禁用"
- ✅ 启用 bob → 200
- ✅ 启用后访问API → 200，正常

### 16:30 Step 5: 验证
- **测试结果**：
  1. ✅ 注册 alice/bob 两个用户成功
  2. ✅ Alice 上传 text，只看到自己的（data isolation）
  3. ✅ Bob 上传 text，只看到自己的
  4. ✅ 无 token → 401 "请先登录"
  5. ✅ 无效 token → 401 "Token 无效"
  6. ✅ 重复注册 → 409 "用户名已存在"
  7. ✅ 密码错误 → 401 "用户名或密码错误"
- **影响范围**：无代码变更，仅验证
