# -*- coding: utf-8 -*-
"""人物摘录：给弧线组（G6）读的「按章编号的原文摘录」。**只此一处**建摘录。纯计算，无 IO。

spec：`docs/specs/phase-start-chapter.md` §2 J5、J11。代码给内容编号、模型只回编号：每章前标
【第N章】，模型交 `start_chapter`，由 `core.phase_anchoring` 把章号换成该章正文起点。规则逐字照
探针第六版的输入（10-25 在原书上重建，祈灼、谢凛羽两份逐字一致）：

- 按 `core.chapters.chapter_bodies` 分章（标题行不算正文）；没有可用的章节标题 → 不建（None）；
- 每章正文按换行切段，去首尾空白，丢掉空段；
- 名字或任一别名所在段，前后各 `WINDOW` 段（只在本章内数）进摘录；
- 回忆区的章（`skip_chapters`，由调用方从 `RetroRegions.chapters` 取）整章不进；
- 章内不相邻处单独一行 `GAP`，章与章之间空一行；没有一段命中的章不出现。
"""
from __future__ import annotations

from typing import AbstractSet, Sequence

from core.chapters import chapter_bodies

WINDOW = 6           # 名字所在段前后各带几段（探针第一至六版同）
GAP = "……"           # 章内略去处（探针第六版的输入用它；有章节的书不另换未验过的标记）


def build_excerpt(source_text: str, terms: Sequence[str],
                  skip_chapters: AbstractSet[int] = frozenset()) -> str | None:
    """按上面的规则建摘录；原文没有可用的章节标题、或没有一段提到此人时返回 None。"""
    names = [t for t in terms if t]
    blocks: list[str] = []
    for chapter, (_, body) in chapter_bodies(source_text).items():
        if chapter in skip_chapters:
            continue
        paras = [s for s in (line.strip() for line in body.split("\n")) if s]
        keep: set[int] = set()
        for i, para in enumerate(paras):
            if any(name in para for name in names):
                keep.update(range(max(0, i - WINDOW), min(len(paras), i + WINDOW + 1)))
        if not keep:
            continue
        lines, prev = [f"【第{chapter}章】"], None
        for i in sorted(keep):
            if prev is not None and i > prev + 1:
                lines.append(GAP)
            lines.append(paras[i])
            prev = i
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or None


def excerpt_prefix(name: str, excerpt: str) -> str:
    """弧线组 system 最前面的那段（与整书前缀 `distiller.book_prefix` 同一个位置）。"""
    return (f"以下是与「{name}」相关的原文摘录，按章排列，【第N章】是章节标记，"
            f"「{GAP}」表示中间略去的原文：\n\n{excerpt}\n\n")
