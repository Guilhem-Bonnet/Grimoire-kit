"""I3 / K12 — une fin tronquée reste isolée et illisible ; l'événement suivant est relu."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grimoire.missions.ledger import MissionLedger

if TYPE_CHECKING:
    from pathlib import Path


def test_truncated_tail_is_isolated_and_next_event_is_read(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "l")
    first = ledger.create_mission("avant", origin="user")
    path = tmp_path / "l" / "events.jsonl"
    with path.open("ab") as fh:
        fh.write(b'{"id": "evt-cut", "event_type": "mission.cre')  # coupure en pleine ligne
    ledger.list_missions()  # chargé avec la fin tronquée
    after = ledger.create_mission("après", origin="user")

    raw = path.read_bytes()
    assert b"mission.cre\n{" in raw  # la ligne tronquée est refermée, pas collée
    fresh = MissionLedger(tmp_path / "l")
    assert {m.id for m in fresh.list_missions()} == {first.id, after.id}
    assert fresh.unreadable_lines() == (2,)
    assert ledger.unreadable_lines() == (2,)
    assert {m.id for m in ledger.list_missions()} == {first.id, after.id}
