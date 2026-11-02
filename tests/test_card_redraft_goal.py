# -*- coding: utf-8 -*-
"""目标检查：用存下的草稿重新出卡，不重蒸（spec card-redraft §1）。

G1、G2、G4 先 xfail(strict=True)；G3 是结构锁，现状即绿。
替身与搭 app 全部复用 `test_distill_draft_persist` / `test_distill_task_api`。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from core.card_draft import card_from_draft
from core.card_quotes import retract_unverified
from core.card_relationships import dedupe_relationship_targets
from core.retro_regions import NOT_DETECTED
from core.schema import POST_FORMAT_FIELDS, CharacterCard
from routers import distill as D
from storage.sqlite_store import SQLiteStore
from test_distill_draft_persist import (
    _CARD_NAME,
    _DISTILL_NAME,
    _SRC,
    _DraftStub,
    _draft,
    _install,
    _load_payload,
    _only_card_id,
    _run_start,
    _seed_text,
)
from test_distill_task_api import _build_client, _run_async

pytestmark = pytest.mark.usefixtures("embedding_key_configured")
_NO_DRAFT = "没有存草稿"


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"redraft_{uuid.uuid4().hex}.db"))


def _redraft(client, card_id: str):
    return client.post(f"/api/distill/card/{card_id}/redraft")


def _seed_card_without_draft(store, uid: str, tid: str) -> str:
    card_id = f"card_{uuid.uuid4().hex[:12]}"
    card = CharacterCard(name=_CARD_NAME)
    _run_async(store.save_card(card_id, tid, card.name, card.model_dump_json(), uid))
    return card_id


class _NoRedistillStub(_DraftStub):
    """重新出卡只许用存下的草稿：蒸馏与补关系被调用即断言失败。"""

    def __init__(self, draft: dict):
        super().__init__(draft)

    def distill_incremental_stream(self, *_a, **_kw):
        raise AssertionError("重新出卡不应重蒸")

    def _relationships_batched(self, *_a, **_kw):
        # 段 2 起关系在 distiller 内补全（`fill_relationships` 已删）；守的仍是
        # 「重新出卡不补关系」，只是名字换成真正干这件事的方法（复审发现 7）。
        raise AssertionError("重新出卡不应补关系")



def _task_rows_of_text(store, tid: str) -> list[dict]:
    return [r for r in _run_async(store.list_distill_tasks()) if r.get("text_id") == tid]


def _assert_no_task_row(store, tid: str) -> None:
    assert _task_rows_of_text(store, tid) == [], "失败请求不该建任务行"


def test_g1_redraft_uses_stored_draft_without_redistilling(monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)

    resp, _ = _run_start(monkeypatch, store, uid, tid)
    assert resp.status_code == 200, resp.text
    card_id = _only_card_id(store, tid, uid)

    record = _run_async(store.get_card_owned(card_id, uid))
    edited = json.loads(record["card_json"])
    hand_background = "手工改过、与草稿不一致的背景"
    edited["background"] = hand_background
    updated = _run_async(
        store.update_card(card_id, edited, expected=record["card_json"]))
    assert updated is not None, "前置：手工改卡应写进库"

    # 只在本用例里给存下的草稿一个独有背景（不碰共用的 `_draft()`）：
    # 出卡必须取草稿的这个值，而不是手改卡上的值。
    payload = _load_payload(store, card_id, uid)
    draft_background = "只在这条用例里、存下的草稿背景"
    payload["draft"]["background"] = draft_background
    _run_async(store.save_card_draft(
        card_id, json.dumps(payload, ensure_ascii=False)))

    stub = _NoRedistillStub(_draft())
    _llm, threads = _install(monkeypatch, store, stub)

    resp = _redraft(_build_client(store, uid), card_id)
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    for t in threads:
        t.join(timeout=30)

    body = _build_client(store, uid).get(f"/api/distill/task/{task_id}").json()
    assert body["status"] == "done", body
    assert body["card_id"] == card_id, "重新出卡应覆盖同一张卡"

    stored = _run_async(store.get_card_owned(card_id, uid))
    stored_card = CharacterCard.model_validate_json(stored["card_json"])
    assert stored_card.background == draft_background, "出卡没取存下草稿的 background"
    assert stored_card.background != hand_background, "手改的 background 没被存下的草稿盖掉"

    payload = _load_payload(store, card_id, uid)
    rebuilt, _ = dedupe_relationship_targets(
        card_from_draft(payload["draft"], _SRC, retro=NOT_DETECTED))
    rebuilt, _ = retract_unverified(rebuilt, _SRC)
    a, b = rebuilt.model_dump(), stored_card.model_dump()
    for field in POST_FORMAT_FIELDS:
        a.pop(field, None)
        b.pop(field, None)
    assert a == b, "存下的草稿还原不出库里那张卡"


def test_g2_redraft_requires_own_card_with_draft(monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)
    card_id = _seed_card_without_draft(store, uid, tid)

    _llm, threads = _install(monkeypatch, store, _DraftStub(_draft()))

    resp = _redraft(_build_client(store, uid), card_id)
    assert resp.status_code == 409, resp.text
    assert _NO_DRAFT in resp.json()["detail"]

    other = f"usr_{uuid.uuid4().hex[:8]}"
    resp = _redraft(_build_client(store, other), card_id)
    assert resp.status_code == 404, resp.text

    assert threads == [], "失败请求不该起后台线程"
    _assert_no_task_row(store, tid)


def test_g4_redraft_shares_the_per_user_gate(monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)
    card_id = _seed_card_without_draft(store, uid, tid)
    payload = json.dumps(
        {"character": _DISTILL_NAME, "draft": _draft()}, ensure_ascii=False)
    _run_async(store.save_card_draft(card_id, payload))

    _llm, threads = _install(monkeypatch, store, _DraftStub(_draft()))

    assert D._reserve_user_slot(uid, (tid, _DISTILL_NAME)) is None, \
        "前置：本用例应能占住该用户这次 redraft 的目标槽位"
    try:
        resp = _redraft(_build_client(store, uid), card_id)
    finally:
        D._release_user_slot(uid, (tid, _DISTILL_NAME))

    assert resp.status_code == 429, resp.text
    assert threads == [], "被闸挡下的请求不该起后台线程"


def test_g3_no_third_card_build_path():
    """结构锁：后台线程与 /run_stream 各一处，别出现第三份。"""
    src = (Path(__file__).resolve().parents[1] / "web" / "routers" / "distill.py").read_text(
        encoding="utf-8")
    assert src.count("save_distilled_card(") == 2
