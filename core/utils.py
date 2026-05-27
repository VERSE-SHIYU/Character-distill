"""Shared utility functions for the core package."""

from __future__ import annotations

import asyncio
import threading
from typing import Any


def try_record_usage(
    storage: Any,
    user_id: str,
    llm: Any,
    action: str = "chat",
    usage: dict | None = None,
    source: str = "core",
) -> None:
    """Record LLM token usage to storage in a background thread.

    Args:
        storage: Storage backend with ``record_usage`` coroutine.
        user_id: The user whose usage to record.
        llm: LLM adapter instance (expects ``last_usage`` dict and ``_model``).
        action: Label for the usage record (e.g. ``"chat"``, ``"distill"``).
        usage: Optional usage dict; falls back to ``llm.last_usage``.
        source: Source name for error messages (e.g. ``"ChatEngine"``, ``"Distiller"``).
    """
    if not storage or not user_id:
        return
    if usage is None:
        usage = llm.last_usage
    if not usage:
        return
    model = getattr(llm, "_model", "") or ""
    pt = usage["prompt_tokens"]
    ct = usage["completion_tokens"]

    def _do() -> None:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                storage.record_usage(user_id, action, pt, ct, model)
            )
            loop.close()
        except Exception as exc:
            print(f"[{source}] Record usage failed (non-fatal): {exc}")

    threading.Thread(target=_do, daemon=True).start()
