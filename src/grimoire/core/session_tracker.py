"""Session momentum tracker for the SOG orchestrator.

Backs the Session Momentum protocol declared in orchestrator-gateway.md.
Tracks exchange count, token estimate, autonomy level transitions,
and momentum classification to help the SOG calibrate response depth
and proactive behavior intensity.

Usage::

    from grimoire.core.session_tracker import SessionTracker

    tracker = SessionTracker(project_root=Path("."))
    tracker.record_exchange(tokens_in=500, tokens_out=1200, autonomy="high")
    snap = tracker.snapshot()
    print(snap.momentum, snap.exchange_count)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["Exchange", "SessionSnapshot", "SessionTracker"]

SESSION_TRACKER_VERSION = "1.0.0"

_SESSION_DIR = "_grimoire/_memory/telemetry"
_SESSION_FILE = "session-momentum.jsonl"
_MAX_ENTRIES = 200


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Exchange:
    """A single user↔agent exchange."""

    tokens_in: int
    tokens_out: int
    autonomy: str  # low, medium, high
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "autonomy": self.autonomy,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Point-in-time session momentum summary."""

    exchange_count: int
    total_tokens_in: int
    total_tokens_out: int
    current_autonomy: str
    autonomy_transitions: int
    momentum: str  # cold, warming, hot, cooling
    avg_tokens_per_exchange: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange_count": self.exchange_count,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "current_autonomy": self.current_autonomy,
            "autonomy_transitions": self.autonomy_transitions,
            "momentum": self.momentum,
            "avg_tokens_per_exchange": self.avg_tokens_per_exchange,
        }


# ── Momentum classifier ─────────────────────────────────────────────────────


def _classify_momentum(exchanges: list[Exchange]) -> str:
    """Classify session momentum based on exchange patterns.

    - cold     = few exchanges, low volume
    - warming  = increasing rate/volume
    - hot      = sustained high activity
    - cooling  = decreasing rate after peak
    """
    n = len(exchanges)
    if n < 2:
        return "cold"

    # Compare first half vs second half token volumes
    mid = n // 2
    first_half = exchanges[:mid]
    second_half = exchanges[mid:]

    avg_first = sum(e.tokens_out for e in first_half) / len(first_half) if first_half else 0
    avg_second = sum(e.tokens_out for e in second_half) / len(second_half) if second_half else 0

    if n >= 5 and avg_second >= avg_first * 0.8:
        return "hot"
    if avg_second > avg_first * 1.2:
        return "warming"
    if avg_second < avg_first * 0.6:
        return "cooling"
    return "warming" if n < 5 else "hot"


# ── Core implementation ──────────────────────────────────────────────────────


class SessionTracker:
    """Track session momentum and autonomy level transitions.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()
        self._dir = self._root / _SESSION_DIR
        self._file = self._dir / _SESSION_FILE
        self._exchanges: list[Exchange] = []

    def record_exchange(
        self,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        autonomy: str = "medium",
    ) -> Exchange:
        """Record a single exchange in the current session."""
        exchange = Exchange(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            autonomy=autonomy,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._exchanges.append(exchange)
        self._persist(exchange)
        return exchange

    def snapshot(self) -> SessionSnapshot:
        """Compute current session snapshot."""
        exchanges = self._exchanges or self._load_exchanges()
        total_in = sum(e.tokens_in for e in exchanges)
        total_out = sum(e.tokens_out for e in exchanges)
        n = len(exchanges)

        # Count autonomy transitions
        transitions = 0
        for i in range(1, n):
            if exchanges[i].autonomy != exchanges[i - 1].autonomy:
                transitions += 1

        current_autonomy = exchanges[-1].autonomy if exchanges else "medium"
        momentum = _classify_momentum(exchanges)
        avg_tokens = (total_in + total_out) // n if n else 0

        return SessionSnapshot(
            exchange_count=n,
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            current_autonomy=current_autonomy,
            autonomy_transitions=transitions,
            momentum=momentum,
            avg_tokens_per_exchange=avg_tokens,
        )

    def reset(self) -> None:
        """Reset in-memory session state (e.g. between sessions)."""
        self._exchanges.clear()

    # ── Internal ──────────────────────────────────────────────────────────

    def _persist(self, exchange: Exchange) -> None:
        """Append exchange to JSONL file."""
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(exchange.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            logger.debug("Failed to persist exchange")

        # Prune
        self._prune()

    def _prune(self) -> None:
        """Keep only the latest entries."""
        if not self._file.is_file():
            return
        try:
            lines = self._file.read_text(encoding="utf-8").strip().splitlines()
            if len(lines) > _MAX_ENTRIES:
                kept = lines[-_MAX_ENTRIES:]
                self._file.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _load_exchanges(self) -> list[Exchange]:
        """Load exchanges from JSONL (for snapshot if none in memory)."""
        if not self._file.is_file():
            return []
        try:
            exchanges: list[Exchange] = []
            for line in self._file.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    d = json.loads(line)
                    exchanges.append(Exchange(
                        tokens_in=d.get("tokens_in", 0),
                        tokens_out=d.get("tokens_out", 0),
                        autonomy=d.get("autonomy", "medium"),
                        timestamp=d.get("timestamp", ""),
                    ))
            return exchanges
        except (json.JSONDecodeError, OSError):
            return []
