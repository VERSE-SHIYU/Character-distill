# -*- coding: utf-8 -*-
"""同步 chat 的截断自愈：上游确定信号（finish_reason=length）必须接回重修环。

背景：Tier 1 给同步 chat 加了 finish_reason 裁决后，length 直接抛
IncompleteResponseError，半截文本永远到不了 core/distiller.py 的
_parse_json_with_retry——为截断建的「请精简输出」重修环，主触发路径不可达。
本文件锁六件事：
  1. 确定信号接回了环（`_chat_accounted` 把半截正文交出去，Attempt 2 走截断专用修复）
  2. 厂商不回 finish_reason 时，`_looks_truncated` 从文本形状猜的老路径没被取代
  3. 重修上限仍是 3 次尝试，到顶后抛的是「超长被截断」而非「格式异常」
  4. map 阶段（`async_chat` 那条链）不受牵连
  5. 非截断的未完成终态（如 content_filter）不被吞成「超长被截断」，原样上抛
  6. 上游已确定截断但 JSON 合法而缺字段时，重修走 schema 支而非截断支（O3）
  7. `length` 但**空正文**的终态（没东西可修的那一格）算瞬时失败，重修环继续下一次
     尝试，而不是提前上抛；用尽后仍报「超长被截断」这条线索

生产侧的边界锁另有一份：`core/` 不得出现异常类名，见
tests/test_chat_stream_error.py::test_no_exception_class_leaks_into_core_web_storage。
本文件在 tests/ 下，可以直接构造该异常（与 test_llm_adapter_finish_reason.py 同）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from adapters.llm_adapter import (
    IncompleteResponseError,
    _extract_content,
    llm_error_payload,
)
from core.distiller import Distiller

PARTIAL = '{"name": "阿'   # 被 max_tokens 截断的半截角色卡
FULL = '{"name": "阿Q"}'   # 重修后的完整角色卡（CharacterCard 只有 name 是必填）
SHAPE_BAD = '{"status": "ok"}'   # 合法 JSON、却不是角色卡（缺 name）
TRUNCATION_PROMPT_MARK = "因长度超限被截断"
SCHEMA_PROMPT_MARK = "缺少必需字段"


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, finish_reason, content):
        self.finish_reason = finish_reason
        self.message = _Msg(content)


def _llm(side_effect=None, return_value=None) -> MagicMock:
    llm = MagicMock()
    llm.model = "test-model"
    llm.last_usage = None
    llm.chat = MagicMock(side_effect=side_effect, return_value=return_value)
    return llm


def _truncated(content: str = PARTIAL) -> IncompleteResponseError:
    """经**真实**的 _extract_content 造异常，不直接构造。

    直接 `IncompleteResponseError("length", "chat", content=...)` 会绕过 content 的
    传递链，于是「去掉 content 传递」的变异不会红——那是个测不出东西的用例。走真实
    提取点才让这个变异可判定（同 test_llm_adapter_finish_reason.py 的取向）。
    """
    with pytest.raises(IncompleteResponseError) as ei:
        _extract_content(_Choice("length", content), where="chat")
    return ei.value


def _prompts(llm: MagicMock) -> list[str]:
    """每次 chat 调用的 system prompt（第 0 个位置参数）。"""
    return [c.args[0] for c in llm.chat.call_args_list]


def _incomplete(reason: str, content: str = "") -> IncompleteResponseError:
    """任意 finish_reason 的未完成终态（同样经真实提取点造，不直接构造异常）。"""
    with pytest.raises(IncompleteResponseError) as ei:
        _extract_content(_Choice(reason, content), where="chat")
    return ei.value


class TestTruncationSelfHeal:
    def test_upstream_length_signal_heals_and_calls_llm_twice(self):
        """正向：一次 length 截断 + 一次成功 → 拿到合法卡，且 chat 恰好 2 次。

        2 次是关键：证明自愈真的发生了（第 2 次是重修），不是碰巧第一次就成功。
        再断言第 2 次是**截断专用** prompt 且喂进去的就是那份半截正文——这才叫
        「把上游信号接进修修环」，而不是随便重试一次。
        """
        llm = _llm(side_effect=[_truncated(), FULL])
        card = Distiller(llm).distill("有些文本", "阿Q")

        assert card.name == "阿Q"
        assert llm.chat.call_count == 2
        assert TRUNCATION_PROMPT_MARK in _prompts(llm)[1], (
            f"第 2 次不是截断专用修复 prompt：{_prompts(llm)[1]!r}"
        )
        assert llm.chat.call_args_list[1].args[1][0]["content"] == PARTIAL

    def test_no_signal_still_guesses_from_text_shape(self):
        """无信号：厂商不回 finish_reason，`_looks_truncated` 那条老路径仍在。

        第 2 次必须仍是截断专用 prompt——若 `_looks_truncated` 分支被删掉，这里会
        退化成通用「无法被解析为JSON」prompt，断言即红。
        """
        llm = _llm(side_effect=[PARTIAL, FULL])   # 无异常：上游没给确定信号
        card = Distiller(llm).distill("有些文本", "阿Q")

        assert card.name == "阿Q"
        assert llm.chat.call_count == 2
        assert TRUNCATION_PROMPT_MARK in _prompts(llm)[1]

    def test_repair_cap_raises_truncation_error_not_format_error(self):
        """上限：连续截断到 3 次尝试用尽 → 抛，且上屏文案是「超长被截断」。

        mock 故意给 4 个值、第 4 个是合法 JSON：这样「把上限改成无限」的变异会走到
        第 4 次并成功返回，断言「抛出」立刻红——变异可判定，不是挂死。

        口径分离（缺陷 17）：运维口径（抬 max_tokens）只能出现在 str() 里进日志，
        绝不能进 user_message 上屏——旧实现把「请提高 max_tokens 上限」直接给用户看。
        """
        llm = _llm(side_effect=[_truncated(), _truncated(), _truncated(), FULL])
        with pytest.raises(ValueError) as ei:
            Distiller(llm).distill("有些文本", "阿Q")

        assert llm.chat.call_count == 3        # 上限仍是 3 次尝试，没多烧第 4 次
        screen = ei.value.user_message
        assert "超长被截断" in screen and "格式异常" not in screen
        assert "max_tokens" not in screen, f"运维口径漏上屏：{screen!r}"
        assert "max_tokens" in str(ei.value), "运维口径还得留在日志里，否则排障无线索"

    def test_non_length_terminal_state_is_not_swallowed_as_truncation(self):
        """非截断的未完成终态（content_filter）原样上抛，**不**吞成「超长被截断」。

        重修环里原先写的是 `if incomplete_response_info(exc) is not None: truncated = True`
        —— 任何未完成终态都被当成截断。于是内容被上游安全策略过滤时，用户看到的是
        「生成内容超长被截断，请重试」，运维指引还叫抬 `max_tokens`：两条都指错方向
        （content_filter 要改输入，重试无用）。现在判据收在 `_unfinished_disposition`：
        **非 `length`** 的未完成终态原样上抛，由 web/server.py 那张 finish_reason 分档
        表配码与上屏；`length` 的两格都不上抛（有正文走重修支，空正文按瞬时失败继续下
        一次尝试——后者的锁是本类最后两条用例）。

        变异：把环内两处 `except` 改回重构前形态（判据不变，但分支体从 `raise` 改回
        `truncated = True` + 写 `last_error`，即任何未完成终态都吞成截断）→ 本用例红
        （抛的是 DistillError、文案里是「超长被截断」）。
        """
        llm = _llm(side_effect=[
            _truncated(PARTIAL),
            _incomplete("content_filter"),
            _incomplete("content_filter"),
        ])
        with pytest.raises(IncompleteResponseError) as ei:
            Distiller(llm).distill("有些文本", "阿Q")

        # 2 次而非 3 次：确定性终态当场中止，不白烧 Attempt 3 —— 重试对 content_filter 无用。
        assert llm.chat.call_count == 2
        assert (llm_error_payload(ei.value) or {}).get("kind") == "incomplete:content_filter", \
            "该按 content_filter 分档原样上抛，而不是包成 DistillError"
        assert "超长被截断" not in str(ei.value), "真实原因是内容被过滤，不是超长"
        assert "超长被截断" not in ei.value.user_message

    def test_empty_length_repair_is_transient_and_reaches_attempt_3(self):
        """空正文的 `length` 终态 = 瞬时失败：重修环不提前上抛，仍走 Attempt 3。

        `length` 那一格有两种：有正文（有可修的东西，走截断支）与**一个字都没生成**
        （没得修，但也不是确定性结论）。后者原先和 content_filter 一起被
        `if incomplete_response_info(exc) is not None: raise` 拦在 Attempt 2，于是环
        只用 2 次就上抛——上游这次碰巧吐空正文（限流夹带的一次空响应、上游抖动），
        用户却直接拿到失败，连第 3 次尝试的机会都没有。

        变异：把两处 `except` 判据改回 `incomplete_response_info(exc) is not None` →
        本用例红（第 2 次就抛 IncompleteResponseError，`distill` 拿不到卡）。
        """
        llm = _llm(side_effect=[_truncated(PARTIAL), _incomplete("length"), FULL])
        card = Distiller(llm).distill("有些文本", "阿Q")

        assert card.name == "阿Q"
        assert llm.chat.call_count == 3, (
            f"空 length 该按瞬时失败继续到 Attempt 3，实得 {llm.chat.call_count} 次"
        )

    def test_empty_length_exhausted_still_reports_truncation_not_format(self):
        """三次都用尽、且首轮没有截断信号时，上屏仍须是「超长被截断」。

        首轮故意给 SHAPE_BAD（合法 JSON 但缺字段）：此时 `truncated` 不会由上游信号
        预置，只能靠重修两轮里读到的 `length` 补上。补不上就会把「上游说过 length」
        这条排障线索换成「格式异常」——同一份失败，两个方向相反的处理建议。
        """
        llm = _llm(side_effect=[
            SHAPE_BAD,
            _incomplete("length"),
            _incomplete("length"),
        ])
        with pytest.raises(ValueError) as ei:
            Distiller(llm).distill("有些文本", "阿Q")

        assert llm.chat.call_count == 3
        screen = ei.value.user_message
        assert "超长被截断" in screen and "格式异常" not in screen, (
            f"上游两次报 length，上屏却是：{screen!r}"
        )

    def test_shape_branch_beats_truncation_branch_when_both_signals_present(self):
        """O3：上游已确定截断 + 回的 JSON 合法但缺字段 → 重修走 **schema 支**。

        两支都「能解释」这次失败，但只有 schema 支能告诉 LLM 具体缺哪个字段；截断支
        让它再精简一遍是假话（这份输出根本没被截断）。两支对调在重构前后都是静默的：
        不报错，只换文案。

        变异：给 `last_bad_shape_reply is not None` 那支加上 `and not truncated`（对调）
        → 本用例红（第 2 次的 prompt 变成截断专用）。
        """
        llm = _llm(side_effect=[_truncated(SHAPE_BAD), FULL])
        card = Distiller(llm).distill("有些文本", "阿Q")

        assert card.name == "阿Q"
        assert llm.chat.call_count == 2, "第 2 次是重修，不是重跑整轮"
        assert SCHEMA_PROMPT_MARK in _prompts(llm)[1], (
            f"第 2 次该走 schema 支：{_prompts(llm)[1]!r}"
        )
        assert TRUNCATION_PROMPT_MARK not in _prompts(llm)[1]


class TestMapStageUnchanged:
    async def test_map_chunk_truncation_lands_empty_string(self):
        """map 阶段（async_chat）不受牵连：截断 → 该片掉进 failures、结果空串。

        这条链的恢复是「整片重算 + checkpoint/续跑」，不是重修环——本轮的改动不该
        碰它。截断在此仍是确定失败（Tier 1 的效果），只是承接方是 map 自己的空串协议。
        """
        llm = MagicMock()

        async def _boom(*a, **kw):
            raise IncompleteResponseError("length", "async_chat", content="半截分析")

        llm.async_chat = _boom
        d = Distiller(llm)

        def _build_prompt(chunk: str) -> tuple[str, str]:
            return Distiller._map_system_prompt("阿Q"), Distiller._map_user_prompt(chunk, "阿Q")

        results, failures = await d._run_map_concurrent(
            ["片段一"], _build_prompt, "distill_map")

        assert results == [(0, "")]
        assert len(failures) == 1
        assert isinstance(failures[0][1], IncompleteResponseError)
