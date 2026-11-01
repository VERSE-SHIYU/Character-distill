# -*- coding: utf-8 -*-
"""D24 ①b 第 2 步目标检查：同片主名否决（两步并组共用一条判据）。

缺陷形态：名单的两步并组都是整组并查集，可传递且不看反证 —— 别名并组
（``group_identify_entries``）与全书判定之后的并组（``merge_identify_groups``）。
1 片写错别名、或判定给出 1 个错误组对，就会吞并整组。线上云汐玥 / 云肆野 /
昭华公主被并进云绮、楚临被并进楚翊，两条路都能造成。

判据：两个名字在同一个分片里**各自是主名** ≥ 2 片 → 有反证，不并。文献依据
Vala et al., EMNLP 2015（同处各自出现 = 两个人）与 arXiv 2607.26298（verified
merge：一条反证即阻止合并）；门槛 ≥ 2 片是本项目自定（模型偶尔在同一片里把同一个人
列成两条）。

夹具用两字姓名：`usable_alias` 丢弃单字别名（`roster_aggregate.usable_alias`），
单字名当别名时别名并组这条路根本不会触发。

期望值全部是字面量，不从被测模块 import 常量 —— 改常量时用例不跟着松。
"""

from __future__ import annotations

import pytest

from core.roster_aggregate import (
    group_identify_entries,
    merge_identify_groups,
    replay_roster,
)


def _chunks(people_by_chunk: dict[int, list[tuple[str, bool]]], total: int):
    """按分片号铺观测：{片号: [(主名, 是否亲口说话), ...]}，其余片为空。"""
    out = [[] for _ in range(total)]
    for idx, people in people_by_chunk.items():
        out[idx] = [
            {"name": n, "aliases": [], "spoke": s, "reason": f"{n}@{idx}"}
            for n, s in people
        ]
    return out


def _all_members(groups) -> set[frozenset]:
    return {frozenset(g["members"]) for g in groups}


def _veto_messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if "[distill] identify veto:" in r.getMessage()]


def _same_main(per, idx, alias):
    """把 per[idx] 第一条的别名写成 alias（别名步的并组依据）。"""
    per[idx][0]["aliases"] = [alias]
    return per


@pytest.mark.xfail(strict=True, reason="同片主名否决尚未实现（目标检查）")
def test_alias_merge_is_vetoed_by_shared_main_chunks(caplog):
    """#1 甲甲、乙乙在片 0–2 同为主名；片 5 把乙乙写成甲甲的别名 → 两组分开。"""
    per = _chunks({**{i: [("甲甲", False), ("乙乙", False)] for i in range(3)},
                   5: [("甲甲", False)]}, 6)
    _same_main(per, 5, "乙乙")   # 片 5：甲甲 的别名里有乙乙

    with caplog.at_level("WARNING"):
        groups = group_identify_entries(per)

    assert _all_members(groups) == {frozenset({"甲甲"}), frozenset({"乙乙"})}
    msgs = _veto_messages(caplog)
    assert len(msgs) == 1, msgs
    assert "甲" in msgs[0] and "乙" in msgs[0] and "3" in msgs[0], msgs[0]


@pytest.mark.xfail(strict=True, reason="同片主名否决尚未实现（目标检查）")
def test_judge_pair_is_vetoed_by_shared_main_chunks(caplog):
    """#2 甲甲、乙乙在片 0–2 同为主名；判定组对 [甲甲,乙乙] → 仍是两组。"""
    per = _chunks({i: [("甲甲", False), ("乙乙", False)] for i in range(3)}, 6)
    groups = group_identify_entries(per)

    with caplog.at_level("WARNING"):
        merged = merge_identify_groups(groups, [("甲甲", "乙乙")])

    assert _all_members(merged) == {frozenset({"甲甲"}), frozenset({"乙乙"})}
    assert _veto_messages(caplog), "被否决的组对要记 WARNING"


def test_one_shared_main_chunk_still_merges():
    """#3 对照：只在片 0 同为主名（1 片 < 门槛 2）→ 按判定组对并成一组。"""
    per = _chunks({0: [("甲甲", False), ("乙乙", False)],
                   1: [("甲甲", False)], 2: [("甲甲", False)]}, 6)
    groups = group_identify_entries(per)

    merged = merge_identify_groups(groups, [("甲甲", "乙乙")])

    assert _all_members(merged) == {frozenset({"甲甲", "乙乙"})}


def test_alias_merge_without_shared_main_chunks_still_happens():
    """#4 对照：甲甲在片 0–2、乙乙在片 3–5、片 2 把乙乙写成甲甲的别名 → 并成一组。"""
    per = _chunks({**{i: [("甲甲", False)] for i in range(3)},
                   **{i: [("乙乙", False)] for i in range(3, 6)}}, 6)
    _same_main(per, 2, "乙乙")   # 片 2：甲甲 的别名里有乙乙

    groups = group_identify_entries(per)

    assert _all_members(groups) == {frozenset({"甲甲", "乙乙"})}


@pytest.mark.xfail(strict=True, reason="同片主名否决尚未实现（目标检查）")
def test_veto_is_checked_per_pair_not_per_group():
    """#5 甲甲–乙乙可并，乙乙–丙丙不可（丙丙与甲甲同片）→ 丙丙仍单独一组。"""
    per = _chunks({**{i: [("甲甲", False)] for i in range(3)},
                   **{i: [("乙乙", False)] for i in range(3, 6)},
                   **{i: [("丙丙", False)] for i in range(3)}}, 6)
    groups = group_identify_entries(per)

    merged = merge_identify_groups(groups, [("甲甲", "乙乙"), ("乙乙", "丙丙")])

    assert _all_members(merged) == {
        frozenset({"甲甲", "乙乙"}), frozenset({"丙丙"})}


@pytest.mark.xfail(strict=True, reason="同片主名否决尚未实现（目标检查）")
def test_replay_roster_applies_the_same_veto():
    """#6 用 #1 的数据拼 trace（judge pairs 为空）→ 重算结果里是两行。"""
    per = _chunks({**{i: [("甲甲", False), ("乙乙", False)] for i in range(3)},
                   5: [("甲甲", False)]}, 6)
    _same_main(per, 5, "乙乙")
    trace = {"per_chunk": per, "judge": {"pairs": [], "impersonal": []}}

    roster = replay_roster(trace)

    assert len(roster) == 2, [r["name"] for r in roster]
    assert {r["name"] for r in roster} == {"甲甲", "乙乙"}
