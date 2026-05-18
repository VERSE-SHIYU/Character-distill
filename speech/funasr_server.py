"""Minimal FunASR WebSocket server using funasr Python package.

Replaces the Docker-based FunASR runtime. Start with:

    python speech/funasr_server.py

Listens on ws://127.0.0.1:10095 — same protocol as the official runtime.
"""

from __future__ import annotations

import asyncio
import json

import websockets


class FunASRServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 10095):
        self.host = host
        self.port = port
        self._model = None
        self._model_ready = asyncio.Event()
        self._model_error = None

    def _load_model(self):
        import os
        os.environ.setdefault(
            "PATH",
            r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin" + os.pathsep + os.environ.get("PATH", ""),
        )
        from funasr import AutoModel
        return AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            model_revision="v2.0.4",
        )

    async def _load_model_async(self):
        try:
            self._model = await asyncio.to_thread(self._load_model)
            print("[FunASR] Model ready", flush=True)
        except Exception as exc:
            self._model_error = exc
            print(f"[FunASR] Model load failed: {exc}", flush=True)
        finally:
            self._model_ready.set()

    async def _ensure_model(self):
        if self._model is not None:
            return
        print("[FunASR] Waiting for model to load...", flush=True)
        await self._model_ready.wait()
        if self._model_error:
            raise RuntimeError(f"Model failed to load: {self._model_error}")
        if self._model is None:
            raise RuntimeError("Model not available")

    async def handle(self, websocket):
        audio_chunks = bytearray()
        mode = "offline"
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    audio_chunks.extend(message)
                    continue
                try:
                    data = json.loads(message)
                    if "mode" in data:
                        mode = data.get("mode", "offline")
                    if data.get("is_speaking") is False:
                        break
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                    audio_chunks.extend(message)

            if len(audio_chunks) < 1600:  # < 0.1s at 16kHz
                await websocket.send(json.dumps({"text": "", "is_final": True}))
                return

            await self._ensure_model()

            import io
            import wave

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(bytes(audio_chunks))
            wav_bytes = buf.getvalue()

            result = await asyncio.to_thread(
                self._model.generate, input=wav_bytes, batch_size_s=300,
            )
            text = ""
            if result and len(result) > 0 and "text" in result[0]:
                text = result[0]["text"]

            await websocket.send(json.dumps({
                "text": text or "",
                "is_final": True,
                "mode": mode,
            }))
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            import traceback
            print(f"[FunASR] Error: {exc}", flush=True)
            traceback.print_exc()
            try:
                await websocket.send(json.dumps({"text": "", "is_final": True}))
            except Exception:
                pass

    async def start(self):
        print(f"[FunASR] Starting WebSocket server on ws://{self.host}:{self.port}", flush=True)
        # Start WebSocket server immediately so port is bound
        async with websockets.serve(self.handle, self.host, self.port):
            print("[FunASR] Listening for connections", flush=True)
            # Load model in background (first run downloads ~1.5GB)
            print("[FunASR] Loading model in background...", flush=True)
            asyncio.create_task(self._load_model_async())
            await asyncio.Future()  # run forever


def main():
    asyncio.run(FunASRServer().start())


if __name__ == "__main__":
    main()
