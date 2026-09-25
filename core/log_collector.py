"""Ring buffer log collector — captures recent log records for admin panel."""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any


class RingBufferHandler(logging.Handler):
    """In-memory ring buffer that retains the last *capacity* records at WARNING+ level."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__(level=logging.WARNING)
        self._capacity = capacity
        self._buffer: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "time": self.format(record),
            "name": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        with self._lock:
            self._buffer.append(entry)

    def snapshot(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer)[-limit:]


# Module-level singleton
_handler = RingBufferHandler(capacity=500)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))


def install_log_collector() -> Callable[[], None]:
    """Attach the ring buffer handler to the root logger; return its undo.

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


def get_recent_logs(limit: int = 100) -> list[dict[str, Any]]:
    """Return the last *limit* log entries from the ring buffer."""
    return _handler.snapshot(limit)
