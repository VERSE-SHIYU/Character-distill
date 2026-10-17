# -*- coding: utf-8 -*-
"""MCP 适配层契约（spec: docs/specs/mcp-and-eval-followups.md）。

仓库里除本文件没有别的 MCP 测试（``grep -rn "_tool_specs\\|list_tools\\|_make_toolkit" tests``
原本零命中）—— MCP 被改坏 CI 不会红，这几条是它唯一的锁：

  1. 真实路径（原卡 JSON → ``_toolkit_for``）建得出引擎、工具能执行；
  2. MCP 无会话 → 卡投影到**最后**阶段（与苏醒台词 / 市场等无会话处同口径）；
  5. 静态列表（``ctx_engine=None``）仍列 3 个工具、每个带必填 card_id，且不因 ctx 为
     None 报错 —— 联网开关不许漏到这条不绑用户开关的路径上；
  6. MCP 执行仍能联网 —— 建引擎处显式开着开关。
"""

from __future__ import annotations

import asyncio

import pytest

import mcp_server.server as S
from core.arc_view import ProjectedCard
from core.card_layers import set_path
from core.schema import ArcPhase, CharacterArc
from evidence_fakes import DDG_ABSTRACT, fake_ddg, make_card


@pytest.fixture
def _isolated_mcp(monkeypatch):
    """MCP 的 toolkit/storage 是模块级全局 —— 清干净，且不真连库、不真挂 LLM。"""
    monkeypatch.setattr(S, "_storage", None)
    monkeypatch.setattr(S, "_toolkit_by_card_id", {})
    monkeypatch.setattr(S, "_rag_by_text_id", {})
    monkeypatch.setattr(S, "_get_storage", lambda: object())
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)  # → _lazy_llm 返回 None


class _StubStorage:
    """MCP 只有一次无身份读：按 card_id 取卡（``server.py:177`` ``get_card_unscoped``）。

    返回**原卡** JSON —— 与生产同源（``server.py:168`` 从 ``card_json`` 解析）；投影该由
    MCP 自己补，替身不许替它补（补了这条检查就绕开了被测的那一步）。
    """

    def __init__(self, card_json: str) -> None:
        self._card_json = card_json

    async def get_card_unscoped(self, card_id: str) -> dict | None:
        return {"card_json": self._card_json, "text_id": ""}


def test_toolkit_for_builds_from_raw_card_json(_isolated_mcp, monkeypatch):
    """检查 1：真实路径不报错、工具真能用。

    修之前这条必红：``ContextEngine`` 只收投影卡（``core/context_engine.py:228``
    ``require_projected``），而 MCP 把存储里解析出的**原卡**直接喂进去 → TypeError。
    """
    monkeypatch.setattr(
        S, "_get_storage", lambda: _StubStorage(make_card().model_dump_json()))

    toolkit = asyncio.run(S._toolkit_for("card-1"))

    assert isinstance(toolkit._ctx.card, ProjectedCard)
    # text_id 为空 → rag=None → 空检索（不发网络请求），但工具确实存在且跑完了
    res = toolkit.execute("search_scenes", {"query": "x"})
    assert res.trace is not None and res.trace.source == "scene"


def test_mcp_engine_projects_card_to_last_phase(_isolated_mcp):
    """检查 2：MCP 没有会话、不选阶段 → 最后阶段（不是第一段）。"""
    card = make_card()
    card.character_arc = CharacterArc(
        axis="从A到B",
        phases=[ArcPhase(label="P1", state="一"), ArcPhase(label="P2", state="二")],
        source_fingerprint="fp",
    )
    set_path(card.character_arc.phases[0].overlay, "personality_traits", ["早期"])
    set_path(card.character_arc.phases[1].overlay, "personality_traits", ["后期"])

    proj = S._make_toolkit(card, rag=None)._ctx.card

    assert "后期" in proj.personality_traits
    assert "早期" not in proj.personality_traits


def test_static_tool_list_still_has_three_tools_with_card_id(_isolated_mcp):
    """检查 5：静态列表不看开关、不需要 ctx。"""
    specs = S._tool_specs()

    assert [t.name for t in specs] == ["search_scenes", "search_memory", "web_search"]
    for t in specs:
        assert t.inputSchema["required"] == ["query", "card_id"]


def test_mcp_toolkit_can_still_web_search(_isolated_mcp):
    """检查 6：MCP 建出来的 toolkit 联网可用（它没有用户开关，这就是它的事实）。

    传**原卡** —— 生产里 ``_make_toolkit`` 收到的就是它（``server.py:174``）。
    """
    toolkit = S._make_toolkit(make_card(), rag=None)

    with fake_ddg(DDG_ABSTRACT) as calls:
        res = toolkit.execute("web_search", {"query": "莲花坞"})

    assert len(calls) == 1  # 真检索了，不是「未知工具」
    assert res.trace is not None and res.trace.status == "hit"
