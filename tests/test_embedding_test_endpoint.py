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

import asyncio
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


# ═══════════════════════════════════════════════════════════════════════════════
# T11–T12（台账 120）：保存端点上的 embedding_region 也要过同一张表
#
# 上面 E7 是**试连**端点上的地域校验；保存端点（PATCH /api-auth/api-config）此前没有这道
# 校验 —— 非法地域照样落库，真要出网的是别处的嵌入调用，那时才在构造函数里抛 KeyError。
# 口径与试连端点一致：空串放行（仓储层「空字段不写」，见 update_user_api_config）、
# 表里的键放行、其余 422 且一个字段都不落库。
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fernet_key(monkeypatch):
    """给 SQLiteStore 的加解密一个当场生成的 key。

    不读环境、不写文件：有 `.env` 与没 `.env` 的机器上结果必须一样（密钥派生在
    两者都没有时是拒绝而不是回落的，见 storage/secret_box.py）。
    """
    from cryptography.fernet import Fernet

    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())


def _save(client, region, ip=None):
    headers = {"X-Real-IP": ip} if ip else {}
    return client.patch("/api/auth/api-config", headers=headers,
                        json={"embedding_key": TEST_KEY, "embedding_region": region})


def _seed_user(store):
    """没有 users 行时 `get_user_api_config` 查不出任何配置，「没写进去」就成了空断言。"""
    asyncio.run(store.create_user(USER_ID, USER_ID, "x"))


@pytest.mark.parametrize("region", ["", "cn"])
def test_T11_save_accepts_blank_or_known_region(client, store, fernet_key, region):
    """空串（不改动现有值）与表里的键都要放行 —— 校验不是「必填」。

    `intl` 不在这张表里：它决定嵌入出站去境外，存不存得下现在是 **geo 判定**（见 T13），
    不再是「地域键合法就一律收下」。
    """
    _seed_user(store)
    r = _save(client, region)
    assert r.status_code == 200, f"region={region!r} 被拒了：{r.status_code} {r.text[:200]}"


def test_T11b_save_rejects_unknown_region_without_writing(client, store, fernet_key):
    """非法地域 422，且一个字段都不落库（改前是照单全收）。"""
    _seed_user(store)
    r = _save(client, "us")
    assert r.status_code == 422, f"非法地域被收下了：{r.status_code} {r.text[:200]}"

    stored = asyncio.run(store.get_user_api_config(USER_ID))
    assert stored["embedding_region"] == "cn", "非法地域落库了"


def test_T12_save_validates_against_the_same_table(client, store, fernet_key, monkeypatch):
    """往表里加一个地域，保存端点就该跟着放行 —— 校验查的是同一张表。

    另写一份 `{"cn", "intl"}` 字面量也能过 T11/T11b（两个合法值恰好就是这两个），
    这条才分得出「同一张表」与「抄了一份恰好相同的字面量」。

    新地域指向的是**白名单主机**：这条问的是「地域键查哪张表」，不是 geo 放不放 ——
    指到非白名单主机上，判定会先被 geo 门拦下（T13 那条的地盘），这条就答非所问了。
    """
    import core.embeddings as E

    monkeypatch.setitem(E.DASHSCOPE_BASE_URLS, "us", "https://api.deepseek.com/v1")
    _seed_user(store)
    r = _save(client, "us")

    assert r.status_code == 200, f"表里加了 us，保存端点仍报非法：{r.status_code} {r.text[:200]}"
    stored = asyncio.run(store.get_user_api_config(USER_ID))
    assert stored["embedding_region"] == "us"


def test_T13_saving_intl_is_judged_by_the_same_geo_rule(client, store, fernet_key, monkeypatch):
    """`embedding_region` 决定嵌入出站去哪，保存它要过与出站**同一道** geo 判定（缺陷 73）。

    只堵出站不堵保存，用户会在下一次试连/蒸馏里撞上拒绝，而保存那一刻一声不响 ——
    他只会以为自己填错了 key。所以这里必须当场 403 + 理由 + 记一条审计。

    判定注入成**两维**（境内 + 非白名单才拦），不看 ip2region 库在不在：库缺席时
    `is_domestic_ip` 一律回 True，「境外 IP 放行」那半条会随环境变红。
    """
    import core.embeddings as E
    import routers.auth as auth_mod
    import web.geo_guard as G
    import web.llm_gate as gate

    domestic, foreign, reason = "1.1.1.1", "8.8.8.8", "境内不支持境外模型"

    def verdict(ip, url):
        # 空 url 与真判定同口径（`check_api_allowed` 首行）：这次不改 base_url，无从判。
        if not url or G.is_whitelisted_base_url(url) or ip == foreign:
            return True, ""
        return False, reason

    monkeypatch.setattr(G, "check_api_allowed", verdict)
    monkeypatch.setattr(gate, "check_api_allowed", verdict)
    audited: list[tuple] = []
    # 打路由模块里的那个绑定：`routers.auth` 是 `from web.llm_gate import ...` 取的名，
    # 改 `web.llm_gate` 的模块属性拦不到它。
    monkeypatch.setattr(
        auth_mod, "emit_geo_block_audit",
        lambda uid, ip, url, r: audited.append((uid, ip, url, r)),
    )
    _seed_user(store)

    blocked = _save(client, "intl", ip=domestic)
    assert blocked.status_code == 403, f"国内 IP 存 intl 被放行了：{blocked.text[:200]}"
    assert blocked.json()["detail"] == reason
    assert [a[2] for a in audited] == [E.DASHSCOPE_BASE_URLS["intl"]], (
        f"审计的不是嵌入端点那一次拒绝：{audited}")
    assert (audited[0][0], audited[0][1]) == (USER_ID, domestic)

    stored = asyncio.run(store.get_user_api_config(USER_ID))
    assert stored["embedding_region"] == "cn", "被拦的那次仍然落库了"

    allowed = _save(client, "intl", ip=foreign)
    assert allowed.status_code == 200, f"境外 IP 存 intl 也被拦：{allowed.text[:200]}"
    assert len(audited) == 1, f"放行的那次不该记审计：{audited}"
