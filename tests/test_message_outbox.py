"""补写队列：顺序、可达性分辨、丢弃粒度。

seam 是 `MessageOutbox` 的四个方法（`write` / `flush` / `discard` / `clear`）—— 队列不碰
存储，`write_fn` 与 `ping` 都是传进来的，所以这一层可以完全脱离数据库测。

**「库不可达」在夹具里的形状是「写入与 ping 一起失败」**，不是「ping 说不可达而写入照常
成功」—— 真实现里连不上时写就是会抛（写完才问 ping 是为了分辨「库挂了」与「这一条坏了」，
不是反过来）。这条校准错了的话，下面几条用例会「红得符合预期」却什么也没证明。

每条用例对应一个「实现退回成什么样就会红」的形态，见交付报告的对账表。
"""

from __future__ import annotations

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
    """假 `write_fn`：每次调用记一笔，成功则从假表里领一个递增行 id。"""

    def __init__(self, db: _DB, fail_all: bool = False) -> None:
        self.db = db
        self.fail_all = fail_all
        self.calls: list[str] = []
        self.rows: list[tuple[str, int]] = []

    async def __call__(self, key: str) -> int:
        self.calls.append(key)
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
