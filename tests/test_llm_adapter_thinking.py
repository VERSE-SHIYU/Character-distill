# -*- coding: utf-8 -*-
"""Regression: async_chat 必须与 sync chat 一致禁用思考。

Map 阶段 async_chat 若漏传 enable_thinking=False，deepseek-v4-pro 思考预算耗尽
会返回空 content → MapReduce 结构失败（Step-2 查实）。此测试锁住该参数。
"""
import asyncio

from adapters.llm_adapter import LLMAdapter


class _Msg:
    content = "ok"


class _Ch:
    message = _Msg()


class _Resp:
    choices = [_Ch()]
    usage = None


class _Completions:
    def __init__(self):
        self.kwargs = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _Resp()


class _Chat:
    def __init__(self):
        self.completions = _Completions()


class _FakeClient:
    def __init__(self):
        self.chat = _Chat()


def test_async_chat_sends_enable_thinking_false():
    llm = LLMAdapter(api_key="sk-test-fake")
    fake = _FakeClient()
    llm._async_client = fake
    result, usage = asyncio.run(llm.async_chat("sys", [{"role": "user", "content": "hi"}]))
    assert result == "ok"
    assert fake.chat.completions.kwargs["extra_body"] == {"enable_thinking": False}
