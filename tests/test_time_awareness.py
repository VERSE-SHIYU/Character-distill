# -*- coding: utf-8 -*-
"""时间感知块（spec `arc-attitude-time-boundary.md` §B1）与边界块亲近档门（§B2）。

B1：注入判定与文案全在纯模块 `core.time_awareness`；引擎只递事实（现在 / 上次对话时间 /
是不是首轮），不再自己读存储。B2：亲近档及以上不注入边界块，「亲近」只有 `is_close` 一处定义。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.affinity_rules import CLOSE_FROM
from core.chat_engine import ChatEngine
from core.clock import UserClock, set_current_timezone
from core.schema import CharacterCard
from core.time_awareness import INJECT_GAP_SECONDS, build_block, should_inject


class _StubLLM:
    model = ""

    def chat(self, system_prompt, messages, **_kw):
        return "回复"

    def chat_stream(self, system_prompt, messages, **_kw):
        yield "回复"


def _engine() -> ChatEngine:
    card = CharacterCard(name="测试角色")
    return ChatEngine(_StubLLM(), None, card, card_id="t", storage=None,
                      session_id="t", is_new_session=True)


def _attached(eng: ChatEngine, history: list[dict], last_activity_at=None) -> str:
    """本轮用户消息末尾附上的块（`_attach_turn_blocks` 的唯一出口）。"""
    eng.history = history
    msgs = eng._build_llm_messages(eng.history, "现在")
    eng._attach_turn_blocks(msgs, last_activity_at)
    return msgs[-1]["content"]


@pytest.fixture(autouse=True)
def _utc():
    """把用户时区钉成 UTC —— 纯计算断言里的时钟与间隔都按同一口径算。"""
    set_current_timezone("UTC")
    yield
    set_current_timezone("")


# ── B1 判定：什么时候注入 ────────────────────────────────────────────────────


class TestShouldInject:
    NOW = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)

    def test_first_turn_injects(self):
        assert should_inject(self.NOW, None, first_turn=True) is True

    def test_first_turn_injects_even_with_recent_activity(self):
        recent = self.NOW - timedelta(seconds=5)
        assert should_inject(self.NOW, recent, first_turn=True) is True

    def test_small_gap_same_period_does_not_inject(self):
        last = self.NOW - timedelta(seconds=60)
        assert should_inject(self.NOW, last, first_turn=False) is False

    def test_gap_of_the_threshold_injects(self):
        last = self.NOW - timedelta(seconds=INJECT_GAP_SECONDS)
        assert should_inject(self.NOW, last, first_turn=False) is True

    def test_period_change_injects_even_with_small_gap(self):
        now = datetime(2026, 10, 14, 23, 2, tzinfo=timezone.utc)
        last = datetime(2026, 10, 14, 22, 58, tzinfo=timezone.utc)   # 夜晚 → 深夜
        assert should_inject(now, last, first_turn=False) is True

    def test_missing_activity_injects(self):
        assert should_inject(self.NOW, None, first_turn=False) is True

    def test_isoformat_string_is_accepted(self):
        last = (self.NOW - timedelta(seconds=60)).isoformat()
        assert should_inject(self.NOW, last, first_turn=False) is False


# ── B1 渲染：注入块写什么 ────────────────────────────────────────────────────


class TestBuildBlock:
    NOW = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)

    def test_block_states_period_and_clock(self):
        block = build_block(self.NOW, None, first_turn=True)
        assert "【现实时间】现在是夜晚（22:30）。" in block

    def test_block_states_the_interval_after_a_long_gap(self):
        block = build_block(self.NOW, self.NOW - timedelta(hours=12), first_turn=False)
        assert "今天早些时候你们聊过。" in block

    def test_block_has_the_story_time_precedence_line(self):
        block = build_block(self.NOW, self.NOW - timedelta(hours=12), first_turn=False)
        assert "对话里已写明剧情时间（如「第二天早上」）时以剧情为准；" in block

    def test_block_has_no_behavior_example(self):
        block = build_block(self.NOW, self.NOW - timedelta(hours=12), first_turn=False)
        assert "怎么还不睡" not in block and "自然融入" not in block

    def test_first_turn_block_omits_the_interval_line(self):
        block = build_block(self.NOW, self.NOW - timedelta(seconds=5), first_turn=True)
        assert "刚刚还在聊" not in block

    def test_empty_when_not_injected(self):
        block = build_block(self.NOW, self.NOW - timedelta(seconds=60), first_turn=False)
        assert block == ""


# ── B1 接线：引擎两轮，近间隔同时段第二轮不注入 ─────────────────────────────


class TestEngineWiring:
    def test_second_turn_one_minute_later_same_period_has_no_block(self, monkeypatch):
        now = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(UserClock, "now", staticmethod(lambda tz=None: now))
        eng = _engine()
        history = [{"role": "user", "content": "在吗"}, {"role": "assistant", "content": "在"}]
        assert _attached(eng, history, now - timedelta(seconds=60)) == "现在"

    def test_twelve_hours_ago_injects_the_block(self, monkeypatch):
        now = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(UserClock, "now", staticmethod(lambda tz=None: now))
        eng = _engine()
        history = [{"role": "user", "content": "在吗"}, {"role": "assistant", "content": "在"}]
        assert _attached(eng, history, now - timedelta(hours=12)).startswith("现在\n\n【现实时间】")

    def test_first_turn_injects_without_touching_storage(self, monkeypatch):
        """首轮注入：`last_activity_at` 为空也出块（引擎不再自己读存储）。"""
        now = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(UserClock, "now", staticmethod(lambda tz=None: now))
        eng = _engine()
        assert _attached(eng, [], None).startswith("现在\n\n【现实时间】")


# ── B1 接线：路由在本轮写库前读会话取 updated_at（spec J9 / 补充 1）────────────


class TestRouterReadsSessionBeforeWriting:
    def test_stream_path_reads_precedes_the_user_message_write(self, monkeypatch):
        """流式路径：路由在存用户消息**之前**读会话，把 `updated_at` 当「上次对话时间」递给引擎。

        J9 的坑正在这里 —— `_do_chat_stream` 先 `save_message("user", …)` 再 `engine.chat_stream`，
        存消息会把 `updated_at` 刷成现在。变异：把读挪到存消息之后（或改回让引擎自己读）→
        引擎拿到的是 `NEW`，断言变红。
        """
        import asyncio

        import routers.chat as chat_router_mod
        from core.text_manager import new_session_entry

        OLD = "2026-01-01T00:00:00"
        NEW = "2026-10-14T22:30:00"

        class _Engine:
            def __init__(self):
                self.history = []
                self.last_traces = []
                self._last_rag_context = ""
                self._ctx_engine = type("Ctx", (), {"web_search_enabled": False})()
                self.affinity_enabled = True
                self.agent_mode = False
                self.seen: dict = {}

            def chat_stream(self, *a, **kw):
                self.seen["last_activity_at"] = kw.get("last_activity_at")
                return iter(["回复"])

            def post_stream_process(self, user_message, full_reply):
                pass

            def _should_retract(self, reply):
                return False

        class _Storage:
            """存消息把 `updated_at` 刷成 `NEW` —— 照生产行为，好让「读晚了」被测出来。"""

            def __init__(self):
                self.updated_at = OLD

            async def get_session_owned(self, session_id, user_id):
                return {"id": session_id, "card_id": "c1", "user_role": "", "avatar_data": "",
                        "created_at": OLD, "updated_at": self.updated_at}

            async def save_message(self, *a, **kw):
                self.updated_at = NEW
                return {"id": 1, "role": "user", "created_at": ""}

            async def ping(self):
                pass

        eng = _Engine()
        session = new_session_entry(eng, None, "u1")

        async def _ensure(session_id, storage_, sessions, user_id=""):
            return session

        monkeypatch.setattr(chat_router_mod, "_ensure_session", _ensure)

        async def _drive():
            resp = await chat_router_mod._do_chat_stream(
                "s1", "hi", storage=_Storage(), sessions={}, user_id="u1")
            async for chunk in resp.body_iterator:
                _ = chunk  # 排干：落库发生在生成器体内

        asyncio.run(_drive())

        assert eng.seen["last_activity_at"] == OLD, \
            "引擎拿到的是写库后的 updated_at（NEW），不是本轮之前的上次对话时间"


# ── B2 边界块：亲近档及以上不注入 ───────────────────────────────────────────


class TestBoundaryBlockCloseGate:
    def test_no_boundary_block_at_max_affinity(self):
        eng = _engine()
        eng._affinity = 100
        assert eng._build_boundary_block() == ""

    def test_no_boundary_block_at_the_close_threshold(self):
        eng = _engine()
        eng._affinity = CLOSE_FROM
        assert eng._build_boundary_block() == ""

    def test_boundary_block_below_the_threshold(self):
        eng = _engine()
        eng._affinity = CLOSE_FROM - 1
        assert "【边界感" in eng._build_boundary_block()

    def test_boundary_block_at_a_low_affinity(self):
        eng = _engine()
        eng._affinity = 60
        assert "【边界感" in eng._build_boundary_block()
