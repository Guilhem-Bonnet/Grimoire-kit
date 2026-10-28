"""Sources runtime de l'observatoire — TraceLedger et journal d'événements.

L'export de ``framework/tools/observatory.py`` ne lisait que les fichiers de
l'ère shell (``Grimoire_TRACE.md``, ``.event-log.jsonl``). Les données réelles
vivent dans ``_grimoire-output/traces/traces.jsonl`` (TraceLedger) et
``_grimoire-output/events/runtime-journal.jsonl``. Ce module les lit et les
met à la forme que l'observatoire attend (``TraceEntry`` / ``EventEntry``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grimoire.traces.ledger import TraceLedger

__all__ = ["JOURNAL_RELPATH", "TRACES_RELPATH", "load_journal_events", "load_ledger_traces"]

TRACES_RELPATH = Path("_grimoire-output") / "traces"
JOURNAL_RELPATH = Path("_grimoire-output") / "events" / "runtime-journal.jsonl"


def load_ledger_traces(project_root: Path) -> list[dict[str, Any]]:
    """Traces du TraceLedger, une entrée ``TraceEntry`` par trace."""
    traces_dir = project_root / TRACES_RELPATH
    if not (traces_dir / "traces.jsonl").is_file():
        return []
    return [
        {
            "timestamp": t.started_at,
            "agent": t.agent_id or "unknown",
            "event_type": ",".join(t.tags) or str(t.outcome),
            "payload": f"{t.recipe_id} task={t.task_id} outcome={t.outcome} run={t.run_id}",
            "session": t.run_id,
        }
        for t in TraceLedger(traces_dir).list_traces()
    ]


def load_journal_events(project_root: Path) -> list[dict[str, Any]]:
    """Événements du journal runtime, une entrée ``EventEntry`` par ligne valide."""
    path = project_root / JOURNAL_RELPATH
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        details = raw.get("details")
        events.append({
            "id": f"journal-{len(events) + 1}",
            "ts": str(raw.get("timestamp", "")),
            "agent": "runtime",
            "type": str(raw.get("event_type", "")),
            "payload": {**(details if isinstance(details, dict) else {}), "profile": raw.get("profile", "")},
            "trace_id": "",
            "seq": len(events) + 1,
            "correlation_id": str(raw.get("task_id", "")),
            "tags": [],
        })
    return events
