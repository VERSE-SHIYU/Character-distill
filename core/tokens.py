# -*- coding: utf-8 -*-
"""全仓库唯一的 token 计数 —— DeepSeek 官方 tokenizer 精确计数。

为什么是官方 tokenizer 而不是按字数估：估算系数（0.6 / 0.8 / 1.5）彼此不一致，
同一段文本在「能不能一次读完」和「记了多少账」两处会算出不同的数，且随文体漂移
（小说实测 0.680–0.701 token/字）。精确计数只此一处，全仓不再有第二套。

模型局限：线上默认模型是 `deepseek-flash`，对它是精确值；用户在设置页换别家模型时
这个数对那个模型是近似值（spec §3.0「已知局限」）。

`tokenizer.json` 与 `SOURCE.md` 同放在 `assets/deepseek_tokenizer/`。文件 6.4MB，
惰性单例 + 锁：首调用读一次，之后复用；并发首调用也只读一次（见 tests/test_tokens.py U3）。
"""
from __future__ import annotations

import threading
from pathlib import Path

from tokenizers import Tokenizer

_TOKENIZER_PATH = (
    Path(__file__).resolve().parent / "assets" / "deepseek_tokenizer" / "tokenizer.json"
)

_tokenizer: Tokenizer | None = None
_tokenizer_lock = threading.Lock()


def _get_tokenizer() -> Tokenizer:
    global _tokenizer
    if _tokenizer is None:
        with _tokenizer_lock:
            if _tokenizer is None:
                _tokenizer = Tokenizer.from_file(str(_TOKENIZER_PATH))
    return _tokenizer


def count_tokens(text: str) -> int:
    """`text` 的 token 数。空串 → 0，不触发 tokenizer 加载。"""
    if not text:
        return 0
    return len(_get_tokenizer().encode(text, add_special_tokens=False).ids)
