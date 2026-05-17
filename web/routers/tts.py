"""TTS: speech synthesis endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from deps import get_tts_engine
from speech.edge_tts_client import EdgeTTSEngine

router = APIRouter(prefix="/api/tts", tags=["tts"])


class TTSRequest(BaseModel):
    text: str
    voice: str = "xiaoxiao"


@router.post("/synthesize")
async def synthesize(
    req: TTSRequest,
    engine: EdgeTTSEngine = Depends(get_tts_engine),
) -> Response:
    """Synthesize speech from text and return MP3 audio bytes."""
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Text cannot be empty")
    if len(text) > 5000:
        raise HTTPException(400, "Text too long (max 5000 chars)")

    try:
        audio = await engine.synthesize(text, voice=req.voice)
    except Exception as exc:
        print(f"[tts] Synthesis failed: {exc}")
        raise HTTPException(500, f"TTS synthesis failed: {exc}") from exc

    return Response(content=audio, media_type="audio/mpeg")
