# -*- coding: utf-8 -*-
"""项目默认模型名**只在一处定义** —— 配置文件里的 `llm.model`。

改默认模型曾经要同时改 `adapters/llm_adapter.py`、`web/deps.py`、`web/routers/auth.py`
和两个存储层；漏一处就会「前端显示一个模型、实际请求另一个」。2026-09-27 改
`deepseek-v4-pro` → `deepseek-flash` 那次就是靠人肉对照才没漏。

本文件钉两件事：
1. `default_model()` 就是那份配置值，回落字面量不得成为第二份定义；
2. **存储层不再知道默认模型** —— 库里没设就原样返回空，由解析层回落。

**为什么用真 PG**：存储层那一半的判据就在 `storage/postgres_store.py`，SQLite 上跑绿
证明不了它（改回写死回落值，SQLite 用例照样全绿）。与 AGENTS.md「存储改动只保证 PG」
一致。asyncpg 的连接池绑定创建它的 loop，所以 app 用 `AsyncClient` 而不是 `TestClient`
（`TestClient` 的 portal loop 与测试 loop 不是同一个，见 `test_comment_likes.py::_app`）。

Run: pytest tests/test_default_model.py -v
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash

import deps
import server
from adapters.llm_adapter import default_model
from conftest import PG_ENV, TEST_DATABASE_URL
from core import roles
from deps import get_storage, get_user_llm
from routers.auth import (
    _create_access_token,
    get_current_user,
    get_jwt_secret,
    router as auth_router,
)
from storage.postgres_store import PostgresStore
from storage.sqlite_store import SQLiteStore

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CFG = ROOT / "config.example.yaml"

_pg = PG_ENV.skipif("默认模型用例")


def _example_model() -> str:
    """`config.example.yaml` 里的模型名 —— **独立真源**，不是从被测代码读的。"""
    return yaml.safe_load(EXAMPLE_CFG.read_text(encoding="utf-8"))["llm"]["model"]


@pytest.fixture
async def store():
    st = PostgresStore(TEST_DATABASE_URL)
    await st._ensure_initialized()
    yield st
    await st.close()


async def _seed_user(store) -> str:
    uid = f"usr_{uuid.uuid4().hex[:12]}"
    await store.create_user(uid, f"u_{uuid.uuid4().hex[:6]}", "x")
    return uid


def _run(coro):
    """给「同步 fixture + TestClient」那条路用：SQLite 没有 loop 亲和，开一个即可。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_default_model_reads_the_configured_file(tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("llm:\n  model: probe-model\n", encoding="utf-8")
    assert default_model(cfg) == "probe-model"


def test_terminal_fallback_does_not_become_a_second_definition():
    """配置读不到时的字面回落，必须与 `config.example.yaml` 一致。

    它只在配置文件缺失/损坏时才走到，但**它就是第二份定义**：改默认模型时若忘了它，
    下一次「文件在、字面量陈旧」的情形又会冒出 v4-pro 那个老毛病。
    """
    assert default_model(Path("/nonexistent/nope.yaml")) == _example_model()


@_pg
async def test_storage_returns_the_stored_model_untouched(store):
    """库里没设模型 → 原样空。存储层只管存取，默认值归解析层。"""
    uid = await _seed_user(store)
    cfg = await store.get_user_api_config(uid)
    assert not cfg["model"], f"存储层自己编了一个默认模型：{cfg['model']!r}"


@_pg
async def test_user_llm_falls_back_to_default_model_when_user_has_none(store):
    uid = await _seed_user(store)
    await store.update_user_api_config(uid, "sk-x", "https://api.deepseek.com", "")

    llm = await get_user_llm(uid, store)

    assert llm is not None, "有用户 key 却没构造出实例"
    assert llm.model == default_model()


@_pg
async def test_auth_me_reports_default_model_when_user_has_none(store):
    uid = await _seed_user(store)
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "t", "role": "user",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = (await ac.get("/api/auth/me")).json()

    assert resp["model"] == default_model()


@_pg
async def test_stored_model_still_wins_over_the_default(store):
    """正控：上面几条改成「一律返回 default_model()」也能过 —— 这条把口子收回来。"""
    uid = await _seed_user(store)
    await store.update_user_api_config(uid, "sk-x", "https://api.deepseek.com", "mine")

    assert (await store.get_user_api_config(uid))["model"] == "mine"
    assert (await get_user_llm(uid, store)).model == "mine"


# ── 设置接口：模型名回落到 default_model() ─────────────────────────────
#
# `default_model()` 换成一个探针，是为了把「读的是这个函数」和「读到的值究竟是什么」
# 分开：真实配置文件在磁盘上，探针不受它内容影响，用例也就不会因为本机 config.yaml
# 长什么样而红绿不定。

_PROBE = "probe-model"
_PW_HASH = PasswordHash.recommended().hash("Pass1234")


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    """生产 app（`server.app`）—— 路由、鉴权中间件与响应装配都是生产那一份。

    照着 `tests/test_settings_config_llm_available.py::client` 来：身份解析走真实 JWT，
    故这里自己签一个；存储换成一次性 SQLite（本用例观测的是响应体，不碰存储层的 PG
    那一半，故不需要 PG）。三处出站副作用就地切断：写真 config.yaml、重建真全局 LLM、
    真去构造全局 LLM。
    """
    store = SQLiteStore(str(tmp_path / "settings.db"))
    uid = f"usr_{uuid.uuid4().hex[:16]}"
    _run(store.create_user(uid, "Admin_" + uuid.uuid4().hex[:6], _PW_HASH))
    _run(store.set_user_role(uid, roles.ADMIN))
    admin = _run(store.get_user_by_id(uid))

    monkeypatch.setattr(deps, "_storage", store)
    monkeypatch.setattr(server, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(server, "get_config", lambda: {"llm": {}, "voice": {}})
    monkeypatch.setattr(server, "reset_llm_and_dependents", lambda: None)
    monkeypatch.setattr(server, "get_llm", lambda: object())
    monkeypatch.setattr(server, "default_model", lambda: _PROBE)

    tok = _create_access_token(admin["id"], admin["username"], get_jwt_secret())
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {tok}"
    return client


def test_both_settings_endpoints_report_the_default_model(settings_client):
    read = settings_client.get("/api/settings/config").json()
    saved = settings_client.post("/api/settings/config", json={}).json()

    assert read["model"] == _PROBE
    assert saved["model"] == _PROBE
    # 读、存共用同一份字典：字段集合一致，且除 `api_key` 外逐一同形。两边各拼一份就会
    # 从这里红。`api_key` 有意不同 —— 读取时还看环境变量 `DEEPSEEK_API_KEY`，保存只看
    # 配置里有没有。
    assert set(saved) - {"llm_available"} == set(read)
    assert ({k: v for k, v in saved.items() if k not in ("llm_available", "api_key")}
            == {k: v for k, v in read.items() if k != "api_key"})
