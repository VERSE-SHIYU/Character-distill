"""召回评测的指标与标注自检 —— **纯函数，零 IO**。

命中看**块**是否进前 3：每块的完整区间由 ``core.rag.chunk_spans`` 从集合的
``npos`` / ``nend`` 算出（评测脚本传入 ``spans``），标注片段换算成原文的**连续**规范化
区间，相交即算 —— 只看区间，不看返回文本（旧集合只存了每块前 800 字，新集合存整块）。
这样不依赖场景怎么切，以后重切场景 / 改成按事件入库，标注不用重做。

不复用 ``core.quotes.locate_in_normalized``：那是为「省略号拼接核对」设计的，位置只取
最长一段、宽度却按整段算，命中段含省略号时区间会跨过中间无关段落（假命中）。连续区间
由 ``core.quotes.intervals_in_normalized`` 给出 —— 与原文匹配只写那一份。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from core.quotes import (
    MIN_ELIDED_FRAGMENT_LEN,
    has_ellipsis,
    intervals_in_normalized,
)


class AnnotationError(ValueError):
    """标注在检索前就没通过自检；消息里逐条列出是哪一条。"""


def _overlaps(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(x0 < y1 and y0 < x1 for x0, x1 in a for y0, y1 in b)


def fragment_problems(
    case_id: Any,
    key: str,
    i: int,
    piece: Any,
    source_norm: str,
    before: int | None,
) -> list[str]:
    """一个标注片段合不合规 —— 人工标注自检与出题关 g_ground 共用这一份规则。"""
    piece = str(piece)
    if has_ellipsis(piece):
        return [f"{case_id}: {key}[{i}] 含省略号（只接受连续原文片段）"]
    if len(piece.strip()) < MIN_ELIDED_FRAGMENT_LEN:
        return [f"{case_id}: {key}[{i}] 短于 {MIN_ELIDED_FRAGMENT_LEN} 字"]
    intervals = intervals_in_normalized(source_norm, piece)
    if not intervals:
        return [f"{case_id}: {key}[{i}] 在原文里找不到"]
    if len(intervals) > 1:
        return [f"{case_id}: {key}[{i}] 在原文里出现 {len(intervals)} 处（须唯一）"]
    if key == "should" and before is not None and intervals[0][0] >= before:
        return [f"{case_id}: {key}[{i}] 落在所选阶段之后"]
    return []


def validate_cases(
    cases: Iterable[Mapping[str, Any]],
    source_norm: str,
    phase_limits: Mapping[Any, int | None],
) -> None:
    """在检索之前跑完；有任何问题抛 ``AnnotationError``。

    ``phase_limits``：阶段 → 该阶段检索上界（规范化坐标，``ArcView.before``）；
    ``None`` 表示该阶段不截断（不检查「落在阶段之后」）。
    """
    problems: list[str] = []
    for case in cases:
        case_id = case.get("id", "?")
        before = phase_limits.get(case.get("phase"))
        for key in ("should", "should_not"):
            for i, fragment in enumerate(case.get(key) or []):
                problems.extend(
                    fragment_problems(case_id, key, i, fragment, source_norm, before))
    if problems:
        raise AnnotationError("标注未通过自检：\n" + "\n".join(problems))


def score_case(
    source_norm: str,
    case: Mapping[str, Any],
    hits: Iterable[Any],
    spans: Mapping[str, tuple[int, int]],
) -> dict:
    """一条查询的命中 / 越界判定；``hits`` 是前 3 段，``spans`` 是块 id → 完整区间。"""
    should = [iv for f in case.get("should") or [] for iv in intervals_in_normalized(source_norm, f)]
    should_not = [iv for f in case.get("should_not") or [] for iv in intervals_in_normalized(source_norm, f)]

    hit = leak = False
    unlocated = 0
    chunk_ids: list[str | None] = []
    for item in hits:
        chunk_id = (item.meta or {}).get("chunk_id")
        chunk_ids.append(chunk_id)
        hit_iv = [spans[chunk_id]] if chunk_id in spans else []
        if not hit_iv:
            unlocated += 1
        hit = hit or _overlaps(hit_iv, should)
        leak = leak or _overlaps(hit_iv, should_not)
    return {"id": case.get("id"), "hit": hit, "leak": leak,
            "unlocated": unlocated, "top3_chunk_ids": chunk_ids}


def aggregate(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    n = len(rows)
    return {
        "r_at_3": sum(1 for r in rows if r["hit"]) / n if n else 0.0,
        "leak_rate": sum(1 for r in rows if r["leak"]) / n if n else 0.0,
        "n": n,
        "per_query": rows,
    }


def aggregate_suite(rows_by_book: Mapping[str, Iterable[dict]]) -> dict:
    """每本各自 aggregate，总体按**全部行**算（不是各书比例的平均）。"""
    by_book = {book: aggregate(rows) for book, rows in rows_by_book.items()}
    overall = aggregate(r for rows in rows_by_book.values() for r in rows)
    return {"by_book": by_book, "overall": overall}
