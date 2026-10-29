"""I3 — l'ajout au ledger ne dépend plus de la taille du journal (plus de relecture ni de réécriture complète)."""

from __future__ import annotations

import json
import statistics
import time
from typing import TYPE_CHECKING

from grimoire.missions.ledger import MissionLedger

if TYPE_CHECKING:
    from pathlib import Path

_LINE = (
    json.dumps(
        {
            "id": "evt-x",
            "event_type": "note.added",
            "entity_id": "T-1",
            "entity_kind": "task",
            "actor_id": "a",
            "created_at": "2026-10-29T00:00:00+00:00",
            "payload": {"text": "x" * 480},
        },
    )
    + "\n"
)


def _synthetic(root: Path, megabytes: int) -> MissionLedger:
    root.mkdir(parents=True)
    (root / "events.jsonl").write_bytes(_LINE.encode() * (megabytes * 1024 * 1024 // len(_LINE)))
    return MissionLedger(root)


def _timings(ledger: MissionLedger, count: int = 20) -> list[float]:
    out = []
    for _ in range(count):
        start = time.perf_counter()
        ledger.append_event("note.added", "T-1", "task", "a", {"text": "y"})
        out.append((time.perf_counter() - start) * 1000)
    return out


def test_append_at_30_mb_is_fast_and_flat(tmp_path: Path) -> None:
    small = _timings(_synthetic(tmp_path / "s", 3))
    big_ledger = _synthetic(tmp_path / "b", 30)
    big = _timings(big_ledger)
    p95 = sorted(big)[int(len(big) * 0.95) - 1]
    assert p95 < 5.0, f"p95 {p95:.2f} ms à 30 Mo"
    assert statistics.median(big) <= 2 * max(statistics.median(small), 0.5)
    assert (tmp_path / "b" / "events.jsonl").read_bytes().count(b"\n") > 20
