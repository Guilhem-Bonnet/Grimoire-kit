"""P82 : le pilote automatique — l'orchestrateur tranche aussi les cas bloqués, sauf une liste fermée réservée.

Critères : sans pilote, ``merge`` et ``retry_exhausted`` restent refusés ; pilote activé ils sont tranchés et
``autopilot.decided`` porte l'option et la justification ; le pilote ne s'active ni ne se coupe depuis une session
d'agent, seulement par le canal humain, et révoquer le mandat le coupe ; la liste fermée (identifiants, secrets,
payant, permission hors du dépôt, mandat…) reste à l'humain, la politique n'y fait qu'ajouter ; une recommandation
est prise, une confiance basse laisse la question à l'humain ; présentations sans borne et tâches faites à la main
éligibles.
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
    RESERVED,
    AutopilotStore,
    load_autopilot_policy,
    reserved_reason,
)
from grimoire.missions.mandate import MandateRefusedError, MandateRequest, MandateStore
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.orchestrator_session import (
    MAX_PRESENTATIONS,
    ORCH_ENDED_EVENT,
    ORCH_LAUNCHED_EVENT,
    OrchestratorStage,
    eligible_questions,
)
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT

ACC = ("la PR est fusionnée",)
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
PROV = {"kind": "rule", "ref": "critere-1"}
WHY = "la PR est verte et relue, les critères sont remplis"


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


@pytest.fixture
def no_tick() -> None:
    """Les tests qui l'ouvrent éprouvent l'appelant CLI (session inscrite), pas l'API en processus du tick."""


@pytest.fixture(autouse=True)
def _tick(request: pytest.FixtureRequest) -> Any:
    """Par défaut l'appelant est le code du tick (``acting_as_orchestrator``) ; ``no_tick`` l'en retire."""
    if "no_tick" in request.fixturenames:
        yield
        return
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


def asks(root: Path, text: str = "Quelle voie ?", **ask: Any) -> tuple[TaskService, str, str]:
    """Une tâche faite à la main (jamais lancée par le tick) qui attend une réponse : ``(service, tâche, question)``."""
    service = TaskService(root)
    tid = service.add("Correctif", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    service.ask(tid, text, "w", **ask)
    return service, tid, service.require(tid).waiting.question.id  # type: ignore[union-attr]


def answer(service: TaskService, tid: str, qid: str, text: str, **kw: Any) -> Any:
    return service.answer(tid, text, "orch", origin=ORIGIN_ORCHESTRATOR, question_id=qid, provenance=PROV, **kw)


def events(service: TaskService, tid: str, kind: str) -> list[Any]:
    return [e for e in service.ledger.events_for(tid) if e.event_type == kind]


# ── Sans pilote : comme aujourd'hui ; pilote activé : tranché, avec sa justification ──────────────────────


@pytest.mark.parametrize(
    ("kind", "options", "pick"),
    [
        ("merge", ("fusionner", "attendre"), "attendre"),
        ("retry_exhausted", ("réessayer", "abandonner"), "réessayer"),
    ],
)
def test_without_the_pilot_merge_and_retry_exhausted_stay_human(
    project: Path, kind: str, options: tuple[str, ...], pick: str
) -> None:
    grant(project)
    service, tid, qid = asks(project, "Que faire ?", kind=kind, options=options, origin="tick")
    with pytest.raises(GrimoireMissionError, match="réservée à l'humain"):
        answer(service, tid, qid, pick, justification=WHY, confidence="high")
    assert not events(service, tid, DECIDED_EVENT)


@pytest.mark.parametrize(
    ("kind", "options", "pick"),
    [
        ("merge", ("fusionner", "attendre"), "attendre"),
        ("retry_exhausted", ("réessayer", "abandonner"), "réessayer"),
    ],
)
def test_with_the_pilot_the_question_is_decided_and_the_decision_is_traced(
    project: Path, kind: str, options: tuple[str, ...], pick: str
) -> None:
    mandate = grant(project)
    pilot_on(project)
    service, tid, qid = asks(project, "Que faire ?", kind=kind, options=options, origin="tick")
    move = answer(service, tid, qid, pick, justification=WHY, confidence="high")
    assert move.task.status is TaskState.READY
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == pick and decided.payload["justification"] == WHY
    assert decided.payload["confidence"] == "high" and decided.payload["recommendation_source"] == "judgment"
    assert decided.payload["mandate_id"] == mandate.id and decided.payload["question_id"] == qid
    (given,) = events(service, tid, HUMAN_ANSWERED_EVENT)
    assert given.payload["origin"] == ORIGIN_ORCHESTRATOR and given.payload["mandate_id"] == mandate.id


def test_the_pilot_can_abandon_with_a_reason_and_never_without_one(project: Path) -> None:
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(
        project, "La relancer encore ?", kind="retry_exhausted", options=("réessayer", "abandonner"), origin="tick"
    )
    with pytest.raises(GrimoireMissionError, match="justification"):
        answer(service, tid, qid, "abandonner", justification="", confidence="high")
    answer(
        service,
        tid,
        qid,
        "abandonner",
        justification="trois pertes de bail : la tâche n'a plus de sens",
        confidence="high",
    )
    task = service.require(tid)
    assert task.status is TaskState.FAILED  # rangée avec sa raison, pas annulée : réouvrable
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["reversible"] is True
    assert service.transition(tid, TaskState.READY, "human", "reprise").task.status is TaskState.READY


def test_without_the_pilot_abandoning_is_still_a_human_gesture(project: Path) -> None:
    grant(project)
    service, tid, qid = asks(project, "Reprendre ?", options=("reprendre", "abandonner"))
    with pytest.raises(GrimoireMissionError, match="geste d'humain"):
        answer(service, tid, qid, "abandonner")


# ── L'autorité : le canal humain seul, lié au mandat ─────────────────────────────────────────────────────


@pytest.mark.parametrize("origin", ["orchestrator", "agent", "tick", "cockpit", ""])
def test_no_non_human_origin_switches_the_pilot(project: Path, origin: str) -> None:
    grant(project)
    store = AutopilotStore(project)
    with pytest.raises(MandateRefusedError, match="Seul l'humain"):
        store.set(True, origin=origin, via="forgé")
    assert not store.state().active
    pilot_on(project)
    with pytest.raises(MandateRefusedError, match="Seul l'humain"):
        store.set(False, origin=origin, via="forgé")
    assert store.state().active


def test_the_cli_channel_without_a_human_turn_writes_nothing(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une session d'agent (aucun tour humain, en-tête ou variable forgés) obtient un challenge, jamais l'état."""
    grant(project)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S-agent")
    monkeypatch.setenv(md.PRINCIPAL_ENV, "human")  # une revendication ne prouve rien
    store = AutopilotStore(project)
    entry, attestation = store.request(True)
    assert entry is None and not attestation.attested
    assert not store.state().active and not store.history()
    entry, _ = store.request(False)
    assert entry is None


def test_the_human_channel_switches_the_pilot_and_journals_who_and_when(project: Path) -> None:
    store = AutopilotStore(project)
    with pytest.raises(MandateRefusedError, match="mandat actif"):
        store.set(True, origin=ORIGIN_HUMAN, via="cockpit")
    mandate = grant(project)
    entry = store.set(True, origin=ORIGIN_HUMAN, via="cockpit")
    state = store.state()
    assert state.active and state.mandate_id == mandate.id and state.by == ORIGIN_HUMAN and state.via == "cockpit"
    assert entry["ts"] and entry["state"] == "on"
    store.set(False, origin=ORIGIN_HUMAN, via="cockpit")
    assert not store.state().active
    assert [e["state"] for e in store.history()] == ["on", "off"]


def test_revoking_the_mandate_cuts_the_pilot_and_a_new_mandate_does_not_relight_it(project: Path) -> None:
    grant(project)
    pilot_on(project)
    store = AutopilotStore(project)
    assert store.state().active
    MandateStore(project).revoke()
    state = store.state()
    assert not state.active and "mandat inactif" in state.reason
    grant(project)
    assert not store.state().active and "autre mandat" in store.state().reason


def test_a_tampered_journal_carries_no_pilot(project: Path) -> None:
    grant(project)
    pilot_on(project)
    journal = AutopilotStore(project).journal.path
    lines = journal.read_text(encoding="utf-8").splitlines()
    forged = lines[-1].replace("human", "robot")
    journal.write_text("\n".join([*lines[:-1], forged]) + "\n", encoding="utf-8")
    assert not AutopilotStore(project).state().active


def test_the_cockpit_route_needs_the_control_key(project: Path) -> None:
    from grimoire.tools.workspace_routes import _autopilot_set

    grant(project)
    with pytest.raises(PermissionError):
        _autopilot_set(project, {"on": True})
    with pytest.raises(PermissionError):
        _autopilot_set(project, {"on": True, "control_key": "faux"})
    assert not AutopilotStore(project).state().active
    md.ensure_control_key()
    key = md.control_key_path().read_text(encoding="utf-8").strip()
    assert _autopilot_set(project, {"on": True, "control_key": key})["autopilot"] == "on"
    assert AutopilotStore(project).state().active
    with pytest.raises(PermissionError):  # couper exige aussi la clé
        _autopilot_set(project, {"on": False})
    assert AutopilotStore(project).state().active


# ── La liste fermée : toujours à l'humain ────────────────────────────────────────────────────────────────

CLOSED = [
    ("Quel identifiant utiliser pour se connecter au registre ?", "choice"),
    ("Faut-il committer le mot de passe dans .env ?", "choice"),
    ("Où ranger la clé privée de signature ?", "choice"),
    ("Lancer le benchmark payant sur l'API facturée ?", "choice"),
    ("Peut-on écrire dans /etc/hosts ?", "permission"),
    ("Autoriser l'accès en écriture ?", "permission"),
    ("Supprimer les données des utilisateurs de la base ?", "choice"),
    ("Étendre le mandat à la fusion ?", "choice"),
    ("Désactiver la protection de branche de main ?", "choice"),
    ("Q​UEL ÎDENTIFIANT, à crédit ?", "choice"),  # invisibles, accents, casse : même verdict
    ("Quel \u0456dentifiant (i cyrillique) ?", "choice"),  # homoglyphe : lettres non latines, réservé
    ("Lire $HOME/.aws/config puis /tmp/proj/src/a.py ?", "permission"),  # chemin par variable
]


@pytest.mark.parametrize(("text", "kind"), CLOSED)
def test_the_closed_list_stays_human_even_with_the_pilot(project: Path, text: str, kind: str) -> None:
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(project, text, kind=kind, options=("oui", "non"))
    with pytest.raises(GrimoireMissionError, match="réservée à l'humain"):
        answer(service, tid, qid, "non", justification=WHY, confidence="high")
    assert not events(service, tid, DECIDED_EVENT)
    assert not [q for q in eligible_questions(service, MandateStore(project)) if q.question_id == qid]
    assert service.require(tid).status is TaskState.WAITING


def test_a_permission_inside_the_repository_is_not_reserved(project: Path) -> None:
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(project, f"Écrire dans {project}/src/app.py ?", kind="permission", options=("oui", "non"))
    task = service.require(tid)
    assert not reserved_reason(task.waiting.question, task, project)  # type: ignore[union-attr]
    answer(service, tid, qid, "oui", justification=WHY, confidence="high")
    assert service.require(tid).status is TaskState.READY


def write_policy(root: Path, body: str) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(body, encoding="utf-8")


def test_the_policy_can_only_add_reserved_categories(project: Path) -> None:
    write_policy(project, "autopilot:\n  reserved:\n    - migration\n")
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(project, "Lancer la migration du schéma ?", options=("oui", "non"))
    with pytest.raises(GrimoireMissionError, match=r"autopilot\.reserved"):
        answer(service, tid, qid, "oui", justification=WHY, confidence="high")
    other, otid, oqid = asks(project, "Quelle couleur pour le bouton ?", options=("bleu", "vert"))
    answer(other, otid, oqid, "bleu", justification=WHY, confidence="high")


@pytest.mark.parametrize(
    "body",
    [
        "autopilot:\n  reserved: []\n  allow: [secrets]\n",
        "autopilot:\n  unreserve: [secrets]\n",
        "autopilot:\n  reserved: secrets\n",
        "autopilot: oui\n",
        "autopilot:\n  reserved: [1]\n",
    ],
)
def test_a_policy_that_tries_to_remove_or_garbles_the_list_reserves_everything(project: Path, body: str) -> None:
    write_policy(project, body)
    assert load_autopilot_policy(project).error
    grant(project)
    with pytest.raises(MandateRefusedError, match="pilote"):
        AutopilotStore(project).set(True, origin=ORIGIN_HUMAN, via="test")
    assert not AutopilotStore(project).state().active
    write_policy(project, "")
    pilot_on(project)
    write_policy(project, body)  # abîmée après l'activation : tout reste à l'humain
    service, tid, qid = asks(project, "Quelle couleur ?", options=("bleu", "vert"))
    with pytest.raises(GrimoireMissionError, match="politique du pilote invalide"):
        answer(service, tid, qid, "bleu", justification=WHY, confidence="high")


def test_the_closed_list_lives_in_the_code_not_in_the_policy() -> None:
    assert {"identifiants", "secrets", "depense", "donnees-utilisateur", "mandat", "reglages-depot"} <= set(RESERVED)


# ── Comment il décide : la recommandation, sinon la justification et la confiance ───────────────────────


def test_a_question_with_a_recommendation_gets_that_option(project: Path) -> None:
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(
        project, "Rendre 404 ou 410 ?", options=("404", "410"), recommended="404", why="les critères disent 404"
    )
    stage = OrchestratorStage(service, load_orchestrator_policy(project).policy)  # type: ignore[arg-type]
    (outcome,) = stage._apply_recommendations()
    assert outcome.status == "done" and f"{tid}/{qid}" in outcome.questions
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["option"] == "404" and decided.payload["recommendation_source"] == "recommended"
    assert decided.payload["justification"] == "les critères disent 404"
    assert service.require(tid).status is TaskState.READY


def test_a_recommendation_is_ignored_without_the_pilot(project: Path) -> None:
    grant(project)
    service, tid, qid = asks(project, "Rendre 404 ou 410 ?", options=("404", "410"), recommended="404")
    stage = OrchestratorStage(service, load_orchestrator_policy(project).policy)  # type: ignore[arg-type]
    assert stage._apply_recommendations() == []
    assert service.require(tid).status is TaskState.WAITING and qid


def test_a_recommendation_outside_the_options_is_refused_at_ask_time(project: Path) -> None:
    service = TaskService(project)
    tid = service.add("Correctif", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    with pytest.raises(ValueError, match="recommandation"):
        service.ask(tid, "Quelle voie ?", "w", options=("A", "B"), recommended="C")


def test_a_recommended_question_is_listed_with_its_recommendation(project: Path) -> None:
    service, tid, qid = asks(project, "Quelle voie ?", options=("A", "B"), recommended="B", why="plus simple")
    from grimoire.missions.questions import question_rows

    (row,) = question_rows(service.require(tid))
    assert row["recommended"] == "B" and row["why"] == "plus simple" and qid == row["id"]


def test_low_or_undeclared_confidence_without_a_recommendation_stays_human_with_the_reason(project: Path) -> None:
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(project, "Quelle voie ?", options=("A", "B"))
    with pytest.raises(GrimoireMissionError, match="confiance basse"):
        answer(service, tid, qid, "A", justification=WHY, confidence="low")
    with pytest.raises(GrimoireMissionError, match="non déclarée"):
        answer(service, tid, qid, "A", justification=WHY)
    with pytest.raises(GrimoireMissionError, match="justification"):
        answer(service, tid, qid, "A", justification="", confidence="high")
    assert service.require(tid).status is TaskState.WAITING
    deferred = events(service, tid, DEFERRED_EVENT)
    assert deferred and "confiance basse" in deferred[0].payload["reason"]
    assert not events(service, tid, DECIDED_EVENT)
    assert not [
        q for q in eligible_questions(service, MandateStore(project)) if q.question_id == qid
    ]  # reste à l'humain


def test_diverging_from_the_recommendation_needs_a_justification_and_confidence(project: Path) -> None:
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(project, "Quelle voie ?", options=("A", "B"), recommended="A")
    with pytest.raises(GrimoireMissionError, match="confiance"):
        answer(service, tid, qid, "B")
    answer(service, tid, qid, "B", justification="la recommandation ignore le critère 2", confidence="high")
    (decided,) = events(service, tid, DECIDED_EVENT)
    assert decided.payload["recommendation_source"] == "judgment" and decided.payload["recommended"] == "A"


# ── Présentations sans borne, tâches faites à la main ───────────────────────────────────────────────────


def test_hand_made_tasks_are_eligible_only_with_the_pilot(project: Path) -> None:
    grant(project)
    service, tid, qid = asks(project, "Quelle couleur ?", options=("bleu", "vert"))
    store = MandateStore(project)
    assert eligible_questions(service, store) == []  # jamais lancée par le tick
    pilot_on(project)
    (found,) = eligible_questions(service, store)
    assert (found.task_id, found.question_id) == (tid, qid)


def test_presentations_are_no_longer_bounded_with_the_pilot(project: Path) -> None:
    grant(project)
    service, tid, qid = asks(project, "Quelle couleur ?", options=("bleu", "vert"))
    stage = OrchestratorStage(service, load_orchestrator_policy(project).policy)  # type: ignore[arg-type]
    now = datetime.now(UTC).isoformat()
    for n in range(MAX_PRESENTATIONS + 1):
        service.ledger.append_event(
            ORCH_LAUNCHED_EVENT,
            "orchestrator",
            "orchestrator",
            "orchestrator",
            {
                "session": f"s{n}",
                "session_sha256": f"d{n}",
                "model": "sonnet",
                "cwd": str(project),
                "mandate_id": "x",
                "questions": [{"task_id": tid, "question_id": qid}],
                "at": now,
            },
        )
        service.ledger.append_event(
            ORCH_ENDED_EVENT, "orchestrator", "orchestrator", "orchestrator", {"session": f"s{n}", "reason": "finished"}
        )
    assert (tid, qid) in stage._not_relisted()  # bornée aujourd'hui
    pilot_on(project)
    assert (tid, qid) not in stage._not_relisted()  # sans borne pilote activé


def test_the_prompt_tells_the_session_how_to_decide_only_under_the_pilot(project: Path) -> None:
    grant(project)
    service, tid, qid = asks(project, "Quelle couleur ?", options=("bleu", "vert"), recommended="bleu")
    stage = OrchestratorStage(service, load_orchestrator_policy(project).policy)  # type: ignore[arg-type]
    assert "PILOTE AUTOMATIQUE" not in stage._pilot_paragraph()
    pilot_on(project)
    paragraph = stage._pilot_paragraph()
    assert "--confidence" in paragraph and "liste fermée" in paragraph
    item = stage._item(eligible_questions(service, MandateStore(project))[0], {})
    assert item["recommended"] == "bleu" and tid and qid


# ── La CLI : `task ask --recommended`, `task answer --why --confidence`, `autonomy autopilot` ─────────────

runner = CliRunner()


def test_cli_ask_carries_the_recommendation_and_the_reason(project: Path) -> None:
    service = TaskService(project)
    tid = service.add("Correctif", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    result = runner.invoke(app, [
        "task", "ask", tid, "Rendre 404 ou 410 ?", "-o", "404", "-o", "410", "--recommended", "404",
        "--why", "les critères disent 404", "--project-root", str(project), "--actor", "w",
    ])
    assert result.exit_code == 0, result.output
    question = TaskService(project).require(tid).waiting.question  # type: ignore[union-attr]
    assert question.recommended == "404" and question.why == "les critères disent 404"
    bad = runner.invoke(app, ["task", "ask", tid, "Autre ?", "-o", "a", "--recommended", "b", "--project-root", str(project)])
    assert bad.exit_code != 0


def test_cli_autopilot_on_awaits_a_human_turn_and_writes_nothing(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    grant(project)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S-agent")
    out = runner.invoke(app, ["autonomy", "autopilot", "on", "--project-root", str(project)])
    assert out.exit_code == 3, out.output
    assert not AutopilotStore(project).state().active
    status = runner.invoke(app, ["autonomy", "autopilot", "status", "--project-root", str(project)])
    assert status.exit_code == 0 and "inactif" in status.output


def test_cli_autopilot_needs_an_active_mandate(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S-agent")
    out = runner.invoke(app, ["autonomy", "autopilot", "on", "--project-root", str(project)])
    assert out.exit_code in (1, 3), out.output
    assert not AutopilotStore(project).state().active


def test_a_registered_session_answers_by_the_cli_with_its_justification_and_confidence(
    project: Path, monkeypatch: pytest.MonkeyPatch, no_tick: None
) -> None:
    """De bout en bout : la CLI d'une session inscrite (sans tour humain), sur une tâche faite à la main, pilote activé."""
    import hashlib

    from grimoire.missions.orchestrator_session import SESSION_TIMEOUT

    monkeypatch.chdir(project)  # hors de tout worktree de tâche, d'où qu'on lance la suite
    grant(project)
    pilot_on(project)
    service, tid, qid = asks(project, "Quelle couleur ?", options=("bleu", "vert"))
    session = "orch-session"
    md.register_orchestrator_session(
        project, session, channels=(md.ANSWER_CHANNEL,), expires_at=(datetime.now(UTC) + SESSION_TIMEOUT).isoformat()
    )
    service.ledger.append_event(
        ORCH_LAUNCHED_EVENT, "orchestrator", "orchestrator", "orchestrator",
        {"session": "f0f0f0f0", "session_sha256": hashlib.sha256(session.encode()).hexdigest(),
         "questions": [{"task_id": tid, "question_id": qid, "kind": "choice", "gesture": "answer"}]},
    )
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.setenv(md.PRINCIPAL_ENV, "orchestrator")
    monkeypatch.setenv(md.PROVENANCE_ENV, "rule:critere-1")
    base = ["task", "answer", tid, "bleu", "--question", qid, "--project-root", str(project)]
    low = runner.invoke(app, [*base, "--why", WHY, "--confidence", "low"])
    assert low.exit_code == 1 and "confiance basse" in low.output
    assert TaskService(project).require(tid).status is TaskState.WAITING
    done = runner.invoke(app, [*base, "--why", WHY, "--confidence", "high"])
    assert done.exit_code == 0, done.output
    (decided,) = events(TaskService(project), tid, DECIDED_EVENT)
    assert decided.payload["option"] == "bleu" and decided.payload["confidence"] == "high"
