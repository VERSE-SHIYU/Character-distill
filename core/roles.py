# -*- coding: utf-8 -*-
"""角色语义的**唯一定义处** —— 门禁、授权、管理接口一律调用这里。

身份只看 `users.role` 一列，取值 `admin` / `user` / `guest`。此前「谁是管理员」由
`users.is_admin` 布尔列回答、「谁是演示账号」由 `DEMO_USERNAMES` 环境变量名单回答，
两套判据各自散落；两边不一致时不报错，只是静默放行或静默拒绝。收成一列 + 一处判定后，
答案只有一个。

**读不出角色就抛异常，不静默当 user。** 库里出现 `ROLES` 之外的值（迁移写坏、手工改库、
将来加角色忘了同步），静默降级成 `user` 会把「权限判定」变成「猜」——而猜错的方向恰好
是放行。这是「失败吞成成功」那一类，必须炸出来。字段整体缺失（老库、手工构造的字典）
不算非法值，按 `DEFAULT_ROLE` 处理；空 user 恒非 admin、非 guest（门禁不是鉴权，
没登录的人不由它管）。
"""

from __future__ import annotations

ADMIN = "admin"
USER = "user"
GUEST = "guest"

#: 全部合法角色。库侧 CHECK 约束与 Pydantic 校验都从这里取，不另写一份字面值。
ROLES = frozenset({ADMIN, USER, GUEST})

#: 新用户、缺 `role` 字段时的角色。
DEFAULT_ROLE = USER

__all__ = ["ADMIN", "USER", "GUEST", "ROLES", "DEFAULT_ROLE", "role_of", "is_admin", "is_guest"]


def role_of(user: dict | None) -> str:
    """这个身份的角色；空 user 或缺 `role` 字段 → `DEFAULT_ROLE`。

    库里的值不在 `ROLES` 里 → 抛 `ValueError`（理由见模块 docstring）。
    """
    role = (user or {}).get("role")
    if role is None:
        return DEFAULT_ROLE
    if role not in ROLES:
        raise ValueError(f"未知角色 {role!r}（用户 {((user or {}).get('username'))!r}）")
    return role


def is_admin(user: dict | None) -> bool:
    """这个身份是不是管理员。空 user 恒 False。"""
    return role_of(user) == ADMIN


def is_guest(user: dict | None) -> bool:
    """这个身份是不是演示（只读）账号。空 user 恒 False。"""
    return role_of(user) == GUEST
