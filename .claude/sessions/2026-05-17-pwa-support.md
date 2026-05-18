# 2026-05-17 PWA 支持

## 改动

### 新建文件（5个）

| 文件 | 说明 |
|------|------|
| `web/frontend/public/manifest.json` | PWA manifest：standalone 模式，indigo 主题色，192+512 图标 |
| `web/frontend/public/sw.js` | Service Worker：install 预缓存 / + /index.html，fetch 网络优先→缓存回退，跳过 /api/ |
| `web/frontend/public/icon-192.png` | 紫色圆角方块 + "CS" 白字 192x192（Pillow 生成） |
| `web/frontend/public/icon-512.png` | 同上 512x512 |

### 修改文件（3个）

| 文件 | 改动 |
|------|------|
| `web/frontend/index.html` | 加 manifest link、theme-color、apple-web-app-capable、apple-touch-icon |
| `web/frontend/src/main.jsx` | load 事件中注册 Service Worker |
| `web/server.py` | 新建 `/manifest.json`、`/sw.js`、`/icon-192.png`、`/icon-512.png` 路由 |

## 验证
- 前端 build：33 modules, 255ms, 0 errors
- dist/ 含 manifest.json, sw.js, icon 文件
- 所有 PWA 路由已注册
