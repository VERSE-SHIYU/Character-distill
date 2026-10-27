"""--query-from 正向对照：``should`` 只把检索文本换成第一段证据片段原文，其余全不变。"""
from __future__ import annotations

import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from core.rag import EvidenceHits  # noqa: E402

SOURCE = "满月宴那天祈灼在廊下听见了那支曲子后来他独自去了后山没有告诉任何人"
CASES = [{"id": "q1", "phase": None, "query": "你还记得满月宴吗",
          "should": ["满月宴那天祈灼在廊下听见", "后来他独自去了后山没有"], "should_not": []}]


class _Rag:
    def chunk_spans(self, n):
        return {}


def test_should_swaps_only_the_query_text(monkeypatch):
    import core.scene_retrieval as sr
    import scripts.eval_recall as ev

    calls: list[tuple] = []

    def recorder(rag, query, **kw):
        calls.append((rag, query, kw))
        return EvidenceHits([])

    monkeypatch.setattr(sr, "scene_hits", recorder)
    rag = _Rag()
    default_rows = ev.run_eval_no_phase(SOURCE, "祈灼", CASES, rag)
    should_rows = ev.run_eval_no_phase(SOURCE, "祈灼", CASES, rag, "should")

    (rag_q, text_q, kw_q), (rag_s, text_s, kw_s) = calls
    assert text_q == "你还记得满月宴吗", "缺省必须仍用问题原文"
    assert text_s == "满月宴那天祈灼在廊下听见", "should 必须用第一段证据片段原文"
    assert rag_q is rag_s and kw_q == kw_s, "集合、角色、窗口不许跟着变"
    assert default_rows == should_rows, "打分只依赖返回块与标注，不随查询来源变化"
    assert ev._parse_args(["--suite", "s.json"]).query_from == "query"
