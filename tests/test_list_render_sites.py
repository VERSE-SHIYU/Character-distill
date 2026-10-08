# -*- coding: utf-8 -*-
"""「一条一行 `- 内容`」两处输出点的表征测试（③ 段 3 §5.1 U4/U5）。

两处此前没有测试守着：卡片扩展层的【关键记忆】/【人际关系】，与提及之人那块。
段 3 把这两处的行拼接收拢进 `bullet_lines`（`core/context_engine.py`），
本文件在**改动之前**就写下期望输出，改动之后仍绿 —— 它证明重构没改字节。

- U4：扩展层 `_build_card_ext()`。人际关系**态度留空时不带冒号**。
- U5：`ChatEngine._build_relationship_block()`。命中的关系行最多 3 条。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.arc_view import project_card
from core.chat_engine import ChatEngine
from core.context_engine import ContextEngine
from core.schema import CharacterCard


def _block(text: str, marker: str) -> str:
    """切出以 marker 开头的那一块（扩展层各块用空行分隔）。"""
    for part in text.split("\n\n"):
        if part.startswith(marker):
            return part
    raise AssertionError(f"没找到块 {marker!r}：{text!r}")


# ── U4 扩展层 ─────────────────────────────────────────────────────────────────

def _ext_card() -> CharacterCard:
    return CharacterCard(
        name="甲",
        key_memories=["记忆甲", "记忆乙"],
        relationships=[
            {"target": "老王", "relation": "邻居", "attitude": "客气"},
            {"target": "小李", "relation": "同事", "attitude": ""},
        ],
    )


def _ext(card: CharacterCard) -> str:
    proj, view = project_card(card, None)
    return ContextEngine(card=proj, rag=None, storage=None, arc_view=view)._build_card_ext()


def test_u4_extension_layer_lines():
    ext = _ext(_ext_card())
    assert _block(ext, "【关键记忆】") == "【关键记忆】\n- 记忆甲\n- 记忆乙"
    assert _block(ext, "【人际关系】") == "【人际关系】\n- 老王（邻居）：客气\n- 小李（同事）"


def test_u4_relationship_without_attitude_has_no_colon():
    ext = _ext(_ext_card())
    assert "- 小李（同事）：" not in ext
    assert "- 小李（同事）\n" in ext + "\n"


# ── U5 提及之人 ───────────────────────────────────────────────────────────────

def _mention_engine(rels: list[dict], history_text: str) -> ChatEngine:
    card = CharacterCard(name="甲", relationships=rels)
    eng = ChatEngine(llm=None, rag=None, card=card, card_id="c", storage=None,
                     session_id="", is_new_session=False)
    eng.history = [{"role": "user", "content": history_text}]
    return eng


def test_u5_mentioned_person_lines():
    eng = _mention_engine(
        [{"target": "老王", "relation": "邻居", "attitude": "客气"}], "老王昨天来过")
    blk = eng._build_relationship_block()
    assert "- 对老王：邻居，客气\n" in blk


def test_u5_at_most_three_lines():
    rels = [{"target": f"甲{i}", "relation": "邻居", "attitude": "客气"} for i in range(1, 5)]
    eng = _mention_engine(rels, "甲1 甲2 甲3 甲4 都来了")
    lines = [ln for ln in eng._build_relationship_block().split("\n") if ln.startswith("- ")]
    assert len(lines) == 3, lines
    assert lines == ["- 对甲1：邻居，客气", "- 对甲2：邻居，客气", "- 对甲3：邻居，客气"]
