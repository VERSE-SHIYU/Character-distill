# -*- coding: utf-8 -*-
"""68：保存 API 配置到**不存在的用户**必须报错，不许回 `{"ok": true}` 却一行没写。

`update_user_api_config` 原本是纯 `UPDATE ... WHERE user_id = ?`：用户行不存在时语句
影响 0 行、又不报错，端点照样回 200 —— 用户以为 key 存下了，下一轮对话却还走旧配置。
契约与 `set_user_role`（同一条 68 的既有先例）一致：**语句实际执行了、受影响行数为 0
→ ValueError**；`auth.py` 在通用 except 之前单独捕它 → 404「用户不存在」。

同时锁住语义的另一半：**没有一条 UPDATE 执行**（全部字段为空 = 「这次什么都不改」）
不是失败，不报错 —— 否则「空白字段不写」这条既有语义会被这次修复顺手改掉。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_storage
from routers.auth import get_current_user, router as auth_router
from storage.sqlite_store import SQLiteStore

BASE_URL = "https://api.deepseek.com"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"cfg_{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex[:12]}"


def _seed(store, uid: str) -> str:
    _run(store.create_user(uid, f"u_{uuid.uuid4().hex[:6]}", "x"))
    return uid


def _client(store, uid: str) -> TestClient:
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "t", "role": "user",
    }
    return TestClient(app)


def _save(client, **body):
    payload = {"api_key": "sk-x", "base_url": BASE_URL, "model": "m"}
    payload.update(body)
    return client.patch("/api/auth/api-config", json=payload)


class TestStoreRejectsUnknownUser:
    def test_unknown_user_raises(self, store):
        """受影响行数为 0 = 什么都没写，那就必须抛，不能静默成功。"""
        with pytest.raises(ValueError, match="用户不存在"):
            _run(store.update_user_api_config(
                f"usr_nope_{uuid.uuid4().hex[:6]}", "sk-x", BASE_URL, "m"))

    def test_known_user_still_saves(self, store, user_id):
        """正控：修成「一律抛」也能过上面那条，这条把口子收回来。"""
        _seed(store, user_id)
        _run(store.update_user_api_config(user_id, "sk-x", BASE_URL, "m"))

        cfg = _run(store.get_user_api_config(user_id))
        assert cfg["api_key"] == "sk-x", "接口没报错，但 key 没落库"
        assert cfg["base_url"] == BASE_URL
        assert cfg["model"] == "m"

    def test_blank_fields_write_nothing_and_do_not_raise(self, store, user_id):
        """全空 = 这次不改任何字段（既有语义）—— 一条 UPDATE 都没执行，不是失败。

        `embedding_region` 要显式给空串：仓储层自己的默认值是 "cn"（会写 users 行），
        「空请求体」在路由层才成立 —— `ApiConfigRequest` 五个字段默认全是空串。
        """
        _seed(store, user_id)
        _run(store.update_user_api_config(user_id, "sk-keep", BASE_URL, "m"))

        _run(store.update_user_api_config(user_id, "", "", "", "", ""))  # 不抛

        cfg = _run(store.get_user_api_config(user_id))
        assert cfg["api_key"] == "sk-keep", "空字段把已有值覆盖掉了"

    def test_blank_fields_on_unknown_user_also_do_not_raise(self, store):
        """同一个口径的另一面：什么都没执行，就无从谈「受影响行数为 0」。"""
        _run(store.update_user_api_config(
            f"usr_nope_{uuid.uuid4().hex[:6]}", "", "", "", "", ""))


class TestEndpointReports404:
    def test_unknown_user_is_404_not_ok(self, store):
        client = _client(store, f"usr_nope_{uuid.uuid4().hex[:6]}")
        r = _save(client)
        assert r.status_code == 404, f"回了 {r.status_code}：{r.text}"
        assert r.json().get("detail") == "用户不存在"

    def test_known_user_saves_200(self, store, user_id):
        _seed(store, user_id)
        r = _save(_client(store, user_id))
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        assert _run(store.get_user_api_config(user_id))["api_key"] == "sk-x"
