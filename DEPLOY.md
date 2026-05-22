# 生产环境部署指南

## 架构

```
国内用户 → 腾讯云CDN (静态加速 + 隐藏源站IP + DDoS)
管理员  → 直连新加坡
             ↓
         安全组 (只开放443/80, SSH白名单)
             ↓
         OpenResty (WAF + CC限流 + HTTPS + 安全头)  ~30MB
             ↓
         FastAPI应用 (CORS + JWT + 限流 + 异常处理)  ~1GB
             ↓
         Fail2Ban + GoAccess   ~15MB
             ↓
         SQLite每日备份 → 腾讯云COS
```

## 1. 服务器初始化

```bash
# Ubuntu 22.04+
apt update && apt install -y docker.io docker-compose-v2 sqlite3
usermod -aG docker ubuntu

# 生成 JWT_SECRET
openssl rand -hex 32
```

## 2. 环境变量

```bash
cp .env.example .env
vim .env  # 填入 JWT_SECRET, DEEPSEEK_API_KEY, ALLOWED_ORIGINS 等
```

## 3. SSL 证书

```bash
# 腾讯云免费证书下载后，放入以下路径:
mkdir -p /etc/ssl/certs /etc/ssl/private
# fullchain.pem → /etc/ssl/certs/
# privkey.pem   → /etc/ssl/private/
```

## 4. 启动

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

## 5. 腾讯云CDN配置

- 源站: 新加坡ECS公网IP
- 回源协议: HTTPS
- 回源Host: 自定义域名
- 缓存规则: /static/*, /assets/* 缓存30天; /api/* 不缓存
- 防盗链: 开启，白名单允许空referer
- IP访问限频: 单IP每秒100次

## 6. 安全组规则

| 端口 | 来源 | 协议 | 说明 |
|------|------|------|------|
| 22 | 堪培拉IP | TCP | SSH管理 |
| 80 | 0.0.0.0/0 | TCP | HTTP重定向 |
| 443 | 腾讯云CDN回源IP段 | TCP | HTTPS |

## 7. 定时备份

```bash
crontab -e
# 添加:
0 3 * * * /home/ubuntu/Character-distill/scripts/backup.sh >> /var/log/backup.log 2>&1
```

## 8. SSH加固

```bash
# /etc/ssh/sshd_config
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
```

## 9. 监控

```bash
# GoAccess实时日志
http://localhost:7890

# 容器状态
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f app

# Fail2Ban封禁列表
docker compose -f docker-compose.prod.yml exec fail2ban fail2ban-client status openresty-waf
```

## 10. 应急恢复

```bash
# 数据库恢复
gunzip /home/ubuntu/backups/charsim_YYYYMMDD_HHMMSS.db.gz
cp charsim_YYYYMMDD_HHMMSS.db /home/ubuntu/Character-distill/data/charsim.db
docker compose -f docker-compose.prod.yml restart app
```
