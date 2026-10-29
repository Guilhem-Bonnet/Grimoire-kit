"""Lot 3 de #876 (verrous 7 et 8) : le relecteur, contre un faux ``claude``, et le verdict structuré par critère.

Critères : une session distincte relit chaque tâche lancée qui arrive en revue (autre identifiant, autre modèle si
possible, inscrite au journal d'autorité sous mandat) ; son verdict par critère est validé par du code et alimente la
couverture du pack ; un reçu écrit par le worker est un verdict forgé (disjoncteur, mandat révoqué) ; un NOT-READY
renvoie au worker, puis escalade à l'humain au cycle ``max_cycles``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, make_repo
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.missions import mandate as md
from grimoire.missions.breaker import read_breaker
from grimoire.missions.gates import _resolve_evidence_gate
from grimoire.missions.launcher import launched_sessions
from grimoire.missions.mandate import ORIGIN_HUMAN, MandateRequest, MandateStore
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.review_receipt import (
    CRITERIA_CHANGED,
    CriterionVerdict,
    ReviewFinding,
    ReviewReceiptError,
    check_receipt,
    read_receipt,
    validate_criteria,
    write_receipt,
)
from grimoire.missions.review_verdict import REVIEW_RECORDED_EVENT
from grimoire.missions.reviewer import REVIEW_FORGED_EVENT, REVIEW_JUDGED_EVENT, REVIEW_LAUNCHED_EVENT, session_digest
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
EVIDENCE = Path("_grimoire-runtime-output/evidence")
CRITERIA = ("la route rend 404 sur un id inconnu", "relu par un autre agent")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un journal d'autorité neuf par test, et aucune session héritée du poste."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", md.PRINCIPAL_ENV, md.PROVENANCE_ENV):
        monkeypatch.delenv(var, raising=False)
    return home


def write_policy(root: Path, *, reviewer: bool = True, max_cycles: int = 2, reviewer_model: str = "") -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    review = (
        f"    reviewer:\n      enabled: {str(reviewer).lower()}\n      max_cycles: {max_cycles}\n"
        "      permission_mode: acceptEdits\n" + (f"      model: {reviewer_model}\n" if reviewer_model else "")
    )
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      model: sonnet\n"
        f"      permission_mode: acceptEdits\n{review}",
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


def ready(service: TaskService, title: str = "route 404", acceptance: tuple[str, ...] = CRITERIA) -> str:
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(title="Lot 3", origin="test")
    task = service.ledger.create_task(mission.id, title, acceptance=acceptance, owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def tick(projet: Path, host: FakeHost):  # type: ignore[no-untyped-def]
    return run_tick(projet, apply=True, exec_=host)


def to_review(projet: Path, tid: str) -> None:
    ledger = TaskService(projet).ledger
    if ledger.get_task(tid).status is not TaskState.RUNNING:  # type: ignore[union-attr]
        ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")


def launched_and_in_review(projet: Path, host: FakeHost) -> tuple[str, dict[str, object]]:
    tid = ready(TaskService(projet))
    tick(projet, host)
    to_review(projet, tid)
    return tid, launched_sessions(TaskService(projet))[tid]


def review_event(projet: Path, tid: str):  # type: ignore[no-untyped-def]
    return [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_LAUNCHED_EVENT][-1]


def reviewer_sid(host: FakeHost) -> str:
    """Le vrai identifiant du dernier relecteur lancé, tel que ``claude agents`` le rend : le ledger n'en garde que l'empreinte."""
    return host.session_of("grimoire-review-")


def receipt_from(worktree: Path, tid: str, verdict: str, session: str, *, reviewer: str = "reviewer") -> None:
    findings = [ReviewFinding("la route rend 500", "pytest tests/test_route.py -q")] if verdict == "NOT-READY" else []
    write_receipt(worktree, tid, verdict, findings=findings, reviewer_id=reviewer, reviewer_session=session)


# ── Verdict par critère, validé par du code (verrou 8) ─────────────────────────


def met(index: int, cmd: str = "pytest -q", note: str = "") -> CriterionVerdict:
    return CriterionVerdict(index, "", "met", cmd, note)


def test_un_verdict_complet_est_rendu_avec_le_texte_des_criteres() -> None:
    judged = validate_criteria(CRITERIA, (met(2, "", "diff relu"), met(1)), "READY")
    assert [c.criterion for c in judged] == list(CRITERIA) and [c.index for c in judged] == [1, 2]


@pytest.mark.parametrize(
    ("criteria", "verdict", "needle"),
    [
        ((met(1),), "READY", "non jugé"),
        ((met(1), met(1), met(2)), "READY", "deux fois"),
        ((met(1), met(3)), "READY", "hors de la tâche"),
        ((met(1), CriterionVerdict(2, "un autre critère", "met", "pytest")), "READY", "pas celui de la tâche"),
        ((met(1), CriterionVerdict(2, "", "peut-être", "pytest")), "READY", "statut"),
        ((met(1), met(2, "", "")), "READY", "sans preuve"),
        ((met(1), CriterionVerdict(2, "", "unmet", "")), "NOT-READY", "sans commande"),
        ((met(1), CriterionVerdict(2, "", "unverifiable")), "NOT-READY", "sans note"),
        ((met(1), CriterionVerdict(2, "", "unmet", "pytest")), "READY", "READY alors"),
        ((met(1), met(2)), "NOT-READY", "NOT-READY alors"),
    ],
)
def test_un_verdict_incoherent_est_refuse(criteria: tuple[CriterionVerdict, ...], verdict: str, needle: str) -> None:
    with pytest.raises(ReviewReceiptError, match=needle):
        validate_criteria(CRITERIA, criteria, verdict)


def _checks_pack(root: Path, tid: str) -> None:
    """Ce que `task verify` laisse : un pack de commandes, critères non couverts, et un verdict vert sur les commandes."""
    from grimoire.evidence.schemas import VerdictResult, VerificationCheck

    service = EvidenceService(root / EVIDENCE)
    item = EvidenceItem.from_text("check-01", EvidenceKind.TEST, "pytest -> exit 0", uri="check:pytest", summary="pytest -> exit 0")
    pack = service.create_pack(tid, EvidenceProfile.LIGHT, [item], acceptance=CRITERIA)
    service.verify(pack, extra_checks=(VerificationCheck(id="command-01", result=VerdictResult.PASSED, reason=""),))


def _verdict_file(tmp_path: Path, verdict: str, statuses: tuple[str, str]) -> Path:
    criteria = [
        {"index": i, "status": s, "evidence_cmd": "pytest tests/test_route.py -q", "note": "lu"} for i, s in enumerate(statuses, 1)
    ]
    path = tmp_path / "verdict.json"
    path.write_text(json.dumps({"$schema": "grimoire-review-verdict/v1", "verdict": verdict, "criteria": criteria}), encoding="utf-8")
    return path


def test_la_relecture_structuree_couvre_les_criteres_tenus_et_rien_d_autre(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_repo(tmp_path)
    service = TaskService(root)
    tid = ready(service)
    _checks_pack(root, tid)
    task = service.require(tid)
    assert _resolve_evidence_gate(root, task, "evidence_gate") is not None  # avant : aucun critère couvert
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "relecteur-1")
    out = runner.invoke(app, ["task", "review", tid, "--criteria", str(_verdict_file(tmp_path, "READY", ("met", "met"))),
                              "--reviewer", "reviewer", "--project-root", str(root)])
    assert out.exit_code == 0, out.output
    assert "2/2 critère(s) tenu(s)" in out.output
    assert _resolve_evidence_gate(root, task, "evidence_gate") is None  # couverts par le verdict, pas par une sous-chaîne
    receipt = read_receipt(root, tid)
    assert receipt is not None and receipt.reviewer_session == "relecteur-1" and receipt.criteria_met == CRITERIA
    assert check_receipt(root, task) is None  # le reçu porte le hash du pack de relecture
    verdict = EvidenceService(root / EVIDENCE).get_latest_verdict(tid)
    assert verdict is not None and verdict.verdict.value == "passed"
    [event] = [e for e in TaskService(root).ledger.events_for(tid) if e.event_type == REVIEW_RECORDED_EVENT]
    assert event.payload["met"] == [1, 2] and event.payload["not_met"] == []


def test_un_verdict_refuse_n_ecrit_ni_pack_ni_recu(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    tid = ready(TaskService(root))
    _checks_pack(root, tid)
    before = len(EvidenceService(root / EVIDENCE).list_packs(tid))
    out = runner.invoke(app, ["task", "review", tid, "--criteria", str(_verdict_file(tmp_path, "READY", ("met", "unmet"))),
                              "--project-root", str(root)])
    assert out.exit_code == 1 and "READY alors" in out.output
    assert len(EvidenceService(root / EVIDENCE).list_packs(tid)) == before and read_receipt(root, tid) is None


def test_une_relecture_ne_rend_pas_vert_un_check_rouge(tmp_path: Path) -> None:
    from grimoire.evidence.schemas import VerdictResult, VerificationCheck

    root = make_repo(tmp_path)
    tid = ready(TaskService(root))
    service = EvidenceService(root / EVIDENCE)
    item = EvidenceItem.from_text("check-01", EvidenceKind.TEST, "pytest -> exit 1", uri="check:pytest", summary="pytest -> exit 1")
    pack = service.create_pack(tid, EvidenceProfile.LIGHT, [item], acceptance=CRITERIA)
    service.verify(pack, extra_checks=(VerificationCheck(id="command-01", result=VerdictResult.FAILED, reason="exit 1"),))
    out = runner.invoke(app, ["task", "review", tid, "--criteria", str(_verdict_file(tmp_path, "READY", ("met", "met"))),
                              "--project-root", str(root)])
    assert out.exit_code == 0, out.output
    assert EvidenceService(root / EVIDENCE).get_latest_verdict(tid).verdict.value == "failed"  # type: ignore[union-attr]


def test_des_criteres_reecrits_perime_le_recu(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    service = TaskService(root)
    tid = ready(service)
    _checks_pack(root, tid)
    runner.invoke(app, ["task", "review", tid, "--criteria", str(_verdict_file(tmp_path, "READY", ("met", "met"))),
                        "--project-root", str(root)])
    task = service.require(tid)
    assert check_receipt(root, task) is None
    rewritten = replace(task, acceptance=(CRITERIA[0], "relu par un humain"))
    refusal = check_receipt(root, rewritten)
    assert refusal is not None and refusal.evidence == CRITERIA_CHANGED


# ── Le relecteur lancé par le tick (verrou 7) ──────────────────────────────────


def test_une_tache_en_revue_recoit_une_session_de_relecture_distincte(projet: Path, host: FakeHost) -> None:
    tid, launch = launched_and_in_review(projet, host)
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("launch", "launched")]
    worker_argv, review_argv = host.of("claude", "--bg")
    assert review_argv[review_argv.index("--name") + 1] == f"grimoire-review-{tid}"
    # #966 : les identités sont celles que `claude agents` rend, jamais un `--session-id` que `--bg` ignore.
    assert "--session-id" not in worker_argv + review_argv
    reviewer = host.session_of(f"grimoire-review-{tid}")
    assert reviewer != launch["session_id"] == host.session_of(f"grimoire-{tid}")
    assert review_event(projet, tid).payload["session_sha256"] == session_digest(reviewer)
    assert review_argv[review_argv.index("--model") + 1] == "opus"  # le worker est en sonnet : un autre modèle
    _, cwd, env = [c for c in host.calls if c[0][:2] == ["claude", "--bg"]][-1]
    # Aucune identité dans l'environnement : le démon des sessions de fond la transmettrait à toutes (#966).
    assert cwd == Path(str(launch["cwd"])) and env == {"GRIMOIRE_UNATTENDED": "1"}
    prompt = review_argv[-1]
    assert f"grimoire task review {tid} --criteria" in prompt and "raisonnement du worker" in prompt
    assert not {"--resume", "--continue", "--agent", "--agents"} & set(review_argv)
    tick(projet, host)  # rejouable : une relecture par entrée en revue
    assert len(host.of("claude", "--bg")) == 2


def test_le_modele_impose_par_la_politique_prime(projet: Path, host: FakeHost) -> None:
    write_policy(projet, reviewer_model="sonnet")
    launched_and_in_review(projet, host)
    tick(projet, host)
    review_argv = host.of("claude", "--bg")[-1]
    assert review_argv[review_argv.index("--model") + 1] == "sonnet"


def test_relecteur_desactive_rien_n_est_relu(projet: Path, host: FakeHost) -> None:
    write_policy(projet, reviewer=False)
    launched_and_in_review(projet, host)
    report = tick(projet, host)
    assert report.reviews == () and len(host.of("claude", "--bg")) == 1 and "reviews" not in report.to_dict()


def test_un_recu_ready_du_relecteur_est_note_et_sa_session_arretee(projet: Path, host: FakeHost) -> None:
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    review = review_event(projet, tid)
    receipt_from(Path(str(launch["cwd"])), tid, "READY", reviewer_sid(host))
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("ready", "done")]
    assert host.review_stops() == [["claude", "stop", review.payload["session"]]]
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION  # l'acceptation est au lot suivant
    assert tick(projet, host).reviews == ()  # jugé une fois


def test_un_recu_ecrit_par_le_worker_est_un_verdict_forge(projet: Path, host: FakeHost) -> None:
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "READY", str(launch["session_id"]))
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("forged", "done")]
    assert read_breaker(projet).open and any("forged" in a for a in report.attention)
    assert any(e.event_type == REVIEW_FORGED_EVENT for e in TaskService(projet).ledger.events_for(tid))
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION


def test_un_recu_d_une_autre_session_ne_fait_rien_bouger(projet: Path, host: FakeHost) -> None:
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "READY", "une-session-humaine", reviewer="guilhem")
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("refused", "refused")]
    assert not read_breaker(projet).open and TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION


def test_le_proprietaire_qui_relit_son_travail_est_refuse_sans_ouvrir_le_disjoncteur(projet: Path, host: FakeHost) -> None:
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "READY", "session-de-amelia", reviewer="amelia")
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("refused", "refused")] and not read_breaker(projet).open


def test_not_ready_renvoie_au_worker_puis_escalade_au_cycle_max(projet: Path, host: FakeHost) -> None:
    tid, launch = launched_and_in_review(projet, host)
    worktree = Path(str(launch["cwd"]))
    tick(projet, host)
    receipt_from(worktree, tid, "NOT-READY", reviewer_sid(host))
    first = tick(projet, host)
    assert [(o.action, o.status) for o in first.reviews] == [("bounce", "done")]
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.RUNNING and any("la route rend 500" in d.text for d in task.directives)
    resume = host.of("claude", "--bg", "--resume")
    assert len(resume) == 1 and resume[0][3] == launch["session_id"]  # la session du worker, reprise
    # Cycle 2 : le worker remet en revue, une nouvelle relecture est lancée, encore NOT-READY → l'humain.
    to_review(projet, tid)
    tick(projet, host)
    assert len([e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_LAUNCHED_EVENT]) == 2
    receipt_from(worktree, tid, "NOT-READY", reviewer_sid(host))
    second = tick(projet, host)
    assert [(o.action, o.status) for o in second.reviews] == [("escalate", "done")]
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.WAITING and task.waiting is not None and task.waiting.reason == "human"
    assert task.waiting.question is not None and "Désaccord persistant" in task.waiting.question.text
    v2 = task.waiting.question
    assert v2.title == "Reprendre la tâche ou l'abandonner ?" and tid not in v2.title + v2.context  # P92 : sans identifiant
    assert [(c.value, c.danger) for c in v2.choices] == [("reprendre", False), ("abandonner", True)]
    assert all(c.consequence for c in v2.choices) and v2.options == ("reprendre", "abandonner")
    assert len(host.of("claude", "--bg", "--resume")) == 1  # pas de troisième tour sans l'humain
    cycles = [e.payload["cycle"] for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_JUDGED_EVENT]
    assert cycles == [1, 2]


# ── Sous mandat : les sessions inscrites au journal d'autorité ─────────────────


def _grant(root: Path) -> md.Mandate:
    request = MandateRequest.build(("accept",), ("implementation",), 5, "+1d")
    return MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")


def test_sous_mandat_worker_et_relecteur_sont_inscrits_et_scelles_sous_leur_vraie_identite(projet: Path, host: FakeHost) -> None:
    """#966 : l'identité inscrite au journal d'autorité, notée au ledger et scellée pour le relecteur est le `sessionId`
    que `claude agents` rend, jamais un uuid passé en `--session-id` (que `claude --bg` ignore)."""
    from grimoire.missions.review_seal import _launch_head

    _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    worker, reviewer = host.session_of(f"grimoire-{tid}"), host.session_of(f"grimoire-review-{tid}")
    store = MandateStore(projet)
    workers = {e["session_id"] for e in store.sessions(role="worker", task_id=tid)}
    reviewers = {e["session_id"] for e in store.sessions(role="reviewer", task_id=tid)}
    assert workers == {launch["session_id"]} == {worker} and reviewers == {reviewer}
    assert review_event(projet, tid).payload["session_sha256"] == session_digest(reviewer)
    assert _launch_head(projet, tid, session_digest(reviewer)) is not None  # le sceau du lancement porte le vrai
    passed = {a[a.index("--session-id") + 1] for a in host.of("claude", "--bg") if "--session-id" in a}
    assert not passed & {worker, reviewer}


def test_le_recu_du_relecteur_sous_sa_vraie_session_est_juge_malgre_l_environnement_du_demon(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le relecteur hérite de l'environnement du démon (celui du premier worker lancé) : son `grimoire task review` signe
    pourtant de sa propre session, et le tick juge ce reçu au lieu de le refuser (« autre session »)."""
    _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    worktree = Path(str(launch["cwd"]))
    tick(projet, host)
    name = f"grimoire-review-{tid}"
    with monkeypatch.context() as m:
        for var, value in host.session_environ(name).items():
            m.setenv(var, value)
        out = runner.invoke(app, ["task", "review", tid, "--criteria", str(_verdict_in(worktree, tid)), "--reviewer", "reviewer",
                                  "--project-root", str(worktree)])
    assert out.exit_code == 0, out.output
    receipt = read_receipt(worktree, tid)
    assert receipt is not None and receipt.reviewer_session == host.session_of(name)
    assert [(o.action, o.status) for o in tick(projet, host).reviews] == [("ready", "done")]
    assert MandateStore(projet).state().active


def test_un_recu_signe_de_la_vraie_session_du_worker_est_forge(projet: Path, host: FakeHost) -> None:
    _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "READY", host.session_of(f"grimoire-{tid}"))
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("forged", "done")] and read_breaker(projet).open
    assert not MandateStore(projet).state().active


def test_un_relecteur_introuvable_est_arrete_sans_rien_inscrire_ni_sceller(projet: Path, host: FakeHost) -> None:
    from grimoire.missions.review_seal import _store

    _grant(projet)
    tid, _ = launched_and_in_review(projet, host)
    host.agents = [dict(s) for s in host.bg]  # figé : le relecteur à venir n'y sera pas
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("launch", "failed")]
    assert "identité non résolue" in report.reviews[0].detail
    short = host.bg[-1]["id"]
    assert host.bg[-1]["name"] == f"grimoire-review-{tid}" and host.review_stops() == [["claude", "stop", short]]
    assert MandateStore(projet).sessions(role="reviewer") == []
    assert not [e for e in _store(projet).journal.read() if e.get("task_id") == tid and e.get("session_sha256")]
    assert not [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_LAUNCHED_EVENT]
    assert read_breaker(projet).failures == 1


def test_sous_mandat_un_worker_introuvable_n_est_pas_inscrit(projet: Path, host: FakeHost) -> None:
    _grant(projet)
    host.agents_fail = True
    tid = ready(TaskService(projet))
    assert tick(projet, host).launches[0].status == "failed"
    assert MandateStore(projet).sessions(role="worker") == [] and host.of("claude", "stop") == [["claude", "stop", "00000001"]]
    assert TaskService(projet).require(tid).status is TaskState.READY


def _refusing(code: str):  # type: ignore[no-untyped-def]
    def refuse(self: MandateStore, session_id: str, role: str, task_id: str = "", now: datetime | None = None) -> None:
        if code == "journal":
            raise OSError("disque plein")
        raise md.MandateRefusedError(code, f"session {session_id!r} refusée")

    return refuse


@pytest.mark.parametrize(("code", "status", "failures"), [("role_conflict", "failed", 1), ("journal", "failed", 1),
                                                          (md.CAP_EXCEEDED, "skipped", 0)])
def test_une_inscription_refusee_apres_le_lancement_arrete_la_session_et_compte_la_tentative(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch, code: str, status: str, failures: int,
) -> None:
    """Une session lancée puis arrêtée est une tentative : notée (plafond du jour) et, sauf refus de plafond (qui révoque
    déjà le mandat), comptée au disjoncteur — sinon chaque tick relancerait une session sans fin."""
    from grimoire.missions.launcher import LAUNCH_FAILED_EVENT, WorkerLauncher
    from grimoire.missions.orchestrator_policy import load_orchestrator_policy

    _grant(projet)
    monkeypatch.setattr(MandateStore, "register_session", _refusing(code))
    tid = ready(TaskService(projet))
    (outcome,) = tick(projet, host).launches
    assert outcome.status == status and "non inscrite" in outcome.detail and "session 00000001 arrêtée" in outcome.detail
    assert host.of("claude", "stop") == [["claude", "stop", "00000001"]]
    after = TaskService(projet)
    assert after.require(tid).status is TaskState.READY and tid not in launched_sessions(after)
    (failed,) = [e for e in after.ledger.events_for(tid) if e.event_type == LAUNCH_FAILED_EVENT]
    assert failed.payload["halted"] is True and failed.payload["session"] == "00000001"
    launcher = WorkerLauncher(after, load_orchestrator_policy(projet).policy, exec_=host)  # type: ignore[arg-type]
    assert launcher._launched_today() == 1  # la tentative compte au plafond du jour
    assert read_breaker(projet).failures == failures


def test_au_plafond_du_mandat_rien_n_est_lance(projet: Path, host: FakeHost) -> None:
    """Relu sans écrire avant de lancer : une inscription certaine d'échouer ne lance pas de session à arrêter."""
    _grant(projet)  # 5 sessions par jour
    store = MandateStore(projet)
    for n in range(5):
        store.register_session(f"autre-{n}", "worker", "ailleurs")
    tid = ready(TaskService(projet))
    (outcome,) = tick(projet, host).launches
    assert outcome.status == "skipped" and "plafond de 5 sessions par jour du mandat atteint" in outcome.detail
    assert host.of("claude", "--bg") == [] and host.of("claude", "stop") == []
    assert TaskService(projet).require(tid).status is TaskState.READY and MandateStore(projet).state().active


def test_un_relecteur_refuse_au_journal_est_arrete_et_n_est_pas_relance(projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions.reviewer import REVIEW_LAUNCH_FAILED_EVENT

    _grant(projet)
    tid, _ = launched_and_in_review(projet, host)
    real = MandateStore.register_session

    def refuse_reviewer(self: MandateStore, session_id: str, role: str, task_id: str = "", now: datetime | None = None) -> None:
        if role == "reviewer":
            raise md.MandateRefusedError("role_conflict", "rôle déjà tenu")
        real(self, session_id, role, task_id, now)

    monkeypatch.setattr(MandateStore, "register_session", refuse_reviewer)
    first = tick(projet, host)
    assert [(o.action, o.status) for o in first.reviews] == [("launch", "failed")]
    short = host.bg[-1]["id"]
    assert host.review_stops() == [["claude", "stop", short]] and read_breaker(projet).failures == 1
    (failed,) = [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_LAUNCH_FAILED_EVENT]
    assert failed.payload["halted"] is True and failed.payload["reason"] == "refused"
    bg = len(host.of("claude", "--bg"))
    second = tick(projet, host)  # pas de relance : l'humain tranche
    assert [(o.action, o.status) for o in second.reviews] == [("escalate", "done")] and len(host.of("claude", "--bg")) == bg
    assert TaskService(projet).require(tid).status is TaskState.WAITING


def test_un_relecteur_non_arrete_bloque_la_relance_jusqu_a_l_arret(projet: Path, host: FakeHost) -> None:
    from grimoire.missions.launcher import ExecResult
    from grimoire.missions.reviewer import REVIEW_HALTED_EVENT, REVIEW_LAUNCH_FAILED_EVENT

    tid, _ = launched_and_in_review(projet, host)
    host.agents = [dict(s) for s in host.bg]  # le relecteur à venir n'y sera pas
    host.stop_result = ExecResult(1, "", "démon injoignable")
    first = tick(projet, host)
    assert [(o.action, o.status) for o in first.reviews] == [("launch", "failed")] and "NON arrêtée" in first.reviews[0].detail
    short = host.bg[-1]["id"]
    (failed,) = [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_LAUNCH_FAILED_EVENT]
    assert failed.payload["halted"] is False and failed.payload["session"] == short
    assert _stage_wip(projet, host) == 1  # le relecteur non arrêté tient un créneau du WIP
    bg = len(host.of("claude", "--bg"))
    second = tick(projet, host)
    assert [(o.action, o.status) for o in second.reviews] == [("launch", "skipped")] and len(host.of("claude", "--bg")) == bg
    assert host.review_stops() == [["claude", "stop", short]] * 2  # l'arrêt est retenté
    host.stop_result, host.agents = ExecResult(0, "stopped\n"), None
    third = tick(projet, host)
    assert [(o.action, o.status) for o in third.reviews] == [("launch", "launched")]
    assert [e.payload["session"] for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_HALTED_EVENT] == [short]


def _stage_wip(projet: Path, host: FakeHost) -> int:
    from grimoire.missions.orchestrator_policy import load_orchestrator_policy
    from grimoire.missions.reviewer import ReviewStage

    return ReviewStage(TaskService(projet), load_orchestrator_policy(projet).policy, exec_=host)._open_reviews()  # type: ignore[arg-type]


def test_sous_mandat_un_verdict_forge_revoque_le_mandat(projet: Path, host: FakeHost) -> None:
    mandate = _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "READY", str(launch["session_id"]))
    report = tick(projet, host)
    assert report.reviews[0].action == "forged" and mandate.id in report.reviews[0].detail
    state = MandateStore(projet).state()
    assert not state.active and state.reason == md.REVOKED


def test_sous_mandat_le_recu_du_relecteur_inscrit_est_accepte(projet: Path, host: FakeHost) -> None:
    _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "READY", reviewer_sid(host))
    assert [(o.action, o.status) for o in tick(projet, host).reviews] == [("ready", "done")]
    assert MandateStore(projet).state().active


# ── Revue adversariale du lot 3 : chaque défaut trouvé, épinglé ─────────────────


def _verdict_in(worktree: Path, tid: str, verdict: str = "READY", statuses: tuple[str, str] = ("met", "met")) -> Path:
    path = worktree / "_grimoire-output" / "reviews" / tid / "verdict.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return _verdict_file(path.parent, verdict, statuses)


def _review_cli(worktree: Path, tid: str, verdict_file: Path, session: str, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", session)
    return runner.invoke(app, ["task", "review", tid, "--criteria", str(verdict_file), "--reviewer", "reviewer",
                               "--project-root", str(worktree)])


def test_le_worker_ne_peut_pas_ecrire_sa_propre_relecture(projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    tid, launch = launched_and_in_review(projet, host)
    worktree = Path(str(launch["cwd"]))
    out = _review_cli(worktree, tid, _verdict_in(worktree, tid), str(launch["session_id"]), monkeypatch)
    assert out.exit_code == 1 and "celle du worker" in out.output
    assert EvidenceService(worktree / EVIDENCE).list_packs(tid) == [] and read_receipt(worktree, tid) is None  # rien d'écrit


def test_sous_mandat_une_session_non_inscrite_ne_peut_pas_relire(projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    worktree = Path(str(launch["cwd"]))
    out = _review_cli(worktree, tid, _verdict_in(worktree, tid), "une-session-quelconque", monkeypatch)
    assert out.exit_code == 1 and read_receipt(worktree, tid) is None
    tick(projet, host)  # le relecteur lancé, inscrit, peut écrire
    assert _review_cli(worktree, tid, _verdict_in(worktree, tid), reviewer_sid(host), monkeypatch).exit_code == 0


def test_le_ledger_ne_garde_que_l_empreinte_de_la_session_du_relecteur(projet: Path, host: FakeHost) -> None:
    tid, _ = launched_and_in_review(projet, host)
    tick(projet, host)
    payload = review_event(projet, tid).payload
    assert "session_id" not in payload and reviewer_sid(host) not in json.dumps(payload)


def test_un_recu_refuse_au_journal_d_autorite_le_reste_au_tick_suivant(projet: Path, host: FakeHost) -> None:
    """Sous mandat, la session du relecteur qui signe au nom du propriétaire : refus définitif, jamais READY ensuite."""
    _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "READY", reviewer_sid(host), reviewer="amelia")
    first = tick(projet, host)
    assert [(o.action, o.status) for o in first.reviews] == [("refused", "done")]
    assert tick(projet, host).reviews == () and MandateStore(projet).state().active


def test_un_mandat_revoque_depuis_le_lancement_ne_leve_pas_la_garde(projet: Path, host: FakeHost) -> None:
    _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    MandateStore(projet).revoke("test", "révoqué entre le lancement et le jugement")
    receipt_from(Path(str(launch["cwd"])), tid, "READY", reviewer_sid(host))
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("refused", "done")] and "mandat inactif" in report.reviews[0].detail


def _stage(projet: Path, host: FakeHost, now: datetime | None = None):  # type: ignore[no-untyped-def]
    from grimoire.missions.orchestrator_policy import load_orchestrator_policy
    from grimoire.missions.reviewer import ReviewStage

    return ReviewStage(TaskService(projet), load_orchestrator_policy(projet).policy, exec_=host, now=now)()  # type: ignore[arg-type]


def test_un_relecteur_muet_est_arrete_relance_puis_escalade(projet: Path, host: FakeHost) -> None:
    from grimoire.missions.reviewer import MAX_RELAUNCHES, REVIEW_TIMEOUT

    tid, _ = launched_and_in_review(projet, host)
    later = datetime.now(UTC)
    for n in range(MAX_RELAUNCHES + 1):
        assert [(o.action, o.status) for o in _stage(projet, host, later)] == [("launch", "launched")], n
        later += REVIEW_TIMEOUT + timedelta(minutes=1)
        assert [(o.action, o.status) for o in _stage(projet, host, later)] == [("timeout", "done")], n
    assert [(o.action, o.status) for o in _stage(projet, host, later)] == [("escalate", "done")]
    assert TaskService(projet).require(tid).status is TaskState.WAITING
    assert len(host.review_stops()) == MAX_RELAUNCHES + 1  # chaque relecteur muet est arrêté


def test_un_ready_perime_est_relu(projet: Path, host: FakeHost) -> None:
    tid, launch = launched_and_in_review(projet, host)
    worktree = Path(str(launch["cwd"]))
    tick(projet, host)
    receipt_from(worktree, tid, "READY", reviewer_sid(host))
    (worktree / "README.md").write_text("modifié après la relecture\n", encoding="utf-8")
    assert [(o.action, o.status) for o in tick(projet, host).reviews] == [("stale", "done")]
    assert [(o.action, o.status) for o in tick(projet, host).reviews] == [("launch", "launched")]


def test_une_reprise_du_worker_ratee_remet_la_tache_en_revue(projet: Path, host: FakeHost) -> None:
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "NOT-READY", reviewer_sid(host))
    host.bg_fails = True
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("bounce", "failed")]
    assert TaskService(projet).require(tid).status is TaskState.NEEDS_VERIFICATION  # pas « running » sans personne
    host.bg_fails = False
    assert [(o.action, o.status) for o in tick(projet, host).reviews] == [("bounce", "done")]  # retentée


def test_disjoncteur_ouvert_aucune_reprise(projet: Path, host: FakeHost) -> None:
    from grimoire.missions.breaker import trip

    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    receipt_from(Path(str(launch["cwd"])), tid, "NOT-READY", reviewer_sid(host))
    trip(projet, "autre tâche")
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("bounce", "skipped")] and host.of("claude", "--bg", "--resume") == []


def test_un_not_ready_rend_le_verdict_du_pack_rouge(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    tid = ready(TaskService(root))
    _checks_pack(root, tid)
    out = runner.invoke(app, ["task", "review", tid, "--criteria", str(_verdict_file(tmp_path, "NOT-READY", ("met", "unmet"))),
                              "--project-root", str(root)])
    assert out.exit_code == 0, out.output
    verdict = EvidenceService(root / EVIDENCE).get_latest_verdict(tid)
    assert verdict is not None and verdict.verdict.value == "failed"
    assert any(c.id == "review-verdict" and c.result.value == "failed" for c in verdict.checks)


def test_une_preparation_ratee_ne_consomme_pas_le_plafond_du_mandat(projet: Path, host: FakeHost) -> None:
    from lot2_doubles import git

    _grant(projet)
    tid = ready(TaskService(projet))
    git(projet, "branch", f"task/{tid}")  # create_task_worktree refuse
    for _ in range(2):
        assert tick(projet, host).launches[0].status == "failed"
    assert MandateStore(projet).sessions(role="worker") == [] and MandateStore(projet).state().active


def test_une_etape_de_relecture_en_erreur_ne_casse_pas_le_tick(projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import reviewer

    launched_and_in_review(projet, host)

    def boom(self: object) -> tuple[()]:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(reviewer.ReviewStage, "__call__", boom)
    report = tick(projet, host)
    assert report.status == "ran" and any("étape en erreur" in a for a in report.attention)
