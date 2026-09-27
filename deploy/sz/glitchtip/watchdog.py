#!/usr/bin/env python3
"""GlitchTip 宿主机看门狗：调度器停摆就重启容器，正常就回一个反向心跳。

每分钟一次（crontab 行见 ``deploy/sz/README.md``）。判定与动作分离：
``decide()`` 是纯函数，不碰 IO；读数、重启、ping、发信四个动作走一个可注入的
执行器，``main()`` 只负责把它们接起来。

只依赖标准库，以及宿主机上的 ``docker`` 与 ``curl``。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import namedtuple

# 心跳取 ``uptime-dispatch-checks``（1 秒调度）的秒龄：调度器一旦停摆它立刻变旧。
# 队列取最老「可执行」QUEUED 任务的秒龄：任务堆积说明 worker 不动了。
HEARTBEAT_MAX_AGE = 60
QUEUE_MAX_AGE = 600

# 1 小时内最多自动重启 1 次；所有通知共用 1 小时最多 1 封的限流。
RESTART_COOLDOWN = 3600
NOTIFY_COOLDOWN = 3600

# 重启后原地确认：最多等 90 秒、每 5 秒读一次心跳原值。实测首页 13.4 秒恢复、
# 调度锁 ttl 15 秒，90 秒留约 3 倍余量。
CONFIRM_TIMEOUT = 90
CONFIRM_STEP = 5

ACTION_PING = "ping"
ACTION_RESTART = "restart"
ACTION_NOTIFY = "notify"
ACTION_NONE = "none"

MSG_PG_DOWN = "读不到调度器心跳（PG 不可用或记录缺失）"
MSG_STILL_ABNORMAL = "重启后仍异常"
MSG_RECOVERED = "已自动重启并恢复"
MSG_NOT_RECOVERED = "重启未恢复"

# 读数三元组，顺带带上心跳原值——重启后凭「值有没有前进」判定恢复，
# 看秒龄会误判（重启前那次读到的旧值还没老化）。
Reading = namedtuple("Reading", "heartbeat_age queue_age heartbeat_value")

SUBJECT = "GlitchTip 看门狗"
MAIL_TO = "bookecho@163.com"

# 落盘与凭据：状态只存两个时间戳；心跳地址单独一个 600 权限文件（仿 /root/.backup_key）。
STATE_PATH = "/root/.glitchtip_watchdog_state.json"
HEARTBEAT_URL_PATH = "/root/.glitchtip_watchdog"

PG_CONTAINER = "character-distill-postgres-1"
GLITCHTIP_CONTAINER = "glitchtip-glitchtip-1"
MANAGE_PY = "/code/manage.py"

PG_TIMEOUT = 15
RESTART_TIMEOUT = 60
MAIL_TIMEOUT = 30
PING_TIMEOUT = 10  # curl --max-time 10

# 只读：心跳秒龄 | 最老可执行 QUEUED 秒龄 | 心跳原值。
# 心跳取 1 秒调度的 uptime-dispatch-checks——调度器一停摆它立刻变旧。
SQL = (
    "select "
    "coalesce((select round(extract(epoch from now()) - value::float) "
    "from db_vtaskmetadata where key='vtasks_last_run:uptime-dispatch-checks'), -1), "
    "coalesce((select round(extract(epoch from now() - min(created_at))) "
    "from db_queuedtask where status='QUEUED' "
    "and (run_after is null or run_after <= now())), 0), "
    "coalesce((select value::float from db_vtaskmetadata "
    "where key='vtasks_last_run:uptime-dispatch-checks'), 0)"
)


def _notify(state, now, message):
    """通知一律先过 1 小时限流：窗口内返回「什么都不做」。"""
    last = state.get("last_notify")
    if last is not None and (now - last) < NOTIFY_COOLDOWN:
        return (ACTION_NONE, None)
    return (ACTION_NOTIFY, message)


def decide(
    reading,
    state,
    now,
    heartbeat_max_age=HEARTBEAT_MAX_AGE,
    queue_max_age=QUEUE_MAX_AGE,
):
    """纯函数：给定读数、状态与当前时间，返回 ``(动作, 消息)``，不碰 IO。

    ``reading`` 为 None 表示读数失败，否则是 ``Reading``。
    动作是 ``ACTION_*`` 之一；只有 ``ACTION_NOTIFY`` 带消息。
    阈值可覆盖，供上线验收时临时把心跳阈值压到必触发的值。
    """
    if reading is None:
        return _notify(state, now, MSG_PG_DOWN)
    if (
        reading.heartbeat_age <= heartbeat_max_age
        and reading.queue_age <= queue_max_age
    ):
        return (ACTION_PING, None)
    last = state.get("last_restart")
    if last is not None and (now - last) < RESTART_COOLDOWN:
        message = "%s：心跳 %s 秒、队列 %s 秒" % (
            MSG_STILL_ABNORMAL,
            reading.heartbeat_age,
            reading.queue_age,
        )
        return _notify(state, now, message)
    return (ACTION_RESTART, None)


def load_state(path):
    """状态只有两个时间戳（上次自动重启、上次发信）；读不出来就当空状态。"""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def heartbeat_value(executor):
    """读一次心跳原值；读失败返回 None，确认阶段跳过这一次读。"""
    reading = executor.read()
    return None if reading is None else reading.heartbeat_value


def await_recovery(read_value, baseline, now_fn, sleep_fn,
                   timeout=CONFIRM_TIMEOUT, step=CONFIRM_STEP):
    """重启后每 step 秒读一次心跳原值，最多等 timeout 秒；值前进即恢复。

    时钟与睡眠都注入，用例里不真睡。判「值前进」而不是「秒龄变小」——
    重启前读到的旧值还没老化，看秒龄会在重启刚落地时假绿。
    """
    deadline = now_fn() + timeout
    while now_fn() < deadline:
        sleep_fn(step)
        value = read_value()
        if value is not None and value > baseline:
            return True
    return False


def handle_restart(executor, reading, state, now, now_fn, sleep_fn, save):
    """重启一次并在同一次运行里原地确认，返回 ``(是否恢复, 发出的消息或 None)``。

    先落盘重启时间再等确认：万一确认期间机器挂了，下一次运行仍受 1 小时重启上限约束，
    不会立刻再来一次。
    """
    executor.restart()
    state["last_restart"] = now
    save(state)

    recovered = await_recovery(
        lambda: heartbeat_value(executor), reading.heartbeat_value, now_fn, sleep_fn
    )
    message = MSG_RECOVERED if recovered else MSG_NOT_RECOVERED
    action, notice = _notify(state, now_fn(), message)
    sent = False
    if action == ACTION_NOTIFY:
        sent = executor.notify(SUBJECT, notice)
        if sent:
            state["last_notify"] = now_fn()
            save(state)
    return recovered, notice if sent else None


class Executor:
    """真实执行器：全部外部动作都经它，测试用一个假的替换掉。

    四个动作：读数（只读 SQL）、重启容器、ping 反向心跳、进容器发信。
    发信复用 GlitchTip 自己的 Django 邮件配置——Django 发信不连库，PG 挂了也能发。
    """

    def read(self):
        command = [
            "docker", "exec", PG_CONTAINER, "psql",
            "-w", "-U", "glitchtip", "-d", "glitchtip", "-Atc", SQL,
        ]
        try:
            done = subprocess.run(
                command, capture_output=True, text=True, timeout=PG_TIMEOUT
            )
        except subprocess.SubprocessError:
            return None
        if done.returncode != 0:
            return None
        parts = done.stdout.strip().split("|")
        if len(parts) != 3:
            return None
        try:
            reading = Reading(int(parts[0]), int(parts[1]), float(parts[2]))
        except ValueError:
            return None
        # 心跳记录缺失时 SQL 的 coalesce 兜底是 -1；那是「读不到」，不是「很健康」。
        # 当成正常会让看门狗照常 ping、永远不报警——静默失明。走读数失败路径。
        if reading.heartbeat_age < 0:
            return None
        return reading

    def _run(self, argv, timeout, what):
        """跑一条外部命令；失败只报到 syslog，绝不回显子进程的 stderr。

        子进程 stderr 可能带出发信配置里的连接串，而 cron 的输出会进 syslog，
        所以这里只报动作名与退出码。
        """
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.SubprocessError as exc:
            print("%s 未能执行：%s" % (what, exc.__class__.__name__), file=sys.stderr)
            return None
        if done.returncode != 0:
            print("%s 失败，退出码 %s" % (what, done.returncode), file=sys.stderr)
        return done

    def restart(self):
        self._run(
            ["docker", "restart", GLITCHTIP_CONTAINER],
            RESTART_TIMEOUT, "重启 GlitchTip",
        )

    def ping(self, url):
        self._run(
            ["curl", "--max-time", str(PING_TIMEOUT), "-fsS", "-o", os.devnull, url],
            PING_TIMEOUT + 5, "反向心跳",
        )

    def notify(self, subject, body):
        """发信；返回是否成功——失败就不许记 ``last_notify``，留给下一次重试。"""
        snippet = (
            "import os;from django.core.mail import send_mail;"
            "send_mail(os.environ['WD_SUBJECT'], os.environ['WD_BODY'], None, "
            "[os.environ['WD_TO']])"
        )
        done = self._run(
            [
                "docker", "exec",
                "-e", "WD_SUBJECT=" + subject,
                "-e", "WD_BODY=" + body,
                "-e", "WD_TO=" + MAIL_TO,
                GLITCHTIP_CONTAINER, "python", MANAGE_PY, "shell", "-c", snippet,
            ],
            MAIL_TIMEOUT, "发信",
        )
        return done is not None and done.returncode == 0


def read_heartbeat_url():
    try:
        with open(HEARTBEAT_URL_PATH, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def parse_args(argv):
    parser = argparse.ArgumentParser(description="GlitchTip 宿主机看门狗")
    parser.add_argument(
        "--heartbeat-max-age",
        type=int,
        default=HEARTBEAT_MAX_AGE,
        help="心跳秒龄阈值（秒），上线验收时临时覆盖用，不改文件",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    now = time.time()
    state = load_state(STATE_PATH)
    executor = Executor()
    reading = executor.read()
    action, message = decide(
        reading, state, now, heartbeat_max_age=args.heartbeat_max_age
    )

    if action == ACTION_PING:
        url = read_heartbeat_url()
        if url:
            executor.ping(url)
        else:
            print(
                "心跳地址文件读不到：%s（反向心跳发不出去）" % HEARTBEAT_URL_PATH,
                file=sys.stderr,
            )
    elif action == ACTION_NOTIFY:
        if executor.notify(SUBJECT, message):
            state["last_notify"] = now
            save_state(STATE_PATH, state)
    elif action == ACTION_RESTART:
        handle_restart(
            executor, reading, state, now, time.time, time.sleep,
            lambda snapshot: save_state(STATE_PATH, snapshot),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
