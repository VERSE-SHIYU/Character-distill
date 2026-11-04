# -*- coding: utf-8 -*-
"""D24 目标检查：章节识别改为「代码列候选行 → 模型逐行判 → 代码按序编号」。

基线 f1232de1。第 1–13 条 xfail(strict)：现在没有 `Chapters` / `chapter_headings` /
`distill_chapters` 存储，必须红；第 14 条是「不传章节表就退回正则」的普通对照，改前就绿。

夹具沿用 `tests/fixtures/kongyiji.txt` 与 #248 的四章切法（`test_d24_catchphrase_goal.py`
的 `_GROUPS`），只换成真实书的标题写法。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
import server
from core.distiller import Distiller
from core.phase_excerpt import build_excerpt
from core.retro_regions import NOT_DETECTED
from core.schema import CharacterCard
from deps import get_storage
from routers import distill as D
from routers.auth import get_current_user
from storage.sqlite_store import SQLiteStore

pytestmark = pytest.mark.usefixtures("embedding_key_configured")

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "kongyiji.txt"
# 审计方实测的分章：第 1 章 = 第 0–3 段，第 2 章 = 4–7，第 3 章 = 8–10，第 4 章 = 11–13。
_GROUPS = ((1, 0, 4), (2, 4, 8), (3, 8, 11), (4, 11, 14))
NAME = "孔乙己"
ROSTER = [{"name": NAME, "aliases": []}, {"name": "掌柜", "aliases": []}]
_CN = {1: "一", 2: "二", 3: "三", 4: "四"}


def _paragraphs() -> list[str]:
    with open(_FIXTURE, encoding="utf-8") as f:
        return [line for line in f.read().splitlines() if line.strip()]


def _assemble(titles: dict[int, str], *, prefix: str = "",
              insert: tuple[int, int, str] | None = None) -> str:
    """按四章切法拼正文；`insert=(章序号, 段内位置, 行)` 往该章正文插一行。"""
    paras = _paragraphs()
    chunks = [prefix]
    for n, a, b in _GROUPS:
        body = paras[a:b]
        if insert is not None and insert[0] == n:
            _, at, line = insert
            body = body[:at] + [line] + body[at:]
        chunks.append(titles[n] + "\n" + "\n".join(body) + "\n")
    return "".join(chunks)


def _book_case1() -> str:
    """魔道祖师写法：`　　☆、第1章 …`。"""
    return _assemble({n: f"　　☆、第{n}章 标题{n}" for n in _CN})


def _book_hui() -> str:
    """中文数字 + 回；正文里另插一行「第二回合的交锋结束。」。"""
    return _assemble({n: f"　　第{_CN[n]}回　标题{n}" for n in _CN},
                     insert=(2, 1, "第二回合的交锋结束。"))


def _book_arabic() -> str:
    """阿拉伯数字第1章…第4章（现有正则认得出）。"""
    return _assemble({n: f"　　第{n}章 标题{n}" for n in _CN})


def _book_intro() -> str:
    """第 1 章前 4 行简介，其中一行含「第五章」。"""
    intro = "　　简介甲\n　　简介乙\n　　简介丙\n    第五章：谁说救风尘的只能是男人？\n"
    return _assemble({n: f"　　第{n}章 标题{n}" for n in _CN}, prefix=intro)


def _book_juan() -> str:
    """「第一卷 甲 第一章 …」且每卷章号从 1 重编。"""
    rows = {1: "　　第一卷 甲 第一章 标题1", 2: "　　第一卷 甲 第二章 标题2",
            3: "　　第二卷 乙 第一章 标题3", 4: "　　第二卷 乙 第二章 标题4"}
    return _assemble(rows)


def _heading_texts(book: str) -> set[str]:
    # 夹具里真正的标题都带「标题N」；正文里的「第二回合」与简介里的「第五章」不带。
    return {ln.strip() for ln in book.splitlines() if "标题" in ln}


def _line_starts(book: str, headings: set[str]) -> list[int]:
    out: list[int] = []
    pos = 0
    for line in book.splitlines(keepends=True):
        if line.strip() in headings:
            out.append(pos)
        pos += len(line)
    return out


def _expected(book: str) -> list[tuple[int, int]]:
    return list(enumerate(_line_starts(book, _heading_texts(book)), 1))


def _Chapters():
    from core.chapters import Chapters
    return Chapters


def _detect_via(book: str, judge) -> object:
    """走生产入口 `Distiller.detect_chapters`，LLM 替身把判定委托给 `judge`。"""
    llm = _HeadingLLM(judge)
    return Distiller(llm=llm, config_path=None).detect_chapters(book)


class _Judge:
    """只认「本行」是否在预设标题集合里；渲染格式不对就说明调用方没照探针写。"""

    def __init__(self, headings: set[str], *, boom: bool = False) -> None:
        self.headings = set(headings)
        self.boom = boom
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str, function=None) -> str:
        self.calls.append((system, user))
        if self.boom:
            raise RuntimeError("模型挂了")
        lines = re.findall(r"^\s*本行：(.*)$", user, re.M)
        return json.dumps(
            {f"h{i}": ("章节标题" if ln.strip() in self.headings else "不是")
             for i, ln in enumerate(lines, 1)}, ensure_ascii=False)


class _HeadingLLM:
    model = "fake-d24-headings"
    last_usage = None

    def __init__(self, judge) -> None:
        self._judge = judge
        self.names: list[str] = []

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        self.names.append(function["name"])
        return json.loads(self._judge(system_prompt, messages[0]["content"]))


class _Detector:
    """`Distiller` 的替身：只提供 detect_chapters / detect_retro_regions 等入口。"""

    def __init__(self, judge) -> None:
        self.judge = judge
        self.calls = 0

    def detect_chapters(self, content):
        from core.chapter_headings import detect
        self.calls += 1
        return detect(content, judge=self.judge)

    def detect_retro_regions(self, content, chapters=None):
        return NOT_DETECTED


class _ChapterStore:
    """存储替身：照生产语义（版本不符返回 ""，写回记一笔）。"""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[int, str]] = {}
        self.saved: list[tuple[str, str, int]] = []

    async def get_chapters_owned(self, text_id, user_id, *, version):
        row = self.rows.get(text_id)
        return row[1] if row and row[0] == version else ""

    async def save_chapters(self, text_id, chapters_json, *, version):
        self.rows[text_id] = (version, chapters_json)
        self.saved.append((text_id, chapters_json, version))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _resolve_chapters():
    from core.chapters_cache import resolve_chapters
    return resolve_chapters


# ── 1. 魔道祖师写法 ────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g1_mdzs_heading_style():
    book = _book_case1()
    judge = _Judge(_heading_texts(book))
    got = _detect_via(book, judge)
    assert list(got.starts) == _expected(book)


# ── 2. 中文数字 + 回；正文里的「第二回合」不算 ────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g2_hui_style_skips_prose_line():
    book = _book_hui()
    judge = _Judge(_heading_texts(book))
    got = _detect_via(book, judge)
    assert list(got.starts) == _expected(book)
    decoy = book.index("第二回合的交锋结束。")
    assert decoy not in [s for _, s in got.starts]


# ── 3. 阿拉伯数字 + 简介里的「第五章」不算；结果与 from_text 逐项相同 ──────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g3_arabic_matches_regex():
    book = _book_intro()
    judge = _Judge(_heading_texts(book))
    got = _detect_via(book, judge)
    Chapters = _Chapters()
    assert list(got.starts) == list(Chapters.from_text(book).starts)
    assert list(got.starts) == _expected(book)


# ── 4. 卷章写一行、每卷重编号：代码按出现顺序编号 1..K ───────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g4_volume_and_chapter_renumbered():
    book = _book_juan()
    judge = _Judge(_heading_texts(book))
    got = _detect_via(book, judge)
    assert [n for n, _ in got.starts] == [1, 2, 3, 4]
    assert [s for _, s in got.starts] == _line_starts(book, _heading_texts(book))


# ── 5. 没有候选行：空表，0 次判定 ─────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g5_no_candidates_is_empty_and_silent():
    book = "\n".join(_paragraphs())
    judge = _Judge(set(), boom=True)          # 一调就炸：证明 0 次调用
    got = _detect_via(book, judge)
    assert list(got.starts) == []
    assert judge.calls == []


# ── 6. 同一本书两次：第二次读缓存，0 次判定 ───────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g6_second_resolve_reads_cache():
    book = _book_case1()
    judge = _Judge(_heading_texts(book))
    detector = _Detector(judge)
    store = _ChapterStore()
    resolve = _resolve_chapters()
    first = _run(resolve(store, detector, "t1", "u1", book))
    calls = len(judge.calls)
    assert calls > 0
    second = _run(resolve(store, detector, "t1", "u1", book))
    assert len(judge.calls) == calls, "第二次又调了模型"
    assert list(second.starts) == list(first.starts)


# ── 7. 判定抛异常：退回正则、不写缓存、1 条 WARNING ───────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g7_failure_falls_back_and_warns(caplog):
    book = _book_arabic()
    Chapters = _Chapters()
    detector = _Detector(_Judge(_heading_texts(book), boom=True))
    store = _ChapterStore()
    resolve = _resolve_chapters()
    with caplog.at_level(logging.WARNING):
        got = _run(resolve(store, detector, "t1", "u1", book))
    assert list(got.starts) == list(Chapters.from_text(book).starts)
    assert store.saved == [], "判定失败还写了缓存"
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "章节" in r.getMessage()]
    assert len(warns) == 1, [r.getMessage() for r in caplog.records]


# ── 8. 缓存与当前原文对不上：当无缓存，重新判一轮 ─────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g8_stale_cache_is_recomputed():
    from core.chapters import CHAPTERS_VERSION
    book = _book_case1()
    judge = _Judge(_heading_texts(book))
    detector = _Detector(judge)
    store = _ChapterStore()
    store.rows["t1"] = (CHAPTERS_VERSION, json.dumps([[0, "第九章"]], ensure_ascii=False))
    got = _run(_resolve_chapters()(store, detector, "t1", "u1", book))
    assert judge.calls, "缓存对不上却没重算"
    assert list(got.starts) == _expected(book)


# ── 9. build_excerpt 读章节表 ─────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g9_excerpt_uses_given_chapters():
    book = _book_case1()
    chapters = _detect_via(book, _Judge(_heading_texts(book)))
    excerpt = build_excerpt(book, [NAME], chapters=chapters)
    assert excerpt is not None
    assert "【第1章】" in excerpt


# ── 10. finalize_card 按章统计：认得出章节，「酒」不再全删 ───────────────


class _VerifyAllLLM:
    model = "fake-d24-verify"
    last_usage = None

    def __init__(self, subject: str) -> None:
        self.subject = subject

    def select_by_schema(self, system_prompt, messages, function, max_tokens=None):
        if function["name"] == "verify_catchphrase":
            n = len([k for k in function["parameters"]["properties"]])
            return {f"who{i}": self.subject for i in range(1, n + 1)}
        return {k: (0 if k.startswith("pick") else "无法判断")
                for k in function["parameters"]["properties"]}


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g10_catchphrase_kept_with_chapters():
    book = _book_case1()
    chapters = _detect_via(book, _Judge(_heading_texts(book)))
    distiller = Distiller(llm=_VerifyAllLLM(NAME), config_path=None)
    card = CharacterCard.model_validate(
        {"name": NAME, "speaking_style": {"catchphrases": ["酒"]}})
    out = distiller.finalize_card(card, book, NAME, [], ROSTER, chapters=chapters)
    assert out.speaking_style.catchphrases == ["酒"]


# ── 11. retro_regions.detect 的目录来自给定章节表 ──────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g11_retro_directory_uses_given_chapters():
    from core import retro_regions
    book = _book_case1()
    chapters = _detect_via(book, _Judge(_heading_texts(book)))
    seen: dict[str, str] = {}

    def call(system: str, user: str) -> str:
        seen["system"] = system
        return '{"ranges": []}'

    retro_regions.detect(book, prefix="", call=call, chapters=chapters)
    lines = [ln for ln in seen["system"].splitlines() if re.match(r"^第\d+章：", ln)]
    assert len(lines) == 4, lines
    assert lines[0].startswith("第1章：") and lines[-1].startswith("第4章：")


# ── 12. 三条通道收到 resolve_chapters 返回的同一个对象 ────────────────────


class _SavingTM:
    async def save_distilled_card(self, *a, **kw):
        return {"card_id": f"card_{uuid.uuid4().hex[:8]}"}


class _SpyDistiller:
    def __init__(self, chapters) -> None:
        self._chapters = chapters
        self.seen: list[tuple[str, object]] = []
        self.llm = object()

    def detect_chapters(self, content):
        return self._chapters

    def detect_retro_regions(self, content, chapters=None):
        return NOT_DETECTED

    def identify_characters(self, content):
        return [{"name": NAME}, {"name": "掌柜"}]

    def identify_roster(self, content):
        from core.distiller import IdentifyResult
        return IdentifyResult(self.identify_characters(content), trace=None)

    def distill_incremental(self, content, character_name, aliases=None, *,
                            chapters=None, retro=NOT_DETECTED):
        self.seen.append(("sync", chapters))
        return CharacterCard.model_validate({"name": character_name})

    def distill_incremental_stream(self, content, name, *, aliases=None, text_type="story",
                                   on_chunk_done=None, resume_candidates=None,
                                   retro=NOT_DETECTED, chapters=None):
        self.seen.append(("stream", chapters))
        yield json.dumps({"name": name}, ensure_ascii=False)

    def draft_to_card(self, data, source_text, *, retro=NOT_DETECTED, chapters=None):
        self.seen.append(("draft", chapters))
        return CharacterCard.model_validate(data)

    def finalize_card(self, card, content, name, aliases=(), roster=(), *, chapters=None):
        self.seen.append(("final", chapters))
        return card

    def _auto_tag(self, card_dict):
        return []


def _seed(store: SQLiteStore, uid: str, body: str) -> str:
    tid = f"txt_{uuid.uuid4().hex}"
    _run(store.save_text(tid, "src.txt", body, user_id=uid))
    return tid


def _build_client(store, uid, monkeypatch, *, distiller, tm):
    app = FastAPI()
    app.include_router(D.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[D.get_sessions] = lambda: {}
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "testuser", "is_admin": False}
    server.register_domain_error_handlers(app)

    async def _llm(*a, **kw):
        return object()

    monkeypatch.setattr(deps, "get_user_llm", _llm)
    monkeypatch.setattr(deps, "get_distiller", lambda *a, **kw: distiller)
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: tm)
    monkeypatch.setattr(D, "get_indexing_service", lambda: None)
    return TestClient(app, raise_server_exceptions=False)


def _run_bg(store, uid, tid, body, spy, monkeypatch):
    task_id = f"dt_{uuid.uuid4().hex}"
    _run(store.create_distill_task(
        task_id, uid, tid, character="", status="running", progress_pct=0,
        message="进行中", card_id="", awakening=""))
    with D._task_lock:
        D._tasks[task_id] = {
            "status": "running", "progress_pct": 0, "user_id": uid,
            "text_id": tid, "character": "", "message": "",
            "card_id": "", "awakening": "", "_db": None,
        }
    monkeypatch.setattr(D, "get_storage", lambda: store)
    monkeypatch.setattr(D, "submit_to_main_loop",
                        lambda coro, timeout=10: asyncio.run(coro))
    monkeypatch.setattr(D, "_generate_awakening", lambda *a, **k: "")
    monkeypatch.setattr(deps, "get_distiller", lambda llm=None: spy)
    monkeypatch.setattr(deps, "get_text_manager", lambda *a, **kw: _SavingTM())
    D._run_distill_task(task_id, tid, NAME, False, uid, body, "story", object())


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g12_three_channels_share_the_object(tmp_path, monkeypatch):
    from core.text_manager import TextManager
    book = _book_case1()
    sentinel = _detect_via(book, _Judge(_heading_texts(book)))

    uid = f"usr_{uuid.uuid4().hex[:8]}"
    # 三条通道各用一份干净存储：首跑都没有缓存，detect_chapters 都返回同一个哨兵。
    spy_bg = _SpyDistiller(sentinel)
    store_bg = SQLiteStore(str(tmp_path / "bg.db"))
    tid_bg = _seed(store_bg, uid, book)
    _run_bg(store_bg, uid, tid_bg, book, spy_bg, monkeypatch)
    assert {k for k, _ in spy_bg.seen} == {"stream", "draft", "final"}, spy_bg.seen
    assert all(c is sentinel for _, c in spy_bg.seen), spy_bg.seen

    spy_stream = _SpyDistiller(sentinel)
    store_stream = SQLiteStore(str(tmp_path / "stream.db"))
    tid_stream = _seed(store_stream, uid, book)
    client = _build_client(store_stream, uid, monkeypatch,
                           distiller=spy_stream, tm=_SavingTM())
    r = client.post("/api/distill/run_stream",
                    json={"text_id": tid_stream, "character_name": NAME})
    assert r.status_code == 200, r.text
    assert {k for k, _ in spy_stream.seen} == {"stream", "draft", "final"}, spy_stream.seen
    assert all(c is sentinel for _, c in spy_stream.seen), spy_stream.seen

    spy_run = _SpyDistiller(sentinel)
    store_run = SQLiteStore(str(tmp_path / "run.db"))
    tid_run = _seed(store_run, uid, book)
    tm = TextManager(lambda: store_run, spy_run, None, {}, memory_manager=None)
    client = _build_client(store_run, uid, monkeypatch, distiller=spy_run, tm=tm)
    r = client.post("/api/distill/run",
                    json={"text_id": tid_run, "character_name": NAME})
    assert r.status_code == 200, r.text
    assert {k for k, _ in spy_run.seen} == {"sync", "final"}, spy_run.seen
    assert all(c is sentinel for _, c in spy_run.seen), spy_run.seen


# ── 13. 回忆区版本升到 2 ──────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 未实现")
def test_g13_retro_version_bumped():
    from core.retro_cache import RETRO_VERSION
    assert RETRO_VERSION == 2


# ── 14. 普通对照：不传章节表 = 今天的行为（改前就绿） ─────────────────────


def test_g14_without_chapters_keeps_regex_behavior():
    book = _book_arabic()
    excerpt = build_excerpt(book, [NAME])
    assert excerpt is not None and "【第1章】" in excerpt

    distiller = Distiller(llm=_VerifyAllLLM(NAME), config_path=None)
    card = CharacterCard.model_validate(
        {"name": NAME, "speaking_style": {"catchphrases": ["酒"]}})
    out = distiller.finalize_card(card, book, NAME, [], ROSTER)
    assert out.speaking_style.catchphrases == ["酒"]
