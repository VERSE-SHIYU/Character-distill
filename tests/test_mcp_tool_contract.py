# -*- coding: utf-8 -*-
"""MCP 适配层的两条契约（spec: docs/specs/agent-web-toggle.md 目标检查 5-6）。

仓库里除本文件没有别的 MCP 测试（``grep -rn "_tool_specs\\|list_tools\\|_make_toolkit" tests``
原本零命中）—— MCP 被改坏 CI 不会红，这两条是它唯一的锁：

  5. 静态列表（``ctx_engine=None``）仍列 3 个工具、每个带必填 card_id，且不因 ctx 为
     None 报错 —— 联网开关不许漏到这条不绑用户开关的路径上；
  6. MCP 执行仍能联网 —— 建引擎处显式开着开关。
"""

from __future__ import annotations

import pytest

import mcp_server.server as S
from core.arc_view import project_card
from evidence_fakes import DDG_ABSTRACT, fake_ddg, make_card


@pytest.fixture
def _isolated_mcp(monkeypatch):
    """MCP 的 toolkit/storage 是模块级全局 —— 清干净，且不真连库、不真挂 LLM。"""
    monkeypatch.setattr(S, "_storage", None)
    monkeypatch.setattr(S, "_toolkit_by_card_id", {})
    monkeypatch.setattr(S, "_rag_by_text_id", {})
    monkeypatch.setattr(S, "_get_storage", lambda: object())
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)  # → _lazy_llm 返回 None


def test_static_tool_list_still_has_three_tools_with_card_id(_isolated_mcp):
    """检查 5：静态列表不看开关、不需要 ctx。"""
    specs = S._tool_specs()

    assert [t.name for t in specs] == ["search_scenes", "search_memory", "web_search"]
    for t in specs:
        assert t.inputSchema["required"] == ["query", "card_id"]


def test_mcp_toolkit_can_still_web_search(_isolated_mcp):
    """检查 6：MCP 建出来的 toolkit 联网可用（它没有用户开关，这就是它的事实）。

    传**投影卡**：`ContextEngine` 只收 `ProjectedCard`（core/arc_view.py:47）。
    """
    card = project_card(make_card(), None)[0]
    toolkit = S._make_toolkit(card, rag=None)

    with fake_ddg(DDG_ABSTRACT) as calls:
        res = toolkit.execute("web_search", {"query": "莲花坞"})

    assert len(calls) == 1  # 真检索了，不是「未知工具」
    assert res.trace is not None and res.trace.status == "hit"
