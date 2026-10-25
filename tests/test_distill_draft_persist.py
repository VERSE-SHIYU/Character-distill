# -*- coding: utf-8 -*-
"""目标检查：蒸馏时把模型产出的草稿随卡存库、重蒸覆盖、不出服务端（spec distill-draft-persist §3）。

走真实路径：`TestClient` 打 `/api/distill/start` 与 `/run_stream`，只把出网那几处（LLM）换成
替身、把「贴对话示例」换成不调模型（示例属 `POST_FORMAT_FIELDS`，检查 2 明确排除）。落卡用的是
**真** `TextManager.save_distilled_card` 与**真** `SQLiteStore`，故草稿的写入路径全程未被替身绕过。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid

import pytest

import deps
from core import concurrency as C
from core.card_draft import card_from_draft
from core.retro_regions import NOT_DETECTED
from core.card_quotes import retract_unverified
from core.card_relationships import dedupe_relationship_targets
from core.schema import POST_FORMAT_FIELDS, CharacterCard
from core.text_manager import TextManager
from routers import distill as D
from storage.sqlite_store import SQLiteStore
from test_distill_task_api import (
    _assert_premise_resolves,
    _build_client,
    _install_user_llm,
    _run_async,
)

pytestmark = pytest.mark.usefixtures("embedding_key_configured")

# 三段，各有独特 token：normalize 后 = 开头甲甲甲中间乙乙乙结尾丙丙丙（len 15），起点 0 / 5 / 10。
_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q1, _Q3 = "开头甲甲甲", "结尾丙丙丙"

# 蒸馏时请求的角色名（"乙"）与草稿里模型写的卡名（"孔乙己"）**故意不同**：生产里模型在
# 格式化那一步写 name，代码不保证与请求名一致（`core/examples_pending.py` 模块说明）。
# 检查 1 断言存的是**蒸馏时那个**角色名，让「存 card.name」的变异可辨。
_DISTILL_NAME = "乙"
_CARD_NAME = "孔乙己"

# 只在草稿里、绝不进卡的独有串：挂在「标了阶段 9」的做法上（共 2 个阶段，9 越界），
# 由编号检查（规则 0）把整条撤回。它的引文用**在原文里**的 `_Q1`，故引文核对拦不住它 ——
# 能把这条删掉的只有编号检查。检查 4 靠它证明「导出没带出草稿」，而不是靠卡里恰好没有。
_DRAFT_ONLY = "越界阶段才有的独有做法串"


def _draft() -> dict:
    """有阶段、有关系的草稿（`CardDraft` 真实形态）。S2 起蒸馏流交出的草稿关系已补全
    （关系分批在 distiller 内部做），故关系带 relation。阶段 2 的锚点留空：转卡时整卡跳过位置核对。"""
    return {
        "name": _CARD_NAME,
        "character_arc": {"axis": "从死要面子到不再分辩", "phases": [
            {"label": "死要面子", "state": "断腿之前，常来店里喝酒", "anchor": ""},
            {"label": "不再分辩", "state": "被打折腿之后，坐着用手走来", "anchor": ""},
        ]},
        "situation_behaviors": [
            {"situation": "被人当众取笑", "behavior": "涨红了脸争辩",
             "occurrences": [{"phase": 1, "quote": _Q1}]},
            {"situation": "标错的阶段", "behavior": _DRAFT_ONLY,
             "occurrences": [{"phase": 9, "quote": _Q1}]},
        ],
        "relationships": [
            {"target": "我", "relation": "酒客与伙计", "attitudes": [
                {"phase": 1, "attitude": "不屑", "quote": _Q1},
            ]},
        ],
    }


class _DraftStub:
    """识别给名单、蒸馏流吐草稿 JSON。后置步骤走**真**实现（去重 + 核引文），只把
    「贴对话示例」那一步（要调模型）换成不加示例 —— 示例是后置产出，检查 2 明确排除。"""

    def __init__(self, draft: dict):
        self.draft = draft

    def effective_chunk_size(self, text_type="story"):
        return 3000

    def identify_characters(self, content):
        return [{"name": _CARD_NAME, "aliases": []}]

    def distill_incremental_stream(self, text, character_name, *, aliases=None,
                                   text_type="story", on_chunk_done=None,
                                   resume_candidates=None, retro=None):
        yield {"status": "formatting", "current": 1, "total": 1}
        yield json.dumps(self.draft, ensure_ascii=False)

    def _auto_tag(self, card_dict):
        return []

    def finalize_card(self, card, content, name, aliases=(), roster=()):
        card, _ = dedupe_relationship_targets(card)
        card, _ = retract_unverified(card, content)
        return card


class _Idx:
    def get_rag_for_session(self, *_a, **_kw):
        return None

    def schedule_scene_index(self, *a, **kw):
        pass

    def schedule_text_reindex(self, *a, **kw):
        pass


class _LLM:
    model = "stub"
    last_usage: dict = {}

    def chat(self, *_a, **_kw):
        raise RuntimeError("本用例不调模型")


def _real_tm(store: SQLiteStore) -> TextManager:
    """真 TextManager 绑真 store；只把名单/审核/记忆三个出网或无关处钉住。"""
    tm = TextManager(lambda: store, None, _LLM(), {},
                     indexing_service=_Idx(), memory_manager=None)

    async def _chars(*_a, **_kw):
        return [{"name": _CARD_NAME, "aliases": []}]

    async def _guard(_card):
        from core.moderation.card_guard import GuardVerdict
        return GuardVerdict()

    tm._build_all_characters = _chars
    tm._guard_card = _guard
    tm.memory_for = lambda *_a, **_kw: None
    return tm


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"draft_{uuid.uuid4().hex}.db"))


def _seed_text(store: SQLiteStore, uid: str) -> str:
    tid = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(tid, "src.txt", _SRC, user_id=uid))
    return tid


def _install(monkeypatch, store: SQLiteStore, distiller: _DraftStub):
    """/start 与 /run_stream 都要的那几处替身：出网边界 + 后台投递 + 信号量 + 线程捕获。"""
    llm = _install_user_llm(monkeypatch)
    monkeypatch.setattr("deps.get_distiller", lambda llm=None: distiller)
    monkeypatch.setattr("deps.get_text_manager", lambda llm=None: _real_tm(store))
    monkeypatch.setattr(D, "get_storage", lambda: store)
    monkeypatch.setattr(D, "submit_to_main_loop",
                        lambda coro, timeout=10: asyncio.run(coro))
    monkeypatch.setattr(D, "_DISTILL_SEMAPHORE", threading.Semaphore(1))
    threads: list[threading.Thread] = []
    real_ctx = C.ctx_thread

    def _spy(target, args=(), **kw):
        t = real_ctx(target, args=args, **kw)
        threads.append(t)
        return t

    monkeypatch.setattr("core.concurrency.ctx_thread", _spy)
    return llm, threads


def _run_start(monkeypatch, store, uid, tid, *, character=_DISTILL_NAME, force=False):
    """跑一次真 `/start`（真后台线程），等线程收尾后返回（resp, distiller）。"""
    distiller = _DraftStub(_draft())
    llm, threads = _install(monkeypatch, store, distiller)
    _assert_premise_resolves(store, llm)
    resp = _build_client(store, uid).post(
        "/api/distill/start",
        json={"text_id": tid, "character_name": character, "force": force},
    )
    for t in threads:
        t.join(timeout=30)
    return resp, distiller


def _run_stream(monkeypatch, store, uid, tid, *, character=_DISTILL_NAME):
    distiller = _DraftStub(_draft())
    llm, _threads = _install(monkeypatch, store, distiller)
    assert llm is not None
    resp = _build_client(store, uid).post(
        "/api/distill/run_stream",
        json={"text_id": tid, "character_name": character},
    )
    assert resp.status_code == 200, resp.text
    return distiller


def _only_card_id(store: SQLiteStore, tid: str, uid: str) -> str:
    rows = _run_async(store.list_cards(tid, uid))
    assert len(rows) == 1, f"该恰好落一张卡，实得 {len(rows)}"
    return rows[0]["id"]


def _load_payload(store: SQLiteStore, card_id: str, uid: str) -> dict:
    raw = _run_async(store.load_card_draft_owned(card_id, uid))
    assert raw, "库里这张卡没有草稿"
    return json.loads(raw)


# ── 检查 1：落卡存草稿，解出 {character, draft} ─────────────────────────────

def test_start_persists_the_draft_with_the_distill_time_character(monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)

    resp, distiller = _run_start(monkeypatch, store, uid, tid)
    assert resp.status_code == 200, resp.text

    card_id = _only_card_id(store, tid, uid)
    payload = _load_payload(store, card_id, uid)
    assert set(payload) == {"character", "draft"}
    assert payload["character"] == _DISTILL_NAME, "存的是蒸馏时的角色名，不是卡上的 name"
    # 存的就是蒸馏流交出的那份草稿
    assert payload["draft"] == distiller.draft


def test_run_stream_persists_the_draft(monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)

    _run_stream(monkeypatch, store, uid, tid)

    card_id = _only_card_id(store, tid, uid)
    payload = _load_payload(store, card_id, uid)
    assert payload["character"] == _DISTILL_NAME
    assert payload["draft"]["name"] == _CARD_NAME


def test_other_user_cannot_read_the_draft(monkeypatch, store):
    """读路径漏掉属主过滤时这条红（SQLite 侧）。"""
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)

    resp, _ = _run_start(monkeypatch, store, uid, tid)
    assert resp.status_code == 200, resp.text
    card_id = _only_card_id(store, tid, uid)
    assert _load_payload(store, card_id, uid)  # 属主读得到

    assert _run_async(store.load_card_draft_owned(card_id, f"usr_{uuid.uuid4().hex}")) == "", \
        "非属主读到了别人的草稿"


# ── 检查 2：用存下的草稿能还原出同一张卡 ────────────────────────────────────

def test_stored_draft_round_trips_to_the_saved_card(monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)

    resp, _ = _run_start(monkeypatch, store, uid, tid)
    assert resp.status_code == 200, resp.text
    card_id = _only_card_id(store, tid, uid)

    payload = _load_payload(store, card_id, uid)
    # 存的是那次转卡真正用的草稿 → 照 `finalize_card` 的后置顺序（先去重、再核引文）重跑一遍，
    # 应还原同一张卡
    rebuilt, _ = dedupe_relationship_targets(card_from_draft(payload["draft"], _SRC, retro=NOT_DETECTED))
    rebuilt, _ = retract_unverified(rebuilt, _SRC)

    stored = _run_async(store.get_card_owned(card_id, uid))
    stored_card = CharacterCard.model_validate_json(stored["card_json"])
    a, b = rebuilt.model_dump(), stored_card.model_dump()
    for f in POST_FORMAT_FIELDS:
        a.pop(f, None)
        b.pop(f, None)
    assert a == b, "存下的草稿还原不出库里那张卡"


# ── 检查 3：同角色再蒸一次，草稿随卡覆盖 ────────────────────────────────────

def test_redistill_overwrites_the_draft(monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)

    resp1, _ = _run_start(monkeypatch, store, uid, tid)
    assert resp1.status_code == 200, resp1.text
    card_id = _only_card_id(store, tid, uid)
    first = _load_payload(store, card_id, uid)

    # 第二次：草稿换一份，重蒸应写回同一行、草稿被覆盖
    distiller = _DraftStub(_draft())
    distiller.draft["situation_behaviors"][0]["behavior"] = "第二次蒸出来的做法"
    llm, threads = _install(monkeypatch, store, distiller)
    _assert_premise_resolves(store, llm)
    resp2 = _build_client(store, uid).post(
        "/api/distill/start",
        json={"text_id": tid, "character_name": _DISTILL_NAME, "force": True},
    )
    assert resp2.status_code == 200, resp2.text
    for t in threads:
        t.join(timeout=30)

    assert _only_card_id(store, tid, uid) == card_id, "重蒸应写回同一行"
    second = _load_payload(store, card_id, uid)
    assert second["draft"]["situation_behaviors"][0]["behavior"] == "第二次蒸出来的做法"
    assert second != first


# ── 检查 4：草稿不出服务端（读卡行 / 导出都没有）────────────────────────────

def test_draft_never_leaves_the_server(monkeypatch, store):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)

    resp, _ = _run_start(monkeypatch, store, uid, tid)
    assert resp.status_code == 200, resp.text
    card_id = _only_card_id(store, tid, uid)

    payload = _load_payload(store, card_id, uid)  # 确实存了
    assert any(b.get("behavior") == _DRAFT_ONLY for b in payload["draft"]["situation_behaviors"]), \
        "草稿里本该留着那条越界阶段的做法（撤回发生在转卡，不是存草稿）"

    owned = _run_async(store.get_card_owned(card_id, uid))
    unscoped = _run_async(store.get_card_unscoped(card_id))
    assert "distill_draft" not in owned, "读卡行带出了草稿列"
    assert "distill_draft" not in unscoped, "读卡行带出了草稿列"
    # 编号检查撤回了那条越界做法 → 它不在卡里。这条同时也钉住「导出没带出草稿」：
    # 判别串在卡里必然不出现，若它出现在导出里，只可能是草稿被序列化出去了
    assert _DRAFT_ONLY not in owned["card_json"], "越界阶段的做法该被编号检查撤回、不该进卡"

    export = _build_client(store, uid).get(f"/api/distill/cards/{card_id}/export")
    assert export.status_code == 200, export.text
    assert _DRAFT_ONLY not in export.text, "导出带出了草稿内容"


# ── 检查 5：写草稿失败不连累落卡（对账表「写草稿失败不连累落卡」）─────────────

def test_draft_write_failure_does_not_break_card_save(monkeypatch, store, caplog):
    uid = f"usr_{uuid.uuid4().hex[:8]}"
    tid = _seed_text(store, uid)

    async def _boom(*_a, **_kw):
        raise RuntimeError("draft 列写不进去")

    monkeypatch.setattr(store, "save_card_draft", _boom)

    with caplog.at_level(logging.ERROR, logger="core.nonfatal"):
        resp, _ = _run_start(monkeypatch, store, uid, tid)

    assert resp.status_code == 200, resp.text
    # 卡照常落库 —— 草稿是「以后免费重转」的加分项，失败不该连坐落卡
    card_id = _only_card_id(store, tid, uid)
    assert _run_async(store.load_card_draft_owned(card_id, uid)) == "", "草稿本该没写进去"
    assert any("save card distill draft" in r.getMessage() for r in caplog.records), (
        "写草稿失败没有留下 nonfatal 上报")
