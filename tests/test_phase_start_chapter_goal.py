# -*- coding: utf-8 -*-
"""目标检查：有章节的书，阶段起点改成章号（spec `docs/specs/phase-start-chapter.md` §1）。

起因：弧线组（G6）读整本书写一句「起始原句」当锚点，祈灼卡的锚点多次取自日札、番外和
章内回忆，落在回忆区，整卡跳过位置核对；按原文重新定位（`locate_phase_starts`）三轮小重放
都不达标。探针第六版（10-25）给 G6 换成「人物摘录 + 返回章号」：16 个起点全部是摘录里有的
章，0 个在回忆区（ev 见 spec §6）。

目标（只对「一次读完、原文有可用章节标题」的书；其余书照旧用锚点）：
S1 G6 读的是代码按章编号的人物摘录（带别名、去掉回忆区的章），不是整本书；其余组仍读整本书
S2 G6 的提示词和结构让模型交章号，不交锚点，也不交 key_moments
S3 章号换成位置只在 `locate_starts` 一处：第 N 章 → 该章正文起点；章不存在 → 查不到；
   章在回忆区 → 回忆区；位置照旧要严格递增
S4 依赖组拿到的阶段列表写的是起始章，不是起始原句
S5 缓存照旧预热：G6 与一个不依赖阶段的组（G1）同批先发，其余组等这两组都返回再发
S6 不再按原文重新定位：没有 `locate_phase_starts`，路由里没有「重定位」这一步
回归守卫（main 上就绿）：
R1 没有章节标题的书：G6 仍读整本书、仍交锚点
R2 旧草稿（只有锚点、没有章号）在有章节的书上仍按锚点核对
R3 分组路径（读不完一次）的书即使有章节：G6 仍读分析档案、仍交锚点

替身照生产行为写（返工经验第 35、40 条）：`chat_stream_long` 是生成器，每次都 return usage
收尾；不改写收到的 system。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

import core.distiller as D
from core import phase_anchoring
from core.card_draft import CardDraft
from core.chapters import chapter_starts
from core.distiller import Distiller
from core.quotes import normalized_starts
from core.schema import FORMAT_GROUPS

_ROOT = Path(__file__).resolve().parent.parent
_NAME = "阿灼"
_ALIAS = "小灼"
_USAGE = {"prompt_tokens": 1, "completion_tokens": 1}
_REL_MARKER = "只产出这几个人物与主角的关系"

# 第 1 章：名字在开头一段，隔 8 段之后有一段与他无关的远景（超出前后 6 段，不该进摘录）。
# 第 2 章：整章没有他（摘录里不该有【第2章】）。
# 第 3 章：只用别名出场（带别名才进摘录）。
# 第 4 章：日札（测试里当回忆区）。第 5 章：他再次出场。
_FAR_1 = "远处的盐场上，晒盐人正把盐耙推过白茫茫的盐田。"
_CH2 = "城南米铺的掌柜一早就在柜台后面拨着算盘，嘴里念着米价。"
_ALIAS_LINE = "小灼推开窗，说他要进宫去。"
_SRC = "\n".join([
    "第1章 楼上",
    "阿灼独自坐在楼上抚琴，不见来客。",
    *[f"楼下的伙计搬来第{i}坛酒。" for i in range(1, 9)],
    _FAR_1,
    "第2章 米铺",
    _CH2,
    "第3章 入宫",
    _ALIAS_LINE,
    "第4章 日札",
    "日札：那年在桥边初遇她，阿灼从此心动。",
    "第5章 退路",
    "阿灼对她说，他愿意做她的退路。",
])
_CHAPTERLESS = "阿灼独自坐在楼上抚琴。\n后来阿灼进了宫。\n" + _FAR_1


def _norm_body_start(src: str, chapter: int) -> int:
    """第 `chapter` 章正文（标题行之后）在规范化原文里的起点 —— 预言，独立于被测代码。"""
    raw_heading = dict(chapter_starts(src))[chapter]
    return normalized_starts(src, [src.index("\n", raw_heading) + 1])[0]


def _retro_ch4(src: str = _SRC) -> list[tuple[int, int, str]]:
    lo = normalized_starts(src, [dict(chapter_starts(src))[4]])[0]
    hi = normalized_starts(src, [dict(chapter_starts(src))[5]])[0]
    return [(lo, hi, "第4章日札")]


_CHAPTER_ARC = {"axis": "从拒人到入世", "phases": [
    {"label": "拒人", "state": "独坐楼上之时", "start_chapter": 0, "boundary_examples": []},
    {"label": "入世", "state": "进宫之后", "start_chapter": 3, "boundary_examples": []},
    {"label": "甘为退路", "state": "说愿做退路之后", "start_chapter": 5, "boundary_examples": []},
]}
_ANCHOR_ARC = {"axis": "从拒人到入世", "phases": [
    {"label": "拒人", "state": "独坐楼上之时", "anchor": "", "boundary_examples": []},
    {"label": "入世", "state": "进宫之后", "anchor": "后来阿灼进了宫。", "boundary_examples": []},
]}


def _payload_for(group: str, arc: dict) -> dict:
    base = CardDraft(name=_NAME).model_dump()
    if group == "G6":
        base["character_arc"] = arc
    if group == "G7":
        base["situation_behaviors"] = []
        base["key_memories"] = []
    if group == "G5":
        base["relationships"] = []
    return {k: base[k] for k in FORMAT_GROUPS[group]}


def _group_llm(arc: dict, *, first_batch: threading.Barrier | None = None):
    calls: list[dict] = []
    events: list[str] = []
    lock = threading.Lock()

    class _Client:
        async def close(self):
            pass

    class _LLM:
        last_usage = None
        model = "stub"

        def _make_async_client(self):
            return _Client()

        async def async_chat(self, system, messages, max_tokens=None, client=None, **kw):
            return ("片段分析", _USAGE)

        def chat_stream_long(self, system, messages, max_tokens=None, **kw):
            if _REL_MARKER in system:
                yield "[]"
                return _USAGE
            if "你正在整合关于" in system:       # 分组路径的合并调用（R3）
                yield "合并结果"
                return _USAGE
            group = re.search(r"CharacterCard\[(G\d)\]", system).group(1)
            with lock:
                events.append("start:" + group)
                calls.append({"group": group, "system": system, "messages": messages})
            if first_batch is not None and group in ("G6", "G1"):
                first_batch.wait()      # 两组不同批 → 等不齐、2 s 破障 → 该组失败
            yield json.dumps(_payload_for(group, arc), ensure_ascii=False)
            with lock:
                events.append("end:" + group)
            return _USAGE

    return _LLM(), calls, events


def _run(text: str, arc: dict, *, aliases=(_ALIAS,), first_batch=None, retro=None):
    llm, calls, events = _group_llm(arc, first_batch=first_batch)
    extra = {} if retro is None else {"retro": retro}
    frames = list(Distiller(llm=llm, config_path=None).distill_incremental_stream(
        text, _NAME, aliases=list(aliases), text_type="story", **extra))
    errors = [f for f in frames if isinstance(f, dict) and "error" in f]
    assert not errors, errors
    drafts = [json.loads(f) for f in frames if isinstance(f, str)]
    assert len(drafts) == 1, frames
    return {c["group"]: c for c in calls}, events, drafts[0]


@pytest.fixture(autouse=True)
def _no_usage_rows(monkeypatch):
    monkeypatch.setattr(D, "try_record_usage", lambda *a, **kw: None)


# ── S1 / S2：G6 读摘录、交章号 ─────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="PR 1 前：G6 读整本书")
def test_s1_arc_group_reads_the_numbered_excerpt_with_aliases():
    from core.retro_regions import ChapterRange, RetroRegions
    retro = RetroRegions("marked", (ChapterRange(4, 4, "日记"),))
    calls, _, _ = _run(_SRC, _CHAPTER_ARC, retro=retro)
    g6 = calls["G6"]["system"]
    for n in (1, 3, 5):
        assert f"【第{n}章】" in g6, f"摘录里缺【第{n}章】"
    assert "【第4章】" not in g6 and "日札：" not in g6, "回忆区的章进了摘录"
    unmarked, _, _ = _run(_SRC, _CHAPTER_ARC)       # 回忆区未标：不去任何章
    assert "【第4章】" in unmarked["G6"]["system"], "回忆区未标时少了章"
    assert "【第2章】" not in g6 and _CH2 not in g6, "没有他的章进了摘录"
    assert _FAR_1 not in g6, "名字前后 6 段以外的段进了摘录"
    assert _ALIAS_LINE in g6, "只用别名出场的段没进摘录（别名没传进来）"
    for group, call in calls.items():
        if group != "G6":
            assert _CH2 in call["system"], f"{group} 不再读整本书"


@pytest.mark.xfail(strict=True, reason="PR 1 前：G6 交锚点与 key_moments")
def test_s2_arc_group_is_asked_for_chapter_numbers_only():
    calls, _, _ = _run(_SRC, _CHAPTER_ARC)
    g6 = calls["G6"]["system"]
    assert "start_chapter" in g6
    assert "anchor" not in g6 and "key_moments" not in g6, "G6 仍要锚点或 key_moments"


# ── S3：章号 → 位置，只在 locate_starts ────────────────────────────────────

def _starts(phases, src=_SRC, retro=()):
    return phase_anchoring.build_anchors(phases, src, retro=retro)


@pytest.mark.xfail(strict=True, reason="PR 1 前：章号不参与定位")
def test_s3a_chapter_number_maps_to_that_chapters_body_start():
    a = _starts(_CHAPTER_ARC["phases"], retro=_retro_ch4())
    assert a.reason == ""
    assert [lo for lo, _ in a.ranges] == [0, _norm_body_start(_SRC, 3), _norm_body_start(_SRC, 5)]


@pytest.mark.xfail(strict=True, reason="PR 1 前：章号不参与定位")
def test_s3b_missing_retro_or_unordered_chapters_skip_the_card():
    def with_chapters(*nums):
        return [{**p, "start_chapter": n} for p, n in zip(_CHAPTER_ARC["phases"], (0, *nums))]

    by = {st.k: st.status for st in _starts(with_chapters(3, 9)).starts}
    assert by[3] is phase_anchoring.StartStatus.NOT_FOUND, "不存在的第 9 章没判成查不到"
    by = {st.k: st.status for st in _starts(with_chapters(4, 5), retro=_retro_ch4()).starts}
    assert by[2] is phase_anchoring.StartStatus.IN_RETRO, "回忆区的第 4 章没判成回忆区"
    assert "不严格递增" in _starts(with_chapters(5, 3)).reason
    chapterless = [{**p, "start_chapter": n} for p, n in zip(_CHAPTER_ARC["phases"][:2], (0, 1))]
    by = {st.k: st.status for st in _starts(chapterless, src=_CHAPTERLESS).starts}
    assert by[2] is phase_anchoring.StartStatus.NOT_FOUND, "没有章节的书给了章号却没判成查不到"


# ── S4：阶段列表写起始章 ──────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="PR 1 前：阶段列表写起始原句")
def test_s4_dependent_groups_see_the_start_chapter():
    from core.distiller import PHASE_DEPENDENT_GROUPS
    calls, _, _ = _run(_SRC, _CHAPTER_ARC)
    for group in PHASE_DEPENDENT_GROUPS:
        system = calls[group]["system"]
        assert "第3章" in system and "第5章" in system, f"{group} 的阶段列表没写起始章"
        assert "起始原句" not in system, f"{group} 的阶段列表仍写起始原句"


# ── S5：缓存预热 ────────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="PR 1 前：G6 单独先发")
def test_s5_g1_warms_the_book_prefix_alongside_g6():
    calls, events, _ = _run(_SRC, _CHAPTER_ARC, first_batch=threading.Barrier(2, timeout=2))
    last_first = max(events.index("end:G6"), events.index("end:G1"))
    rest = [g for g in FORMAT_GROUPS if g not in ("G6", "G1")]
    assert all(events.index("start:" + g) > last_first for g in rest), events
    assert calls["G1"]["system"].startswith(D.book_prefix(_SRC)), "G1 不是整书前缀开头"


# ── S6：不再重新定位 ────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason="PR 1 前：重定位还在")
def test_s6_relocation_is_gone():
    assert not hasattr(Distiller, "locate_phase_starts")
    assert not (_ROOT / "core" / "phase_locate.py").exists()
    route = (_ROOT / "web" / "routers" / "distill.py").read_text(encoding="utf-8")
    assert "locate_phase_starts" not in route and '"重定位"' not in route


# ── 回归守卫（main 上就绿）─────────────────────────────────────────────────

def test_r1_chapterless_book_keeps_whole_book_and_anchor():
    calls, _, draft = _run(_CHAPTERLESS, _ANCHOR_ARC)
    g6 = calls["G6"]["system"]
    assert g6.startswith(D.book_prefix(_CHAPTERLESS)) and "anchor" in g6
    assert "start_chapter" not in g6
    assert _starts(draft["character_arc"]["phases"], src=_CHAPTERLESS).reason == ""


def test_r3_grouped_path_keeps_anchor_even_with_chapters():
    """分组路径（`_takes_one_pass` 为假）：有章节也不换摘录，G6 仍读分析档案、仍交锚点。"""
    llm, calls, _ = _group_llm(_ANCHOR_ARC)
    d = Distiller(llm=llm, config_path=None)
    d._longctx_threshold = 0            # 强制走分组路径（生产判据同一个属性，`:1903-1908`）
    frames = list(d.distill_incremental_stream(_SRC, _NAME, aliases=[_ALIAS], text_type="story"))
    assert not [f for f in frames if isinstance(f, dict) and "error" in f], frames
    g6 = next(c for c in calls if c["group"] == "G6")["system"]
    assert "anchor" in g6 and "start_chapter" not in g6
    assert "【第" not in g6, "分组路径的 G6 换成了摘录"


def test_r2_old_anchor_draft_on_chaptered_book_still_checked_by_anchor():
    phases = [{"label": "拒人", "state": "", "anchor": ""},
              {"label": "入世", "state": "", "anchor": "小灼推开窗，说他要进宫去。"}]
    a = _starts(phases, retro=_retro_ch4())
    assert a.reason == ""
    assert a.ranges[1][0] == _norm_body_start(_SRC, 3)
