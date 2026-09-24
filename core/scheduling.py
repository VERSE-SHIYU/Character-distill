"""跨 loop 投递的**唯一**原语（spec v5 §2.2）。

背景：后台线程不能碰 asyncpg 池 —— 池绑定在主 event loop 上，从别的 loop 用会炸。
所以「把这些协程送回主 loop 跑」这件事在生产里有三个写法各写一遍（`deps.run_on_main_loop`、
`core.utils` 自建 loop 的线程、审计投递），本模块把语义收敛到一处。

方向：core 不认识 web。`web/deps.py` 捕获主 loop 时**向下注册**实现
（`scheduling.set_loop_submitter`）；未注册时这里**当场报错**，没有退路。

未注册为什么不留退路：以前 ``wait=True`` 走 ``asyncio.run``、``wait=False`` 走
``create_task``，看着像「独立进程和测试也能用」，其实是拿**当前线程**另起一个 loop ——
池被跨 loop 触碰（正是本模块要消灭的那件事），而且测试会借这条路在错误的线程上干活：
实测退路被命中 22 次，其中 21 次被调用方的宽 ``except`` 吞掉，用例照常显示通过 ——
测的根本不是生产形状。独立进程要投递就自己注册（``scripts/smoke_eval_e2e.py``、
``scripts/integration_check.py`` 就是这么做的）。
"""
from __future__ import annotations

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
    未注册时抛 `RuntimeError` —— 不许在本线程另起 loop：那会跨 loop 触碰连接池，
    而且让测试在错误的线程上过关（见模块头）。
    """
    if _submitter is None:
        raise RuntimeError(
            "未注册投递实现：submit_to_main_loop 需要 web 层启动时经 "
            "scheduling.set_loop_submitter 注册；独立进程请自行注册"
        )
    return _submitter(coro, wait=wait, timeout=timeout)
