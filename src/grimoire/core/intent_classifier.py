"""Lightweight intent classifier for the SOG orchestrator.

Provides keyword/pattern-based intent detection with confidence scoring.
Backs the SOG's intelligent routing by producing ranked intent matches
with confidence levels, enabling fallback cascading when the primary
match is uncertain.

Usage::

    from grimoire.core.intent_classifier import IntentClassifier

    clf = IntentClassifier()
    result = clf.classify("refactor the login module to use dependency injection")
    print(result.intent, result.confidence)  # "dev", 0.85
    print(result.fallbacks)  # [("architect", 0.45)]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["IntentClassifier", "IntentMatch"]

INTENT_CLASSIFIER_VERSION = "1.0.0"


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IntentMatch:
    """Result of intent classification."""

    intent: str  # agent name (dev, architect, pm, qa, etc.)
    confidence: float  # 0.0–1.0
    keywords_matched: tuple[str, ...]
    fallbacks: tuple[tuple[str, float], ...]  # (agent, confidence) pairs

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": round(self.confidence, 3),
            "keywords_matched": list(self.keywords_matched),
            "fallbacks": [
                {"agent": a, "confidence": round(c, 3)}
                for a, c in self.fallbacks
            ],
        }


# ── Keyword domains ──────────────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[str, tuple[tuple[str, float], ...]] = {
    "dev": (
        ("implement", 0.9), ("code", 0.8), ("fix bug", 0.9), ("refactor", 0.85),
        ("function", 0.6), ("class", 0.5), ("module", 0.6), ("import", 0.5),
        ("variable", 0.5), ("debug", 0.8), ("compile", 0.7), ("build", 0.6),
        ("tdd", 0.9), ("unit test", 0.7), ("write code", 0.9), ("coding", 0.8),
        ("développer", 0.9), ("implémenter", 0.9), ("corriger", 0.8),
        ("coder", 0.9), ("bugfix", 0.9),
    ),
    "architect": (
        ("architecture", 0.9), ("system design", 0.9), ("adr", 0.9),
        ("infrastructure", 0.85), ("scalability", 0.8), ("coupling", 0.8),
        ("dependency", 0.6), ("api design", 0.85), ("data model", 0.8),
        ("microservice", 0.8), ("monolith", 0.7), ("pattern", 0.5),
        ("tech debt", 0.75), ("dette technique", 0.75),
    ),
    "pm": (
        ("prd", 0.9), ("product", 0.7), ("roadmap", 0.9), ("priorit", 0.8),
        ("backlog", 0.6), ("requirement", 0.8), ("user story", 0.7),
        ("stakeholder", 0.8), ("brief", 0.7), ("scope", 0.6),
        ("produit", 0.7), ("prioriser", 0.8),
    ),
    "qa": (
        ("test plan", 0.9), ("quality", 0.7), ("regression", 0.85),
        ("coverage", 0.7), ("acceptance", 0.8), ("test strat", 0.9),
        ("qa", 0.9), ("bug report", 0.7), ("validate", 0.6),
        ("vérifier", 0.6), ("tester", 0.7),
    ),
    "sm": (
        ("sprint", 0.9), ("scrum", 0.9), ("retrospective", 0.9),
        ("velocity", 0.85), ("standup", 0.85), ("story point", 0.9),
        ("kanban", 0.8), ("burndown", 0.85), ("backlog groom", 0.9),
    ),
    "tech-writer": (
        ("document", 0.7), ("readme", 0.85), ("changelog", 0.7),
        ("documentation", 0.85), ("rédiger", 0.8), ("écrire doc", 0.9),
        ("writing", 0.5), ("guide", 0.5), ("tutorial", 0.6),
    ),
    "ux-designer": (
        ("wireframe", 0.9), ("user flow", 0.9), ("persona", 0.9),
        ("ux", 0.9), ("ui", 0.8), ("design system", 0.85),
        ("accessibility", 0.7), ("prototype", 0.7), ("mockup", 0.9),
    ),
    "tea": (
        ("test architect", 0.95), ("fixture", 0.7), ("atdd", 0.9),
        ("ci gate", 0.8), ("test framework", 0.85), ("test infra", 0.85),
        ("risk-based testing", 0.9), ("quality gate", 0.8),
    ),
    "analyst": (
        ("business analysis", 0.9), ("market research", 0.85),
        ("domain", 0.5), ("stakeholder analysis", 0.85),
        ("competitive analysis", 0.85), ("business rules", 0.85),
        ("analyse métier", 0.9), ("recherche marché", 0.85),
    ),
}

# Minimum confidence to be considered a match
_MIN_CONFIDENCE = 0.3
_FALLBACK_THRESHOLD = 0.25


# ── Core implementation ──────────────────────────────────────────────────────


class IntentClassifier:
    """Keyword-based intent classifier with confidence scoring.

    Parameters
    ----------
    custom_keywords :
        Optional extra keyword mappings ``{agent: [(keyword, weight), ...]}``.
    """

    def __init__(
        self,
        *,
        custom_keywords: dict[str, tuple[tuple[str, float], ...]] | None = None,
    ) -> None:
        self._keywords = dict(_INTENT_KEYWORDS)
        if custom_keywords:
            for agent, kws in custom_keywords.items():
                existing = self._keywords.get(agent, ())
                self._keywords[agent] = existing + kws

    def classify(self, text: str) -> IntentMatch:
        """Classify user text into an intent with confidence.

        Returns the best matching intent and any fallback candidates
        above the fallback threshold.
        """
        text_lower = text.lower()
        scores: dict[str, tuple[float, list[str]]] = {}

        for agent, patterns in self._keywords.items():
            matched_keywords: list[str] = []
            weighted_sum = 0.0
            total_weight = sum(w for _, w in patterns)

            for keyword, weight in patterns:
                if re.search(r"\b" + re.escape(keyword) + r"\b", text_lower):
                    weighted_sum += weight
                    matched_keywords.append(keyword)

            if matched_keywords and total_weight > 0:
                confidence = min(1.0, weighted_sum / (total_weight * 0.3))
                scores[agent] = (confidence, matched_keywords)

        if not scores:
            return IntentMatch(
                intent="dev",
                confidence=0.0,
                keywords_matched=(),
                fallbacks=(),
            )

        # Sort by confidence descending
        ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
        best_agent, (best_conf, best_kws) = ranked[0]

        # Collect fallbacks
        fallbacks: list[tuple[str, float]] = []
        for agent, (conf, _kws) in ranked[1:]:
            if conf >= _FALLBACK_THRESHOLD:
                fallbacks.append((agent, conf))

        return IntentMatch(
            intent=best_agent,
            confidence=best_conf,
            keywords_matched=tuple(best_kws),
            fallbacks=tuple(fallbacks[:3]),
        )

    def classify_multi(self, text: str, *, top_k: int = 3) -> list[IntentMatch]:
        """Return top-k intent matches for ambiguous inputs."""
        text_lower = text.lower()
        results: list[tuple[str, float, list[str]]] = []

        for agent, patterns in self._keywords.items():
            matched_keywords: list[str] = []
            weighted_sum = 0.0
            total_weight = sum(w for _, w in patterns)

            for keyword, weight in patterns:
                if re.search(r"\b" + re.escape(keyword) + r"\b", text_lower):
                    weighted_sum += weight
                    matched_keywords.append(keyword)

            if matched_keywords and total_weight > 0:
                confidence = min(1.0, weighted_sum / (total_weight * 0.3))
                results.append((agent, confidence, matched_keywords))

        results.sort(key=lambda x: x[1], reverse=True)
        return [
            IntentMatch(
                intent=agent,
                confidence=conf,
                keywords_matched=tuple(kws),
                fallbacks=(),
            )
            for agent, conf, kws in results[:top_k]
        ]

    @property
    def known_intents(self) -> list[str]:
        """List all known intent agents."""
        return sorted(self._keywords.keys())
