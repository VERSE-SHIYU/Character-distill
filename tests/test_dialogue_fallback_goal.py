# -*- coding: utf-8 -*-
"""保底出卡目标检查（docs/specs/dialogue-fallback.md §1）。

现状（main 57a1f88）：给卡片配对话示例这一步挑不出来，整张卡就失败 —— 原文里找不到本角色
的对话句时，三条通道在蒸馏前的预检就报错；模型没选出可用的编号、或调用出错时，蒸馏的钱
已经花了，卡照样作废。

目标：对话示例配不上，卡照常生成；配不上这件事要让用户知道，不能悄悄少一项。有示例时
一切照旧。

走真实通道：bg 任务（`_run_distill_task`）、SSE（`POST /api/distill/run_stream`）、
`TextManager.get_or_distill`（`POST /api/distill/run`），蒸馏器是真 `Distiller`，只把模型
换成假适配器。通道夹具复用 `test_identify_failure_channels`（跨文件引用在本仓有先例）。
期望文案是字面量，不从源码 import。

照常出卡
F1 bg：原文里没有本角色的对话句 → 任务完成、卡已保存、卡上没有示例
F2 SSE：同上 → 没有错误帧，完成帧照常，保存的卡上没有示例
F3 /run：同上 → 200，返回的卡上没有示例
F4 模型没选出可用的编号 → 任务完成，卡上其余字段都在
F5 调用模型出错 → 任务完成，卡上其余字段都在
F6 名单里除本角色外没有别人 → 照常返回卡
告诉用户
F7 没配上示例时，bg 的完成文案与 SSE 的完成帧都点明这件事，两处是同一句
不多放
F8 配上示例时完成文案照旧，卡上有示例（main 上就绿：回归守卫）
F9 保底只管对话示例这一步：引文核对出错仍然抛（main 上就绿：回归守卫）
F10 「有没有示例」把阶段下的示例也算上：有起点的卡，示例不在顶层
"""
from __future__ import annotations

import json
import uuid

import pytest

import deps
from core import distiller as distiller_module
from core.distiller import Distiller
from core.schema import CharacterCard
from core.text_manager import TextManager
from test_identify_failure_channels import (  # noqa: F401  （fixture 靠名字注入）
    _BODY,
    _FormattingLLM,
    _SavingTM,
    _all_dialogue_examples,
    _build_client,
    _clean_tasks,
    _run_bg,
    _seed_text,
    store,
    user_id,
)

DONE = "蒸馏完成 ✓"
DONE_WITHOUT_EXAMPLES = "蒸馏完成，未配上对话示例（可在编辑角色卡时手动填写）"
EXAMPLE = "路人：先前的话。\n角色：我说一句话。"
# 草稿里的摘录（「开头甲甲甲」）得能在正文里定位，否则状态类字段进未定位区（见夹具文件）。
LOCATABLE = "\n开头甲甲甲。"


def _no_quote_body() -> str:
    """没有引号的正文：名单照常识别出「角色」「路人」，但找不到任何对话句。uuid 防识别缓存。"""
    return f"角色说的话，没有引号{uuid.uuid4().hex}" + LOCATABLE


class _NoPickLLM(_FormattingLLM):
    """每一格都填 0：模型如实表示没有合适的候选。"""

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        return {k: (0 if k.startswith("pick") else "无法判断")
                for k in function["parameters"]["properties"]}


class _BrokenPickLLM(_FormattingLLM):
    """挑选那一次调用直接出错（网络、限流、返回不是合法 JSON 都是这个形态）。"""

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        raise RuntimeError("upstream exploded")


def _distiller(llm=None) -> Distiller:
    d = Distiller(llm=llm or _FormattingLLM(), config_path=None)
    d._longctx_threshold = 0
    d._chunk_size = 3000
    return d


def _bg(store, user_id, monkeypatch, body, llm=None):
    tid = _seed_text(store, user_id, body)
    tm = _SavingTM()
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: tm)
    row = _run_bg(store, user_id, tid, _distiller(llm), monkeypatch, body=body)
    return row, tm.saved


def _sse(store, user_id, monkeypatch, body):
    tid = _seed_text(store, user_id, body)
    tm = _SavingTM()
    client = _build_client(store, user_id, monkeypatch, distiller=_distiller(), tm=tm)
    r = client.post("/api/distill/run_stream", json={"text_id": tid, "character_name": "角色"})
    assert r.status_code == 200
    frames = [json.loads(l[len("data: "):]) for l in r.text.splitlines() if l.startswith("data: ")]
    return frames, tm.saved


def _assert_card_is_whole(card: CharacterCard) -> None:
    """对话示例之外的字段都在：保底丢掉的只能是示例这一项。"""
    assert card.name == "角色"
    assert card.decision_style == "谨慎型"
    assert [r.target for r in card.relationships] == ["某人"]


# ── 照常出卡 ───────────────────────────────────────────────────────────────

def test_f1_bg_a_character_with_no_spoken_line_still_gets_a_card(store, user_id, monkeypatch):
    row, saved = _bg(store, user_id, monkeypatch, _no_quote_body())

    assert row["status"] == "done", row
    assert len(saved) == 1
    assert _all_dialogue_examples(saved[0]) == []
    _assert_card_is_whole(saved[0])


def test_f2_sse_a_character_with_no_spoken_line_still_gets_a_card(store, user_id, monkeypatch):
    frames, saved = _sse(store, user_id, monkeypatch, _no_quote_body())

    assert [f for f in frames if "error" in f] == []
    assert [f for f in frames if f.get("done")] != []
    assert len(saved) == 1
    assert _all_dialogue_examples(saved[0]) == []


def test_f3_run_a_character_with_no_spoken_line_still_gets_a_card(store, user_id, monkeypatch):
    tid = _seed_text(store, user_id, _no_quote_body())
    distiller = _distiller()
    tm = TextManager(lambda: store, distiller, object(), {}, memory_manager=None)
    client = _build_client(store, user_id, monkeypatch, distiller=distiller, tm=tm)

    r = client.post("/api/distill/run", json={"text_id": tid, "character_name": "角色"})

    assert r.status_code == 200, r.text
    assert r.json()["name"] == "角色"
    assert r.json()["dialogue_examples"] == []


def test_f4_bg_no_usable_pick_still_gets_a_card(store, user_id, monkeypatch):
    row, saved = _bg(store, user_id, monkeypatch, _BODY + LOCATABLE, llm=_NoPickLLM())

    assert row["status"] == "done", row
    assert _all_dialogue_examples(saved[0]) == []
    _assert_card_is_whole(saved[0])


def test_f5_bg_a_failed_pick_call_still_gets_a_card(store, user_id, monkeypatch):
    row, saved = _bg(store, user_id, monkeypatch, _BODY + LOCATABLE, llm=_BrokenPickLLM())

    assert row["status"] == "done", row
    assert _all_dialogue_examples(saved[0]) == []
    _assert_card_is_whole(saved[0])


def test_f6_a_roster_with_nobody_else_still_returns_the_card():
    card = CharacterCard(name="角色", identity="独角戏")

    out = _distiller().finalize_card(card, _BODY, "角色", [], [{"name": "角色"}])

    assert out.identity == "独角戏"
    assert _all_dialogue_examples(out) == []


# ── 告诉用户 ───────────────────────────────────────────────────────────────

def test_f7_the_done_message_says_the_examples_are_missing_on_both_channels(
        store, user_id, monkeypatch):
    row, _ = _bg(store, user_id, monkeypatch, _no_quote_body())
    frames, _ = _sse(store, user_id, monkeypatch, _no_quote_body())

    assert row["message"] == DONE_WITHOUT_EXAMPLES
    assert [f["message"] for f in frames if f.get("done")] == [DONE_WITHOUT_EXAMPLES]


# ── 不多放 ─────────────────────────────────────────────────────────────────

def test_f8_with_examples_nothing_changes(store, user_id, monkeypatch):
    row, saved = _bg(store, user_id, monkeypatch, _BODY + LOCATABLE)

    assert row["status"] == "done", row
    assert row["message"] == DONE
    assert _all_dialogue_examples(saved[0]) == [EXAMPLE]


def test_f9_only_the_dialogue_step_is_forgiven(monkeypatch):
    def _boom(card, content):
        raise RuntimeError("retract exploded")

    monkeypatch.setattr(distiller_module, "retract_unverified", _boom)

    with pytest.raises(RuntimeError, match="retract exploded"):
        _distiller().finalize_card(
            CharacterCard(name="角色"), _BODY, "角色", [], [{"name": "角色"}, {"name": "路人"}])


def test_f10_examples_filed_under_a_phase_count_as_having_examples():
    top = CharacterCard(name="角色", dialogue_examples=[EXAMPLE])
    under_phase = CharacterCard.model_validate({
        "name": "角色",
        "character_arc": {"phases": [{"label": "早", "state": "早年"},
                                     {"label": "晚", "state": "晚年",
                                      "overlay": {"dialogue_examples": [EXAMPLE]}}]}})

    assert top.has_dialogue_examples()
    assert under_phase.has_dialogue_examples()
    assert not CharacterCard(name="角色").has_dialogue_examples()
