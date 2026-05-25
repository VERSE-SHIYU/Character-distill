"""Keyword-based moderation classifier with runtime blocklist reload."""

from __future__ import annotations

import json
import os
import re
import time

from core.moderation.config import CRISIS_KEYWORDS, MILD_KEYWORDS, MODERATE_KEYWORDS, SEVERE_KEYWORDS


class KeywordFilter:
    """Detect risk keywords and assign confidence tags.

    Keyword lists are reloaded every RELOAD_INTERVAL seconds from the JSON
    file at BLOCKLIST_PATH. Falls back to config constants if not configured.
    """

    RELOAD_INTERVAL = 300  # 5 minutes

    def __init__(self) -> None:
        """Load keyword lists from configuration or external JSON file."""
        self._last_load = 0.0
        self._load_lists()

    def _load_lists(self) -> None:
        """Load from env-configured JSON file, fallback to config constants."""
        blocklist_path = os.getenv("BLOCKLIST_PATH", "")
        if blocklist_path and os.path.isfile(blocklist_path):
            with open(blocklist_path, encoding="utf-8") as f:
                data = json.load(f)
            self.crisis = data.get("crisis", CRISIS_KEYWORDS)
            self.severe = data.get("severe", SEVERE_KEYWORDS)
            self.moderate = data.get("moderate", MODERATE_KEYWORDS)
            self.mild = data.get("mild", MILD_KEYWORDS)
        else:
            self.crisis = CRISIS_KEYWORDS
            self.severe = SEVERE_KEYWORDS
            self.moderate = MODERATE_KEYWORDS
            self.mild = MILD_KEYWORDS
        self._last_load = time.monotonic()

    def _maybe_reload(self) -> None:
        """Reload keyword lists if the reload interval has elapsed."""
        if time.monotonic() - self._last_load > self.RELOAD_INTERVAL:
            self._load_lists()

    def match(self, text: str) -> tuple[float, list[str]]:
        """Return first-hit (confidence, reason_tags). Crisis keywords take priority over severe."""
        self._maybe_reload()

        for keyword in self.crisis:
            pattern = re.escape(keyword)
            if re.search(pattern, text, re.IGNORECASE):
                return 1.0, ["crisis:self_harm"]

        for keyword in self.severe:
            pattern = re.escape(keyword)
            if re.search(pattern, text, re.IGNORECASE):
                return 1.0, [f"severe:{keyword}"]

        for keyword in self.moderate:
            pattern = re.escape(keyword)
            if re.search(pattern, text, re.IGNORECASE):
                return 0.6, [f"moderate:{keyword}"]

        for keyword in self.mild:
            pattern = re.escape(keyword)
            if re.search(pattern, text, re.IGNORECASE):
                return 0.3, [f"mild:{keyword}"]

        return 0.0, []
