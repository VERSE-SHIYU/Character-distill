"""把 WARNING+ 记录写到 stdout，让 `docker logs` 留得住。

根日志器一旦挂上环形缓冲，Python 的 lastResort 就不再生效（它只在「根上没有任何
handler」时兜底）—— `logger.error/warning` 于是既不进 stdout 也不进 `docker logs`，
线上排障时这些记录只活在进程内存里。本模块补上缺的那条出口。

与 `core/log_collector` 的分工：那个负责「留在进程里等人来查」（环形缓冲），本模块
负责「推出去」（stdout → docker logs）。两者级别相同、互不替代。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable


class _StdoutHandler(logging.StreamHandler):
    """读时现取 `sys.stdout`，写法照抄标准库 `logging._StderrHandler`。

    `logging.StreamHandler` 在**构造时**就把 stream 绑死了，而本 handler 是模块级
    单例 —— 构造发生在导入期，那一刻的 stdout 只是「导入时恰好是哪个对象」。宿主换过
    流（pytest 的捕获、`contextlib.redirect_stdout`、嵌进别的 runner）之后，绑死的那
    个对象就不再是当前 stdout 了。

    标准库给同一问题（它用于 `logging.lastResort`）的答案是：不走
    `StreamHandler.__init__` 绑流，改调 `Handler.__init__` 只取级别，再把 `stream`
    做成只读 property。这里照抄，只把 `sys.stderr` 换成 `sys.stdout`。
    """

    def __init__(self) -> None:
        logging.Handler.__init__(self, logging.WARNING)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout


_handler = _StdoutHandler()
_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
)


def install_stdout_logging() -> Callable[[], None]:
    """把 stdout handler 挂到根日志器上；返回撤销。

    去重仍由 `logging.Logger.addHandler` 自己保证 —— 它的实现就是
    ``if not (hdlr in self.handlers)``，且在 ``_acquireLock()`` 之下。

    外面这一次查询**不是为了去重**（那是上面那步的事），是为了知道这次是不是**我们**
    装上的：不是我们装的就不能由我们撤掉，否则关停会把别人的 handler 摘了。
    """
    root = logging.getLogger()
    already = _handler in root.handlers
    root.addHandler(_handler)
    if already:
        return lambda: None
    return lambda: root.removeHandler(_handler)
