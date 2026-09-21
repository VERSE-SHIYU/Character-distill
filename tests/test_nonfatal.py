# -*- coding: utf-8 -*-
"""`core/nonfatal` 的契约：吞掉异常，但**必须**在后台日志面板上留一条 ERROR。

面板（`core/log_collector.RingBufferHandler`，装在 root logger 上）只收 WARNING+，
且只存 `record.getMessage()`。所以「吞掉数据写入失败」若只 print 到 stdout，面板上一片
干净 —— SG「全员用量为 0 数月无人察觉」就是这个形态（AGENTS.md 缺陷 93）。本文件锁的
是那条可见性：**吞掉 ≠ 沉默**。

两类用例：
  1. 构造本身（吞什么、放行什么、记几条）；
  2. 真正被改的吞错点（用量写库、非流式消息落库）在失败时确实上了面板，且调用方不受影响。

RingBufferHandler 是模块级单例、跨用例共享，所以每条断言都用一个本用例独有的 marker
去筛记录，不假设缓冲区里只有自己。
"""
from __future__ import annotations

import asyncio

import pytest

import core.utils as utils
import routers.chat as chat_router_mod
from core.log_collector import get_recent_logs, install_log_collector
from core.nonfatal import nonfatal


@pytest.fixture(autouse=True)
def _panel_installed():
    """把面板装到 root logger 上 —— 生产里由 web/server.py 的 lifespan 装。"""
    install_log_collector()


def _panel_errors(marker: str) -> list[dict]:
    return [
        e for e in get_recent_logs(500)
        if e["level"] == "ERROR" and marker in e["message"]
    ]


# ── 1. 构造本身 ──────────────────────────────────────────────────────

def test_ordinary_exception_is_swallowed_and_reported_once():
    """块内抛普通异常 → 被吞，面板恰好收到 1 条 ERROR，且带 source / what / 异常类型。

    面板只存 getMessage()，所以异常类型必须**拼进消息文本**（exc_info 单独给是看不见的）。
    """
    marker = "nonfatal-probe-ordinary"

    async def _run() -> None:
        async with nonfatal("chat", marker):
            raise RuntimeError("boom")

    asyncio.run(_run())

    errs = _panel_errors(marker)
    assert len(errs) == 1, f"面板上应有且只有 1 条，实得 {len(errs)}"
    msg = errs[0]["message"]
    assert "chat" in msg, "缺 source"
    assert "RuntimeError" in msg, "异常类型没进消息文本 —— 面板上看不到是谁"
    assert "boom" in msg, "缺异常消息"


def test_clean_block_reports_nothing():
    """正常走完不留记录 —— 留痕不能变成每轮一行噪音。"""
    marker = "nonfatal-probe-clean"

    async def _run() -> None:
        async with nonfatal("chat", marker):
            pass

    asyncio.run(_run())
    assert _panel_errors(marker) == []


@pytest.mark.parametrize(
    "exc_type", [KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_control_flow_exceptions_pass_through(exc_type):
    """取消／关闭不是「失败」：照常上抛，且**不**进面板。

    必须走真的 `async with` 语句 —— 手搓 `cm.__aexit__(typ, ...)` 测不出这条：
    `_AsyncGeneratorContextManager.__aexit__` 对「生成器原样重抛同一个实例」返回 False，
    即「不抑制」，由 `async with` 自己去抛；直接调用 `__aexit__` 就把它咽了。
    """
    marker = f"nonfatal-probe-{exc_type.__name__}"

    async def _run() -> None:
        async with nonfatal("chat", marker):
            raise exc_type()

    with pytest.raises(exc_type):
        asyncio.run(_run())
    assert _panel_errors(marker) == [], "控制流异常被当失败记了 —— 面板会被关闭噪音淹掉"


# ── 2. 真正改过的吞错点 ───────────────────────────────────────────────

class _BoomStorage:
    async def record_usage(self, *a, **kw):
        raise RuntimeError("usage db down")


class _StubLLM:
    _model = "stub-model"
    last_usage = {"prompt_tokens": 1, "completion_tokens": 2}


async def test_try_record_usage_reports_write_failure(monkeypatch):
    """`storage.record_usage` 抛异常 → 面板收到 ERROR，且 `try_record_usage` 自身不抛。

    投递出去的那段生产里跑在主 loop 上，这里直接 await 它 —— 断言的是那段协程体，
    不是调度器（调度器的契约在 core/scheduling 那边）。
    """
    captured: dict = {}
    monkeypatch.setattr(utils, "current_user_id", lambda: "u1")
    monkeypatch.setattr(
        utils, "submit_to_main_loop",
        lambda coro, **kw: captured.setdefault("coro", coro),
    )

    utils.try_record_usage(_BoomStorage(), _StubLLM(), action="chat", source="usageprobe")

    assert "coro" in captured, "没投递出去 —— 写库失败根本轮不到被吞"
    await captured["coro"]

    errs = _panel_errors("usageprobe")
    assert len(errs) == 1, f"用量写库失败必须上一条 ERROR，实得 {len(errs)}"
    assert "usage db down" in errs[0]["message"]


class _Engine:
    """够 `_do_chat` 跑完的最小引擎替身（与 test_message_evidence 同形）。"""

    def __init__(self):
        self.history = []
        self.last_traces = []
        self.last_summary = ""
        self._last_rag_context = ""
        self._ctx_engine = type("Ctx", (), {"web_search_enabled": False})()
        self.affinity_enabled = True
        self.agent_mode = False

    def chat(self, *a, **kw):
        return "回复"

    def _should_retract(self, reply):
        return False


class _FailingSaveStorage:
    """只坏在落库上：get_messages 照常返回，好让失败面窄到「消息写库」这一处。"""

    async def save_message(self, *a, **kw):
        raise RuntimeError("message db down")

    async def get_messages(self, session_id):
        return []


def _wire(monkeypatch):
    session = {"engine": _Engine(), "lock": asyncio.Lock(), "user_id": "u1"}

    async def _fake_ensure(session_id, storage, sessions, user_id=""):
        return session

    monkeypatch.setattr(chat_router_mod, "_ensure_session", _fake_ensure)
    return session


def test_do_chat_save_failure_reaches_the_panel(monkeypatch):
    """非流式落库失败 → 面板收到 ERROR。这是本次改动的交付物（失败可见）。

    调用**自身**仍会炸（chat.py:380 的 `user_rec` 未初始化 → UnboundLocalError），
    这是与「可见性」无关的另一条缺陷，已单独报告、未在本轮修 —— 见文件末尾那条 xfail。
    这里只断言交付物：面板收到了那一条。
    """
    _wire(monkeypatch)
    with pytest.raises(Exception):  # UnboundLocalError，见上
        asyncio.run(
            chat_router_mod._do_chat(
                "s1", "hi", storage=_FailingSaveStorage(), sessions={}, user_id="u1",
            )
        )

    errs = _panel_errors("chat")
    assert any("message db down" in e["message"] for e in errs), \
        "消息落库失败没上面板 —— 又回到「失败只 print」的老样子"


@pytest.mark.xfail(
    strict=True,
    reason="chat.py:380/533 在落库失败后引用未绑定的 user_rec/char_rec —— "
           "「失败不影响主流程」实际不成立；待裁定后另行修",
)
def test_do_chat_returns_normally_when_save_fails(monkeypatch):
    """落库失败时接口应当照常返回回复（spec 的验收项之一）。

    现状不成立：`user_rec`/`char_rec` 只在 try 体内绑定，吞掉异常之后 380/381 行
    立刻 UnboundLocalError。strict=True —— 谁修好了谁就得来把这条翻正，
    免得缺陷被悄悄修掉而用例还挂着 xfail。
    """
    _wire(monkeypatch)
    result = asyncio.run(
        chat_router_mod._do_chat(
            "s1", "hi", storage=_FailingSaveStorage(), sessions={}, user_id="u1",
        )
    )
    assert result["reply"] == "回复", "落库失败不该吃掉回复"
    assert result["user_msg_id"] is None and result["char_msg_id"] is None
