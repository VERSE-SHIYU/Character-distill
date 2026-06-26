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
_distiller: Distiller | None = None
_rag_config: dict[str, Any] = _config["rag"]
_summary_threshold: int = _config.get("llm", {}).get("summary_threshold", 50)

# {session_id: {"engine": ChatEngine, "card": CharacterCard}}
# Transitional: kept until chat_engine migrates to storage-backed history
_sessions: dict[str, dict[str, Any]] = {}

_text_manager: TextManager | None = None
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
                    await storage.record_geo_block(user_id, client_ip, base_url, reason)
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
    """Capture the main event loop so background threads can schedule DB work on it."""
    global _main_loop
    _main_loop = loop


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    """Return the captured main event loop, or None if not yet set."""
    return _main_loop


def run_on_main_loop(coro, timeout=600):
    """Submit a coroutine to the main event loop and block until it completes.

    All DB I/O from background threads MUST go through this function so that
    the asyncpg pool is only ever touched from the main event loop.
    ``run_coroutine_threadsafe`` schedules the coroutine on the main loop and
    ``future.result()`` blocks the calling thread until it finishes — keeping
    the original serial semantics intact.

    Falls back to ``asyncio.run()`` if the main loop reference has not been
    captured (should never happen in normal operation, but prevents a hard
    crash if startup order changes).
    """
    loop = get_main_loop()
    if loop is None:
        import warnings
        warnings.warn("[deps] Main loop not captured, falling back to asyncio.run()")
        return asyncio.run(coro)
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


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
    """Return the Distiller singleton (lazy-init), or a per-user instance if llm is given."""
    if llm is not None:
        return Distiller(llm)
    global _distiller
    if _distiller is None:
        fallback = get_llm()
        if fallback is None:
            return None
        _distiller = Distiller(fallback)
    return _distiller


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


def get_indexing_service() -> IndexingService | None:
    """Return the IndexingService singleton."""
    global _indexing_service
    if _indexing_service is None:
        _indexing_service = IndexingService(get_storage(), _rag_config)
    return _indexing_service


def get_text_manager(llm: LLMAdapter | None = None) -> TextManager | None:
    """Return the TextManager singleton (lazy-init), or a per-user instance if llm is given."""
    indexing_svc = get_indexing_service()
    if llm is not None:
        return TextManager(get_storage(), Distiller(llm), llm, _rag_config, _sessions, _summary_threshold,
                           indexing_service=indexing_svc)
    global _text_manager
    if _text_manager is None:
        distiller = get_distiller()
        fallback = get_llm()
        if distiller is None or fallback is None:
            return None
        _text_manager = TextManager(get_storage(), distiller, fallback, _rag_config, _sessions, _summary_threshold,
                                    indexing_service=indexing_svc)
    return _text_manager


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
    """Hot-reload: recreate LLM, Distiller, TextManager, IndexingService, and MemoryManager."""
    global _llm, _distiller, _text_manager, _indexing_service
    global _summary_threshold, _config, _rag_config, _memory_config, _memory_manager
    _llm = LLMAdapter()
    _distiller = Distiller(_llm)
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _config = yaml.safe_load(_f)
    _rag_config = _config["rag"]
    _summary_threshold = _config.get("llm", {}).get("summary_threshold", 50)
    _indexing_service = IndexingService(get_storage(), _rag_config)
    _text_manager = TextManager(get_storage(), _distiller, _llm, _rag_config, _sessions, _summary_threshold,
                                indexing_service=_indexing_service)
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

