# -*- coding: utf-8 -*-
"""chat SSE 错误帧契约：未完成终态必须可识别（code + finish_reason），且不把运维口径
和内部 where 标识漏给终端用户；其余异常只给通用文案，原文进日志。

前端 client.js 靠 payload.error 展示、靠 payload.code 区分——这条契约断了，
用户看到的就会退回「连接中断」或一句暴露内部标识的乱码。
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import httpx2
import openai
import pytest
from conftest import cause_chain
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import OpenAI as _REAL_OPENAI

import adapters.llm_adapter as M
import server  # web/server.py 装配层（conftest 已把 web/ 加进 sys.path）
from adapters.llm_adapter import (
    IncompleteResponseError,
    UpstreamFailure,
    llm_error_payload,
    llm_error_types,
)
from routers.chat import _stream_error_payload


def test_incomplete_payload_is_identifiable():
    p = _stream_error_payload(IncompleteResponseError("length", "chat_stream"))
    assert p["code"] == "incomplete_response"
    assert p["finish_reason"] == "length"


def test_incomplete_payload_is_user_facing():
    p = _stream_error_payload(IncompleteResponseError("content_filter", "chat_stream"))
    assert p["error"] == "内容被安全策略拦截，请修改后重试"
    assert "chat_stream" not in p["error"]          # 内部 where 标识不上屏
    assert "max_tokens" not in p["error"]           # 运维口径不上屏


def test_unregistered_errors_get_generic_text():
    """未登记的异常只给通用文案：上游原文（缺陷 94 泄漏那半）不许上屏，只进日志。"""
    raw = "LLM API failed after 3 attempts: 500 upstream boom"
    p = _stream_error_payload(RuntimeError(raw))
    assert p == {"error": "服务暂时不可用，请稍后重试"}
    assert raw not in p["error"]
    assert "code" not in p


# ── 错误边界：路由层认 code 字符串，不 import 异常类 ──────────────────

def test_boundary_maps_known_failure_and_rejects_others():
    p = llm_error_payload(IncompleteResponseError("length", "chat_stream"))
    assert p["code"] == "incomplete_response"
    assert p["finish_reason"] == "length"
    assert p["error"] == "回复被截断，请重试"
    assert llm_error_payload(RuntimeError("boom")) is None


_BOUNDARY_SUBTREES = ("core", "web", "storage")


def _boundary_sources(overrides: dict[str, str] | None = None) -> dict[str, str]:
    root = pathlib.Path(__file__).resolve().parent.parent
    sources = {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for sub in _BOUNDARY_SUBTREES
        for p in (root / sub).rglob("*.py")
    }
    if overrides:
        sources.update(overrides)
    return sources


def _boundary_offenders(sources: dict[str, str], names: list[str]) -> list[str]:
    """源码里**真的引用了**这些类名的地方 —— 只看 import / Name / Attribute。

    走 AST、不扫原文：注释与 docstring 里为了讲清机制而提一句类名，不是耦合
    （反过来，原文扫描会逼着文档绕开标识符，把说明写糊）。判据是「这行代码
    认不认得这个类」：`from … import X`、裸 `X`、`mod.X` 三种形态。
    解析失败**不跳过**：整批调用点会随文件一起消失，锁假绿。
    """
    hits: list[str] = []
    for rel, src in sources.items():
        try:
            tree = ast.parse(src, filename=rel)
        except SyntaxError as exc:
            hits.append(f"{rel}: 解析失败（{type(exc).__name__}: {exc}）")
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                found = [a.name for a in n.names if a.name in names]
            elif isinstance(n, ast.Name):
                found = [n.id] if n.id in names else []
            elif isinstance(n, ast.Attribute):
                found = [n.attr] if n.attr in names else []
            else:
                continue
            hits += [f"{rel}:{n.lineno}: {name}" for name in found]
    return hits


def test_no_exception_class_leaks_into_core_web_storage():
    """边界锁：core/web/storage 只认 code 字符串（`llm_error_payload` 的 `kind`）。

    类名清单**取自 `llm_error_types()`**、不写字面量：新增一种 LLM 失败 = 在那一个元组
    加一个类，本锁自动纳入，不会出现「新异常类漏检」。在路由层 import / isinstance /
    `except` 具体类，这条会红——它拦的就是那类耦合的重现。
    """
    names = [cls.__name__ for cls in llm_error_types()]
    assert names, "llm_error_types() 为空 —— 锁在假绿"
    assert _boundary_offenders(_boundary_sources(), names) == []


def test_boundary_lock_reddens_on_a_real_reference_in_core():
    """变异自测①：core 里加一行真 import → 红。锁不具分辨力时这条先塌。"""
    names = [cls.__name__ for cls in llm_error_types()]
    injected = f"from adapters.llm_adapter import {names[0]}\n"
    hits = _boundary_offenders({"core/utils.py": injected}, names)
    assert hits, f"core 里 import 了异常类名，锁没红 —— 判据是假的：{hits}"


def test_boundary_lock_ignores_comments_and_docstrings():
    """变异自测②：只写进注释/docstring → **不**红（文档要能讲清机制）。"""
    names = [cls.__name__ for cls in llm_error_types()]
    src = (
        f'"""说明机制时会提到 {names[0]}，但那不是引用。"""\n'
        f"# 历史注释里提 {names[-1]} 同理\n"
        "x = 1\n"
    )
    hits = _boundary_offenders({"core/utils.py": src}, names)
    assert hits == [], f"注释/docstring 被当成耦合 —— 判据扫过头了：{hits}"


# ── WP2 S3：流中断归类为上游失败（真 socket）→ 503 + 上屏文案 ──────────
#
# 「读流中途断掉」无法用桩表达：桩不会真的在读上超时，也不会真的断连。本文件里的
# 假 SSE 服务吐一片之后关连接（并承诺一个远大于实发的 Content-Length —— HTTP/1.0
# 无长度头时中途断开是以 EOF 干净收尾，客户端读不到任何错，那条路测不出协议错）。

_UPSTREAM_TEXT = "模型服务暂时不可用，请稍后重试"
_MSGS = [{"role": "user", "content": "hi"}]


def _minimal_app(monkeypatch, fake_sse):
    """最小 app：一个消费 `chat_stream` 的路由 + 与生产**同一处**注册的异常出口。"""
    monkeypatch.setattr(M, "OpenAI", _REAL_OPENAI)      # 真客户端（真 socket）
    monkeypatch.setattr(M, "_STREAM_ATTEMPT_S", 0.5)     # 不真等：读窗缩到 0.5s
    monkeypatch.setattr(M, "_STREAM_DEADLINE_S", 1.5)
    llm = fake_sse.adapter()

    app = FastAPI()

    @app.get("/boom")
    def boom():
        for _ in llm.chat_stream("sys", _MSGS):
            pass
        return {"ok": True}

    server.register_domain_error_handlers(app)
    return llm, TestClient(app, raise_server_exceptions=False)


def test_s3_stream_disconnect_is_upstream_failure(fake_sse, monkeypatch):
    """吐一片后断开 → UpstreamFailure，上屏文案取自本层；原因链保留上游协议错。"""
    fake_sse.plan.update(tokens=["a"], disconnect_after=1)
    llm, _ = _minimal_app(monkeypatch, fake_sse)

    with pytest.raises(UpstreamFailure) as ei:
        list(llm.chat_stream("sys", _MSGS))

    assert ei.value.user_message == _UPSTREAM_TEXT
    kinds = [type(e).__name__ for e in cause_chain(ei.value)]
    assert "RemoteProtocolError" in kinds, f"原因链必须保留上游协议错，实际 {kinds}"


def test_s3_stream_read_timeout_is_upstream_failure(fake_sse, monkeypatch):
    """首字节前的读超时（生产 500 的那条）同样归成上游失败，不再是裸的传输层异常。"""
    fake_sse.plan.update(silent_ms=1000, tokens=["never"])
    llm, _ = _minimal_app(monkeypatch, fake_sse)

    with pytest.raises(UpstreamFailure) as ei:
        list(llm.chat_stream("sys", _MSGS))

    assert ei.value.user_message == _UPSTREAM_TEXT
    kinds = [type(e).__name__ for e in cause_chain(ei.value)]
    assert "ReadTimeout" in kinds, f"原因链必须保留读超时，实际 {kinds}"


def test_s3_stream_disconnect_maps_to_503(fake_sse, monkeypatch):
    """HTTP 侧：装同一份注册后，流中断回 503 + 上游文案（全文即用户可见文案）。"""
    fake_sse.plan.update(tokens=["a"], disconnect_after=1)
    _, client = _minimal_app(monkeypatch, fake_sse)

    r = client.get("/boom")

    assert r.status_code == 503, f"流中断必须回 503，实际 {r.status_code}"
    assert r.json()["detail"] == _UPSTREAM_TEXT


def test_s3_our_own_bug_is_not_reported_as_upstream(fake_sse, monkeypatch):
    """包装只认传输层：流里抛出的自身缺陷必须**不是** 503。

    变异（把包装放宽成 `except Exception`）时这条变红。把我们自己的 bug 报成上游失败，
    用户会被引去「稍后重试」，而真正该修的是我们 —— 宁可 500 显形。
    """
    fake_sse.plan.update(tokens=["a"])
    _, client = _minimal_app(monkeypatch, fake_sse)
    monkeypatch.setattr(
        M, "_check_finish_reason",
        lambda *a, **kw: (_ for _ in ()).throw(AttributeError("our own bug")),
    )

    r = client.get("/boom")

    assert r.status_code != 503, "自身缺陷被冒充成上游失败（用户会被误导去重试）"
    assert r.status_code == 500


# ── WP2 S3：传输层失败的**版本面**与「两条路同一句话」──────────────────
#
# 上面几条用真 socket 造断连/读超时；这两条判的是**分类**，只能手搓异常 ——
# openai ≥3.19 在 `_streaming.py` 读到传输层错之后会 new 一个自己的 APIConnectionError，
# 那个对象不是真 socket 能造出来的（3.13.0 不这么包，故本地栈上跑不出这一形态）。

class _RaisingStream:
    def __iter__(self):
        return self

    def __init__(self, exc):
        self._exc = exc

    def __next__(self):
        raise self._exc


class _RaisingCompletions:
    def __init__(self, exc, at):
        self._exc, self._at = exc, at

    def create(self, **kwargs):
        if self._at == "create":
            raise self._exc
        return _RaisingStream(self._exc)


class _NoopOpenAI:
    """替真实构造：用例随后会换掉 `_client`，这里只要__init__不真连网。"""

    def __init__(self, **kwargs):
        pass


def _stub_llm(monkeypatch, exc, at):
    """一个指向「按 `at` 抛 `exc`」的桩客户端的真 adapter（`at` ∈ create / read）。"""
    monkeypatch.setattr(M, "OpenAI", _NoopOpenAI)
    monkeypatch.setattr(M, "_STREAM_ATTEMPT_S", 0.5)   # 不真等：读窗与天花板都缩到 0.5s
    monkeypatch.setattr(M, "_STREAM_DEADLINE_S", 1.5)
    llm = M.LLMAdapter(api_key="sk-test-fake")
    llm._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_RaisingCompletions(exc, at)))
    return llm


def _wrapped_connection_error() -> openai.APIConnectionError:
    """openai ≥3.19 读流中断的形态：APIConnectionError（**不是** httpx2 的子类）。"""
    return openai.APIConnectionError(
        request=httpx2.Request("POST", "http://fake-upstream/v1/chat/completions"))


def test_s3_openai_wrapped_transport_error_is_upstream_failure(monkeypatch):
    """变异③：`_TRANSPORT_ERRORS` 去掉 `APIConnectionError` → 本条红（退回 500）。

    sentinel 上的 3 条红就是这一形态：openai 3.19.2 把流中途的传输层异常包成
    APIConnectionError（超时是它的子类 APITimeoutError），而它**不是** httpx2.TransportError
    的子类 —— 只认 httpx2 的包装会在 openai 升过 3.1x 后静默失效。
    """
    exc = _wrapped_connection_error()
    llm = _stub_llm(monkeypatch, exc, at="read")

    with pytest.raises(UpstreamFailure) as ei:
        list(llm.chat_stream("sys", _MSGS))

    assert ei.value.user_message == _UPSTREAM_TEXT
    assert exc in cause_chain(ei.value), "原因链必须保留 openai 包出来的那个异常（排障要看它）"


def test_s3_transport_text_is_identical_on_both_paths(monkeypatch):
    """变异⑥：`_upstream_user_message` 不认传输层 → 本条红（两条都退回通用口径）。

    同一个传输层故障，建流阶段（create() 直接失败、重试耗尽）与读流阶段（迭代中断）
    必须说出**同一句话**。两处各写一份文案时（旧写法：读流侧用自己的常量、建流侧查
    状态码表得到 ""），用户看到「重发一次」还是笼统的「服务暂时不可用」取决于故障恰巧
    落在哪一段 —— 相等断言拦的是这个；字面量那半拦「两边一起改成同一句错话」。
    """
    build = _stub_llm(monkeypatch, httpx2.ConnectError("connect boom"), at="create")
    with pytest.raises(UpstreamFailure) as e_build:
        list(build.chat_stream("sys", _MSGS))

    mid = _stub_llm(monkeypatch, httpx2.ConnectError("connect boom"), at="read")
    with pytest.raises(UpstreamFailure) as e_mid:
        list(mid.chat_stream("sys", _MSGS))

    assert e_build.value.user_message == e_mid.value.user_message == _UPSTREAM_TEXT
