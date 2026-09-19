# -*- coding: utf-8 -*-
"""解析策略：这一次出站该用**哪个** LLM 实例（spec v5 §2.7）。

纯函数。三类事实在这里各归各位 ——「有没有用户凭据」（配置）、「构造得出实例吗」
（`build_user`，调用方给）、「全局兜底有没有」（`get_global`，调用方给）。于是整套
策略可以在没有数据库、没有环境变量、没有 HTTP 的地方测完（L5 就是这么构造的：
`build_user` 传一个抛错的闭包即可）。

**本模块不做的事**（分层不串的判据）：
  - 缓存与 IO 在 `web/deps.py` —— 谁是单例、用户的 key 变了要不要重建，是它的账；
  - geo 判定在 `web/llm_gate.py` —— 本函数连 IP 都看不见；
  - 出站拒绝在 `adapters/llm_adapter.py` —— 本函数只**选**实例，不判它出不出得去。

所以这里没有 `import` 出去的依赖，只有 `collections.abc` 与 `dataclasses`。
"""
from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class Source(enum.Enum):
    """实例的出处 —— 判据是**谁给的 key**，不是「是不是同一个对象」。"""

    USER = "user"
    GLOBAL = "global"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Resolution:
    """一次解析的结果。`reason` 非空 = 这次回落**有账要记**（D2 的唯一留痕处）。

    `llm is None` 与 `source is UNAVAILABLE` 是同一件事的两种读法，都在这里；
    调用方按 `source` 决定缓存不缓存、按 `llm` 决定 503 不 503。
    """

    source: Source
    llm: Any
    reason: str = ""


def resolve_llm(
    config: dict[str, Any],
    *,
    build_user: Callable[[dict[str, Any]], Any],
    get_global: Callable[[], Any],
) -> Resolution:
    """四情形表（L5 逐格钉住）：

    | 有用户 key | 构造用户实例 | 全局 | 结果 |
    |---|---|---|---|
    | 是 | 成功 | — | USER |
    | 是 | 抛错 | 有 | GLOBAL，reason 写明异常 |
    | 是 | 抛错 | 无 | UNAVAILABLE，reason 写明异常 |
    | 否 | 不试 | 有 / 无 | GLOBAL / UNAVAILABLE |

    **构造失败不抛给调用方**：D2 定的是「初始化失败回落全局 key」，不是「请求失败」。
    但失败必须**留痕** —— reason 就是那笔账，由调用方打日志。这是「失败吞成成功」族里
    唯一被允许的吞法：吞了，但记了。
    """
    reason = ""
    if config.get("api_key"):
        try:
            return Resolution(Source.USER, build_user(config), "")
        except Exception as exc:
            reason = f"per-user LLM init failed, falling back to global: {exc}"
    llm = get_global()
    if llm is None:
        return Resolution(Source.UNAVAILABLE, None, reason)
    return Resolution(Source.GLOBAL, llm, reason)


__all__ = ["Source", "Resolution", "resolve_llm"]
