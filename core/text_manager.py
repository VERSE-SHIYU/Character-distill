"""Text manager: format parsing, upload, and cached character distillation."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import uuid
from pathlib import Path
from typing import Any

from adapters.llm_adapter import LLMAdapter
from core.chat_engine import ChatEngine
from core.chat_preprocessor import ChatPreprocessor
from core.distiller import Distiller
from core.moderation.card_guard import GuardVerdict, guard_card_obj
from core.schema import CharacterCard
from core.text_failure import TEXT_FAILURE_MESSAGES as _MSG
from core.utils import try_record_usage
from storage.base import StorageBase, new_review_id


class TextManager:
    """Handles text upload with format parsing and cached character distillation.

    Owns the full lifecycle: parse file -> save text -> identify characters
    -> distill card -> create in-memory session -> persist to storage.
    """

    def __init__(
        self,
        storage: StorageBase,
        distiller: Distiller,
        llm: LLMAdapter,
        sessions: dict[str, dict[str, Any]],
        *,
        indexing_service=None,
        memory_manager,
    ) -> None:
        self._storage = storage
        self._guard_enabled = os.getenv("CARD_GUARD_ENABLED", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        self._distiller = distiller
        self._llm = llm
        self._sessions = sessions
        self._indexing_service = indexing_service
        self._memory_manager = memory_manager

    # ── Prompt-injection field guard (2.1/2.6) ─────────────
    # Runs on every freshly distilled card before persistence. Flagged leaves
    # are neutralized in place; a flag or a judge error is written to
    # review_log so the card is held out of the market until an admin clears it.
    # Judge LLM calls are pushed to a worker thread so the event loop isn't blocked.
    # OFF by default (CARD_GUARD_ENABLED=1 turns it on): measured (2026-09-08)
    # zero detection on narrativized residue + FP 0/23 means the per-distill LLM
    # call is pure cost today; re-enable once the judge targets executable-config
    # field landings (decision_style/speaking_style/values + precedence wording).

    async def _guard_card(self, card: CharacterCard, user_id: str = "") -> GuardVerdict:
        if not self._guard_enabled:
            return GuardVerdict()
        if self._llm is None:
            return GuardVerdict()
        try:
            return await asyncio.to_thread(
                guard_card_obj, card, self._llm, storage=self._storage, user_id=user_id,
            )
        except Exception as exc:
            print(f"[TextManager] Card guard crashed (flagging): {exc}")
            return GuardVerdict(error=True, error_msg=f"{type(exc).__name__}: {exc}")

    async def _flag_review(self, card_id: str, user_id: str, reason: str) -> None:
        try:
            await self._storage.save_review_log(
                new_review_id(), card_id, user_id, "flag", reason
            )
        except Exception as exc:
            print(f"[TextManager] Record review flag failed (non-fatal): {exc}")

    @staticmethod
    def _parse_wechat_json(data: dict) -> str:
        """Extract text + quote messages from wechat JSON export into clean transcript.

        Only ``renderType`` in (``text``, ``quote``) is kept — system / emoji /
        image / voice / video / voip / link / file / transfer / redPacket are
        all discarded.  Quote messages include the referenced content inline.
        Output is one line per message: ``[2025-04-15] 发送者: 内容``.
        """
        messages = data.get("messages")
        if not isinstance(messages, list):
            return ""

        conv = data.get("conversation", {}) if isinstance(data.get("conversation"), dict) else {}
        partner_name = conv.get("displayName", "") or conv.get("username", "")
        account_name = data.get("account", "")

        def _resolve_sender(msg: dict) -> str:
            disp = (msg.get("senderDisplayName") or "").strip()
            if disp:
                return disp
            user = msg.get("senderUsername", "")
            if account_name and user == account_name:
                return "我"
            if partner_name:
                return partner_name
            return user

        def _extract_quote_text(msg: dict) -> str | None:
            quote = msg.get("quote")
            if isinstance(quote, dict):
                q_sender = _resolve_sender(quote)
                q_content = (quote.get("content") or "").strip()
                if q_content:
                    return f"[引用 {q_sender}: {q_content}]"
            # Flat quoteContent field (MemoTrace variant)
            qc = (msg.get("quoteContent") or "").strip()
            if qc:
                return f"[引用: {qc}]"
            return None

        lines: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            rt = msg.get("renderType", "")
            if rt not in ("text", "quote"):
                continue

            content = (msg.get("content") or "").strip()
            quote_part = _extract_quote_text(msg) if rt == "quote" else None

            if not content and not quote_part:
                continue

            sender = _resolve_sender(msg)
            date_str = (msg.get("createTimeText") or "")[:10]
            combined = f"{quote_part} {content}".strip() if quote_part else content
            lines.append(f"[{date_str}] {sender}: {combined}")

        return "\n".join(lines)

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
                # Wechat JSON export: detect by messages list + schemaVersion or conversation
                if isinstance(data.get("messages"), list) and (
                    "schemaVersion" in data or "conversation" in data
                ):
                    cleaned = TextManager._parse_wechat_json(data)
                    if cleaned:
                        return cleaned
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

    async def upload_text_from_file(self, file_path: str, filename: str, title: str = "", description: str = "", text_type: str = "story", user_id: str = "") -> dict[str, Any]:
        """Parse an on-disk file and save to storage. Returns dict with text_id and char stats."""
        ext = Path(filename).suffix.lower()

        if ext in (".txt", ".md", ".log", ""):
            content = await self._read_text_file(file_path)
            parsed = content.strip()
        elif ext == ".json":
            raw = await self._read_text_file(file_path)
            parsed = self._parse_content(filename, raw).strip()
        elif ext == ".csv":
            raw = await self._read_text_file(file_path)
            parsed = self._parse_content(filename, raw).strip()
        elif ext == ".pdf":
            parsed = await asyncio.to_thread(self._extract_pdf, file_path)
        elif ext == ".docx":
            parsed = await asyncio.to_thread(self._extract_docx, file_path)
        else:
            raise ValueError(_MSG["unsupported_ext"].format(ext=ext))

        if not parsed or not parsed.strip():
            raise ValueError(_MSG["empty_after_parse"])

        original_chars = len(parsed)
        max_chars = 2_000_000 if text_type == "chat" else 1_000_000
        if original_chars > max_chars:
            limit_text = "200 万" if text_type == "chat" else "100 万"
            raise ValueError(_MSG["too_long"].format(limit_text=limit_text))

        # Chat preprocessing: Layer 0 (format clean) + Layer 1 (quality filter)
        # Layer 2 (character context) is deferred to distillation time.
        cleaned = parsed.strip()
        cleaned_chars = original_chars
        if text_type == "chat":
            preprocessor = ChatPreprocessor()
            cleaned = preprocessor.preprocess(cleaned)  # no target → only Layer 0+1
            cleaned_chars = len(cleaned)
            if not cleaned.strip():
                raise ValueError(_MSG["chat_clean_empty"])

        text_id = uuid.uuid4().hex[:12]

        try:
            occ = original_chars if text_type == "chat" else None
            await self._storage.save_text(text_id, filename, cleaned, title, description, text_type, occ, user_id)
        except Exception as exc:
            print(f"[TextManager] Save text failed: {exc}")
            raise
        return {"text_id": text_id, "original_chars": original_chars, "cleaned_chars": cleaned_chars}

    @staticmethod
    async def _read_text_file(file_path: str) -> str:
        """Read a text file with encoding fallback for Chinese sources.

        Tries utf-8-sig (strips BOM) -> utf-8 -> gb18030 (GBK superset,
        covers Windows-generated Simplified Chinese). Clear error if none fit.
        """
        import aiofiles

        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                async with aiofiles.open(file_path, "r", encoding=enc) as f:
                    return await f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(_MSG["encoding_unknown"])

    @staticmethod
    def _extract_pdf(file_path: str) -> str:
        """Extract the text layer of a PDF (digitally generated, not scanned).

        Reads `page.get_text()` and nothing else — no OCR, no layout model, no
        inference runtime. The production image ships **no** onnxruntime, and
        this path must never need one: the Markdown-conversion front-end this
        used to call pulled onnxruntime in at **package import** time, so a file
        that could not even be opened still paid for it — and once the image
        dropped that runtime, every PDF upload failed (缺陷 44 names the library
        and the chain; the import must not come back, see
        `test_l6_no_parser_runtime_in_source`).

        Output is plain text, **not Markdown**: no `#` headings are emitted.
        The frontend's chapter detector then falls through to its
        "line consisting of 1–4 digits" rule, and page numbers match it — see
        docs/evidence/pagenum-chapter-rules.json.

        Guards against oversized PDFs (page count) and never rasterises a page,
        so memory stays bounded. Does NOT OCR scanned PDFs.
        """
        import pymupdf

        MAX_PDF_PAGES = 2000

        try:
            doc = pymupdf.open(file_path)
        except Exception as e:
            # 库原文含服务器路径（实测 "Failed to open file '\\\\tmp\\\\...'"），只进日志（缺陷 39）。
            print(f"[TextManager] PDF open failed: {e!r}")
            raise ValueError(_MSG["pdf_open_failed"]) from e

        try:
            page_count = doc.page_count
            if page_count > MAX_PDF_PAGES:
                raise ValueError(
                    _MSG["pdf_page_limit"].format(page_count=page_count, max_pages=MAX_PDF_PAGES)
                )
            try:
                pdf_text = "".join(page.get_text() for page in doc)
            except Exception as e:
                print(f"[TextManager] PDF get_text failed: {e!r}")
                raise ValueError(_MSG["pdf_parse_failed"]) from e
        finally:
            doc.close()

        if not pdf_text or not pdf_text.strip():
            raise ValueError(_MSG["pdf_no_text"])
        return pdf_text

    @staticmethod
    def _extract_docx(file_path: str) -> str:
        """Extract text from DOCX, joining paragraphs."""
        from docx import Document

        try:
            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        except Exception as e:
            print(f"[TextManager] DOCX open/read failed: {e!r}")
            raise ValueError(_MSG["docx_parse_failed"]) from e
        # 「无有效文本」是本仓的判定，**必须留在这圈 try 之外** —— 放进去会被同一个
        # `except Exception` 抓走再包一层（缺陷 39 的 DOCX 双包：上屏成了
        # "DOCX 解析失败: DOCX 文件无有效文本内容"，一个 except 同时接住我们的语义
        # 和库的噪声）。
        if not paragraphs:
            raise ValueError(_MSG["docx_empty"])
        return "\n\n".join(paragraphs)

    # ---- Public API ----

    async def upload_text(self, filename: str, content: str, title: str = "", description: str = "", text_type: str = "story", user_id: str = "") -> dict[str, Any]:
        """Parse content by file extension, save to storage. Returns dict with text_id and char stats."""
        parsed = self._parse_content(filename, content).strip()
        if not parsed:
            raise ValueError(_MSG["empty_after_parse"])

        original_chars = len(parsed)
        max_chars = 2_000_000 if text_type == "chat" else 1_000_000
        if original_chars > max_chars:
            limit_text = "200 万" if text_type == "chat" else "100 万"
            raise ValueError(_MSG["too_long"].format(limit_text=limit_text))

        # Chat preprocessing: Layer 0 (format clean) + Layer 1 (quality filter)
        cleaned = parsed
        cleaned_chars = original_chars
        if text_type == "chat":
            preprocessor = ChatPreprocessor()
            cleaned = preprocessor.preprocess(cleaned)  # no target → only Layer 0+1
            cleaned_chars = len(cleaned)
            if not cleaned.strip():
                raise ValueError(_MSG["chat_clean_empty"])

        text_id = uuid.uuid4().hex[:12]

        try:
            occ = original_chars if text_type == "chat" else None
            await self._storage.save_text(text_id, filename, cleaned, title, description, text_type, occ, user_id)
        except Exception as exc:
            print(f"[TextManager] Save text failed: {exc}")
            raise
        return {"text_id": text_id, "original_chars": original_chars, "cleaned_chars": cleaned_chars}

    async def distill_all(self, text_id: str, user_id: str) -> list[dict[str, Any]]:
        """Identify every character in a stored text and distill each one.

        user_id 必需：读取原文走属主过滤，缺失即 TypeError 而不是静默读到他人文本。

        Skips characters that fail distillation rather than aborting the batch.
        """
        text_rec = await self._storage.get_text_owned(text_id, user_id)
        if not text_rec:
            raise ValueError(_MSG["text_not_found"])
        content = text_rec.get("content", "")

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
                    self._distiller.distill_incremental, content, name, char_info.get("aliases", [])
                )
            except Exception as exc:
                print(f"[TextManager] Distill '{name}' failed, skipping: {exc}")
                continue

            card_id = uuid.uuid4().hex[:12]
            try:
                await self._storage.save_card(
                    card_id, text_id, card.name, card.model_dump_json(), user_id
                )
            except Exception as exc:
                print(f"[TextManager] Save card '{name}' failed, skipping: {exc}")
                continue

            result = card.model_dump()
            result["card_id"] = card_id
            results.append(result)

        return results

    async def get_or_distill(
        self, text_id: str, character_name: str, user_id: str, force: bool = False,
        embedding_key: str = "", embedding_region: str = "",
    ) -> dict[str, Any]:
        """Return a card + fresh session. Reuses a cached card when available. Set force=True to re-distill.

        user_id 必需：读取原文走属主过滤，缺失即 TypeError 而不是静默读到他人文本。

        Pass embedding_key/embedding_region from the user's saved API config
        so RAGEngine can initialize DashScope embedding.
        """
        text_rec = await self._storage.get_text_owned(text_id, user_id)
        if not text_rec:
            raise ValueError(_MSG["text_not_found"])
        content = text_rec.get("content", "")

        existing_cards = await self._storage.list_cards(text_id, user_id)
        card: CharacterCard | None = None
        card_id: str | None = None
        if not force:
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
            # Resolve aliases for incremental distill
            aliases: list[str] = []
            try:
                chars = await asyncio.to_thread(self._distiller.identify_characters, content)
                for c in chars:
                    if c["name"] == character_name:
                        aliases = c.get("aliases", [])
                        break
            except Exception as exc:
                print(f"[TextManager] Identify aliases failed, using empty: {exc}")

            try:
                card = await asyncio.to_thread(
                    self._distiller.distill_incremental, content, character_name, aliases
                )
            except Exception as exc:
                print(f"[TextManager] Distill '{character_name}' failed: {exc}")
                raise

            verdict = await self._guard_card(card, user_id)
            card_id = uuid.uuid4().hex[:12]
            try:
                await self._storage.save_card(
                    card_id, text_id, card.name, card.model_dump_json(), user_id
                )
            except Exception as exc:
                print(f"[TextManager] Save card failed: {exc}")
                raise

            if verdict.error:
                await self._flag_review(
                    card_id, user_id,
                    f"[distill-validator] 校验失败，待人工复核（{verdict.error_msg[:200]}）",
                )
                print(f"[card-guard] judge error → flag pending review {card_id}")
            elif verdict.flagged:
                await self._flag_review(
                    card_id, user_id,
                    f"[distill-validator] 检测到注入性内容，已清除并待人工复核：{verdict.summary[:300]}",
                )
                print(f"[card-guard] flagged {len(verdict.flagged)} leaves (neutralized {verdict.neutralized}): {verdict.summary}")

        # Generate a variation of the first message to avoid repetition
        generated_opening = ""
        if card.first_message and self._llm:
            try:
                variation_prompt = (
                    f"你是「{card.name}」。以下是你的标准开场白：\n"
                    f"「{card.first_message}」\n\n"
                    f"请用同样的语气、口癖和风格，重新说一句意思相近但措辞不同的开场白。"
                    f"只输出开场白本身，不要解释。保持{card.name}的说话习惯。50字以内。"
                )
                opening = await asyncio.to_thread(
                    self._llm.chat,
                    f"你是{card.name}，保持角色风格。",
                    [{"role": "user", "content": variation_prompt}],
                )
                try_record_usage(self._storage, user_id, self._llm,
                                 "chat_opening_variation", source="TextManager")
                opening = opening.strip()
                if opening and len(opening) <= 200:
                    generated_opening = opening
                else:
                    print(f"[TextManager] Opening variation invalid, using original")
            except Exception as exc:
                print(f"[TextManager] Opening variation failed, using original: {exc}")

        try:
            all_characters = await self._build_all_characters(text_id, existing_cards, user_id)
            session_id = await asyncio.to_thread(
                self._create_session, content, card,
                all_characters=all_characters, rag=None,
                card_id=card_id, user_id=user_id,
            )
        except Exception as exc:
            print(f"[TextManager] Create session failed: {exc}")
            raise

        try:
            await self._storage.save_session(session_id, card_id, "", "", user_id)
        except Exception as exc:
            print(f"[TextManager] Persist session failed (non-fatal): {exc}")

        # Fire-and-forget scene index (non-blocking, degraded silently)
        if self._indexing_service:
            self._indexing_service.schedule_scene_index(
                text_id, card_id, content, card.name, all_characters,
                embedding_key=embedding_key, embedding_region=embedding_region,
            )

        result = card.model_dump()
        result["session_id"] = session_id
        result["card_id"] = card_id
        if generated_opening:
            result["first_message"] = generated_opening
        return result

    async def save_distilled_card(
        self, text_id: str, card: CharacterCard, user_id: str,
        embedding_key: str = "", embedding_region: str = "",
    ) -> dict[str, Any]:
        """Persist a freshly distilled card and create its chat session.

        user_id 必需：读取原文走属主过滤，缺失即 TypeError 而不是静默读到他人文本。
        """
        # Prompt-injection field guard: neutralize flagged leaves on the card
        # *before* persist, so no injection text ever lands in stored card_json.
        verdict = await self._guard_card(card, user_id)
        card_id = uuid.uuid4().hex[:12]
        result_card = await self._storage.save_card(card_id, text_id, card.name, card.model_dump_json(), user_id)
        # save_card does upsert by text_id+name — on re-distill it returns the existing ID
        actual_card_id = result_card.get("id") or card_id

        if verdict.error:
            await self._flag_review(
                actual_card_id, user_id,
                f"[distill-validator] 校验失败，待人工复核（{verdict.error_msg[:200]}）",
            )
            print(f"[card-guard] judge error → flag pending review {actual_card_id}")
        elif verdict.flagged:
            await self._flag_review(
                actual_card_id, user_id,
                f"[distill-validator] 检测到注入性内容，已清除并待人工复核：{verdict.summary[:300]}",
            )
            print(f"[card-guard] flagged {len(verdict.flagged)} leaves (neutralized {verdict.neutralized}): {verdict.summary}")

        text_rec = await self._storage.get_text_owned(text_id, user_id)
        content = text_rec.get("content", "")

        existing_cards = await self._storage.list_cards(text_id, user_id)
        all_chars = await self._build_all_characters(text_id, existing_cards, user_id)

        session_id = await asyncio.to_thread(
            self._create_session, content, card,
            all_characters=all_chars, rag=None,
            card_id=actual_card_id, user_id=user_id,
        )
        await self._storage.save_session(session_id, actual_card_id, "", "", user_id)

        result = card.model_dump()
        result["session_id"] = session_id
        result["card_id"] = actual_card_id

        # Fire-and-forget scene index (non-blocking, degraded silently)
        if self._indexing_service:
            self._indexing_service.schedule_scene_index(
                text_id, actual_card_id, content, card.name, all_chars,
                embedding_key=embedding_key, embedding_region=embedding_region,
            )
        return result

    async def switch_character(
        self, text_id: str, character_name: str, user_id: str,
    ) -> dict[str, Any]:
        """Switch to another character from the same text.

        Reuses the cached card if it exists, otherwise distills on the fly.
        Always creates a fresh session with a new RAG index.
        """
        return await self.get_or_distill(text_id, character_name, user_id=user_id)

    # ---- Internal helpers ----

    async def _build_all_characters(self, text_id: str, existing_cards: list[dict], user_id: str) -> list[dict[str, Any]]:
        """Build all_characters list with aliases merged from cached identify results.

        user_id 必需：别名缓存走属主过滤，缺失即 TypeError 而不是静默读到他人缓存
        （与 get_or_distill 同口径）。
        """
        all_characters = [{"name": c["name"], "aliases": []} for c in existing_cards]
        try:
            cached = await self._storage.get_characters_owned(
                text_id, user_id, version=Distiller.IDENTIFY_VERSION)
            if cached:
                name_to_aliases = {c["name"]: c.get("aliases", []) for c in cached}
                for char in all_characters:
                    if char["name"] in name_to_aliases:
                        char["aliases"] = name_to_aliases[char["name"]]
        except Exception as exc:
            print(f"[TextManager] Alias cache merge failed: {exc}")
        return all_characters

    def _create_session(
        self,
        text: str,
        card: CharacterCard,
        *,
        all_characters: list[dict[str, Any]] | None = None,
        rag: Any = None,
        card_id: str = "",
        user_id: str = "",
        user_role: str = "",
    ) -> str:
        """Build ChatEngine in memory; rag=None means no retrieval (pure card prompt). (sync)

        `*` 之后全 keyword-only：可选参数有 5 个且类型都是 str，按位置传来错位不会报错，
        只会静默把值装进邻近的参数。曾发生过一次 —— 调用点多传两个实参，用户配置里的
        嵌入凭据落进了 `user_role`，随 prompt 发给模型方并明文落进
        `sessions.affinity_state`（缺陷 G）。锁在这里，调用点增加也不会失效。

        原先本函数还有「嵌入凭据」与「所在区域」两个参数，函数体里从未引用过 ——
        它们正是那次错位的落脚点：没有这两格，调用点多出来的两个实参只会得到
        `TypeError`，不会有地方可落。**先问这个参数有没有人用，再问怎么传。**
        """
        engine = ChatEngine(
            self._llm, rag, card,
            all_characters=all_characters,
            memory_manager=self._memory_manager,
            card_id=card_id,
            user_role=user_role,
        )
        session_id = uuid.uuid4().hex[:12]
        self._sessions[session_id] = {"engine": engine, "card": card, "message_ids": [], "user_id": user_id}
        return session_id
