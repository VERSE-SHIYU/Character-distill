"""目标检查（D20 第 1 步）：评测传给路由的角色背景与生产一致（先红）。

测的是「达成目标」，不是「符合 spec」：

1. 有卡行：同一张卡、同一轮，「评测传给 ``AgentLoop.run`` 的角色背景」与「生产
   ``ChatEngine`` 走到 ``_run_agent_phase`` 时传给 ``AgentLoop.run`` 的角色背景」，
   截断到路由实际看到的前 300 字逐字相同；且评测侧不是裸角色名。
2. 无卡行：评测用 ``CharacterCard(name=character)`` 这张最小卡走生产同一组装函数；
   结果不等于裸角色名，且前 300 字与「最小卡直接走生产 ChatEngine」逐字相同。

生产侧用真实 ``ChatEngine.chat()``（LLM / 存储替身），路由侧用真实 ``run_eval`` /
``run_eval_no_phase``；两侧都只把 ``AgentLoop.run`` 换成记录第一参数的 spy。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from core.agent.agent_loop import AgentLoop, AgentLoopResult  # noqa: E402
from core.chat_engine import ChatEngine  # noqa: E402
from core.schema import CharacterCard  # noqa: E402
from evidence_fakes import make_card  # noqa: E402

QUERY = "你还记得满月宴那天吗"
SOURCE = "满月宴那天祈灼在廊下听见了那支曲子"
CASE = {"id": "q1", "phase": None, "query": QUERY, "should": [], "should_not": []}


class _StubLLM:
    """生产侧只要 ``chat`` 返回一句；路由侧 ``AgentLoop.run`` 已被 spy 截住，不调 LLM。"""

    model = "deepseek-flash"
    last_usage = None

    def chat(self, system_prompt, messages):
        return "好。"

    def chat_with_tools(self, system_prompt, messages, schemas):
        return types.SimpleNamespace(content="", tool_calls=None)


@pytest.fixture
def captured_hints(monkeypatch):
    hints: list[str] = []

    def fake_run(self, character_hint, messages):
        hints.append(character_hint)
        return AgentLoopResult(messages=list(messages), steps=[], degraded=False)

    monkeypatch.setattr(AgentLoop, "run", fake_run)
    return hints


def _production_hint(captured_hints: list[str], card: CharacterCard) -> str:
    """真实 ChatEngine 走 chat() → _run_agent_phase，spy 截到的第一个参数。"""
    engine = ChatEngine(
        llm=_StubLLM(), rag=None, card=card, storage=None,
        session_id="", is_new_session=True,
    )
    engine.agent_mode = True
    engine.affinity_enabled = False
    engine.chat(QUERY)
    return captured_hints[-1]


def _rag():
    return types.SimpleNamespace(chunk_spans=lambda _n: {})


@pytest.mark.xfail(strict=True, reason="D20 第 1 步未实现：评测只传裸角色名")
def test_card_row_hint_matches_production(captured_hints):
    import scripts.eval_recall as ev

    card = make_card()
    prod_hint = _production_hint(captured_hints, card)

    ev.run_eval(SOURCE, card, [CASE], _rag(), router_llm=_StubLLM())
    eval_hint = captured_hints[-1]

    assert eval_hint != card.name, "评测仍只传裸角色名"
    assert eval_hint[:300] == prod_hint[:300], "路由看到的前 300 字与生产不一致"


@pytest.mark.xfail(strict=True, reason="D20 第 1 步未实现：无卡行只传裸角色名")
def test_no_card_row_hint_uses_minimal_card(captured_hints):
    import scripts.eval_recall as ev

    character = "祈灼"
    prod_hint = _production_hint(captured_hints, CharacterCard(name=character))

    ev.run_eval_no_phase(SOURCE, character, [CASE], _rag(), router_llm=_StubLLM())
    eval_hint = captured_hints[-1]

    assert eval_hint != character, "无卡行仍只传裸角色名"
    assert eval_hint[:300] == prod_hint[:300], "无卡行没有走生产同一组装模板"
