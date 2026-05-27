"""Ring buffer log collector — captures recent log records for admin panel."""

from __future__ import annotations

import logging
import threading
from collections import deque
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


def install_log_collector() -> None:
    """Attach the ring buffer handler to the root logger."""
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)


def get_recent_logs(limit: int = 100) -> list[dict[str, Any]]:
    """Return the last *limit* log entries from the ring buffer."""
    return _handler.snapshot(limit)
