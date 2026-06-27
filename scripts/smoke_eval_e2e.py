#!/usr/bin/env python3
"""真 key 端到端冒烟：EvaluationPipeline + 真实 DeepSeek LLM + SQLite 持久化。

用法:
  DEEPSEEK_API_KEY=sk-xxx python scripts/smoke_eval_e2e.py

三段冒烟：
  (a) 单聊一轮 → 断言 session affinity 落库 + importance 1-10 + stage 合法
  (b) 群聊一轮 → 断言 group_affinity 表 (group_id,card_id) 写入
  (c) 故障注入 → time_event 存库抛错，断言单聊 affinity 仍正常更新
"""

from __future__ import annotations

import json
import os
import sys
import types
import uuid

# ── Inject fake deps before any project import ──
# Pipeline._persist_affinity 内部做了 `from deps import run_on_main_loop`。
# Fake 版本在当前 event loop 中直接 await 协程。
if "deps" not in sys.modules:
    _fake_deps = types.ModuleType("deps")
    _fake_deps.run_on_main_loop = lambda coro, timeout=600: _run_await(coro)
    sys.modules["deps"] = _fake_deps


def _run_await(coro):
    """在已有 event loop 的线程中同步地 await 一个协程。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # 已有 loop → 用 run_coroutine_threadsafe 配合 future
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=600)


# ── Now safe to import from the project ──
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.llm_adapter import LLMAdapter
from core.affinity_service import AffinityService, calc_stage
from core.evaluation_pipeline import EvaluationPipeline, EvalContext, EvalResult

# ═══════════════════════════════════════════════════════════════
# 辅助对象
# ═══════════════════════════════════════════════════════════════

class FakePsyche:
    affinity_baseline = 50
    volatility = "适中"
    grudge_inertia = "一般"
    triggers: list[str] = []
    soft_spots: list[str] = []

class FakeCard:
    name = "林若溪"
    values = ["温柔", "敏感", "执着"]
    inner_tensions = ["渴望被理解 vs 害怕暴露脆弱"]
    psyche = FakePsyche()

class FakeMemory:
    def __init__(self, raise_on_add_manual: bool = False):
        self.enabled = True
        self.raise_on_add_manual = raise_on_add_manual
        self.added: list[tuple] = []

    def add_manual(self, text: str, card_id: str, metadata: dict | None = None) -> None:
        if self.raise_on_add_manual:
            raise RuntimeError("FakeMemory.add_manual 强制异常")
        self.added.append((text, card_id, metadata))


# ═══════════════════════════════════════════════════════════════
# SQLite 数据库辅助
# ═══════════════════════════════════════════════════════════════

def _init_db(db_path: str) -> None:
    """Create a fresh SQLite database with all migrations applied."""
    os.environ["STORAGE_BACKEND"] = "sqlite"
    # 直接创建 store 实例，不走 get_store() 以避免 env 依赖
    from storage.sqlite_store import SQLiteStore
    store = SQLiteStore(db_path)
    import asyncio
    asyncio.run(store._ensure_initialized())
    return store


async def _seed_session(store, db_path: str) -> str:
    """Insert minimum records (text → card → session) and return session_id."""
    import aiosqlite
    session_id = uuid.uuid4().hex[:12]
    text_id = uuid.uuid4().hex[:12]
    card_id = uuid.uuid4().hex[:12]

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute(
            "INSERT INTO texts (id, filename, content, char_count) VALUES (?, ?, ?, ?)",
            (text_id, "e2e_test.txt", "林若溪是个温柔敏感的女孩。", 20),
        )
        await conn.execute(
            "INSERT INTO cards (id, text_id, name, card_json) VALUES (?, ?, ?, ?)",
            (card_id, text_id, "林若溪", '{"name":"林若溪"}'),
        )
        await conn.execute(
            "INSERT INTO sessions (id, card_id, user_role) VALUES (?, ?, ?)",
            (session_id, card_id, "对方"),
        )
        await conn.commit()

    return session_id, card_id


async def _verify_session_affinity(store, session_id: str) -> dict | None:
    return await store.get_session_affinity(session_id)


async def _verify_group_affinity(store, group_id: str, card_id: str) -> dict | None:
    return await store.get_group_affinity(group_id, card_id)


# ═══════════════════════════════════════════════════════════════
# 冒烟测试主体
# ═══════════════════════════════════════════════════════════════

def smoke_a_single_chat(store, db_path: str, llm: LLMAdapter) -> dict:
    """(a) 单聊一轮：发消息→拿回复→断言 session affinity 已落库。"""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session_id, card_id = loop.run_until_complete(_seed_session(store, db_path))

    svc = AffinityService()
    svc.affinity = 40  # 初始值

    ctx = EvalContext(
        card=FakeCard(),
        user_message="若溪，你今天心情怎么样？",
        assistant_reply="还好吧，就是有点累。谢谢你关心我。",
        user_role="对方",
        old_stage="陌生",
        session_id=session_id,
        group_id="",
        card_id=card_id,
        storage=store,
        memory=FakeMemory(),
        affinity_service=svc,
        reaction_service=object(),
        llm=llm,
        reaction_appraisal="",
    )

    pipeline = EvaluationPipeline()
    result = pipeline.run(ctx)

    # Assert 1: pipeline 执行成功
    assert result.applied is True, f"pipeline.applied should be True, got {result.applied}"
    assert 1 <= result.importance <= 10, f"importance out of range: {result.importance}"

    # Assert 2: stage 合法
    stage, emoji = calc_stage(result.affinity)
    assert stage in ("陌生", "认识", "熟悉", "朋友", "亲近", "心意相通"), f"invalid stage: {stage}"

    # Assert 3: DB 落库
    row = loop.run_until_complete(_verify_session_affinity(store, session_id))
    assert row is not None, "session affinity not persisted"
    assert row["affinity"] == svc.affinity, f"DB affinity {row['affinity']} != {svc.affinity}"

    loop.close()
    return {
        "stage": stage,
        "stage_emoji": emoji,
        "affinity": result.affinity,
        "importance": result.importance,
        "reason_preview": svc.affinity_reason[:80],
    }


def smoke_b_group_chat(store, db_path: str, llm: LLMAdapter) -> dict:
    """(b) 群聊一轮：断言 group_affinity 表 (group_id,card_id) 已写入。"""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    group_id = "grp_e2e_" + uuid.uuid4().hex[:8]
    _, card_id = loop.run_until_complete(_seed_session(store, db_path))

    svc = AffinityService()
    svc.affinity = 50

    ctx = EvalContext(
        card=FakeCard(),
        user_message="大家好，今天有什么有意思的事吗？",
        assistant_reply="",
        user_role="导演",
        old_stage="陌生",
        session_id="",
        group_id=group_id,
        card_id=card_id,
        storage=store,
        memory=FakeMemory(),
        affinity_service=svc,
        reaction_service=object(),
        llm=llm,
        reaction_appraisal="",
    )

    pipeline = EvaluationPipeline()
    result = pipeline.run(ctx)

    assert result.applied is True, f"group pipeline.applied should be True, got {result.applied}"

    # Verify group_affinity table
    row = loop.run_until_complete(_verify_group_affinity(store, group_id, card_id))
    assert row is not None, f"group_affinity ({group_id},{card_id}) not persisted"
    assert row["affinity"] == svc.affinity, f"DB affinity {row['affinity']} mismatch"

    loop.close()
    return {
        "group_id": group_id,
        "affinity": result.affinity,
        "importance": result.importance,
    }


def smoke_c_fault_injection(store, db_path: str, llm: LLMAdapter) -> dict:
    """(c) 故障注入：time_event 存库抛错 → 断言单聊 affinity 仍正常更新。"""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session_id, card_id = loop.run_until_complete(_seed_session(store, db_path))

    svc = AffinityService()
    svc.affinity = 30
    old_aff = svc.affinity

    # memory 的 add_manual 会抛异常
    memory = FakeMemory(raise_on_add_manual=True)

    # LLM reply 中包含 time_event，触发故障路径
    llm_reply = (
        '{"affinity":58,"trust":42,"mood":"开心","guard":38,'
        '"inner_voice":"她主动关心我，有点感动。","mood_emoji":"😊",'
        '"importance":7,'
        '"time_event":{"event":"明天有面试","when_text":"明天","due_at":"2026-06-26T10:00"},'
        '"in_character":85,"ooc_reason":""}'
    )

    class FaultLLM:
        def chat(self, system, messages):
            return llm_reply

    ctx = EvalContext(
        card=FakeCard(),
        user_message="别太累，早点休息。",
        assistant_reply="嗯，你也是。",
        user_role="对方",
        old_stage="陌生",
        session_id=session_id,
        group_id="",
        card_id=card_id,
        storage=store,
        memory=memory,
        affinity_service=svc,
        reaction_service=object(),
        llm=FaultLLM(),
        reaction_appraisal="",
    )

    pipeline = EvaluationPipeline()
    result = pipeline.run(ctx)

    # CORE 落定 — affinity 应已更新且不为旧值
    assert result.applied is True, f"fault test: applied should be True, got {result.applied}"
    assert svc.affinity != old_aff, "affinity should have changed despite fault"
    assert result.importance == 7, f"importance should be 7, got {result.importance}"

    # DB 应已落库（_persist_affinity 不受 time_event 故障影响）
    row = loop.run_until_complete(_verify_session_affinity(store, session_id))
    assert row is not None, "session affinity should have been persisted despite fault"
    assert row["affinity"] == svc.affinity, f"DB affinity mismatch after fault injection"

    loop.close()
    return {
        "old_affinity": old_aff,
        "new_affinity": svc.affinity,
        "importance": result.importance,
        "time_event_persisted": len(memory.added) == 0,  # 应为 0（故障被吞）
    }


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    # 尝试从 .env 加载
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        print("❌ 需要设置 DEEPSEEK_API_KEY 环境变量")
        sys.exit(1)

    print("=" * 56)
    print("  EvaluationPipeline 真 Key 端到端冒烟")
    print("=" * 56)
    print()

    # ── 初始化 LLM ──
    print("[setup] 创建 LLMAdapter ...", end=" ")
    llm = LLMAdapter(api_key=api_key)
    print("OK")
    print()

    # ── 初始化 DB ──
    db_path = str(REPO_ROOT / "data" / "e2e_smoke_test.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    print(f"[setup] 创建临时 SQLite 数据库: {db_path}")
    # 设置 env 确保 SQLiteStore 能读取
    os.environ.setdefault("STORAGE_BACKEND", "sqlite")
    os.environ.setdefault("DB_PATH", db_path)
    from storage.sqlite_store import SQLiteStore
    store = SQLiteStore(db_path)
    import asyncio
    asyncio.run(store._ensure_initialized())
    print("[setup] DB 初始化完成")
    print()

    passed = 0
    failed = 0

    # ── (a) 单聊 ──
    print("─" * 48)
    print("  (a) 单聊 → session affinity 落库")
    print("─" * 48)
    try:
        a = smoke_a_single_chat(store, db_path, llm)
        print(f"  ✅ stage={a['stage']}({a['stage_emoji']}) "
              f"affinity={a['affinity']} importance={a['importance']}")
        print(f"     reason: {a['reason_preview']}")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        import traceback
        traceback.print_exc()
        failed += 1
    print()

    # ── (b) 群聊 ──
    print("─" * 48)
    print("  (b) 群聊 → group_affinity 表写入")
    print("─" * 48)
    try:
        b = smoke_b_group_chat(store, db_path, llm)
        print(f"  ✅ group={b['group_id']} affinity={b['affinity']} importance={b['importance']}")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        import traceback
        traceback.print_exc()
        failed += 1
    print()

    # ── (c) 故障注入 ──
    print("─" * 48)
    print("  (c) 故障注入 → 副作用炸了 CORE 仍落定")
    print("─" * 48)
    try:
        c = smoke_c_fault_injection(store, db_path, llm)
        print(f"  ✅ affinity: {c['old_affinity']} → {c['new_affinity']} "
              f"(changed={c['old_affinity']!=c['new_affinity']})")
        print(f"     importance={c['importance']}, time_event_fault_trapped={c['time_event_persisted']}")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        import traceback
        traceback.print_exc()
        failed += 1
    print()

    # ── 清理 ──
    try:
        os.remove(db_path)
        print(f"[cleanup] 删除临时数据库: {db_path}")
    except Exception:
        pass

    print()
    print("=" * 56)
    print(f"  结果: {passed} passed, {failed} failed")
    print("=" * 56)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
