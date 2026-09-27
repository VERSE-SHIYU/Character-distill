# GlitchTip 看门狗 —— 步骤 13-S0 只读调查结果

> 对应 `docs/specs/observability-glitchtip.md` 的「W 段 / 步骤 13-S0」。
> **基线**：`origin/main` `5f11e57`；分支 `worktree-observability-watchdog`。**执行日期**：2026-09-27（UTC）。
> **除第 7 条外全部只读**：未改任何代码、未改服务器配置。第 7 条在本报告记录的唯一一次 `docker restart` 之后完成，未做任何补救动作。

## 0. 结论速览

1. **反向心跳的邮件链路目前整条不成立**（不是参数问题，是缺三件套）：`uptime_monitor` 0 行、两个 ProjectAlert 都是 `uptime=false`、`alerts_alertrecipient` 0 行。详见第 8 条。
2. **心跳信号建议换一个数**：`uptime-dispatch-checks` 的调度间隔是 **1 秒**，比 `send-alert-notifications` 的 **60 秒**细 60 倍。详见第 3 条。
3. **告警评估窗口是 5 分钟，不是 60 秒。** 停摆超过 5 分钟，早于窗口的事件在重启后**不会补发通知**。详见第 4 条。
4. **`/health` 按配置返回 403，不能作为外部探针地址** —— 影响 C 段「Shiyu 的手动步骤」第 2 条的站点监控探测地址。详见第 12 条（单列一节）。
5. 约束 1–4 的坐标在最新 main 上逐行复核一致，无出入。详见第 1 条。
6. 第 7 条实测：GlitchTip 重启到首页可访问 **13.4 秒**，内存峰值 **230.6 MiB / 384 MiB**，重启后调度器确认恢复。详见第 7 条。

---

## 1. 复核约束 1–4 的坐标（最新 main `5f11e57`）

全部逐行核对，**与 W 段「已查实的约束」完全一致，无需修订**。

**约束 1 —— `deploy/sz/glitchtip/docker-compose.yml`**

| 约束里写的 | 实读 | 结果 |
|---|---|---|
| `:18` 按 digest 钉 6.2.6 | `image: glitchtip/glitchtip@sha256:a3d8eb1b…e0dff6` | ✅ |
| `:22` `SERVER_ROLE=all_in_one` | 同 | ✅ |
| `:24` `VALKEY_URL=` 置空 | 同 | ✅ |
| `:27` 库/角色均为 `glitchtip`，宿主 `postgres` | `postgresql://glitchtip:${…}@postgres:5432/glitchtip` | ✅ |
| `:32` `ALLOWED_HOSTS=errors.bookecho-shiyu.cn` | 同 | ✅ |
| `:36` `EMAIL_URL` 取自 `/opt/glitchtip/.env` | `EMAIL_URL=${EMAIL_URL:?…}` | ✅ |
| `:40-41` `mem_limit 384m` / `mem_reservation 192m` | 同 | ✅ |
| `:47` `restart: unless-stopped` | 同 | ✅ |
| `:57` 外部网络 `character-distill_prod` | `:55-57` `networks.prod` external + 显式 name | ✅ |

**约束 2 —— `deploy/sz/README.md`**

| 写的 | 实读 | 结果 |
|---|---|---|
| `:3` 不参与 build/deploy 流水线 | 「由人工 scp 到深圳主机，**不参与** `build.yml` / `deploy.yml`」 | ✅ |
| `:20-42` 改动后「scp 覆盖 + diff 验证」三步 | `:20` 标题 + 三步命令（scp→`sudo cp`→`diff -u`→`up -d --force-recreate`） | ✅ |
| `:47` `/opt/glitchtip/.env` 不入库、`chmod 600` | 同 | ✅ |

**约束 3 —— 宿主机 cron 先例**

| 写的 | 实读 | 结果 |
|---|---|---|
| `scripts/backup.sh:12-15` | `:13` crontab 示例 `. /root/.backup_key; … backup.sh`；`:15` 「口令存 `/root/.backup_key`（chmod 600），不在 crontab 明文、不在仓库」 | ✅ |
| `DEPLOY.md:233-240` | `:233` 「## 9. 定时备份」小节，`:239` 的 crontab 行 | ✅ |

**约束 4 —— `deploy/sz/nginx/glitchtip.conf`**

| 写的 | 实读 | 结果 |
|---|---|---|
| `:14-15` 443 上的 `errors.bookecho-shiyu.cn` | `:14 listen 443 ssl http2;`、`:15 server_name errors.bookecho-shiyu.cn;` | ✅ |
| `:34-38` 经 docker DNS 转给容器名 `glitchtip` | `:34 resolver 127.0.0.11 valid=30s ipv6=off;`、`:35 set $glitchtip http://glitchtip:8000;`、`:37-38 location / → proxy_pass $glitchtip` | ✅ |

> 约束 1 里的 digest 与本轮容器实际运行的镜像一致（见第 5 条）。

---

## 2. 深圳容器名

```
glitchtip-glitchtip-1            glitchtip/glitchtip:6
character-distill-postgres-1     …/verse-shiyu/postgres:16-alpine
character-distill-app-1          ghcr.io/verse-shiyu/character-distill-app:92d5150…
character-distill-nginx-1        …/character-distill-nginx:92d5150…
character-distill-fail2ban-1     …/verse-shiyu/fail2ban:1.1.0
```

- 看门狗要重启的是 **`glitchtip-glitchtip-1`**（容器级重启；沙箱演练已证明进程级会因 `/tmp/django_vtasks_scheduler.lock` 残留而失败）。
- 只读查询走 **`character-distill-postgres-1`**（主站 PG，两库共用实例）。
- `docker ps` 显示的 tag 是 `glitchtip/glitchtip:6`，既不是 `6.2.6` 也不是 digest —— **不要用 tag 判断运行版本**。

---

## 3. `VTASKS_SCHEDULE` 实读

文件：容器内 `/code/glitchtip/settings.py`

```
:971  UPTIME_CHECK_INTERVAL = 1
:972  ALERT_NOTIFICATION_INTERVAL = env.int("ALERT_NOTIFICATION_INTERVAL", 60)
:973  VTASKS_SCHEDULE = {
:974      "send-alert-notifications": {
:975          "task": "apps.alerts.tasks.process_event_alerts",
:976          "schedule": ALERT_NOTIFICATION_INTERVAL,          # 默认 60 秒
:977      },
:978      "perform-maintenance": {
:979          "task": "glitchtip.tasks.perform_maintenance",
:980          "schedule": crontab(hour=5, minute=0),             # 每天 05:00
:981      },
:982  }
:984  if str(GLITCHTIP_ENABLE_DUCKDB or "").lower() == "true":   # 本机未开
:985      VTASKS_SCHEDULE["promote-spans"] = …                   #   (300 秒)
:990      VTASKS_SCHEDULE["compact-span-chunks"] = …             #   (900 秒)
:996  if GLITCHTIP_ENABLE_UPTIME:
:997      VTASKS_SCHEDULE["uptime-dispatch-checks"] = {
:998          "task": "apps.uptime.tasks.dispatch_checks",
:999          "schedule": UPTIME_CHECK_INTERVAL,                 # 1 秒
:1000     }
```

**结论：**

- W 段「告警调度间隔 60 秒（待 S0 复核）」——**复核通过**：`ALERT_NOTIFICATION_INTERVAL` 默认 60 秒，容器未覆盖该变量（`.env` 只有 `GLITCHTIP_DB_PASSWORD` / `SECRET_KEY` / `EMAIL_URL` / `RESEND_FROM_EMAIL` 四类）。
- **更适合作心跳的任务是 `uptime-dispatch-checks`（1 秒）**：
  - 它只在 `GLITCHTIP_ENABLE_UPTIME` 为真时注册，而本机确实在跑（`db_vtaskmetadata` 里有 `vtasks_last_run:uptime-dispatch-checks` 行，秒龄恒为 0–1）。
  - 1 秒间隔意味着「秒龄 > 60」已是 60 个周期没跑，而 60 秒间隔的任务「秒龄 > 60」只是 1 个周期 —— 同样阈值下前者几乎不可能由抖动触发。
  - 它是**投递任务**，与 `process_event_alerts` 走同一条队列路径，对「web + worker + 调度器都活着」的证明力更强。
- 另一个可选项是 `worker_alive:<worker_id>`（worker 每 5 秒续期）。它额外覆盖 **worker**，但粒度 5 秒、且是「行 + `expires_at`」形态而非「秒龄」形态，脚本里要多一步换算。**建议心跳读 `uptime-dispatch-checks` 的秒龄**，把 `worker_alive` 作为辅助判据。
- `perform-maintenance` 每天 05:00，不适合做心跳。DuckDB 两个任务本机未开。

---

## 4. 停摆期间产生的事件，重启后还会不会触发通知

文件：容器内 `/code/apps/alerts/tasks.py`（`process_event_alerts`，`:30`）

```python
:53   start_time = now - timedelta(minutes=alert.timespan_minutes)
      # Filter by UUIDv7 which encodes timestamp (enables partition pruning)
:62   issueevent__id__gte=start_uuid,
:65   .exclude(notification__project_alert=alert)
:66   .annotate(num_events=Count("issueevent"))
:67   .filter(num_events__gte=quantity_in_timespan)
```

`alert.timespan_minutes` 实读为 **5**（第 8 条的表：两条都是 `quantity=1, timespan_minutes=5`）。

**结论：**

- 评估时**只看最近 `timespan_minutes`（=5 分钟）窗口内**的事件数，且要求 `num_events >= quantity`（=1）。
- **停摆 ≤ 5 分钟**：重启后第一次 `process_event_alerts` 落进窗口，事件照常计数 → **会补发通知**。
- **停摆 > 5 分钟**：停摆早期产生的事件在评估时已滑出窗口 → **那部分通知永久丢失**；只有落在窗口内的会被补发。
- `:65` 的 `.exclude(notification__project_alert=alert)` 保证**同一告警对同一批事件不重复发**，重启后不会重复轰炸。
- `CACHE_IS_VALKEY` 在本机为假（`VALKEY_URL` 置空），`:36-38` 的 ValKey `issue_ids` 过滤分支不生效；`:45` 的 `if issue_ids == []: return` 不会命中。即 DB 后端下该任务每次都扫近期 issue，不存在「因缓存无新 issue 而提前 return」。
- **对看门狗的直接约束**：这 5 分钟窗口就是容忍停摆的上限。W 段现在写的「阈值 300 秒 + cron ≤ 300 秒 → 最迟约 10 分钟」**已经把窗口吃满还超了**，需要拍板收紧。

---

## 5. 第 5 条 SQL 与表名

**表名确认**：

```
public|db_queuedtask      owner=glitchtip
public|db_vtaskmetadata   owner=glitchtip
```

两表都在 `glitchtip` 库的 `public` schema 下，属主 `glitchtip` —— 与 W 段「表名以 S0 实读为准」一致，**脚本里用不带 schema 前缀的表名即可**。

**查询**（免密、只读，在 `character-distill-postgres-1` 上以 `glitchtip` 角色执行）：

```sql
select coalesce((select round(extract(epoch from now()) - value::float)
                 from db_vtaskmetadata where key='vtasks_last_run:send-alert-notifications'), -1) as alert_age_s,
       coalesce((select round(extract(epoch from now()-min(created_at)))
                 from db_queuedtask where status='QUEUED' and (run_after is null or run_after<=now())), 0) as oldest_queued_s,
       coalesce((select round(extract(epoch from now()) - value::float)
                 from db_vtaskmetadata where key='vtasks_last_run:uptime-dispatch-checks'), -1) as uptime_age_s;
```

> 前两列照抄 W 段演练用的那条 SQL，第三列是本轮为第 3 条加的 `uptime-dispatch-checks` 秒龄。

**实跑输出**（2026-09-27T05:16Z，第 7 条重启前的基线之一）：

```
alert_age_s|oldest_queued_s|uptime_age_s
15|0|0
(1 row)
```

**`db_vtaskmetadata` 当前内容**（只列 key 与过期时间，值的形态照录）：

```
vtasks_last_run:perform-maintenance        value=1790485200.75   expires_at=(空)
vtasks_last_run:send-alert-notifications   value=1790486095.90   expires_at=(空)
vtasks_last_run:uptime-dispatch-checks     value=1790486118.13   expires_at=(空)
vtasks_rescue_lock                         value=c9febe9ffb33-60  expires_at=2026-09-27 05:15:38Z
vtasks_scheduler_lock                      value=c9febe9ffb33-60  expires_at=2026-09-27 05:15:33Z
worker_alive:c9febe9ffb33-60               value=c9febe9ffb33-60  expires_at=2026-09-27 05:15:28Z
```

- `worker_id` = `c9febe9ffb33-60`，与 W 段约束 5 里 `worker_alive:<worker_id>` 的形态一致；`expires_at` 比读取时刻晚约 5 秒 → 印证「5 秒续期、TTL = 3×」。
- `vtasks_scheduler_lock` / `vtasks_rescue_lock` 的 `expires_at` 也是「当前时刻 + 15 秒」→ 印证 `acquire_lock(..., ttl=15)` 与 1 秒 tick 续期。

---

## 6. 30 分钟取样（每 5 分钟一次，共 7 次）

取样命令与第 5 条 SQL 完全相同（三列 `alert_age_s | oldest_queued_s | uptime_age_s`），时间戳为 UTC。

| # | 时刻 (UTC) | alert_age_s | oldest_queued_s | uptime_age_s |
|---|---|---|---|---|
| 1 | 05:07:32 | 46 | 0 | 0 |
| 2 | 05:12:37 | 49 | 1 | 1 |
| 3 | 05:17:44 | 53 | 0 | 0 |
| 4 | 05:22:51 | 55 | 1 | 0 |
| 5 | 05:27:57 | 60 | 1 | 0 |
| 6 | 05:33:04 | 3 | 1 | 0 |
| 7 | 05:38:10 | 7 | 0 | 0 |

（取样循环于 05:38:17Z 结束，7/7 样本齐全。）

**最大值（7 个样本全体）**：`alert_age_s = 60`，`oldest_queued_s = 1`，`uptime_age_s = 1`。
**纯基线（重启前的样本 1–5）**：`alert_age_s = 60`，`oldest_queued_s = 1`，`uptime_age_s = 1` —— 与全体一致，样本 6–7 未抬高任何最大值。

**余量评估：**

- `alert_age_s`：`send-alert-notifications` 每 60 秒跑一次，秒龄在 **[0, 60]** 锯齿波动；7 个样本里摸到 **60**（样本 5，采样点刚好落在周期末端）。即 **300 秒阈值 = 5 个周期，余量 5 倍** —— 抖动最多摸到 60，离 300 还差 240 秒。
- `oldest_queued_s`：本机几乎不积压（观测 0/1），**600 秒阈值对日常负载余量极大**，只在真正卡住时触发，不会被正常抖动误报。
- `uptime_age_s`：恒为 0–1，印证第 3 条 —— **用 1 秒任务做心跳，阈值可以设得比 300 秒紧得多**（例如 30–60 秒）而仍不会误报。
- **重启不留下抬高的秒龄**：重启后的样本 6、7 分别是 `3` 和 `7`，回到正常锯齿内 —— 一次 `docker restart` 不会让任何秒龄持续偏高（与第 7 条一致）。

> **取样窗口跨越了第 7 条的重启**（重启发生于 05:28:19）。样本 1–5 是纯基线，样本 6–7 在重启之后。两者的最大值一致，故合并统计；若将来要用这段数据做基线，样本 1–5 是更干净的那一半。

---

## 7. `docker restart` 实测（2026-09-27T05:28:19Z 起，只此一次）

**执行前**（05:28:19Z）：

| 检查 | 结果 |
|---|---|
| 主站 `/` | **200** |
| app 容器内 `/api/health` | **200** |
| GlitchTip 首页 | 200 |
| 第 5 条 SQL | `16\|0\|0` |

**执行**：`sudo docker restart glitchtip-glitchtip-1`（只此一条命令，未用 compose）。命令自身在 **+1230 ms** 返回；容器 `StartedAt = 2026-09-27T05:28:21.200416382Z`。

**计时结果**：

| 指标 | 实测 |
|---|---|
| GlitchTip 首页恢复 200 | **+13 437 ms（约 13.4 秒）** |
| 内存峰值（cgroup v2 `memory.peak`） | **241 831 936 B = 230.6 MiB** |
| 内存上限（`memory.max`，即 `mem_limit: 384m`） | 402 653 184 B = 384 MiB |
| 峰值占上限 | **60.1%**（余量约 153 MiB） |
| 读峰值时的 `memory.current` | 241 369 088 B = 230.2 MiB |
| 主站 `/`（重启后） | **200** |
| app 容器内 `/api/health`（重启后） | **200** |
| 重启后第 5 条 SQL | `5\|0\|0`（05:29:09Z） |
| 容器状态 | `running`，`RestartCount=0` |

**调度器是否恢复 —— 用「时间戳是否前进」判定，不用「秒龄是否够小」**：

```
uptime-dispatch-checks value  t0     = 1790486943.199849
uptime-dispatch-checks value  t0+6s  = 1790486949.262098
→ 前进 6.06 秒 / 6 秒 → 调度器在重启后确实在跑
```

> **这里有一条需要写明的纠正**：轮询脚本最初用的判据是「两个秒龄 < 60 秒」，它在 **+1407 ms** 就「通过」了（读到 `18|0|1`）—— 但那是**重启前的**时间戳，本来就还没过 60 秒，**这个判据证明不了任何恢复**。真正的证据是上面的「值前进」测试。两者都记在这里，避免把假绿当结论。

**两个附带读数及其口径**：

- `docker stats` 的逐秒采样在这次窗口里只有 8 行、峰值 216.2 MiB —— 采样起止只覆盖约 17 秒，且窗口末尾数值仍在上升，**这个数偏低、不足以当峰值**。上表的 230.6 MiB 来自 cgroup v2 的 `memory.peak`（自本次容器启动以来的高水位，覆盖所有时刻，不依赖采样），是权威值。
- `RestartCount=0` —— 手动 `docker restart` 不计入 `RestartCount`（它只统计 `restart` 策略触发的重启）。**看门狗不能用这个字段判断自己重启过**，要自己写状态文件。

**对 W 段参数的直接影响**：

- **13.4 秒 ≪ 5 分钟** → 约束 6 表里「GlitchTip 重启耗时须明显短于 5 分钟，否则阿里云站点监控会误报」，**实测通过**，一次重启不会触发站点监控误报。
- 反向心跳监控的 `interval`（宽限期，见第 8 条）只需覆盖「cron 间隔 5 分钟 + 一次重启 13.4 秒 + 抖动」→ **≥ 6 分钟即够，建议 10–15 分钟留余量**。
- 内存 230.6 MiB / 384 MiB，**余量约 153 MiB**；`mem_reservation: 192m` 会被峰值超过（保留值是软保证，不构成问题）。看门狗重启不会把容器推到 limit。

---

## 8. GlitchTip Heartbeat 监控的语义与前提

**DOWN 判定**（容器内 `/code/apps/uptime/utils.py:70-87`）：

```python
:70  async def fetch(session, monitor):
:71      monitor["is_up"] = False
:72      if monitor["monitor_type"] == MonitorType.HEARTBEAT:
:73          interval = timedelta(seconds=monitor["interval"])
:74          since = timezone.now() - interval
:85          if await MonitorCheck.objects.filter(…, id__gte=_uuid7_for_timestamp(since…)).aexists():
:86              monitor["is_up"] = True
:87          return monitor
```

**结论：宽限期就是监控自己的 `interval`，没有单独的宽限字段。** 规则是「最近 `interval` 秒内有没有 `MonitorCheck` 行」，没有即 DOWN。

**心跳地址格式**（`/code/apps/uptime/api.py:189-215`）：

```
:189  "organizations/{slug:organization_slug}/heartbeat_check/{uuid:endpoint_id}/"
:190  response=MonitorCheckSchema,
:191  auth=None,          # ← 不需要认证头
```

- 形式为 `https://errors.bookecho-shiyu.cn/api/0/organizations/<org_slug>/heartbeat_check/<endpoint_id>/`（`api/0` 前缀以网页上给出的完整地址为准）。
- `auth=None`：**URL 里的不可猜 UUID 就是凭据**，看门狗不带 token 也能 POST。因此必须按「凭据」对待（W 段已定：存 `/root/.glitchtip_watchdog`，`chmod 600`，比对只打 sha256 前 12 位）。
- 评估又回到 GlitchTip 自己的 `uptime-dispatch-checks`（1 秒）任务 —— 所以 **GlitchTip 调度器一旦停摆，它自己的心跳告警也不会发**。这正是 W 段设计「反向心跳」的动机。

**超时走不走邮件 —— 走，但需要三件套齐全，目前一件都没有：**

邮件派发路径（`/code/apps/uptime/tasks.py:264-288`）：

```python
:273  recipients = AlertRecipient.objects.filter(
:274      alert__project__monitor__id=monitor_id, alert__uptime=True
:275  )
:277  if recipient.recipient_type == RecipientType.EMAIL:
:279      MonitorEmail(pk=…, went_down=went_down, …).send_users_email
```

收件人再经 `/code/apps/users/models.py:51-58` 的 `uptime_monitor_recipients(monitor)` 过滤（用户在监控所属项目的团队里、且没关掉该项目告警）。

**当前实读（`glitchtip` 库）：**

```
users                                = 1        (bookecho@163.com, subscribe_by_default = true)
organizations                        = 1        (bookecho)
projects                             = 2        (1=backend, 2=frontend)
teams / team_members / team_projects = 1 / 1 / 2  (团队 bookecho 拥有 project 1 和 2)
userprojectalert                     = 0
uptime_monitor                       = 0        ← 一个监控都没有
alerts_projectalert                  = 2        (id 1→project 1, id 2→project 2)
alerts_projectalert.uptime=true      = 0        ← 没有任何 uptime 告警
alerts_alertrecipient                = 0        ← 没有任何告警收件人
```

**结论：心跳 DOWN 的邮件目前发不出去** —— `tasks.py:274` 的 `recipients` 查询返回空集，`for` 循环体一次都不执行，**静默无输出**（没有日志、没有回退）。

**好在收件人本身合格**：`bookecho@163.com` 的 `subscribe_by_default=true`，所在团队 `bookecho` 拥有 project 1 和 2 —— 只要监控建在这两个项目之一，`uptime_monitor_recipients` 就能选中它。

**步骤 13 落地前必须在网页上补齐的三件（建议 Shiyu 操作）：**

1. 在 project `backend`（或 `frontend`）下新建一个 **Heartbeat** 类型的监控；
2. 为该项目新建/启用一个 `uptime = true` 的告警（`AlertRecipient` 类型选 email），收件人填 `bookecho@163.com`；
3. 从网页上抄下完整心跳 URL（含 `endpoint_id`），交给我放进 `/root/.glitchtip_watchdog`。

**监控间隔怎么设**：宽限期 == `interval`，`interval` 要 ≥ cron 间隔（5 分钟）+ 一次重启（实测 13.4 秒）+ 抖动。**建议 10–15 分钟**；不要低于 6 分钟，否则 GlitchTip 自己重启一次就可能误报 DOWN。

---

## 9. 深圳到 Resend 的线路

- **`EMAIL_URL` 的类型：SMTP（隐式 TLS）**。从容器内解析（**只打 scheme/host/port，不打印口令**）：

  ```
  scheme=smtp+ssl host=smtp.resend.com port=465
  ```

- **SMTP 线路（宿主机直连）**：

  ```
  CONNECTION ESTABLISHED
  Protocol version: TLSv1.3
  Ciphersuite: TLS_AES_256_GCM_SHA384
  Peer certificate: CN = *.resend.com
  Verification: OK
  ```

  → 到 `smtp.resend.com:465` 的 TLS 握手正常、证书校验通过。

- **HTTP API 线路**：

  ```
  api.resend.com http_code=200
  ```

  → 443 出网正常。

**结论**：宿主机具备发信能力。看门狗用 **Resend HTTP API**（`curl` 一次 POST）还是复用 **SMTP**，取决于 `/root/.glitchtip_watchdog` 里存什么凭据 —— 两条线路都通，是个待拍板的实现细节。

---

## 10. 本机 nginx 与 cron

- **经本机 nginx 访问 GlitchTip**：

  ```
  curl --resolve errors.bookecho-shiyu.cn:443:127.0.0.1 -so /dev/null -w '%{http_code} %{time_total}'
  → 200 0.038483
  ```

  → 反代链路正常，38 ms。

- **cron 在跑**：

  ```
  systemctl is-active cron   → active
  crontab -l 非注释行数      → 1        （只有备份那一行；内容按指令不贴）
  ```

  → cron 已启用、在运行，目前只有 1 条非注释任务。

**结论**：看门狗要挂的 cron 环境是干净的。备份 03:00 跑、看门狗每 5 分钟跑，03:00 那一刻会重叠，但备份是 `pg_dump` 全库、看门狗只是一条只读查询，**重叠不影响正确性**，只是那一分钟查询会稍慢。

---

## 11. 对 W 段「待 S0 定」参数的建议

> 参数最终由 Shiyu 拍板；下表是 S0 实读支持的建议值。

| 参数 | W 段原值 | S0 后的建议 | 依据 |
|---|---|---|---|
| 心跳读哪个数 | `send-alert-notifications`（60 s） | **`uptime-dispatch-checks`（1 s）** | 第 3 条 |
| 心跳超时阈值 | 300 秒 | **30–60 秒** | 第 6 条：1 秒任务的秒龄恒为 0–1 |
| 队列积压阈值 | 600 秒 | **维持 600 秒** | 第 6 条：日常积压恒为 0，余量极大、不会误报 |
| cron 间隔 | 5 分钟 | **维持 5 分钟**（或缩到 1–2 分钟） | 第 4 条：5 分钟窗口是通知不丢的上限 |
| 检测→重启的最迟延迟 | 约 10 分钟 | **必须 < 5 分钟** | 第 4 条：超过 5 分钟，停摆早期的通知永久丢失 |
| 冷却期 | 1 小时 | **维持 1 小时** | 第 7 条：重启仅 13.4 秒、内存余量 153 MiB，代价低 |
| 心跳监控 `interval` | 未定 | **10–15 分钟**（下限 6 分钟） | 第 7 条 13.4 秒 + 第 8 条宽限期 == `interval` |
| 邮件收件人 | `bookecho@163.com` | **维持**，但需先建三件套 | 第 8 条：目前 0 监控 / 0 uptime 告警 / 0 收件人 |

**未决（等 Shiyu）：**

1. 第 12 条的 `/health` 403 怎么处理（**本报告只记录、不给改法**）；
2. 第 8 条的三件套由 Shiyu 在网页上建（**不得动 Shiyu 账户**；`bookecho` 组织下只有 `bookecho@163.com` 一个用户）；
3. 上表参数拍板后进入步骤 13 实现。

---

## 12. `/health` 返回 403（单列一节，只记录）

**现象**：`https://bookecho-shiyu.cn/health` 返回 **403**，而 `/` 返回 200。不是故障，是配置如此。

**规则位置** —— 仓库 `nginx/nginx.conf`，与深圳实际运行的容器内 `/etc/nginx/nginx.conf` 逐行一致：

```
nginx/nginx.conf:92          location /health {
nginx/nginx.conf:93              allow 127.0.0.1;
nginx/nginx.conf:94              deny all;
nginx/nginx.conf:95              proxy_pass http://app:7860/api/health;
nginx/nginx.conf:96          }
```

同一文件里还有两处同形态的白名单：

```
nginx/nginx.conf:103-105     location = /api/health/ready { allow 127.0.0.1; deny all; … }
nginx/nginx.conf:116-118     （对端节点）allow 43.134.55.201; allow 127.0.0.1; deny all;
```

**实读结果**：

| 请求 | 结果 |
|---|---|
| `https://bookecho-shiyu.cn/`（公网） | **200** |
| `https://bookecho-shiyu.cn/health`（公网） | **403** |
| `https://bookecho-shiyu.cn/health`（浏览器 UA） | **403** |
| `https://bookecho-shiyu.cn/health`（宿主机经 `--resolve …:127.0.0.1`） | **403** |
| app 容器内 `http://127.0.0.1:7860/api/health` | **200 `{"status":"ok"}`** |

403 响应头为 `server: openresty`、`content-type: text/html`、`content-length: 150` —— 是 nginx 自己生成的 403 页，不是应用层或 WAF。

**为什么宿主机经 `127.0.0.1` 也是 403**：nginx 容器运行在桥接网络 `character-distill_prod` 上、把 443 发布到宿主机（`NetworkMode=character-distill_prod`，`443/tcp → 0.0.0.0:443`）。从宿主机发起、目标 `127.0.0.1:443` 的请求，进到容器里源地址是**桥接网关**（172.x），永远不是 `127.0.0.1`，因此命不中 `allow 127.0.0.1`，落到 `deny all`。所以这条白名单实际只对「容器内部发起」的调用生效，以及显式列出的新加坡对端 `43.134.55.201`。

**它影响什么** —— C 段「Shiyu 的手动步骤」第 2 条的站点监控探测地址：

- `docs/specs/observability-glitchtip.md:409` 「### Shiyu 的手动步骤（C1 之后做，阿里云控制台，不涉及服务器）」
- `docs/specs/observability-glitchtip.md:412` 「2. `https://bookecho-shiyu.cn/health`：主站（nginx 把 `/health` 转发到 `/api/health`，`nginx/nginx.conf:92`）」

该探测由阿里云国内探测点从公网发起，源地址既不是 `127.0.0.1` 也不在 `allow` 名单内，**必然得到 403**；按阿里云站点监控的判定，该任务会一直处于失败状态。同一手动步骤里的第 1 条探测（`https://errors.bookecho-shiyu.cn/`）不受影响，实测 200。

W 段也依赖这条探测：`docs/specs/observability-glitchtip.md:455`（「4. **整机宕机**：阿里云站点监控，通知改邮件」）与 `:504`（「PG 挂了主站也挂，由站点监控报警」）。

**本节只记录现象、文件与行号，不含改法。**
