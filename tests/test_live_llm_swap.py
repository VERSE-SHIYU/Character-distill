# -*- coding: utf-8 -*-
"""72 线 · 第 3 步：换连接只换「下一次出站」，在飞的那一轮归旧实例。

用户存完 model / base_url / key 之后，活会话从**下一轮**起用新连接 —— 不踢会话、不重建
RAG。本文件锁这条链的下半截（引擎侧的换与归属）；上半截（deps 把新实例发给活会话）在
``tests/test_live_llm_refresh.py``。四件事各一条红源：

- T1 ``ContextEngine.set_llm`` 按**新**模型重算五档预算（改前没有这个方法）
- T2 ``ChatEngine.set_llm`` 把 ContextEngine 一起换（只换 ``self.llm`` 是半换）
- T3a 流式中途换连接：**已经在飞**的那一轮仍归旧实例
- T3b 群聊 ``await`` 期间换连接：同 T3a

判据不落在「调没调到 set_llm」上 —— 那是实现。落在两个用户可见的事实上：预算档位
（决定长对话会不会被提前截断）与那笔 usage 落在谁头上（用量面板按模型分档）。

记账不落库：把 ``core.chat_engine.try_record_usage`` 换成同步记录器 —— 它是
``_try_record_usage`` 的唯一被调下游，主路径上就是从 ``core.utils`` 绑进来的同一个名字
（与 ``tests/test_distill_usage_accounting.py`` 同法）。post-turn 那条链（好感评估 /
反思）在 ``core.evaluation_pipeline`` 里自己绑了一份名字，不经过这个记录器。
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys

from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.chat_engine import ChatEngine
from core.context_engine import ContextEngine
from core.group_session import GroupSession
from core.schema import CharacterCard

# 预算表是**精确查表**（`MODEL_BUDGET_MAP.get(model)`），故桩的模型名必须恰好等于字典键：
# 32000 / 24000 两档才分得开。写 "claude-sonnet-4-20250514" 会落到未知回退 32000，
# 与新值相同，T1 就恒绿了。
OLD_MODEL = "deepseek-v4-pro"   # 32000
NEW_MODEL = "claude-sonnet"     # 24000

CARD = CharacterCard(name="角色", identity="测试用角色")


class _StubLLM:
    """假连接：构造 ContextEngine 要读的 ``model``、落账要读的 ``last_usage``、三个出站方法。

    两个实例的 ``last_usage`` 给**不同的值**：落账落的是谁家的 usage 是用户看得见的那件事
    （用量面板按模型分档），断言它就分得出「在飞的那轮归了谁」。只断言实例身份是机制，
    断言 usage 值才是后果。
    """

    def __init__(self, model: str, prompt_tokens: int, pieces: tuple[str, ...] = ("甲", "乙")):
        self._model = model
        self._pieces = list(pieces)
        self.last_usage = {"prompt_tokens": prompt_tokens, "completion_tokens": 1}
        self.calls: list[str] = []

    @property
    def model(self) -> str:
        return self._model

    def preflight(self) -> None:
        return None

    def chat(self, *_a, **_kw) -> str:
        self.calls.append("chat")
        return "固定回复"

    def chat_stream(self, *_a, **_kw):
        self.calls.append("chat_stream")
        yield from self._pieces

    async def achat(self, *_a, **_kw) -> str:
        self.calls.append("achat")
        return "固定回复"


class _InFlightLLM(_StubLLM):
    """``achat`` 停在闸门上 —— 让用例能在「调用已发出、还没回来」的那一瞬换连接。

    真身是网络调用，await 期间事件循环在跑别人（保存设置正是其中之一）；这里把那个
    窗口显式化，才不用靠计时碰运气。
    """

    def __init__(self, model: str, prompt_tokens: int):
        super().__init__(model, prompt_tokens)
        self.entered = asyncio.Event()
        self.hold = asyncio.Event()

    async def achat(self, *_a, **_kw) -> str:
        self.calls.append("achat")
        self.entered.set()
        await self.hold.wait()
        return "固定回复"


@pytest.fixture
def recorded(monkeypatch):
    """收下 ``_try_record_usage`` 每次发出的 (action, llm, usage)。

    记录器**照真出口的签名**收参数（``inspect.signature(try_record_usage).bind``）：参数名
    写错时当场炸，而不是静默收不到东西、让「一条都没记」看起来像通过。
    """
    from core.utils import try_record_usage

    sink: list[dict] = []

    def _rec(*args, **kwargs):
        bound = inspect.signature(try_record_usage).bind(*args, **kwargs)
        llm = bound.arguments["llm"]
        usage = bound.arguments["usage"]
        # 照真出口那一行回落（``if usage is None: usage = llm.last_usage``）—— 不回落的话
        # 记录器收到的恒是 None，usage 值这条判据就空转，只剩实例身份。
        sink.append({
            "action": bound.arguments["action"],
            "llm": llm,
            "usage": llm.last_usage if usage is None else usage,
        })

    monkeypatch.setattr("core.chat_engine.try_record_usage", _rec)
    return sink


def _engine(llm: _StubLLM) -> ChatEngine:
    """最小可用引擎：``rag=None`` 让检索降级成空块（``_retrieve_via`` 自己吞），
    ``storage=None`` 关掉好感落库那条下游 —— 本文件测的不是它们。"""
    return ChatEngine(llm=llm, rag=None, card=CARD, card_id="c1", storage=None)


# ═══════════════════════════════════════════════════════════════════════════════
# T1–T2：换连接这个动作本身
# ═══════════════════════════════════════════════════════════════════════════════

def test_T1_set_llm_recomputes_budgets_for_the_new_model():
    """换连接后预算按新模型重算 —— 端到端观测量是那五档 token 数。

    期望值是**手算的字面量**（32000×0.40=12800、24000×0.40=9600 …），不在这儿重跑一遍
    生产公式：重跑一遍的话公式改错了两边一起错，这条就永远不会红。
    """
    old, new = _StubLLM(OLD_MODEL, 11), _StubLLM(NEW_MODEL, 22)
    ctx = ContextEngine(card=CARD, rag=None, llm=old, model=OLD_MODEL, storage=MagicMock())
    assert (ctx.TOTAL_BUDGET, ctx.MAX_HISTORY, ctx.MAX_SCENE,
            ctx.MAX_MEMORY, ctx.MAX_CARD_EXT) == (32000, 12800, 8000, 1920, 2560)

    ctx.set_llm(new)

    assert ctx._llm is new
    assert (ctx.TOTAL_BUDGET, ctx.MAX_HISTORY, ctx.MAX_SCENE,
            ctx.MAX_MEMORY, ctx.MAX_CARD_EXT) == (24000, 9600, 6000, 1440, 1920), (
        "换成了 24k 的模型，预算还按旧模型算 —— 长对话会被提前截断，用户只会觉得角色变健忘")


def test_T2_chat_engine_swaps_the_context_engine_too():
    """``ChatEngine.set_llm`` 必须把 ContextEngine 一起换。

    只换 ``self.llm`` 是半换：出站走了新连接，而 ContextEngine 手里那份旧实例还在管
    web 过滤那次调用、还按旧模型算预算 —— 一个引擎指着两个连接。
    """
    old, new = _StubLLM(OLD_MODEL, 11), _StubLLM(NEW_MODEL, 22)
    engine = _engine(old)
    assert engine._ctx_engine._llm is old

    engine.set_llm(new)

    assert engine.llm is new
    assert engine._ctx_engine._llm is new, "ContextEngine 还拿着旧实例（web 过滤与预算仍是旧的）"
    assert engine._ctx_engine.TOTAL_BUDGET == 24000


# ═══════════════════════════════════════════════════════════════════════════════
# T3a–T3b：在飞的那一轮归旧连接
# ═══════════════════════════════════════════════════════════════════════════════

def test_T3a_in_flight_stream_records_on_the_old_connection(recorded):
    """流式中途换连接：本轮**已发出**的那次调用归旧实例。

    红源只能是流式：非流式 ``chat()`` 是同步阻塞调用，出站与落账之间没有可插入换连接的
    await 点；``chat_stream`` 在 yield 处让出，调用方 await 的那段时间正是一次保存能跑完的
    窗口 —— 出站用旧实例、落账却重读 ``self.llm``，那笔 usage 就记到了新连接头上。
    """
    old, new = _StubLLM(OLD_MODEL, 11), _StubLLM(NEW_MODEL, 22)
    engine = _engine(old)

    gen = engine.chat_stream("你好")
    assert next(gen) == "甲"        # 出站已发生、第一块已吐 —— 调用此刻在飞
    engine.set_llm(new)             # 保存设置落地
    assert list(gen) == ["乙"]

    chats = [r for r in recorded if r["action"] == "chat"]
    assert len(chats) == 1, f"本轮应恰记一笔 chat，实得 {[r['action'] for r in recorded]}"
    assert chats[0]["llm"] is old, "在飞的这轮被记到了新连接上"
    assert chats[0]["usage"]["prompt_tokens"] == 11, (
        "usage 取的是新连接的 last_usage —— 用量面板会把这笔算到还没用过的模型头上")


def test_T3b_group_round_records_on_the_connection_it_was_issued_on(recorded):
    """群聊同理：``await engine.llm.achat(...)`` 期间换连接，回来时重读 ``engine.llm``
    会把这一笔记到新实例头上（旧实例的 ``last_usage`` 才是这次回复的）。"""
    async def _run():
        old = _InFlightLLM(OLD_MODEL, 11)
        engine = _engine(old)
        session = GroupSession(id="g1", engines={"c1": engine}, storage=None, user_id="u1")

        task = asyncio.create_task(session.send("c1", "你好"))
        await old.entered.wait()          # 调用已发出，还没回来
        engine.set_llm(_StubLLM(NEW_MODEL, 22))
        old.hold.set()
        assert await task == "固定回复"

    asyncio.run(_run())

    chats = [r for r in recorded if r["action"] == "chat"]
    assert len(chats) == 1, f"本轮应恰记一笔 chat，实得 {[r['action'] for r in recorded]}"
    assert chats[0]["llm"] is not None and chats[0]["llm"].model == OLD_MODEL, (
        "群聊这一轮记到了新连接上")
    assert chats[0]["usage"]["prompt_tokens"] == 11
