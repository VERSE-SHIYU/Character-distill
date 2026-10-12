"""目标检查：一个存档的长期记忆「读全量」真的读全，不再被 mem0 的默认 20 条截断。

spec：`docs/specs/memory-read-all.md` §5。真 mem0 + 本地 qdrant，embedding 只替换出网那一步（`fake_embedding_api`），
不调任何模型。4 个「读全部」的地方（到期事件 / 重逢问候 / 反思 / 面板列表）都走
`MemoryView.get_all`，这里逐个验它们在各个总条数下读到的是全部。
"""
from __future__ import annotations

import os

os.environ.setdefault("MEM0_TELEMETRY", "False")

import pytest  # noqa: E402
from conftest import persist_time_event  # noqa: E402
import mem0.embeddings.openai as _m0_embed  # noqa: E402
import mem0.llms.openai as _m0_llm  # noqa: E402

from core.event_service import EventService  # noqa: E402
from core.memory_manager import MemoryManager, REFLECTION_MIN_QUALITY  # noqa: E402
from core.memory_scope import MemoryScope, ScopedMemory  # noqa: E402
from core.reflection_service import ReflectionService  # noqa: E402

DIM = 1024
USER = "usr-read-all"
CARD = "card-read-all"
# 存档 A 的总条数。边界：旧上限 20；mem0 内部多取的下限 60；第一批 200；翻倍后 400。
EXTRA = REFLECTION_MIN_QUALITY + 2   # _seed 在普通记忆之外固定写的条数：高重要度 + 2 个事件（「已问过」在事件自己身上）
TOTALS = [6, 20, 21, 60, 61, 200, 201, 400, 401, 460]
SIZES = [t - EXTRA for t in TOTALS]


class _Quiet:
    def __init__(self, **_kw):
        pass


class _UserLLM:
    model = "stub-user"

    def credential_fingerprint(self):
        return "fp-read-all"

    def derive(self, **_kw):
        return self


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, fake_embedding_api):
    monkeypatch.setattr(_m0_llm, "OpenAI", _Quiet)
    monkeypatch.setattr(_m0_embed, "OpenAI", _Quiet)


@pytest.fixture
def manager(tmp_path):
    return MemoryManager({}, db_dir=tmp_path / "mem0_db")


def _seed(view, scope, fillers: int) -> None:
    """写入 fillers 条普通记忆 + EXTRA 条「读取点要找的」记忆。"""
    for i in range(fillers):
        assert view.add_manual(f"普通的一件小事{i}", scope, metadata={"importance": 3})
    for i in range(REFLECTION_MIN_QUALITY):   # 重逢 top-2 / 反思高质量素材
        assert view.add_manual(f"关键记忆{i}", scope, metadata={"importance": 10})
    # 两件到期事件，用生产的写入函数写；较晚的「体检」走生产的注入 → 标记流程问过一次
    scoped = ScopedMemory(view, scope)
    persist_time_event(scoped, "面试", "2020-01-01T00:00")
    persist_time_event(scoped, "体检", "2020-06-01T00:00")
    svc = EventService(scoped)
    assert "体检" in svc.build_candidate_block(), "前提破了：最晚到期的不是「体检」"
    svc.mark_asked()


@pytest.mark.parametrize("fillers", SIZES)
def test_R1_every_reader_sees_the_whole_archive(manager, fillers):
    view = manager.for_user(user_id=USER, llm=_UserLLM(), embedding_key="sk-emb-x")
    scope = MemoryScope.archive(CARD, "arch-A")
    other = MemoryScope.archive(CARD, "arch-B")
    for i in range(30):
        view.add_manual(f"别的存档的事{i}", other, metadata={"importance": 10})
    _seed(view, scope, fillers)
    total = len(view._mem.vector_store.list(filters=scope.filters(), top_k=100000)[0])   # 库里的真数
    assert total == fillers + EXTRA

    # 视图 / 面板列表（面板走 MemoryManager.get_all → 同一个视图方法）
    assert len(view.get_all(scope)) == total, "视图没读全"
    assert len(manager.get_all(scope)) == total, "面板列表没读全"
    assert all("别的存档" not in (m.get("memory") or "") for m in view.get_all(scope)), "读全量时串了别的存档"

    scoped = ScopedMemory(view, scope)
    # 到期事件：找得到没问过的那个；问过的那个不再问
    due = EventService(scoped).get_due_event()
    assert due is not None and due["event"] == "面试", f"到期事件读错：{due}"

    # 反思：高质量素材都在全量里，够数就触发
    captured = {}

    class _Spy(ScopedMemory):
        def reflect(self, llm, recent, char_name, storage=None):
            captured["recent"] = recent

    rs = ReflectionService(_Spy(view, scope))
    rs._importance_acc = rs._rounds_since_reflect = 10 ** 6
    assert rs.maybe_reflect(10, object(), "角色") is True, "反思没拿到足够的高质量素材"
    assert sum(1 for r in captured["recent"] if r["text"].startswith("关键记忆")) == REFLECTION_MIN_QUALITY

    # 重逢问候：取 importance 最高的两条 → 必是「关键记忆」
    from core.chat_engine import ChatEngine
    eng = ChatEngine.__new__(ChatEngine)
    eng._scoped = scoped
    top = eng._build_reunion_memories()
    assert top.count("关键记忆") == 2, f"重逢没取到最重要的记忆：{top!r}"
