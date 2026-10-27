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


def test_should_skips_cases_without_fragment_and_lists_them(tmp_path, monkeypatch):
    """寒暄题没有 should：should 模式跳过、不中止；输出列题号与条数，统计只算有片段的题。"""
    import json

    import core.scene_retrieval as sr
    import scripts.eval_recall as ev
    from core.quotes import intervals_in_normalized, normalize
    from core.schema import scene_evidence

    frag = CASES[0]["should"][0]
    span = intervals_in_normalized(normalize(SOURCE), frag)[0]
    queried: list[str] = []

    def recorder(rag, query, **kw):
        queried.append(query)
        return EvidenceHits([scene_evidence(text=frag, rerank_score=None, vector_rank=0,
                                            chunk_id="c0", chapter=None)])

    monkeypatch.setattr(sr, "scene_hits", recorder)

    class _Service:
        def build_eval_scene_index(self, *a, **kw):
            pass

        def get_rag_for_session(self, *a, **kw):
            class _R:
                def chunk_spans(self, n):
                    return {"c0": span}
            return _R()

    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in [
        CASES[0],
        {"id": "hi1", "phase": None, "query": "你好呀", "should": [], "should_not": []},
        {"id": "hi2", "phase": None, "query": "在吗", "should_not": []},
    ]) + "\n", encoding="utf-8")
    book = ev.Book(book="t1", title="书", user_id="u", text_id="t1", character="祈灼",
                   source=SOURCE, aliases=[], cases=str(cases_path), card_id=None, card=None,
                   embedding_key="k", embedding_region="cn")

    result = ev.run_suite([book], _Service(), query_from="should")

    assert result["skipped"] == {"n": 2, "ids": ["书/祈灼:hi1", "书/祈灼:hi2"]}
    assert queried == [frag], "跳过的题不许发检索"
    assert (result["overall"]["n"], result["overall"]["r_at_3"]) == (1, 1.0)

    plain = ev.run_suite([book], _Service())
    assert plain["skipped"] == {"n": 0, "ids": []}
    assert plain["overall"]["n"] == 3, "query 模式不跳过"
