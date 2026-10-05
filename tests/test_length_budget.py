# -*- coding: utf-8 -*-
"""distill-capacity：全仓库唯一的 token 计数（`core.tokens.count_tokens`）与唯一的上限来源
（`core.length_budget`）落在**每个调用点**上的判据。

本文件是变异驱动 `tests/perf/distill_capacity_mutations.py` 的**覆盖域**：驱动表里每条变异
的靶子都指向本文件，于是**本文件里的每一条判别器都必须被某条变异撞到**（元锁
`tests/test_lock_coverage.py` 判，两个方向：现场未覆盖 ⊆ 名单、名单 ⊆ 现场未覆盖）。
所以本文件的写法是刻意的：**一个用例恰一条判别器**，且每条判别器都有一条变异专门打它。

粒度说明（别当意外）：`tests/test_tokens.py` 的 U1/U3 不在本域里（它们判 tokenizer 本身，
不需要变异），本文件只收 §4.1 的 U2/U4/U5 与 §4.2 的 R1–R10、§4.3 的 S1/S2。

Run: pytest tests/test_length_budget.py -v
"""
from __future__ import annotations

import asyncio
import contextlib
import pathlib
import re
import threading
from types import SimpleNamespace

import pytest

from core.chat_engine import ChatEngine
from core.distiller import Distiller
from core.group_session import GroupSession
from core.length_budget import (
    CHAT_MAX_CHARS,
    LONGCTX_THRESHOLD_TOKENS,
    MAX_FILE_BYTES,
    PROMPT_RESERVE_TOKENS,
    assert_budget_fits,
    fits_one_pass,
    public_limits,
)
from core.text_manager import TextManager

_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── 无判别的辅助件（**不得含 assert / raise / pytest.raises** —— 它们会被算成判别器）────


class _Store:
    """`save_text` 只吞不记：本文件的用例要么在落盘前失败，要么只关心接线。"""

    async def save_text(self, *a, **kw) -> None:
        return None


def _text_manager() -> TextManager:
    return TextManager(lambda: _Store(), None, None, {}, memory_manager=None)


class _LLM:
    """蒸馏器的最小假件：本文件只判**走哪条路**，不判蒸馏结果。"""

    last_usage = None
    model = "m"

    def chat(self, system, messages, max_tokens=None):
        return '{"name": "T", "identity": "T"}'

    def chat_stream_long(self, system, messages, max_tokens=None):
        yield '{"name": "T", "identity": "T"}'
        return {"prompt_tokens": 1, "completion_tokens": 1}


def _distiller() -> Distiller:
    return Distiller(llm=_LLM(), config_path=None)


class _Upstream(Exception):
    """带 status_code 的上游异常替身 —— `_status_code` 只认属性，不认具体类。"""

    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        super().__init__(text)


def _client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import deps
    from deps import get_storage
    from limiter import limiter
    from routers.auth import get_current_user
    from routers.text import router as text_router

    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(text_router)
    app.dependency_overrides[get_storage] = lambda: _Store()
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "t", "role": "user"}

    async def _fake_user_llm(*a, **kw):
        return object()

    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(
        deps, "get_text_manager",
        lambda *a, **kw: TextManager(lambda: _Store(), None, None, {}, memory_manager=None),
    )
    return TestClient(app, raise_server_exceptions=False)


def _upload_file(monkeypatch, name: str, content: bytes):
    return _client(monkeypatch).post(
        "/api/text/upload",
        data={"text_type": "other"},
        files={"file": (name, content, "application/octet-stream")},
    )


# ── §4.1 单元：U2 / U4 / U5 ────────────────────────────────────────────────


def test_fits_one_pass_is_strictly_below_the_token_threshold():
    """U2：阈值本身**不**算一次读完（`<`，不是 `<=`）。变异 M1。"""
    assert fits_one_pass(LONGCTX_THRESHOLD_TOKENS - 1) and not fits_one_pass(LONGCTX_THRESHOLD_TOKENS)


def test_the_budget_guard_rejects_a_sum_that_does_not_fit_the_window():
    """U4：导入时那条预算断言可复用、且真的会失败（预留调大到越窗即抛）。变异 M10。"""
    with pytest.raises(AssertionError):
        assert_budget_fits(1_000_000, 900_000, 100_000, 1_000_000)


def test_public_limits_mirrors_the_module_constants():
    """U5：`public_limits()` 三个字段就是模块常量本身，不许各写一份字面量。变异 M14。"""
    assert public_limits() == {
        "story_max_tokens": LONGCTX_THRESHOLD_TOKENS,
        "chat_max_chars": CHAT_MAX_CHARS,
        "max_file_bytes": MAX_FILE_BYTES,
    }


# ── §4.2 调用点矩阵：R1 文件上传 ───────────────────────────────────────────


def test_file_upload_story_boundary_by_token_count():
    """R1·收拒：文件上传按 **token 数**判小说上限（打桩到阈值 → 拒）。变异 M1/M2/M3。"""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("正文")
        path = f.name

    tm = _text_manager()
    from core import length_budget

    length_budget.count_tokens = lambda text: LONGCTX_THRESHOLD_TOKENS
    try:
        with pytest.raises(ValueError):
            asyncio.run(tm.upload_text_from_file(path, "book.txt", text_type="story", user_id="u"))
    finally:
        length_budget.count_tokens = _real_count_tokens


def test_the_upload_token_count_runs_off_the_event_loop_thread(monkeypatch):
    """R1·线程：整本计数是 CPU 密集，必须放 `asyncio.to_thread`（不在事件循环线程上跑）。变异 M4。"""
    seen: list[threading.Thread] = []

    def spy(text, text_type):
        seen.append(threading.current_thread())

    monkeypatch.setattr("core.text_manager.check_upload", spy)
    tm = _text_manager()
    asyncio.run(tm.upload_text("book.txt", "正文", text_type="story", user_id="u"))
    assert seen and seen[0] is not threading.main_thread()


# ── §4.2：R2 文件体积 413 ─────────────────────────────────────────────────


def test_file_upload_size_413_message_from_constants(monkeypatch):
    """R2·文案：413 文案由 `MAX_FILE_SIZE` 常量生成（改常量 → 文案跟着变）。变异 M8。"""
    import routers.text as text_routes

    monkeypatch.setattr(text_routes, "MAX_FILE_SIZE", 2 * 1024 * 1024)
    r = _upload_file(monkeypatch, "big.txt", b"x" * (2 * 1024 * 1024 + 16))
    assert r.status_code == 413 and "2MB" in r.json()["detail"]


# ── §4.2：R3 文本上传 ─────────────────────────────────────────────────────


def test_text_upload_story_boundary_by_token_count():
    """R3·收拒：文本上传走同一条 `_check_length`（打桩到阈值 → 拒）。变异 M1/M2/M3。"""
    from core import length_budget

    length_budget.count_tokens = lambda text: LONGCTX_THRESHOLD_TOKENS
    tm = _text_manager()
    try:
        with pytest.raises(ValueError):
            asyncio.run(tm.upload_text("book.txt", "正文", text_type="story", user_id="u"))
    finally:
        length_budget.count_tokens = _real_count_tokens


def test_text_upload_chat_boundary_by_char_count():
    """R3·收拒（聊天）：聊天仍按 **200 万字**判，不按 token。变异 M17。

    `match=` 不是修饰：这段「一堵同字」的正文会被 `ChatPreprocessor` 整行丢掉，若只写
    裸 `pytest.raises(ValueError)`，清洗后的空文本那条 `chat_clean_empty` 也能让它通过
    —— 判据满足于错的守卫（M17 把 `if n > CHAT_MAX_CHARS` 改成 `if False` 时实测过）。
    """
    tm = _text_manager()
    with pytest.raises(ValueError, match="超过聊天记录上限"):
        asyncio.run(tm.upload_text("log.txt", "字" * (CHAT_MAX_CHARS + 1),
                                   text_type="chat", user_id="u"))


# ── §4.2：R4/R5 蒸馏选路径 ────────────────────────────────────────────────


def test_sync_route_by_token_count(monkeypatch):
    """R4：同步选路径按**精确 token 数**与阈值比 —— 恰等于阈值走分片（不进一次读完）。变异 M1/M5。"""
    seen: list[str] = []
    d = _distiller()
    d._longctx_threshold = 1000
    monkeypatch.setattr("core.distiller.count_tokens", lambda text: 1000)
    monkeypatch.setattr(d, "_distill_longcontext", lambda text, name: seen.append("one_pass"))
    with contextlib.suppress(Exception):
        d.distill_incremental("正文正文", "角色")
    assert seen == []


def test_stream_route_by_token_count(monkeypatch):
    """R5：流式选路径同一判断 —— 恰等于阈值走分片。变异 M1/M5。"""
    seen: list[str] = []
    d = _distiller()
    d._longctx_threshold = 1000
    monkeypatch.setattr("core.distiller.count_tokens", lambda text: 1000)
    monkeypatch.setattr(d, "_distill_longcontext_stream",
                        lambda text, name: (seen.append("one_pass"), iter(()))[1])
    with contextlib.suppress(Exception):
        list(d.distill_incremental_stream("正文正文", "角色"))
    assert seen == []


# ── §4.2：R6 超窗上屏 ─────────────────────────────────────────────────────


def test_overflow_400_user_message():
    """R6·超窗：400 + 已知超窗措辞 → 「文本过长」那句，不是通用文案。变异 M6。"""
    from adapters.llm_adapter import _upstream_user_message

    assert "文本过长" in _upstream_user_message(_Upstream(400, "Input token exceed the limit"))


def test_other_400_unchanged():
    """R6·反例：措辞命中但**状态码不是 400** → 不算超窗（判定要两个条件同时成立）。变异 M7。"""
    from adapters.llm_adapter import _upstream_user_message

    assert _upstream_user_message(_Upstream(500, "maximum context length exceeded")) == ""


# ── §4.2：R7 limits 接口 ──────────────────────────────────────────────────


def test_limits_endpoint(monkeypatch):
    """R7：`GET /api/text/limits` 返回的就是 `public_limits()`（纯读常量，不另抄一份）。变异 M15。"""
    r = _client(monkeypatch).get("/api/text/limits")
    assert r.status_code == 200 and r.json() == public_limits()


# ── §4.2：R8 聊天上下文预算 ───────────────────────────────────────────────


def test_chat_context_budget_uses_count_tokens(monkeypatch):
    """R8：聊天历史按**精确 token 数**裁剪（预算 100，单轮 60+60 超预算 → 整轮丢）。变异 M11。"""
    engine = ChatEngine.__new__(ChatEngine)
    engine._ctx_engine = SimpleNamespace(MAX_HISTORY=100)
    monkeypatch.setattr("core.chat_engine.count_tokens", lambda text: 60)
    history = [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    ]
    out = engine._build_llm_messages(history, "当前问题")
    assert [m["content"] for m in out] == ["当前问题"]


# ── §4.2：R9 群聊预算 ─────────────────────────────────────────────────────


def test_group_budget_uses_count_tokens(monkeypatch):
    """R9：群聊历史按**精确 token 数**从最旧丢起（上限 2000，三条各 1000 → 丢最旧那条）。变异 M16。"""
    gs = GroupSession(id="g", engines={}, user_id="u")
    gs.group_history = [
        {"role": "assistant", "speaker_card_id": "c", "content": "A"},
        {"role": "assistant", "speaker_card_id": "c", "content": "B"},
        {"role": "assistant", "speaker_card_id": "c", "content": "C"},
    ]
    monkeypatch.setattr("core.group_session.count_tokens", lambda text: 1000)
    out = gs._convert_history("c")
    assert [m["content"] for m in out] == ["B", "C"]


# ── §4.2：R10 用量估算兜底 ────────────────────────────────────────────────


def test_estimated_usage_counts_text(monkeypatch):
    """R10·非流式：兜底计数用**原文的精确 token 数**（不再按字数除 1.5）。变异 M12。"""
    from core.utils import estimate_usage

    monkeypatch.setattr("core.utils.count_tokens", lambda text: len(text))
    assert estimate_usage("角色甲说", "回复乙") == {
        "prompt_tokens": 4, "completion_tokens": 3, "estimated": True,
    }


def test_stream_usage_fallback_accumulates_every_piece(monkeypatch):
    """R10·流式：失败兜底要把**已吐出的每一片**累加成文本再计数（不是只算最后一片）。变异 M13。"""
    rows: list[dict] = []
    monkeypatch.setattr("core.distiller.try_record_usage", lambda **kw: rows.append(kw))
    monkeypatch.setattr("core.utils.count_tokens", lambda text: len(text))

    class _Raising:
        last_usage = None
        _exc = RuntimeError("boom")   # 转抛存起来的异常：替身的失败注入，不是判据

        def chat_stream_long(self, system, messages, max_tokens=None):
            yield "甲"
            yield "乙"
            raise self._exc

    d = Distiller(llm=_Raising(), config_path=None)
    with contextlib.suppress(RuntimeError):
        d._collect_stream("sys", [{"role": "user", "content": "hi"}], "标签", "distill")
    assert rows[-1]["usage"]["completion_tokens"] == 2


# ── §4.3 结构锁：S1 / S2 ──────────────────────────────────────────────────


_SCAN_DIRS = ("core", "web/routers", "web/frontend/src", "adapters", "scripts")
# S2 只判 Python 侧的 token 计数：前端没有 token 计数，`* 0.8` 之类的视口判断不该被它盯上
_S2_SCAN_DIRS = ("core", "web", "adapters", "scripts")
_S2_SUFFIXES = (".py",)
_S1_EXCLUDE = {"core/length_budget.py"}
_S1_LEGACY = (
    r"\*\s*0\.6", r"\b1_000_000\b", r"\b900_000\b", r"\b900000\b",
    r"30 \* 1024 \* 1024", r"100 \* 1024 \* 1024", "100 万",
)
_S1_OVERRIDE = (r'longctx_threshold["\']',)
_S2_EXCLUDE = {"core/tokens.py"}
_S2_OTHER_COUNTER = (
    r"_count_tokens", r"_estimate_tokens", r"_CHARS_PER_TOKEN",
    r"estimate_usage_from_chars", r"len\s*\(.*?\)\s*\*\s*0\.[68]\b", r"/\s*1\.5\b",
)


def _iter_sources(dirs=_SCAN_DIRS, suffixes=(".py", ".js", ".jsx")):
    for d in dirs:
        base = _ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in suffixes and "__tests__" not in p.parts:
                yield p


def _grep_sources(patterns, exclude, dirs=_SCAN_DIRS, suffixes=(".py", ".js", ".jsx")):
    offenders: list[str] = []
    for p in _iter_sources(dirs, suffixes):
        rel = p.relative_to(_ROOT).as_posix()
        if rel in exclude:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        for pat in patterns:
            if re.search(pat, src):
                offenders.append(f"{rel}: {pat}")
    return sorted(offenders)


def _tokenizer_from_file_sites():
    sites = set()
    for p in _iter_sources(_S2_SCAN_DIRS, _S2_SUFFIXES):
        if "Tokenizer.from_file" in p.read_text(encoding="utf-8", errors="replace"):
            sites.add(p.relative_to(_ROOT).as_posix())
    return sites


def test_s1_no_legacy_limits_or_config_threshold_override():
    """S1：旧上限字面量与「读配置的 longctx_threshold」两样都不许再出现（length_budget 除外）。变异 M8。"""
    assert not _grep_sources(_S1_LEGACY, _S1_EXCLUDE) and not _grep_sources(_S1_OVERRIDE, set())


def test_s2_count_tokens_is_the_single_token_counter():
    """S2：全仓唯一的 token 计数 —— 三套旧估算的标识一个不剩，`Tokenizer.from_file` 只此一处。变异 M5/M11/M12。"""
    assert not _grep_sources(
        _S2_OTHER_COUNTER, _S2_EXCLUDE, _S2_SCAN_DIRS, _S2_SUFFIXES
    ) and _tokenizer_from_file_sites() == {"core/tokens.py"}


# 保存真身供打桩用例还原（放到文件末尾，避免被 _iter_sources 之类的扫描误判）
from core.tokens import count_tokens as _real_count_tokens  # noqa: E402
