"""Text manager: format parsing, upload, and cached character distillation."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from adapters.llm_adapter import LLMAdapter
from core.character_roster import aliases_for, cached_characters, resolve_characters
from core.chat_engine import ChatEngine
from core.chat_preprocessor import ChatPreprocessor
from core.distiller import Distiller
from core.message_outbox import MessageOutbox
from core.moderation.card_guard import GuardVerdict, guard_card_obj
from core.schema import CharacterCard
from core.text_failure import TEXT_FAILURE_MESSAGES as _MSG
from core.utils import try_record_usage
from storage.base import StorageBase, new_review_id


def new_session_entry(engine: Any, card: Any, user_id: str) -> dict[str, Any]:
    """一对一会话条目的**唯一**定义 —— 建会话和测试夹具都从这里拿，别处不许手搓 dict。

    `lock` / `outbox` / `retract_state` 都是条目的一部分，不是「谁用到谁 `setdefault`」：
    队列跟着会话走（`history.py` 与 `_ensure_session` 里「把引擎挪到原 session_id 名下」
    那一步整份搬条目，队列一起搬）；锁每个会话一把，`setdefault` 版会在条目漏带时**静默
    补上**，看着没事，漏掉的却是「建会话那条路没带上」这件事本身。

    `message_ids` 曾经也在这里 —— 它**只写不读**（没有任何地方靠它做事），已删。

    `user_id` 与 `card` 没有默认值：漏登记属主的后果是**属主本人**被挡在门外（响亮，当场
    暴露），而不是所有登录用户都能进来（静默）；`card` 同理，缺了会当场露出来。
    """
    return {
        "engine": engine, "card": card, "user_id": user_id,
        "lock": asyncio.Lock(),
        "retract_state": {"last_retract_turn": -999, "retract_count": 0, "turn_index": 0},
        "outbox": MessageOutbox(),
    }


class TextManager:
    """Handles text upload with format parsing and cached character distillation.

    Owns the full lifecycle: parse file -> save text -> identify characters
    -> distill card -> create in-memory session -> persist to storage.
    """

    def __init__(
        self,
        get_storage: Callable[[], StorageBase],
        distiller: Distiller,
        llm: LLMAdapter,
        sessions: dict[str, dict[str, Any]],
        *,
        indexing_service=None,
        memory_manager,
    ) -> None:
        # 收的是「取库的方式」，不是库本身。构造时捕获实例会让本对象与 `deps._storage`
        # 那个单例分叉：之后任何一次替换（测试把存储换成 sqlite）只落到 `Depends(get_storage)`
        # 那条路，本对象建出的引擎仍攥着旧库，于是非 PG 的用例照样连真库（缺陷 117）。
        # 谁都不能替调用方决定生命周期，故用得到时现取。
        self._get_storage = get_storage
        self._guard_enabled = os.getenv("CARD_GUARD_ENABLED", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        self._distiller = distiller
        self._llm = llm
        self._sessions = sessions
        self._indexing_service = indexing_service
        self._memory_manager = memory_manager

    @property
    def _storage(self) -> StorageBase:
        """现取现用；绝不缓存（缓存就等于回到构造时捕获）。"""
        return self._get_storage()

    # ── Prompt-injection field guard (2.1/2.6) ─────────────
    # Runs on every freshly distilled card before persistence. Flagged leaves
    # are neutralized in place; a flag or a judge error is written to
    # review_log so the card is held out of the market until an admin clears it.
    # Judge LLM calls are pushed to a worker thread so the event loop isn't blocked.
    # OFF by default (CARD_GUARD_ENABLED=1 turns it on): measured (2026-09-08)
    # zero detection on narrativized residue + FP 0/23 means the per-distill LLM
    # call is pure cost today; re-enable once the judge targets executable-config
    # field landings (decision_style/speaking_style/values + precedence wording).

    async def _guard_card(self, card: CharacterCard) -> GuardVerdict:
        if not self._guard_enabled:
            return GuardVerdict()
        if self._llm is None:
            return GuardVerdict()
        try:
            return await asyncio.to_thread(
                guard_card_obj, card, self._llm, storage=self._storage,
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
            await self._storage.save_text(
                text_id, filename, cleaned,
                title=title, description=description, text_type=text_type,
                original_char_count=occ, user_id=user_id,
            )
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
            await self._storage.save_text(
                text_id, filename, cleaned,
                title=title, description=description, text_type=text_type,
                original_char_count=occ, user_id=user_id,
            )
        except Exception as exc:
            print(f"[TextManager] Save text failed: {exc}")
            raise
        return {"text_id": text_id, "original_chars": original_chars, "cleaned_chars": cleaned_chars}

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
            # Resolve aliases for incremental distill.
            # **不设就地捕获**：识别失败（DistillError 家族）必须冒泡 —— 原先的宽捕获
            # 把它降级成「没有别名」，用户看到的是蒸馏成功而别名缺失，故障无声。
            # 上屏口径由调用方的 `except DistillError: raise` 交给统一出口
            # （`web/server.py::_domain_error_status` → 400 + `user_message`）。
            chars = await resolve_characters(
                self._storage, self._distiller, text_id, user_id, content)
            aliases = aliases_for(chars, character_name)

            try:
                card = await asyncio.to_thread(
                    self._distiller.distill_incremental, content, character_name, aliases
                )
            except Exception as exc:
                print(f"[TextManager] Distill '{character_name}' failed: {exc}")
                raise

            verdict = await self._guard_card(card)
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
                try_record_usage(self._storage, self._llm,
                                 action="chat_opening_variation", source="TextManager")
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
                self._create_session, card,
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
                text_id, card_id, content, card.name,
                all_characters=all_characters,
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
        verdict = await self._guard_card(card)
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
            self._create_session, card,
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
                text_id, actual_card_id, content, card.name,
                all_characters=all_chars,
                embedding_key=embedding_key, embedding_region=embedding_region,
            )
        return result

    # ---- Internal helpers ----

    async def _build_all_characters(self, text_id: str, existing_cards: list[dict], user_id: str) -> list[dict[str, Any]]:
        """Build all_characters list with aliases merged from cached identify results.

        user_id 必需：别名缓存走属主过滤，缺失即 TypeError 而不是静默读到他人缓存
        （与 get_or_distill 同口径）。
        """
        all_characters = [{"name": c["name"], "aliases": []} for c in existing_cards]
        # **不设就地捕获**：别名缓存的读失败（存储 / 序列化）必须冒泡 —— 原先的宽捕获
        # 把它降级成「没有别名」，会话照常建起来，用户只看到别名缺失、故障无声。
        # 与 `get_or_distill` 取别名那段同口径（86 的 §H 第 1 项）。
        cached = await cached_characters(self._storage, text_id, user_id)
        if cached:
            for char in all_characters:
                char["aliases"] = aliases_for(cached, char["name"])
        return all_characters

    def _create_session(
        self,
        card: CharacterCard,
        *,
        all_characters: list[dict[str, Any]] | None = None,
        rag: Any = None,
        card_id: str = "",
        user_id: str = "",
        user_role: str = "",
    ) -> str:
        """Build ChatEngine in memory; rag=None means no retrieval (pure card prompt). (sync)

        `text` 曾是本函数的第一个位置参数，函数体里从未引用 —— 正文经由 `card` 与
        调用方的 RAG 进入引擎。**死参数为错位留出空间**：留着它，调用点按位置多传一个
        实参就有地方可落，静默装错值；删掉后同样的调用点当场 `TypeError`。

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
            storage=self._storage,
        )
        session_id = uuid.uuid4().hex[:12]
        self._sessions[session_id] = new_session_entry(engine, card, user_id)
        return session_id
