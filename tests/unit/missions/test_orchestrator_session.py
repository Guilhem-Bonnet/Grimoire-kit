"""#962 : sous mandat, une session orchestrateur lancée par le tick répond aux questions des workers.

Contre un faux ``claude`` (:class:`lot2_doubles.FakeHost`, qui liste les sessions ``--bg`` pour ``claude agents
--json``) et la vraie CLI ``task answer``. Critères : une session lancée seulement quand une question éligible attend et
qu'un mandat la couvre ; sa vraie identité inscrite en rôle ``orchestrator``, bornée à ``answer`` et ``resume`` ; sa
réponse acceptée de bout en bout et journalisée avec sa provenance ; refus pour toute autre session, tout geste ou type
hors mandat, toute question réservée à l'humain ; jamais d'acceptation, de fusion ni d'approbation de check.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, make_repo
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import mandate as md
from grimoire.missions.breaker import read_breaker
from grimoire.missions.launcher import launched_sessions
from grimoire.missions.mandate import MandateRefusedError, MandateRequest, MandateStore, require_authority
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.orchestrator_session import (
    ORCH_ENDED_EVENT,
    ORCH_LAUNCH_FAILED_EVENT,
    ORCH_LAUNCHED_EVENT,
    SESSION_GESTURES,
    SESSION_NAME,
    SESSION_TIMEOUT,
)
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT, Question, Waiting, new_question_id

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
CRITERIA = ("la route rend 404 sur un id inconnu",)
PROV = {"kind": "rule", "ref": "critere-1"}
runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un journal d'autorité neuf par test, et aucune session ni principal hérités du poste."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED",
                md.PRINCIPAL_ENV, md.PROVENANCE_ENV):
        monkeypatch.delenv(var, raising=False)
    return home


def write_policy(root: Path, *, orchestrator: bool = True, wip: int = 1) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      model: sonnet\n"
        "      permission_mode: acceptEdits\n"
        f"    orchestrator:\n      enabled: {str(orchestrator).lower()}\n      wip: {wip}\n"
        "      permission_mode: acceptEdits\n",
        encoding="utf-8",
    )


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    write_policy(root)
    return root


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


def grant(root: Path, gestures: tuple[str, ...] = ("answer", "resume"), types: tuple[str, ...] = ("implementation",)) -> md.Mandate:
    request = MandateRequest.build(gestures, types, 10, "+1d")
    return MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")


def ready(service: TaskService, title: str = "route 404") -> str:
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(title="#962", origin="test")
    task = service.ledger.create_task(mission.id, title, acceptance=CRITERIA, owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def tick(projet: Path, host: FakeHost, now: datetime | None = None):  # type: ignore[no-untyped-def]
    return run_tick(projet, apply=True, exec_=host, now=now)


def worker_asks(projet: Path, host: FakeHost, text: str = "Rendre 404 ou 410 ?", *, options: tuple[str, ...] = ("404", "410"),
                **ask: object) -> tuple[str, str]:
    """Une tâche lancée par le tick dont le worker pose une question : ``(tâche, question)``."""
    tid = ready(TaskService(projet))
    tick(projet, host)
    assert tid in launched_sessions(TaskService(projet))
    service = TaskService(projet)
    service.ask(tid, text, "worker", options=options, **ask)  # type: ignore[arg-type]
    return tid, service.require(tid).waiting.question.id  # type: ignore[union-attr]


def reaped_resume(projet: Path, host: FakeHost) -> tuple[str, str]:
    """Une tâche lancée garée par la récolte sur « reprendre ? » (``resume_unfenced``, origine ``tick``)."""
    tid = ready(TaskService(projet))
    tick(projet, host)
    service = TaskService(projet)
    task = service.require(tid)
    moment = datetime.now(UTC).isoformat()
    question = Question(new_question_id(), "resume_unfenced", f"la session de {tid} ne donne plus signe ; reprendre ?",
                        ("reprendre", "abandonner"), "tick", moment)
    service.ledger.transition_task(
        tid, TaskState.WAITING, actor_id="tick", reason="bail perdu",
        extra_payload={"waiting": Waiting("human", "", question, None, moment).to_dict()},
        lease_epoch=task.claim.lease_epoch,  # type: ignore[union-attr]
    )
    return tid, question.id


def orchestrator_argv(host: FakeHost) -> list[str]:
    return next(a for a in reversed(host.of("claude", "--bg")) if a[a.index("--name") + 1] == SESSION_NAME)


def as_session(monkeypatch: pytest.MonkeyPatch, session_id: str, provenance: str = "rule:critere-1") -> None:
    """L'environnement d'une commande lancée dans une session de fond : hôte, sans humain, principal revendiqué."""
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session_id)
    monkeypatch.setenv(md.PRINCIPAL_ENV, "orchestrator")
    monkeypatch.setenv(md.PROVENANCE_ENV, provenance)


def answer(projet: Path, tid: str, text: str, qid: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["task", "answer", tid, text, "--question", qid, "--project-root", str(projet)])


def answered(projet: Path, tid: str) -> list[dict[str, object]]:
    return [dict(e.payload) for e in TaskService(projet).ledger.events_for(tid) if e.event_type == HUMAN_ANSWERED_EVENT]


def orch_events(projet: Path, kind: str) -> list[dict[str, object]]:
    return [dict(e.payload) for e in TaskService(projet).ledger.events_for("orchestrator") if e.event_type == kind]


# ── Politique ─────────────────────────────────────────────────────────────────


def test_le_role_orchestrator_se_lit_desactive_par_defaut_et_exige_un_mode(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    default = load_orchestrator_policy(root).policy
    assert default is not None and default.orchestrator.enabled is False and default.orchestrator.model == "sonnet"
    assert default.to_dict()["orchestrator"]["wip"] == 1
    write_policy(root)
    role = load_orchestrator_policy(root).policy.orchestrator  # type: ignore[union-attr]
    assert role.enabled and role.permission_mode == "acceptEdits" and role.model == "sonnet"
    (root / POLICY).write_text("orchestrator:\n  roles:\n    orchestrator:\n      enabled: true\n", encoding="utf-8")
    assert "permission_mode est obligatoire" in load_orchestrator_policy(root).error
    (root / POLICY).write_text("orchestrator:\n  roles:\n    orchestrator:\n      tier: cheap\n", encoding="utf-8")
    assert "clé inconnue tier" in load_orchestrator_policy(root).error


# ── Lancement ─────────────────────────────────────────────────────────────────


def test_une_question_de_worker_sous_mandat_lance_une_session_inscrite(projet: Path, host: FakeHost) -> None:
    mandate = grant(projet)
    tid, qid = worker_asks(projet, host)
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.orchestrator] == [("launch", "launched")]
    argv = orchestrator_argv(host)
    _, cwd, env = next(c for c in host.calls if c[0] == argv)
    # La racine du projet, pas un worktree ; aucune identité dans l'environnement (#966).
    assert cwd == projet and env == {"GRIMOIRE_UNATTENDED": "1"} and "--session-id" not in argv
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "Bash(gh pr merge *)" in argv[argv.index("--disallowed-tools") + 1]
    prompt = argv[-1]
    assert tid in prompt and qid in prompt and "Rendre 404 ou 410 ?" in prompt and "404 | 410" in prompt
    assert CRITERIA[0] in prompt and "GRIMOIRE_PRINCIPAL=orchestrator GRIMOIRE_PROVENANCE=rule:" in prompt
    assert "--question <question>" in prompt and "geste answer" in prompt
    # La vraie identité, lue dans `claude agents` après le lancement, inscrite bornée à answer et resume.
    real = host.session_of(SESSION_NAME)
    [entry] = MandateStore(projet).sessions(role="orchestrator")
    assert entry["session_id"] == real and entry["mandate_id"] == mandate.id
    assert entry["gestures"] == sorted(SESSION_GESTURES)
    [launch] = orch_events(projet, ORCH_LAUNCHED_EVENT)
    assert launch["questions"] == [{"task_id": tid, "question_id": qid, "kind": "choice", "gesture": "answer"}]
    assert launch["session"] == report.orchestrator[0].session and launch["mandate_id"] == mandate.id


@pytest.mark.parametrize("case", ["sans_mandat", "role_desactive", "aucune_question", "geste_hors_mandat",
                                  "type_hors_mandat"])
def test_rien_n_est_lance_sans_question_eligible_couverte(projet: Path, host: FakeHost, case: str) -> None:
    if case == "role_desactive":
        write_policy(projet, orchestrator=False)
    if case != "sans_mandat":
        grant(projet, gestures=("resume",) if case == "geste_hors_mandat" else ("answer", "resume"),
              types=("test",) if case == "type_hors_mandat" else ("implementation",))
    if case == "aucune_question":
        tid = ready(TaskService(projet))
        tick(projet, host)
        assert tid in launched_sessions(TaskService(projet))
    else:
        worker_asks(projet, host)
    report = tick(projet, host)
    assert report.orchestrator == ()
    assert not [a for a in host.of("claude", "--bg") if SESSION_NAME in a]
    assert "orchestrator" not in report.to_dict()


def test_une_session_vivante_tient_le_wip(projet: Path, host: FakeHost) -> None:
    grant(projet)
    worker_asks(projet, host)
    tick(projet, host)
    worker_asks(projet, host, "Quel format de date ?", options=("ISO", "RFC 2822"))
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.orchestrator] == [("launch", "skipped")]
    assert "WIP de l'orchestrateur atteint (1/1)" in report.orchestrator[0].detail
    assert len([a for a in host.of("claude", "--bg") if SESSION_NAME in a]) == 1


def test_les_questions_reservees_a_l_humain_ne_sont_pas_listees(projet: Path, host: FakeHost) -> None:
    grant(projet)
    _flow_tid, flow_q = worker_asks(projet, host, "Checkpoint destructif ?", options=("approve", "reject"), origin="flow")
    _perm_tid, perm_q = worker_asks(projet, host, "Écrire dans /etc ?", options=(), kind="permission")
    _tid, qid = worker_asks(projet, host)
    tick(projet, host)
    prompt = orchestrator_argv(host)[-1]
    assert qid in prompt and flow_q not in prompt and perm_q not in prompt
    assert [q["question_id"] for q in orch_events(projet, ORCH_LAUNCHED_EVENT)[0]["questions"]] == [qid]  # type: ignore[index]


def test_une_reprise_de_la_recolte_est_listee_sous_le_geste_resume(projet: Path, host: FakeHost) -> None:
    grant(projet)
    _tid, qid = reaped_resume(projet, host)
    tick(projet, host)
    prompt = orchestrator_argv(host)[-1]
    assert qid in prompt and "geste resume" in prompt and "session du worker : au travail" in prompt


# ── Inscription : échec fermé ─────────────────────────────────────────────────


def test_une_inscription_refusee_arrete_la_session(projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    grant(projet)
    worker_asks(projet, host)

    def refuse(*_a: object, **_k: object) -> None:
        raise MandateRefusedError("principal_unattested", "refus simulé")

    monkeypatch.setattr(md, "register_orchestrator_session", refuse)
    report = tick(projet, host)
    [outcome] = report.orchestrator
    assert (outcome.action, outcome.status) == ("launch", "failed") and "refus simulé" in outcome.detail
    short = outcome.session
    assert ["claude", "stop", short] in host.of("claude", "stop") and short in host.stopped
    assert not orch_events(projet, ORCH_LAUNCHED_EVENT)
    [failed] = orch_events(projet, ORCH_LAUNCH_FAILED_EVENT)
    assert failed["reason"] == "refused" and failed["halted"] is True
    assert read_breaker(projet).failures == 1 and MandateStore(projet).sessions(role="orchestrator") == []
    assert any("orchestrateur" in line for line in report.attention)


def test_une_identite_introuvable_arrete_la_session(projet: Path, host: FakeHost) -> None:
    grant(projet)
    worker_asks(projet, host)
    host.agents = [{"id": "ffffffff", "kind": "background", "sessionId": "ffffffff-0000-0000-0000-000000000000"}]
    [outcome] = tick(projet, host).orchestrator
    assert outcome.status == "failed" and "identité non résolue" in outcome.detail
    assert host.of("claude", "stop") and MandateStore(projet).sessions(role="orchestrator") == []


# ── Fin de session ────────────────────────────────────────────────────────────


def test_une_session_trop_longue_est_arretee_et_ses_questions_restent_a_l_humain(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, qid = worker_asks(projet, host)
    [launched] = tick(projet, host).orchestrator
    later = datetime.now(UTC) + SESSION_TIMEOUT + timedelta(minutes=1)
    report = tick(projet, host, now=later)
    [ended] = report.orchestrator  # la question, déjà présentée, n'est pas relistée : aucune relance
    assert (ended.action, ended.status) == ("end", "done") and f"{tid}/{qid}" in ended.detail
    assert launched.session in host.stopped and orch_events(projet, ORCH_ENDED_EVENT)[0]["reason"] == "timeout"
    assert any("laissées à l'humain" in line for line in report.attention)
    assert tick(projet, host, now=later).orchestrator == ()


def test_une_session_finie_libere_le_wip(projet: Path, host: FakeHost) -> None:
    grant(projet)
    worker_asks(projet, host)
    [launched] = tick(projet, host).orchestrator
    host.stopped.add(launched.session)  # la session a rendu la main : `claude agents` ne la liste plus
    [(action, status)] = [(o.action, o.status) for o in tick(projet, host).orchestrator]
    assert (action, status) == ("end", "done") and orch_events(projet, ORCH_ENDED_EVENT)[0]["reason"] == "finished"
    _tid2, qid2 = worker_asks(projet, host, "Quel format de date ?", options=("ISO", "RFC 2822"))
    assert [(o.action, o.status) for o in tick(projet, host).orchestrator] == [("launch", "launched")]
    assert qid2 in orchestrator_argv(host)[-1]


# ── La réponse, de bout en bout, par la vraie CLI ─────────────────────────────


def test_la_session_inscrite_repond_par_la_cli_avec_sa_provenance(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    mandate = grant(projet)
    tid, qid = worker_asks(projet, host)
    tick(projet, host)
    as_session(monkeypatch, host.session_of(SESSION_NAME))
    out = answer(projet, tid, "404", qid)
    assert out.exit_code == 0, out.output
    assert TaskService(projet).require(tid).status is TaskState.READY
    [event] = answered(projet, tid)
    assert event["origin"] == ORIGIN_ORCHESTRATOR and event["mandate_id"] == mandate.id and event["provenance"] == PROV
    [gesture] = MandateStore(projet).gestures_taken(tid)
    assert gesture["gesture"] == "answer" and gesture["provenance"] == PROV and gesture["origin"] == "orchestrator"


def test_la_session_reprend_une_tache_recoltee_sous_le_geste_resume(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant(projet)
    tid, qid = reaped_resume(projet, host)
    tick(projet, host)
    as_session(monkeypatch, host.session_of(SESSION_NAME), "rule:worker-absent")
    out = answer(projet, tid, "reprendre", qid)
    assert out.exit_code == 0, out.output
    [gesture] = MandateStore(projet).gestures_taken(tid)
    assert gesture["gesture"] == "resume" and gesture["provenance"] == {"kind": "rule", "ref": "worker-absent"}


def _orchestrator_enrolled(projet: Path, host: FakeHost, gestures: tuple[str, ...] = ("answer", "resume"),
                           types: tuple[str, ...] = ("implementation",)) -> tuple[str, str]:
    """Une question de worker et une session orchestrateur inscrite (comme l'étape la laisse) sous ce mandat."""
    grant(projet, gestures, types)
    tid, qid = worker_asks(projet, host)
    md.register_orchestrator_session(projet, "orch-1", gestures=SESSION_GESTURES)
    return tid, qid


@pytest.mark.parametrize(
    ("who", "needle"),
    [("worker", "attesté"), ("reviewer", "attesté"), ("inconnue", "attesté")],
)
def test_aucune_autre_session_ne_repond_en_orchestrateur(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch, who: str, needle: str
) -> None:
    tid, qid = _orchestrator_enrolled(projet, host)
    session = {"worker": host.session_of(f"grimoire-{tid}"), "inconnue": "sess-inconnue", "reviewer": "rev-1"}[who]
    if who == "reviewer":
        MandateStore(projet).register_session("rev-1", "reviewer", tid)
    as_session(monkeypatch, session)
    out = answer(projet, tid, "404", qid)
    assert out.exit_code == 1 and needle in out.output, out.output
    assert answered(projet, tid) == [] and MandateStore(projet).gestures_taken() == []


@pytest.mark.parametrize(
    ("gestures", "types", "needle"),
    [(("resume",), ("implementation",), "ne couvre pas ce geste"), (("answer", "resume"), ("test",), "ne couvre pas le type")],
)
def test_un_geste_ou_un_type_hors_mandat_est_refuse(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch, gestures: tuple[str, ...], types: tuple[str, ...],
    needle: str,
) -> None:
    tid, qid = _orchestrator_enrolled(projet, host, gestures, types)
    as_session(monkeypatch, "orch-1")
    out = answer(projet, tid, "404", qid)
    assert out.exit_code == 1 and needle in out.output, out.output
    assert answered(projet, tid) == []


@pytest.mark.parametrize(
    ("ask", "text", "needle"),
    [
        ({"origin": "flow"}, "approve", "réservée à l'humain"),
        ({"kind": "permission"}, "oui", "réservée à l'humain"),
        ({}, "abandonner", "annule la tâche"),
    ],
)
def test_une_question_reservee_ou_un_abandon_est_refuse_a_l_orchestrateur(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch, ask: dict[str, str], text: str, needle: str
) -> None:
    grant(projet)
    tid, qid = worker_asks(projet, host, "Que faire ?", options=("approve", "oui", "abandonner"), **ask)
    md.register_orchestrator_session(projet, "orch-1", gestures=SESSION_GESTURES)
    as_session(monkeypatch, "orch-1")
    out = answer(projet, tid, text, qid)
    assert out.exit_code == 1 and needle in out.output, out.output
    # Refusé avant le mandat : le journal ne dit jamais « pris » d'un geste refusé.
    assert answered(projet, tid) == [] and MandateStore(projet).gestures_taken() == []
    assert TaskService(projet).require(tid).status is TaskState.WAITING


@pytest.mark.parametrize("asker", ["merger", "reviewer"])
def test_une_escalade_du_merger_reste_a_l_humain_celle_du_relecteur_est_une_reprise(
    projet: Path, host: FakeHost, asker: str
) -> None:
    grant(projet)
    tid = ready(TaskService(projet))
    tick(projet, host)
    service = TaskService(projet)
    service.ask(tid, f"Désaccord persistant ({asker}) — reprendre ou abandonner ?", asker, options=("reprendre", "abandonner"))
    qid = service.require(tid).waiting.question.id  # type: ignore[union-attr]
    report = tick(projet, host)
    if asker == "merger":
        assert report.orchestrator == ()
        with md.acting_as_orchestrator(), pytest.raises(GrimoireMissionError, match="merger"):
            service.answer(tid, "reprendre", "orch", origin=ORIGIN_ORCHESTRATOR, question_id=qid, provenance=PROV)
    else:
        assert qid in orchestrator_argv(host)[-1] and "geste resume" in orchestrator_argv(host)[-1]


def test_la_session_orchestrateur_n_accepte_ne_fusionne_ni_n_approuve(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mandat et politique délèguent accept, merge et approve-check : la session inscrite par l'étape ne les prend pas."""
    with (projet / POLICY).open("a", encoding="utf-8") as fh:
        fh.write("escalation:\n  accept: orchestrator\n  merge: orchestrator\n")
    grant(projet, gestures=("accept", "answer", "approve-check", "resume", "merge", "queue-release"))
    tid, _qid = worker_asks(projet, host)
    tick(projet, host)
    session = host.session_of(SESSION_NAME)
    as_session(monkeypatch, session)
    task = TaskService(projet).require(tid)
    for gesture in ("accept", "merge", "approve-check", "queue-release"):
        with pytest.raises(MandateRefusedError) as refused:
            require_authority(projet, gesture, task, ORIGIN_ORCHESTRATOR, what="x", provenance=PROV)
        assert refused.value.code == "gesture_out_of_session_scope", refused.value
    assert require_authority(projet, "answer", task, ORIGIN_ORCHESTRATOR, what="x", provenance=PROV, record=False)
    out = runner.invoke(app, ["task", "accept", tid, "--reason", "relu", "--project-root", str(projet)])
    assert out.exit_code == 1 and "answer, resume seulement" in out.output, out.output
    assert {g["gesture"] for g in MandateStore(projet).gestures_taken()} <= {"answer", "resume"}
