"""Session telemetry — JSONL-based skill and tool usage analytics.

Inspired by gstack's skill-usage.jsonl analytics pipeline.  Records
every skill invocation, tool execution, and session event for
retrospective analysis and preamble injection.

Usage::

    from grimoire.core.telemetry import Telemetry

    telem = Telemetry(project_root=Path("."))
    telem.record_skill("grimoire-tdd", outcome="success", duration_s=12.5)
    telem.record_tool("ruff", outcome="failure", message="3 lint errors")
    recent = telem.recent(skill="grimoire-tdd", limit=5)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["Telemetry", "TelemetryEntry"]

TELEMETRY_VERSION = "1.0.0"

_TELEMETRY_DIR = "_grimoire/_memory/telemetry"
_SKILL_USAGE_FILE = "skill-usage.jsonl"
_MAX_ENTRIES = 1000


@dataclass(frozen=True, slots=True)
class TelemetryEntry:
    """A single telemetry event."""

    event_type: str  # skill, tool, session
    skill: str = ""
    tool: str = ""
    outcome: str = ""  # success, failure, skipped, timeout
    duration_s: float = 0.0
    message: str = ""
    metadata: dict[str, Any] | None = None
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "outcome": self.outcome,
        }
        if self.skill:
            d["skill"] = self.skill
        if self.tool:
            d["tool"] = self.tool
        if self.duration_s > 0:
            d["duration_s"] = round(self.duration_s, 3)
        if self.message:
            d["message"] = self.message
        if self.metadata:
            d["metadata"] = self.metadata
        return d


class Telemetry:
    """JSONL-based skill and tool usage tracker.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()
        self._dir = self._root / _TELEMETRY_DIR
        self._file = self._dir / _SKILL_USAGE_FILE

    def record_skill(
        self,
        skill: str,
        *,
        outcome: str = "success",
        duration_s: float = 0.0,
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryEntry:
        """Record a skill invocation."""
        entry = TelemetryEntry(
            event_type="skill",
            skill=skill,
            outcome=outcome,
            duration_s=duration_s,
            message=message,
            metadata=metadata,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._append(entry)
        logger.debug("Telemetry: skill=%s outcome=%s", skill, outcome)
        return entry

    def record_tool(
        self,
        tool: str,
        *,
        outcome: str = "success",
        duration_s: float = 0.0,
        message: str = "",
        skill: str = "",
    ) -> TelemetryEntry:
        """Record a tool execution."""
        entry = TelemetryEntry(
            event_type="tool",
            tool=tool,
            skill=skill,
            outcome=outcome,
            duration_s=duration_s,
            message=message,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._append(entry)
        return entry

    def record_session(
        self,
        *,
        outcome: str = "completed",
        duration_s: float = 0.0,
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryEntry:
        """Record a session-level event."""
        entry = TelemetryEntry(
            event_type="session",
            outcome=outcome,
            duration_s=duration_s,
            message=message,
            metadata=metadata,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._append(entry)
        return entry

    def recent(
        self,
        *,
        skill: str = "",
        event_type: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return recent telemetry entries, optionally filtered."""
        entries = self._load_all()
        if skill:
            entries = [e for e in entries if e.get("skill") == skill]
        if event_type:
            entries = [e for e in entries if e.get("event_type") == event_type]
        return entries[-limit:]

    def skill_stats(self) -> dict[str, dict[str, Any]]:
        """Aggregate stats per skill: count, success_rate, avg_duration."""
        entries = self._load_all()
        skills: dict[str, list[dict[str, Any]]] = {}
        for e in entries:
            if e.get("event_type") == "skill" and e.get("skill"):
                skills.setdefault(e["skill"], []).append(e)

        stats: dict[str, dict[str, Any]] = {}
        for skill_name, records in skills.items():
            total = len(records)
            successes = sum(1 for r in records if r.get("outcome") == "success")
            durations = [r.get("duration_s", 0) for r in records if r.get("duration_s")]
            stats[skill_name] = {
                "count": total,
                "success_rate": round(successes / total * 100, 1) if total else 0,
                "avg_duration_s": round(sum(durations) / len(durations), 2) if durations else 0,
                "last_used": records[-1].get("timestamp", ""),
            }
        return stats

    def count(self) -> int:
        """Total telemetry entries."""
        return len(self._load_all())

    def prune(self, *, max_entries: int = _MAX_ENTRIES) -> int:
        """Remove oldest entries if over limit."""
        entries = self._load_all()
        if len(entries) <= max_entries:
            return 0
        to_remove = len(entries) - max_entries
        entries = entries[to_remove:]
        self._save_all(entries)
        return to_remove

    # ── Internal ──────────────────────────────────────────────────────────

    def _append(self, entry: TelemetryEntry) -> None:
        """Append a single entry to the JSONL file."""
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Failed to write telemetry: %s", exc)

    def _load_all(self) -> list[dict[str, Any]]:
        """Load all entries from JSONL."""
        if not self._file.exists():
            return []
        try:
            entries: list[dict[str, Any]] = []
            for line in self._file.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    entries.append(json.loads(line))
            return entries
        except (json.JSONDecodeError, OSError):
            return []

    def _save_all(self, entries: list[dict[str, Any]]) -> None:
        """Overwrite JSONL with given entries (for prune)."""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
            tmp.replace(self._file)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
