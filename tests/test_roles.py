# -*- coding: utf-8 -*-
"""core/roles.py：角色语义的唯一定义处。

两条判据：① 三种合法角色各自的读数；② **非法值必须炸**——静默降级成 user 等于把权限
判定变成猜，而猜错的方向恰好是放行。空 user / 缺 `role` 字段走默认角色，不算非法值。
"""
from __future__ import annotations

import pytest

from core import roles


def test_three_roles_read_back_exactly():
    admin = {"username": "a", "role": "admin"}
    user = {"username": "u", "role": "user"}
    guest = {"username": "g", "role": "guest"}

    assert roles.role_of(admin) == roles.ADMIN
    assert roles.role_of(user) == roles.USER
    assert roles.role_of(guest) == roles.GUEST

    assert (roles.is_admin(admin), roles.is_guest(admin)) == (True, False)
    assert (roles.is_admin(user), roles.is_guest(user)) == (False, False)
    assert (roles.is_admin(guest), roles.is_guest(guest)) == (False, True)


@pytest.mark.parametrize("empty", [None, {}])
def test_empty_user_is_neither_admin_nor_guest(empty):
    """空 user 恒非 admin、非 guest —— 门禁不是鉴权，没登录的人不由它管。"""
    assert roles.role_of(empty) == roles.DEFAULT_ROLE
    assert roles.is_admin(empty) is False
    assert roles.is_guest(empty) is False


def test_missing_role_field_falls_back_to_default():
    """缺 `role` 字段（老库、手工构造的字典）不算非法值。"""
    assert roles.role_of({"username": "legacy"}) == roles.DEFAULT_ROLE
    assert roles.is_admin({"username": "legacy"}) is False


def test_illegal_role_raises_instead_of_silently_being_a_user():
    """库里的非法值必须炸 —— 静默当 user 就是把「猜」当成判定。"""
    for bogus in ("superadmin", "Admin", "ADMIN", "", "root", 1, True):
        with pytest.raises(ValueError):
            roles.role_of({"username": "x", "role": bogus})
    with pytest.raises(ValueError):
        roles.is_admin({"username": "x", "role": "superadmin"})
    with pytest.raises(ValueError):
        roles.is_guest({"username": "x", "role": "superadmin"})


def test_roles_constant_is_the_single_source_of_legality():
    """合法值集合只有一个源 —— 库侧 CHECK、Pydantic 校验都从它取。"""
    assert roles.ROLES == {roles.ADMIN, roles.USER, roles.GUEST}
    assert roles.DEFAULT_ROLE in roles.ROLES
