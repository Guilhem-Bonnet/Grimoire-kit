"""Operational learnings system — cross-session knowledge accumulation.

Inspired by gstack's ``/learn`` skill: every session can log operational
discoveries (CLI quirks, build flags, env vars, timing issues) that are
auto-injected into future sessions.

Learnings are stored as JSONL per-project.  At session start, the top
entries are surfaced to avoid repeating past mistakes.

Usage::

    from grimoire.tools.learnings import Learnings

    learn = Learnings(project_root=Path("."))
    learn.log("pytest-xdist", "Never use -n auto in this project — tests share state")
    results = learn.search("pytest")
    top = learn.top(limit=5)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.tools._common import GrimoireTool

logger = logging.getLogger(__name__)

__all__ = ["LearningEntry", "Learnings"]

LEARNINGS_VERSION = "1.0.0"

# ── Data structures ──────────────────────────────────────────────────────────

_MAX_ENTRIES = 200
_LEARNINGS_DIR = "_grimoire/_memory/learnings"
_LEARNINGS_FILE = "operational.jsonl"


@dataclass(frozen=True, slots=True)
class LearningEntry:
    """A single operational learning."""

    key: str
    insight: str
    confidence: int = 80  # 0-100
    source: str = "observed"  # observed, documented, inferred
    skill: str = ""
    tags: tuple[str, ...] = ()
    timestamp: str = ""
    hit_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "insight": self.insight,
            "confidence": self.confidence,
            "source": self.source,
            "skill": self.skill,
            "tags": list(self.tags),
            "timestamp": self.timestamp,
            "hit_count": self.hit_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearningEntry:
        return cls(
            key=str(data.get("key", "")),
            insight=str(data.get("insight", "")),
            confidence=int(data.get("confidence", 80)),
            source=str(data.get("source", "observed")),
            skill=str(data.get("skill", "")),
            tags=tuple(data.get("tags") or []),
            timestamp=str(data.get("timestamp", "")),
            hit_count=int(data.get("hit_count", 0)),
        )


# ── Core implementation ──────────────────────────────────────────────────────


class Learnings(GrimoireTool):
    """Operational learnings accumulator.

    Stores JSONL-based learnings per project.  Supports log, search,
    top, and prune operations.
    """

    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root)
        self._dir = self._project_root / _LEARNINGS_DIR
        self._file = self._dir / _LEARNINGS_FILE

    def run(self, **kwargs: Any) -> Any:
        """Dispatch to sub-commands."""
        action = kwargs.get("action", "top")
        if action == "log":
            return self.log(
                key=kwargs["key"],
                insight=kwargs["insight"],
                confidence=kwargs.get("confidence", 80),
                source=kwargs.get("source", "observed"),
                skill=kwargs.get("skill", ""),
                tags=tuple(kwargs.get("tags") or []),
            )
        if action == "search":
            return self.search(kwargs["query"], limit=kwargs.get("limit", 5))
        if action == "top":
            return self.top(limit=kwargs.get("limit", 5))
        if action == "prune":
            return self.prune()
        if action == "count":
            return self.count()
        msg = f"Unknown action: {action}"
        raise ValueError(msg)

    def log(
        self,
        key: str,
        insight: str,
        *,
        confidence: int = 80,
        source: str = "observed",
        skill: str = "",
        tags: tuple[str, ...] = (),
    ) -> LearningEntry:
        """Log a new operational learning.

        A good test: would knowing this save 5+ minutes in a future session?
        If yes, log it.
        """
        entry = LearningEntry(
            key=key,
            insight=insight,
            confidence=min(100, max(0, confidence)),
            source=source,
            skill=skill,
            tags=tags,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

        # Check for duplicate key — update instead of append
        entries = self._load_all()
        updated = False
        for i, existing in enumerate(entries):
            if existing.key == key:
                entries[i] = entry
                updated = True
                break

        if not updated:
            entries.append(entry)

        self._save_all(entries)
        logger.info("Learning logged: [%s] %s", key, insight)
        return entry

    def search(self, query: str, *, limit: int = 5) -> list[LearningEntry]:
        """Search learnings by keyword in key + insight."""
        query_lower = query.lower()
        entries = self._load_all()
        scored: list[tuple[int, LearningEntry]] = []

        for entry in entries:
            score = 0
            searchable = f"{entry.key} {entry.insight} {' '.join(entry.tags)}".lower()
            if query_lower in searchable:
                score += 10
            if query_lower in entry.key.lower():
                score += 5
            if score > 0:
                # Boost by confidence and hit count
                score += entry.confidence // 20
                score += min(entry.hit_count, 5)
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def top(self, *, limit: int = 5) -> list[LearningEntry]:
        """Return highest-confidence learnings for session injection."""
        entries = self._load_all()
        # Sort by confidence desc, then by recency
        entries.sort(key=lambda e: (e.confidence, e.timestamp), reverse=True)
        return entries[:limit]

    def count(self) -> int:
        """Return total number of learnings."""
        return len(self._load_all())

    def prune(self, *, max_entries: int = _MAX_ENTRIES) -> int:
        """Remove oldest low-confidence entries if over limit."""
        entries = self._load_all()
        if len(entries) <= max_entries:
            return 0
        # Sort: keep high confidence + high hit_count
        entries.sort(key=lambda e: (e.confidence, e.hit_count, e.timestamp))
        to_remove = len(entries) - max_entries
        entries = entries[to_remove:]
        self._save_all(entries)
        return to_remove

    def inject_context(self, *, limit: int = 3) -> str:
        """Format top learnings for LLM context injection.

        Returns a compact Markdown block suitable for prepending to
        agent system prompts.
        """
        top_entries = self.top(limit=limit)
        if not top_entries:
            return ""
        lines = ["## Operational Learnings (auto-injected)", ""]
        for entry in top_entries:
            lines.append(f"- **{entry.key}**: {entry.insight} (confidence: {entry.confidence}%)")
        return "\n".join(lines)

    # ── Internal ─────────────────────────────────────────────────────────

    def _load_all(self) -> list[LearningEntry]:
        """Load all learnings from JSONL."""
        if not self._file.exists():
            return []
        entries: list[LearningEntry] = []
        try:
            for line in self._file.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    entries.append(LearningEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load learnings: %s", exc)
        return entries

    def _save_all(self, entries: list[LearningEntry]) -> None:
        """Persist all learnings to JSONL (atomic write)."""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(
                    json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
                    for entry in entries
                )
            tmp.replace(self._file)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
