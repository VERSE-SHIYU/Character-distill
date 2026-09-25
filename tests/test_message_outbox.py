"""补写队列：顺序、可达性分辨、丢弃粒度。

seam 是 `MessageOutbox` 的五个方法（`write` / `flush` / `discard` / `clear` / `clear_after`）
—— 队列不碰存储，`write_fn` 与 `ping` 都是传进来的，所以这一层可以完全脱离数据库测。

**「库不可达」在夹具里的形状是「写入与 ping 一起失败」**，不是「ping 说不可达而写入照常
成功」—— 真实现里连不上时写就是会抛（写完才问 ping 是为了分辨「库挂了」与「这一条坏了」，
不是反过来）。这条校准错了的话，下面几条用例会「红得符合预期」却什么也没证明。

每条用例对应一个「实现退回成什么样就会红」的形态，见交付报告的对账表。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.message_outbox import MessageOutbox


class _DB:
    """假存储：`up=False` 时写入与 ping 一起失败。"""

    def __init__(self, up: bool = True) -> None:
        self.up = up
        self.ping_calls = 0
        # 自增行 id 属于**表**不属于某一次写入 —— 补写顺序对不对，看的就是这些 id 的序
        self.rows: list[tuple[str, int]] = []

    async def ping(self) -> None:
        self.ping_calls += 1
        if not self.up:
            raise RuntimeError("db down")


class _Writer:
    """假 `write_fn`：每次调用记一笔，成功则从假表里领一个递增行 id。

    `started` / `gate` 只给并发用例用：进了写入就举手，然后等闸门放行 —— 用来复现
    「慢写入占着锁」那个状态。
    """

    def __init__(
        self,
        db: _DB,
        fail_all: bool = False,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
    ) -> None:
        self.db = db
        self.fail_all = fail_all
        self.gate = gate
        self.started = started
        self.calls: list[str] = []
        self.rows: list[tuple[str, int]] = []

    async def __call__(self, key: str) -> int:
        self.calls.append(key)
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if not self.db.up:
            raise RuntimeError(f"write {key}: db down")
        if self.fail_all:
            raise RuntimeError(f"write {key}: bad row")
        row_id = len(self.db.rows) + 1
        self.db.rows.append((key, row_id))
        self.rows.append((key, row_id))
        return row_id


async def test_later_entry_is_not_written_while_an_earlier_one_is_unpersisted():
    """顺序：前一条还没落库时，后一条连试都不许试。"""
    db = _DB(up=False)
    outbox = MessageOutbox()
    first, second = _Writer(db), _Writer(db)

    state_first, _ = await outbox.write(first, ping=db.ping)
    assert state_first.state == "pending"

    state_second, _ = await outbox.write(second, ping=db.ping)
    assert state_second.state == "pending"
    assert second.calls == [], "前一条还没落库，后一条就不该被写（顺序不变量）"
    assert outbox.has_pending is True


async def test_backfill_after_recovery_keeps_the_original_order():
    """恢复后按原顺序补，行 id 递增（读回来 `ORDER BY id ASC` 才是用户看到的顺序）。"""
    db = _DB(up=False)
    outbox = MessageOutbox()
    writers = [_Writer(db), _Writer(db), _Writer(db)]

    keys = []
    for writer in writers:
        state, _ = await outbox.write(writer, ping=db.ping)
        assert state.state == "pending"
        keys.append(state.key)

    db.up = True
    report = await outbox.flush(ping=db.ping)

    assert [key for key, _ in report.flushed] == keys
    row_ids = [row_id for _, row_id in report.flushed]
    assert row_ids == sorted(row_ids) and len(set(row_ids)) == 3
    assert outbox.has_pending is False


async def test_one_bad_message_is_retried_once_then_dropped_and_the_rest_still_goes():
    """库可达、只是这一条写不进去：重试一次 → 丢掉它 → 继续写后面的。

    「继续」那一半是重点：一条坏消息不该把整队堵死。
    """
    db = _DB(up=True)
    outbox = MessageOutbox()
    bad, good = _Writer(db, fail_all=True), _Writer(db)

    state_bad, report_bad = await outbox.write(bad, ping=db.ping)
    assert state_bad.state == "failed"
    assert len(bad.calls) == 2, "失败后必须**恰好**重试一次"
    assert db.ping_calls == 1, "写失败后要问一次可达性，才分得清「库挂了」与「这条坏了」"
    assert report_bad.dropped == [state_bad.key]
    assert outbox.has_pending is False

    state_good, _ = await outbox.write(good, ping=db.ping)
    assert state_good.state == "saved"
    assert state_good.id == 1
    assert good.rows == [(state_good.key, 1)]


async def test_write_still_reports_its_own_message_when_a_retry_flush_races_it():
    """并发：慢写入占着锁时，`write(第二句)` 与一次「重试」flush 依次排在它后面。

    排在前面的那次 flush 会顺路把第二句也补写掉；于是 `write` 自己的报告里没有自己的
    key，`_state_of` 判成 `pending` —— 消息**已经落库**，前端却会永久停在未保存。

    变异：`write` 入队后就把锁放掉、`flush` 再重新加锁（修复前的写法）→ 本条红。
    此时 `fast.rows` 里**有**那一行（它被别人的 flush 写掉了），状态却是 `pending`。
    """
    db = _DB(up=True)
    outbox = MessageOutbox()
    gate, started = asyncio.Event(), asyncio.Event()
    slow = _Writer(db, gate=gate, started=started)
    fast = _Writer(db)

    t_slow = asyncio.create_task(outbox.write(slow, ping=db.ping))
    await started.wait()  # 慢写入已经进了写入、占着锁
    t_fast = asyncio.create_task(outbox.write(fast, ping=db.ping))
    await asyncio.sleep(0)  # 排队等锁
    t_retry = asyncio.create_task(outbox.flush(ping=db.ping))
    await asyncio.sleep(0)  # 也排队等锁

    gate.set()
    (state_slow, _), (state_fast, _), _ = await asyncio.wait_for(
        asyncio.gather(t_slow, t_fast, t_retry), timeout=5,
    )

    assert state_slow.state == "saved"
    assert fast.rows == [(state_fast.key, 2)], "第二句确实落库了（行序：慢的在前，id=2）"
    assert state_fast.state == "saved", (
        "消息已经落库，`write` 却报 `pending` —— 前端会一直显示未保存"
    )
    assert state_fast.id == 2


async def test_unreachable_db_drops_nothing_however_many_flushes():
    """库不可达：一个字都不许丢 —— 连 flush 三次，队还在，每条都保留。"""
    db = _DB(up=False)
    outbox = MessageOutbox()
    writer = _Writer(db)

    state, _ = await outbox.write(writer, ping=db.ping)
    assert state.state == "pending"

    for _ in range(3):
        report = await outbox.flush(ping=db.ping)
        assert report.dropped == []
        assert report.flushed == []

    assert outbox.has_pending is True
    assert len(writer.calls) == 4, "建队那一次 + 三次 flush 各试一次（确实走到写入，不是空转）"


async def test_flush_uses_the_ping_passed_at_call_time():
    """`ping` 是**每次调用**传进来的，不是构造时绑死的那个存储。

    依据缺陷 117：存储实例在 `web/deps` 里是用到才解析的，构造时绑死一份会与
    `deps._storage` 分叉 —— 队列拿着过期的可达性判断，把本来补得上的消息丢掉。
    探针是「换了个存储之后，它到底问了哪一个」。
    """
    stale_db, fresh_db = _DB(up=False), _DB(up=True)
    outbox = MessageOutbox()
    writer = _Writer(stale_db, fail_all=True)

    state, _ = await outbox.write(writer, ping=stale_db.ping)
    assert state.state == "pending"
    stale_calls = stale_db.ping_calls

    # 同一个队列、同一个对象，换了个存储实例再来一次
    report = await outbox.flush(ping=fresh_db.ping)

    assert fresh_db.ping_calls == 1, "flush 必须问**调用时**传进来的那个存储"
    assert stale_db.ping_calls == stale_calls, "不许再回去问上一次（或构造期）那个"
    assert report.dropped == [state.key], "新存储说可达 → 走「这一条自己坏了」的重试路径"


async def test_discard_removes_exactly_that_entry():
    """`discard`：流式回滚只摘掉那一条，别人的补写照旧。"""
    db = _DB(up=False)
    outbox = MessageOutbox()
    kept, rolled_back = _Writer(db), _Writer(db)

    state_kept, _ = await outbox.write(kept, ping=db.ping)
    state_rolled_back, _ = await outbox.write(rolled_back, ping=db.ping)

    outbox.discard(state_rolled_back.key)

    db.up = True
    report = await outbox.flush(ping=db.ping)
    assert report.flushed == [(state_kept.key, 1)]
    assert rolled_back.rows == []
    assert outbox.has_pending is False


async def test_clear_empties_the_queue():
    """`clear`：`/revoke` 之后补写不许把撤回掉的消息又写回来。"""
    db = _DB(up=False)
    outbox = MessageOutbox()
    first, second = _Writer(db), _Writer(db)

    await outbox.write(first, ping=db.ping)
    await outbox.write(second, ping=db.ping)

    outbox.clear()
    assert outbox.has_pending is False

    db.up = True
    report = await outbox.flush(ping=db.ping)
    assert (report.flushed, report.dropped) == ([], [])
    assert first.rows == [] and second.rows == []


# ── clear_after：撤回 = 删库成功之后才清队（持锁） ───────────────────────────


def _deleter(result: int = 7, *, exc: Exception | None = None):
    """假 `action`：`/revoke` 里那步「删库」。`exc` 非空时抛它。"""

    async def _run() -> int:
        if exc is not None:
            raise exc
        return result

    return _run


async def test_revoke_keeps_the_queue_when_the_delete_fails():
    """删库抛错 → 队里未落库的消息**一条都不许少**。

    这是撤回的失败路径：库删不掉，历史就没删掉，那些消息仍然该被补写。

    变异：`clear_after` 改成先清队再执行 `action` → 本条红（队被清空，消息真丢了）。
    """
    db = _DB(up=False)
    outbox = MessageOutbox()
    writer = _Writer(db)
    state, _ = await outbox.write(writer, ping=db.ping)
    assert state.state == "pending"

    with pytest.raises(RuntimeError, match="delete failed"):
        await outbox.clear_after(_deleter(exc=RuntimeError("delete failed")))

    assert outbox.has_pending is True, "删库失败了，队里那条还在排队，不该被清掉"

    db.up = True
    report = await outbox.flush(ping=db.ping)
    assert report.flushed == [(state.key, 1)], "撤回没成功，这条照样要能补上"


async def test_revoke_clears_the_queue_after_the_delete_succeeds():
    """删库成功 → 队空，且把 `action` 的结果原样交回调用方（`/revoke` 要拿它当 `deleted`）。"""
    db = _DB(up=False)
    outbox = MessageOutbox()
    writer = _Writer(db)
    await outbox.write(writer, ping=db.ping)

    result = await outbox.clear_after(_deleter(3))

    assert result == 3, "`clear_after` 要把 action 的返回值原样交回（`deleted` 就是它）"
    assert outbox.has_pending is False

    db.up = True
    report = await outbox.flush(ping=db.ping)
    assert (report.flushed, report.dropped) == ([], [])


async def test_revoke_holds_the_lock_while_the_delete_is_in_flight():
    """删库进行中排进来的 flush **等撤回结束**才执行，且不会把已撤回的消息写回库。

    这条管的是「撤回之后它自己又冒出来了」：库在删的这段时间里，如果补写能插进
    「删库」与「清队」之间，那些正在被撤回的消息就被写进了库 —— 而且是在删完之后写的，
    所以留在库里。

    变异：`clear_after` 只保留「删完再清」但**不持锁** → 本条两条断言都红（flush 抢在
    清除之前把那两条补写进库）。
    """
    db = _DB(up=False)
    outbox = MessageOutbox()
    writer = _Writer(db)
    state, _ = await outbox.write(writer, ping=db.ping)
    assert state.state == "pending"

    db.up = True  # 库回来了：这之后任何一次补写都会把这条写进库
    gate, started = asyncio.Event(), asyncio.Event()

    async def _slow_delete() -> int:
        started.set()
        await gate.wait()
        return 1

    t_revoke = asyncio.create_task(outbox.clear_after(_slow_delete))
    await started.wait()  # 删库已经进到一半、正占着锁
    t_flush = asyncio.create_task(outbox.flush(ping=db.ping))
    await asyncio.sleep(0)  # 那次 flush 排队等锁

    assert len(writer.calls) == 1, "撤回还没结束，flush 不许先动队列（撤回持锁）"

    gate.set()
    assert await asyncio.wait_for(t_revoke, timeout=5) == 1
    report = await asyncio.wait_for(t_flush, timeout=5)

    assert (report.flushed, report.dropped) == ([], [])
    assert len(writer.calls) == 1, "撤回已把这段历史整个清掉，补写不许把它写回库"
    assert outbox.has_pending is False


# ── reconcile：点「重试」时拿幂等键回库对账 ─────────────────────────────────


class _Lookup:
    """假 `lookup`：只回答表里有的 key，并记下每次被问了哪些。"""

    def __init__(self, found: dict[str, int] | None = None) -> None:
        self.found = dict(found or {})
        self.asked: list[list[str]] = []

    async def __call__(self, keys: list[str]) -> dict[str, int]:
        self.asked.append(list(keys))
        return {k: v for k, v in self.found.items() if k in keys}


async def test_reconcile_reports_a_message_the_database_already_has():
    """① 队里没有、但库里**有**这一行 → 并入 `flushed`（带真实行 id）。

    这就是「写成功了、响应没回到前端」那半边：前端那条停在「未保存」，而会话被空闲清理
    逐出后队列是空的 —— 只 `flush` 的话读数里什么都没有，标记永远翻不回来。
    """
    outbox = MessageOutbox()
    db = _DB(up=True)
    key = "a" * 32
    lookup = _Lookup({key: 7})

    report = await outbox.reconcile([key], scope="s1", ping=db.ping, lookup=lookup)

    assert report.flushed == [(key, 7)]
    assert report.dropped == []
    assert lookup.asked == [[key]], "该问的没问，或问了不止一次"


async def test_reconcile_reports_a_lost_message_and_logs_it(caplog):
    """② 队里没有、库里也没有 → 判死并入 `dropped`，同时记一条 ERROR。

    它是「消息真的丢了」的第二个时刻（第一个是关停那一段）。只记 key 与 scope，不记正文。
    """
    caplog.set_level(logging.ERROR, logger="core.message_outbox")
    outbox = MessageOutbox()
    db = _DB(up=True)
    key = "b" * 32

    report = await outbox.reconcile([key], scope="s1", ping=db.ping, lookup=_Lookup())

    assert report.dropped == [key]
    assert report.flushed == []
    lost = [r for r in caplog.records if "lost" in r.getMessage()]
    assert len(lost) == 1, f"丢了一条却没留痕（或留了不止一条）：{caplog.records}"
    assert lost[0].args == ("s1", 1, [key]), (
        f"告警要同时给出会话、条数与前 10 个 key：{lost[0].args}")


async def test_reconcile_logs_one_alert_for_the_whole_round(caplog):
    """②′ 一次对账只记**一条**告警，不是每个 key 一条。

    `keys` 是请求体里来的（上限 200），逐 key 记的话一个构造出来的大 body 就能让一次重试
    刷满 200 行日志 —— 日志面板正是拿来追这条会话的地方，被刷满了就等于没有。

    条数仍给**全量**（不能因为截断而少报），只有列出来的 key 截到前 10 个。
    """
    caplog.set_level(logging.ERROR, logger="core.message_outbox")
    outbox = MessageOutbox()
    db = _DB(up=True)
    keys = [f"{i:032x}" for i in range(12)]

    report = await outbox.reconcile(keys, scope="s1", ping=db.ping, lookup=_Lookup())

    assert report.dropped == keys and report.flushed == [], report
    lost = [r for r in caplog.records if "lost" in r.getMessage()]
    assert len(lost) == 1, f"12 条丢失记了 {len(lost)} 条告警，应当整轮一条：{lost}"
    assert lost[0].args == ("s1", 12, keys[:10]), (
        f"告警要报全量条数、只列前 10 个 key：{lost[0].args}")


async def test_reconcile_leaves_a_still_queued_message_alone():
    """③ 那条还在队里 → 两边都不出现，且**不许**拿它去查库。

    对账问的是「队列已经不记得的那条到底落库没有」；还在队里的，答案就是「还没写，
    下次补」。拿去查库不仅白问，还会把一条 pending 判成 lost —— 库挂了的时候每点一次
    重试，就在日志里多报一次「消息丢失」。
    """
    db = _DB(up=False)
    outbox = MessageOutbox()
    writer = _Writer(db)
    state, _ = await outbox.write(writer, ping=db.ping)
    assert state.state == "pending"
    lookup = _Lookup({state.key: 9})

    report = await outbox.reconcile([state.key], scope="s1", ping=db.ping, lookup=lookup)

    assert (report.flushed, report.dropped) == ([], [])
    assert lookup.asked == [], "还在队里的那条不该被拿去查库"


async def test_reconcile_does_not_re_ask_about_a_key_this_round_settled():
    """本次补写里已经了结的 key 不再查库 —— 再问一次会把补上的那条又判成丢的。

    这一轮 `flush` 已经把它写进去了（读数里有真实行 id）；若不排除，`lookup` 会拿到
    一个「库里查不到」的答案 —— 因为查的是**别的** id 空间里没有它 —— 于是同一条消息
    既在 `flushed` 又在 `dropped`，前端按后者标成「保存失败」。
    """
    db = _DB(up=False)
    outbox = MessageOutbox()
    writer = _Writer(db)
    state, _ = await outbox.write(writer, ping=db.ping)
    assert state.state == "pending"

    db.up = True  # 库回来了：这次 reconcile 的第一段就会把它补上
    lookup = _Lookup()
    report = await outbox.reconcile([state.key], scope="s1", ping=db.ping, lookup=lookup)

    assert [key for key, _ in report.flushed] == [state.key]
    assert report.dropped == []
    assert lookup.asked == []
