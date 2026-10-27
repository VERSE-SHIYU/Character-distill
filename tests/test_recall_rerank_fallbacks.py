"""评测记录重排退回次数（spec recall-r2-rerank §3 最后一行）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import recall_eval  # noqa: E402
from core.rag import EvidenceHits  # noqa: E402

CASE = {"id": "q", "should": [], "should_not": []}


def _hits(*, fallback: bool) -> EvidenceHits:
    return EvidenceHits([], rerank_fallback=fallback)


def test_score_case_records_rerank_fallback():
    assert recall_eval.score_case("", CASE, _hits(fallback=True), {})["rerank_fallback"] is True
    assert recall_eval.score_case("", CASE, _hits(fallback=False), {})["rerank_fallback"] is False


def test_score_case_treats_plain_iterable_as_no_fallback():
    assert recall_eval.score_case("", CASE, [], {})["rerank_fallback"] is False


def test_aggregate_suite_counts_fallbacks_across_rows():
    rows = {
        "书/甲": [{"hit": True, "leak": False, "rerank_fallback": True},
                  {"hit": False, "leak": False, "rerank_fallback": False}],
    }
    assert recall_eval.aggregate_suite(rows)["rerank_fallbacks"] == 1


def test_aggregate_keys_unchanged():
    rows = [{"hit": True, "leak": False, "rerank_fallback": True}]
    assert set(recall_eval.aggregate(rows)) == {"r_at_3", "leak_rate", "n", "per_query"}
