# -*- coding: utf-8 -*-
"""§6.3 结构锁 S1–S12：每条锁钉一个「只许有一处」的实现点。

判据是**源码文本 / AST**，不是运行时行为 —— 行为由 §6.1/§6.2 覆盖，这里只保证
「只有一份」不被下一次改动悄悄破坏。先红后绿：本段开始时这些点都还不存在。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CORE = _REPO / "core"
_WEB = _REPO / "web"
_BOTH = [_CORE, _WEB]


def _py(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _hits(root: Path | list[Path], pattern: str) -> list[tuple[str, int]]:
    rx = re.compile(pattern)
    roots = root if isinstance(root, list) else [root]
    out = []
    for r in roots:
        for p in _py(r):
            for n, line in enumerate(_read(p).splitlines(), 1):
                if rx.search(line):
                    out.append((str(p.relative_to(_REPO)).replace("\\", "/"), n))
    return out


def _files(root: Path | list[Path], pattern: str) -> set[str]:
    return {f for f, _ in _hits(root, pattern)}


def _funcs(path: Path) -> dict[str, str]:
    text = _read(path)
    return {
        n.name: (ast.get_source_segment(text, n) or "")
        for n in ast.walk(ast.parse(text))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _imports(path: Path) -> set[str]:
    mods: set[str] = set()
    for n in ast.walk(ast.parse(_read(path))):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0] or "core")
    return mods


# ── S1 登记表 = `CharacterCard` 叶子全集（与 U1 同源，这里只锁「定义在 card_layers」） ──
def test_s1_registry_lives_in_card_layers_only():
    defs = _hits(_CORE, r"^REGISTRY\s*(:|=)")
    assert [f for f, _ in defs] == ["core/card_layers.py"], f"REGISTRY 应只在 card_layers 定义一次：{defs}"


# ── S2 `project_card` 不出现具体字段名分支（custom 除外） ──────────────────────
def test_s2_project_card_has_no_per_field_branch():
    from core.card_layers import REGISTRY

    custom = {"axis", "phases", "relationships", "first_message", "situation_behaviors"}
    generic = {p.split(".")[-1] for p, s in REGISTRY.items() if s.layer in ("state", "experience")}
    src = _funcs(_CORE / "arc_view.py")["project_card"]
    offenders = sorted(n for n in generic if n not in custom and n in src)
    assert not offenders, f"project_card 里仍逐字段写死了状态/经历字段：{offenders}"


# ── S3 拼人设 prompt 的模块只读投影卡 ─────────────────────────────────────────
def test_s3_prompt_builders_go_through_project_card():
    assert "project_card(" in _read(_WEB / "routers" / "market.py"), "market.py 未走 project_card"
    assert "project_card(" in _read(_CORE / "opening.py"), "opening.py 未走 project_card"


def test_s3b_projected_card_constructed_only_in_arc_view():
    files = _files(_BOTH, r"\bProjectedCard\(")
    assert files <= {"core/arc_view.py"}, f"ProjectedCard(...) 应只在 arc_view 构造：{files}"
    assert "ProjectedCard" in _read(_CORE / "context_engine.py"), "ContextEngine 入口未收 ProjectedCard"
    assert "ProjectedCard" in _read(_CORE / "opening.py"), "opening 入口未收 ProjectedCard"


# ── S4 描述规则、稳定类规则常量各一次 ────────────────────────────────────────
def test_s4_rule_constants_defined_once():
    text = _read(_CORE / "distiller.py")
    for name in ("_PHASE_EXCERPT_RULE", "_PHASE_DESCRIPTION_RULE", "_STABLE_FIELD_RULE"):
        n = len(re.findall(rf"^{name}\s*(?::[^=]+)?=", text, re.M))
        assert n == 1, f"{name} 在 distiller.py 定义 {n} 次（应恰好一次）"


# ── S5 `distiller.py` 无写死 "G5" 依赖 ───────────────────────────────────────
def test_s5_no_hardcoded_g5_dependency():
    text = _read(_CORE / "distiller.py")
    assert not re.search(r"""!=\s*["']G5["']""", text), "还留着写死的 G5 特判"
    assert "PHASE_DEPENDENT_GROUPS" in text, "依赖组未改由登记表推导"


# ── S6 `by_phase[` 只在 dispatch ───────────────────────────────────────────
def test_s6_by_phase_index_only_in_dispatch():
    text = _read(_CORE / "card_draft.py")
    holders = [n for n, s in _funcs(_CORE / "card_draft.py").items() if "by_phase[" in s]
    assert holders == ["dispatch"], f"`by_phase[` 出现在这些函数里：{holders}"


# ── S7 起点判定只在 `CharacterArc.has_positions` ────────────────────────────
def test_s7_has_positions_only_in_schema():
    defs = _hits(_CORE, r"def has_positions\(")
    assert [f for f, _ in defs] == ["core/schema.py"], f"has_positions 应只在 schema 定义一次：{defs}"
    assert "def has_positions(" not in _read(_CORE / "arc_view.py"), "arc_view 里还留着 has_positions"


# ── S8 关系分批只有 `_relationships_batched` 一处、批大小常量一次 ─────────────
def test_s8_batching_single_entry_and_batch_size_constant():
    d = _read(_CORE / "distiller.py")
    assert len(re.findall(r"def _relationships_batched\b", d)) == 1, "distiller 里 _relationships_batched 不是恰好一处"
    rb = _read(_CORE / "relationship_batch.py")
    assert len(re.findall(r"^REL_BATCH_SIZE\s*(?::[^=]+)?=", rb, re.M)) == 1, "REL_BATCH_SIZE 不是恰好一处"
    assert "REL_BATCH_SIZE" in d, "distiller 未从 relationship_batch 取批大小"


# ── S9 分批调用只经 `_collect_stream` ──────────────────────────────────────
def test_s9_batches_go_through_collect_stream():
    src = _funcs(_CORE / "distiller.py")["_relationships_batched"]
    assert "_collect_stream" in src, "分批未走 _collect_stream（流式）"


# ── S10 审核与守卫共用一个遍历函数 ──────────────────────────────────────────
def test_s10_review_and_guard_share_one_walker():
    defs = _hits(_CORE, r"def iter_texts\(")
    assert [f for f, _ in defs] == ["core/moderation/card_text.py"], f"共享遍历未唯一：{defs}"
    for f in ("core/moderation/auto_review.py", "core/moderation/card_guard.py"):
        assert "iter_texts" in _read(_REPO / f), f"{f} 未用共享遍历"


# ── S11 导入方向（§4.0）：AST 扫描各模块导入，违反即红 ───────────────────────
def test_s11_import_direction():
    cl = _imports(_CORE / "card_layers.py")
    assert cl <= {"pydash", "core", "typing"}, f"card_layers 导入了越界模块：{cl}"
    assert "core" in cl or "pydash" in cl

    rv = _imports(_CORE / "arc_view.py")
    assert not (rv & {"distiller", "adapters", "web", "storage"}), f"arc_view 导入越界：{rv}"

    rb = _imports(_CORE / "relationship_batch.py")
    assert not (rb & {"distiller", "adapters", "web"}), f"relationship_batch 导入越界：{rb}"

    op = _imports(_CORE / "opening.py")
    assert not (op & {"web", "storage", "distiller"}), f"opening 导入越界：{op}"


# ── S12 开场白 / 苏醒台词提示词只在 `core/opening.py` ─────────────────────────
def test_s12_opening_prompts_only_in_opening_module():
    markers = ("放进当下场景", "第一眼认出眼前人")
    for m in markers:
        assert m in _read(_CORE / "opening.py"), f"opening.py 缺提示词特征串：{m}"
    src = _read(_WEB / "routers" / "distill.py")
    for m in markers:
        assert m not in src, f"distill.py 里还有内联开场白提示词：{m}"
