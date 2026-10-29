"""I3 — 8 processus × 50 ajouts : 400 lignes JSON valides, aucune perdue ni collée."""

from __future__ import annotations

import json
import multiprocessing
from typing import TYPE_CHECKING

from grimoire.missions.ledger import MissionLedger

if TYPE_CHECKING:
    from pathlib import Path


def _worker(root: str, n: int) -> None:
    from pathlib import Path as _Path

    ledger = MissionLedger(_Path(root))
    for i in range(50):
        ledger.append_event("note.added", "T-1", "task", f"w{n}", {"i": i})


def test_eight_processes_fifty_appends(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    MissionLedger(root)
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_worker, args=(str(root), n)) for n in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    lines = (root / "events.jsonl").read_bytes().split(b"\n")
    assert lines.pop() == b""
    assert len(lines) == 400
    seen = {(json.loads(x)["actor_id"], json.loads(x)["payload"]["i"]) for x in lines}
    assert len(seen) == 400


def test_in_memory_state_follows_own_appends_and_foreign_ones(tmp_path: Path) -> None:
    a, b = MissionLedger(tmp_path / "l"), MissionLedger(tmp_path / "l")
    m = a.create_mission("m", origin="user")
    assert [x.id for x in a.list_missions()] == [m.id]
    b.create_mission("n", origin="user")  # écrit par un autre : a doit le voir
    a.create_mission("o", origin="user")
    assert len(a.list_missions()) == 3
    assert [x.id for x in a.list_missions()] == [x.id for x in MissionLedger(tmp_path / "l").list_missions()]
