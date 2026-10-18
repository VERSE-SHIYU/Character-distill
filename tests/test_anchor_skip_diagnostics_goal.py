# -*- coding: utf-8 -*-
"""目标检查：整卡跳过位置核对时，日志说清楚错在哪（补日志）。

起因（祈灼重蒸 10-18，任务 bac8b73b6380）：日志只有一行「锚点位置不严格递增：716034 → 224224」，
而且打了两次；看不出是哪个阶段的哪句锚点、落在第几章、是不是回忆，只能手工回原文查。目标：
1. 每张卡只打一次「整卡跳过位置检查」（做法、记忆、字段、关系四类都有内容时也只一次）；
2. 跳过时每个阶段一行明细：阶段号、label、锚点原句、查没查到、第几章、在不在回忆区（哪一段）、
   前文 60 字（原文，不是规范化后的文字）；
3. 监测汇总行 `[phase_anchoring]` 带上这张卡用的回忆区，未标写「未标」；
4. 不跳过时不打明细。

替身书：第 1–3 章正文，第 4 章是日札（倒叙）。
"""
from __future__ import annotations

import logging

from core.card_draft import card_from_draft
from core.quotes import normalize
from core.retro_regions import NOT_DETECTED, ChapterRange, RetroRegions


_SRC = ("第1章\n开头甲甲甲甲甲。旧事丁丁丁丁丁。\n"
        "第2章\n中间乙乙乙乙乙。\n"
        "第3章\n结尾丙丙丙丙丙。\n"
        "第4章\n【日札】回忆戊戊戊戊戊。旧事丁丁丁丁丁。中间己己己己己。\n")
_RETRO = RetroRegions("marked", (ChapterRange(4, 4, "日记"),))


def _occ(phase, quote):
    return [{"phase": phase, "quote": quote}]


def _draft(anchor2, anchor3="结尾丙丙丙丙丙", *, full=True):
    d = {"name": "角色",
         "character_arc": {"axis": "a", "phases": [
             {"label": "L1", "state": "S1", "anchor": "开头甲甲甲甲甲"},
             {"label": "L2", "state": "S2", "anchor": anchor2},
             {"label": "L3", "state": "S3", "anchor": anchor3}]},
         "situation_behaviors": [{"situation": "做法甲", "behavior": "b",
                                  "occurrences": _occ(3, "结尾丙丙丙丙丙")}]}
    if full:                     # 四类都有内容：今天做法与关系各打一次
        d["key_memories"] = [{"memory": "记忆甲", "occurrences": _occ(1, "开头甲甲甲甲甲")}]
        d["relationships"] = [{"target": "某人", "relation": "友",
                               "attitudes": [{"phase": 2, "attitude": "信任",
                                              "quote": "中间乙乙乙乙乙"}]}]
    return d


def _messages(caplog, retro, draft):
    with caplog.at_level(logging.WARNING):
        card_from_draft(draft, _SRC, retro=retro)
    return [r.getMessage() for r in caplog.records]


def test_g1_one_skip_warning_per_card(caplog):
    msgs = _messages(caplog, _RETRO, _draft("中间己己己己己"))
    assert [m for m in msgs if "整卡跳过位置检查" in m] == [
        "整卡跳过位置检查（阶段 2 锚点落在回忆区（第4–4章日记）：中间己己己己己）：角色"]


def test_g2_each_phase_gets_one_detail_line(caplog):
    msgs = _messages(caplog, _RETRO, _draft("中间己己己己己", anchor3="查无此句"))
    assert [m for m in msgs if m.startswith("整卡跳过明细")] == [
        "整卡跳过明细（角色）阶段 1「L1」锚点「开头甲甲甲甲甲」→ 第1章，规范化位置 3，不在回忆区；"
        "前文：第1章⏎",
        "整卡跳过明细（角色）阶段 2「L2」锚点「中间己己己己己」→ 第4章，规范化位置 58，"
        "在回忆区（第4–4章日记）；前文：" + _SRC[_SRC.index("中间己") - 60:_SRC.index("中间己")].replace("\n", "⏎"),
        "整卡跳过明细（角色）阶段 3「L3」锚点「查无此句」→ 原文查不到"]


def test_g3_out_of_order_anchors_show_both_chapters(caplog):
    """祈灼那种情形：两个锚点都查得到，只是先后反了 —— 明细要把两处章号都给出来。"""
    msgs = _messages(caplog, NOT_DETECTED, _draft("结尾丙丙丙丙丙", anchor3="中间乙乙乙乙乙", full=False))
    detail = [m for m in msgs if m.startswith("整卡跳过明细")]
    assert ([("→ 第3章" in m, "→ 第2章" in m) for m in detail[1:]],
            [m for m in msgs if "整卡跳过位置检查" in m]) == (
        [(True, False), (False, True)],
        ["整卡跳过位置检查（锚点位置不严格递增：30 → 20）：角色"])


def test_g4_summary_line_names_the_retro_regions(caplog):
    marked = _messages(caplog, _RETRO, _draft("中间乙乙乙乙乙", full=False))
    caplog.clear()
    unmarked = _messages(caplog, NOT_DETECTED, _draft("中间乙乙乙乙乙", full=False))
    pick = lambda ms: [m.rsplit(" ", 1)[-1] for m in ms if m.startswith("[phase_anchoring]")]
    assert (pick(marked), pick(unmarked)) == (
        ["retro=第4–4章日记"], ["retro=未标（本入口不识别回忆区）"])


def test_g5_no_detail_lines_when_not_skipped(caplog):
    msgs = _messages(caplog, _RETRO, _draft("中间乙乙乙乙乙"))
    assert [m for m in msgs if "整卡跳过" in m] == []


def test_g6_raw_conversion_failure_falls_back_to_unknown_chapter(caplog, monkeypatch):
    """换不回原文坐标（`raw_offsets` 给 None）→ 章号写「章号未知」，前文退回规范化文字。"""
    monkeypatch.setattr("core.phase_anchoring.raw_offsets", lambda *a: [None] * len(a[2]))
    msgs = _messages(caplog, _RETRO, _draft("中间己己己己己"))
    detail = [m for m in msgs if m.startswith("整卡跳过明细")]
    norm = normalize(_SRC)
    assert ([m.split("；前文：")[0].rsplit("→ ", 1)[-1] for m in detail],
            [m.split("；前文：")[1] for m in detail]) == (
        ["章号未知，规范化位置 3，不在回忆区",
         "章号未知，规范化位置 58，在回忆区（第4–4章日记）",
         "章号未知，规范化位置 30，不在回忆区"],
        [norm[max(0, p - 60):p] for p in (3, 58, 30)])
