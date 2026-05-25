### 16:50 海景玻璃主题对比度修复 — 颜色加深、透明度提高、布局修复
- **做了什么**：根据用户反馈"惨淡兰+字体看不见+首页卡片上移遮内容"，实施了全面重设计划
  - 颜色系统：`--text-primary` #1D6F7F → #052F3A（深青色），`--text-secondary` → #1D6F7F
  - 玻璃透明度：panel 0.30→0.40，sidebar 0.18→0.30，glass-bg 0.18→0.35
  - 侧栏所有硬编码 #1D6F7F → #052F3A（菜单、头像、图标、折叠按钮等）
  - 侧栏 sub 文字 rgba(29,111,127,0.5) → #1D6F7F
  - 登录输入框背景 0.60→0.75，边框加深
  - 首页 `.home-card-section` flex:1 → flex:none（阻止溢出遮内容）
  - 角色卡片悬停删除 translateY(-4px)（消除位置偏移）
- **影响范围**：`web/frontend/src/styles/global.css` — .theme-ocean 变量块、sidebar、panel、home-card-section、home-char-card:hover、login-field input

### 17:10 修复所有主题 active 文字串色为绿色
- **做了什么**：排查并修复各主题下点击/选中态文字均为绿色 `#5a9c5e` 的问题
  - base CSS 中 `.sidebar-item.active`、`.sidebar-item-badge`、`.home-card-badge`、`.pill-value` 硬编码 `color: #5a9c5e` → 改为 `var(--accent)`，各主题独立配色
  - base `.login-tab.active` 使用 `color: var(--accent)` 而 `--primary` == `--accent` 导致文字背景同色不可见 → 改为 `color: #fff`
  - 语音/状态类（voice-guide-badge、voice-msg-success 等）保留 `#5a9c5e` 绿色，不跟随主题
- **为什么**：`#5a9c5e` 硬编码在所有主题下覆盖了各主题自己的 accent 颜色
- **影响范围**：`web/frontend/src/styles/global.css` — base 层 6 处颜色值修改

### 17:30 统一 UI 图标为线性 SVG — 替换所有 emoji 导航/操作图标
- **做了什么**：将所有界面中的 emoji 操作图标替换为 `stroke="currentColor"` 的线性 SVG 图标，与侧栏图标风格统一
  - 新建 `web/frontend/src/components/common/Icon.jsx` — 共享 SVG 图标组件库（12 个图标）
  - ChatArea 顶栏：🌐→Globe、🔊/🔇→Speaker/SpeakerOff、🔄→RefreshCw、👤→User、A- → FontDecrease、A+ → FontIncrease
  - AuthorPage：👁️→Eye、🚫→EyeOff（统计数据可见性切换）
  - MarketCardDetail：👁️→Eye（版本详情查看）
  - SettingsPanel：⚠️→AlertTriangle（API 配置提醒）
- **为什么**：用户要求统一设计感与导航栏线性图标一致，消除 emoji 在不同主题下的风格不一致
- **影响范围**：新建 `Icon.jsx`，修改 `ChatArea.jsx`、`AuthorPage.jsx`、`MarketCardDetail.jsx`、`SettingsPanel.jsx`

### 17:55 替换全部剩余 emoji 为 SVG + 统一 ChatArea 顶栏图标大小
- **做了什么**：一次清空全部界面 emoji 图标，均替换为线性 SVG；统一聊天窗口右上角工具栏图标尺寸和对齐
  - Icon.jsx 新增 Heart、HeartOff、Smile、Shield、Handshake、Star、Book、Theater、MessageSquare、Mic、Lock、Mail
  - 情感面板：❤️→Heart、🤝→Handshake、😊→Smile、🛡️→Shield（含折叠栏和浮动按钮）
  - 点赞按钮：❤️/🤍→Heart/HeartOff 覆盖 MarketPage、MarketCardDetail、PostCard、TextDetailPage 所有评论/回复
  - ProfilePage 统计：❤️→Heart、⭐→Star、🎭→Theater、📖→Book + 网格菜单🎙️→Mic、🔒→Lock、📧→Mail
  - 评论气泡：💬→MessageSquare 覆盖 9 处（ChatArea壳状态、AuthorPage标题、CharCard按钮、PostCard、MarketCardDetail、TextPanel等）
  - HistoryPanel/TextPanel：📖→Book
  - CSS 统一：chat-topbar-btn 移除 font-size（改由SVG统一尺寸）、chat-font-btn 28→34px、voice-toggle-mini 改为标准按钮样式（34px + border + bg）
- **为什么**：用户要求统一设计感与导航栏图标一致，同时顶栏图标大小不一不整齐
- **影响范围**：Icon.jsx（+11 图标）、ChatArea/AuthorPage/MarketPage/MarketCardDetail/ProfilePage/TextDetailPage/CharCard/HistoryPanel/TextPanel/PostCard 共 10+ 文件 + global.css 顶栏按钮样式
