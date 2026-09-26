# SZ 专属文件（手工落盘，不走发版）

本目录下的文件由人工 scp 到深圳主机，**不参与** `build.yml` / `deploy.yml`：
`docker-compose.yml` 用的镜像是上游的 `glitchtip/glitchtip`，**按 digest 钉死**（不按 tag ——
tag 可变，发布者能把同一个 tag 指向新镜像；digest 指向唯一一份内容），与主站的 commit sha
没有任何对应关系，一旦并进发版流水线，「当前运行版 = commit_sha」这条规则（以及 rollback
指向的镜像）就失去意义；同时它是 2G 机器上的可摘除件，要求「停掉它主站照常」。所以它的
落点、升级、回滚都由这份说明管，不由流水线管。

**钉 digest 的后果**：本机已有那份镜像时 digest 引用直接解析、不触发拉取（这也是选它而
不选 tag 的原因之一）；换成**新** digest（比如将来升级）则必须能从镜像源拉到。所以改动
`image:` 之后，先跑 `docker compose -p glitchtip config --images` 看解析结果是哪个 digest，
再决定要不要 `up`。

| 仓库路径 | 服务器落点 |
| --- | --- |
| `deploy/sz/glitchtip/docker-compose.yml` | `/opt/glitchtip/docker-compose.yml` |
| `deploy/sz/nginx/glitchtip.conf` | `/opt/character-distill/nginx/conf.d/glitchtip.conf` |

## 改了本目录的文件：必须重新 scp 覆盖 + diff 验证

因为不走流水线，**在仓库里改了不会自动到服务器**——服务器那份是上次 scp 的快照，会一直停在旧版本。
实例：`ALLOWED_HOSTS=errors.bookecho-shiyu.cn` 于 `3dc95f3` 进了仓库，服务器那份仍缺这一行，
容器里 `ALLOWED_HOSTS` 为空 → Django 取通配默认并每次打告警，直到 2026-09-25 手工补传才生效。

所以每次改完走三步（以 compose 为例）：

```bash
# 1) 传到临时位置再覆盖。cp 到已存在的文件会保留原权限（现为 644 root:root）
scp -i ~/.ssh/shenzhen_deploy deploy/sz/glitchtip/docker-compose.yml \
    admin@47.107.42.111:/tmp/glitchtip-compose.yml
ssh -i ~/.ssh/shenzhen_deploy admin@47.107.42.111 \
  'sudo cp /tmp/glitchtip-compose.yml /opt/glitchtip/docker-compose.yml && rm /tmp/glitchtip-compose.yml'

# 2) diff 确认与仓库一致（无输出 = 一致）
ssh -i ~/.ssh/shenzhen_deploy admin@47.107.42.111 \
    'sudo cat /opt/glitchtip/docker-compose.yml' \
  | diff -u deploy/sz/glitchtip/docker-compose.yml -

# 3) 重建。该 compose 只有一个服务、无 depends_on，不会碰到主站 postgres
ssh -i ~/.ssh/shenzhen_deploy admin@47.107.42.111 \
  'cd /opt/glitchtip && sudo docker compose -p glitchtip up -d --force-recreate'
```

改 `glitchtip.conf` 只需第 1、2 步，然后按文末的 `openresty -t` + reload 生效。

`/opt/glitchtip/.env`（**不入库**，`chmod 600`）需要四个变量，只列名：

- `GLITCHTIP_DB_PASSWORD` — PG 角色 `glitchtip` 的口令；建议只用纯字母数字（它会被拼进
  连接串，含 `@ : /` 等字符必须 URL 编码）
- `SECRET_KEY` — 任意随机串
- `EMAIL_URL` — 发信传输。本项目走 Resend 的 SMTP 中继：
  `smtp+ssl://resend:<RESEND_API_KEY>@smtp.resend.com:465`。**TLS 开关由 scheme 决定**（django-environ
  0.13.0：`smtp+ssl`→`EMAIL_USE_SSL=True`、`smtps`/`smtp+tls`→`EMAIL_USE_TLS=True`、`smtp`→明文），
  另设 `EMAIL_USE_SSL` 无效会被盖掉；换别的邮箱把 scheme 改成 `smtp://用户:口令@主机:端口`
- `RESEND_FROM_EMAIL` — 发件地址（取主站 `.env` 同一个值）。compose 把它作为 `DEFAULT_FROM_EMAIL`
  传给容器；不给的话 GlitchTip 默认 `webmaster@localhost`，Resend 会拒

四个都缺不得：compose 里用的是 `:?` 守卫（不是 `:-` 给默认值），缺哪个就在解析期点名哪个。
`RESEND_API_KEY` 的轮换要连带改这个文件，见 `docs/credentials-rotation.md`。

日常操作：

```bash
cd /opt/glitchtip
docker compose -p glitchtip up -d          # 起/更新
docker compose -p glitchtip logs -f        # 看日志
docker compose -p glitchtip stop           # 隔离验证：停掉后主站必须照常
```

管理员初始口令写在 `/opt/glitchtip/ADMIN_INITIAL_PASSWORD.txt`（`chmod 600` root，**不入库**）。
Shiyu 首次登录、改完口令后**删掉这个文件**——之后改口令走 GlitchTip 界面，不再需要它。

改完 `glitchtip.conf` 后，先 `docker exec character-distill-nginx-1 openresty -t -c /etc/nginx/nginx.conf`
通过，再 `docker exec character-distill-nginx-1 openresty -s reload -c /etc/nginx/nginx.conf`。

官方文档（环境变量与 nginx 示例的出处）：<https://glitchtip.com/documentation/install>
