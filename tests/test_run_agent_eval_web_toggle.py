# -*- coding: utf-8 -*-
"""评测脚本的引擎开着联网开关（spec: docs/specs/mcp-and-eval-followups.md 检查 B1）。

评测量的是「工具调用决策」，评测集里有 realtime 用例期望调 web_search
（tests/eval/agent_eval_cases.jsonl:31）。#170 之后工具清单按开关过滤，脚本若沿用
`ChatEngine` 的默认（关）就恒调不到 —— 老路径同样看开关（core/context_engine.py:332）。
"""

from __future__ import annotations

import scripts.run_agent_eval as ev
from evidence_fakes import make_card


def test_eval_engine_turns_web_search_on():
    """三种模式共用的构造出口把开关打开 —— 删掉那一行这条就红。"""
    engine = ev._make_eval_engine(llm=None, card=make_card())

    assert engine._ctx_engine.web_search_enabled is True
