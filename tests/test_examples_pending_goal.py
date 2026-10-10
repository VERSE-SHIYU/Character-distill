# -*- coding: utf-8 -*-
"""「待补对话示例」目标检查 · 后端（docs/specs/examples-pending.md §1）。

定好的目标（Shiyu 2026-10-10）：
  1. 蒸馏出来的卡缺对话示例，打开这张卡时弹出编辑页让用户自己填；
  2. 填不出来，有一个按钮让系统重新找一次并填上；
  3. 这个按钮只能用一次；
  4. 重新找了还是没有，就认定这张卡没有，不再提醒；
  5. 聊天时没有就跳过，照常聊。
提醒只针对新蒸馏出来的卡，只有蒸馏好的这一次。修的过程不影响原有卡片的建立。

后端要守住的：蒸馏落卡时记下「待补」；保存、重新找、关掉三个出口都清掉它；「重新找」用的
是蒸馏里贴示例的同一步，原文照抄；只在「待补」时能用；「待补」不写进卡的内容。

走真实路由与真 `Distiller`、真存储，只把模型换成假适配器。夹具复用
`test_identify_failure_channels`（跨文件引用在本仓有先例）。期望文案与字段是字面量。

落卡时记「待补」（三条产卡通道都走真实的落库）
P1  后台任务：没配上示例的卡落库 → 待补，记下的是蒸馏时用的角色名
P2  后台任务：配上示例的卡 → 不待补
P3  同一角色重新蒸馏后配上了 → 上一次留下的待补清掉
P3b SSE 那条通道同样记
P4  `/run` 那条通道同样记
P4b 记「待补」这一步出错 → 卡照常落库、任务照常完成（不影响卡片的建立）
三个出口都清掉
P5  保存（PATCH）→ 不待补
P6  关掉（dismiss）→ 不待补，卡的内容和版本号都没变
重新找一次
P7  找到了 → 卡上是原文照抄的「对方一句 + 角色一句」，不待补，found 为真
P8  原文里没有这个角色的对话句 → 不调模型，卡不变，不待补，found 为假
P9  模型没选出可用的 → 卡不变，不待补，found 为假
P10 调用模型出错 → 请求失败，仍待补，卡不变；再点一次能成功
P11 有起点的卡 → 找到的示例贴在阶段下，出卡的内容里看得到
P11b 卡上的名字和蒸馏时用的角色名不一样 → 按蒸馏时的名字找，照样找到
只能用一次
P12 已经不是「待补」的卡再调 → 409，不调模型
P13 版本号不符 → 409，不调模型，仍待补
P14 别人的卡 → 404（重新找、关掉都是）
贴示例是「换掉」
P15 阶段下已有示例的卡再贴一次 → 阶段下只有这一次挑的
不跟着卡的内容走
P16 「待补」不在 card_json 里：出卡的内容、原样导出里都没有这个键
P17 聊天：有示例就用（手填在顶层的对有阶段的卡也生效），没有就没有【对话风格示范】这一节
    （main 上就绿：回归守卫）
"""
from __future__ import annotations

import json
import uuid

import deps
from core.card_out import card_revision
from core.distiller import Distiller
from core.schema import CharacterCard
from core.text_manager import TextManager
from test_identify_failure_channels import (  # noqa: F401  （fixture 靠名字注入）
    _BODY,
    _FormattingLLM,
    _build_client,
    _clean_tasks,
    _run_async,
    _run_bg,
    _seed_text,
    store,
    user_id,
)

EXAMPLE = "路人：先前的话。\n角色：我说一句话。"
ROSTER = [{"name": "角色"}, {"name": "路人"}]
NOT_PENDING = "这张卡已经处理过了，不能再重新找"
CONFLICT = "这张卡已在别处更新，请刷新后再改"


class _CountingLLM(_FormattingLLM):
    """记下挑选对话示例那一步调了几次模型。"""

    def __init__(self) -> None:
        super().__init__()
        self.picks = 0
        self.chats = 0

    def chat(self, system, messages, max_tokens=None, **kw):
        self.chats += 1
        return super().chat(system, messages, max_tokens=max_tokens, **kw)

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        self.picks += 1
        return super().select_by_schema(system_prompt, messages, function, max_tokens)


class _NoPickLLM(_CountingLLM):
    """每一格都填 0：模型如实表示没有合适的候选。"""

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        self.picks += 1
        return {k: (0 if k.startswith("pick") else "无法判断")
                for k in function["parameters"]["properties"]}


class _FlakyPickLLM(_CountingLLM):
    """第一次挑选直接出错，之后正常。"""

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        if self.picks == 0:
            self.picks += 1
            raise RuntimeError("upstream exploded")
        return super().select_by_schema(system_prompt, messages, function, max_tokens)


def _distiller(llm=None) -> Distiller:
    d = Distiller(llm=llm or _CountingLLM(), config_path=None)
    d._longctx_threshold = 0
    d._chunk_size = 3000
    return d


def _no_quote_body() -> str:
    return f"角色说的话，没有引号{uuid.uuid4().hex}"


def _seed_card(store, uid, tid, card: CharacterCard, *, pending: bool) -> str:
    cid = f"card_{uuid.uuid4().hex[:8]}"
    _run_async(store.save_card(cid, tid, card.name, card.model_dump_json(), uid))
    if pending:
        assert _run_async(store.set_card_examples_pending(cid, uid, "角色"))
    return cid


def _seed_roster(store, tid) -> None:
    """名单缓存：真实流程里蒸馏时已经识别并存下，「重新找」命中缓存、不再识别。"""
    _run_async(store.save_characters(tid, ROSTER, version=Distiller.IDENTIFY_VERSION))


def _setup(store, uid, monkeypatch, *, body=_BODY, card=None, pending=True, llm=None):
    tid = _seed_text(store, uid, body)
    _seed_roster(store, tid)
    cid = _seed_card(store, uid, tid, card or CharacterCard(name="角色", identity="原来的身份"),
                     pending=pending)
    llm = llm or _CountingLLM()
    client = _build_client(store, uid, monkeypatch, distiller=_distiller(llm))
    return client, tid, cid, llm


def _row(client, tid, cid) -> dict:
    rows = client.get(f"/api/distill/cards/by-text/{tid}").json()
    return next(r for r in rows if r["id"] == cid)


def _card_json(row) -> dict:
    raw = row["card_json"]
    return json.loads(raw) if isinstance(raw, str) else raw


def _all_examples(card_json: dict) -> list[str]:
    return list(card_json.get("dialogue_examples") or []) + [
        d for p in (card_json.get("character_arc") or {}).get("phases") or []
        for d in ((p.get("overlay") or {}).get("dialogue_examples") or [])]


def _refind(client, tid, cid, revision=None):
    revision = revision if revision is not None else _row(client, tid, cid)["revision"]
    return client.post(f"/api/distill/card/{cid}/examples/refind", json={"revision": revision})


def _tm(store, distiller) -> TextManager:
    return TextManager(lambda: store, distiller, object(), {}, memory_manager=None)


# ── 落卡时记「待补」────────────────────────────────────────────────────────

LOCATABLE = "\n开头甲甲甲。"   # 草稿里的摘录得能在正文里定位（见夹具文件）


def _bg(store, uid, monkeypatch, tid, body, llm=None):
    """跑一次真的后台任务，落库用真的 TextManager。返回 (任务行, 这段文本下的卡行)。"""
    d = _distiller(llm)
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: _tm(store, d))
    task = _run_bg(store, uid, tid, d, monkeypatch, body=body)
    return task, _run_async(store.list_cards(tid, uid))


def test_p1_bg_a_card_saved_without_examples_is_marked_pending(store, user_id, monkeypatch):
    body = _no_quote_body() + LOCATABLE
    tid = _seed_text(store, user_id, body)

    task, rows = _bg(store, user_id, monkeypatch, tid, body)

    assert task["status"] == "done", task
    assert [r["examples_pending_for"] for r in rows] == ["角色"]


def test_p2_bg_a_card_saved_with_examples_is_not_pending(store, user_id, monkeypatch):
    body = _BODY + LOCATABLE
    tid = _seed_text(store, user_id, body)

    task, rows = _bg(store, user_id, monkeypatch, tid, body)

    assert task["status"] == "done", task
    assert [r["examples_pending_for"] for r in rows] == [None]


def test_p3_redistilling_with_examples_clears_pending(store, user_id, monkeypatch):
    body = _BODY + LOCATABLE
    tid = _seed_text(store, user_id, body)
    _, first = _bg(store, user_id, monkeypatch, tid, body, llm=_NoPickLLM())
    assert [r["examples_pending_for"] for r in first] == ["角色"]

    _, second = _bg(store, user_id, monkeypatch, tid, body)

    assert [r["id"] for r in second] == [r["id"] for r in first]      # 同文本同名：还是那一行
    assert [r["examples_pending_for"] for r in second] == [None]


def test_p3b_the_sse_channel_marks_pending_too(store, user_id, monkeypatch):
    body = _no_quote_body() + LOCATABLE
    tid = _seed_text(store, user_id, body)
    d = _distiller()
    client = _build_client(store, user_id, monkeypatch, distiller=d, tm=_tm(store, d))

    r = client.post("/api/distill/run_stream", json={"text_id": tid, "character_name": "角色"})

    assert r.status_code == 200, r.text
    assert [x["examples_pending_for"] for x in _run_async(store.list_cards(tid, user_id))] == ["角色"]


def test_p4_the_run_channel_marks_pending_too(store, user_id, monkeypatch):
    tid = _seed_text(store, user_id, _no_quote_body() + LOCATABLE)
    d = _distiller()
    client = _build_client(store, user_id, monkeypatch, distiller=d, tm=_tm(store, d))

    r = client.post("/api/distill/run", json={"text_id": tid, "character_name": "角色"})

    assert r.status_code == 200, r.text
    assert _row(client, tid, r.json()["card_id"])["examples_pending_for"] == "角色"


def test_p4b_a_failure_to_mark_pending_does_not_fail_the_save(store, user_id, monkeypatch):
    body = _no_quote_body() + LOCATABLE
    tid = _seed_text(store, user_id, body)

    async def _boom(card_id, uid, character):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(store, "set_card_examples_pending", _boom)

    task, rows = _bg(store, user_id, monkeypatch, tid, body)

    assert task["status"] == "done", task
    assert [json.loads(r["card_json"])["name"] for r in rows] == ["角色"]


# ── 三个出口都清掉 ─────────────────────────────────────────────────────────

def test_p5_saving_the_card_clears_pending(store, user_id, monkeypatch):
    client, tid, cid, _ = _setup(store, user_id, monkeypatch)
    row = _row(client, tid, cid)
    data = _card_json(row)
    data["dialogue_examples"] = ["路人：你好。\n角色：我自己填的。"]

    r = client.patch(f"/api/distill/card/{cid}", json={"card_json": data, "revision": row["revision"]})

    assert r.status_code == 200, r.text
    assert r.json()["card"]["examples_pending_for"] is None
    after = _row(client, tid, cid)
    assert after["examples_pending_for"] is None
    assert _card_json(after)["dialogue_examples"] == ["路人：你好。\n角色：我自己填的。"]


def test_p6_dismissing_clears_pending_and_leaves_the_card_alone(store, user_id, monkeypatch):
    client, tid, cid, llm = _setup(store, user_id, monkeypatch)
    before = _row(client, tid, cid)

    r = client.post(f"/api/distill/card/{cid}/examples/dismiss")

    assert r.status_code == 200, r.text
    assert r.json()["card"]["examples_pending_for"] is None
    after = _row(client, tid, cid)
    assert after["examples_pending_for"] is None
    assert after["revision"] == before["revision"]
    assert _card_json(after) == _card_json(before)
    assert (llm.picks, llm.chats) == (0, 0)


# ── 重新找一次 ─────────────────────────────────────────────────────────────

def test_p7_refind_fills_in_examples_copied_from_the_text(store, user_id, monkeypatch):
    client, tid, cid, llm = _setup(store, user_id, monkeypatch)

    r = _refind(client, tid, cid)

    assert r.status_code == 200, r.text
    assert r.json()["found"] is True
    assert _all_examples(_card_json(r.json()["card"])) == [EXAMPLE]
    after = _row(client, tid, cid)
    assert _all_examples(_card_json(after)) == [EXAMPLE]
    assert _card_json(after)["identity"] == "原来的身份"
    assert after["examples_pending_for"] is None
    assert llm.picks == 1


def test_p8_refind_with_no_spoken_line_settles_without_calling_the_model(
        store, user_id, monkeypatch):
    client, tid, cid, llm = _setup(store, user_id, monkeypatch, body=_no_quote_body())
    before = _row(client, tid, cid)

    r = _refind(client, tid, cid)

    assert r.status_code == 200, r.text
    assert r.json()["found"] is False
    after = _row(client, tid, cid)
    assert _card_json(after) == _card_json(before)
    assert after["examples_pending_for"] is None
    assert (llm.picks, llm.chats) == (0, 0)


def test_p9_refind_when_the_model_picks_nothing_settles_as_none(store, user_id, monkeypatch):
    client, tid, cid, llm = _setup(store, user_id, monkeypatch, llm=_NoPickLLM())
    before = _row(client, tid, cid)

    r = _refind(client, tid, cid)

    assert r.status_code == 200, r.text
    assert r.json()["found"] is False
    after = _row(client, tid, cid)
    assert _card_json(after) == _card_json(before)
    assert after["examples_pending_for"] is None
    assert llm.picks == 1


def test_p10_a_failed_model_call_does_not_use_up_the_one_chance(store, user_id, monkeypatch):
    client, tid, cid, _ = _setup(store, user_id, monkeypatch, llm=_FlakyPickLLM())
    before = _row(client, tid, cid)

    failed = _refind(client, tid, cid)

    assert failed.status_code >= 500, failed.text
    still = _row(client, tid, cid)
    assert still["examples_pending_for"] == "角色"
    assert _card_json(still) == _card_json(before)

    again = _refind(client, tid, cid)

    assert again.status_code == 200, again.text
    assert again.json()["found"] is True
    assert _all_examples(_card_json(_row(client, tid, cid))) == [EXAMPLE]


def test_p11_on_a_card_with_phases_the_examples_land_under_a_phase(store, user_id, monkeypatch):
    card = CharacterCard.model_validate({"name": "角色", "character_arc": {
        "source_fingerprint": "fp",
        "phases": [{"label": "早", "state": "早年", "start": 0},
                   {"label": "晚", "state": "晚年", "start": 100000}]}})
    client, tid, cid, _ = _setup(store, user_id, monkeypatch, card=card)

    r = _refind(client, tid, cid)

    assert r.status_code == 200, r.text
    data = _card_json(_row(client, tid, cid))
    assert data["dialogue_examples"] == []
    assert data["character_arc"]["phases"][0]["overlay"] == {"dialogue_examples": [EXAMPLE]}
    assert r.json()["found"] is True


def test_p11b_refind_uses_the_name_the_card_was_distilled_as(store, user_id, monkeypatch):
    """卡上的 `name` 是模型写的，不保证和蒸馏时指定的角色名一样；找示例要用蒸馏时的那个名字。"""
    card = CharacterCard(name="角色（主角）", identity="原来的身份")
    client, tid, cid, _ = _setup(store, user_id, monkeypatch, card=card)

    r = _refind(client, tid, cid)

    assert r.status_code == 200, r.text
    assert r.json()["found"] is True
    assert _all_examples(_card_json(_row(client, tid, cid))) == [EXAMPLE]


# ── 只能用一次 ─────────────────────────────────────────────────────────────

def test_p12_refind_is_refused_once_the_card_is_no_longer_pending(store, user_id, monkeypatch):
    client, tid, cid, llm = _setup(store, user_id, monkeypatch)
    assert _refind(client, tid, cid).status_code == 200
    picks_after_first = llm.picks

    r = _refind(client, tid, cid)

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == NOT_PENDING
    assert llm.picks == picks_after_first
    assert _all_examples(_card_json(_row(client, tid, cid))) == [EXAMPLE]   # 没有贴第二遍


def test_p13_refind_with_a_stale_revision_is_a_conflict(store, user_id, monkeypatch):
    client, tid, cid, llm = _setup(store, user_id, monkeypatch)

    r = _refind(client, tid, cid, revision="stale")

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == CONFLICT
    assert llm.picks == 0
    assert _row(client, tid, cid)["examples_pending_for"] == "角色"


def test_p14_someone_elses_card_is_not_found(store, user_id, monkeypatch):
    client, tid, cid, llm = _setup(store, user_id, monkeypatch)
    other = _build_client(store, f"usr_{uuid.uuid4().hex[:8]}", monkeypatch,
                          distiller=_distiller(llm))

    refind = other.post(f"/api/distill/card/{cid}/examples/refind", json={"revision": "x"})
    dismiss = other.post(f"/api/distill/card/{cid}/examples/dismiss")

    assert (refind.status_code, dismiss.status_code) == (404, 404)
    assert _row(client, tid, cid)["examples_pending_for"] == "角色"
    assert llm.picks == 0


# ── 贴示例是「换掉」────────────────────────────────────────────────────────

def test_p15_attaching_replaces_examples_already_under_the_phases():
    card = CharacterCard.model_validate({"name": "角色", "character_arc": {
        "source_fingerprint": "fp",
        "phases": [{"label": "早", "state": "早年", "start": 0,
                    "overlay": {"dialogue_examples": ["路人：旧的。\n角色：上一次贴的。"],
                                "values": ["留着"]}},
                   {"label": "晚", "state": "晚年", "start": 100000,
                    "overlay": {"dialogue_examples": ["路人：也是旧的。\n角色：别的阶段的。"]}}]}})

    out = _distiller().attach_dialogue_examples(card, _BODY, "角色", [], ROSTER)

    assert out.character_arc.phases[0].overlay == {"dialogue_examples": [EXAMPLE], "values": ["留着"]}
    assert out.character_arc.phases[1].overlay == {}
    assert out.dialogue_examples == []


# ── 不跟着卡的内容走 ───────────────────────────────────────────────────────

def test_p16_pending_is_not_part_of_the_card_content(store, user_id, monkeypatch):
    client, tid, cid, _ = _setup(store, user_id, monkeypatch)
    row = _row(client, tid, cid)

    exported = client.get(f"/api/distill/cards/{cid}/export?format=raw")

    assert row["examples_pending_for"] == "角色"
    assert "examples_pending_for" not in _card_json(row)
    assert "examples_pending_for" not in exported.json()
    assert row["revision"] == card_revision(_run_async(store.get_card_owned(cid, user_id))["card_json"])


def _chat_ext(card: CharacterCard) -> str:
    from core.arc_view import project_card
    from core.context_engine import ContextEngine

    return ContextEngine(project_card(card, None)[0], rag=None, storage=None)._build_card_ext()


def test_p17_chat_uses_examples_when_present_and_skips_the_section_when_absent():
    phased = CharacterCard.model_validate({"name": "角色", "dialogue_examples": [EXAMPLE], "character_arc": {
        "source_fingerprint": "fp",
        "phases": [{"label": "早", "state": "早年", "start": 0},
                   {"label": "晚", "state": "晚年", "start": 100000}]}})

    assert "【对话风格示范】\n" + EXAMPLE in _chat_ext(CharacterCard(name="角色", dialogue_examples=[EXAMPLE]))
    assert "【对话风格示范】\n" + EXAMPLE in _chat_ext(phased)      # 手填在顶层的，对有阶段的卡也生效
    assert "【对话风格示范】" not in _chat_ext(CharacterCard(name="角色"))
