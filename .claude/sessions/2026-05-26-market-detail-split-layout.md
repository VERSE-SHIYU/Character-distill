### 22:10 市场角色详情页布局重构
- **做了什么**：将 MarketCardDetail 从纵向堆叠改为左栏 sticky 身份卡 + 右栏双列 grid
- **为什么**：桌面端首屏信息密度太低，用户需要滚动才能看到性格特征
- **影响范围**：
  - `MarketCardDetail.jsx` — 拆分为 `.market-detail-split` > `sidebar` + `main`，头像 120→96，名字 24→20，新增 bgExpanded/isMobile/collapsedSections 状态，移动端 ≤960px accordion 折叠
  - `global.css` — 新增 `.market-detail-split/sidebar/main/grid/use-btn` 布局规则，`.market-detail-grid .card-section` 卡片化样式（glass-bg、border、radius），响应式 ≤960px 回退单栏，修复 4 处硬编码 rgba 色值为 CSS 变量
