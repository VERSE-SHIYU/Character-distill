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

import asyncio
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
    "set_context_carriers", "ctx_thread", "ctx_submit", "AdaptiveGate",
    "AccountGate", "GatedLLM", "account_gate", "reset_account_gates",
]


class AdaptiveGate:
    """按**在途请求数**自适应的并发闸（AIMD）。

    为什么不是固定 `Semaphore`：上游的并发上限按账号计、随余额动态变化（一台上限
    18 的账号拿到 60 并发就 429×328、11 片失败），固定的 `map_concurrency` 总会
    超过一部分账号。闸把上限当估计值：踩到 429 乘性下探，连续成功加性上探，收敛到
    该账号当前的真实上限附近。不解析 429 文案里的数字 —— 那是未写进文档、随时会变
    的运行时行为。

    用法：`async with gate:` 包住**每一次尝试**（不是整次带重试的调用）；进闸时记下
    `gate.generation`，成功调 `await gate.on_success()`、429 调
    `await gate.on_rate_limited(gen)`。退避睡眠必须在 `async with` **之外** —— 睡着的
    请求若占着名额，上限永远探不上去。

    上调会让等在 `__aenter__` 里的协程立刻可进，故 `on_success` 需 `notify_all`；
    下调相反（更严），不唤醒任何等待者，等 `__aexit__` 释放名额时自然复查。

    加性上探只在**满载**时发生（RFC 7661：未用满拥塞窗口的发送方 MUST NOT 增大窗口）
    —— 见 `on_success`。
    """

    def __init__(self, cap: int, initial: int | None = None) -> None:
        """*cap* 是上限（`map_concurrency`）；*initial* 是该账户已学到的上限，缺省从 cap 起。"""
        self._cap = max(1, int(cap))
        self._ceiling = self._cap if initial is None else min(self._cap, max(1, int(initial)))
        self._inflight = 0
        self._ok = 0
        # 代数：每下调一次 +1。一波并发同时撞 429 时，只有「当时代数仍是当前代数」的那个
        # 下调 —— 否则 N 个 429 会按次数连乘（20 × 0.75^15 → 1），闸当场自锁。
        self._generation = 0
        # 最近一次**进闸**时闸是否满载。缺省 True：还没有过任何准入就不抑制 —— 生产路径
        # 上每次 on_success 前必有一次 `__aenter__`，此缺省只对直接驱动闸的算术用例可见。
        self._admitted_full = True
        self._cond = asyncio.Condition()

    @property
    def ceiling(self) -> int:
        """当前上限 —— 允许同时在途的请求数。"""
        return self._ceiling

    @property
    def inflight(self) -> int:
        """当前在途请求数。"""
        return self._inflight

    @property
    def generation(self) -> int:
        """当前代数 —— 进闸时记下它，报 429 时交回 `on_rate_limited`。"""
        return self._generation

    async def __aenter__(self) -> AdaptiveGate:
        async with self._cond:
            # 进闸时记下「此刻是否满载」：在途 + 1 ≥ 上限（本请求占的是最后的名额），
            # 或已经排过队（需求超过上限）—— 见 on_success 的 RFC 7661 说明。
            full = self._inflight + 1 >= self._ceiling
            while self._inflight >= self._ceiling:
                await self._cond.wait()
                full = True
            self._inflight += 1
            self._admitted_full = full
        return self

    async def __aexit__(self, *_exc) -> None:
        async with self._cond:
            self._inflight -= 1
            self._cond.notify_all()

    async def on_success(self) -> None:
        """一次尝试成功。连续成功次数达到当前上限即加性 +1，不超过 cap。

        只在**满载**时计数（RFC 7661：未用满拥塞窗口的发送方 MUST NOT 增大窗口）。
        满载与否取自最近一次进闸（`_admitted_full`）—— 排水期的分片在途数已低于上限，
        成功只说明收尾顺利，不代表还有余量，照旧计数会把写回的账户记忆抬高。
        """
        async with self._cond:
            if not self._admitted_full:
                return
            self._ok += 1
            if self._ok >= self._ceiling and self._ceiling < self._cap:
                self._ceiling += 1
                self._ok = 0
                self._cond.notify_all()

    async def on_rate_limited(self, generation: int) -> None:
        """一次 429。乘性下调（×0.75 向下取整），最低 1。

        *generation* 是本次尝试**进闸时**记下的代数；它已不是当前代数，说明这一波里
        已经有别的请求下调过了（闸更严了，在途的那批是按旧上限发的），本次不再连乘。
        """
        async with self._cond:
            if generation != self._generation:
                return
            self._generation += 1
            self._ceiling = max(1, int(self._ceiling * 0.75))
            self._ok = 0


# ── 账号级共享闸 ─────────────────────────────────────────────────────────
# `AdaptiveGate` 内部是 `asyncio.Condition`，只能在一个事件循环里用；而蒸馏里的
# 同步调用（字段组 / 关系分批 / 合并 / 格式化）跑在各自的线程与各自的临时循环里。
# 同一个账号的每一次尝试必须落在同一只闸上，故给闸一个进程级的家循环，所有线程
# 都投递过去 —— 闸本身（AIMD 算法）不改。

_HOME_LOOP: asyncio.AbstractEventLoop | None = None
_HOME_THREAD: threading.Thread | None = None
_HOME_LOCK = threading.Lock()


def _gate_home_loop() -> asyncio.AbstractEventLoop:
    """所有账号闸的家循环（daemon 线程，懒启动、只启一次）。"""
    global _HOME_LOOP, _HOME_THREAD
    if _HOME_LOOP is not None:
        return _HOME_LOOP
    with _HOME_LOCK:
        if _HOME_LOOP is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="account-gate-home", daemon=True)
            thread.start()
            _HOME_THREAD = thread
            _HOME_LOOP = loop
        return _HOME_LOOP


async def _make_gate(cap: int) -> AdaptiveGate:
    # 类名在家循环执行时才解析：测试 monkeypatch `core.concurrency.AdaptiveGate`
    # 必须先于 Distiller 构造生效，别名会在 import 时把旧类钉死。
    return AdaptiveGate(cap)


class AccountGate:
    """同一账号共用的一只 `AdaptiveGate`，可被任意线程 / 任意事件循环使用。

    异步接口与 `AdaptiveGate` 同形（`async with`、`generation`、`ceiling`、
    `inflight`、`await on_success()`、`await on_rate_limited(gen)`），内部把每个操作
    投递到进程级家循环；同步接口（`acquire` / `release` / `report_*`）直接阻塞取结果，
    只允许在**非家循环**线程调用（否则 `run_coroutine_threadsafe(...).result()` 自锁）。
    """

    def __init__(self, cap: int) -> None:
        self._loop = _gate_home_loop()
        self._gate = self._call(lambda: _make_gate(cap))

    def _call(self, make_coro):
        if threading.current_thread() is _HOME_THREAD:
            raise RuntimeError(
                "AccountGate 的同步接口不能在闸的家循环线程里调用（会自锁）")
        return asyncio.run_coroutine_threadsafe(make_coro(), self._loop).result()

    @property
    def ceiling(self) -> int:
        return self._gate.ceiling

    @property
    def inflight(self) -> int:
        return self._gate.inflight

    @property
    def generation(self) -> int:
        return self._gate.generation

    async def __aenter__(self) -> "AccountGate":
        fut = asyncio.run_coroutine_threadsafe(self._gate.__aenter__(), self._loop)
        try:
            await asyncio.wrap_future(fut)
        except asyncio.CancelledError:
            # 目标 future 被取消 → CPython 会取消源任务；若源任务**已经拿到名额**
            # （future 已完成），那次 cancel 是空操作，名额必须由调用方补还。
            fut.add_done_callback(self._return_if_acquired)
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        fut = asyncio.run_coroutine_threadsafe(
            self._gate.__aexit__(*exc_info), self._loop)
        # 释放名额不随调用方一起被取消：shield 让匿名 inner future 继续跑完。
        await asyncio.shield(asyncio.wrap_future(fut))

    async def on_success(self) -> None:
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
            self._gate.on_success(), self._loop))

    async def on_rate_limited(self, generation: int) -> None:
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
            self._gate.on_rate_limited(generation), self._loop))

    def acquire(self) -> None:
        self._call(lambda: self._gate.__aenter__())

    def release(self) -> None:
        self._call(lambda: self._gate.__aexit__(None, None, None))

    def report_success(self) -> None:
        self._call(lambda: self._gate.on_success())

    def report_rate_limited(self, generation: int) -> None:
        self._call(lambda: self._gate.on_rate_limited(generation))

    def _return_if_acquired(self, fut) -> None:
        if fut.cancelled() or fut.exception() is not None:
            return
        asyncio.run_coroutine_threadsafe(
            self._gate.__aexit__(None, None, None), self._loop)


_ACCOUNT_GATES: dict[str, AccountGate] = {}
_ACCOUNT_GATES_LOCK = threading.Lock()


def account_gate(account_id: str, cap: int) -> AccountGate:
    """按账号（base_url + key 指纹）共享的闸；同一账号的并发上限只为它探一次。"""
    with _ACCOUNT_GATES_LOCK:
        gate = _ACCOUNT_GATES.get(account_id)
        if gate is None:
            gate = AccountGate(cap)
            _ACCOUNT_GATES[account_id] = gate
        return gate


def reset_account_gates() -> None:
    """测试隔离用：丢掉进程内所有共享闸。"""
    with _ACCOUNT_GATES_LOCK:
        _ACCOUNT_GATES.clear()


class GatedLLM:
    """模型实例 + 账号闸：蒸馏面向的每个模型方法都在这里显式定义并带上闸，其余属性原样转发。

    只包请求方法：`chat` / `chat_stream_long` / `async_chat` / `select_by_schema` ——
    聊天（`chat_stream`）、审核、记忆提炼不过账号闸（2026-11-02 定）。**新增蒸馏模型
    调用必须同时在这里加转发**，否则走 `__getattr__` 静默不过闸（G13 形态守卫会红）。
    """

    def __init__(self, llm: Any, gate: AccountGate) -> None:
        self._llm = llm
        self._gate = gate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)

    @property
    def gate(self) -> AccountGate:
        """账号闸 —— 给蒸馏器读上限做日志，不碰代理的私有字段。"""
        return self._gate

    def chat(self, *args, **kwargs):
        return self._llm.chat(*args, gate=self._gate, **kwargs)

    def chat_stream_long(self, *args, **kwargs):
        return self._llm.chat_stream_long(*args, gate=self._gate, **kwargs)

    def select_by_schema(self, *args, **kwargs):
        return self._llm.select_by_schema(*args, gate=self._gate, **kwargs)

    async def async_chat(self, *args, **kwargs):
        return await self._llm.async_chat(*args, gate=self._gate, **kwargs)
