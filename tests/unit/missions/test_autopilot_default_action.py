"""GAO-pilote-appli-001 : ``autopilot.default_action`` — demander (défaut), ou appliquer l'option recommandée puis informer.

Critères : sans réglage (``ask``) rien ne change, y compris pour les questions que le kit écrit et qui portent désormais
une option recommandée ; ``apply_recommended`` tranche par l'option recommandée d'une question hors liste fermée même en
confiance basse ou absente (``autopilot.decided`` de source ``default``, drapeau ``reversible``) ; sans option
recommandée, ou dans une catégorie réservée, la question reste à l'humain ; une valeur inconnue rend la politique
invalide (échec fermé) ; ``task ask`` avertit l'agent qui ne recommande rien ; la route d'attention rend le bloc
« Décisions à revoir », hors de toute attente de l'humain.

Sûreté, éprouvée ici : une décision par défaut n'applique que l'une des options de la question, jamais « abandonner »,
ni une fusion ni une permission ; une question écrite par un agent ne sort pas de la liste fermée en imitant une question
du kit ; les décisions par défaut d'une tâche sont bornées ; une reprise n'est pas prise sur un worker au travail.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    default_option,
    load_autopilot_policy,
)
from grimoire.missions.autopilot_view import revert
from grimoire.missions.ledger_rules import deferred_waiting
from grimoire.missions.ledger_views import LeaseView
from grimoire.missions.mandate import MandateRequest, MandateStore
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.orchestrator_session import (
    MAX_DEFAULT_DECISIONS,
    OrchestratorStage,
    eligible_questions,
    kit_written,
)
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT, Choice, Question
from grimoire.tools import workspace_attention as wa
from grimoire.tools import workspace_reviews

ACC = ("le test passe",)
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
PROV = {"kind": "rule", "ref": "critere-1"}
WHY = "les critères de la tâche disent cette voie"
APPLY = "autopilot:\n  default_action: apply_recommended\n"
#: Un titre de tâche qui touche la liste fermée (dépense) sans que la question en parle (#1159).
TARIFF = "Tarif par modèle dans le registre pour chiffrer un worker routé"
REVIEW_ESCALATED = "task.review_escalated"
MERGE_ESCALATED = "task.merge_escalated"

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


def write_policy(root: Path, body: str) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(body, encoding="utf-8")


def pilot(root: Path, policy: str = "") -> None:
    """Mandat et pilote actifs ; *policy* : le corps de ``orchestration-policy.yaml`` (vide : aucun réglage)."""
    if policy:
        write_policy(root, policy)
    request = MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d")
    MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")
    AutopilotStore(root).set(True, origin=ORIGIN_HUMAN, via="test")


def running(root: Path, title: str = "Correctif") -> tuple[TaskService, str]:
    service = TaskService(root)
    tid = service.add(title, ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    return service, tid


def asks(root: Path, text: str = "Quelle voie ?", *, title: str = "Correctif", **ask: Any) -> tuple[TaskService, str, str]:
    """Une tâche qui attend la réponse à une question posée par ``task ask`` : ``(service, tâche, question)``."""
    service, tid = running(root, title)
    service.ask(tid, text, "w", **ask)
    return service, tid, service.require(tid).waiting.question.id  # type: ignore[union-attr]


def escalated(
    root: Path, event: str = REVIEW_ESCALATED, *, title: str = "Correctif", text: str = "", **ask: Any
) -> tuple[TaskService, str, str]:
    """La question « reprendre ou abandonner ? » telle que le relecteur ou le merger l'écrit : nommée par son
    événement d'escalade (ce que seul le code du kit écrit), puis posée avec son option recommandée."""
    service, tid = running(root, title)
    qid = "q-escalade-1"
    service.ledger.append_event(event, tid, "task", "tick", {"task_id": tid, "question_id": qid, "why": "2 retours"})
    service.ask(
        tid, text or f"Désaccord persistant sur {tid} (2 relectures NOT-READY) — reprendre la tâche ou l'abandonner ?",
        "tick", options=("reprendre", "abandonner"), recommended="reprendre", question_id=qid, **ask,
    )
    return service, tid, qid


def answer(service: TaskService, tid: str, qid: str, text: str, **kw: Any) -> Any:
    return service.answer(tid, text, "orch", origin=ORIGIN_ORCHESTRATOR, question_id=qid, provenance=PROV, **kw)


def events(service: TaskService, tid: str, kind: str) -> list[Any]:
    return [e for e in service.ledger.events_for(tid) if e.event_type == kind]


def stage(service: TaskService, root: Path) -> OrchestratorStage:
    return OrchestratorStage(service, load_orchestrator_policy(root).policy)  # type: ignore[arg-type]


def question(**fields: Any) -> Question:
    return replace(Question("q-1", "choice", "Quelle voie ?", ("A", "B"), recommended="B"), **fields)


# ── Le réglage : lu, borné, échec fermé ─────────────────────────────────────


def test_sans_reglage_l_action_par_defaut_est_ask(project: Path) -> None:
    assert load_autopilot_policy(project).default_action == "ask"
    write_policy(project, "autopilot:\n  reserved: [legal]\n")
    policy = load_autopilot_policy(project)
    assert not policy.error and policy.default_action == "ask" and policy.extra == ("legal",)


def test_apply_recommended_est_lu_et_garde_les_reservations(project: Path) -> None:
    write_policy(project, "autopilot:\n  reserved: [legal]\n  default_action: apply_recommended\n")
    policy = load_autopilot_policy(project)
    assert not policy.error and policy.default_action == "apply_recommended" and policy.extra == ("legal",)


@pytest.mark.parametrize("value", ["always", "Apply_Recommended", "apply", "3", "true", "", "[apply_recommended]", "~"])
def test_une_valeur_inconnue_rend_la_politique_invalide(project: Path, value: str) -> None:
    write_policy(project, f"autopilot:\n  default_action: {value}\n")
    policy = load_autopilot_policy(project)
    assert policy.error and "default_action" in policy.error
    assert policy.default_action == "ask"  # une politique invalide n'applique rien par défaut


def test_une_politique_invalide_ne_laisse_ni_activer_le_pilote_ni_decider(project: Path) -> None:
    write_policy(project, "autopilot:\n  default_action: always\n")
    request = MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d")
    MandateStore(project).grant(request, origin=ORIGIN_HUMAN, via="test")
    with pytest.raises(md.MandateRefusedError, match="pilote"):
        AutopilotStore(project).set(True, origin=ORIGIN_HUMAN, via="test")
    assert not AutopilotStore(project).state().active


def test_une_politique_devenue_invalide_reserve_tout_pilote_actif(project: Path) -> None:
    pilot(project, APPLY)
    service, tid, qid = asks(project, options=("A", "B"), recommended="B")
    write_policy(project, "autopilot:\n  default_action: always\n")
    with pytest.raises(GrimoireMissionError, match="politique du pilote invalide"):
        answer(service, tid, qid, "A", confidence="low")
    assert stage(service, project)._apply_recommendations({}) == []
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


# ── `ask`, ou aucun réglage : le comportement d'avant ───────────────────────


@pytest.mark.parametrize("policy", ["", "autopilot:\n  default_action: ask\n"])
@pytest.mark.parametrize("confidence", ["low", ""])
def test_ask_laisse_une_confiance_basse_ou_absente_a_l_humain(project: Path, policy: str, confidence: str) -> None:
    pilot(project, policy)
    service, tid, qid = asks(project, options=("A", "B"), recommended="B", why="plus simple")
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "A", justification=WHY, confidence=confidence)
    assert service.require(tid).status is TaskState.WAITING
    assert events(service, tid, DEFERRED_EVENT) and not events(service, tid, DECIDED_EVENT)


@pytest.mark.parametrize("policy", ["", "autopilot:\n  default_action: ask\n"])
def test_ask_la_recommandation_d_un_agent_est_toujours_prise_par_le_tick(project: Path, policy: str) -> None:
    pilot(project, policy)
    service, tid, _qid = asks(project, options=("A", "B"), recommended="B", why="plus simple")
    (outcome,) = stage(service, project)._apply_recommendations({})
    assert outcome.status == "done"
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "B" and decided.payload["recommendation_source"] == "recommended"


@pytest.mark.parametrize("policy", ["", "autopilot:\n  default_action: ask\n"])
@pytest.mark.parametrize("event", [REVIEW_ESCALATED, MERGE_ESCALATED])
def test_ask_l_option_par_defaut_d_une_question_du_kit_n_engage_pas_le_pilote(
    project: Path, policy: str, event: str
) -> None:
    """Rien ne change sans le réglage : le tick ne l'applique pas, la session ne la reçoit pas et juge comme avant."""
    pilot(project, policy)
    service, tid, qid = escalated(project, event)
    assert stage(service, project)._apply_recommendations({}) == []
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)
    (open_question,) = eligible_questions(service, MandateStore(project))
    assert open_question.question_id == qid and open_question.recommended == ""
    # Répondre l'option recommandée sans rien assumer ne décide rien ; l'assumer est un jugement, comme avant.
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "reprendre")
    answer(service, tid, qid, "reprendre", justification=WHY, confidence="high")
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["recommendation_source"] == "judgment"


def test_sans_pilote_le_reglage_ne_decide_rien(project: Path) -> None:
    write_policy(project, APPLY)
    request = MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d")
    MandateStore(project).grant(request, origin=ORIGIN_HUMAN, via="test")
    service, tid, _qid = escalated(project, MERGE_ESCALATED)
    assert stage(service, project)._apply_recommendations({}) == []
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


# ── `apply_recommended` : l'option recommandée tranche, puis informe ────────


@pytest.mark.parametrize("confidence", ["low", ""])
def test_apply_recommended_tranche_par_l_option_recommandee_sans_confiance(project: Path, confidence: str) -> None:
    pilot(project, APPLY)
    service, tid, qid = asks(project, options=("A", "B"), recommended="B", why="plus simple")
    move = answer(service, tid, qid, "A", justification=WHY, confidence=confidence)
    assert move.task.status is TaskState.READY
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "B" and decided.payload["question_id"] == qid
    assert decided.payload["recommendation_source"] == "default" and decided.payload["reversible"] is True
    assert decided.payload["confidence"] == (confidence or "low")
    # La justification est celle de l'option appliquée, jamais celle de la réponse écartée.
    assert decided.payload["justification"] == "plus simple" and WHY not in decided.payload["justification"]
    (given,) = events(service, tid, HUMAN_ANSWERED_EVENT)
    assert given.payload["answer"] == "B"  # la décision, pas le texte soumis
    assert not events(service, tid, DEFERRED_EVENT)
    assert any("B" in d.text for d in service.require(tid).directives)  # le worker reçoit l'option appliquée


def test_apply_recommended_ecarte_aussi_les_donnees_de_choix_de_la_reponse_soumise(project: Path) -> None:
    """Le ledger ne dit pas « B » en texte et « A » en choix structuré : seule la décision appliquée est écrite."""
    pilot(project, APPLY)
    service, tid, qid = asks(
        project, title="Correctif", choices=(Choice("A", "Voie A"), Choice("B", "Voie B")), recommended="B"
    )
    answer(service, tid, qid, "A", confidence="low", answer_data={"choices": ["A"]})
    (given,) = events(service, tid, HUMAN_ANSWERED_EVENT)
    assert given.payload["answer"] == "B" and "answer_data" not in given.payload


def test_apply_recommended_sans_option_recommandee_la_question_reste_a_l_humain(project: Path) -> None:
    pilot(project, APPLY)
    service, tid, qid = asks(project, options=("A", "B"))
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "A", justification=WHY, confidence="low")
    assert stage(service, project)._apply_recommendations({}) == []
    assert service.require(tid).status is TaskState.WAITING
    assert events(service, tid, DEFERRED_EVENT) and not events(service, tid, DECIDED_EVENT)


@pytest.mark.parametrize(
    "text",
    [
        "Faut-il payer l'abonnement du fournisseur ?",
        "Renouveler le token de publication ?",
        "Révoquer le mandat de l'orchestrateur ?",
        "Modifier .github/workflows/release.yml ?",
        "Supprimer les données des utilisateurs de test ?",
    ],
)
def test_apply_recommended_ne_tranche_jamais_une_categorie_reservee(project: Path, text: str) -> None:
    pilot(project, APPLY)
    service, tid, qid = asks(project, text, options=("oui", "non"), recommended="oui")
    with pytest.raises(GrimoireMissionError, match="réservée à l'humain"):
        answer(service, tid, qid, "oui", confidence="low")
    assert stage(service, project)._apply_recommendations({}) == []
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


def test_apply_recommended_ne_remplace_pas_un_jugement_assume(project: Path) -> None:
    pilot(project, APPLY)
    service, tid, qid = asks(project, options=("A", "B"), recommended="A")
    answer(service, tid, qid, "B", justification=WHY, confidence="high")
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "B" and decided.payload["recommendation_source"] == "judgment"


def test_apply_recommended_un_jugement_sans_justification_n_est_pas_rattrape_par_defaut(project: Path) -> None:
    pilot(project, APPLY)
    service, tid, qid = asks(project, options=("A", "B"), recommended="A")
    with pytest.raises(GrimoireMissionError, match="justification"):
        answer(service, tid, qid, "B", confidence="high")
    assert not events(service, tid, DECIDED_EVENT)


# ── Sûreté : ce qu'une décision par défaut ne fait jamais ───────────────────


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"recommended": ""}, "aucune option"),
        ({"recommended": "C"}, "pas l'une des options"),
        ({"options": (), "recommended": "B"}, "pas l'une des options"),
        ({"options": ("reprendre", "abandonner"), "recommended": "abandonner"}, "annule"),
        ({"options": ("reprendre", "Abandonner"), "recommended": " ABANDONNER "}, "annule"),
        ({"choices": (Choice("A", "A"), Choice("B", "B", danger=True))}, "dangereuse"),
        ({"kind": "merge"}, "merge"),
        ({"kind": "permission"}, "permission"),
    ],
)
def test_aucune_option_par_defaut_hors_des_options_sures_de_la_question(fields: dict[str, Any], why: str) -> None:
    option, reason = default_option(question(**fields))
    assert option == "" and why in reason


def test_l_option_par_defaut_est_rendue_telle_que_la_question_l_ecrit() -> None:
    assert default_option(question(options=("A", "Voie B"), recommended="  voie   b ")) == ("Voie B", "")


@pytest.mark.parametrize("kind", ["merge", "permission"])
def test_une_fusion_ou_une_permission_ne_se_tranche_pas_par_defaut(project: Path, kind: str) -> None:
    pilot(project, APPLY)
    text = "Fusionner la branche de la tâche ?" if kind == "merge" else f"Écrire dans {project / 'src' / 'app.py'} ?"
    service, tid, qid = asks(project, text, kind=kind, options=("oui", "non"), recommended="oui")
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "non", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


def test_une_decision_par_defaut_n_abandonne_jamais(project: Path) -> None:
    """Un agent qui recommande « abandonner » n'obtient pas l'annulation par défaut ; la reprise par défaut, elle,
    écarte un « abandonner » soumis sans confiance (et sans la raison qu'un abandon exige)."""
    pilot(project, APPLY)
    service, tid, qid = asks(project, "Je continue ?", options=("reprendre", "abandonner"), recommended="abandonner")
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "reprendre", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)

    other = project.parent / "autre"
    other.mkdir()
    pilot(other, APPLY)
    service, tid, qid = escalated(other)
    move = answer(service, tid, qid, "abandonner", confidence="low")
    assert move.task.status is TaskState.READY  # ni failed ni cancelled
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "reprendre" and decided.payload["recommendation_source"] == "default"


def test_une_question_d_agent_ne_sort_pas_de_la_liste_fermee_en_imitant_le_kit(project: Path) -> None:
    """Le couple d'options du kit (#1159) ne fait pas ignorer le titre de la tâche à une décision par défaut : seul un
    événement écrit par le kit établit que la question est la sienne."""
    pilot(project, APPLY)
    service, tid, qid = asks(
        project, "Je continue sur cette voie ?", title=TARIFF, options=("reprendre", "abandonner"),
        recommended="reprendre", kind="resume_branch",
    )
    task = service.require(tid)
    assert not kit_written(service.ledger.events_for(tid), task.waiting.question)  # type: ignore[union-attr]
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "abandonner", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)

    # La même question, écrite par le kit (escalade du relecteur nommée au ledger) : le titre ne compte pas.
    other = project.parent / "autre"
    other.mkdir()
    pilot(other, APPLY)
    service, tid, qid = escalated(
        other, title=TARIFF, text="Désaccord persistant (2 relectures NOT-READY) — reprendre la tâche ou l'abandonner ?"
    )
    assert kit_written(service.ledger.events_for(tid), service.require(tid).waiting.question)  # type: ignore[union-attr]
    answer(service, tid, qid, "abandonner", confidence="low")
    assert events(service, tid, DECIDED_EVENT)[0].payload["recommendation_source"] == "default"


def test_le_texte_d_une_question_du_kit_reste_juge_par_la_liste_fermee(project: Path) -> None:
    """Le motif d'une escalade recopie des constats (relecteur, CI) : s'il touche la liste fermée, rien par défaut."""
    pilot(project, APPLY)
    service, tid, qid = escalated(
        project, text="Désaccord persistant (le relecteur demande de régénérer le token d'API) — reprendre ou abandonner ?"
    )
    assert stage(service, project)._apply_recommendations({}) == []
    with pytest.raises(GrimoireMissionError, match="réservée à l'humain"):
        answer(service, tid, qid, "reprendre", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


def test_les_decisions_par_defaut_d_une_tache_sont_bornees(project: Path) -> None:
    """Une escalade du merger se répète (son geste n'est pas une reprise bornée) : après la borne, plus rien par défaut."""
    pilot(project, APPLY)
    service, tid = running(project)
    for n in range(MAX_DEFAULT_DECISIONS + 1):
        qid = f"q-escalade-{n}"
        service.ledger.append_event(MERGE_ESCALATED, tid, "task", "tick", {"task_id": tid, "question_id": qid})
        service.ask(tid, "La fusion échoue à répétition — reprendre la tâche ou l'abandonner ?", "tick",
                    options=("reprendre", "abandonner"), recommended="reprendre", question_id=qid)
        outcomes = stage(service, project)._apply_recommendations({})
        if n < MAX_DEFAULT_DECISIONS:
            assert [o.status for o in outcomes] == ["done"]
            service.claim(tid, "w")
            service.transition(tid, TaskState.RUNNING, "w")
    assert outcomes == [] and service.require(tid).status is TaskState.WAITING
    assert len(events(service, tid, DECIDED_EVENT)) == MAX_DEFAULT_DECISIONS
    with pytest.raises(GrimoireMissionError, match="confiance"):  # la session juge alors comme sous `ask`
        answer(service, tid, qid, "reprendre", confidence="low")


def test_une_reprise_par_defaut_attend_que_le_worker_ne_travaille_plus(project: Path) -> None:
    pilot(project, APPLY)
    service, tid, _qid = escalated(project)
    service.ledger.append_event(
        "task.session_launched", tid, "task", "tick", {"task_id": tid, "session": "abcd1234", "session_id": "s-1"}
    )
    orchestrator = stage(service, project)
    assert orchestrator._apply_recommendations(None) == []  # `claude agents` illisible : rien n'est repris
    assert orchestrator._apply_recommendations({"abcd1234": "busy"}) == []
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)
    (outcome,) = orchestrator._apply_recommendations({"abcd1234": "idle"})
    assert outcome.status == "done" and "par défaut" in outcome.detail
    assert service.require(tid).status is TaskState.READY


# ── Les questions écrites par le kit portent une option recommandée ─────────


@pytest.mark.parametrize(
    ("cause", "pick"),
    [("retry_exhausted", "réessayer"), ("resume_branch", "reprendre"), ("resume_unfenced", "reprendre")],
)
def test_les_questions_de_la_recolte_recommandent_de_reprendre(project: Path, cause: str, pick: str) -> None:
    service, tid = running(project)
    waiting = deferred_waiting(
        service.require(tid), LeaseView(tid, "lost", "", 1, "w", "claude"), cause, 3, "Reprendre ?", datetime.now(UTC)
    )
    asked = waiting.question
    assert asked is not None and asked.recommended == pick and asked.recommended in asked.options
    assert asked.origin == "tick" and kit_written([], asked)
    assert default_option(asked) == (pick, "")


def test_une_tache_garee_sur_erreur_de_fournisseur_recommande_de_reessayer(project: Path) -> None:
    from grimoire.missions.launcher import _park_provider_error

    service, tid = running(project)
    assert _park_provider_error(service, service.require(tid), "overloaded", 2)
    asked = service.require(tid).waiting.question  # type: ignore[union-attr]
    assert asked is not None and asked.recommended == "réessayer" and asked.options == ("réessayer", "abandonner")
    assert kit_written(service.ledger.events_for(tid), asked)


@pytest.mark.parametrize("event", [REVIEW_ESCALATED, MERGE_ESCALATED])
def test_apply_recommended_le_tick_applique_l_option_par_defaut_d_une_escalade(project: Path, event: str) -> None:
    pilot(project, APPLY)
    service, tid, qid = escalated(project, event)
    (outcome,) = stage(service, project)._apply_recommendations({})
    assert outcome.status == "done" and f"{tid}/{qid}" in outcome.questions
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "reprendre" and decided.payload["recommendation_source"] == "default"
    assert decided.payload["reversible"] is True and decided.payload["justification"]
    (given,) = events(service, tid, HUMAN_ANSWERED_EVENT)
    assert given.payload["provenance"] == {"kind": "rule", "ref": "autopilot-default"}
    assert service.require(tid).status is TaskState.READY


# ── La CLI avertit l'agent qui ne recommande rien ───────────────────────────


def ask_cli(project: Path, tid: str, *extra: str) -> Any:
    return runner.invoke(app, [
        "task", "ask", tid, "Rendre 404 ou 410 ?", "-o", "404", "-o", "410", *extra,
        "--project-root", str(project), "--actor", "w",
    ])


def test_cli_ask_avertit_l_agent_qui_ne_recommande_rien(project: Path) -> None:
    service, tid = running(project)
    result = ask_cli(project, tid)
    assert result.exit_code == 0, result.output
    assert "sans option recommandée" in result.output and "--recommended" in result.output
    assert service.require(tid).status is TaskState.WAITING  # avertie, la question est posée quand même


def test_cli_ask_avertit_aussi_avec_une_raison_mais_sans_recommandation(project: Path) -> None:
    _service, tid = running(project)
    result = ask_cli(project, tid, "--why", "les critères ne tranchent pas")
    assert result.exit_code == 0, result.output
    assert "sans option recommandée" in result.output


def test_cli_ask_v2_avertit_sans_recommend(project: Path) -> None:
    _service, tid = running(project)
    result = runner.invoke(app, [
        "task", "ask", tid, "--title", "Rendre 404 ou 410 ?", "--choice", "404=Rendre 404", "--choice", "410=Rendre 410",
        "--project-root", str(project), "--actor", "w",
    ])
    assert result.exit_code == 0, result.output
    assert "sans option recommandée" in result.output and "--recommend" in result.output


def test_cli_ask_n_avertit_pas_quand_l_option_est_recommandee(project: Path) -> None:
    _service, tid = running(project)
    result = ask_cli(project, tid, "--recommended", "404", "--why", "les critères disent 404")
    assert result.exit_code == 0, result.output
    assert "sans option recommandée" not in result.output


def test_cli_ask_n_avertit_pas_un_humain_au_terminal(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli import cmd_task_waiting

    monkeypatch.setattr(cmd_task_waiting, "_agent_session", lambda: False)
    _service, tid = running(project)
    result = ask_cli(project, tid)
    assert result.exit_code == 0, result.output
    assert "sans option recommandée" not in result.output


# ── Le cockpit : « Décisions à revoir », hors de l'attente de l'humain ──────


def attention(project: Path, now: datetime | None = None) -> dict[str, Any]:
    return wa.attention_view(
        project, now=now, pr_reader=lambda repo, cwd: [], review_cache=workspace_reviews.ReviewCache(), pr_wait=1.0
    )


def test_la_route_d_attention_rend_le_bloc_des_decisions_a_revoir(project: Path) -> None:
    pilot(project, APPLY)
    service, tid, qid = asks(
        project, "Rendre 404 ou 410 ?", options=("404", "410"), recommended="410", why="les critères disent 410"
    )
    before = attention(project)
    assert before["defaultDecisions"] == {"label": "Décisions à revoir", "count": 0, "items": []}

    answer(service, tid, qid, "404", confidence="low")
    view = attention(project)
    block = view["defaultDecisions"]
    assert block["label"] == "Décisions à revoir" and block["count"] == 1
    (item,) = block["items"]
    assert item["task_id"] == tid and item["question_id"] == qid and item["title"] == "Correctif"
    assert item["question"] == "Rendre 404 ou 410 ?" and item["option"] == "410"
    assert item["justification"] == "les critères disent 410"
    # Non bloquant : hors de toute section et de tout compteur — aucun geste n'attend l'humain.
    kinds = {i["kind"] for section in view["sections"] for i in section["items"]}
    assert wa.DEFAULT_DECISION not in kinds and wa.QUESTION not in kinds
    assert wa.DEFAULT_DECISION not in view["counts"] and view["count"] == 0


def test_le_bloc_ne_montre_ni_jugement_ni_recommandation_d_auteur_ni_decision_reprise_ou_ancienne(project: Path) -> None:
    pilot(project, APPLY)
    service, tid, qid = asks(project, options=("A", "B"), recommended="A")
    answer(service, tid, qid, "B", justification=WHY, confidence="high")  # jugement assumé
    service, tid, qid = asks(project, options=("A", "B"), recommended="A")
    stage(service, project)._apply_recommendations({})  # recommandation d'auteur, prise par le tick
    assert attention(project)["defaultDecisions"]["count"] == 0

    service, tid, qid = asks(project, options=("A", "B"), recommended="A")
    answer(service, tid, qid, "B", confidence="low")
    assert attention(project)["defaultDecisions"]["count"] == 1
    assert attention(project, datetime.now(UTC) + wa.DEFAULT_DECISION_VISIBLE + timedelta(hours=1))["defaultDecisions"]["count"] == 0
    revert(service, tid, qid, "guilhem")  # « Revenir » : l'humain a repris la décision
    assert attention(project)["defaultDecisions"]["count"] == 0
