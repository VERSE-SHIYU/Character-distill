# -*- coding: utf-8 -*-
"""聊天接口默认走 Agent 工具编排。

不带 ``agent_mode`` 字段的请求（API 直连、旧客户端）必须与前端默认一致地进入
工具编排路径；只有显式传 ``false`` 才回到旧的「无条件注入」路径。前端那一侧的
默认值由 ``web/frontend/src/store/agentModeDefault.test.js`` 钉住。
"""
from routers.chat import ChatRequest


def test_chat_request_defaults_to_agent_mode():
    req = ChatRequest(session_id="s", message="m")
    assert req.agent_mode is True


def test_explicit_false_still_selects_the_legacy_path():
    req = ChatRequest(session_id="s", message="m", agent_mode=False)
    assert req.agent_mode is False
