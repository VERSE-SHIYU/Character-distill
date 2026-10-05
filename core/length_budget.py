# -*- coding: utf-8 -*-
"""全仓库上限的**唯一来源**（纯常量与纯函数）。

为什么集中：上传、蒸馏选路径、聊天预算三处曾各写各的数字（`1_000_000` 与
`int(len × 0.6)`、`0.8`、`1.5`），改一处漏一处，且单位还不一致（字 vs token）。
阈值、窗口、预留、文件体积只在这里定义一次，其余模块 import 引用。

窗口与预留的关系由**导入时断言**钉住：预留给提示词与输出，阈值必须连同预留在窗口内，
否则「一次读完」的路径会撞上游硬门。这条断言可复用（`assert_budget_fits`），
调大预留即失败。

单位：token 阈值（`LONGCTX_THRESHOLD_TOKENS`、`PROMPT_RESERVE_TOKENS`）用
`core.tokens.count_tokens` 的官方计数；`CHAT_MAX_CHARS` 按**字符**（聊天记录清洗后
仍按字判，D4）；`MAX_FILE_BYTES` 按**字节**（HTTP 层的体积门）。
"""
from __future__ import annotations

from core.tokens import count_tokens

# 官方给定 deepseek-v4-pro 上下文 1M token（https://api-docs.deepseek.com/quick_start/pricing）
CONTEXT_WINDOW_TOKENS = 1_048_576
# 900_000：留给提示词与输出的余量 —— 超过即走分片路径
LONGCTX_THRESHOLD_TOKENS = 900_000
# ≥ 实测提示词 4,807 token，留余量
PROMPT_RESERVE_TOKENS = 8_192
# 聊天记录上限：按字符（D4）
CHAT_MAX_CHARS = 2_000_000
# HTTP 层的单文件体积上限：按字节
MAX_FILE_BYTES = 30 * 1024 * 1024
# 一次读完与合并的输出上限 —— `distiller.LONG_OUTPUT_MAX_TOKENS` 从这里取
LONG_OUTPUT_MAX_TOKENS = 16_384


def assert_budget_fits(window: int, threshold: int, reserve: int, output_max: int) -> None:
    """阈值 + 预留 + 输出上限必须落在窗口内。复用与导入时校验共用这一条判据。"""
    assert threshold + reserve + output_max < window, (
        f"预算越窗：{threshold} + {reserve} + {output_max} >= {window} "
        "（阈值、提示词预留与输出上限之和超过了模型上下文窗口）"
    )


assert_budget_fits(
    CONTEXT_WINDOW_TOKENS, LONGCTX_THRESHOLD_TOKENS, PROMPT_RESERVE_TOKENS,
    LONG_OUTPUT_MAX_TOKENS,
)


def fits_one_pass(n_tokens: int, threshold: int = LONGCTX_THRESHOLD_TOKENS) -> bool:
    """`n_tokens` 能否一次读完 —— 严格小于阈值，恰等于阈值即走分片。"""
    return n_tokens < threshold


_STORY_OVER_LIMIT = "文本共 {n:,} tokens，超过小说上限 {limit:,} tokens，请分卷上传"
_CHAT_OVER_LIMIT = "文本共 {n:,} 字，超过聊天记录上限 {limit:,} 字，请分卷上传"


def check_upload(text: str, text_type: str) -> None:
    """上传前判长度：chat 按字符，其余按 token。超限抛 `ValueError`。"""
    if text_type == "chat":
        n = len(text)
        if n > CHAT_MAX_CHARS:
            raise ValueError(_CHAT_OVER_LIMIT.format(n=n, limit=CHAT_MAX_CHARS))
        return
    n = count_tokens(text)
    if not fits_one_pass(n):
        raise ValueError(_STORY_OVER_LIMIT.format(n=n, limit=LONGCTX_THRESHOLD_TOKENS))


def public_limits() -> dict[str, int]:
    """前端取用的上限（`GET /api/text/limits`）—— 就是上面这些常量本身。"""
    return {
        "story_max_tokens": LONGCTX_THRESHOLD_TOKENS,
        "chat_max_chars": CHAT_MAX_CHARS,
        "max_file_bytes": MAX_FILE_BYTES,
    }
