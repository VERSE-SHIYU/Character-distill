# -*- coding: utf-8 -*-
"""名单唯一入口：命中即不识别、未命中识别一次并按当前算法版本写回。

本文件锁三件事：
  1. 命中缓存**不发 LLM**（变异对象 = 拿掉命中短路：`identify` 调用数变 1）
  2. 未命中只识别一次，且**按当前算法版本写回**（拿掉写回 → 第二次仍然是冷启动）
  3. 版本号进 memo 的键 —— 版本一改必须当未命中（从键里去掉版本号 → 不再调 LLM、
     写回的仍是旧口径名单）

外加 `aliases_for`：三处调用点原先各写一遍「遍历找 name 再取 aliases」的循环，
行为必须一致 —— 找不到人、找到但没别名，都返回空列表。

夹具用假件而非真 store/distiller：本模块的契约是**调用序列**（读了几次、识别了几次、
写了什么版本），真件只会让序列更难读。唯一例外是第 3 条 —— 它锁的正是**真 `Distiller`
内部那份 memo 的键**，换成假 distiller 就把被测的那一段换掉了。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core.character_roster import (
    aliases_for,
    cached_characters,
    finalize_identify_roster,
    group_identify_entries,
    merge_identify_groups,
    resolve_characters,
)
from core.distiller import Distiller, _IDENTIFY_CACHE

BAOYU = [{"name": "宝玉", "aliases": ["怡红公子"], "importance": "主要"}]
DAIYU = [{"name": "黛玉", "aliases": [], "importance": "主要"}]


class _FakeStorage:
    def __init__(self, cached=None):
        self._cached = cached
        self.saved: list[tuple[str, list, int]] = []
        self.reads: list[tuple[str, str, int]] = []

    async def get_characters_owned(self, text_id, user_id, *, version):
        self.reads.append((text_id, user_id, version))
        return self._cached

    async def save_characters(self, text_id, characters, *, version):
        self.saved.append((text_id, characters, version))
        self._cached = characters      # 写回后缓存随之生效


class _FakeDistiller:
    def __init__(self, chars):
        self._chars = chars
        self.calls = 0

    def identify_characters(self, text):
        self.calls += 1
        return self._chars


def _identify_llm(*rosters) -> MagicMock:
    """真 `Distiller` 用的假 LLM：每次 `chat` 依次吐一份名单。"""
    llm = MagicMock()
    llm.model = "test-model-version-key"
    llm.chat = MagicMock(side_effect=[json.dumps(r) for r in rosters])
    return llm


class TestResolveCharacters:
    async def test_cache_hit_does_not_identify(self):
        """命中缓存：零次识别、零次写回，直接返回缓存内容。"""
        storage = _FakeStorage(cached=BAOYU)
        distiller = _FakeDistiller(DAIYU)

        got = await resolve_characters(
            storage, distiller, "t1", "u1", "全文……")

        assert got == BAOYU
        assert distiller.calls == 0
        assert storage.saved == []

    async def test_miss_identifies_once_and_persists(self):
        """未命中：识别一次、按当前算法版本写回、返回识别结果。"""
        storage = _FakeStorage(cached=None)
        distiller = _FakeDistiller(BAOYU)

        got = await resolve_characters(storage, distiller, "t1", "u1", "全文……")

        assert got == BAOYU
        assert distiller.calls == 1
        assert storage.saved == [("t1", BAOYU, Distiller.IDENTIFY_VERSION)]

    async def test_cached_characters_passes_the_single_version_definition(self):
        """`cached_characters` 读的版本号来自唯一定义，不是某处的字面量。"""
        storage = _FakeStorage(cached=BAOYU)

        assert await cached_characters(storage, "t1", "u1") == BAOYU
        assert storage.reads == [("t1", "u1", Distiller.IDENTIFY_VERSION)]


class TestMemoKeyCoversIdentifyVersion:
    """识别口径版本是决定名单的输入之一 → 必须进 memo 的键。

    生产**当前不可达**（台账 89 的 S0 复核）：`IDENTIFY_VERSION` 是类常量，改它必改
    代码、必重启进程，重启则 memo 为空。本类锁的是**缓存键覆盖全部决定输入**这一形态：
    键漏了版本号，一旦出现「不改代码就能改版本号」的入口（读 env / config），旧口径的
    名单会被 memo 直接返回、再以**新版本号**落库 —— 旧名单被洗成新名单，S3 的版本门
    形同虚设。

    变异对象 = 把版本号从 memo 的键里去掉（`core/distiller.py::identify_characters`
    的 `key = ...`）→ 第二次取名单时命中 memo，LLM 不再被调、落库的仍是旧名单 → 红。
    """

    @pytest.fixture(autouse=True)
    def _clean_memo(self):
        _IDENTIFY_CACHE.clear()
        yield
        _IDENTIFY_CACHE.clear()

    async def test_version_change_is_a_memo_miss(self, monkeypatch):
        monkeypatch.setattr(Distiller, "IDENTIFY_VERSION", 2)
        llm = _identify_llm(BAOYU, DAIYU)
        distiller = Distiller(llm)

        # 版本 2：冷库 → 识别一次，memo 按「文本:模型:版本」记下 BAOYU
        await resolve_characters(_FakeStorage(), distiller, "t1", "u1", "全文……")
        assert llm.chat.call_count == 1

        # 版本一改，真库按版本号把旧行当无缓存 —— 这里用第二个冷库等价表达
        monkeypatch.setattr(Distiller, "IDENTIFY_VERSION", 3)
        storage = _FakeStorage()
        got = await resolve_characters(storage, distiller, "t1", "u1", "全文……")

        assert llm.chat.call_count == 2, "版本号变了，memo 必须当未命中"
        assert got == DAIYU, "拿到的必须是新识别的名单，不是 memo 里的旧名单"
        assert storage.saved == [("t1", DAIYU, 3)], "落库的版本号与名单都必须是新的"


class TestAliasesFor:
    def test_returns_aliases(self):
        assert aliases_for(BAOYU, "宝玉") == ["怡红公子"]

    def test_name_without_aliases_returns_empty(self):
        assert aliases_for(DAIYU, "黛玉") == []

    def test_unknown_name_returns_empty(self):
        assert aliases_for(BAOYU, "宝钗") == []


# 片序即分片序号：片1 甲{二爷}、片2 乙{二爷}（「二爷」两处都挂 = 有歧义），
# 片3 丙{三爷}、片4 丁{三爷}（「三爷」同形）。甲另带一个唯一别名 —— 用来验
# 「移除歧义别名」不是「清空 aliases」。
_AMBIGUOUS_PER_CHUNK = [
    [{"name": "甲", "aliases": ["二爷", "甲别名"]}],
    [{"name": "乙", "aliases": ["二爷"]}],
    [{"name": "丙", "aliases": ["三爷"]}],
    [{"name": "丁", "aliases": ["三爷"]}],
]


def _all_members(groups) -> set[frozenset]:
    return {frozenset(g["members"]) for g in groups}


class TestAmbiguousAliases:
    """歧义别名：不据此并组，**且从所有组移除**（不只是不并组）。

    泛称一旦进了 aliases 就会污染下游的子串匹配（蒸馏选片的 `match_terms`、RAG
    打标签的 `_tag_characters`）——「二爷」同时指两个人时，留着它会把别人的场景
    标给此人。判据在代码里（模型只判「哪些组是同一个人」，管不了哪些称呼唯一）。

    变异对象 = ① 共享任一别名即并组（甲乙被并成一组 → 红）
              ② 删掉「从所有组移除」那一步（甲、乙的 aliases 里留着「二爷」→ 红）
    """

    def test_shared_alias_does_not_merge_and_is_stripped_everywhere(self):
        groups = group_identify_entries(_AMBIGUOUS_PER_CHUNK)

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
        groups = group_identify_entries(_AMBIGUOUS_PER_CHUNK)

        merged = merge_identify_groups(groups, [("甲", "丙")])

        assert _all_members(merged) == {
            frozenset({"甲", "丙"}), frozenset({"乙"}), frozenset({"丁"}),
        }
        group = next(g for g in merged if "丙" in g["members"])
        assert "三爷" not in group["aliases"], "合并组同样不许留下歧义别名"
        assert "甲别名" in group["aliases"]
        assert group["name"] in {"甲", "丙"}


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
