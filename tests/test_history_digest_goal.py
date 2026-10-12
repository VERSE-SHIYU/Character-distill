"""目标检查：历史摘要块（P13，spec `docs/specs/history-digest.md`）。

目标：窗口外满 6 轮的旧轮按块写成摘要，写完不重写，总量设 token 上限，注入在历史窗口之前。
main 上没有 `core/history_digest.py`，整个文件在 main 上是收集错误（红）。

分两层：
- D1–D4、D6、D5a —— 模块纯函数（`core/history_digest`）；
- D5b —— 真引擎 + 真 PG：连续 3 次摘要调用失败后本会话不再调用（存储检查只在 PG 上跑）。
"""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import pathlib
import uuid

import pytest
from pwdlib import PasswordHash

from conftest import PG_ENV, TEST_DATABASE_URL, main_loop_registered
from core import history_digest as hd
from core.affinity_protocol import FIELD_DELTA, FIELD_EVENT, FIELD_TIER
from core.chat_engine import ChatEngine
from core.schema import CharacterCard, PsycheProfile
from core.tokens import count_tokens
from storage.postgres_store import PostgresStore

USER_SAYS = "我叫林小满，在杭州读研究生，下个月要去北京面试"
CHAR_SAYS = "原来你在杭州读书啊，我也去过西湖"
_PW = PasswordHash.recommended().hash("Pass1234")


# ═══ 替身与构造 ══════════════════════════════════════════════════════════════

def _turns(n: int) -> list[list[dict]]:
    """n 轮，每轮 [user, assistant]。"""
    return [
        [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
        for i in range(n)
    ]


def _flat(n: int) -> list[dict]:
    """n 轮的扁平历史（窗口起点按偶数下标算）。"""
    return [m for turn in _turns(n) for m in turn]


def _digests(*texts: str) -> hd.Digests:
    return hd.Digests(tuple(
        hd.Digest(from_turn=i * hd.BLOCK_TURNS, to_turn=(i + 1) * hd.BLOCK_TURNS, text=t)
        for i, t in enumerate(texts)
    ))


# ═══ D1：满一块才调用 ═════════════════════════════════════════════════════════

def test_D1_only_a_full_block_is_digested():
    """窗口外满 BLOCK_TURNS 轮才产出一块；不足返回 None（不调用）。"""
    assert hd.BLOCK_TURNS == 6
    five = hd.pending_turns(_flat(6), window_start=10, digests=hd.Digests())   # 5 轮在窗口外
    six = hd.pending_turns(_flat(8), window_start=12, digests=hd.Digests())    # 6 轮在窗口外
    assert len(five) == 5 and len(six) == 6
    assert hd.next_block(five) is None
    block = hd.next_block(six)
    assert block is not None and len(block) == 6
    assert block[0][0]["content"] == "u0" and block[-1][1]["content"] == "a5"


# ═══ D2：写完不重写 ═══════════════════════════════════════════════════════════

def test_D2_already_digested_turns_are_not_pending():
    """已摘要的轮不再进 `pending_turns`（写完不重写）。"""
    done = _digests("第一块")                                   # 盖了 0–5 轮
    assert hd.pending_turns(_flat(8), window_start=12, digests=done) == []
    pending = hd.pending_turns(_flat(10), window_start=16, digests=done)   # 8 轮在窗口外，盖了 6
    assert [turn[0]["content"] for turn in pending] == ["u6", "u7"]
    assert hd.pending_turns(_flat(10), window_start=16, digests=done) == \
        hd.pending_turns(_flat(10), window_start=16, digests=done)


# ═══ D3：注入块上限 ═══════════════════════════════════════════════════════════

def test_D3_render_block_drops_the_oldest_over_budget():
    """总量超 token 上限丢最旧、保最新；无摘要 → 空串。"""
    assert hd.render_block(hd.Digests()) == ""
    old, new = "旧" * 400, "新" * 400
    budget = count_tokens(new) + 10                             # 只装得下最新一块
    block = hd.render_block(_digests(old, new), budget=budget)
    assert "新" in block and "旧" not in block and block.startswith("\n\n")
    assert hd.BLOCK_TITLE in block
    whole = hd.render_block(_digests(old, new), budget=count_tokens(old + new) + 10)
    assert "新" in whole and "旧" in whole                      # 装得下就都在


# ═══ D4：解析 ═════════════════════════════════════════════════════════════════

def test_D4_parse_strips_analysis_and_takes_summary():
    assert hd.parse("<analysis>草稿</analysis><summary>正文</summary>") == "正文"
    assert hd.parse("<summary>只有正文</summary>") == "只有正文"
    for bad in ("没有标签", "<analysis>只有草稿</analysis>", ""):
        with pytest.raises(ValueError):
            hd.parse(bad)


# ═══ D5a：失败不改状态（纯）══════════════════════════════════════════════════

def test_D5a_extract_appends_one_block_and_failure_leaves_digests_unchanged():
    """成功追加一块（轮区间正确）；调用失败 / 回复不可解析 → 抛错，digests 不动。"""
    block = _turns(hd.BLOCK_TURNS)
    out = hd.extract(hd.Digests(), block, char_name="角色",
                     call=lambda s, u: "<analysis>x</analysis><summary>摘要</summary>")
    assert len(out.items) == 1
    assert (out.items[0].from_turn, out.items[0].to_turn, out.items[0].text) == (0, 6, "摘要")

    def _boom(_s, _u):
        raise RuntimeError("digest call failed")

    before = _digests("已有的", "更多")
    snapshot = copy.deepcopy(before)
    for bad in (_boom, lambda s, u: "没有标签"):
        with pytest.raises(Exception):
            hd.extract(before, block, char_name="角色", call=bad)
        assert before == snapshot                              # 失败不改 digests


# ═══ D6：结构锁 ═══════════════════════════════════════════════════════════════

def test_D6_module_does_not_import_chat_engine_or_storage():
    """结构锁：模块不 import chat_engine / storage（同 session_record，全规则在模块内）。"""
    src = pathlib.Path(hd.__file__).read_text(encoding="utf-8")
    roots: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert not (roots & {"chat_engine", "storage", "web"}), roots


# ═══ D8：角色开场白不算一轮（按角色配对，不按位置）═══════════════════════════

def test_D8_opening_line_is_not_a_turn():
    """历史以角色开场白开头时按角色配对：每轮都是 [user, assistant]，开场白不属于任何一轮。"""
    opening = {"role": "assistant", "content": "开场白"}
    history = [opening] + [m for turn in _turns(2) for m in turn]      # 开场白 + 2 轮
    pending = hd.pending_turns(history, window_start=len(history), digests=hd.Digests())
    assert [t[0]["content"] for t in pending] == ["u0", "u1"]          # 配的是 user，不是开场白
    assert all(t[0]["role"] == "user" and t[1]["role"] == "assistant" for t in pending)
    assert all(opening not in t for t in pending)                      # 开场白不属于任何一轮
    # 窗口起点落在某轮中间（assistant 下标）时，那一轮不算窗口外
    assert hd.pending_turns(history, window_start=2, digests=hd.Digests()) == []
    # 边界：只有开场白、还没有用户发言
    assert hd.pending_turns([opening], window_start=1, digests=hd.Digests()) == []


# ═══ D5b：连续 3 次失败后本会话不再调用（真引擎 + 真 PG）═══════════════════════

_EVAL_SYS = "你是精确的JSON输出器"
_RECORD_SYS = "You are a character state analyst"
_DIGEST_SYS = "You are a conversation summarizer"


def _verdict() -> dict:
    return {FIELD_EVENT: "friendly", FIELD_TIER: "small", FIELD_DELTA: 2,
            "trust": 32, "guard": 68, "mood": "平静", "inner_voice": "嗯", "importance": 5}


class _FailDigestLLM:
    """好感评估、本场记录照常；摘要调用抛错。数一数摘要被调了几次。"""
    model = "stub"

    def __init__(self):
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.digest_calls: list[tuple[str, str]] = []

    def preflight(self):
        return None

    def derive(self, **_kw):
        return self

    def chat(self, system, messages, **_kw):
        if system.startswith(_DIGEST_SYS):
            self.digest_calls.append((system, messages[0]["content"]))
            raise RuntimeError("digest call failed")
        if system.startswith(_RECORD_SYS):
            return "{}"
        if system.startswith(_EVAL_SYS):
            return json.dumps(_verdict(), ensure_ascii=False)
        return "回复"


_pg = PG_ENV.skipif("PG 历史摘要块目标检查")
CARD = CharacterCard(name="角色", identity="历史摘要块检查用角色")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def _seed(store: PostgresStore) -> tuple[str, str]:
    uid = _uid("u")
    await store.create_user(uid, _uid("n"), _PW)
    tid = _uid("t")
    await store.save_text(tid, "src.txt", "正文", user_id=uid)
    cid = _uid("c")
    assert await store.save_card(cid, tid, "角色", '{"name": "角色"}', uid)
    sid = uuid.uuid4().hex[:12]
    await store.save_session(sid, cid, "user", "", user_id=uid)
    return cid, sid


@_pg
async def test_D5b_three_consecutive_failures_stop_automatic_digest(monkeypatch):
    """摘要调用连续失败 3 次后，本会话不再调用；失败期间 digests 不变、注入块为空、对话照常。"""
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    try:
        cid, sid = await _seed(store)
        llm = _FailDigestLLM()
        eng = ChatEngine(llm=llm, rag=None, card=CARD, card_id=cid, storage=store,
                         session_id=sid, is_new_session=False)
        eng.history = _flat(hd.BLOCK_TURNS)                    # 6 轮
        monkeypatch.setattr(eng, "_window_start", lambda h: 12)   # 6 轮全在窗口外
        async with main_loop_registered():
            for _ in range(4):
                await asyncio.to_thread(eng._post_turn, USER_SAYS, CHAR_SAYS)
        assert len(llm.digest_calls) == hd.MAX_CONSECUTIVE_FAILURES, llm.digest_calls
        assert eng._digests.items == (), "失败的这一轮把摘要状态改了"
        assert eng._build_history_digest_block() == ""
    finally:
        await store.close()
