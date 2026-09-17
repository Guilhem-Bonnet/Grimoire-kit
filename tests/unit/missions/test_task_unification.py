"""ADR-007 — migration idempotente et réversible task-board.yaml -> Mission Ledger.

Le bug qu'ADR-007 documente (issue #559, constat #521) : `grimoire standard
init` scaffold `task-board.yaml` sans jamais ouvrir de ledger, donc
`workspace_api.tasks_view` (Exécuter, Preuves du cockpit) ne voit rien tant que
personne n'a migré. Le premier test ci-dessous le prouve avant de prouver le
correctif.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.missions.board import BOARD_LIFECYCLE, board_status_of, write_board
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType
from grimoire.missions.service import TaskService
from grimoire.missions.task_unification import (
    TASK_UNIFICATION_MISSION_ID,
    migrate_standard_tasks,
    restore_task_unification,
    tasks_unification_status,
)

LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")

_BOOTSTRAP_TASK: dict[str, object] = {
    "task_id": "bootstrap",
    "title": "Bootstrap agentic standard runtime",
    "status": "proposed",
    "priority": "medium",
    "owner": "project-maintainer",
    "agent_roles": ["planner", "context_orchestrator"],
    "acceptance_criteria": ["Standard artifacts are generated and verified."],
    "blockers": [],
    "context_bundle_ref": "_grimoire-output/context/bootstrap/context-bundle.yaml",
    "decision_trace_ref": "_grimoire-output/decisions/bootstrap/decision-trace.yaml",
    "evidence_pack_ref": "_grimoire-output/evidence/bootstrap/evidence-pack.md",
    "remediation_ref": "_grimoire/standard/remediation-plan.yaml",
}

# Un task_id par colonne du board — les 8 états de BOARD_LIFECYCLE.
_LIFECYCLE_TASKS: list[dict[str, object]] = [
    {"task_id": f"t-{status}", "title": f"Task {status}", "status": status, "acceptance_criteria": ["ok"]}
    for status in BOARD_LIFECYCLE
]


def _write_scaffolded_board(root: Path, tasks: list[dict[str, object]]) -> Path:
    """Un board « scaffoldé » comme `grimoire standard init` le fait aujourd'hui : sans ledger."""
    board = {
        "$schema": "grimoire-agentic-standard-task-board/v1",
        "metadata": {
            "project": root.name,
            "generated_by": "grimoire standard init",
            "purpose": "Governed kanban for agentic work.",
        },
        "states": list(BOARD_LIFECYCLE),
        "transitions": {},
        "tasks": tasks,
    }
    path = root / STANDARD_DIR / "task-board.yaml"
    write_board(path, board)
    return path


def _read_board(path: Path) -> dict[str, object]:
    return YAML(typ="safe").load(path)  # type: ignore[no-any-return]


def _ledger(root: Path) -> MissionLedger:
    return MissionLedger(root / LEDGER_RELPATH)


def _make_task(**overrides: object) -> MissionTask:
    fields: dict[str, object] = {
        "id": "GAO-test-001",
        "mission_id": "MIS-test-001",
        "title": "Test",
        "status": TaskState.PROPOSED,
        "type": TaskType.IMPLEMENTATION,
        "risk_profile": RiskProfile.STANDARD,
        "acceptance": ("ok",),
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return MissionTask(**fields)  # type: ignore[arg-type]


# ── Le bug, rouge avant le correctif ───────────────────────────────────────────


def test_scaffolded_board_without_ledger_has_no_tasks(tmp_path: Path) -> None:
    """Ce que #521 constate : un board scaffoldé ne nourrit aucun ledger tant que rien ne l'y met."""
    _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    service = TaskService(tmp_path)
    assert service.has_ledger is False
    assert _ledger(tmp_path).list_tasks() == []


def test_migration_makes_the_task_appear_in_the_ledger(tmp_path: Path) -> None:
    """Le correctif : après migration, la tâche du board est une vraie tâche du ledger."""
    _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    report = migrate_standard_tasks(tmp_path)
    assert report.tasks_read == 1
    assert report.tasks_imported == 1
    assert report.tasks_skipped == 0
    assert report.mission_created is True

    assert TaskService(tmp_path).has_ledger is True
    task = _ledger(tmp_path).get_task("bootstrap")
    assert task is not None
    assert task.title == "Bootstrap agentic standard runtime"
    assert task.mission_id == TASK_UNIFICATION_MISSION_ID
    assert task.owner == "project-maintainer"
    assert list(task.acceptance) == ["Standard artifacts are generated and verified."]


# ── Les 8 états du board ───────────────────────────────────────────────────────


def test_every_lifecycle_state_has_a_reachable_path(tmp_path: Path) -> None:
    """Chaque colonne du board doit atterrir sur le bon état (ou sa classe d'équivalence ADR-005)."""
    _write_scaffolded_board(tmp_path, [dict(t) for t in _LIFECYCLE_TASKS])
    report = migrate_standard_tasks(tmp_path)
    assert report.tasks_imported == len(BOARD_LIFECYCLE)

    ledger = _ledger(tmp_path)
    # « released » se fusionne sur « accepted » côté ledger (CLOSED) — la classe
    # d'équivalence documentée par ADR-005, pas une perte.
    expected_column = {status: status for status in BOARD_LIFECYCLE}
    expected_column["released"] = "accepted"

    for status in BOARD_LIFECYCLE:
        task = ledger.get_task(f"t-{status}")
        assert task is not None, status
        assert board_status_of(task.status) == expected_column[status], status


def test_a_failed_or_blocked_origin_never_reads_as_accepted(tmp_path: Path) -> None:
    _write_scaffolded_board(tmp_path, [dict(t) for t in _LIFECYCLE_TASKS])
    migrate_standard_tasks(tmp_path)
    ledger = _ledger(tmp_path)
    blocked = ledger.get_task("t-blocked")
    assert blocked is not None
    assert board_status_of(blocked.status) not in {"accepted", "released"}


# ── Idempotence ─────────────────────────────────────────────────────────────


def test_migration_is_idempotent(tmp_path: Path) -> None:
    _write_scaffolded_board(tmp_path, [dict(t) for t in _LIFECYCLE_TASKS])
    board_path = tmp_path / STANDARD_DIR / "task-board.yaml"

    first = migrate_standard_tasks(tmp_path)
    assert first.tasks_imported == len(BOARD_LIFECYCLE)
    board_after_first = board_path.read_bytes()

    second = migrate_standard_tasks(tmp_path)
    assert second.tasks_imported == 0
    assert second.tasks_skipped == len(BOARD_LIFECYCLE)
    assert board_path.read_bytes() == board_after_first

    ledger = _ledger(tmp_path)
    assert len(ledger.list_tasks(TASK_UNIFICATION_MISSION_ID)) == len(BOARD_LIFECYCLE)


# ── Réversibilité ───────────────────────────────────────────────────────────


def test_migration_is_reversible(tmp_path: Path) -> None:
    board_path = _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    original_board = board_path.read_bytes()

    report = migrate_standard_tasks(tmp_path)
    assert report.tasks_imported == 1
    assert board_path.read_bytes() != original_board

    restored = restore_task_unification(tmp_path, report.stamp)
    assert restored
    assert board_path.read_bytes() == original_board
    ledger_root = tmp_path / LEDGER_RELPATH
    assert not (ledger_root / "events.jsonl").is_file()


def test_restore_without_snapshot_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        restore_task_unification(tmp_path, "NOPE")


# ── Aucune perte (I1) ───────────────────────────────────────────────────────


def test_no_evidence_ref_is_lost_across_migration(tmp_path: Path) -> None:
    _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    migrate_standard_tasks(tmp_path)
    board = _read_board(tmp_path / STANDARD_DIR / "task-board.yaml")
    tasks = board["tasks"]
    assert isinstance(tasks, list)
    entry = next(t for t in tasks if t["task_id"] == "bootstrap")
    assert entry["context_bundle_ref"] == _BOOTSTRAP_TASK["context_bundle_ref"]
    assert entry["decision_trace_ref"] == _BOOTSTRAP_TASK["decision_trace_ref"]
    assert entry["evidence_pack_ref"] == _BOOTSTRAP_TASK["evidence_pack_ref"]


# ── Cas limites ─────────────────────────────────────────────────────────────


def test_no_board_is_a_noop(tmp_path: Path) -> None:
    report = migrate_standard_tasks(tmp_path)
    assert report.tasks_read == 0
    assert report.tasks_imported == 0
    assert report.snapshot_path == ""


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    board_path = _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    before = board_path.read_bytes()

    report = migrate_standard_tasks(tmp_path, dry_run=True)

    assert report.tasks_imported == 1
    assert report.snapshot_path == ""
    assert board_path.read_bytes() == before
    assert not (tmp_path / LEDGER_RELPATH / "events.jsonl").is_file()


def test_dry_run_counts_idempotence_against_a_real_ledger(tmp_path: Path) -> None:
    _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    migrate_standard_tasks(tmp_path)  # migration réelle

    report = migrate_standard_tasks(tmp_path, dry_run=True)

    assert report.tasks_imported == 0
    assert report.tasks_skipped == 1


# ── Statut de divergence (terrain du futur doctor, PR 2/3) ─────────────────


def test_status_not_enrolled_never_diverges(tmp_path: Path) -> None:
    status = tasks_unification_status(tmp_path)
    assert status["enrolled"] is False
    assert status["diverged"] is False


def test_status_enrolled_board_without_ledger_diverges(tmp_path: Path) -> None:
    _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    status = tasks_unification_status(tmp_path)
    assert status["enrolled"] is True
    assert status["board_exists"] is True
    assert status["ledger_exists"] is False
    assert status["diverged"] is True
    assert status["missing_in_ledger"] == ["bootstrap"]


def test_status_after_migration_no_longer_diverges(tmp_path: Path) -> None:
    _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    migrate_standard_tasks(tmp_path)
    status = tasks_unification_status(tmp_path)
    assert status["diverged"] is False
    assert status["missing_in_ledger"] == []


# ── Le champ `finition` (lot 4.3, terrain préparé ici) ──────────────────────


@pytest.mark.parametrize("value", ["maquette", "peaufine"])
def test_finition_accepts_known_values(value: str) -> None:
    task = _make_task(finition=value)
    assert task.finition == value


def test_finition_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="finition"):
        _make_task(finition="autre")


def test_finition_round_trips_through_dict() -> None:
    task = _make_task(finition="maquette")
    restored = MissionTask.from_dict(task.to_dict())
    assert restored.finition == "maquette"


def test_finition_default_is_absent_from_dict() -> None:
    task = _make_task()
    assert task.finition == ""
    assert "finition" not in task.to_dict()


# ── CLI ───────────────────────────────────────────────────────────────────


def test_cli_migrate_standard_reports_counts(tmp_path: Path) -> None:
    from grimoire.cli.app import app

    typer_testing = pytest.importorskip("typer.testing")
    _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    runner = typer_testing.CliRunner()

    result = runner.invoke(app, ["-o", "json", "task", "migrate-standard", str(tmp_path)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tasks_imported"] == 1
    assert _ledger(tmp_path).get_task("bootstrap") is not None


def test_cli_migrate_standard_restore_round_trips(tmp_path: Path) -> None:
    from grimoire.cli.app import app

    typer_testing = pytest.importorskip("typer.testing")
    board_path = _write_scaffolded_board(tmp_path, [dict(_BOOTSTRAP_TASK)])
    original_board = board_path.read_bytes()
    runner = typer_testing.CliRunner()

    migrate_result = runner.invoke(app, ["-o", "json", "task", "migrate-standard", str(tmp_path)])
    assert migrate_result.exit_code == 0, migrate_result.output
    stamp = json.loads(migrate_result.output)["stamp"]

    restore_result = runner.invoke(app, ["task", "migrate-standard", str(tmp_path), "--restore", stamp])

    assert restore_result.exit_code == 0, restore_result.output
    assert board_path.read_bytes() == original_board
