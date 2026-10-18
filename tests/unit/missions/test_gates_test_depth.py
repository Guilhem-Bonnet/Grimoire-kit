"""La profondeur de test déclarée est opposable à la fermeture (W6-01).

Le refus porte le code ``acceptance.depth_floor_unmet`` et se mesure sur les
commandes du dernier verdict ``task verify`` — pas sur ce que la tâche promet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.evidence.schemas import VerdictResult, VerificationCheck
from grimoire.missions.ceremony import DEPTH_UNMET
from grimoire.missions.gates import GATES_FILE, check_transition
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType
from grimoire.missions.service import TaskService

STANDARD = Path("_grimoire/standard")
EVIDENCE_LEDGER = Path("_grimoire-runtime-output/evidence")
LEDGER = Path("_grimoire-runtime-output/ledger")

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["review_gate"]
profile_strictness:
  governed: hard_fail
"""


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    (tmp_path / GATES_FILE).write_text(GATES, encoding="utf-8")
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    return tmp_path


def tache(**kw: object) -> MissionTask:
    base: dict[str, object] = {
        "id": "GAO-depth-001", "mission_id": "M-1", "title": "Démo", "status": TaskState.NEEDS_VERIFICATION,
        "type": TaskType.IMPLEMENTATION, "risk_profile": RiskProfile.STANDARD,
        "acceptance": ("les tests passent",), "created_at": "2026-10-18T00:00:00Z", "owner": "amelia",
    }
    base.update(kw)
    return MissionTask(**base)  # type: ignore[arg-type]


def verdict(root: Path, *commands: tuple[str, bool]) -> None:
    """Le pack et le verdict tels que ``task verify`` les écrit : une pièce et un contrôle par commande."""
    svc = EvidenceService(root / EVIDENCE_LEDGER)
    items = [
        EvidenceItem.from_text(f"check-{i:02d}", EvidenceKind.TEST, cmd, uri=f"check:{cmd}", summary=cmd)
        for i, (cmd, _) in enumerate(commands, start=1)
    ]
    pack = svc.create_pack("GAO-depth-001", EvidenceProfile.LIGHT, items)
    svc.verify(pack, extra_checks=tuple(
        VerificationCheck(id=f"command-{i:02d}", result=VerdictResult.PASSED if ok else VerdictResult.FAILED)
        for i, (_, ok) in enumerate(commands, start=1)
    ))


def _depth(verdict_: object) -> list[str]:
    return [r.reason for r in getattr(verdict_, "refusals", ()) if r.evidence == DEPTH_UNMET]


def test_profondeur_non_atteinte_bloque_la_fermeture(projet: Path) -> None:
    verdict(projet, ("ruff check src", True))
    v = check_transition(projet, tache(test_depth="standard"), "review", "accepted")
    assert v.blocked
    assert _depth(v) and "standard" in _depth(v)[0] and "floor" in _depth(v)[0]


def test_profondeur_atteinte_ne_refuse_pas(projet: Path) -> None:
    verdict(projet, ("pytest -q", True), ("ruff check src", True))
    v = check_transition(projet, tache(test_depth="strict"), "review", "accepted")
    assert _depth(v) == []
    assert not v.blocked


def test_une_commande_rouge_ne_compte_pas(projet: Path) -> None:
    verdict(projet, ("pytest -q", False))
    assert _depth(check_transition(projet, tache(test_depth="floor"), "review", "accepted"))


def test_sans_verdict_la_profondeur_est_refusee(projet: Path) -> None:
    reasons = _depth(check_transition(projet, tache(test_depth="floor"), "review", "accepted"))
    assert reasons and "aucune" in reasons[0]


def test_sans_test_depth_le_gate_est_inchange(projet: Path) -> None:
    verdict(projet, ("ruff check src", True))
    v = check_transition(projet, tache(), "review", "accepted")
    assert _depth(v) == [] and not v.blocked


@pytest.mark.parametrize("cmd", ["true || pytest", "pytest || true", "pytest &", "pytest --version"])
def test_une_commande_verte_qui_ne_lance_pas_le_test_est_refusee(projet: Path, cmd: str) -> None:
    verdict(projet, (cmd, True))
    v = check_transition(projet, tache(test_depth="standard"), "review", "accepted")
    assert v.blocked and _depth(v) and "floor" in _depth(v)[0]


def test_strict_refuse_des_segments_masques(projet: Path) -> None:
    verdict(projet, ("ruff check . || true && pytest -q || true", True))
    assert _depth(check_transition(projet, tache(test_depth="strict"), "review", "accepted"))


def test_transition_non_declaree_garde_la_profondeur(projet: Path) -> None:
    """La profondeur suit la tâche, même là où le fichier de gates ne déclare rien."""
    v = check_transition(projet, tache(test_depth="standard"), "in_progress", "accepted")
    assert v.transition_id == "test_depth" and v.blocked and _depth(v)
    assert check_transition(projet, tache(test_depth="standard"), "ready", "in_progress").refusals == ()


def test_profondeur_inconnue_ferme_la_porte(projet: Path) -> None:
    verdict(projet, ("pytest -q", True), ("mypy src", True))
    reasons = _depth(check_transition(projet, tache(test_depth="paranoid"), "review", "accepted"))
    assert reasons and "inconnue" in reasons[0]


def test_la_commande_de_test_declaree_du_projet_compte(projet: Path) -> None:
    (projet / "project-context.yaml").write_text(
        'project:\n  name: demo\nneeds:\n  commands:\n    test-runner: "./run-suite.sh"\n', encoding="utf-8",
    )
    verdict(projet, ("./run-suite.sh --fast", True))
    assert _depth(check_transition(projet, tache(test_depth="standard"), "review", "accepted")) == []


# ── Schéma, ledger, service, CLI ────────────────────────────────────────────

def test_aller_retour_du_schema() -> None:
    assert "test_depth" not in tache().to_dict()
    data = tache(test_depth="strict").to_dict()
    assert data["test_depth"] == "strict"
    assert MissionTask.from_dict(data).test_depth == "strict"
    # Une valeur d'une version plus récente se relit telle quelle.
    assert MissionTask.from_dict({**data, "test_depth": "paranoid"}).test_depth == "paranoid"


def test_le_service_pose_et_valide_la_profondeur(tmp_path: Path) -> None:
    service = TaskService(tmp_path, tmp_path / LEDGER)
    added = service.add("Fix the parser", ("les tests passent",), test_depth="standard")
    assert MissionLedger(tmp_path / LEDGER).get_task(added.task.id).test_depth == "standard"  # type: ignore[union-attr]
    with pytest.raises(GrimoireMissionError, match="test_depth"):
        service.add("Fix the parser", ("les tests passent",), test_depth="paranoid")


def test_cli_task_add_test_depth(tmp_path: Path) -> None:
    runner = CliRunner()
    base = ["--output", "json", "task", "add", "--project-root", str(tmp_path)]
    auto = runner.invoke(app, [*base, "Fix the parser", "-a", "les tests passent", "--check", "pytest",
                               "--test-depth", "auto"])
    assert auto.exit_code == 0, auto.output
    # Fichiers inconnus à la création : jamais sous `checked`, donc `standard`.
    assert json.loads(auto.stdout)["test_depth"] == "standard"
    refus = runner.invoke(app, [*base, "Fix the parser", "-a", "les tests passent", "--test-depth", "bof"])
    assert refus.exit_code == 1
