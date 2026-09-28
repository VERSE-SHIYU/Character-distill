# -*- coding: utf-8 -*-
"""mem0 ↔ 项目适配器之间**唯一**的一层（mem0 升级时只看这个文件）。

为什么不配 mem0 自带的 LLM 提供方：`core/memory_manager.py` 原先写死
`model: "deepseek-chat"`，而这个名字官方自 2026-07-24 起已完全停用（请求直接失败）；
且 openai / deepseek 两条自带提供方都**没有传 `extra_body` 的通道**，关不掉思考。

`Memory.add` 只经 `self.llm.generate_response(...)` 调 LLM（mem0 2.0.20
`memory/main.py:956`，每轮恰好一次），故在 `Memory.from_config` 之后把本类注入为
`Memory.llm` 就够了 —— 只依赖这一个属性、这一个方法。`chat_stream` 之类都不在这条路上。

**身份沿用调用方**：提炼发出的内容就是该用户的聊天，出站就该归属该用户，地理合规判定
照常生效（`add` 路径上 `LLM_CALLER` 在场）。这里**不**包 `system_llm_context()` ——
那会无条件放行，`base_url` 一旦被配到境外就绕过合规。
"""
from __future__ import annotations

from typing import Any

from mem0.llms.base import LLMBase

from adapters.llm_adapter import LLMAdapter


class AdapterLLM(LLMBase):
    """把 mem0 的 `generate_response` 接到 `LLMAdapter.chat`。

    继承 `LLMBase` 不是为了要一个基类，而是让 mem0 若换掉这个接口时**在 import 处就炸**：
    真正成立的契约只有 `generate_response` 这一个方法的形状，注释挡不住人，import 能。
    """

    def __init__(self, adapter: LLMAdapter) -> None:
        # LLMBase 要求 config 带 model。填适配器**实际**用的那一个，别再造出第二份模型名。
        super().__init__({"model": adapter.model})
        self._adapter = adapter

    def generate_response(self, messages: list[dict[str, str]], tools: list[dict] | None = None,
                          tool_choice: str = "auto", **kwargs: Any) -> str:
        """mem0 的向量记忆不带 tools；带了说明它升级后换了调用形态，宁可炸也不静默忽略。"""
        if tools:
            raise NotImplementedError(
                "mem0 的记忆提炼不带 tools —— 若 mem0 升级后开始传，需在 core/mem0_llm.py 实现"
            )
        system = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        rest = [m for m in messages if m.get("role") != "system"]
        return self._adapter.chat(system, rest, response_format=kwargs.get("response_format"))


__all__ = ["AdapterLLM"]
