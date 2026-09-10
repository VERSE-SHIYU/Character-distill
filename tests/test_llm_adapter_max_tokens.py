# -*- coding: utf-8 -*-
"""max_tokens 取值阶梯：显式 arg > LLM_MAX_TOKENS > config.yaml > 4096。

env 必须压过 config.yaml，否则这个出口是死的（镜像里总有 config.yaml）。默认值
本次不动 —— 4096 无量化依据，换新值要先按 AGENTS.md「蒸馏管线」第四节拿数据。
"""
import pytest

import adapters.llm_adapter as M
from adapters.llm_adapter import LLMAdapter


class _FakeOpenAIClient:
    def __init__(self, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(M, "OpenAI", _FakeOpenAIClient)
    monkeypatch.setattr(M, "AsyncOpenAI", _FakeOpenAIClient)
    monkeypatch.setattr(M, "load_dotenv", lambda *a, **k: None)  # 隔离真实 .env
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)


def _cfg(tmp_path, text="llm:\n  max_tokens: 1234\n"):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _llm(tmp_path, **kwargs) -> LLMAdapter:
    return LLMAdapter(config_path=_cfg(tmp_path), api_key="sk-test-fake", **kwargs)


def test_env_beats_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "777")
    assert _llm(tmp_path)._max_tokens == 777


def test_config_used_without_env(tmp_path):
    assert _llm(tmp_path)._max_tokens == 1234


def test_default_is_4096_when_config_silent(tmp_path):
    llm = LLMAdapter(config_path=_cfg(tmp_path, "llm: {}\n"), api_key="sk-test-fake")
    assert llm._max_tokens == 4096


def test_explicit_arg_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "777")
    assert _llm(tmp_path, max_tokens=99)._max_tokens == 99


def test_garbage_env_falls_back_to_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "abc")
    assert _llm(tmp_path)._max_tokens == 1234
