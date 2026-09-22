"""S5：`PATCH /api/admin/users/{id}/role` 的四条语义 + 存储层不吞失败。

身份**每个请求从库里现读**（`resolve_identity` → `get_user_by_id`），所以这里的
`get_current_user` 覆盖不注入一份写死的字典，而是回读 store —— 「下一个请求立即按新
角色判定」这条才是真的被测到，而不是测了覆盖函数自己。

拒绝路径断言的是**状态码所属的那道门**：403 只有 `require_admin` 会发（消息「需要管理
员权限」），不像 demo_gate 那样会被端点自身的 403 混淆。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import roles
from deps import get_storage
from routers import admin as A
from routers.auth import get_current_user
from storage.sqlite_store import SQLiteStore

_DENIED = "需要管理员权限"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"role_{uuid.uuid4().hex}.db"))


@pytest.fixture
def ids(store):
    """三个账号：admin / user(靶子) / other(另一个靶子)。"""
    made = {}
    for name, role in (("admin", roles.ADMIN), ("target", roles.USER), ("other", roles.USER)):
        uid = f"usr_{uuid.uuid4().hex[:12]}"
        _run(store.create_user(uid, f"{name}_{uuid.uuid4().hex[:6]}", "x"))
        _run(store.set_user_role(uid, role))
        made[name] = uid
    return made


def _client(store, uid: str) -> TestClient:
    """一个「登录成 uid」的客户端；身份每请求从库里现读。"""
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[get_storage] = lambda: store

    async def _live_user():
        return await store.get_user_by_id(uid)

    app.dependency_overrides[get_current_user] = _live_user
    return TestClient(app)


def _role_of(store, uid: str) -> str:
    return (_run(store.get_user_by_id(uid)) or {}).get("role")


class TestAdminChangesAnotherUser:
    def test_patch_persists_and_the_next_request_is_judged_by_the_new_role(self, store, ids):
        admin, target = _client(store, ids["admin"]), _client(store, ids["target"])

        # 靶子当前是 user：这一格先证「判定是活的」，否则下面的放行说明不了任何事
        assert target.get("/api/admin/users").status_code == 403

        r = admin.patch(f"/api/admin/users/{ids['target']}/role", json={"role": roles.GUEST})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "role": roles.GUEST}
        assert _role_of(store, ids["target"]) == roles.GUEST, "接口回了成功但库里没变"

        # 提成 admin → 同一个客户端、下一条请求立即按新角色放行
        assert admin.patch(
            f"/api/admin/users/{ids['target']}/role", json={"role": roles.ADMIN},
        ).status_code == 200
        assert target.get("/api/admin/users").status_code == 200, "新角色没在下一条请求生效"

        # 降回 guest → 立刻又拦上（不是单向的「一旦放行就放行」）
        assert admin.patch(
            f"/api/admin/users/{ids['target']}/role", json={"role": roles.GUEST},
        ).status_code == 200
        assert target.get("/api/admin/users").status_code == 403


class TestSelfChangeIsRefused:
    def test_admin_patching_itself_is_400_and_does_not_touch_the_row(self, store, ids):
        admin = _client(store, ids["admin"])
        for wanted in (roles.USER, roles.GUEST, roles.ADMIN):
            r = admin.patch(f"/api/admin/users/{ids['admin']}/role", json={"role": wanted})
            assert r.status_code == 400, r.text
        assert _role_of(store, ids["admin"]) == roles.ADMIN, "被拒的请求仍改了库里的值"


class TestNonAdminCannotChangeRoles:
    @pytest.mark.parametrize("actor", [roles.USER, roles.GUEST])
    def test_user_and_guest_are_403(self, store, ids, actor):
        _run(store.set_user_role(ids["target"], actor))
        client = _client(store, ids["target"])
        r = client.patch(f"/api/admin/users/{ids['other']}/role", json={"role": roles.GUEST})
        assert r.status_code == 403, r.text
        assert r.json().get("detail") == _DENIED, "403 不是 require_admin 发的"
        assert _role_of(store, ids["other"]) == roles.USER, "非管理员的请求改了库里的值"


class TestRequestValidation:
    @pytest.mark.parametrize("bad", ["superuser", "", "ADMIN", "admin "])
    def test_unknown_role_is_422_not_silently_coerced(self, store, ids, bad):
        client = _client(store, ids["admin"])
        r = client.patch(f"/api/admin/users/{ids['target']}/role", json={"role": bad})
        assert r.status_code == 422, r.text
        assert _role_of(store, ids["target"]) == roles.USER

    def test_missing_target_user_is_404(self, store, ids):
        client = _client(store, ids["admin"])
        r = client.patch("/api/admin/users/usr_nobody/role", json={"role": roles.USER})
        assert r.status_code == 404, r.text


class TestStoreDoesNotSwallowFailure:
    """`set_user_role` 对不存在的 id 必须抛，不许回「成功却没写」（68 号的覆辙）。"""

    def test_unknown_user_id_raises(self, store):
        with pytest.raises(ValueError):
            _run(store.set_user_role("usr_nope", roles.USER))

    def test_unknown_role_raises(self, store, ids):
        with pytest.raises(ValueError):
            _run(store.set_user_role(ids["target"], "superuser"))
