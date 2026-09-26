# -*- coding: utf-8 -*-
"""A1（第 8 节 / WP14）：Map 并发自适应闸 `core.concurrency.AdaptiveGate`。

闸按**在途请求数**控制（与厂商文档「并发」的定义一致：一个请求从发出到响应完成
记为一个并发）：每次尝试前 acquire、结束后 release；收到 429 → 上限 ×0.75 向下
取整、最低 1；连续成功次数达到当前上限 → 上限 +1、不超过 `cap`。

这里只测闸本身。接入点（`async_chat` 的每次 `create()`）与账户记忆见 A2、A3。
"""
from __future__ import annotations

import asyncio

from core.concurrency import AdaptiveGate


async def _spin(n: int = 5) -> None:
    """把控制权让给事件循环若干轮，让挂起的 acquire 跑到它的下一个挂起点。"""
    for _ in range(n):
        await asyncio.sleep(0)


class TestRateLimitedShrinksTheCeiling:
    async def test_429_multiplies_the_ceiling_by_three_quarters_flooring(self):
        g = AdaptiveGate(cap=100, initial=8)

        await g.on_rate_limited()
        assert g.ceiling == 6, "8 × 0.75"

        await g.on_rate_limited()
        assert g.ceiling == 4, "6 × 0.75"

        await g.on_rate_limited()
        assert g.ceiling == 3, "4 × 0.75"

    async def test_the_ceiling_never_drops_below_one(self):
        g = AdaptiveGate(cap=100, initial=3)

        for _ in range(6):
            await g.on_rate_limited()

        assert g.ceiling == 1, "3 → 2 → 1 → 1 …，不降到 0"
        async with g:  # 降到 0 会让这里永远取不到名额（死锁）
            assert g.inflight == 1


class TestConsecutiveSuccessesRaiseTheCeilingByOne:
    async def test_additive_increase_stops_at_the_cap(self):
        g = AdaptiveGate(cap=4, initial=1)

        await g.on_success()
        assert g.ceiling == 2, "1 次连续成功达上限 1 → +1"

        await g.on_success()
        await g.on_success()
        assert g.ceiling == 3, "2 次连续成功达上限 2 → +1"

        for _ in range(3):
            await g.on_success()
        assert g.ceiling == 4, "3 次连续成功达上限 3 → +1"

        for _ in range(10):
            await g.on_success()
        assert g.ceiling == 4, "已达 cap，再多的成功也不再涨"

    async def test_a_429_resets_the_success_streak(self):
        g = AdaptiveGate(cap=100, initial=10)

        for _ in range(9):
            await g.on_success()
        assert g.ceiling == 10, "9 < 10，还没到 +1 的门槛"

        await g.on_rate_limited()
        assert g.ceiling == 7, "10 × 0.75"

        for _ in range(6):
            await g.on_success()
        assert g.ceiling == 7, "重启计数：6 < 7，不该按 429 前那 9 次累计"


class TestAcquireWaitsWhileTheCeilingSitsBelowInflight:
    async def test_new_acquire_blocks_until_inflight_drops_below_the_ceiling(self):
        g = AdaptiveGate(cap=5, initial=5)
        for _ in range(5):
            await g.__aenter__()
        assert g.inflight == 5

        await g.on_rate_limited()  # 5 × 0.75 → 3，而在途仍是 5
        assert (g.ceiling, g.inflight) == (3, 5)

        pending = asyncio.create_task(g.__aenter__())
        await _spin()
        assert not pending.done(), "在途 5 未降到上限 3 以下"

        await g.__aexit__(None, None, None)  # 在途 4
        await _spin()
        assert not pending.done(), "在途 4 仍不小于上限 3"

        await g.__aexit__(None, None, None)  # 在途 3
        await _spin()
        assert not pending.done(), "在途 3 与上限相等，仍要等"

        await g.__aexit__(None, None, None)  # 在途 2 < 3 → 放行
        await _spin()
        assert pending.done()
        assert g.inflight == 3
