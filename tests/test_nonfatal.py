# -*- coding: utf-8 -*-
"""`core/nonfatal` 的契约：吞掉异常，但**必须**在日志里留一条 ERROR。

留痕的出口就是 `logging` 本身（生产上由 stdout handler 与 Sentry 收走），判据用
`caplog` 直接看记录。所以「吞掉数据写入失败」若只 print 到 stdout，日志里一片干净
—— SG「全员用量为 0 数月无人察觉」就是这个形态（AGENTS.md 缺陷 93）。本文件锁的是那条
可见性：**吞掉 ≠ 沉默**。

两类用例：
  1. 构造本身（吞什么、放行什么、记几条）；
  2. 真正被改的吞错点（用量写库、非流式消息落库）在失败时确实留下了记录，且调用方不受影响。

每条断言都用一个本用例独有的 marker 去筛记录：同一次调用里的多笔非致命失败（队列首写 +
重试）各留一条，marker 把它们与别处的记录分开。
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

import core.utils as utils
import routers.chat as chat_router_mod
from core.nonfatal import nonfatal, nonfatal_sync
from core.text_manager import new_session_entry


def _errors(records: list[logging.LogRecord], marker: str) -> list[logging.LogRecord]:
    """筛出本用例关心的那几条 —— 级别与 marker 一起判，缺一个都会把噪音算进来。

    `caplog` 是按用例取的，所以不需要像从前那样假设「缓冲区里还有别人的记录」。
    """
    return [
        r for r in records
        if r.levelno == logging.ERROR and marker in r.getMessage()
    ]


# ── 1. 构造本身 ──────────────────────────────────────────────────────

def test_ordinary_exception_is_swallowed_and_reported_once(caplog):
    """块内抛普通异常 → 被吞，恰好 1 条 ERROR，且带 source / what / 异常类型。

    异常类型必须**拼进消息文本** —— `exc_info` 给的是堆栈，`getMessage()` 看不见它，
    而消息模板正是按「谁在哪一步失败」归并的那一项。
    """
    marker = "nonfatal-probe-ordinary"

    async def _run() -> None:
        async with nonfatal("chat", marker):
            raise RuntimeError("boom")

    asyncio.run(_run())

    errs = _errors(caplog.records, marker)
    assert len(errs) == 1, f"应有且只有 1 条，实得 {len(errs)}"
    msg = errs[0].getMessage()
    assert "chat" in msg, "缺 source"
    assert "RuntimeError" in msg, "异常类型没进消息文本 —— 日志里看不到是谁"
    assert "boom" in msg, "缺异常消息"


def test_clean_block_reports_nothing(caplog):
    """正常走完不留记录 —— 留痕不能变成每轮一行噪音。"""
    marker = "nonfatal-probe-clean"

    async def _run() -> None:
        async with nonfatal("chat", marker):
            pass

    asyncio.run(_run())
    assert _errors(caplog.records, marker) == []


def test_outcome_tells_the_caller_whether_the_block_failed():
    """`as ... as` 拿到的结果对象是调用方唯一的失败信号：成功 `False`、失败 `True`。

    没有这个信号时，「吞掉」与「成功」在调用方眼里完全一样 —— 保存失败的那条消息
    只能靠「id 为 None」去猜，而 `hidden` 时 id 本来就是 None（假判据）。
    """

    async def _run() -> None:
        async with nonfatal("chat", "nonfatal-probe-outcome-ok") as ok:
            pass
        assert ok.failed is False, "正常走完却报了失败"

        async with nonfatal("chat", "nonfatal-probe-outcome-bad") as bad:
            raise RuntimeError("boom")
        assert bad.failed is True, "块内抛了异常，调用方却以为成功"

    asyncio.run(_run())


def test_sync_and_async_share_the_single_reporting_outlet(monkeypatch):
    """同步版必须走 `_report` 这**一个**出口 —— 两版各写一遍格式，级别/模板迟早分叉。

    只断言「有一条记录」证明不了共用：各写一遍照样留一条。所以直接 patch 生产调用
    的那个绑定（`core.nonfatal._report`），断言两版都经过它，且参数一致。
    """
    import core.nonfatal as nonfatal_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        nonfatal_mod, "_report",
        lambda exc, source, what, level: calls.append((type(exc).__name__, source, what, level)),
    )
    marker = "nonfatal-shared-probe"

    with nonfatal_sync("sync-probe", marker, level=logging.WARNING):
        raise RuntimeError("sync-boom")

    async def _run() -> None:
        async with nonfatal("async-probe", marker):
            raise RuntimeError("async-boom")

    asyncio.run(_run())

    assert calls == [
        ("RuntimeError", "sync-probe", marker, logging.WARNING),
        ("RuntimeError", "async-probe", marker, logging.ERROR),
    ], f"两版没有共用一个上报出口，实得 {calls}"


# ── 1b. 同步版（`nonfatal_sync`）：契约与异步版逐条对齐 ────────────────────

def test_sync_ordinary_exception_is_swallowed_and_reported_once(caplog):
    """同步版与异步版同一条契约：吞掉，恰好 1 条 ERROR，带 source / what / 类型。"""
    marker = "nonfatal-sync-ordinary"

    with nonfatal_sync("clock", marker):
        raise RuntimeError("tz-boom")

    errs = _errors(caplog.records, marker)
    assert len(errs) == 1, f"应有且只有 1 条，实得 {len(errs)}"
    msg = errs[0].getMessage()
    assert "clock" in msg, "缺 source"
    assert "RuntimeError" in msg, "异常类型没进消息文本 —— 日志里看不到是谁"
    assert "tz-boom" in msg, "缺异常消息"


def test_sync_clean_block_reports_nothing(caplog):
    """正常走完不留记录 —— 与异步版一致，留痕不能变成噪音。"""
    marker = "nonfatal-sync-clean"

    with nonfatal_sync("clock", marker):
        pass

    assert _errors(caplog.records, marker) == []


def test_sync_outcome_tells_the_caller_whether_the_block_failed():
    """同步版的 `as` 信号：成功 `False`、失败 `True`（调用方唯一能拿到的失败信号）。"""
    with nonfatal_sync("clock", "nonfatal-sync-outcome-ok") as ok:
        pass
    assert ok.failed is False, "正常走完却报了失败"

    with nonfatal_sync("clock", "nonfatal-sync-outcome-bad") as bad:
        raise RuntimeError("boom")
    assert bad.failed is True, "块内抛了异常，调用方却以为成功"


@pytest.mark.parametrize(
    "exc_type", [KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_sync_control_flow_exceptions_pass_through(exc_type, caplog):
    """同步版放行同一组控制流异常（共用 `_PASS_THROUGH`），且**不**记成失败。"""
    marker = f"nonfatal-sync-{exc_type.__name__}"

    with pytest.raises(exc_type):
        with nonfatal_sync("clock", marker):
            raise exc_type()

    assert _errors(caplog.records, marker) == [], "控制流异常被当失败记了 —— 日志会被关闭噪音淹掉"


@pytest.mark.parametrize(
    "exc_type", [KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_control_flow_exceptions_pass_through(exc_type, caplog):
    """取消／关闭不是「失败」：照常上抛，且**不**记成失败。

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
    assert _errors(caplog.records, marker) == [], "控制流异常被当失败记了 —— 日志会被关闭噪音淹掉"


# ── 2. 真正改过的吞错点 ───────────────────────────────────────────────

class _BoomStorage:
    async def record_usage(self, *a, **kw):
        raise RuntimeError("usage db down")


class _StubLLM:
    _model = "stub-model"
    last_usage = {"prompt_tokens": 1, "completion_tokens": 2}


async def test_try_record_usage_reports_write_failure(monkeypatch, caplog):
    """`storage.record_usage` 抛异常 → 留一条 ERROR，且 `try_record_usage` 自身不抛。

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

    errs = _errors(caplog.records, "usageprobe")
    assert len(errs) == 1, f"用量写库失败必须留一条 ERROR，实得 {len(errs)}"
    assert "usage db down" in errs[0].getMessage()


class _Engine:
    """够 `_do_chat` 与 `_do_chat_stream` **两条**路径跑完的最小引擎替身
    （与 test_message_evidence 同形：一份替身服务两条路，接口一变一起滞后）。

    `last_summary` 非空才会走到摘要那一段，故它是参数而不是常量。
    """

    def __init__(self, stream_pieces=("回", "复"), last_summary=""):
        self.history = []
        self.last_traces = []
        self.last_summary = last_summary
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
    """**库不可达**：写与 ping 一起失败，队列因此整队保留（一条也不丢）。

    `get_messages` 照常返回，好让失败面窄到「消息写库」这一处。ping 是**显式**写出来的：
    不写它，`storage.ping` 是 AttributeError，也恰好被 `nonfatal` 吞成「探不到库」——
    那是碰巧对，不是判据。这里要的是「不可达」这件事本身。
    """

    async def save_message(self, *a, **kw):
        raise RuntimeError("message db down")

    async def ping(self):
        raise RuntimeError("message db down")

    async def get_messages(self, session_id):
        return []


def _wire(monkeypatch, **engine_kwargs):
    # 条目形状只从 `new_session_entry` 拿（不手搓 dict），也不在这里补字段 ——
    # 在调用点补等于把「条目长什么样」又拆成两处定义。
    session = new_session_entry(_Engine(**engine_kwargs), None, "u1")

    async def _fake_ensure(session_id, storage, sessions, user_id=""):
        return session

    monkeypatch.setattr(chat_router_mod, "_ensure_session", _fake_ensure)
    return session


def test_do_chat_save_failure_is_logged(monkeypatch, caplog):
    """非流式落库失败 → 留一条 ERROR。这是本次改动的交付物（失败可见）。

    调用**自身不炸**（缺陷 98 已修：`user_rec` / `char_rec` 有初值，取值统一走
    `_msg_fields`）。这里只断言交付物：那条记录确实留下了 —— 换成 `pytest.raises`
    会与「失败不影响主流程」这条相反，两条判据分属不同用例。

    `source` 从 `chat` 变成了 `outbox`：消息写入现在由队列代劳，异常在队列那一层被
    吞掉。文案仍是同一处 `nonfatal` 出口，故日志里照样看得见。
    """
    _wire(monkeypatch)
    asyncio.run(
        chat_router_mod._do_chat(
            "s1", "hi", storage=_FailingSaveStorage(), sessions={}, user_id="u1",
        )
    )

    errs = _errors(caplog.records, "message db down")
    assert errs, "消息落库失败没留痕 —— 又回到「失败只 print」的老样子"


def test_do_chat_returns_normally_when_save_fails(monkeypatch):
    """落库失败时接口应当照常返回回复（缺陷 98 的验收项）。

    这条**曾挂着 strict xfail**：当时 `user_rec`/`char_rec` 只在 nonfatal 块内绑定，
    块一吞掉异常，后面那两行 `.get("created_at")` 就是 UnboundLocalError → 500。
    修法是给它们初值 `None` 并统一走 `_msg_fields`（`None` → `(None, "")`）。

    判据是**四样一起**：回复没被吃掉、两个 id 为 `None`、两个 created_at 为空串、
    两条 save 都报 `pending` —— 只断言「没抛异常」的话，「把 deref 挪走但给个假
    时间戳」也会绿；而 `pending` 是「还留在队里，下次写/重试/关停会补上」这件事的
    唯一表述（库不可达 → 整队保留，一条不丢）。
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
    assert result["user_save"]["state"] == "pending"
    assert result["char_save"]["state"] == "pending", \
        "库不可达却报「丢了」—— 那会把还能补上的消息判死"
    assert result["flushed"] == [] and result["dropped"] == [], \
        "库不可达时不该有任何一条被判成补上或丢掉"


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
    assert done[0]["user_save"]["state"] == "pending"
    assert done[0]["char_save"]["state"] == "pending"
    assert "".join(f.get("token", "") for f in frames) == "回复", "正文没流出来"


# ── 3. 逐条保存逐条上报（口径统一：未保存的那一条自己标） ──────────────────
#
# 上面那两条只证「失败不上屏、回复不丢」。本段证**失败信号到了客户端**：done 帧带
# `save`（`{"state", "key"}`）与 `flushed` / `dropped`，前端才可能给失败的那一条标
# 「未保存」并在补上之后填回真 id。失败面收窄到单一 role，正是为了把「一个块串起
# 三笔」与「三笔各管各的」分开。
#
# 这里的库是**可达**的（ping 正常）：一条坏消息重试一次后判死（`failed` + `dropped`），
# 后面的消息照写。与第 2 段那个「库不可达 → 整队保留（`pending`）」是两条不同的路，
# 判据也分属不同用例。

class _RoleFailingStorage:
    """库**可达**，只让指定 role 的那一次落库失败 —— 失败面窄到「这一条消息」。"""

    def __init__(self, *, fail_role: str | None = None, fail_get_messages: bool = False):
        self._fail_role = fail_role
        self._fail_get_messages = fail_get_messages
        self.saved: list[str] = []

    async def save_message(self, session_id, role, content, rag_context, **kw):
        if role == self._fail_role:
            raise RuntimeError(f"{role} save down")
        self.saved.append(role)
        return {"id": len(self.saved), "created_at": "2026-01-01 00:00:00"}

    async def ping(self):
        """探得到库 —— 于是「写不进去」是这一条自己的问题，不是库的问题。"""

    async def get_messages(self, session_id):
        if self._fail_get_messages:
            raise RuntimeError("summary read down")
        return []


def _save_pair(frame: dict) -> tuple[dict | None, dict | None]:
    """取 done 帧 / 返回体里那两条 save 字段（没有即「这一条压根不存在」，如 `hidden`）。"""
    return frame.get("user_save"), frame.get("char_save")


def _done_frame(frames: list[dict]) -> dict:
    done = [f for f in frames if f.get("done") is True]
    assert len(done) == 1, f"应恰有一个 done 帧，实得 {len(done)}：{frames}"
    return done[0]


def _assert_no_error_frame(frames: list[dict]) -> None:
    errors = [f for f in frames if "error" in f]
    assert errors == [], f"失败升级成了 error 帧（本轮回复被中断）：{errors}"


def test_stream_char_save_failure_reports_only_that_one(monkeypatch):
    """流式：角色那条写不进去 → 只它报 `failed` 并进 `dropped`，用户那条照存。

    `failed` 是「重试过一次、还是写不进去，这条放弃了」—— 与「库不可达所以留着
    `pending`」是两回事，两者的前端动作也不同（一个没有重试按钮，一个有）。
    """
    _wire(monkeypatch)
    st = _RoleFailingStorage(fail_role="char")
    frames = asyncio.run(_drive_stream(st))

    _assert_no_error_frame(frames)
    assert "".join(f.get("token", "") for f in frames) == "回复", "正文没流出来"
    done = _done_frame(frames)
    user_save, char_save = _save_pair(done)
    assert char_save["state"] == "failed", "角色那条没存上，done 帧却报成功 —— 前端无从标「保存失败」"
    assert char_save["key"] in done["dropped"], "判死了却没进 dropped —— 前端找不到是哪一条"
    assert done["char_msg_id"] is None
    assert user_save is None, "用户那条存上了却还带着 save 字段 —— 前端会误标「未保存」"
    assert done["user_msg_id"] is not None, \
        "角色那条失败把用户那条也连坐了（改前三笔共用一个 nonfatal 的形态）"
    assert [f["id"] for f in done["flushed"]] == [done["user_msg_id"]], \
        f"用户那条补上后应出现在 flushed 里，实得 {done['flushed']}"


def test_stream_user_save_failure_does_not_take_the_reply_down(monkeypatch):
    """流式：用户那条写不进去 → 只它报 `failed`，角色那条照存。"""
    _wire(monkeypatch)
    st = _RoleFailingStorage(fail_role="user")
    frames = asyncio.run(_drive_stream(st))

    _assert_no_error_frame(frames)
    done = _done_frame(frames)
    user_save, char_save = _save_pair(done)
    assert user_save["state"] == "failed", "用户那条没存上，done 帧却报成功"
    assert user_save["key"] in done["dropped"]
    assert done["user_msg_id"] is None
    assert char_save is None and done["char_msg_id"] is not None, \
        "用户那条失败后角色那条根本没执行"
    assert st.saved == ["char"], f"实际写进库的 role 不对：{st.saved}"


def test_nonstream_char_save_failure_reports_only_that_one(monkeypatch):
    """非流式同上：三笔各自入队，返回值逐条上报。"""
    _wire(monkeypatch)
    st = _RoleFailingStorage(fail_role="char")
    result = asyncio.run(
        chat_router_mod._do_chat("s1", "hi", storage=st, sessions={}, user_id="u1")
    )
    assert result["reply"] == "回复"
    assert result["char_save"]["state"] == "failed" and result["char_msg_id"] is None
    assert result["char_save"]["key"] in result["dropped"]
    assert "user_save" not in result and result["user_msg_id"] is not None


def test_nonstream_user_save_failure_reports_only_that_one(monkeypatch):
    """非流式同流式的用户那条失败。"""
    _wire(monkeypatch)
    st = _RoleFailingStorage(fail_role="user")
    result = asyncio.run(
        chat_router_mod._do_chat("s1", "hi", storage=st, sessions={}, user_id="u1")
    )
    assert result["reply"] == "回复"
    assert result["user_save"]["state"] == "failed" and result["user_msg_id"] is None
    assert result["user_save"]["key"] in result["dropped"]
    assert "char_save" not in result and result["char_msg_id"] is not None
    assert st.saved == ["char"], f"实际写进库的 role 不对：{st.saved}"


def test_hidden_has_no_user_save_field(monkeypatch):
    """`hidden` 时压根没有用户消息要存，报个 save 字段会让前端给**上一条**真消息误标。

    判据不能用「id 为 None」推：`hidden` 时 id 本来就是 None（缺陷 98）。所以这里让
    用户那条的保存**必定失败**（`fail_role="user"`），`user_save` 仍须缺席 —— 假判据
    在这条上必红。
    """
    _wire(monkeypatch)
    st = _RoleFailingStorage(fail_role="user")
    frames = asyncio.run(_drive_stream(st, hidden=True))

    _assert_no_error_frame(frames)
    done = _done_frame(frames)
    user_save, _char_save = _save_pair(done)
    assert user_save is None, "hidden 没有用户消息可存，不该报未保存"
    assert st.saved == ["char"], f"hidden 却写了用户消息：{st.saved}"


def test_stream_summary_read_failure_does_not_break_the_turn(monkeypatch, caplog):
    """摘要那段的**读**失败也不能打断已流完的回复（改前 `get_messages` 裸露在块外）。

    改前形态：读抛在大 try 里 → 正文已整段流给用户，收尾却发 error 帧、没有 done 帧。
    读没进队列（它不产生行 id），所以仍是一次失败一条日志。
    """
    _wire(monkeypatch, last_summary="新摘要")
    frames = asyncio.run(_drive_stream(_RoleFailingStorage(fail_get_messages=True)))

    _assert_no_error_frame(frames)
    _done_frame(frames)
    assert "".join(f.get("token", "") for f in frames) == "回复", "正文没流出来"
    errs = _errors(caplog.records, "summary read down")
    assert len(errs) == 1, f"摘要读失败没留下记录，实得 {len(errs)} 条"


def test_stream_summary_save_failure_does_not_break_the_turn(monkeypatch, caplog):
    """摘要**写**失败：不中断、不上屏，但必须留痕（吞掉 ≠ 沉默，本文件的主题）。

    这里**是两条**：队列对「库可达、这一条写不进去」重试一次才判死，两次都留痕。
    数成一条就会把「重试过」这件事从日志里抹掉。
    """
    _wire(monkeypatch, last_summary="新摘要")
    frames = asyncio.run(_drive_stream(_RoleFailingStorage(fail_role="summary")))

    _assert_no_error_frame(frames)
    _done_frame(frames)
    errs = _errors(caplog.records, "summary save down")
    assert len(errs) == 2, f"摘要写失败（首写 + 重试）应留 2 条，实得 {len(errs)} 条"
