"""FunASR WebSocket client — sends audio to local FunASR service for recognition."""

from __future__ import annotations

import asyncio
import json

import websockets


class FunASRClient:
    """Async client for FunASR WebSocket-based speech recognition."""

    def __init__(self, url: str = "ws://127.0.0.1:10095", timeout: float = 30.0):
        self.url = url
        self.timeout = timeout

    async def is_available(self) -> bool:
        """Check if FunASR service is reachable."""
        try:
            async with websockets.connect(self.url, open_timeout=3) as ws:
                await ws.close()
            return True
        except Exception:
            return False

    async def recognize(self, audio_bytes: bytes) -> str:
        """Send audio bytes to FunASR and return recognized text.

        Args:
            audio_bytes: PCM/WAV audio data (16kHz, 16bit, mono).

        Returns:
            Recognized text string.

        Raises:
            RuntimeError: If recognition fails.
        """
        try:
            async with websockets.connect(self.url, open_timeout=5) as ws:
                config = json.dumps({
                    "mode": "offline",
                    "chunk_size": [5, 10, 5],
                    "wav_name": "voice_input",
                    "is_speaking": True,
                    "wav_format": "pcm",
                    "audio_fs": 16000,
                })
                await ws.send(config)

                chunk_size = 10240
                for i in range(0, len(audio_bytes), chunk_size):
                    await ws.send(audio_bytes[i:i + chunk_size])

                end_msg = json.dumps({"is_speaking": False})
                await ws.send(end_msg)

                text_parts = []
                while True:
                    try:
                        result = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                        data = json.loads(result)
                        if "text" in data and data["text"]:
                            text_parts.append(data["text"])
                        if data.get("is_final", False) or data.get("mode", "") == "offline":
                            break
                    except asyncio.TimeoutError:
                        break

                return "".join(text_parts)
        except websockets.exceptions.ConnectionClosed:
            raise RuntimeError("FunASR connection closed unexpectedly")
        except ConnectionRefusedError:
            raise RuntimeError("FunASR service not reachable")
        except Exception as exc:
            raise RuntimeError(f"FunASR recognition failed: {exc}")
