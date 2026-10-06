# -*- coding: utf-8 -*-
"""§6.2 E11–E14、E18：4 个蒸馏入口与苏醒台词都过同一个关系分批钩子与同一个开场白模块。

这四个入口的「草稿 → overlay / 经历 1..k / 关系口径」由 Commit 3/4 的 `card_from_draft`
用例覆盖（U11/U7）；这里只钉**调用点**：每个入口在 `card_from_draft` 之前都调了
`_relationships_batched`（关系不截断不丢人），苏醒台词改走 `core/opening.py`。
"""

from __future__ import annotations

import ast
from pathlib import Path

from core.schema import ArcPhase, CharacterArc, CharacterCard

_REPO = Path(__file__).resolve().parent.parent


def _entry_sources(name: str) -> list[str]:
    text = (_REPO / "core" / "distiller.py").read_text(encoding="utf-8")
    return [ast.get_source_segment(text, n) or ""
            for n in ast.walk(ast.parse(text))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


def _assert_batched(name: str) -> None:
    srcs = _entry_sources(name)
    assert srcs, f"distiller.py 里找不到入口 {name}"
    assert any("_relationships_batched(" in s for s in srcs), f"{name} 未走关系分批"


# ── E11 `distill` ──────────────────────────────────────────────────────────
def test_entry_distill_fields():
    _assert_batched("distill")


# ── E12 `_distill_longcontext` ─────────────────────────────────────────────
def test_entry_longctx_fields():
    _assert_batched("_distill_longcontext")


# ── E13 `distill_incremental` ──────────────────────────────────────────────
def test_entry_incremental_fields():
    _assert_batched("distill_incremental")


# ── E14 `distill_incremental_stream`（一次读完 / 分组两支） ──────────────────
def test_entry_stream_fields():
    _assert_batched("distill_incremental_stream")


# ── E18 苏醒台词走 `core/opening.py`（最后阶段投影） ─────────────────────────
def test_awakening_projected():
    from core.arc_view import project_card
    from core.opening import build_awakening_prompt

    c = CharacterCard(name="甲", identity="身份", first_message="惯常开场白",
                      character_arc=CharacterArc(phases=[ArcPhase(state="一"),
                                                         ArcPhase(state="二")]))
    prompt = build_awakening_prompt(project_card(c, None)[0])
    assert "惯常开场白" in prompt

    src = (_REPO / "web" / "routers" / "distill.py").read_text(encoding="utf-8")
    assert "build_awakening_prompt" in src or "core.opening" in src, (
        "苏醒台词未走 core.opening")
