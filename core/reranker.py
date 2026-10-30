"""qwen3-rerank 重排：调用重排接口的唯一一处。

截止时刻照 ``core.embeddings`` 的 D2 做法：读同一个 ``current_embed_deadline()``，
有 deadline 时每次尝试 ``timeout = min(ceiling, 剩余 − margin)``、≤3 次、剩余不够
直接放弃；没有 deadline（非工具路径）才走 ``timeout=ceiling``、≤3 次。失败抛
``RerankUnavailable``，由调用方退回向量顺序。
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from adapters.llm_adapter import _RETRYABLE_STATUS
from core.embeddings import (
    DASHSCOPE_BASE_URLS,
    _EMBED_ATTEMPT_S,
    _EMBED_ATTEMPTS,
    _EMBED_MARGIN_S,
    _EMBED_MIN_S,
    current_embed_deadline,
)
from core.http_keepalive import EMBED_KEEPALIVE_S, KEEPALIVE_SOCKET_OPTIONS

logger = logging.getLogger(__name__)

RERANK_MODEL = "qwen3-rerank"
_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


# 按地域缓存的长期客户端（httpx 连接池复用）。每次新建 Client 要重新握手：D15 实测建连
# p50 684 ms；复用连接池省掉这次建连（D15b 已复现不了 878 ms 的复用后总延迟，不再引用
# 那组数）。httpx.Client 可跨线程共享
# （httpx 0.28.1 ``Client`` docstring「It can be shared between threads」；httpcore 连接池
# 状态在 ThreadLock 内变更）。key 不进客户端：请求头按次传，不同用户共用同一地域的池。
# 超时也按次传（``post(timeout=)``），deadline 逻辑不变。同嵌入 ``core/embeddings.py``
# 构造期建一次 OpenAI 客户端、``with_options`` 共享连接池的做法。
_CLIENTS: dict[str, httpx.Client] = {}
_CLIENTS_LOCK = threading.Lock()


def _client(region: str) -> httpx.Client:
    """该地域的长期客户端：惰性创建，进程内复用。"""
    client = _CLIENTS.get(region)
    if client is not None:
        return client
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(region)
        if client is None:
            # 传 transport 后 Client 级 limits 不生效（httpx _client.py:728），
            # 保活与 socket 选项必须放进 HTTPTransport；环境代理同时被关掉（:685）。
            client = httpx.Client(
                timeout=_EMBED_ATTEMPT_S,
                transport=httpx.HTTPTransport(
                    limits=httpx.Limits(
                        max_connections=100,
                        max_keepalive_connections=20,
                        keepalive_expiry=EMBED_KEEPALIVE_S,
                    ),
                    socket_options=KEEPALIVE_SOCKET_OPTIONS,
                ),
            )
            _CLIENTS[region] = client
        return client


class RerankUnavailable(RuntimeError):
    """重排本次不可用。``kind`` 供调用方写日志；``status`` 是 HTTP 状态码（有则带）。"""

    def __init__(self, kind: str, status: int | None = None) -> None:
        detail = f" ({status})" if status is not None else ""
        super().__init__(f"rerank unavailable: {kind}{detail}")
        self.kind = kind
        self.status = status

    def __reduce__(self):
        # 默认 reduce 只交 args（已格式化的整句），重建后 __init__ 会把整句再当 kind
        # 套一层前缀，str() 变成 "...: rerank unavailable: ..."。交原始字段才对。
        return (type(self), (self.kind, self.status))


def _endpoint(region: str) -> str:
    """重排地址：与嵌入共用地域表，去掉兼容模式后缀换成重排路径。"""
    base = DASHSCOPE_BASE_URLS.get(region)
    if base is None:
        raise RerankUnavailable("unknown_region")
    return base.rsplit("/compatible-mode", 1)[0] + _RERANK_PATH


def _retry_after(resp: httpx.Response) -> float | None:
    val = (resp.headers or {}).get("Retry-After", "")
    if val and val.isdigit():
        return min(float(val), 5.0)
    return None


def _sleep_within(deadline: float | None, wait: float) -> None:
    """等待重试；有 deadline 时不睡过预算（剩余不够下一次尝试就交给循环顶部拒绝）。"""
    if deadline is None:
        time.sleep(wait)
        return
    remaining = deadline - time.monotonic()
    if remaining - _EMBED_MARGIN_S < _EMBED_MIN_S:
        raise RerankUnavailable("deadline")
    time.sleep(min(wait, remaining - _EMBED_MARGIN_S))


class Ranked(list):
    """``rerank()`` 的返回值：仍是按相关度降序的 ``(候选下标, 相关度)`` 列表（值不变）。

    ``total_tokens`` 是响应 ``usage.total_tokens``（没有则 None），供调用方带进证据统计。
    """

    def __init__(self, pairs=(), *, total_tokens: int | None = None) -> None:
        super().__init__(pairs)
        self.total_tokens = total_tokens


def _parse(resp: httpx.Response) -> Ranked:
    try:
        body = resp.json()
        results = (body.get("output") or {}).get("results") or []
        pairs = [(int(it["index"]), float(it["relevance_score"])) for it in results]
    except (AttributeError, TypeError, ValueError, KeyError) as exc:
        raise RerankUnavailable("bad_response") from exc
    if not pairs:
        raise RerankUnavailable("empty")
    return Ranked(sorted(pairs, key=lambda p: p[1], reverse=True),
                  total_tokens=_total_tokens(body))


def _total_tokens(body: dict) -> int | None:
    """``usage.total_tokens``：有就读，没有或不是整数记 None（不编数）。"""
    usage = body.get("usage")
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None


def rerank(
    query: str, docs: list[str], *, api_key: str, region: str
) -> Ranked:
    """给 ``docs`` 打分，返回 ``(候选下标, 相关度)``，按相关度降序（``Ranked``，带 token 数）。

    成功时记一条 INFO：``n_docs`` / ``elapsed_ms``（含重试与等待）/ ``attempts`` /
    ``total_tokens`` —— spec S4 的延迟与用量从这里量。

    空文档列表不调接口；没有 key 直接视为不可用（由调用方退回向量顺序）。
    """
    if not docs:
        return Ranked()
    if not api_key:
        raise RerankUnavailable("no_key")

    url = _endpoint(region)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": RERANK_MODEL,
        "input": {"query": query, "documents": list(docs)},
        "parameters": {"return_documents": False, "top_n": len(docs)},
    }

    deadline = current_embed_deadline()
    started = time.monotonic()
    attempt = 0
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining - _EMBED_MARGIN_S < _EMBED_MIN_S:
                raise RerankUnavailable("deadline")
            timeout = min(_EMBED_ATTEMPT_S, remaining - _EMBED_MARGIN_S)
        else:
            timeout = _EMBED_ATTEMPT_S

        attempt += 1
        failure: RerankUnavailable
        try:
            resp = _client(region).post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            failure = RerankUnavailable("timeout")
        except httpx.TransportError:
            failure = RerankUnavailable("network")
        else:
            status = resp.status_code
            if 200 <= status < 300:
                ranked = _parse(resp)
                logger.info(
                    "rerank ok: n_docs=%d elapsed_ms=%d attempts=%d total_tokens=%s",
                    len(docs), round((time.monotonic() - started) * 1000), attempt,
                    ranked.total_tokens,
                )
                return ranked
            if status not in _RETRYABLE_STATUS and status < 500:
                raise RerankUnavailable("http", status)
            failure = RerankUnavailable(
                "rate_limit" if status == 429 else "http", status
            )
            if attempt >= _EMBED_ATTEMPTS:
                raise failure
            _sleep_within(deadline, _retry_after(resp) or 0.3)
            continue

        if attempt >= _EMBED_ATTEMPTS:
            raise failure
        _sleep_within(deadline, 0.3)
