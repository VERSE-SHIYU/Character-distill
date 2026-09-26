"""「失败不影响主流程」的唯一定义 —— 吞掉异常，但必须留痕。

**为什么要有这个构造**：后台日志面板（`core/log_collector.RingBufferHandler`，装设在
`web/server.py` 的 lifespan 里）只收 `logging` WARNING+，而各处手写的
`try/except + print` 到不了那里 —— SG「全员用量为 0 数月无人察觉」就是这个形态：
每一次写入失败都只落在容器 stdout 里，面板上一片干净。同一个意图（失败不影响主流程）
在仓里被手写了十来遍，于是「沉默」也被手写了十来遍。

**为什么不用 `contextlib.suppress`**：它按类型静默丢弃 —— 不记录，也不区分
「控制流」与「失败」。这里要的恰好相反：吞掉，但以 ERROR（或调用方点名的
`level`）上报，并放行取消。

**为什么带 `source` / `what`**：面板上只有一行文本。读的人需要知道是谁、在哪一步
失败 —— `source` 是子系统名（`"chat"`），`what` 是动宾短语（`"save assistant message"`），
都不是函数名。异常类型与消息由本构造拼进同一行，因为面板只存 `getMessage()`。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator

_logger = logging.getLogger(__name__)

# 这些不是「失败」，是**控制流**：吞掉它们会让取消／关闭失效（任务停不下、进程关不掉）。
# `except Exception` 本来就够不着它们（三者都继承自 BaseException），所以下面这两行是
# **把口径写下来**而不是修某个活 bug —— 将来若有人把它放宽成 `except BaseException`，
# 这几行就是那条判据的落点。
_PASS_THROUGH = (KeyboardInterrupt, SystemExit, asyncio.CancelledError)


class NonFatalOutcome:
    """`nonfatal` 交给调用方的失败信号 —— 块内异常被吞掉后 `failed` 为 True。

    只有这一个字段：调用方要回答的是「这一步成没成」，不是「为什么没成」（那是日志
    的事）。**不提供异常对象**，免得调用方又把上游原文端到用户面前 —— 屏幕上的文案
    统一走 `adapters.llm_adapter.user_facing_error`。
    """

    __slots__ = ("failed",)

    def __init__(self) -> None:
        self.failed = False


def _report(exc: BaseException, source: str, what: str, level: int) -> None:
    """吞错点唯一的留痕出口 —— 同步／异步两版共用，好让级别与格式只有一处定义。

    消息用 `%s` 占位而不是 f-string：告警面板与 GlitchTip 都按**消息模板**归并，
    把变量拼进模板会把同一个故障拆成一堆各发一封邮件的问题。消息里只写 source /
    what / 异常类型与消息，不写正文和用户输入。
    """
    _logger.log(
        level,
        "[%s] %s failed (non-fatal): %s: %s",
        source, what, type(exc).__name__, exc,
        exc_info=True,
    )


@asynccontextmanager
async def nonfatal(
    source: str, what: str, *, level: int = logging.ERROR,
) -> AsyncIterator[NonFatalOutcome]:
    """吞掉块内异常，并以一条 ERROR 上报到日志面板；结果对象告知调用方成没成。

    不带 `as` 的写法（`async with nonfatal(...)`）照旧可用 —— 不关心结果的就别接。

    **`level` 是关键字参数，默认不变（ERROR = 会发告警邮件）。** 口径只有两档：
    「数据没存进去 / 用户的请求失败了」记 ERROR；「已经兜底、不影响结果的后台动作」
    （好感度评估、阅读进度、预热、缓存）记 WARNING —— 后者仍上面板（面板收 WARNING+），
    但不发信。写成 ERROR 的代价是真会给人发邮件，所以默认档必须留在「这次真的没成」上。

    **异常只在最终吞掉它的地方记录一次。** 上游若还会重抛（例如 storage 层的
    `print + raise`），不要在那里再包一层本构造 —— 同一次失败会记成两条。

    **一个块只放一件「失败也不该连坐别件」的事**：共用块时前一件失败会让后一件整个不
    执行，而调用方拿到的只有一个 `failed`，分不出是哪件没成。
    """
    outcome = NonFatalOutcome()
    try:
        yield outcome
    except _PASS_THROUGH:
        raise
    except Exception as exc:
        outcome.failed = True
        _report(exc, source, what, level)


@contextmanager
def nonfatal_sync(
    source: str, what: str, *, level: int = logging.ERROR,
) -> Iterator[NonFatalOutcome]:
    """`nonfatal` 的同步版本 —— 契约、级别口径、放行规则完全一致，只是不用 `await`。

    **什么时候用它**：同步函数里的吞错点（`with nonfatal_sync(...)`）。异步上下文里
    一律用 `nonfatal` —— 同步版套在 `await` 外面接不住对端抛出的异常，而调用方会
    以为自己已经吞了。

    `KeyboardInterrupt` / `SystemExit` / `CancelledError` 同样放行（共用
    `_PASS_THROUGH`）。第三项在同步代码里仍会经过：协程被取消时异常可能落在同步
    调用栈上，吞掉它会让任务停不下来。
    """
    outcome = NonFatalOutcome()
    try:
        yield outcome
    except _PASS_THROUGH:
        raise
    except Exception as exc:
        outcome.failed = True
        _report(exc, source, what, level)
