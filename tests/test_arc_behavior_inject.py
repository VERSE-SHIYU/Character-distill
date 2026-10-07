# -*- coding: utf-8 -*-
"""② 「情境→做法」注入（spec docs/specs/arc-behavior-inject.md）。

P 组：投影 —— 投影卡的 `situation_behaviors` = 阶段 k 的做法在前 + 全程做法在后。
C 组：卡片核心层 —— 「## 遇事的做法」位置、内容、空块不出现。
R 组：重注入 —— 每 4 个用户回合，把做法表作为临时块附在当前用户消息末尾。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import context_engine as ce_mod
from core.arc_view import project_card
from core.chat_engine import REINJECT_EVERY, ChatEngine, reinject_due
from core.context_engine import ContextEngine
from core.schema import CharacterCard


def _b(s: str, b: str) -> dict:
    return {"situation": s, "behavior": b, "source_quote": ""}


def _card(**kw) -> CharacterCard:
    """两阶段卡：全程做法 T；阶段 1 做法 A；阶段 2 做法 B；未定位做法 U。"""
    data = {
        "name": "甲",
        "personality_traits": ["多疑"],
        "situation_behaviors": [_b("全程情境", "全程做法")],
        "character_arc": {
            "axis": "从冷到热", "source_fingerprint": "FP",
            "phases": [
                {"label": "冷", "state": "起初", "start": 0,
                 "behaviors": [_b("早期情境", "早期做法")]},
                {"label": "热", "state": "后来", "start": 10,
                 "behaviors": [_b("后期情境", "后期做法")]},
            ],
            "unlocated": {"behaviors": [_b("未定位情境", "未定位做法")]},
        },
    }
    data.update(kw)
    return CharacterCard.model_validate(data)


def _situations(card) -> list[str]:
    return [b.situation for b in card.situation_behaviors]


# ── P 投影 ────────────────────────────────────────────────────────────────────

def test_p1_phase_k_behaviors_first_then_lifelong():
    assert _situations(project_card(_card(), 1)[0]) == ["早期情境", "全程情境"]
    assert _situations(project_card(_card(), 2)[0]) == ["后期情境", "全程情境"]


def test_p2_no_phase_arg_projects_to_last_phase():
    assert _situations(project_card(_card(), None)[0]) == ["后期情境", "全程情境"]


def test_p3_card_without_arc_keeps_only_lifelong():
    card = _card(character_arc={})
    assert _situations(project_card(card, None)[0]) == ["全程情境"]


def test_p4_unlocated_and_other_phases_never_projected():
    for k in (1, 2):
        sits = _situations(project_card(_card(), k)[0])
        assert "未定位情境" not in sits
        other = "后期情境" if k == 1 else "早期情境"
        assert other not in sits, f"阶段 {k} 的投影带上了别的阶段的做法"


def test_p5_original_card_untouched():
    card = _card()
    project_card(card, 1)
    assert _situations(card) == ["全程情境"]
    assert [[b.situation for b in p.behaviors] for p in card.character_arc.phases] == [
        ["早期情境"], ["后期情境"]]


def test_p6_phase_without_behaviors_keeps_lifelong():
    card = _card()
    card.character_arc.phases[0].behaviors = []
    assert _situations(project_card(card, 1)[0]) == ["全程情境"]


# ── C 卡片核心层 ──────────────────────────────────────────────────────────────

def _core(card, k):
    proj, view = project_card(card, k)
    return ContextEngine(card=proj, rag=None, storage=None, arc_view=view)._build_card_core()


def test_c1_section_lists_phase_and_lifelong_in_order():
    core = _core(_card(), 1)
    assert "\n## 遇事的做法\n- 早期情境 → 早期做法\n- 全程情境 → 全程做法\n" in core


def test_c2_section_sits_between_behavior_mode_and_speaking_style():
    core = _core(_card(), 1)
    assert core.index("## 行为模式") < core.index("## 遇事的做法") < core.index("## 语言风格")


def test_c3_no_behaviors_no_section():
    card = _card(situation_behaviors=[])
    card.character_arc.phases[0].behaviors = []
    assert "遇事的做法" not in _core(card, 1)


def test_c4_other_phase_and_unlocated_absent_from_prompt():
    core = _core(_card(), 1)
    assert "后期做法" not in core and "未定位做法" not in core


def test_c5_section_reaches_full_system_prompt():
    """build_ex 的产物（一对一、群聊、agent 都经它）带上这一块。"""
    proj, view = project_card(_card(), 2)
    prompt = ContextEngine(card=proj, rag=None, storage=None, arc_view=view).build_ex(
        "你好", include_dynamic=False).prompt
    assert "- 后期情境 → 后期做法" in prompt


def test_c6_sections_table_is_the_only_entry(monkeypatch):
    """③ 只在 `_CORE_SECTIONS` 加一行就能接入：追加一项即在「遇事的做法」后出现。"""
    monkeypatch.setattr(ce_mod, "_CORE_SECTIONS",
                        ce_mod._CORE_SECTIONS + (("想要什么", lambda c: "- 想要X"),))
    core = _core(_card(), 1)
    assert core.index("## 遇事的做法") < core.index("## 想要什么\n- 想要X") < core.index("## 语言风格")


# ── R 重注入 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prior,due", [(0, False), (1, False), (2, False), (3, False),
                                       (4, True), (5, False), (6, False), (7, False),
                                       (8, True), (12, True)])
def test_r1_reinject_due_boundaries(prior, due):
    assert reinject_due(prior) is due


def test_r1b_interval_is_four():
    assert REINJECT_EVERY == 4


class _CaptureLLM:
    model = "stub"

    def __init__(self):
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.sent: list[list[dict]] = []

    def preflight(self):
        return None

    def chat(self, system_prompt, messages, **_kw):
        self.sent.append([dict(m) for m in messages])
        return "回复"

    def chat_stream(self, system_prompt, messages, **_kw):
        self.sent.append([dict(m) for m in messages])
        yield "回复"


def _engine(card=None, arc_phase=1, llm=None):
    eng = ChatEngine(llm=llm or _CaptureLLM(), rag=None, card=card or _card(), card_id="c",
                     storage=None, session_id="", is_new_session=False, arc_phase=arc_phase)
    eng._build_time_awareness_block = lambda: "\n\n[时间]"
    return eng


def _history(user_turns: int, greeting: bool = True) -> list[dict]:
    h = [{"role": "assistant", "content": "开场白"}] if greeting else []
    for i in range(user_turns):
        h += [{"role": "user", "content": f"问{i}"}, {"role": "assistant", "content": f"答{i}"}]
    return h


_REMINDER = "\n\n【提醒：你遇事的做法】\n- 早期情境 → 早期做法\n- 全程情境 → 全程做法\n"


def _attached(eng, user_turns):
    eng.history = _history(user_turns)
    msgs = eng._build_llm_messages(eng.history, "现在")
    eng._attach_turn_blocks(msgs)
    return msgs[-1]["content"]


def test_r2_due_turn_appends_time_then_reminder():
    assert _attached(_engine(), 4) == "现在\n\n[时间]" + _REMINDER


def test_r3_not_due_turn_only_time():
    assert _attached(_engine(), 3) == "现在\n\n[时间]"
    assert _attached(_engine(), 5) == "现在\n\n[时间]"


def test_r4_due_but_no_behaviors_no_reminder():
    card = _card(situation_behaviors=[])
    card.character_arc.phases[0].behaviors = []
    assert _attached(_engine(card), 4) == "现在\n\n[时间]"


def test_r5_greeting_and_assistant_turns_do_not_count():
    """只数用户回合：开场白、主动消息都是角色说的，不算一轮。"""
    eng = _engine()
    eng.history = _history(4) + [{"role": "assistant", "content": "主动消息"}]
    msgs = eng._build_llm_messages(eng.history, "现在")
    eng._attach_turn_blocks(msgs)
    assert msgs[-1]["content"].endswith(_REMINDER)


def test_r6_reminder_uses_selected_phase():
    content = _attached(_engine(arc_phase=2), 4)
    assert "- 后期情境 → 后期做法" in content and "早期做法" not in content


@pytest.mark.parametrize("stream", [False, True])
def test_r7_chat_paths_send_reminder_but_history_keeps_raw_message(stream, monkeypatch):
    llm = _CaptureLLM()
    eng = _engine(llm=llm)
    monkeypatch.setattr(eng, "_post_turn", lambda *a, **k: None)
    eng.history = _history(4)
    if stream:
        list(eng.chat_stream("现在"))
    else:
        eng.chat("现在")
    assert llm.sent[-1][-1]["content"] == "现在\n\n[时间]" + _REMINDER, "这一轮没带上重注入"
    users = [m["content"] for m in eng.history if m["role"] == "user"]
    assert users[-1] == "现在", "重注入写进了对话记录"
    assert all("提醒：你遇事的做法" not in m["content"] for m in eng.history)


@pytest.mark.parametrize("stream", [False, True])
def test_r8_following_turn_does_not_repeat(stream, monkeypatch):
    llm = _CaptureLLM()
    eng = _engine(llm=llm)
    monkeypatch.setattr(eng, "_post_turn", lambda *a, **k: None)
    eng.history = _history(4)
    for msg in ("第五问", "第六问"):
        if stream:
            list(eng.chat_stream(msg))
        else:
            eng.chat(msg)
    assert "提醒：你遇事的做法" in llm.sent[0][-1]["content"]
    assert "提醒：你遇事的做法" not in llm.sent[1][-1]["content"]
    assert all("提醒：你遇事的做法" not in m["content"] for m in llm.sent[1][:-1]), (
        "上一轮的重注入留在了历史里")


# ── 审计补充：引擎级多次到期、群聊、agent（spec 补充 1–3）────────────────────

def test_r9_engine_reinjects_at_turn_5_9_13_only(monkeypatch):
    """连聊 13 句：只有第 5、9、13 句带提醒（纯函数之外，钉住引擎级的周期绑定）。"""
    llm = _CaptureLLM()
    eng = _engine(llm=llm)
    monkeypatch.setattr(eng, "_post_turn", lambda *a, **k: None)
    eng.history = [{"role": "assistant", "content": "开场白"}]
    for i in range(13):
        eng.chat(f"第{i + 1}句")
    hit = [i + 1 for i, msgs in enumerate(llm.sent) if "提醒：你遇事的做法" in msgs[-1]["content"]]
    assert hit == [5, 9, 13], f"提醒落在第 {hit} 句"


class _AsyncCaptureLLM(_CaptureLLM):
    def __init__(self):
        super().__init__()
        self.systems: list[str] = []

    async def achat(self, system_prompt, messages, **_kw):
        self.systems.append(system_prompt)
        self.sent.append([dict(m) for m in messages])
        return "回复"


def test_g1_group_chat_has_section_but_no_reinjection():
    """群聊：system prompt 带最后阶段的做法块；多轮之后也不重注入（§9：本轮群聊不重注入）。"""
    import asyncio

    from core.group_session import GroupSession

    llm = _AsyncCaptureLLM()
    eng = ChatEngine(llm=llm, rag=None, card=_card(), card_id="c1", storage=None,
                     session_id="", is_new_session=True)
    session = GroupSession(id="g1", engines={"c1": eng}, storage=None, user_id="u1")

    async def _run():
        for i in range(9):
            await session.send("c1", f"第{i + 1}句")

    asyncio.run(_run())
    assert all("## 遇事的做法\n- 后期情境 → 后期做法\n- 全程情境 → 全程做法\n" in sp
               for sp in llm.systems), "群聊 system prompt 没有最后阶段的做法块"
    blob = "".join(llm.systems) + "".join(m["content"] for msgs in llm.sent for m in msgs)
    assert "提醒：你遇事的做法" not in blob, "群聊被重注入了"


def test_a1_agent_path_carries_section_and_reminder(monkeypatch):
    """agent 模式：最终那次调用的 system prompt 有做法块，第 5 句的消息带提醒。"""
    from core.agent import agent_loop

    llm = _CaptureLLM()
    systems: list[str] = []
    orig_chat = llm.chat

    def _chat(system_prompt, messages, **kw):
        systems.append(system_prompt)
        return orig_chat(system_prompt, messages, **kw)

    llm.chat = _chat
    monkeypatch.setattr(agent_loop.AgentLoop, "run", lambda self, hint, messages: agent_loop.AgentLoopResult(
        messages=messages, steps=[], degraded=False))
    eng = _engine(llm=llm)
    eng.agent_mode = True
    monkeypatch.setattr(eng, "_post_turn", lambda *a, **k: None)
    eng.history = _history(4)
    eng.chat("现在")
    assert "## 遇事的做法\n- 早期情境 → 早期做法\n- 全程情境 → 全程做法\n" in systems[-1]
    assert llm.sent[-1][-1]["content"].endswith(_REMINDER)
