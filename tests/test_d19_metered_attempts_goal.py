"""D19 收尾目标检查：多批路径每次 HTTP 尝试恰好付一份令牌，取消后不再发。

测的是同一条不变量，不是「符合 spec」：

1. 一批前 2 次 429、第 3 次成功（共 3 批）：替身收到的请求数 == 限流器发放的令牌数。
2. 一批触发审核错误、降级逐条：同上。
3. 批 A（第 2 批）立即 400、批 B（第 1 批）持续 429：``_embed_impl`` 抛出的是批 A 的
   F9 ``RuntimeError``（批号对、``from`` 链上是 400），不是取消异常；抛出后不再有新
   请求，且批 B 总尝试 < 6。

实现前三条都是红的：重试与降级逐条直接出网、不取令牌；tenacity 不看 cancel。
"""

from __future__ import annotations

import threading
import time
import uuid

import httpx
import openai
import pytest

import core.embeddings as embeddings_mod
from core.embed_rate_limit import EmbedRateLimiter, reset_limiters
from core.embeddings import (
    DashScopeEmbedding,
    _SHARED_CACHE,
    _SHARED_CACHE_LOCK,
    reset_embed_stats,
)

BATCH_SIZE = DashScopeEmbedding.MAX_BATCH


def _vec_for(text: str) -> list[float]:
    return [float(len(text)), float(sum(text.encode("utf-8")) % 1000)]


def _texts(n: int, tag: str) -> list[str]:
    prefix = uuid.uuid4().hex[:8]
    return [f"{prefix}-{tag}-{i:04d}-padding" for i in range(n)]


class _FakeData:
    def __init__(self, index: int, embedding: list[float]):
        self.index = index
        self.embedding = embedding


class _FakeResponse:
    def __init__(self, embeddings: list[list[float]]):
        self.data = [_FakeData(i, emb) for i, emb in enumerate(embeddings)]


class _FakeEmbeddings:
    def __init__(self, create):
        self.create = create


class _FakeClient:
    def __init__(self, create):
        self.embeddings = _FakeEmbeddings(create)

    def with_options(self, **_kwargs):
        return self


def _install(monkeypatch, create) -> None:
    monkeypatch.setattr(openai, "OpenAI", lambda **_kwargs: _FakeClient(create))


@pytest.fixture(autouse=True)
def _clean_module_state():
    reset_embed_stats()
    reset_limiters()
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE.clear()
    yield
    reset_embed_stats()
    reset_limiters()
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE.clear()


def _rate_limit_error() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://dashscope.invalid/v1/embeddings")
    response = httpx.Response(429, request=request)
    return openai.RateLimitError("429 rate limited", response=response, body=None)


def _bad_request_error() -> openai.BadRequestError:
    request = httpx.Request("POST", "https://dashscope.invalid/v1/embeddings")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError("400 bad request", response=response, body=None)


class _RequestCounter:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0

    def count(self) -> None:
        with self.lock:
            self.calls += 1


class _CountingLimiter:
    """包住真实 ``EmbedRateLimiter``：阻塞/FIFO 行为不变，只数成功发放的令牌次数。"""

    def __init__(self, real: EmbedRateLimiter):
        self._real = real
        self._lock = threading.Lock()
        self.granted = 0

    def acquire(self, tokens: float, *, cancel=None) -> bool:
        ok = self._real.acquire(tokens, cancel=cancel)
        if ok:
            with self._lock:
                self.granted += 1
        return ok

    def wake(self) -> None:
        self._real.wake()


def test_retry_attempts_each_pay_one_token(monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)

    counter = _RequestCounter()
    lock = threading.Lock()
    failures = 0

    def create(*, input, **_kwargs):
        nonlocal failures
        texts = input if isinstance(input, list) else [input]
        counter.count()
        if any("FLAKY" in t for t in texts):
            with lock:
                fail = failures < 2
                if fail:
                    failures += 1
            if fail:
                raise _rate_limit_error()
        return _FakeResponse([_vec_for(t) for t in texts])

    _install(monkeypatch, create)
    limiter = _CountingLimiter(EmbedRateLimiter(rpm=1800, tpm=1_200_000))
    monkeypatch.setattr(embeddings_mod, "get_limiter", lambda *_a: limiter)

    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(3 * BATCH_SIZE, "meter")
    texts[0] = "FLAKY-" + texts[0]

    out = emb(texts)

    assert len(out) == len(texts)
    # 标记批 3 次尝试（2×429 + 成功）+ 另两批各 1 次 = 5 次出网。
    assert counter.calls == 5, f"出网尝试次数 {counter.calls} != 5"
    assert counter.calls == limiter.granted, (
        f"HTTP 尝试 {counter.calls} 次，限流器只发了 {limiter.granted} 份令牌"
    )


def test_moderation_fallback_attempts_each_pay_one_token(monkeypatch):
    counter = _RequestCounter()

    def create(*, input, **_kwargs):
        texts = input if isinstance(input, list) else [input]
        counter.count()
        if any("BAD" in t for t in texts):
            raise RuntimeError("DataInspectionFailed: content_filter")
        return _FakeResponse([_vec_for(t) for t in texts])

    _install(monkeypatch, create)
    limiter = _CountingLimiter(EmbedRateLimiter(rpm=1800, tpm=1_200_000))
    monkeypatch.setattr(embeddings_mod, "get_limiter", lambda *_a: limiter)

    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(2 * BATCH_SIZE, "modmeter")
    bad_index = BATCH_SIZE + 1
    texts[bad_index] = "BAD-" + texts[bad_index]

    out = emb(texts)

    assert len(out) == len(texts)
    # 两批首次 + 降级逐条 10 次 = 12 次出网。
    assert counter.calls == 12, f"出网尝试次数 {counter.calls} != 12"
    assert counter.calls == limiter.granted, (
        f"HTTP 尝试 {counter.calls} 次，限流器只发了 {limiter.granted} 份令牌"
    )


class _PrepayThenBlockOnCancel:
    """主线程预付立即发放；工作线程的重试 acquire 阻塞到 cancel，照生产返回 False。"""

    def __init__(self, prepay_thread: threading.Thread):
        self._prepay_thread = prepay_thread
        self._lock = threading.Lock()
        self.granted = 0

    def acquire(self, tokens: float, *, cancel=None) -> bool:
        if threading.current_thread() is self._prepay_thread:
            with self._lock:
                self.granted += 1
            return True
        while cancel is not None and not cancel.is_set():
            time.sleep(0.005)
        return False

    def wake(self) -> None:
        return None


def test_failure_stops_inflight_retries_and_first_real_error_wins(monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)

    lock = threading.Lock()
    attempts_b = 0
    requests_seen = 0

    def create(*, input, **_kwargs):
        nonlocal attempts_b, requests_seen
        texts = input if isinstance(input, list) else [input]
        with lock:
            requests_seen += 1
            if any("FAIL429" in t for t in texts):
                attempts_b += 1
                raise _rate_limit_error()
            if any("FAIL400" in t for t in texts):
                raise _bad_request_error()
        return _FakeResponse([_vec_for(t) for t in texts])

    _install(monkeypatch, create)
    limiter = _PrepayThenBlockOnCancel(threading.current_thread())
    monkeypatch.setattr(embeddings_mod, "get_limiter", lambda *_a: limiter)

    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(2 * BATCH_SIZE, "cancel")
    for i in range(BATCH_SIZE):
        texts[i] = "FAIL429-" + texts[i]
    texts[BATCH_SIZE] = "FAIL400-" + texts[BATCH_SIZE]

    with pytest.raises(RuntimeError) as ei:
        emb(texts)

    assert str(ei.value).startswith("百炼 embedding 失败：第2批，10条"), (
        f"调用方看到的不是批 A 的真实失败：{ei.value!r}"
    )
    assert isinstance(ei.value.__cause__, openai.BadRequestError), (
        f"from 链上不是 400：{ei.value.__cause__!r}"
    )
    assert ei.value.__cause__.status_code == 400
    assert attempts_b < 6, f"取消后批 B 仍把重试跑完：{attempts_b} 次"

    at_raise = requests_seen
    time.sleep(0.5)
    assert requests_seen == at_raise, "抛出后仍有新请求出网"
