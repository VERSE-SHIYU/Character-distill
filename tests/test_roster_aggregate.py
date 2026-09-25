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
  I4   主次、排序、理由、不截断 —— 四个输入全是代码可数的。

判定「歧义」的时机是本文件的中心：**并组做完、按最终分组计数**。放在并组之前，
片1 贾宝玉{宝玉,宝二爷} 里的「宝玉」会被当成挂在两个主名下的泛称而丢掉。
"""

from __future__ import annotations

from core.roster_aggregate import (
    alias_prompt_rows,
    finalize_identify_roster,
    group_identify_entries,
    merge_identify_groups,
)


def _all_members(groups) -> set[frozenset]:
    return {frozenset(g["members"]) for g in groups}


def _merged(per_chunk, pairs=()):
    """建组 → 按组对并组 → 出最终组（移除歧义别名在这一步做）。"""
    return merge_identify_groups(group_identify_entries(per_chunk), list(pairs))


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


# 40 片，10% = 4 片。X / Y / Z 各按一种边界摆：
#   X 3 片、其中 2 片是主要（逐片那层写的「主角」「主要角色」）→ 靠 main_count 过线
#   Y 3 片、只有 1 片主要，且那一片里同一人出现两次 → 条目数 4 但分片数 3，不该过线
#   Z 恰 4 片、全「配角」→ 恰好在 10% 那条线上（`>=` 与 `>` 在这里分开）
# 其余 57 个一次性人物把总数顶到 60，用来锁「不截断」。
_LONG_BOOK_PER_CHUNK: list[list[dict]] = [[] for _ in range(40)]
_LONG_BOOK_PER_CHUNK[0] = [{"name": "X", "aliases": [], "importance": "配角", "reason": "起(x)"}]
_LONG_BOOK_PER_CHUNK[1] = [{"name": "X", "aliases": [], "importance": "主角", "reason": "主(x)"}]
_LONG_BOOK_PER_CHUNK[2] = [{"name": "X", "aliases": [], "importance": "主要角色", "reason": "再主(x)"}]
_LONG_BOOK_PER_CHUNK[3] = [{"name": "Y", "aliases": [], "importance": "主角", "reason": "主(y)"}]
_LONG_BOOK_PER_CHUNK[4] = [{"name": "Y", "aliases": [], "importance": "配角", "reason": "配(y1)"},
                           {"name": "Y", "aliases": [], "importance": "配角", "reason": "配(y2)"}]
_LONG_BOOK_PER_CHUNK[5] = [{"name": "Y", "aliases": [], "importance": "配角", "reason": "配(y3)"}]
for _i in range(6, 10):
    _LONG_BOOK_PER_CHUNK[_i] = [
        {"name": "Z", "aliases": [], "importance": "配角", "reason": f"配(z{_i})"}]
for _n in range(57):
    _LONG_BOOK_PER_CHUNK[10 + _n % 30].append(
        {"name": f"路人{_n:02d}", "aliases": [], "importance": "配角", "reason": "一闪"})


class TestImportanceAndOrdering:
    """主次、排序、理由、不截断 —— 四件都是纯代码可判的事，原先整包交给模型。

    逐片那层的 `importance` 只看得到本片，全书尺度必须在这里重判；判据的三个输入
    全是可数的（被判主要的分片数、出现的不同分片数、全书分片数）。

    变异对象 = ① 只按 chunk_count 判（X 3 片 < 4 → 次要）
              ② `>=` 改 `>`（Z 恰 4 片 → 次要）
              ③ 规范化改 `== "主要"`（「主角」「主要角色」都不算 → X 次要）
              ④ 按**条目**数而非分片数计（Y 同片两条 → 4 片 → 主要）
              ⑤ 去掉主次分层（Z 排到 Y 后面）
              ⑥ 理由取第一条（X 拿到「起(x)」而不是主要片那条）
              ⑦ 加 `[:50]`（60 组被截到 50）
    """

    def setup_method(self):
        self.groups = group_identify_entries(_LONG_BOOK_PER_CHUNK)
        self.roster = finalize_identify_roster(self.groups, total_chunks=40)
        self.rows = {r["name"]: r for r in self.roster}

    def test_nobody_is_truncated(self):
        assert len(self.roster) == 60

    def test_chunk_count_counts_distinct_chunks(self):
        by_name = {g["name"]: g for g in self.groups}
        assert by_name["X"]["chunk_count"] == 3
        assert by_name["Y"]["chunk_count"] == 3, "同一片里出现两次仍算一片"
        assert by_name["Z"]["chunk_count"] == 4

    def test_importance_is_judged_by_the_whole_book(self):
        assert self.rows["X"]["importance"] == "主要", "2 片被判为主要"
        assert self.rows["Y"]["importance"] == "次要", "1 片主要、3 片出现，两条线都不够"
        assert self.rows["Z"]["importance"] == "主要", "恰 4 片 = 全书的 10%"

    def test_main_characters_first_then_by_weight(self):
        assert [r["name"] for r in self.roster[:3]] == ["X", "Z", "Y"]

    def test_reason_comes_from_a_main_chunk(self):
        assert self.rows["X"]["reason"] == "主(x)"
        assert self.rows["Y"]["reason"] == "主(y)"
