"""任务级诊断日志：任务 ID、分步计时、token 合计。

只服务蒸馏后台任务：`:data:`TASK_ID` 经 stdout handler 的 ``%(task)s`` 贴到每行；
``task_scope`` 记开始 / 结束与耗时；``step`` 记每一步；``add_usage`` 由记账唯一出口
（``core.utils.try_record_usage``）调用。不认识 web 层，也不改任何业务状态。
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable

logger = logging.getLogger(__name__)

TASK_ID: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="-")
_USAGE: contextvars.ContextVar[_Usage | None] = contextvars.ContextVar("task_usage", default=None)


class TaskIdFilter(logging.Filter):
    """把当前任务 ID 贴到记录上，供 handler 的 ``%(task)s`` 取用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.task = TASK_ID.get()
        return True


class _Usage:
    """一次任务内的用量累加器。

    可变对象经 contextvar 拷贝后仍指向同一个实例（工作线程、主 loop 投递都拷上下文），
    所以并发累加只需一把锁。
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = 0
        self.prompt = 0
        self.completion = 0
        self.cache_hit = 0
        self.cache_miss = 0
        self.by_action: dict[str, list[int]] = {}

    def add(self, action: str, prompt: int | None, completion: int | None,
            cache_hit: int | None, cache_miss: int | None) -> None:
        with self.lock:
            self.calls += 1
            self.prompt += prompt or 0
            self.completion += completion or 0
            self.cache_hit += cache_hit or 0
            self.cache_miss += cache_miss or 0
            row = self.by_action.setdefault(action, [0, 0, 0])
            row[0] += 1
            row[1] += prompt or 0
            row[2] += completion or 0

    def summary(self) -> str:
        with self.lock:
            text = (f"调用 {self.calls} 次；输入 {self.prompt}"
                    f"（缓存命中 {self.cache_hit} / 未命中 {self.cache_miss}）"
                    f"输出 {self.completion}")
            if self.by_action:
                text += "；按用途 " + ", ".join(
                    f"{action}:{n}/{p}/{c}" for action, (n, p, c) in self.by_action.items())
            return text


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@contextmanager
def task_scope(task_id: str, **fields):
    """绑任务 ID 与累加器，进出各打一行。``fields`` 按传入顺序写成 ``键=值``。"""
    usage = _Usage()
    token = TASK_ID.set(task_id)
    usage_token = _USAGE.set(usage)
    started = time.monotonic()
    logger.warning("任务开始%s", "".join(f" {key}={_fmt(value)}" for key, value in fields.items()))
    try:
        yield
    finally:
        logger.warning("任务结束 耗时 %.2fs；%s", time.monotonic() - started, usage.summary())
        _USAGE.reset(usage_token)
        TASK_ID.reset(token)


def scoped(fields_of: Callable[..., tuple[str, dict]]):
    """把函数包进 ``task_scope``；``(task_id, fields)`` 由 ``fields_of`` 按参数名取。"""

    def decorator(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            task_id, fields = fields_of(bound.arguments)
            with task_scope(task_id, **fields):
                return fn(*args, **kwargs)

        return wrapper

    return decorator


@contextmanager
def step(name: str):
    """记一步的耗时；失败先记类名再原样抛出。"""
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        logger.warning("步骤 %s 失败 耗时 %.2fs：%s",
                       name, time.monotonic() - started, type(exc).__name__)
        raise
    else:
        logger.warning("步骤 %s 完成 耗时 %.2fs", name, time.monotonic() - started)


def add_usage(action: str, prompt_tokens: int | None, completion_tokens: int | None,
              cache_hit: int | None, cache_miss: int | None) -> None:
    """记一笔任务内用量；不在任务里就什么都不做。"""
    usage = _USAGE.get()
    if usage is not None:
        usage.add(action, prompt_tokens, completion_tokens, cache_hit, cache_miss)
