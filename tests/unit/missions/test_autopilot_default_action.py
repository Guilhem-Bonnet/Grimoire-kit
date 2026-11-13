"""GAO-pilote-appli-001 : ``autopilot.default_action`` — demander (défaut), ou appliquer l'option recommandée.

Critères : ``ask`` garde le comportement actuel (une confiance basse ou non déclarée laisse la question à l'humain) ;
``apply_recommended`` tranche par l'option recommandée d'une question hors liste réservée, même en confiance basse ou
absente, avec ``autopilot.decided`` de source ``default`` et le drapeau ``reversible`` ; sans option recommandée, ou
dans une catégorie réservée, la question reste à l'humain ; une valeur inconnue rend la politique invalide (échec
fermé) ; les questions de conduite écrites par le kit portent une option recommandée ; l'agent averti sans
``--recommended`` ; la route d'attention rend le bloc « Décisions à revoir », qui ne compte pas comme attente humaine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import mandate as md
from grimoire.missions.autopilot import (
    DECIDED_EVENT,
    DEFERRED_EVENT,
    AutopilotStore,
    load_autopilot_policy,
)
from grimoire.missions.ledger_rules import deferred_waiting
from grimoire.missions.ledger_views import LeaseView
from grimoire.missions.mandate import MandateRequest, MandateStore
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT
from grimoire.tools import workspace_attention as wa
from grimoire.tools import workspace_reviews

ACC = ("le test passe",)
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
PROV = {"kind": "rule", "ref": "critere-1"}
WHY = "les critères de la tâche disent cette voie"
RECOMMENDED_POLICY = "autopilot:\n  default_action: apply_recommended\n"

runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        md.PRINCIPAL_ENV,
        md.PROVENANCE_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture(autouse=True)
def _tick() -> Any:
    """L'appelant est le code du tick (``acting_as_orchestrator``), pas une session d'agent."""
    with md.acting_as_orchestrator():
        yield


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


def grant(root: Path) -> md.Mandate:
    request = MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d")
    return MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")


def pilot_on(root: Path) -> None:
    AutopilotStore(root).set(True, origin=ORIGIN_HUMAN, via="test")


def write_policy(root: Path, body: str) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(body, encoding="utf-8")


def applies_recommended(root: Path) -> None:
    """Active le pilote avec ``default_action: apply_recommended``."""
    write_policy(root, RECOMMENDED_POLICY)
    grant(root)
    pilot_on(root)


def asks(root: Path, text: str = "Quelle voie ?", *, title: str = "Correctif", **ask: Any) -> tuple[TaskService, str, str]:
    service = TaskService(root)
    tid = service.add(title, ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    service.ask(tid, text, "w", **ask)
    return service, tid, service.require(tid).waiting.question.id  # type: ignore[union-attr]


def answer(service: TaskService, tid: str, qid: str, text: str, **kw: Any) -> Any:
    return service.answer(tid, text, "orch", origin=ORIGIN_ORCHESTRATOR, question_id=qid, provenance=PROV, **kw)


def events(service: TaskService, tid: str, kind: str) -> list[Any]:
    return [e for e in service.ledger.events_for(tid) if e.event_type == kind]


# ── La politique : lue, bornée, échec fermé ─────────────────────────────────


def test_default_action_vaut_ask_sans_reglage(project: Path) -> None:
    assert load_autopilot_policy(project).default_action == "ask"


def test_la_politique_accepte_apply_recommended_et_refuse_une_valeur_inconnue(project: Path) -> None:
    write_policy(project, RECOMMENDED_POLICY)
    policy = load_autopilot_policy(project)
    assert not policy.error and policy.default_action == "apply_recommended"

    write_policy(project, "autopilot:\n  default_action: always\n")
    policy = load_autopilot_policy(project)
    assert policy.error and "default_action" in policy.error

    write_policy(project, "autopilot:\n  default_action: 3\n")
    assert load_autopilot_policy(project).error


def test_une_politique_invalide_coupe_le_pilote(project: Path) -> None:
    write_policy(project, "autopilot:\n  default_action: always\n")
    grant(project)
    with pytest.raises(md.MandateRefusedError, match="pilote"):
        AutopilotStore(project).set(True, origin=ORIGIN_HUMAN, via="test")
    assert not AutopilotStore(project).state().active


# ── `ask` : le comportement actuel ──────────────────────────────────────────


@pytest.mark.parametrize("confidence", ["low", ""])
def test_ask_laisse_une_confiance_basse_ou_absente_a_l_humain(project: Path, confidence: str) -> None:
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(project, options=("A", "B"), recommended="B", why="plus simple")
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "A", justification=WHY, confidence=confidence)
    assert service.require(tid).status is TaskState.WAITING
    assert events(service, tid, DEFERRED_EVENT) and not events(service, tid, DECIDED_EVENT)


# ── `apply_recommended` : l'option recommandée tranche ──────────────────────


@pytest.mark.parametrize("confidence", ["low", ""])
def test_apply_recommended_prend_l_option_recommandee_meme_sans_confiance(
    project: Path, confidence: str
) -> None:
    applies_recommended(project)
    service, tid, qid = asks(project, options=("A", "B"), recommended="B", why="plus simple")
    move = answer(service, tid, qid, "A", confidence=confidence)
    assert move.task.status is TaskState.READY
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "B" and decided.payload["question_id"] == qid
    assert decided.payload["recommendation_source"] == "default"
    assert decided.payload["reversible"] is True
    assert decided.payload["confidence"] == (confidence or "low")
    (given,) = events(service, tid, HUMAN_ANSWERED_EVENT)
    assert given.payload["answer"] == "B"  # la décision, pas le texte soumis


def test_apply_recommended_sans_option_recommandee_laisse_la_question_a_l_humain(project: Path) -> None:
    applies_recommended(project)
    service, tid, qid = asks(project, options=("A", "B"))
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "A", justification=WHY, confidence="low")
    assert service.require(tid).status is TaskState.WAITING
    assert events(service, tid, DEFERRED_EVENT) and not events(service, tid, DECIDED_EVENT)


def test_apply_recommended_ne_tranche_pas_une_categorie_reservee(project: Path) -> None:
    applies_recommended(project)
    service, tid, qid = asks(project, "Faut-il payer l'abonnement du fournisseur ?", options=("oui", "non"),
                             recommended="oui")
    with pytest.raises(GrimoireMissionError, match="réservée à l'humain"):
        answer(service, tid, qid, "oui", confidence="low")
    assert service.require(tid).status is TaskState.WAITING
    assert not events(service, tid, DECIDED_EVENT)


def test_apply_recommended_ne_remplace_pas_un_jugement_assume(project: Path) -> None:
    """Une confiance ``high`` qui diverge reste un jugement : l'action par défaut ne l'écrase pas."""
    applies_recommended(project)
    service, tid, qid = asks(project, options=("A", "B"), recommended="A")
    answer(service, tid, qid, "B", justification=WHY, confidence="high")
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "B" and decided.payload["recommendation_source"] == "judgment"


# ── Les questions écrites par le kit portent une recommandation ─────────────


@pytest.mark.parametrize(
    ("cause", "pick"),
    [("retry_exhausted", "réessayer"), ("resume_branch", "reprendre"), ("resume_unfenced", "reprendre")],
)
def test_les_questions_de_conduite_du_kit_recommandent_la_reprise(project: Path, cause: str, pick: str) -> None:
    service, tid, _qid = asks(project, options=("reprendre", "abandonner"))
    task = service.require(tid)
    waiting = deferred_waiting(
        task, LeaseView(tid, "lost", "", 1, "w", "claude"), cause, 3, "Reprendre ?", datetime.now(UTC)
    )
    assert waiting.question is not None and waiting.question.recommended == pick


def test_une_tache_garee_sur_erreur_de_fournisseur_recommande_de_reessayer(project: Path) -> None:
    from grimoire.missions.launcher import _park_provider_error

    service = TaskService(project)
    tid = service.add("Correctif", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    task = service.require(tid)
    assert task.claim is not None
    assert _park_provider_error(service, task, "overloaded", 2)
    question = service.require(tid).waiting.question  # type: ignore[union-attr]
    assert question is not None and question.recommended == "réessayer" and question.options == ("réessayer", "abandonner")


# ── La CLI avertit une session d'agent sans option recommandée ──────────────


def test_cli_ask_avertit_un_agent_qui_ne_recommande_rien(project: Path) -> None:
    service = TaskService(project)
    tid = service.add("Correctif", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    result = runner.invoke(app, [
        "task", "ask", tid, "Rendre 404 ou 410 ?", "-o", "404", "-o", "410",
        "--project-root", str(project), "--actor", "w",
    ])
    assert result.exit_code == 0, result.output
    assert "recommandée" in result.output and "--recommended" in result.output


def test_cli_ask_sans_avertissement_quand_l_option_est_recommandee(project: Path) -> None:
    service = TaskService(project)
    tid = service.add("Correctif", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    result = runner.invoke(app, [
        "task", "ask", tid, "Rendre 404 ou 410 ?", "-o", "404", "-o", "410", "--recommended", "404",
        "--project-root", str(project), "--actor", "w",
    ])
    assert result.exit_code == 0, result.output
    assert "sans option recommandée" not in result.output


# ── Le cockpit : « Décisions à revoir », hors de l'attente humaine ──────────


def test_la_route_d_attention_rend_le_bloc_des_decisions_par_defaut(project: Path) -> None:
    applies_recommended(project)
    service, tid, qid = asks(
        project, "Rendre 404 ou 410 ?", options=("404", "410"), recommended="410", why="les critères disent 410"
    )
    answer(service, tid, qid, "404", confidence="low")

    view = wa.attention_view(
        project,
        pr_reader=lambda repo, cwd: [],
        review_cache=workspace_reviews.ReviewCache(),
        pr_wait=1.0,
    )
    block = view["defaultDecisions"]
    assert block["label"] == "Décisions à revoir" and block["count"] == 1
    (item,) = block["items"]
    assert item["question_id"] == qid and item["option"] == "410"
    assert item["question"] and "les critères disent 410" in item["justification"]
    # Non bloquant : aucune décision par défaut dans les sections d'attention, ni dans le compte des gestes.
    assert not any(i["kind"] == wa.DEFAULT_DECISION for section in view["sections"] for i in section["items"])
    assert wa.DEFAULT_DECISION not in view["counts"]
