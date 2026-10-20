# -*- coding: utf-8 -*-
"""目标检查：单值字段每阶段一条（修复包 B1 / P4，spec `docs/specs/scalar-one-per-phase.md`）。

G1–G9。G5、G8 是「不误伤」回归守卫，实现之初即绿（两条都原标本阶段时留先写的；
顶层格本来也优先原标 0），故从未标 `xfail`。G9 锁单值分发必传 `tagged`。

预言是本文件里的字面量；被测的是发给模型的提示词与转卡结果。
"""
from __future__ import annotations

import ast
import logging
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.card_draft import card_from_draft, dispatch
from core.card_layers import REGISTRY
from core.distiller import format_prompt_after
from core.retro_regions import NOT_DETECTED
from core.schema import FORMAT_GROUPS

import test_phase_anchoring as PA

_REPO = Path(__file__).resolve().parents[1]

# spec 3.1 第 1 条的原句（单值规则整句 + 底色标 0 规则）。
_SCALAR_RULE = (
    '取值按阶段给，写成 {"value": "结论（「原话」）", "occurrences": '
    '[{"phase": 1, "quote": "该阶段原文摘录"}]} —— 每个阶段只写一条：'
    '同一个阶段只能出现在一条取值的 occurrences 里；'
    '此人对不同的人表现不同时，只写他这一阶段对一般人的样子，'
    '只对某个特定的人才有的表现不写在这里；'
    '从头到尾一直如此的（此人的底色），只写一条，phase 填 0；'
    '故事里因某人某事才出现或改变的（如对某人的称呼），不填 0，标出它确实成立的阶段。'
)


def _scalar_paths() -> list[str]:
    return [p for p, s in REGISTRY.items()
            if s.kind == "scalar" and s.layer in ("state", "experience")]


def _group_of(path: str) -> str:
    top = path.split(".")[0]
    return next(g for g, fields in FORMAT_GROUPS.items() if top in fields)


def _template_phases(prompt: str, leaf: str) -> list[str]:
    """模板里 `"<leaf>": ...` 那一行的阶段号（模板每个字段独占一行）。"""
    assert f'"{leaf}":' in prompt, f"提示词模板里找不到字段 {leaf}"
    line = prompt.split(f'"{leaf}":', 1)[1].split("\n", 1)[0]
    return re.findall(r'"phase":\s*(\d+)', line)


def test_g1_each_scalar_path_gets_one_per_phase_rule_and_two_phase_template():
    paths = _scalar_paths()
    assert len(paths) == 8, f"按阶段单值字段应为 8 个，实际 {paths}"
    for path in paths:
        prompt = format_prompt_after(_group_of(path))
        assert _SCALAR_RULE in prompt, f"[{path}] 所在组提示词没有单值规则整句"
        phases = _template_phases(prompt, path.split(".")[-1])
        assert len(phases) == 2 and phases[0] != phases[1], \
            f"[{path}] 模板应示范两条取值且阶段号不同，实际 {phases}"


def test_g2_rule_follows_the_registry(monkeypatch):
    import core.distiller as d

    original = d.REGISTRY["values"]
    assert d._phase_rule("values") == d._PHASE_DESCRIPTION_RULE
    monkeypatch.setitem(d.REGISTRY, "values", type(original)("state", "scalar", "价值观"))
    assert "每个阶段只写一条" in d._phase_rule("values"), "登记表改成单值后，维度规则没跟着变"
    phases = re.findall(r'"phase":\s*(\d+)', d._timed_tpl("values"))
    assert len(phases) == 2 and phases[0] != phases[1], \
        f"登记表改成单值后，模板应示范两条且阶段不同，实际 {phases}"

    monkeypatch.setitem(d.REGISTRY, "values", original)
    assert d._phase_rule("values") == d._PHASE_DESCRIPTION_RULE, "改回列表后仍带着单值规则"
    assert len(re.findall(r'"phase":\s*(\d+)', d._timed_tpl("values"))) == 1


def test_g3_rule_names_the_general_audience_and_relational_modes_carry_tone():
    full = format_prompt_after()
    assert "只写他这一阶段对一般人的样子" in _SCALAR_RULE
    assert "只写他这一阶段对一般人的样子" in full
    assert "各一句具体做法与语气" in full, "三档说明没写明它同时决定语气"


def _collision_card():
    return {
        "name": "角色",
        "character_arc": {"axis": "a", "phases": PA._phases(2, {2: PA._Q3})},
        "decision_style": [
            {"value": "B", "occurrences": [{"phase": 1, "quote": PA._Q3}]},
            {"value": "A", "occurrences": [{"phase": 2, "quote": PA._Q3}]},
        ],
    }


def test_g4_originally_tagged_phase_wins_collision(caplog):
    with caplog.at_level(logging.WARNING):
        card = card_from_draft(_collision_card(), PA._SRC, retro=NOT_DETECTED)
    assert card.character_arc.phases[1].overlay.get("decision_style") == "A"
    assert [r.getMessage() for r in caplog.records if "有多条取值" in r.getMessage()] == [
        "[card_draft] decision_style 的阶段 2 有多条取值，"
        "保留「A」（原标此阶段），丢弃「B」（由阶段 1 改挂进来）"]


def test_g5_two_rows_tagged_same_phase_keep_the_first(caplog):
    draft = _collision_card()
    draft["decision_style"] = [
        {"value": "B", "occurrences": [{"phase": 2, "quote": PA._Q3}]},
        {"value": "A", "occurrences": [{"phase": 2, "quote": PA._Q3}]},
    ]
    with caplog.at_level(logging.WARNING):
        card = card_from_draft(draft, PA._SRC, retro=NOT_DETECTED)
    assert card.character_arc.phases[1].overlay.get("decision_style") == "B"
    assert [r.getMessage() for r in caplog.records if "有多条取值" in r.getMessage()] == [
        "[card_draft] decision_style 的阶段 2 有多条取值，"
        "保留「B」（原标此阶段），丢弃「A」（原标此阶段）"]


def test_g8_top_level_prefers_originally_tagged_zero():
    """顶层格（phase 0）也走「原标 0 优先」：先出现的那条标的是阶段，零号标注的那条赢。"""
    top, _, _ = dispatch([[1, 2], []], 2, kind="scalar", zero_only=[1],
                         tagged=lambda i: {0} if i == 1 else {1, 2})
    assert top == [1]


def test_g9_scalar_dispatch_requires_tagged():
    with pytest.raises(ValueError, match="g9_field"):
        dispatch([[1], []], 2, kind="scalar", label="g9_field")
    top, by_phase, loose = dispatch([[1], [2]], 2, kind="list", label="g9_field")
    assert (top, by_phase, loose) == ([], [[0], [1]], [])


def _top_level_funcs(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _calls(node: ast.AST, name: str) -> bool:
    return any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == name
               for c in ast.walk(node))


def test_g6_one_winner_function_used_by_both_call_sites():
    tree = ast.parse((_REPO / "core" / "card_draft.py").read_text(encoding="utf-8"))
    funcs = _top_level_funcs(tree)
    assert "slot_winner" in funcs, "没有统一的「一格留谁」函数 slot_winner"
    callers = {name for name, node in funcs.items() if _calls(node, "slot_winner")}
    assert callers == {"_cap_slot", "_convert_relationships"}, \
        f"slot_winner 的调用方应恰好是 _cap_slot 与 _convert_relationships，实际 {callers}"
    phase_cmp = set()
    for name, node in funcs.items():
        for c in ast.walk(node):
            if isinstance(c, ast.Compare) and any(isinstance(o, ast.Eq) for o in c.ops):
                operands = [c.left, *c.comparators]
                if any(isinstance(o, ast.Attribute) and o.attr == "phase" for o in operands):
                    phase_cmp.add(name)
    assert phase_cmp <= {"slot_winner"}, f"这些函数里还有第二份「一格留谁」判据：{phase_cmp}"


def test_g7_dimension_rules_go_through_phase_rule():
    tree = ast.parse((_REPO / "core" / "distiller.py").read_text(encoding="utf-8"))

    def assigned(name: str):
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == name:
                    return node.value
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        raise AssertionError(f"distiller.py 里找不到 {name}")

    dims = [assigned("_FORMAT_DIMS"), assigned("_FORMAT_DIM_M")]
    direct = {n.id for d in dims for n in ast.walk(d)
              if isinstance(n, ast.Name) and n.id in ("_PHASE_DESCRIPTION_RULE", "_PHASE_VERBATIM_RULE")}
    assert not direct, f"维度说明还直接拼规则常量：{direct}"
    called = {n.args[0].value for d in dims for n in ast.walk(d)
              if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_phase_rule"
              and n.args and isinstance(n.args[0], ast.Constant)}
    bad = [p for p in called
           if p not in REGISTRY or REGISTRY[p].layer not in ("state", "experience")]
    assert not bad, f"这些 _phase_rule 路径不是登记表里按阶段给的路径：{bad}"
    assert len(called) >= 10, f"维度说明里经 _phase_rule 的路径只有 {len(called)} 个"
