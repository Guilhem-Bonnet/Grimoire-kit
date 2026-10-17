"""Lecteur tolérant du ledger — lot K1 de l'épopée #705.

Un état ou un champ écrit par une version plus récente ne doit jamais rendre
le ledger illisible chez un lecteur plus ancien : l'état s'affiche « inconnu »
et la tâche est figée ; un champ absent prend sa valeur par défaut.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.board import board_status_of
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import (
    TARGETABLE_TASK_STATES,
    MissionTask,
    TaskClaim,
    TaskState,
)

REPO = Path(__file__).resolve().parents[3]


def _ledger_with_task(root: Path) -> tuple[MissionLedger, str]:
    ledger = MissionLedger(root)
    mission = ledger.create_mission("Mission K1", origin="test", created_by="t")
    task = ledger.create_task(mission.id, "Tâche", acceptance=("ok",), owner="t")
    ledger.transition_task(task.id, TaskState.READY, actor_id="t")
    ledger.claim_task(task.id, "t", "local")
    return ledger, task.id


def _append_raw(root: Path, record: dict) -> None:
    with (root / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _newer_transition(task_id: str, to_state: str) -> dict:
    return {
        "id": "evt-newer",
        "schema_version": "grimoire.ledger_event.v2",
        "event_type": "task.transitioned",
        "entity_id": task_id,
        "entity_kind": "task",
        "actor_id": "agent-newer",
        "created_at": "2026-10-16T10:00:00+00:00",
        "payload": {"task_id": task_id, "from_state": "running", "to_state": to_state, "reason": "ci"},
    }


def test_unknown_state_displayed_unknown_and_frozen(tmp_path: Path) -> None:
    _, task_id = _ledger_with_task(tmp_path)
    _append_raw(tmp_path, _newer_transition(task_id, "waiting"))

    reader = MissionLedger(tmp_path)  # rejeu complet par un lecteur qui ignore « waiting »
    task = reader.get_task(task_id)

    assert task is not None
    assert task.status is TaskState.UNKNOWN
    assert task.raw_status == "waiting"
    assert "waiting" in task.status_label and "inconnu" in task.status_label
    assert board_status_of(task.status) == "blocked"
    for target in TARGETABLE_TASK_STATES:
        with pytest.raises(GrimoireMissionError, match="inconnu"):
            reader.transition_task(task_id, target, actor_id="t")
    # figée : rien n'a été ajouté au journal par les refus
    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8").count("task.transitioned") == 3


def test_unknown_state_survives_a_rewrite(tmp_path: Path) -> None:
    _, task_id = _ledger_with_task(tmp_path)
    _append_raw(tmp_path, _newer_transition(task_id, "waiting"))
    task = MissionLedger(tmp_path).get_task(task_id)
    assert task is not None
    assert task.to_dict()["status"] == "waiting"
    assert MissionTask.from_dict(task.to_dict()).raw_status == "waiting"


def test_unknown_state_is_not_offered_as_a_target() -> None:
    assert TaskState.UNKNOWN not in TARGETABLE_TASK_STATES


def test_missing_lease_epoch_defaults_zero() -> None:
    claim = TaskClaim.from_dict({"actor_id": "a", "host_id": "h"})
    assert claim.lease_epoch == 0
    assert "lease_epoch" not in claim.to_dict()
    assert TaskClaim.from_dict({"actor_id": "a", "host_id": "h", "lease_epoch": "x"}).lease_epoch == 0
    newer = TaskClaim.from_dict({"actor_id": "a", "host_id": "h", "lease_epoch": 3})
    assert newer.lease_epoch == 3
    assert newer.with_session("s1").lease_epoch == 3
    assert TaskClaim.from_dict(newer.to_dict()).lease_epoch == 3


def test_ledger_of_current_main_replays_identically(tmp_path: Path) -> None:
    ledger, task_id = _ledger_with_task(tmp_path)
    ledger.transition_task(task_id, TaskState.RUNNING, actor_id="t")
    before = ledger.get_task(task_id)
    assert before is not None
    raw = (tmp_path / "events.jsonl").read_text(encoding="utf-8")

    after = MissionLedger(tmp_path).get_task(task_id)

    assert after == before
    assert after is not None and after.to_dict() == before.to_dict()
    assert "raw_status" not in raw  # lease_epoch : posé par le claim depuis K2, rejoué à l'identique
    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8") == raw  # lecture seule


def test_rust_extensions_do_not_validate_task_states() -> None:
    """Absence prouvée : aucun crate Rust ne porte les états de tâche du ledger.

    Si un port Rust se met à les valider, ce test rougit et impose la parité
    (états inconnus tolérés) avant de continuer.
    """
    states = ('"claimed"', '"needs_verification"', '"proposed"')
    hits = [
        f"{path.relative_to(REPO)}: {s}"
        for path in (REPO / "rust").glob("*/src/**/*.rs")
        for text in [path.read_text(encoding="utf-8")]
        for s in states
        if s in text
    ]
    assert hits == []


def test_board_of_an_unknown_state_task_passes_the_governed_verifier(tmp_path: Path) -> None:
    """K1-S1-01 : la carte « blocked » d'un état inconnu doit porter un motif.

    Sans motif, ``board.blocker_reason_missing`` rejette le board et chaque
    geste du service qui le projette échoue.
    """
    from grimoire.core.agentic_standard import get_profile
    from grimoire.core.standard_checks.base import StandardVerificationResult
    from grimoire.core.standard_checks.verifiers import _verify_task_board
    from grimoire.missions.board import build_board, write_board

    _, task_id = _ledger_with_task(tmp_path)
    _append_raw(tmp_path, _newer_transition(task_id, "waiting"))
    ledger = MissionLedger(tmp_path)

    board = build_board(ledger)
    root = tmp_path / "proj"
    write_board(root / "_grimoire" / "standard" / "task-board.yaml", board)
    profile = get_profile("governed")
    result = StandardVerificationResult(profile=profile.id, project_root=root)
    _verify_task_board(root, profile, result)

    assert "board.blocker_reason_missing" not in {c.id for c in result.checks}
    assert [c.id for c in result.checks if c.is_error] == []
    card = next(c for c in board["tasks"] if c["task_id"] == task_id)
    assert card["status"] == "blocked"
    assert card["blockers"] and "waiting" in card["blockers"][0]["reason"]
    assert card["blockers"][0]["target"] == task_id
