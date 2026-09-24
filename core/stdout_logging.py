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


class _StdoutHandler(logging.StreamHandler):
    """每次 emit 现取 `sys.stdout`。

    `logging.StreamHandler` 在**构造时**就把 stream 绑死了，而本 handler 是模块级
    单例 —— 构造发生在导入期，那一刻的 stdout 只是「导入时恰好是哪个对象」。宿主换过
    流（pytest 的捕获、`contextlib.redirect_stdout`、嵌进别的 runner）之后，绑死的那
    个对象就不再是当前 stdout 了。现取让「当前谁是 stdout」说了算。
    """

    def __init__(self) -> None:
        super().__init__(sys.stdout)
        self.setLevel(logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


_handler = _StdoutHandler()
_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
)


def install_stdout_logging() -> None:
    """把 stdout handler 挂到根日志器上。

    幂等由 `logging.Logger.addHandler` 自己保证 —— 它的实现就是
    ``if not (hdlr in self.handlers)``，且在 ``_acquireLock()`` 之下。本函数不重复
    这一层：写在外面既冗余，又不是原子的（两个线程可以同时通过守卫）。
    """
    logging.getLogger().addHandler(_handler)
