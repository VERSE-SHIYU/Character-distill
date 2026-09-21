# -*- coding: utf-8 -*-
"""主动告警（`core/alerting.py`）的判据。

**这套判据最要紧的一条是「不多发」。** 告警的价值全在信噪比上：一次持续故障（PG 拒写、
上游 429）会在每个请求上各留一条 ERROR，不节流就是几百封邮件 —— 收件人会先把告警规则
静音，于是真正的第一条也一起没了。所以下面逐条钉住节流，而不是只钉「能发出来」。

**仪器纪律**：`_dispatch` 与 `send_email` 都替换成同步/记录式替身 —— 前者免得等线程，
后者免得真发信。两者都替换在**模块自身的名字**上（`alerting.send_email`），这正是生产
调用的那个绑定；只 patch 一处绑定的话，绕开它的变异会假绿。

Run: pytest tests/test_alerting.py -v
"""
from __future__ import annotations

import logging

import pytest

import core.alerting as alerting


def _record(
    name: str = "core.probe",
    msg: str = "boom",
    level: int = logging.ERROR,
    exc: Exception | None = None,
) -> logging.LogRecord:
    """造一条日志记录。`exc` 给异常 → 键里的「异常类型」才有值。"""
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    return logging.LogRecord(name, level, __file__, 0, msg, (), exc_info)


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, str, str]]:
    """把发信换成记录，把后台线程换成同步 —— 用例里就能直接数发了几封。"""
    out: list[tuple[str, str, str]] = []
    monkeypatch.setattr(alerting, "_dispatch", lambda fn: fn())
    monkeypatch.setattr(
        alerting, "send_email",
        lambda to, subject, body: out.append((to, subject, body)),
    )
    return out


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    """可控时钟。`_now` 是模块级绑定，替换它即替换节流的「现在」。"""
    now = [1000.0]
    monkeypatch.setattr(alerting, "_now", lambda: now[0])
    return now


@pytest.fixture
def clean_root():
    """`install_*` 往 root logger 上挂东西，用完摘掉，别污染同一进程里的其他用例。"""
    root = logging.getLogger()
    before = list(root.handlers)
    yield root
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)


# ── 节流 ─────────────────────────────────────────────────────────────────────

def test_first_error_sends_immediately(sent, clock):
    """首次即发 —— 节流不能节掉第一条，那是这个模块存在的全部理由。"""
    alerting.AlertHandler("ops@example.com").handle(_record(exc=ValueError("boom")))

    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "ops@example.com"
    assert "ValueError" in subject and "core.probe" in subject
    assert "boom" in body
    assert "被压下" not in body, "第一次就报「压下了几次」—— 计数被算错了"


def test_same_key_within_window_is_counted_not_sent(sent, clock):
    """同键在窗口内再出错 → 不发，只计数。"""
    h = alerting.AlertHandler("ops@example.com")
    h.handle(_record(exc=ValueError("boom")))

    for _ in range(3):
        clock[0] += 60
        h.handle(_record(exc=ValueError("boom")))

    assert len(sent) == 1, f"窗口内又发了 {len(sent) - 1} 封 —— 节流没生效"


def test_after_window_sends_with_the_suppressed_count(sent, clock):
    """窗口后再出错 → 发，且邮件里带上「上个窗口压下了几次」。

    这一条是节流的**另一半**：只压不发的话，一个持续一小时的故障在收件人眼里
    与「出过一次就没了」长得一模一样。
    """
    h = alerting.AlertHandler("ops@example.com")
    h.handle(_record(exc=ValueError("boom")))
    for _ in range(2):
        clock[0] += 60
        h.handle(_record(exc=ValueError("boom")))
    assert len(sent) == 1

    clock[0] += alerting.WINDOW_SECONDS
    h.handle(_record(exc=ValueError("boom")))

    assert len(sent) == 2, "窗口过后没补发"
    assert "2 次被压下" in sent[1][2], f"没带上下个窗口的计数：{sent[1][2]!r}"

    # 计数要被清零：紧接着的下一封不该把上一窗口的 2 次又报一遍。
    clock[0] += alerting.WINDOW_SECONDS
    h.handle(_record(exc=ValueError("boom")))
    assert len(sent) == 3
    assert "被压下" not in sent[2][2], "计数没清零，同一个数字被反复上报"


def test_window_boundary_is_inclusive(sent, clock):
    """恰好满一个窗口即放行 —— 边界必须是 `>=`，`>` 会让窗口每轮多漂一次。"""
    h = alerting.AlertHandler("ops@example.com")
    h.handle(_record(exc=ValueError("boom")))
    clock[0] += alerting.WINDOW_SECONDS
    h.handle(_record(exc=ValueError("boom")))
    assert len(sent) == 2


def test_distinct_keys_each_send(sent, clock):
    """键是 (logger 名, 异常类型) —— 任一项不同就是另一件事，各发各的。"""
    h = alerting.AlertHandler("ops@example.com")
    h.handle(_record(name="core.a", exc=ValueError("x")))
    h.handle(_record(name="core.b", exc=ValueError("x")))     # 换来源
    h.handle(_record(name="core.a", exc=TypeError("y")))      # 换异常类型
    h.handle(_record(name="core.a"))                          # 无异常信息 → 键 "-"

    assert len(sent) == 4, f"不同键被并成一封了，实得 {len(sent)}"


# ── 等级 ─────────────────────────────────────────────────────────────────────

def test_warning_is_not_sent(sent, clock):
    """WARNING 不发。两个入口都走一遍：`handle` 是生产路径，`emit` 是直接调用。

    只钉 `handle` 的话，把等级判断整个删掉也不会红 —— 框架在 `handle` 那层已经挡了。
    """
    h = alerting.AlertHandler("ops@example.com")
    h.handle(_record(level=logging.WARNING, exc=ValueError("warn")))
    h.emit(_record(level=logging.WARNING, exc=ValueError("warn")))

    assert sent == [], "WARNING 被当成告警发了"


# ── 发送失败：不递归、不惊动调用方 ───────────────────────────────────────────

def test_send_failure_writes_stderr_and_does_not_recurse(monkeypatch, capsys, clock):
    """`send_email` 抛异常 → 只写 stderr，调用方不受影响，且**不得再触发自身**。

    递归检测落在 root logger 上（与生产同一处装配）：`_send` 里若改成 `logger.error`，
    那条 ERROR 会命中本 handler → 再发一次 → 再抛。因为节流对同一个键在窗口内返回
    False，递归只差一轮就停 —— 所以「只调用了一次」这件事本身是可判定的。
    """
    calls: list[int] = []

    def _boom(to, subject, body):
        calls.append(1)
        raise RuntimeError("smtp down")

    monkeypatch.setattr(alerting, "_dispatch", lambda fn: fn())
    monkeypatch.setattr(alerting, "send_email", _boom)

    root = logging.getLogger()
    h = alerting.AlertHandler("ops@example.com")
    root.addHandler(h)
    try:
        logging.getLogger("core.alerting.recursion-probe").error("boom")
    finally:
        root.removeHandler(h)

    err = capsys.readouterr().err
    assert "smtp down" in err, f"发送失败没有留下 stderr 痕迹：{err!r}"
    assert len(calls) == 1, f"发送失败触发了第 {len(calls)} 次发送 —— 递归了"


def test_compose_failure_does_not_escape_emit(monkeypatch, clock):
    """记账阶段自己炸了也不能往外抛 —— `emit` 抛出去会走 logging 的 `handleError`，
    一次记账失败会变成一条与本模块同级的噪声。"""
    monkeypatch.setattr(alerting, "_dispatch", lambda fn: fn())
    h = alerting.AlertHandler("ops@example.com")
    monkeypatch.setattr(h, "_throttle", None)  # admit 调用时 AttributeError

    h.handle(_record(exc=ValueError("boom")))  # 不抛即通过


# ── 装配 ─────────────────────────────────────────────────────────────────────

def test_unset_alert_email_installs_nothing_and_warns(monkeypatch, clean_root, caplog):
    """`ALERT_EMAIL` 为空 → 不安装，且启动时留一条 WARNING。

    「不安装」比「装一个默认收件人」重要：发到一个没人看的地址，比不发更坏 ——
    它看起来像有人在收。所以这里连「装了个空收件人的 handler」都不允许。
    """
    monkeypatch.delenv(alerting.ALERT_EMAIL_ENV, raising=False)

    with caplog.at_level(logging.WARNING):
        alerting.install_alert_handler()

    assert not any(isinstance(x, alerting.AlertHandler) for x in clean_root.handlers), \
        "ALERT_EMAIL 为空却装了 handler —— 会静默发往一个空地址"
    assert any(
        r.levelno == logging.WARNING and "ALERT_EMAIL" in r.getMessage()
        for r in caplog.records
    ), "未启用却什么都没说 —— 运维无从知道告警是关着的"


def test_alert_email_installs_once(monkeypatch, clean_root):
    """配了 `ALERT_EMAIL` → 挂上，且重复调用不会挂第二份（否则每封发两遍）。"""
    monkeypatch.setenv(alerting.ALERT_EMAIL_ENV, "  ops@example.com  ")

    alerting.install_alert_handler()
    installed = [x for x in clean_root.handlers if isinstance(x, alerting.AlertHandler)]
    assert len(installed) == 1
    assert installed[0]._to == "ops@example.com", "两侧空白没去掉"

    alerting.install_alert_handler()
    installed = [x for x in clean_root.handlers if isinstance(x, alerting.AlertHandler)]
    assert len(installed) == 1, "装了第二份 —— 同一封告警会发两遍"
