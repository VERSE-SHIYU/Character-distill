"""☁ `eval_recall.py --suite`：无卡书按角色建场景集合、phase 必须为 null、
多本按全部行汇总。本地不跑（spec §0），替身不连库、不嵌入。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.quotes import intervals_in_normalized, normalize  # noqa: E402
from eval_recall import Book, EvalAbort, run_suite  # noqa: E402

NAME = "甲"
FRAGMENT = "丙丁戊己庚辛壬癸子丑"  # 10 字，全书唯一
SOURCE = "乙" * 100 + NAME + FRAGMENT + "乙" * 100


class _Hit:
    def __init__(self, chunk_id):
        self.meta = {"chunk_id": chunk_id}


class _Rag:
    def __init__(self, spans):
        self._spans = spans

    def chunk_spans(self, source_norm_len):
        return self._spans

    def query_scenes(self, query, **kwargs):
        return [_Hit("scene_0")]


class _Service:
    """只记建了哪些集合；会话检索固定给同一个替身。"""

    def __init__(self):
        start, end = intervals_in_normalized(normalize(SOURCE), FRAGMENT)[0]
        self.rag = _Rag({"scene_0": (start, end)})
        self.built = []

    def build_eval_scene_index(self, card_id, content, char_name,
                               embedding_key, embedding_region):
        self.built.append(card_id)

    def get_rag_for_session(self, text_id, *, card_id, embedding_key, embedding_region):
        return self.rag


def _book(tmp_path, *, text_id="t1", title="书", character="甲", phase=None):
    cases_path = tmp_path / f"{text_id}_{character}.jsonl"
    cases_path.write_text(
        json.dumps({"id": "q01", "phase": phase, "query": "你当时在场吗？",
                    "should": [FRAGMENT]}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return Book(book=text_id, title=title, user_id="u", text_id=text_id,
                character=character, source=SOURCE, aliases=[], cases=str(cases_path),
                card_id=None, card=None, embedding_key="k", embedding_region="cn")


def test_no_card_rows_build_numbered_scene_collections_and_aggregate(tmp_path):
    books = [_book(tmp_path, character="甲"), _book(tmp_path, character="乙")]
    service = _Service()

    result = run_suite(books, service)

    assert service.built == ["eval_t1_c1", "eval_t1_c2"]
    assert set(result["by_book"]) == {"书/甲", "书/乙"}
    assert (result["overall"]["n"], result["overall"]["r_at_3"]) == (2, 1.0)


def test_card_row_does_not_consume_scene_collection_number(tmp_path, monkeypatch):
    """同一 text_id、有卡行在前：n 只数无卡行，无卡行仍是 `_c1`。"""
    import eval_recall

    card_book = _book(tmp_path, character="甲", phase=1)
    card_book.card = object()
    card_book.card_id = "real_card"
    books = [card_book, _book(tmp_path, character="乙")]
    service = _Service()
    # 有卡分支的打分不在本条范围内，只看无卡行的集合编号。
    monkeypatch.setattr(eval_recall, "run_eval", lambda *a, **k: {"per_query": []})

    run_suite(books, service)

    assert service.built == ["eval_t1_c1"]


def test_eval_scene_index_builds_only_the_scene_collection(monkeypatch):
    """评测只建 `scenes_{card_id}`：不走 `_build_text_collection`（不嵌入整本原文）。"""
    from core import indexing_service as mod

    service = mod.IndexingService({})
    engine = object()
    built = []

    def _no_text_collection(*args, **kwargs):
        raise AssertionError("评测不该建原文集合")

    def _index_scenes(self, text, rag, character_name, collection_name=None,
                      need_positions=False):
        built.append((rag, character_name, collection_name, need_positions))
        return 1

    monkeypatch.setattr(service, "_new_rag", lambda key, region: engine)
    monkeypatch.setattr(service, "_build_text_collection", _no_text_collection)
    monkeypatch.setattr(mod.SceneIndexer, "index_scenes", _index_scenes)

    service.build_eval_scene_index("eval_t1_c1", SOURCE, NAME, "k", "cn")

    assert built == [(engine, NAME, "scenes_eval_t1_c1", False)]


def test_phase_must_be_null_without_card(tmp_path):
    with pytest.raises(EvalAbort, match="phase"):
        run_suite([_book(tmp_path, phase=1)], _Service())


def test_row_without_cases_is_refused(tmp_path):
    book = _book(tmp_path)
    book.cases = None
    with pytest.raises(EvalAbort, match="标注"):
        run_suite([book], _Service())
