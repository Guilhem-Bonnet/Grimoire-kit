"""Friction budget tracker for the SOG orchestrator.

Backs the Friction Budget protocol declared in grimoire-master.md.
Tracks clarification questions asked to the user and computes a
friction score.  The SOG uses this to batch questions (QEC protocol)
and avoid exceeding the per-session friction budget.

Usage::

    from grimoire.core.friction_tracker import FrictionTracker

    tracker = FrictionTracker(project_root=Path("."), budget=5)
    tracker.record_question("What testing framework do you prefer?")
    print(tracker.budget_remaining)  # 4
    print(tracker.should_batch)       # False (still under threshold)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["FrictionEvent", "FrictionSnapshot", "FrictionTracker"]

FRICTION_TRACKER_VERSION = "1.0.0"

_SESSION_DIR = "_grimoire/_memory/telemetry"
_FRICTION_FILE = "friction-events.jsonl"
_DEFAULT_BUDGET = 5
_BATCH_THRESHOLD = 2  # After this many individual questions, start batching
_MAX_ENTRIES = 200


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FrictionEvent:
    """A single friction event (question asked to user)."""

    question: str
    category: str  # clarification, preference, confirmation, escalation
    timestamp: str
    batched: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "category": self.category,
            "timestamp": self.timestamp,
            "batched": self.batched,
        }


@dataclass(frozen=True, slots=True)
class FrictionSnapshot:
    """Point-in-time friction budget status."""

    budget: int
    spent: int
    remaining: int
    should_batch: bool
    events: tuple[FrictionEvent, ...]
    friction_score: float  # 0.0–1.0, lower is better

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "spent": self.spent,
            "remaining": self.remaining,
            "should_batch": self.should_batch,
            "friction_score": round(self.friction_score, 3),
            "event_count": len(self.events),
        }


# ── Core implementation ──────────────────────────────────────────────────────


class FrictionTracker:
    """Track and manage the friction budget for user interactions.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    budget :
        Maximum number of individual questions allowed per session.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        budget: int = _DEFAULT_BUDGET,
    ) -> None:
        self._root = project_root.resolve()
        self._dir = self._root / _SESSION_DIR
        self._file = self._dir / _FRICTION_FILE
        self._budget = budget
        self._events: list[FrictionEvent] = []

    @property
    def budget_remaining(self) -> int:
        """Questions remaining before budget exhaustion."""
        return max(0, self._budget - len(self._events))

    @property
    def should_batch(self) -> bool:
        """Whether the SOG should batch upcoming questions."""
        return len(self._events) >= _BATCH_THRESHOLD

    @property
    def budget_exhausted(self) -> bool:
        """Whether the friction budget is fully spent."""
        return len(self._events) >= self._budget

    def record_question(
        self,
        question: str,
        *,
        category: str = "clarification",
        batched: bool = False,
    ) -> FrictionEvent:
        """Record a question asked to the user."""
        event = FrictionEvent(
            question=question,
            category=category,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            batched=batched,
        )
        self._events.append(event)
        self._persist(event)
        return event

    def record_batch(self, questions: list[str], *, category: str = "clarification") -> list[FrictionEvent]:
        """Record a batch of questions (counts as 1 friction point)."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        events: list[FrictionEvent] = []
        for q in questions:
            event = FrictionEvent(
                question=q,
                category=category,
                timestamp=ts,
                batched=True,
            )
            events.append(event)
            self._persist(event)
        # A batch counts as ONE friction point
        if events:
            self._events.append(events[0])
        return events

    def snapshot(self) -> FrictionSnapshot:
        """Compute current friction status."""
        spent = len(self._events)
        remaining = max(0, self._budget - spent)
        friction_score = spent / self._budget if self._budget > 0 else 1.0

        return FrictionSnapshot(
            budget=self._budget,
            spent=spent,
            remaining=remaining,
            should_batch=self.should_batch,
            events=tuple(self._events),
            friction_score=min(1.0, friction_score),
        )

    def reset(self) -> None:
        """Reset for a new session."""
        self._events.clear()

    # ── Internal ──────────────────────────────────────────────────────────

    def _persist(self, event: FrictionEvent) -> None:
        """Append event to JSONL file."""
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            logger.debug("Failed to persist friction event")
