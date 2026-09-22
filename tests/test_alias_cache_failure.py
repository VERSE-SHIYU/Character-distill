# -*- coding: utf-8 -*-
"""别名缓存的读失败必须冒泡（缺陷 86 同族，第六次显形）。

形态同 86：宽捕获把上游故障（存储 / 序列化）降级成「这个角色没有别名」。
`_build_all_characters` 原先自带 `except Exception: print(...)` —— 会话照常建起来，
用户看到的是角色没有别名，运维什么都收不到，两类结果（真没别名 / 读挂了）不可辨。

与 §H 第 1 项同口径（`get_or_distill` 取别名那段已先收口）：**调用方一行不改**，
只让异常走它们各自的既有出口。四个调用点：`get_or_distill`（此时卡已落库）、
`save_distilled_card`、`web/routers/distill.py::start_session`（外层宽 except → 500）、
`web/routers/chat.py::_ensure_session`（无就地捕获，冒泡到路由的 500 收口）。

变异对象 = 把 `except Exception: print(...)` 加回 `core/text_manager.py` 的
`_build_all_characters` → 本用例红（异常被吞、返回空别名的列表）。
"""

from __future__ import annotations

import pytest

from core import text_manager as TM
from core.text_manager import TextManager


class _Boom:
    """存储层故障：本用例只走 `cached_characters` 那一跳，其余接口用不到。"""

    async def get_characters_owned(self, *a, **kw):
        raise RuntimeError("storage exploded")


async def test_alias_cache_read_failure_propagates(monkeypatch):
    """`cached_characters` 抛错 → `_build_all_characters` 抛出，不降级成空别名。

    打在**模块属性**上（`core.text_manager.cached_characters`）而不是真 store 上：
    被测的就是「这个名字抛出来时，本函数吞不吞」。
    """
    async def _boom(*a, **kw):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(TM, "cached_characters", _boom)
    tm = TextManager(_Boom(), None, None, {}, memory_manager=None)

    with pytest.raises(RuntimeError, match="storage exploded"):
        await tm._build_all_characters("t1", [{"name": "甲"}], "u1")
