# -*- coding: utf-8 -*-
"""蒸馏遗留③ 目标检查：字段正文里依据的写法统一，口癖只写原话。

调查（6 张现卡，2026-10-09）：
- 括号里的依据是有用的内容：157 组里 93 条带引号原话、引文核对全部通过，不能删；
- 口癖被引文核对整条撤回 16 条：提示词给口癖也套了「描述（原文出处）」模板，可口癖要求整条逐字是原文
  （`core/card_quotes.py` 的 `_VERBATIM_TOP`）—— 同一个模板套在了性质不同的两类字段上；
- 写法四种并存（带引号原话 / 不带引号原话 / 只写章节号 / 不写），章节号与「原文出处」标签代码核对不了。

目标：
- 描述类取值统一写成「结论（「原话」）」；找不到原话就写一句场景转述、不加引号；不写章节号，
  不写「原文出处」标签。这条写法只定义一次。
- 原话类取值（以 `card_quotes` 的逐字字段清单为准）只写原话本身。
- 稳定项（文化程度、用词层次…）只写取值，不附依据（现卡里它们本来就不带，`chat_engine` 直接拼进
  「你的文化程度：{…}」）。

走真实代码：拼出发给模型的完整提示词与每个分组提示词。预言是本文件里的字面量。

V1 所有提示词里不再出现「原文出处」「出处用括号」「出处写在括号」
V2 所有提示词都写明：不写章节号；依据引号里逐字照抄
V3 口癖模板的 value 是原话本身：不含「描述」「结论」，不含括号
V4 描述类模板（性格）的 value 示例写成「结论（「原话」）」
V5 稳定项的取值说明不再要求附依据（「只写取值本身」）
V6 「原话类」由逐字字段清单决定：清单里有的模板路径用原话模板，其余用描述模板（main 上口癖不满足）
V7 回归守卫：文化程度、用词层次的可选值原样保留（main 上就绿）
V8 模板跟着逐字清单走：清单里临时加一项，那个字段的模板就变成原话模板（防写死字段名）
V9 模板里每个字段路径都是登记表里按阶段给的路径（状态 / 经历类）（防路径写错被悄悄当成描述类）
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_quotes import VERBATIM_FIELDS
from core.distiller import format_prompt_after
from core.schema import FORMAT_GROUPS

_FULL = format_prompt_after()
_PROMPTS = {"full": _FULL, **{g: format_prompt_after(g) for g in FORMAT_GROUPS}}


def _template_value(prompt: str, key: str) -> str:
    """模板里 `"key": [{"value": "<这里>"` 的示例取值。"""
    m = re.search(rf'"{key}": \[\{{"value": "([^"]*)"', prompt)
    assert m, f"提示词模板里找不到 {key} 的 value 示例"
    return m.group(1)


@pytest.mark.parametrize("name", list(_PROMPTS))
def test_v1_no_location_label_instructions(name):
    p = _PROMPTS[name]
    for bad in ("原文出处", "出处用括号", "出处写在括号"):
        assert bad not in p, f"[{name}] 还在要求「{bad}」"


@pytest.mark.parametrize("name", list(_PROMPTS))
def test_v2_evidence_rule_forbids_chapter_and_requires_verbatim(name):
    p = _PROMPTS[name]
    assert "不写章节号" in p, f"[{name}] 没写「不写章节号」"
    assert "逐字照抄" in p


def test_v3_catchphrase_template_is_the_line_itself():
    v = _template_value(_FULL, "catchphrases")
    assert "描述" not in v and "结论" not in v, f"口癖模板还要求写描述：{v!r}"
    assert "（" not in v and "(" not in v, f"口癖模板还带括号：{v!r}"
    assert "原话" in v


def test_v4_descriptive_template_shows_conclusion_with_quoted_evidence():
    v = _template_value(_FULL, "personality_traits")
    assert v == "结论（「原话」）", f"描述类模板示例应为「结论（「原话」）」，实际 {v!r}"


def test_v5_stable_fields_carry_no_evidence():
    assert "只写取值本身" in _FULL, "稳定项没写明只写取值本身"


def test_v6_verbatim_list_decides_the_template():
    keys = re.findall(r'"([a-z_]+)": \[\{"value": "', _FULL)
    assert keys, "模板里没找到任何按阶段的字段"
    verbatim_leafs = {p.split(".")[-1].rstrip("[]") for p in VERBATIM_FIELDS}
    for k in keys:
        v = _template_value(_FULL, k)
        if k in verbatim_leafs:
            assert v != "结论（「原话」）", f"{k} 在逐字清单里，却用了描述模板"
        else:
            assert v == "结论（「原话」）", f"{k} 不在逐字清单里，却没用描述模板：{v!r}"


def test_v7_stable_choices_unchanged():
    assert "文盲/识字不多/普通/受过良好教育/学者" in _FULL
    assert "粗白/日常/文雅/书面" in _FULL


def test_v8_template_follows_the_verbatim_list(monkeypatch):
    """逐字清单再加一项，模板跟着变 —— 不许在 `_timed_tpl` 里写死字段名。"""
    import core.distiller as d
    monkeypatch.setattr(d, "VERBATIM_FIELDS", tuple(d.VERBATIM_FIELDS) + ("values[]",))
    assert "结论" not in d._timed_tpl("values"), "清单加了 values[]，模板没跟着变成原话模板"
    assert d._timed_tpl("motives") != d._timed_tpl("values")


def test_v9_template_paths_come_from_the_registry():
    """模板里每个 `_timed_tpl("<路径>")` 的路径都是登记表里按阶段给的路径（状态 / 经历类）—— 写错路径会被悄悄当成描述类，这里拦住。"""
    import ast
    from pathlib import Path
    from core.card_layers import REGISTRY
    src = (Path(__file__).resolve().parents[1] / "core" / "distiller.py").read_text(encoding="utf-8")
    assert "_TIMED_TPL" not in src, "还留着不分原话 / 描述的旧模板常量 _TIMED_TPL"
    calls = [n.args[0].value for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_timed_tpl"
             and n.args and isinstance(n.args[0], ast.Constant)]
    assert len(calls) >= 17, f"模板里的 _timed_tpl 调用只有 {len(calls)} 处"
    bad = [p for p in calls if p not in REGISTRY or REGISTRY[p].layer not in ("state", "experience")]
    assert not bad, f"这些路径不是登记表里按阶段给的路径：{bad}"


def test_v10_dimension_c_tells_catchphrases_to_be_the_line_itself():
    """维度说明里的口癖口径与模板一致：口癖单列为「取值就是原话本身」，不再与语气/句式共用描述写法。"""
    assert "口癖按阶段给，取值就是原话本身" in _FULL, "维度 C 没把口癖单列成原话写法"
    assert "语气/句式/口癖按阶段给" not in _FULL, "维度 C 还把口癖和语气/句式放在同一描述写法下"


@pytest.mark.parametrize("name", list(_PROMPTS))
def test_v11_evidence_rule_keeps_the_paraphrase_fallback(name):
    """找不到原话时退回场景转述（不加引号）——没有这条，模型会为凑引号编原话。"""
    assert "找不到原话就写一句场景转述，不加引号" in _PROMPTS[name], f"[{name}] 依据规则丢了「场景转述」的退路"
