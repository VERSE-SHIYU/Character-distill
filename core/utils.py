"""Shared utility functions for the core package."""

from __future__ import annotations

import logging
from typing import Any

from core import task_log
from core.nonfatal import nonfatal
from core.request_context import current_user_id
from core.scheduling import submit_to_main_loop
from core.tokens import count_tokens

logger = logging.getLogger(__name__)


# 用量 dict 里「厂商可能不回」的字段（DeepSeek 专有）。字段名与含义以官方文档为准：
# https://api-docs.deepseek.com/guides/kv_cache 。别的厂商不回 → 值为 ``None`` →
# 落库 ``NULL``；**不写 0** —— 0 表示「确实一次没命中」，与「没有这项数据」必须可区分。
CACHE_HIT_FIELD = "prompt_cache_hit_tokens"
CACHE_MISS_FIELD = "prompt_cache_miss_tokens"
CACHE_USAGE_FIELDS = (CACHE_HIT_FIELD, CACHE_MISS_FIELD)


def estimate_usage(prompt_text: str, completion_text: str = "") -> dict:
    """真实 usage 不可得时的估算兜底 —— 传**原文**，内部走唯一的 `count_tokens`。

    两处会走到这里：厂商全程不回 usage chunk（chat_stream 兜底）、请求在拿到
    响应前就失败（超时/429 重试墙耗尽，客户端根本看不到 usage）。返回值带
    ``estimated=True``，与真实值在库里是**可区分的结构化字段**（usage_stats.is_estimated），
    不是文案 —— 查询时能过滤、能分别统计。
    """
    return {
        "prompt_tokens": count_tokens(prompt_text),
        "completion_tokens": count_tokens(completion_text),
        "estimated": True,
    }


def aggregate_usage(usages: list[dict | None], chunk_count: int) -> dict | None:
    """把并发分片的 usage 汇总成**一条**记录的载荷（Map 阶段用）。

    - 分片可能是真实值（成功）也可能是估算值（失败/厂商未回）→ 汇总后
      ``estimated=True`` 的语义是「**含**估算值」而非「全部估算」：聚合天然可能混合，
      整条汇总行按保守侧标注，真实值不会被标成零成本、估算值也不会冒充真实值。
    - 缓存命中/未命中字段（:data:`CACHE_USAGE_FIELDS`）只在**报了值的分片**上求和；
      全员没报 → ``None``（落库 NULL），不折成 0。
    - ``chunk_count`` 随载荷一起走，由 :func:`try_record_usage` 落到 `usage_stats.chunk_count`。
      它是本次聚合**调了几次 LLM**，不是文本分片数（续跑命中/重试会让二者不一致）。
    - 全部为空 → ``None``（没有任何可记账的事实，不写零成本假记录）。
    """
    present = [u for u in usages if u]
    if not present:
        return None
    merged = {
        "prompt_tokens": sum(u.get("prompt_tokens", 0) for u in present),
        "completion_tokens": sum(u.get("completion_tokens", 0) for u in present),
        "estimated": any(u.get("estimated", False) for u in present),
        "chunk_count": chunk_count,
    }
    for name in CACHE_USAGE_FIELDS:
        merged[name] = _sum_optional(present, name)
    return merged


def _sum_optional(usages: list[dict], name: str) -> int | None:
    """只在**报了值**的分片上求和；全员没报 → ``None``。

    「没有数据」不能折成 0：折下去会把「含估算值、缓存字段未知」的汇总行伪造成
    「命中 0」—— 恰好是这两个字段存在的意义（把「没命中」与「没数据」分开）的反面。
    """
    values = [u[name] for u in usages if u.get(name) is not None]
    return sum(values) if values else None


def try_record_usage(
    storage: Any,
    llm: Any,
    *,
    action: str = "chat",
    usage: dict | None = None,
    source: str = "core",
) -> None:
    """Record LLM token usage to storage **without blocking the caller**.

    Args:
        storage: Storage backend with ``record_usage`` coroutine.
        llm: LLM adapter instance (expects ``last_usage`` dict and ``_model``).
        action: Label for the usage record (e.g. ``"chat"``, ``"distill"``).
        usage: Optional usage dict; falls back to ``llm.last_usage``.
        source: Source name for error messages (e.g. ``"ChatEngine"``, ``"Distiller"``).

    **归属在出口读一次**：谁在调由 ``core.request_context.current_user_id()`` 回答
    （请求身份，缺陷 35 收敛的那一个）；调用方不再逐处把 user_id 喂进来 —— 那条路在
    聊天族兑现成了「有的地方只写 `_storage`、有的地方只写 `_user_id`」，写漏一处就
    整条路静默不记账（缺陷 83）。身份在**本函数入口**读，不在 ``_write`` 里读：
    投递出去的那段跑在主 loop 上，那一刻的上下文未必还是发起者的。

    投递经 ``core.scheduling.submit_to_main_loop(wait=False)`` —— 唯一的跨 loop 出口。
    此前这里自建 event loop 起线程写库，等于把「投递」这件事又定义了一遍（缺陷 16 形态）。
    """
    user_id = current_user_id()
    if not storage or not user_id:
        logger.warning("[%s] usage not recorded: storage/user_id missing (user=%s, action=%s)",
                       source, user_id, action)
        return
    if usage is None:
        usage = llm.last_usage
    if not usage:
        logger.warning("[%s] usage not recorded: no usage data (user=%s, action=%s)",
                       source, user_id, action)
        return
    model = getattr(llm, "_model", "") or ""
    pt = usage.get("prompt_tokens", 0)
    ct = usage.get("completion_tokens", 0)
    is_est = bool(usage.get("estimated", False))
    chunk_count = usage.get("chunk_count")
    cache_hit = usage.get(CACHE_HIT_FIELD)
    cache_miss = usage.get(CACHE_MISS_FIELD)
    task_log.add_usage(action, pt, ct, cache_hit, cache_miss)

    async def _write() -> None:
        async with nonfatal("usage", f"record usage ({source}/{action})"):
            await storage.record_usage(user_id, action, pt, ct, model,
                                       is_estimated=is_est, chunk_count=chunk_count,
                                       prompt_cache_hit_tokens=cache_hit,
                                       prompt_cache_miss_tokens=cache_miss)

    submit_to_main_loop(_write(), wait=False)
