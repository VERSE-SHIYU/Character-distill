# -*- coding: utf-8 -*-
"""mem0 的记忆提炼走项目适配器：注入点、请求形态、出站守卫、tools。

mem0 自带的两条 LLM 提供方都关不掉思考、也绕开项目的守卫与重试预算（约束 2/3），
故 `core/memory_manager.py` 在 `Memory.from_config` 之后把 `Memory.llm` 换成
`core.mem0_llm.AdapterLLM`。这里用**仓内锁定的真实 mem0（2.0.20）**跑一遍 `add`，
钉住注入点就是 `Memory.llm` 这一个属性、以及落到适配器的请求形态 —— mem0 一升级
就会在这里红，不必等到线上没有记忆。
"""
from __future__ import annotations

import os

# 必须在 import mem0 **之前**：mem0 在 import 期读这个变量（memory/telemetry.py）。
os.environ["MEM0_TELEMETRY"] = "False"

import json  # noqa: E402
from contextlib import contextmanager  # noqa: E402

import pytest  # noqa: E402

import adapters.llm_adapter as M  # noqa: E402
import mem0.embeddings.openai as _m0_embed  # noqa: E402
import mem0.llms.openai as _m0_llm  # noqa: E402
from adapters.llm_adapter import (  # noqa: E402
    LLMCallRefused, LLMAdapter, default_model, set_call_guard,
)
from core.mem0_llm import AdapterLLM  # noqa: E402
from core.memory_manager import MemoryManager  # noqa: E402
from core.request_context import Caller, LLM_CALLER  # noqa: E402
from web.llm_gate import geo_call_guard  # noqa: E402

DIM = 1024
DEEPSEEK_OFF = {"thinking": {"type": "disabled"}}
DOMESTIC_IP = "114.114.114.114"   # 非白名单主机 + 大陆 IP = 拦
WHITELISTED = "https://api.deepseek.com"


# ── 替身 ──────────────────────────────────────────────────────────────

class _Msg:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self):
        self.message = _Msg(json.dumps({"memory": []}))  # mem0 解析得出「没提炼出记忆」
        self.finish_reason = "stop"


class _Resp:
    def __init__(self):
        self.choices = [_Choice()]
        self.usage = None


class _Completions:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp()


class _FakeClient:
    """适配器的 OpenAI 替身：记下请求体，不回网络。"""

    def __init__(self):
        self.chat = type("_Chat", (), {"completions": _Completions()})()


class _StubEmbedder:
    """嵌入替身（生产用 Mem0BridgeEmbedder，同形）：`add` 先检索既有记忆，不该出网。"""

    def embed(self, text, memory_action=None):
        return [0.0] * DIM

    def embed_batch(self, texts, memory_action="add"):
        return [[0.0] * DIM for _ in texts]


class _ForbiddenOpenAI:
    """mem0 自带提供方的构造替身 —— 用上它就说明注入没生效，直接炸。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    @property
    def chat(self):
        raise AssertionError("mem0 自带的 LLM 客户端被调用了 —— Memory.llm 的注入没生效")


class _QuietOpenAI:
    def __init__(self, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _no_real_openai(monkeypatch):
    # 本沙箱里真实构造 OpenAI 会在 httpx ssl create_default_context 上偶发挂起；
    # mem0 的两条提供方在 `Memory.from_config` 里各建一个客户端 —— 都不该被调。
    monkeypatch.setattr(M, "OpenAI", _QuietOpenAI)
    monkeypatch.setattr(M, "AsyncOpenAI", _QuietOpenAI)
    monkeypatch.setattr(_m0_llm, "OpenAI", _ForbiddenOpenAI)
    monkeypatch.setattr(_m0_embed, "OpenAI", _ForbiddenOpenAI)


# ── 装配 ──────────────────────────────────────────────────────────────

def _make_adapter(base_url: str | None = WHITELISTED) -> tuple[LLMAdapter, _FakeClient]:
    adapter = LLMAdapter(api_key="sk-test-fake", base_url=base_url)
    fake = _FakeClient()
    adapter._client = fake
    return adapter, fake


def _build_memory(tmp_path, adapter: LLMAdapter):
    from mem0 import Memory

    mem = Memory.from_config({
        "vector_store": {
            "provider": "qdrant",
            "config": {"path": str(tmp_path / "qdrant"), "on_disk": True,
                       "embedding_model_dims": DIM},
        },
        "llm": {
            "provider": "openai",
            "config": {"model": "placeholder", "api_key": "sk-test-fake",
                       "openai_base_url": WHITELISTED},
        },
        "embedder": {
            "provider": "openai",
            "config": {"model": "text-embedding-v4", "api_key": "sk-test-fake",
                       "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                       "embedding_dims": DIM},
        },
        "history_db_path": str(tmp_path / "history.db"),
    })
    mem.embedding_model = _StubEmbedder()
    mem.llm = AdapterLLM(adapter)
    return mem


def _add(mem, text: str = "我喜欢喝美式"):
    return mem.add([{"role": "user", "content": text}], user_id="card1")


# ── 注入点与请求形态 ───────────────────────────────────────────────────

def test_add_goes_through_the_adapter_in_json_mode_with_thinking_off(tmp_path):
    adapter, fake = _make_adapter()
    mem = _build_memory(tmp_path, adapter)

    _add(mem)

    assert len(fake.chat.completions.calls) == 1, "每轮 add 应恰好一次 LLM 调用"
    call = fake.chat.completions.calls[0]
    assert call["model"] == default_model(), "模型名没走配置里的那一个"
    assert call["extra_body"] == DEEPSEEK_OFF, "没关思考"
    assert call["response_format"] == {"type": "json_object"}, "JSON 输出没透传"
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1]["role"] == "user"
    assert "我喜欢喝美式" in call["messages"][1]["content"]


def test_generate_response_rejects_tools():
    """mem0 的向量记忆不带 tools；带了就是换了调用形态，宁可炸也不静默忽略。"""
    adapter, _ = _make_adapter()
    with pytest.raises(NotImplementedError):
        AdapterLLM(adapter).generate_response(
            [{"role": "user", "content": "hi"}], tools=[{"type": "function"}])


# ── 出站守卫：沿用调用方身份 ───────────────────────────────────────────

@pytest.fixture
def gate():
    prev = set_call_guard(geo_call_guard)
    try:
        yield
    finally:
        set_call_guard(prev)


@contextmanager
def _as_caller(ip: str):
    token = LLM_CALLER.set(Caller(ip=ip, user_id="usr_test"))
    try:
        yield
    finally:
        LLM_CALLER.reset(token)


def test_memory_manager_injects_the_adapter_as_the_llm(monkeypatch):
    """上面几条自己注入，锁不住生产那行 —— 这里跑 `MemoryManager.__init__` 本体的接线。

    `Memory.from_config` 换成空壳：本测试只问「构造完之后 `Memory.llm` 是谁」，
    qdrant/配置那些不是这里的事。
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-fake")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-fake")

    import mem0

    class _BareMemory:
        pass

    monkeypatch.setattr(mem0.Memory, "from_config", lambda cfg: _BareMemory())

    mm = MemoryManager({})

    assert mm.enabled, "构造没走通"
    assert isinstance(mm._mem.llm, AdapterLLM), "MemoryManager 没把 Memory.llm 接到适配器上"
    assert mm._mem.llm._adapter.model == default_model()


def test_add_is_refused_for_a_domestic_user_on_a_non_whitelisted_host(tmp_path, gate):
    """提炼发的是该用户的聊天 → 出站归属该用户，大陆 IP + 非白名单主机必须被拦。"""
    adapter, fake = _make_adapter(base_url="https://api.openai.com/v1")
    mem = _build_memory(tmp_path, adapter)

    with _as_caller(DOMESTIC_IP), pytest.raises(Exception) as err:
        _add(mem)

    # mem0 会把提供方的异常包成 LLMError，拒绝理由要能从根因里读出来。
    assert isinstance(err.value.__cause__, LLMCallRefused), err.value
    assert fake.chat.completions.calls == [], "被拦下的调用不该真的发出去"


def test_add_passes_the_gate_for_a_whitelisted_host(tmp_path, gate):
    adapter, fake = _make_adapter(base_url=WHITELISTED)
    mem = _build_memory(tmp_path, adapter)

    with _as_caller(DOMESTIC_IP):
        _add(mem)

    assert len(fake.chat.completions.calls) == 1
