# -*- coding: utf-8 -*-
"""非流式 chat 的失败状态码契约：上游返回不完整响应 ≠ 我们的服务端故障。

修复前一律 500「操作失败，请稍后重试」—— content_filter 尤其糟：那是用户输入问题，
用户会当成 bug 反复重试。本测试锁三件事：
  1. 三类未完成终态各给可辨状态码（content_filter→400，length→502，资源不足→503）
  2. 未登记的未完成终态兜 502（上游问题），不兜 500（我们的故障）
  3. 非 LLM 失败仍是 500，不被这层吞掉
外加接线锁：_do_chat 必须真的走这条映射（删掉调用 → 断言变红）。
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routers.chat as chat
from adapters.llm_adapter import IncompleteResponseError


def _err(finish_reason: str) -> IncompleteResponseError:
    return IncompleteResponseError(finish_reason, "chat")


# ── 映射本身 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("finish_reason,status", [
    ("content_filter", 400),
    ("length", 502),
    ("insufficient_system_resource", 503),
])
def test_status_by_finish_reason(finish_reason, status):
    he = chat._http_error_from(_err(finish_reason))
    assert he is not None and he.status_code == status


def test_unregistered_incomplete_defaults_to_502_not_500():
    he = chat._http_error_from(_err("some_new_incomplete_value"))
    assert he.status_code == 502
    assert he.status_code != 500


def test_non_llm_failure_is_not_mapped():
    assert chat._http_error_from(RuntimeError("boom")) is None


def test_status_message_matches_sse_wording():
    """同一 finish_reason，HTTP 文案与 SSE 帧同口径（都取 adapter 的上屏表）。"""
    he = chat._http_error_from(_err("content_filter"))
    assert he.detail == chat._stream_error_payload(_err("content_filter"))["error"]


# ── 接线：_do_chat 必须真的调用映射 ──────────────────────────────────

class _Engine:
    def __init__(self, exc):
        self._exc = exc
        self.history = []
        self._ctx_engine = SimpleNamespace(web_search_enabled=False)
        self.affinity_enabled = True
        self.agent_mode = False

    def chat(self, *args, **kwargs):
        raise self._exc


def _wire(monkeypatch, exc):
    session = {"engine": _Engine(exc), "lock": asyncio.Lock(), "user_id": "u1"}

    async def _fake_ensure(session_id, storage, sessions, user_id=""):
        return session

    monkeypatch.setattr(chat, "_ensure_session", _fake_ensure)
    return session


def test_do_chat_wires_the_mapping(monkeypatch):
    _wire(monkeypatch, _err("insufficient_system_resource"))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat._do_chat("s1", "hi", storage=None, sessions={}, user_id="u1"))
    assert ei.value.status_code == 503


def test_do_chat_still_500s_on_plain_failure(monkeypatch):
    _wire(monkeypatch, RuntimeError("boom"))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(chat._do_chat("s1", "hi", storage=None, sessions={}, user_id="u1"))
    assert ei.value.status_code == 500
