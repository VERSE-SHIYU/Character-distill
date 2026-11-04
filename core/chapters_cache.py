# -*- coding: utf-8 -*-
"""章节表的唯一入口：按书缓存（`core.book_cache`）读缓存 → 没有就判一次 → 写回。

章节表是**作品的属性**：同一本书换角色蒸、用存下的草稿重新转卡，都用同一份。缓存键是
（text_id、属主、`CHAPTERS_VERSION`）。缓存里存「标题行原文起点 + 该行原文」，读回时逐条
核对原文该位置仍是这一行 —— 原文变了就当无缓存重算。

**失败不挡蒸馏**：读缓存、判定、写回任何一步出错都只打 warning 并返回
`Chapters.from_text(content)`（今天的行为），不写缓存。

**前置条件**：调用方已用 `get_text_owned(text_id, user_id)` 做过属主校验（同名单入口）。
"""
from __future__ import annotations

import json
from typing import Any

from core.book_cache import resolve_cached
from core.chapters import CHAPTERS_VERSION, Chapters


async def resolve_chapters(storage: Any, distiller: Any, text_id: str, user_id: str,
                           content: str) -> Chapters:
    return await resolve_cached(
        storage=storage, text_id=text_id, user_id=user_id, version=CHAPTERS_VERSION,
        getter="get_chapters_owned", setter="save_chapters",
        compute=lambda: distiller.detect_chapters(content),
        parse=lambda raw: _parse(raw, content),
        serialize=lambda chapters: _serialize(chapters, content),
        cacheable=lambda _chapters: True,
        fallback=lambda _exc: Chapters.from_text(content),
        label="章节",
    )


def _serialize(chapters: Chapters, content: str) -> str:
    rows = [[start, _line_at(content, start)] for _, start in chapters.starts]
    return json.dumps(rows, ensure_ascii=False)


def _parse(raw: str, content: str) -> Chapters | None:
    """逐条核对原文该位置仍是这一行；任一不符或格式不对 → None（当无缓存，重算）。"""
    try:
        rows = json.loads(raw)
        starts = []
        for start, line in rows:
            start = int(start)
            if _line_at(content, start) != line:
                return None
            starts.append(start)
        return Chapters.from_positions(starts)
    except (ValueError, TypeError):
        return None


def _line_at(content: str, start: int) -> str:
    end = content.find("\n", start)
    return content[start:] if end == -1 else content[start:end]
