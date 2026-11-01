# -*- coding: utf-8 -*-
"""摘录修复的纯计算部分：在原文里模糊预筛候选段落、核对模型抄回的句子。

**纯计算，不调模型、不认识草稿结构**（同 `core/quotes.py` 的做法）：本模块只认识
「原文是一段字符串、摘录是一句字符串」这个形状。谁去调模型、模型回什么算数，在
`core.distiller.Distiller.draft_to_card`。

做法依据 arXiv 2511.11594：先模糊预筛候选位置，再让模型核对短片段、逐字抄回原文。
"""
from __future__ import annotations

import bisect

from rapidfuzz import fuzz

from core.quotes import locate_in_normalized, normalize, raw_offsets

# 规范化后不足 6 字的摘录太短，模糊匹配没有分辨力（「你老」满篇都是），不处理。
MIN_LEN = 6
# 预筛分数线：低于它的窗口不交给模型 —— 省一次调用，也不让模型在无关段落里硬找。
PREFILTER_CUTOFF = 60
# 每条摘录最多给模型几个候选段落。
TOP_K = 3
# 模型抄回的句子与原摘录的最低相似度（0–100）。
ACCEPT_SIMILARITY = 75


def candidates(source_text: str, source_norm: str, quote: str) -> list[str]:
    """在 `source_norm` 上滑窗打分，返回至多 `TOP_K` 个候选段落（原文里整行，按 `\\n` 分）。

    窗长按摘录长度的 1.5 倍留一点上下文；步长取摘录的三分之一，保证改字处落进某个窗。
    分数是 `partial_ratio`（摘录对窗口的最优子串）。位置取**匹配到的那一段**在窗口里的
    起点（`dest_start`），不是窗口起点 —— 窗口可能从上一行开头，取窗口起点会选错行。
    位置相距不足一个窗长的只留分高的 —— 同一处连续几窗是同一个候选。每个命中位置用
    `raw_offsets` 换回原文坐标，取那一行。
    """
    q = normalize(quote)
    if len(q) < MIN_LEN:
        return []
    win = int(len(q) * 1.5) + 2
    step = max(1, len(q) // 3)
    scored: list[tuple[int, float]] = []
    # 原文短于一个窗时也要比一次：`max(1, …)` 让 range 至少给出起点 0。
    for start in range(0, max(1, len(source_norm) - win + 1), step):
        hit = fuzz.partial_ratio_alignment(
            q, source_norm[start:start + win], score_cutoff=PREFILTER_CUTOFF)
        if hit is not None:
            scored.append((start + hit.dest_start, hit.score))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[1], item[0]))
    kept: list[tuple[int, float]] = []
    for start, score in scored:
        if any(abs(start - other) < win for other, _ in kept):
            continue
        kept.append((start, score))
        if len(kept) == TOP_K:
            break

    lines = source_text.split("\n")
    line_starts = [0]
    for line in lines[:-1]:
        line_starts.append(line_starts[-1] + len(line) + 1)
    out: list[str] = []
    for offset in raw_offsets(source_text, source_norm, [start for start, _ in kept]):
        if offset is None:
            continue
        line = lines[max(0, bisect.bisect_right(line_starts, offset) - 1)].strip()
        if line and line not in out:
            out.append(line)
    return out


def accept(source_norm: str, quote: str, sentence: str | None) -> bool:
    """模型抄回的 `sentence` 能不能替换原摘录：非空、逐字在原文里、与原摘录足够像。"""
    if not sentence:
        return False
    if locate_in_normalized(source_norm, sentence) is None:
        return False
    return fuzz.partial_ratio(normalize(quote), normalize(sentence)) >= ACCEPT_SIMILARITY
