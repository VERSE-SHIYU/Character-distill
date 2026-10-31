"""建索引嵌入的账号级限流：RPM / TPM 两个令牌桶（功能隔离：不 import embeddings，不碰 HTTP）。

配额与并发数只在本模块定义一次；`core/embeddings.py` 取实例、按批取令牌。
依据：百炼「限流」与「限流应对最佳实践」（spec v5 §2-7，O3/O5/O6/O7）：

- 桶容量 = 配额，补充速率 = 配额 ÷ 60 每秒，初始令牌 0（空桶启动）；
- 先取 RPM 令牌、再取 TPM 令牌、再占并发槽（O6）；TPM 按本批预估 token 事前预扣（O7）；
- 严格 FIFO：只有队首拿得到令牌，后来者不插队；
- 两个桶按 (key 指纹, 地域) 在模块级共享（O1、F13、F14）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

# spec v5 §2-7：出处见模块 docstring（O3 / O5 / O4）。
EMBED_BUILD_CONCURRENCY = 8
EMBED_RPM_LIMIT = {"cn": 1800, "intl": 1800}
EMBED_TPM_LIMIT = {"cn": 1_200_000, "intl": 1_000_000}

_MIN_WAIT_S = 0.001          # 防止 wait(0) 空转
_CANCEL_POLL_S = 0.05        # 有取消请求时最多多久回看一眼


class TokenBucket:
    """容量 / 补充速率固定的令牌桶；空桶启动。时钟可注入，便于不真睡地测。"""

    def __init__(self, capacity: float, per_second: float,
                 clock: Callable[[], float] = time.monotonic):
        self.capacity = float(capacity)
        self.rate = float(per_second)
        self._clock = clock
        self._tokens = 0.0
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now

    def available(self) -> float:
        self._refill()
        return self._tokens

    def take(self, amount: float) -> None:
        self._refill()
        self._tokens -= amount

    def seconds_until(self, amount: float) -> float:
        self._refill()
        if self._tokens >= amount:
            return 0.0
        if self.rate <= 0:
            return float("inf")
        return (amount - self._tokens) / self.rate


@dataclass
class _Waiter:
    rpm: float
    tpm: float
    done: bool = False


class EmbedRateLimiter:
    """一对令牌桶 + 严格 FIFO 队列。`acquire` 阻塞到两桶都够；`try_acquire` 不阻塞。"""

    def __init__(self, rpm: int, tpm: int,
                 clock: Callable[[], float] = time.monotonic):
        self._rpm = TokenBucket(rpm, rpm / 60.0, clock)
        self._tpm = TokenBucket(tpm, tpm / 60.0, clock)
        self._cond = threading.Condition()
        self._waiters: deque[_Waiter] = deque()

    def wake(self) -> None:
        """外部状态变了（时钟推进 / 取消）时叫醒等待者重算。"""
        with self._cond:
            self._cond.notify_all()

    def try_acquire(self, tokens: float) -> bool:
        """不阻塞地取 1 个 RPM + `tokens` 个 TPM；队列非空时不插队。"""
        with self._cond:
            if self._waiters:
                return False
            self._serve_locked()
            if self._waiters or self._rpm.available() < 1 or self._tpm.available() < tokens:
                return False
            self._rpm.take(1)
            self._tpm.take(tokens)
            return True

    def acquire(self, tokens: float, *, cancel: threading.Event | None = None) -> bool:
        """阻塞取令牌；`cancel` 置位或被叫醒后检查到取消 → 返回 False（不占令牌）。"""
        with self._cond:
            if cancel is not None and cancel.is_set():
                return False
            waiter = _Waiter(1.0, max(0.0, float(tokens)))
            self._waiters.append(waiter)
            try:
                while True:
                    self._serve_locked()
                    if waiter.done:
                        return True
                    if cancel is not None and cancel.is_set():
                        return False
                    head = self._waiters[0]
                    wait = max(self._rpm.seconds_until(head.rpm),
                               self._tpm.seconds_until(head.tpm))
                    if cancel is not None:
                        wait = min(wait, _CANCEL_POLL_S)
                    self._cond.wait(max(wait, _MIN_WAIT_S))
            finally:
                if not waiter.done:
                    self._drop_locked(waiter)

    def _serve_locked(self) -> None:
        """只服务队首；服务完继续看新队首（严格 FIFO）。调用方持有 `_cond`。"""
        while self._waiters:
            head = self._waiters[0]
            if self._rpm.available() < head.rpm or self._tpm.available() < head.tpm:
                return
            self._rpm.take(head.rpm)
            self._tpm.take(head.tpm)
            head.done = True
            self._waiters.popleft()
            self._cond.notify_all()

    def _drop_locked(self, waiter: _Waiter) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            return
        self._serve_locked()
        self._cond.notify_all()


_LIMITERS: dict[tuple[str, str], EmbedRateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def get_limiter(fingerprint: str, region: str) -> EmbedRateLimiter:
    """按 (key 指纹, 地域) 共享的限流器；同一账号下所有卡共用这两只桶。"""
    key = (fingerprint, region)
    with _LIMITERS_LOCK:
        limiter = _LIMITERS.get(key)
        if limiter is None:
            limiter = EmbedRateLimiter(EMBED_RPM_LIMIT[region], EMBED_TPM_LIMIT[region])
            _LIMITERS[key] = limiter
        return limiter


def reset_limiters() -> None:
    """测试隔离用：丢掉进程内所有共享桶。"""
    with _LIMITERS_LOCK:
        _LIMITERS.clear()
