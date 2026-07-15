# 密钥/凭据轮换记录

## 当前凭据清单

| 密钥 | 用途 | 上次轮换 | 轮换周期 | 生成命令 | 双地域独立？ |
|------|------|---------|---------|---------|------------|
| `JWT_SECRET` | JWT Token 签名 | 首次部署 | 90 天 | `openssl rand -hex 32` | 是 |
| `FERNET_KEY` | 敏感数据独立加密 | 首次部署 | 90 天 | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | 是 |
| `POSTGRES_PASSWORD` | 数据库连接密码 | 首次部署 | 180 天 | `openssl rand -hex 16` | 是 |
| `DEEPSEEK_API_KEY` | DeepSeek LLM API | 首次部署 | 按需（API key 泄露时立即换） | DeepSeek 控制台 | 共用同一 Key |
| `RESEND_API_KEY` | 邮件发送（Resend） | 首次部署 | 按需 | Resend 控制台 | 共用同一 Key |
| `ADMIN_INVITE_CODE` | 注册邀请码种子 | 首次部署 | 按需 | 手动设定 | 是 |
| `FERNET_KEY` 回退 | 若未单独设 `FERNET_KEY`，`JWT_SECRET` 兼做加密密钥（不推荐） | — | — | — | — |

## 轮换操作步骤

### JWT_SECRET / FERNET_KEY（不停机轮换）

新旧密钥同时生效的窗口期内完成轮换：

```bash
# 1. 生成新密钥
NEW_JWT=$(openssl rand -hex 32)
NEW_FERNET=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# 2. 在 .env 中将旧密钥改为 NEW_OLD_KEY=旧值, NEW_KEY=新值 形式
#    （需代码支持多密钥验证——暂不支持，需先实现）
#    当前实现只认单一密钥，轮换需停服：
#    a) 修改 .env 为新值
#    b) docker compose -f docker-compose.prod.yml up -d --build app
#    c) 现有 JWT Token 将立即失效，用户需重新登录

# 3. 两地各自执行（密钥独立）
```

### POSTGRES_PASSWORD（需停服）

```bash
# 1. 在 postgres 容器内 ALTER USER 改密码
# 2. 更新 .env 中的 POSTGRES_PASSWORD 和 DATABASE_URL
# 3. docker compose -f docker-compose.prod.yml up -d
#    所有 service 同时重启，免去先后顺序问题
```

### API Key（DEEPSEEK / RESEND / DASHSCOPE）

在对应厂商控制台重新生成后，更新 `.env` 中对应的值，然后：

```bash
docker compose -f docker-compose.prod.yml up -d --build app
```

（无需重启 postgres / nginx）

## 轮换提醒

- `JWT_SECRET` 和 `FERNET_KEY` 建议每年至少轮换一次
- 凭据泄露时立即轮换，不受周期限制
- 轮换后更新本文件"上次轮换"列

## 安全原则

- `.env` 文件权限设为 `600`（仅 owner 可读）
- 密钥不提交到 git（`.gitignore` 已排除 `.env`）
- 双地域各自密钥独立，一地泄露不影响另一地
