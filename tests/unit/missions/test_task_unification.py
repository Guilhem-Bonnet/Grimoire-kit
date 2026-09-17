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


# ── Priorité, rôles, remediation_ref, champs inconnus (défaut réel Forge) ────
#
# Constat du 2026-09-17 sur la Forge (kit 3.55.0) : `migrate_standard_tasks`
# reprojetait `task-board.yaml` en écrasant `priority`/`agent_roles` par des
# défauts (medium/[implementation]) dès que la tâche n'était pas « blocked »,
# perdait `remediation_ref` hors de cet état, et jetait silencieusement tout
# champ de board que `MissionTask` ne modélisait pas (ex. `labels`).

_LOSSY_TASK: dict[str, object] = {
    "task_id": "lossy-check",
    "title": "Vérifier le round-trip du board",
    "status": "accepted",
    "priority": "high",
    "owner": "grimoire-maintainers",
    "agent_roles": ["orchestrator", "reviewer"],
    "acceptance_criteria": ["Le board reprojeté porte les mêmes valeurs que le board scaffoldé."],
    "blockers": [],
    "context_bundle_ref": "_grimoire-output/context/lossy-check/context-bundle.yaml",
    "decision_trace_ref": "_grimoire-output/decisions/lossy-check/decision-trace.yaml",
    "evidence_pack_ref": "_grimoire-output/evidence/lossy-check/evidence-pack.md",
    "remediation_ref": "_grimoire/standard/remediation-plan.yaml",
    "labels": ["x"],
}


def test_priority_roles_remediation_ref_and_unknown_fields_survive_migration(tmp_path: Path) -> None:
    """Reproduction exacte du défaut Forge : rien n'est écrasé par un défaut."""
    _write_scaffolded_board(tmp_path, [dict(_LOSSY_TASK)])
    report = migrate_standard_tasks(tmp_path)
    assert report.tasks_imported == 1

    board = _read_board(tmp_path / STANDARD_DIR / "task-board.yaml")
    tasks = board["tasks"]
    assert isinstance(tasks, list)
    entry = next(t for t in tasks if t["task_id"] == "lossy-check")

    for key, value in _LOSSY_TASK.items():
        assert entry.get(key) == value, key

    # Le seul écart admis avec l'entrée d'origine : le bloc de vérifiabilité
    # ajouté par la projection — voulu, documenté, pas une perte.
    assert "verifiability" in entry

    task = _ledger(tmp_path).get_task("lossy-check")
    assert task is not None
    assert task.priority == "high"
    assert list(task.agent_roles) == ["orchestrator", "reviewer"]
    assert task.remediation_ref == "_grimoire/standard/remediation-plan.yaml"
    assert task.extra == {"labels": ["x"]}


def test_task_list_shows_the_original_status_after_migration(tmp_path: Path) -> None:
    """Critère (1) : `grimoire task list` doit montrer l'état d'origine de la
    tâche (« accepted » -> ledger CLOSED), pas une régression à PROPOSED.
    """
    from grimoire.cli.app import app

    typer_testing = pytest.importorskip("typer.testing")
    _write_scaffolded_board(tmp_path, [dict(_LOSSY_TASK)])
    migrate_standard_tasks(tmp_path)

    runner = typer_testing.CliRunner()
    result = runner.invoke(
        app,
        ["task", "list", "--project-root", str(tmp_path), "--ledger-root", str(tmp_path / LEDGER_RELPATH)],
    )
    assert result.exit_code == 0, result.output
    assert "closed" in result.output
    assert "accepted" in result.output


# ── Réparation d'un projet déjà migré à perte (issue Forge du 2026-09-17) ───


def _old_lossy_import_one_task(ledger: MissionLedger, entry: dict[str, object]) -> None:
    """Copie figée du comportement *avant* correctif — pour prouver la réparation.

    Cette fonction ne doit plus jamais tourner en dehors d'un test : elle
    reproduit fidèlement l'ancien `_import_one_task`, qui ignorait
    `priority`/`agent_roles`/`remediation_ref` et jetait tout champ de board
    inconnu du schéma.
    """
    from grimoire.missions import task_unification as tu
    from grimoire.missions.board import task_state_of as _task_state_of

    task_id = str(entry["task_id"])
    acceptance = tuple(entry.get("acceptance_criteria") or ["Migré depuis task-board.yaml — critère à préciser"])
    task = ledger.create_task(
        TASK_UNIFICATION_MISSION_ID,
        str(entry.get("title") or task_id),
        acceptance=acceptance,
        owner=str(entry.get("owner") or ""),
        description=str(entry.get("description") or ""),
        guardrails=tuple(entry.get("guardrails") or ()),
        expected_evidence=tuple(entry.get("expected_evidence") or ()),
        task_id=task_id,
    )
    try:
        target = _task_state_of(str(entry.get("status", "proposed")))
    except ValueError:
        return
    tu._walk_to_state(ledger, task.id, target)


def test_repair_sequence_restore_then_remigrate_matches_original_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La séquence de réparation d'un projet déjà migré à perte (ex. la Forge) :

    1. un projet migré par l'ancien comportement a perdu priorité/rôles/refs ;
    2. `grimoire task migrate-standard --restore <stamp>` ramène le board et le
       ledger à leur état d'avant migration (l'instantané n'est jamais lossy) ;
    3. remigrer avec le correctif applique produit un board identique à
       l'original (à `verifiability` près).
    """
    import grimoire.missions.task_unification as tu

    board_path = _write_scaffolded_board(tmp_path, [dict(_LOSSY_TASK)])
    original_board = board_path.read_bytes()

    # 1. Migration avec l'ancien comportement (simulé) — reproduit le défaut.
    monkeypatch.setattr(tu, "_import_one_task", _old_lossy_import_one_task)
    lossy_report = tu.migrate_standard_tasks(tmp_path)
    assert lossy_report.tasks_imported == 1
    lossy_entry = next(t for t in _read_board(board_path)["tasks"] if t["task_id"] == "lossy-check")
    assert lossy_entry["priority"] != _LOSSY_TASK["priority"]
    assert lossy_entry.get("remediation_ref") is None
    assert "labels" not in lossy_entry

    # 2. Réparation : restaurer l'instantané pris avant cette migration lossy.
    monkeypatch.undo()
    restored = restore_task_unification(tmp_path, lossy_report.stamp)
    assert restored
    assert board_path.read_bytes() == original_board
    assert not (tmp_path / LEDGER_RELPATH / "events.jsonl").is_file()

    # 3. Remigrer avec le correctif : plus aucune perte.
    fixed_report = migrate_standard_tasks(tmp_path)
    assert fixed_report.tasks_imported == 1
    fixed_entry = next(t for t in _read_board(board_path)["tasks"] if t["task_id"] == "lossy-check")
    for key, value in _LOSSY_TASK.items():
        assert fixed_entry.get(key) == value, key


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
