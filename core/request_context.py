# -*- coding: utf-8 -*-
"""请求身份上下文：这一次请求是**谁**发起的（spec v5 §2.9）。

只装身份，不装策略。geo 判定（白名单、境内拦截、审计）在 `web/llm_gate.py` ——
那扇门 import 本模块，**不重新定义这里的任何东西**。分开的理由是职责线本身：
「谁在调」是事实，「许不许调」是策略；混在一个模块里，上下文那一半会被策略那一半
的落地时间拖着走。

**住在 `core/` 而不是 `web/`**：身份有两个消费方 —— 门（`web/`）与记账出口
（`core/distiller.py`）。`core` 不许 import `web`（L13），故共享的上下文只能下沉到
`core`。反过来「在 core 里另起一份身份 ContextVar」不是解法：`web/` 没有
`__init__.py`，两处定义出的 `LLM_CALLER` 会是**两个** ContextVar，中间件设的值那边
根本读不到（开头那条约定挡的就是这个）。**身份只有 `LLM_CALLER` 这一份。**

写口只有两处，都在同一个出口形态上：`web/server.py` 的 `AuthMiddleware`（请求内，
在它那个**单一** `call_next` 出口前设一次）与下面的 `system_llm_context`（请求外，
显式声明）。设在出口前不是风格 —— 设在 `call_next` 之后，下游（端点体、流式响应体）
读到的就是默认值（实测，见 tests/test_llm_access_gate.py 的 L8）。
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Caller:
    """一次请求的调用方身份。`ip` 可为 None（取不到真实 IP 时不在本模块兜底）。"""

    ip: str | None
    user_id: str | None


class _SystemCaller:
    """内部调用的身份标记。判据是 `is SYSTEM`，**不是字段比较** —— 否则某个 ip 与
    user_id 恰好都是 None 的真实请求就会被误当成系统调用放行。"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "SYSTEM"


SYSTEM = _SystemCaller()

#: 不设默认值：`.get(None)` 读到的 None 唯一地表示「不在请求上下文里」，门据此
#: fail-closed。给一个默认值会让「没设」与「设成了那个值」不可分辨。
LLM_CALLER: contextvars.ContextVar[Caller | _SystemCaller] = contextvars.ContextVar("llm_caller")


@contextmanager
def system_llm_context() -> Iterator[None]:
    """把当前上下文声明为「系统内部调用」—— fail-closed 的**唯一**合法出口。

    生产代码里当前零调用点（审计现跑：请求之外的线程/定时任务都不触达 adapter，
    见 tests/census_llm_call_contexts.py 的归属表）。它存在是为了让「将来出现的
    请求外入口」有一处**可声明**的地方，而不是让调用方随手绕开门。
    """
    token = LLM_CALLER.set(SYSTEM)
    try:
        yield
    finally:
        LLM_CALLER.reset(token)


def current_user_id() -> str | None:
    """当前上下文的用户身份；拿不到就是 None。

    非门消费方（如记账出口）走这个读口，不必认识 `Caller` / `SYSTEM`。`SYSTEM`
    （请求外调用）也返回 None —— 系统调用没有用户归属，账该落空，不该落给某个人。
    """
    caller = LLM_CALLER.get(None)
    return caller.user_id if isinstance(caller, Caller) else None


__all__ = ["Caller", "SYSTEM", "LLM_CALLER", "system_llm_context", "current_user_id"]
