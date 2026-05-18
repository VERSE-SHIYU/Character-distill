"""Voice: preset Edge TTS, custom library, GPT-SoVITS stubs, ASR via FunASR WebSocket."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from deps import get_tts_engine
from speech.edge_tts_client import EdgeTTSEngine, VOICES

router = APIRouter(prefix="/api/voice", tags=["voice"])

VOICE_LIBRARY_DIR = Path("data/voice_library")
VOICE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
VOICE_LIBRARY_META = VOICE_LIBRARY_DIR / "voices.json"


def _read_voice_library() -> list[dict[str, Any]]:
    if not VOICE_LIBRARY_META.exists():
        return []
    try:
        data = json.loads(VOICE_LIBRARY_META.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _write_voice_library(library: list[dict[str, Any]]) -> None:
    VOICE_LIBRARY_META.write_text(
        json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8",
    )


# ---- Status ----

@router.get("/status")
async def voice_status() -> dict[str, Any]:
    import yaml

    funasr_ok = False
    try:
        cfg = yaml.safe_load(open("config.yaml"))
        funasr_url = cfg.get("voice", {}).get("funasr_url", "ws://127.0.0.1:10095")
        from speech.funasr_client import FunASRClient
        funasr_client = FunASRClient(url=funasr_url)
        funasr_ok = await funasr_client.is_available()
    except Exception:
        pass

    return {
        "available": True,
        "preset_voices": list(VOICES.keys()),
        "gptsovits": False,
        "funasr": funasr_ok,
    }


# ---- Voice list (presets + custom) ----

@router.get("/list")
async def list_voices() -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = [
        {"voice_id": k, "name": k, "type": "preset"} for k in VOICES
    ]
    customs = _read_voice_library()
    for entry in customs:
        entry.setdefault("type", "custom")
    return presets + customs


# ---- Custom voice library CRUD ----

@router.post("/upload")
async def upload_custom_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "请选择音频文件")
    if not name.strip():
        raise HTTPException(400, "请填写音色名称")

    ext = Path(file.filename).suffix.lower()
    if ext not in (".wav", ".mp3"):
        raise HTTPException(400, "仅支持 wav / mp3 格式")

    voice_id = uuid.uuid4().hex[:12]
    dest_path = VOICE_LIBRARY_DIR / f"{voice_id}{ext}"
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "文件大小不能超过 10MB")
    dest_path.write_bytes(content)

    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(str(dest_path))
        duration = round(audio.info.length, 1) if audio and hasattr(audio.info, "length") else 0.0
    except Exception:
        duration = 0.0

    from datetime import datetime
    entry: dict[str, Any] = {
        "voice_id": voice_id,
        "name": name.strip(),
        "ext": ext,
        "duration": duration,
        "type": "custom",
        "created_at": datetime.now().isoformat(),
    }
    library = _read_voice_library()
    library.append(entry)
    _write_voice_library(library)
    return entry


@router.delete("/{voice_id}")
async def delete_custom_voice(voice_id: str) -> dict[str, bool]:
    library = _read_voice_library()
    entry = next((v for v in library if v["voice_id"] == voice_id), None)
    if entry:
        ext = entry.get("ext", ".wav")
        (VOICE_LIBRARY_DIR / f"{voice_id}{ext}").unlink(missing_ok=True)
        library = [v for v in library if v["voice_id"] != voice_id]
        _write_voice_library(library)
    return {"ok": True}


# ---- Preview / synthesize (Edge TTS) ----

@router.get("/preview-audio/{voice_id}")
async def preview_audio(
    voice_id: str,
    engine: EdgeTTSEngine = Depends(get_tts_engine),
) -> Response:
    # If it's a custom voice, serve the raw file
    if voice_id not in VOICES:
        library = _read_voice_library()
        entry = next((v for v in library if v["voice_id"] == voice_id), None)
        if entry:
            ext = entry.get("ext", ".wav")
            path = VOICE_LIBRARY_DIR / f"{voice_id}{ext}"
            if path.exists():
                return FileResponse(str(path), media_type="audio/mpeg" if ext == ".mp3" else "audio/wav")
        raise HTTPException(404, "音色不存在")

    voice_name = VOICES[voice_id]
    audio = await engine.synthesize("你好，这是音色试听。", voice=voice_name)
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/synthesize")
async def voice_synthesize(
    request: Request,
    engine: EdgeTTSEngine = Depends(get_tts_engine),
) -> Response:
    """Edge TTS synthesis. Accepts JSON body with text + optional voice key."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body required")

    text = body.get("text", "")
    voice_key = body.get("voice", "xiaoxiao")
    if not text:
        raise HTTPException(400, "text is required")

    voice_name = VOICES.get(voice_key, VOICES["xiaoxiao"])
    audio = await engine.synthesize(text, voice=voice_name)
    return Response(content=audio, media_type="audio/mpeg")


# ---- Reference audio stubs (GPT-SoVITS not yet implemented) ----

@router.get("/ref-audio/{card_id}")
async def get_ref_audio(card_id: str) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": "Not implemented"})


@router.post("/ref-audio/upload")
async def upload_ref_audio(
    file: UploadFile = File(...),
    card_id: str = Form(...),
    prompt_text: str = Form(""),
) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": "Not implemented"})


@router.delete("/ref-audio/{card_id}")
async def delete_ref_audio(card_id: str) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": "Not implemented"})


# ---- ASR (FunASR via WebSocket) ----

@router.post("/asr")
async def speech_to_text(
    file: UploadFile = File(...),
) -> JSONResponse:
    """Convert uploaded audio to text using FunASR."""
    import os
    import subprocess
    import tempfile

    import yaml

    cfg = yaml.safe_load(open("config.yaml"))
    funasr_url = cfg.get("voice", {}).get("funasr_url", "ws://127.0.0.1:10095")

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp_in:
        content = await file.read()
        tmp_in.write(content)
        tmp_in_path = tmp_in.name

    tmp_wav_path = tmp_in_path.replace(".webm", ".wav")
    try:
        import os as _os
        _ffmpeg = _os.environ.get(
            "FFMPEG_PATH",
            r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe",
        )
        result = subprocess.run(
            [
                _ffmpeg, "-y", "-i", tmp_in_path,
                "-ar", "16000", "-ac", "1", "-f", "wav",
                tmp_wav_path,
            ],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            raise HTTPException(400, f"音频转码失败: {result.stderr.decode()[:200]}")

        with open(tmp_wav_path, "rb") as f:
            wav_data = f.read()

        pcm_data = wav_data[44:] if wav_data[:4] == b"RIFF" else wav_data

        from speech.funasr_client import FunASRClient
        client = FunASRClient(url=funasr_url)

        if not await client.is_available():
            raise HTTPException(503, "FunASR 服务未连接")

        text = await client.recognize(pcm_data)
        if not text.strip():
            return JSONResponse({"text": "", "message": "未识别到语音内容"})

        return JSONResponse({"text": text.strip()})

    except HTTPException:
        raise
    except Exception as exc:
        print(f"[voice] ASR failed: {exc}")
        raise HTTPException(500, f"语音识别失败: {str(exc)}")
    finally:
        for p in [tmp_in_path, tmp_wav_path]:
            try:
                os.unlink(p)
            except OSError:
                pass
