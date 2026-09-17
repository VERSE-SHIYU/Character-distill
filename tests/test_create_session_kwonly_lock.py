# -*- coding: utf-8 -*-
"""锁：`TextManager._create_session` 的可选参数只许按关键字传。

**它防的是什么。** 这个函数的可选参数**全是 str**（`card_id` / `user_id` /
`user_role` …），按位置传错位**不会报错** —— 只会静默把值装进邻近的参数。
2026-09 真发生过一次：`web/routers/history.py` 按位置传了 8 个实参，第 7 位是
`user_role`，于是用户配置里的**嵌入凭据**落进 `user_role` → 随 prompt 发给模型方
（「对话者身份：sk-…」）→ 又明文落进 `sessions.affinity_state`（缺陷 G）。

**那次的落脚点已经拆掉。** 当时签名尾部还有两个从未被函数体引用的参数
（「嵌入凭据」与「所在区域」），多出来的两个实参正是落在那两格上。它们已删 ——
**死参数为错位留出空间，先问这个参数有没有人用，再问怎么传。**

**为什么五个调用点当年只有一处翻车**：`core/text_manager.py`×2、`chat.py`、`distill.py`
四处都恰好停在**第 6 个**参数（`user_id`）为止，只有 `history.py` 多传了两个。
**「今天对」是边界巧合，不是设计** —— 所以判据不落在那五个调用点上（调用点会增加、
会重排），只落在**签名**上：`*` 之后任何位置传参都是 `TypeError`。

**判据怎么读**：签名里第 3 个及以后的参数，kind 只能是 `KEYWORD_ONLY`。
这条锁不依赖任何调用点、不依赖仓库现状 —— 「新来的人按位置传就直接报错，
不需要知道这段历史」。
"""

from __future__ import annotations

import inspect

import pytest

from core.text_manager import TextManager

# 位置传参是这几个参数的既定用法，不设限；第 3 个之后必须 keyword-only。
_POSITIONAL_OK = ("self", "text", "card")


def _sig() -> inspect.Signature:
    return inspect.signature(TextManager._create_session)


def test_optional_params_are_keyword_only():
    """锁本体：`*` 之后不许再出现可位置传的参数。"""
    offenders = [
        (name, p.kind.name)
        for name, p in _sig().parameters.items()
        if name not in _POSITIONAL_OK
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    assert not offenders, (
        f"`_create_session` 这些参数又变回可位置传了：{offenders}。\n"
        "缺陷 G 正是从这里发生的：7 个可选参数全是 str，错位不报错、只静默装错值。\n"
        "修法是把它们放在 `*` 之后（keyword-only），不是靠调用点自觉。"
    )


def test_positional_optional_arg_raises_typeerror():
    """变异：把任一调用点改回位置传参 → `TypeError`，而不是静默错位。

    `("正文", object(), None, [], None)` 的前两个位置参数（`text` / `card`）是合法的，
    从第 3 个起越界。若签名退化回可位置传，这一句就不会抛 `TypeError` —— 这条锁随即变红。
    """
    args = (None, "正文", object(), None, [], None)  # self, text, card, + 3 个多余位置实参
    with pytest.raises(TypeError):
        TextManager._create_session(*args)


def test_keyword_call_binds_each_name_to_its_own_value():
    """正控：关键字传参照常能绑上，且**名字对名字** —— 锁不是靠「一律报错」变绿的。

    用 `Signature.bind` 只做绑定、不进函数体（真调用会建 ChatEngine、写 `_sessions`，
    是副作用）。
    """
    bound = _sig().bind(
        None, "正文", object(),
        all_characters=[], rag=None,
        card_id="card_x", user_id="u_x",
        user_role="读者",
    )
    assert bound.arguments["user_role"] == "读者"
    assert bound.arguments["card_id"] == "card_x"


def test_dead_params_that_gave_the_overrun_somewhere_to_land_stay_deleted():
    """缺陷 G 的落脚点不许回来。

    当年签名尾部有两个参数函数体从未引用（嵌入凭据 / 所在区域）—— 调用点多传的
    两个实参就落在它们身上。**死参数为错位留出空间**：留着它们，同类错位就还有地方可落。
    判据直接问签名，不问文档。
    """
    params = set(_sig().parameters)
    assert "embedding_key" not in params, "嵌入凭据参数又回到 _create_session 签名里了"
    assert "embedding_region" not in params, "所在区域参数又回到 _create_session 签名里了"

