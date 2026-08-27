"""``grimoire task board export`` — et le critère d'acceptation du lot L1.

Le critère n'est pas « le module existe » : c'est qu'un board projeté depuis un
**ledger réel** passe le vérificateur du standard, profil `governed` compris.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import (
    DependencyKind,
    RiskProfile,
    TaskDependency,
    TaskState,
    TaskType,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_ledger(root: Path) -> MissionLedger:
    ledger = MissionLedger(root / "_grimoire-runtime-output" / "ledger")
    mission = ledger.create_mission("Chantier reel", origin="cli")
    ready = ledger.create_task(
        mission.id,
        "Exporter le board",
        acceptance=("le board passe verify",),
        description="Projection du ledger.",
        owner="amelia",
        risk_profile=RiskProfile.STRICT,
        type=TaskType.IMPLEMENTATION,
    )
    ledger.transition_task(ready.id, TaskState.READY)

    blocked = ledger.create_task(
        mission.id,
        "Tache bloquee",
        acceptance=("debloquer",),
        dependencies=(TaskDependency(kind=DependencyKind.BLOCKS, target=ready.id),),
    )
    ledger.transition_task(blocked.id, TaskState.READY)
    ledger.transition_task(blocked.id, TaskState.BLOCKED, reason="dep")

    closed = ledger.create_task(mission.id, "Tache close", acceptance=("fini",))
    ledger.transition_task(closed.id, TaskState.READY)
    ledger.claim_task(closed.id, actor_id="quinn", host_id="local")
    ledger.transition_task(closed.id, TaskState.RUNNING)
    ledger.transition_task(closed.id, TaskState.NEEDS_VERIFICATION)
    ledger.transition_task(closed.id, TaskState.CLOSED)
    return ledger


def _standard_project(runner: CliRunner, tmp_path: Path, profile: str = "governed") -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    result = runner.invoke(app, ["standard", "init", str(root), "--profile", profile])
    assert result.exit_code == 0, result.output
    return root


def test_export_then_verify_is_green_on_a_real_ledger(runner: CliRunner, tmp_path: Path) -> None:
    """Le critère d'acceptation de L1, de bout en bout."""
    root = _standard_project(runner, tmp_path)
    _seed_ledger(root)

    export = runner.invoke(app, ["task", "board", "export", str(root)])
    assert export.exit_code == 0, export.output

    verify = runner.invoke(app, ["standard", "board", "verify", str(root)])
    assert verify.exit_code == 0, verify.output


def test_export_replaces_the_hand_written_board(runner: CliRunner, tmp_path: Path) -> None:
    """La projection écrase : le YAML est une sortie, pas une source."""
    root = _standard_project(runner, tmp_path)
    board_path = root / "_grimoire" / "standard" / "task-board.yaml"
    before = board_path.read_text(encoding="utf-8")
    assert "bootstrap" in before  # le gabarit du standard

    _seed_ledger(root)
    runner.invoke(app, ["task", "board", "export", str(root)])

    after = board_path.read_text(encoding="utf-8")
    assert "bootstrap" not in after
    assert "source: mission-ledger" in after


def test_export_without_a_ledger_refuses_rather_than_wiping(runner: CliRunner, tmp_path: Path) -> None:
    """Sans ledger, écrire un board vide effacerait le travail déclaré."""
    root = _standard_project(runner, tmp_path)
    board_path = root / "_grimoire" / "standard" / "task-board.yaml"
    before = board_path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["task", "board", "export", str(root)])
    assert result.exit_code == 1
    assert board_path.read_text(encoding="utf-8") == before


def test_dry_run_writes_nothing(runner: CliRunner, tmp_path: Path) -> None:
    root = _standard_project(runner, tmp_path)
    _seed_ledger(root)
    board_path = root / "_grimoire" / "standard" / "task-board.yaml"
    before = board_path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["task", "board", "export", str(root), "--dry-run"])
    assert result.exit_code == 0
    assert board_path.read_text(encoding="utf-8") == before
    assert json.loads(result.stdout)["metadata"]["source"] == "mission-ledger"


def test_json_output_reports_what_was_written(runner: CliRunner, tmp_path: Path) -> None:
    root = _standard_project(runner, tmp_path)
    _seed_ledger(root)
    result = runner.invoke(app, ["--output", "json", "task", "board", "export", str(root)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["tasks"] == 3
    assert payload["by_status"] == {"ready": 1, "blocked": 1, "accepted": 1}


def test_output_option_leaves_the_standard_board_alone(runner: CliRunner, tmp_path: Path) -> None:
    root = _standard_project(runner, tmp_path)
    _seed_ledger(root)
    board_path = root / "_grimoire" / "standard" / "task-board.yaml"
    before = board_path.read_text(encoding="utf-8")

    dest = tmp_path / "ailleurs.yaml"
    result = runner.invoke(app, ["task", "board", "export", str(root), "--output", str(dest)])
    assert result.exit_code == 0, result.output
    assert dest.is_file()
    assert board_path.read_text(encoding="utf-8") == before
