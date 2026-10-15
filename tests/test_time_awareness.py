# -*- coding: utf-8 -*-
"""时间感知（spec `time-awareness-fix.md`，取代 `arc-attitude-time-boundary.md` §B1 的门控）
与边界块亲近档门（§B2）。

时间：现实时间行**每轮都在系统提示里**；「隔了多久」只在隔了 ≥ 10 分钟时接在用户消息末尾。
文案与判定全在纯模块 `core.time_awareness`；引擎只递事实，不读存储。
替身照生产：首轮 history 里已有开场白（SG 验收 10-15 抓到的坑就是替身首轮 history 为空）。
B2：亲近档及以上不注入边界块，「亲近」只有 `is_close` 一处定义。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.affinity_rules import CLOSE_FROM
from core.chat_engine import ChatEngine
from core.clock import UserClock, set_current_timezone
from core.schema import CharacterCard
from core.time_awareness import INTERVAL_GAP_SECONDS, interval_note, reality_line


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


# ── 纯函数：reality_line / interval_note ─────────────────────────────────────

_GREETING = [{"role": "assistant", "content": "喂，云绮，你最好给我个解释！"}]   # 生产首轮已有开场白
_REALITY = "【现实时间】"
_STORY = "对话里已写明剧情时间（如「第二天早上」）时以剧情为准；对方问起现实时间时才按这里回答。"


class TestRealityLine:
    NOW = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)

    def test_states_period_and_clock(self):
        assert "【现实时间】现在是夜晚（22:30）。" in reality_line(self.NOW)

    def test_has_the_story_time_precedence_sentence_whole(self):
        assert _STORY in reality_line(self.NOW)

    def test_has_no_behavior_example(self):
        line = reality_line(self.NOW)
        assert "怎么还不睡" not in line and "自然融入" not in line


class TestIntervalNote:
    NOW = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)

    def test_one_minute_gap_says_nothing(self):
        assert interval_note(self.NOW, self.NOW - timedelta(seconds=60)) == ""

    def test_just_below_the_threshold_says_nothing(self):
        assert interval_note(self.NOW, self.NOW - timedelta(seconds=INTERVAL_GAP_SECONDS - 1)) == ""

    def test_at_the_threshold_speaks(self):
        assert "今天早些时候你们聊过。" in interval_note(
            self.NOW, self.NOW - timedelta(seconds=INTERVAL_GAP_SECONDS))

    def test_period_change_alone_does_not_speak(self):
        now = datetime(2026, 10, 14, 23, 2, tzinfo=timezone.utc)
        last = datetime(2026, 10, 14, 22, 58, tzinfo=timezone.utc)   # 夜晚 → 深夜，只隔 4 分钟
        assert interval_note(now, last) == ""

    def test_yesterday(self):
        assert "你们昨天聊过。" in interval_note(self.NOW, self.NOW - timedelta(days=1))

    def test_missing_activity_says_nothing(self):
        assert interval_note(self.NOW, None) == ""

    def test_isoformat_string_is_accepted(self):
        last = (self.NOW - timedelta(hours=12)).isoformat()
        assert "今天早些时候你们聊过。" in interval_note(self.NOW, last)

    def test_never_says_just_now(self):
        assert "刚刚还在聊" not in interval_note(self.NOW, self.NOW - timedelta(seconds=5))


# ── 接线：系统提示每轮都有现实时间；用户消息末尾只在隔了一段才有间隔句 ──────


@pytest.fixture
def _frozen_now(monkeypatch):
    now = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(UserClock, "now", staticmethod(lambda tz=None: now))
    return now


class TestEngineWiring:
    def test_system_prompt_has_reality_line_one_minute_later(self, _frozen_now):
        eng = _engine()
        eng.history = _GREETING + [{"role": "user", "content": "在吗"},
                                   {"role": "assistant", "content": "在"}]
        sp = eng._compose_system_prompt("现在几点了")
        assert "【现实时间】现在是夜晚（22:30）。" in sp and _STORY in sp

    def test_first_turn_with_greeting_in_history_has_reality_line(self, _frozen_now):
        """新会话第一轮：history 里只有开场白，现实时间照样在系统提示里。"""
        eng = _engine()
        eng.history = list(_GREETING)
        assert _REALITY in eng._compose_system_prompt("现在几点了")

    def test_user_message_tail_never_carries_the_reality_line(self, _frozen_now):
        eng = _engine()
        tail = _attached(eng, _GREETING + [{"role": "user", "content": "在吗"},
                                           {"role": "assistant", "content": "在"}],
                         _frozen_now - timedelta(hours=12))
        assert _REALITY not in tail

    def test_one_minute_later_tail_is_bare(self, _frozen_now):
        eng = _engine()
        assert _attached(eng, list(_GREETING), _frozen_now - timedelta(seconds=30)) == "现在"

    def test_twelve_hours_later_tail_has_the_interval(self, _frozen_now):
        eng = _engine()
        tail = _attached(eng, _GREETING + [{"role": "user", "content": "在吗"},
                                           {"role": "assistant", "content": "在"}],
                         _frozen_now - timedelta(hours=12))
        assert tail == "现在\n\n今天早些时候你们聊过。\n"

    def test_no_behavior_example_anywhere(self, _frozen_now):
        eng = _engine()
        eng.history = list(_GREETING)
        sp = eng._compose_system_prompt("在吗")
        tail = _attached(eng, list(_GREETING), _frozen_now - timedelta(hours=12))
        assert "怎么还不睡" not in sp + tail


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
