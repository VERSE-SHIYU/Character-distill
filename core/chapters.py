# -*- coding: utf-8 -*-
"""原文的章节标题 → 章号与原文起点。**只此一处**认章节标题。

只认行首的「第N章」，N 为阿拉伯数字。认不出、章号重复（如分卷后重新编号）或不递增时
返回 None：调用方据此判定「这本书没有可用的章节标题」，不猜。

与 `core/scene_indexer.py` 的 `SCENE_BREAKS` 分工：那条是场景切分的触发词（不锚行首、
也认「节」「回」和时间词），用来切块；这条只回答「第几章从原文哪里开始」，口径不同，不合并。
"""
from __future__ import annotations

import re
from bisect import bisect_right

from core.quotes import normalize

_HEADING = re.compile(r"(?m)^[ \t　]*第(\d+)章")


def chapter_starts(source_text: str) -> list[tuple[int, int]] | None:
    """``[(章号, 原文起点), …]``，按原文顺序；没有可用的章节标题时返回 None。"""
    hits = [(int(m.group(1)), m.start()) for m in _HEADING.finditer(source_text)]
    numbers = [n for n, _ in hits]
    if not hits or len(set(numbers)) != len(numbers) or numbers != sorted(numbers):
        return None
    return hits


def chapter_norm_starts(source_text: str, chapters: list[tuple[int, int]],
                        source_norm: str) -> list[int] | None:
    """各章起点在**规范化**原文里的偏移（与 ``chapters`` 一一对应）；拼不回整段时 None。

    逐段规范化累加，与 `core.quotes.paragraph_bounds` 同一条不变式：累加长度 == 规范化整段长度，
    对不上就不给（宁可不用，不可用错坐标）。不用 `normalized_starts`：那条每个起点都重算一遍前缀，
    509 章 × 百万字太慢。
    """
    cuts = [raw for _, raw in chapters] + [len(source_text)]
    acc = len(normalize(source_text[:cuts[0]]))
    starts: list[int] = []
    for a, b in zip(cuts, cuts[1:]):
        starts.append(acc)
        acc += len(normalize(source_text[a:b]))
    return starts if acc == len(source_norm) else None


def chapter_at(chapters: list[tuple[int, int]], norm_starts: list[int], pos: int) -> int | None:
    """规范化位置 ``pos`` 所在的章号；在第一章之前返回 None。"""
    i = bisect_right(norm_starts, pos) - 1
    return chapters[i][0] if i >= 0 else None
