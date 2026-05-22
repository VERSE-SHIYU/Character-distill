# 2026-05-22 Docker 修复

### 14:30 Docker 启动失败：argon2 缺失
- **做了什么**：requirements.txt `pwdlib>=0.2.0` → `pwdlib[argon2]>=0.2.0`
- **为什么**：pwdlib.recommended() 默认使用 argon2 hasher，但未安装 argon2-cffi 导致容器启动崩溃
- **影响范围**：requirements.txt

### 14:35 Docker 健康检查误报 unhealthy
- **做了什么**：Dockerfile HEALTHCHECK 端点从 `/api/auth/me` 改为 `/docs`
- **为什么**：`/api/auth/me` 返回 401，urllib.urlopen 对非 2xx 抛异常，导致健康检查误报 unhealthy
- **影响范围**：Dockerfile

### 14:40 提交推送
- commit `fix: add argon2 to pwdlib, fix Docker health check endpoint`
