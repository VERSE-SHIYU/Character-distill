# -*- coding: utf-8 -*-
"""缺陷 17 不变量锁：异常 → 上屏文案只有 `user_facing_error` 一个出口，且内部标识不上屏。

背景：蒸馏失败曾经在**源头**（core/distiller.py）和**路由**（web/routers/distill.py）各拼
一次「蒸馏失败：」前缀 → 用户看到双重前缀；且源头文案本身就是运维口径（
「请提高 max_tokens 上限」），路由兜底还会打异常类名，流式那条链更直接 `str(exc)` 上屏。

本文件锁三件事，缺一条这个不变量就守不住：
  A. 出口行为：已知异常集合逐个过 `user_facing_error`，上屏文案不含内部标识
  B. AST 锁：蒸馏失败的 raise / 上屏字面量必须带已审口径（新增一处裸
     `raise ValueError("蒸馏失败…")` 或往文案里塞 finish_reason 会立刻红）
  C. 真实路径：真跑 `Distiller.distill`、真抛 `DistillError`，断言上屏只有**一个**前缀、
     而运维细节确实留在 `str()` 里——两套口径真的分开了，不是只改了个常量
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from adapters.llm_adapter import (
    IncompleteResponseError,
    _extract_content,
    user_facing_error,
)
from core.distiller import DistillError, Distiller

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 内部标识：异常类名 / 运维口径 / 内部参数名 / 内部路径 / 分片术语。
# 上屏文案里出现任何一个都是缺陷 17 复发。
_FORBIDDEN = (
    "IncompleteResponseError", "DistillError", "RateLimitError", "RuntimeError",
    "ValueError", "JSONDecodeError", "ValidationError", "Exception",
    "finish_reason", "content_filter", "insufficient_system_resource",
    "max_tokens", "chunk_size", "where=", "last_error", "truncated",
    "distiller.py", "llm_adapter.py", "traceback",
    "个分片",   # 用「个分片」不用裸「分片」：上屏的「部分片段处理失败」是合法中文，会假阳
)


def _assert_screen_clean(screen: str, label: str) -> None:
    assert screen and screen.strip(), f"{label}: 上屏文案不能为空"
    leaked = [t for t in _FORBIDDEN if t in screen]
    assert not leaked, f"{label}: 内部标识漏上屏 {leaked} —— {screen!r}"


# ── A. 出口行为：已知异常集合 ───────────────────────────────────────────


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, finish_reason, content):
        self.finish_reason = finish_reason
        self.message = _Msg(content)


def _real_incomplete(finish_reason: str = "length") -> IncompleteResponseError:
    """经**真实**的 `_extract_content` 造异常，不直接构造。

    直接 `IncompleteResponseError("length", "chat")` 绕过了 content 传递链，
    「上屏文案从真实异常取」这条链就测不到了（同 truncation 自愈测试的取向）。
    """
    with pytest.raises(IncompleteResponseError) as ei:
        _extract_content(_Choice(finish_reason, '{"name": "阿'), where="distill_stream")
    return ei.value


def _known_cases() -> list[tuple[str, BaseException, str]]:
    """(标签, 异常, 期望上屏文案)。期望值是契约，不是实现细节。"""
    return [
        ("incomplete/length", _real_incomplete("length"), "回复被截断，请重试"),
        ("incomplete/content_filter",
         IncompleteResponseError("content_filter", "distill_stream"),
         "内容被安全策略拦截，请修改后重试"),
        ("incomplete/未登记 finish_reason",
         IncompleteResponseError("insufficient_system_resource", "distill_stream"),
         "服务繁忙，请稍后重试"),
        ("distill/限流",
         DistillError("蒸馏失败：上游接口限流，请稍后重试", "API 429；9/12 个分片失败"),
         "蒸馏失败：上游接口限流，请稍后重试"),
        ("distill/格式异常",
         DistillError("蒸馏失败：LLM 输出格式异常，请重试",
                      "last_error=Expecting value: line 1 column 1 (char 0)"),
         "蒸馏失败：LLM 输出格式异常，请重试"),
    ]


def test_known_failures_screen_exact_text_and_no_internals():
    for label, exc, expected in _known_cases():
        screen = user_facing_error(exc)
        assert screen == expected, f"{label}: {screen!r} != {expected!r}"
        _assert_screen_clean(screen, label)


def test_known_failures_keep_ops_detail_out_of_screen():
    """反向断言：运维细节必须真的在 ``str()`` 里（否则是「删了信息」而非「分了口径」）。"""
    limiter = DistillError("蒸馏失败：上游接口限流，请稍后重试", "API 429；9/12 个分片失败")
    assert "429" in str(limiter) and "分片" in str(limiter)
    assert "429" not in user_facing_error(limiter)

    trunc = _real_incomplete("length")
    assert "finish_reason" in str(trunc) and "max_tokens" in str(trunc)
    assert user_facing_error(trunc) == "回复被截断，请重试"


def test_unknown_failures_get_generic_text_never_str():
    """未登记异常：上屏只能是通用文案。裸 `str(exc)` 上屏就是这条要拦的东西。"""
    cases = [
        ("裸 ValueError 带内部细节",
         ValueError("蒸馏失败：LLM 返回格式不正确；last_error=max_tokens=4096 exceeded"
                    " at core/distiller.py:620")),
        ("网络故障",
         ConnectionError("HTTPSConnectionPool(host='api.deepseek.com', port=443): Read timed out")),
        ("缺 key", KeyError("api_key")),
        ("带类名前缀", RuntimeError("ValidationError: 3 validation errors for CharacterCard")),
    ]
    for label, exc in cases:
        screen = user_facing_error(exc)
        _assert_screen_clean(screen, label)
        assert str(exc) not in screen, f"{label}: str(exc) 直接上屏了"
        assert screen == user_facing_error(RuntimeError("x")), (
            f"{label}: 未登记异常必须落到同一句通用文案")


def test_duck_typed_declared_message_wins_over_generic():
    """``exc.user_message`` 走 duck typing —— 出口不 import DistillError（边界锁要求）。"""

    class _Declared(Exception):
        user_message = "  已审话术  "

    assert user_facing_error(_Declared()) == "已审话术"

    class _Blank(Exception):
        user_message = "   "

    assert user_facing_error(_Blank()) != "   "      # 空白话术不算话术，落通用文案


def test_preserve_unknown_is_opt_in_for_chat_sse_only():
    """chat 的 SSE 帧显式保留未登记异常原文（排障契约）；蒸馏路径不许传这个开关。"""
    exc = RuntimeError("LLM API failed after 3 attempts")
    assert user_facing_error(exc, preserve_unknown=True) == str(exc)
    assert user_facing_error(exc) != str(exc)


# ── B. AST 锁：上屏字面量与 raise 点 ────────────────────────────────────

_SCANNED = ("core/distiller.py", "web/routers/distill.py")
_SCREEN_KEYS = ("error", "message")


def _screen_literals(path: str):
    """产出 ``(行号, 文案, 位置)``：raise 的首个字符串实参 + dict 里 error/message 的字符串值。

    只看**值**、不看 docstring —— 文档里当然可以写内部标识（那是给维护者的）。
    """
    tree = ast.parse((_ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            args = node.exc.args
            if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                yield path, node.lineno, args[0].value, "raise"
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value in _SCREEN_KEYS
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    yield path, node.lineno, v.value, f"dict[{k.value}]"


def test_no_internal_identifier_in_any_screen_literal():
    """上屏字面量扫描：这两条链上**所有**可能出现在响应里的字符串都要过同一道筛。"""
    offenders = []
    for path in _SCANNED:
        for p, lineno, text, where in _screen_literals(path):
            leaked = [t for t in _FORBIDDEN if t in text]
            if leaked:
                offenders.append(f"{p}:{lineno} ({where}) {text!r} 含 {leaked}")
    assert offenders == []


def test_distill_failure_raises_are_declared_errors():
    """蒸馏失败的 raise 必须抛 `DistillError`（自带 user_message），不许裸 ValueError。

    变异判定：把任一处改回 ``raise ValueError("蒸馏失败：…")`` 这条即红。
    """
    tree = ast.parse((_ROOT / "core/distiller.py").read_text(encoding="utf-8"))
    wrong_cls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        args = node.exc.args
        if not args or not isinstance(args[0], ast.Constant) or not isinstance(args[0].value, str):
            continue
        if "蒸馏失败" not in args[0].value:
            continue
        func = node.exc.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "DistillError":
            wrong_cls.append((node.lineno, name))
    assert wrong_cls == [], f"蒸馏失败必须抛 DistillError（带 user_message），实际：{wrong_cls}"


# ── C. 真实路径：真跑一遍蒸馏，两套口径真的分开了 ────────────────────────


def _llm(side_effect=None):
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.model = "test-model"
    llm.last_usage = None
    llm.chat = MagicMock(side_effect=side_effect)
    return llm


def test_real_distill_failure_single_prefix_and_split_payloads():
    """真路径：LLM 三次都回非 JSON → 真 `DistillError`。

    仅断言常量相等（如上面 A 组）测不出「路由又拼了一次前缀」——必须让上屏文案
    经**真实产出链**走一遍，才能锁住「只有前缀一处、且只有一份」。
    """
    llm = _llm(side_effect=["这不是 JSON"] * 3)
    with pytest.raises(DistillError) as ei:
        Distiller(llm).distill("有些文本", "阿Q")

    screen = user_facing_error(ei.value)
    assert screen.count("蒸馏失败：") == 1, f"前缀重复或缺失：{screen!r}"
    _assert_screen_clean(screen, "真实蒸馏失败")

    # 同一异常的另一半：运维口径留在 str()，排障不丢线索
    assert ei.value.user_message == screen
    assert "｜" in str(ei.value)
    assert screen != str(ei.value)


def test_real_distill_failure_reaches_storage_boundary_without_internals():
    """路由层拿到的就是这一句——把它当作写进 `_set_task(message=…)` 的值验一遍。"""
    llm = _llm(side_effect=["这不是 JSON"] * 3)
    with pytest.raises(DistillError) as ei:
        Distiller(llm).distill("有些文本", "阿Q")
    payload = {"status": "error", "message": user_facing_error(ei.value)}
    assert payload["message"].startswith("蒸馏失败：")
    _assert_screen_clean(payload["message"], "任务状态 message")
