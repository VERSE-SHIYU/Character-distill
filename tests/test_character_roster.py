# -*- coding: utf-8 -*-
"""名单唯一入口：命中即不识别、未命中识别一次并写回、refresh 强制重算。

本文件锁三件事：
  1. 命中缓存**不发 LLM**（变异对象 = 拿掉命中短路：`identify` 调用数变 1）
  2. 未命中只识别一次，且**按当前算法版本写回**（拿掉写回 → 第二次仍然是冷启动）
  3. `refresh=True` 越过缓存强制重算并覆盖（拿掉 refresh 分支 → 调用数变 0）

外加 `aliases_for`：三处调用点原先各写一遍「遍历找 name 再取 aliases」的循环，
行为必须一致 —— 找不到人、找到但没别名，都返回空列表。

夹具用假件而非真 store/distiller：本模块的契约是**调用序列**（读了几次、识别了几次、
写了什么版本），真件只会让序列更难读。
"""

from __future__ import annotations

import pytest

from core.character_roster import aliases_for, cached_characters, resolve_characters
from core.distiller import Distiller

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

    async def test_refresh_ignores_cache_and_overwrites(self):
        """refresh：缓存里是旧名单也照样重算，并把新名单写回覆盖。"""
        storage = _FakeStorage(cached=BAOYU)
        distiller = _FakeDistiller(DAIYU)

        got = await resolve_characters(
            storage, distiller, "t1", "u1", "全文……", refresh=True)

        assert got == DAIYU, "refresh 必须吐新识别的结果，不是缓存里的旧名单"
        assert distiller.calls == 1
        assert storage.saved == [("t1", DAIYU, Distiller.IDENTIFY_VERSION)]

    async def test_cached_characters_passes_the_single_version_definition(self):
        """`cached_characters` 读的版本号来自唯一定义，不是某处的字面量。"""
        storage = _FakeStorage(cached=BAOYU)

        assert await cached_characters(storage, "t1", "u1") == BAOYU
        assert storage.reads == [("t1", "u1", Distiller.IDENTIFY_VERSION)]


class TestAliasesFor:
    def test_returns_aliases(self):
        assert aliases_for(BAOYU, "宝玉") == ["怡红公子"]

    def test_name_without_aliases_returns_empty(self):
        assert aliases_for(DAIYU, "黛玉") == []

    def test_unknown_name_returns_empty(self):
        assert aliases_for(BAOYU, "宝钗") == []
