### 15:30 SettingsPanel voice removal + MinePage isolation
- **做了什么**: SettingsPanel.jsx — 删除全部语音相关代码（状态/选择器/函数/3个section，约280行）；MinePage.jsx — 添加 handleTabChange 用户隔离，关注列表点击改为打开独立 AuthorPage
- **为什么**: 设置页不再需要语音功能配置；MinePage 用户隔离防止 store 污染
- **影响范围**: web/frontend/src/components/SettingsPanel.jsx, MinePage.jsx

### 17:50 MarketPage 封面图导向 + 无限滚动
- **做了什么**: MarketPage.jsx — 卡片改为 cover-image 导向布局（封面图/渐变字母 + 作者名 + 点赞 + 评论数），去掉操作按钮（使用/评论/删除），整卡点击进详情；分页改为 IntersectionObserver 无限滚动，搜索时同步 query 状态
- **为什么**: 市场页改版，图片导向提升浏览体验，无限滚动去分页摩擦
- **影响范围**: web/frontend/src/components/MarketPage.jsx, web/frontend/src/styles/global.css
