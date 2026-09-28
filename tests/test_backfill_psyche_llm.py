# -*- coding: utf-8 -*-
"""回填脚本的 LLM 调用走项目适配器（补充 1 步骤 4）。

原先它自己 `OpenAI(...)` + `model="deepseek-chat"`：这个名字 2026-07-24 起已停用，
而且这条调用同时绕开关闭思考与出站守卫。改走适配器后三件事一起解决 —— 模型名跟着
`default_model()` 走、请求带关闭思考、守卫按正常流程判定。

**为什么在**线程**里跑**：脚本是独立进程、请求之外调用，`geo_call_guard` 对它是
fail-closed（`web/llm_gate.py:66-70`）。新线程默认不继承 ContextVar，正好复现
「没有请求身份」这个现场；`system_llm_context()` 若被去掉，这里就红。
"""
from __future__ import annotations

import json
import threading

import pytest

import adapters.llm_adapter as M
from adapters.llm_adapter import default_model, set_call_guard
from scripts.backfill_psyche import _call_llm_for_psyche
from web.llm_gate import geo_call_guard

DEEPSEEK_OFF = {"thinking": {"type": "disabled"}}
_REPLY = json.dumps({
    "openness": 3, "conscientiousness": 3, "extraversion": 3,
    "agreeableness": 3, "neuroticism": 3, "affinity_baseline": 50,
    "volatility": "适中", "grudge_inertia": "一般",
    "triggers": ["被轻视"], "soft_spots": ["旧友"],
}, ensure_ascii=False)


class _Msg:
    content = _REPLY


class _Choice:
    message = _Msg()
    finish_reason = "stop"


class _Resp:
    choices = [_Choice()]
    usage = None


class _Completions:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp()


class _FakeOpenAI:
    """适配器构造时建的替身（同步、异步同一份）：记请求体，不出网。"""

    completions = None  # 每个实例各一个，见 __init__

    def __init__(self, **kwargs):
        self.completions = _Completions()
        self.chat = type("_Chat", (), {"completions": self.completions})()

    async def close(self):
        pass


@pytest.fixture
def fake_openai(monkeypatch):
    """两处客户端构造点都换掉：适配器那一份，以及**改前的脚本自己建的那一份** ——
    后者不换，这条用例会真的发一次出网请求（401 才回来），红得再对也不能这么红。"""
    import openai

    made: list[_FakeOpenAI] = []

    def _ctor(**kwargs):
        inst = _FakeOpenAI(**kwargs)
        made.append(inst)
        return inst

    monkeypatch.setattr(M, "OpenAI", _ctor)
    monkeypatch.setattr(M, "AsyncOpenAI", _ctor)
    monkeypatch.setattr(openai, "OpenAI", _ctor)
    return made


@pytest.fixture
def gate():
    """生产守卫装上 —— 与 `tests/test_mem0_llm.py::gate` 同一处注册。"""
    prev = set_call_guard(geo_call_guard)
    try:
        yield
    finally:
        set_call_guard(prev)


def _call_in_fresh_thread(prompt: str) -> dict:
    """在新线程里调用（默认不继承 ContextVar = 没有请求身份）。异常带回主线程再抛。"""
    box: dict = {}

    def target():
        try:
            box["value"] = _call_llm_for_psyche("sk-test-fake", prompt)
        except BaseException as exc:  # noqa: BLE001 — 原样带给主线程
            box["exc"] = exc

    t = threading.Thread(target=target)
    t.start()
    t.join()
    return box


class TestBackfillGoesThroughTheAdapter:
    def test_request_uses_the_configured_model_and_disables_thinking(self, fake_openai, gate):
        box = _call_in_fresh_thread("角色名: 测试")

        assert "exc" not in box, f"出站被拒：{box.get('exc')!r}"
        assert box["value"] is not None, "响应没解析出 psyche"
        assert fake_openai, "脚本没有经适配器建客户端"

        call = fake_openai[0].completions.calls[0]
        assert call["model"] == default_model()
        assert call["extra_body"] == DEEPSEEK_OFF
        assert call["temperature"] == 0.3 and call["max_tokens"] == 1024

    def test_no_caller_identity_is_not_fail_closed_rejected(self, fake_openai, gate):
        """`system_llm_context()` 是真身：去掉它，守卫会抛 LLMCallerMissing。"""
        assert _call_in_fresh_thread("角色名: 测试").get("value") is not None
