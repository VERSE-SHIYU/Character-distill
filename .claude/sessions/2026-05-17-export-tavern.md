# 2026-05-17 角色卡导出 SillyTavern v2 JSON

### 操作摘要

- **做了什么**：新建 `core/export.py`，新增 `GET /api/distill/cards/{card_id}/export` 端点，前端 `CharCard` 加「导出角色卡」按钮
- **为什么**：让蒸馏出的角色卡可导出为 SillyTavern character-card-v2 格式，用户可直接导入 SillyTavern 使用
- **影响范围**：`core/export.py`（新增）、`web/routers/distill.py`（新增路由）、`web/frontend/src/components/CharCard.jsx`（加按钮）、`web/frontend/src/styles/global.css`（加样式）

### 字段映射（CharacterCard → SillyTavern v2）

| ST 字段 | 来源 |
|---------|------|
| name | card.name |
| description | identity + background + personality_text |
| personality | traits + values + memories + tensions |
| first_mes | card.first_message 或查询参数覆盖 |
| mes_example | speaking_style 拼接 |
| creator_notes | card.background |
| extensions | {} (留空) |

### 关键决策
- `description` 合入所有维度使其在 SillyTavern 中显示更完整
- `first_mes` 支持查询参数 `?first_mes=xxx` 覆盖，方便前端自定义开场白
- 中文字符文件名用 RFC 5987 `filename*=UTF-8''` 编码避免 latin-1 崩溃
