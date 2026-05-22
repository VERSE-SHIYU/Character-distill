"""Voice: preset Edge TTS, custom library, GPT-SoVITS stubs, ASR via FunASR WebSocket."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from deps import get_config, get_storage, get_tts_engine, get_voice_client
from speech.edge_tts_client import EdgeTTSEngine, VOICES
from speech.voice_clone import VoiceCloneClient
from limiter import limiter
from routers.auth import get_current_user

router = APIRouter(prefix="/api/voice", tags=["voice"])

VOICE_LIBRARY_DIR = Path("data/voice_library")
VOICE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
VOICE_LIBRARY_META = VOICE_LIBRARY_DIR / "voices.json"

REF_AUDIO_DIR = Path("data/ref_audio")
REF_AUDIO_DIR.mkdir(parents=True, exist_ok=True)


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
async def voice_status(
    user: dict = Depends(get_current_user),
    voice_client: VoiceCloneClient = Depends(get_voice_client),
    config: dict[str, Any] = Depends(get_config),
) -> dict[str, Any]:
    funasr_ok = False
    try:
        funasr_url = config.get("voice", {}).get("funasr_url", "ws://127.0.0.1:10095")
        from speech.funasr_client import FunASRClient
        funasr_client = FunASRClient(url=funasr_url)
        funasr_ok = await funasr_client.is_available()
    except Exception:
        pass

    gptsovits_ok = False
    try:
        gptsovits_ok = await voice_client.health_check()
    except Exception:
        pass

    return {
        "available": True,
        "preset_voices": list(VOICES.keys()),
        "gptsovits": gptsovits_ok,
        "funasr": funasr_ok,
    }


# ---- Voice list (presets + custom) ----

@router.get("/list")
async def list_voices(
    user: dict = Depends(get_current_user),
) -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = [
        {"voice_id": k, "name": k, "type": "preset"} for k in VOICES
    ]
    customs = _read_voice_library()
    for entry in customs:
        entry.setdefault("type", "custom")
    return presets + customs


# ---- Custom voice library CRUD ----

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

def _extract_audio_from_video(filepath: Path) -> Path:
    """Extract audio track from video file using ffmpeg. Returns new .wav path."""
    import subprocess
    wav_path = filepath.with_suffix(".wav")
    ffmpeg = os.environ.get("FFMPEG_PATH", "ffmpeg")
    result = subprocess.run(
        [ffmpeg, "-y", "-i", str(filepath), "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", str(wav_path)],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0:
        err_msg = result.stderr.decode()[:200] if result.stderr else "unknown error"
        raise HTTPException(400, f"视频音频提取失败: {err_msg}")
    filepath.unlink()  # Remove original video, keep audio only
    return wav_path


@router.post("/upload")
async def upload_custom_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "请选择音频或视频文件")
    if not name.strip():
        raise HTTPException(400, "请填写音色名称")

    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTS and ext not in VIDEO_EXTS:
        raise HTTPException(400, "仅支持音频（wav/mp3/flac）和视频（mp4/mov/avi/mkv/webm）格式")

    voice_id = uuid.uuid4().hex[:12]
    dest_path = VOICE_LIBRARY_DIR / f"{voice_id}{ext}"
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "文件大小不能超过 20MB")
    dest_path.write_bytes(content)

    # Extract audio from video
    if ext in VIDEO_EXTS:
        dest_path = _extract_audio_from_video(dest_path)
        ext = ".wav"

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
async def delete_custom_voice(
    voice_id: str,
    user: dict = Depends(get_current_user),
) -> dict[str, bool]:
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
    user: dict = Depends(get_current_user),
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
@limiter.limit("20/minute")
async def voice_synthesize(
    request: Request,
    user: dict = Depends(get_current_user),
    engine: EdgeTTSEngine = Depends(get_tts_engine),
    storage = Depends(get_storage),
    voice_client: VoiceCloneClient = Depends(get_voice_client),
) -> Response:
    """Synthesize speech. Uses GPT-SoVITS if ref audio exists, otherwise Edge TTS."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body required")

    text = body.get("text", "")
    if not text:
        raise HTTPException(400, "text is required")

    voice_key = body.get("voice", "xiaoxiao")
    card_id = body.get("card_id", "")

    # Try GPT-SoVITS if card has reference audio
    if card_id:
        try:
            ref_json_str = await storage.get_session_voice_ref(card_id)
            if ref_json_str:
                ref_data = json.loads(ref_json_str)
                ref_path = ref_data.get("path", "")
                if ref_path and Path(ref_path).exists():
                    if await voice_client.health_check():
                        cache_path = await voice_client.synthesize(
                            text=text,
                            ref_audio_path=ref_path,
                            prompt_text=ref_data.get("ref_text", ""),
                        )
                        audio = Path(cache_path).read_bytes()
                        return Response(content=audio, media_type="audio/wav")
                    else:
                        print("[voice] GPT-SoVITS not available, falling back to Edge TTS")
        except Exception as exc:
            print(f"[voice] GPT-SoVITS synthesis failed, falling back to Edge TTS: {exc}")

    # Fallback: Edge TTS
    voice_name = VOICES.get(voice_key, VOICES["xiaoxiao"])
    audio = await engine.synthesize(text, voice=voice_name)
    return Response(content=audio, media_type="audio/mpeg")


# ---- Reference audio for GPT-SoVITS voice cloning ----

@router.get("/ref-audio/{card_id}")
async def get_ref_audio(
    card_id: str,
    user: dict = Depends(get_current_user),
    storage = Depends(get_storage),
) -> JSONResponse:
    """Get reference audio info for a character card."""
    try:
        ref_json_str = await storage.get_session_voice_ref(card_id)
        if ref_json_str:
            ref_data = json.loads(ref_json_str)
            if ref_data.get("path") and Path(ref_data["path"]).exists():
                return JSONResponse(ref_data)
        return JSONResponse({"exists": False})
    except Exception as exc:
        print(f"[voice] Get ref audio failed: {exc}")
        return JSONResponse({"exists": False})


@router.post("/ref-audio/upload")
async def upload_ref_audio(
    card_id: str = Form(...),
    ref_text: str = Form(""),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    storage = Depends(get_storage),
) -> JSONResponse:
    """Upload a reference audio file and bind it to a character card.

    Supports audio (wav/mp3/flac) and video (mp4/mov/avi/mkv/webm).
    Video files are auto-converted: audio track extracted to 16kHz mono wav.
    """
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in AUDIO_EXTS and ext not in VIDEO_EXTS:
        raise HTTPException(400, f"不支持的格式: {ext}。支持音频（wav/mp3/flac）和视频（mp4/mov/avi/mkv/webm）")

    filename = f"{card_id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = REF_AUDIO_DIR / filename

    import aiofiles
    async with aiofiles.open(filepath, "wb") as f:
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(400, "文件过大，最大支持 20MB")
        await f.write(content)

    # Extract audio from video
    if ext in VIDEO_EXTS:
        import subprocess
        wav_path = filepath.with_suffix(".wav")
        ffmpeg = os.environ.get("FFMPEG_PATH", "ffmpeg")
        result = subprocess.run(
            [ffmpeg, "-y", "-i", str(filepath), "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", str(wav_path)],
            capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            err_msg = result.stderr.decode()[:200] if result.stderr else "unknown error"
            filepath.unlink(missing_ok=True)
            raise HTTPException(400, f"视频音频提取失败: {err_msg}")
        filepath.unlink()  # Remove video, keep audio
        filepath = wav_path
        ext = ".wav"

    ref_json = json.dumps({
        "path": str(filepath),
        "filename": file.filename,
        "ref_text": ref_text,
        "exists": True,
    }, ensure_ascii=False)

    try:
        await storage.update_session_voice_ref(card_id, ref_json)
    except Exception as exc:
        print(f"[voice] Save ref audio metadata failed: {exc}")

    return JSONResponse({"ok": True, "path": str(filepath), "ref_text": ref_text, "exists": True})


@router.delete("/ref-audio/{card_id}")
async def delete_ref_audio(
    card_id: str,
    user: dict = Depends(get_current_user),
    storage = Depends(get_storage),
) -> JSONResponse:
    """Delete reference audio for a character card."""
    try:
        ref_json_str = await storage.get_session_voice_ref(card_id)
        if ref_json_str:
            ref_data = json.loads(ref_json_str)
            filepath = ref_data.get("path")
            if filepath and Path(filepath).exists():
                Path(filepath).unlink()
            await storage.update_session_voice_ref(card_id, "")
        return JSONResponse({"ok": True})
    except Exception as exc:
        print(f"[voice] Delete ref audio failed: {exc}")
        raise HTTPException(500, str(exc))


# ---- ASR (FunASR via WebSocket) ----

@router.post("/asr")
async def speech_to_text(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    config: dict[str, Any] = Depends(get_config),
) -> JSONResponse:
    """Convert uploaded audio to text using FunASR."""
    import os
    import subprocess
    import tempfile

    funasr_url = config.get("voice", {}).get("funasr_url", "ws://127.0.0.1:10095")

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp_in:
        content = await file.read()
        tmp_in.write(content)
        tmp_in_path = tmp_in.name

    tmp_wav_path = tmp_in_path.replace(".webm", ".wav")
    try:
        import os as _os
        _ffmpeg = _os.environ.get("FFMPEG_PATH", "ffmpeg")
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

        import io as _io
        import wave as _wave
        _buf = _io.BytesIO(wav_data)
        with _wave.open(_buf, "rb") as _wf:
            pcm_data = _wf.readframes(_wf.getnframes())

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
