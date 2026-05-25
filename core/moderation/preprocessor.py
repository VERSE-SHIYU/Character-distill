"""Text preprocessing utilities for moderation pipeline."""

from __future__ import annotations

import re
import unicodedata

from core.moderation.config import LEETSPEAK_MAP, MAX_TOKEN_LENGTH


class TextPreprocessor:
    """Preprocess user comments before moderation checks."""

    def strip_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        return re.sub(r"<[^>]+>", "", text)

    def normalize_unicode(self, text: str) -> str:
        """NFD normalize, strip combining marks and zero-width chars."""
        normalized = unicodedata.normalize("NFD", text)
        stripped = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
        return re.sub(r"[​-‍﻿]", "", stripped)

    def expand_leetspeak(self, text: str) -> str:
        """Expand common leetspeak substitutions using config mapping."""
        return "".join(LEETSPEAK_MAP.get(ch, ch) for ch in text)

    def truncate(self, text: str, max_tokens: int = MAX_TOKEN_LENGTH) -> str:
        """Truncate text by whitespace tokens."""
        tokens = text.split()
        if len(tokens) <= max_tokens:
            return text
        return " ".join(tokens[:max_tokens])

    def process(self, text: str) -> str:
        """Run full preprocessing flow and return cleaned text."""
        cleaned = self.strip_html(text)
        cleaned = self.normalize_unicode(cleaned)
        cleaned = self.expand_leetspeak(cleaned)
        cleaned = self.truncate(cleaned)
        return cleaned
