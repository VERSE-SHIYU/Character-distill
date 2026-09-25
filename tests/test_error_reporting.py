"""`init_error_reporting` —— 后端报错经 sentry-sdk 上报到自托管 GlitchTip。

判据对应约束里的三句：DSN 空则整个模块不生效、报错带堆栈与 `region` 标签、同一个
未捕获的路由异常只产生**一条**事件且不带请求正文。

事件靠**录制 transport** 收：换掉 SDK 发信的那一段，全程不出网。
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
import sentry_sdk
from pydantic import BaseModel
from sentry_sdk.transport import Transport

from core.error_reporting import _scrub_request, init_error_reporting

DSN = "https://public@errors.example.test/1"

# 探针用的字面量放模块级：Sentry 的堆栈帧会带**源码上下文**（出事那行的上下几行），
# 字面量若紧挨路由函数的定义，就会被算成「事件里出现了正文」，那是测试自己造的假象。
EXC_MARKER = "obs-probe-uncaught"
BODY_MARKER = "obs-probe-request-body"
STARTUP_MARKER = "obs-probe-startup"
PROBE_PATH = "/__error_reporting_probe"


class _Recorder(Transport):
    """只把信封里的 event 收下来，不往外发。"""

    def __init__(self, options=None):
        super().__init__(options)
        self.events: list[dict] = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


@pytest.fixture(autouse=True)
def _restore_global_client():
    """用例后还原全局 client。

    `sentry_sdk.init()` 只动**全局** scope 上的 client（实测隔离 scope 不动），
    所以还原这一点就够；不还原的话，本用例初始化过的 client 会跟着后面所有用例。
    """
    before = sentry_sdk.get_global_scope().client
    yield
    sentry_sdk.get_global_scope().set_client(before)


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    """给被测模块调用的 `sentry_sdk.init` 注入录制 transport。

    被测代码调的是 `sentry_sdk.init(...)`（模块属性查表），所以换掉模块上那一个即可，
    不必给生产代码开测试专用参数。
    """
    rec = _Recorder()
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: real_init(transport=rec, **kw))
    return rec


def test_empty_dsn_does_not_initialize(monkeypatch):
    """`SENTRY_DSN` 为空 → 一次 init 都不调。"""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    calls: list[dict] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))

    init_error_reporting()

    assert calls == [], f"SENTRY_DSN 为空却调了 init：{calls}"
    assert not sentry_sdk.is_initialized(), "DSN 为空却把 SDK 初始化了"


def test_error_with_traceback_carries_stack_and_region_tag(recorder, monkeypatch):
    """`logger.error(..., exc_info=True)` → 一条事件，带堆栈、带 `region` 标签。

    `NODE_REGION` 故意设成**非缺省**值：设成缺省值时，标签对不上也说明不了读没读环境
    变量 —— 一律回落成缺省同样满足断言，那这条就证不到 `region` 来自节点身份。
    """
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("NODE_REGION", "probe-region")

    init_error_reporting()
    try:
        raise ZeroDivisionError("obs-probe-div")
    except ZeroDivisionError:
        logging.getLogger("error_reporting_probe").error("obs-probe-error", exc_info=True)
    sentry_sdk.flush()

    assert len(recorder.events) == 1, f"应恰好一条事件，实得 {len(recorder.events)}"
    event = recorder.events[0]
    assert event["logentry"]["message"] == "obs-probe-error", event.get("logentry")
    assert event["tags"]["region"] == "probe-region", event.get("tags")
    last = event["exception"]["values"][-1]
    assert last["type"] == "ZeroDivisionError", last
    assert last["stacktrace"]["frames"], f"事件里没有堆栈帧：{last}"


def test_request_content_is_scrubbed(recorder, monkeypatch):
    """`before_send` 挂上了，且真删得掉正文与 cookie。

    本接线方式下 SDK 不往日志事件里放 `request`（实测），所以这条闸没有一个「现状下会
    漏」的场景可演；它钉的是**闸本身** —— 函数删得对，且确实挂在 init 上。
    """
    monkeypatch.setenv("SENTRY_DSN", DSN)

    init_error_reporting()

    before_send = sentry_sdk.get_client().options["before_send"]
    assert before_send is _scrub_request, "before_send 没挂到 init 上"

    event = {
        "request": {
            "url": "https://errors.example.test/x",
            "data": {"note": BODY_MARKER},
            "cookies": {"sid": "should-not-be-reported"},
        }
    }
    assert before_send(event, {})["request"] == {"url": "https://errors.example.test/x"}


def test_only_the_logging_path_is_wired(recorder, monkeypatch):
    """只留日志汇合点这一条路：`logging` 集成在，`fastapi` / `starlette` 集成不在。

    这条钉的是**配置**，不是「观察到的事件条数」。spec 给的理由是「放开自动集成会让
    Starlette 集成对同一个异常再报一条」，实测在本接线方式下**不成立**：`server.app` 在
    导入期就建好了，SDK 却是 lifespan 首行才 init，那两个集成的补丁打在类上、对既有的
    app 不生效 —— 把 `auto_enabling_integrations` 打开、路由函数换成同步的，事件照样只有
    一条。所以「只报一条」那条断言在 `auto_enabling_integrations` 这个杠杆上打不红，
    要它有效力就只能直接钉集成集合。
    """
    monkeypatch.setenv("SENTRY_DSN", DSN)

    init_error_reporting()

    identifiers = {i.identifier for i in sentry_sdk.get_client().integrations.values()}
    assert "logging" in identifiers, f"日志汇合点那条路没了：{sorted(identifiers)}"
    assert not identifiers & {"fastapi", "starlette"}, (
        f"自动集成被放开了，同一个异常会多报一条：{sorted(identifiers)}"
    )


class _ProbeBody(BaseModel):
    """探针请求体。**带真模型**是这条用例的一半：正文要真的被解析进某一帧的局部变量，
    「正文不上报」才有东西可判 —— 空体路由谁都不会漏。"""

    note: str


async def _boom(payload: _ProbeBody) -> None:
    raise RuntimeError(EXC_MARKER)


def test_startup_failure_is_reported_before_the_client_closes(recorder, monkeypatch):
    """启动期自身抛的错也要报出去 —— 这正是这条出口排在 lifespan 首位的理由。

    判据落在「异常冒出去之前，事件已经发出去了」上。撤销若走 `stack.callback`，回调拿不
    到正在冒出的异常，顺序就反了：flush + close 先跑，异常这才冒出 lifespan 交给 uvicorn
    —— 那时 client 已经关了，这条出口的唯一目的正好落空。故撤销是 `stack.push` 的退出
    函数（见 `core/error_reporting._ReportingExit`）。

    变异：把 `_lifespan` 里那处 `stack.push` 换回 `stack.callback`（撤销不带异常信息）
    → 本条红。
    """
    monkeypatch.setenv("SENTRY_DSN", DSN)

    import server as server_mod

    def _boom() -> None:
        raise RuntimeError(STARTUP_MARKER)

    # 只留一个**会抛**的校验项，就落在上报之后几步的位置上（lifespan 里的顺序）：
    # fernet 与 inter-node 的校验关掉，让 jwt 那一处成为唯一的失败点。
    monkeypatch.setattr(server_mod, "validate_fernet_key", lambda: None)
    monkeypatch.setattr(server_mod, "validate_jwt_secret", _boom)

    class _App:
        state = type("S", (), {"limiter": object()})()

    async def _drive() -> None:
        async with server_mod._lifespan(_App()):
            pass  # pragma: no cover —— 上面那步就该抛，走不到这里

    with pytest.raises(RuntimeError, match=STARTUP_MARKER):
        asyncio.run(_drive())

    sentry_sdk.flush()
    blob = json.dumps(recorder.events)
    assert STARTUP_MARKER in blob, f"启动期抛的错没上报（撤销顺序反了？）：{blob}"


def test_uncaught_route_error_reports_once_without_request_body(recorder, monkeypatch):
    """未捕获的路由异常 → 一条事件，且事件里没有请求正文。

    判据落在「带这个异常标记的事件恰好一条」上：装了 `[fastapi]` extra 或放开自动集成
    时，Starlette 集成会对同一个异常再报一条，两条都带这个标记 —— 单看总数会与启动期
    的其它事件混在一起，看不出重复。

    不需要额外夹具收拾进程级全局：`with TestClient(server.app)` 退出后 lifespan 自己
    撤销守卫与投递器（缺陷 113 的修法，见 `web/server.py::_lifespan`）。本文件按字母序
    排在 `test_llm_access_gate.py` **之前**，撤不干净就会把那个文件里直接调适配器出站
    的用例在**自己那条断言之前**打红成 `LLMCallerMissing`。
    """
    monkeypatch.setenv("SENTRY_DSN", DSN)

    from fastapi.testclient import TestClient

    import server

    # 不带 /api/ 前缀：`AuthMiddleware` 对非 /api/ 路径一律放行（server.py:374），
    # 否则未登录的请求在中途就被 401 掉了，根本走不到路由。
    server.app.add_api_route(PROBE_PATH, _boom, methods=["POST"])
    try:
        with TestClient(server.app, raise_server_exceptions=False) as client:
            resp = client.post(PROBE_PATH, json={"note": BODY_MARKER})
        assert resp.status_code == 500, resp.text
        sentry_sdk.flush()
    finally:
        server.app.router.routes[:] = [
            r for r in server.app.router.routes if getattr(r, "path", None) != PROBE_PATH
        ]

    blob = json.dumps(recorder.events)
    assert EXC_MARKER in blob, f"这个异常压根没上报：{blob}"
    hits = [e for e in recorder.events if EXC_MARKER in json.dumps(e)]
    assert len(hits) == 1, (
        f"同一个未捕获异常应只报一次，带标记的实得 {len(hits)} 条"
        f"（本次共收 {len(recorder.events)} 条）"
    )
    assert BODY_MARKER not in blob, f"请求正文被带上报了：{blob}"
