# -*- coding: utf-8 -*-
"""69：管理员保存全局配置后，响应里要说明**全局 LLM 到底可不可用**。

`reset_llm_and_dependents()` 会把构造不出实例的全局 LLM 留成 `None`（`_make_global_llm`
对「没配 key」的答复），此后没有个人 key 的用户一律收到 503 —— 但保存端点的响应体里
原本只有配置字段，调用方看不出这件事发生了。字段取值就是 `get_llm() is not None`，
**保存本身不拦**：置 None 是 C4 已定的口径，管理员可能就是有意清空。

spec §2.3 / §5 原本还要求 `ApiConfigPanel` 渲染这条提示；该面板不调这个端点（它只
`GET /api/settings/config` 和 `PATCH /api/auth/api-config`），全仓没有前端调用方，
故前端那半作废（用户裁定，见交付报告）。本文件只锁后端。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

import deps
import server
from core import roles
from routers.auth import _create_access_token, get_jwt_secret
from storage.sqlite_store import SQLiteStore

_PW_HASH = PasswordHash.recommended().hash("Pass1234")
_SAVE_BODY = {"base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro"}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "settings_cfg.db"))


@pytest.fixture
def admin(store):
    uid = f"usr_{uuid.uuid4().hex[:16]}"
    _run(store.create_user(uid, "Admin_" + uuid.uuid4().hex[:6], _PW_HASH))
    _run(store.set_user_role(uid, roles.ADMIN))
    return _run(store.get_user_by_id(uid))


@pytest.fixture
def client(store, admin, monkeypatch, tmp_path):
    """**生产 app**（`server.app`）：`require_admin` 与这段响应装配都是生产那一份。

    只换 storage（中间件与依赖读的都是 `deps._storage`），并把三个出站副作用就地切断：
    写真 config.yaml、重建真全局 LLM、读真 config —— 本用例要观测的是响应字段，
    不是这台机器上恰好有没有 key。
    """
    monkeypatch.setattr(deps, "_storage", store)
    monkeypatch.setattr(server, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(server, "get_config", lambda: {"llm": {}, "voice": {}})
    monkeypatch.setattr(server, "reset_llm_and_dependents", lambda: None)
    return TestClient(server.app, raise_server_exceptions=False)


def _save(client, admin, monkeypatch, llm):
    monkeypatch.setattr(server, "get_llm", lambda: llm)
    tok = _create_access_token(admin["id"], admin["username"], get_jwt_secret())
    return client.post(
        "/api/settings/config", json=_SAVE_BODY,
        headers={"Authorization": f"Bearer {tok}"},
    )


class TestResponseReportsGlobalLlmAvailability:
    def test_no_global_llm_reports_false_and_still_saves(self, client, admin, monkeypatch):
        """保存不拦（管理员可能就是有意清空），但必须如实回报 —— 否则调用方只能猜。"""
        r = _save(client, admin, monkeypatch, None)
        assert r.status_code == 200, r.text
        assert r.json()["llm_available"] is False

    def test_global_llm_present_reports_true(self, client, admin, monkeypatch):
        """正控：字段恒 False 也能过上面那条，这条把口子收回来。"""
        r = _save(client, admin, monkeypatch, object())
        assert r.status_code == 200, r.text
        assert r.json()["llm_available"] is True

    def test_saved_fields_are_still_returned(self, client, admin, monkeypatch):
        """新字段是**加**上去的，不是把原来的配置字段挤掉。"""
        r = _save(client, admin, monkeypatch, object())
        body = r.json()
        assert body["base_url"] == "https://api.deepseek.com"
        assert body["model"] == "deepseek-v4-pro"
