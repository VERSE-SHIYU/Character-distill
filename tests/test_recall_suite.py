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
        self.schemes = []
        self.sessions = []

    def build_eval_scene_index(self, card_id, content, char_name,
                               embedding_key, embedding_region, scheme=None):
        self.built.append(card_id)
        self.schemes.append(scheme)

    def get_rag_for_session(self, text_id, *, card_id, embedding_key, embedding_region):
        self.sessions.append(card_id)
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


def _capture_index_scenes(monkeypatch, service):
    """把 `index_scenes` 换成记录器；返回记录列表。"""
    from core import indexing_service as mod

    built = []

    def _index_scenes(self, text, rag, character_name, collection_name=None,
                      need_positions=False, scheme=None):
        built.append((rag, character_name, collection_name, need_positions, scheme))
        return 1

    monkeypatch.setattr(mod.SceneIndexer, "index_scenes", _index_scenes)
    return built


def test_eval_scene_index_builds_only_the_scene_collection(monkeypatch):
    """评测只建 `scenes_{card_id}`：不走 `_build_text_collection`（不嵌入整本原文）；
    `scheme` 原样传到 `index_scenes`，不传时为 None（即产品缺省切法）。"""
    from core import indexing_service as mod
    from core.scene_indexer import ChunkScheme

    service = mod.IndexingService({})
    engine = object()

    def _no_text_collection(*args, **kwargs):
        raise AssertionError("评测不该建原文集合")

    monkeypatch.setattr(service, "_new_rag", lambda key, region: engine)
    monkeypatch.setattr(service, "_build_text_collection", _no_text_collection)
    built = _capture_index_scenes(monkeypatch, service)
    scheme = ChunkScheme(mode="para", chars=238, overlap=0)

    service.build_eval_scene_index("eval_t1_c1", SOURCE, NAME, "k", "cn")
    service.build_eval_scene_index("eval_t1_c2", SOURCE, NAME, "k", "cn", scheme=scheme)

    assert built == [(engine, NAME, "scenes_eval_t1_c1", False, None),
                     (engine, NAME, "scenes_eval_t1_c2", False, scheme)]


def test_product_scene_index_passes_no_scheme(monkeypatch):
    """产品路径 `build_scene_index` 不传切法：行为不变，用 `CHUNK_SCHEME`。"""
    from core import indexing_service as mod

    service = mod.IndexingService({})

    class _Rag:
        collection = object()

    rag = _Rag()
    monkeypatch.setattr(service, "_build_text_collection", lambda *a, **k: rag)
    built = _capture_index_scenes(monkeypatch, service)

    service.build_scene_index("t1", "c1", SOURCE, NAME, None, "k", "cn", need_positions=True)

    assert built == [(rag, NAME, "scenes_c1", True, None)]


def test_phase_must_be_null_without_card(tmp_path):
    with pytest.raises(EvalAbort, match="phase"):
        run_suite([_book(tmp_path, phase=1)], _Service())


def test_row_without_cases_is_refused(tmp_path):
    book = _book(tmp_path)
    book.cases = None
    with pytest.raises(EvalAbort, match="标注"):
        run_suite([book], _Service())


# ── S3：--chunk-scheme 按切法建变体集合 ─────────────────────────────────────

def test_chunk_scheme_names_variant_collections(tmp_path):
    """不传：沿用 `eval_{text_id}_c{n}`（0e 基线的跑法）；传了：每行建
    `eval_r1_{mode}-{chars}-{overlap}_{text_id}_c{n}`，切法原样交给建索引。"""
    from eval_recall import _parse_args
    from core.scene_indexer import ChunkScheme

    args = _parse_args(["--suite", "s.json", "--chunk-scheme", "para:238:0"])
    assert args.chunk_scheme == ChunkScheme(mode="para", chars=238, overlap=0)
    assert _parse_args(["--suite", "s.json"]).chunk_scheme is None
    with pytest.raises(SystemExit):            # 固定切法已删除，CLI 明确拒绝
        _parse_args(["--suite", "s.json", "--chunk-scheme", "fixed:512:50"])

    plain = _Service()
    run_suite([_book(tmp_path, character="甲"), _book(tmp_path, character="乙")], plain)
    assert (plain.built, plain.schemes) == (["eval_t1_c1", "eval_t1_c2"], [None, None])

    variant = _Service()
    run_suite([_book(tmp_path, character="甲"), _book(tmp_path, character="乙")], variant,
              scheme=args.chunk_scheme)
    assert variant.built == ["eval_r1_para-238-0_t1_c1", "eval_r1_para-238-0_t1_c2"]
    assert variant.schemes == [args.chunk_scheme] * 2
    assert variant.sessions == variant.built


def test_card_row_builds_eval_collection_in_variant_mode(tmp_path, monkeypatch):
    """有卡行（祈灼）在变体模式下也建 eval 集合并在它上面检索，不读线上卡集合；
    窗口仍取自卡（照旧 `run_eval`），无卡行编号照旧只数无卡行。"""
    import eval_recall
    from core.scene_indexer import ChunkScheme

    card = object()
    card_book = _book(tmp_path, character="甲", phase=1)
    card_book.card = card
    card_book.card_id = "real_card"
    books = [card_book, _book(tmp_path, character="乙")]
    service = _Service()
    seen = []
    monkeypatch.setattr(eval_recall, "run_eval",
                        lambda source, c, cases, rag, **k: seen.append((c, rag)) or {"per_query": []})
    scheme = ChunkScheme(mode="para", chars=300, overlap=0)

    run_suite(books, service, scheme=scheme)

    assert service.built == ["eval_r1_para-300-0_t1_card", "eval_r1_para-300-0_t1_c1"]
    assert "real_card" not in service.sessions
    assert service.sessions[0] == "eval_r1_para-300-0_t1_card"
    assert seen == [(card, service.rag)]
