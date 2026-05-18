"""FastAPI dependency injection: singletons and config loaders."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml
from adapters.llm_adapter import LLMAdapter
from core.distiller import Distiller
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
    """Hot-reload: recreate LLM, Distiller, and TextManager singletons after config.yaml changes."""
    global _llm, _distiller, _text_manager, _summary_threshold, _config, _rag_config
    _llm = LLMAdapter()
    _distiller = Distiller(_llm)
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _config = yaml.safe_load(_f)
    _rag_config = _config["rag"]
    _summary_threshold = _config.get("llm", {}).get("summary_threshold", 50)
    _text_manager = TextManager(_storage, _distiller, _llm, _rag_config, _sessions, _summary_threshold)


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


_asr_client = None


def get_asr_client():
    """Return the ASRClient singleton."""
    global _asr_client
    if _asr_client is None:
        from speech.asr_client import ASRClient
        voice_cfg = _config.get("voice", {})
        _asr_client = ASRClient(
            base_url=voice_cfg.get("funasr_url", "http://127.0.0.1:10095"),
        )
    return _asr_client
