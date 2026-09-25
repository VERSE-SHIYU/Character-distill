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
