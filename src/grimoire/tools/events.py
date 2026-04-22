"""GrimoireEvent — canonical event schema for Grimoire hooks, tasks, and runtime.

BM-53 SOG ledger : all hooks (9 scripts under ``.github/hooks/scripts/``) and
task flows emit :class:`GrimoireEvent` instances.  The gateway persists them
into an append-only ledger consumed by the TS surfaces (cockpit, mission
board, observatory).

Contract (stable, versioned by ``SCHEMA_VERSION``)::

    {
      "schema_version": "1.0",
      "event_id": "<uuid4>",
      "ts": "2026-04-21T23:12:45.123Z",
      "scope": "session|prompt|tool|subagent|compact|stop|task|anomaly",
      "phase": "start|end|block|correct|info",
      "source_hook": "grimoire-post-edit.sh",
      "agent": {"id": "...", "role": "...", "parent": "..."} | null,
      "correlation_id": "..." | null,
      "payload": {...}
    }

Design notes
------------
- **One writer only**: the hook gateway routes all writes through
  :func:`write_event`.  Direct file append by scripts is prohibited; use
  :func:`emit_event` which prints a line to stdout (picked up by the
  gateway pipeline).
- **Append-only**: events are never mutated or deleted.  A rotation tool
  may move old events to ``activity.<yyyymm>.jsonl`` in a future vague.
- **Fail-open**: schema violations never block a hook.  Invalid events are
  logged to ``_grimoire-runtime-output/hook-runtime/events-errors.jsonl``
  and the original hook output passes through unchanged.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

SCHEMA_VERSION = "1.0"
"""Semver-ish schema version.  Bump minor for additive fields, major for breaks."""

VALID_SCOPES: frozenset[str] = frozenset(
    {"session", "prompt", "tool", "subagent", "compact", "stop", "task", "anomaly"}
)
VALID_PHASES: frozenset[str] = frozenset(
    {"start", "end", "block", "correct", "info"}
)


LEDGER_RELATIVE = Path("_grimoire-runtime") / "_memory" / "activity.jsonl"
ERROR_LOG_RELATIVE = (
    Path("_grimoire-runtime-output") / "hook-runtime" / "events-errors.jsonl"
)


class GrimoireEventError(ValueError):
    """Raised when a payload cannot be coerced to a valid GrimoireEvent."""


@dataclass(slots=True)
class GrimoireEvent:
    """One event in the Grimoire runtime ledger."""

    scope: str
    phase: str
    source_hook: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: str = field(default_factory=lambda: _iso_now())
    agent: dict[str, Any] | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    REQUIRED_KEYS: ClassVar[tuple[str, ...]] = (
        "schema_version",
        "event_id",
        "ts",
        "scope",
        "phase",
        "source_hook",
    )

    def __post_init__(self) -> None:
        if self.scope not in VALID_SCOPES:
            msg = f"invalid scope {self.scope!r}; expected one of {sorted(VALID_SCOPES)}"
            raise GrimoireEventError(msg)
        if self.phase not in VALID_PHASES:
            msg = f"invalid phase {self.phase!r}; expected one of {sorted(VALID_PHASES)}"
            raise GrimoireEventError(msg)
        if not self.source_hook or not isinstance(self.source_hook, str):
            raise GrimoireEventError("source_hook must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise GrimoireEventError("payload must be a dict")
        if self.agent is not None and not isinstance(self.agent, dict):
            raise GrimoireEventError("agent must be a dict or None")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict with stable key ordering."""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "ts": self.ts,
            "scope": self.scope,
            "phase": self.phase,
            "source_hook": self.source_hook,
            "agent": self.agent,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        """Serialize to single-line JSON (JSONL-compatible)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GrimoireEvent:
        """Build a GrimoireEvent from a parsed JSON object.

        Unknown extra fields are dropped silently to allow forward
        compatibility (clients on older schema versions).
        """
        if not isinstance(data, dict):
            raise GrimoireEventError("event payload must be a JSON object")
        missing = [k for k in cls.REQUIRED_KEYS if k not in data]
        if missing:
            raise GrimoireEventError(f"missing required fields: {missing}")
        return cls(
            schema_version=str(data["schema_version"]),
            event_id=str(data["event_id"]),
            ts=str(data["ts"]),
            scope=str(data["scope"]),
            phase=str(data["phase"]),
            source_hook=str(data["source_hook"]),
            agent=data.get("agent"),
            correlation_id=(
                str(data["correlation_id"])
                if data.get("correlation_id") is not None
                else None
            ),
            payload=dict(data.get("payload", {}) or {}),
        )


def _iso_now() -> str:
    """UTC ISO 8601 with millisecond precision and trailing ``Z``."""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# ── Ledger I/O ────────────────────────────────────────────────────────────────

def ledger_path(project_root: Path) -> Path:
    """Return the canonical activity ledger path."""
    return project_root / LEDGER_RELATIVE


def error_log_path(project_root: Path) -> Path:
    """Return the path where invalid events are quarantined."""
    return project_root / ERROR_LOG_RELATIVE


def write_event(
    event: GrimoireEvent,
    project_root: Path,
    *,
    ledger_override: Path | None = None,
) -> Path:
    """Append ``event`` to the ledger.

    Writer is atomic-per-line: the single ``open(..., "a")`` + ``write()``
    call is guarded by the OS append semantics on Linux/macOS.  Callers
    that need stronger concurrency guarantees should go through the hook
    gateway which serializes writes.

    Returns the path that was written.
    """
    target = ledger_override or ledger_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(event.to_json())
        handle.write("\n")
    return target


def write_error(
    raw_line: str,
    reason: str,
    project_root: Path,
    *,
    error_override: Path | None = None,
) -> Path:
    """Quarantine an invalid event line with diagnostic context."""
    target = error_override or error_log_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _iso_now(),
        "reason": reason,
        "raw": raw_line,
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
    return target


def emit_event(event: GrimoireEvent) -> None:
    """Print the event to stdout as JSONL for the gateway to pick up.

    Scripts call this helper when they want the gateway to route the event
    through the canonical writer.  Never opens the ledger directly.
    """
    print(event.to_json(), flush=True)


def read_ledger(
    project_root: Path,
    *,
    since_ts: str | None = None,
    scope: str | None = None,
    limit: int | None = None,
    ledger_override: Path | None = None,
) -> list[GrimoireEvent]:
    """Read events from the ledger with optional filters.

    Filters are applied in order: ``since_ts`` (lexicographic ISO compare)
    then ``scope``, then ``limit`` (most recent first after reversal).

    Invalid lines are skipped silently (they're already in the error log).
    """
    source = ledger_override or ledger_path(project_root)
    if not source.is_file():
        return []
    events: list[GrimoireEvent] = []
    with source.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                event = GrimoireEvent.from_dict(data)
            except (json.JSONDecodeError, GrimoireEventError):
                continue
            if since_ts and event.ts <= since_ts:
                continue
            if scope and event.scope != scope:
                continue
            events.append(event)
    if limit is not None and limit >= 0:
        events = events[-limit:]
    return events


def parse_line(line: str) -> GrimoireEvent:
    """Parse one JSONL line into a GrimoireEvent or raise.

    Exposed for the gateway and tests.
    """
    data = json.loads(line)
    return GrimoireEvent.from_dict(data)


# ── Aggregation helpers for observability ─────────────────────────────────────

def counters(events: Iterable[GrimoireEvent]) -> dict[str, dict[str, int]]:
    """Compute roll-up counters by scope and phase.

    Returns a nested dict ``{scope: {phase: count}}``.
    """
    result: dict[str, dict[str, int]] = {}
    for event in events:
        bucket = result.setdefault(event.scope, {})
        bucket[event.phase] = bucket.get(event.phase, 0) + 1
    return result


__all__ = [
    "ERROR_LOG_RELATIVE",
    "LEDGER_RELATIVE",
    "SCHEMA_VERSION",
    "VALID_PHASES",
    "VALID_SCOPES",
    "GrimoireEvent",
    "GrimoireEventError",
    "counters",
    "emit_event",
    "error_log_path",
    "ledger_path",
    "parse_line",
    "read_ledger",
    "write_error",
    "write_event",
]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_emit(args: list[str]) -> int:
    """CLI: read JSON from stdin or ``--payload``, validate, print JSONL."""
    import argparse

    parser = argparse.ArgumentParser(prog="grimoire-events emit")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--source-hook", required=True)
    parser.add_argument("--correlation-id")
    parser.add_argument("--agent-json")
    parser.add_argument("--payload-json", default="{}")
    parsed = parser.parse_args(args)

    try:
        payload = json.loads(parsed.payload_json or "{}")
    except json.JSONDecodeError as exc:
        print(f"invalid --payload-json: {exc}", file=__import__("sys").stderr)
        return 2
    agent = None
    if parsed.agent_json:
        try:
            agent = json.loads(parsed.agent_json)
        except json.JSONDecodeError as exc:
            print(f"invalid --agent-json: {exc}", file=__import__("sys").stderr)
            return 2
    try:
        event = GrimoireEvent(
            scope=parsed.scope,
            phase=parsed.phase,
            source_hook=parsed.source_hook,
            correlation_id=parsed.correlation_id,
            agent=agent,
            payload=payload,
        )
    except GrimoireEventError as exc:
        print(f"invalid event: {exc}", file=__import__("sys").stderr)
        return 2
    emit_event(event)
    return 0


def _cli_read(args: list[str]) -> int:
    """CLI: dump recent ledger entries as JSONL."""
    import argparse

    parser = argparse.ArgumentParser(prog="grimoire-events read")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--since-ts")
    parser.add_argument("--scope")
    parser.add_argument("--limit", type=int, default=100)
    parsed = parser.parse_args(args)
    events = read_ledger(
        parsed.project_root,
        since_ts=parsed.since_ts,
        scope=parsed.scope,
        limit=parsed.limit,
    )
    for event in events:
        print(event.to_json())
    return 0


def _cli_counters(args: list[str]) -> int:
    """CLI: print counters as JSON."""
    import argparse

    parser = argparse.ArgumentParser(prog="grimoire-events counters")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--since-ts")
    parsed = parser.parse_args(args)
    events = read_ledger(parsed.project_root, since_ts=parsed.since_ts)
    print(json.dumps(counters(events), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys as _sys

    args = list(argv) if argv is not None else _sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print("usage: grimoire-events {emit|read|counters} ...")
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    dispatch = {"emit": _cli_emit, "read": _cli_read, "counters": _cli_counters}
    if cmd not in dispatch:
        print(f"unknown command: {cmd}", file=_sys.stderr)
        return 2
    return dispatch[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main())
