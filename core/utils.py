"""Shared utility functions for the core package."""

from __future__ import annotations

from typing import Any

from core.scheduling import submit_to_main_loop

# 字符→token 的估算系数。**唯一出处**：chat_stream 的兜底与这里必须同源，
# 各写一份的话改系数必漏一边（缺陷 16「出口分散」的形态）。
_CHARS_PER_TOKEN = 1.5


def estimate_usage_from_chars(prompt_chars: int, completion_chars: int = 0) -> dict:
    """真实 usage 不可得时的估算兜底 —— 字符→token 的**唯一出口**。

    两处会走到这里：厂商全程不回 usage chunk（chat_stream 兜底）、请求在拿到
    响应前就失败（超时/429 重试墙耗尽，客户端根本看不到 usage）。返回值带
    ``estimated=True``，与真实值在库里是**可区分的结构化字段**（usage_stats.is_estimated），
    不是文案 —— 查询时能过滤、能分别统计。
    """
    return {
        "prompt_tokens": int(prompt_chars / _CHARS_PER_TOKEN),
        "completion_tokens": int(completion_chars / _CHARS_PER_TOKEN),
        "estimated": True,
    }


def aggregate_usage(usages: list[dict | None], chunk_count: int) -> dict | None:
    """把并发分片的 usage 汇总成**一条**记录的载荷（Map 阶段用）。

    - 分片可能是真实值（成功）也可能是估算值（失败/厂商未回）→ 汇总后
      ``estimated=True`` 的语义是「**含**估算值」而非「全部估算」：聚合天然可能混合，
      整条汇总行按保守侧标注，真实值不会被标成零成本、估算值也不会冒充真实值。
    - ``chunk_count`` 随载荷一起走，由 :func:`try_record_usage` 落到 `usage_stats.chunk_count`。
      它是本次聚合**调了几次 LLM**，不是文本分片数（续跑命中/重试会让二者不一致）。
    - 全部为空 → ``None``（没有任何可记账的事实，不写零成本假记录）。
    """
    present = [u for u in usages if u]
    if not present:
        return None
    return {
        "prompt_tokens": sum(u.get("prompt_tokens", 0) for u in present),
        "completion_tokens": sum(u.get("completion_tokens", 0) for u in present),
        "estimated": any(u.get("estimated", False) for u in present),
        "chunk_count": chunk_count,
    }


def try_record_usage(
    storage: Any,
    user_id: str,
    llm: Any,
    action: str = "chat",
    usage: dict | None = None,
    source: str = "core",
) -> None:
    """Record LLM token usage to storage **without blocking the caller**.

    Args:
        storage: Storage backend with ``record_usage`` coroutine.
        user_id: The user whose usage to record.
        llm: LLM adapter instance (expects ``last_usage`` dict and ``_model``).
        action: Label for the usage record (e.g. ``"chat"``, ``"distill"``).
        usage: Optional usage dict; falls back to ``llm.last_usage``.
        source: Source name for error messages (e.g. ``"ChatEngine"``, ``"Distiller"``).

    投递经 ``core.scheduling.submit_to_main_loop(wait=False)`` —— 唯一的跨 loop 出口。
    此前这里自建 event loop 起线程写库，等于把「投递」这件事又定义了一遍（缺陷 16 形态）。
    """
    if not storage or not user_id:
        print(f"[{source}] usage not recorded: storage/user_id missing (user={user_id}, action={action})")
        return
    if usage is None:
        usage = llm.last_usage
    if not usage:
        print(f"[{source}] usage not recorded: no usage data (user={user_id}, action={action})")
        return
    model = getattr(llm, "_model", "") or ""
    pt = usage.get("prompt_tokens", 0)
    ct = usage.get("completion_tokens", 0)
    is_est = bool(usage.get("estimated", False))
    chunk_count = usage.get("chunk_count")

    async def _write() -> None:
        try:
            await storage.record_usage(user_id, action, pt, ct, model,
                                       is_estimated=is_est, chunk_count=chunk_count)
        except Exception as exc:
            print(f"[{source}] Record usage failed (non-fatal): {exc}")

    submit_to_main_loop(_write(), wait=False)
