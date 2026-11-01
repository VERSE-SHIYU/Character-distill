# -*- coding: utf-8 -*-
"""`core/quote_repair.py` 的纯计算单测：候选段落预筛、模型抄回句子的核对。

原文是手写的一小段，每行一句 —— 候选按行取，行边界就是期望值。
"""
from __future__ import annotations

from core.quotes import normalize
from core.quote_repair import (
    ACCEPT_SIMILARITY,
    MIN_LEN,
    PREFILTER_CUTOFF,
    TOP_K,
    accept,
    candidates,
)

_SRC = (
    "云绮冷冷地看着她。\n"
    "她偏想做那投潭的石子，不肯回头。\n"
    "云绮扬手又是一记耳光，这次打得兰香踉跄后退。\n"
    "兰香低头道：谢陛下隆恩册封，谢太后垂爱赐宝。\n"
)
_NORM = normalize(_SRC)


# ── candidates ─────────────────────────────────────────────────────────────

def test_candidates_finds_the_changed_line():
    assert candidates(_SRC, _NORM, "她偏要做那投潭的石子") == [
        "她偏想做那投潭的石子，不肯回头。"]


def test_candidates_returns_the_matched_line_not_the_window_start():
    # 偏一个字，但窗口从上一行开头就可能覆盖到 —— 取的是命中位置那行。
    assert candidates(_SRC, _NORM, "扬手又是一记巴掌，这次打得兰香踉跄后退") == [
        "云绮扬手又是一记耳光，这次打得兰香踉跄后退。"]


def test_candidates_empty_for_unrelated_quote():
    # 与原文任何位置都远低于 PREFILTER_CUTOFF。
    assert candidates(_SRC, _NORM, "银河战舰在木星轨道集结完毕") == []


def test_candidates_empty_for_too_short_quote():
    short = "投潭呀"                                     # 规范化后 3 字
    assert len(normalize(short)) < MIN_LEN
    assert candidates(_SRC, _NORM, short) == []


def test_candidates_are_deduped_and_capped_at_top_k():
    src = "\n".join(f"第{i}句：她偏想做那投潭的石子，不肯回头然后继续走。" for i in range(10))
    out = candidates(src, normalize(src), "她偏要做那投潭的石子")
    assert len(out) <= TOP_K
    assert len(out) == len(set(out))


def test_candidates_skip_lines_that_normalize_to_nothing():
    src = "\n" + "她偏想做那投潭的石子。\n" + "\n"
    out = candidates(src, normalize(src), "她偏要做那投潭的石子")
    assert out == ["她偏想做那投潭的石子。"]


# ── accept ─────────────────────────────────────────────────────────────────

def test_accept_verbatim_original_sentence():
    assert accept(_NORM, "她偏要做那投潭的石子", "她偏想做那投潭的石子") is True


def test_accept_rejects_sentence_absent_from_source():
    assert accept(_NORM, "她偏要做那投潭的石子", "她偏要做那投潭的石头") is False


def test_accept_rejects_locatable_but_dissimilar_sentence():
    # 在原文里（第 4 行规范化后含「谢陛下隆恩册封」），但与摘录相似度远低于阈值。
    other = "谢陛下隆恩册封，谢太后垂爱赐宝"
    assert normalize(other) in _NORM
    assert accept(_NORM, "她偏要做那投潭的石子", other) is False


def test_accept_rejects_empty():
    assert accept(_NORM, "她偏要做那投潭的石子", None) is False
    assert accept(_NORM, "她偏要做那投潭的石子", "") is False


def test_accept_threshold_is_the_stated_value():
    assert ACCEPT_SIMILARITY == 75
    assert PREFILTER_CUTOFF == 60
