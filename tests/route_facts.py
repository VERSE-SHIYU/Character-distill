"""路由**框架事实**层：只回答「框架认为这条路由长什么样」，不含任何本仓业务知识。

本文件不是一个测试，`tests/` 下的辅助模块（先例 ``tests/evidence_fakes.py``），供锁
调用。它存在的理由：

**为什么需要这一层。** 已有几把锁想断言「某类参数没出现在某条路由上」，但它们是拿
AST 的形状猜框架语义的 —— 「默认值是不是一个名叫某框架名字的 ``ast.Call``」这种判据
是**代理指标**：同一个语义在框架里有多种等价写法（位置参数 / 只有关键字的参数 /
``Annotated[...]`` 注解 / 通过模块属性引用），代理指标只认其中一种，其余写法**静默
漏过**。代理指标失效时不报错、只是不再覆盖 —— 这正是要消灭的失败形态。

**为什么不去遍历那个 app 对象的 ``.routes``。** 在部分框架版本里，被 ``include`` 进来
的路由会被包装成一种没有 ``.routes`` 属性的对象，于是按路由类型筛 ``app.routes`` 只能
拿到很小一部分 operation，**且不抛异常**（实测某版本下只拿到个位数 / 总数两百余）。
换版本数量还会变。所以本层从**模块**枚举（模块级 router 的 ``.routes`` 是稳定的），并用
OpenAPI 文档作为第二份独立账本对账。

**为什么 ``census_diff`` 只算不断言。** 两份账本「都对得上」这件事本身要被测，而
「对得上」只有在两份账本独立时才有信息量。此函数是那两块料，断言归调用方。

**边界。** 本文件不写任何具体路径、字段名、依赖名 —— 哪些字段允许、哪些端点豁免是各把
锁自己的策略。这里只提供「框架的账本」和「从账本里取数」的机械操作。
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
for _p in (_repo, _repo / "web"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import APIRouter, FastAPI
from fastapi.dependencies.utils import get_dependant
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

# HTTP 方法名，用于把 openapi 的 path item 与路由对象的 methods 对齐。
_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")

# 表单类 content type。框架按「有没有文件字段」在两者之间择一，本层都要认。
_FORM_CONTENT_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")

# get_openapi 的两个必填 kwarg（文档自身的标题/版本），不是任何业务字段。
_SPEC_TITLE = "route-facts"
_SPEC_VERSION = "0"

_REF_PREFIX = "#/components/schemas/"
_MAX_REF_DEPTH = 8

_cache: dict = {}


# ── 模块枚举 ───────────────────────────────────────────────────────────────


def _default_modules() -> list:
    """默认模块集：路由子包下全部模块 + 入口模块（派生，不写手工清单）。

    用 ``pkgutil.iter_modules`` 而不是硬编码名单：手工名单就成了一份「守卫与被守对象
    之间的第二份清单」，新增模块时锁不会红，只会变成假绿。
    """
    import routers as routers_pkg

    mods = [
        importlib.import_module(f"routers.{m.name}")
        for m in sorted(pkgutil.iter_modules(routers_pkg.__path__), key=lambda m: m.name)
    ]
    mods.append(importlib.import_module("web.server"))
    return mods


def enumerate_routes(modules=None) -> dict[tuple[str, str], APIRoute]:
    """``{(path, method小写): APIRoute}`` —— 从模块级 router 对象枚举。

    键用 ``route.path_format``：``/{x}`` 与 ``/{x:path}`` 在别处会归一成同一个串，用
    ``path_format`` 才与 OpenAPI 的点名逐字一致。
    """
    out: dict[tuple[str, str], APIRoute] = {}
    for mod in _default_modules() if modules is None else modules:
        for obj in vars(mod).values():
            if not isinstance(obj, (APIRouter, FastAPI)):
                continue
            for route in obj.routes:
                if not isinstance(route, APIRoute):
                    continue
                for method in route.methods:
                    out[(route.path_format, method.lower())] = route
    return out


# ── OpenAPI 账本 ───────────────────────────────────────────────────────────


def _spec_from(routes) -> dict:
    return get_openapi(title=_SPEC_TITLE, version=_SPEC_VERSION, routes=list(routes))


def openapi(routes=None) -> dict:
    """OpenAPI 文档。显式传 ``routes`` 时不缓存（调用方要的是「这份输入」的答案）。"""
    if routes is not None:
        return _spec_from(routes)
    if "spec" not in _cache:
        import web.server as server

        _cache["spec"] = _spec_from(server.app.routes)
    return _cache["spec"]


def _operations(spec: dict) -> set[tuple[str, str]]:
    """``{(path, method小写)}`` —— 从文档取，path item 里的非方法键（如 parameters）被滤掉。"""
    return {
        (path, method.lower())
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method.lower() in _METHODS
    }


def census_diff(modules=None, routes=None) -> tuple[set, set]:
    """两份账本的差集：``(只在文档里的, 只在枚举里的)``。只计算，断言归调用方。"""
    declared = _operations(openapi(routes))
    enumerated = set(enumerate_routes(modules))
    return declared - enumerated, enumerated - declared


# ── 取数 ───────────────────────────────────────────────────────────────────


def _resolve(schema, spec: dict, _depth: int = 0):
    """把 ``{"$ref": "#/components/schemas/X"}`` 换成 X 的定义；非 ``$ref`` 原样返回。"""
    if not isinstance(schema, dict) or "$ref" not in schema:
        return schema
    if _depth > _MAX_REF_DEPTH:
        raise ValueError(f"$ref 嵌套超过 {_MAX_REF_DEPTH} 层，疑似环：{schema['$ref']}")
    ref = schema["$ref"]
    if not ref.startswith(_REF_PREFIX):
        raise ValueError(f"不认识的 $ref 前缀：{ref}")
    name = ref[len(_REF_PREFIX):]
    return _resolve(spec["components"]["schemas"][name], spec, _depth + 1)


def form_fields(path: str, method: str, spec: dict | None = None) -> dict[str, dict]:
    """该 operation 的表单字段 → 字段 schema（已解 ``$ref``）。

    operation 不存在时 ``raise``，**不返回空** —— 空字典是「这条路由没有表单字段」的
    合法答案，不能与「路径写错了」共用一个返回值（否则调用方分不出自己错在哪）。
    """
    spec = openapi() if spec is None else spec
    op = spec.get("paths", {}).get(path, {}).get(method.lower())
    if op is None:
        raise KeyError(f"{method.upper()} {path} 不在该 spec 里")
    content = (op.get("requestBody") or {}).get("content") or {}
    out: dict[str, dict] = {}
    for ctype in _FORM_CONTENT_TYPES:
        media = content.get(ctype)
        if media is None:
            continue
        body = _resolve(media.get("schema") or {}, spec)
        for name, sub in (body.get("properties") or {}).items():
            out[name] = _resolve(sub, spec)
    return out


def is_file_field(schema: dict) -> bool:
    """这个字段是不是文件上传通道 —— 判据直接从 schema 推出。

    文件字段的实形态是 ``anyOf`` 里那个 ``contentMediaType`` 分支（可选文件多一个
    ``null`` 分支），不是 ``format: binary``；两个都认。
    """
    if not isinstance(schema, dict):
        return False
    if "contentMediaType" in schema or schema.get("format") == "binary":
        return True
    return any(is_file_field(b) for b in schema.get("anyOf") or ())


def injected_params(route: APIRoute, dependency) -> set[str]:
    """这条路由上，由 ``dependency`` 这个对象注入进来的参数名。

    判据是**依赖对象本身**（``d.call is dependency``），不是参数叫什么、也不是源码里
    写成哪种形态 —— 同一个依赖用位置参数 / 只有关键字 / ``Annotated`` 三种写法进来，
    这里都得看见；写成路由装饰器上的 ``dependencies=[...]`` 时它没有参数名，故不出现。
    """
    dep = get_dependant(path=route.path_format, call=route.endpoint)
    return {d.name for d in dep.dependencies if d.call is dependency}
