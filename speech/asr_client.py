"""FunASR HTTP API client for speech recognition."""

from __future__ import annotations

from pathlib import Path

import httpx


class ASRClient:
    """FunASR HTTP API client."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def health_check(self) -> bool:
        """Check if FunASR service is reachable."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
                resp = await client.get(self._base_url)
                return resp.is_success or resp.status_code < 500
        except Exception:
            return False

    async def transcribe(self, audio_path: str) -> str:
        """Send audio to FunASR and return recognized text.

        Raises:
            ConnectionError: FunASR service is unreachable.
            ValueError: Transcription returned an error or empty result.
        """
        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise ValueError(f"音频文件不存在: {audio_path}")

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                with open(audio_path, "rb") as f:
                    resp = await client.post(
                        f"{self._base_url}/asr",
                        files={"file": (audio_file.name, f, "audio/wav")},
                    )
        except httpx.ConnectError:
            raise ConnectionError(f"无法连接 FunASR 服务 ({self._base_url})")
        except Exception as exc:
            raise ConnectionError(f"FunASR 请求异常: {exc}")

        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("detail", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            raise ValueError(f"语音识别失败 ({resp.status_code}): {detail}")

        try:
            data = resp.json()
            text = data.get("text", "")
        except Exception:
            raise ValueError(f"语音识别响应解析失败: {resp.text[:200]}")

        if not text or not text.strip():
            raise ValueError("语音识别结果为空（可能是静音或噪音）")

        return text.strip()
