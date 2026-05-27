"""Tests for RingBufferHandler — log capture for admin panel."""

from __future__ import annotations

import logging
import threading

from core.log_collector import (
    RingBufferHandler,
    get_recent_logs,
    install_log_collector,
)


def _make_logger(name: str, handler: RingBufferHandler) -> logging.Logger:
    """Create an isolated logger with the given handler (no root propagation)."""
    log = logging.getLogger(name)
    log.setLevel(logging.WARNING)
    log.propagate = False
    log.handlers.clear()
    log.addHandler(handler)
    return log


class TestRingBufferHandler:
    """Unit tests for RingBufferHandler."""

    def test_emit_and_snapshot(self):
        handler = RingBufferHandler(capacity=50)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = _make_logger("test_rb", handler)
        logger.warning("test message")

        snap = handler.snapshot()
        assert len(snap) == 1
        assert snap[0]["message"] == "test message"
        assert snap[0]["level"] == "WARNING"
        assert snap[0]["name"] == "test_rb"

    def test_capacity_bound(self):
        handler = RingBufferHandler(capacity=10)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = _make_logger("test_cap", handler)

        for i in range(20):
            logger.warning(f"msg {i}")

        snap = handler.snapshot()
        assert len(snap) == 10
        assert snap[0]["message"] == "msg 10"

    def test_below_warning_not_captured(self):
        handler = RingBufferHandler(capacity=50)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = _make_logger("test_level", handler)
        logger.setLevel(logging.DEBUG)  # handler itself filters at WARNING

        logger.info("info message")
        logger.debug("debug message")
        snap = handler.snapshot()
        assert len(snap) == 0

    def test_snapshot_limit(self):
        handler = RingBufferHandler(capacity=50)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = _make_logger("test_limit", handler)

        for i in range(20):
            logger.warning(f"msg {i}")

        snap = handler.snapshot(limit=5)
        assert len(snap) == 5

    def test_thread_safety(self):
        """Concurrent emit calls should not crash or corrupt the buffer."""
        from concurrent.futures import ThreadPoolExecutor, wait
        handler = RingBufferHandler(capacity=500)
        handler.setFormatter(logging.Formatter("%(message)s"))
        import logging as _logging
        rec = _logging.LogRecord("t", logging.WARNING, "", 0, "safe", (), None)

        def emit_one(_):
            handler.emit(rec)

        N = 200
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(emit_one, i) for i in range(N)]
            done, _ = wait(futures)
            for f in done:
                assert f.exception() is None, f.exception()

        # snaphot default limit=100, so check buffer directly
        assert len(handler._buffer) == N, f"Expected {N}, got {len(handler._buffer)}"

    def test_install_log_collector_idempotent(self):
        """install_log_collector should not add duplicate handlers."""
        root = logging.getLogger()
        count_before = sum(1 for h in root.handlers if isinstance(h, RingBufferHandler))
        install_log_collector()
        count_after = sum(1 for h in root.handlers if isinstance(h, RingBufferHandler))
        assert count_after == count_before + 1

        install_log_collector()
        count_after2 = sum(1 for h in root.handlers if isinstance(h, RingBufferHandler))
        assert count_after2 == count_after

        # Cleanup to avoid interfering with pytest capture
        root.handlers[:] = [h for h in root.handlers if not isinstance(h, RingBufferHandler)]

    def test_get_recent_logs(self):
        install_log_collector()
        logging.getLogger("test_recent").warning("recent log entry")
        logs = get_recent_logs(limit=10)
        assert len(logs) >= 1
        assert any("recent log entry" in r["message"] for r in logs)

        # Cleanup to avoid interfering with pytest capture
        root = logging.getLogger()
        root.handlers[:] = [h for h in root.handlers if not isinstance(h, RingBufferHandler)]
