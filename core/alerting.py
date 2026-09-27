# -*- coding: utf-8 -*-
"""主动告警：把 ERROR 级日志接到邮箱上。

**为什么要有这个模块。** 日志写出来了也得有人去翻才看得见 —— SG「全员用量为 0 数月
无人察觉」（AGENTS.md 缺陷 93）就是这个形态：失败**已经可见**了（缺陷 93 的修法把
`try/except + print` 换成了 `core/nonfatal`，每条都记了日志），但可见与**被看见**是
两件事，中间隔着「有没有人去看」。本模块补的是那一跳：ERROR 直接投递到人手上。

**与既有构造的分工。** `nonfatal` 负责「吞掉但留痕」，Sentry 的 LoggingIntegration
负责「把 ERROR 变成 GlitchTip 事件」，本模块负责「推去邮箱」。各自只用 `logging` 的
标准接口，互不感知。

**只依赖 `send_email`。** 本模块**不**并入 `email_service`（那里是「怎么发」，本模块是
「什么时候发、发给谁、发多勤」）。发信逻辑只有一份，在
`core.email_service.send_email`。

**节流是必须的，不是优化。** 一个持续的故障（PG 拒写、上游 429）会在每个请求上各留
一条 ERROR。不节流的话，一次故障就是几百封邮件 —— 收件人会先把告警规则静音，于是
真正的第一条告警也一起没了。所以按 (logger 名, 异常类型) 分键，同键一小时一封。

**`ALERT_EMAIL` 为空 = 不安装。** 见 `install_alert_handler`。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from html import escape
from typing import Any, Callable

from core.email_service import send_email

#: 环境变量名。**唯一读取点**在本模块（`alert_email`）。
ALERT_EMAIL_ENV = "ALERT_EMAIL"

#: 只有 ERROR 及以上才告警。WARNING 是「知道一下」，不该把人从床上叫起来。
ALERT_LEVEL = logging.ERROR

#: 节流窗口（秒）。同一个键在窗口内只发一封，其余计入被压下的条数。
WINDOW_SECONDS = 3600

#: 取时间的方式。测试替换它，免得为了验一小时窗口真等一小时。
_now: Callable[[], float] = time.monotonic


def alert_email() -> str:
    """`ALERT_EMAIL` 的当前值（两侧空白去掉）。空串 = 未配置。"""
    return os.getenv(ALERT_EMAIL_ENV, "").strip()


class _Throttle:
    """按 (logger 名, 异常类型) 记账：上次发送时间 + 本窗口被压下的条数。

    只记这两样 —— 不存正文、不落盘。进程重启即清零：重启本身就是一次「重新开始」，
    且重启后的第一封告警**本该**发出去（不然一次重启会让正在发生的故障彻底静音）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[tuple[str, str], float] = {}
        self._suppressed: dict[tuple[str, str], int] = {}

    def admit(self, key: tuple[str, str]) -> tuple[bool, int]:
        """`(该不该发, 上一个窗口被压下的条数)`。

        首次见到这个键 → 立即发。窗口内再来 → 不发、计数 +1。窗口外的下一次 →
        发，并把「上个窗口压下了几条」带出去（否则邮件读起来像「只出过一次错」）。
        """
        now = _now()
        with self._lock:
            last = self._last.get(key)
            if last is None or now - last >= WINDOW_SECONDS:
                suppressed = self._suppressed.get(key, 0)
                self._last[key] = now
                self._suppressed[key] = 0
                return True, suppressed
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return False, 0


def _dispatch(fn: Callable[[], Any]) -> None:
    """把发信丢到后台线程 —— `emit` 跑在**调用方的线程**上（可能是请求线程），
    十来秒的 SMTP 超时不能挂在那上面。测试替换成同步调用，免得等线程。"""
    threading.Thread(target=fn, daemon=True, name="alert-mailer").start()


class AlertHandler(logging.Handler):
    """ERROR 级日志 → 告警邮件。`emit` 立即返回，发信在后台线程。"""

    def __init__(self, to_email: str) -> None:
        super().__init__(level=ALERT_LEVEL)
        self._to = to_email
        self._throttle = _Throttle()

    def emit(self, record: logging.LogRecord) -> None:
        # handler 的 level 已经在 `handle()` 那层挡过一道，但 handler 也可能被直接调用
        # （`callHandlers` 之外），而「WARNING 不发」是明写的判据，故这里再判一次。
        if record.levelno < ALERT_LEVEL:
            return
        try:
            key = (record.name, _exc_name(record))
            send, suppressed = self._throttle.admit(key)
            if not send:
                return
            subject, body = _compose(record, suppressed)
            _dispatch(lambda: self._send(subject, body))
        except Exception:
            # emit **绝不能**往外抛：抛出去会走 logging 的 `handleError`，那是另一条
            # 与本模块同级的输出通道，一次记账失败会变成一条噪声而不是一次失败。
            self.handleError(record)

    def _send(self, subject: str, body: str) -> None:
        try:
            send_email(self._to, subject, body)
        except Exception as exc:
            # 只写 stderr，**绝不**经 logging —— 本模块挂在 root logger 上，在这里再记
            # 一条 ERROR 就是自己触发自己（递归）。这条纪律就是防递归的全部机制，
            # 不要为了「让失败也可见」在这儿改成 logger.error。
            sys.stderr.write(
                f"[alerting] 告警邮件发送失败（不再重试、不递归）: "
                f"{type(exc).__name__}: {exc}\n"
            )


def _exc_name(record: logging.LogRecord) -> str:
    """日志记录里的异常类名；没带异常信息时给 `-`。

    `-` 而不是空串：键要能把「无异常的 ERROR」聚成一组，空串读起来像「漏取了」。
    """
    return record.exc_info[0].__name__ if record.exc_info else "-"


def _compose(record: logging.LogRecord, suppressed: int) -> tuple[str, str]:
    """`(主题, HTML 正文)`。正文含来源、异常、消息与堆栈。"""
    exc_name = _exc_name(record)
    subject = f"[CharSim 告警] {record.name}: {exc_name}"
    # `self.format` 走 logging.Formatter，它会把 exc_info 的堆栈一并附上 ——
    # 不必自己拼 traceback（拼法随 Python 版本改过一轮）。
    detail = logging.Formatter().format(record)
    parts = [
        f"<p><strong>来源</strong>：{escape(record.name)}</p>",
        f"<p><strong>异常</strong>：{escape(exc_name)}</p>",
        f"<p><strong>消息</strong>：{escape(record.getMessage())}</p>",
    ]
    if suppressed:
        parts.append(
            f"<p><strong>上一个 {WINDOW_SECONDS // 60} 分钟窗口内，同类错误另有 "
            f"{suppressed} 次被压下</strong>（同一来源 + 同一异常类型只发一封）。</p>"
        )
    parts.append(f"<pre>{escape(detail)}</pre>")
    return subject, "".join(parts)


def install_alert_handler() -> Callable[[], None]:
    """装配入口：把告警 handler 挂到 root logger 上；返回撤销。

    生产在 `web/server.py` 的 lifespan 里调用，紧跟 `install_stdout_logging()`。

    `ALERT_EMAIL` 为空 → **不安装**，只记一条 WARNING（这条只落 stdout）。
    **不设默认收件人**：告警发到一个没人看的地址，比不发更坏 —— 它看起来像有人在收。

    两种「没装上」都返回空操作：`ALERT_EMAIL` 没配，以及根上已经有同**类**的 handler
    （那个是别人装的，不该由这一处的撤销摘掉）。
    """
    to = alert_email()
    if not to:
        logging.getLogger(__name__).warning(
            "ALERT_EMAIL 未配置，主动告警未启用（ERROR 仍进 GlitchTip）"
        )
        return lambda: None
    root = logging.getLogger()
    if any(isinstance(h, AlertHandler) for h in root.handlers):
        return lambda: None
    handler = AlertHandler(to)
    root.addHandler(handler)
    return lambda: root.removeHandler(handler)


def _main(argv: list[str]) -> int:
    """手动验证入口：`python -m core.alerting --test` 发一封测试邮件。"""
    if "--test" not in argv:
        print("用法: python -m core.alerting --test   # 往 ALERT_EMAIL 发一封测试邮件")
        return 2
    to = alert_email()
    if not to:
        print("ALERT_EMAIL 未配置，无处可发。", file=sys.stderr)
        return 1
    try:
        send_email(
            to,
            "[CharSim 告警] 测试邮件",
            "<p>告警通道测试：收到这封说明 ALERT_EMAIL 的投递链路是通的。</p>",
        )
    except Exception as exc:
        print(f"发送失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"已发送测试告警邮件到 {to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
