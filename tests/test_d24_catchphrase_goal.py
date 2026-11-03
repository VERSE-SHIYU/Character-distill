# -*- coding: utf-8 -*-
"""D24 ③b 目标检查：口头禅按「经常说 + 能区分开」核对。

现状：口头禅只核「原文里逐字查得到」，不核谁说、说几次、别人说不说；提示词又拿「对某人的
称呼」当例子，SG 现有 4 张卡 12 条口头禅没有一条符合定义。

目标（Shiyu 11-04 定）：口头禅 = 本人经常说、且别人不说的词或短句；称呼不算。「经常」=
本人说过它的章数 ÷ 本人出场章数 ≥ 1/3；出场 = 本章正文出现本人名字或别名；宁可少。

走真 `finalize_card`、假模型：核对调用只按 `function["name"]` 分派，回 `who1…whoN`。

G1 「酒」4 处全本人、落在 2/4 出场章 → 留，核对恰好 1 次（只断言「留」现在就成立，故并核调用数）
G2 「不多」只落 1 章 → 上限 1/4 < 1/3 → 去，0 次调用
G3 「还欠十九个钱」都是掌柜 → 去，1 次调用
G4 「书」有一处是喝酒的人 → 去（别人说过就区分不开）
G5 同 G1、但没有「第N章」标题 → 认不出章节，去，0 次调用
G6 顶层「酒」留、阶段 overlay 的「不多」删
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.card_layers import get_path
from core.distiller import Distiller
from core.schema import CharacterCard

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kongyiji.txt")
# 审计方实测的分章：第 1 章 = 第 0–3 段，第 2 章 = 4–7，第 3 章 = 8–10，第 4 章 = 11–13。
_GROUPS = ((1, 0, 4), (2, 4, 8), (3, 8, 11), (4, 11, 14))
NAME = "孔乙己"
ROSTER = [{"name": NAME, "aliases": []}, {"name": "掌柜", "aliases": []}]


def _paragraphs() -> list[str]:
    with open(_FIXTURE, encoding="utf-8") as f:
        return [line for line in f.read().splitlines() if line.strip()]


def _with_chapters() -> str:
    paras = _paragraphs()
    return "".join(
        f"第{n}章\n" + "\n".join(paras[a:b]) + "\n" for n, a, b in _GROUPS)


def _card(catchphrases, overlay=None) -> CharacterCard:
    data = {"name": NAME, "speaking_style": {"catchphrases": list(catchphrases)}}
    if overlay is not None:
        data["character_arc"] = {"phases": [{
            "label": "一", "state": "起",
            "overlay": {"speaking_style": {"catchphrases": list(overlay)}},
        }]}
    return CharacterCard.model_validate(data)


class _VerifyLLM:
    """假模型：每次核对调用按顺序回一组说话人；挑选对话示例的格子一律填 0（挑不出）。"""

    model = "fake-d24"
    last_usage = None

    def __init__(self, batches):
        self._batches = [list(b) for b in batches]
        self.calls: list[str] = []

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        self.calls.append(function["name"])
        if function["name"] == "verify_catchphrase":
            speakers = self._batches.pop(0) if self._batches else []
            return {f"who{i}": s for i, s in enumerate(speakers, 1)}
        return {k: (0 if k.startswith("pick") else "无法判断")
                for k in function["parameters"]["properties"]}

    @property
    def verify_calls(self) -> int:
        return self.calls.count("verify_catchphrase")


def _finalize(card, content, llm):
    return Distiller(llm=llm, config_path=None).finalize_card(
        card, content, NAME, [], ROSTER)


@pytest.mark.xfail(strict=True, reason="D24 ③b：口头禅核对尚未实现")
def test_g1_said_often_by_the_character_is_kept():
    llm = _VerifyLLM([[NAME] * 4])

    out = _finalize(_card(["酒"]), _with_chapters(), llm)

    assert out.speaking_style.catchphrases == ["酒"]
    assert llm.verify_calls == 1, "核对「酒」应当恰好调用模型一次"


@pytest.mark.xfail(strict=True, reason="D24 ③b：口头禅核对尚未实现")
def test_g2_too_few_chapters_is_dropped_without_a_call():
    llm = _VerifyLLM([])

    out = _finalize(_card(["不多"]), _with_chapters(), llm)

    assert out.speaking_style.catchphrases == []
    assert llm.verify_calls == 0, "上限预判就能判掉，不该调模型"


@pytest.mark.xfail(strict=True, reason="D24 ③b：口头禅核对尚未实现")
def test_g3_said_by_another_person_is_dropped():
    llm = _VerifyLLM([["掌柜"] * 4])

    out = _finalize(_card(["还欠十九个钱"]), _with_chapters(), llm)

    assert out.speaking_style.catchphrases == []
    assert llm.verify_calls == 1


@pytest.mark.xfail(strict=True, reason="D24 ③b：口头禅核对尚未实现")
def test_g4_any_line_by_someone_else_is_dropped():
    llm = _VerifyLLM([["名单外的人", NAME, NAME, NAME]])

    out = _finalize(_card(["书"]), _with_chapters(), llm)

    assert out.speaking_style.catchphrases == []


@pytest.mark.xfail(strict=True, reason="D24 ③b：口头禅核对尚未实现")
def test_g5_without_chapter_headings_is_dropped_without_a_call():
    llm = _VerifyLLM([])

    out = _finalize(_card(["酒"]), "\n".join(_paragraphs()), llm)

    assert out.speaking_style.catchphrases == []
    assert llm.verify_calls == 0, "认不出章节就不该调模型"


@pytest.mark.xfail(strict=True, reason="D24 ③b：口头禅核对尚未实现")
def test_g6_top_level_kept_and_phase_overlay_dropped():
    llm = _VerifyLLM([[NAME] * 4])

    out = _finalize(_card(["酒"], overlay=["不多"]), _with_chapters(), llm)

    assert out.speaking_style.catchphrases == ["酒"]
    overlay = out.character_arc.phases[0].overlay
    assert get_path(overlay, "speaking_style.catchphrases") == []
