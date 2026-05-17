"""GPT-SoVITS HTTP API client for voice cloning."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx


class VoiceCloneClient:
    """GPT-SoVITS HTTP API client."""

    def __init__(self, base_url: str, cache_dir: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def health_check(self) -> bool:
        """Check if GPT-SoVITS service is reachable."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
                resp = await client.get(self._base_url)
                return resp.is_success or resp.status_code < 500
        except Exception:
            return False

    async def synthesize(
        self,
        text: str,
        ref_audio_path: str,
        prompt_text: str,
        text_lang: str = "zh",
        prompt_lang: str = "zh",
        speed_factor: float = 1.0,
    ) -> str:
        """Synthesize voice via GPT-SoVITS. Returns cache file path.

        Raises:
            ConnectionError: GPT-SoVITS service is unreachable.
            ValueError: Synthesis returned an error.
        """
        key = self._cache_key(text, ref_audio_path, speed_factor)
        cache_path = self._cache_path(key)

        if cache_path.exists():
            return str(cache_path)

        payload = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang,
            "media_type": "wav",
            "streaming_mode": False,
            "speed_factor": speed_factor,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                resp = await client.post(
                    f"{self._base_url}/tts",
                    json=payload,
                )
        except httpx.TimeoutException:
            raise ConnectionError("GPT-SoVITS 服务响应超时（60秒）")
        except httpx.ConnectError:
            raise ConnectionError(f"无法连接 GPT-SoVITS 服务 ({self._base_url})")
        except Exception as exc:
            raise ConnectionError(f"GPT-SoVITS 请求异常: {exc}")

        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("detail", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            raise ValueError(f"语音合成失败 ({resp.status_code}): {detail}")

        cache_path.write_bytes(resp.content)
        return str(cache_path)

    def _cache_key(self, text: str, ref_path: str, speed: float) -> str:
        raw = f"{text}|{ref_path}|{speed:.2f}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.wav"

    def get_cached(self, text: str, ref_path: str, speed: float) -> str | None:
        """Return cached audio path if it exists."""
        cache_path = self._cache_path(self._cache_key(text, ref_path, speed))
        return str(cache_path) if cache_path.exists() else None
