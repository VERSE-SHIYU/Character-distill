# -*- coding: utf-8 -*-
"""`tests/perf/longbook_acceptance.py` 的 B2 判据、称呼定位与判定耗时的读数锁。

**为什么入库。** 这三处都只在跑真验收（真模型、真库）时才看得出对错，而它们的坏法都是
「安静地判错」：称呼只按组名查（查别名「宝玉」判成查不到），泛称判据把合法的次要泛称组
判成失败，耗时盯错了行（把逐片的 100 s 也算进判定）。三条都不会抛异常，只会让 §10 的
读数骗人 —— 所以每条断言都配一句它挡住的变异。
"""
from __future__ import annotations

import os
import sys
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "perf"))

import longbook_acceptance as acc  # noqa: E402

# 「宝玉」是「贾宝玉」组的别名 —— 识别的第 3 步判别名正是干这个，所以查询词常常不是组名
JIA_BAOYU = {"name": "贾宝玉", "aliases": ["宝玉", "宝二爷"], "importance": "主要"}


def test_alias_lookup_returns_the_group_name_plus_its_other_aliases():
    """查别名「宝玉」落到「贾宝玉」组，取回本名与其余别名（不含查询词本身）。

    挡住：`_groups_of` 退回「只比组名」（`g.get("name") == query`）—— 那样查「宝玉」
    命中 0 组、返回空列表，本断言红。
    """
    assert acc._aliases_of([JIA_BAOYU, {"name": "袭人", "aliases": [], "importance": "次要"}],
                           "宝玉") == ["贾宝玉", "宝二爷"]


def test_main_criterion_accepts_a_character_named_by_its_alias():
    """§10 B2 的主要人物判据按「name ∪ aliases」查（「恰命中 1 组且为主要」）。

    挡住：`_check_b2` 的主要人物判据退回按组名精确查 —— 「宝玉」查不到组，main_ok 变 0。
    """
    b2 = acc._check_b2([JIA_BAOYU], {"main": ["宝玉"]})
    assert b2["main_ok"] == 1 and b2["main_miss"] == []


def test_same_group_criterion_also_looks_through_aliases():
    """同组判据与 `_aliases_of` 共用同一个定位（「宝玉」与「贾宝玉」是同一个人）。

    挡住：同组判据退回只比组名 —— 「宝玉」查不到组，a/b 有一侧为空，same_ok 变 0。
    """
    b2 = acc._check_b2([JIA_BAOYU], {"same_group": [["宝玉", "贾宝玉"]]})
    assert b2["same_ok"] == 1 and b2["same_bad"] == []


def test_ambiguous_term_yields_no_aliases_and_fails_the_criterion(capsys):
    """同一称呼挂在 2 组上时定位不到唯一的人：返回空、stderr 报组数，B2 判其不通过。

    挡住：命中多组时仍无条件取 `idxs[0]` 的别名，以及主要人物判据放宽成「命中 ≥1 组
    即通过」—— 后者会把「贾宝玉/甄宝玉 是同一称呼」这种歧义报成合格。
    """
    dup = [{"name": "贾宝玉", "aliases": ["宝玉"], "importance": "主要"},
           {"name": "甄宝玉", "aliases": ["宝玉"], "importance": "次要"}]
    assert acc._aliases_of(dup, "宝玉") == []
    assert "2" in capsys.readouterr().err          # 命中组数要在 stderr 里看得见
    b2 = acc._check_b2(dup, {"main": ["宝玉"]})
    assert b2["main_ok"] == 0 and b2["main_miss"][0]["groups"] == 2


def test_a_secondary_generic_group_passes_but_a_major_one_fails():
    """WP16 修订把泛称组留在名单里、标为次要，判据只能看主要人物的组名。

    挡住：判据退回「不得是任何组的 name」—— 次要泛称组「婆子」被判失败，第一条红。
    """
    minor = [JIA_BAOYU, {"name": "婆子", "aliases": [], "importance": "次要"}]
    assert acc._check_b2(minor, {"generics": ["婆子"]})["generics_bad"] == []

    major = [{"name": "贾宝玉", "aliases": [], "importance": "主要"},
             {"name": "婆子", "aliases": [], "importance": "主要"}]
    assert acc._check_b2(major, {"generics": ["婆子"]})["generics_bad"] == ["婆子"]


# ── 判定耗时：`identify_mode` 里那两行 usage_stats 的减法 ──────────────────
# 把 `started_wall` 冻在一个定值上，耗时断言才是定值而不是「大概」。

BASE = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime:
    @staticmethod
    def now(tz=None):
        return BASE


class _Resp:
    status_code = 200

    @staticmethod
    def json():
        return {"characters": []}


class _Client:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @staticmethod
    def post(*a, **k):
        return _Resp()


def _usage_row(seconds: float) -> dict:
    return {"action": "distill_identify", "prompt_tokens": 1, "completion_tokens": 1,
            "created_at": BASE + timedelta(seconds=seconds)}


def _identify_stats(monkeypatch, rows) -> dict:
    """只打断网络与库：`identify_mode` 自己的取行、减法、组装照跑。"""
    monkeypatch.setattr(acc, "datetime", _FrozenDatetime)
    monkeypatch.setattr(acc, "http_client", lambda *a, **k: _Client())
    monkeypatch.setattr(acc, "fetch", lambda *a, **k: rows)
    args = types.SimpleNamespace(base_url="http://x", text_id="t", criteria="", character="")
    return acc.identify_mode(args, "dsn", "tok", "uid", {"_content": "", "text_type": "classic"})


def test_judge_s_is_the_span_between_the_first_and_last_usage_rows(monkeypatch):
    """判定耗时 = 末行 − 首行；逐片耗时的口径不变（首行 − 起算）。

    挡住：`judge_s` 退回「末行 − 起算」（WP16 修订前的 `alias_s`）—— 那样把逐片的
    100 s 也算进判定，得到 118.0 而不是 18.0，红。
    """
    stats = _identify_stats(monkeypatch, [_usage_row(100), _usage_row(118)])
    assert stats["map_s"] == 100.0
    assert stats["judge_s"] == 18.0


def test_judge_s_is_absent_with_a_single_usage_row(monkeypatch):
    """只有逐片一行时没有判定阶段可计时：报 None，不报 0（0 会读成「判定不花时间」）。"""
    stats = _identify_stats(monkeypatch, [_usage_row(100)])
    assert stats["map_s"] == 100.0
    assert stats["judge_s"] is None
