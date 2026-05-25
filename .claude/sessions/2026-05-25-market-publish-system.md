### 07:00 完成市场发布系统 Step 3+4：前端发布表单 + 版本历史 + 衍生网络
- **做了什么**：
  - CharCard.jsx 两个分享确认弹窗改为可编辑发布表单（描述/标签/发布说明），侧边栏用 POST、详情页用 POST/PUT（已发布则更新）
  - MarketCardDetail.jsx 删除接口从 `/api/cards/{id}` 改为 `/api/market/{id}`；新增评论/版本历史/衍生角色三个 Tab
  - sqlite_store.py 中 get_market_card_detail、list_public_cards、search_public_cards、get_author_cards 补充 market_description/market_tags 字段
  - market.py 补充 import json
  - global.css 新增 publish form、market tabs、version list、fork list 样式
  - CharSidebar 初始化 sharedCards 来自已有卡片的 visibility 字段
- **为什么**：发布系统需要前端可编辑的表单，以及市场详情页展示版本历史和 fork 网络
- **影响范围**：CharCard.jsx、MarketCardDetail.jsx、sqlite_store.py、market.py、global.css
