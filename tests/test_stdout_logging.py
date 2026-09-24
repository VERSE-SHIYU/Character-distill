"""`install_stdout_logging` —— 把 WARNING+ 记录写进 stdout，让 `docker logs` 看得见。

在此之前根日志器上只挂了环形缓冲（留在进程里等人来查）。Python 的 lastResort 只在
「根上没有任何 handler」时才生效，所以一旦挂上环形缓冲，`logger.error/warning` 既不进
stdout 也不进 `docker logs` —— 线上排障时这些记录只存在于内存里。
"""

from __future__ import annotations

import logging
import re

from core.stdout_logging import install_stdout_logging

_ROOT = logging.getLogger()


def _handlers_added_since(before: list[logging.Handler]) -> list[logging.Handler]:
    """`before` 之后新挂到根日志器上的 handler。

    按「快照前后差集」认人，**不看** `handler.stream is sys.stdout`：捕获夹具会替换
    `sys.stdout`，handler 持有的又是构造期那一个 —— 照身份认会在捕获开启时认不出来，
    那样的判据依赖的是夹具，不是被测物。
    """
    return [h for h in _ROOT.handlers if h not in before]


def _probe_logger(name: str) -> logging.Logger:
    """探针 logger：级别放到 DEBUG 并传播到根，好让 handler 自己的级别成为唯一闸门。"""
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    log.propagate = True
    return log


def test_install_is_idempotent_and_sits_at_warning():
    """装几次都只有一个 handler，且级别与环形缓冲同级。"""
    before = list(_ROOT.handlers)
    try:
        install_stdout_logging()
        install_stdout_logging()

        added = _handlers_added_since(before)
        assert len(added) == 1, f"装两次应只多出一个 handler，实得 {len(added)}"
        assert added[0].level == logging.WARNING, (
            f"handler 级别应是 WARNING（与环形缓冲同级），"
            f"实得 {logging.getLevelName(added[0].level)}"
        )
    finally:
        _ROOT.handlers[:] = before


def test_warning_and_error_with_traceback_reach_stdout(capsys):
    """消息、级别、logger 名、时间，以及 exc_info 的完整堆栈，都要落在 stdout 上。"""
    before = list(_ROOT.handlers)
    try:
        install_stdout_logging()
        log = _probe_logger("stdout_logging_probe")
        log.warning("stdout-probe-warning")
        try:
            raise ZeroDivisionError("stdout-probe-div")
        except ZeroDivisionError:
            log.error("stdout-probe-error", exc_info=True)
        out, err = capsys.readouterr()
    finally:
        _ROOT.handlers[:] = before

    assert "stdout-probe-warning" in out, f"WARNING 没进 stdout：{out!r}"
    assert "stdout-probe-error" in out, f"ERROR 没进 stdout：{out!r}"
    assert "Traceback" in out and "ZeroDivisionError" in out, (
        f"exc_info 的堆栈没完整进 stdout：{out!r}"
    )
    assert "stdout_logging_probe" in out, f"缺 logger 名：{out!r}"
    assert "WARNING" in out and "ERROR" in out, f"缺级别名：{out!r}"
    assert re.search(r"\d{2}:\d{2}:\d{2}", out), f"缺时间：{out!r}"
    assert "stdout-probe-warning" not in err, "写到了 stderr —— Docker 看的是另一个流"


def test_below_warning_is_not_written(capsys):
    """DEBUG/INFO 不进 stdout：闸门是 handler 自己的级别，不是调用方 logger 的。"""
    before = list(_ROOT.handlers)
    try:
        install_stdout_logging()
        _probe_logger("stdout_logging_probe_info").info("stdout-probe-info")
        out, _ = capsys.readouterr()
    finally:
        _ROOT.handlers[:] = before

    assert "stdout-probe-info" not in out, f"INFO 不该进 stdout：{out!r}"


def test_the_lifespan_routes_error_to_stdout(capsys):
    """装配处（lifespan）负责装它 —— 走真 app 的启动流程，不直接调装配函数。

    判据落在「启动之后 ERROR 出现在 stdout」上，而不是「根日志器上多了某个对象」：
    后者要靠对象身份认人，前者才是这条线要保证的事。
    """
    from fastapi.testclient import TestClient

    import server

    before = list(_ROOT.handlers)
    try:
        with TestClient(server.app):
            _probe_logger("stdout_logging_probe_lifespan").error("stdout-probe-lifespan")
            out, _ = capsys.readouterr()
    finally:
        _ROOT.handlers[:] = before

    assert "stdout-probe-lifespan" in out, f"lifespan 起来后 ERROR 仍没进 stdout：{out!r}"
