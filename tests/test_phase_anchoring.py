# -*- coding: utf-8 -*-
"""按原文位置校正「做法属于哪个阶段」—— spec `docs/specs/arc-phase-anchoring.md` §4。

纯计算层（`core.phase_anchoring` + `core.quotes.locate_in_normalized`）与五个调用点
（E1–E5）都在本文件核。本文是变异驱动 `tests/perf/arc_phase_anchoring_mutations.py` 的
覆盖域（`tests/test_lock_coverage.py` 按文件计域）。

**两条贯穿全篇的约定**（否则变异会存活）：
1. 拒侧用例里，被测做法必须**另有一个能通过的标注** —— 否则兜底 D5 会把整条退回，被测的
   标注去没去掉结果一样（§4.1）。
2. 位置证据只取**最长一段**在原文里的位置；短段（「这」）满篇都是，拿它定位置等于没定（C12）。

**每条用例只留一条 assert。** `pytest --tb=long` 只报**每条用例第一处**失败断言，同一条用例
里的后续断言永远进不了红源，会被元锁记成「没有任何变异撞到」的死判据。故凡是要判断多件事
的，都并成一条元组/结构相等的断言 —— 任一分量变坏，这条断言就红。
"""
import json
import logging
import re
import uuid
from pathlib import Path

from core import phase_anchoring
from core.card_draft import DraftBehavior, card_from_draft
from core.quotes import locate_in_normalized, normalize, verbatim_in_normalized
from core.schema import SituationBehavior

REPO = Path(__file__).resolve().parent.parent

# 三段，各有独特 token：锚点位置与阶段范围精确可算。
# normalize 后 = 开头甲甲甲中间乙乙乙结尾丙丙丙  (len 15)
_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q1, _Q2, _Q3 = "开头甲甲甲", "中间乙乙乙", "结尾丙丙丙"   # 起点 0 / 5 / 10

_KONG = normalize((REPO / "tests" / "fixtures" / "kongyiji.txt").read_text(encoding="utf-8"))


def _phases(n, anchors=None):
    return [{"label": f"L{i}", "state": f"S{i}", "anchor": (anchors or {}).get(i, "")}
            for i in range(1, n + 1)]


def _row(occurrences, situation="情境"):
    return {"situation": situation, "behavior": "做法", "occurrences": occurrences}


def _occ(phase, quote):
    return {"phase": phase, "quote": quote}


def _draft(behaviors, n=3, anchors=None, name="角色"):
    return {"name": name,
            "character_arc": {"axis": "a", "phases": _phases(n, anchors)},
            "situation_behaviors": behaviors}


_A3 = {2: _Q2, 3: _Q3}          # 阶段 1 锚点留空 → 从 0 开始


def _card(behaviors, src=_SRC, n=3, anchors=_A3):
    return card_from_draft(_draft(behaviors, n=n, anchors=anchors), src)


def _placement(card):
    """(顶层做法名单, 各阶段做法名单)。"""
    dump = card.model_dump()
    return (dump["situation_behaviors"],
            [p["behaviors"] for p in dump["character_arc"]["phases"]])


def _beh(situation, *, quote, behavior="做法"):
    return {"situation": situation, "behavior": behavior, "source_quote": quote}


# ── 4.1 单元 ──────────────────────────────────────────────────────────

def test_u1_phase_ranges_are_half_open():
    """半开区间 [锚点 k, 锚点 k+1)，锚点取首次出现；阶段 1 空锚点从 0 起，末阶段到全文末尾。"""
    assert phase_anchoring.phase_ranges(_phases(3, _A3), normalize(_SRC)) == (
        [(0, 5), (5, 10), (10, 15)], "")


def test_u1b_anchor_position_belongs_to_the_later_phase_not_the_earlier():
    """端到端边界：锚点位置（5）上的摘录属阶段 2 —— 上界若含（<=），它也会落进阶段 1。"""
    card = _card([_row([_occ(1, _Q2), _occ(2, _Q2)])])
    assert _placement(card) == ([], [[], [_beh("情境", quote=_Q2)], []])


def test_u1c_character_just_before_the_anchor_belongs_to_the_earlier_phase():
    """锚点前一字（位置 4）属阶段 1 —— 上界若退一（不含锚点前一字），它会被去掉。"""
    card = _card([_row([_occ(1, "甲中"), _occ(2, _Q2)], "前一字")])
    assert _placement(card) == ([], [[_beh("前一字", quote="甲中")],
                                     [_beh("前一字", quote=_Q2)], []])


def test_u2_locate_takes_the_longest_segment():
    """「这……下回还清罢」的位置由最长段「下回还清罢」定（断腿之后），不是开篇的「这」。"""
    assert locate_in_normalized(_KONG, "这……下回还清罢") == [1907]


def test_u3_locate_returns_every_start_of_the_segment():
    """最长段在原文出现多次 → 返回**全部**起点（拿它判是否落在某阶段范围）。"""
    assert locate_in_normalized(_KONG, "这") == [m.start() for m in re.finditer("这", _KONG)]


def test_u4_locate_is_none_when_any_segment_is_absent():
    assert locate_in_normalized(_KONG, "从没出现过的句子") is None


def test_u5_verbatim_is_false_when_any_segment_is_absent():
    assert verbatim_in_normalized(_KONG, "下回还清罢……从没出现过的句子") is False


def test_u6_verbatim_ignores_segment_order():
    """C11：不新增顺序要求 —— 正序与倒序两种写法都通过。"""
    assert (verbatim_in_normalized(_KONG, "温一碗酒……下回还清罢"),
            verbatim_in_normalized(_KONG, "下回还清罢……温一碗酒")) == (True, True)


def test_u7_tag_kept_when_quote_is_in_the_tagged_phase_dropped_when_not():
    card = _card([_row([_occ(1, _Q1)], "在阶段1"),
                  _row([_occ(2, _Q1), _occ(3, _Q3)], "错标阶段2")])
    assert _placement(card) == (
        [],
        [[_beh("在阶段1", quote=_Q1)],
         [],
         [_beh("错标阶段2", quote=_Q3)]])


def test_u8_quote_not_found_is_dropped():
    card = _card([_row([_occ(1, "从没出现过"), _occ(2, _Q2)], "查不到")])
    assert _placement(card) == ([], [[], [_beh("查不到", quote=_Q2)], []])


def test_u9_up_to_max_occurrences_is_evidence():
    """最长段恰好出现 3 次、一处在范围内 → 保留（3 ≤ MAX_OCCURRENCES）。两阶段都成立 → 顶层。"""
    src = "重复重复重复此处独有"                        # 「重复」@0/2/4；阶段 2 锚点 @6
    card = _card([_row([_occ(1, "重复"), _occ(2, "此处独有")], "三次")],
                 src=src, n=2, anchors={2: "此处独有"})
    assert _placement(card) == ([_beh("三次", quote="重复")], [[], []])


def test_u9b_over_max_occurrences_is_not_evidence():
    """出现 4 次（> MAX_OCCURRENCES）→ 不作位置证据，该阶段标去掉。"""
    src = "重复重复重复重复此处独有"                     # 「重复」×4
    card = _card([_row([_occ(1, "重复"), _occ(2, "此处独有")], "四次")],
                 src=src, n=2, anchors={2: "此处独有"})
    assert _placement(card) == ([], [[], [_beh("四次", quote="此处独有")]])


def test_u10_out_of_range_number_alone_retracts_the_row():
    """全部越界 → 整条撤回（与改前一致）。9 若被夹到末阶段，这条又会挂回阶段 3。"""
    card = _card([_row([_occ(9, _Q3)], "越界九")])
    assert _placement(card) == ([], [[], [], []])


def test_u11_in_range_number_kept_alongside_an_out_of_range_one():
    """越界编号先去掉，剩下的合法编号照常查位置。9 若被夹到末阶段，阶段 3 会多出一条。"""
    card = _card([_row([_occ(1, _Q1), _occ(9, _Q3)], "混合")])
    assert _placement(card) == ([], [[_beh("混合", quote=_Q1)], [], []])


def test_u12_invalid_number_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        _card([_row([_occ(1, _Q1), _occ(9, _Q3)], "混合")])
    assert any("阶段编号不合法" in r.getMessage() for r in caplog.records)


def test_u13_fallback_restores_when_every_tag_was_dropped():
    card = _card([_row([_occ(3, _Q1)], "全丢")])
    assert _placement(card) == ([], [[], [], [_beh("全丢", quote=_Q1)]])


def test_u14_fallback_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        _card([_row([_occ(3, _Q1)], "全丢")])
    assert any("兜底" in r.getMessage() for r in caplog.records)


def test_u15_partial_drop_does_not_fallback():
    card = _card([_row([_occ(1, _Q1), _occ(3, _Q1)], "去一半")])
    assert _placement(card) == ([], [[_beh("去一半", quote=_Q1)], [], []])


def test_u16_missing_anchor_keeps_every_tag_unchecked():
    """锚点查不到 → 整卡跳过位置检查：阶段 2 的错标（Q1 其实在阶段 1）不被去掉。"""
    card = _card([_row([_occ(2, _Q2), _occ(3, _Q1)], "错标")],
                 anchors={2: _Q2, 3: "查无此句"})
    assert _placement(card) == (
        [],
        [[], [_beh("错标", quote=_Q2)], [_beh("错标", quote=_Q1)]])


def test_u17_missing_anchor_warns_skip(caplog):
    with caplog.at_level(logging.WARNING, logger="core.phase_anchoring"):
        _card([_row([_occ(2, _Q2)], "x")], anchors={2: _Q2, 3: "查无此句"})
    assert any("跳过" in r.getMessage() for r in caplog.records)


def test_u18_reversed_anchors_skip_the_whole_card():
    """锚点位置不严格递增 → 整卡跳过（保留模型标注）。

    标注必须**能被位置检查部分去掉**，否则「跳过」与「不跳过+兜底」结果相同、这条判据
    对 M5b（去掉递增检查）没有分辨力：阶段 3 的摘录在乱序范围里仍落在范围内，不跳过就会
    只留阶段 3。
    """
    card = _card([_row([_occ(2, _Q2), _occ(3, _Q3)], "乱序")],
                 anchors={2: _Q3, 3: _Q2})
    assert _placement(card) == (
        [],
        [[], [_beh("乱序", quote=_Q2)], [_beh("乱序", quote=_Q3)]])


def test_u19_duplicate_quote_any_hit_keeps_and_all_miss_drops():
    """「甲乙」@0、@6：一处在范围内 → 该标注通过；两处都不在范围内 → 去掉。

    拒侧那条另有一个能通过的标注（阶段 1 的「甲乙」）—— 否则兜底 D5 会把整条退回。
    """
    src = "甲乙丙丁戊己甲乙"
    anchors = {2: "丙丁", 3: "戊己"}                     # 阶段 1/2/3 = [0,2)/[2,4)/[4,8)
    card = _card([_row([_occ(3, "甲乙")], "收：一处在阶段3"),
                  _row([_occ(1, "甲乙"), _occ(2, "甲乙")], "拒：两处都不在阶段2")],
                 src=src, anchors=anchors)
    assert _placement(card) == (
        [],
        [[_beh("拒：两处都不在阶段2", quote="甲乙")],
         [],
         [_beh("收：一处在阶段3", quote="甲乙")]])


def test_u20_phase_absent_when_no_quote_is_in_range():
    """摘录落在所标阶段之外 → 该阶段不成立（不因「标了就算」而留下一条空引用）。"""
    src = "甲乙丙丁戊己甲乙"
    anchors = {2: "丙丁", 3: "戊己"}                     # 阶段 3 = [4,8)；「丙丁」@2 不在其中
    card = _card([_row([_occ(1, "甲乙"), _occ(3, "丙丁")], "阶段3不成立")],
                 src=src, anchors=anchors)
    assert _placement(card) == ([], [[_beh("阶段3不成立", quote="甲乙")], [], []])


def test_u21_source_quote_is_the_passing_one_not_the_first():
    """同阶段多段摘录：`source_quote` 取**通过**的那段，不是该做法的第一条。"""
    card = _card([_row([_occ(3, "查无此句"), _occ(3, _Q3)], "多段")])
    assert _placement(card) == ([], [[], [], [_beh("多段", quote=_Q3)]])


def test_u22_monitoring_line_reports_counts(caplog):
    """监测行（D8）：字段齐全、计数按口径。这条做法摘录落在阶段 3 之外 → 去掉并兜底。"""
    with caplog.at_level(logging.INFO, logger="core.card_draft"):
        _card([_row([_occ(3, _Q1)], "全丢")])
    line = [r.getMessage() for r in caplog.records if "[phase_anchoring]" in r.getMessage()][0]
    assert line == ("[phase_anchoring] card=角色 tags=1 dropped=1 ambiguous=0 "
                    "unverified_quotes=0 fallback=1 skipped_card=False memories_dropped=0")


def test_u23_empty_anchor_on_last_phase_skips_the_whole_card():
    """末阶段锚点为空 = 不可定位 → 整卡跳过（§9.5 A1）。

    不跳过就会得到空区间 `[len, len)`，阶段 3 的标注被静默去掉，只剩阶段 2。
    """
    card = _card([_row([_occ(2, _Q2), _occ(3, _Q3)], "末阶段")], anchors={2: _Q2, 3: ""})
    assert _placement(card) == (
        [], [[], [_beh("末阶段", quote=_Q2)], [_beh("末阶段", quote=_Q3)]])


def test_u24_empty_anchor_on_a_middle_phase_skips_with_its_own_reason(caplog):
    """中间阶段锚点为空 → 整卡跳过，原因写「锚点为空」，不是碰巧撞上递增检查（§9.5 A1）。"""
    with caplog.at_level(logging.WARNING, logger="core.phase_anchoring"):
        _card([_row([_occ(3, _Q3)], "x")], anchors={2: "", 3: _Q3})
    assert [r.getMessage() for r in caplog.records if "跳过" in r.getMessage()] == [
        "整卡跳过位置检查（阶段 2 锚点为空）：角色"]


_COUNT_ROWS = [_row([_occ(2, "查无此句"), _occ(2, _Q2), _occ(1, _Q3)], "计数")]
"""阶段 2：第一段核对不上、第二段通过；阶段 1：摘录在阶段 3 → 去掉。
共 2 个标注、去掉 1 个、核对不上的摘录 1 段（§9.5 A2）。"""


def test_u25_monitoring_counts_tags_not_quotes(caplog):
    with caplog.at_level(logging.INFO, logger="core.card_draft"):
        _card(_COUNT_ROWS)
    line = [r.getMessage() for r in caplog.records if "[phase_anchoring]" in r.getMessage()][0]
    assert line == ("[phase_anchoring] card=角色 tags=2 dropped=1 ambiguous=0 "
                    "unverified_quotes=1 fallback=0 skipped_card=False memories_dropped=0")


def test_u26_skipped_card_still_counts_its_tags(caplog):
    """整卡跳过时 tags 照常计数 —— 监测比例才有分母（§9.5 A2）。"""
    with caplog.at_level(logging.INFO, logger="core.card_draft"):
        _card(_COUNT_ROWS, anchors={2: _Q2, 3: "查无此句"})
    line = [r.getMessage() for r in caplog.records if "[phase_anchoring]" in r.getMessage()][0]
    assert line == ("[phase_anchoring] card=角色 tags=2 dropped=0 ambiguous=0 "
                    "unverified_quotes=0 fallback=0 skipped_card=True memories_dropped=0")


def test_u27_each_dropped_tag_warns_once(caplog):
    """规则 3：去掉的每个标注各打一条 warning（情境 + 阶段号）；通过的不打（§9.5 A3）。"""
    with caplog.at_level(logging.WARNING, logger="core.phase_anchoring"):
        _card(_COUNT_ROWS)
    assert [r.getMessage() for r in caplog.records if "去掉标注" in r.getMessage()] == [
        "去掉标注：做法 '计数' 在阶段 1 没有落在该阶段的摘录"]


# ── 4.2 调用点矩阵：五个入口各喂同一份草稿 + 原文 ─────────────────────

# 情境一在阶段 1（第 2 条摘录）与阶段 3 都通过；情境二阶段 2 那条错标去掉、阶段 3 通过。
_ANCHOR_DRAFT = _draft([
    _row([_occ(1, "查无此句"), _occ(1, _Q1), _occ(3, _Q3)], "情境一"),
    _row([_occ(2, _Q1), _occ(3, _Q3)], "情境二"),
], anchors=_A3, name="角色")

_ANCHORED = (
    [],
    ["情境一"], [], ["情境一", "情境二"],
    _Q1, _Q3,                                     # 阶段 1 / 阶段 3 首条的 source_quote
)


def _assert_anchored(card):
    """位置检查生效 + 阶段取到通过的那条摘录。"""
    top, by = _placement(card)
    assert (top,
            [b["situation"] for b in by[0]],
            [b["situation"] for b in by[1]],
            [b["situation"] for b in by[2]],
            by[0][0]["source_quote"], by[2][0]["source_quote"]) == _ANCHORED


def _distiller(monkeypatch, *, chunked):
    from unittest.mock import MagicMock

    from core.distiller import Distiller
    monkeypatch.setattr("core.distiller.try_record_usage", lambda **kw: None)

    async def _close():
        return None

    async def _map(system, messages, max_tokens=None, client=None, **kw):
        return ("片段分析", {"prompt_tokens": 1, "completion_tokens": 1})

    client = MagicMock()
    client.close = _close
    llm = MagicMock()
    llm.last_usage = None
    llm.model = "m"
    llm._make_async_client = MagicMock(return_value=client)
    llm.async_chat = _map
    d = Distiller(llm=llm, config_path=None)
    d._chunk_size = 3000
    d._longctx_threshold = 0 if chunked else 10 ** 9
    return d


def _reply(monkeypatch, d, payload):
    text = json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(d, "_chat_accounted", lambda *a, **kw: (text, False))
    monkeypatch.setattr(d, "_do_reduce", lambda *a, **kw: "合并档案")


class TestEntries:
    def test_entry_distill_anchors(self, monkeypatch):
        d = _distiller(monkeypatch, chunked=False)
        _reply(monkeypatch, d, _ANCHOR_DRAFT)
        _assert_anchored(d.distill(_SRC, "角色"))

    def test_entry_longcontext_anchors(self, monkeypatch):
        d = _distiller(monkeypatch, chunked=False)
        _reply(monkeypatch, d, _ANCHOR_DRAFT)
        _assert_anchored(d.distill_incremental(_SRC, "角色"))

    def test_entry_incremental_anchors(self, monkeypatch):
        d = _distiller(monkeypatch, chunked=True)
        _reply(monkeypatch, d, _ANCHOR_DRAFT)
        _assert_anchored(d.distill_incremental(_SRC, "角色"))


class _DraftStubDistiller:
    """`_CardStubDistiller` 的草稿版：流式交出整份草稿（带 anchor/occurrences）。"""

    def __init__(self, draft):
        self.draft = draft
        self.CARD = draft

    def identify_characters(self, content):
        return [{"name": "角色", "aliases": []}]

    def distill_incremental_stream(self, text, character_name, *, aliases=None,
                                   text_type="story", on_chunk_done=None,
                                   resume_candidates=None):
        yield {"status": "formatting", "current": 1, "total": 1}
        yield json.dumps(self.draft, ensure_ascii=False)

    def dialogue_candidates(self, content, name, aliases=(), roster=()):
        return [object()]

    def _auto_tag(self, card_dict):
        return []

    def finalize_card(self, card, content, name, aliases=(), roster=()):
        return card


async def _stub_roster(storage, distiller, text_id, user_id, content):
    return distiller.identify_characters(content)


def _anchoring_content_route(monkeypatch, draft):
    """让 `/start` 与 `/run_stream` 两条路由都拿到 `_SRC` 作原文。"""
    import threading

    import deps
    from routers import distill as D
    from test_distill_task_api import _FakeStore, _build_client, _run_to_card

    stub = _DraftStubDistiller(draft)

    # /start 后台任务：`_run_to_card` 把 content 直接交给任务
    monkeypatch.setattr(D, "resolve_characters", _stub_roster)
    saved, _ = _run_to_card(monkeypatch, _FakeStore(), stub, content=_SRC)

    # /run_stream：原文来自 text 记录。记下 `card_from_draft` 与落库各跑在哪条线程上，
    # 供 E5 核「位置检查挪出了事件循环线程」。
    threads: list[tuple[str, int]] = []
    real_cfd = D.card_from_draft

    def _spy(data, content):
        threads.append(("draft", threading.get_ident()))
        return real_cfd(data, content)

    monkeypatch.setattr(D, "card_from_draft", _spy)

    uid = f"usr_{uuid.uuid4().hex[:8]}"

    class _Store:
        async def get_text_owned(self, text_id, user_id):
            return {"id": text_id, "content": _SRC, "text_type": "story"}

        async def get_user_api_config(self, user_id):
            return {}

    stream_saved: list = []

    class _TM:
        async def save_distilled_card(self, text_id, card, user_id, *,
                                      embedding_key="", embedding_region=""):
            threads.append(("save", threading.get_ident()))
            stream_saved.append(card)
            return {"card_id": "card_x"}

    async def _no_llm(user_id, storage=None):
        return None

    monkeypatch.setattr(deps, "get_user_llm", _no_llm)
    monkeypatch.setattr(deps, "get_text_manager", lambda llm=None: _TM())
    client = _build_client(_Store(), uid)
    monkeypatch.setattr(deps, "get_distiller", lambda llm=None: stub)
    threads.clear()                      # 只留 /run_stream 这一段（R1 也走了同一份桩）
    resp = client.post("/api/distill/run_stream", json={"text_id": "txt1", "character_name": "角色"})
    assert resp.status_code == 200
    return saved, stream_saved, threads


def test_bg_task_anchors(monkeypatch):
    saved, _stream, _threads = _anchoring_content_route(monkeypatch, _ANCHOR_DRAFT)
    _assert_anchored(saved[0])


def test_run_stream_anchors(monkeypatch):
    _saved, stream, threads = _anchoring_content_route(monkeypatch, _ANCHOR_DRAFT)
    assert len(stream) == 1
    _assert_anchored(stream[0])
    draft_thread = next(t for tag, t in threads if tag == "draft")
    save_thread = next(t for tag, t in threads if tag == "save")
    assert draft_thread != save_thread, (
        "位置检查（card_from_draft）必须挪出事件循环线程 —— 同步跑会堵住整条 SSE")


# ── 4.3 结构锁 ────────────────────────────────────────────────────────

def _code_lines(path: Path):
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        yield no, line.split("#", 1)[0]


def test_s1_card_from_draft_has_five_production_callers_each_with_two_args():
    calls: dict[str, list[str]] = {}
    for root in ("core", "web"):
        for path in (REPO / root).rglob("*.py"):
            for _, code in _code_lines(path):
                # 覆盖直接调用 `card_from_draft(` 与 `to_thread(card_from_draft, …)` 两种形态；
                # import 行（`from … import card_from_draft, …`）与 def 行排除。
                if (re.search(r"card_from_draft\s*[(,]", code)
                        and not re.search(r"\bdef\s", code)
                        and not re.match(r"\s*(from|import)\b", code)):
                    rel = path.relative_to(REPO).as_posix()
                    calls.setdefault(rel, []).append(code.strip())
    counts = {k: len(v) for k, v in calls.items()}
    two_args = all("," in line.split("card_from_draft", 1)[1] for lines in calls.values()
                   for line in lines)
    assert (counts, two_args) == ({"core/distiller.py": 3, "web/routers/distill.py": 2}, True)


def test_s2_normalized_match_is_written_once():
    """「分段 + 在规范化全文里找位置」只写一份（spec §3.1、M14）。

    判据取三个特征串的**全 core 出现文件集合**：分段 `_ELLIPSIS.split(`、容器判定 `in
    source_norm`、找位置 `re.finditer(` —— 都只许落在 `core/quotes.py`。`re.finditer(`
    在 quotes.py 内出现多次（抽取对话也用它）是允许的。变异 M14 让 `verbatim_in_normalized`
    另写一份匹配，会重新引入分段与容器判定 → 前两条立刻多出一个文件（就是 quotes.py 自己，
    计数变 2）。**已知盲区**：换成别的变量名、别的找法（如 `str.index`）的改写溜得过 ——
    这是代理判据，见 spec §3.1「匹配只写一份」的意图。
    """
    def files_with(token: str) -> list[str]:
        out: list[str] = []
        for path in (REPO / "core").rglob("*.py"):
            for _, code in _code_lines(path):
                if token in code:
                    out.append(path.relative_to(REPO).as_posix())
        return sorted(set(out))

    assert [files_with("_ELLIPSIS.split("), files_with("in source_norm"),
            files_with("re.finditer(")] == [["core/quotes.py"]] * 3


def test_s3_draft_has_no_phase_tags_and_stored_shape_is_unchanged():
    fields = DraftBehavior.model_fields
    shape = ("phases" not in fields, "source_quote" not in fields, "occurrences" in fields,
             set(SituationBehavior.model_fields))
    assert shape == (True, True, True, {"situation", "behavior", "source_quote"})
