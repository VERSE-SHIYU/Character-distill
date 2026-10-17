# -*- coding: utf-8 -*-
"""原文的章节标题 → 章号与原文起点。**只此一处**认章节标题。

只认行首的「第N章」，N 为阿拉伯数字。认不出、章号重复（如分卷后重新编号）或不递增时
返回 None：调用方据此判定「这本书没有可用的章节标题」，不猜。

与 `core/scene_indexer.py` 的 `SCENE_BREAKS` 分工：那条是场景切分的触发词（不锚行首、
也认「节」「回」和时间词），用来切块；这条只回答「第几章从原文哪里开始」，口径不同，不合并。
"""
from __future__ import annotations

import re

_HEADING = re.compile(r"(?m)^[ \t　]*第(\d+)章")
_FILLER = re.compile(r"[…—\-·.。\s]+")


def chapter_starts(source_text: str) -> list[tuple[int, int]] | None:
    """``[(章号, 原文起点), …]``，按原文顺序；没有可用的章节标题时返回 None。"""
    hits = [(int(m.group(1)), m.start()) for m in _HEADING.finditer(source_text)]
    numbers = [n for n, _ in hits]
    if not hits or len(set(numbers)) != len(numbers) or numbers != sorted(numbers):
        return None
    return hits


def chapter_openings(source_text: str, chapters: list[tuple[int, int]],
                     width: int = 40) -> list[tuple[int, str]]:
    """``[(章号, 本章开头 width 字), …]``：标题行之后的正文开头，跳过只有省略号或破折号的行。"""
    cuts = [raw for _, raw in chapters] + [len(source_text)]
    out: list[tuple[int, str]] = []
    for (n, _), a, b in zip(chapters, cuts, cuts[1:]):
        lines = source_text[a:b].split("\n")[1:]                       # 去掉标题行
        body = [s for s in (ln.strip() for ln in lines) if s and _FILLER.fullmatch(s) is None]
        out.append((n, " ".join(body)[:width]))
    return out
