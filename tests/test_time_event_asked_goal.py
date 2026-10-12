"""目标检查：时间事件「已问过」写在事件自己身上（P9，spec `docs/specs/time-event-asked.md` §1）。

真 mem0 + 本地 qdrant；embedding 只替换出网那一步（`fake_embedding_api`），空串怎么处理
走生产代码 —— main 上「已问」标记写不进去，就是因为这一步，这里不能再用宽容的替身。
"""
from __future__ import annotations

import inspect
import logging
import os
from pathlib import Path

os.environ.setdefault("MEM0_TELEMETRY", "False")

import pytest  # noqa: E402
from conftest import persist_time_event  # noqa: E402
import mem0.embeddings.openai as _m0_embed  # noqa: E402
import mem0.llms.openai as _m0_llm  # noqa: E402

from core.event_service import EventService  # noqa: E402
from core.memory_manager import MemoryManager  # noqa: E402
from core.memory_scope import MemoryScope, ScopedMemory  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
USER = "usr-asked"
CARD = "card-asked"


class _Quiet:
    def __init__(self, **_kw):
        pass


class _UserLLM:
    model = "stub-user"

    def credential_fingerprint(self):
        return "fp-asked"

    def derive(self, **_kw):
        return self


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, fake_embedding_api):
    monkeypatch.setattr(_m0_llm, "OpenAI", _Quiet)
    monkeypatch.setattr(_m0_embed, "OpenAI", _Quiet)


@pytest.fixture
def view(tmp_path):
    manager = MemoryManager({}, db_dir=tmp_path / "mem0_db")
    return manager.for_user(user_id=USER, llm=_UserLLM(), embedding_key="sk-emb-x")


def _scoped(view, archive: str) -> ScopedMemory:
    return ScopedMemory(view, MemoryScope.archive(CARD, archive))


def _seed_due_event(scoped: ScopedMemory, event: str = "面试") -> None:
    persist_time_event(scoped, event, "2020-01-01T00:00")
    assert any((m.get("metadata") or {}).get("type") == "time_event" for m in scoped.get_all()), "前提破了：事件没写进去"


def _ask(scoped: ScopedMemory) -> str:
    """一轮里注入到期事件、收尾时标记已问；返回这件事的 id。"""
    svc = EventService(scoped)
    assert svc.build_candidate_block(), "前提破了：没有到期事件可注入"
    eid = svc._injected_event_id
    svc.mark_asked()
    return eid


def test_T1_asked_survives_a_restart(view):
    """问过以后新建一个引擎（重启后进程内什么都没有），这件事不再被当成没问过。"""
    a = _scoped(view, "arch-A")
    _seed_due_event(a)
    _ask(a)
    assert EventService(_scoped(view, "arch-A")).get_due_event() is None, "重启后同一件事又被当成没问过"


def test_T2_marking_adds_no_memory(view):
    """标记生效，而且写在事件自己身上：重启后不再问，记忆条数不变，不多出一条假记忆。"""
    a = _scoped(view, "arch-A")
    _seed_due_event(a)
    before = len(a.get_all())
    _ask(a)
    assert EventService(_scoped(view, "arch-A")).get_due_event() is None, "标记没生效"
    after = a.get_all()
    assert len(after) == before, f"标记多存了记录：{before} → {len(after)}"
    assert all((m.get("memory") or "").strip() for m in after), "存进了空文本记忆"


def test_T3_failed_mark_is_reported_as_failed(view, monkeypatch, caplog, capsys):
    """写不进去时打 warning，不打「Marked … as asked」。"""
    a = _scoped(view, "arch-A")
    _seed_due_event(a)

    def _boom(*_a, **_kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(view._mem, "update", _boom)
    monkeypatch.setattr(view._mem, "add", _boom)
    with caplog.at_level(logging.WARNING):
        _ask(a)
    assert "as asked" not in capsys.readouterr().out, "写入失败却打了成功日志"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), "写入失败没有 warning"
    assert EventService(a).get_due_event() is not None, "写入失败后这件事不再出现：失败被当成了已问（spec §4.4）"


def test_T4_another_archive_cannot_mark_it(view):
    """别的存档拿到这件事的 id 也改不了它：归属核对在视图里。"""
    a = _scoped(view, "arch-A")
    _seed_due_event(a)
    eid = EventService(a).get_due_event()["event_id"]
    b = _scoped(view, "arch-B")
    assert b.update_metadata(eid, {"asked": True}) is False, "跨存档改了别人的事件"
    assert EventService(_scoped(view, "arch-A")).get_due_event() is not None, "A 的事件被 B 标成了已问"


def test_T5_asked_state_lives_only_on_the_event():
    """结构锁：「问过没有」只有一处 —— 事件元数据。不再另存一类记录，也没有进程内的第二份。"""
    leaks = [str(p.relative_to(ROOT)) for p in (ROOT / "core").rglob("*.py")
             if "time_event_asked" in p.read_text(encoding="utf-8")]
    assert not leaks, f"还有另存「已问」记录的代码：{leaks}"
    svc = EventService(ScopedMemory(None, None))
    assert set(vars(svc)) == {"_memory", "_injected_event_id"}, f"EventService 多了进程内状态：{set(vars(svc))}"
    src = inspect.getsource(EventService)
    assert "add_manual" not in src, "EventService 还在另写记忆"
    assert ".enabled" not in src, "「没有记忆」又在 EventService 里判了一次（应只由 ScopedMemory 处理）"
    assert svc.get_due_event() is None, "没有记忆时应当没有到期事件"
