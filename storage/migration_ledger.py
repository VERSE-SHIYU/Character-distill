"""迁移账本的共用判定：两后端共用「哪些文件待执行」与「文件摘要」两件事。

纯函数、无 IO —— 读盘、连库、记日志都在各自执行器里。放这里是因为两个执行器的
判据必须同口径：分开写会各自漂移，而漂移只在某一侧表现为「迁移没跑」。
"""
from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence


def file_sha256(text: str) -> str:
    """迁移正文的摘要。

    对**文本**取而不是对原始字节：`Path.read_text` 已把换行归一成 `\\n`，故同一份文件
    在 Windows 上按 autocrlf 检出成 CRLF 也不会算出不同的摘要 —— 摘要判的是「文件内容
    改没改」，不是「这台机器的检出风格」。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pending_files(
    recorded: Collection[str],
    on_disk: Collection[str],
    order: Sequence[str],
) -> list[str]:
    """待执行的迁移文件名，按 *order* 排序。三个入参各答一个问题：

    `recorded` 库里记过账的、`on_disk` 这次真读到的、`order` 它们该按什么次序跑。
    `order` 不取 `sorted(on_disk)`：次序是各后端清单自己的事，与「磁盘上有什么」是
    两个问题（SQLite 侧还有夹在迁移中间、不是迁移文件的重建步骤）。
    """
    done = set(recorded)
    present = set(on_disk)
    return [name for name in order if name in present and name not in done]
