# -*- coding: utf-8 -*-
"""嵌入连通性测试端点：中文提示 + 上游原话 + 日志（72 线 · 94 泄漏收尾 C 方案）。

口径（用户已定）：这个按钮的用途就是帮用户查自己的 key 哪里不通，所以**有意**保留上游
原话；但先给一句能指导下一步的中文提示，且失败必须写日志。与 chat / 群聊 / 上传任务那三处
「一律通用文案」不同 —— 那三处是用户的日常路径，原文只是噪声与泄漏面；这里是排障工具，
原文就是用户要的一半答案。

`describe_embedding_failure` 只认 `status_code` / `code` / 异常类型，**不按报错文本匹配**：
文本是上游可自由改写的，拿它当判据会在上游换文案时静默失效（判据须可判定，见台账口径）。
这里的异常用 `httpx.Response` + `OpenAI._make_status_error_from_response` 造**真** SDK
对象，让 `status_code` / `code` 的取值就是生产里那份，而不是我们手搓的同名字段。

端到端打 HTTP 面、不直接调 `describe_embedding_failure`：地域校验、构造点、返回体、日志
是同一个改动的四个面，只测纯函数会漏掉「接线没接上」。
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_storage
from storage.sqlite_store import SQLiteStore

from routers.auth import get_current_user, router as auth_router

TEST_KEY = "sk-test-embedding-key-must-never-be-logged"
USER_ID = "u_embed_probe"

FALLBACK = "连接失败，请检查 API Key 和地域"
REGION_ERROR = "地域只能选 cn 或 intl"


def _status_error(status: int, body: dict) -> Exception:
    """造一个**真**的 openai SDK 异常（类型、status_code、code 都由 SDK 决定）。"""
    from openai import OpenAI

    resp = httpx.Response(
        status,
        request=httpx.Request("POST", "http://upstream.local/v1/embeddings"),
        json=body,
    )
    return OpenAI(api_key="unused")._make_status_error_from_response(resp)


def _error_body(code: str, message: str = "boom") -> dict:
    return {"error": {"code": code, "message": message, "type": "invalid_request_error"}}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _rate_limit_off(monkeypatch):
    """关掉慢速限流器（`5/minute`）—— 本文件要发 10 个请求。

    **不能**照 `test_auth_tokens.py` 在 import 期把 `limiter.limit` 换成 no-op：那只在
    「本文件是第一个 import `routers.auth` 的模块」时成立。全量跑时别的文件先 import 了
    `routers.auth`，`@limiter.limit("5/minute")` 早已被真限流器装饰完，之后再改
    `limiter.limit` 不回头 —— 第 6 个请求起 429，本文件红 5 条（现跑复现：
    `pytest -p no:randomly tests/test_ownership_404.py tests/test_embedding_test_endpoint.py`）。
    改关 `enabled`：slowapi 在**每次请求**的校验里读它，与 import 顺序无关。
    """
    import limiter as _lim_

    monkeypatch.setattr(_lim_.limiter, "enabled", False)


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": USER_ID, "username": "testuser", "role": "user",
    }
    return TestClient(app)


class _Recorder:
    """假 embedder 的观测点：记下每次构造的参数，并决定 `__call__` 抛什么。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.exc: Exception | None = None


@pytest.fixture
def embedder(monkeypatch):
    """把 `core.embeddings.DashScopeEmbedding` 换成一个不碰网络的替身。

    路由里是**函数内** import，所以打模块属性即可命中当次调用。
    """
    import core.embeddings as E

    rec = _Recorder()

    class _Stub:
        def __init__(self, api_key, region="cn", **kw):
            rec.calls.append({"api_key": api_key, "region": region})

        def __call__(self, texts):
            if rec.exc is not None:
                raise rec.exc
            return [[0.01, 0.02, 0.03] for _ in texts]

    monkeypatch.setattr(E, "DashScopeEmbedding", _Stub)
    return rec


def _post(client, region="cn", key=TEST_KEY):
    return client.post("/api/auth/test-embedding",
                       json={"embedding_key": key, "embedding_region": region})


# ═══════════════════════════════════════════════════════════════════════════════
# E1–E5：按 status_code / code / 异常类型给中文提示
# ═══════════════════════════════════════════════════════════════════════════════

def test_E1_401_key_or_region_mismatch(client, embedder):
    embedder.exc = _status_error(401, _error_body("invalid_api_key"))
    r = _post(client)
    assert r.json()["ok"] is False
    assert r.json()["error"] == (
        "API Key 无效，或与所选地域不匹配（中国站的 Key 选 cn，国际站的 Key 选 intl）")


def test_E2_400_arrearage_is_debt(client, embedder):
    embedder.exc = _status_error(400, _error_body("Arrearage", "Account arrears"))
    assert _post(client).json()["error"] == "阿里云账户欠费，请充值后重试"


def test_E2b_400_other_code_falls_back(client, embedder):
    """欠费只认 `code == "Arrearage"`：另加码的 400 不算欠费，走兜底。"""
    embedder.exc = _status_error(400, _error_body("InvalidParameter", "bad param"))
    assert _post(client).json()["error"] == FALLBACK


def test_E3_429_rate_limited(client, embedder):
    embedder.exc = _status_error(429, _error_body("Throttling"))
    assert _post(client).json()["error"] == "请求过于频繁，请稍后再试"


def test_E4_connection_error(client, embedder):
    from openai import APIConnectionError

    embedder.exc = APIConnectionError(
        request=httpx.Request("POST", "http://upstream.local/v1/embeddings"))
    assert _post(client).json()["error"] == "连不上 DashScope 服务器，请检查网络后重试"


# ═══════════════════════════════════════════════════════════════════════════════
# E5–E6：未登记错误带原话；失败写日志且不含 key
# ═══════════════════════════════════════════════════════════════════════════════

def test_E5_unregistered_error_keeps_raw_text_after_hint(client, embedder):
    raw = "RuntimeError: downstream exploded at core/embeddings.py:214"
    embedder.exc = RuntimeError(raw)
    body = _post(client).json()
    assert body["ok"] is False
    assert body["error"] == FALLBACK
    assert body["detail"] == raw, "排障按钮的原话是结果的一半，不能被收走"


def test_E6_failure_is_logged_without_the_key(client, embedder, capsys):
    embedder.exc = _status_error(401, _error_body("invalid_api_key"))
    _post(client)
    out = capsys.readouterr().out
    assert USER_ID in out, "失败没有写日志，owner 不翻容器日志就无从得知"
    assert "invalid_api_key" in out, "日志里要有异常本身，否则排障无据"
    assert TEST_KEY not in out, "凭据进了日志"


# ═══════════════════════════════════════════════════════════════════════════════
# E7：非法地域在构造客户端之前拦下
# ═══════════════════════════════════════════════════════════════════════════════

def test_E7_unknown_region_never_constructs(client, embedder):
    r = _post(client, region="us")
    assert r.json() == {"ok": False, "error": REGION_ERROR}
    assert embedder.calls == [], "非法地域仍构造了客户端（改前是构造里抛 KeyError）"
    assert TEST_KEY not in r.text


# ═══════════════════════════════════════════════════════════════════════════════
# E8：成功路径不变
# ═══════════════════════════════════════════════════════════════════════════════

def test_E8_success_path_unchanged(client, embedder):
    r = _post(client)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert embedder.calls == [{"api_key": TEST_KEY, "region": "cn"}]


# ═══════════════════════════════════════════════════════════════════════════════
# E9a/E9b：地域表只有一份
# ═══════════════════════════════════════════════════════════════════════════════

def test_E9a_constructor_reads_the_module_table(monkeypatch):
    """构造函数查模块常量 —— 改表即改行为，证明地域表没被内联成第二份。"""
    import core.embeddings as E

    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.setitem(E.DASHSCOPE_BASE_URLS, "cn", "http://patched.invalid/v1")

    emb = E.DashScopeEmbedding(api_key="unused", region="cn")
    assert str(emb._client.base_url).rstrip("/") == "http://patched.invalid/v1"


def test_E9b_region_validation_reads_the_same_table(client, embedder, monkeypatch):
    """往表里加一个地域，端点就不该再报「只能选 cn 或 intl」——校验查的是同一张表，不是
    另写一份 `{"cn", "intl"}` 字面量。"""
    import core.embeddings as E

    monkeypatch.setitem(E.DASHSCOPE_BASE_URLS, "us", "http://us.invalid/v1")
    r = _post(client, region="us")
    assert r.json() == {"ok": True}
    assert embedder.calls == [{"api_key": TEST_KEY, "region": "us"}]
