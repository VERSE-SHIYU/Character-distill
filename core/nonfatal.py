"""「失败不影响主流程」的唯一定义 —— 吞掉异常，但必须留痕。

**为什么要有这个构造**：后台日志面板（`core/log_collector.RingBufferHandler`，装设在
`web/server.py` 的 lifespan 里）只收 `logging` WARNING+，而各处手写的
`try/except + print` 到不了那里 —— SG「全员用量为 0 数月无人察觉」就是这个形态：
每一次写入失败都只落在容器 stdout 里，面板上一片干净。同一个意图（失败不影响主流程）
在仓里被手写了十来遍，于是「沉默」也被手写了十来遍。

**为什么不用 `contextlib.suppress`**：它按类型静默丢弃 —— 不记录，也不区分
「控制流」与「失败」。这里要的恰好相反：吞掉，但以 ERROR 上报，并放行取消。

**为什么带 `source` / `what`**：面板上只有一行文本。读的人需要知道是谁、在哪一步
失败 —— `source` 是子系统名（`"chat"`），`what` 是动宾短语（`"save assistant message"`），
都不是函数名。异常类型与消息由本构造拼进同一行，因为面板只存 `getMessage()`。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

_logger = logging.getLogger(__name__)

# 这些不是「失败」，是**控制流**：吞掉它们会让取消／关闭失效（任务停不下、进程关不掉）。
# `except Exception` 本来就够不着它们（三者都继承自 BaseException），所以下面这两行是
# **把口径写下来**而不是修某个活 bug —— 将来若有人把它放宽成 `except BaseException`，
# 这几行就是那条判据的落点。
_PASS_THROUGH = (KeyboardInterrupt, SystemExit, asyncio.CancelledError)


@asynccontextmanager
async def nonfatal(source: str, what: str) -> AsyncIterator[None]:
    """吞掉块内异常，并以一条 ERROR 上报到日志面板。

    **异常只在最终吞掉它的地方记录一次。** 上游若还会重抛（例如 storage 层的
    `print + raise`），不要在那里再包一层本构造 —— 同一次失败会记成两条。
    """
    try:
        yield
    except _PASS_THROUGH:
        raise
    except Exception as exc:
        _logger.error(
            "[%s] %s failed (non-fatal): %s: %s",
            source, what, type(exc).__name__, exc,
            exc_info=True,
        )
