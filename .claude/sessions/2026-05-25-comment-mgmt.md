# 2026-05-25 — 评论管理：删除 + 举报 + 审核

## 改了什么

### 新增功能
- **评论删除**：卡片作者可删任意评论，评论作者可删自己的，管理员可删任意。有 ConfirmModal + "删除后无法恢复"提示。
- **批量删除**：卡片作者可进入批量模式，勾选多条评论后一次删除。
- **举报评论**：非作者/非卡片所有人的用户可举报评论（需填写原因）。
- **审核面板**：AdminPanel 新增"举报管理"Tab，管理员可"驳回"或"删除评论"。

### 改动文件

| 文件 | 改动 |
|------|------|
| `web/routers/market.py` | 新增 3 个路由：`DELETE /{card_id}/comments/{comment_id}`, `POST /batch-delete`, `POST /report` |
| `web/routers/admin.py` | 新增 3 个管理路由：`GET /reports`, `POST /{comment_id}/resolve`, `POST /{comment_id}/delete-comment` |
| `storage/sqlite_store.py` | 新增 6 个方法：`get_comment`, `get_comment_reports_grouped`, `resolve_all_reports`, `delete_comment_and_resolve_reports` (已有 delete_comment, batch_delete_comments, add_comment_report) |
| `web/frontend/src/api/client.js` | adminAPI 新增 `listReports`, `resolveReport`, `deleteReportedComment` |
| `web/frontend/src/components/MarketCardDetail.jsx` | 评论列表重构：批量模式、删除、举报 UI |
| `web/frontend/src/components/AdminPanel.jsx` | 新增 `ReportsTab` 组件 + TABS 加 "举报管理" |
| `web/frontend/src/styles/global.css` | 新增 comment action / batch bar / report textarea 样式 |

### API 设计
- 举报按 comment_id 分组（同一条评论被多次举报聚合为一条记录）
- 管理员操作也基于 comment_id（驳回/删除评论时自动关闭该评论的全部举报）
- 硬删除 + 级联举报关闭

### 验证
- `npx vite build` ✅
- Python AST parse 所有修改文件 ✅
