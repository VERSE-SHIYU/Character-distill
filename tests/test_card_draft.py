# -*- coding: utf-8 -*-
"""模型输出契约（草稿）→ 存卡：分发、各入口接线、唯一出口、PG 往返。

spec：docs/specs/arc-behaviors-draft.md §4（草稿契约）+ docs/specs/arc-phase-anchoring.md §3
（位置校正接在唯一出口上）。草稿契约的判别器全部在本文件 —— 它是变异驱动
`tests/perf/card_draft_mutations.py` 的覆盖域（`tests/test_lock_coverage.py` 按文件计域）。
**位置校正本身**（阶段范围、摘录核对、兜底、监测）的判别器在 `tests/test_phase_anchoring.py`
（驱动 `arc_phase_anchoring_mutations.py`）；本文件里的草稿都带锚点，使位置检查照常跑。
路由两个消费点（R1/R2）的通用桩从 `test_distill_task_api` 复用，不复制。
"""
import copy
import json
import logging
import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from core.card_draft import CardDraft, card_from_draft, draft_schema
import deps
from core.distiller import DistillError, Distiller, format_prompt_after
from routers import distill as D
from test_distill_task_api import _build_client, _CardStubDistiller, _FakeStore, _run_to_card
from core.schema import FORMAT_GROUPS, CharacterCard

REPO = Path(__file__).resolve().parent.parent

# 三段，各有独特 token：锚点位置与阶段范围精确可算。
# normalize 后 = 开头甲甲甲中间乙乙乙结尾丙丙丙  (len 15)，三字起点 0 / 5 / 10。
_SRC = "开头甲甲甲。中间乙乙乙。结尾丙丙丙。"
_Q1, _Q2, _Q3 = "开头甲甲甲", "中间乙乙乙", "结尾丙丙丙"
_QBY = {1: _Q1, 2: _Q2, 3: _Q3}


def _phases(n):
    """n 个阶段，锚点取各段起点（阶段 1 留空 → 从 0 起）。"""
    return [{"label": f"阶段{i}", "state": f"时期{i}",
             "anchor": "" if i == 1 else _QBY[i]} for i in range(1, n + 1)]


# 孔乙己：两个阶段。A 只在阶段 1，B 两个阶段都有，C 只在阶段 2。
KONG_DRAFT = {
    "name": "孔乙己",
    "character_arc": {"axis": "从死要面子到不再分辩", "phases": [
        {"label": "死要面子", "state": "断腿之前，常来店里喝酒", "anchor": ""},
        {"label": "不再分辩", "state": "被打折腿之后，坐着用手走来", "anchor": _Q3},
    ]},
    "situation_behaviors": [
        {"situation": "被人当众取笑", "behavior": "涨红了脸争辩",
         "occurrences": [{"phase": 1, "quote": _Q1}]},
        {"situation": "讨酒", "behavior": "排出九文大钱",
         "occurrences": [{"phase": 1, "quote": _Q1}, {"phase": 2, "quote": _Q3}]},
        {"situation": "被问腿怎么断的", "behavior": "低声说跌断，不再分辩",
         "occurrences": [{"phase": 2, "quote": _Q3}]},
    ],
}
_A = {"situation": "被人当众取笑", "behavior": "涨红了脸争辩", "source_quote": _Q1}
_B = {"situation": "讨酒", "behavior": "排出九文大钱", "source_quote": _Q1}
_C = {"situation": "被问腿怎么断的", "behavior": "低声说跌断，不再分辩", "source_quote": _Q3}


def _assert_kong(card: CharacterCard) -> None:
    """KONG_CARD：顶层只有 B；阶段 1 只有 A；阶段 2 只有 C；任何做法里没有 occurrences。"""
    dump = card.model_dump()
    assert dump["situation_behaviors"] == [_B]
    assert [p["behaviors"] for p in dump["character_arc"]["phases"]] == [[_A], [_C]]
    assert [p["label"] for p in dump["character_arc"]["phases"]] == ["死要面子", "不再分辩"]


def _draft(behaviors, phases=2):
    d = copy.deepcopy(KONG_DRAFT)
    d["character_arc"]["phases"] = _phases(phases)
    d["situation_behaviors"] = behaviors
    return d


def _row(phases, situation="s"):
    return {"situation": situation, "behavior": "b",
            "occurrences": [{"phase": p, "quote": _QBY.get(p, "")} for p in phases]}


# ── 4.1 单元 ──────────────────────────────────────────────────────────

def test_u1_tagged_in_every_phase_goes_top_level():
    card = card_from_draft(_draft([_row([1, 2])]), _SRC)
    assert len(card.situation_behaviors) == 1
    assert all(p.behaviors == [] for p in card.character_arc.phases)


def test_u2_tagged_in_some_phases_goes_under_them_in_order():
    card = card_from_draft(_draft([_row([2], "x"), _row([2], "y")], phases=3), _SRC)
    assert card.situation_behaviors == []
    assert [b.situation for b in card.character_arc.phases[1].behaviors] == ["x", "y"]
    assert card.character_arc.phases[0].behaviors == card.character_arc.phases[2].behaviors == []


def test_u3_out_of_range_numbers_are_dropped_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card = card_from_draft(_draft([_row([1, 9])]), _SRC)
    assert [len(p.behaviors) for p in card.character_arc.phases] == [1, 0]
    assert sum("阶段编号不合法" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize("phases", [[], [0], [3, 9]])
def test_u4_no_valid_number_retracts_the_row_with_a_warning(caplog, phases):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card = card_from_draft(_draft([_row(phases)]), _SRC)
    assert card.situation_behaviors == []
    assert all(p.behaviors == [] for p in card.character_arc.phases)
    assert sum("阶段编号不合法" in r.getMessage() for r in caplog.records) == 1


def test_u5_card_without_phases_keeps_everything_top_level(caplog):
    with caplog.at_level(logging.WARNING, logger="core.card_draft"):
        card = card_from_draft(_draft([_row([1]), _row([])], phases=0), _SRC)
    assert len(card.situation_behaviors) == 2
    assert not caplog.records


@pytest.mark.parametrize("arc", [[], ["起初冷漠", "学会信任"]])
def test_u6_legacy_arc_shapes_still_convert(arc):
    card = card_from_draft({"name": "x", "character_arc": arc}, "")
    assert [p.state for p in card.character_arc.phases] == arc


def test_u7_wrong_shape_raises_validation_error():
    with pytest.raises(ValidationError):
        card_from_draft({"name": "x", "situation_behaviors": "不是列表"}, "")


def test_u8_schema_sent_to_the_model_is_the_draft():
    full, g6 = draft_schema(), draft_schema("G6")
    assert full["title"] == "CharacterCard"
    assert "occurrences" in full["$defs"]["DraftBehavior"]["properties"]
    assert "anchor" in full["$defs"]["DraftPhase"]["properties"]
    assert "behaviors" not in full["$defs"]["DraftPhase"]["properties"]
    for group, fields in FORMAT_GROUPS.items():
        assert set(draft_schema(group)["properties"]) == set(fields), group
    assert g6["properties"]["situation_behaviors"]["items"]["$ref"].endswith("/DraftBehavior")


def test_u9_stored_card_has_no_phase_tags_and_round_trips():
    dump = card_from_draft(KONG_DRAFT, _SRC).model_dump()
    rows = dump["situation_behaviors"] + [b for p in dump["character_arc"]["phases"] for b in p["behaviors"]]
    assert rows and all("occurrences" not in b and "anchor" not in p for p in dump["character_arc"]["phases"]
                        for b in [rows])  # 存卡不带草稿字段
    assert CharacterCard.model_validate(dump).model_dump() == dump


def test_kong_draft_converts_to_kong_card():
    _assert_kong(card_from_draft(KONG_DRAFT, _SRC))


# ── 4.2 调用点矩阵：distiller 五个入口 ────────────────────────────────

_TEXT = "AB" * 5000          # 分片阈值 3000 → 4 片（≤80，走单次合并）
_USAGE = {"prompt_tokens": 1, "completion_tokens": 1}


def _distiller(monkeypatch, *, chunked: bool) -> Distiller:
    """真 Distiller；LLM 打桩。`chunked` 决定走分片还是一次读完。"""
    monkeypatch.setattr("core.distiller.try_record_usage", lambda **kw: None)

    async def _close():
        return None

    async def _map(system, messages, max_tokens=None, client=None, **kw):
        return ("片段分析", _USAGE)

    client = MagicMock()
    client.close = _close
    llm = MagicMock()
    llm.last_usage = None
    llm.model = "m"
    llm._make_async_client = MagicMock(return_value=client)
    llm.async_chat = _map
    d = Distiller(llm=llm, config_path=None)
    d._chunk_size = 3000
    d._longctx_threshold = 0 if chunked else 10 ** 9
    return d


def _reply(monkeypatch, d: Distiller, payload) -> None:
    """同步入口的格式化调用一律回 `payload`（模型原文）。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(d, "_chat_accounted", lambda *a, **kw: (text, False))
    monkeypatch.setattr(d, "_do_reduce", lambda *a, **kw: "合并档案")


_BAD = {"name": "孔乙己", "situation_behaviors": "不是列表"}


class TestEntries:
    def test_distill_sync(self, monkeypatch):
        d = _distiller(monkeypatch, chunked=False)
        _reply(monkeypatch, d, KONG_DRAFT)
        _assert_kong(d.distill(_SRC, "孔乙己"))
        _reply(monkeypatch, d, _BAD)
        with pytest.raises(DistillError):
            d.distill(_SRC, "孔乙己")

    def test_longcontext_sync(self, monkeypatch):
        d = _distiller(monkeypatch, chunked=False)
        _reply(monkeypatch, d, KONG_DRAFT)
        _assert_kong(d.distill_incremental(_TEXT, "AB"))
        _reply(monkeypatch, d, _BAD)
        with pytest.raises(DistillError):
            d.distill_incremental(_TEXT, "AB")

    def test_incremental_format_sync(self, monkeypatch):
        d = _distiller(monkeypatch, chunked=True)
        _reply(monkeypatch, d, KONG_DRAFT)
        _assert_kong(d.distill_incremental(_TEXT, "AB"))
        _reply(monkeypatch, d, _BAD)
        with pytest.raises(DistillError):
            d.distill_incremental(_TEXT, "AB")

    def test_stream_longcontext_yields_draft(self, monkeypatch):
        d = _distiller(monkeypatch, chunked=False)
        body = json.dumps(KONG_DRAFT, ensure_ascii=False)

        def _stream(system, messages, max_tokens=None, **kw):
            for i in range(0, len(body), 50):
                yield body[i:i + 50]
            return _USAGE

        d._llm.chat_stream_long = _stream
        out = "".join(p for p in d.distill_incremental_stream(_TEXT, "AB") if isinstance(p, str))
        assert json.loads(out) == KONG_DRAFT          # 原样交出草稿，occurrences 未被转换
        _assert_kong(card_from_draft(json.loads(out), _SRC))

    @staticmethod
    def _grouped(monkeypatch, draft):
        """每组回本组全部字段（缺的用草稿默认值补齐），同 test_distiller_routing 的按组回包。"""
        d = _distiller(monkeypatch, chunked=True)
        draft = {**CardDraft(name=draft["name"]).model_dump(), **draft}

        def _stream(system, messages, max_tokens=None, **kw):
            if "你正在整合关于" in system:
                yield "合并结果"
                return _USAGE
            group = re.search(r"CharacterCard\[(G\d)\]", system).group(1)
            yield json.dumps({k: draft[k] for k in FORMAT_GROUPS[group]}, ensure_ascii=False)
            return _USAGE

        d._llm.chat_stream_long = _stream
        return list(d.distill_incremental_stream(_TEXT, "AB"))

    def test_stream_grouped_yields_draft(self, monkeypatch):
        pieces = self._grouped(monkeypatch, KONG_DRAFT)
        out = [p for p in pieces if isinstance(p, str)]
        assert len(out) == 1
        draft = json.loads(out[0])
        assert draft["situation_behaviors"] == KONG_DRAFT["situation_behaviors"]
        assert CardDraft.model_validate(draft)
        _assert_kong(card_from_draft(draft, _SRC))

        bad = self._grouped(monkeypatch, _BAD)
        assert not [p for p in bad if isinstance(p, str)]
        assert any(isinstance(p, dict) and "error" in p for p in bad)


# ── 4.2 调用点矩阵：路由两个消费点（R1 /start 后台任务、R2 /run_stream）──────
# 蒸馏流交出的是草稿（做法带 occurrences），转成卡只经 `card_from_draft`。这里不用
# SQLiteStore：store 用内存 `_FakeStore`，名单入口打桩，TextManager 用桩记下收到的卡。

class _DraftStubDistiller(_CardStubDistiller):
    def __init__(self, draft):
        super().__init__()
        self.CARD = draft


async def _stub_roster(storage, distiller, text_id, user_id, content):
    return distiller.identify_characters(content)


class _DraftRouteStore:
    """`/run_stream` 前半段只读这两样；其余落库由 TextManager 桩接走。"""

    def __init__(self, uid):
        self.uid = uid

    async def get_text_owned(self, text_id, user_id):
        return {"id": text_id, "content": _SRC, "text_type": "story"} if user_id == self.uid else None

    async def get_user_api_config(self, user_id):
        return {}


class TestDraftConversion:
    def test_bg_task_converts_draft(self, monkeypatch):
        monkeypatch.setattr(D, "resolve_characters", _stub_roster)
        saved, _ = _run_to_card(monkeypatch, _FakeStore(), _DraftStubDistiller(KONG_DRAFT),
                                content=_SRC)
        assert len(saved) == 1
        _assert_kong(saved[0])

        saved, snaps = _run_to_card(monkeypatch, _FakeStore(), _DraftStubDistiller(_BAD),
                                    content=_SRC, task_id="tCardBad")
        assert saved == []
        assert {"status": "error", "message": "蒸馏失败：数据校验错误，请重试"}.items() <= snaps[-1].items()

    def test_run_stream_converts_draft(self, monkeypatch):
        uid = f"usr_{uuid.uuid4().hex[:8]}"
        saved: list = []

        class _TM:
            async def save_distilled_card(self, text_id, card, user_id, *,
                                          embedding_key="", embedding_region=""):
                saved.append(card)
                return {"card_id": "card_x"}

        async def _no_llm(user_id, storage=None):
            return None

        monkeypatch.setattr(D, "resolve_characters", _stub_roster)
        monkeypatch.setattr(deps, "get_user_llm", _no_llm)
        monkeypatch.setattr(deps, "get_text_manager", lambda llm=None: _TM())
        client = _build_client(_DraftRouteStore(uid), uid)

        def _frames(draft):
            monkeypatch.setattr(deps, "get_distiller", lambda llm=None: _DraftStubDistiller(draft))
            resp = client.post("/api/distill/run_stream", json={"text_id": "txt1", "character_name": "乙"})
            assert resp.status_code == 200
            return [json.loads(l[6:]) for l in resp.text.splitlines() if l.startswith("data: ")]

        frames = _frames(KONG_DRAFT)
        assert frames[-1].get("done") is True
        assert len(saved) == 1
        _assert_kong(saved[0])

        # 校验失败即收场：错误帧是最后一帧，之后不会再落库（再落库只能发生在错误帧之后，
        # 那时上一句已经红了 —— 故不再单列「落库次数」，它在任何合法变异下都撞不到）。
        frames = _frames(_BAD)
        assert frames[-1] == {"error": "蒸馏失败：数据校验错误，请重试"}


def test_g6_template_tags_behaviors_with_phase_numbers():
    """G6 提示词模板里的示例做法带 `occurrences` 的阶段编号 —— 模型照模板写草稿。"""
    assert '"phase": 1' in format_prompt_after("G6")


# ── 4.3 结构：两处唯一出处 ─────────────────────────────────────────────

def _code_lines(path: Path):
    """去掉 # 注释后的行；docstring 里的提及不带括号，不影响下面的匹配。"""
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        yield no, line.split("#", 1)[0]


def _calls(pattern: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for root in ("core", "web"):
        for path in (REPO / root).rglob("*.py"):
            for _, code in _code_lines(path):
                if re.search(pattern, code) and not re.search(r"\bdef\s", code):
                    rel = path.relative_to(REPO).as_posix()
                    found[rel] = found.get(rel, 0) + 1
    return found


def test_s1_schema_for_the_model_has_one_source():
    assert _calls(r"model_json_schema\(") == {"core/card_draft.py": 1}


# ── 4.3 PG 往返 ───────────────────────────────────────────────────────

async def test_p1_converted_card_round_trips_through_postgres():
    from conftest import TEST_DATABASE_URL
    from storage.postgres_store import PostgresStore

    store = PostgresStore(TEST_DATABASE_URL)
    await store._ensure_initialized()
    try:
        card = card_from_draft(KONG_DRAFT, _SRC)
        tag = uuid.uuid4().hex[:12]
        user = await store.create_user(f"u_{tag}", f"n_{tag}", "x")
        text = await store.save_text(f"t_{tag}", "f.txt", "正文", user_id=user["id"])
        saved = await store.save_card(f"c_{tag}", text["id"], card.name, card.model_dump_json(), user["id"])
        row = await store.get_card_unscoped(saved["id"])
        assert CharacterCard.model_validate_json(row["card_json"]) == card
    finally:
        await store.close()
