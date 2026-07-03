"""Tests for ChatEngine._build_life_exchange_block."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.chat_engine import ChatEngine
from core.schema import CharacterCard


def _make_engine(stage: str) -> ChatEngine:
    """Build minimal ChatEngine with given stage for life exchange testing."""
    card = CharacterCard(name="测试角色", identity="一个温柔的人")
    llm = MagicMock()
    rag = MagicMock()
    engine = ChatEngine(llm=llm, rag=rag, card=card)
    engine._session_id = "test-session"
    engine._stage = stage
    # Mock time awareness to avoid run_on_main_loop
    engine._build_time_awareness_block = MagicMock(return_value="")
    return engine


LIFE_EXCHANGE_MARKER = "对方没在聊生活时，不要硬塞"


@pytest.mark.parametrize("stage", ("陌生", "认识"))
def test_no_life_exchange_below_familiar(stage: str):
    engine = _make_engine(stage)
    block = engine._build_life_exchange_block()
    assert LIFE_EXCHANGE_MARKER not in block
    assert block == ""


@pytest.mark.parametrize("stage", ("熟悉", "朋友", "亲近", "心意相通"))
def test_life_exchange_at_familiar_and_above(stage: str):
    engine = _make_engine(stage)
    block = engine._build_life_exchange_block()
    assert LIFE_EXCHANGE_MARKER in block


def test_life_exchange_included_in_all_enhancements():
    """熟悉档以上，_build_all_enhancements 返回的字符串应含生活交换段。"""
    engine = _make_engine("熟悉")
    enhancements = engine._build_all_enhancements()
    assert LIFE_EXCHANGE_MARKER in enhancements


def test_no_life_exchange_in_all_enhancements_below_familiar():
    """陌生档，_build_all_enhancements 应不含生活交换段。"""
    engine = _make_engine("陌生")
    enhancements = engine._build_all_enhancements()
    assert LIFE_EXCHANGE_MARKER not in enhancements
