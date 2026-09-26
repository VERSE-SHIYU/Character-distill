"""lifespan 装的进程级注册，关停时逐项撤销（spec E 段 / 步骤 9）。

判据是**注册本身**，不是「事件条数」：起一次真 app 再关掉，之后调用守卫、投递实现、
主 loop 三件套都回到起点，根日志器上不剩本仓的 handler。

不装不撤的代价（缺陷 113）：注册留在进程里，投递实现还指着**已经关掉**的 loop ——
同会话后面任何走 `submit_to_main_loop` 的用例都把协程投到死 loop 上，`chat_engine`
的宽 `except` 吞掉异常，只在**别的**用例头上飘一句 `coroutine ... was never awaited`。
"""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

import adapters.llm_adapter as llm_adapter
import core.scheduling as scheduling
import deps
import server
from core.alerting import AlertHandler
from core.stdout_logging import _StdoutHandler

#: 本仓挂到根日志器上的 handler 类型。按**类型**认人：pytest 自己也往根上挂 handler，
#: 按对象身份或整张表比会把那些算进来。
_OUR_HANDLERS = (_StdoutHandler, AlertHandler)


def _ours_on_root() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, _OUR_HANDLERS)]


def _registrations() -> tuple:
    return (
        deps.get_main_loop(),
        scheduling.get_loop_submitter(),
        llm_adapter.get_call_guard(),
    )


def test_lifespan_undoes_every_process_wide_registration():
    """`with TestClient(server.app)` 退出后，lifespan 装的东西全部撤销。"""
    root = logging.getLogger()
    before_registrations = _registrations()

    # handler 这一半要**先清成干净的起点**再断言「关停后一个不剩」：装是幂等的
    # （按对象身份去重），别处漏下的同款 handler 会让本次 install 变成空操作 ——
    # 那时「关停后还剩着」是别人漏的，却记在本用例头上。注册那一半不需要清：
    # 快照比的是「还回没还回」，起点是不是 None 都不影响判据。
    saved_handlers = list(root.handlers)
    root.handlers[:] = [h for h in saved_handlers if not isinstance(h, _OUR_HANDLERS)]
    try:
        with TestClient(server.app):
            # 前提：真装上了。缺了这几条，下面的断言在「lifespan 什么都没干」时恒真。
            assert llm_adapter.get_call_guard() is not None, "启动后守卫没装上 —— 本用例测不到撤销"
            assert scheduling.get_loop_submitter() is not None, "启动后投递实现没装上"
            assert deps.get_main_loop() is not None, "启动后主 loop 没捕获"
            assert _ours_on_root(), "启动后根上没挂上本仓的 handler"

        assert _registrations() == before_registrations, (
            "关停后进程级注册没还回去：守卫 / 投递实现 / 主 loop 还留在进程里，"
            f"实得 {_registrations()!r}，起点是 {before_registrations!r}"
        )
        assert _ours_on_root() == [], (
            f"关停后根日志器上还剩本仓的 handler：{_ours_on_root()}"
        )
    finally:
        root.handlers[:] = saved_handlers
