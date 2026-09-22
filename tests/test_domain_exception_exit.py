# -*- coding: utf-8 -*-
"""缺陷 38：领域异常 → (状态码, 上屏文案) 的**统一出口**。

路由层只 raise 领域异常，不碰文案、不碰状态码；配码与取文案集中在
``web/server.py::register_domain_error_handlers`` 一处（文案仍走 adapters 的
``user_facing_error``，**不新建第二张消息表**）。本文件锁四件事：

  A. 注册面 —— 领域异常真的挂在 app 上。变异对象：把 ``_DOMAIN_ERROR_STATUS`` 改空 /
     把 ``llm_error_types()`` 返回空元组 → A 组直接红。
  B. 非流式（JSON）—— ``/run`` 收 ``DistillError`` → 400 + ``user_message``，
     **运维口径（``str()`` 的后半截）不上屏**。这正是缺陷 38 本体。
  C. **反向验收** —— A 类（用户输入校验的裸 ``ValueError``）没被顺手统一：
     仍走路由自己的 400 + ``str(exc)``。它是唯一上屏通道，收了就是删信息。
  D. 另两种形状 —— SSE 错误帧 / 后台任务状态行与 HTTP detail 取**同一份口径链**；
     它们的「码」各归各的（SSE 已发 200、任务无响应），故不入状态码表。

为什么 C 组不是多余的：本缺陷的诱因正是「看着像泄漏就统一」。C 组锁住「不像泄漏的才收，
像泄漏但有正当消费者的不碰」这条边界。
"""
from __future__ import annotations

import ast
import json
import pathlib
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
import server  # web/server.py 装配层（conftest 已把 web/ 加进 sys.path）
from adapters.llm_adapter import IncompleteResponseError, llm_error_types, user_facing_error
from core.distiller import DistillError
from deps import get_storage
from routers.auth import get_current_user
from routers.distill import router as distill_router
from routers.history import router as history_router

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DISTILL_SRC = (_ROOT / "web" / "routers" / "distill.py").read_text(encoding="utf-8")


# ── 假件 ──────────────────────────────────────────────────────────────────


class _FakeTM:
    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc

    async def get_or_distill(self, *args: Any, **kwargs: Any):
        raise self._exc


class _FakeDistiller:
    """只提供 /run_stream 真正走到的那两个入口。

    ``distill_incremental_stream`` 写成 generator：函数体在**首次 next()** 时才执行，
    异常正落在路由那圈 ``try`` 里 —— 与生产里「流读到一半炸」同形状。
    """

    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc

    def identify_characters(self, content: str) -> list:
        return []

    def distill_incremental_stream(self, content, name, aliases, text_type):
        raise self._exc
        yield  # noqa: unreachable —— 只为把它变成 generator


class _Store:
    def __init__(self, *, session=None, export_exc: BaseException | None = None) -> None:
        self._session = session
        self._export_exc = export_exc

    async def get_text_owned(self, text_id: str, user_id: str) -> dict:
        return {"text_id": text_id, "content": "正文", "text_type": "story", "user_id": user_id}

    async def get_user_api_config(self, user_id: str) -> dict:
        return {}

    async def get_session_owned(self, session_id: str, user_id: str):
        return self._session

    async def export_session(self, session_id: str, fmt: str) -> str:
        raise self._export_exc


def _make_app(monkeypatch, store, *, tm=None, distiller=None, with_handlers: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(distill_router)
    app.include_router(history_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "t", "role": "user"}

    async def _fake_user_llm(*args, **kwargs):
        return object()

    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(deps, "get_distiller", lambda *a, **kw: distiller or _FakeDistiller())
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: tm or _FakeTM())

    if with_handlers:
        # 与生产装配**同一处**注册 —— 不是测试里另抄一份，否则注册表被改空测试也不动。
        server.register_domain_error_handlers(app)
    return app


def _client(app: FastAPI) -> TestClient:
    # raise_server_exceptions=False：没被处理器接住时表现为 500 而不是把异常抛穿，
    # 让「映射表被改空」这种变异以状态码断言失败的形式变红（而不是把用例炸成 error）。
    return TestClient(app, raise_server_exceptions=False)


def _sse_frames(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


# ── A. 注册面 ─────────────────────────────────────────────────────────────


def test_a1_distill_error_is_registered_at_400():
    assert DistillError in server.app.exception_handlers, (
        "DistillError 没挂到 app 的统一出口 —— 路由里 `raise` 出去会落到全局 500")
    assert server._domain_error_status(DistillError("上屏", "运维")) == 400


def test_a2_llm_error_types_are_registered():
    types_ = llm_error_types()
    assert types_, "adapters 必须发布至少一种 LLM 侧异常，否则未完成终态没了出口"
    for cls in types_:
        assert cls in server.app.exception_handlers, f"{cls} 未注册到统一出口"


def test_a3_user_input_value_error_is_deliberately_not_registered():
    """反向验收的静态一半：表里**只有**领域异常，没有 `ValueError` 这种宽口径。

    若哪天有人图省事注册 `ValueError`（pydantic 的 ValidationError 也是它的子类），
    会把「用户输入校验」与「上游库内部报错」一起卷进来原样上屏 —— 这条先红。
    """
    assert ValueError not in server.app.exception_handlers
    assert set(server._DOMAIN_ERROR_STATUS) == {DistillError}


# ── B. 非流式（JSON）：/run ───────────────────────────────────────────────


def test_b1_run_distill_error_is_400_and_screens_only_user_message(monkeypatch):
    """缺陷 38 本体：原先路由 `except ValueError: HTTPException(400, str(exc))` 把
    ``user_message｜ops_detail`` 整条上屏。"""
    exc = DistillError("蒸馏失败：未能从任何片段中提取到角色信息", "raw_analyses=0；100 个分片全失败")
    store = _Store()
    app = _make_app(monkeypatch, store, tm=_FakeTM(exc))

    r = _client(app).post("/api/distill/run", json={"text_id": "t1", "character_name": "阿Q"})

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail == exc.user_message
    assert "raw_analyses" not in detail, f"运维口径漏上屏：{detail!r}"
    assert "个分片" not in detail, f"运维口径漏上屏：{detail!r}"
    assert "｜" not in detail, f"两套口径的分隔符也不该出现：{detail!r}"


@pytest.mark.parametrize("finish_reason,status", [
    ("content_filter", 400),
    ("length", 502),
    ("insufficient_system_resource", 503),
    ("brand_new_unregistered_value", 502),
])
def test_b2_llm_incomplete_maps_by_finish_reason(finish_reason, status):
    """LLM 侧未完成终态：同一异常类下 `finish_reason` 语义不同 → 码不同。

    这张表从 web/routers/chat.py **搬家**到 web/server.py（不是再造）——
    未登记值兜 502（上游问题），**不兜 500**（我们的故障）。
    """
    app = FastAPI()
    server.register_domain_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise IncompleteResponseError(finish_reason, "test")

    r = _client(app).get("/boom")
    assert r.status_code == status, r.text
    assert r.json()["detail"] == user_facing_error(IncompleteResponseError(finish_reason, "test"))
    assert "finish_reason" not in r.json()["detail"]


# ── C. 反向验收：A 类没被顺手统一 ─────────────────────────────────────────


def test_c1_a_class_value_error_still_screens_its_own_message(monkeypatch):
    """用户输入校验类的裸 `ValueError` 必须仍走路由自己的 400 + `str(exc)`。

    它没有 `user_message`，走统一出口会落到「服务暂时不可用」—— **那是删信息**。
    等价契约见 tests/test_security_authz.py::test_10_value_error_400（本组把它
    在**新注册**在场的条件下再锁一遍：处理器确实没越权接走它）。
    """
    store = _Store(session={"id": "s1"}, export_exc=ValueError("不支持的导出格式: xlsx"))
    app = _make_app(monkeypatch, store)

    r = _client(app).get("/api/history/s1/export")

    assert r.status_code == 400, r.text
    assert "xlsx" in r.json()["detail"], f"用户需要的信息被删了：{r.json()['detail']!r}"


# ── D. 另两种形状：码各归各的，文案同源 ────────────────────────────────────


def test_d1_sse_error_frame_uses_the_same_wording(monkeypatch):
    """SSE（/run_stream）：异常发生时 200 + event-stream 已在线，处理器产出的是**第二个响应**、
    Starlette 不会再发 —— 管不到，不是不让管。故它不进状态码表，只共用文案出口。"""
    exc = DistillError("蒸馏失败：上游接口限流，请稍后重试", "API 429；9/12 个分片失败")
    app = _make_app(monkeypatch, _Store(), distiller=_FakeDistiller(exc))

    r = _client(app).post("/api/distill/run_stream", json={"text_id": "t1", "character_name": "阿Q"})

    assert r.status_code == 200
    errs = [f for f in _sse_frames(r.text) if "error" in f]
    assert len(errs) == 1, f"期望恰好一帧 error，实际 {errs}"
    assert errs[0]["error"] == user_facing_error(exc) == exc.user_message
    assert "429" not in errs[0]["error"] and "个分片" not in errs[0]["error"]


def test_d2_background_task_message_uses_the_same_wording():
    """第三种形状：``/start`` 的后台任务连响应都没有，只能写任务状态行 —— 同样取这一份口径。

    锁在源码上（这条链要真跑得动线程池 + 信号量 + DB 落盘，那已经是端到端用例的活；
    这里只锁「出口没被绕开」）。变异：把 `user_facing_error(exc)` 换回 `str(exc)` → 红。

    锚点收窄到**引用了该 except 绑定的异常变量**的 message —— 字面量（如「已取消」
    「蒸馏失败：数据校验错误」）是刻意写死的人话，不该被这条锁管；`piece["error"]`
    是上游流自带的 code，另说。只有「把 `exc` 原样 (或经 str) 上屏」才是缺陷 38 形态。
    """
    tree = ast.parse(_DISTILL_SRC)
    found = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        for node in ast.walk(handler):
            if not isinstance(node, ast.Dict):
                continue
            pairs = {
                k.value: v
                for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if pairs.get("status") is None or "message" not in pairs:
                continue
            if not (isinstance(pairs["status"], ast.Constant) and pairs["status"].value == "error"):
                continue
            uses_bound_exc = any(
                isinstance(n, ast.Name) and n.id == handler.name
                for n in ast.walk(pairs["message"])
            )
            if uses_bound_exc:
                found.append(ast.unparse(pairs["message"]))

    assert found, "distill.py 里找不到「except ... as exc 且把 exc 写进 status='error' 的 message」—— 定位锚点没了"
    assert all(e == "user_facing_error(exc)" for e in found), (
        f"后台任务的失败 message 必须走统一文案出口，实际：{found}")
