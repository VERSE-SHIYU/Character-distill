# -*- coding: utf-8 -*-
"""② 目标检查（spec `arc-behavior-inject.md` §1）：四张公版样本卡，逐阶段走真实 `ChatEngine`。

预言**独立于被测代码**：期望出现 / 不得出现的做法直接从样本卡 JSON 算（阶段 k 的
`behaviors` + 顶层 `situation_behaviors`），不调 `project_card` / `behavior_lines`；
观测的是引擎真正发给模型的 system prompt 与 messages（桩 LLM 记下来）。

G1 选阶段 k：system prompt 含阶段 k 与全程的每一条做法；别的阶段独有的做法、未定位区的
   做法在 system prompt 与 messages 里都不出现。
G2 第 1–4 句不带提醒；第 5 句带，且提醒里正好是阶段 k + 全程的做法。
G3 提醒不进对话记录：引擎历史里的用户消息都是原文。
G0 夹具自检：至少一张卡有「别的阶段独有」的做法，否则 G1 的「不出现」空转。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.chat_engine import ChatEngine
from core.schema import CharacterCard

_SAMPLES = Path(__file__).resolve().parent.parent / "docs" / "specs" / "arc-behaviors-draft-samples"
_CARDS = sorted(_SAMPLES.glob("*.json"))
_MARK = "【提醒：你遇事的做法】"


def _raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pairs(items) -> set[tuple[str, str]]:
    return {(b["situation"], b["behavior"]) for b in items}


def _oracle(raw: dict, k: int) -> tuple[set, set]:
    """（必须出现，不得出现）—— 只读样本 JSON。"""
    phases = raw["character_arc"]["phases"]
    want = _pairs(phases[k - 1].get("behaviors", [])) | _pairs(raw.get("situation_behaviors", []))
    others = set().union(*(_pairs(p.get("behaviors", [])) for i, p in enumerate(phases, 1) if i != k))
    unloc = _pairs(raw["character_arc"].get("unlocated", {}).get("behaviors", []))
    return want, (others | unloc) - want


class _CaptureLLM:
    model = "stub"

    def __init__(self):
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.calls: list[tuple[str, list[dict]]] = []

    def preflight(self):
        return None

    def chat(self, system_prompt, messages, **_kw):
        self.calls.append((system_prompt, [dict(m) for m in messages]))
        return "回复"


def _cases():
    for path in _CARDS:
        n = len(_raw(path)["character_arc"]["phases"])
        for k in range(1, n + 1):
            yield pytest.param(path, k, id=f"{path.stem}-k{k}")


def _run_five_turns(path: Path, k: int, monkeypatch):
    llm = _CaptureLLM()
    card = CharacterCard.model_validate(_raw(path))
    eng = ChatEngine(llm=llm, rag=None, card=card, card_id="c", storage=None,
                     session_id="", is_new_session=False, arc_phase=k)
    monkeypatch.setattr(eng, "_post_turn", lambda *a, **kw: None)
    eng.history = [{"role": "assistant", "content": "开场白"}]
    for i in range(5):
        eng.chat(f"第{i + 1}句")
    return llm, eng


def test_g0_fixture_has_phase_only_behaviors():
    assert _CARDS, f"样本卡不在 {_SAMPLES}"
    assert any(_oracle(_raw(p), k)[1]
               for p in _CARDS for k in range(1, len(_raw(p)["character_arc"]["phases"]) + 1)), (
        "没有任何「别的阶段独有」的做法 —— G1 的「不得出现」空转")


@pytest.mark.parametrize("path,k", list(_cases()))
def test_g1_g2_g3_behaviors_reach_the_model(path, k, monkeypatch):
    want, banned = _oracle(_raw(path), k)
    llm, eng = _run_five_turns(path, k, monkeypatch)

    for sp, msgs in llm.calls:
        for s, b in want:
            assert s in sp and b in sp, f"system prompt 缺阶段 {k} / 全程的做法：{s}"
        blob = sp + "".join(m["content"] for m in msgs)
        for s, b in banned:
            assert b not in blob, f"选阶段 {k} 却出现了别的阶段或未定位的做法：{b}"

    lasts = [msgs[-1]["content"] for _, msgs in llm.calls]
    assert all(_MARK not in c for c in lasts[:4]), "第 1–4 句就带了提醒"
    reminder = lasts[4].split(_MARK, 1)
    assert len(reminder) == 2, "第 5 句没带提醒"
    for s, b in want:
        assert s in reminder[1] and b in reminder[1], f"提醒缺做法：{s}"

    users = [m["content"] for m in eng.history if m["role"] == "user"]
    assert users == [f"第{i + 1}句" for i in range(5)], "提醒写进了对话记录"
