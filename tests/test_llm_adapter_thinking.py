# -*- coding: utf-8 -*-
"""供应商方言层回归：四个调用点都必须按 base_url/model 发对应的「关闭思考」payload。

历史：四处曾硬编码 extra_body={"enable_thinking": False} —— 那是 Qwen 方言，DeepSeek
不认、静默忽略 → 思考照开、与正文共享 max_tokens → 正文被吃光返回空 content。
本测试锁的是「意图 → 方言」的解析结果，以及四个调用点都接上了这一控制点。
"""
import asyncio
from types import SimpleNamespace

import pytest

import adapters.llm_adapter as M
from adapters.llm_adapter import LLMAdapter

DEEPSEEK_OFF = {"thinking": {"type": "disabled"}}
QWEN_OFF = {"enable_thinking": False}


class _Msg:
    content = "ok"


class _Choice:
    message = _Msg()


class _Resp:
    choices = [_Choice()]
    usage = None


class _Delta:
    content = "a"


class _StreamChunk:
    usage = None
    choices = [SimpleNamespace(delta=_Delta())]


class _Completions:
    def __init__(self):
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return iter([_StreamChunk()]) if kwargs.get("stream") else _Resp()


class _AsyncCompletions:
    def __init__(self):
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _Resp()


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _Client:
    def __init__(self, completions=None):
        self.chat = _Chat(completions or _Completions())


class _FakeOpenAIClient:
    """替代真实 OpenAI/AsyncOpenAI 构造：本沙箱里真实构造会在 httpx ssl
    create_default_context 上偶发挂起（环境性，非代码缺陷）。"""

    def __init__(self, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _no_real_openai(monkeypatch):
    monkeypatch.setattr(M, "OpenAI", _FakeOpenAIClient)
    monkeypatch.setattr(M, "AsyncOpenAI", _FakeOpenAIClient)


def _make_llm(base_url=None, model=None) -> LLMAdapter:
    return LLMAdapter(api_key="sk-test-fake", base_url=base_url, model=model)


# ── 方言解析 ──────────────────────────────────────────────────────────

def test_dialect_deepseek_by_default_base_url():
    assert _make_llm()._request_options() == DEEPSEEK_OFF


def test_dialect_qwen_by_base_url():
    llm = _make_llm(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert llm._request_options() == QWEN_OFF


def test_dialect_qwen_by_model_name():
    llm = _make_llm(base_url="https://gateway.example.com/v1", model="qwen-plus")
    assert llm._request_options() == QWEN_OFF


def test_dialect_unknown_sends_no_extra_body():
    # 安全默认：认不出供应商就不发任何 extra_body，宁可开着思考，
    # 也不发一个可能被 400 拒绝的未知字段（那会让该用户所有调用永久失败）。
    llm = _make_llm(base_url="https://api.moonshot.cn/v1", model="kimi-k2")
    assert llm._request_options() == {}


def test_request_options_returns_copy():
    llm = _make_llm()
    llm._request_options()["thinking"] = "mutated"
    assert llm._request_options() == DEEPSEEK_OFF


# ── 四个调用点都接上控制点 ─────────────────────────────────────────────

def test_chat_sends_dialect_payload():
    llm = _make_llm()
    fake = _Client()
    llm._client = fake
    assert llm.chat("sys", [{"role": "user", "content": "hi"}]) == "ok"
    assert fake.chat.completions.kwargs["extra_body"] == DEEPSEEK_OFF


def test_async_chat_sends_dialect_payload():
    llm = _make_llm()
    fake = _Client(_AsyncCompletions())
    llm._async_client = fake
    result, _usage = asyncio.run(llm.async_chat("sys", [{"role": "user", "content": "hi"}]))
    assert result == "ok"
    assert fake.chat.completions.kwargs["extra_body"] == DEEPSEEK_OFF


def test_chat_stream_sends_dialect_payload():
    llm = _make_llm(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    fake = _Client()
    llm._client = fake
    assert list(llm.chat_stream("sys", [{"role": "user", "content": "hi"}])) == ["a"]
    assert fake.chat.completions.kwargs["extra_body"] == QWEN_OFF


def test_chat_with_tools_sends_dialect_payload():
    llm = _make_llm()
    fake = _Client()
    llm._client = fake
    llm.chat_with_tools("sys", [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert fake.chat.completions.kwargs["extra_body"] == DEEPSEEK_OFF
