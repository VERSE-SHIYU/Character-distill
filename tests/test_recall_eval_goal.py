"""召回评测段 0a 的目标检查（先红）。

测的是「达成目标」，不是「符合 spec」：

1. 聊天与评测工具**走同一次检索调用**（替换 ``core.scene_retrieval.scene_hits``
   为记录参数的替身后，两次调用的参数相同；``top_k`` / ``current_emotion`` 由
   ``scene_hits`` 内部给出，故单独锁一次它的委托参数）。
2. 指标算得对：区间重叠即命中 / 越界；命中段被截断仍算重叠；都不重叠两项都不计。
3. 标注先自检：找不到、不止一处、短于 10 字、落在所选阶段之后 —— 在检索前报错
   并列出是哪一条，不发任何检索调用。
4. （0c）评测按**块完整区间**判命中：含标注的块进前 3 即算，与返回文本截到
   800 字无关；跨块片段任一块命中；区间表取自检索实际绑定的集合。

模块尚不存在时函数内 import 会失败 → ``xfail(strict=True)`` 记红；实现落地后
去掉 marker，这些用例必须真绿。
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from core.arc_view import arc_view  # noqa: E402
from core.quotes import intervals_in_normalized, normalize  # noqa: E402
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
                       "unlocated": 0, "top3_chunk_ids": ["scene_0"]}

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
                       "unlocated": 0, "top3_chunk_ids": ["scene_0"]}

    def test_no_overlap_counts_neither(self):
        row = self._score(
            {"id": "q4",
             "should": ["满月宴那天祈灼在廊下听见了那支曲子"],
             "should_not": ["魏无羡在另一处杀了人"]},
            [_scene("后来他独自去了后山没有告诉任何人")],
        )
        assert row["hit"] is False and row["leak"] is False

    def test_ellipsis_hit_does_not_overlap_the_gap(self):
        # 命中段含「……」：位置只取最长一段、宽度按整段算时，区间会跨过中间无关段落
        # （B 段）而假命中。评测只认连续原文，拼接文本在原文里找不到。
        from core import recall_eval

        source = (
            "满月宴那天祈灼在廊下听见了那支曲子"      # 0..17
            "后来他独自去了后山没有告诉任何人"        # 18..34
            "魏无羡在另一处杀了人这件事祈灼不知道"    # 35..54
            "最后他在渡口等到天亮"                    # 55..64
        )
        hit_text = source[:18] + "……" + source[55:65]
        row = recall_eval.score_case(
            normalize(source),
            {"id": "q5", "should": [source[18:35]], "should_not": []},
            [_scene(hit_text)],
        )
        assert row["hit"] is False, "省略号拼接的命中段被当成了与中间段落重叠"
        assert row["unlocated"] == 1

    def test_unlocated_hits_are_counted(self):
        from core import recall_eval

        row = recall_eval.score_case(
            normalize(SOURCE),
            {"id": "q6", "should": [], "should_not": []},
            [_scene(SOURCE[18:35]), _scene("这句话原文里根本没有啊")],
        )
        assert row["unlocated"] == 1

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
            {"id": "q_ellipsis", "phase": 1, "query": QUERY,
             "should": ["满月宴那天……那支曲子"], "should_not": []},
        ]

        with pytest.raises(re_mod.AnnotationError) as excinfo:
            ev.run_eval(SOURCE, _card(), cases, rag=object())

        message = str(excinfo.value)
        for bad in ("q_missing", "q_dup", "q_short", "q_late", "q_ellipsis"):
            assert bad in message, f"{bad} 的问题没有被列出"
        assert "q_ok" not in message
        assert called == [], "标注有问题时不许发出任何检索调用"


class TestWriteSeparation:
    RESULT = {"r_at_3": 0.5, "leak_rate": 0.25, "n": 4, "per_query": []}

    def test_compute_mode_prints_json_and_never_writes(self, monkeypatch, capsys):
        import scripts.eval_recall as ev
        import web.deps as deps

        class _Storage:
            async def close(self):
                pass

        class _Service:
            def get_rag_for_session(self, *args, **kwargs):
                return object()

        async def fake_load_inputs(storage, *, user_id, card_id):
            return ev.Loaded(card=_card(), text_id="t1", source=SOURCE,
                             embedding_key="k", embedding_region="r")

        monkeypatch.setattr(ev, "load_inputs", fake_load_inputs)
        monkeypatch.setattr(ev, "load_cases", lambda path: [])
        monkeypatch.setattr(ev, "run_eval", lambda *a, **k: self.RESULT)
        monkeypatch.setattr(deps, "get_storage", lambda: _Storage())
        monkeypatch.setattr(deps, "get_indexing_service", lambda: _Service())

        writer = types.ModuleType("evidence_writer")

        def boom(*args, **kwargs):
            raise AssertionError("检索模式不许写证据")

        writer.write_evidence = boom
        writer.code_sha = boom
        monkeypatch.setitem(sys.modules, "evidence_writer", writer)

        ev.main(["--user-id", "u1", "--card-id", "c1", "--cases", "cases.jsonl"])
        assert json.loads(capsys.readouterr().out) == self.RESULT

    def test_from_json_only_writes_evidence(self, tmp_path, monkeypatch):
        import scripts.eval_recall as ev

        path = tmp_path / "result.json"
        path.write_text(json.dumps(self.RESULT), encoding="utf-8")
        calls: dict = {}

        writer = types.ModuleType("evidence_writer")

        def record(evidence_id, payload, *, claim, script, env, code_sha, reproduce=None):
            calls.update(evidence_id=evidence_id, payload=payload, claim=claim,
                         script=script, env=env, code_sha=code_sha,
                         reproduce=reproduce)

        writer.write_evidence = record
        writer.code_sha = lambda: "abc1234"
        monkeypatch.setitem(sys.modules, "evidence_writer", writer)

        ev.main(["--from-json", str(path), "--evidence-id", "recall-baseline-qizhuo"])

        assert calls["evidence_id"] == "recall-baseline-qizhuo"
        assert calls["payload"] == self.RESULT
        assert "R@3 0.50" in calls["claim"] and "越界率 0.25" in calls["claim"]
        assert calls["code_sha"] == "abc1234"
        assert "--from-json" in calls["reproduce"]

    def test_evidence_id_is_rejected_on_retrieval_path(self):
        import scripts.eval_recall as ev

        with pytest.raises(SystemExit):
            ev.main(["--user-id", "u", "--card-id", "c",
                     "--cases", "cases.jsonl", "--evidence-id", "recall-baseline-qizhuo"])
        with pytest.raises(SystemExit):
            ev.main(["--from-json", "result.json"])


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


# ── 0c：按块完整区间判命中，与返回文本截到多少字无关 ──────────────────────────

# 真实样式：中文引号、省略号、换行、重复句（「甲说……」出现两次）。
_C0 = '第一章\n甲说：“你来了……我等了很久。”\n乙沉默不语，转身离去。\n'
_C1 = '第二章\n甲说：“你来了……我等了很久。”\n丙说：“我知道。”\n'
_PAD = "山雨欲来风满楼" * 120  # 840 字：把下一句推到块内第 800 字之后
_C2 = _PAD + '\n祈灼在渡口等到天亮。\n'
_C3 = '尾声\n魏无羡在另一处杀了人，这件事祈灼不知道。'
_RAW = {"scene_0": _C0, "scene_1": _C1, "scene_2": _C2, "scene_3": _C3}


def _tile(raw: dict[str, str]) -> tuple[str, dict[str, tuple[int, int]]]:
    """把各块原样拼成原文，返回规范化原文与每块的完整区间（首尾相接）。"""
    source_norm = normalize("".join(raw.values()))
    spans: dict[str, tuple[int, int]] = {}
    cursor = 0
    for cid, chunk in raw.items():
        piece = normalize(chunk)
        start = source_norm.index(piece, cursor)
        spans[cid] = (start, start + len(piece))
        cursor = start + len(piece)
    assert cursor == len(source_norm), "各块没有铺满原文 —— 下面的区间与原文对不上"
    return source_norm, spans


@pytest.mark.xfail(strict=True, reason="0c 未实现：score_case 还不接受块区间")
class TestBlockSpanRuler:
    def test_ideal_retrieval_hits_every_annotation(self):
        """含标注的那一块进前 3 就算命中；片段在块内 800 字之后也必须算（D1 原样复现）。"""
        from core import recall_eval

        source_norm, spans = _tile(_RAW)
        cases = [
            ("q0", "scene_0", "乙沉默不语，转身离去"),
            ("q1", "scene_1", "丙说：“我知道。”"),
            ("q2", "scene_2", "祈灼在渡口等到天亮"),
        ]
        rows = [
            recall_eval.score_case(
                source_norm,
                {"id": cid, "should": [fragment], "should_not": []},
                [_scene(_RAW[block][:800], block)],  # 生产返回文本：整块截前 800 字
                spans,
            )
            for cid, block, fragment in cases
        ]
        assert [row["hit"] for row in rows] == [True, True, True]
        assert recall_eval.aggregate(rows)["r_at_3"] == 1.0

        fragment = normalize("祈灼在渡口等到天亮")
        assert source_norm.index(fragment) - spans["scene_2"][0] > 800, (
            "前提：这一句在块内第 800 字之后")
        assert fragment not in normalize(_RAW["scene_2"][:800]), (
            "前提：截到 800 字的返回文本里根本没有这一句 —— 旧尺子必然判不中")

        leak = recall_eval.score_case(
            source_norm,
            {"id": "leak", "should": [], "should_not": ["魏无羡在另一处杀了人"]},
            [_scene(_RAW["scene_3"][:800], "scene_3")],
            spans,
        )
        assert leak["leak"] is True

    def test_fragment_across_two_blocks_hits_either(self):
        """片段跨块边界：两块中任一进前 3 都算命中（包含它的块全都算）。"""
        from core import recall_eval

        raw = {"a": '甲在廊下听见了那支曲子，', "b": '后来他独自去了后山，没有告诉任何人。'}
        source_norm, spans = _tile(raw)
        fragment = "那支曲子，后来他独自去了后山"
        found = intervals_in_normalized(source_norm, fragment)
        assert len(found) == 1
        start, end = found[0]
        assert start < spans["a"][1] < end, "前提：片段确实跨过两块边界"

        case = {"id": "cross", "should": [fragment], "should_not": []}
        for block in ("a", "b"):
            row = recall_eval.score_case(
                source_norm, case, [_scene(raw[block][:800], block)], spans)
            assert row["hit"] is True, f"只有 {block} 进前 3，跨块片段却判不中"

    def test_unrelated_block_and_unknown_id(self):
        """块与所有片段都不重叠 → 都不计；块 id 不在区间表里 → 计 unlocated，不算命中。"""
        from core import recall_eval

        source_norm, spans = _tile(_RAW)
        case = {"id": "q", "should": ["乙沉默不语，转身离去"],
                "should_not": ["魏无羡在另一处杀了人"]}

        row = recall_eval.score_case(
            source_norm, case, [_scene(_RAW["scene_1"][:800], "scene_1")], spans)
        assert (row["hit"], row["leak"], row["unlocated"]) == (False, False, 0)

        ghost = recall_eval.score_case(
            source_norm, case, [_scene("不相干的文本", "scene_99")], spans)
        assert (ghost["hit"], ghost["leak"], ghost["unlocated"]) == (False, False, 1)

    def test_spans_come_from_the_collection_the_session_is_bound_to(self):
        """区间表读的就是检索实际用的那个集合（``collection_name``），不是另一个候选集合。"""
        import core.indexing_service as IS

        class _Col:
            def __init__(self, name, ids, metas):
                self.name = name
                self.ids = ids
                self.metas = metas
                self.reads = 0

            def get(self, include=None):
                assert include == ["metadatas"]
                self.reads += 1
                return {"ids": self.ids, "metadatas": self.metas}

        class _Engine:
            def __init__(self, col):
                self.collection = col
                self.collection_name = col.name

        class _Svc:
            def __init__(self, engine):
                self.engine = engine

            def _new_rag(self, key, region):
                return self.engine

            def _candidates_stamp(self, rag, text_id, card_id):
                return ("v",)

            def _load_session_rag(self, rag, text_id, card_id):
                return rag, None

        scenes = _Col("scenes_c1", ["scene_2", "scene_0", "scene_1"],
                      [{"npos": 40}, {"npos": 0}, {"npos": 13}])
        text = _Col("text_t1", ["chunk_0"], [{"npos": 0}])
        view = IS.SessionRag(_Svc(_Engine(scenes)), "t1", "c1", "k", "r")

        spans = view.chunk_spans(60)
        assert view.collection_name == "scenes_c1"
        assert spans == {
            "scene_0": (0, 13), "scene_1": (13, 40), "scene_2": (40, 60),
        }
        assert (scenes.reads, text.reads) == (1, 0), "区间表读了候选集合之外的另一个集合"

        class _NoSvc(_Svc):
            def _load_session_rag(self, rag, text_id, card_id):
                return None, None

        assert IS.SessionRag(_NoSvc(None), "t1", "c1", "k", "r").chunk_spans(10) == {}
