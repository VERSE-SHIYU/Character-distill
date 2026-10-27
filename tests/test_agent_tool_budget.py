"""工具执行预算的唯一出处：``tool_budget_s``。

聊天里 ``execute`` 给检索开的 embed deadline 与评测（``scripts/eval_recall.py``）用的
场景时限必须是同一个数 —— 两处各算一遍就会漂。这里锁三件事：公式、
``SCENE_TOOL_BUDGET_S`` 由它派生、``execute`` 真的走它。
"""

from __future__ import annotations

import time

import pytest

from core.agent import tools
from core.agent.tools import AgentToolkit
from core.embeddings import current_embed_deadline
from evidence_fakes import FakeCollection, build_ctx, make_rag


@pytest.mark.parametrize(
    ("timeout", "budget"),
    [(5, 5 - tools._EXECUTE_DEADLINE_MARGIN_S), (15, 15 - tools._EXECUTE_DEADLINE_MARGIN_S)],
)
def test_budget_is_timeout_minus_margin(timeout, budget):
    assert tools.tool_budget_s(timeout) == budget


@pytest.mark.parametrize("timeout", [tools._EXECUTE_DEADLINE_MARGIN_S, 0.5, 0])
def test_budget_none_when_not_positive(timeout):
    assert tools.tool_budget_s(timeout) is None


def test_scene_tool_budget_derives_from_scene_timeout():
    assert tools.SCENE_TOOL_BUDGET_S == tools.tool_budget_s(AgentToolkit.SCENE_TIMEOUT)
    assert tools.SCENE_TOOL_BUDGET_S == 4.0


def test_execute_takes_deadline_from_tool_budget(monkeypatch):
    """``execute`` 开的 deadline 必须来自 ``tool_budget_s``，而不是自己再算一遍。"""
    seen: list[float | None] = []
    monkeypatch.setattr(tools, "tool_budget_s", lambda timeout: 2.5)

    def handler(self, query):
        seen.append(current_embed_deadline())
        return self._ctx._retrieve_scenes_ex(query)

    monkeypatch.setattr(AgentToolkit, "_call_search_scenes", handler)
    before = time.monotonic()
    AgentToolkit(build_ctx(rag=make_rag(FakeCollection()))).execute(
        "search_scenes", {"query": "q"})
    after = time.monotonic()
    assert len(seen) == 1 and seen[0] is not None
    assert before + 2.5 <= seen[0] <= after + 2.5
