# -*- coding: utf-8 -*-
"""归并预算与格式分组拆分的读数锁（WP17）。

三处坏法都只在真验收（真模型、真长书）里才露头，且都不抛异常：

- 归并提示词没写字数上限 → 单批归并撞 `CARD_MAX_TOKENS` 被 `finish_reason=length`
  截断，整任务失败（实测：刘姥姥）。
- 上限换成的 token 估算超过上限的 60% → 留给提示词与 JSON 骨架的余量不够，照样截断。
- relationships 与其余字段同组 → 关系条数随登场人数增长（宝玉几十条），那一组顶爆而
  其余字段都没超，卡就少关系。

每条断言都配一句它挡住的变异。
"""
from __future__ import annotations

import asyncio

from core.distiller import PROFILE_PRIORITY_LINE, Distiller, format_prompt_after


class _CapturingLLM:
    """归并三入口共用的假 LLM：把 system prompt 记下来，正文与用量照常回。"""

    last_usage = None
    model = "m"

    def __init__(self) -> None:
        self.systems: list[str] = []

    def chat(self, system, messages, **kw):
        self.systems.append(system)
        return "归并结果"

    def chat_stream_long(self, system, messages, max_tokens=None, **kw):
        self.systems.append(system)
        yield "归并结果"
        return {"prompt_tokens": 1, "completion_tokens": 1}


def _reduce_distiller(monkeypatch) -> tuple[Distiller, _CapturingLLM]:
    """只打断落库：三入口自己的组装与调用形态照跑。"""
    monkeypatch.setattr("core.distiller.try_record_usage", lambda **kw: None)
    llm = _CapturingLLM()
    d = Distiller(llm=llm, config_path=None)

    def fake_chat_accounted(system, messages, label, action, **kw):
        llm.systems.append(system)
        return ("归并结果", False)

    # 实例属性盖住同名方法：`_single_reduce_async` 里 `to_thread` 拿到的就是它
    d._chat_accounted = fake_chat_accounted
    return d, llm


def test_all_three_reduce_entry_points_carry_the_budget_and_the_priority(monkeypatch):
    """三处归并入口的 system prompt 都含字数上限与优先级那一句（共用 `_reduce_system_prompt`）。

    挡住：把任意一处改回自带的旧提示词（不再走共用那份）—— 那一处既没上限也没优先级，
    本断言红。分批归并（`_single_reduce_async`）与单批流式（`_single_reduce_stream`）是
    两条真实路径，各自都可能被单独「顺手改回」。
    """
    d, llm = _reduce_distiller(monkeypatch)
    d._single_reduce(["片段分析"], "角色")
    asyncio.run(d._single_reduce_async(["片段分析"], "角色"))
    list(d._single_reduce_stream(["片段分析"], "角色"))

    assert len(llm.systems) == 3, f"三个入口没各发一次：{len(llm.systems)}"
    for system in llm.systems:
        assert str(Distiller.REDUCE_BUDGET_CHARS) in system, system[:80]
        assert PROFILE_PRIORITY_LINE in system, system[:80]


def test_the_budget_leaves_margin_under_the_card_token_cap():
    """字数上限按 0.6 token/字换算后不得超过上限的 60%（其余留给提示词与 JSON 骨架）。

    挡住：把 `REDUCE_BUDGET_CHARS` 调大到 9000 —— 9000 × 0.6 = 5400 > 8192 × 0.6 = 4915.2，
    估算的正文 token 吃掉六成以上，截断余量被挤掉，本断言红。
    """
    estimated = Distiller.REDUCE_BUDGET_CHARS * 0.6
    assert estimated <= 0.6 * Distiller.CARD_MAX_TOKENS, (
        f"{Distiller.REDUCE_BUDGET_CHARS} 字 ≈ {estimated} tokens，超过上限的 60%"
    )


def test_prestep_and_dimension_f_ride_with_G5_while_dimension_M_stays_in_G4():
    """关系拆到 G5 后，讲关系的片段（前置步骤、维度 F）跟着走，维度 M 留在 G4。

    挡住：前置步骤仍挂 `keep("G4")` —— G5 提示词就没有「枚举全部有名字角色」这一步，
    维度 F 找不到枚举结果；本断言前两条同时红（G5 缺、G4 多）。
    """
    g5, g4 = format_prompt_after("G5"), format_prompt_after("G4")
    assert "前置步骤" in g5 and "F. 人际关系" in g5
    assert "前置步骤" not in g4 and "F. 人际关系" not in g4
    assert "M. 心理画像" in g4


def test_relationships_and_key_memories_land_in_different_groups():
    """卡模板里的 relationships 只出现在 G5、key_memories 只出现在 G4。

    挡住：relationships 只从分组表移走、卡模板仍留在 G4 —— G4 提示词会同时带两个模板
    键，本断言红。这条不重复上一条：模板键是输出形态，落错组会让 G4 照旧吐关系。
    """
    g5, g4 = format_prompt_after("G5"), format_prompt_after("G4")
    assert '"relationships"' in g5 and '"key_memories"' not in g5
    assert '"key_memories"' in g4 and '"relationships"' not in g4
