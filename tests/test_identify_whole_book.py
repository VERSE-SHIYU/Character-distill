# -*- coding: utf-8 -*-
"""识别覆盖全书：分片 Map + 合并，失败率判据，合并走流式。

缺陷形态：``identify_characters`` 取 ``text[:10000]`` —— 红楼梦这类长篇只覆盖头两章，
名单天然残缺，而残缺名单会被落库、被所有下游当成全书名单用。本文件锁五件事：

  1. 只在**最后一个分片**出现的角色也进名单，且**每个分片都被送进了识别**
     （变异对象 = 恢复 ``excerpt = text[:10000]``：单次调用、末章角色丢失 → 红）
  2. 单分片**不合并**（`_identify_merge` 不被调用）——短文本的调用形态与改前一致
  3. 合并调用走 ``chat_stream``（非流式生成有 45s/60s 墙钟上限，长输出必然撞墙）
  4. 分片失败率越过容忍上限即抛；**解析失败与调用失败同权**计
  5. 重修重试也不许退回非流式 ``chat``（否则重修那两次还是撞墙）
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.distiller import (
    _IDENTIFY_CACHE,
    IDENTIFY_MERGE_PROMPT,
    DistillError,
    Distiller,
)

FILLER = "甲" * 2000          # chunk_size=3000 → 一段一片
TAIL = "孔明在末章登场"        # 只在最后一分片出现的角色
# 6 段填充（12000+ 字）+ 末段 → 6 个分片；末段落在 10000 字之后，正是被
# `text[:10000]` 砍掉的那一段
WHOLE_BOOK_TAIL_CHARS = "\n\n".join([FILLER] * 6 + [TAIL])

KONGMING = {"name": "孔明", "aliases": ["诸葛亮"], "importance": "主要", "reason": "末章登场"}


def _chars_json(items) -> str:
    return json.dumps(items, ensure_ascii=False)


def _client_stub() -> MagicMock:
    """真实的 ``_make_async_client()`` 返回 AsyncOpenAI，close() 是**协程**。

    桩必须和真实接口同形：裸 MagicMock 的 close 是同步方法，``await client.close()``
    会 TypeError，考的就成了 mock 的瑕疵。
    """
    async def _close() -> None:
        return None

    client = MagicMock()
    client.close = _close
    return client


def _make_llm(async_chat=None, chat_stream=None, chat=None) -> MagicMock:
    llm = MagicMock()
    llm.model = "test-model"
    llm.last_usage = None
    llm._make_async_client = MagicMock(return_value=_client_stub())
    llm.async_chat = AsyncMock(side_effect=async_chat)
    llm.chat_stream = MagicMock(
        side_effect=chat_stream if chat_stream is not None else (lambda *a, **kw: iter([]))
    )
    llm.chat = MagicMock(side_effect=chat)
    return llm


def _make_distiller(llm) -> Distiller:
    d = Distiller(llm=llm, config_path=None)
    d._chunk_size = 3000
    return d


def _map_stub():
    """Map 桩：分片正文里出现末章名字才返回角色数组，其余分片返回空数组。"""
    async def async_chat(system, messages, max_tokens=None, **kwargs):
        content = messages[0]["content"]
        if "孔明" in content:
            return (_chars_json([KONGMING]), {"prompt_tokens": 1, "completion_tokens": 1})
        return ("[]", {"prompt_tokens": 1, "completion_tokens": 1})

    return async_chat


def _merge_stream(*, first=None):
    """合并桩：``chat_stream`` 的 side_effect（返回迭代器，模拟增量）。

    ``first`` 给定时，第一次调用吐它（用于考「解析失败后的重修」）。
    """
    payload = _chars_json([KONGMING])
    replies = list(first) if first is not None else []

    def chat_stream(system, messages, max_tokens=None, **kwargs):
        return iter([replies.pop(0)] if replies else [payload])

    return chat_stream


class TestWholeBookCoverage:
    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_character_only_in_last_chunk_is_identified(self):
        """末章才登场的角色进名单 —— 且每个分片都送了识别，不是只送前 1 万字。"""
        llm = _make_llm(async_chat=_map_stub(), chat_stream=_merge_stream())
        d = _make_distiller(llm)

        result = d.identify_characters(WHOLE_BOOK_TAIL_CHARS)

        assert [c["name"] for c in result] == ["孔明"]
        # 6 个分片全送：变异「恢复 text[:10000]」会让这里变成 1，末章角色随之消失
        assert llm.async_chat.await_count == 6
        sent = [c.args[1][0]["content"] for c in llm.async_chat.await_args_list]
        assert any("孔明" in s for s in sent), "末章那一片没被送进识别"

    def test_single_chunk_does_not_merge(self):
        """单分片走原来那次调用（`chat`），不合并 —— 短文本形态与改前一致。"""
        llm = _make_llm(chat=lambda *a, **kw: _chars_json([KONGMING]))
        d = _make_distiller(llm)

        with patch.object(d, "_identify_merge") as mock_merge:
            result = d.identify_characters("短文本，只有一个分片")

        mock_merge.assert_not_called()
        assert llm.chat.call_count == 1
        assert llm.chat_stream.call_count == 0
        assert [c["name"] for c in result] == ["孔明"]

    def test_merge_call_uses_stream(self):
        """合并走 chat_stream：非流式生成墙钟装不下整本书的名单。"""
        llm = _make_llm(async_chat=_map_stub(), chat_stream=_merge_stream())
        d = _make_distiller(llm)

        d.identify_characters(WHOLE_BOOK_TAIL_CHARS)

        assert llm.chat_stream.call_count == 1
        assert llm.chat.call_count == 0, "合并这条路上不该出现非流式 chat"
        assert llm.chat_stream.call_args.args[0] == IDENTIFY_MERGE_PROMPT

    def test_merge_repair_also_uses_stream(self):
        """合并解析失败后的重修也不许退回非流式 chat（否则重修照样撞墙）。"""
        llm = _make_llm(
            async_chat=_map_stub(),
            chat_stream=_merge_stream(first=["不是 JSON"]),
            chat=lambda *a, **kw: pytest.fail("重修走了非流式 chat"),
        )
        d = _make_distiller(llm)

        result = d.identify_characters(WHOLE_BOOK_TAIL_CHARS)

        assert [c["name"] for c in result] == ["孔明"]
        assert llm.chat_stream.call_count == 2      # 初次 + 重修
        assert llm.chat.call_count == 0


class TestChunkFailurePolicy:
    """分片失败率判据（与 Map 阶段同一条：``_map_failure_exceeds_tolerance``）。"""

    CHUNKED = "\n\n".join(FILLER for _ in range(4))   # 4 个分片

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def _llm_with_failures(self, fail_count: int, *, parse_fail: bool = False):
        call = [0]

        async def async_chat(system, messages, max_tokens=None, **kwargs):
            call[0] += 1
            if call[0] <= fail_count:
                if parse_fail:
                    return ("这不是 JSON 数组", {"prompt_tokens": 1, "completion_tokens": 1})
                raise RuntimeError("connection timeout")
            return (_chars_json([KONGMING]), {"prompt_tokens": 1, "completion_tokens": 1})

        return _make_llm(async_chat=async_chat, chat_stream=_merge_stream())

    def test_failure_over_tolerance_raises(self):
        """4 片坏 3 片（75% > 50%）→ 抛，不拿半本书的名单当全书名单。"""
        llm = self._llm_with_failures(3)
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "识别失败" in excinfo.value.user_message
        assert "connection timeout" not in excinfo.value.user_message
        assert "connection timeout" in str(excinfo.value)   # 排障线索只进日志
        assert llm.chat_stream.call_count == 0              # 没走到合并

    def test_failure_within_tolerance_returns(self):
        """4 片坏 1 片（25% ≤ 50%）→ 正常返回合并结果。"""
        llm = self._llm_with_failures(1)
        d = _make_distiller(llm)

        result = d.identify_characters(self.CHUNKED)

        assert [c["name"] for c in result] == ["孔明"]
        assert llm.chat_stream.call_count == 1

    def test_unparseable_chunk_counts_as_failure(self):
        """**解析失败与调用失败同权**：3 片返回的不是数组 → 计入失败率 → 抛。

        变异对象 = 只数 ``failures``（调用异常）不数 ``parse_failed``：这里会静默
        按 1/4 的失败率继续，把三片没识别的书当全书名单合并出去。
        """
        llm = self._llm_with_failures(3, parse_fail=True)
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "识别失败" in excinfo.value.user_message
        assert llm.chat_stream.call_count == 0
