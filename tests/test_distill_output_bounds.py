# -*- coding: utf-8 -*-
"""归并限量与格式分组拆分的读数锁（WP17）。

两处坏法都只在真验收（真模型、真长书）里才露头，且都不抛异常：

- 归并提示词要模型「保留所有原文对话原句」而输出有硬上限 → 输入是几十片分析，
  原句全留装不下，模型保了原句就被 `finish_reason=length` 截断，整任务失败
  （实测：刘姥姥）。改成按维度限量（200 字概括 + 5 条原句）后上限不随输入增长。
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


def test_all_three_reduce_entry_points_ask_for_a_capped_summary_plus_five_quotes(monkeypatch):
    """三处归并入口都按维度限量，且都不再要求「保留所有原文对话原句」。

    挡住两件事：把任意一处改回自带的旧提示词（不再走共用那份），以及把限量规则换回
    无上限的「保留所有原文对话原句」—— 后者正是刘姥姥那次把正文顶到 `max_tokens`
    截断的写法。分批归并（`_single_reduce_async`）与单批流式（`_single_reduce_stream`）
    是两条真实路径，各自都可能被单独「顺手改回」。
    """
    d, llm = _reduce_distiller(monkeypatch)
    d._single_reduce(["片段分析"], "角色")
    asyncio.run(d._single_reduce_async(["片段分析"], "角色"))
    list(d._single_reduce_stream(["片段分析"], "角色"))

    assert len(llm.systems) == 3, f"三个入口没各发一次：{len(llm.systems)}"
    for system in llm.systems:
        assert "每个维度先写不超过 200 字的概括，再列最有代表性的 5 条原文原句" in system, system[:100]
        assert PROFILE_PRIORITY_LINE in system, system[:100]
        assert "保留所有原文对话原句" not in system, system[:100]


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
    """卡模板里的 relationships 只出现在 G5、key_memories 只出现在 G6（本段 §3.2：记忆随阶段编号走）。

    挡住：relationships 只从分组表移走、卡模板仍留在 G5 之外 —— 模板键是输出形态，落错组
    会让那一组照旧吐关系；key_memories 没跟着阶段（G6）走则记忆缺阶段编号。
    """
    g5, g6 = format_prompt_after("G5"), format_prompt_after("G6")
    assert '"relationships"' in g5 and '"key_memories"' not in g5
    assert '"key_memories"' in g6 and '"relationships"' not in g6
