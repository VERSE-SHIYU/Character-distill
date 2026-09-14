"""Evidence 线 2b：ContextEngine 三源收敛到同一机制后的锁。

字符串块现在**由 EvidenceItem 列表渲染得到**，于是 commit 1 那条「两出口逐字节相等」
成了构造上恒真 —— 锁会空转。故不变量换了形态，三条红源互不重叠：

- **B3 离线基线**（``TestOfflineBaseline``）：抓**改动前** ContextEngine 三源的字符串
  产出冻结成字面量。这是抓「三源共用的那份实现被改」的唯一红源。
- **B1 字符串 ≡ 独立重算的 items 渲染**（``TestStringDerivesFromItems``）：测试自己写
  拼装公式，**不调生产渲染器**。抓「字符串与 items 分家」。
- **B2 源码级唯一出处锁**（``TestTemplateLiteralsHaveOneSource``）：三个模板字面量在
  ``core/context_engine.py`` 里各只出现一次。这是「不存在第二条构造路径」的**真锁**
  —— 同仓 ``script_role`` / ``non_repo_paths`` 那种锁的同一形态。spy 版
  （``TestStringExitGoesThroughTheRenderer``）是次级：它只锁得住「已知出口走了渲染器」，
  锁不住「将来新增第四条源自己手拼一块」。

其余锁各自盯一个已实证会错的点：web 的 text 必须是 DDG 原文（不是 LLM 改写结果）、
缺 url 必须 None（不许填空串）、过滤器返回空串时 items 不许丢、memory 的
``memory_mood → mood`` 纯改名、memory 的 score 恒 None（final 上界 1.25 不在 0-1）。

夹具在 ``tests/evidence_fakes.py`` —— 三条源共用一套。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

import core.context_engine as ce  # noqa: E402
from core.context_engine import _MEMORY_FMT, _SCENE_FMT  # noqa: E402
from core.rag import CollectionUnusableError  # noqa: E402
from core.schema import MemoryMeta  # noqa: E402
from evidence_fakes import (  # noqa: E402
    DDG_ABSTRACT,
    DDG_NESTED,
    DDG_TOPICS,
    FakeCollection,
    FakeLLM,
    FakeMemory,
    RaisingCollection,
    build_ctx,
    fake_ddg,
    make_rag,
)

QUERY = "莲花坞里的旧事"


def _scene_ctx(rows=None):
    return build_ctx(rag=make_rag(FakeCollection() if rows is None else FakeCollection(rows)))


def _memory_ctx(rows=None, enabled=True, llm=None):
    return build_ctx(memory=FakeMemory(rows=rows, enabled=enabled), llm=llm)


def _capture_all() -> dict[str, str]:
    """跑完改动前抓过的同一批场景，产出 {tag: 字符串出口}。"""
    out = {}
    out["scene_hit"] = _scene_ctx()._retrieve_scenes(QUERY)
    out["scene_empty"] = _scene_ctx(rows=[])._retrieve_scenes(QUERY)
    out["scene_none_rag"] = build_ctx(rag=None)._retrieve_scenes(QUERY)

    out["memory_hit"] = _memory_ctx()._retrieve_memories(QUERY, current_mood="平静")
    out["memory_empty"] = _memory_ctx(rows=[])._retrieve_memories(QUERY)
    out["memory_disabled"] = _memory_ctx(enabled=False)._retrieve_memories(QUERY)

    with fake_ddg(DDG_ABSTRACT):
        out["web_abstract"] = _memory_ctx(llm=FakeLLM())._search_web(QUERY)
    with fake_ddg(DDG_TOPICS):
        out["web_topics"] = _memory_ctx(llm=FakeLLM())._search_web(QUERY)
        out["web_topics_filtered_empty"] = _memory_ctx(llm=FakeLLM(reply=""))._search_web(QUERY)
        out["web_topics_no_llm"] = build_ctx(llm=None)._search_web(QUERY)
    with fake_ddg(DDG_NESTED):
        out["web_nested"] = _memory_ctx(llm=FakeLLM())._search_web(QUERY)
    with fake_ddg({"AbstractText": "", "RelatedTopics": []}):
        out["web_empty"] = _memory_ctx(llm=FakeLLM())._search_web(QUERY)
    return out


# 改动前抓的基线（e2e/scratch/capture_ctx_golden.py，2026-09-14，跑在改 core/context_engine.py
# 之前；产物 e2e/scratch/ctx_golden.json）。改后实跑与者逐字节相同。
BASELINE = {
    "scene_hit": "【参考原文片段（酌情使用，不要逐字复述）】\n屋顶上的旧事，风很凉。",
    "scene_empty": "",
    "scene_none_rag": "",
    "memory_hit": (
        "【你的长期记忆——这些是你和对方之前交流中记住的事】\n"
        "- 他说过要在莲花坞种满莲花。\n- 她答应过下次带酒来。\n"
        "注意：自然地在对话中体现这些记忆，不要刻意逐条复述。"
    ),
    "memory_empty": "",
    "memory_disabled": "",
    "web_abstract": "【角色的见闻感知】\n我听说过莲花坞这个地方。",
    "web_topics": "【角色的见闻感知】\n我听说过莲花坞这个地方。",
    "web_topics_filtered_empty": "",
    "web_topics_no_llm": "",
    "web_nested": "【角色的见闻感知】\n我听说过莲花坞这个地方。",
    "web_empty": "",
}


class TestOfflineBaseline:
    """B3：抓「三源共用的那份实现被改」的唯一红源。"""

    def test_all_scenarios_match_frozen_baseline(self):
        got = _capture_all()
        assert set(got) == set(BASELINE), "场景集变了，基线的覆盖面跟着失效"
        for tag, expected in BASELINE.items():
            assert got[tag] == expected, f"{tag} 的 prompt 侧产出偏离改动前基线"


class TestStringDerivesFromItems:
    """B1：字符串出口必须就是 items 的渲染 —— 拼装公式由测试自己写（不调生产渲染器）。

    字符串一旦与 items 分家（改了一边忘一边），本条红；生产渲染器被改，B3 红。
    """

    def test_scene_block_equals_independent_render(self):
        res = _scene_ctx()._retrieve_scenes_ex(QUERY)
        assert res.items, "本用例需要非空命中"
        expected = f"{_SCENE_FMT.title}\n" + "\n".join(
            f"{_SCENE_FMT.line_prefix}{e.text}" for e in res.items
        )
        assert res.block == expected

    def test_memory_block_equals_independent_render(self):
        res = _memory_ctx()._retrieve_memories_ex(QUERY, current_mood="平静")
        assert res.items
        expected = (
            f"{_MEMORY_FMT.title}\n"
            + "\n".join(f"{_MEMORY_FMT.line_prefix}{e.text}" for e in res.items)
            + _MEMORY_FMT.trailer
        )
        assert res.block == expected

    def test_string_exit_is_literally_the_block(self):
        ctx = _scene_ctx()
        assert ctx._retrieve_scenes(QUERY) == ctx._retrieve_scenes_ex(QUERY).block


class TestTemplateLiteralsHaveOneSource:
    """B2（真锁）：块模板字面量在 core/context_engine.py 里各只出现一次。

    将来新增第四条源自己手拼一块（第二份构造路径）→ 字面量出现两次 → 红。
    spy 只锁得住已知出口，锁不住新出口。
    """

    LITERALS = [
        "【参考原文片段（酌情使用，不要逐字复述）】",
        "【你的长期记忆——这些是你和对方之前交流中记住的事】",
        "注意：自然地在对话中体现这些记忆，不要刻意逐条复述。",
        "【角色的见闻感知】",
    ]

    @pytest.mark.parametrize("literal", LITERALS)
    def test_literal_appears_exactly_once(self, literal):
        src = (Path(_repo) / "core" / "context_engine.py").read_text(encoding="utf-8")
        n = src.count(literal)
        assert n == 1, f"模板字面量出现 {n} 次（应为 1）：{literal}"


class TestStringExitGoesThroughTheRenderer:
    """B2 次级：spy 计数 —— 字符串出口绕开渲染器直接拼 → 调用数 0 → 红。"""

    def test_scene_string_exit_calls_render_block_once(self, monkeypatch):
        calls = []
        real = ce._render_block

        def spy(fmt, body):
            calls.append((fmt, body))
            return real(fmt, body)

        monkeypatch.setattr(ce, "_render_block", spy)
        out = _scene_ctx()._retrieve_scenes(QUERY)
        assert len(calls) == 1, f"应恰好经过渲染器一次，实得 {len(calls)} 次"
        assert calls[0][0] is ce._SCENE_FMT
        assert out == real(ce._SCENE_FMT, calls[0][1])

    def test_memory_string_exit_calls_render_block_once(self, monkeypatch):
        calls = []
        real = ce._render_block

        def spy(fmt, body):
            calls.append((fmt, body))
            return real(fmt, body)

        monkeypatch.setattr(ce, "_render_block", spy)
        _memory_ctx()._retrieve_memories(QUERY)
        assert len(calls) == 1 and calls[0][0] is ce._MEMORY_FMT


class TestWebItemsAreRawSnippets:
    """web 的 text 必须是 DDG 原文 —— 不是 LLM 改写结果（本线只宣称检索到了什么）。"""

    REWRITE = "我听说过莲花坞这个地方。"

    def test_text_is_the_snippet_not_the_llm_rewrite(self):
        with fake_ddg(DDG_ABSTRACT):
            res = build_ctx(llm=FakeLLM(reply=self.REWRITE))._search_web_ex(QUERY)
        assert [e.text for e in res.items] == [DDG_ABSTRACT["AbstractText"]]
        assert all(e.text != self.REWRITE for e in res.items), "items 的 text 被填成了改写结果"
        # 块体仍是改写结果（prompt 侧不变），只有 items 是原文 —— 两者不混。
        assert res.block == f"{ce._WEB_FMT.title}\n{self.REWRITE}"

    def test_topics_text_is_raw_and_url_missing_stays_none(self):
        with fake_ddg(DDG_TOPICS):
            res = build_ctx(llm=FakeLLM())._search_web_ex(QUERY)
        assert [e.text for e in res.items] == [t["Text"] for t in DDG_TOPICS["RelatedTopics"]]
        assert res.items[1].meta["url"] is None, "取不到 url 必须 None，不许填空串"
        assert all(e.meta["url"] != "" for e in res.items)
        assert res.items[0].meta["url"] == "https://example.org/zidian"
        assert res.items[0].meta["source"] == "example.org"
        assert res.items[2].meta["source"] == "example.org"

    def test_abstract_carries_url_source_and_timestamp(self):
        with fake_ddg(DDG_ABSTRACT):
            res = build_ctx(llm=FakeLLM())._search_web_ex(QUERY)
        meta = res.items[0].meta
        assert (meta["url"], meta["source"]) == ("https://example.org/lianhua", "示例百科")
        assert meta["fetched_at"], "fetched_at 不许空"

    def test_filtered_empty_keeps_items_with_empty_block(self):
        """过滤器判定「全不适合」→ 块空，但来源确实检索到了：items 照出，不许一起丢。"""
        with fake_ddg(DDG_TOPICS):
            res = build_ctx(llm=FakeLLM(reply=""))._search_web_ex(QUERY)
        assert res.block == "" and res.items and res.status == "hit", \
            f"实得 block={res.block!r} items={len(res.items)} status={res.status}"

    def test_rewrite_failure_keeps_items(self):
        """改写阶段**抛异常**（限流 / 超时 / 网络）→ 只降级 body，items 必须保留。

        这是改写失败的三条独立分支里最常发生的一条（另两条：返回空串、llm 为 None），
        且失败形态最隐蔽：DDG 确实检索到了，前端却显示「检索来源 0 条」。
        """
        with fake_ddg(DDG_TOPICS):
            res = build_ctx(llm=FakeLLM(raise_on_chat=RuntimeError("rate limited"))
                            )._search_web_ex(QUERY)
        assert len(res.items) == 3, f"改写抛异常时 items 被丢了，实得 {len(res.items)} 条"
        assert res.block == ""

    def test_no_llm_still_yields_items(self):
        with fake_ddg(DDG_TOPICS):
            res = build_ctx(llm=None)._search_web_ex(QUERY)
        assert res.items and res.block == ""

    def test_nested_topics_are_not_descended_into(self):
        """**故意不下钻**嵌套 Topics：下钻会改变喂给改写阶段的拼接文本（prompt 字节），
        本轮硬约束是 prompt 侧不变。要放开是单独的判定，不是顺手做。"""
        with fake_ddg(DDG_NESTED):
            res = build_ctx(llm=FakeLLM())._search_web_ex(QUERY)
        assert [e.text for e in res.items] == ["顶层片段。"]
        assert all("嵌套片段" not in e.text for e in res.items)


class TestMemoryMapping:
    def test_mood_is_the_renamed_memory_mood(self):
        res = _memory_ctx()._retrieve_memories_ex(QUERY, current_mood="怀念")
        assert [e.meta["mood"] for e in res.items] == ["怀念", "平静"]

    def test_score_is_none_and_final_is_kept_verbatim(self):
        """final 上界 1.25 不在 0-1，硬压/clamp 是编造 —— 故 score=None，排序读 meta.final。"""
        res = _memory_ctx()._retrieve_memories_ex(QUERY)
        assert all(e.score is None for e in res.items)
        assert [e.meta["final"] for e in res.items] == [0.660, 0.312]

    def test_final_above_one_is_passed_through_verbatim(self):
        """final 上界 1.25 —— >1 真的会出现。不许压成 1.0，score 也不许跟着填。"""
        rows = [{
            "text": "高情感加成的一条", "relevance": 0.9, "importance": 9,
            "age_seconds": 10.0, "memory_mood": "狂喜", "emo_affinity": 0.9, "final": 1.164,
        }]
        res = _memory_ctx(rows=rows)._retrieve_memories_ex(QUERY)
        assert res.items[0].meta["final"] == 1.164
        assert res.items[0].score is None, "score 是 0-1 契约，不许拿 final 硬填"

    def test_produced_meta_satisfies_the_contract(self):
        res = _memory_ctx()._retrieve_memories_ex(QUERY)
        assert res.items
        for e in res.items:
            assert set(e.meta) == set(MemoryMeta.__annotations__)

    def test_missing_field_fails_loudly_instead_of_defaulting(self):
        """工厂不许填默认：少一个 memory_mood 就 KeyError，不许静默补空串。"""
        rows = [{
            "text": "半条记忆", "relevance": 1.0, "importance": 5,
            "age_seconds": 0.0, "emo_affinity": 0.1, "final": 0.5,
        }]
        res = _memory_ctx(rows=rows)._retrieve_memories_ex(QUERY)
        assert res.status == "failed" and res.items == [] and res.block == ""


class TestStatusIsThreeValued:
    def test_hit(self):
        assert _scene_ctx()._retrieve_scenes_ex(QUERY).status == "hit"

    def test_failed_is_distinguishable_from_empty(self):
        """失败与「真无匹配」在 prompt 侧同形（都空块）—— 只能靠 status 分辨。"""
        boom = CollectionUnusableError("集合不可用")
        failed = build_ctx(rag=make_rag(RaisingCollection(boom)))._retrieve_scenes_ex(QUERY)
        empty = _scene_ctx(rows=[])._retrieve_scenes_ex(QUERY)
        assert (failed.status, failed.items, failed.block) == ("failed", [], "")
        assert (empty.status, empty.items, empty.block) == ("empty", [], "")
        assert failed.block == empty.block, "本用例的前提：两者 prompt 侧同形"

    def test_none_rag_is_empty_not_failed(self):
        assert build_ctx(rag=None)._retrieve_scenes_ex(QUERY).status == "empty"
