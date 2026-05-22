# 2026-05-22 10项安全漏洞修复

### 16:30 高危4项修复
- **做了什么**：修复4个高危安全漏洞：禁用生产环境FastAPI文档、voice ref audio所有权校验、voice library用户隔离、nginx body size对齐
- **为什么**：/docs暴露API结构、ref audio无卡归属校验可跨用户操作、voice library全局共享无隔离、nginx 10m与FastAPI 20MB不一致
- **影响范围**：web/server.py, web/routers/voice.py, nginx/nginx.conf

### 16:30 中危4项修复
- **做了什么**：修复4个中危安全漏洞：distill task用户隔离、affinity会话鉴权绕过、test端点无admin校验、ADMIN_INVITE_CODE种子逻辑
- **为什么**：task poll/cancel可跨用户操作、in-memory session直接返回affinity无所有权校验、test端点裸奔、种子码定义但未使用
- **影响范围**：web/routers/distill.py, web/routers/chat.py, web/server.py, web/routers/auth.py

### 16:30 低危2项修复
- **做了什么**：修复2个改善项：Docker HEALTHCHECK改用/api/health、backup.sh路径对齐config.yaml
- **为什么**：/docs禁用后healthcheck失效、charsim.db与character_sim.db不一致
- **影响范围**：Dockerfile, scripts/backup.sh, web/server.py (新增/api/health), nginx/nginx.conf (health proxy)
