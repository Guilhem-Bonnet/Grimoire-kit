"""Preamble injection system — dynamic context assembly for skills and agents.

Inspired by gstack's ``{{PREAMBLE}}`` template system.  Instead of static
placeholders, this module assembles a live context block suitable for
injection at the top of any skill or agent invocation.

The preamble combines:
  1. **Session context** — session chain summary (last N sessions)
  2. **Operational learnings** — top entries from the learnings store
  3. **Skill telemetry** — recent usage for the target skill
  4. **Project vitals** — project name, phase, memory status

Usage::

    from grimoire.core.preamble import PreambleBuilder

    builder = PreambleBuilder(project_root=Path("."))
    preamble = builder.build(skill="grimoire-tdd")
    # → Markdown block ready for LLM injection
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["PreambleBuilder", "PreambleConfig"]

PREAMBLE_VERSION = "1.0.0"

_SESSION_CHAIN_FILE = "_grimoire/_memory/session-chain.jsonl"
_LEARNINGS_FILE = "_grimoire/_memory/learnings/operational.jsonl"
_TELEMETRY_FILE = "_grimoire/_memory/telemetry/skill-usage.jsonl"


@dataclass(frozen=True, slots=True)
class PreambleConfig:
    """Controls what gets injected into the preamble."""

    max_learnings: int = 5
    max_session_entries: int = 3
    max_telemetry_entries: int = 5
    include_learnings: bool = True
    include_session_chain: bool = True
    include_telemetry: bool = True
    include_vitals: bool = True


class PreambleBuilder:
    """Assembles a contextual preamble for skill/agent injection.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    config :
        Optional customization of preamble content.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        config: PreambleConfig | None = None,
    ) -> None:
        self._root = project_root.resolve()
        self._config = config or PreambleConfig()

    def build(self, *, skill: str = "", agent: str = "") -> str:
        """Build the full preamble as a Markdown block.

        Parameters
        ----------
        skill :
            Target skill name (for filtered telemetry).
        agent :
            Target agent name (for context enrichment).

        Returns
        -------
        str
            Ready-to-inject Markdown. Empty string if nothing to inject.
        """
        sections: list[str] = []

        if self._config.include_vitals:
            vitals = self._build_vitals()
            if vitals:
                sections.append(vitals)

        if self._config.include_session_chain:
            chain = self._build_session_chain()
            if chain:
                sections.append(chain)

        if self._config.include_learnings:
            learnings = self._build_learnings(skill=skill)
            if learnings:
                sections.append(learnings)

        if self._config.include_telemetry:
            telemetry = self._build_telemetry(skill=skill)
            if telemetry:
                sections.append(telemetry)

        if not sections:
            return ""

        header = "<!-- PREAMBLE:START -->"
        footer = "<!-- PREAMBLE:END -->"
        body = "\n\n".join(sections)
        return f"{header}\n{body}\n{footer}"

    # ── Section builders ──────────────────────────────────────────────────

    def _build_vitals(self) -> str:
        """Project vitals summary."""
        ctx_path = self._root / "project-context.yaml"
        if not ctx_path.exists():
            return ""

        try:
            from grimoire.tools._common import load_yaml

            data = load_yaml(ctx_path)
            project = data.get("project", {})
            name = project.get("name", "unknown")
            lines = [
                "### Project Vitals",
                f"- **Project**: {name}",
                f"- **Timestamp**: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
            ]
            # Add phase if available in shared-context
            shared = self._root / "_grimoire" / "_memory" / "shared-context.md"
            if shared.exists():
                content = shared.read_text(encoding="utf-8")[:500]
                if "phase" in content.lower():
                    for line in content.splitlines():
                        if "phase" in line.lower() and ":" in line:
                            lines.append(f"- **Phase**: {line.split(':', 1)[1].strip()}")
                            break
            return "\n".join(lines)
        except Exception:
            return ""

    def _build_session_chain(self) -> str:
        """Last N session summaries."""
        chain_path = self._root / _SESSION_CHAIN_FILE
        if not chain_path.exists():
            return ""

        entries = self._load_jsonl(chain_path, self._config.max_session_entries)
        if not entries:
            return ""

        lines = ["### Session History (recent)"]
        for entry in entries:
            ts = entry.get("timestamp", "?")[:16]
            phase = entry.get("phase", "?")
            status = entry.get("status", "?")
            summaries = entry.get("summaries", [])
            summary_text = "; ".join(summaries[:2]) if summaries else "no summary"
            lines.append(f"- `{ts}` [{phase}] {status} — {summary_text}")
        return "\n".join(lines)

    def _build_learnings(self, *, skill: str = "") -> str:
        """Top operational learnings, optionally filtered by skill."""
        learn_path = self._root / _LEARNINGS_FILE
        if not learn_path.exists():
            return ""

        entries = self._load_jsonl(learn_path, limit=0)  # load all, then filter
        if not entries:
            return ""

        # Filter by skill if specified
        if skill:
            skill_entries = [e for e in entries if e.get("skill") == skill]
            general_entries = [e for e in entries if e.get("skill") != skill]
            # Prefer skill-specific, backfill with general
            entries = skill_entries + general_entries

        # Sort by confidence desc, take top N
        entries.sort(key=lambda e: e.get("confidence", 0), reverse=True)
        entries = entries[: self._config.max_learnings]

        lines = ["### Operational Learnings"]
        for entry in entries:
            key = entry.get("key", "?")
            insight = entry.get("insight", "")
            conf = entry.get("confidence", 0)
            lines.append(f"- **{key}** ({conf}%): {insight}")
        return "\n".join(lines)

    def _build_telemetry(self, *, skill: str = "") -> str:
        """Recent skill usage telemetry."""
        telem_path = self._root / _TELEMETRY_FILE
        if not telem_path.exists():
            return ""

        entries = self._load_jsonl(telem_path, self._config.max_telemetry_entries)
        if not entries:
            return ""

        # Filter by skill if specified
        if skill:
            entries = [e for e in entries if e.get("skill") == skill] or entries

        lines = ["### Recent Skill Usage"]
        for entry in entries:
            sk = entry.get("skill", "?")
            ts = entry.get("timestamp", "?")[:16]
            outcome = entry.get("outcome", "?")
            lines.append(f"- `{ts}` {sk} → {outcome}")
        return "\n".join(lines)

    # ── JSONL loader ──────────────────────────────────────────────────────

    @staticmethod
    def _load_jsonl(path: Path, limit: int = 5) -> list[dict[str, Any]]:
        """Load last *limit* entries from a JSONL file.

        Parameters
        ----------
        limit :
            Max entries from tail.  0 = load all.
        """
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            if limit > 0:
                lines = lines[-limit:]
            entries: list[dict[str, Any]] = []
            for line in lines:
                if line.strip():
                    entries.append(json.loads(line))
            return entries
        except (json.JSONDecodeError, OSError):
            return []
