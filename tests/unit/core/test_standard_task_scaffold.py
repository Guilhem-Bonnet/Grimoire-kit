"""``grimoire standard task scaffold`` — issue #582 lot G1.

Le banc du 2026-09-17 a mesuré une médiane de 11 tours par run passés par
l'agent gouverné à reconstituer, dans le source installé du kit, où créer les
artefacts que ``gate check`` réclame et quoi mettre dedans. Ces tests fixent le
contrat qui ferme ce trou : les artefacts existent, pré-remplis, avant la
première commande de l'agent ; un squelette frais n'est jamais refusé par le
gate pour un motif de forme ; rien n'est réécrit ; rien n'est écrit en
``--dry-run`` ; et le chemin « tout existe déjà » du hook ``SessionStart``
reste sous le budget d'une session.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import (
    _generation_targets,
    check_evidence_gates,
    setup_standard_profile,
)
from grimoire.core.standard_checks.gate_remedy import (
    GATE_ARTIFACT_KEYS,
    TASK_LEVEL_KEYS,
    gate_artifact_relpath,
    remedy_command,
)
from grimoire.core.standard_task_scaffold import (
    missing_task_artifacts,
    scaffold_task_artifacts,
    task_artifact_relpaths,
)
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

JOURNAL = Path("_grimoire-output/events/runtime-journal.jsonl")


def _open_task(root: Path, *, claim: bool = True) -> str:
    """Une tâche du Mission Ledger, réclamée (``in_progress`` sur le board) et projetée."""
    service = TaskService(root)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(
        mission.id,
        "Ajouter la fonction somme",
        acceptance=("somme(2,3) vaut 5", "les tests passent"),
        owner="dev",
    )
    if claim:
        service.ledger.transition_task(task.id, TaskState.READY, actor_id="dev")
        service.ledger.claim_task(task.id, "dev", "local")
    service.project_board()
    return task.id


@pytest.fixture
def starter(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="starter")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    return tmp_path


def test_scaffold_paths_match_the_profile_map_and_the_gate_keys() -> None:
    """Une seule convention de chemins : la table du remède, la carte des profils, le gate."""
    targets = _generation_targets()
    for key in ("task_envelope", "evidence_pack", "claim_ledger", "acceptance_record"):
        assert str(gate_artifact_relpath(key, "T-1")) == targets[key].replace("{task-id}", "T-1")
    assert set(task_artifact_relpaths("T-1")) == TASK_LEVEL_KEYS
    for key in GATE_ARTIFACT_KEYS:
        assert remedy_command(key, root=Path("/p"), task_id="T-1", profile_id="starter").startswith("grimoire ")


def test_scaffold_creates_every_missing_artifact_prefilled(starter: Path) -> None:
    task_id = _open_task(starter)
    assert set(missing_task_artifacts(starter, task_id)) == TASK_LEVEL_KEYS

    result = scaffold_task_artifacts(starter, task_id=task_id)

    assert not result.dry_run
    assert len(result.written) == 6 and result.skipped == ()
    assert missing_task_artifacts(starter, task_id) == []
    evidence = starter / "_grimoire-output/evidence" / task_id
    envelope = (evidence / "task-envelope.md").read_text(encoding="utf-8")
    assert f"- Task id: {task_id}\n" in envelope
    assert "- Request: Ajouter la fonction somme\n" in envelope
    assert "- Profile: starter\n" in envelope
    assert "- Current state: `executing`\n" in envelope
    assert "| `pytest -q` | execute |" in envelope, "la commande de test résolue borne l'outillage"
    pack = (evidence / "evidence-pack.md").read_text(encoding="utf-8")
    assert (
        "- Outcome: Ajouter la fonction somme — critères : AC-001 somme(2,3) vaut 5; AC-002 les tests passent\n" in pack
    )
    assert "- Outcome:\n" not in pack, "le résumé placeholder est remplacé par un résumé généré"
    assert "- Final state: in_progress" in pack
    record = (evidence / "acceptance-record.md").read_text(encoding="utf-8")
    assert "| AC-001 | somme(2,3) vaut 5 |  | à vérifier |\n| AC-002 | les tests passent |  | à vérifier |\n" in record
    assert "- Deliverable: Ajouter la fonction somme\n" in record
    assert f"- Task id: {task_id}\n" in (evidence / "claim-ledger.md").read_text(encoding="utf-8")
    assert (starter / "_grimoire-output/context" / task_id / "context-bundle.yaml").is_file()
    assert (starter / "_grimoire-output/decisions" / task_id / "decision-trace.yaml").is_file()
    assert "task.scaffolded" in (starter / JOURNAL).read_text(encoding="utf-8")


def test_a_fresh_skeleton_is_never_refused_by_the_gate_for_form(starter: Path) -> None:
    """Le critère du lot : après scaffold, seul un motif de fond peut fermer le gate."""
    task_id = _open_task(starter)
    scaffold_task_artifacts(starter, task_id=task_id)

    in_progress = check_evidence_gates(starter, task_id=task_id)
    review = check_evidence_gates(starter, task_id=task_id, target_state="review")

    assert in_progress.ok, in_progress.checks
    assert review.ok, review.checks
    assert not [c for c in review.checks if c.is_error]


def test_scaffold_is_idempotent_and_never_rewrites_a_file(starter: Path) -> None:
    task_id = _open_task(starter)
    scaffold_task_artifacts(starter, task_id=task_id)
    envelope = starter / "_grimoire-output/evidence" / task_id / "task-envelope.md"
    envelope.write_text("rempli par l'agent\n", encoding="utf-8")
    (starter / "_grimoire-output/context" / task_id / "context-bundle.yaml").unlink()

    again = scaffold_task_artifacts(starter, task_id=task_id)

    assert [str(p) for p in again.written] == [f"_grimoire-output/context/{task_id}/context-bundle.yaml"]
    assert envelope.read_text(encoding="utf-8") == "rempli par l'agent\n"
    third = scaffold_task_artifacts(starter, task_id=task_id)
    assert third.written == () and len(third.skipped) == 6


def test_dry_run_plans_everything_and_writes_nothing(starter: Path) -> None:
    task_id = _open_task(starter)
    before = sorted(str(p.relative_to(starter)) for p in starter.rglob("*") if p.is_file())

    result = scaffold_task_artifacts(starter, task_id=task_id, dry_run=True)

    assert result.dry_run and len(result.written) == 6
    after = sorted(str(p.relative_to(starter)) for p in starter.rglob("*") if p.is_file())
    assert after == before, "un dry-run n'écrit ni fichier ni événement de journal"


def test_scaffold_refuses_a_task_unknown_to_ledger_and_board(starter: Path) -> None:
    """Pas de dossier orphelin sous ``_grimoire-output/`` : le remède est ``task add``."""
    with pytest.raises(ValueError, match="grimoire task add"):
        scaffold_task_artifacts(starter, task_id="T-inconnue")
    assert not (starter / "_grimoire-output/evidence/T-inconnue").exists()


def test_facts_come_from_the_board_when_there_is_no_ledger(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter")
    board = tmp_path / "_grimoire/standard/task-board.yaml"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text(
        "tasks:\n  - task_id: T-board\n    title: Tâche du board\n    status: review\n"
        '    priority: high\n    owner: ops\n    acceptance_criteria: ["un critère"]\n',
        encoding="utf-8",
    )

    result = scaffold_task_artifacts(tmp_path, task_id="T-board")

    assert result.facts is not None and result.facts.source == "board"
    envelope = (tmp_path / "_grimoire-output/evidence/T-board/task-envelope.md").read_text(encoding="utf-8")
    assert "- Current state: `validating`\n" in envelope
    assert "- Risk level: `high`\n" in envelope
    assert "- Owner agent: ops\n" in envelope


def test_scaffold_noop_stays_under_the_session_start_budget(starter: Path) -> None:
    """Le chemin « tout existe » du hook SessionStart : six ``stat``, pas un YAML.

    Seuil délibérément large (100 ms) par rapport à la mesure (de l'ordre de
    la milliseconde) : il borne une régression de nature — ré-ouvrir le ledger
    ou re-parser le profil à chaque session — pas un jitter de machine.
    """
    task_id = _open_task(starter)
    scaffold_task_artifacts(starter, task_id=task_id)

    samples = []
    for _ in range(20):
        started = time.perf_counter()
        result = scaffold_task_artifacts(starter, task_id=task_id)
        samples.append(time.perf_counter() - started)
        assert result.written == ()
    assert statistics.median(samples) < 0.1, f"médiane {statistics.median(samples) * 1000:.1f} ms"
