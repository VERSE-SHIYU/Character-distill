"""目标检查：一对一存档的本场记录（④ 段 1，spec `docs/specs/session-record.md` §5）。

目标：每个一对一存档有自己的本场记录；每轮单独一次抽取调用（spec `session-record-extract.md`）；有效条目注入提示词动态区；
不显示给用户；群聊不受影响。存储相关的检查只在 PG 上跑（线上只用 PG）。

分三层：
- R* —— 规则（`core/session_record.apply` / `render_block`，纯函数）；
- P* —— 评估流水线：本场记录是单独一次抽取调用，和好感各是各的失败域；群聊不变；只在有变化时落库；
- X* —— 抽取提示词：照抄文献的句子一字不差、第三人称；
- E* —— 真引擎 + 真 PG：存档之间隔离、恢复后接得上、读好感接口和会话行都不带它、
  每轮一个保存点。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

import pytest
from pwdlib import PasswordHash

from conftest import PG_ENV, TEST_DATABASE_URL, main_loop_registered
from core import session_record as rec
from core.affinity_protocol import FIELD_DELTA, FIELD_EVENT, FIELD_TIER
from core.affinity_service import AffinityService, resolve_session_affinity
from core.chat_engine import ChatEngine
from core.evaluation_pipeline import EvalContext, EvaluationPipeline
from core.schema import CharacterCard, PsycheProfile
from core.session_record import SessionRecord
from core.session_restore import restore_engine_state
from storage.postgres_store import PostgresStore

USER_SAYS = "我叫林小满，在杭州读研究生，下个月要去北京面试"
CHAR_SAYS = "原来你在杭州读书啊，我也去过西湖"
_PW = PasswordHash.recommended().hash("Pass1234")
_EVAL_SYS = "你是精确的JSON输出器"


def _verdict(**extra) -> dict:
    return {FIELD_EVENT: "friendly", FIELD_TIER: "small", FIELD_DELTA: 2,
            "trust": 32, "guard": 68, "mood": "平静", "inner_voice": "嗯", "importance": 5, **extra}


def _add(col: str, text: str, evidence: str) -> dict:
    return {"col": col, "text": text, "evidence": evidence}


def _apply(record: SessionRecord, *ops):
    return rec.apply(record, {rec.FIELD_OPS: list(ops)}, USER_SAYS, CHAR_SAYS)


def _sup(target, col: str, text: str, evidence: str) -> dict:
    """一项「取代 #target」的操作。"""
    return {**_add(col, text, evidence), rec.FIELD_SUPERSEDES: target}


def _one(col: str, text: str, evidence: str) -> SessionRecord:
    record, warns = _apply(SessionRecord(), *[_add(col, text, evidence)])
    assert not warns and len(record.active()) == 1, warns
    return record


# ═══ R：规则 ═════════════════════════════════════════════════════════════════

def test_R0_an_entry_needs_verbatim_evidence_from_this_turn():
    """必须有依据：依据原话不在这一轮里 → 丢掉（R1、R4 都靠它）。"""
    ok, _ = _apply(SessionRecord(), *[_add("requests", "下个月去北京面试", "下个月要去北京面试")])
    bad, warns = _apply(SessionRecord(), *[_add("requests", "下个月去北京面试", "他说过想去上海")])
    assert [e.text for e in ok.active()] == ["下个月去北京面试"]
    assert bad.active() == [] and warns


def test_R0b_evidence_is_one_line_and_the_prompt_says_so():
    """依据只能来自这一轮的某一句（对方那句或角色那句）；跨两句拼起来的会被丢掉 ——
    抽取提示词必须把这条写给模型（10-12 全量重放第 7 轮就是拼接依据被丢）。"""
    stitched = USER_SAYS[-6:] + "……" + CHAR_SAYS[:6]
    out, warns = _apply(SessionRecord(), _add("requests", "下个月去北京面试", stitched))
    assert out.active() == [] and warns
    system, _ = rec.render_extraction_prompt(SessionRecord(), char_name="孔乙己",
                                             user_message="", assistant_reply="")
    assert "ONE continuous excerpt copied verbatim from ONE line" in system
    assert "Never join pieces of different lines" in system


def test_R1_relation_needs_evidence_and_never_touches_affinity():
    """R1：关系一栏只收有对话依据的条目；只有结论、没依据的丢掉。好感只看③（P3 测结构）。"""
    ok, _ = _apply(SessionRecord(), *[_add("relation", "说起西湖时放松了些", "我也去过西湖")])
    bad, warns = _apply(SessionRecord(), *[_add("relation", "关系变好了", "")])
    assert len(ok.active()) == 1
    assert bad.active() == [] and warns


def test_R1_relation_column_is_a_trajectory_not_overwritten():
    """关系一栏按先后记成轨迹：对它的「取代」按新增处理，旧的一条仍然有效。"""
    record = _one("relation", "一开始有些防备", "原来你在杭州读书啊")
    record, warns = _apply(record, *[_sup(1, "relation", "愿意多说了", "我也去过西湖")])
    assert [e.text for e in record.active()] == ["一开始有些防备", "愿意多说了"]
    assert warns
    assert "一开始有些防备 → 愿意多说了" in rec.render_block(record)


def test_R2_replace_checks_the_id():
    """R2：编号存在且有效 → 旧的失效、新的生效；不存在 / 已失效 → 按新增，旧的不受影响，有 warning。"""
    base = _one("requests", "下个月去上海面试", "面试")
    good, warns = _apply(base, *[_sup(1, "requests", "下个月去北京面试", "北京面试")])
    assert not warns
    assert [(e.id, e.active) for e in good.entries] == [(1, False), (2, True)]
    assert good.get(2).replaces == 1

    for bad_id in (99, "1"):
        out, warns = _apply(base, *[_sup(bad_id, "requests", "新的", "北京面试")])
        assert out.get(1).active, f"id={bad_id!r} 把旧条目弄失效了"
        assert len(out.active()) == 2 and warns, f"id={bad_id!r} 没按新增处理"
    # 不带编号（null）就是普通新增，不算错
    out, warns = _apply(base, _sup(None, "requests", "新的", "北京面试"))
    assert out.get(1).active and len(out.active()) == 2 and not warns

    # 已失效的条目不能再被取代（同一轮里第二次取代同一条也算）
    twice, warns = _apply(base, *[
        _sup(1, "requests", "去北京面试", "北京面试"),
        _sup(1, "requests", "去杭州面试", "杭州读研究生")])
    assert [e.text for e in twice.active()] == ["去北京面试", "去杭州面试"] and warns


def test_R2_ids_belong_to_one_archive():
    """别的存档的编号对这份记录来说就是不存在 —— 按新增，不动这里的任何条目。"""
    mine = _one("requests", "下个月去北京面试", "北京面试")
    out, warns = _apply(mine, *[_sup(7, "requests", "去杭州", "杭州读研究生")])
    assert out.get(1).active and len(out.active()) == 2 and warns


def test_R2b_supersede_stays_within_its_column():
    """跨栏取代不成立：拿别一栏的条目去取代，按新增处理，旧的仍有效（spec §3.4）。"""
    base = _one("known", "在上海读书", "读研究生")
    out, warns = _apply(base, _sup(1, "requests", "下个月去北京面试", "北京面试"))
    assert out.get(1).active and len(out.active()) == 2 and warns


def test_R3_long_column_warns_but_is_injected_whole(caplog):
    """R3：某一栏超线只打 warning，条目完整注入；未超线没有 warning。"""
    short = _one("requests", "下个月去北京面试", "北京面试")
    with caplog.at_level(logging.WARNING, logger="core.session_record"):
        rec.render_block(short)
    assert not caplog.records

    long_text = "杭州读研究生" * 80
    record = SessionRecord(tuple(
        rec.Entry(id=i, col="requests", text=f"{i}{long_text}", evidence="x", source="user")
        for i in range(1, 4)))
    with caplog.at_level(logging.WARNING, logger="core.session_record"):
        block = rec.render_block(record)
    assert any("requests" in r.getMessage() for r in caplog.records)
    assert all(e.text in block for e in record.entries), "超线的栏被截了"


def test_R4_replace_needs_an_explicit_contradiction_this_turn():
    """R4：取代必须带这一轮里推翻旧条目的原话；「没再提」给不出原话 → 丢掉，旧条目仍有效。"""
    base = _one("requests", "下个月去北京面试", "北京面试")
    out, warns = _apply(base, *[
        _sup(1, "requests", "好像不去面试了", "最近没再提面试")])
    assert [e.text for e in out.active()] == ["下个月去北京面试"] and warns


def test_R5_source_is_decided_by_code_and_known_takes_only_the_user():
    """R5：来源由依据出现在哪一边定（不信模型）；「他对你的了解」只收用户说的。"""
    ok, _ = _apply(SessionRecord(), *[_add("known", "在杭州读研究生", "杭州读研究生")])
    assert ok.active()[0].source == rec.SOURCE_USER

    bad, warns = _apply(SessionRecord(), *[
        _add("known", "去过西湖", "我也去过西湖"),
        {**_add("known", "去过西湖", "我也去过西湖"), "source": "user"}])   # 模型自称来源也不信
    assert bad.active() == [] and len(warns) == 2

    events, _ = _apply(SessionRecord(), *[_add("promises", "他提到去过西湖", "我也去过西湖")])
    assert events.active()[0].source == rec.SOURCE_CHARACTER


def test_R5b_each_side_can_only_overturn_its_own_entries():
    """R5：对方自己说的条目，角色复述的话不能取代（10-12 重放 B 第 136 轮：角色复述「教下棋」，
    把带着「孔乙己没明确答应」的原条目盖掉了）。第五轮起反过来也一样：角色答应的事，对方的话
    取代不了（对方的话进不了 `promises`）。各自改口照常取代（spec「第五轮修改」）。"""
    base = _one("requests", "下个月去北京面试", "下个月要去北京面试")
    out, warns = _apply(base, _sup(1, "requests", "他提到西湖", "我也去过西湖"))   # 依据在角色那句
    assert [e.text for e in out.active()] == ["下个月去北京面试"] and warns
    out, warns = _apply(base, _sup(1, "requests", "改去杭州", "杭州读研究生"))      # 依据在对方那句
    assert [e.text for e in out.active()] == ["改去杭州"] and not warns
    promised = _one("promises", "答应改天讲西湖", "我也去过西湖")
    out, warns = _apply(promised, _sup(1, "promises", "在杭州读研究生", "杭州读研究生"))
    assert [e.text for e in out.active()] == ["答应改天讲西湖"] and warns
    out, warns = _apply(promised, _sup(1, "promises", "说自己去过西湖", "我也去过西湖"))
    assert [e.text for e in out.active()] == ["说自己去过西湖"] and not warns


def test_R5c_requests_take_only_the_users_words():
    """P10：角色对对方的臆测不能记成对方的计划（重放 A 第 21 轮「明日还要赶路呢」）。"""
    bad, warns = _apply(SessionRecord(), _add("requests", "对方要去西湖", "我也去过西湖"))
    assert bad.active() == [] and warns
    ok, warns = _apply(SessionRecord(), _add("requests", "对方下个月去北京面试", "下个月要去北京面试"))
    assert [e.source for e in ok.active()] == [rec.SOURCE_USER] and not warns


def test_R5d_promises_take_only_the_characters_words():
    """「角色答应的事」只收角色自己的话（PHASE-Tree :77–79：not other speakers）。"""
    bad, warns = _apply(SessionRecord(), _add("promises", "答应陪对方去北京", "下个月要去北京面试"))
    assert bad.active() == [] and warns
    ok, warns = _apply(SessionRecord(), _add("promises", "答应改天讲西湖", "我也去过西湖"))
    assert [e.source for e in ok.active()] == [rec.SOURCE_CHARACTER] and not warns


def test_R5e_column_rules_live_only_in_the_column_table():
    """结构锁：一栏的规则（收谁的话、是不是轨迹）只写在 `COLUMNS` 那一行；函数只读 `Column` 的字段，
    不按栏名或来源写分支；提示词里的来源那句由 ``sources`` 生成，栏定义里不再写一遍
    （spec「第五轮修改」结构自检）。"""
    import inspect
    sources = {rec.SOURCE_USER, rec.SOURCE_CHARACTER}
    for col in rec.COLUMNS:
        assert col.sources and set(col.sources) <= sources, col.key
        assert "依据只能是" not in col.definition, col.key           # 来源那句只由 sources 生成
    functions = [fn for _, fn in inspect.getmembers(rec, inspect.isfunction)]
    for cls in (rec.Column, rec.SessionRecord):
        functions += [fn for _, fn in inspect.getmembers(cls, inspect.isfunction)]
    for fn in functions:
        if fn.__code__.co_filename != rec.__file__:                   # 别处导入的、dataclass 生成的
            continue
        body = inspect.getsource(fn)
        for col in rec.COLUMNS:
            assert f'"{col.key}"' not in body, (fn.__name__, col.key)
        if fn.__name__ != "_source_of":
            assert "SOURCE_USER" not in body and "SOURCE_CHARACTER" not in body, fn.__name__


def test_R5f_the_prompt_states_each_columns_sources():
    """提示词里每栏都带上它收谁的话，且和 ``sources`` 一致：只收对方的写「对方的原话」，
    只收角色的写「孔乙己的原话」，两边都收的不写。"""
    text = _prompt(name="孔乙己")
    for col in rec.COLUMNS:
        line = next(ln for ln in text.splitlines() if ln.startswith(f"- {col.key}「"))
        assert ("依据只能是对方的原话" in line) == (col.sources == {rec.SOURCE_USER}), col.key
        assert ("依据只能是孔乙己的原话" in line) == (col.sources == {rec.SOURCE_CHARACTER}), col.key


def test_R6_superseded_entries_stay_stored_but_are_not_injected():
    base = _one("known", "在上海读书", "读研究生")
    out, _ = _apply(base, *[_sup(1, "known", "在杭州读研究生", "杭州读研究生")])
    block = rec.render_block(out)
    assert "在杭州读研究生" in block and "在上海读书" not in block
    assert "在上海读书" in out.to_persist()


def test_R7_no_ops_leave_the_record_unchanged():
    base = _one("requests", "下个月去北京面试", "北京面试")
    for data in ({}, {rec.FIELD_OPS: []}, {rec.FIELD_OPS: None}):
        out, warns = rec.apply(base, data, USER_SAYS, CHAR_SAYS)
        assert out.entries == base.entries and not warns


def test_R8_persist_round_trip_and_corrupt_data():
    base = _one("requests", "下个月去北京面试", "北京面试")
    assert SessionRecord.from_persist(base.to_persist()) == base
    assert SessionRecord.from_persist("") == SessionRecord()
    assert SessionRecord.from_persist("{broken") == SessionRecord()


# ═══ P：评估流水线 ═══════════════════════════════════════════════════════════

_RECORD_SYS = "You are a character state analyst"


class _ScriptLLM:
    """按 system 提示词分流的替身：好感评估回 ``reply``，本场记录抽取回 ``record``（默认没有操作）。"""
    model = "stub"

    def __init__(self, reply: dict, record: dict | None = None):
        self.reply = json.dumps(reply, ensure_ascii=False)
        self.record = json.dumps(record if record is not None else {}, ensure_ascii=False)
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.prompts: list[str] = []              # 好感评估的提示词
        self.record_calls: list[tuple[str, str]] = []   # 抽取调用的 (system, user)
        self.derived: list[float | None] = []

    def preflight(self):
        return None

    def derive(self, *, temperature=None, **_kw):
        self.derived.append(temperature)
        return self

    def chat(self, system, messages, **_kw):
        if system.startswith(_EVAL_SYS):
            self.prompts.append(messages[0]["content"])
            return self.reply
        if system.startswith(_RECORD_SYS):
            self.record_calls.append((system, messages[0]["content"]))
            return self.record
        return "回复"


class _FailingRecordLLM(_ScriptLLM):
    """抽取调用抛错（模拟网络 / 额度问题），好感评估照常。"""

    def chat(self, system, messages, **kw):
        if system.startswith(_RECORD_SYS):
            raise RuntimeError("record call failed")
        return super().chat(system, messages, **kw)


class _FailingEvalLLM(_ScriptLLM):
    """好感评估回的不是 JSON（评估这一轮失败），抽取照常。"""

    def chat(self, system, messages, **kw):
        if system.startswith(_EVAL_SYS):
            return "不是 JSON"
        return super().chat(system, messages, **kw)


class _Card:
    name = "角色"
    values: list = []
    personality_traits: list = []
    inner_tensions: list = []
    psyche = PsycheProfile()


class _SpyStorage:
    def __init__(self):
        self.calls: list[str] = []

    async def save_affinity_state(self, sid, state):
        self.calls.append("affinity")

    async def save_session_record(self, sid, raw):
        self.calls.append("record")

    async def update_group_affinity(self, *a):
        self.calls.append("group")


def _ctx(llm, *, record: SessionRecord | None, group_id: str = "", storage=None, svc=None):
    return EvalContext(
        card=_Card(), user_message=USER_SAYS, assistant_reply=CHAR_SAYS, user_role="对方",
        old_stage="陌生", session_id="" if group_id else "s1", group_id=group_id, card_id="c1",
        storage=storage, memory=None, affinity_service=svc or AffinityService(),
        reaction_service=object(), llm=llm, session_record=record)


@pytest.fixture
def loop_submitter(monkeypatch):
    """流水线落库走 submit_to_main_loop；这里同步跑完协程，只为看保存了几次、存了什么。"""
    import core.scheduling as scheduling
    monkeypatch.setattr(scheduling, "_submitter",
                        lambda coro, wait=True, timeout=0: asyncio.new_event_loop().run_until_complete(coro))


_BJ = {rec.FIELD_OPS: [_add("requests", "下个月去北京面试", "北京面试")]}


def test_P1_extraction_is_its_own_call_and_the_affinity_prompt_is_untouched():
    """P1（G1）：一对一每轮两次调用 —— 好感评估的提示词里没有任何本场记录内容（和群聊、
    不带本场记录时逐字相同）；本场记录由单独一次抽取调用更新，用低温派生的适配器。"""
    record = _one("requests", "下个月去北京面试", "北京面试")
    single = _ScriptLLM(_verdict())
    EvaluationPipeline().run(_ctx(single, record=record))
    plain = AffinityService().build_evaluation_prompt(_Card(), USER_SAYS, CHAR_SAYS, "对方", "")
    assert single.prompts == [plain], "好感评估的提示词被本场记录改动了"
    assert len(single.record_calls) == 1 and single.derived == [rec.EXTRACT_TEMPERATURE]
    system, user = single.record_calls[0]
    assert "#1 [requests] 下个月去北京面试" in user and USER_SAYS in user and CHAR_SAYS in user
    for frame in ("你现在就是", "内心", "inner_voice", "好感"):
        assert frame not in system + user, f"抽取提示词里混进了角色扮演 / 好感的框架：{frame}"

    group = _ScriptLLM(_verdict())
    EvaluationPipeline().run(_ctx(group, record=None, group_id="g1"))
    assert group.prompts == [plain] and group.record_calls == [], "群聊不该有抽取调用"


def test_P1b_build_evaluation_prompt_has_no_record_parameters():
    import inspect
    params = inspect.signature(AffinityService.build_evaluation_prompt).parameters
    assert not [p for p in params if p.startswith("record")]


def test_P2_record_and_affinity_fail_separately(loop_submitter):
    """好感评估坏了，本场记录照常更新并落库；抽取调用坏了，好感照常、本场记录原样。"""
    spy = _SpyStorage()
    out = EvaluationPipeline().run(_ctx(_FailingEvalLLM(_verdict(), record=_BJ),
                                        record=SessionRecord(), storage=spy))
    assert not out.applied, "前提：好感评估这一轮确实失败了"
    assert [e.text for e in out.session_record.active()] == ["下个月去北京面试"] and spy.calls == ["record"]

    def state_after(llm):
        svc = AffinityService()
        result = EvaluationPipeline().run(_ctx(llm, record=record, storage=_SpyStorage(), svc=svc))
        return result, (svc.affinity, svc.trust, svc.guard, svc.mood, svc.relation)

    record = SessionRecord()
    _, clean = state_after(_ScriptLLM(_verdict()))
    for broken_llm in (_FailingRecordLLM(_verdict(), record=_BJ),
                       _ScriptLLM(_verdict(), record={rec.FIELD_OPS: "not a list"})):
        out, broken = state_after(broken_llm)
        assert out.applied and broken == clean, "本场记录坏了把好感也拖下水"
        assert out.session_record is record
    assert clean[1] == 32, "前提：信任确实按这一轮的判定更新了"

    # 抽取回的不是 JSON：同样只丢本场记录这一轮
    class _Prose(_ScriptLLM):
        def chat(self, system, messages, **kw):
            return "没有变化" if system.startswith(_RECORD_SYS) else super().chat(system, messages, **kw)
    out, prose = state_after(_Prose(_verdict()))
    assert out.applied and prose == clean and out.session_record is record


def test_P3_record_ops_do_not_change_the_affinity_verdict(loop_submitter):
    """R1 的结构面：同一份好感判定，抽取出什么本场记录，好感和关系状态都一样。"""
    def run(record_reply):
        svc = AffinityService()
        EvaluationPipeline().run(_ctx(_ScriptLLM(_verdict(), record=record_reply), record=SessionRecord(),
                                      storage=_SpyStorage(), svc=svc))
        return svc.affinity, svc.relation
    assert run({}) == run({rec.FIELD_OPS: [_add("relation", "放松了些", "我也去过西湖")]})


def test_P4_one_save_point_per_turn(loop_submitter):
    """一对一：有变化时好感、本场记录各存一次；没变化时不存本场记录。群聊不存本场记录。"""
    spy = _SpyStorage()
    EvaluationPipeline().run(_ctx(_ScriptLLM(_verdict(), record=_BJ), record=SessionRecord(), storage=spy))
    assert spy.calls == ["affinity", "record"]

    spy = _SpyStorage()
    EvaluationPipeline().run(_ctx(_ScriptLLM(_verdict()), record=SessionRecord(), storage=spy))
    assert spy.calls == ["affinity"]

    spy = _SpyStorage()
    EvaluationPipeline().run(_ctx(_ScriptLLM(_verdict(), record=_BJ), record=None, group_id="g1", storage=spy))
    assert spy.calls == ["group"]


def test_P5_extraction_usage_is_recorded_under_its_own_action(loop_submitter, monkeypatch):
    import core.evaluation_pipeline as ep
    actions: list[str] = []
    monkeypatch.setattr(ep, "try_record_usage", lambda *a, action="", **kw: actions.append(action))
    EvaluationPipeline().run(_ctx(_ScriptLLM(_verdict()), record=SessionRecord(), storage=_SpyStorage()))
    assert actions == ["chat_affinity_eval", "chat_session_record"]


def test_P6_derived_adapter_is_reused_across_turns(loop_submitter):
    """低温适配器每个源适配器只派生一次（派生会新建客户端）；换了源适配器才重新派生。"""
    pipe, first, second = EvaluationPipeline(), _ScriptLLM(_verdict()), _ScriptLLM(_verdict())
    for llm in (first, first, second):
        pipe.run(_ctx(llm, record=SessionRecord(), storage=_SpyStorage()))
    assert first.derived == [rec.EXTRACT_TEMPERATURE] and second.derived == [rec.EXTRACT_TEMPERATURE]


# ═══ X：抽取提示词（照抄文献的句子一字不差；视角是第三人称）════════════════════

_TRANSCRIBED = (
    # StateMem 附录 E.1（arXiv 2608.19652v1，p.14–15）
    "Only create units for information RELEVANT to future decisions",
    "Do NOT duplicate information already in an existing unit -- check the current state first",
    "Keep content concise -- one clear sentence per unit",
    'Look ACTIVELY for supersession patterns: "actually", "instead", "no longer", "switched to", '
    '"moved off", "cancelled", "replaced X with Y", "now Y not X"',
    "If the turn contains no new state-relevant information, or if the state already captures everything",
    # PHASE-Tree update_session_moment.py:68 / :81 / :96 / :155（commit 0be378f）
    "You are a character state analyst.",
    "Do NOT fabricate events or emotions not supported by the text.",
    "You MUST write ALL",
    "**你必须用中文撰写所有输出内容。**",
    # mem0 2.0.20 configs/prompts.py:51
    "Do not return anything from the custom few shot example prompts provided above.",
    # AutoPersonas（arXiv 2607.08252v1，p.45）
    "a recurring person mention becomes a relationship only when it changes obligations, "
    "conflicts, mutual expectations, or later reachability",
    # 第五轮：StateMem 附录 E.1（raw.txt :2072–2073）；PHASE-Tree update_session_moment.py:114
    "From USER statements: explicit requests",
    "Promises or decisions the character made",
)


def _prompt(name="孔乙己"):
    system, user = rec.render_extraction_prompt(SessionRecord(), char_name=name, user_message=USER_SAYS,
                                                assistant_reply=CHAR_SAYS)
    return system + user


def test_X1_transcribed_rules_are_verbatim():
    """规则是照抄的（只把 unit 换成 entry 之外不改字）—— 以后谁改写了原句，这里红。"""
    text = _prompt().replace("entry", "unit").replace("entries", "units")
    missing = [t for t in _TRANSCRIBED if t not in text]
    assert not missing, missing


def test_X1b_examples_are_not_taken_from_the_replay_conversation():
    """示范用另编的人物和句子：不能含验收那场对话里的内容，否则重放成了对答案（spec §3.3）。"""
    system, _ = rec.render_extraction_prompt(SessionRecord(), char_name="孔乙己",
                                             user_message="", assistant_reply="")
    examples = system[system.index("## Few shot examples"):]
    assert "老陈" in examples
    for leaked in ("孔乙己", "林小满", "苏州", "猫", "西湖", "桂花糕", "论语", "孟子", "京城", "金陵",
                   "下棋", "小芸", "歇"):
        assert leaked not in examples, leaked


def _example_outputs() -> list[tuple[str, str, list[dict]]]:
    """示范里每一例的 (对方那句, 老陈那句, 输出项)。"""
    system, _ = rec.render_extraction_prompt(SessionRecord(), char_name="孔乙己",
                                             user_message="", assistant_reply="")
    lines = system[system.index("## Few shot examples"):].splitlines()
    out = []
    for i, ln in enumerate(lines):
        if ln.startswith("Output: "):
            ops = json.loads(ln[len("Output: "):])[rec.FIELD_OPS]
            out.append((lines[i - 2].removeprefix("对方："), lines[i - 1].removeprefix("老陈："), ops))
    return out


def test_X3_examples_keep_each_fact_in_its_speakers_column():
    """示范自己守规则：每项依据都在它那栏该收的那一边；对方的请求和角色的应允是两件事，分两项记；
    角色只是推托时不记 promises（10-12 重放第 7 轮：例子只记「老陈答应」，模型就把对方的请求
    丢了、把推托写成「答应」）。"""
    by_key = {c.key: c for c in rec.COLUMNS}
    examples = _example_outputs()
    for user_line, char_line, ops in examples:
        for op in ops:
            side = rec.SOURCE_USER if op["evidence"] in user_line else rec.SOURCE_CHARACTER
            assert op["evidence"] in (user_line if side == rec.SOURCE_USER else char_line), op
            assert side in by_key[op["col"]].sources, op
    cols = [sorted(o["col"] for o in ops) for _, _, ops in examples]
    assert ["promises", "requests"] in cols, "缺「对方请求 + 角色应允」分两项的示范"
    assert any(c == ["requests"] and "再" in ch for c, (_, ch, _) in zip(cols, examples)), "缺推托的示范"


def test_X2_third_person_with_names_extraction_and_second_person_only_in_the_block():
    """P2（G2）：抽取提示词里栏名用角色名，没有「他对你」；注入给角色的块才用「你」。"""
    text = _prompt(name="孔乙己")
    assert "他对你" not in text and "你们关系" not in text
    for col in rec.COLUMNS:
        assert col.label.format(who="孔乙己") in text
    block = rec.render_block(_one("known", "在杭州读研究生", "杭州读研究生"))
    assert "你知道的关于对方的事" in block and "孔乙己" not in block


# ═══ E：真引擎 + 真 PG ═══════════════════════════════════════════════════════

_pg = PG_ENV.skipif("PG 本场记录目标检查")
CARD = CharacterCard(name="角色", identity="本场记录检查用角色")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def _seed(store: PostgresStore) -> tuple[str, str, str, str]:
    """一个用户、一张卡、两个存档。返回 (uid, card_id, A, B)。"""
    uid = _uid("u")
    await store.create_user(uid, _uid("n"), _PW)
    tid = _uid("t")
    await store.save_text(tid, "src.txt", "正文", user_id=uid)
    cid = _uid("c")
    assert await store.save_card(cid, tid, "角色", '{"name": "角色"}', uid)
    a, b = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    for sid in (a, b):
        await store.save_session(sid, cid, "user", "", user_id=uid)
    return uid, cid, a, b


def _engine(store, llm, card_id: str, session_id: str = "", group_id: str = "") -> ChatEngine:
    return ChatEngine(llm=llm, rag=None, card=CARD, card_id=card_id, storage=store,
                      session_id=session_id, group_id=group_id, is_new_session=False)


class _CountingStore:
    """真 PG 存储，外加数一数每轮各存了几次。"""

    def __init__(self, store: PostgresStore):
        self._store, self.calls = store, []

    def __getattr__(self, name):
        attr = getattr(self._store, name)
        if name in ("save_affinity_state", "save_session_record"):
            async def counted(*a, **kw):
                self.calls.append(name)
                return await attr(*a, **kw)
            return counted
        return attr


@_pg
async def test_E1_a_record_belongs_to_its_archive_and_survives_a_restore():
    """存档 A 的一轮写进 A；B 读不到；A 重新打开后注入块里有它，B 没有。"""
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    try:
        _, cid, a, b = await _seed(store)
        llm = _ScriptLLM(_verdict(), record={rec.FIELD_OPS: [_add("known", "在杭州读研究生", "杭州读研究生")]})
        live = _engine(store, llm, cid, a)
        async with main_loop_registered():
            await asyncio.to_thread(live.post_stream_process, USER_SAYS, CHAR_SAYS)
            assert "在杭州读研究生" in live._build_all_enhancements(), "这一轮之后，本引擎自己没拿到新条目"

            assert "在杭州读研究生" in await store.load_session_record_unscoped(a)
            assert await store.load_session_record_unscoped(b) == ""

            fresh_a, fresh_b = _engine(store, llm, cid, a), _engine(store, llm, cid, b)
            await restore_engine_state(fresh_a, a, store)
            await restore_engine_state(fresh_b, b, store)
        assert "在杭州读研究生" in fresh_a._build_all_enhancements()
        assert rec.BLOCK_TITLE not in fresh_b._build_all_enhancements()
    finally:
        await store.close()


@_pg
async def test_E2_the_user_never_sees_it():
    """读好感的接口、会话行（属主读 / 无属主读）都不带本场记录。"""
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    try:
        uid, cid, a, _ = await _seed(store)
        llm = _ScriptLLM(_verdict(), record={rec.FIELD_OPS: [_add("known", "在杭州读研究生", "杭州读研究生")]})
        async with main_loop_registered():
            await asyncio.to_thread(_engine(store, llm, cid, a).post_stream_process, USER_SAYS, CHAR_SAYS)
        assert "在杭州读研究生" in await store.load_session_record_unscoped(a), "前提：确实存进去了"
        affinity = await resolve_session_affinity(a, store, {})
        owned = await store.get_session_owned(a, uid)
        unscoped = await store.get_session_unscoped(a)
        for name, payload in (("好感接口", affinity), ("属主会话行", owned), ("无属主会话行", unscoped)):
            text = json.dumps(payload, ensure_ascii=False, default=str)
            assert "session_record" not in text and "在杭州读研究生" not in text, name
    finally:
        await store.close()


@_pg
async def test_E3_one_turn_saves_affinity_once_and_the_record_once():
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    try:
        _, cid, a, _ = await _seed(store)
        counting = _CountingStore(store)
        llm = _ScriptLLM(_verdict(), record=_BJ)
        async with main_loop_registered():
            await asyncio.to_thread(_engine(counting, llm, cid, a).post_stream_process, USER_SAYS, CHAR_SAYS)
        assert sorted(counting.calls) == ["save_affinity_state", "save_session_record"], counting.calls
    finally:
        await store.close()


@_pg
async def test_E4_group_engines_never_carry_a_record():
    """群聊引擎：评估提示词里没有本场记录、注入块里没有、也不存。"""
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    try:
        _, cid, a, _ = await _seed(store)
        counting = _CountingStore(store)
        llm = _ScriptLLM(_verdict(), record=_BJ)
        eng = _engine(counting, llm, cid, group_id=_uid("g"))
        eng.load_session_record(_one("requests", "下个月去北京面试", "北京面试"))  # 装进去也不用
        async with main_loop_registered():
            await asyncio.to_thread(eng.post_stream_process, USER_SAYS, CHAR_SAYS)
        assert llm.record_calls == [], "群聊不该有抽取调用"
        assert rec.BLOCK_TITLE not in eng._build_all_enhancements()
        assert "save_session_record" not in counting.calls
    finally:
        await store.close()


def test_R9_one_turn_keeps_at_most_the_op_cap():
    ops = [_add("requests", f"第{i}件事", "北京面试") for i in range(rec.MAX_OPS_PER_TURN + 2)]
    out, warns = _apply(SessionRecord(), *ops)
    assert len(out.active()) == rec.MAX_OPS_PER_TURN and warns


@_pg
async def test_E5_saving_the_record_leaves_updated_at_alone():
    """`updated_at` 是离开提示读的上次聊天时间；存本场记录不是聊天，不能动它。"""
    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    try:
        uid, _, a, _ = await _seed(store)
        before = (await store.get_session_owned(a, uid))["updated_at"]
        await asyncio.sleep(1.1)
        await store.save_session_record(a, _one("requests", "下个月去北京面试", "北京面试").to_persist())
        assert (await store.get_session_owned(a, uid))["updated_at"] == before
        assert "北京面试" in await store.load_session_record_unscoped(a)
    finally:
        await store.close()
