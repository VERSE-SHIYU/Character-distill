# -*- coding: utf-8 -*-
"""联网开关对**工具模式**生效（spec: docs/specs/agent-web-toggle.md 目标检查 1-4）。

关：清单里没有 web_search、执行走「未知工具」出口、一个网络请求都不发；
开：与既有 web 用例同款行为；清单**每次现读**开关 —— 构造之后再改也立刻生效。
"""

from __future__ import annotations

from core.agent.agent_loop import AgentLoop
from core.agent.tools import AgentToolkit
from evidence_fakes import DDG_ABSTRACT, FakeLLM, build_ctx, fake_ddg
from test_agent_evidence import ToolLLM

QUERY = "莲花坞里的旧事"
HINT = "角色背景提示"


def _names(toolkit: AgentToolkit) -> list[str]:
    return [s["function"]["name"] for s in toolkit.get_schemas()]


def test_web_off_hides_web_search_and_blocks_execution():
    """检查 1：关 → 清单无 web_search；直接 execute 走「未知工具」，不发网络请求。"""
    ctx = build_ctx()  # 开关默认关 —— 与生产同源（core/context_engine.py:235）
    assert ctx.web_search_enabled is False
    toolkit = AgentToolkit(ctx)

    assert _names(toolkit) == ["search_scenes", "search_memory"]

    with fake_ddg(DDG_ABSTRACT) as calls:
        res = toolkit.execute("web_search", {"query": QUERY})
    assert res.trace is None  # 没发生检索，不是「检索了但空」
    assert res.content == "未知工具: web_search"
    assert calls == []


def test_web_on_keeps_schemas_and_execution():
    """检查 2：开 → 清单里有 web_search，执行照常（检索+改写都跑）。"""
    ctx = build_ctx(llm=FakeLLM(), web=True)
    toolkit = AgentToolkit(ctx)

    assert _names(toolkit) == ["search_scenes", "search_memory", "web_search"]

    with fake_ddg(DDG_ABSTRACT) as calls:
        res = toolkit.execute("web_search", {"query": QUERY})
    assert res.trace is not None and res.trace.status == "hit"
    assert len(res.trace.items) == 1
    assert len(calls) == 1


def test_agent_round_with_switch_off_never_reaches_web():
    """检查 3：工具模式整轮 —— 开关关、路由替身硬请求 web_search。

    模型看不到这个工具却仍然点了名：执行走「未知工具」，``retrieved`` 里没有它的块
    （ChatEngine 拼「[联网]」块只读 retrieved），网络调用 0 次。
    """
    ctx = build_ctx()
    llm = ToolLLM([[("web_search", {"query": QUERY})]])
    with fake_ddg(DDG_ABSTRACT) as calls:
        res = AgentLoop(llm, AgentToolkit(ctx)).run(HINT, [{"role": "user", "content": "hi"}])

    assert res.retrieved == []
    assert res.evidence == []
    assert calls == []


def test_switch_is_read_live_not_cached_at_construction():
    """检查 4：同一个 toolkit 构造后改开关 → 清单跟着变（现读，不构造时定死）。"""
    ctx = build_ctx()
    toolkit = AgentToolkit(ctx)
    assert "web_search" not in _names(toolkit)

    ctx.web_search_enabled = True
    assert "web_search" in _names(toolkit)

    ctx.web_search_enabled = False
    assert "web_search" not in _names(toolkit)
    with fake_ddg(DDG_ABSTRACT) as calls:
        res = toolkit.execute("web_search", {"query": QUERY})
    assert res.content == "未知工具: web_search"
    assert calls == []
