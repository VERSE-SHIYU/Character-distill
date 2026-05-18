### 16:30 Apple 毛玻璃 UI 重构
- **做了什么**：全面重构前端 UI，苹果风格毛玻璃+蓝紫配色+动态背景+首页文本网格+历史无限滚动+页面过渡动画
- **为什么**：用户要求实现 Apple 风格 UI，优化交互流程
- **影响范围**：
  - `web/frontend/index.html` — 添加 4 个彩色背景 blob
  - `web/frontend/src/styles/global.css` — 色系全套替换（蓝紫/绿/黄/紫），毛玻璃效果，100+行新 CSS
  - `web/frontend/src/components/HomePage.jsx` — 重写为"我的文本库"玻璃卡片网格+展开角色
  - `web/frontend/src/components/HistoryPanel.jsx` — 无限滚动替代分页
  - `web/frontend/src/App.jsx` — view-transition 淡入淡出动画
- **构建**：0 errors, 41KB CSS, 250KB JS, 137ms
