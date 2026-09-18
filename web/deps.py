"""FastAPI dependency injection: singletons and config loaders."""

from __future__ import annotations

import asyncio
import os
import sys
import time
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
from fastapi import HTTPException
from storage import get_store
from storage.base import StorageBase

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


async def get_user_llm(user_id: str, storage: StorageBase | None = None, client_ip: str | None = None) -> LLMAdapter | None:
    """Get or create a per-user LLMAdapter from their saved API config.

    Falls back to the global _llm (config.yaml / DEEPSEEK_API_KEY env) if the
    user has not configured their own API key.

    If *client_ip* is provided, a geo-guard check is performed: domestic IPs
    with non-whitelisted base_url are blocked (raises HTTPException 403).
    """
    if storage is None:
        storage = get_storage()
    cached = _user_llm_cache.get(user_id)
    if cached is not None:
        return cached

    try:
        config = await storage.get_user_api_config(user_id)
        if config.get("api_key"):
            # Geo guard: block domestic IPs from using non-whitelisted APIs
            if client_ip is not None:
                from web.geo_guard import check_api_allowed
                base_url = config.get("base_url", "https://api.deepseek.com")
                allowed, reason = check_api_allowed(client_ip, base_url)
                if not allowed:
                    # 审计写入失败不得改写判定。**这里必须自己吞**：外层 `except Exception`
                    # 会把非 HTTPException 一律吃掉并回落到全局管理员 key 的 LLM ——
                    # 若让 store 的 StoreError 冒到外层，被拦截的境内 IP 反而拿到了全局 key。
                    # 容忍策略必须在 HTTPException 之前就地表达，不能藏回 store。
                    try:
                        await storage.record_geo_block(user_id, client_ip, base_url, reason)
                    except Exception as exc:
                        print(f"[deps] Record geo block failed (non-fatal): {exc}")
                    raise HTTPException(403, detail=reason)

            llm = LLMAdapter(
                api_key=config["api_key"],
                base_url=config.get("base_url", "https://api.deepseek.com"),
                model=config.get("model", "deepseek-v4-pro"),
            )
            _user_llm_cache[user_id] = llm
            return llm
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[deps] Failed to create per-user LLM for {user_id}: {exc}")

    # Fallback: global config / admin key
    return get_llm()


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


def get_llm() -> LLMAdapter | None:
    """Return the LLMAdapter singleton (lazy-init). Returns None if API is not configured."""
    global _llm
    if _llm is None:
        try:
            _llm = LLMAdapter()
        except Exception as exc:
            print(f"[deps] LLMAdapter init failed (API not configured?): {exc}")
            return None
    return _llm


def get_distiller(llm: LLMAdapter | None = None) -> Distiller | None:
    """Return a new Distiller bound to *llm*, or None if llm is None.

    没有单例路径：`llm is None ⟺ get_llm() is None`，取单例那条分支走不到（缺陷 37）。
    """
    if llm is None:
        return None
    return Distiller(llm)


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


def touch_session(session: dict) -> None:
    """Update the last_active timestamp on a session dict."""
    session["last_active"] = time.time()


_SESSION_IDLE_TTL = int(os.getenv("SESSION_IDLE_TTL_SECONDS", "3600"))


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
    路径每次都要新实例（`/run_stream` 往 distiller 上写请求级身份，共享即跨请求
    错归，见缺陷 37）。
    """
    return TextManager(get_storage(), distiller, llm, get_sessions(),
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
    """Hot-reload: recreate LLM, IndexingService, and MemoryManager."""
    global _llm, _indexing_service
    global _config, _rag_config, _memory_config, _memory_manager
    _llm = LLMAdapter()
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _config = yaml.safe_load(_f)
    _rag_config = _config["rag"]
    _indexing_service = _make_indexing_service()
    _memory_config = _config.get("memory", {})
    _memory_manager = MemoryManager(_memory_config)


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

