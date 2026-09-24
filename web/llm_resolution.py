# -*- coding: utf-8 -*-
"""解析策略：这一次出站该用**哪个** LLM 实例（spec v5 §2.7），以及**哪一对嵌入凭据**
（缺陷 74）。

纯函数。三类事实在这里各归各位 ——「有没有用户凭据」（配置）、「构造得出实例吗」
（`build_user`，调用方给）、「全局兜底有没有」（`get_global`，调用方给）。于是整套
策略可以在没有数据库、没有环境变量、没有 HTTP 的地方测完（L5 就是这么构造的：
`build_user` 传一个抛错的闭包即可）。`resolve_embedding` 同理：进程环境由调用方
以实参给（`env_key` / `env_region`），本模块不 import os。

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
    """实例的出处 —— 判据是**谁给的 key**，不是「是不是同一个对象」。

    `GLOBAL` = 全局单例那一路（LLM）；`GLOBAL_ENV` = 进程环境变量那一路（嵌入，
    MCP 无用户会话时用服务级 key）。
    """

    USER = "user"
    GLOBAL = "global"
    GLOBAL_ENV = "global_env"
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


@dataclass(frozen=True)
class EmbeddingResolution:
    """一次嵌入凭据解析的结果。`key` 为空 ⟺ `source is UNAVAILABLE`。

    `region` **恒非空** —— 缺省收敛成 `"cn"`（见 `resolve_embedding` 的 docstring）。
    """

    source: Source
    key: str = ""
    region: str = "cn"


def resolve_embedding(
    config: dict[str, Any],
    *,
    env_key: str = "",
    env_region: str = "cn",
) -> EmbeddingResolution:
    """嵌入凭据（key + region）的**唯一**归一出口。

    改前这份「读 key、读 region、给默认值」在三族里各抄了一遍，共 11 处，默认值还
    不一致：A 族（chat / history / group / distill 的会话段）给 `"cn"`，B 族（distill
    的四个蒸馏端点）给 `""`。两处不一致各自都能跑，所以没人发现 —— B 族那个 `""` 一旦
    真流到 `core/rag.py` 的 `config.get("embedding_region", "cn")`，键**在**而值为空，
    `.get` 返回 `""` 而非默认值，`DASHSCOPE_BASE_URLS[""]` 当场 KeyError。

    默认值统一取 **`"cn"`**，不是 `""`：`DASHSCOPE_BASE_URLS` 里没有 `""` 这个键；
    `DashScopeEmbedding.__init__(region="cn")`、两个 store 的读侧 `row[4] or "cn"`、
    以及建表默认 `users.embedding_region TEXT DEFAULT 'cn'` 都已经把 `"cn"` 当作这个
    字段的缺省。`""` 只是「没填过」的语法，不是这个字段的合法取值。

    `env_key` / `env_region` 由调用方给（与 `build_user` / `get_global` 同理）：本模块
    不 import os，才能在没有进程环境的测试里跑完。**用户 key 优先** —— 只有 config 里
    没有用户 key、而 `env_key` 非空时才落 `GLOBAL_ENV`。
    """
    key = config.get("embedding_key") or ""
    if key:
        return EmbeddingResolution(Source.USER, key, config.get("embedding_region") or "cn")
    if env_key:
        return EmbeddingResolution(Source.GLOBAL_ENV, env_key, env_region or "cn")
    return EmbeddingResolution(Source.UNAVAILABLE)


__all__ = [
    "Source",
    "Resolution",
    "EmbeddingResolution",
    "resolve_llm",
    "resolve_embedding",
]
