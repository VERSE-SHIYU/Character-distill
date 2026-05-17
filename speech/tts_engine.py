"""Abstract base class for TTS engines."""

from abc import ABC, abstractmethod


class TTSEngine(ABC):
    """TTS engine interface — all implementations return MP3 bytes."""

    @abstractmethod
    async def synthesize(
        self, text: str, voice: str = "zh-CN-XiaoxiaoNeural"
    ) -> bytes:
        """Synthesize text into MP3 audio bytes.

        Args:
            text: Input text to speak.
            voice: Voice identifier (e.g. ``zh-CN-XiaoxiaoNeural``).

        Returns:
            MP3-encoded audio bytes.
        """
        ...
