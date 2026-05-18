### 19:30 9条 UI/UX 修复 + 三套精炼配色方案 + ThemeSwitcher

- **做了什么**：按编号顺序修复 #1-#9 用户反馈的 UI 问题；将三套主题颜色精炼为独立完整色板（暖杏/蓝绿潮汐/淡雅）；创建 ThemeSwitcher 复用组件
- **为什么**：用户逐条指定修复方案；原主题配色不够协调统一，需要三套并列可选
- **影响范围**：
  - `web/frontend/src/components/ChatArea.jsx` — #1 撤回按钮，#2 头像尺寸，#3 用户头像上传，#4 头像同步
  - `web/frontend/src/components/CharCard.jsx` — #4 头像存储同步，#6 加载态，#9 scrollIntoView
  - `web/frontend/src/components/SettingsPanel.jsx` — #5 模型提示，ThemeSwitcher 替换内联按钮
  - `web/frontend/src/components/HistoryPanel.jsx` — #7 分组折叠，#8 清空按钮
  - `web/frontend/src/components/ThemeSwitcher.jsx` — 新增复用组件（侧边栏/设置共用）
  - `web/frontend/src/styles/global.css` — 三套 :root 完整变量重写 + .theme-switch-btn 样式
  - `web/frontend/src/store/useAppStore.js` — cardAvatars store map
  - `web/frontend/src/utils/theme.js` — 支持 'sakura' 第三主题
  - `storage/sqlite_store.py` — clear_all_sessions()
  - `web/routers/history.py` — POST /api/history/clear-all
