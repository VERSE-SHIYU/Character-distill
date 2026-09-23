"""会话级的消息补写队列：写失败的消息留在队里，等下一次写 / 用户点重试 / 关停时补上。

**不变量（顺序由它成立，别改成并发写）**：队里的条目永远排在「本会话已落库的消息」之后
—— 只从**队尾**入队、只从**队头**补写，补写成功才出队。于是一条消息若已在库里，排在它
前面的队内条目就已经落库，绝不会出现「后写的先落库」。这正是幂等键之外还需要队列的理由：
`messages.id` 是自增的，补写顺序一乱，读回来（`ORDER BY id ASC`）的历史就不是用户看到的
那个顺序。

**为什么 `ping` 每次调用时传入、不在构造时绑定存储实例**：存储实例在 `web/deps` 里是用到
才解析的（缺陷 117——`TextManager` 刻意持的是存储**函数**）。构造时绑死一份会与
`deps._storage` 分叉：队列拿着过期的可达性判断，就会把本来补得上的消息丢掉。

**两处已知边界（都在台账 94 里写明，不是遗漏）**：

- **进程异常退出**（崩溃、被 kill -9）会丢掉队里还没补上的消息 —— 队列在内存里，不落盘。
  正常关停由 `web/server.py` 的 lifespan 兜住（先停清理循环，再 flush 全部队列）。
- 对一条**还没落库**的 user 消息做出的反应不会被持久化（既有的
  `user_msg_id is not None` 判断），因为反应表要的是消息 id，而这条消息还没有 id。

队列只依赖一个 `write_fn(key) -> int`：真正落库那件事由调用方提供（它才知道写哪张表、
哪些字段）。幂等键由队列生成并传给 `write_fn`，补写重复执行时由存储层按 key 收敛成一行。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from uuid import uuid4

from core.nonfatal import nonfatal

# 一条待补写的消息：(幂等键, 真正去落库的协程构造器)
_Entry = tuple[str, Callable[[str], Awaitable[int]]]


@dataclass(frozen=True)
class SaveState:
    """一条消息此刻的落库状态 —— 响应体里 `save` 字段的唯一来源。

    `id` 只有 `saved` 有：另外两种状态下这条消息在库里没有行，给个假 id 会让「刷新后
    消息回来了、id 却对不上」变成静默错。
    """

    key: str
    state: str          # "saved" | "pending" | "failed"
    id: int | None = None


@dataclass
class FlushReport:
    """一次 flush 补上的与丢掉的 —— 帧与接口的 `flushed` / `dropped` 字段的唯一来源。"""

    # 补写成功的 (幂等键, 行 id)，按补写顺序（= 原顺序）
    flushed: list[tuple[str, int]] = field(default_factory=list)
    # 库可达、但这条消息自己写不进去，重试一次后放弃的幂等键
    dropped: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        """前端形状。补上的按 key 去找那条消息填 id，丢掉的按 key 标「保存失败」。"""
        return {
            "flushed": [{"key": key, "id": row_id} for key, row_id in self.flushed],
            "dropped": list(self.dropped),
        }


class MessageOutbox:
    """一个会话（一对一 / 群聊各一个）的补写队列。见模块 docstring 的不变量与边界。"""

    def __init__(self) -> None:
        """只建队列与那把锁 —— 存储实例不进构造，理由见模块 docstring。"""
        self._queue: list[_Entry] = []
        self._lock = asyncio.Lock()

    @property
    def has_pending(self) -> bool:
        """队里还有没有没落库的消息（空闲清理与关停据此决定要不要 flush）。"""
        return bool(self._queue)

    async def write(
        self, write_fn: Callable[[str], Awaitable[int]], *, ping: Callable[[], Awaitable[None]],
    ) -> tuple[SaveState, FlushReport]:
        """入队一条消息并立刻尝试补写，返回**这一条**的状态和本次补写的报告。

        入队即尝试：正常路径下（库健康）这就是一次普通写入，调用方拿到 `saved` 与真实行 id；
        只有在写失败时才留下 `pending` / `failed`。
        """
        key = uuid4().hex
        async with self._lock:
            self._queue.append((key, write_fn))
        report = await self.flush(ping=ping)
        return self._state_of(key, report), report

    async def flush(self, *, ping: Callable[[], Awaitable[None]]) -> FlushReport:
        """从队头按顺序补写，直到队空、或探到库不可达为止。

        失败要分辨两类（这是本模块唯一的判断分支，别压成一类）：

        - **库不可达** → 立刻停手，整队保留（含正在写的那条）。此时「写不进去」是所有消息
          的共同处境，继续往下写只会把后面每一条也一条条判死。
        - **库可达、这一条有问题** → 立刻重试一次，还失败就丢掉这一条并继续。一条坏消息
          不该把整队堵死。
        """
        report = FlushReport()
        async with self._lock:
            while self._queue:
                key, write_fn = self._queue[0]
                async with nonfatal("outbox", "write queued message") as attempt:
                    row_id = await write_fn(key)
                if not attempt.failed:
                    self._drop_head(key)
                    report.flushed.append((key, row_id))
                    continue

                async with nonfatal("outbox", "ping storage") as probe:
                    await ping()
                if probe.failed:
                    break
                async with nonfatal("outbox", "retry queued message") as retry:
                    row_id = await write_fn(key)
                if retry.failed:
                    self._drop_head(key)
                    report.dropped.append(key)
                    continue
                self._drop_head(key)
                report.flushed.append((key, row_id))
        return report

    def discard(self, key: str) -> None:
        """摘掉一条**还没落库**的消息（流式回滚时用户消息不该留下）。

        队里没有这个 key 时什么也不做 —— 已经落库的消息不在队里，撤回由存储层管。
        """
        self._queue = [entry for entry in self._queue if entry[0] != key]

    def clear(self) -> None:
        """清空队列（`/revoke` 用：这段历史整个不要了，补写会把它们又写回来）。"""
        self._queue.clear()

    def _drop_head(self, key: str) -> None:
        """出队，但只删「还是原来那个头」。

        上面两个方法都是**同步**的，可能在 `await write_fn` 期间把队头抽走；无脑
        `pop(0)` 会删掉别人的条目。
        """
        if self._queue and self._queue[0][0] == key:
            self._queue.pop(0)

    @staticmethod
    def _state_of(key: str, report: FlushReport) -> SaveState:
        """这一条在这次补写里落库了 / 被判死了 / 还留着 —— 三种，无第四种。"""
        for flushed_key, row_id in report.flushed:
            if flushed_key == key:
                return SaveState(key, "saved", row_id)
        if key in report.dropped:
            return SaveState(key, "failed")
        return SaveState(key, "pending")
