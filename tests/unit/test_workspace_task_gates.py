"""Ce que l'inspecteur de tâche montre avant une transition (P11), prouvé sur un vrai ledger.

Chaque test nomme le défaut qu'il empêche : un passage « prêt » que la clôture
refuse ensuite (reçu de revue oublié), un refus passé invisible hors de la
timeline, un modèle recommandé deviné par la vue, un journal illisible qui
casserait l'inspecteur.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from grimoire.tools import workspace_api as wa
from grimoire.tools import workspace_task_gates as wtg

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["acceptance_criteria", "review_gate"]
  - id: review_to_in_progress
    from: review
    to: in_progress
    required_evidence: ["decision_trace"]
profile_strictness:
  governed: hard_fail
"""


@pytest.fixture
def projet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    gates = tmp_path / "_grimoire" / "standard" / "evidence-gates.yaml"
    gates.parent.mkdir(parents=True)
    gates.write_text(GATES, encoding="utf-8")
    return tmp_path.resolve()


def _en_revue(root: Path, critere: str = "un critère observable") -> str:
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(root)
    task_id = service.add("Une tâche en revue", (critere,), actor="t", owner="g", ready=True).task.id
    service.claim(task_id, "agent-a", host="host-claude-code-cli")
    service.transition(task_id, TaskState.RUNNING, "agent-a")
    service.transition(task_id, TaskState.NEEDS_VERIFICATION, "agent-a")
    return task_id


def _refuse_la_cloture(root: Path, task_id: str) -> None:
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskRefusedError, TaskService

    with pytest.raises(TaskRefusedError):
        TaskService(root).transition(task_id, TaskState.CLOSED, "agent-a")


# ── Routage : classe, modèle, palier ────────────────────────────────────────


@pytest.mark.parametrize(
    ("critere", "classe", "modele", "palier"),
    [
        ("`pytest tests/unit/test_x.py` est vert", "V1", "sonnet", "mid"),
        ("un critère observable", "V2", None, "session"),
    ],
)
def test_la_carte_porte_classe_modele_et_palier_du_meme_calcul_que_dispatch(
    projet: Path, critere: str, classe: str, modele: str | None, palier: str
) -> None:
    """Sans ces champs, la vue devinait le modèle (ou affichait « None ») au lieu de lire `dispatch_advice`."""
    from grimoire.missions.dispatch_advice import dispatch_advice
    from grimoire.missions.service import TaskService

    task_id = _en_revue(projet, critere)
    task = TaskService(projet).require(task_id)
    card = next(t for t in wa.tasks_view(projet)["tasks"] if t["id"] == task_id)
    advice = dispatch_advice(task)

    assert (card["verifiability_class"], card["recommended_model"], card["model_tier"]) == (classe, modele, palier)
    assert card["recommended_model"] == advice["recommended_model"]
    assert card["model_tier"] == advice["model_tier"]
    assert wtg.routing_fields(task) == {
        "verifiability_class": classe, "recommended_model": modele, "model_tier": palier,
    }


# ── Gates : chaque passage déclaré, le reçu de revue compris ────────────────


def test_chaque_passage_declare_porte_ses_conditions_et_la_cloture_exige_le_recu(projet: Path) -> None:
    """Le cockpit montrait « review → accepted » sans le reçu de revue que `TaskService.gate` exige."""
    task_id = _en_revue(projet)

    detail = wa.task_view(projet, task_id)

    assert set(detail["gate_checks"]) == set(detail["next_moves_require"]) == {"accepted", "in_progress"}
    accepted = detail["gate_checks"]["accepted"]
    assert accepted["transition_id"] == "review_receipt"
    by_gate = {c["gate"]: c for c in accepted["conditions"]}
    assert list(by_gate) == ["acceptance_criteria", "review_gate", "review.receipt_missing"]
    assert by_gate["acceptance_criteria"]["ok"] is True
    for name in ("review_gate", "review.receipt_missing"):
        assert by_gate[name]["ok"] is False and by_gate[name]["reason"] and by_gate[name]["remedy"]
    back = detail["gate_checks"]["in_progress"]["conditions"]
    assert [(c["gate"], c["ok"]) for c in back] == [("decision_trace", False)]
    # La carte et l'inspecteur lisent le même verdict pour le passage suivant.
    card = next(t for t in wa.tasks_view(projet)["tasks"] if t["id"] == task_id)
    assert [c["gate"] for c in card["next_gate"]["conditions"]] == list(by_gate)
    # Jamais tentée : aucun refus inventé.
    assert detail["last_gate_refusal"] is None
    assert all(c["refused_at"] == "" for entry in detail["gate_checks"].values() for c in entry["conditions"])


def test_le_dernier_refus_est_lu_et_date_les_gates_qu_il_a_nommes(projet: Path) -> None:
    """Un refus passé ne se lisait que dans la timeline : l'humain rejouait le même geste à l'aveugle."""
    task_id = _en_revue(projet)
    _refuse_la_cloture(projet, task_id)

    detail = wa.task_view(projet, task_id)

    refusal = detail["last_gate_refusal"]
    assert refusal["to"] == "accepted"
    assert refusal["transition_id"] == "review_receipt"
    assert set(refusal["missing"]) == {"review_gate", "review.receipt_missing"}
    assert refusal["actor"] == "agent-a"
    accepted = {c["gate"]: c["refused_at"] for c in detail["gate_checks"]["accepted"]["conditions"]}
    assert accepted == {"acceptance_criteria": "", "review_gate": refusal["at"], "review.receipt_missing": refusal["at"]}
    # Un refus de la clôture ne date pas les conditions d'un autre passage.
    assert detail["gate_checks"]["in_progress"]["conditions"][0]["refused_at"] == ""


def test_sans_journal_ou_avec_un_journal_illisible_aucun_refus_n_est_rendu(
    projet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le TraceLedger est best-effort : son absence ou sa panne ne casse jamais l'inspecteur."""
    assert wtg.last_gate_refusal(projet, "GAO-inexistante-001") is None

    task_id = _en_revue(projet)
    _refuse_la_cloture(projet, task_id)
    import grimoire.traces.ledger as ledger

    def _panne(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("journal illisible")

    monkeypatch.setattr(ledger.TraceLedger, "list_traces", _panne)
    assert wtg.last_gate_refusal(projet, task_id) is None


def test_un_refus_sans_trace_de_cette_tache_n_est_pas_rendu(projet: Path) -> None:
    other = _en_revue(projet)
    _refuse_la_cloture(projet, other)
    assert wtg.last_gate_refusal(projet, "GAO-autre-tache-001") is None


def test_la_colonne_visee_se_lit_dans_action_kind_et_reste_vide_sinon() -> None:
    def verdict(kind: str) -> SimpleNamespace:
        return SimpleNamespace(action_kind=kind)

    assert wtg.refused_target([verdict("task.transition:needs_verification->closed")]) == "accepted"
    assert wtg.refused_target([verdict("task.transition:running->etat-inconnu")]) == ""
    assert wtg.refused_target([verdict("autre")]) == ""
    assert wtg.refused_target([]) == ""


def test_un_fichier_de_gates_illisible_ne_rend_aucun_passage(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import grimoire.missions.gates as gates
    from grimoire.core.exceptions import GrimoireError

    task_id = _en_revue(projet)

    def _illisible(_root: Path) -> dict[tuple[str, str], dict[str, object]]:
        raise GrimoireError("fichier de gates illisible")

    monkeypatch.setattr(gates, "declared_transitions", _illisible)
    from grimoire.missions.service import TaskService

    task = TaskService(projet).require(task_id)
    assert wtg.gate_checks(projet, task, "review", ["accepted"], None) == {}


def test_un_verdict_illisible_retire_le_passage_au_lieu_de_l_afficher_vert(
    projet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import grimoire.missions.gates as gates
    from grimoire.missions.service import TaskService

    task_id = _en_revue(projet)
    task = TaskService(projet).require(task_id)

    def _verdict_illisible(*_args: object, **_kwargs: object) -> object:
        raise OSError("preuve illisible")

    monkeypatch.setattr(gates, "check_transition", _verdict_illisible)
    assert wtg.gate_checks(projet, task, "review", ["accepted", "in_progress"], None) == {}
