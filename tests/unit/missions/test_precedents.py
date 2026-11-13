"""GAO-jurisprudenc-001 : les réponses humaines font jurisprudence (``autopilot.precedents``, ``off`` par défaut).

Critères : une réponse d'origine ``human`` à une question écrite par le kit est un précédent de sa classe ; sous
``autopilot.precedents: on``, avant de laisser une question de la même classe à l'humain, le pilote applique cette
option (``autopilot.decided`` de source ``precedent``, référence de la réponse) ; ``task questions --precedents`` les
liste, ``--forget`` en retire un par le canal humain ; jamais pour une catégorie réservée ; illisibles, aucun n'est
appliqué et le pilote le dit.

Sûreté, éprouvée ici : rien ne change sans le réglage ; la classe vient de ce que le kit écrit, une question d'agent
n'en a pas ; un précédent passe les refus de la décision par défaut (option encore proposée, jamais « abandonner »,
ni fusion ni permission, liste fermée, worker prouvé à l'arrêt, borne par tâche) ; seule une réponse humaine fait
précédent (ni décision du pilote, ni précédent appliqué) ; les précédents se lisent au ledger, expirent, s'oublient
et se reprennent ; le mandat n'est pas touché.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import mandate as md
from grimoire.missions import orchestrator_session as osess
from grimoire.missions import precedents as pr
from grimoire.missions.autopilot import (
    DECIDED_EVENT,
    DEFERRED_EVENT,
    AutopilotStore,
    load_autopilot_policy,
)
from grimoire.missions.autopilot_view import journal, revert
from grimoire.missions.ledger_rules import deferred_waiting
from grimoire.missions.ledger_views import LeaseView
from grimoire.missions.mandate import MandateRequest, MandateStore
from grimoire.missions.merger import MERGE_ESCALATED_EVENT
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.orchestrator_session import MAX_DEFAULT_DECISIONS, OrchestratorStage
from grimoire.missions.reviewer import REVIEW_ESCALATED_EVENT
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, LedgerEvent, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT
from grimoire.tools import workspace_attention as wa
from grimoire.tools import workspace_reviews

ACC = ("le test passe",)
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
PROV = {"kind": "rule", "ref": "critere-1"}
WHY = "les critères de la tâche disent cette voie"
ON = "autopilot:\n  precedents: on\n"
ON_AND_DEFAULT = "autopilot:\n  precedents: on\n  default_action: apply_recommended\n"
#: L'identifiant court de la session de fond du worker, et ce que le tick en lit quand elle est prouvée à l'arrêt.
WORKER = "abcd1234"
AT_REST = {WORKER: "ended"}
REVIEW = (REVIEW_ESCALATED_EVENT, "not_ready_cycles")
MERGE = (MERGE_ESCALATED_EVENT, "ci_red")
#: Un texte d'escalade qui change d'une tâche à l'autre : la classe n'en dépend pas.
TEXTS = (
    "Désaccord persistant (2 relectures NOT-READY) : nommage — reprendre la tâche ou l'abandonner ?",
    "Désaccord persistant (3 relectures NOT-READY) : couverture — reprendre la tâche ou l'abandonner ?",
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", md.PRINCIPAL_ENV, md.PROVENANCE_ENV):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture(autouse=True)
def _tick() -> Any:
    """L'appelant est le code du tick (``acting_as_orchestrator``), pas une session d'agent."""
    with md.acting_as_orchestrator():
        yield


@pytest.fixture(autouse=True)
def _agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """``claude agents --json`` n'est jamais lancé pour de vrai : par défaut, la session du worker y est arrêtée."""
    monkeypatch.setattr(osess, "_read_agents", lambda *_a, **_k: [{"id": WORKER, "kind": "background", "state": "stopped"}])


def no_process(argv: Any, _cwd: Any, _env: Any = None) -> Any:
    raise AssertionError(f"aucun processus externe attendu dans ces tests : {list(argv)}")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


def write_policy(root: Path, body: str) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(body, encoding="utf-8")


def pilot(root: Path, policy: str = "") -> TaskService:
    """Mandat et pilote actifs ; *policy* : le corps de ``orchestration-policy.yaml`` (vide : aucun réglage)."""
    if policy:
        write_policy(root, policy)
    request = MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d")
    MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")
    AutopilotStore(root).set(True, origin=ORIGIN_HUMAN, via="test")
    return TaskService(root)


def running(service: TaskService, title: str = "Correctif", *, session: str = WORKER) -> str:
    """Une tâche en cours, dont le tick a lancé la session de fond *session* (``""`` : faite à la main)."""
    tid = service.add(title, ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    if session:
        service.ledger.append_event(
            "task.session_launched", tid, "task", "tick", {"task_id": tid, "session": session, "session_id": "s-1"}
        )
    return tid


def escalate(
    service: TaskService, tid: str, kind: tuple[str, str] = REVIEW, *, text: str = TEXTS[0], n: int = 1,
    options: tuple[str, ...] = ("reprendre", "abandonner"), **payload: Any,
) -> str:
    """L'escalade « reprendre ou abandonner ? » telle que le relecteur ou le merger l'écrit : l'événement d'escalade
    (question, cause codée), puis la question avec son option recommandée. Rend l'identifiant de la question."""
    event, cause = kind
    qid = f"q-esc-{tid[-3:]}-{n}"
    body: dict[str, Any] = {"task_id": tid, "question_id": qid, "why": "2 retours", "cause": cause}
    if event == MERGE_ESCALATED_EVENT:
        body.update({"gesture": "bounce", "blocking": True, "pr": 12})
    body.update(payload)
    service.ledger.append_event(event, tid, "task", "tick", {k: v for k, v in body.items() if v is not None})
    service.ask(tid, text, "tick", options=options, recommended=options[0], question_id=qid)
    return qid


def human(service: TaskService, tid: str, qid: str, text: str = "reprendre") -> None:
    service.answer(tid, text, "guilhem", origin=ORIGIN_HUMAN, question_id=qid)


def ruled(service: TaskService, kind: tuple[str, str] = REVIEW, text: str = "reprendre") -> pr.Precedent:
    """Un précédent : une première escalade de la classe, tranchée par l'humain."""
    tid = running(service, "Première tâche")
    human(service, tid, escalate(service, tid, kind), text)
    return pr.precedents(service)[-1]


def again(service: TaskService, kind: tuple[str, str] = REVIEW, **kw: Any) -> tuple[str, str]:
    """Une autre tâche, une question de la même classe (autre texte) : ``(tâche, question)``."""
    tid = running(service, kw.pop("title", "Seconde tâche"), session=kw.pop("session", WORKER))
    return tid, escalate(service, tid, kind, text=kw.pop("text", TEXTS[1]), **kw)


def answer(service: TaskService, tid: str, qid: str, text: str, **kw: Any) -> Any:
    return service.answer(tid, text, "orch", origin=ORIGIN_ORCHESTRATOR, question_id=qid, provenance=PROV, **kw)


def events(service: TaskService, tid: str, kind: str) -> list[Any]:
    return [e for e in service.ledger.events_for(tid) if e.event_type == kind]


def stage(service: TaskService, root: Path, now: datetime | None = None) -> OrchestratorStage:
    policy = load_orchestrator_policy(root).policy
    return OrchestratorStage(service, policy, exec_=no_process, now=now)  # type: ignore[arg-type]


def apply(service: TaskService, root: Path, states: dict[str, str] | None = AT_REST, now: datetime | None = None) -> list[Any]:
    return stage(service, root, now)._apply_recommendations(states)


# ── Le réglage : lu, borné, échec fermé ─────────────────────────────────────


def test_sans_reglage_la_jurisprudence_est_coupee(project: Path) -> None:
    policy = load_autopilot_policy(project)
    assert policy.precedents is False and policy.precedent_days == 30
    write_policy(project, "autopilot:\n  default_action: apply_recommended\n")
    assert load_autopilot_policy(project).precedents is False


@pytest.mark.parametrize(("value", "enabled"), [("on", True), ("off", False), ("true", True), ("false", False), ('"on"', True)])
def test_on_et_off_sont_lus(project: Path, value: str, enabled: bool) -> None:
    write_policy(project, f"autopilot:\n  precedents: {value}\n  precedent_days: 7\n")
    policy = load_autopilot_policy(project)
    assert not policy.error and policy.precedents is enabled and policy.precedent_days == 7


@pytest.mark.parametrize("value", ["yes", "ON", "On", "1", "0", "2", "1.0", '""', "[on]", "~", "always"])
def test_une_valeur_inconnue_rend_la_politique_invalide(project: Path, value: str) -> None:
    write_policy(project, f"autopilot:\n  precedents: {value}\n")
    policy = load_autopilot_policy(project)
    assert policy.error and "precedents" in policy.error and policy.precedents is False


@pytest.mark.parametrize("value", ["0", "366", "-3", '"30"', "true", "1.5", "~", "[30]"])
def test_une_duree_hors_bornes_rend_la_politique_invalide(project: Path, value: str) -> None:
    write_policy(project, f"autopilot:\n  precedents: on\n  precedent_days: {value}\n")
    policy = load_autopilot_policy(project)
    assert policy.error and "precedent_days" in policy.error and policy.precedents is False


def test_une_politique_devenue_invalide_n_applique_aucun_precedent(project: Path) -> None:
    service = pilot(project, ON)
    ruled(service)
    tid, qid = again(service)
    write_policy(project, "autopilot:\n  precedents: toujours\n")
    assert apply(service, project) == []
    with pytest.raises(GrimoireMissionError, match="politique du pilote invalide"):
        answer(service, tid, qid, "reprendre", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


# ── Sans le réglage : rien ne change ────────────────────────────────────────


@pytest.mark.parametrize("policy", ["", "autopilot:\n  precedents: off\n", "autopilot:\n  default_action: ask\n"])
def test_sans_le_reglage_un_precedent_n_est_jamais_applique(project: Path, policy: str) -> None:
    """La réponse humaine est au ledger (elle y était déjà) et se liste, mais le pilote n'en tire rien."""
    service = pilot(project, policy)
    listed = ruled(service)
    assert listed.state == pr.ACTIVE and listed.question_class == "review:not_ready_cycles"
    tid, qid = again(service)
    assert apply(service, project) == []
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "reprendre", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


def test_sans_pilote_le_reglage_ne_decide_rien(project: Path) -> None:
    write_policy(project, ON)
    request = MandateRequest.build(("answer", "resume"), ("implementation",), 10, "+1d")
    MandateStore(project).grant(request, origin=ORIGIN_HUMAN, via="test")
    service = TaskService(project)
    ruled(service)
    tid, _qid = again(service)
    assert apply(service, project) == []
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


# ── Sous `on` : la dernière réponse humaine de la classe tranche ────────────


@pytest.mark.parametrize("kind", [REVIEW, MERGE, (MERGE_ESCALATED_EVENT, "conflict"), (REVIEW_ESCALATED_EVENT, "unjudgeable_reviews")])
def test_le_tick_applique_le_precedent_de_la_meme_classe(project: Path, kind: tuple[str, str]) -> None:
    service = pilot(project, ON)
    ruling = ruled(service, kind)
    tid, qid = again(service, kind)
    (outcome,) = apply(service, project)
    assert outcome.status == "done" and "précédent" in outcome.detail and f"{tid}/{qid}" in outcome.questions
    (decided,) = events(service, tid, DECIDED_EVENT)
    payload = decided.payload
    assert payload["recommendation_source"] == "precedent" and payload["option"] == "reprendre"
    assert payload["precedent"] == {
        "id": ruling.id, "class": ruling.question_class, "question": TEXTS[0], "answered_at": ruling.answered_at,
    }
    assert ruling.answered_at[:10] in payload["justification"] and payload["reversible"] is True
    (given,) = events(service, tid, HUMAN_ANSWERED_EVENT)
    assert given.payload["origin"] == ORIGIN_ORCHESTRATOR and given.payload["answer"] == "reprendre"
    assert given.payload["provenance"] == {"kind": "rule", "ref": "autopilot-precedent"}
    assert service.require(tid).status is TaskState.READY


def test_une_reponse_sans_confiance_recoit_l_option_du_precedent(project: Path) -> None:
    """La voie de la session : « abandonner » soumis sans rien assumer est écarté pour la réponse humaine de la classe."""
    service = pilot(project, ON)
    ruling = ruled(service)
    tid, qid = again(service)
    move = answer(service, tid, qid, "abandonner", confidence="low")
    assert move.task.status is TaskState.READY
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "reprendre" and decided.payload["precedent"]["id"] == ruling.id
    assert not events(service, tid, DEFERRED_EVENT)


def test_un_jugement_assume_n_est_pas_remplace_par_un_precedent(project: Path) -> None:
    service = pilot(project, ON)
    ruled(service)
    tid, qid = again(service)
    answer(service, tid, qid, "abandonner", justification="la tâche n'a plus d'objet : le module est retiré", confidence="high")
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["recommendation_source"] == "judgment" and "precedent" not in decided.payload


def test_le_precedent_passe_avant_l_option_par_defaut_qui_reste_le_repli(project: Path) -> None:
    service = pilot(project, ON_AND_DEFAULT)
    ruled(service, REVIEW)
    tid, _qid = again(service, REVIEW)
    apply(service, project)
    assert events(service, tid, DECIDED_EVENT)[0].payload["recommendation_source"] == "precedent"
    other, _ = again(service, MERGE, title="Troisième tâche")  # aucune réponse humaine pour cette classe
    apply(service, project)
    assert events(service, other, DECIDED_EVENT)[0].payload["recommendation_source"] == "default"


def test_une_autre_classe_n_herite_pas_du_precedent(project: Path) -> None:
    service = pilot(project, ON)
    ruled(service, REVIEW)
    for kind in (MERGE, (REVIEW_ESCALATED_EVENT, "no_worker_session")):
        tid, qid = again(service, kind, title=f"Tâche {kind[1]}")
        assert apply(service, project) == []
        with pytest.raises(GrimoireMissionError, match="confiance"):
            answer(service, tid, qid, "reprendre", confidence="low")
        assert not events(service, tid, DECIDED_EVENT)


# ── La classe est calculée par le kit, jamais déclarée ──────────────────────


def test_une_question_d_agent_n_a_ni_classe_ni_precedent(project: Path) -> None:
    """Même texte, mêmes options, même nature que l'escalade du kit : sans l'événement que seul le kit écrit, rien."""
    service = pilot(project, ON_AND_DEFAULT)
    ruled(service)
    tid = running(service, "Tâche de l'agent")
    for n, kind in enumerate(("choice", "resume_branch", "retry_exhausted")):
        service.ask(tid, TEXTS[1], "w", kind=kind, options=("reprendre", "abandonner"), why="review:not_ready_cycles")
        asked = service.require(tid).waiting.question  # type: ignore[union-attr]
        task_events = service.ledger.events_for(tid)
        assert not osess.kit_written(task_events, asked) and pr.question_class(task_events, asked) == ""
        plan = osess.precedent_plan(service, service.require(tid), asked, task_events, load_autopilot_policy(project))
        assert plan.option == "" and plan.precedent is None and "agent" in plan.reason
        human(service, tid, asked.id)  # et sa réponse humaine ne fait pas davantage précédent
        assert len(pr.precedents(service)) == 1, n
        service.claim(tid, "w")
        service.transition(tid, TaskState.RUNNING, "w")


@pytest.mark.parametrize(
    "payload",
    [
        {"cause": None},  # escalade d'avant la jurisprudence : aucune cause écrite
        {"cause": "close_refused"},  # acceptation et clôture : hors de la liste des classes, à dessein
        {"cause": "no_evidence_pack"},
        {"cause": "reviewer_unregistered"},
        {"cause": "ci_red: mais écrit par quelqu'un d'autre"},
        {"cause": ["not_ready_cycles"]},
    ],
)
@pytest.mark.parametrize("kind", [MERGE, REVIEW])
def test_une_escalade_sans_cause_reconnue_ne_fait_pas_precedent(
    project: Path, payload: dict[str, Any], kind: tuple[str, str]
) -> None:
    service = pilot(project, ON)
    tid = running(service)
    qid = escalate(service, tid, kind, **payload)
    asked = service.require(tid).waiting.question  # type: ignore[union-attr]
    assert pr.question_class(service.ledger.events_for(tid), asked) == ""
    human(service, tid, qid)
    assert pr.precedents(service) == []


def test_la_classe_d_une_escalade_ne_passe_pas_a_une_autre_question_de_la_meme_tache(project: Path) -> None:
    """L'événement d'escalade nomme *sa* question : celle qu'un agent pose ensuite sur la même tâche n'en hérite pas."""
    service = pilot(project, ON)
    tid = running(service)
    human(service, tid, escalate(service, tid))
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    service.ask(tid, TEXTS[0], "w", options=("reprendre", "abandonner"))
    asked = service.require(tid).waiting.question  # type: ignore[union-attr]
    assert pr.question_class(service.ledger.events_for(tid), asked) == ""
    human(service, tid, asked.id)
    assert len(pr.precedents(service)) == 1


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("merge", {"gesture": "merge", "blocking": False}),  # « fusionner ? » sur chemins réservés
        ("choice", {"gesture": "accept", "blocking": False}),  # « accepter ? »
        ("choice", {"gesture": "merge", "blocking": True}),
        ("merge", {"gesture": "bounce", "blocking": True}),
    ],
)
def test_une_reponse_humaine_a_une_question_de_fusion_ou_d_acceptation_ne_fait_jamais_jurisprudence(
    project: Path, kind: str, payload: dict[str, Any]
) -> None:
    service = pilot(project, ON)
    tid = running(service)
    qid = "q-fusion-1"
    service.ledger.append_event(
        MERGE_ESCALATED_EVENT, tid, "task", "tick", {"task_id": tid, "question_id": qid, "cause": "ci_red", **payload}
    )
    service.ask(tid, "Fusionner la PR #12 ? Elle touche des chemins réservés.", "tick", kind=kind,
                options=("fusionne", "attends"), question_id=qid)
    human(service, tid, qid, "fusionne")
    assert pr.precedents(service) == []


def lease(tid: str) -> LeaseView:
    return LeaseView(tid, "lost", "", 1, "w", "claude")


@pytest.mark.parametrize("cause", ["resume_branch", "resume_unfenced", "retry_exhausted"])
def test_la_classe_d_une_question_de_la_recolte_vient_de_son_evenement(project: Path, cause: str) -> None:
    service = TaskService(project)
    tid = running(service)
    asked = deferred_waiting(service.require(tid), lease(tid), cause, 3, "Reprendre la branche ?", datetime.now(UTC)).question
    assert asked is not None

    def deferred(**changes: Any) -> list[LedgerEvent]:
        body = {"task_id": tid, "cause": cause, "question": {"kind": asked.kind, "text": asked.text}, **changes}
        return [LedgerEvent("evt-1", "task.reap_deferred", tid, "task", "tick", "2026-11-13T10:00:00+00:00", payload=body)]

    assert pr.question_class(deferred(), asked) == f"reap:{cause}"
    assert pr.question_class([], asked) == ""  # sans l'événement de la récolte : aucune classe
    assert pr.question_class(deferred(cause="autre"), asked) == ""
    assert pr.question_class(deferred(question={"kind": asked.kind, "text": "un autre texte"}), asked) == ""


def test_une_question_d_origine_agent_n_a_pas_la_classe_de_la_recolte(project: Path) -> None:
    from dataclasses import replace

    service = TaskService(project)
    tid = running(service)
    asked = deferred_waiting(service.require(tid), lease(tid), "resume_branch", 3, "Reprendre ?", datetime.now(UTC)).question
    assert asked is not None
    body = {"task_id": tid, "cause": "resume_branch", "question": {"kind": asked.kind, "text": asked.text}}
    reaped = [LedgerEvent("evt-1", "task.reap_deferred", tid, "task", "tick", "2026-11-13T10:00:00+00:00", payload=body)]
    assert pr.question_class(reaped, asked) == "reap:resume_branch"
    assert pr.question_class(reaped, replace(asked, origin="agent")) == ""


def test_une_vraie_recolte_fait_precedent_pour_sa_classe(project: Path) -> None:
    """Par le vrai chemin du kit (``ledger.reap``) : la question de la récolte, la réponse humaine, puis la classe."""
    service = pilot(project, ON)
    tid = running(service)
    late = datetime.now(UTC) + timedelta(minutes=30)
    (decision,) = service.ledger.reap(now=late, fenced_hosts=frozenset()).decisions
    assert (decision.action, decision.cause) == ("defer", "resume_unfenced")
    asked = service.require(tid).waiting.question  # type: ignore[union-attr]
    assert asked.origin == "tick" and asked.recommended == "reprendre"
    assert pr.question_class(service.ledger.events_for(tid), asked) == "reap:resume_unfenced"
    human(service, tid, asked.id)
    (ruling,) = pr.precedents(service)
    assert (ruling.question_class, ruling.option, ruling.question_id) == ("reap:resume_unfenced", "reprendre", asked.id)


def test_la_tache_garee_sur_erreur_de_fournisseur_a_sa_classe_et_son_precedent(project: Path) -> None:
    """Par le vrai chemin du kit (``launcher._park_provider_error``), de la réponse humaine à l'application."""
    from grimoire.missions.launcher import _park_provider_error

    service = pilot(project, ON)
    first = running(service, "Première tâche")
    assert _park_provider_error(service, service.require(first), "overloaded", 2)
    asked = service.require(first).waiting.question  # type: ignore[union-attr]
    assert pr.question_class(service.ledger.events_for(first), asked) == "launcher:provider_error"
    human(service, first, asked.id, "réessayer")
    (ruling,) = pr.precedents(service)
    assert ruling.question_class == "launcher:provider_error" and ruling.option == "réessayer"

    second = running(service, "Seconde tâche")
    assert _park_provider_error(service, service.require(second), "rate limited", 2)
    (outcome,) = apply(service, project)
    assert outcome.status == "done"
    (decided,) = events(service, second, DECIDED_EVENT)
    assert decided.payload["option"] == "réessayer" and decided.payload["precedent"]["id"] == ruling.id


def test_les_noms_d_evenements_sont_ceux_du_kit() -> None:
    from grimoire.missions.ledger import REAP_DEFERRED_EVENT

    assert pr._REVIEW_ESCALATED == REVIEW_ESCALATED_EVENT and pr._MERGE_ESCALATED == MERGE_ESCALATED_EVENT
    assert pr._REAP_DEFERRED == REAP_DEFERRED_EVENT


# ── Seule une réponse humaine fait précédent ────────────────────────────────


def test_ni_une_decision_du_pilote_ni_un_precedent_applique_ne_font_precedent(project: Path) -> None:
    """Pas de jurisprudence en chaîne : jugement d'une session, option par défaut, précédent appliqué — aucun."""
    service = pilot(project, ON_AND_DEFAULT)
    tid, qid = again(service, MERGE, title="Jugée par une session")
    answer(service, tid, qid, "reprendre", justification=WHY, confidence="high")
    tid, _qid = again(service, MERGE, title="Tranchée par défaut")
    apply(service, project)
    assert events(service, tid, DECIDED_EVENT)[0].payload["recommendation_source"] == "default"
    assert pr.precedents(service) == []

    ruling = ruled(service, MERGE)
    tid, _qid = again(service, MERGE, title="Tranchée par précédent")
    apply(service, project)
    assert events(service, tid, DECIDED_EVENT)[0].payload["recommendation_source"] == "precedent"
    assert [p.id for p in pr.precedents(service)] == [ruling.id]


@pytest.mark.parametrize("origin", ["orchestrator", "agent", "unverified", "tick", ""])
def test_une_reponse_d_une_autre_origine_ne_fait_pas_precedent(project: Path, origin: str) -> None:
    """Lu au ledger tel quel : seule l'origine ``human`` posée par le canal de réponse compte."""
    service = pilot(project, ON)
    tid = running(service)
    qid = escalate(service, tid)
    service.ledger.append_event(
        HUMAN_ANSWERED_EVENT, tid, "task", "quelqu-un",
        {"task_id": tid, "question_id": qid, "answer": "reprendre", "origin": origin},
    )
    assert pr.precedents(service) == []


# ── Les mêmes refus que la décision par défaut ──────────────────────────────


def test_un_precedent_abandonner_ne_s_applique_jamais_et_efface_le_precedent_plus_ancien(project: Path) -> None:
    """La dernière réponse humaine de la classe gouverne : « abandonner » n'est jamais appliqué, et « reprendre »,
    plus ancien, ne revient pas à sa place."""
    service = pilot(project, ON)
    old = ruled(service)
    tid = running(service, "Tâche abandonnée")
    human(service, tid, escalate(service, tid), "abandonner")
    states = {p.id: p.state for p in pr.precedents(service)}
    assert states[old.id] == pr.SUPERSEDED and list(states.values()).count(pr.ACTIVE) == 1
    tid, qid = again(service)
    assert apply(service, project) == []
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "abandonner", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


def test_l_option_par_defaut_ne_reprend_pas_contre_un_abandon_humain_de_la_classe(project: Path) -> None:
    """Sous `apply_recommended`, le défaut reprendrait : tant que « abandonner » est le dernier mot de l'humain pour
    la classe, il s'abstient et la question se juge. Une autre classe garde son option par défaut."""
    service = pilot(project, ON_AND_DEFAULT)
    ruled(service, MERGE, "abandonner")
    tid, qid = again(service, MERGE)
    assert apply(service, project) == []
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "reprendre", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)
    answer(service, tid, qid, "reprendre", justification=WHY, confidence="high")  # un jugement assumé reste possible
    assert events(service, tid, DECIDED_EVENT)[0].payload["recommendation_source"] == "judgment"
    other, _ = again(service, REVIEW, title="Troisième tâche")
    assert [o.status for o in apply(service, project)] == ["done"]
    assert events(service, other, DECIDED_EVENT)[0].payload["recommendation_source"] == "default"


def test_un_abandon_humain_oublie_ou_expire_ne_retient_plus_l_option_par_defaut(project: Path) -> None:
    service = pilot(project, ON_AND_DEFAULT)
    ruling = ruled(service, MERGE, "abandonner")
    tid, _qid = again(service, MERGE)
    assert apply(service, project) == []
    pr.forget(service, ruling.id, "guilhem", origin=ORIGIN_HUMAN)
    assert [o.status for o in apply(service, project)] == ["done"]
    assert events(service, tid, DECIDED_EVENT)[0].payload["recommendation_source"] == "default"


def test_un_precedent_dont_l_option_n_est_plus_proposee_ne_s_applique_pas(project: Path) -> None:
    service = pilot(project, ON)
    ruled(service)
    tid, qid = again(service, options=("continuer", "abandonner"))
    assert apply(service, project) == []
    task = service.require(tid)
    plan = osess.precedent_plan(
        service, task, task.waiting.question, service.ledger.events_for(tid), load_autopilot_policy(project)  # type: ignore[union-attr]
    )
    assert plan.option == "" and "n'est pas l'une des options" in plan.reason and qid
    assert plan.contrary is False  # seul « abandonner » retient l'option par défaut


def test_un_precedent_ne_s_applique_jamais_a_une_categorie_reservee(project: Path) -> None:
    """Le texte d'une escalade recopie des constats : s'il touche la liste fermée, la question reste à l'humain."""
    service = pilot(project, ON)
    ruled(service)
    tid, qid = again(service, text="Désaccord persistant (le relecteur demande de régénérer le token d'API) — reprendre ?")
    assert apply(service, project) == []
    with pytest.raises(GrimoireMissionError, match="réservée à l'humain"):
        answer(service, tid, qid, "reprendre", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)
    task = service.require(tid)  # et le plan lui-même le refuse, sans compter sur la garde générale
    plan = osess.precedent_plan(
        service, task, task.waiting.question, service.ledger.events_for(tid), load_autopilot_policy(project)  # type: ignore[union-attr]
    )
    assert plan.option == "" and "réservée à l'humain" in plan.reason


def test_la_reserve_ajoutee_par_le_projet_retient_aussi_un_precedent(project: Path) -> None:
    service = pilot(project, "autopilot:\n  precedents: on\n  reserved: [nommage]\n")
    ruled(service)
    tid, _qid = again(service, text="Désaccord persistant : nommage du module — reprendre la tâche ou l'abandonner ?")
    assert apply(service, project) == []
    assert service.require(tid).status is TaskState.WAITING


@pytest.mark.parametrize("states", [None, {}, {WORKER: "busy"}, {WORKER: ""}, {"ffff0000": "ended"}])
def test_un_precedent_attend_la_preuve_que_le_worker_ne_travaille_plus(project: Path, states: dict[str, str] | None) -> None:
    service = pilot(project, ON)
    ruled(service)
    tid, _qid = again(service)
    assert apply(service, project, states) == []
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)
    assert [o.status for o in apply(service, project, {WORKER: "idle"})] == ["done"]


def test_un_precedent_ne_s_applique_pas_a_une_tache_sans_session_notee_par_le_tick(project: Path) -> None:
    service = pilot(project, ON)
    ruled(service)
    tid, _qid = again(service, session="")
    assert apply(service, project) == []
    assert service.require(tid).status is TaskState.WAITING


@pytest.mark.parametrize("agents", [[{"id": WORKER, "kind": "background", "state": "working"}], [], None])
def test_la_voie_de_la_session_porte_la_meme_garde(
    project: Path, monkeypatch: pytest.MonkeyPatch, agents: list[dict[str, Any]] | None
) -> None:
    service = pilot(project, ON)
    ruled(service)
    tid, qid = again(service)
    monkeypatch.setattr(osess, "_read_agents", lambda *_a, **_k: agents)
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "reprendre", confidence="low")
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


def test_les_precedents_appliques_partagent_la_borne_des_decisions_par_defaut(project: Path) -> None:
    """Précédent ou défaut, l'option relance la tâche : la même boucle escalade → reprise, la même borne."""
    service = pilot(project, ON_AND_DEFAULT)
    ruled(service, MERGE)
    tid = running(service, "Tâche qui boucle")
    outcomes: list[Any] = []
    for n in range(MAX_DEFAULT_DECISIONS + 1):
        escalate(service, tid, MERGE, text=f"La fusion échoue encore ({n}) — reprendre la tâche ou l'abandonner ?", n=n)
        outcomes = apply(service, project)
        if n < MAX_DEFAULT_DECISIONS:
            assert [o.status for o in outcomes] == ["done"]
            service.claim(tid, "w")
            service.transition(tid, TaskState.RUNNING, "w")
    assert outcomes == [] and service.require(tid).status is TaskState.WAITING  # ni précédent, ni défaut en repli
    sources = [e.payload["recommendation_source"] for e in events(service, tid, DECIDED_EVENT)]
    assert sources == ["precedent"] * MAX_DEFAULT_DECISIONS


# ── Péremption, oubli, « Revenir » ──────────────────────────────────────────


def test_un_precedent_expire(project: Path) -> None:
    service = pilot(project, "autopilot:\n  precedents: on\n  precedent_days: 3\n")
    ruling = ruled(service)
    tid, _qid = again(service)
    late = datetime.now(UTC) + timedelta(days=3, hours=1)
    assert [p.state for p in pr.precedents(service, days=3, now=late)] == [pr.EXPIRED]
    assert apply(service, project, now=late) == []
    assert service.require(tid).status is TaskState.WAITING
    assert [p.state for p in pr.precedents(service, days=3, now=datetime.now(UTC) + timedelta(days=2))] == [pr.ACTIVE]
    assert [o.status for o in apply(service, project, now=datetime.now(UTC) + timedelta(days=2))] == ["done"] and ruling


def test_une_date_illisible_vaut_expiration(project: Path) -> None:
    service = pilot(project, ON)
    tid = running(service)
    qid = escalate(service, tid)
    service.ledger.append_event(
        HUMAN_ANSWERED_EVENT, tid, "task", "guilhem",
        {"task_id": tid, "question_id": qid, "answer": "reprendre", "origin": ORIGIN_HUMAN},
    )
    (listed,) = pr.precedents(service, now=datetime.now(UTC) + timedelta(days=400))
    assert listed.state == pr.EXPIRED


def test_oublier_un_precedent_le_retire_sans_ressusciter_le_precedent(project: Path) -> None:
    service = pilot(project, ON)
    old = ruled(service)
    latest = ruled(service)
    gone = pr.forget(service, latest.id, "guilhem", origin=ORIGIN_HUMAN)
    assert gone.state == pr.FORGOTTEN
    states = {p.id: p.state for p in pr.precedents(service)}
    assert states == {old.id: pr.SUPERSEDED, latest.id: pr.FORGOTTEN}
    (noted,) = events(service, latest.task_id, pr.FORGOTTEN_EVENT)
    assert noted.payload["precedent"] == latest.id and noted.payload["origin"] == ORIGIN_HUMAN
    tid, _qid = again(service)
    assert apply(service, project) == [] and service.require(tid).status is TaskState.WAITING
    with pytest.raises(GrimoireMissionError, match="déjà oublié"):
        pr.forget(service, latest.id, "guilhem", origin=ORIGIN_HUMAN)
    with pytest.raises(GrimoireMissionError, match="inconnu"):
        pr.forget(service, "evt-inconnu", "guilhem", origin=ORIGIN_HUMAN)


@pytest.mark.parametrize("origin", ["orchestrator", "agent", "unverified", "tick", "cockpit", ""])
def test_seul_le_canal_humain_oublie_un_precedent(project: Path, origin: str) -> None:
    service = pilot(project, ON)
    ruling = ruled(service)
    with pytest.raises(GrimoireMissionError, match="canal humain"):
        pr.forget(service, ruling.id, "quelqu-un", origin=origin)
    assert [p.state for p in pr.precedents(service)] == [pr.ACTIVE]
    assert not events(service, ruling.task_id, pr.FORGOTTEN_EVENT)


def test_revenir_sur_une_application_la_defait_et_retire_le_precedent(project: Path) -> None:
    service = pilot(project, ON)
    ruling = ruled(service)
    tid, qid = again(service)
    apply(service, project)
    assert service.require(tid).status is TaskState.READY
    assert revert(service, tid, qid, "guilhem")["how"] == "hold"  # « Revenir » fonctionne : la tâche est rendue à l'humain
    assert service.require(tid).status is TaskState.BLOCKED
    assert [p.state for p in pr.precedents(service)] == [pr.REVERTED] and ruling
    other, _ = again(service, title="Troisième tâche")
    assert apply(service, project) == [] and service.require(other).status is TaskState.WAITING


def test_une_nouvelle_reponse_humaine_remplace_un_precedent_repris(project: Path) -> None:
    service = pilot(project, ON)
    ruled(service)
    tid, qid = again(service)
    apply(service, project)
    revert(service, tid, qid, "guilhem")
    fresh = ruled(service)
    assert fresh.state == pr.ACTIVE
    other, _ = again(service, title="Troisième tâche")
    assert [o.status for o in apply(service, project)] == ["done"]
    assert events(service, other, DECIDED_EVENT)[0].payload["precedent"]["id"] == fresh.id


# ── Précédents illisibles : aucun appliqué, et le pilote le dit ─────────────


def unreadable(service: TaskService, monkeypatch: pytest.MonkeyPatch, how: str, ruling: pr.Precedent) -> None:
    if how == "oubli-sans-identifiant":
        service.ledger.append_event(pr.FORGOTTEN_EVENT, ruling.task_id, "task", "guilhem", {"origin": ORIGIN_HUMAN})
    elif how == "decision-sans-precedent":
        service.ledger.append_event(
            DECIDED_EVENT, ruling.task_id, "task", "orchestrator",
            {"task_id": ruling.task_id, "question_id": "q-x", "recommendation_source": "precedent"},
        )
    else:
        real = service.ledger.list_events

        def broken(entity_id: str | None = None) -> list[LedgerEvent]:
            if entity_id is None:
                raise OSError("ledger illisible")
            return real(entity_id)

        monkeypatch.setattr(service.ledger, "list_events", broken)


@pytest.mark.parametrize("how", ["oubli-sans-identifiant", "decision-sans-precedent", "ledger"])
def test_des_precedents_illisibles_n_appliquent_rien_et_le_tick_le_dit(
    project: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    service = pilot(project, ON)
    ruling = ruled(service)
    first, _ = again(service)
    second, _ = again(service, title="Troisième tâche")
    unreadable(service, monkeypatch, how, ruling)
    with pytest.raises(pr.PrecedentsUnreadableError, match="précédents illisibles"):
        pr.precedents(service)
    outcomes = apply(service, project)
    assert [(o.action, o.status) for o in outcomes] == [("pilot", "skipped")]  # dit une fois, pas par question
    assert "précédents illisibles" in outcomes[0].detail and "aucun précédent appliqué" in outcomes[0].detail
    for tid in (first, second):
        assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


def test_des_precedents_illisibles_laissent_la_question_a_l_humain_avec_la_raison(project: Path) -> None:
    service = pilot(project, ON)
    ruling = ruled(service)
    tid, qid = again(service)
    service.ledger.append_event(pr.FORGOTTEN_EVENT, ruling.task_id, "task", "guilhem", {"precedent": "  "})
    with pytest.raises(GrimoireMissionError, match="précédents illisibles"):
        answer(service, tid, qid, "reprendre", confidence="low")
    (deferred,) = events(service, tid, DEFERRED_EVENT)
    assert "précédents illisibles" in deferred.payload["reason"]
    assert service.require(tid).status is TaskState.WAITING and not events(service, tid, DECIDED_EVENT)


def test_sous_illisible_l_option_par_defaut_reste_le_repli_regle_par_le_projet(project: Path) -> None:
    """Illisibles, les précédents n'appliquent rien ; le réglage `apply_recommended`, lui, ne dépend pas d'eux."""
    service = pilot(project, ON_AND_DEFAULT)
    ruling = ruled(service)
    tid, _qid = again(service)
    service.ledger.append_event(pr.FORGOTTEN_EVENT, ruling.task_id, "task", "guilhem", {})
    outcomes = apply(service, project)
    assert [(o.action, o.status) for o in outcomes] == [("pilot", "skipped"), ("pilot", "done")]
    assert events(service, tid, DECIDED_EVENT)[0].payload["recommendation_source"] == "default"


# ── La CLI : `task questions --precedents`, `--forget` ──────────────────────


def cli(project: Path, *args: str) -> Any:
    return runner.invoke(app, ["task", "questions", *args, "--project-root", str(project)])


def test_cli_liste_les_precedents(project: Path) -> None:
    service = pilot(project, ON)
    ruling = ruled(service)
    result = cli(project, "--precedents")
    assert result.exit_code == 0, result.output
    assert ruling.id in result.output and "review:not_ready_cycles" in result.output and "en vigueur" in result.output
    assert "Jurisprudence activée" in result.output and "reprendre" in result.output
    listed = json.loads(runner.invoke(
        app, ["--output", "json", "task", "questions", "--precedents", "--project-root", str(project)]
    ).stdout)
    assert listed["enabled"] is True and listed["days"] == 30
    assert [p["id"] for p in listed["precedents"]] == [ruling.id] and listed["precedents"][0]["state"] == "active"


def test_cli_dit_que_rien_n_est_applique_sans_le_reglage(project: Path) -> None:
    service = pilot(project)
    ruled(service)
    result = cli(project, "--precedents")
    assert result.exit_code == 0 and "désactivée" in result.output and "rien n'est appliqué" in result.output


def test_cli_forget_est_refuse_a_un_agent(project: Path) -> None:
    """``CliRunner`` n'a pas de terminal : c'est le canal d'un agent, comme pour ``task answer``."""
    service = pilot(project, ON)
    ruling = ruled(service)
    result = cli(project, "--forget", ruling.id)
    assert result.exit_code == 1 and "canal humain" in result.output
    assert [p.state for p in pr.precedents(TaskService(project))] == [pr.ACTIVE]


def test_cli_forget_est_refuse_a_l_orchestrateur_sous_mandat(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = pilot(project, ON)
    ruling = ruled(service)
    monkeypatch.setenv(md.PRINCIPAL_ENV, "orchestrator")
    monkeypatch.setenv(md.PROVENANCE_ENV, "rule:critere-1")
    result = cli(project, "--forget", ruling.id)
    assert result.exit_code == 1 and "canal humain" in result.output
    assert [p.state for p in pr.precedents(TaskService(project))] == [pr.ACTIVE]


def test_cli_forget_par_le_canal_humain(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli import cmd_task

    service = pilot(project, ON)
    ruling = ruled(service)
    monkeypatch.setattr(cmd_task, "_comment_origin", lambda: ORIGIN_HUMAN)
    result = cli(project, "--forget", ruling.id)
    assert result.exit_code == 0, result.output
    assert "oublié" in result.output
    assert [p.state for p in pr.precedents(TaskService(project))] == [pr.FORGOTTEN]
    assert cli(project, "--forget", "evt-inconnu").exit_code == 1


def test_cli_dit_des_precedents_illisibles(project: Path) -> None:
    service = pilot(project, ON)
    ruling = ruled(service)
    service.ledger.append_event(pr.FORGOTTEN_EVENT, ruling.task_id, "task", "guilhem", {})
    result = cli(project, "--precedents")
    assert result.exit_code == 1 and "précédents illisibles" in result.output


# ── Le cockpit : « Décisions à revoir » et le journal ───────────────────────


def test_l_application_d_un_precedent_est_a_revoir_avec_la_question_d_origine_et_sa_date(project: Path) -> None:
    service = pilot(project, ON)
    ruling = ruled(service)
    tid, qid = again(service)
    apply(service, project)
    view = wa.attention_view(
        project, pr_reader=lambda repo, cwd: [], review_cache=workspace_reviews.ReviewCache(), pr_wait=1.0
    )
    block = view["reviewDecisions"]
    assert block["sources"] == {"precedent": 1} and block["count"] == 1
    (item,) = block["items"]
    assert (item["source"], item["task_id"], item["question_id"], item["option"]) == ("precedent", tid, qid, "reprendre")
    assert item["precedent"] == {
        "id": ruling.id, "class": "review:not_ready_cycles", "question": TEXTS[0], "answered_at": ruling.answered_at,
    }
    kinds = {i["kind"] for section in view["sections"] for i in section["items"]}
    assert wa.REVIEW_DECISION not in kinds and wa.REVIEW_DECISION not in view["counts"]
    (entry,) = [e for e in journal(service) if e["task_id"] == tid]
    assert entry["source"] == "precedent" and entry["precedent"]["id"] == ruling.id


# ── Le mandat n'est pas touché ──────────────────────────────────────────────


def test_la_jurisprudence_n_ajoute_rien_au_mandat() -> None:
    """Ni nouvelle provenance, ni nouveau geste : un précédent appliqué est un geste ``answer`` ou ``resume`` existant."""
    assert md.PROVENANCE_KINDS == ("rule", "verdict", "receipt")
    assert not [g for g in md.GESTURES if "preced" in g]
