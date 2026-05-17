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
_llm = LLMAdapter()
_distiller = Distiller(_llm)
_rag_config: dict[str, Any] = _config["rag"]

# {session_id: {"engine": ChatEngine, "card": CharacterCard}}
# Transitional: kept until chat_engine migrates to storage-backed history
_sessions: dict[str, dict[str, Any]] = {}

_text_manager = TextManager(_storage, _distiller, _llm, _rag_config, _sessions)


def get_storage() -> SQLiteStore:
    """Return the SQLiteStore singleton."""
    return _storage


def get_llm() -> LLMAdapter:
    """Return the LLMAdapter singleton."""
    return _llm


def get_distiller() -> Distiller:
    """Return the Distiller singleton."""
    return _distiller


def get_rag_config() -> dict[str, Any]:
    """Return RAG configuration dict."""
    return dict(_rag_config)


def get_sessions() -> dict[str, dict[str, Any]]:
    """Return the in-memory session store."""
    return _sessions


def get_text_manager() -> TextManager:
    """Return the TextManager singleton."""
    return _text_manager


def get_config() -> dict[str, Any]:
    """Return the full parsed config dict."""
    return dict(_config)


_tts_engine = None


def get_tts_engine():
    """Return the EdgeTTSEngine singleton."""
    global _tts_engine
    if _tts_engine is None:
        from speech.edge_tts_client import EdgeTTSEngine
        _tts_engine = EdgeTTSEngine()
    return _tts_engine
