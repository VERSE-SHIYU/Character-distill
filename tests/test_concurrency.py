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


async def _burst(g: AdaptiveGate, limit: int) -> list[int]:
    """一波按当前上限放出的尝试：前 limit 个成功，其余 429。返回每个的结果码。

    上游模型：一波 n 个尝试同时进闸，前 `limit` 个在限内（200），其余（在途 > limit）
    回 429 —— 与 A2 的假上游同一规则，只是这里不需要真 socket。A5、A6 共用。
    """
    n = g.ceiling
    statuses: list[int | None] = [None] * n

    async def attempt(i: int) -> None:
        async with g:
            # 代数在**进闸时**记下，不是报 429 时现读 —— `async_chat` 就是
            # `gen = gate.generation` 取在 create 之前（见 adapters/llm_adapter.py
            # 的 `gen = gate.generation`）。现读的话每次拿到的都是最新代数，判据
            # 恒等成立，一波 15 个 429 照样连乘到 1 —— 仪器没复现被测形态。
            gen = g.generation
            # 让整波都先进闸再判——不然 gather 逐个跑，在途数永远只有 1
            await asyncio.sleep(0)
            if i < limit:
                statuses[i] = 200
                await g.on_success()
            else:
                statuses[i] = 429
                await g.on_rate_limited(gen)

    await asyncio.gather(*(attempt(i) for i in range(n)))
    return statuses


class TestRateLimitedShrinksTheCeiling:
    async def test_429_multiplies_the_ceiling_by_three_quarters_flooring(self):
        g = AdaptiveGate(cap=100, initial=8)

        await g.on_rate_limited(g.generation)
        assert g.ceiling == 6, "8 × 0.75"

        await g.on_rate_limited(g.generation)
        assert g.ceiling == 4, "6 × 0.75"

        await g.on_rate_limited(g.generation)
        assert g.ceiling == 3, "4 × 0.75"

    async def test_the_ceiling_never_drops_below_one(self):
        g = AdaptiveGate(cap=100, initial=3)

        for _ in range(6):
            await g.on_rate_limited(g.generation)

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

        await g.on_rate_limited(g.generation)
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

        await g.on_rate_limited(g.generation)  # 5 × 0.75 → 3，而在途仍是 5
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


class TestABurstOfRateLimitsDecreasesTheCeilingOnlyOnce:
    """WP14 A5：一波并发同时撞 429，只下调一次 —— 不按次数连乘。

    缺陷形态：60 路同时撞 429，每片各报一次 → 20 × 0.75^15 → 1，闸当场自锁（在途 1，
    并发退回串行，整本蒸馏退化成逐片跑）。依据是 NVIDIA DataDesigner 的做法：一波
    429 里只有**第一个**该下调 —— 在途那批是按**旧**上限发出去的，它们的 429 是同一个
    事实的 N 次重复，不是 N 个新事实。闸用「代数」区分：进闸时记下当时代数，报 429 时
    代数已经变了，说明同波已经有人下调过，本次不再连乘。

    上游模型见模块级 `_burst`（与 A6 共用）。

    变异：`on_rate_limited` 去掉代数判据（每次 429 都下调）→ `ceilings[0]` 塌到 1。
    """

    LIMIT = 5
    CAP = 20
    WAVES = 8

    async def test_first_burst_leaves_the_ceiling_at_three_quarters(self):
        g = AdaptiveGate(cap=self.CAP, initial=self.CAP)
        ceilings: list[int] = []
        seen_429 = 0
        for _ in range(self.WAVES):
            statuses = await _burst(g, self.LIMIT)
            seen_429 += statuses.count(429)
            ceilings.append(g.ceiling)

        assert seen_429 > 0, "一波都没回 429 —— 闸压根没被打到，这条锁空转"
        first = ceilings[0]
        assert first == 15, (
            f"首波 20 个并发里 15 个 429，上限应只乘一次 0.75 落到 15（实际 {first}）"
            f"—— 15 次连乘会落到 1，闸自锁。全程：{ceilings}")
        assert min(ceilings) >= 3, f"上限塌到 {min(ceilings)} —— 一波里的 429 被连乘了：{ceilings}"
        # 收敛要 5 波：一波只下调一次，20→15→11→8→6→4。只看尾部（末 3 波）—— 数完
        # 首波那一次下调之后，还得容许它按 0.75 逐波走完，那些波的上限当然高于上限值。
        assert max(ceilings[-3:]) <= self.LIMIT + 1, (
            f"数波之后上限没收敛到上游限附近：{ceilings}（+1 是 AIMD 的探针）")


class TestSuccessesOnlyCountWhileTheGateIsFull:
    """WP14 A6（二审后第二次补充）：未用满窗口的成功不上探 —— RFC 7661。

    规范原文：未用满拥塞窗口的发送方「MUST NOT」增大窗口。这里窗口就是闸的上限：
    尾部分片在途数已低于上限，它们的成功**不代表还有余量**，只是收尾。若照旧计数，
    `_ok` 攒够 `ceiling` 就 +1，逐格把写回的账户记忆抬高（实测偶发到 9，真上限 5）。

    实现：进闸时记下「此刻是否满载」（在途 + 1 ≥ 上限，本请求占的是最后的名额；或
    已经在排队，说明需求超过上限），`on_success` 只在满载时计数。

    上游模型与 A5 共用 `_burst`：满载跑到上限收敛在上游真上限附近（此刻远低于 cap），
    再撤到 2 路在途 —— 一有抬升就看得见。

    变异：`on_success` 去掉满载判据（不看 `_admitted_full` 一律计数，即修复前的行为）
    → 排水尾 50 次成功把上限从收敛值抬到 cap。
    """

    LIMIT = 6
    CAP = 10
    WAVES = 8

    async def test_a_drain_tail_does_not_probe_upward(self):
        g = AdaptiveGate(cap=self.CAP, initial=self.LIMIT)
        ceilings: list[int] = []
        seen_429 = 0
        for _ in range(self.WAVES):
            statuses = await _burst(g, self.LIMIT)
            seen_429 += statuses.count(429)
            ceilings.append(g.ceiling)

        assert seen_429 > 0, "一波都没回 429 —— 闸压根没被打到，这条锁空转"
        settled = g.ceiling
        assert settled < self.CAP, (
            f"满载跑到上限已顶到 cap={self.CAP}，排水尾就没区分度了：{ceilings}")

        # 排水尾：只剩 2 路在途（上限 settled ≥ 5），连续成功。空闸里的成功不是余量。
        async with g, g:
            for _ in range(50):
                await g.on_success()
        assert g.ceiling == settled, (
            f"排水尾 2 路在途（上限 {settled}）连续 50 次成功把上限抬到 {g.ceiling}"
            f"—— 未用满窗口不应上探，写回的账户记忆会假高；全程 {ceilings}")
