"""Text manager: format parsing, upload, and cached character distillation."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import time
import uuid
from pathlib import Path
from typing import Any

from adapters.llm_adapter import LLMAdapter
from core.chat_engine import ChatEngine
from core.distiller import Distiller
from core.rag import RAGEngine
from core.schema import CharacterCard
from storage.sqlite_store import SQLiteStore


class TextManager:
    """Handles text upload with format parsing and cached character distillation.

    Owns the full lifecycle: parse file -> save text -> identify characters
    -> distill card -> create in-memory session -> persist to storage.
    """

    def __init__(
        self,
        storage: SQLiteStore,
        distiller: Distiller,
        llm: LLMAdapter,
        rag_config: dict[str, Any],
        sessions: dict[str, dict[str, Any]],
    ) -> None:
        self._storage = storage
        self._distiller = distiller
        self._llm = llm
        self._rag_config = rag_config
        self._sessions = sessions

    # ---- Format parsing ----

    @staticmethod
    def _parse_content(filename: str, raw: str) -> str:
        """Extract text body from various file formats.

        .txt / .md / .log  -> as-is
        .json              -> extract first text-like field, or pretty-print
        .csv               -> join all cells per row with space, rows with newline
        other              -> as-is
        """
        ext = Path(filename).suffix.lower()

        if ext in (".txt", ".md", ".log", ""):
            return raw

        if ext == ".json":
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return raw
            if isinstance(data, dict):
                for key in ("text", "content", "body", "data"):
                    if key in data and isinstance(data[key], str):
                        return data[key]
                return json.dumps(data, ensure_ascii=False, indent=2)
            if isinstance(data, list):
                parts: list[str] = []
                for item in data:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        for key in ("text", "content", "body"):
                            if key in item and isinstance(item[key], str):
                                parts.append(item[key])
                                break
                return "\n".join(parts) if parts else raw
            return raw

        if ext == ".csv":
            try:
                reader = csv.reader(io.StringIO(raw))
                return "\n".join(" ".join(row) for row in reader)
            except csv.Error:
                return raw

        return raw

    # ---- File-based upload (PDF/DOCX support) ----

    async def upload_text_from_file(self, file_path: str, filename: str, title: str = "", description: str = "") -> str:
        """Parse an on-disk file and save to storage. Returns text_id."""
        import aiofiles

        ext = Path(filename).suffix.lower()

        if ext in (".txt", ".md", ".log", ""):
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
            parsed = content.strip()
        elif ext == ".json":
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                raw = await f.read()
            parsed = self._parse_content(filename, raw).strip()
        elif ext == ".csv":
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                raw = await f.read()
            parsed = self._parse_content(filename, raw).strip()
        elif ext == ".pdf":
            parsed = await asyncio.to_thread(self._extract_pdf, file_path)
        elif ext == ".docx":
            parsed = await asyncio.to_thread(self._extract_docx, file_path)
        else:
            raise ValueError(f"Unsupported file extension: {ext}")

        if not parsed or not parsed.strip():
            raise ValueError("Text content is empty after parsing")

        text_id = uuid.uuid4().hex[:12]
        try:
            await self._storage.save_text(text_id, filename, parsed.strip(), title, description)
        except Exception as exc:
            print(f"[TextManager] Save text failed: {exc}")
            raise
        return text_id

    @staticmethod
    def _extract_pdf(file_path: str) -> str:
        """Extract text from PDF, returning Markdown format."""
        import pymupdf4llm

        try:
            md_text = pymupdf4llm.to_markdown(file_path)
            if not md_text or not md_text.strip():
                raise ValueError("PDF 提取文本为空，可能是扫描件或图片PDF")
            return md_text
        except Exception as e:
            raise ValueError(f"PDF 解析失败: {str(e)}") from e

    @staticmethod
    def _extract_docx(file_path: str) -> str:
        """Extract text from DOCX, joining paragraphs."""
        from docx import Document

        try:
            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            if not paragraphs:
                raise ValueError("DOCX 文件无有效文本内容")
            return "\n\n".join(paragraphs)
        except Exception as e:
            raise ValueError(f"DOCX 解析失败: {str(e)}") from e

    # ---- Public API ----

    async def upload_text(self, filename: str, content: str, title: str = "", description: str = "") -> str:
        """Parse content by file extension, save to storage, return text_id."""
        parsed = self._parse_content(filename, content).strip()
        if not parsed:
            raise ValueError("Text content is empty after parsing")

        text_id = uuid.uuid4().hex[:12]
        try:
            await self._storage.save_text(text_id, filename, parsed, title, description)
        except Exception as exc:
            print(f"[TextManager] Save text failed: {exc}")
            raise
        return text_id

    async def distill_all(self, text_id: str) -> list[dict[str, Any]]:
        """Identify every character in a stored text and distill each one.

        Skips characters that fail distillation rather than aborting the batch.
        """
        text_rec = await self._storage.get_text(text_id)
        if not text_rec:
            raise ValueError("Text not found")
        content = text_rec["content"]

        try:
            chars = await asyncio.to_thread(
                self._distiller.identify_characters, content
            )
        except Exception as exc:
            print(f"[TextManager] Identify characters failed: {exc}")
            raise

        results: list[dict[str, Any]] = []
        for char_info in chars:
            name = char_info.get("name", "")
            if not name:
                continue
            try:
                card = await asyncio.to_thread(
                    self._distiller.distill, content, name
                )
            except Exception as exc:
                print(f"[TextManager] Distill '{name}' failed, skipping: {exc}")
                continue

            card_id = uuid.uuid4().hex[:12]
            try:
                await self._storage.save_card(
                    card_id, text_id, card.name, card.model_dump_json()
                )
            except Exception as exc:
                print(f"[TextManager] Save card '{name}' failed, skipping: {exc}")
                continue

            result = card.model_dump()
            result["card_id"] = card_id
            results.append(result)

        return results

    async def get_or_distill(
        self, text_id: str, character_name: str
    ) -> dict[str, Any]:
        """Return a card + fresh session. Reuses a cached card when available."""
        text_rec = await self._storage.get_text(text_id)
        if not text_rec:
            raise ValueError("Text not found")
        content = text_rec["content"]

        existing_cards = await self._storage.list_cards(text_id)
        card: CharacterCard | None = None
        card_id: str | None = None
        for c in existing_cards:
            if c["name"] == character_name:
                card_id = c["id"]
                try:
                    card = CharacterCard.model_validate_json(c["card_json"])
                except Exception as exc:
                    print(f"[TextManager] Parse cached card failed: {exc}")
                    card = None
                break

        if card is None:
            try:
                card = await asyncio.to_thread(
                    self._distiller.distill, content, character_name
                )
            except Exception as exc:
                print(f"[TextManager] Distill '{character_name}' failed: {exc}")
                raise

            card_id = uuid.uuid4().hex[:12]
            try:
                await self._storage.save_card(
                    card_id, text_id, card.name, card.model_dump_json()
                )
            except Exception as exc:
                print(f"[TextManager] Save card failed: {exc}")
                raise

        try:
            all_characters: list[dict[str, Any]] = [
                {"name": c["name"], "aliases": []} for c in existing_cards
            ]
            session_id = await asyncio.to_thread(
                self._create_session, content, card, all_characters
            )
        except Exception as exc:
            print(f"[TextManager] Create session failed: {exc}")
            raise

        try:
            await self._storage.save_session(session_id, card_id, "", "")
        except Exception as exc:
            print(f"[TextManager] Persist session failed (non-fatal): {exc}")

        result = card.model_dump()
        result["session_id"] = session_id
        result["card_id"] = card_id
        return result

    async def switch_character(
        self, text_id: str, character_name: str
    ) -> dict[str, Any]:
        """Switch to another character from the same text.

        Reuses the cached card if it exists, otherwise distills on the fly.
        Always creates a fresh session with a new RAG index.
        """
        return await self.get_or_distill(text_id, character_name)

    # ---- Internal helpers ----

    def _create_session(
        self,
        text: str,
        card: CharacterCard,
        all_characters: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build RAG + ChatEngine in memory and return session_id. (sync)"""
        import os as _os
        import torch as _torch

        _os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        _os.environ.setdefault("ACCELERATE_CPU_DEVICE", "true")
        _os.environ.setdefault("ACCELERATE_USE_DEVICE_MAP", "false")
        _torch.set_default_device("cpu")

        rag = RAGEngine(self._rag_config)
        rag.index(text, all_characters=all_characters)
        engine = ChatEngine(self._llm, rag, card, all_characters=all_characters)
        session_id = hashlib.md5(
            f"{card.name}_{time.time()}".encode()
        ).hexdigest()[:12]
        self._sessions[session_id] = {"engine": engine, "card": card, "message_ids": []}
        return session_id
