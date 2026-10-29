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
    assert ledger._loaded_key is None  # fin réparée : l'état en mémoire est jeté, pas prolongé

    raw = path.read_bytes()
    assert b"mission.cre\n{" in raw  # la ligne tronquée est refermée, pas collée
    fresh = MissionLedger(tmp_path / "l")
    assert {m.id for m in fresh.list_missions()} == {first.id, after.id}
    assert fresh.unreadable_lines() == (2,)
    assert ledger.unreadable_lines() == (2,)
    assert {m.id for m in ledger.list_missions()} == {first.id, after.id}


def test_unterminated_tail_is_an_append_in_progress_not_corruption(tmp_path: Path) -> None:
    """Un lecteur sans verrou qui voit une dernière ligne sans ``\\n`` ne lève pas de fausse alerte K12."""
    ledger = MissionLedger(tmp_path / "l")
    first = ledger.create_mission("avant", origin="user")
    path = tmp_path / "l" / "events.jsonl"
    with path.open("ab") as fh:
        fh.write(b'{"id": "evt-cut", "event_type": "mission.created", "entity_id": "MIS-x"')  # ajout en cours

    reader = MissionLedger(tmp_path / "l")
    assert reader.unreadable_lines() == ()
    assert {m.id for m in reader.list_missions()} == {first.id}
    assert all(e.id != "evt-cut" for e in reader._events)
    assert reader._loaded_key is not None and reader._loaded_key[0] is not None
    assert reader._loaded_key[0][0] == path.stat().st_size  # empreinte du fichier entier, état des lignes refermées

    after = ledger.create_mission("après", origin="user")  # l'ajout suivant referme la fin
    assert reader.unreadable_lines() == (2,)  # le fichier a bougé : rejeu, la ligne est désormais signalée
    assert {m.id for m in reader.list_missions()} == {first.id, after.id}
