"""`install_stdout_logging` —— 把 WARNING+ 记录写进 stdout，让 `docker logs` 看得见。

在此之前根日志器上只挂了环形缓冲（留在进程里等人来查）。Python 的 lastResort 只在
「根上没有任何 handler」时才生效，所以一旦挂上环形缓冲，`logger.error/warning` 既不进
stdout 也不进 `docker logs` —— 线上排障时这些记录只存在于内存里。
"""

from __future__ import annotations

import logging
import re

from core.stdout_logging import _StdoutHandler, install_stdout_logging

_ROOT = logging.getLogger()


def _our_handlers() -> list[logging.Handler]:
    """根上属于本模块的 handler。

    按**类型**认人，不按对象身份，也不看 `handler.stream is sys.stdout`：捕获夹具会替换
    `sys.stdout`，handler 又是模块级单例 —— 照身份认的判据依赖的是夹具，不是被测物。
    """
    return [h for h in _ROOT.handlers if isinstance(h, _StdoutHandler)]


def _probe_logger(name: str) -> logging.Logger:
    """探针 logger：级别放到 DEBUG 并传播到根，好让 handler 自己的级别成为唯一闸门。"""
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    log.propagate = True
    return log


def test_install_is_idempotent_and_sits_at_warning():
    """装几次都只剩一个 handler，且级别与环形缓冲同级。

    起点先清干净再装：别的用例（如 `test_llm_access_gate` 起真 app 的 lifespan）早就
    把本模块的单例挂上去了，按「装前装后差集」算会得空 —— 那判的是「这次调用新挂了
    几个」，不是「根上最终有几个」。
    """
    before = list(_ROOT.handlers)
    _ROOT.handlers[:] = [h for h in before if not isinstance(h, _StdoutHandler)]
    try:
        install_stdout_logging()
        install_stdout_logging()

        ours = _our_handlers()
        assert len(ours) == 1, f"装两次应只留一个 handler，实得 {len(ours)}"
        assert ours[0].level == logging.WARNING, (
            f"handler 级别应是 WARNING（与环形缓冲同级），"
            f"实得 {logging.getLevelName(ours[0].level)}"
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

    起点先剔除根上的本模块 handler：`test_llm_access_gate` 排在本文件前面，它起的真 app
    lifespan 早就装过一个了 —— 不剔的话，把 lifespan 里的装配调用删掉这条照样绿，
    「装配处负责装」就无从判起。

    lifespan 装上的守卫与投递器由它退出时自己撤销（缺陷 113 的修法，见
    `web/server.py::_lifespan`），不需要夹具兜。
    """
    from fastapi.testclient import TestClient

    import server

    before = list(_ROOT.handlers)
    _ROOT.handlers[:] = [h for h in before if not isinstance(h, _StdoutHandler)]
    try:
        with TestClient(server.app):
            _probe_logger("stdout_logging_probe_lifespan").error("stdout-probe-lifespan")
            out, _ = capsys.readouterr()
    finally:
        _ROOT.handlers[:] = before

    assert "stdout-probe-lifespan" in out, f"lifespan 起来后 ERROR 仍没进 stdout：{out!r}"
