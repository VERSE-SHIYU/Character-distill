# 生产环境部署指南（双地域 · PostgreSQL）

> 本项目采用**同一域名、双地域分库**部署：境内用户走深圳、境外用户走新加坡，
> 两套数据库各自独立、互不同步（符合《隐私政策》第三条分库存储要求）。

## 架构总览

```
                     同一域名 bookecho-shiyu.cn（一套证书）
                                  │
                  阿里云云解析（DNS 按线路分流）
                  ┌───────────────┴───────────────┐
            默认/境内线路                      境外线路
                  ↓                                ↓
        阿里云 · 深圳 47.107.42.111      腾讯云 · 新加坡 43.134.55.201
        （境内用户、境内库）              （境外用户、境外库）
                  │                                │
   ┌──────────────┼──────────────┐   （同一套 compose，各跑一份）
   ↓              ↓              ↓
安全组(443/80,SSH白名单) → OpenResty(WAF+HTTPS) → FastAPI → PostgreSQL(本机容器)
   │
   └─ Fail2Ban + GoAccess + 每日 pg_dump 备份 → 对应地域对象存储
      （深圳→阿里云OSS，新加坡→腾讯云COS）

两库互不连接、互不同步。
```

**关键 IP / 域名对照**

| 项 | 值 |
|---|---|
| 域名 | `bookecho-shiyu.cn` / `www.bookecho-shiyu.cn` |
| 深圳源站（默认/境内线路） | `47.107.42.111`（阿里云） |
| 新加坡源站（境外线路） | `43.134.55.201`（腾讯云） |
| SSH 管理来源 | `1.144.109.101`（堪培拉，**动态IP，变更后需在安全组更新**） |
| 证书 | 阿里云 DigiCert DV，含两域名，**有效期至 2026-08-30，到期前需重签部署到两地** |

---

## 0. 部署顺序

> 深圳、新加坡两台机器**各自独立**执行第 1–9 步（同一套代码、同一套 compose），
> 区别仅在第 5 步对象存储和第 7 步 CDN/解析配置。两台都跑通后做第 10 步 DNS 分流。

## 1. 服务器初始化（两台都做）

```bash
# Ubuntu 22.04+
apt update && apt install -y docker.io docker-compose-v2
usermod -aG docker ubuntu

# 生成 JWT_SECRET
openssl rand -hex 32
```

> 不再需要 sqlite3：生产用 PostgreSQL（由 docker-compose 的 postgres 容器提供）。

## 2. 前端构建

```bash
cd web/frontend && npm install && npm run build
ls web/frontend/dist/index.html   # 确认已生成
```

> Docker 部署时 `up -d --build` 会自动执行此步。

## 3. 环境变量

```bash
cp .env.example .env
vim .env
```

必填项：

- `JWT_SECRET`（`openssl rand -hex 32`）
- `STORAGE_BACKEND=postgres`
- `DATABASE_URL=postgresql://charsim:你的密码@127.0.0.1:5432/charsim`
- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`（与 DATABASE_URL 一致）
- `ALLOWED_ORIGINS=https://bookecho-shiyu.cn,https://www.bookecho-shiyu.cn`
- `RESEND_API_KEY`、`ADMIN_INVITE_CODE`、`FERNET_KEY`

> 两台机器的 `JWT_SECRET`、`FERNET_KEY` 可各自独立；数据库密码各自设置。

## 4. SSL 证书（两台都装同一套）

```bash
# 阿里云证书下载（nginx 格式）后得到 bookecho-shiyu.cn.pem 和 .key
mkdir -p /etc/ssl/certs /etc/ssl/private
# bookecho-shiyu.cn.pem → /etc/ssl/certs/fullchain.pem
# bookecho-shiyu.cn.key → /etc/ssl/private/privkey.pem
chmod 600 /etc/ssl/private/privkey.pem
```

> ⚠️ 证书 2026-08-30 到期。到期前在阿里云重新签发，重新下载部署到**两台**，并 `docker compose restart nginx`。建议设置日历提醒。

## 5. 启动

```bash
docker compose -f docker-compose.prod.yml up -d --build

# 确认 postgres 健康、app 已连上
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f app
```

首次启动时 app 会自动执行 `storage/migrations_pg/001_init.sql` 建表（幂等，可重复运行）。

## 6. 安全组规则（两台都配）

| 端口 | 来源 | 协议 | 说明 |
|------|------|------|------|
| 22 | `1.144.109.101`（堪培拉，按需更新） | TCP | SSH 管理 |
| 80 | 0.0.0.0/0 | TCP | HTTP 重定向到 HTTPS |
| 443 | 0.0.0.0/0（或 CDN 回源 IP 段） | TCP | HTTPS |

> 5432（PostgreSQL）**绝不对公网开放**，compose 已限制为 `127.0.0.1:5432` 仅本机。

## 7. CDN / 加速（可选，按地域分别配）

- **深圳**：可接阿里云 CDN，源站 `47.107.42.111`，回源 HTTPS，回源 Host 用域名；`/assets/*`、`/static/*` 缓存 30 天，`/api/*` 不缓存。
- **新加坡**：可接腾讯云 CDN / EdgeOne，源站 `43.134.55.201`，配置同上。

> 个人项目初期可不接 CDN，直接由 DNS 解析到源站；后续要隐藏源站/抗 DDoS 再加。

## 8. SSH 加固（两台都做）

```bash
# /etc/ssh/sshd_config
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
# 改完: systemctl restart sshd
```

## 9. 定时备份（两台各自，存到对应地域）

```bash
chmod +x scripts/backup.sh
crontab -e
# 添加：
0 3 * * * DATABASE_URL='postgresql://charsim:密码@127.0.0.1:5432/charsim' /home/ubuntu/Character-distill/scripts/backup.sh >> /var/log/backup.log 2>&1
```

- 深圳机：在 `backup.sh` 中启用 OSS 上传段（境内备份留境内）
- 新加坡机：启用 COS 上传段（境外备份留境外）

## 10. DNS 分流（两台都部署好后，最后做）

在阿里云云解析为 `bookecho-shiyu.cn` 配置：

| 主机记录 | 类型 | 线路 | 记录值 |
|---|---|---|---|
| @ | A | 默认 | `47.107.42.111`（深圳） |
| @ | A | 境外 | `43.134.55.201`（新加坡） |

> 已配置完成（见控制台）。境内用户解析到深圳、境外解析到新加坡，
> 配合后端按请求 IP 的境内/境外拦截，实现"中国对中国、外国对外国"。

## 11. 监控

```bash
# GoAccess 实时日志（仅本机）
curl http://localhost:7890

# 容器状态 / 日志
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f app

# Fail2Ban 封禁列表
docker compose -f docker-compose.prod.yml exec fail2ban fail2ban-client status
```

## 12. 应急恢复（PostgreSQL）

```bash
# 从备份恢复（在对应地域机器上执行）
gunzip -c /home/ubuntu/backups/charsim_YYYYMMDD_HHMMSS.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T postgres \
  psql "postgresql://charsim:密码@127.0.0.1:5432/charsim"

docker compose -f docker-compose.prod.yml restart app
```

---

## 附：与旧架构的差异（迁移注意）

- **数据库**：SQLite → PostgreSQL（自建容器，用户 <200 足够）。
- **存储切换**：由 `.env` 的 `STORAGE_BACKEND` 控制，可回退 sqlite。
- **架构图修正**：旧文档写"国内用户→新加坡"有误；实为境内→深圳、境外→新加坡。
- **备份**：sqlite `.backup` → `pg_dump`；按地域分别上传 OSS / COS，互不跨境。
