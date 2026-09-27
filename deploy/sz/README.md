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

## 看门狗 `watchdog.py`（宿主机 cron）

GlitchTip 的调度器停摆时，站内告警也一起哑掉——没人报警。看门狗从外面盯：每分钟只读 PG
两个秒龄，`uptime-dispatch-checks` 心跳（阈值 60 秒）与最老可执行 QUEUED 任务（阈值 600 秒）。
两项都正常就 ping 一次反向心跳地址；异常就 `docker restart` 看门狗容器一次（1 小时最多 1 次），
并在同一次运行里原地确认（心跳值前进=恢复）；通知 1 小时最多 1 封。设计依据与参数见
`docs/specs/observability-glitchtip.md` 的「S0 后定稿」。

| 仓库路径 | 服务器落点 |
| --- | --- |
| `deploy/sz/glitchtip/watchdog.py` | `/opt/glitchtip/watchdog.py` |

**落盘**：同上一节的三步（scp 到 `/tmp` → `sudo cp` 覆盖 → `diff` 验证）。只依赖标准库 +
宿主机的 `docker`、`curl`，没有常驻进程。

**心跳地址文件**：`/root/.glitchtip_watchdog`（`chmod 600`），内容是 Shiyu 在 GlitchTip 网页上
建好的 Heartbeat 监控地址（一行）。宿主机上只有这一项凭据；发信复用 GlitchTip 容器自己的
`EMAIL_URL` / `DEFAULT_FROM_EMAIL`，宿主机不解析、不复制任何发信凭据。地址留空或文件不存在时，
正常路径只是 ping 不出去（反向心跳失效），会在 syslog 留一行——所以要先建好监控再装 cron。

**crontab**（root，`crontab -e` 加一行）：

```
* * * * * /usr/bin/flock -n /run/glitchtip-watchdog.lock /usr/bin/python3 /opt/glitchtip/watchdog.py 2>&1 | /usr/bin/logger -t glitchtip-watchdog
```

`flock -n` 防叠跑：单次最坏约 131 秒（13 秒重启 + 90 秒确认 + 30 秒发信），随后一两次 cron
直接退出不排队。脚本自己不写锁。输出走 `logger` 进 syslog，由系统轮转——不落自管的增长文件。

**回滚**：`crontab -e` 删掉那一行，保存即可。`/opt/glitchtip/watchdog.py` 与状态文件
`/root/.glitchtip_watchdog_state.json` 留着不生效，也不影响任何东西；要清干净再删这两个文件。

**上线验收**（先建好监控、写好心跳地址文件，再装 cron）：

1. 手动跑一次，把心跳阈值临时压到必触发（**不改文件**）：
   ```
   sudo /usr/bin/python3 /opt/glitchtip/watchdog.py --heartbeat-max-age -1
   ```
   确认：执行了容器级重启、90 秒内心跳值前进、状态文件被写、`bookecho@163.com` 收到
   「已自动重启并恢复」（这一步同时证明发信复用路径可用）。
   > 为什么不是 `0`：判据是「秒龄 ≤ 阈值」，而正常时心跳秒龄在 0/1 之间取整，`0` 有约一半
   > 概率判成正常、不一定触发；`-1` 才能确定性触发。缺键时读到的秒龄是 `-1`，会判成正常。
2. 临时注释掉 crontab 那一行：约 10 分钟内应收到 GlitchTip 的心跳缺席邮件（反向心跳停了），
   然后恢复那一行。
