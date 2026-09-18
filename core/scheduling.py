"""跨 loop 投递的**唯一**原语（spec v5 §2.2）。

背景：后台线程不能碰 asyncpg 池 —— 池绑定在主 event loop 上，从别的 loop 用会炸。
所以「把这些协程送回主 loop 跑」这件事在生产里有三个写法各写一遍（`deps.run_on_main_loop`、
`core.utils` 自建 loop 的线程、审计投递），本模块把语义收敛到一处。

方向：core 不认识 web。`web/deps.py` 捕获主 loop 时**向下注册**实现
（`scheduling.set_loop_submitter`）；未注册时的回退语义在这里定义一次。

未注册回退（只有独立进程/测试会走到）：
  - ``wait=True``：``asyncio.run`` 并**响亮**警告 —— 调用方以为在等主 loop 的结果，
    实际是在当前线程另起了个 loop，池会被跨 loop 触碰。warning 是给这个错觉留的痕迹。
  - ``wait=False``：当前线程有运行中的 loop 就 ``create_task``，否则 ``asyncio.run``。
"""
from __future__ import annotations

import asyncio
import warnings
from typing import Any, Callable

LoopSubmitter = Callable[..., Any]

_submitter: LoopSubmitter | None = None


def set_loop_submitter(fn: LoopSubmitter | None) -> None:
    """注册投递实现（契约：``fn(coro, *, wait, timeout)``）；传 None 注销。"""
    global _submitter
    _submitter = fn


def get_loop_submitter() -> LoopSubmitter | None:
    """当前注册的实现（无则 None）—— 与 `set_loop_submitter` 配对的读口。"""
    return _submitter


def submit_to_main_loop(coro, *, wait: bool = True, timeout: float = 600) -> Any:
    """把 *coro* 投递到主 loop。

    已注册实现时委托给它，并**原样返回它的返回值**（`wait=True` 时是协程的结果）。
    未注册时走下文的回退语义。
    """
    if _submitter is not None:
        return _submitter(coro, wait=wait, timeout=timeout)

    if wait:
        warnings.warn(
            "[scheduling] No loop submitter registered, falling back to asyncio.run() "
            "— 这不是主 loop，跨 loop 复用的连接池会炸"
        )
        return asyncio.run(coro)

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return running.create_task(coro)
