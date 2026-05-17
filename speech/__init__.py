"""Speech synthesis module."""

from speech.tts_engine import TTSEngine
from speech.edge_tts_client import EdgeTTSEngine, VOICES

__all__ = ["TTSEngine", "EdgeTTSEngine", "VOICES"]
