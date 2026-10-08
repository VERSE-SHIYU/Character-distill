# -*- coding: utf-8 -*-
"""③ S1 目标检查：评估时冒犯、触雷、修复只判一次。

目标：段 1 之后，好感按评估模型报的事件走（`affinity_event`）；但评估 prompt 里还留着一套
平行的判定 —— `trigger_hit` / `in_story_conflict` / `repair_signal`，只喂疏远检测的影子日志。
同一件事判两遍：模型可能两边说法不一（报了 `trigger` 却 `trigger_hit=false`），而
「剧情内演戏的冲突不算冒犯」这条只写在旧的那一套里，好感用的事件说明里没有。

S1 之后：只有事件这一套。剧情内冲突不算冒犯 / 触雷；疏远检测从事件里数触雷次数；修复的细分
（道歉 / 解释 / 补偿 / 软肋）只在事件是 `repair` 时作为它的附属字段 `repair_kind` 记下。

走真实 `ChatEngine` 与评估管道，桩只回放标签；观测评估模型收到的 prompt 和影子日志那一行。
预言独立于被测代码：字段名、事件名、日志片段都是本文件里的字面量。

E1 评估 prompt 里没有旧的三个字段名；JSON 段里有 `repair_kind`。
E2 事件说明里写了：剧情内演戏的冲突不算冒犯、不算触雷。
E3 疏远检测从事件数触雷：连续两轮 `trigger` → 影子日志记 2 次、进入 active；
   负对照：两轮 `offended` 不算触雷。
E4 修复细分只跟着 `repair` 走：冒犯后报 `repair` + `apology` → 日志 `repair=apology`；
   没有待修复的冒犯时报 `repair`（规则表把它按 friendly 算）→ 日志 `repair=none`。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.chat_engine import ChatEngine
from core.schema import CharacterCard, PsycheProfile

_EVAL_SYS = "你是精确的JSON输出器"
_OLD_FIELDS = ("trigger_hit", "in_story_conflict", "repair_signal")


class _ReplayLLM:
    """聊天调用回「回复」；评估调用按脚本回放一条标签（只回放，不模拟评估模型怎么判）。"""
    model = "stub"

    def __init__(self, script):
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.script, self.i = list(script), 0
        self.eval_prompts: list[str] = []

    def preflight(self):
        return None

    def chat(self, system_prompt, messages, **_kw):
        if system_prompt.startswith(_EVAL_SYS):
            event, tier, delta, extra = self.script[self.i]
            self.i += 1
            self.eval_prompts.append(messages[0]["content"])
            return json.dumps({"affinity_event": event, "affinity_tier": tier,
                               "affinity_delta": delta, "met_condition_index": None,
                               "trust": 30, "guard": 80, "importance": 5, **extra},
                              ensure_ascii=False)
        return "回复"


def _run(script, *, start=60, triggers=("被人当众揭短",)):
    llm = _ReplayLLM(script)
    card = CharacterCard(name="甲", identity="一个人", psyche=PsycheProfile(triggers=list(triggers)))
    eng = ChatEngine(llm=llm, rag=None, card=card, card_id="c", storage=None,
                     session_id="s1-goal", is_new_session=False)
    eng.load_affinity({"affinity": start, "trust": 30, "mood": "平静", "guard": 80,
                       "reason": "", "inner_voice": ""}, initialized=True)
    for i in range(len(script)):
        eng.chat(f"第{i + 1}句")
    assert llm.i == len(script), "有一轮没跑评估"
    return llm


def _shadow_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("[estrangement-shadow]")]


def _event_rules(prompt: str) -> str:
    """评估 prompt 里讲事件怎么判的那一段。"""
    assert "好感事件判定规则" in prompt, "评估 prompt 里没有事件判定规则"
    return prompt.split("好感事件判定规则", 1)[1].split("\n\n", 1)[0]


def test_e1_old_parallel_fields_are_gone_and_repair_kind_is_asked():
    prompt = _run([("neutral", "small", 1, {})]).eval_prompts[0]
    for field in _OLD_FIELDS:
        assert field not in prompt, f"评估 prompt 还在要旧字段 {field}"
    assert '"repair_kind"' in prompt, "JSON 段里没有 repair_kind"


def test_e2_in_story_conflict_is_not_offence_in_event_rules():
    rules = _event_rules(_run([("neutral", "small", 1, {})]).eval_prompts[0])
    assert "剧情" in rules, "事件说明里没写剧情内演戏的冲突不算冒犯 / 触雷"


def test_e3_trigger_hits_are_counted_from_events(capsys):
    _run([("trigger", "medium", 4, {}), ("trigger", "medium", 4, {})])
    last = _shadow_lines(capsys.readouterr().out)[-1]
    assert "trigger_hits=2" in last and "would_enter=active" in last and "trigger(2hits)" in last, last


def test_e3_negative_offended_is_not_a_trigger_hit(capsys):
    _run([("offended", "small", 1, {}), ("offended", "small", 1, {})])
    last = _shadow_lines(capsys.readouterr().out)[-1]
    assert "trigger_hits=0" in last and "trigger(" not in last, last


def test_e4_repair_kind_is_logged_only_with_a_real_repair(capsys):
    _run([("offended", "medium", 4, {}), ("repair", "small", 2, {"repair_kind": "apology"})])
    assert "repair=apology" in _shadow_lines(capsys.readouterr().out)[-1]


def test_e4_repair_without_pending_offence_logs_no_repair(capsys):
    _run([("repair", "small", 2, {"repair_kind": "apology"})])
    assert "repair=none" in _shadow_lines(capsys.readouterr().out)[-1]
