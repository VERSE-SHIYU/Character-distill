"""D19 / D8 行为用例（spec v5 §6）：有界并发、限流、失败语义、上下文传递。

替身照生产行为写：``openai.OpenAI`` 返回同一个替身，``with_options`` 返回标了
``no_retry`` 的视图，于是「单批走 SDK（max_retries=2）、多批走无 SDK 重试克隆」
这一接线也能被观察到。退避与时钟都可注入，不真睡。
"""

from __future__ import annotations

import threading
import time
import uuid

import httpx
import openai
import pytest

import core.embeddings as embeddings_mod
from adapters.llm_adapter import OutboundRefused
from core.embed_rate_limit import EMBED_BUILD_CONCURRENCY, EmbedRateLimiter, reset_limiters
from core.embeddings import (
    DashScopeEmbedding,
    _SHARED_CACHE,
    _SHARED_CACHE_LOCK,
    current_embed_deadline,
    embed_deadline,
    reset_embed_stats,
)

BATCH_SIZE = DashScopeEmbedding.MAX_BATCH


def _vec_for(text: str) -> list[float]:
    return [float(len(text)), float(sum(text.encode("utf-8")) % 1000)]


def _texts(n: int, tag: str) -> list[str]:
    prefix = uuid.uuid4().hex[:8]
    return [f"{prefix}-{tag}-{i:04d}-padding" for i in range(n)]


def _as_lists(vectors) -> list[list[float]]:
    return [list(map(float, v)) for v in vectors]


def _rate_limit_error() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://dashscope.invalid/v1/embeddings")
    response = httpx.Response(429, request=request)
    return openai.RateLimitError("429 rate limited", response=response, body=None)


class _FakeData:
    def __init__(self, index: int, embedding: list[float]):
        self.index = index
        self.embedding = embedding


class _FakeResponse:
    def __init__(self, embeddings: list[list[float]]):
        self.data = [_FakeData(i, emb) for i, emb in enumerate(embeddings)]


class _FakeEmbeddings:
    def __init__(self, owner: "_FakeClient", kind: str):
        self._owner = owner
        self._kind = kind

    def create(self, *, input, **kwargs):
        texts = input if isinstance(input, list) else [input]
        return self._owner._create(texts, self._kind)


class _FakeClient:
    """同一个 create；``with_options`` 给出仅标签不同的克隆。"""

    def __init__(self, create):
        self._create = create
        self.embeddings = _FakeEmbeddings(self, "sdk")

    def with_options(self, **_kwargs):
        clone = object.__new__(_FakeClient)
        clone._create = self._create
        clone.embeddings = _FakeEmbeddings(clone, "no_retry")
        return clone


def _install(monkeypatch, create) -> None:
    monkeypatch.setattr(openai, "OpenAI", lambda **_kwargs: _FakeClient(create))


def _ok(texts: list[str]) -> _FakeResponse:
    return _FakeResponse([_vec_for(t) for t in texts])


@pytest.fixture(autouse=True)
def _clean_module_state():
    reset_embed_stats()
    reset_limiters()
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE.clear()
    yield
    reset_embed_stats()
    reset_limiters()


class _Clock:
    """可注入单调时钟；advancer 线程把它推快，于是限流等待不真睡。"""

    def __init__(self):
        self._t = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._t

    def advance(self, dt: float) -> None:
        with self._lock:
            self._t += dt


class _Advancer:
    def __init__(self, clock: _Clock, limiter: EmbedRateLimiter,
                 step: float = 0.05, interval: float = 0.002):
        self.clock = clock
        self.limiter = limiter
        self.step = step
        self.interval = interval
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.is_set():
            time.sleep(self.interval)
            self.clock.advance(self.step)
            self.limiter.wake()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=5)
        return False


# ── 在途峰值、顺序 ─────────────────────────────────────────────────────────


class _Slow:
    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.lock = threading.Lock()
        self.inflight = 0
        self.peak = 0
        self.calls = 0

    def create(self, texts, _kind):
        with self.lock:
            self.inflight += 1
            self.calls += 1
            self.peak = max(self.peak, self.inflight)
        try:
            time.sleep(self.delay)
            return _ok(texts)
        finally:
            with self.lock:
                self.inflight -= 1


def test_inflight_peak_is_bounded_and_order_matches_input(monkeypatch):
    slow = _Slow()
    _install(monkeypatch, slow.create)
    emb = DashScopeEmbedding(api_key="test_key")

    texts = _texts(20 * BATCH_SIZE, "peak")
    out = emb(texts)

    assert slow.calls == 20
    assert 1 < slow.peak <= EMBED_BUILD_CONCURRENCY, f"在途峰值 {slow.peak}"
    assert _as_lists(out) == [_vec_for(t) for t in texts]


# ── 单批查询：不建池、不拿令牌、仍走 SDK 重试 ──────────────────────────


def test_single_batch_uses_sdk_client_without_pool_or_tokens(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    def create(texts, kind):
        calls.append((kind, list(texts)))
        return _ok(texts)

    def _forbidden(*_a, **_kw):
        raise AssertionError("单批路径不得建池 / 拿令牌")

    _install(monkeypatch, create)
    monkeypatch.setattr(embeddings_mod, "get_limiter", _forbidden)
    monkeypatch.setattr(embeddings_mod, "ThreadPoolExecutor", _forbidden)

    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(BATCH_SIZE, "single")
    out = emb(texts)

    assert len(calls) == 1
    assert calls[0] == ("sdk", texts)
    assert _as_lists(out) == [_vec_for(t) for t in texts]


# ── contextvar 传到工作线程 ─────────────────────────────────────────────


def test_worker_thread_sees_caller_deadline(monkeypatch):
    seen: list[float | None] = []
    lock = threading.Lock()

    def create(texts, _kind):
        with lock:
            seen.append(current_embed_deadline())
        return _ok(texts)

    _install(monkeypatch, create)
    emb = DashScopeEmbedding(api_key="test_key")

    with embed_deadline(12345.5):
        out = emb(_texts(3 * BATCH_SIZE, "deadline"))

    assert len(out) == 3 * BATCH_SIZE
    assert seen and all(v == 12345.5 for v in seen), seen


# ── 令牌桶：空桶启动、容量 = 配额、两个桶都生效 ──────────────────────────


def test_bucket_starts_empty_and_caps_at_capacity():
    clock = _Clock()
    limiter = EmbedRateLimiter(rpm=120, tpm=10**9, clock=clock)

    assert not limiter.try_acquire(0), "空桶启动：t=0 不得取到令牌"
    clock.advance(0.4)
    assert not limiter.try_acquire(0)
    clock.advance(0.2)
    assert limiter.try_acquire(0)

    clock.advance(1000)
    granted = sum(1 for _ in range(120) if limiter.try_acquire(0))
    assert granted == 120, "容量必须等于配额"
    assert not limiter.try_acquire(0)


def test_tpm_bucket_is_enforced_too():
    clock = _Clock()
    limiter = EmbedRateLimiter(rpm=10**9, tpm=120, clock=clock)
    clock.advance(60)  # 120 TPM tokens

    assert limiter.try_acquire(100)
    assert not limiter.try_acquire(30)


def test_receipt_rate_never_exceeds_quota_per_second(monkeypatch):
    clock = _Clock()
    # 2 RPM/s 的桶：到达时刻必须被拉开；若令牌在入队时拿，5 批会挤在一起。
    limiter = EmbedRateLimiter(rpm=120, tpm=10**9, clock=clock)
    monkeypatch.setattr(embeddings_mod, "get_limiter", lambda *_a: limiter)

    arrivals: list[float] = []
    lock = threading.Lock()

    def create(texts, _kind):
        with lock:
            arrivals.append(clock())
        return _ok(texts)

    _install(monkeypatch, create)
    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(5 * BATCH_SIZE, "rate")

    with _Advancer(clock, limiter):
        started = clock()
        out = emb(texts)

    assert len(out) == 5 * BATCH_SIZE
    arrivals.sort()
    assert len(arrivals) == 5
    assert arrivals[0] - started >= 0.4, "空桶启动第一秒不应立即放量"
    windows = [len([b for b in arrivals if a <= b < a + 1.0]) for a in arrivals]
    assert max(windows) <= 3, f"每秒到达数超过 2/s 配额：{arrivals}"


# ── 失败语义 ───────────────────────────────────────────────────────────


def test_kth_batch_failure_keeps_original_runtime_error_and_stops_dispatch(
    monkeypatch,
):
    clock = _Clock()
    limiter = EmbedRateLimiter(rpm=120, tpm=10**9, clock=clock)
    monkeypatch.setattr(embeddings_mod, "get_limiter", lambda *_a: limiter)

    sent: list[int] = []
    lock = threading.Lock()

    def create(texts, _kind):
        with lock:
            sent.append(len(sent) + 1)
        if any("FAIL" in t for t in texts):
            raise RuntimeError("boom")
        return _ok(texts)

    _install(monkeypatch, create)
    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(55 * BATCH_SIZE, "kth")
    texts[BATCH_SIZE] = "FAIL-" + texts[BATCH_SIZE]

    with _Advancer(clock, limiter):
        with pytest.raises(RuntimeError) as ei:
            emb(texts)

    assert str(ei.value).startswith("百炼 embedding 失败：第2批，10条")
    assert isinstance(ei.value.__cause__, RuntimeError)
    assert len(sent) <= 3, f"失败后仍在发新批：{len(sent)}"


def test_outbound_refused_keeps_original_type(monkeypatch):
    def create(texts, _kind):
        if any("OUTBOUND" in t for t in texts):
            raise OutboundRefused("blocked before egress")
        return _ok(texts)

    _install(monkeypatch, create)
    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(3 * BATCH_SIZE, "gate")
    texts[0] = "OUTBOUND-" + texts[0]

    with pytest.raises(OutboundRefused):
        emb(texts)


def test_moderation_falls_back_per_item_with_zero_vector(monkeypatch):
    def create(texts, _kind):
        if any("BAD" in t for t in texts):
            raise RuntimeError("DataInspectionFailed: content_filter")
        return _ok(texts)

    _install(monkeypatch, create)
    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(2 * BATCH_SIZE, "mod")
    texts[1] = "BAD-" + texts[1]
    expected = [_vec_for(t) for t in texts]

    out = _as_lists(emb(texts))

    assert len(out) == len(texts)
    assert out[1] == [0.0] * emb._dimensions
    assert out[0] == expected[0]
    assert out[2:] == expected[2:]


def test_rate_limit_retries_six_times_with_injectable_backoff(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda seconds: sleeps.append(seconds))

    calls: list[tuple[str, ...]] = []
    lock = threading.Lock()

    def create(texts, _kind):
        with lock:
            calls.append(tuple(texts))
        if any("FAIL" in t for t in texts):
            raise _rate_limit_error()
        return _ok(texts)

    _install(monkeypatch, create)
    emb = DashScopeEmbedding(api_key="test_key")
    texts = _texts(3 * BATCH_SIZE, "d8")
    texts[0] = "FAIL-" + texts[0]

    with pytest.raises(RuntimeError) as ei:
        emb(texts)

    assert str(ei.value).startswith("百炼 embedding 失败：第1批，10条")
    assert isinstance(ei.value.__cause__, openai.RateLimitError)
    failing_calls = [c for c in calls if any("FAIL" in t for t in c)]
    assert len(failing_calls) == 6, f"最多 6 次尝试，实际 {len(failing_calls)}"
    assert len(sleeps) == 5
    assert all(1.0 <= s <= 60.0 for s in sleeps), sleeps
