# -*- coding: utf-8 -*-
"""本次请求的用户身份（缺陷 35）。

只装身份，**不装依赖** —— storage 是进程级单例依赖（不随请求变），走构造注入
（`web/deps.get_distiller`）；两者塞进同一个变量，生命周期就说不清了。

唯一的写口是 `web/server.py` 的 `AuthMiddleware`：在它那个**单一** `call_next`
出口前设一次（与 `web/request_context.LLM_CALLER` 同一处、同一理由）。
不 reset 是刻意的：端点体与流式响应体在 `call_next` 返回**之后**才读，
在 finally 里 reset 会把它们打回默认值（L8 实测过反向形态）。

传播不用本模块做任何事：`core.concurrency` 的 `ctx_thread` / `ctx_submit` 走
`contextvars.copy_context()`，派生线程与线程池自动带着它。

读不到 = None = 「不在请求上下文里」或「匿名请求」。记账不做安全判定，故
两者不必区分，给 `default=None` 即可（`LLM_CALLER` 那边 fail-closed 才需要无默认值）。
"""
from __future__ import annotations

import contextvars

_USER_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_user_id", default=None
)


def set_request_user_id(user_id: str | None) -> None:
    """设本次请求的用户身份。**只应由 `AuthMiddleware` 调用**（判据见
    `tests/test_usage_identity_context.py` 的唯一写口锁）。"""
    _USER_ID.set(user_id)


def current_user_id() -> str | None:
    """当前上下文的用户身份；不在请求上下文里时为 None。"""
    return _USER_ID.get()


__all__ = ["current_user_id", "set_request_user_id"]
