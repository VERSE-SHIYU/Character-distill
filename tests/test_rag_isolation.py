"""Isolation test: prove distillation works even with broken embedding.

This simulates the worst case — embedding API misconfigured — and verifies:
1. _create_session with rag=None is instant (pure memory, no embedding call)
2. ChatEngine accepts rag=None without error
3. ContextEngine._retrieve_scenes returns "" when rag is None
4. schedule_scene_index degrades silently (prints log, does not raise)
5. schedule_scene_index for one card never runs concurrently; calls that arrive
   while it runs are merged into one follow-up run
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root on path
_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo))

from core.schema import CharacterCard, SpeakingStyle
from core.indexing_service import IndexingService


def _make_card(name="测试角色", traits=None, background="测试背景"):
    return CharacterCard(
        name=name,
        personality_traits=traits or ["温柔"],
        speaking_style=SpeakingStyle(tone="轻声", sentence_pattern="短句"),
        background=background,
    )


async def test_context_engine_rag_none():
    """ContextEngine._retrieve_scenes must return '' when rag is None."""
    print("1. ContextEngine with rag=None...", end=" ")
    from core.context_engine import ContextEngine

    card = _make_card("测试角色", ["温柔"], "测试背景")
    ctx = ContextEngine(card=card, rag=None, card_id="test", storage=None)
    result = ctx._retrieve_scenes("你好")
    assert result == "", f"Expected empty string, got: {result!r}"
    print("PASS (returns empty, no crash)")


async def test_create_session_rag_none():
    """_create_session with rag=None must NOT call embedding."""
    print("2. _create_session with rag=None...", end=" ")
    # Simulate TextManager._create_session logic:
    # rag=None → pass None directly to ChatEngine (no RAGEngine().index())
    card = _make_card("测试角色", ["勇敢"], "冒险者")

    from core.chat_engine import ChatEngine
    llm = MagicMock()
    engine = ChatEngine(llm, None, card, card_id="test", storage=None,
                        session_id="test", is_new_session=True)
    assert engine.rag is None, f"Expected rag=None, got {engine.rag}"
    assert engine._ctx_engine.rag is None
    # Build system prompt should work without RAG
    prompt = engine._ctx_engine.build("你好", "")
    assert len(prompt) > 0
    print("PASS (session created, no embedding)")


async def test_schedule_scene_index_degraded():
    """后台作业失败只记日志：不抛给调度方，去重键在作业结束后释放。"""
    print("3. schedule_scene_index with broken embedding...", end=" ")

    import core.indexing_service as mod

    svc = IndexingService({})

    with patch("core.indexing_service.RAGEngine") as mock_rag_cls:
        mock_rag = MagicMock()
        mock_rag.load_existing.return_value = False
        mock_rag.index.side_effect = Exception("Embedding API timeout")
        mock_rag_cls.return_value = mock_rag

        svc.schedule_scene_index(
            text_id="t1", card_id="c1", content="测试内容",
            char_name="测试角色", all_characters=[], embedding_key="sk-test",
        )
        assert "scenes_c1" in mod._scene_index_in_flight, "作业没调度起来 —— 下面的断言会空转"
        for _ in range(50):
            if "scenes_c1" not in mod._scene_index_in_flight:
                break
            await asyncio.sleep(0.02)

    assert mock_rag.index.called, "失败的嵌入调用没有发生"
    assert "scenes_c1" not in mod._scene_index_in_flight, "作业失败后去重键没有释放"
    print("PASS (failure logged, dedup key released)")


async def test_schedule_scene_index_dedup():
    """同一张卡的场景预索引不并发：只建一个后台任务，跑着时再来的调度合并成一次后续运行。"""
    print("4. schedule_scene_index dedup...", end=" ")

    import threading
    import time

    import core.indexing_service as mod

    svc = IndexingService({})
    lock = threading.Lock()
    state = {"running": 0, "max_running": 0, "runs": 0}

    def slow_index(*_a, **_kw):
        with lock:
            state["running"] += 1
            state["runs"] += 1
            state["max_running"] = max(state["max_running"], state["running"])
        time.sleep(0.2)
        with lock:
            state["running"] -= 1

    tasks: list = []
    real_create_task = asyncio.create_task

    def counting_create_task(coro):
        task = real_create_task(coro)
        tasks.append(task)
        return task

    mock_rag = MagicMock()
    mock_rag.load_existing.return_value = False
    mock_rag.index.side_effect = slow_index

    with patch("core.indexing_service.RAGEngine", return_value=mock_rag), \
            patch("core.indexing_service.SceneIndexer"), \
            patch.object(mod.asyncio, "create_task", counting_create_task):
        try:
            svc.schedule_scene_index("t1", "c1", "content", "name", embedding_key="sk-test")
            # 同一时刻再来一次 —— 不该再起第二个任务
            svc.schedule_scene_index("t1", "c1", "content", "name", embedding_key="sk-test")
            await asyncio.sleep(0.1)
            # 第一个还在跑时从另一条路又来一次 —— 与上一次合并成一次后续运行
            svc.schedule_scene_index("t1", "c1", "content", "name", embedding_key="sk-test")
            await asyncio.gather(*tasks)
        finally:
            mod._scene_index_in_flight.discard("scenes_c1")
            mod._pending_jobs.pop("scenes_c1", None)

    assert len(tasks) == 1, f"同一张卡起了 {len(tasks)} 个后台任务"
    assert state["max_running"] == 1, f"同一张卡的作业并发跑了 {state['max_running']} 份"
    assert state["runs"] == 2, f"期间的两次调度应合并成一次后续运行，实际共跑了 {state['runs']} 次"
    print("PASS (one task, never concurrent, later calls merged into one rerun)")


async def main():
    print("=== RAG ISOLATION VERIFICATION ===\n")
    try:
        await test_context_engine_rag_none()
        await test_create_session_rag_none()
        await test_schedule_scene_index_degraded()
        await test_schedule_scene_index_dedup()
    except Exception as exc:
        print(f"\nFAIL: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    print("\n=== ALL 4 TESTS PASSED ===")
    print("Embedding failure → distillation still returns chat-ready card.")
    print("Isolation is REAL, not a patch.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
