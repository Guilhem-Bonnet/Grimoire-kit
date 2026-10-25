"""Lot 3 de #876 (verrous 7 et 8) : le relecteur, contre un faux ``claude``, et le verdict structuré par critère.

Critères : une session distincte relit chaque tâche lancée qui arrive en revue (autre identifiant, autre modèle si
possible, inscrite au journal d'autorité sous mandat) ; son verdict par critère est validé par du code et alimente la
couverture du pack ; un reçu écrit par le worker est un verdict forgé (disjoncteur, mandat révoqué) ; un NOT-READY
renvoie au worker, puis escalade à l'humain au cycle ``max_cycles``.
"""

from __future__ import annotations

import json
from dataclasses import replace
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
from grimoire.missions.reviewer import REVIEW_FORGED_EVENT, REVIEW_JUDGED_EVENT, REVIEW_LAUNCHED_EVENT
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
    reviewer_sid = review_argv[review_argv.index("--session-id") + 1]
    assert reviewer_sid != launch["session_id"] == worker_argv[worker_argv.index("--session-id") + 1]
    assert review_argv[review_argv.index("--model") + 1] == "opus"  # le worker est en sonnet : un autre modèle
    _, cwd, env = host.calls[-1]
    assert cwd == Path(str(launch["cwd"])) and env == {"GRIMOIRE_UNATTENDED": "1", "GRIMOIRE_SESSION_ID": reviewer_sid}
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
    receipt_from(Path(str(launch["cwd"])), tid, "READY", str(review.payload["session_id"]))
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("ready", "done")]
    assert host.of("claude", "stop") == [["claude", "stop", review.payload["session"]]]
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
    receipt_from(worktree, tid, "NOT-READY", str(review_event(projet, tid).payload["session_id"]))
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
    receipt_from(worktree, tid, "NOT-READY", str(review_event(projet, tid).payload["session_id"]))
    second = tick(projet, host)
    assert [(o.action, o.status) for o in second.reviews] == [("escalate", "done")]
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.WAITING and task.waiting is not None and task.waiting.reason == "human"
    assert task.waiting.question is not None and "Désaccord persistant" in task.waiting.question.text
    assert len(host.of("claude", "--bg", "--resume")) == 1  # pas de troisième tour sans l'humain
    cycles = [e.payload["cycle"] for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_JUDGED_EVENT]
    assert cycles == [1, 2]


# ── Sous mandat : les sessions inscrites au journal d'autorité ─────────────────


def _grant(root: Path) -> md.Mandate:
    request = MandateRequest.build(("accept",), ("implementation",), 5, "+1d")
    return MandateStore(root).grant(request, origin=ORIGIN_HUMAN, via="test")


def test_sous_mandat_worker_et_relecteur_sont_inscrits_avant_leur_lancement(projet: Path, host: FakeHost) -> None:
    _grant(projet)
    tid, launch = launched_and_in_review(projet, host)
    tick(projet, host)
    store = MandateStore(projet)
    workers = {e["session_id"] for e in store.sessions(role="worker", task_id=tid)}
    reviewers = {e["session_id"] for e in store.sessions(role="reviewer", task_id=tid)}
    assert workers == {launch["session_id"]} and reviewers == {review_event(projet, tid).payload["session_id"]}


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
    receipt_from(Path(str(launch["cwd"])), tid, "READY", str(review_event(projet, tid).payload["session_id"]))
    assert [(o.action, o.status) for o in tick(projet, host).reviews] == [("ready", "done")]
    assert MandateStore(projet).state().active
