# 密钥/凭据轮换记录

## 当前凭据清单

| 密钥 | 用途 | 上次轮换 | 轮换周期 | 生成命令 | 双地域独立？ |
|------|------|---------|---------|---------|------------|
| `JWT_SECRET` | JWT Token 签名 | 首次部署 | 90 天 | `openssl rand -hex 32` | 是 |
| `FERNET_KEY` | 敏感数据独立加密 | 首次部署 | 90 天 | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | 是 |
| `POSTGRES_PASSWORD` | 数据库连接密码 | 首次部署 | 180 天 | `openssl rand -hex 16` | 是 |
| `DEEPSEEK_API_KEY` | DeepSeek LLM API | 首次部署 | 按需（API key 泄露时立即换） | DeepSeek 控制台 | 共用同一 Key |
| `RESEND_API_KEY` | 邮件发送（Resend）。使用方：SZ `/opt/glitchtip/.env`（GlitchTip 发信） | 首次部署 | 按需 | Resend 控制台 | 共用同一 Key |
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
#    b) 钉住当前运行版 tag 再重建 app（镜像来自 GHCR，服务器不再构建；不钉 tag 会因
#       docker-compose.prod.yml:78 的 ${APP_IMAGE_TAG:-latest} 回落到浮动的旧 latest）：
#         export APP_IMAGE_TAG=$(docker inspect -f '{{.Config.Image}}' \
#           "$(docker compose -f docker-compose.prod.yml ps -q app)" | sed 's/.*://')
#         docker compose -f docker-compose.prod.yml up -d --no-deps app
#         （--no-deps 不能省：不带它会把 app 依赖的 postgres 一起 recreate）
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

这三个 Key 不由仓库变量下发，走服务器 `.env`（`env_file:`），所以改完必须重建 app 容器
（`restart` 只重启进程，不重读 `.env`）。

镜像来自 GHCR，服务器**不再构建**（`--build` 的写法已过时）。且必须把当前运行版的 tag
钉住：`docker-compose.prod.yml:78` 是 `${APP_IMAGE_TAG:-latest}`，tag 未设就回落到浮动
的 `:latest`（旧版）：

```bash
cd /opt/character-distill
export APP_IMAGE_TAG=$(docker inspect -f '{{.Config.Image}}' \
  "$(docker compose -f docker-compose.prod.yml ps -q app)" | sed 's/.*://')
docker compose -f docker-compose.prod.yml up -d --no-deps app
```

`--no-deps` 不能省。点名 `app` 时 compose 仍会连带它 `depends_on` 的 postgres 一起 recreate
（2026-09-25 实测把 `character-distill-postgres-1` 重建了一次——卷没动、数据完好，但违反了
「重启只重建 app」那条铁律）；`--no-deps` 才是真的只动 app。

（无需重启 postgres / nginx。改仓库变量是另一条路：见 `DEPLOY.md`「由部署下发的配置」，
重跑一次 deploy `both` 即可，不需要登服务器。）

### RESEND_API_KEY 额外一步：GlitchTip（SZ）

GlitchTip 复用同一把 Resend Key，但读的是它自己的 `/opt/glitchtip/.env`——它由独立 compose
项目 `glitchtip` 管理，**不在** `docker-compose.prod.yml` 里，所以上面那条 `up -d` 不会带它。
轮换后必须同步改这个文件并重建该容器，否则 GlitchTip 的告警邮件会用旧 key 静默失败：

```bash
# 1) 改 /opt/glitchtip/.env 里 EMAIL_URL 的 key 段（文件权限 600）：
#    EMAIL_URL=smtp+ssl://resend:<新key>@smtp.resend.com:465
# 2) 重建 glitchtip（env 变化即重建容器；与主站互不影响）
cd /opt/glitchtip && docker compose -p glitchtip up -d
```

## 轮换提醒

- `JWT_SECRET` 和 `FERNET_KEY` 建议每年至少轮换一次
- 凭据泄露时立即轮换，不受周期限制
- 轮换后更新本文件"上次轮换"列

## 安全原则

- `.env` 文件权限设为 `600`（仅 owner 可读）
- 密钥不提交到 git（`.gitignore` 已排除 `.env`）
- 双地域各自密钥独立，一地泄露不影响另一地
