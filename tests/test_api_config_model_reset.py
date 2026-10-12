# -*- coding: utf-8 -*-
"""设置页「跟随默认」：`reset_model` 清空存量模型，`/api/auth/me` 报告模型来源。

背景：#156 让前端只在用户改过 model 时才回写，但「改回默认」仍无路可走 —— 空串的
`model` 是「这次不改」（仓储层对空字段一律不写），没有任何通道能把已存的清空。于是
2026-10-12 被钉死 `deepseek-v4-pro` 的那批账号在页面上改不回来。本文件钉两件事：

1. `update_user_api_config(..., reset_model=True)` 把存储的 model 清空（NULL），
   `/api/auth/me` 随之回落到 `default_model()` 并报 `model_source == "default"`；
2. `model_source` 如实区分「用户存量」与「默认值」，且 `model` 字段含义不变。

**为什么用真 PG**：存储层那一半的判据就在 `storage/postgres_store.py`，SQLite 上跑绿
证明不了它（同 `tests/test_default_model.py`）。SQLite 侧另有一条轻用例锁「改了没改坏」。

Run: pytest tests/test_api_config_model_reset.py -v
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from adapters.llm_adapter import SELECTABLE_MODELS, default_model
from conftest import PG_ENV, TEST_DATABASE_URL
from deps import get_storage
from routers.auth import get_current_user, router as auth_router
from storage.postgres_store import PostgresStore
from storage.sqlite_store import SQLiteStore

BASE_URL = "https://api.deepseek.com"

_pg = PG_ENV.skipif("模型 reset/来源用例")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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


def _me_app(store, uid: str) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "t", "role": "user",
    }
    return app


async def _get_me(store, uid: str) -> dict:
    async with AsyncClient(transport=ASGITransport(app=_me_app(store, uid)), base_url="http://test") as ac:
        return (await ac.get("/api/auth/me")).json()


@_pg
async def test_reset_model_clears_the_stored_model(store):
    """被钉死的型号能清回空 —— 这是本改动存在的唯一理由。"""
    uid = await _seed_user(store)
    await store.update_user_api_config(uid, "sk-x", BASE_URL, "deepseek-v4-pro")
    assert (await store.get_user_api_config(uid))["model"] == "deepseek-v4-pro"

    await store.update_user_api_config(uid, "", "", "", reset_model=True)

    assert not (await store.get_user_api_config(uid))["model"], "reset_model 没把存量清掉"


@_pg
async def test_reset_model_reports_default_source_and_falls_back(store):
    uid = await _seed_user(store)
    await store.update_user_api_config(uid, "sk-x", BASE_URL, "deepseek-v4-pro")
    await store.update_user_api_config(uid, "", "", "", reset_model=True)

    resp = await _get_me(store, uid)

    assert resp["model_source"] == "default"
    assert resp["model"] == default_model()


@_pg
async def test_stored_model_reports_user_source(store):
    """正控：恒返回 "default" 也能过上面那条，这条把口子收回来。"""
    uid = await _seed_user(store)
    await store.update_user_api_config(uid, "sk-x", BASE_URL, "mine")

    resp = await _get_me(store, uid)

    assert resp["model_source"] == "user"
    assert resp["model"] == "mine", "model 字段含义被改动了"


@_pg
async def test_no_stored_model_reports_default_source(store):
    """没存过的用户（正控的另一半）：来源就该是 default。"""
    uid = await _seed_user(store)

    assert (await _get_me(store, uid))["model_source"] == "default"


@_pg
async def test_reset_on_unknown_user_raises_like_any_other_write(store):
    """reset 也是一次 UPDATE：受影响行数为 0 = 用户不存在 → 抛（调用方回 404）。"""
    with pytest.raises(ValueError, match="用户不存在"):
        await store.update_user_api_config(
            f"usr_nope_{uuid.uuid4().hex[:6]}", "", "", "", reset_model=True)


def test_reset_model_clears_the_stored_model_sqlite(tmp_path):
    """SQLite 侧同形（AGENTS.md「存储改动只保证 PG」，但两端都改就要都有一条兜底）。"""
    store = SQLiteStore(str(tmp_path / f"reset_{uuid.uuid4().hex}.db"))
    uid = f"usr_{uuid.uuid4().hex[:12]}"
    _run(store.create_user(uid, f"u_{uuid.uuid4().hex[:6]}", "x"))
    _run(store.update_user_api_config(uid, "sk-x", BASE_URL, "deepseek-v4-pro"))

    _run(store.update_user_api_config(uid, "", "", "", reset_model=True))

    assert not _run(store.get_user_api_config(uid))["model"]


def test_settings_payload_exposes_the_selectable_models():
    """下拉选项只有后端一份真源 —— 经设置接口出给前端（前端不另写一份）。"""
    from server import _settings_payload

    payload = _settings_payload({}, {}, has_key=False)

    assert payload["available_models"] == list(SELECTABLE_MODELS)
