# -*- coding: utf-8 -*-
"""Phase 0 + Phase 1 backend integration checker."""

from __future__ import annotations

import json
import mimetypes
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


class IntegrationTester:
    """Integration test runner."""

    def __init__(self, repo_root: Path, base_url: str = "http://127.0.0.1:7860") -> None:
        self.repo_root = repo_root
        self.base_url = base_url.rstrip("/")
        self.server_proc: subprocess.Popen[str] | None = None
        self.uploaded_text_id = ""
        self.session_id = ""
        self.legacy_session_id = ""
        self.selected_character_name = ""
        self.results: list[tuple[str, bool, str]] = []

    def _log(self, msg: str) -> None:
        print(f"[integration] {msg}")

    def _record(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))
        state = "PASS" if ok else "FAIL"
        self._log(f"{state} - {name}" + (f" | {detail}" if detail else ""))

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base_url}{path}"

    def _port_open(self, host: str, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            return sock.connect_ex((host, port)) == 0
        finally:
            sock.close()

    def start_server(self) -> None:
        if self._port_open("127.0.0.1", 7860):
            self._log("\u68c0\u6d4b\u5230 7860 \u7aef\u53e3\u5df2\u5728\u4f7f\u7528\uff0c\u5c1d\u8bd5\u590d\u7528\u73b0\u6709\u670d\u52a1")
            return

        self.server_proc = subprocess.Popen(
            [sys.executable, "web/server.py"],
            cwd=str(self.repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.server_proc and self.server_proc.poll() is not None:
                text = ""
                if self.server_proc.stdout:
                    text = self.server_proc.stdout.read()
                raise RuntimeError(f"server.py \u63d0\u524d\u9000\u51fa: {text[:500]}")
            try:
                self.http_json("GET", "/api/text/list")
                return
            except Exception:
                time.sleep(0.6)
        raise RuntimeError("\u7b49\u5f85 server.py \u542f\u52a8\u8d85\u65f6")

    def stop_server(self) -> None:
        if not self.server_proc:
            return
        try:
            self.server_proc.terminate()
            self.server_proc.wait(timeout=8)
        except Exception:
            try:
                self.server_proc.kill()
                self.server_proc.wait(timeout=3)
            except Exception:
                pass
        self.server_proc = None

    def restart_server(self) -> None:
        self.stop_server()
        time.sleep(1.0)
        self.start_server()

    def http_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        body = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._url(path), data=body, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} HTTP {exc.code}: {detail}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{method} {path} JSON \u89e3\u6790\u5931\u8d25: {exc}") from exc

    def upload_file(self, file_path: Path) -> dict[str, Any]:
        if not file_path.exists():
            raise RuntimeError(f"\u6587\u4ef6\u4e0d\u5b58\u5728: {file_path}")
        boundary = "----Boundary" + uuid.uuid4().hex
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        content = file_path.read_bytes()
        parts = [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8"),
            content,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        req = urllib.request.Request(
            self._url("/api/text/upload"),
            data=b"".join(parts),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=80) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def read_sse(self, session_id: str) -> tuple[int, bool]:
        req = urllib.request.Request(
            self._url("/api/chat/send"),
            data=json.dumps(
                {"session_id": session_id, "message": "\u6d41\u5f0f\u8bf7\u6d4b\u8bd5", "stream": True},
                ensure_ascii=False,
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        tokens = 0
        done = False
        with urllib.request.urlopen(req, timeout=90) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text.startswith("data: "):
                    continue
                payload = json.loads(text[6:])
                if "token" in payload:
                    tokens += 1
                if payload.get("done"):
                    done = True
                    break
                if payload.get("error"):
                    raise RuntimeError(f"SSE error: {payload['error']}")
        return tokens, done

    def run(self) -> int:
        sample_file = self._pick_sample_file()
        sample_text = sample_file.read_text(encoding="utf-8", errors="ignore")
        if not sample_text.strip():
            sample_text = "\u4f60\u597d\uff0c\u8fd9\u662f\u6d4b\u8bd5\u6587\u672c\u3002"

        try:
            self.start_server()
            self._record("python web/server.py \u542f\u52a8\u65e0\u62a5\u9519", True)

            uploaded = self.upload_file(sample_file)
            self.uploaded_text_id = str(uploaded.get("id", ""))
            ok_upload = bool(self.uploaded_text_id and uploaded.get("filename") and uploaded.get("char_count") is not None)
            self._record('POST /api/text/upload -F "file=@content/*.txt"', ok_upload, json.dumps(uploaded, ensure_ascii=False)[:160])
            if not ok_upload:
                return 1

            text_list = self.http_json("GET", "/api/text/list")
            in_text_list = isinstance(text_list, list) and any(str(item.get("id")) == self.uploaded_text_id for item in text_list)
            self._record("GET /api/text/list", in_text_list)

            identify = self.http_json("POST", "/api/distill/identify", {"text_id": self.uploaded_text_id})
            chars = identify.get("characters", []) if isinstance(identify, dict) else []
            has_aliases = bool(chars) and all("aliases" in c for c in chars if isinstance(c, dict))
            self._record("POST /api/distill/identify", bool(chars) and has_aliases)
            if not chars:
                return 1
            self.selected_character_name = str(chars[0].get("name") or "").strip()
            if not self.selected_character_name:
                self._record("identify \u7ed3\u679c\u5305\u542b\u89d2\u8272\u540d", False)
                return 1

            run_card = self.http_json(
                "POST",
                "/api/distill/run",
                {"text_id": self.uploaded_text_id, "character_name": self.selected_character_name},
            )
            self.session_id = str(run_card.get("session_id", "")) if isinstance(run_card, dict) else ""
            self._record("POST /api/distill/run", bool(self.session_id))

            cards = self.http_json("GET", f"/api/distill/cards/{self.uploaded_text_id}")
            has_cards = isinstance(cards, list) and len(cards) > 0
            self._record("GET /api/distill/cards/{text_id}", has_cards)

            chat_once = self.http_json(
                "POST",
                "/api/chat/send",
                {"session_id": self.session_id, "message": "\u4f60\u597d", "stream": False},
            )
            self._record(
                "POST /api/chat/send stream=false",
                isinstance(chat_once, dict) and "reply" in chat_once and "rag_context" in chat_once,
            )

            sse_tokens, sse_done = self.read_sse(self.session_id)
            self._record("POST /api/chat/send stream=true SSE", sse_tokens > 0 and sse_done, f"tokens={sse_tokens}")

            hist = self.http_json("GET", "/api/history/list")
            hist_items = hist.get("items", []) if isinstance(hist, dict) else []
            has_hist = any(str(item.get("id")) == self.session_id for item in hist_items if isinstance(item, dict))
            self._record("GET /api/history/list", has_hist)

            detail = self.http_json("GET", f"/api/history/{self.session_id}")
            messages = detail.get("messages", []) if isinstance(detail, dict) else []
            self._record("GET /api/history/{session_id}", isinstance(messages, list) and len(messages) > 0)

            revoke_id = None
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user" and isinstance(msg.get("id"), int):
                    revoke_id = int(msg["id"])
                    break
            if revoke_id is None:
                for msg in messages:
                    if isinstance(msg, dict) and isinstance(msg.get("id"), int):
                        revoke_id = int(msg["id"])
                        break
            if revoke_id is None:
                self._record("POST /api/chat/revoke", False, "\u7f3a\u5c11 message_id")
            else:
                revoke = self.http_json("POST", "/api/chat/revoke", {"session_id": self.session_id, "message_id": revoke_id})
                self._record("POST /api/chat/revoke", isinstance(revoke, dict) and "deleted" in revoke)

            reset = self.http_json("POST", "/api/chat/reset", {"session_id": self.session_id})
            self._record("POST /api/chat/reset", isinstance(reset, dict) and reset.get("ok") is True)

            export_req = urllib.request.Request(self._url(f"/api/history/{self.session_id}/export?format=json"), method="GET")
            with urllib.request.urlopen(export_req, timeout=30) as resp:
                export_data = json.loads(resp.read().decode("utf-8", errors="replace"))
            self._record("GET /api/history/{session_id}/export?format=json", isinstance(export_data, dict))

            legacy_identify = self.http_json("POST", "/api/identify", {"text": sample_text[:2000]})
            legacy_chars = legacy_identify.get("characters", []) if isinstance(legacy_identify, dict) else []
            self._record("\u65e7\u63a5\u53e3 POST /api/identify", bool(legacy_chars))

            legacy_name = str(legacy_chars[0].get("name") or "") if legacy_chars and isinstance(legacy_chars[0], dict) else ""
            legacy_distill = self.http_json("POST", "/api/distill", {"text": sample_text[:2000], "character_name": legacy_name})
            self.legacy_session_id = str(legacy_distill.get("session_id", "")) if isinstance(legacy_distill, dict) else ""
            self._record("\u65e7\u63a5\u53e3 POST /api/distill", bool(self.legacy_session_id))

            legacy_chat_ok = False
            if self.legacy_session_id:
                legacy_chat = self.http_json("POST", "/api/chat", {"session_id": self.legacy_session_id, "message": "\u4f60\u597d"})
                legacy_chat_ok = isinstance(legacy_chat, dict) and "reply" in legacy_chat
            self._record("\u65e7\u63a5\u53e3 POST /api/chat", legacy_chat_ok)

            legacy_reset_ok = False
            if self.legacy_session_id:
                legacy_reset = self.http_json("POST", "/api/reset", {"session_id": self.legacy_session_id})
                legacy_reset_ok = isinstance(legacy_reset, dict) and legacy_reset.get("ok") is True
            self._record("\u65e7\u63a5\u53e3 POST /api/reset", legacy_reset_ok)

            delete_hist = self.http_json("DELETE", f"/api/history/{self.session_id}")
            self._record("DELETE /api/history/{session_id}", isinstance(delete_hist, dict) and delete_hist.get("ok") is True)

            self.restart_server()
            list_after_restart = self.http_json("GET", "/api/text/list")
            persisted = isinstance(list_after_restart, list) and any(str(item.get("id")) == self.uploaded_text_id for item in list_after_restart)
            self._record("\u91cd\u542f server.py \u540e GET /api/text/list \u4ecd\u6709\u6570\u636e", persisted)

            delete_text = self.http_json("DELETE", f"/api/text/{self.uploaded_text_id}")
            self._record("DELETE /api/text/{text_id}", isinstance(delete_text, dict) and delete_text.get("ok") is True)

            index_req = urllib.request.Request(self._url("/"), method="GET")
            with urllib.request.urlopen(index_req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            host_ok = ("<!doctype html" in html.lower()) or ('id="root"' in html.lower())
            self._record("python web/server.py \u6258\u7ba1 dist \u524d\u7aef\u9875\u9762", host_ok)
        finally:
            self.stop_server()

        failed = [x for x in self.results if not x[1]]
        print("\n=== integration summary ===")
        for name, ok, detail in self.results:
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {name}" + (f" | {detail}" if detail else ""))
        print(f"total={len(self.results)}, passed={len(self.results)-len(failed)}, failed={len(failed)}")
        return 1 if failed else 0

    def _pick_sample_file(self) -> Path:
        """Pick a UTF-8 text file, or generate one."""
        sample_files = sorted((self.repo_root / "content").glob("*.txt"))
        for candidate in sample_files:
            try:
                _ = candidate.read_text(encoding="utf-8")
                return candidate
            except UnicodeDecodeError:
                continue

        generated = self.repo_root / "content" / "_integration_sample_utf8.txt"
        generated.write_text(
            "\u8fd9\u662f\u7528\u4e8e\u96c6\u6210\u6d4b\u8bd5\u7684 UTF-8 \u6587\u672c\u3002\n"
            "\u89d2\u8272\uff1a\u5c0f\u660e\u3002\n"
            "\u5c0f\u660e\u8bf4\uff1a\u4eca\u5929\u5f00\u5fc3\u3002\n",
            encoding="utf-8",
        )
        return generated


def main() -> int:
    try:
        root = Path(__file__).resolve().parent.parent
        tester = IntegrationTester(repo_root=root)
        return tester.run()
    except Exception as exc:
        print(f"[integration] \u6267\u884c\u5931\u8d25: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

