"""管理员对用户账号的状态操作：禁用 / 启用 / 封禁。

本地路由（`routers/admin.py`）与节点间接收端（`routers/inter_node.py`）都只调这里，
前置条件因此只有一份：

  1. 目标用户不存在 → 404（存储层写 0 行即抛 ValueError，不「成功却没写」）；
  2. 禁用 / 封禁自己 → 400。跨节点同样适用：两台管理员账号整号复制、
     id 相同，从一台禁用对端同 id 的账号 = 把自己锁在对端外面；
  3. 操作者由调用方从**已认证**的来源传入（本地 = 登录身份；节点间 = 签过名的请求体），
     这里不从任何别的地方取。

启用自己不拦：被禁用的管理员登录不了，本地走不到这里；跨节点启用自己是解锁，无害。

拒绝直接抛 `HTTPException`：两类调用方都是路由，回给客户端的就是这个形态，不另造
一个异常类再在每个调用点各翻译一遍。
"""

from __future__ import annotations

from fastapi import HTTPException

from storage.base import StorageBase


def _refuse_self(target_id: str, operator_id: str, what: str) -> None:
    if target_id == operator_id:
        raise HTTPException(400, f"不能{what}自己的账号")


async def set_user_disabled(
    storage: StorageBase, *, target_id: str, operator_id: str, disabled: bool,
) -> None:
    if disabled:
        _refuse_self(target_id, operator_id, "禁用")
    try:
        await storage.set_user_disabled(target_id, disabled)
    except ValueError:
        raise HTTPException(404, "用户不存在") from None


async def ban_user(storage: StorageBase, *, target_id: str, operator_id: str) -> dict:
    _refuse_self(target_id, operator_id, "封禁")
    try:
        return await storage.ban_user_and_contents(target_id, operator_id)
    except ValueError:
        raise HTTPException(404, "用户不存在") from None
