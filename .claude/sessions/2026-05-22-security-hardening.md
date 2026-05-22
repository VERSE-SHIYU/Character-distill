# 2026-05-22 安全加固（12项）

### 15:00 安全审计 + 修复实施
- **做了什么**：按 OWASP Top 10:2025 + FastAPI 生产部署标准，实施 12 项安全加固
- **为什么**：CORS全开、JWT默认值、无限流等为高危漏洞；CDN回源IP检测失效
- **影响范围**：
  - 修改: web/server.py, auth.py, chat.py, text.py, voice.py, limiter.py, .env.example
  - 新增: web/security.py, nginx/, fail2ban/, scripts/backup.sh, docker-compose.prod.yml, DEPLOY.md
  - commit: `feat: 12-point security hardening (OWASP + production deployment)`
