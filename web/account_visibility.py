"""账号对查看者可见到什么 —— 唯一的规则表。

账号来自账号目录（`StorageBase.get_public_account`，只 PG 实现），可能是本地
用户，也可能是对端地区用户。查看者看到的是四种视图之一：

======== ===================================================================
self     本人：一切照旧
local    本地其他用户：一切照旧（再受对方的隐私设置约束，那是路由里的另一层）
remote   对端地区用户：本库只有其公开资料与公开角色（隐私政策 3.2(1)(2)），
         粉丝、书架、动态、在线状态都在对端库，不出境 —— 所以没有
disabled 已禁用账号（本地或对端同一规则）：只给身份标识，内容一律不给
======== ===================================================================

每种视图能做什么由 `Capabilities` 说明，**原样下发给前端**：前端按能力渲染，不自己从
「是不是对端」「是不是禁用」再推一遍规则。新增账号类型 / 调整规则只改本文件。

「能不能被搜到」不在这里：搜索要在库里过滤才能让 LIMIT 准，判据在
`StorageBase.search_discoverable_accounts`（禁用账号不可被搜到）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class AccountView(str, Enum):
    SELF = "self"
    LOCAL = "local"
    REMOTE = "remote"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Capabilities:
    """主页上每一块 / 每个动作是否存在。字段名即下发给前端的键名。"""

    message: bool    # 发私信
    follow: bool     # 关注 / 取关
    fork: bool       # 角色卡「使用」
    cards: bool      # 公开角色区块
    stats: bool      # 粉丝 / 关注 / 角色 / 书籍统计
    bookshelf: bool  # 书架
    posts: bool      # 动态
    presence: bool   # 在线状态


_ALL = Capabilities(message=True, follow=True, fork=True, cards=True,
                    stats=True, bookshelf=True, posts=True, presence=True)
_NONE = Capabilities(message=False, follow=False, fork=False, cards=False,
                     stats=False, bookshelf=False, posts=False, presence=False)

CAPABILITIES: dict[AccountView, Capabilities] = {
    AccountView.SELF: Capabilities(**{**asdict(_ALL), "message": False, "follow": False}),
    AccountView.LOCAL: _ALL,
    # 对端卡不在本库 cards 表里，fork 不可能成功；关注表只认本地用户。
    AccountView.REMOTE: Capabilities(**{**asdict(_NONE), "message": True, "cards": True}),
    AccountView.DISABLED: _NONE,
}

#: 非本地视图能给出的身份字段 —— 恰是资料同步带过来的 3.2(1) 公开字段，一个不多。
IDENTITY_FIELDS: dict[AccountView, tuple[str, ...]] = {
    AccountView.REMOTE: ("id", "username", "avatar_data", "home_region"),
    AccountView.DISABLED: ("id", "username", "home_region"),
}


def view_of(account: dict, viewer_id: str) -> AccountView:
    """查看者 `viewer_id` 看账号 `account`（账号目录的一行）时是哪种视图。

    本人优先于禁用：被禁用的人看自己的主页仍是完整视图。
    """
    if account["id"] == viewer_id:
        return AccountView.SELF
    if account["is_disabled"]:
        return AccountView.DISABLED
    return AccountView.REMOTE if account["is_remote"] else AccountView.LOCAL


def identity(account: dict, view: AccountView) -> dict:
    """按白名单裁出该视图可以下发的身份字段（只对 remote / disabled 有定义）。"""
    return {k: account.get(k) or "" for k in IDENTITY_FIELDS[view]}


def page_meta(view: AccountView) -> dict:
    """主页响应里描述「这是哪种视图、能做什么」的两个键。"""
    return {"view": view.value, "capabilities": asdict(CAPABILITIES[view])}
