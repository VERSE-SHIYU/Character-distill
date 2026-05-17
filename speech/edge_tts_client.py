"""Edge TTS client using Microsoft Edge's free TTS API."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import edge_tts

from speech.tts_engine import TTSEngine

VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",  # 女声，活泼
    "yunxi": "zh-CN-YunxiNeural",        # 男声，青年
    "xiaoyi": "zh-CN-XiaoyiNeural",      # 女声，温柔
    "yunyang": "zh-CN-YunyangNeural",    # 男声，新闻播报
}


def _resolve_voice(voice: str) -> str:
    """Resolve a short voice key or return the full ID as-is."""
    return VOICES.get(voice, voice)


class EdgeTTSEngine(TTSEngine):
    """Microsoft Edge TTS implementation with file-based caching.

    Args:
        voice: Default voice identifier.
        cache_dir: Directory for cached MP3 files.
    """

    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        cache_dir: str = "data/tts_cache",
    ) -> None:
        self.voice = voice
        self._cache_dir = Path(cache_dir)
        os.makedirs(self._cache_dir, exist_ok=True)

    @staticmethod
    def _cache_key(text: str, voice: str) -> str:
        """Return an MD5 filename for the given text+voice pair."""
        payload = f"{voice}:{text}"
        return hashlib.md5(payload.encode("utf-8")).hexdigest() + ".mp3"

    async def synthesize(
        self, text: str, voice: str | None = None
    ) -> bytes:
        """Stream audio from Edge TTS, caching results on disk.

        Args:
            text: Input text.
            voice: Override default voice (short key or full ID).

        Returns:
            MP3 audio bytes.
        """
        resolved = _resolve_voice(voice or self.voice)
        cache_path = self._cache_dir / self._cache_key(text, resolved)

        if cache_path.exists():
            return cache_path.read_bytes()

        communicate = edge_tts.Communicate(text, resolved)
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        audio = buffer.getvalue()

        cache_path.write_bytes(audio)
        return audio
