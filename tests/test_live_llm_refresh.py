# -*- coding: utf-8 -*-
"""72 线 · 第 3 步上半截：deps 把新解析出的实例发到**活会话**手里。

用户存完 model / base_url / key 之后，活会话从**下一轮**起走新连接 —— 不踢会话、不重建
RAG。本文件锁这条链的上半截（谁拿到新实例）：按用户筛（一对一按条目的 `user_id`、群聊按
`GroupSession.user_id`）、保存端点确实触发、换失败不改写保存结果、保存这条路**不**过
`preflight()`、管理员热重载只带走还在用旧全局的那些。下半截（引擎侧怎么换、在飞的那轮归
谁）在 ``tests/test_live_llm_swap.py``。

HTTP 面走 `TestClient`（与 `test_ownership_404.py` / `test_embedding_test_endpoint.py`
同法），base_url 取白名单内的 `api.deepseek.com` —— geo 门在保存端点上真实生效
（`auth.py` 的 `geo_refusal`），换个域名就得连门一起打桩，那测的就不是保存这条路了。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.chat_engine import ChatEngine
from core.group_session import GroupSession
from core.schema import CharacterCard
from core.text_manager import new_session_entry
from deps import get_storage
from storage.sqlite_store import SQLiteStore

from routers.auth import get_current_user, router as auth_router

CARD = CharacterCard(name="角色", identity="测试用角色")

# 模型名必须**恰好**是预算表的键：`_compute_budgets` 是精确查表
# （`MODEL_BUDGET_MAP.get(model)`），写成 "claude-sonnet-4-20250514" 会落到未知回退
# 32000，与旧值相同 —— 「换了连接」这件事就再也看不出来了。
OLD_MODEL = "deepseek-v4-pro"   # 32000
NEW_MODEL = "claude-sonnet"     # 24000

BASE_URL = "https://api.deepseek.com"


# ── 替身 ──────────────────────────────────────────────────────────────────────

class _StubLLM:
    """假连接：`model` 给 ContextEngine 算预算，`preflight` 用来演「这次出站会被拒」。"""

    def __init__(self, model: str = OLD_MODEL):
        self._model = model
        self.last_usage = {"prompt_tokens": 11, "completion_tokens": 1}
        self.refuse = False

    @property
    def model(self) -> str:
        return self._model

    def preflight(self) -> None:
        if self.refuse:
            from adapters.llm_adapter import LLMCallRefused

            raise LLMCallRefused("当前网络环境（中国大陆）暂不支持境外模型", BASE_URL)

    def chat(self, *_a, **_kw) -> str:
        return "固定回复"

    def chat_stream(self, *_a, **_kw):
        yield "甲"

    async def achat(self, *_a, **_kw) -> str:
        return "固定回复"


class _ExplodingEngine:
    """`set_llm` 抛错的引擎替身：演「换连接这一步自己坏了」。"""

    llm = None

    def set_llm(self, llm) -> None:
        raise RuntimeError("swap failed")


class _FakeStorage:
    """只答「这个用户的 API 配置」—— `refresh_user_llm` 的第一件事就是读它。"""

    def __init__(self, config: dict | None = None):
        self.config = config or {"api_key": "sk-user", "base_url": BASE_URL, "model": NEW_MODEL}
        self.reads: list[str] = []

    async def get_user_api_config(self, user_id: str) -> dict:
        self.reads.append(user_id)
        return dict(self.config)


def _engine(llm: _StubLLM) -> ChatEngine:
    """最小可用引擎：`rag=None` 让检索降级成空块，`storage=None` 关掉落库那条下游。"""
    return ChatEngine(llm=llm, rag=None, card=CARD, card_id="c1", storage=None,
                      session_id="c1", is_new_session=True)


# ── 夹具 ──────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_deps_state(monkeypatch):
    """deps 的进程级全局：用例之间必须互不泄漏。

    `_sessions` / `_group_sessions` / `_user_llm_cache` 是**可变容器**（patch 换对象
    只护得住「重新赋值」那种改法），故清空式还原；`reset_llm_and_dependents` 会**重新
    赋值**那几个模块全局，故用 monkeypatch 记下原值。
    """
    import deps

    for name in ("_llm", "_config", "_rag_config", "_memory_config",
                 "_indexing_service", "_memory_manager"):
        monkeypatch.setattr(deps, name, getattr(deps, name))
    sessions = deps.get_sessions()
    groups = deps.get_group_sessions()
    cache = deps._user_llm_cache
    yield
    sessions.clear()
    groups.clear()
    cache.clear()


@pytest.fixture(autouse=True)
def _rate_limit_off(monkeypatch):
    """关掉慢速限流器 —— 本文件会多次打同一个端点。

    改 `enabled` 而不是在 import 期换掉 `limiter.limit`：后者只在「本文件是第一个
    import `routers.auth` 的模块」时成立，全量跑时装饰早已生效（test_embedding_test_endpoint
    里有同款注释与实测）。
    """
    import limiter as _lim_

    monkeypatch.setattr(_lim_.limiter, "enabled", False)


@pytest.fixture
def user_llm(monkeypatch):
    """`_make_user_llm` 换成不碰网络的桩，并钉死「没有全局兜底」。

    打 `deps._make_user_llm`（不是 `resolve_llm`）：解析**策略**不是本步的改动面，这里要
    的是「解析出来之后发给了谁」。`get_llm` 钉成 None 是为了消除 ambient 依赖 ——
    有 `.env` 与没 `.env` 的机器上「回落到了什么」必须一样。

    返回构造出来的实例列表，用例可据此断言「两个会话换到的是同一个新实例」。
    """
    import deps

    built: list[_StubLLM] = []

    def _build(config):
        stub = _StubLLM(config.get("model", OLD_MODEL))
        built.append(stub)
        return stub

    monkeypatch.setattr(deps, "_make_user_llm", _build)
    monkeypatch.setattr(deps, "get_llm", lambda: None)
    return built


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))


@pytest.fixture
def owner():
    return f"owner_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def client(store, owner):
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": owner, "username": "testuser", "role": "user",
    }
    return TestClient(app)


def _seed_user(store, uid):
    """`get_user_api_config` 走 `users LEFT JOIN user_secrets`，没有行就查不出任何配置。"""
    asyncio.run(store.create_user(uid, uid, "x"))


def _save(client, model: str = NEW_MODEL):
    return client.patch("/api/auth/api-config", json={
        "api_key": "sk-new", "base_url": BASE_URL, "model": model,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# T4–T5：换的是谁的会话
# ═══════════════════════════════════════════════════════════════════════════════

def test_T4_refresh_reaches_only_that_users_one_to_one_sessions(user_llm):
    """保存设置只该影响自己的活会话 —— 把别人的也换掉，等于替别人改了连接。"""
    import deps

    mine_a, mine_b = _engine(_StubLLM()), _engine(_StubLLM())
    theirs = _engine(_StubLLM())
    deps.get_sessions()["s_mine_a"] = new_session_entry(mine_a, None, "u_owner")
    deps.get_sessions()["s_mine_b"] = new_session_entry(mine_b, None, "u_owner")
    deps.get_sessions()["s_theirs"] = new_session_entry(theirs, None, "u_other")

    swapped = asyncio.run(deps.refresh_user_llm("u_owner", _FakeStorage()))

    assert swapped == 2, f"换掉的引擎数不对：{swapped}"
    assert mine_a.llm is mine_b.llm is user_llm[-1], "属主的两个会话没换到同一个新实例"
    assert mine_a.llm.model == NEW_MODEL
    assert theirs.llm.model == OLD_MODEL, "别人的会话被顺手换掉了"


def test_T5_refresh_reaches_group_member_engines(user_llm):
    """群聊里每个角色一个引擎，属主在**会话**上 —— 漏了这条，群聊会一直用旧连接。"""
    import deps

    e1, e2 = _engine(_StubLLM()), _engine(_StubLLM())
    outsider = _engine(_StubLLM())
    deps.get_group_sessions()["g_mine"] = GroupSession(
        id="g_mine", engines={"c1": e1, "c2": e2}, storage=None, user_id="u_owner")
    deps.get_group_sessions()["g_theirs"] = GroupSession(
        id="g_theirs", engines={"c9": outsider}, storage=None, user_id="u_other")

    swapped = asyncio.run(deps.refresh_user_llm("u_owner", _FakeStorage()))

    assert swapped == 2, f"换掉的群聊引擎数不对：{swapped}"
    assert e1.llm is e2.llm is user_llm[-1], "群聊成员的引擎没换到同一个新实例"
    assert e1.llm.model == NEW_MODEL
    assert outsider.llm.model == OLD_MODEL, "别人的群聊被顺手换掉了"


# ═══════════════════════════════════════════════════════════════════════════════
# T6–T8：保存这条路上会发生什么
# ═══════════════════════════════════════════════════════════════════════════════

def test_T6_saving_settings_swaps_the_live_session(store, client, owner, user_llm):
    """存设置这个动作本身要换活会话 —— 只清缓存的话，用户得等下一个请求才见效。"""
    import deps

    _seed_user(store, owner)
    engine = _engine(_StubLLM())
    deps.get_sessions()["s_save"] = new_session_entry(engine, None, owner)

    r = _save(client)

    assert r.status_code == 200, r.text[:200]
    assert engine.llm.model == NEW_MODEL, "存了设置，活会话还在用旧连接"
    assert engine._ctx_engine.TOTAL_BUDGET == 24000, "换了连接，预算还按旧模型算"


def test_T7_refresh_failure_does_not_undo_the_save(store, client, owner, user_llm):
    """换连接失败只是「晚一轮生效」，不是保存失败 —— 配置已经落库了。

    失败由引擎替身制造（`set_llm` 抛错）：这是这条路上真会发生的坏法，不需要为了造失败
    去打桩被测代码自己的协作方。
    """
    import deps

    _seed_user(store, owner)
    deps.get_sessions()["s_boom"] = new_session_entry(_ExplodingEngine(), None, owner)

    r = _save(client)

    assert r.status_code == 200, f"换连接失败把保存也带崩了：{r.status_code} {r.text[:200]}"
    stored = asyncio.run(store.get_user_api_config(owner))
    assert stored["model"] == NEW_MODEL, "换连接失败顺带把配置回滚了"


def test_T8_save_path_does_not_run_preflight(store, client, owner, monkeypatch):
    """保存这条路**不判放行** —— 除非为这次出站放行判定「不许」是另一码事。

    判定用的是**当下**的 IP 与 base_url，与用户刚存进去的配置无关。跟着 `get_user_llm`
    走就会因为一个此刻被拒的理由，让活会话换不成、白等一轮（配置本身仍会落库）。
    """
    import deps

    _seed_user(store, owner)

    def _refusing(config):
        stub = _StubLLM(config.get("model", OLD_MODEL))
        stub.refuse = True
        return stub

    monkeypatch.setattr(deps, "_make_user_llm", _refusing)
    monkeypatch.setattr(deps, "get_llm", lambda: None)

    engine = _engine(_StubLLM())
    deps.get_sessions()["s_refuse"] = new_session_entry(engine, None, owner)

    r = _save(client)

    assert r.status_code == 200, r.text[:200]
    assert engine.llm.model == NEW_MODEL, (
        "保存被 preflight 的判定挡下了 —— 活会话这轮没换成，尽管配置已经生效")


# ═══════════════════════════════════════════════════════════════════════════════
# T10：管理员热重载
# ═══════════════════════════════════════════════════════════════════════════════

def test_T10_hot_reload_leaves_self_configured_sessions_alone(monkeypatch):
    """热重载只带走还在用**旧全局**的会话（判据 `engine.llm is 旧全局`）。

    自己配了 key 的会话拿的不是这条全局连接；一起换，等于管理员改一次全局配置就把用户的
    私有 key 顶掉。反过来「一个都不换」也不行 —— 那些会话会一直连着已下线的旧实例。
    """
    import deps

    old_global, new_global = _StubLLM(), _StubLLM(NEW_MODEL)
    user_own = _StubLLM()
    on_global, on_user = _engine(old_global), _engine(user_own)
    deps.get_sessions()["s_global"] = new_session_entry(on_global, None, "u1")
    deps.get_sessions()["s_user"] = new_session_entry(on_user, None, "u1")

    monkeypatch.setattr(deps, "_llm", old_global)
    monkeypatch.setattr(deps, "_make_global_llm", lambda: new_global)
    monkeypatch.setattr(deps, "_make_indexing_service", lambda: object())
    monkeypatch.setattr(deps, "MemoryManager", lambda *_a, **_kw: object())

    deps.reset_llm_and_dependents()

    assert on_global.llm is new_global, "还在用旧全局的会话没被带走"
    assert on_user.llm is user_own, "自己配了 key 的会话被管理员的热重载波及了"
