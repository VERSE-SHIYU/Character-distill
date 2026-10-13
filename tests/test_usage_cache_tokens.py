# -*- coding: utf-8 -*-
"""目标检查：DeepSeek 缓存命中/未命中 token 记进用量表。

`docs/specs/arc-phase-fields.md` §2.3 末列承诺「命中率由用量字段
`prompt_cache_hit_tokens` 记录」—— 这几条把承诺钉成可判定的判据：

  1. 厂商 usage 带 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` → **原样**写进表；
  2. 不带（别的厂商不回这两个字段）→ 落库 **NULL**，**不是 0**：0 表示「确实一次没命中」，
     与「没有这项数据」必须可区分（否则「没数据」会被读成「没命中」）；
  3. 两端存储（SQLite / PG）都覆盖。

链路：厂商对象 → `adapters.llm_adapter._usage_from`（唯一取值点）→ 汇总 → `try_record_usage`
→ `storage.record_usage` → 库。落库那几条走**真出口** `try_record_usage`（不只调
`record_usage`），把「汇总→落库」这段转发也一起锁住。
"""
from __future__ import annotations

import os
import uuid

import pytest
from openai.types.completion_usage import CompletionUsage

import adapters.llm_adapter as LA
import core.utils as U
from conftest import PG_ENV
from storage.postgres_store import PostgresStore
from storage.sqlite_store import SQLiteStore

ACTION = "cache_probe"


def _vendor_usage(*, hit: int | None = None, miss: int | None = None) -> CompletionUsage:
    """deepseek 形态的 usage：命中/未命中字段是 OpenAI schema 之外的 extra（SDK `extra=allow`）。

    不给 hit/miss = 别的厂商的 usage —— 那两个键根本不在。
    """
    raw = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
    if hit is not None:
        raw["prompt_cache_hit_tokens"] = hit
    if miss is not None:
        raw["prompt_cache_miss_tokens"] = miss
    return CompletionUsage.model_validate(raw)


# ── 取值口径（统一取值函数）──────────────────────────────────────────────

def test_usage_from_carries_cache_fields_when_vendor_reports_them():
    usage = LA._usage_from(_vendor_usage(hit=64, miss=36))
    assert usage["prompt_cache_hit_tokens"] == 64
    assert usage["prompt_cache_miss_tokens"] == 36


def test_usage_from_leaves_cache_fields_none_when_vendor_is_silent():
    """别的厂商不回 → None（落库 NULL），不是 0。"""
    usage = LA._usage_from(_vendor_usage())
    assert usage["prompt_cache_hit_tokens"] is None
    assert usage["prompt_cache_miss_tokens"] is None


def test_usage_from_does_not_invent_fields_the_vendor_reported_as_zero():
    """厂商回了 0（确实一次没命中）→ 原样保留 0，不能与「没数据」混为一谈。"""
    usage = LA._usage_from(_vendor_usage(hit=0, miss=100))
    assert usage["prompt_cache_hit_tokens"] == 0
    assert usage["prompt_cache_miss_tokens"] == 100


# ── 汇总（并发分片）──────────────────────────────────────────────────────

def test_aggregate_sums_cache_fields_across_shards_that_reported():
    merged = U.aggregate_usage(
        [
            {"prompt_tokens": 10, "completion_tokens": 1,
             "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6},
            {"prompt_tokens": 20, "completion_tokens": 2},  # 该分片厂商没回
        ],
        chunk_count=2,
    )
    assert merged["prompt_cache_hit_tokens"] == 4
    assert merged["prompt_cache_miss_tokens"] == 6


def test_aggregate_keeps_cache_fields_none_when_nobody_reported():
    """全员没数据 → None。折成 0 会把「没有这项数据」伪造成「命中 0」。"""
    merged = U.aggregate_usage(
        [
            {"prompt_tokens": 10, "completion_tokens": 1},
            {"prompt_tokens": 20, "completion_tokens": 2},
        ],
        chunk_count=2,
    )
    assert merged["prompt_cache_hit_tokens"] is None
    assert merged["prompt_cache_miss_tokens"] is None


# ── 落库（走真出口 try_record_usage）───────────────────────────────────────

class _StubLLM:
    _model = "stub-model"
    last_usage = None


async def _record(monkeypatch, store, usage: dict, user_id: str) -> None:
    """走**真出口**落一行：身份与投递按 test_nonfatal 的形态替换掉（写库协程体照跑）。"""
    captured: dict = {}
    monkeypatch.setattr(U, "current_user_id", lambda: user_id)
    monkeypatch.setattr(U, "submit_to_main_loop",
                        lambda coro, **kw: captured.setdefault("coro", coro))
    U.try_record_usage(store, _StubLLM(), action=ACTION, usage=usage)
    assert "coro" in captured, "没投递出去 —— 断言的是那段写库协程，不是调度器"
    await captured["coro"]


async def _fetch_sqlite(store, user_id: str):
    async with await store._connect() as conn:
        cur = await conn.execute(
            "SELECT prompt_cache_hit_tokens, prompt_cache_miss_tokens "
            "FROM usage_stats WHERE user_id = ? AND action = ?",
            (user_id, ACTION),
        )
        return await cur.fetchone()


async def _fetch_pg(store, user_id: str):
    async with await store._connect() as conn:
        row = await conn.fetchrow(
            "SELECT prompt_cache_hit_tokens, prompt_cache_miss_tokens "
            "FROM usage_stats WHERE user_id = $1 AND action = $2",
            user_id, ACTION,
        )
        return (row["prompt_cache_hit_tokens"], row["prompt_cache_miss_tokens"])


# — SQLite（本地必跑）—

@pytest.fixture
async def sqlite_store(tmp_path):
    store = SQLiteStore(str(tmp_path / f"cache_{uuid.uuid4().hex}.db"))
    await store._ensure_initialized()
    user_id = "u-cache"
    await store.create_user(user_id, "cache_user", "hash")
    yield store, user_id


async def test_sqlite_writes_cache_fields(monkeypatch, sqlite_store):
    store, user_id = sqlite_store
    await _record(monkeypatch, store,
                  {"prompt_tokens": 100, "completion_tokens": 10,
                   "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36},
                  user_id)
    assert await _fetch_sqlite(store, user_id) == (64, 36)


async def test_sqlite_stores_null_when_vendor_silent(monkeypatch, sqlite_store):
    store, user_id = sqlite_store
    await _record(monkeypatch, store, {"prompt_tokens": 100, "completion_tokens": 10}, user_id)
    assert await _fetch_sqlite(store, user_id) == (None, None)


# — PG（CI 必跑；本地无 PG 时显式 skip）—

_pg = PG_ENV.skipif("用量缓存字段的 PG 落库用例")


@pytest.fixture
async def pg_store():
    store = PostgresStore(os.environ["DATABASE_URL"])
    await store._ensure_initialized()
    user_id = f"cache-{uuid.uuid4().hex}"
    await store.create_user(user_id, f"cache_{uuid.uuid4().hex[:10]}", "hash")
    yield store, user_id
    async with await store._connect() as conn:
        await conn.execute("DELETE FROM usage_stats WHERE user_id = $1", user_id)
    await store.close()


@_pg
async def test_pg_writes_cache_fields(monkeypatch, pg_store):
    store, user_id = pg_store
    await _record(monkeypatch, store,
                  {"prompt_tokens": 100, "completion_tokens": 10,
                   "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36},
                  user_id)
    assert await _fetch_pg(store, user_id) == (64, 36)


@_pg
async def test_pg_stores_null_when_vendor_silent(monkeypatch, pg_store):
    store, user_id = pg_store
    await _record(monkeypatch, store, {"prompt_tokens": 100, "completion_tokens": 10}, user_id)
    assert await _fetch_pg(store, user_id) == (None, None)
