"""W 段看门狗：判定、限流、重启确认（假读数、假状态、注入时钟）。

被测对象 ``deploy/sz/glitchtip/watchdog.py`` 是宿主机 cron 每分钟跑的独立脚本：
只做一条只读查询，全部外部动作走一个可注入的执行器。所以这里不连 PG、不碰 docker，
读数、时钟、执行器都是假的。

时间关系按比例复刻真实值：心跳阈值 60 秒、队列阈值 600 秒，用例取阈值的
0.5 / 1 / 5 倍；重启与通知各自 1 小时的窗口取窗口内、窗口外两侧。
"""

import importlib.util
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "deploy" / "sz" / "glitchtip" / "watchdog.py"
)
_spec = importlib.util.spec_from_file_location("glitchtip_watchdog", _MODULE_PATH)
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)

NOW = 1_800_000_000.0
HB = wd.HEARTBEAT_MAX_AGE  # 60
QUEUE = wd.QUEUE_MAX_AGE  # 600


def reading(hb_age, queue_age, value=NOW):
    return wd.Reading(
        heartbeat_age=hb_age, queue_age=queue_age, heartbeat_value=value
    )


def fresh():
    return {}


# ── 阈值：心跳秒龄 ───────────────────────────────────────────────────────────


def test_heartbeat_half_threshold_is_healthy():
    assert wd.decide(reading(HB // 2, 0), fresh(), NOW) == (wd.ACTION_PING, None)


def test_heartbeat_exactly_at_threshold_is_healthy():
    # 判据是「秒龄 > 阈值」，等于阈值不算异常（边界取闭区间下侧）
    assert wd.decide(reading(HB, 0), fresh(), NOW) == (wd.ACTION_PING, None)


def test_heartbeat_five_times_threshold_restarts():
    assert wd.decide(reading(HB * 5, 0), fresh(), NOW)[0] == wd.ACTION_RESTART


# ── 阈值：队列秒龄 ───────────────────────────────────────────────────────────


def test_queue_half_threshold_is_healthy():
    assert wd.decide(reading(0, QUEUE // 2), fresh(), NOW) == (wd.ACTION_PING, None)


def test_queue_exactly_at_threshold_is_healthy():
    assert wd.decide(reading(0, QUEUE), fresh(), NOW) == (wd.ACTION_PING, None)


def test_queue_five_times_threshold_restarts():
    assert wd.decide(reading(0, QUEUE * 5), fresh(), NOW)[0] == wd.ACTION_RESTART


# ── 自动重启上限：1 小时 1 次 ────────────────────────────────────────────────


def test_abnormal_inside_restart_window_notifies_only():
    state = {"last_restart": NOW - wd.RESTART_COOLDOWN / 2}
    action, msg = wd.decide(reading(HB * 5, 0), state, NOW)
    assert action == wd.ACTION_NOTIFY
    assert "仍异常" in msg


def test_abnormal_outside_restart_window_restarts():
    state = {"last_restart": NOW - wd.RESTART_COOLDOWN * 2}
    assert wd.decide(reading(HB * 5, 0), state, NOW) == (wd.ACTION_RESTART, None)


# ── 通知限流：1 小时最多 1 封 ────────────────────────────────────────────────


def test_notify_inside_throttle_window_is_suppressed():
    state = {"last_notify": NOW - wd.NOTIFY_COOLDOWN / 2}
    assert wd.decide(None, state, NOW) == (wd.ACTION_NONE, None)


def test_notify_outside_throttle_window_fires():
    state = {"last_notify": NOW - wd.NOTIFY_COOLDOWN * 2}
    action, msg = wd.decide(None, state, NOW)
    assert action == wd.ACTION_NOTIFY
    assert "PG" in msg


def test_throttle_also_clamps_still_abnormal_notice():
    state = {
        "last_restart": NOW - wd.RESTART_COOLDOWN / 2,
        "last_notify": NOW - wd.NOTIFY_COOLDOWN / 2,
    }
    assert wd.decide(reading(HB * 5, 0), state, NOW) == (wd.ACTION_NONE, None)


# ── 读数失败 ─────────────────────────────────────────────────────────────────


def test_read_failure_notifies_pg_unavailable():
    action, msg = wd.decide(None, fresh(), NOW)
    assert action == wd.ACTION_NOTIFY
    assert "PG" in msg


# ── 重启后的原地确认 ─────────────────────────────────────────────────────────


class Clock:
    """注入的时钟：now 只读，sleep 推进——用例不会真睡 90 秒。"""

    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class FakeExecutor:
    """假执行器：只记下重启与发信，心跳读数按脚本依次吐出。"""

    def __init__(self, values=()):
        self.values = list(values)
        self.restarts = 0
        self.notices = []
        self.pings = []

    def restart(self):
        self.restarts += 1

    def notify(self, subject, body):
        self.notices.append((subject, body))

    def ping(self, url):
        self.pings.append(url)

    def read(self):
        if not self.values:
            return None
        return wd.Reading(0, 0, self.values.pop(0))


def test_await_recovery_true_when_value_advances():
    clock = Clock(NOW)
    seen = iter([NOW, NOW + 3])  # 第一次还没前进，第二次前进
    assert (
        wd.await_recovery(lambda: next(seen), NOW, clock.now, clock.sleep) is True
    )


def test_await_recovery_skips_failed_reads():
    clock = Clock(NOW)
    seen = iter([None, None, NOW + 2])
    assert (
        wd.await_recovery(lambda: next(seen), NOW, clock.now, clock.sleep) is True
    )


def test_await_recovery_false_after_timeout():
    clock = Clock(NOW)
    assert (
        wd.await_recovery(lambda: NOW, NOW, clock.now, clock.sleep) is False
    )
    assert clock.t - NOW >= wd.CONFIRM_TIMEOUT  # 等满了窗口才判负


def test_handle_restart_recovers_and_writes_state():
    clock = Clock(NOW)
    ex = FakeExecutor([NOW + 1])
    state = {}
    saved = []
    recovered, msg = wd.handle_restart(
        ex, wd.Reading(300, 0, NOW), state, NOW, clock.now, clock.sleep, saved.append
    )
    assert recovered is True
    assert msg == wd.MSG_RECOVERED
    assert ex.restarts == 1
    assert ex.notices[-1][1] == wd.MSG_RECOVERED
    assert state["last_restart"] == NOW
    assert state["last_notify"] == clock.t
    assert saved  # 状态落了盘（重启时间 + 发信时间）


def test_handle_restart_not_recovered_still_writes_state():
    clock = Clock(NOW)
    ex = FakeExecutor([NOW] * 30)  # 90 秒内始终不前进
    state = {}
    saved = []
    recovered, msg = wd.handle_restart(
        ex, wd.Reading(300, 0, NOW), state, NOW, clock.now, clock.sleep, saved.append
    )
    assert recovered is False
    assert msg == wd.MSG_NOT_RECOVERED
    assert ex.restarts == 1
    assert ex.notices[-1][1] == wd.MSG_NOT_RECOVERED
    assert state["last_restart"] == NOW  # 未恢复也要记住重启过
    assert saved


def test_handle_restart_still_restarts_when_notice_is_throttled():
    clock = Clock(NOW)
    ex = FakeExecutor([NOW + 1])
    state = {"last_notify": NOW - wd.NOTIFY_COOLDOWN / 2}
    saved = []
    recovered, msg = wd.handle_restart(
        ex, wd.Reading(300, 0, NOW), state, NOW, clock.now, clock.sleep, saved.append
    )
    assert recovered is True
    assert msg is None  # 限流窗口内不发信
    assert ex.notices == []
    assert state["last_restart"] == NOW
