"""D19 + D8 目标检查：建索引嵌入有界并发 + 瞬时 429 按官方退避恢复。

测的是「达成目标」，不是「符合 spec」：

1. D19：55 批（550 条）交给替身客户端（每次固定睡 0.2 s），改后总耗时必须
   ≤ 顺序耗时 / 4，且同时在途调用数 ≤ 并发上限；返回向量逐条等于顺序版。
2. D8：某一批前 3 次抛 429（``openai.RateLimitError``，照生产构造）、第 4 次成功，
   喂 3 批；改后返回 30 条向量且逐条等于无故障结果。

实现前两条都是红的：当前是一次一批顺序发、且单批失败直接包成
``RuntimeError("百炼 embedding 失败…")``。退避用替身时钟，不真睡。
"""

from __future__ import annotations

import threading
import time
import uuid

import httpx
import openai
import pytest

import core.embeddings as embeddings_mod
from core.embeddings import (
    DashScopeEmbedding,
    _SHARED_CACHE,
    _SHARED_CACHE_LOCK,
    reset_embed_stats,
)

CONCURRENCY_LIMIT = 8  # spec v5 §2-7：EMBED_BUILD_CONCURRENCY
DELAY_S = 0.2
BATCHES = 55
BATCH_SIZE = embeddings_mod.DashScopeEmbedding.MAX_BATCH


def _vec_for(text: str) -> list[float]:
    """确定性的逐文本向量：顺序版与并发版必须得到同一份。"""
    return [float(len(text)), float(sum(text.encode("utf-8")) % 1000)]


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
    """替身客户端：``with_options`` 返回自身，于是 ``_client_no_retry`` 也走同一替身。"""

    def __init__(self, create):
        self.embeddings = _FakeEmbeddings(create)

    def with_options(self, **_kwargs):
        return self


@pytest.fixture(autouse=True)
def _clean_module_state():
    reset_embed_stats()
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE.clear()


def _install(monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(openai, "OpenAI", lambda **_kwargs: client)


def _texts(n: int, tag: str) -> list[str]:
    prefix = uuid.uuid4().hex[:8]
    return [f"{prefix}-{tag}-{i:04d}-padding" for i in range(n)]


def _rate_limit_error() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://dashscope.invalid/v1/embeddings")
    response = httpx.Response(429, request=request)
    return openai.RateLimitError("429 rate limited", response=response, body=None)


class _SlowClient:
    """每次调用固定睡 ``delay``；记录在途峰值与调用次数。"""

    def __init__(self, delay: float = DELAY_S):
        self.delay = delay
        self.lock = threading.Lock()
        self.inflight = 0
        self.peak = 0
        self.calls = 0
        self.client = _FakeClient(self._create)

    def _create(self, *, input, **_kwargs):
        texts = input if isinstance(input, list) else [input]
        with self.lock:
            self.inflight += 1
            self.calls += 1
            self.peak = max(self.peak, self.inflight)
        try:
            time.sleep(self.delay)
            return _FakeResponse([_vec_for(t) for t in texts])
        finally:
            with self.lock:
                self.inflight -= 1


class _Transient429Client:
    """带 ``TRANSIENT`` 的那批：前 3 次抛 429，第 4 次起成功。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.failures = 0
        self.calls = 0
        self.client = _FakeClient(self._create)

    def _create(self, *, input, **_kwargs):
        texts = input if isinstance(input, list) else [input]
        marked = any("TRANSIENT" in t for t in texts)
        with self.lock:
            self.calls += 1
            if marked and self.failures < 3:
                self.failures += 1
                raise _rate_limit_error()
        return _FakeResponse([_vec_for(t) for t in texts])


@pytest.mark.xfail(
    strict=True,
    reason="D19 实现前是顺序发：550 条 ≈ 11 s，且峰值为 1（不是红在并发上，是耗时）",
)
def test_goal_1_bounded_concurrency_is_at_least_4x_faster(monkeypatch):
    slow = _SlowClient()
    _install(monkeypatch, slow.client)
    emb = DashScopeEmbedding(api_key="test_key")

    texts = _texts(BATCHES * BATCH_SIZE, "fast")
    expected = [_vec_for(t) for t in texts]

    started = time.monotonic()
    out = emb(texts)
    elapsed = time.monotonic() - started

    sequential = BATCHES * DELAY_S
    assert slow.calls == BATCHES
    assert elapsed <= sequential / 4, (
        f"耗时 {elapsed:.2f}s 未达到顺序版 {sequential:.2f}s 的 1/4"
    )
    assert slow.peak <= CONCURRENCY_LIMIT, f"在途峰值 {slow.peak} 超过 {CONCURRENCY_LIMIT}"
    assert out == expected, "并发返回的向量与顺序版不一致"


@pytest.mark.xfail(
    strict=True,
    reason="D8 实现前单批 429 直接包成 RuntimeError，不会按官方退避重试",
)
def test_goal_2_transient_429_on_one_batch_recovers(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(
        "tenacity.nap.time.sleep", lambda seconds: sleeps.append(seconds)
    )

    transient = _Transient429Client()
    _install(monkeypatch, transient.client)
    emb = DashScopeEmbedding(api_key="test_key")

    texts = _texts(3 * BATCH_SIZE, "d8")
    texts[0] = "TRANSIENT-" + texts[0]
    expected = [_vec_for(t) for t in texts]

    out = emb(texts)

    assert len(out) == len(texts)
    assert out == expected, "重试后的向量与无故障结果不一致"
    assert transient.failures == 3
    assert transient.calls == 3 + 1, "3 次 429 + 第 4 次成功"
    assert sleeps, "退避没有经过可注入的 sleep"
