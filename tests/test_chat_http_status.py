# -*- coding: utf-8 -*-
"""非流式 chat 的失败**放行**契约：上游返回不完整响应 ≠ 我们的服务端故障。

原先本文件锁的是「路由自己的 `_http_error_from` 映射」。缺陷 38 收口后那张表**搬到了**
``web/server.py`` 的统一出口（`_LLM_ERROR_STATUS`，按判别键 `kind` 查）—— 路由层不再知道
「哪个 finish_reason 配哪个码」。本文件锁两件事，都是「`_do_chat` 一个异常都不吞」：
**LLM 侧未完成终态原样放行**（放行了才轮得到统一出口配码，就地吞成 500 就是回到修复前的
样子），**普通失败也原样上抛**（改后由统一出口记带堆栈的 ERROR 并告警）。

码本身的契约（400/502/503 + 未登记兜 502）随表搬家 → ``tests/test_domain_exception_exit.py
::test_b2_llm_incomplete_maps_by_finish_reason``，此处不重复断言。
"""
import asyncio
from types import SimpleNamespace

import pytest

import routers.chat as chat
from adapters.llm_adapter import IncompleteResponseError
from core.text_manager import new_session_entry


def _err(finish_reason: str) -> IncompleteResponseError:
    return IncompleteResponseError(finish_reason, "chat")


# ── 接线：_do_chat 必须放行 LLM 侧异常，且只放行这一族 ──────────────────

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
    # 条目形状只从 `new_session_entry` 拿（不手搓 dict），也不在这里补字段 ——
    # 在调用点补等于把「条目长什么样」又拆成两处定义。
    session = new_session_entry(_Engine(exc), None, "u1")

    async def _fake_ensure(session_id, storage, sessions, user_id=""):
        return session

    monkeypatch.setattr(chat, "_ensure_session", _fake_ensure)
    return session


def test_do_chat_lets_llm_incomplete_propagate(monkeypatch):
    """未完成终态必须**向外传播**（不是包成 HTTPException）—— 统一出口才配得了码。

    变异：把 `_do_chat` 的 `try/except Exception` 加回来（`engine.chat` 外面恢复到删除包装
    前的样子）→ 这里看到的是 HTTPException(500) 而不是 IncompleteResponseError，红。
    """
    _wire(monkeypatch, _err("insufficient_system_resource"))
    with pytest.raises(IncompleteResponseError):
        asyncio.run(chat._do_chat("s1", "hi", storage=None, sessions={}, user_id="u1"))


def test_do_chat_lets_plain_failure_reach_the_unified_exit(monkeypatch):
    """普通失败（非 LLM 侧）也**原样上抛** —— 就地 500 会绕过全局处理器的日志与告警。

    变异：在 `_do_chat` 加回 `except Exception: raise HTTPException(500, …)` →
    这里看到的是 HTTPException 而不是 RuntimeError，红。
    """
    _wire(monkeypatch, RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        asyncio.run(chat._do_chat("s1", "hi", storage=None, sessions={}, user_id="u1"))
