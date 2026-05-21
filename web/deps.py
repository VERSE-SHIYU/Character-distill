"""FastAPI dependency injection: singletons and config loaders."""

from __future__ import annotations

import sys
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
from core.memory_manager import MemoryManager
from core.text_manager import TextManager
from storage.sqlite_store import SQLiteStore

_CFG_PATH = _REPO_ROOT / "config.yaml"

try:
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _config: dict[str, Any] = yaml.safe_load(_f)
except Exception as exc:
    print(f"[deps] Failed to read config: {exc}")
    raise

_storage = SQLiteStore(str(_REPO_ROOT / _config["storage"]["path"]))
_llm: LLMAdapter | None = None
_distiller: Distiller | None = None
_rag_config: dict[str, Any] = _config["rag"]
_summary_threshold: int = _config.get("llm", {}).get("summary_threshold", 50)

# {session_id: {"engine": ChatEngine, "card": CharacterCard}}
# Transitional: kept until chat_engine migrates to storage-backed history
_sessions: dict[str, dict[str, Any]] = {}

_text_manager: TextManager | None = None

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


async def get_user_llm(user_id: str, storage: SQLiteStore | None = None) -> LLMAdapter | None:
    """Get or create a per-user LLMAdapter from their saved API config.

    Falls back to the global _llm (config.yaml / DEEPSEEK_API_KEY env) if the
    user has not configured their own API key.
    """
    if storage is None:
        storage = _storage
    cached = _user_llm_cache.get(user_id)
    if cached is not None:
        return cached

    try:
        config = await storage.get_user_api_config(user_id)
        if config.get("api_key"):
            llm = LLMAdapter(
                api_key=config["api_key"],
                base_url=config.get("base_url", "https://api.deepseek.com"),
                model=config.get("model", "deepseek-v4-pro"),
            )
            _user_llm_cache[user_id] = llm
            return llm
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


def get_storage() -> SQLiteStore:
    """Return the SQLiteStore singleton."""
    return _storage


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


def get_distiller() -> Distiller | None:
    """Return the Distiller singleton (lazy-init). Returns None if LLM is unavailable."""
    global _distiller
    if _distiller is None:
        llm = get_llm()
        if llm is None:
            return None
        _distiller = Distiller(llm)
    return _distiller


def get_rag_config() -> dict[str, Any]:
    """Return RAG configuration dict."""
    return dict(_rag_config)


def get_sessions() -> dict[str, dict[str, Any]]:
    """Return the in-memory session store."""
    return _sessions


def get_text_manager() -> TextManager | None:
    """Return the TextManager singleton (lazy-init). Returns None if dependencies are unavailable."""
    global _text_manager
    if _text_manager is None:
        distiller = get_distiller()
        llm = get_llm()
        if distiller is None or llm is None:
            return None
        _text_manager = TextManager(_storage, distiller, llm, _rag_config, _sessions, _summary_threshold)
    return _text_manager


def get_config() -> dict[str, Any]:
    """Return the full parsed config dict."""
    return dict(_config)


def reset_llm_and_dependents() -> None:
    """Hot-reload: recreate LLM, Distiller, TextManager, and MemoryManager singletons after config.yaml changes."""
    global _llm, _distiller, _text_manager, _summary_threshold, _config, _rag_config, _memory_config, _memory_manager
    _llm = LLMAdapter()
    _distiller = Distiller(_llm)
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _config = yaml.safe_load(_f)
    _rag_config = _config["rag"]
    _summary_threshold = _config.get("llm", {}).get("summary_threshold", 50)
    _text_manager = TextManager(_storage, _distiller, _llm, _rag_config, _sessions, _summary_threshold)
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

