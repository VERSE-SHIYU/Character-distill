# -*- coding: utf-8 -*-
"""派生（裸线程 / 线程池）与上下文传播的**唯一**原语（spec v5 §2.3）。

为什么需要包装器：`threading.Thread` 与 `ThreadPoolExecutor.submit` 都不拷贝
contextvar —— 调用方上下文里的东西（请求身份、OTel span）在工作线程里一律读不到。

形态 —— capture 在调用方线程，其余三者同处一个 `ctx.run`：

    调用方线程   ctx = copy_context()；states = [c.capture() for c in carriers]
    工作线程     ctx.run(body)，body 依次 restore → fn → 逆序 release

**三者必须在同一个 `ctx.run` 内。** 这不是风格：`restore` 在拷贝出的 Context 里
set 出的 token，只有回到那个 Context 才 release 得掉。跑到外面去会得到一个
「token 属另一个 Context」的失败，而 OTel 的 `detach` 把它吞进日志（只留一条
"Failed to detach context"），调用方看不到异常 —— 真实后果是父上下文一直挂在拷贝出
的 Context 里没被释放。故 release 与 restore 在同一层用 finally 配对，不提到
`ctx.run` 之外（判据与变异见 tests/test_llm_access_gate.py 的 L15）。

OTEL 开还是关走**同一条**路径：载体在开关关闭时三步皆 no-op（见 telemetry）。
本模块内不出现 opentelemetry（L15）。
"""
from __future__ import annotations

import contextvars
import threading
from typing import Any, Callable, Protocol


class ContextCarrier(Protocol):
    """一种「可跨 context 搬运」的上下文。三步的契约：

    - `capture() -> state`：在**调用方**线程取一份可搬运的状态；
    - `restore(state) -> token`：在**工作线程的拷贝 context 内**恢复它；
    - `release(token)`：与 `restore` **同一个** `ctx.run` 内撤销。
    """

    def capture(self) -> Any: ...

    def restore(self, state: Any) -> Any: ...

    def release(self, token: Any) -> None: ...


_carriers: list[ContextCarrier] = []


def register_context_carrier(carrier: ContextCarrier) -> None:
    """登记一个载体（追加）。web 在启动时向下注册实现。"""
    _carriers.append(carrier)


def get_context_carriers() -> list[ContextCarrier]:
    """当前已登记的载体 —— 与 `register_context_carrier` 配对的读口。

    返回**副本**：调用方拿到的是快照，改它不会悄悄改派生路径的行为。
    """
    return list(_carriers)


def set_context_carriers(carriers: list[ContextCarrier]) -> None:
    """整体替换已登记集合。测试用来按**原集合**还原（不是清空 —— 生产在导入期注册过）。"""
    global _carriers
    _carriers = list(carriers)


def _derive_body(fn: Callable[[], Any]) -> Callable[[], Any]:
    """把 *fn* 包成「在拷贝出的 context 内、载体已恢复」的可调用体。

    `copy_context()` 与各载体 `capture()` 都在**调用方**线程执行 —— 这是必须的，
    捕获的就是那一刻的上下文。返回的闭包交给工作线程，由它 `ctx.run(...)`。
    """
    ctx = contextvars.copy_context()
    states = [(carrier, carrier.capture()) for carrier in _carriers]

    def _body():
        tokens: list[tuple[ContextCarrier, Any]] = []
        try:
            for carrier, state in states:
                tokens.append((carrier, carrier.restore(state)))
            return fn()
        finally:
            for carrier, token in reversed(tokens):
                carrier.release(token)

    return lambda: ctx.run(_body)


def ctx_thread(target: Callable, args: tuple = (), kwargs: dict | None = None,
               *, daemon: bool = True, name: str | None = None) -> threading.Thread:
    """裸线程 + 上下文传播。"""
    if kwargs is None:
        kwargs = {}
    run = _derive_body(lambda: target(*args, **kwargs))
    return threading.Thread(target=run, daemon=daemon, name=name)


def ctx_submit(pool, fn: Callable, *args, **kwargs):
    """`ThreadPoolExecutor.submit` + 上下文传播（实测裸 submit 不拷 contextvar）。"""
    return pool.submit(_derive_body(lambda: fn(*args, **kwargs)))


__all__ = [
    "ContextCarrier", "register_context_carrier", "get_context_carriers",
    "set_context_carriers", "ctx_thread", "ctx_submit",
]
