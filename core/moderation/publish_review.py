"""Market publish review: signals → one verdict.

Policy (2026-10-01): publish first, the admin reviews afterwards in the back
office (review log + takedown). Nothing on the publish path waits in a manual
queue.

- **reject** — definite violations only: a keyword *block*, an LLM injection
  *verdict*, an LLM content *verdict*.
- **pass**   — everything else. When a check could not clear the card (keyword
  *flag*, injection review call failed) it still passes, but ``log_reason``
  says why, so the admin can find every card that was not actually cleared.

Kept free of HTTP and storage: the router turns a verdict into a status code
and a review_log row. ``decide`` is pure; ``review_for_publish`` only gathers
the signals and calls it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from adapters.llm_adapter import LLMAdapter
from core.moderation.auto_review import _flatten_card, auto_review_split
from core.moderation.decision_engine import DecisionEngine
from core.moderation.keyword_filter import KeywordFilter
from core.moderation.preprocessor import TextPreprocessor

logger = logging.getLogger(__name__)

# review_log reason prefixes — one per layer, so each layer's hits can be
# tallied independently (never merged into one counter).
KEYWORD_LAYER = "[keyword-pregate]"
INJECTION_LAYER = "[publish-injection]"

LOG_REASON_MAX = 300  # review_log.reason budget


class Outcome(StrEnum):
    PASS = "pass"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class KeywordSignal:
    decision: str           # DecisionEngine: allow / flag / block
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublishVerdict:
    outcome: Outcome
    log_reason: str = ""    # admin-facing, stored in review_log
    user_message: str = ""  # shown to the publisher on reject

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.PASS


def keyword_signal(card_json: dict[str, Any]) -> KeywordSignal:
    """Keyword pre-screen (2.2). Content-safety blocklist only — an injection
    with no flagged words passes here by design; the LLM layer is the injection
    defense."""
    text = TextPreprocessor().process(_flatten_card(card_json))
    score, tags = KeywordFilter().match(text)
    return KeywordSignal(DecisionEngine().decide(score, None, None).decision, tuple(tags))


def decide(keyword: KeywordSignal, review: dict[str, Any] | None) -> PublishVerdict:
    """Apply the publish policy. ``review`` is ``auto_review_split``'s result,
    or None when the keyword layer already blocked (no LLM spend)."""
    kw = ",".join(keyword.tags)
    if keyword.decision == "block":
        return _reject(f"{KEYWORD_LAYER} {kw}", f"内容含违禁关键词：{kw}")
    if review is None:
        raise ValueError("review is required unless the keyword layer blocked")

    notes: list[str] = []
    if keyword.decision == "flag":
        notes.append(f"{KEYWORD_LAYER} 疑似，已放行待后台复核：{kw}")

    injection, content = review["injection"], review["content"]
    if injection.get("error"):
        notes.append(f"{INJECTION_LAYER} 审核失败已放行：{injection.get('reason', '')}")
    elif not injection.get("pass"):
        reason = injection.get("reason", "")
        return _reject(f"{INJECTION_LAYER} {reason}",
                       f"内容审核未通过：检测到指令性/越权内容（{reason}）")
    if not content.get("pass"):
        reason = str(content.get("reason", ""))
        return _reject(reason, f"发布失败：内容审核未通过 — {reason}")
    return PublishVerdict(Outcome.PASS, _clip(" | ".join(notes)))


async def review_for_publish(card_json: dict[str, Any], llm: LLMAdapter | None,
                             storage: Any = None) -> PublishVerdict:
    """Gather the signals for one card and decide. ``llm`` None (no key
    configured) surfaces as an injection review error → pass with a note."""
    keyword = keyword_signal(card_json)
    review = None
    if keyword.decision != "block":
        review = await auto_review_split(card_json, llm, storage=storage)
    verdict = decide(keyword, review)
    if verdict.allowed and verdict.log_reason:
        logger.warning("[market-pregate] published without full clearance: %s", verdict.log_reason)
    return verdict


def _reject(log_reason: str, user_message: str) -> PublishVerdict:
    return PublishVerdict(Outcome.REJECT, _clip(log_reason), user_message)


def _clip(reason: str) -> str:
    return reason[:LOG_REASON_MAX]
