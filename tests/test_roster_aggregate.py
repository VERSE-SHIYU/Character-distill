# -*- coding: utf-8 -*-
"""逐片名单的纯函数：建组、并组、判主次、取理由 —— 不碰 LLM，直接喂列表。

住在 `core/roster_aggregate.py`（`Distiller` 的下游纯函数），**不放**
`core/character_roster.py`：那个模块是名单生命周期层，位于 `Distiller` 之上
（它 import `Distiller`），`Distiller` 反过来依赖它就是层级倒置。

三组锁：

  I3   歧义别名：不并组，且从**每一个**最终组里移除 —— 包括模型回 `[]` 时
       原样返回、没被合并的那些组。
  I3b  同一人不同主名共列的别名**不算**歧义（片1 贾宝玉{宝玉,宝二爷}、片2 宝玉{宝二爷}
       是同一组）；模型并组后原先多组共有的称呼只指向一个人，要保留；而送给别名
       判断模型的清单里也不列**当时**挂在 ≥2 组上的别名（泛称会诱导模型误并）。
  I3c  成员主名优先：「宝玉」是合并组的成员主名、又被甄宝玉组当别名 —— 只属于有它
       的那一组。只以别名身份挂在 ≥2 组上的，才从所有组移除。
  I3d  别名至少两个字 —— 单字称呼（「他」「玉」「爷」）按子串匹配几乎命中每一片。
  I4   主次按「本人说话的不同分片数 ≥ 6 且非泛称」判，组内按它降序，理由取自本人
       说话的分片 —— 出场多但几乎不说话的（「和尚」形态）出不了卡。
  I5   全书判定的多份样本按多数票取结果；同一份样本里重复列的组对只计一票。

判定「歧义」的时机是本文件的中心：**并组做完、按最终分组计数**。放在并组之前，
片1 贾宝玉{宝玉,宝二爷} 里的「宝玉」会被当成挂在两个主名下的泛称而丢掉。
"""

from __future__ import annotations

from core.roster_aggregate import (
    alias_prompt_rows,
    finalize_identify_roster,
    group_identify_entries,
    merge_identify_groups,
    tally_judge_samples,
)


def _all_members(groups) -> set[frozenset]:
    return {frozenset(g["members"]) for g in groups}


def _merged(per_chunk, pairs=(), impersonal=()):
    """建组 → 按组对并组（含泛称标注）→ 出最终组（移除歧义别名在这一步做）。"""
    return merge_identify_groups(
        group_identify_entries(per_chunk), list(pairs), impersonal)


def _chunks(people_by_chunk: dict[int, list[tuple[str, bool]]], total: int):
    """按分片号铺观测：{片号: [(主名, 是否亲口说话), ...]}，其余片为空。

    片号即分片序号 —— `_observations` 的 `chunk` 是 `enumerate(per_chunk)` 给的下标。
    """
    out = [[] for _ in range(total)]
    for idx, people in people_by_chunk.items():
        out[idx] = [
            {"name": n, "aliases": [], "spoke": s, "reason": f"{n}@{idx}"}
            for n, s in people
        ]
    return out


def _row(per_chunk, pairs=(), impersonal=()):
    """单个组的一条名单行 —— 这类用例都只关心一个组。"""
    roster = finalize_identify_roster(_merged(per_chunk, pairs, impersonal))
    assert len(roster) == 1, [r["name"] for r in roster]
    return roster[0]


# 片序即分片序号：片1 甲{二爷}、片2 乙{二爷}（「二爷」两处都挂 = 有歧义），
# 片3 丙{三爷}、片4 丁{三爷}（「三爷」同形）。甲另带一个唯一别名 —— 用来验
# 「移除歧义别名」不是「清空 aliases」。
_AMBIGUOUS_PER_CHUNK = [
    [{"name": "甲", "aliases": ["二爷", "甲别名"]}],
    [{"name": "乙", "aliases": ["二爷"]}],
    [{"name": "丙", "aliases": ["三爷"]}],
    [{"name": "丁", "aliases": ["三爷"]}],
]


class TestAmbiguousAliases:
    """歧义别名：不据此并组，**且从每一个最终组移除**（不只是不并组）。

    泛称一旦进了 aliases 就会污染下游的子串匹配（蒸馏选片的 `match_terms`、RAG
    打标签的 `_tag_characters`）——「二爷」同时指两个人时，留着它会把别人的场景
    标给此人。判据在代码里（模型只判「哪些组是同一个人」，管不了哪些称呼唯一）。

    变异对象 = ① 共享任一别名即并组（甲乙被并成一组 → 红）
              ② 删掉「从所有组移除」那一步（甲、乙的 aliases 里留着「二爷」→ 红）
    """

    def test_shared_alias_does_not_merge_and_is_stripped_everywhere(self):
        groups = _merged(_AMBIGUOUS_PER_CHUNK)

        assert _all_members(groups) == {
            frozenset({"甲"}), frozenset({"乙"}),
            frozenset({"丙"}), frozenset({"丁"}),
        }, "共用一个泛称不构成并组依据"
        by_name = {g["name"]: g for g in groups}
        assert by_name["甲"]["aliases"] == ("甲别名",), "唯一别名要留着（移除 ≠ 清空）"
        assert by_name["乙"]["aliases"] == ()
        assert "三爷" not in by_name["丙"]["aliases"]
        assert "三爷" not in by_name["丁"]["aliases"]

    def test_model_pairs_merge_and_ambiguous_alias_stays_out(self):
        groups = _merged(_AMBIGUOUS_PER_CHUNK, pairs=[("甲", "丙")])

        assert _all_members(groups) == {
            frozenset({"甲", "丙"}), frozenset({"乙"}), frozenset({"丁"}),
        }
        group = next(g for g in groups if "丙" in g["members"])
        assert "三爷" not in group["aliases"], "合并组同样不许留下歧义别名"
        assert "甲别名" in group["aliases"]
        assert group["name"] in {"甲", "丙"}


# 片1 贾宝玉 列了「宝玉」「宝二爷」；片2 只用主名「宝玉」，也列了「宝二爷」。
# 「宝玉」既是别名又是另一片的主名 —— 旧口径（按主名数）会把它当歧义丢掉，
# 两个人也并不到一起。
_SAME_PERSON_TWO_NAMES = [
    [{"name": "贾宝玉", "aliases": ["宝玉", "宝二爷"]}],
    [{"name": "宝玉", "aliases": ["宝二爷"]}],
]

# 甲、乙各列「某爷」：此刻「某爷」挂在两个组上。模型判它们同一人之后，
# 「某爷」就只指向一个人了。
_TWO_GROUPS_ONE_ALIAS = [
    [{"name": "甲", "aliases": ["某爷"]}],
    [{"name": "乙", "aliases": ["某爷"]}],
]


class TestSamePersonDifferentNames:
    """同一人的两个主名要被并成一组，且共有的称呼按**最终**分组判歧义。

    「宝玉」在片1 是别名、在片2 是主名。别名等于另一个主名、且此刻只挂在一个组上
    → 两组并成一组，反复做到不动点（这里一轮即可）。歧义要等并组之后再数：并完
    只剩一组，「宝二爷」就不该被移除。

    变异对象 = ① 歧义改回按「主名」计（`宝玉` 被当泛称 → 两组并不起来、`宝二爷`
                 被丢 → 红）
              ② 移除放到别名判断之前（甲/乙 合并前「某爷」就被删 → 合并组里没有
                 「某爷」→ 红）
              ③ 送模型的清单里带上多组共有的别名（清单里出现「某爷」→ 红）
    """

    def test_alias_that_is_another_name_merges_the_two_groups(self):
        groups = _merged(_SAME_PERSON_TWO_NAMES)

        assert len(groups) == 1, "「宝玉」是另一片的主名，不是泛称，两组该并"
        group = groups[0]
        assert set(group["members"]) == {"贾宝玉", "宝玉"}
        assert "宝二爷" in group["aliases"], "并完只剩一组，共有的称呼要留着"

    def test_shared_alias_survives_when_the_model_merges_the_groups(self):
        groups = _merged(_TWO_GROUPS_ONE_ALIAS, pairs=[("甲", "乙")])

        assert len(groups) == 1
        assert "某爷" in groups[0]["aliases"], "并组后「某爷」只指向一个人，该保留"

    def test_alias_prompt_rows_hide_aliases_listed_by_several_groups(self):
        """送模型的清单里不列当时挂在 ≥2 组上的别名 —— 泛称会诱导模型误并。"""
        groups = group_identify_entries(_TWO_GROUPS_ONE_ALIAS)

        rows = alias_prompt_rows(groups)
        sent = [(name, chunks, aliases) for name, chunks, aliases in rows]

        assert [name for name, _c, _a in sent] == ["甲", "乙"], "每组主名都要进清单"
        assert all(chunks == 1 for _n, chunks, _a in sent), "出现分片数要进清单"
        assert all("某爷" not in aliases for _n, _c, aliases in sent), (
            "当时挂在两个组上的别名不许进清单")
        # 反面对照：没有任何别名挂在多组上时，别名照常送（不是一律清空）
        single = group_identify_entries(_SAME_PERSON_TWO_NAMES)
        assert any("宝二爷" in a for _n, _c, a in alias_prompt_rows(single))


# 片1 贾宝玉{宝玉}、片2 宝玉{宝二爷}、片3 甄宝玉{宝玉}；模型判（贾宝玉, 宝玉）同一人。
# 「宝玉」是合并组（成员 贾宝玉、宝玉）的成员主名，同时又被甄宝玉组当别名。
_MEMBER_NAME_BEATS_ALIAS = [
    [{"name": "贾宝玉", "aliases": ["宝玉"]}],
    [{"name": "宝玉", "aliases": ["宝二爷"]}],
    [{"name": "甄宝玉", "aliases": ["宝玉"]}],
]


class TestMemberNameBeatsAlias:
    """成员主名优先：一个称呼若是某组的成员主名，它只属于那一组。

    旧口径只数 aliases（挂在几个组的 `aliases` 里），于是「宝玉」被算成挂在两个组上
    = 泛称 → 从两边一起移除：合并组丢掉最常用的称呼，甄宝玉组也留着不该留的（反之
    亦同）。蒸馏选片按 `match_terms = [name] + aliases` 做子串匹配，丢了「宝玉」就等于
    把此人的片判给别人（B2 挂）。

    变异对象 = ① 所有权只数 aliases、不数主名（合并组丢「宝玉」→ 红）
              ② 成员主名也被移除（`_visible_aliases` 不要成员归属那一支 → 红）
              ③ 清单不过滤（与移除不同口径 → 红）
    """

    def test_member_main_name_is_owned_by_its_group_only(self):
        groups = _merged(_MEMBER_NAME_BEATS_ALIAS, pairs=[("贾宝玉", "宝玉")])

        assert len(groups) == 2, "只并了模型点名的那一对"
        merged = next(g for g in groups if "宝玉" in g["members"])
        assert set(merged["members"]) == {"贾宝玉", "宝玉"}
        assert "宝玉" in merged["aliases"], "成员主名要留在自己组（哪怕不是显示名）"
        assert "宝二爷" in merged["aliases"], "唯一别名照常留着（移除 ≠ 清空）"
        other = next(g for g in groups if g is not merged)
        assert other["aliases"] == (), "别组把它当别名，不足以让它降格成泛称"
        assert [g["name"] for g in groups if "宝玉" == g["name"] or "宝玉" in g["aliases"]] \
            == ["贾宝玉"], "在 name ∪ aliases 里「宝玉」恰命中一组"

    def test_alias_prompt_rows_use_the_same_ownership(self):
        """清单过滤与最终移除同口径 —— 不写两份。

        这一条在修复前即为绿（修复不改这三个组的清单内容），它的分辨力在变异③：
        清单不过滤时，贾宝玉组与甄宝玉组会把「宝玉」送给模型，诱导它误判。
        """
        rows = alias_prompt_rows(group_identify_entries(_MEMBER_NAME_BEATS_ALIAS))
        sent = [(name, chunks, aliases) for name, chunks, aliases in rows]

        assert [name for name, _c, _a in sent] == ["贾宝玉", "宝玉", "甄宝玉"]
        assert all("宝玉" not in aliases for _n, _c, aliases in sent), (
            "成员主名不进别组清单（本组它是主名，也不在别名里）")
        assert any("宝二爷" in aliases for _n, _c, aliases in sent), (
            "不属于任何组主名的独有别名照常送（过滤 ≠ 清空）")


# 片1 宝玉 列了单字称呼「他」「玉」与唯一别名「宝二爷」。单字在下游按子串匹配
# （`match_terms = [name] + aliases`）几乎命中每一片 —— 「他」实测覆盖 212/213 片。
_SINGLE_CHAR_ALIASES = [
    [{"name": "宝玉", "aliases": ["他", "玉", "宝二爷"]}],
]


class TestAliasLength:
    """别名至少两个字 —— 单字称呼一律丢弃，判据只写一处。

    变异对象 = ① 去掉长度判据（`aliases` 里留着「他」「玉」→ 红）
              ② 只在一条路径上判（纯函数这条绿，`tests/test_identify_whole_book.py`
                 `TestAliasLength` 的单分片那条红）
    """

    def test_single_char_aliases_are_dropped(self):
        groups = group_identify_entries(_SINGLE_CHAR_ALIASES)

        assert groups[0]["aliases"] == ("宝二爷",), "单字称呼按子串匹配几乎命中全书"


class TestSpeakChunkThreshold:
    """主次只看「本人说话的不同分片数 ≥ 6」——卡片里的说话风格与对话示例取自原文
    对话，铁律 1（一个特质至少在 2 个场景出现）× 核心性格至少 3 个 ⇒ 至少 6 片。

    门槛写死在用例里（不引用 `MIN_SPEAK_CHUNKS`）：拿被测常量当期望值，改常量时
    用例会跟着改，边界就锁不住了。

    变异对象 = ① 门槛写成 `>= 5`（5 片那条红）
              ② 条件反过来（6 片判次要 → 红）
    """

    def test_five_speaking_chunks_is_minor_and_six_is_major(self):
        five = _chunks({i: [("甲", True)] for i in range(5)}, 8)
        six = _chunks({i: [("甲", True)] for i in range(6)}, 8)

        assert _row(five)["importance"] == "次要"
        assert _row(six)["importance"] == "主要"

    def test_appearing_in_30_chunks_but_speaking_in_2_is_minor(self):
        """「和尚」形态：出场多不等于能蒸馏 —— 出现分片数不参与判主次。

        变异对象 = 把出现分片数也算进判据（`chunk_count` 30 ≥ 6 → 判主要 → 红）。
        """
        per = _chunks({i: [("和尚", i < 2)] for i in range(30)}, 30)

        assert group_identify_entries(per)[0]["chunk_count"] == 30
        row = _row(per)
        assert row["speak_chunks"] == 2
        assert row["importance"] == "次要", "出场 30 片、只亲口说话 2 片"

    def test_spoke_only_counts_json_true(self):
        """只认 JSON `true`：字符串 `"true"` / 数字 / 缺省都不计入说话分片。

        变异对象 = 判据改成真值判断（`"false"` 非空串为真 → 6 片 → 判主要 → 红）。
        """
        for marker in ("false", "true", 1, None):
            item = {"name": "甲", "aliases": [], "reason": "r"}
            if marker is not None:
                item["spoke"] = marker
            row = _row([[dict(item)] for _ in range(6)])

            assert row["speak_chunks"] == 0, marker
            assert row["importance"] == "次要", marker


class TestImpersonalGroups:
    """泛称组（`impersonal`）一律次要，但**仍在名单里** —— RAG 打标签要用全名单。

    变异对象 = ① 泛称组直接删掉（名单只剩 1 组 → 红）
              ② `impersonal` 不参与判主次（40 片说话 → 判主要 → 红）
    """

    def test_generic_group_with_40_speaking_chunks_stays_minor_and_listed(self):
        per = _chunks({i: [("婆子", True)] for i in range(40)}, 40)
        roster = finalize_identify_roster(_merged(per, impersonal=["婆子"]))

        assert [r["name"] for r in roster] == ["婆子"], "泛称组保留，只是标次要"
        assert roster[0]["speak_chunks"] == 40
        assert roster[0]["importance"] == "次要"


class TestImpersonalPropagation:
    """`impersonal` 按并组前的组名标注，并完之后「所并各组全为泛称」才算。

    变异对象 = ① 并组后用显示名重新查泛称集合（泛称并入真人组后显示名可能变 → 红）
              ② 并组时丢掉 `impersonal`（`_assemble_group` 不传 → 红）
    """

    def test_generic_merged_into_a_real_person_is_not_impersonal(self):
        per = _chunks({i: [("甲", True), ("婆子", False)] for i in range(7)}, 7)
        row = _row(per, pairs=[("甲", "婆子")], impersonal=["婆子"])

        assert row["importance"] == "主要", "一个真人物并进泛称组，那组就是具体的人"

    def test_two_generic_groups_merged_stay_impersonal(self):
        per = _chunks(
            {**{i: [("婆子", True)] for i in range(7)},
             **{i: [("小丫头", True)] for i in range(7, 14)}}, 14)
        row = _row(per, pairs=[("婆子", "小丫头")], impersonal=["婆子", "小丫头"])

        assert row["speak_chunks"] == 14
        assert row["importance"] == "次要", "两个泛称组互并仍是泛称"


class TestSpeakCountAcrossMergedGroups:
    """并组后按**最终分组**计说话分片 —— 两组各自的片加起来，重叠的片只计一次。"""

    def test_disjoint_speaking_chunks_add_up(self):
        per = _chunks({**{i: [("甲", True)] for i in range(4)},
                       **{i: [("乙", True)] for i in range(4, 8)}}, 8)
        row = _row(per, pairs=[("甲", "乙")])

        assert row["speak_chunks"] == 8, "4 + 4，两片不重叠"
        assert row["importance"] == "主要", "各自 4 片不够，并完 8 片才够"

    def test_overlapping_chunks_are_counted_once(self):
        per = [[{"name": "甲", "aliases": [], "spoke": True, "reason": "a"},
                {"name": "乙", "aliases": [], "spoke": True, "reason": "b"}]
               for _ in range(4)]
        row = _row(per, pairs=[("甲", "乙")])

        assert row["speak_chunks"] == 4, "同一片里两人都说话仍只算一片"
        assert row["importance"] == "次要"


class TestRosterShape:
    """对外的名单行形状与顺序：恰五个键、按说话分片数降序、理由取自说话的分片。"""

    def test_each_row_has_exactly_five_keys(self):
        roster = finalize_identify_roster(_merged(_chunks({0: [("甲", True)]}, 1)))

        assert set(roster[0]) == {"name", "aliases", "importance", "reason", "speak_chunks"}

    def test_rows_are_ordered_by_speaking_chunks_desc(self):
        per = _chunks({i: [("乙", True)] for i in range(3)}, 12)
        for i in range(9):
            per[i].append({"name": "甲", "aliases": [], "spoke": True, "reason": "a"})
        roster = finalize_identify_roster(_merged(per))

        assert [(r["name"], r["speak_chunks"]) for r in roster] == [("甲", 9), ("乙", 3)]

    def test_reason_comes_from_a_speaking_chunk(self):
        """没说话那片的理由排在前面也不取 —— 理由要能代表此人的说话风格。

        变异对象 = 去掉 `o.spoke` 那一支（取到「没说话」→ 红）。
        """
        per = [[{"name": "甲", "aliases": [], "spoke": False, "reason": "没说话"},
                {"name": "甲", "aliases": [], "spoke": True, "reason": "说话了"}]]
        roster = finalize_identify_roster(_merged(per))

        assert roster[0]["reason"] == "说话了"

    def test_reason_falls_back_to_the_first_non_empty_reason(self):
        """一句都没说的人（泛称组）仍要有理由可看。"""
        per = [[{"name": "婆子", "aliases": [], "spoke": False, "reason": ""},
                {"name": "婆子", "aliases": [], "spoke": False, "reason": "传话"}]]
        roster = finalize_identify_roster(_merged(per))

        assert roster[0]["reason"] == "传话"


class TestJudgeVoteTally:
    """5 份判定样本的计票：组对与泛称各需 ≥ 3 票，条内拆两两组对，条间不重复计。"""

    def test_pair_needs_three_of_five(self):
        yes = [{"merge": [["甲", "乙"]], "impersonal": []} for _ in range(3)]
        no = [{"merge": [], "impersonal": []} for _ in range(2)]

        assert tally_judge_samples(yes + no, 3)[0] == [("乙", "甲")]
        assert tally_judge_samples(no + yes[:2], 3)[0] == [], "只有 2 票不生效"

    def test_three_name_merge_splits_into_all_pairs(self):
        samples = [{"merge": [["甲", "乙", "丙"]], "impersonal": []} for _ in range(3)]

        # 组对按名字排序后返回，顺序由码位定（丙 U+4E19 < 乙 U+4E59 < 甲 U+7532）
        assert tally_judge_samples(samples, 3)[0] == [
            ("丙", "乙"), ("丙", "甲"), ("乙", "甲")]

    def test_a_pair_is_counted_once_per_sample(self):
        """同一份里重复列（或 3 名条目与 2 名条目并存）只算一票。

        变异对象 = 不去重（甲–乙 在同一样本里数两次 → 越过门槛 → 红）。
        """
        samples = [{"merge": [["甲", "乙"], ["甲", "乙", "丙"]], "impersonal": []}]

        assert tally_judge_samples(samples, 2)[0] == [], "一份样本里同一组对只计一票"

    def test_impersonal_needs_three_of_five(self):
        yes = [{"merge": [], "impersonal": ["婆子"]} for _ in range(3)]
        no = [{"merge": [], "impersonal": []} for _ in range(2)]

        assert tally_judge_samples(yes + no, 3)[1] == {"婆子"}
        assert tally_judge_samples(no + yes[:2], 3)[1] == set()
