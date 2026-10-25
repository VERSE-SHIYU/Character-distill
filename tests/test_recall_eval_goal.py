"""召回评测段 0a 的目标检查（先红）。

测的是「达成目标」，不是「符合 spec」：

1. 聊天与评测工具**走同一次检索调用**（替换 ``core.scene_retrieval.scene_hits``
   为记录参数的替身后，两次调用的参数相同；``top_k`` / ``current_emotion`` 由
   ``scene_hits`` 内部给出，故单独锁一次它的委托参数）。
2. 指标算得对：区间重叠即命中 / 越界；命中段被截断仍算重叠；都不重叠两项都不计。
3. 标注先自检：找不到、不止一处、短于 10 字、落在所选阶段之后 —— 在检索前报错
   并列出是哪一条，不发任何检索调用。

模块尚不存在时函数内 import 会失败 → ``xfail(strict=True)`` 记红；实现落地后
去掉 marker，这些用例必须真绿。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from core.arc_view import arc_view  # noqa: E402
from core.quotes import normalize  # noqa: E402
from core.rag import EvidenceHits  # noqa: E402
from core.schema import (  # noqa: E402
    ArcPhase,
    CharacterArc,
    CharacterCard,
    scene_evidence,
)
from evidence_fakes import build_ctx  # noqa: E402

# 全部为汉字、无标点：规范化坐标 == 字符下标，方便算阶段边界。
SOURCE = (
    "满月宴那天祈灼在廊下听见了那支曲子"      # 0..17
    "后来他独自去了后山没有告诉任何人"        # 18..34
    "魏无羡在另一处杀了人这件事祈灼不知道"    # 35..54
    "祈灼始终不知道这件事"                    # 55..64（重复，用于唯一性自检）
    "祈灼始终不知道这件事"                    # 65..74
)

QUERY = "你还记得满月宴那天吗"


def _card() -> CharacterCard:
    return CharacterCard(
        name="祈灼",
        character_arc=CharacterArc(
            phases=[ArcPhase(start=0), ArcPhase(start=30), ArcPhase(start=60)],
            source_fingerprint="fp-test",
        ),
    )


def _scene(text: str, chunk_id: str = "scene_0"):
    return scene_evidence(
        text=text, final=0.5, semantic=0.5, emotion_affinity=0.5,
        chunk_id=chunk_id, chapter=None,
    )


class TestSameRetrievalCall:
    def test_chat_and_eval_pass_identical_arguments(self, monkeypatch):
        import core.scene_retrieval as sr
        import scripts.eval_recall as ev

        calls: list[dict] = []

        def recorder(rag, query, *, character_name, window):
            calls.append({"rag": rag, "query": query,
                          "character_name": character_name, "window": window})
            return EvidenceHits([])

        monkeypatch.setattr(sr, "scene_hits", recorder)

        card = _card()
        rag = object()
        ctx = build_ctx(card=card, rag=rag)
        ctx.arc_view = arc_view(card, 1)
        ctx._scene_items(QUERY)

        ev.run_eval(
            SOURCE, card,
            [{"id": "q1", "phase": 1, "query": QUERY,
              "should": ["满月宴那天祈灼在廊下听见"], "should_not": []}],
            rag=rag,
        )

        assert len(calls) == 2, "聊天与评测各应发出一次 scene_hits"
        chat, tool = calls
        for key in ("rag", "query", "character_name", "window"):
            assert chat[key] == tool[key], f"{key} 不一致：聊天与评测量的不是同一次调用"

    def test_scene_hits_delegates_top3_and_detected_emotion(self):
        import core.scene_retrieval as sr
        from core.scene_indexer import _detect_emotion

        class _FakeRag:
            def __init__(self):
                self.seen = None

            def query_with_emotion_ex(self, query, **kwargs):
                self.seen = (query, kwargs)
                return EvidenceHits([])

        rag = _FakeRag()
        sentinel_window = object()
        sr.scene_hits(rag, QUERY, character_name="祈灼", window=sentinel_window)

        query, kwargs = rag.seen
        assert query == QUERY
        assert kwargs["top_k"] == 3
        assert kwargs["character_name"] == "祈灼"
        assert kwargs["window"] is sentinel_window
        assert kwargs["current_emotion"] == _detect_emotion(QUERY)


class TestMetrics:
    def _score(self, case, hits):
        from core import recall_eval

        return recall_eval.score_case(normalize(SOURCE), case, hits)

    def test_overlap_counts_as_hit(self):
        row = self._score(
            {"id": "q1", "should": ["在廊下听见了那支曲子"], "should_not": []},
            [_scene(SOURCE[:18])],
        )
        assert row == {"id": "q1", "hit": True, "leak": False,
                       "top3_chunk_ids": ["scene_0"]}

    def test_truncated_hit_still_overlaps(self):
        # 命中段只含片段前半（截断到 800 字内的自然形态），位置仍重叠。
        row = self._score(
            {"id": "q2", "should": ["后来他独自去了后山没有告诉任何人"], "should_not": []},
            [_scene("后来他独自去了后山")],
        )
        assert row["hit"] is True

    def test_overlap_with_should_not_counts_as_leak(self):
        row = self._score(
            {"id": "q3", "should": [], "should_not": ["魏无羡在另一处杀了人"]},
            [_scene(SOURCE[35:55])],
        )
        assert row == {"id": "q3", "hit": False, "leak": True,
                       "top3_chunk_ids": ["scene_0"]}

    def test_no_overlap_counts_neither(self):
        row = self._score(
            {"id": "q4",
             "should": ["满月宴那天祈灼在廊下听见了那支曲子"],
             "should_not": ["魏无羡在另一处杀了人"]},
            [_scene("后来他独自去了后山没有告诉任何人")],
        )
        assert row["hit"] is False and row["leak"] is False

    def test_aggregate(self):
        from core import recall_eval

        rows = [
            {"id": "q1", "hit": True, "leak": False, "top3_chunk_ids": []},
            {"id": "q2", "hit": True, "leak": False, "top3_chunk_ids": []},
            {"id": "q3", "hit": False, "leak": True, "top3_chunk_ids": []},
            {"id": "q4", "hit": False, "leak": False, "top3_chunk_ids": []},
        ]
        assert recall_eval.aggregate(rows) == {
            "r_at_3": 0.5, "leak_rate": 0.25, "n": 4, "per_query": rows,
        }


class TestAnnotationSelfCheck:
    def test_bad_annotations_abort_before_any_retrieval(self, monkeypatch):
        import core.recall_eval as re_mod
        import core.scene_retrieval as sr
        import scripts.eval_recall as ev

        called: list[object] = []
        monkeypatch.setattr(sr, "scene_hits", lambda *a, **k: called.append(a))

        cases = [
            {"id": "q_ok", "phase": 1, "query": QUERY,
             "should": ["满月宴那天祈灼在廊下听见"], "should_not": []},
            {"id": "q_missing", "phase": 1, "query": QUERY,
             "should": ["原文里根本没有的一句话"], "should_not": []},
            {"id": "q_dup", "phase": 1, "query": QUERY,
             "should": ["祈灼始终不知道这件事"], "should_not": []},
            {"id": "q_short", "phase": 1, "query": QUERY,
             "should": ["满月宴"], "should_not": []},
            {"id": "q_late", "phase": 1, "query": QUERY,
             "should": ["魏无羡在另一处杀了人"], "should_not": []},
        ]

        with pytest.raises(re_mod.AnnotationError) as excinfo:
            ev.run_eval(SOURCE, _card(), cases, rag=object())

        message = str(excinfo.value)
        for bad in ("q_missing", "q_dup", "q_short", "q_late"):
            assert bad in message, f"{bad} 的问题没有被列出"
        assert "q_ok" not in message
        assert called == [], "标注有问题时不许发出任何检索调用"


class TestScriptLoading:
    def test_missing_embedding_key_reports_production_message(self):
        import scripts.eval_recall as ev
        from core.embeddings import EMBEDDING_KEY_REQUIRED

        class _Storage:
            async def get_card_owned(self, card_id, user_id):
                return {"text_id": "t1", "card_json": "{}"}

            async def get_text_owned(self, text_id, user_id):
                return {"content": "原文"}

            async def get_user_api_config(self, user_id):
                return {}

        with pytest.raises(ev.EvalAbort) as excinfo:
            asyncio.run(ev.load_inputs(_Storage(), user_id="u1", card_id="c1"))
        assert str(excinfo.value) == EMBEDDING_KEY_REQUIRED
