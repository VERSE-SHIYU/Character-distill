# -*- coding: utf-8 -*-
"""豁免表校验层（``tests/policy_table.py``）自己的锁：用**合成表**逐个函数断言恰等于期望。

合成表里不含任何真实路由 / 字段名 / 依赖名 —— 本层的命题不依赖仓库里有哪些表，
拿真实数据当料会让这层跟着业务一起变，而它本来要验的是「机械操作做对了没有」。

覆盖边界：**空表**（不是「表里没有键」，是「表本身为空」）、**全部合规**（三个函数都该
返回空集 —— 判据不能靠「总是返回点什么」蒙对）、**理由只有空白字符**（`" "` / `"\\t\\n "`
都是空，只有 strip 之后才算数）、**理由不是字符串**（`None` / `0` 一律算空 ——
「理由在不在」是结构问题，字符串化之后判长短是代理判据）、**键为元组**（本仓两张真实表
的键都是复合键，平铺的字符串键测不出「键本身是元组」这条路径）。
"""
from __future__ import annotations

import pytest

import policy_table


def test_empty_reasons_flags_blank_and_whitespace_only_reasons():
    table = {
        ("ok", 1): "有内容",
        ("empty", 2): "",
        ("spaces", 3): "   ",
        ("tab", 4): "\t\n ",
        ("padded", 5): "  有内容  ",
    }
    assert policy_table.empty_reasons(table) == {("empty", 2), ("spaces", 3), ("tab", 4)}


def test_empty_reasons_of_an_empty_table_is_empty():
    assert policy_table.empty_reasons({}) == set()


@pytest.mark.parametrize("reason", [None, 0, "", "   ", "\t\n "])
def test_empty_reasons_treats_a_missing_reason_as_empty(reason):
    """缺失值**不因为字符化后长得像文本**就蒙混过关（`None` → `"None"`、`0` → `"0"`）。

    逐值参数化而不是一条断言：`None` 与 `""` 走的是同一行代码的两个不同分支
    （类型判定 / 内容判定），合并成一条时其中一条分支坏了仍会绿。
    """
    assert policy_table.empty_reasons({("k",): reason}) == {("k",)}


def test_empty_reasons_keeps_a_real_reason():
    """反向：真的写了理由就不许红 —— 否则「一律判空」也能让上面那条通过。"""
    assert policy_table.empty_reasons({("k",): "理由"}) == set()
    assert policy_table.empty_reasons({("k",): "  理由  "}) == set()


def test_stale_keys_is_table_minus_observed():
    table = {("keep", "get"): "还在用", ("gone", "post"): "已经没了"}
    observed = {("keep", "get"), ("new", "post")}
    assert policy_table.stale_keys(table, observed) == {("gone", "post")}


def test_stale_keys_is_not_symmetric_in_its_two_arguments():
    """两个参数换向会得到**另一个**集合 —— 这条用例存在的唯一理由就是让那件事变红。"""
    table = {("a",): "x"}
    observed = {("b",)}
    assert policy_table.stale_keys(table, observed) == {("a",)}


def test_unexpected_is_observed_minus_table():
    observed = {("keep", "get"), ("new", "post")}
    table = {("keep", "get"): "还在用"}
    assert policy_table.unexpected(observed, table) == {("new", "post")}


def test_all_compliant_yields_only_empty_sets():
    """三种失效方向都试过之后仍要有「没问题」这个答案，否则判据只会恒红。"""
    table = {("a",): "理由"}
    observed = {("a",)}
    assert policy_table.empty_reasons(table) == set()
    assert policy_table.stale_keys(table, observed) == set()
    assert policy_table.unexpected(observed, table) == set()


def test_both_sides_empty():
    assert policy_table.stale_keys({}, set()) == set()
    assert policy_table.unexpected(set(), {}) == set()
