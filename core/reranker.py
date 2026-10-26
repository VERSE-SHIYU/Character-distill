"""qwen3-rerank 重排：调用重排接口的唯一一处。

截止时刻照 ``core.embeddings`` 的 D2 做法：读同一个 ``current_embed_deadline()``，
有 deadline 时每次尝试 ``timeout = min(ceiling, 剩余 − margin)``、≤3 次、剩余不够
直接放弃；没有 deadline（非工具路径）才走 ``timeout=ceiling``、≤3 次。失败抛
``RerankUnavailable``，由调用方退回向量顺序。
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

RERANK_MODEL = "qwen3-rerank"
_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


class RerankUnavailable(RuntimeError):
    """重排本次不可用。``kind`` 供调用方写日志；``status`` 是 HTTP 状态码（有则带）。"""

    def __init__(self, kind: str, status: int | None = None) -> None:
        detail = f" ({status})" if status is not None else ""
        super().__init__(f"rerank unavailable: {kind}{detail}")
        self.kind = kind
        self.status = status


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


def _parse(resp: httpx.Response) -> list[tuple[int, float]]:
    try:
        results = (resp.json().get("output") or {}).get("results") or []
        pairs = [(int(it["index"]), float(it["relevance_score"])) for it in results]
    except (AttributeError, TypeError, ValueError, KeyError) as exc:
        raise RerankUnavailable("bad_response") from exc
    if not pairs:
        raise RerankUnavailable("empty")
    return sorted(pairs, key=lambda p: p[1], reverse=True)


def rerank(
    query: str, docs: list[str], *, api_key: str, region: str
) -> list[tuple[int, float]]:
    """给 ``docs`` 打分，返回 ``(候选下标, 相关度)``，按相关度降序。

    空文档列表不调接口；没有 key 直接视为不可用（由调用方退回向量顺序）。
    """
    if not docs:
        return []
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
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException:
            failure = RerankUnavailable("timeout")
        except httpx.TransportError:
            failure = RerankUnavailable("network")
        else:
            status = resp.status_code
            if 200 <= status < 300:
                return _parse(resp)
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
