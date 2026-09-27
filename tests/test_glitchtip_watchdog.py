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


def test_read_treats_missing_heartbeat_record_as_failure(monkeypatch):
    """心跳记录缺失（SQL 的 coalesce 兜底 -1）不能当成正常——否则看门狗静默失明。

    上游一旦改键名/表名，秒龄就是 -1；当成正常会让它照常 ping、永远不报警。
    走的是既有的读数失败路径，不新增分支。
    """

    class _Done:
        returncode = 0
        stdout = "-1|0|1790489099.6"

    monkeypatch.setattr(wd.subprocess, "run", lambda *a, **k: _Done())
    assert wd.Executor().read() is None


def test_read_parses_healthy_reading(monkeypatch):
    """正控：记录还在时照常解析，别把整个 read() 判死。"""

    class _Done:
        returncode = 0
        stdout = "1|0|1790489099.6"

    monkeypatch.setattr(wd.subprocess, "run", lambda *a, **k: _Done())
    assert wd.Executor().read() == wd.Reading(1, 0, 1790489099.6)


# ── 反向心跳必须是 POST ──────────────────────────────────────────────────────


def test_ping_posts_to_the_heartbeat_url(monkeypatch):
    """GlitchTip 的心跳端点只收 POST，GET 会 405（线上实测），所以 ping 必须 POST。"""
    seen = {}

    class _Done:
        returncode = 0

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        return _Done()

    monkeypatch.setattr(wd.subprocess, "run", fake_run)
    wd.Executor().ping("https://example.invalid/hb/")

    assert "POST" in seen["argv"]
    assert seen["argv"][-1] == "https://example.invalid/hb/"


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
    """假执行器：只记下重启与发信，心跳读数按脚本依次吐出。

    ``notify_ok`` 控制发信是否成功——失败时调用方不能记 ``last_notify``。
    """

    def __init__(self, values=(), notify_ok=True):
        self.values = list(values)
        self.notify_ok = notify_ok
        self.restarts = 0
        self.notices = []
        self.pings = []

    def restart(self):
        self.restarts += 1

    def notify(self, subject, body):
        self.notices.append((subject, body))
        return self.notify_ok

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


# ── 发信失败不记账：失败必须能在下一次运行重试 ───────────────────────────────


def test_handle_restart_does_not_record_notify_when_send_fails():
    """发信失败不能写 last_notify——写了，1 小时限流会把下一次补发一起吞掉。"""
    clock = Clock(NOW)
    ex = FakeExecutor([NOW + 1], notify_ok=False)
    state = {}
    saved = []
    recovered, msg = wd.handle_restart(
        ex, wd.Reading(300, 0, NOW), state, NOW, clock.now, clock.sleep, saved.append
    )
    assert recovered is True
    assert msg is None  # 没发出去，就不算发过
    assert "last_notify" not in state
    assert state["last_restart"] == NOW  # 重启本身仍要记账


def test_main_retries_notify_after_send_failure(monkeypatch):
    """发信失败后重跑：不写 last_notify，所以下一次运行还会再发。"""
    ex = FakeExecutor(notify_ok=False)  # 读数为空 → 读数失败 → ACTION_NOTIFY
    monkeypatch.setattr(wd, "Executor", lambda: ex)
    monkeypatch.setattr(wd, "load_state", lambda path: {})
    saved = []
    monkeypatch.setattr(wd, "save_state", lambda path, s: saved.append(dict(s)))

    wd.main([])
    wd.main([])

    assert saved == []  # 一次也没记 last_notify
    assert len(ex.notices) == 2  # 第二次运行仍然再发


def test_main_records_notify_when_send_succeeds(monkeypatch):
    """正控：发信成功才写 last_notify——否则上面那条用例是空过。"""
    ex = FakeExecutor(notify_ok=True)
    monkeypatch.setattr(wd, "Executor", lambda: ex)
    monkeypatch.setattr(wd, "load_state", lambda path: {})
    saved = []
    monkeypatch.setattr(wd, "save_state", lambda path, s: saved.append(dict(s)))

    wd.main([])

    assert len(saved) == 1
    assert "last_notify" in saved[0]
