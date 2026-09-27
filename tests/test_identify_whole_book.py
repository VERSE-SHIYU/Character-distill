# -*- coding: utf-8 -*-
"""识别覆盖全书：分片 Map → 代码归组 → 全书判定（5 次独立采样取多数票），失败率判据。

缺陷形态：``identify_characters`` 取 ``text[:10000]`` —— 红楼梦这类长篇只覆盖头两章，
名单天然残缺，而残缺名单会被落库、被所有下游当成全书名单用。本文件锁六件事：

  1. 只在**最后一个分片**出现的角色也进名单，且**每个分片都被送进了识别**
     （变异对象 = 恢复 ``excerpt = text[:10000]``：单次调用、末章角色丢失 → 红）
  2. 单分片**不走全书判定**（走原来那一次 ``chat``）——短文本的调用形态与改前一致，
     主次原样保留模型给的 `importance`
  3. 全书判定独立调 **5 次**、并发（与逐片 Map 同一条 `_run_map_with_client` 骨架）；
     它的输入只有主名 / 别名 / 出现分片数，**没有 reason、没有正文**
  4. **任一分片失败即整体失败**（解析失败与调用失败同权计），且失败不进 memo ——
     名单会落库长期复用，半本书的名单会一直错下去。蒸馏那条容忍线（50%）不在这条路上
  5. 判定：执行器报错的调用与不合法样本都不计票，合法样本不足 3 份即整体失败，
     且失败不进 memo
  6. 别名至少两个字：单字称呼按子串匹配几乎命中每一片，两条路径都丢

归组、并组、判主次、取理由是纯计算，已在 `tests/test_roster_aggregate.py` 逐条锁过
（I3–I5）—— 这里只锁编排：调了几次、走的哪条通道、喂进去的是什么。
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.llm_adapter import IncompleteResponseError
from core.distiller import (
    _IDENTIFY_CACHE,
    IDENTIFY_JUDGE_PROMPT,
    IDENTIFY_SYSTEM_PROMPT,
    DistillError,
    Distiller,
)

FILLER = "甲" * 2000          # chunk_size=3000 → 一段一片
TAIL = "孔明在末章登场"        # 只在最后一分片出现的角色
# 6 段填充（12000+ 字）+ 末段 → 6 个分片；末段落在 10000 字之后，正是被
# `text[:10000]` 砍掉的那一段
WHOLE_BOOK_TAIL_CHARS = "\n\n".join([FILLER] * 6 + [TAIL])

KONGMING = {"name": "孔明", "aliases": ["诸葛亮"], "spoke": True, "reason": "末章登场"}

_USAGE = {"prompt_tokens": 1, "completion_tokens": 1}
#: 判定桩的「谁都不用并、没有泛称」—— 常见且正确的答案，与本文件绝大多数用例无关
JUDGE_NONE = '{"merge": [], "impersonal": []}'


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


def _make_llm(async_chat=None, chat=None) -> MagicMock:
    llm = MagicMock()
    llm.model = "test-model"
    llm.last_usage = None
    # 自适应并发闸的初值取「该账户已学到的上限」：新账号是 None（从 map_concurrency
    # 起）。MagicMock 的 `int()` 默认是 1，不显式置 None 会让闸一开始就收敛到 1 路。
    llm.learned_map_concurrency = None
    llm._make_async_client = MagicMock(return_value=_client_stub())
    llm.async_chat = AsyncMock(side_effect=async_chat)
    llm.chat = MagicMock(side_effect=chat)
    return llm


def _make_distiller(llm) -> Distiller:
    d = Distiller(llm=llm, config_path=None)
    d._chunk_size = 3000
    return d


def _stub(map_reply, judge=None):
    """逐片 Map 与全书判定**共用**一个 `async_chat` 桩。

    两条路都走 `async_chat`（判定经 `_run_map_with_client` 并发 5 次），靠 system
    提示词分路 —— 桩若不分路，判定会拿到 Map 的角色数组，5 份全不合法。``judge``
    给定时是一个 ``async (system, messages) -> (text, usage)``，缺省回空 merge。
    """
    async def async_chat(system, messages, max_tokens=None, **kwargs):
        if system == IDENTIFY_JUDGE_PROMPT:
            if judge is None:
                return (JUDGE_NONE, dict(_USAGE))
            return await judge(system, messages)
        return (map_reply(messages[0]["content"]), dict(_USAGE))

    return async_chat


def _systems(llm) -> list[str]:
    return [c.args[0] for c in llm.async_chat.await_args_list]


def _map_calls(llm) -> list:
    return [c for c in llm.async_chat.await_args_list if c.args[0] == IDENTIFY_SYSTEM_PROMPT]


def _judge_calls(llm) -> list:
    return [c for c in llm.async_chat.await_args_list if c.args[0] == IDENTIFY_JUDGE_PROMPT]


def _map_stub():
    """Map 桩：分片正文里出现末章名字才返回角色数组，其余分片返回空数组。"""
    return _stub(lambda content: _chars_json([KONGMING]) if "孔明" in content else "[]")


# 三片桩：每片一个角色，片内的「正文标记」与理由里的「理由标记」都不许出现在全书
# 判定的输入里 —— 输入若带了正文或 reason，这两个标记就会露出来。
_NAMES = ("阿尔法", "贝塔", "伽马")
_TEXT_MARKERS = ("正文标记一", "正文标记二", "正文标记三")
_REASON_MARKERS = ("理由标记一", "理由标记二", "理由标记三")
THREE_CHUNK_TEXT = "\n\n".join(FILLER + m for m in _TEXT_MARKERS)


def _three_char_map_stub():
    """Map 桩：按片内的正文标记各回一个角色（带理由标记与一个唯一别称）。"""
    def reply(content: str) -> str:
        for marker, name, reason in zip(_TEXT_MARKERS, _NAMES, _REASON_MARKERS):
            if marker in content:
                return _chars_json([{
                    "name": name, "aliases": [f"{name}别称"],
                    "spoke": True, "reason": reason,
                }])
        return "[]"

    return _stub(reply)


# 七片：甲在 6 片亲口说话、乙出现 7 片只在 1 片说话。每片带一个可分辨的标记，
# 桩据此知道该片有谁。
_CHUNK_TOKENS = tuple(f"片{i}号标记" for i in range(7))
SEVEN_CHUNK_TEXT = "\n\n".join(FILLER + t for t in _CHUNK_TOKENS)


def _seven_chunk_map_stub():
    """甲：6 片 `spoke: true`（主要）；乙：7 片里只有第 7 片说话（次要）。"""
    def people(i: int) -> list[dict]:
        out = []
        if i < 6:
            out.append({"name": "甲", "aliases": [], "spoke": True, "reason": f"甲@{i}"})
        out.append({"name": "乙", "aliases": [], "spoke": i == 6, "reason": f"乙@{i}"})
        return out

    def reply(content: str) -> str:
        for i, token in enumerate(_CHUNK_TOKENS):
            if token in content:
                return _chars_json(people(i))
        return "[]"

    return _stub(reply)


@pytest.fixture
def usage_rows(monkeypatch) -> list[tuple[str, dict | None]]:
    """收下每次记账的 (action, usage) —— `_try_record_usage` 的唯一被调下游。"""
    rows: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        "core.distiller.try_record_usage",
        lambda **kw: rows.append((kw["action"], kw["usage"])),
    )
    return rows


class TestWholeBookCoverage:
    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_character_only_in_last_chunk_is_identified(self):
        """末章才登场的角色进名单 —— 且每个分片都送了识别，不是只送前 1 万字。"""
        llm = _make_llm(async_chat=_map_stub())
        d = _make_distiller(llm)

        result = d.identify_characters(WHOLE_BOOK_TAIL_CHARS)

        # 名字、别名、主次、理由四件都在纯函数里走完全程（不是模型原样返回的那份）
        assert len(result) == 1
        assert result[0]["name"] == "孔明"
        assert result[0]["aliases"] == ["诸葛亮"]
        assert result[0]["importance"] == "次要", "只在 1 片说话，够不着 6 片的门槛"
        assert result[0]["speak_chunks"] == 1
        assert result[0]["reason"] == "末章登场"
        # 6 个分片全送：变异「恢复 text[:10000]」会让这里变成 1，末章角色随之消失
        sent = [c.args[1][0]["content"] for c in _map_calls(llm)]
        assert len(sent) == 6
        assert any("孔明" in s for s in sent), "末章那一片没被送进识别"

    def test_single_chunk_does_not_judge_groups(self):
        """单分片走原来那次调用（`chat`），不归组也不判组 —— 短文本形态与改前一致。"""
        llm = _make_llm(chat=lambda *a, **kw: _chars_json([KONGMING]))
        d = _make_distiller(llm)

        result = d.identify_characters("短文本，只有一个分片")

        assert llm.chat.call_count == 1
        assert llm.async_chat.await_count == 0, "单分片不该走到全书判定那一步"
        assert [c["name"] for c in result] == ["孔明"]


class TestJudgeInput:
    """全书判定的输入：只有主名 / 别名 / 出现分片数 —— 没有 reason、没有正文。"""

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_judge_sees_names_but_not_reason_or_text(self):
        """变异对象 = 把各片名单整包丢给模型（正文与理由标记都会露出来 → 红）。"""
        llm = _make_llm(async_chat=_three_char_map_stub())
        d = _make_distiller(llm)

        result = d.identify_characters(THREE_CHUNK_TEXT)

        calls = _judge_calls(llm)
        assert len(calls) == 5
        body = calls[0].args[1][0]["content"]
        assert not any(m in body for m in _TEXT_MARKERS), "正文不许进判定"
        assert not any(m in body for m in _REASON_MARKERS), "理由不许进判定"
        for name in _NAMES:
            assert name in body, "每组主名要进判定"
        assert body.count("出现 1 个分片") == 3, "每组出现分片数要进判定"
        # 三个组各在 1 片说话 → 全部够不着门槛，名单全靠纯函数算出来
        assert [c["name"] for c in result] == list(_NAMES)
        assert [c["reason"] for c in result] == list(_REASON_MARKERS)
        assert [c["aliases"] for c in result] == [[f"{n}别称"] for n in _NAMES]

    def test_all_five_samples_get_the_same_input(self):
        """同一输入独立采样 —— 采样的是模型，不是输入。"""
        llm = _make_llm(async_chat=_three_char_map_stub())
        d = _make_distiller(llm)

        d.identify_characters(THREE_CHUNK_TEXT)

        bodies = {c.args[1][0]["content"] for c in _judge_calls(llm)}
        assert len(bodies) == 1, "5 次的 user 内容必须完全相同"


class TestJudgeVoting:
    """执行器报错与不合法样本都不计票；合法样本不足 3 份即整体失败。"""

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    CHUNKED = "\n\n".join([FILLER] * 4)
    MAP = staticmethod(lambda content: _chars_json([KONGMING]))

    @staticmethod
    def _judge_with_replies(replies: list) -> "callable":
        """按调用序回放：元素是字符串就回它，是异常就抛。"""
        seq = list(replies)

        async def judge(system, messages):
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return (item, dict(_USAGE))

        return judge

    def test_two_valid_samples_are_not_enough(self):
        """5 份只有 2 份合法 → `DistillError`，且失败不进 memo。

        变异对象 = 拿少数样本当结论（这里会「成功」返回一份名单 → 红）。
        """
        llm = _make_llm(async_chat=_stub(
            self.MAP,
            judge=self._judge_with_replies([JUDGE_NONE, JUDGE_NONE, "[]", "[]", "[]"]),
        ))
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "识别失败" in excinfo.value.user_message
        assert "2/5" in str(excinfo.value)
        before = len(_map_calls(llm))
        assert before == 4

        with pytest.raises(DistillError):
            d.identify_characters(self.CHUNKED)
        assert len(_map_calls(llm)) == before + 4, "失败的识别不许进 memo"

    def test_four_of_five_valid_is_enough(self):
        """执行器失败 1 次、不合法 1 次、合法 3 次 → 恰好够门槛，正常出名单。"""
        llm = _make_llm(async_chat=_stub(
            self.MAP,
            judge=self._judge_with_replies([
                JUDGE_NONE,
                IncompleteResponseError("length", "chat", content=""),
                JUDGE_NONE,
                "[]",
                JUDGE_NONE,
            ]),
        ))
        d = _make_distiller(llm)

        assert [c["name"] for c in d.identify_characters(self.CHUNKED)] == ["孔明"]

    def test_three_executor_failures_raise(self):
        """5 份里 3 份执行器报错（含截断）→ 合法 2 份，不足门槛。"""
        llm = _make_llm(async_chat=_stub(
            self.MAP,
            judge=self._judge_with_replies([
                IncompleteResponseError("length", "chat", content=""),
                RuntimeError("connection timeout"),
                JUDGE_NONE,
                RuntimeError("connection timeout"),
                JUDGE_NONE,
            ]),
        ))
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "执行器失败 3 次" in str(excinfo.value)

    def test_malformed_samples_are_not_counted(self):
        """两键不都是数组、或根本不是 JSON 对象 → 不计票（这里带 1 个合法 → 抛）。"""
        llm = _make_llm(async_chat=_stub(
            self.MAP,
            judge=self._judge_with_replies([
                JUDGE_NONE, '{"merge": []}', '{"impersonal": []}', "不是 JSON", '[["甲","乙"]]',
            ]),
        ))
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "合法样本 1/5" in str(excinfo.value)


class TestJudgeConcurrency:
    """5 次判定并行发起 —— 串行会让整阶段多花 4 倍时间。"""

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_five_judge_calls_run_concurrently(self):
        """桩对判定每次睡 0.3 s：并行 → 约 0.3 s，串行 → 1.5 s。

        变异对象 = 判定改成 5 次顺序调用（耗时 ≥ 1.5 s → 红）。
        """
        async def judge(system, messages):
            await asyncio.sleep(0.3)
            return (JUDGE_NONE, dict(_USAGE))

        llm = _make_llm(async_chat=_stub(
            lambda content: _chars_json([KONGMING]), judge=judge))
        d = _make_distiller(llm)
        text = "\n\n".join([FILLER] * 4)

        start = time.monotonic()
        d.identify_characters(text)
        elapsed = time.monotonic() - start

        assert len(_judge_calls(llm)) == 5
        assert elapsed < 1.0, f"5 次判定没并行（耗时 {elapsed:.2f}s）"


class TestWholeChain:
    """从 `identify_characters` 入口走完整条链路：说话分片数定主次与顺序。"""

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_speaking_chunks_decide_rank_and_order(self, capsys):
        llm = _make_llm(async_chat=_seven_chunk_map_stub())
        d = _make_distiller(llm)

        result = d.identify_characters(SEVEN_CHUNK_TEXT)

        assert [(r["name"], r["speak_chunks"], r["importance"]) for r in result] == [
            ("甲", 6, "主要"), ("乙", 1, "次要")]
        systems = _systems(llm)
        assert systems.count(IDENTIFY_JUDGE_PROMPT) == 5
        assert systems.count(IDENTIFY_SYSTEM_PROMPT) == 7
        out = capsys.readouterr().out
        assert "[distill] identify judge merge=" in out
        assert "[distill] identify judge impersonal=" in out


class TestIdentifyUsageAccounting:
    """非空名单的一次识别记**恰好 2 行** `distill_identify`：逐片汇总一条、判定汇总一条。"""

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_non_empty_roster_records_map_and_judge_rows(self, usage_rows):
        llm = _make_llm(async_chat=_seven_chunk_map_stub())
        d = _make_distiller(llm)

        d.identify_characters(SEVEN_CHUNK_TEXT)

        actions = [a for a, _ in usage_rows]
        assert actions == ["distill_identify", "distill_identify"], usage_rows
        map_usage, judge_usage = usage_rows[0][1], usage_rows[1][1]
        assert map_usage["chunk_count"] == 7, "逐片那行数的是真的调了几次"
        assert judge_usage["chunk_count"] == 5, "判定 5 次汇总成一行"


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


class TestSingleChunkKeepsModelImportance:
    """单分片路径不判主次 —— 原样返回模型给的 `importance` 与那套键。

    多分片那套「说话分片数 ≥ 6」不适用于一段短文：全片就是一个场景，样本量为 1。
    """

    def setup_method(self):
        _IDENTIFY_CACHE.clear()

    def test_importance_is_the_model_answer(self):
        llm = _make_llm(chat=lambda *a, **kw: _chars_json([{
            "name": "宝玉", "aliases": [], "importance": "主要", "reason": "主角",
        }]))
        d = _make_distiller(llm)

        result = d.identify_characters("短文本，只有一个分片")

        assert result[0]["importance"] == "主要"
        assert llm.async_chat.await_count == 0


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
            if system == IDENTIFY_JUDGE_PROMPT:
                return (JUDGE_NONE, dict(_USAGE))
            call[0] += 1
            if call[0] <= fail_count:
                if parse_fail:
                    return ("这不是 JSON 数组", dict(_USAGE))
                raise RuntimeError("connection timeout")
            return (_chars_json([KONGMING]), dict(_USAGE))

        return _make_llm(async_chat=async_chat)

    def test_any_chunk_failure_raises(self):
        """4 片坏 1 片（25%）→ 抛。原先这条在容忍线内，会拿 3/4 本书的名单当真。"""
        llm = self._llm_with_failures(1)
        d = _make_distiller(llm)

        with pytest.raises(DistillError) as excinfo:
            d.identify_characters(self.CHUNKED)

        assert "识别失败" in excinfo.value.user_message
        assert "connection timeout" not in excinfo.value.user_message
        assert "connection timeout" in str(excinfo.value)   # 排障线索只进日志
        assert _judge_calls(llm) == []                      # 没走到判定

    def test_failure_is_not_memoized(self):
        """失败不进 memo：同文本再识别一次，Map 重新发起（下次可能就好了）。

        桩按**片内容**判失败（不按调用序号），所以两次请求都是同一片坏 —— 第二次
        若不是重新发起，它就会成功返回，`pytest.raises` 直接红。
        """
        async def async_chat(system, messages, max_tokens=None, **kwargs):
            if system == IDENTIFY_JUDGE_PROMPT:
                return (JUDGE_NONE, dict(_USAGE))
            if "坏片标记" in messages[0]["content"]:
                raise RuntimeError("connection timeout")
            return (_chars_json([KONGMING]), dict(_USAGE))

        llm = _make_llm(async_chat=async_chat)
        d = _make_distiller(llm)
        content = "\n\n".join(["坏片标记" + FILLER] + [FILLER] * 3)

        with pytest.raises(DistillError):
            d.identify_characters(content)
        assert len(_map_calls(llm)) == 4

        with pytest.raises(DistillError):
            d.identify_characters(content)
        assert len(_map_calls(llm)) == 8, "失败的识别不许进 memo"

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
        assert _judge_calls(llm) == []

    def test_429_gets_its_own_message(self):
        """429 专用文案：限流要与「片段处理失败」分开 —— 用户该等，不是该改内容。"""
        async def async_chat(system, messages, max_tokens=None, **kwargs):
            if system == IDENTIFY_JUDGE_PROMPT:
                return (JUDGE_NONE, dict(_USAGE))
            raise RuntimeError("API 429 rate limited")

        llm = _make_llm(async_chat=async_chat)
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
        """多分片：每一片都回合法的空数组 → 空名单，不抛、也不进判定。"""
        llm = _make_llm(async_chat=_stub(lambda content: "[]"))
        d = _make_distiller(llm)

        assert d.identify_characters("\n\n".join([FILLER] * 4)) == []
        assert _judge_calls(llm) == [], "没有可归组的条目，不该走到判定那一步"

    def test_empty_roster_is_cached_like_any_other_result(self):
        """空名单同样是**成功结果**，照样进 memo —— 失败才不进缓存。"""
        llm = _make_llm(async_chat=_stub(lambda content: "[]"))
        d = _make_distiller(llm)
        text = "\n\n".join([FILLER] * 4)

        assert d.identify_characters(text) == []
        assert d.identify_characters(text) == []
        assert len(_map_calls(llm)) == 4, "第二次该命中缓存，不该再发分片调用"
