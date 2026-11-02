# -*- coding: utf-8 -*-
"""D25-3b 目标检查：同一本书并发蒸多个角色时只付一次整书未命中。

S1–S6 检验前缀错峰原语（`C.prefix_turn`）与 `_format_groups` 的接线：同一账号、同一
整书前缀下，后到的预热组等领头吐出第一段再发。S7–S8 检验回忆区识别的在途共享：
同一本书同时两个任务只识别一次。S6 是对照行 —— 没有账号身份、或没有整书前缀时
不得登记、不得等待，与改前相同。
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest

import test_distiller_routing as R
from core import concurrency as C
from core.distiller import (
    _CACHE_WARM_GROUP,
    _PHASE_SOURCE_GROUP,
    ArcInput,
    Distiller,
)
from core.retro_cache import resolve_retro_regions
from core.retro_regions import ChapterRange, RetroRegions
from storage.sqlite_store import SQLiteStore

WARM = _CACHE_WARM_GROUP
ARC = _PHASE_SOURCE_GROUP
BOOK_PREFIX = "以下是完整的文本内容：\n\n# 第一本书\n"
EXCERPT_PREFIX = "以下是人物摘录：\n\n"
WARM_DELAY = 0.3
WAIT_LIMIT = 10.0

MARKED = RetroRegions("marked", (ChapterRange(1, 1, "日记"),))


@pytest.fixture(autouse=True)
def _reset_shared_ledgers():
    """每个用例前后清掉进程内的错峰登记与共享闸。"""
    C.reset_prefix_turns()
    C.reset_account_gates()
    yield
    C.reset_prefix_turns()
    C.reset_account_gates()


# ── S1/S2：同键一个领头，其余跟随；领头放行后键位释放 ──────────────────────

def test_s1_follower_waits_until_leader_releases():
    key = ("acc", "book-hash")
    leader = C.prefix_turn(key)
    follower = C.prefix_turn(key)
    assert leader.is_leader is True
    assert follower.is_leader is False

    returned = threading.Event()

    def _wait():
        follower.wait()
        returned.set()

    t = threading.Thread(target=_wait, daemon=True)
    t.start()
    try:
        assert not returned.wait(0.2), "跟随者在领头放行前就返回了"
        leader.release()
        assert returned.wait(2.0), "领头放行后跟随者仍被阻塞"
    finally:
        leader.release()
        t.join(2.0)


def test_s2_released_key_yields_a_new_leader():
    key = ("acc", "book-hash")
    first = C.prefix_turn(key)
    assert first.is_leader is True
    first.release()
    second = C.prefix_turn(key)
    try:
        assert second.is_leader is True
    finally:
        second.release()


# ── S3：首段信号只发一次，且在第 2 段之前 ──────────────────────────────────

class _StreamLLM:
    def __init__(self, events):
        self.events = events
        self.last_usage = None
        self.model = "fake"

    def chat_stream_long(self, system, messages, max_tokens=None, gate=None):
        self.events.append("piece1")
        yield "甲"
        self.events.append("piece2")
        yield "乙"
        self.events.append("piece3")
        yield "丙"
        return {"prompt_tokens": 3, "completion_tokens": 3}


def test_s3_first_piece_fires_once_before_second_piece():
    events: list[str] = []
    d = Distiller(llm=_StreamLLM(events), config_path=None)

    d._collect_stream(
        "system", [{"role": "user", "content": "u"}], "测试", "distill_test",
        on_first_piece=lambda: events.append("callback"),
    )

    assert events.count("callback") == 1
    assert events == ["piece1", "callback", "piece2", "piece3"]


# ── S4/S5/S6：`_format_groups` 的预热组错峰 ────────────────────────────────

class _PrefixLLM:
    """按生产形状逐段产出，记录每次流式调用的开始/首段时刻与 system 是否带整书前缀。"""

    def __init__(self, *, account_id: str | None = None, fail_first_warm: bool = False):
        if account_id is not None:
            self.account_id = account_id
        self.model = "fake"
        self.last_usage = None
        self.fail_first_warm = fail_first_warm
        self.calls: list[dict] = []
        self.error_at: float | None = None
        self._lock = threading.Lock()
        self._warm_calls = 0

    def chat_stream_long(self, system, messages, max_tokens=None, gate=None):
        group = R._format_group_of(system)
        record = {
            "group": group,
            "start": time.monotonic(),
            "first": None,
            "end": None,
            "book_prefix": system.startswith(BOOK_PREFIX),
        }
        with self._lock:
            self.calls.append(record)
            index = self._warm_calls
            if group == WARM:
                self._warm_calls += 1

        def _gen():
            if group != WARM:
                record["first"] = time.monotonic()
                yield R._group_reply(group)
                return {"prompt_tokens": 1, "completion_tokens": 1}
            if self.fail_first_warm and index == 0:
                time.sleep(WARM_DELAY)
                self.error_at = record["error_at"] = time.monotonic()
                raise RuntimeError("预热流在首段前失败")
            time.sleep(WARM_DELAY)
            record["first"] = time.monotonic()
            yield R._group_reply(group)
            time.sleep(WARM_DELAY)
            record["end"] = time.monotonic()
            return {"prompt_tokens": 1, "completion_tokens": 1}

        return _gen()


def _two_distillers(llm) -> list[Distiller]:
    return [Distiller(llm=llm, config_path=None), Distiller(llm=llm, config_path=None)]


def _run_format_together(distillers, *, prefix: str) -> list:
    """两线程同时跑完整 `_format_groups`；返回值 0 = 完成，异常对象 = 失败。"""
    results: list = [None, None]
    barrier = threading.Barrier(len(distillers))

    def _body(i: int) -> None:
        barrier.wait(5)
        try:
            list(distillers[i]._format_groups(
                character_name="角色",
                user_message="以下是关于「角色」的完整分析档案",
                rel_prefix="共享前缀",
                rel_material="档案",
                action="distill_format",
                system_prefix=prefix,
                arc_input=ArcInput(EXCERPT_PREFIX, "以下是关于「角色」的完整分析档案"),
            ))
            results[i] = "ok"
        except Exception as exc:      # 领头失败是可预期的一支（S5）
            results[i] = exc

    threads = [threading.Thread(target=_body, args=(i,), daemon=True)
               for i in range(len(distillers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT_LIMIT)
    assert not any(t.is_alive() for t in threads), "格式化线程没有在时限内结束"
    return results


def test_s4_late_warm_group_starts_after_leaders_first_piece():
    llm = _PrefixLLM(account_id="acc")
    results = _run_format_together(_two_distillers(llm), prefix=BOOK_PREFIX)
    assert results == ["ok", "ok"]

    warm = sorted((c for c in llm.calls if c["group"] == WARM), key=lambda c: c["start"])
    assert len(warm) == 2
    assert all(c["book_prefix"] for c in warm)
    earlier, later = warm
    assert earlier["first"] is not None
    assert later["start"] >= earlier["first"], "后开始的预热组在领头首段之前就发了"
    assert later["start"] < earlier["end"], "跟随者等到领头整组结束才发，没有在首段时放行"

    arc = [c for c in llm.calls if c["group"] == ARC]
    assert len(arc) == 2
    assert not any(c["book_prefix"] for c in arc)
    assert max(c["start"] for c in arc) < min(c["first"] for c in warm), "弧线组被错峰拖住了"


def test_s5_leader_failure_still_releases_the_follower():
    llm = _PrefixLLM(account_id="acc", fail_first_warm=True)
    results = _run_format_together(_two_distillers(llm), prefix=BOOK_PREFIX)

    warm = sorted((c for c in llm.calls if c["group"] == WARM), key=lambda c: c["start"])
    assert len(warm) == 2, "跟随者的预热组没有发出"
    assert warm[0]["error_at"] is not None
    assert warm[1]["start"] >= warm[0]["error_at"], "领头失败后跟随者没有被放行"

    assert results.count("ok") == 1, "跟随者没有照常出卡"
    assert sum(isinstance(r, Exception) for r in results) == 1, "领头应只失败一次"


@pytest.mark.parametrize("prefix,account_id", [
    (BOOK_PREFIX, None),   # 替身没有账号身份，不包闸、不登记
    ("", "acc"),           # 没有整书前缀，本来就没有可命中的缓存
])
def test_s6_no_account_or_no_prefix_never_waits(prefix, account_id):
    llm = _PrefixLLM(account_id=account_id)
    results = _run_format_together(_two_distillers(llm), prefix=prefix)
    assert results == ["ok", "ok"]

    warm = [c for c in llm.calls if c["group"] == WARM]
    assert len(warm) == 2
    assert all(c["first"] is not None for c in warm)
    assert max(c["start"] for c in warm) < min(c["first"] for c in warm), "不该等待时发生了等待"


# ── S7/S8：同一本书的回忆区识别结果共享 ────────────────────────────────────

class _Detector:
    def __init__(self, result):
        self.result = result
        self.calls = 0
        self._lock = threading.Lock()

    def detect_retro_regions(self, content):
        with self._lock:
            self.calls += 1
        time.sleep(0.1)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _gather_inside(*coros):
    return await asyncio.gather(*coros)


def _gather(*coros):
    """两个协程必须跑在**同一个**循环上 —— 在途共享用的 Future 绑在创建它的循环上。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_gather_inside(*coros))
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"stagger_{uuid.uuid4().hex}.db"))


def _seed(store, uid) -> str:
    tid = f"txt_{uuid.uuid4().hex}"
    _run(store.save_text(tid, "src.txt", "第1章\n甲", user_id=uid))
    return tid


def test_s7_concurrent_resolve_detects_once(store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed(store, uid)
    detector = _Detector(MARKED)

    results = _gather(
        resolve_retro_regions(store, detector, tid, uid, "x"),
        resolve_retro_regions(store, detector, tid, uid, "x"),
    )

    assert detector.calls == 1
    assert results[0] == results[1] == MARKED


def test_s8_concurrent_failure_is_shared_and_not_cached(store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed(store, uid)
    detector = _Detector(RuntimeError("网络断了"))

    results = _gather(
        resolve_retro_regions(store, detector, tid, uid, "x"),
        resolve_retro_regions(store, detector, tid, uid, "x"),
    )

    assert detector.calls == 1
    assert results[0] == results[1]
    assert results[0].status == "unavailable" and results[0].final is False

    again = _run(resolve_retro_regions(store, detector, tid, uid, "x"))
    assert detector.calls == 2, "失败结果被缓存了，后一次没有再识别"
    assert again.status == "unavailable" and again.final is False
