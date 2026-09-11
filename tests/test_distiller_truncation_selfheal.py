# -*- coding: utf-8 -*-
"""同步 chat 的截断自愈：上游确定信号（finish_reason=length）必须接回重修环。

背景：Tier 1 给同步 chat 加了 finish_reason 裁决后，length 直接抛
IncompleteResponseError，半截文本永远到不了 core/distiller.py 的
_parse_json_with_retry——为截断建的「请精简输出」重修环，主触发路径不可达。
本文件锁四件事：
  1. 确定信号接回了环（`_chat_initial` 把半截正文交出去，Attempt 2 走截断专用修复）
  2. 厂商不回 finish_reason 时，`_looks_truncated` 从文本形状猜的老路径没被取代
  3. 重修上限仍是 3 次尝试，到顶后抛的是「超长被截断」而非「格式异常」
  4. map 阶段（`async_chat` 那条链）不受牵连

生产侧的边界锁另有一份：`core/` 不得出现异常类名，见
tests/test_chat_stream_error.py::test_no_exception_class_leaks_into_core_web_storage。
本文件在 tests/ 下，可以直接构造该异常（与 test_llm_adapter_finish_reason.py 同）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from adapters.llm_adapter import IncompleteResponseError, _extract_content
from core.distiller import Distiller

PARTIAL = '{"name": "阿'   # 被 max_tokens 截断的半截角色卡
FULL = '{"name": "阿Q"}'   # 重修后的完整角色卡（CharacterCard 只有 name 是必填）
TRUNCATION_PROMPT_MARK = "因长度超限被截断"


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


def _truncated() -> IncompleteResponseError:
    """经**真实**的 _extract_content 造异常，不直接构造。

    直接 `IncompleteResponseError("length", "chat", content=...)` 会绕过 content 的
    传递链，于是「去掉 content 传递」的变异不会红——那是个测不出东西的用例。走真实
    提取点才让这个变异可判定（同 test_llm_adapter_finish_reason.py 的取向）。
    """
    with pytest.raises(IncompleteResponseError) as ei:
        _extract_content(_Choice("length", PARTIAL), where="chat")
    return ei.value


def _prompts(llm: MagicMock) -> list[str]:
    """每次 chat 调用的 system prompt（第 0 个位置参数）。"""
    return [c.args[0] for c in llm.chat.call_args_list]


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
        """上限：连续截断到 3 次尝试用尽 → 抛，且文案是「超长被截断」。

        mock 故意给 4 个值、第 4 个是合法 JSON：这样「把上限改成无限」的变异会走到
        第 4 次并成功返回，断言「抛出」立刻红——变异可判定，不是挂死。
        """
        llm = _llm(side_effect=[_truncated(), _truncated(), _truncated(), FULL])
        with pytest.raises(ValueError) as ei:
            Distiller(llm).distill("有些文本", "阿Q")

        assert llm.chat.call_count == 3        # 上限仍是 3 次尝试，没多烧第 4 次
        msg = str(ei.value)
        assert "超长被截断" in msg and "max_tokens" in msg
        assert "格式异常" not in msg


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

        results, failures = await d._run_map_concurrent(["片段一"], "阿Q")

        assert results == [(0, "")]
        assert len(failures) == 1
        assert isinstance(failures[0][1], IncompleteResponseError)
