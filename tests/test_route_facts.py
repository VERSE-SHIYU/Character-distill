# -*- coding: utf-8 -*-
"""缺陷 42 第 1 步：给「路由事实层」本身立锁 —— 它报的必须是框架的真值。

`tests/route_facts.py` 是用来**替代代理指标**的，所以它自己的正确性不能再用代理指标
核对（那等于用同一把尺子量它自己）。本文件走两条互相独立的核对面：

  A. **合成 app**：自建 FastAPI，把同一语义的**每一种等价写法**各写一条路由（表单
     四种写法、依赖三种写法 + 装饰器形态），断言事实层给出的集合**恰等于**期望。
     这挡住的是「代理指标只认某一种写法，其余静默漏过」。
  B. **真实 app 对账**：`census_diff()` 双向都必须为空 —— 模块枚举与 OpenAPI 文档是
     两份独立账本，任一侧漏一条都会点名（server 模块与路由子包各是不同方向的盲区）。

外加**非空负控**：探测器一旦塌成空集，「所有 X 都满足 P」会恒真 —— 那是最危险的假绿。
负控数的是**探测器命中的东西**，不是被断言的性质本身（§四）。

本文件不依赖仓库里任何具体路由，A 组的期望值全部写死在这里，与仓库路由变动解耦。
"""
from __future__ import annotations

import sys
from typing import Annotated

import fastapi
import pytest
from fastapi import APIRouter, Depends, FastAPI, File, Form, UploadFile

import route_facts
from route_facts import (
    _FORM_CONTENT_TYPES,
    _METHODS,
    census_diff,
    enumerate_routes,
    form_fields,
    injected_params,
    is_file_field,
    openapi,
)


# ── A. 合成 app：四种表单写法 / 三种依赖写法 ────────────────────────────────


def _dep_fn():
    return {"injected": True}


_forms = APIRouter(prefix="/synth-forms")


@_forms.post("/positional")
def _f_positional(alpha: str = Form(...), beta: str = Form(...)):
    return {}


@_forms.post("/kwonly")
def _f_kwonly(*, gamma: str = Form(...)):
    return {}


@_forms.post("/annotated")
def _f_annotated(delta: Annotated[str, Form()]):
    return {}


@_forms.post("/attr")
def _f_attr(epsilon: str = fastapi.Form(...)):
    return {}


@_forms.post("/optional-file")
def _f_optional_file(blob: UploadFile | None = File(None), zeta: str = Form(...)):
    return {}


@_forms.post("/required-file")
def _f_required_file(blob2: UploadFile = File(...), eta: str = Form(...)):
    return {}


_deps = APIRouter(prefix="/synth-deps")


@_deps.get("/positional")
def _d_positional(u: dict = Depends(_dep_fn)):
    return {}


@_deps.get("/kwonly")
def _d_kwonly(*, u: dict = Depends(_dep_fn)):
    return {}


@_deps.get("/annotated")
def _d_annotated(u: Annotated[dict, Depends(_dep_fn)]):
    return {}


@_deps.get("/decorator", dependencies=[Depends(_dep_fn)])
def _d_decorator():
    return {}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(_forms)
    app.include_router(_deps)
    return app


# 合成 router 对象就挂在本模块的 globals 里，故枚举的输入就是本模块自己 ——
# 不必另造一份「手工清单」（那正是 route_facts 要消灭的东西）。
_SYN_APP = _build_app()
_SYN_ROUTES = enumerate_routes([sys.modules[__name__]])
_SYN_SPEC = openapi(_SYN_APP.routes)


@pytest.mark.parametrize(
    "path, expected, expected_files",
    [
        ("/synth-forms/positional", {"alpha", "beta"}, set()),
        ("/synth-forms/kwonly", {"gamma"}, set()),
        ("/synth-forms/annotated", {"delta"}, set()),
        ("/synth-forms/attr", {"epsilon"}, set()),
        ("/synth-forms/optional-file", {"blob", "zeta"}, {"blob"}),
        # 必填文件：schema 是**顶层** contentMediaType（不走 anyOf），与可选文件是两种形态。
        ("/synth-forms/required-file", {"blob2", "eta"}, {"blob2"}),
    ],
)
def test_synth_form_fields_are_exactly_the_declared_ones(path, expected, expected_files):
    """四种等价写法各自一条用例 —— 漏任何一种写法都只让**那一条**红。"""
    fields = form_fields(path, "post", spec=_SYN_SPEC)
    assert set(fields) == expected, f"{path} 的字段集合不对：{sorted(fields)}"
    assert {n for n, s in fields.items() if is_file_field(s)} == expected_files


def test_synth_pure_form_route_is_urlencoded():
    """没有文件字段的路由走 urlencoded —— 另一个 content 分支的专属红源（F-5）。

    `form_fields` 只认 multipart 时，这条断言本身仍然绿，但上面参数化里
    `/synth-forms/attr` 那条会红（它的字段全部取不到）。两条一起才说明「两个分支都读」。
    """
    content = _SYN_SPEC["paths"]["/synth-forms/attr"]["post"]["requestBody"]["content"]
    assert set(content) == {"application/x-www-form-urlencoded"}, sorted(content)


def test_synth_file_route_is_multipart():
    content = _SYN_SPEC["paths"]["/synth-forms/optional-file"]["post"]["requestBody"]["content"]
    assert "multipart/form-data" in content, sorted(content)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/synth-deps/positional", {"u"}),
        ("/synth-deps/kwonly", {"u"}),
        ("/synth-deps/annotated", {"u"}),
        # 装饰器形态：依赖没有参数名，故不计入（不是「漏了」，是它本来就没有名字）。
        ("/synth-deps/decorator", set()),
    ],
)
def test_synth_injected_params_across_spellings(path, expected):
    route = _SYN_ROUTES[(path, "get")]
    assert injected_params(route, _dep_fn) == expected


def test_form_fields_raises_on_unknown_operation():
    """契约：查不到就 raise。空字典是「这条路由没有表单字段」的合法答案，两者不能混。"""
    with pytest.raises(KeyError):
        form_fields("/synth-does-not-exist", "post", spec=_SYN_SPEC)


# ── B0. 入口模块的身份 ─────────────────────────────────────────────────────


def test_fact_layer_describes_the_repo_app_object_under_a_single_identity():
    """事实层描述的 app 必须**就是** ``sys.modules["server"].app``，不是另一个同内容的 app。

    装配层是同一个文件，却可以有**两个模块身份**（``server`` 与 ``web.server``）：同进程
    内两个名字各加载一次，文件被**执行两次**，得到两个互不相干的模块对象、两个 app。
    危害不是报错，是**静默错位** —— 两边内容看起来一样，``is`` 与属性比对却为假，于是
    任何「是不是同一个对象」的判断都失去意义（app 上挂着 lifespan / 中间件 / state，
    这类比对正是隔离性证据的来源）。

    全仓在测试进程里的叫法是 ``server``（conftest 把 ``web/`` 加进了 ``sys.path``）。

    **先把默认路径走一遍再断言**：事实层里 import 装配层的地方不止 ``app()``（枚举默认
    模块集也要它），不先跑一遍，「第二个身份」可能还没被加载，断言就会在它出现之前通过
    —— 那样的锁是纸锁（实测：只改枚举那处、不先跑默认路径，本用例全绿）。

    最后一条按**文件**而不是按名字找并列身份：名字是代理指标，换个别名（``web.server``
    之外的名字、或第三方 importlib 装载）就抓不到 —— 而这一层存在的理由正是拒绝代理指标。
    """
    import server as server_module

    route_facts.openapi()            # 默认路径之一
    route_facts.enumerate_routes()   # 默认路径之二

    assert route_facts.app() is server_module.app, (
        "route_facts 描述的 app 不是全仓那一个 —— 装配层被加载了第二个身份")
    assert "web.server" not in sys.modules, (
        "server 模块出现了并列身份 'web.server'：同一文件会被执行两次，对象比对静默错位")

    aliases = sorted(
        name for name, mod in sys.modules.items()
        if getattr(mod, "__file__", None) is not None
        and getattr(mod, "__file__", None) == getattr(server_module, "__file__", None)
    )
    assert aliases == ["server"], (
        f"装配层这一份文件有并列的模块身份 {aliases} —— 同一文件被加载多次，"
        "得到互不相干的模块对象，`is` 与属性比对静默为假")


# ── B. 真实 app 对账 ───────────────────────────────────────────────────────


def test_real_app_census_matches_in_both_directions():
    """两份独立账本必须逐条对上；任一侧的差集单独点名（方向不同，病根不同）。"""
    only_declared, only_enumerated = census_diff()
    assert not only_declared, (
        f"OpenAPI 文档里有、模块枚举里没有的 operation（枚举侧有盲区）：{sorted(only_declared)}")
    assert not only_enumerated, (
        f"模块枚举里有、OpenAPI 文档里没有的 operation（枚举侧多了不该有的）：{sorted(only_enumerated)}")


# ── 非空负控 ───────────────────────────────────────────────────────────────


def test_census_is_not_vacuous():
    """探测器塌成空集时「双向差集为空」会恒真 —— 先把「非空且数量相等」钉住。"""
    spec = openapi()
    ops = {
        (path, method.lower())
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method.lower() in _METHODS
    }
    enumerated = enumerate_routes()
    assert len(ops) > 0, "OpenAPI 文档里一个 operation 都没有 —— spec 生成失效"
    assert len(enumerated) == len(ops), (
        f"枚举到 {len(enumerated)} 条、文档声明 {len(ops)} 条 —— 数量不等时"
        "「差集为空」不可能同时成立，说明比对的前提已经坏了")


def test_every_form_operation_exposes_its_fields():
    """对文档里**每一个**声明了表单 content 的 operation，`form_fields` 都必须非空。

    从 spec 派生、不点名任何路径 —— 新增一条表单路由自动纳入，删掉全部表单路由则由
    末尾的 `checked > 0` 挡住「空集恒真」。
    """
    spec = openapi()
    checked = 0
    for path, item in spec.get("paths", {}).items():
        for method in item:
            if method.lower() not in _METHODS:
                continue
            content = (item[method].get("requestBody") or {}).get("content") or {}
            if not any(ct in content for ct in _FORM_CONTENT_TYPES):
                continue
            checked += 1
            assert form_fields(path, method, spec=spec), (
                f"{method.upper()} {path} 声明了表单 content 却解析不出字段 —— "
                "探测器（$ref 解析 / content 分支）失效")
    assert checked > 0, (
        "spec 里连一个表单 operation 都没有 —— 要么真的删光了，要么 spec 生成失效，"
        "两种都该有人看一眼，不该静默通过")
