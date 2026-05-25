### 19:00 极光紫微光视觉体系 — 前端主题全面重构
- **做了什么**：
  - **新增 `.theme-aurora` 主题**：`:root` 默认改为极光紫（`#7B61FF` 亮紫为主色），milktea 转为单独 `.theme-milktea` 保持兼容
  - **CSS 变量重构**：`--card-radius: 24px`、`--btn-radius: 12px`、`--input-radius: 8px`，所有边框改为 `rgba(123,97,255,0.08)` 极淡紫
  - **仪表盘**：stats-bar → 24px 白卡紫阴影；char-grid → 24px 白卡 + hover 紫弥散阴影（`!important` 头像 squircle 16px）；recent → 白底紫阴影容器
  - **个人主页**：profile-card 24px 白卡紫阴影；status-card focus 白底 24px 圆角紫虚线框；publish-btn 极光渐变 12px 圆角；chars-widget 24px 白卡紫阴影
  - **通用组件**：`.btn-primary` 纯紫底白字，hover 极光渐变；`.post-card` 24px 圆角白卡紫底
  - **ThemeSwitcher**：极光紫设为第一项 + 默认主题
- **为什么**：视觉体系升级，从暖杏绿转向科技极光紫，统一所有卡片圆角和视觉语言
- **影响范围**：global.css、ThemeSwitcher.jsx

### 19:30 SQL JOIN 修复 + 前端时间格式化统一
- **做了什么**：sqlite_store.py 两处 LEFT JOIN + 去掉 c.updated_at；4 个 JSX 文件替换为微博/微信风 fmtTime
- **为什么**：INNER JOIN 导致动态发布不显示；时间格式化统一为刚刚/X分钟前/X小时前/昨天/星期X/月日/年月日
- **影响范围**：sqlite_store.py、PostCard.jsx、TextDetailPage.jsx、HomePage.jsx、MarketCardDetail.jsx
