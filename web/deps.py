"""FastAPI dependency injection: singletons and config loaders."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

load_dotenv(_REPO_ROOT / ".env")

import yaml
from adapters.llm_adapter import LLMAdapter
from core import scheduling
from core.distiller import Distiller
from core.indexing_service import IndexingService
from core.memory_manager import MemoryManager
from core.text_manager import TextManager
from storage import get_store
from storage.base import StorageBase
from web.llm_resolution import Source, resolve_llm

_CFG_PATH = _REPO_ROOT / "config.yaml"
if not _CFG_PATH.exists():
    _CFG_PATH = _REPO_ROOT / "config.example.yaml"

try:
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _config: dict[str, Any] = yaml.safe_load(_f)
except Exception as exc:
    print(f"[deps] Failed to read config: {exc}")
    raise

_storage: StorageBase | None = None
_main_loop: asyncio.AbstractEventLoop | None = None
_llm: LLMAdapter | None = None
_rag_config: dict[str, Any] = _config["rag"]

# {session_id: {"engine": ChatEngine, "card": CharacterCard}}
# Transitional: kept until chat_engine migrates to storage-backed history
_sessions: dict[str, dict[str, Any]] = {}

# {group_id: GroupSession}
_group_sessions: dict[str, Any] = {}

_indexing_service: IndexingService | None = None

_memory_config: dict[str, Any] = _config.get("memory", {})
_memory_manager: MemoryManager | None = None

# Per-user LLM cache: user_id → LLMAdapter
_user_llm_cache: dict[str, LLMAdapter] = {}


def clear_user_llm_cache(user_id: str | None = None) -> None:
    """Clear cached LLMAdapter for a user (or all if user_id is None)."""
    global _user_llm_cache
    if user_id is None:
        _user_llm_cache.clear()
    else:
        _user_llm_cache.pop(user_id, None)


def _make_user_llm(config: dict[str, Any]) -> LLMAdapter:
    """构造（不是「取得」）一个绑定**用户凭据**的实例 —— 生命周期由调用方定。

    只在 `config["api_key"]` 非空时会被调到（`resolve_llm` 那边判过），故这里是
    唯一可以直接取下标的地方。默认值沿用原实现，不新增策略。
    """
    return LLMAdapter(
        api_key=config["api_key"],
        base_url=config.get("base_url", "https://api.deepseek.com"),
        model=config.get("model", "deepseek-v4-pro"),
    )


async def _resolve_user_llm(user_id: str, storage: StorageBase | None = None) -> LLMAdapter | None:
    """解析出口**本体**：这一次给 *user_id* 用的 LLMAdapter（没配 key 时回落全局）。

    **选谁**是策略，在 `web/llm_resolution.resolve_llm`；本函数只做三件它不做的事：
    读配置、按来源缓存、返回。**不判放行** —— `preflight()` 在 `get_user_llm` 那一层。

    拆出这一层是为了保存设置那条路：用户存完自己的 key，要把活会话换到新实例上，而
    「这次出站放不放行」是**另一码事**（判定用当前的 IP 与 base_url，与用户刚存进去的
    配置无关）。保存路径跟着 `get_user_llm` 走，就会因为一个当下被拒的理由而整笔不写库。

    **只缓存 USER 结果**（§2.8）：回落与不可用都是「**当下**的事实」—— 管理员补上全局
    key、或用户刚存好自己的配置，下个请求就该生效；缓存住它们会把这件事实冻到进程重启。
    """
    if storage is None:
        storage = get_storage()
    cached = _user_llm_cache.get(user_id)
    if cached is not None:
        return cached

    config = await storage.get_user_api_config(user_id)
    resolved = resolve_llm(config, build_user=_make_user_llm, get_global=get_llm)
    if resolved.source is Source.USER:
        # 先入缓存：用户实例本身没有变坏 —— 换个放行的 IP 再来，该命中的还是这条缓存。
        _user_llm_cache[user_id] = resolved.llm
    if resolved.reason:
        print(f"[deps] {resolved.reason} (user_id={user_id})")
    return resolved.llm


async def get_user_llm(user_id: str, storage: StorageBase | None = None) -> LLMAdapter | None:
    """出站用的解析出口 = `_resolve_user_llm` + 返回前过 `preflight()`。

    **缓存命中也过 `preflight()`**：调用方把返回的实例挂进长生命周期对象（会话、
    TextManager），而入站时解析过的实例到出站时可能已经换了 IP。判定放在**每次**返回
    之前，热缓存这条路才和冷路径同口径（L3）。`LLMCallRefused` **不吞** —— 那是判定，
    交给统一出口配码。

    **不再收 `client_ip`**（§2.8）：geo 判定在调用点门上做（`web/llm_gate.py`）。
    两处各判一次就会分叉，而门那一处盖得住会话里那个陈旧实例。
    """
    llm = await _resolve_user_llm(user_id, storage)
    if llm is not None:
        llm.preflight()
    return llm


def _live_engines(match: Callable[[str], bool]) -> list[Any]:
    """内存里属主满足 *match* 的活引擎 —— 一对一与群聊两张表都看。

    一对面：`_sessions` 每条 dict 的 `"engine"`，属主是条目自己的 `"user_id"`（第 1 步
    起唯一构造点 `TextManager._create_session` 必写）。群聊面：`GroupSession.engines` 是
    card_id → ChatEngine，属主在**会话**上（`GroupSession.user_id`）。

    按 `id()` 去重：同一个实例若同时挂在两张表上，`set_llm` 幂等，但重复计数会让
    「换了几个」这个返回值骗人。
    """
    seen: dict[int, Any] = {}
    for session in _sessions.values():
        engine = session.get("engine")
        if engine is not None and match(session.get("user_id", "")):
            seen[id(engine)] = engine
    for group in _group_sessions.values():
        if match(getattr(group, "user_id", "")):
            for engine in group.engines.values():
                seen[id(engine)] = engine
    return list(seen.values())


async def refresh_user_llm(user_id: str, storage: StorageBase | None = None) -> int:
    """把 *user_id* 活会话手里的连接换成按新配置解析出来的那一个；返回换掉的引擎数。

    存完设置后由 `update_api_config` 调 —— 不踢会话、不重建 RAG，只让**下一轮**出站
    走新连接（在飞的那一轮归旧连接，见 `ChatEngine.set_llm`）。

    解析结果 `None`（自己没配 key、全局兜底也没有）时记一笔日志、返回 0：这是「这次没得
    换」，不是失败。

    **单进程前提**：`_sessions` / `_group_sessions` 是本进程的内存表（`web/server.py` 的
    `uvicorn.run` 不带 `workers`），故「活会话」就是这两张表。将来要多 worker，得改成
    「按配置版本号每轮重新解析」—— 跨进程换不了别人手里的实例。
    """
    clear_user_llm_cache(user_id)
    llm = await _resolve_user_llm(user_id, storage)
    if llm is None:
        print(f"[deps] refresh_user_llm: no usable LLM for user_id={user_id}, live sessions left as-is")
        return 0
    engines = _live_engines(lambda owner: owner == user_id)
    for engine in engines:
        engine.set_llm(llm)
    return len(engines)


def get_memory_manager() -> MemoryManager | None:
    """Return the MemoryManager singleton (lazy-init)."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(_memory_config)
    return _memory_manager


def get_storage() -> StorageBase:
    """Return the storage singleton (lazy-init)."""
    global _storage
    if _storage is None:
        _storage = get_store()
    return _storage


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the main event loop, and register the submitter that routes DB work to it.

    捕获与注册是同一件事的两半：本模块是**唯一**知道主 loop 的地方，故由它向下
    注册投递实现（`core.scheduling.set_loop_submitter`）。注册只发生在启动时捕获到
    loop 之后 —— 那之前没有主 loop 可投，`core.scheduling` 的回退语义接管。
    """
    global _main_loop
    _main_loop = loop
    scheduling.set_loop_submitter(_submit_to_main_loop)


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    """Return the captured main event loop, or None if not yet set."""
    return _main_loop


def _submit_to_main_loop(coro, *, wait: bool = True, timeout: float = 600):
    """`core.scheduling` 的注册实现（契约见那个模块）。

    wait=True 时取结果并抛出协程的异常（调用方原本就在等，串行语义不变）；
    wait=False 时不取结果、不阻塞，异常交给 done-callback 落日志 —— 那正是
    「记账/审计不该拖住请求」的写法。
    """
    fut = asyncio.run_coroutine_threadsafe(coro, _main_loop)
    if wait:
        return fut.result(timeout=timeout)
    fut.add_done_callback(_log_loop_error)
    return fut


def _log_loop_error(fut: asyncio.Future) -> None:
    """投递出去的协程没人取结果，异常只能在这里落地 —— 静默吞掉就查不出来了。"""
    if fut.cancelled():  # 取消不是失败：`exception()` 对已取消的 future 会抛 CancelledError
        return
    exc = fut.exception()
    if exc is not None:
        print(f"[deps] Submitted coroutine failed (non-fatal): {type(exc).__name__}: {exc}")


def _make_global_llm() -> LLMAdapter | None:
    """构造**全局兜底**实例（config.yaml / `DEEPSEEK_API_KEY`）；没配 key 时返回 None。

    None 是「没配」这件事的**信号**，不是失败 —— 调用方据它决定 503 还是回落。
    构造失败**不做负缓存**（每次都重试）：管理员补上 key 之后，下个请求就该能用了。
    """
    try:
        return LLMAdapter()
    except Exception as exc:
        print(f"[deps] LLMAdapter init failed (API not configured?): {exc}")
        return None


def get_llm() -> LLMAdapter | None:
    """Return the LLMAdapter singleton (lazy-init). Returns None if API is not configured.

    构造走唯一工厂 `_make_global_llm`（§2.8）—— `reset_llm_and_dependents` 用的是同一处，
    两边的「没配就是 None」口径不会分叉。
    """
    global _llm
    if _llm is None:
        _llm = _make_global_llm()
    return _llm


def get_distiller(llm: LLMAdapter | None = None) -> Distiller | None:
    """Return a new Distiller bound to *llm*, or None if llm is None.

    没有单例路径：`llm is None ⟺ get_llm() is None`，取单例那条分支走不到（缺陷 37）。

    storage 在这里注入：这是蒸馏器**唯一**的生产装配出口，七个蒸馏路由全走它。
    身份不在这里 —— 它是请求级的，由 `core.request_context` 的上下文带（缺陷 35）。
    """
    if llm is None:
        return None
    return Distiller(llm, storage=get_storage())


def get_rag_config(embedding_key: str = "", embedding_region: str = "") -> dict[str, Any]:
    """Return RAG configuration dict with optional embedding overrides."""
    cfg = dict(_rag_config)
    if embedding_key:
        cfg["embedding_key"] = embedding_key
    if embedding_region:
        cfg["embedding_region"] = embedding_region
    return cfg


def get_sessions() -> dict[str, dict[str, Any]]:
    """Return the in-memory session store."""
    return _sessions


def get_group_sessions() -> dict[str, Any]:
    """Return the in-memory group session store."""
    return _group_sessions


def touch_session(session: dict) -> None:
    """Update the last_active timestamp on a session dict."""
    session["last_active"] = time.time()


_SESSION_IDLE_TTL = int(os.getenv("SESSION_IDLE_TTL_SECONDS", "3600"))


def _outbox_of(sess: Any) -> Any | None:
    """会话条目上的补写队列 —— 一对一那张表是 dict，群聊那张表是 `GroupSession` 对象。

    两种形状都认，是为了让空闲清理与关停**共用同一个出口**（下面那个函数）：各写一遍的
    话，改了一处另一处会悄悄漏。没有队列的条目（老会话）返回 None，不为此发一次写。
    """
    if isinstance(sess, dict):
        return sess.get("outbox")
    return getattr(sess, "outbox", None)


async def flush_outboxes(sessions: dict[str, Any]) -> int:
    """把这张会话表里所有带队列的会话补写掉，返回补上的条数（只用于日志）。

    **空闲清理与关停共用这一个出口**：会话一旦从内存里消失，队列跟着消失 —— 没补上的
    消息就永久丢了。

    存储**每次调用时才解析**（`get_storage()`），与 `_assemble_text_manager` 同口径：
    队列不持有存储实例（缺陷 117）。
    """
    ping = get_storage().ping
    flushed = 0
    for sess in list(sessions.values()):
        outbox = _outbox_of(sess)
        if outbox is None or not outbox.has_pending:
            continue
        report = await outbox.flush(ping=ping)
        flushed += len(report.flushed)
    return flushed


async def _session_cleanup_loop() -> None:
    """Periodically evict idle sessions from the in-memory cache.

    Only removes from memory — never touches the database.
    Sessions with an active lock (mid-generation) are skipped.
    """
    while True:
        await asyncio.sleep(300)
        sessions = get_sessions()
        ttl = _SESSION_IDLE_TTL
        now = time.time()
        evicted = 0
        for sid, sess in list(sessions.items()):
            if now - sess.get("last_active", now) <= ttl:
                continue
            lk = sess.get("lock")
            if lk is not None and lk.locked():
                continue
            # 先补写再出队：出队之后队列就没了，没补上的消息永久丢。
            await flush_outboxes({sid: sess})
            sessions.pop(sid, None)
            evicted += 1
        if evicted:
            print(f"[session_cleanup] evicted={evicted} remaining={len(sessions)}")


def _make_indexing_service() -> IndexingService:
    """构造（不是「取得」）一个 IndexingService —— 生命周期由调用方定。"""
    return IndexingService(get_storage(), _rag_config)


def get_indexing_service() -> IndexingService | None:
    """Return the IndexingService singleton."""
    global _indexing_service
    if _indexing_service is None:
        _indexing_service = _make_indexing_service()
    return _indexing_service


def _assemble_text_manager(distiller: Distiller, llm: LLMAdapter) -> TextManager:
    """唯一的 TextManager 装配出口。

    `distiller` / `llm` 必须由调用方传实例 —— 本函数不取单例、不缓存：per-user
    路径每次都要新实例（共享即跨请求错归，见缺陷 37）。请求级身份不再靠「往
    distiller 上写属性」传，改走 `core.request_context` 的上下文（缺陷 35）。

    `get_storage` 传的是**函数**不是它的返回值：装配点在这里，取库的时机在 TextManager
    用到时 —— 中间任何一次替换（测试换 sqlite）才盖得住本对象建出的引擎（缺陷 117）。
    """
    return TextManager(get_storage, distiller, llm, get_sessions(),
                       indexing_service=get_indexing_service(),
                       memory_manager=get_memory_manager())


def get_text_manager(llm: LLMAdapter | None = None) -> TextManager | None:
    """Return a per-user TextManager instance, or None if llm is None.

    没有单例路径：`llm is None ⟺ get_llm() is None`，取单例那条分支走不到（缺陷 37）。
    """
    if llm is None:
        return None
    return _assemble_text_manager(get_distiller(llm), llm)


def get_config() -> dict[str, Any]:
    """Return the full parsed config dict."""
    return dict(_config)


def get_config_path() -> str:
    """Return the config.yaml file path."""
    return str(_CFG_PATH)


def patch_config(key: str, value: Any) -> dict[str, Any]:
    """Update a top-level key in the in-memory config and persist to disk.

    Does NOT trigger LLM reload — use for registration, rate_limits etc.
    """
    global _config
    _config[key] = value
    try:
        with open(_CFG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(_config, f, allow_unicode=True, default_flow_style=False)
    except Exception as exc:
        print(f"[deps] Failed to persist config: {exc}")
    return dict(_config)


def reset_llm_and_dependents() -> None:
    """Hot-reload: recreate LLM, IndexingService, and MemoryManager.

    **活会话也一起换**，判据是 `engine.llm is 旧全局`：自己配了 key 的用户不是在用这条
    全局连接说话，不该被管理员的热重载波及。故先记住旧实例再构造新的 —— 不先记住，
    换完之后就没法把「谁在用旧的」分辨出来了。

    新全局构造不出来（`None`）时就**不换**：拿 `None` 去 `set_llm` 会让活会话下一轮
    直接 `None.chat`。已经建起来的会话保持旧全局，直到用户自己存 key。
    """
    global _llm, _indexing_service
    global _config, _rag_config, _memory_config, _memory_manager
    old_global = _llm
    _llm = _make_global_llm()
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _config = yaml.safe_load(_f)
    _rag_config = _config["rag"]
    _indexing_service = _make_indexing_service()
    _memory_config = _config.get("memory", {})
    _memory_manager = MemoryManager(_memory_config)
    if _llm is not None and old_global is not None:
        for engine in _live_engines(lambda _owner: True):
            if engine.llm is old_global:
                engine.set_llm(_llm)


_tts_engine = None


def get_tts_engine():
    """Return the EdgeTTSEngine singleton."""
    global _tts_engine
    if _tts_engine is None:
        from speech.edge_tts_client import EdgeTTSEngine
        _tts_engine = EdgeTTSEngine()
    return _tts_engine


_voice_client = None


def get_voice_client():
    """Return the VoiceCloneClient singleton."""
    global _voice_client
    if _voice_client is None:
        from speech.voice_clone import VoiceCloneClient
        voice_cfg = _config.get("voice", {})
        _voice_client = VoiceCloneClient(
            base_url=voice_cfg.get("gptsovits_url", "http://127.0.0.1:9880"),
            cache_dir=voice_cfg.get("cache_dir", "data/voice_cache"),
        )
    return _voice_client

