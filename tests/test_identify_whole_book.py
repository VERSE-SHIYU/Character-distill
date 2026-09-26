# -*- coding: utf-8 -*-
"""识别覆盖全书：分片 Map → 代码归组 → 一次别名判断，失败率判据。

缺陷形态：``identify_characters`` 取 ``text[:10000]`` —— 红楼梦这类长篇只覆盖头两章，
名单天然残缺，而残缺名单会被落库、被所有下游当成全书名单用。本文件锁五件事：

  1. 只在**最后一个分片**出现的角色也进名单，且**每个分片都被送进了识别**
     （变异对象 = 恢复 ``excerpt = text[:10000]``：单次调用、末章角色丢失 → 红）
  2. 单分片**不判别名**（走原来那一次 ``chat``）——短文本的调用形态与改前一致
  3. 合并阶段只 **1 次**模型调用，走 ``chat_stream_long``（非流式的 45s/60s 墙钟
     装不下长输出，通道与逐片 Map 保持一致）；它的输入只有主名 / 别名 / 出现分片数，
     **没有 reason、没有正文**——判主次与取理由都是纯计算
  4. **任一分片失败即整体失败**（解析失败与调用失败同权计），且失败不进 memo ——
     名单会落库长期复用，半本书的名单会一直错下去。蒸馏那条容忍线（50%）不在这条路上
  5. 别名判断解析失败后的重修同样不许退回非流式 ``chat``
  6. 别名至少两个字：单字称呼按子串匹配几乎命中每一片，两条路径都丢

归组、并组、判主次、取理由是纯计算，已在 `tests/test_character_roster.py` 逐条锁过
（I3、I4）—— 这里只锁编排：调了几次、走的哪条通道、喂进去的是什么。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.distiller import _IDENTIFY_CACHE, DistillError, Distiller

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
    # 长输出入口在生产里是 chat_stream 的薄委托（只放宽读超时）：桩共用同一份记录，
    # 调用计数/参数断言因此对两条入口都成立。
    llm.chat_stream_long = llm.chat_stream
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


# 三片桩（I1 / I2 用）：每片一个角色，片内的「正文标记」与理由里的「理由标记」都不许
# 出现在别名判断的输入里 —— 输入若带了正文或 reason，这两个标记就会露出来。
_NAMES = ("阿尔法", "贝塔", "伽马")
_TEXT_MARKERS = ("正文标记一", "正文标记二", "正文标记三")
_REASON_MARKERS = ("理由标记一", "理由标记二", "理由标记三")
THREE_CHUNK_TEXT = "\n\n".join(FILLER + m for m in _TEXT_MARKERS)


def _three_char_map_stub():
    """Map 桩：按片内的正文标记各回一个角色（带理由标记与一个唯一别称）。"""
    async def async_chat(system, messages, max_tokens=None, **kwargs):
        content = messages[0]["content"]
        for marker, name, reason in zip(_TEXT_MARKERS, _NAMES, _REASON_MARKERS):
            if marker in content:
                return (_chars_json([{
                    "name": name, "aliases": [f"{name}别称"],
                    "importance": "主要", "reason": reason,
                }]), {"prompt_tokens": 1, "completion_tokens": 1})
        return ("[]", {"prompt_tokens": 1, "completion_tokens": 1})

    return async_chat


def _alias_stream(*, first=None):
    """别名判断桩：``chat_stream`` 的 side_effect（返回迭代器，模拟增量）。

    默认回合法空数组 —— 「这些组谁都不用并」是常见答案（`allow_empty` 就是为它开的）。
    ``first`` 给定时第一次调用吐它（用于考「解析失败后的重修」）。
    """
    replies = list(first) if first is not None else []

    def chat_stream(system, messages, max_tokens=None, **kwargs):
        return iter([replies.pop(0)] if replies else ["[]"])

    return chat_stream


class TestWholeBookCoverage:
    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_character_only_in_last_chunk_is_identified(self):
        """末章才登场的角色进名单 —— 且每个分片都送了识别，不是只送前 1 万字。"""
        llm = _make_llm(async_chat=_map_stub(), chat_stream=_alias_stream())
        d = _make_distiller(llm)

        result = d.identify_characters(WHOLE_BOOK_TAIL_CHARS)

        # 名字、别名、主次、理由四件都在纯函数里走完全程（不是模型原样返回的那份）
        assert len(result) == 1
        assert result[0]["name"] == "孔明"
        assert result[0]["aliases"] == ["诸葛亮"]
        assert result[0]["importance"] == "主要"
        assert result[0]["reason"] == "末章登场"
        # 6 个分片全送：变异「恢复 text[:10000]」会让这里变成 1，末章角色随之消失
        assert llm.async_chat.await_count == 6
        sent = [c.args[1][0]["content"] for c in llm.async_chat.await_args_list]
        assert any("孔明" in s for s in sent), "末章那一片没被送进识别"

    def test_single_chunk_does_not_merge(self):
        """单分片走原来那次调用（`chat`），不归组也不判别名 —— 短文本形态与改前一致。"""
        llm = _make_llm(chat=lambda *a, **kw: _chars_json([KONGMING]))
        d = _make_distiller(llm)

        result = d.identify_characters("短文本，只有一个分片")

        assert llm.chat.call_count == 1
        assert llm.chat_stream.call_count == 0, "单分片不该走到别名判断那一步"
        assert [c["name"] for c in result] == ["孔明"]

    def test_alias_judgement_is_one_call_without_reason_or_text(self):
        """合并阶段只 1 次模型调用，输入只有主名 / 别名 / 出现分片数。

        变异对象 = 恢复 main 上的 `_identify_merge`（把各片名单整包丟给模型重写）：
        调用次数与输入内容两条断言都会红。
        """
        llm = _make_llm(async_chat=_three_char_map_stub(), chat_stream=_alias_stream())
        d = _make_distiller(llm)

        result = d.identify_characters(THREE_CHUNK_TEXT)

        assert llm.chat_stream.call_count == 1
        assert llm.chat.call_count == 0, "别名判断这条路上不该出现非流式 chat"
        body = llm.chat_stream.call_args.args[1][0]["content"]
        assert not any(m in body for m in _TEXT_MARKERS), "正文不许进别名判断"
        assert not any(m in body for m in _REASON_MARKERS), "理由不许进别名判断"
        for name in _NAMES:
            assert name in body, "每组主名要进别名判断"
        assert body.count("出现 1 个分片") == 3, "每组出现分片数要进别名判断"
        # 三个组各在 1 片、各被判主要一次 → 名单全靠纯函数算出来，不是模型原样返回
        assert [c["name"] for c in result] == list(_NAMES)
        assert [c["reason"] for c in result] == list(_REASON_MARKERS)
        assert [c["aliases"] for c in result] == [[f"{n}别称"] for n in _NAMES]

    def test_alias_judgement_repair_also_streams(self):
        """别名判断解析失败后的重修也不许退回非流式 chat（否则重修照样撞墙）。

        变异对象 = 把重修改成 `stream=False`：`chat_stream.call_count` 变 1 且
        `chat` 被调到 → 红。
        """
        llm = _make_llm(
            async_chat=_three_char_map_stub(),
            chat_stream=_alias_stream(first=["不是 JSON"]),
            chat=lambda *a, **kw: pytest.fail("重修走了非流式 chat"),
        )
        d = _make_distiller(llm)

        result = d.identify_characters(THREE_CHUNK_TEXT)

        assert [c["name"] for c in result] == list(_NAMES)
        assert llm.chat_stream.call_count == 2      # 初次 + 重修
        assert llm.chat.call_count == 0


class TestAliasLength:
    """单分片路径同样丢单字别名 —— 判据只写一处（`roster_aggregate.usable_alias`），
    多分片汇总（`_observations`）与单分片（`_normalize_identify_items`）都调它。

    单分片那条路不经过归组，只走 `_parse_identify_list` → `_normalize_identify_items`；
    只在纯函数那条路径上判，这里就会漏 —— 短文本的名单带着单字称呼落库。
    """

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_single_chunk_drops_single_char_aliases(self):
        llm = _make_llm(chat=lambda *a, **kw: _chars_json([{
            "name": "宝玉", "aliases": ["他", "玉", "宝二爷"],
            "importance": "主要", "reason": "主角",
        }]))
        d = _make_distiller(llm)

        result = d.identify_characters("短文本，只有一个分片")

        assert result[0]["aliases"] == ["宝二爷"]


class TestChunkFailurePolicy:
    """识别路径**不容忍**分片失败 —— 任一片坏即整体失败。

    与蒸馏的分界：蒸馏有续跑兜底（checkpoint + 重跑合并与格式化），失败代价小；
    识别没有，且它的产物经 `resolve_characters` → `save_characters` **落库长期复用**
    （按文本 + 版本缓存）—— 用半本书建的名单会一直错下去，且下游全部当真。
    实测（识别验收，2026-09-26）：121/242 片失败恰在 50% 容忍线内，名单是半本书建的，
    妙玉因此被判次要。所以判据不是「等号算不算越线」，是**识别这条路不许有容忍线**。

    变异对象 = ① 恢复 50% 容忍（4 片坏 1 片就继续 → 红）
              ② 失败时仍写 memo（第二次识别命中缓存、Map 不再发起 → 红）
    """

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

        return _make_llm(async_chat=async_chat, chat_stream=_alias_stream())

    def test_any_chunk_failure_raises(self):
        """4 片坏 1 片（25%）→ 抛。原先这条在容忍线内，会拿 3/4 本书的名单当真。"""
        llm = self._llm_with_failures(1)
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "识别失败" in excinfo.value.user_message
        assert "connection timeout" not in excinfo.value.user_message
        assert "connection timeout" in str(excinfo.value)   # 排障线索只进日志
        assert llm.chat_stream.call_count == 0              # 没走到合并

    def test_failure_is_not_memoized(self):
        """失败不进 memo：同文本再识别一次，Map 重新发起（下次可能就好了）。

        桩按**片内容**判失败（不按调用序号），所以两次请求都是同一片坏 —— 第二次
        若不是重新发起，它就会成功返回，`pytest.raises` 直接红。
        """
        async def async_chat(system, messages, max_tokens=None, **kwargs):
            if "坏片标记" in messages[0]["content"]:
                raise RuntimeError("connection timeout")
            return (_chars_json([KONGMING]), {"prompt_tokens": 1, "completion_tokens": 1})

        llm = _make_llm(async_chat=async_chat, chat_stream=_alias_stream())
        d = _make_distiller(llm)
        content = "\n\n".join(["坏片标记" + FILLER] + [FILLER] * 3)

        with pytest.raises(DistillError):
            d.identify_characters(content)
        assert llm.async_chat.await_count == 4

        with pytest.raises(DistillError):
            d.identify_characters(content)
        assert llm.async_chat.await_count == 8, "失败的识别不许进 memo"

    def test_unparseable_chunk_counts_as_failure(self):
        """**解析失败与调用失败同权**：1 片返回的不是数组 → 同样整体失败。

        变异对象 = 只数 ``failures``（调用异常）不数 ``parse_failed``：这一片会静默
        按「没角色」算，把没识别的书当全书名单交出去。
        """
        llm = self._llm_with_failures(1, parse_fail=True)
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "识别失败" in excinfo.value.user_message
        assert llm.chat_stream.call_count == 0

    def test_429_gets_its_own_message(self):
        """429 专用文案：限流要与「片段处理失败」分开 —— 用户该等，不是该改内容。"""
        async def async_chat(system, messages, max_tokens=None, **kwargs):
            raise RuntimeError("API 429 rate limited")

        llm = _make_llm(async_chat=async_chat, chat_stream=_alias_stream())
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "限流" in excinfo.value.user_message
        assert "429" in str(excinfo.value)


class TestEmptyRosterIsNotFailure:
    """名单为空是**合法的识别结果**，不是识别失败 —— 两条路径同口径。

    「这本书没有具名角色」与「识别不出来」原先被两条相反的规则各判了一半：
    单分片把解析失败降级成空名单，多分片把真空名单升级成失败。本组把两者钉在
    同一口径上：**空名单是结果，失败才抛**。

    变异对象 = 恢复 ``_identify_over_chunks`` 末尾的 `if not parts: raise`：
    多分片那条变红。单分片那条守 ``_identify_single_call`` 不把合法空数组当解析失败。

    与 ``TestChunkFailurePolicy`` 的分界：那一组考「片坏了怎么办」（任一片坏即抛），
    本组考「片全好但确实没人」（失败判据不该被真空名单触发）。
    """

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_single_chunk_empty_array_is_an_empty_roster(self):
        """单分片：模型回合法的空数组 → 空名单，不抛。"""
        llm = _make_llm(chat=lambda *a, **kw: "[]")
        d = _make_distiller(llm)

        assert d.identify_characters("短文本，只有一个分片") == []

    def test_all_chunks_empty_array_is_an_empty_roster(self):
        """多分片：每一片都回合法的空数组 → 空名单，不抛、也不进合并。"""
        async def async_chat(system, messages, max_tokens=None, **kwargs):
            return ("[]", {"prompt_tokens": 1, "completion_tokens": 1})

        llm = _make_llm(async_chat=async_chat)
        d = _make_distiller(llm)

        assert d.identify_characters("\n\n".join([FILLER] * 4)) == []
        assert llm.chat_stream.call_count == 0, "没有可归组的条目，不该走到别名判断那一步"

    def test_empty_roster_is_cached_like_any_other_result(self):
        """空名单同样是**成功结果**，照样进 memo —— 失败才不进缓存。"""
        async def async_chat(system, messages, max_tokens=None, **kwargs):
            return ("[]", {"prompt_tokens": 1, "completion_tokens": 1})

        llm = _make_llm(async_chat=async_chat)
        d = _make_distiller(llm)
        text = "\n\n".join([FILLER] * 4)

        assert d.identify_characters(text) == []
        assert d.identify_characters(text) == []
        assert llm.async_chat.call_count == 4, "第二次该命中缓存，不该再发分片调用"
