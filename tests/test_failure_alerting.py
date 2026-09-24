# -*- coding: utf-8 -*-
"""线上失败要能告警（spec-119）：全局处理器 + 吞错落点的级别口径。

**为什么单独一个文件。** 本份改的是「失败可见」这条链上的两处：往上抛的错误由
`web/server.py` 的全局处理器统一记录，被吞掉的错误由各落点自己记录。两者的判据
都要**装真的告警出口**才判得准 —— 面板（`RingBufferHandler`）只收 WARNING+，
告警邮件只收 ERROR，于是「记没记」「记成哪一档」必须分开断言：只断言「有日志」
的话，把 ERROR 写成 WARNING 照样绿，而那一档之差就是「发不发邮件」。

**仪器纪律**（与 test_alerting 同一套）：`_dispatch` 与 `send_email` 都替换在
`core.alerting` 模块自身的名字上 —— 那是生产调用的同一个绑定；只 patch 一处绑定，
绕开它的变异会假绿。

Run: pytest tests/test_failure_alerting.py -v
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import core.alerting as alerting
from core.nonfatal import nonfatal


@pytest.fixture
def alerts(monkeypatch) -> list[tuple[str, str, str]]:
    """把告警出口装成生产那一个（真 `AlertHandler`），发信换成记录式替身。

    装真 handler 而不是手搓一条 `if level >= ERROR` —— 后者测的是本文件里的复制品，
    生产那档门槛（`ALERT_LEVEL`）改了也照绿。
    """
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(alerting, "_dispatch", lambda fn: fn())
    monkeypatch.setattr(
        alerting, "send_email",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    root = logging.getLogger()
    handler = alerting.AlertHandler("ops@example.com")
    root.addHandler(handler)
    try:
        yield sent
    finally:
        root.removeHandler(handler)


def _levels_for(caplog, marker: str) -> list[int]:
    """本次调用产生的、带 marker 的记录级别（marker 是本用例独有的，不假设空缓冲区）。"""
    return [r.levelno for r in caplog.records if marker in r.getMessage()]


# ── 1. `nonfatal` 的级别：默认 ERROR（会告警），可选 WARNING（只进面板） ──────

def test_nonfatal_level_warning_is_recorded_but_sends_no_alert(alerts, caplog):
    """`level=WARNING` → 记 WARNING，且**不**发告警邮件。

    这一档就是「已经兜底了、不影响结果」的后台动作（好感度、阅读进度、预热、缓存）：
    它们该上面板，但不该把人从床上叫起来。写成 ERROR 的代价是真会发信。
    """
    marker = "spec119-nonfatal-warning"

    async def _run() -> None:
        async with nonfatal("probe", marker, level=logging.WARNING):
            raise RuntimeError("warn-boom")

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run())

    assert _levels_for(caplog, marker) == [logging.WARNING], \
        f"应恰有一条 WARNING（吞掉 ≠ 沉默），实得 {_levels_for(caplog, marker)}"
    assert alerts == [], "WARNING 记成了告警 —— 兜底动作失败会开始发邮件"


def test_nonfatal_default_level_is_unchanged(alerts, caplog):
    """不传 `level` → 仍是 ERROR 且**触发告警**（现有 9 处调用行为不变）。

    「默认值不变」不能只靠「现有用例还绿」来说：那些用例断言的是面板上有记录，
    而面板收 WARNING+，把默认降成 WARNING 它们照样绿。
    """
    marker = "spec119-nonfatal-default"

    async def _run() -> None:
        async with nonfatal("probe", marker):
            raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        asyncio.run(_run())

    assert _levels_for(caplog, marker) == [logging.ERROR], \
        f"不传 level 时必须是 ERROR，实得 {_levels_for(caplog, marker)}"
    assert len(alerts) == 1, "默认档（数据没存进去 / 请求失败）必须发得出告警"
