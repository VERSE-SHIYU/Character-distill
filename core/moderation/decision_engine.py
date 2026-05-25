"""Decision logic for combining classifier scores."""

from __future__ import annotations

from dataclasses import dataclass

from core.moderation.config import BLOCK_THRESHOLD, TRUST_THRESHOLD


@dataclass(slots=True)
class Decision:
    """Moderation decision result."""

    decision: str
    confidence: float
    tier: str
    queue_priority: str | None


class DecisionEngine:
    """Combine keyword/AI/LLM scores with conservative strategy."""

    def decide(
        self, keyword_score: float, ai_score: float | None, llm_score: float | None
    ) -> Decision:
        """Return final moderation decision by maximum available score."""
        scores = [keyword_score, ai_score, llm_score]
        confidence = max(score for score in scores if score is not None)

        if confidence < TRUST_THRESHOLD:
            return Decision(
                decision="allow",
                confidence=confidence,
                tier="trust",
                queue_priority=None,
            )

        if confidence < BLOCK_THRESHOLD:
            return Decision(
                decision="flag",
                confidence=confidence,
                tier="uncertain",
                queue_priority="normal",
            )

        return Decision(
            decision="block",
            confidence=confidence,
            tier="block",
            queue_priority="medium",
        )
