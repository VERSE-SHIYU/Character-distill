# -*- coding: utf-8 -*-
"""边界锁：端点注入了 get_current_user 却从不引用 user，是一条会重复长出来的缺陷。

text.py:204 / voice.py:189 / voice.py:213 三处都是这个形态，靠人扫 AST 才发现的。
这条测试把「扫」固化下来：将来新增同类端点会自动变红，不用等人再扫一遍。

判据来源（缺陷 42 第 3 步 —— 从 AST 形状层迁到框架账本层）：

  - 「**哪些参数**是被 `get_current_user` 注入的」读**框架自己的依赖树**
    （`route_facts.injected_params`，判据是 `d.call is get_current_user`）—— 0 层：与
    写法彻底无关（位置参数 / 只有关键字 / `Annotated[...]` 都看得见），也不受「路由定义
    在哪个文件、函数叫什么」影响。旧版扫 AST 找「默认值是不是 `Depends(get_current_user)`」，
    只认那一种写法，且只 glob `web/routers/*.py` —— `web/server.py` 里
    `/api/settings/config` 的 `_user` 因此整条看不见（第 3 步实测坐实）。
  - 「这个参数**被用上了吗**」**仍是 ②层**：数的是有没有出现同名 `Load`。
    **这是本锁的边界，不是遗漏**：`route_facts` 没有这个能力（框架的账本里只有「注入了
    什么」，「用它做了什么」不在账本里），而「user 是否被用于**归属校验**」的真值在
    **SQL 谓词**那一层（缺陷 25 的解法）。所以注入 user 却只把它塞进日志 / 返回值
    （`str(user)`、`logger.debug(user)`）而不做归属校验的写法，本锁看不见 —— 与缺陷 25
    同形（签名里有身份参数 ≠ 它被用于归属）。要覆盖它，要么下沉到 SQL 谓词层，要么上
    运行时探针。

豁免名单按 **(path, method)** 键 —— 路由路径是对外契约，文件位置与函数名都可以改。
表与现场的对账（未登记 / 陈旧 / 空理由）走 `policy_table` —— 与 L5 共用同一层：同一套
差集与判空在两处各写一遍会各自漂移，而漂移**不报错**（缺陷 42 结案）。
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import policy_table
import route_facts
from routers.auth import get_current_user

# 有意**不引用** user 的端点（纯登录门 / 读全局或公开数据）。键是 `(path, method)`。
ALLOWLIST = {
    ("/api/auth/announcement", "get"): "全局公告",
    ("/api/market/{card_id}/forks", "get"): "公开 fork 列表",
    ("/api/voice/status", "get"): "全局服务状态",
    ("/api/voice/asr", "post"): "全局 ASR",
    ("/api/settings/config", "get"): "纯登录门：读全局 LLM/语音配置，不做归属校验",
}


def _endpoint_node(route) -> ast.AST:
    """端点函数的 AST。读不到源码**必须响** —— 静默跳过就是静默漏过（§四）。"""
    try:
        src = inspect.getsource(route.endpoint)
    except (OSError, TypeError) as exc:
        raise AssertionError(
            f"读不到 {route.path_format} 端点函数的源码（{exc!r}）—— 本锁对该路由失效。"
            "换成源码可读的函数，或把该路由显式移进 ALLOWLIST。") from exc
    return ast.parse(textwrap.dedent(src)).body[0]


def _is_read(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
               for n in ast.walk(node))


def _unused_user_endpoints() -> dict[tuple[str, str], set[str]]:
    """`(path, method) -> 注入了却没有被引用的参数名集合`。"""
    found: dict[tuple[str, str], set[str]] = {}
    for key, route in sorted(route_facts.enumerate_routes().items()):
        injected = route_facts.injected_params(route, get_current_user)
        if not injected:
            continue
        node = _endpoint_node(route)
        unread = {name for name in injected if not _is_read(node, name)}
        if unread:
            found[key] = unread
    return found


def test_no_endpoint_injects_user_without_reading_it():
    found = _unused_user_endpoints()
    unregistered = policy_table.unexpected(found, ALLOWLIST)
    assert not unregistered, (
        f"新出现「注入 user 却不引用」的端点："
        f"{ {k: found[k] for k in sorted(unregistered)} }。"
        "要么用上 user（归属校验 / 按用户过滤），要么加进本文件 ALLOWLIST 并注明为何不需要。")


def test_allowlist_is_neither_stale_nor_reasonless():
    """反过来：名单里的端点若已经用上了 user（或已删除），条目就该删掉，别让名单腐烂；
    理由也不许是空的 —— 「写了理由」不等于「理由有内容」。"""
    found = _unused_user_endpoints()
    stale = policy_table.stale_keys(ALLOWLIST, found)
    assert not stale, f"ALLOWLIST 里的条目已不再命中，请删除：{sorted(stale)}"
    blank = policy_table.empty_reasons(ALLOWLIST)
    assert not blank, f"ALLOWLIST 里这些键的理由是空的：{sorted(blank)}"


def test_scan_is_not_vacuous():
    """负控：扫到的「注入了 user 的端点」必须非空，否则上面两条都在对空集断言（假绿）。

    与 test_text_failure_messages.py 的 L4/L5 负控同一形态 —— 数的是**扫描命中的东西**，
    不是被断言的性质本身（§四）。
    """
    injected = [key for key, route in route_facts.enumerate_routes().items()
                if route_facts.injected_params(route, get_current_user)]
    assert injected, (
        "一条「注入 get_current_user 的端点」都没扫到 —— 判据面失效（依赖对象换了 / "
        "get_dependant 的用法变了），上面两条断言都在假绿")
