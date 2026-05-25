### 15:30 修复设置页非管理员 403 + API key 回填显示
- **做了什么**：
  - `web/server.py` GET `/api/settings/config` — `require_admin` → `get_current_user`，非管理员也能读系统配置
  - `SettingsPanel.jsx` — 已配置 key 时字段显示 `••••••••`、placeholder 联动、保存时判据 sentinel 避免覆盖
- **为什么**：设置页对所有用户开放读取 summary_threshold 等系统配置，POST 写入仍限管理员；用户 key 独立存储，回填显示让用户知道是否已配置
- **影响范围**：`web/server.py:171-174`、`web/frontend/src/components/SettingsPanel.jsx`
