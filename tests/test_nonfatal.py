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
import json

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
    """够 `_do_chat` 与 `_do_chat_stream` **两条**路径跑完的最小引擎替身
    （与 test_message_evidence 同形：一份替身服务两条路，接口一变一起滞后）。"""

    def __init__(self, stream_pieces=("回", "复")):
        self.history = []
        self.last_traces = []
        self.last_summary = ""
        self._last_rag_context = ""
        self._ctx_engine = type("Ctx", (), {"web_search_enabled": False})()
        self.affinity_enabled = True
        self.agent_mode = False
        self._stream_pieces = list(stream_pieces)

    def chat(self, *a, **kw):
        return "回复"

    def chat_stream(self, *a, **kw):
        # 真引擎的 chat_stream 是同步迭代器，调用方用 next() 逐片取。
        return iter(self._stream_pieces)

    def post_stream_process(self, user_message, full_reply):
        pass

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

    调用**自身不炸**（缺陷 98 已修：`user_rec` / `char_rec` 有初值，取值统一走
    `_msg_fields`）。这里只断言交付物：面板收到了那一条 —— 换成 `pytest.raises`
    会与「失败不影响主流程」这条相反，两条判据分属不同用例。
    """
    _wire(monkeypatch)
    asyncio.run(
        chat_router_mod._do_chat(
            "s1", "hi", storage=_FailingSaveStorage(), sessions={}, user_id="u1",
        )
    )

    errs = _panel_errors("chat")
    assert any("message db down" in e["message"] for e in errs), \
        "消息落库失败没上面板 —— 又回到「失败只 print」的老样子"


def test_do_chat_returns_normally_when_save_fails(monkeypatch):
    """落库失败时接口应当照常返回回复（缺陷 98 的验收项）。

    这条**曾挂着 strict xfail**：当时 `user_rec`/`char_rec` 只在 nonfatal 块内绑定，
    块一吞掉异常，后面那两行 `.get("created_at")` 就是 UnboundLocalError → 500。
    修法是给它们初值 `None` 并统一走 `_msg_fields`（`None` → `(None, "")`）。

    判据是**三样一起**：回复没被吃掉、两个 id 为 `None`、两个 created_at 为空串 ——
    只断言「没抛异常」的话，「把 deref 挪走但给个假时间戳」也会绿。
    """
    _wire(monkeypatch)
    result = asyncio.run(
        chat_router_mod._do_chat(
            "s1", "hi", storage=_FailingSaveStorage(), sessions={}, user_id="u1",
        )
    )
    assert result["reply"] == "回复", "落库失败不该吃掉回复"
    assert result["user_msg_id"] is None and result["char_msg_id"] is None
    assert result["user_created_at"] == "" and result["char_created_at"] == "", \
        "没有这条消息却给了时间戳 —— 假默认值"


async def _drive_stream(storage, **kwargs) -> list[dict]:
    """排干 `_do_chat_stream` 的 SSE，按到达顺序返回 payload。

    落库发生在**生成器体内**，不排干就看不到；调用与排干同在**一个**事件循环里
    （`_run_async` 每次新建 loop，跨 loop 用 `asyncio.Lock` / `to_thread` 是自找麻烦）。
    与 test_message_evidence 的同名帮手同形。
    """
    resp = await chat_router_mod._do_chat_stream(
        "s1", "hi", storage=storage, sessions={}, user_id="u1", **kwargs,
    )
    frames: list[dict] = []
    async for chunk in resp.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("data: "):
                frames.append(json.loads(line[6:]))
    return frames


def test_do_chat_stream_finishes_with_a_done_frame_when_save_fails(monkeypatch):
    """流式等价用例：落库失败 → 正文照常流、末尾仍是 **done 帧**（不是 error 帧）。

    缺陷 98 在流式这条路的表现与一路之内不同：解引用点在**大 try 之内**，所以失败
    形态不是 500，而是「正文已整段流给用户，收尾却把 done 帧换成 error 帧」——
    客户端拿不到 `user_msg_id` / `char_msg_id`，而这两样正是它标记未同步消息的依据。

    判据必须落在**帧的类型**上：只断言「有回复文字」的话，error 帧那条路也绿。
    """
    _wire(monkeypatch)
    frames = asyncio.run(_drive_stream(_FailingSaveStorage()))

    done = [f for f in frames if f.get("done") is True]
    errors = [f for f in frames if "error" in f]
    assert errors == [], f"落库失败把 done 帧换成了 error 帧：{errors}"
    assert len(done) == 1, f"应恰有一个 done 帧，实得 {len(done)}：{frames}"

    assert done[0]["user_msg_id"] is None and done[0]["char_msg_id"] is None
    assert done[0]["user_created_at"] == "" and done[0]["char_created_at"] == "", \
        "没有这条消息却给了时间戳 —— 假默认值"
    assert "".join(f.get("token", "") for f in frames) == "回复", "正文没流出来"
