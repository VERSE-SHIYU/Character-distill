# SZ 专属文件（手工落盘，不走发版）

本目录下的文件由人工 scp 到深圳主机，**不参与** `build.yml` / `deploy.yml`：
`docker-compose.yml` 用的镜像是上游的 `glitchtip/glitchtip:6`，tag 与主站的 commit sha
没有任何对应关系，一旦并进发版流水线，「当前运行版 = commit_sha」这条规则（以及 rollback
指向的镜像）就失去意义；同时它是 2G 机器上的可摘除件，要求「停掉它主站照常」。所以它的
落点、升级、回滚都由这份说明管，不由流水线管。

| 仓库路径 | 服务器落点 |
| --- | --- |
| `deploy/sz/glitchtip/docker-compose.yml` | `/opt/glitchtip/docker-compose.yml` |
| `deploy/sz/nginx/glitchtip.conf` | `/opt/character-distill/nginx/conf.d/glitchtip.conf` |

`/opt/glitchtip/.env`（**不入库**，`chmod 600`）需要三个变量，只列名：

- `GLITCHTIP_DB_PASSWORD` — PG 角色 `glitchtip` 的口令；建议只用纯字母数字（它会被拼进
  连接串，含 `@ : /` 等字符必须 URL 编码）
- `SECRET_KEY` — 任意随机串
- `EMAIL_URL` — `smtp://用户:口令@主机:端口`；不发邮件时用 `consolemail://`，邮件内容会
  进容器日志

三个都缺不得：compose 里用的是 `:?` 守卫（不是 `:-` 给默认值），缺哪个就在解析期点名哪个。

日常操作：

```bash
cd /opt/glitchtip
docker compose -p glitchtip up -d          # 起/更新
docker compose -p glitchtip logs -f        # 看日志
docker compose -p glitchtip stop           # 隔离验证：停掉后主站必须照常
```

改完 `glitchtip.conf` 后，先 `docker exec character-distill-nginx-1 openresty -t -c /etc/nginx/nginx.conf`
通过，再 `docker exec character-distill-nginx-1 openresty -s reload -c /etc/nginx/nginx.conf`。

官方文档（环境变量与 nginx 示例的出处）：<https://glitchtip.com/documentation/install>
