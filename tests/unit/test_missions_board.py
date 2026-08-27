"""ADR-005 — la projection du Mission Ledger vers le task board gouverné.

Le critère du lot n'est pas « le module existe » : c'est qu'un board projeté
depuis un ledger réel passe le vérificateur du standard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import BOARD_STATES
from grimoire.missions.board import (
    BOARD_LIFECYCLE,
    board_status_of,
    build_board,
    task_state_of,
)
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import (
    DependencyKind,
    RiskProfile,
    TaskDependency,
    TaskState,
    TaskType,
)

# ── Le mapping, écrit une fois ───────────────────────────────────────────────


def test_every_ledger_state_has_a_column() -> None:
    """Un état sans colonne, c'est une carte qui disparaît du tableau."""
    for state in TaskState:
        assert board_status_of(state) in BOARD_STATES


def test_lifecycle_matches_the_standard() -> None:
    """La dérive entre les deux vocabulaires est la panne que l'ADR prévient."""
    assert set(BOARD_LIFECYCLE) == BOARD_STATES


def test_every_column_maps_back_to_a_state() -> None:
    for status in BOARD_LIFECYCLE:
        assert isinstance(task_state_of(status), TaskState)


def test_unknown_column_is_refused_not_guessed() -> None:
    with pytest.raises(ValueError, match="inconnu"):
        task_state_of("done")


def test_projection_is_idempotent_through_the_reverse_mapping() -> None:
    """9 états vers 8 : l'aller-retour ne rend pas l'état, mais rend la colonne."""
    for state in TaskState:
        column = board_status_of(state)
        assert board_status_of(task_state_of(column)) == column


@pytest.mark.parametrize(
    ("state", "column"),
    [
        (TaskState.CLAIMED, "in_progress"),
        (TaskState.RUNNING, "in_progress"),
        (TaskState.NEEDS_VERIFICATION, "review"),
        (TaskState.FAILED, "blocked"),
        (TaskState.CLOSED, "accepted"),
        (TaskState.CANCELLED, "archived"),
    ],
)
def test_documented_merges(state: TaskState, column: str) -> None:
    """Les fusions décidées par l'ADR — les figer évite qu'on les change par accident."""
    assert board_status_of(state) == column


def test_a_failed_task_never_reads_as_accepted() -> None:
    """Garde-fou de sens : un échec ne doit jamais atterrir côté succès."""
    assert board_status_of(TaskState.FAILED) not in {"accepted", "released"}


# ── La projection complète ───────────────────────────────────────────────────


def _seeded_ledger(root: Path) -> MissionLedger:
    ledger = MissionLedger(root / "ledger")
    mission = ledger.create_mission("Chantier de test", origin="test")
    ready = ledger.create_task(
        mission.id,
        "Tâche prête",
        acceptance=("le critère tient",),
        description="Ce que la carte veut dire.",
        guardrails=("ne pas toucher à la prod",),
        expected_evidence=("un test vert",),
        type=TaskType.IMPLEMENTATION,
        risk_profile=RiskProfile.STRICT,
        owner="amelia",
        surface="cli",
    )
    ledger.transition_task(ready.id, TaskState.READY)

    blocked = ledger.create_task(
        mission.id,
        "Tâche bloquée",
        acceptance=("débloquer",),
        dependencies=(TaskDependency(kind=DependencyKind.BLOCKS, target=ready.id),),
    )
    ledger.transition_task(blocked.id, TaskState.READY)
    ledger.transition_task(blocked.id, TaskState.BLOCKED, reason="dépendance non résolue")

    done = ledger.create_task(mission.id, "Tâche close", acceptance=("fini",))
    ledger.transition_task(done.id, TaskState.READY)
    ledger.claim_task(done.id, actor_id="quinn", host_id="local")
    ledger.transition_task(done.id, TaskState.RUNNING)
    ledger.transition_task(done.id, TaskState.NEEDS_VERIFICATION)
    ledger.transition_task(done.id, TaskState.CLOSED)
    return ledger


def test_board_carries_what_the_yaml_never_had(tmp_path: Path) -> None:
    """Une carte doit dire de quoi elle parle — le YAML n'avait que le titre."""
    board = build_board(_seeded_ledger(tmp_path), project="demo")
    ready = next(t for t in board["tasks"] if t["title"] == "Tâche prête")
    assert ready["description"] == "Ce que la carte veut dire."
    assert ready["guardrails"] == ["ne pas toucher à la prod"]
    assert ready["expected_evidence"] == ["un test vert"]
    assert ready["surface"] == "cli"
    assert ready["priority"] == "high"  # risk_profile strict
    assert ready["owner"] == "amelia"


def test_blocked_card_always_declares_a_reason(tmp_path: Path) -> None:
    """Le vérificateur refuse une carte bloquée sans motif — la projection en fournit un."""
    board = build_board(_seeded_ledger(tmp_path), project="demo")
    blocked = [t for t in board["tasks"] if t["status"] == "blocked"]
    assert blocked
    for task in blocked:
        assert task["blockers"], task["task_id"]
        assert task["blockers"][0]["reason"]


def test_owner_falls_back_to_the_claim(tmp_path: Path) -> None:
    board = build_board(_seeded_ledger(tmp_path), project="demo")
    closed = next(t for t in board["tasks"] if t["status"] == "accepted")
    assert closed["owner"] == "quinn"


def test_board_is_ordered_by_lifecycle(tmp_path: Path) -> None:
    board = build_board(_seeded_ledger(tmp_path), project="demo")
    columns = [BOARD_LIFECYCLE.index(t["status"]) for t in board["tasks"]]
    assert columns == sorted(columns)


def test_metadata_says_it_is_generated(tmp_path: Path) -> None:
    """Un artefact de sortie doit se déclarer comme tel, sinon on l'édite à la main."""
    board = build_board(_seeded_ledger(tmp_path), project="demo")
    assert board["metadata"]["source"] == "mission-ledger"
    assert "ne pas éditer" in board["metadata"]["purpose"].lower()


def test_mission_filter_narrows_the_projection(tmp_path: Path) -> None:
    ledger = _seeded_ledger(tmp_path)
    other = ledger.create_mission("Autre chantier", origin="test")
    ledger.create_task(other.id, "Ailleurs", acceptance=("x",))
    assert len(build_board(ledger)["tasks"]) == 4
    assert len(build_board(ledger, mission_id=other.id)["tasks"]) == 1
