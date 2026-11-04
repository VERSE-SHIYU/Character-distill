# -*- coding: utf-8 -*-
"""一本原文的章节表：章号、标题行起点、每章正文。**正文边界只在这里算**。

`Chapters.from_text` 是历史口径（只认行首阿拉伯「第N章」，重复或不递增则空表）；
`Chapters.from_positions` 接受「判为章节标题的行」（`core.chapter_headings`）并按出现
顺序编号 1..K。调用方一律 `Chapters.of(source_text, chapters)` —— 「没传就退回正则」
全仓只此一处。

场景切块（`core/scene_indexer.py`）按段落与句末切，不认章节标题；这里只回答「第几章从原文
哪里开始」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

_HEADING = re.compile(r"(?m)^[ \t　]*第(\d+)章")
_FILLER = re.compile(r"[…—\-·.。\s]+")


@dataclass(frozen=True)
class Chapters:
    """``((章号, 标题行原文起点), …)``，按原文顺序。空表 = 没有可用的章节标题。"""
    starts: tuple[tuple[int, int], ...] = ()

    @classmethod
    def from_text(cls, source_text: str) -> "Chapters":
        """历史正则口径：重复、不递增或没有命中 → 空表。"""
        hits = [(int(m.group(1)), m.start()) for m in _HEADING.finditer(source_text)]
        numbers = [n for n, _ in hits]
        if not hits or len(set(numbers)) != len(numbers) or numbers != sorted(numbers):
            return cls()
        return cls(tuple(hits))

    @classmethod
    def from_positions(cls, starts: Sequence[int]) -> "Chapters":
        """判为章节标题的行（原文起点，按出现顺序）→ 章号 1..K。"""
        return cls(tuple(enumerate(starts, 1)))

    @classmethod
    def of(cls, source_text: str, chapters: "Chapters | None") -> "Chapters":
        """调用方没传章节表就退回正则 —— 这个兜底只在这里写。"""
        return chapters if chapters is not None else cls.from_text(source_text)

    def bodies(self, source_text: str) -> dict[int, tuple[int, str]]:
        """``{章号: (正文在原文里的起点, 正文)}``，正文不含标题行；空表为空。"""
        return {n: (a, source_text[a:b]) for n, (a, b) in self._spans(source_text).items()}

    def body_start(self, source_text: str, chapter: int) -> int | None:
        """第 `chapter` 章正文（标题行之后）在原文里的起点；没有这一章为 None。"""
        span = self._spans(source_text).get(chapter)
        return None if span is None else span[0]

    def openings(self, source_text: str, width: int = 40) -> list[tuple[int, str]]:
        """``[(章号, 本章开头 width 字), …]``：标题行之后的正文开头，跳过只有省略号或破折号的行。"""
        cuts = [raw for _, raw in self.starts] + [len(source_text)]
        out: list[tuple[int, str]] = []
        for (n, _), a, b in zip(self.starts, cuts, cuts[1:]):
            lines = source_text[a:b].split("\n")[1:]                       # 去掉标题行
            body = [s for s in (ln.strip() for ln in lines) if s and _FILLER.fullmatch(s) is None]
            out.append((n, " ".join(body)[:width]))
        return out

    def _spans(self, source_text: str) -> dict[int, tuple[int, int]]:
        """``{章号: (正文起点, 正文终点)}``（原文坐标，正文不含标题行）。"""
        cuts = [r for _, r in self.starts] + [len(source_text)]
        out: dict[int, tuple[int, int]] = {}
        for (n, a), b in zip(self.starts, cuts[1:]):
            nl = source_text.find("\n", a, b)
            out[n] = (b if nl == -1 else nl + 1, b)
        return out


def chapter_starts(source_text: str) -> list[tuple[int, int]] | None:
    """``[(章号, 原文起点), …]``，按原文顺序；没有可用的章节标题时返回 None。"""
    starts = Chapters.from_text(source_text).starts
    return list(starts) if starts else None


def chapter_openings(source_text: str, chapters: list[tuple[int, int]],
                     width: int = 40) -> list[tuple[int, str]]:
    """``[(章号, 本章开头 width 字), …]``：标题行之后的正文开头，跳过只有省略号或破折号的行。"""
    return Chapters(tuple(chapters)).openings(source_text, width)


def chapter_bodies(source_text: str) -> dict[int, tuple[int, str]]:
    """``{章号: (正文在原文里的起点, 正文)}``，正文不含标题行；没有可用的章节标题时为空。"""
    return Chapters.from_text(source_text).bodies(source_text)


def chapter_body_start(source_text: str, chapter: int) -> int | None:
    """第 `chapter` 章正文（标题行之后）在原文里的起点；没有这一章（或没有可用的章节标题）为 None。"""
    return Chapters.from_text(source_text).body_start(source_text, chapter)
