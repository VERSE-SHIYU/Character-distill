"""召回评测的指标与标注自检 —— **纯函数，零 IO**。

命中按**原文位置区间重叠**判定，不按段 id：命中段只带文本（``meta.chunk_id``），
标注写的是原文片段，两边都用 ``core.quotes.locate_in_normalized`` 换算成规范化坐标，
区间相交即算。这样不依赖场景怎么切，以后重切场景 / 改成按事件入库，标注不用重做。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from core.quotes import MIN_ELIDED_FRAGMENT_LEN, locate_in_normalized, normalize


class AnnotationError(ValueError):
    """标注在检索前就没通过自检；消息里逐条列出是哪一条。"""


def _intervals(source_norm: str, text: str) -> list[tuple[int, int]]:
    """文本在原文里的所有区间；找不到 → 空列表（不判命中）。"""
    positions = locate_in_normalized(source_norm, text) or []
    width = len(normalize(text))
    return [(p, p + width) for p in positions]


def _overlaps(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(x0 < y1 and y0 < x1 for x0, x1 in a for y0, y1 in b)


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
                piece = str(fragment)
                if len(piece.strip()) < MIN_ELIDED_FRAGMENT_LEN:
                    problems.append(f"{case_id}: {key}[{i}] 短于 {MIN_ELIDED_FRAGMENT_LEN} 字")
                    continue
                positions = locate_in_normalized(source_norm, piece)
                if positions is None:
                    problems.append(f"{case_id}: {key}[{i}] 在原文里找不到")
                elif len(positions) > 1:
                    problems.append(
                        f"{case_id}: {key}[{i}] 在原文里出现 {len(positions)} 处（须唯一）")
                elif key == "should" and before is not None and positions[0] >= before:
                    problems.append(f"{case_id}: {key}[{i}] 落在所选阶段之后")
    if problems:
        raise AnnotationError("标注未通过自检：\n" + "\n".join(problems))


def score_case(source_norm: str, case: Mapping[str, Any], hits: Iterable[Any]) -> dict:
    """一条查询的命中 / 越界判定；``hits`` 是 ``EvidenceItem`` 序列（前 3 段）。"""
    should = [iv for f in case.get("should") or [] for iv in _intervals(source_norm, f)]
    should_not = [iv for f in case.get("should_not") or [] for iv in _intervals(source_norm, f)]

    hit = leak = False
    chunk_ids: list[str | None] = []
    for item in hits:
        chunk_ids.append((item.meta or {}).get("chunk_id"))
        hit_iv = _intervals(source_norm, item.text)
        hit = hit or _overlaps(hit_iv, should)
        leak = leak or _overlaps(hit_iv, should_not)
    return {"id": case.get("id"), "hit": hit, "leak": leak, "top3_chunk_ids": chunk_ids}


def aggregate(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    n = len(rows)
    return {
        "r_at_3": sum(1 for r in rows if r["hit"]) / n if n else 0.0,
        "leak_rate": sum(1 for r in rows if r["leak"]) / n if n else 0.0,
        "n": n,
        "per_query": rows,
    }
