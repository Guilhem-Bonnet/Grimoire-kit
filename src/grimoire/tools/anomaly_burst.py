"""Burst detection for sub-agent activity — V4.6 BM-anomaly.

Detects bursts of repeated `(agent, event_name)` pairs within a sliding
window and emits an anomaly descriptor.  Designed to be invoked as a CLI
from `grimoire-subagent-trace.sh` after each subagent event:

    python -m grimoire.tools.anomaly_burst record \
        --agent dev --event SubagentStop \
        --state-file _grimoire-runtime-output/hook-runtime/anomaly-burst-state.json

Exit codes (fail-open by design — the CLI never blocks the calling hook):

* ``0`` — handled (whether or not an anomaly was detected).
* ``2`` — IO error on the state file.

stdout is JSON ``{"anomaly": null}`` when nothing crosses threshold, or
``{"anomaly": {...}}`` otherwise.  The shell hook is responsible for
forwarding the anomaly to ``grimoire-emit-event.sh --scope anomaly`` so
the single-writer guarantee on ``activity.jsonl`` is preserved.

Companion to BM-19/BM-20/BM-31 (V4.4 wave).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

STATE_VERSION = "1.0.0"
DEFAULT_WINDOW_SECONDS = 120
DEFAULT_THRESHOLD = 8
DEFAULT_COOLDOWN_SECONDS = 300
MAX_RETAINED_EVENTS = 1000


@dataclass(frozen=True)
class RecordedEvent:
    ts: str
    agent: str
    event: str


@dataclass(frozen=True)
class BurstAnomaly:
    agent: str
    event: str
    count: int
    window_seconds: int
    threshold: int
    first_ts: str
    last_ts: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class BurstState:
    version: str = STATE_VERSION
    events: list[RecordedEvent] = field(default_factory=list)
    cooldowns: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "events": [asdict(e) for e in self.events],
            "cooldowns": dict(self.cooldowns),
        }


# ── time helpers ─────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> datetime:
    # Accept both "...Z" and "+00:00" suffixes.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _cooldown_key(agent: str, event: str) -> str:
    return f"{agent}::{event}"


# ── state I/O ────────────────────────────────────────────────────────────────

def load_state(path: Path) -> BurstState:
    if not path.exists():
        return BurstState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BurstState()
    events_raw = raw.get("events", []) if isinstance(raw, dict) else []
    cooldowns_raw = raw.get("cooldowns", {}) if isinstance(raw, dict) else {}
    events: list[RecordedEvent] = []
    for entry in events_raw:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("ts")
        agent = entry.get("agent")
        event = entry.get("event")
        if isinstance(ts, str) and isinstance(agent, str) and isinstance(event, str):
            events.append(RecordedEvent(ts=ts, agent=agent, event=event))
    cooldowns: dict[str, str] = {}
    if isinstance(cooldowns_raw, dict):
        cooldowns = {
            key: value
            for key, value in cooldowns_raw.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    return BurstState(version=str(raw.get("version", STATE_VERSION)) if isinstance(raw, dict) else STATE_VERSION,
                      events=events, cooldowns=cooldowns)


def save_state(path: Path, state: BurstState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── core detection ───────────────────────────────────────────────────────────

def prune_events(
    events: Iterable[RecordedEvent],
    *,
    now: datetime,
    window_seconds: int,
    max_retained: int = MAX_RETAINED_EVENTS,
) -> list[RecordedEvent]:
    """Drop events older than the window; cap retention to avoid unbounded growth."""
    cutoff = now.timestamp() - window_seconds
    kept: list[RecordedEvent] = []
    for event in events:
        try:
            ts = _parse_ts(event.ts).timestamp()
        except ValueError:
            continue
        if ts >= cutoff:
            kept.append(event)
    if len(kept) > max_retained:
        kept = kept[-max_retained:]
    return kept


def prune_cooldowns(
    cooldowns: dict[str, str], *, now: datetime, cooldown_seconds: int
) -> dict[str, str]:
    cutoff = now.timestamp() - cooldown_seconds
    pruned: dict[str, str] = {}
    for key, ts in cooldowns.items():
        try:
            value = _parse_ts(ts).timestamp()
        except ValueError:
            continue
        if value >= cutoff:
            pruned[key] = ts
    return pruned


def detect_burst(
    events: list[RecordedEvent],
    *,
    agent: str,
    event_name: str,
    threshold: int,
    window_seconds: int,
) -> BurstAnomaly | None:
    matches = [e for e in events if e.agent == agent and e.event == event_name]
    if len(matches) < threshold:
        return None
    first = matches[0]
    last = matches[-1]
    return BurstAnomaly(
        agent=agent,
        event=event_name,
        count=len(matches),
        window_seconds=window_seconds,
        threshold=threshold,
        first_ts=first.ts,
        last_ts=last.ts,
    )


def record(
    state: BurstState,
    *,
    agent: str,
    event_name: str,
    now: datetime,
    threshold: int = DEFAULT_THRESHOLD,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> tuple[BurstState, BurstAnomaly | None]:
    """Append an event and return the (new_state, anomaly_if_threshold_crossed).

    A cooldown prevents re-emitting the same anomaly every additional event
    inside the same burst window.
    """
    events = prune_events(state.events, now=now, window_seconds=window_seconds)
    cooldowns = prune_cooldowns(state.cooldowns, now=now, cooldown_seconds=cooldown_seconds)
    new_event = RecordedEvent(ts=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), agent=agent, event=event_name)
    events.append(new_event)

    anomaly: BurstAnomaly | None = None
    cooldown_key = _cooldown_key(agent, event_name)
    if cooldown_key not in cooldowns:
        anomaly = detect_burst(
            events,
            agent=agent,
            event_name=event_name,
            threshold=threshold,
            window_seconds=window_seconds,
        )
        if anomaly is not None:
            cooldowns[cooldown_key] = new_event.ts

    new_state = BurstState(version=STATE_VERSION, events=events, cooldowns=cooldowns)
    return new_state, anomaly


# ── CLI ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grimoire.tools.anomaly_burst",
        description="Sliding-window burst detector for sub-agent activity (V4.6 BM-anomaly).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    record_p = sub.add_parser("record", help="Record an event and emit anomaly if threshold crossed.")
    record_p.add_argument("--agent", required=True)
    record_p.add_argument("--event", required=True)
    record_p.add_argument("--state-file", required=True, type=Path)
    record_p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    record_p.add_argument("--window-seconds", type=int, default=DEFAULT_WINDOW_SECONDS)
    record_p.add_argument("--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    record_p.add_argument(
        "--now",
        default=None,
        help="ISO timestamp override (UTC). Defaults to current time.",
    )

    inspect_p = sub.add_parser("inspect", help="Print the current state as JSON.")
    inspect_p.add_argument("--state-file", required=True, type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        state = load_state(args.state_file)
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return 0

    # record
    if args.now is not None:
        try:
            now = _parse_ts(args.now)
        except ValueError:
            print(json.dumps({"error": "invalid --now timestamp"}), file=sys.stderr)
            return 2
    else:
        now = datetime.now(UTC)

    try:
        state = load_state(args.state_file)
    except OSError:
        return 2

    new_state, anomaly = record(
        state,
        agent=args.agent,
        event_name=args.event,
        now=now,
        threshold=args.threshold,
        window_seconds=args.window_seconds,
        cooldown_seconds=args.cooldown_seconds,
    )

    try:
        save_state(args.state_file, new_state)
    except OSError:
        return 2

    payload: dict[str, object] = {"anomaly": anomaly.to_dict() if anomaly is not None else None}
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
