"""K6 (issue #771) — l'état ``waiting`` à raison nommée, les questions et réponses tracées, la notification.

Constat sur la base (origin/main) : une tâche qui attend quelqu'un n'a nulle part où le dire. Elle
reste ``running`` jusqu'à ce que son bail périsse, ou passe ``blocked`` avec une raison libre
(``cmd_task.py``, ``task block``) ; la récolte différée de K2 laisse la tâche tenue (« jusqu'à
l'état waiting (K6) ») ; ``awaiting_human`` n'est qu'un texte d'erreur de flow ; une question de
fusion ou de permission reste dans le fil de discussion, et rien ne la fait remonter.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import ledger as ledger_mod
from grimoire.missions.board import build_board
from grimoire.missions.decisions import (
    NOTIFY_ENV,
    decisions_summary,
    lint_decisions,
    notify_pending,
    pending_decisions,
)
from grimoire.missions.lease import lease_path, read_lease
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import Question, Waiting, lint_decision_text

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
        "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX", NOTIFY_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def _running(service: TaskService, title: str = "Tâche", actor: str = "agent-a", session: str = "S1") -> str:
    tid = service.add(title, ("critère",), actor="t", owner="guilhem", ready=True).task.id
    service.claim(tid, actor, host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, actor)
    if session:
        service.attach_session(tid, session, session_host="claude-code-cli", actor=actor)
    return tid


def _ask(service: TaskService, tid: str, text: str = "Fusionner la PR #12 ?", **kw: Any) -> Question:
    service.ask(tid, text, "agent-a", kind=kw.pop("kind", "merge"), options=("fusionner", "attendre"), **kw)
    task = service.require(tid)
    assert task.waiting is not None and task.waiting.question is not None
    return task.waiting.question


# ── CA : la table de transitions, exhaustive ────────────────────────────────

S = TaskState
#: §2.1 du plan, écrit ici sans lire le code : les états actuels, plus les lignes de ``waiting``.
EXPECTED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    S.PROPOSED: frozenset({S.READY, S.CANCELLED}),
    S.READY: frozenset({S.CLAIMED, S.BLOCKED, S.CANCELLED}),
    S.CLAIMED: frozenset({S.RUNNING, S.READY, S.BLOCKED, S.FAILED, S.CANCELLED, S.WAITING}),
    S.RUNNING: frozenset({S.NEEDS_VERIFICATION, S.BLOCKED, S.FAILED, S.CANCELLED, S.READY, S.WAITING}),
    S.BLOCKED: frozenset({S.READY, S.CANCELLED}),
    S.NEEDS_VERIFICATION: frozenset({S.CLOSED, S.RUNNING, S.FAILED, S.CANCELLED}),
    S.FAILED: frozenset({S.READY, S.CANCELLED}),
    S.CLOSED: frozenset(),
    S.CANCELLED: frozenset(),
    S.WAITING: frozenset({S.RUNNING, S.READY, S.CANCELLED}),
}

_WAITING_PAYLOAD = {"waiting": Waiting("quota", "unknown", None, None, "2026-10-17T00:00:00+00:00").to_dict()}


def _in_state(ledger: MissionLedger, state: TaskState) -> str:
    mission = ledger.list_missions()[0] if ledger.list_missions() else ledger.create_mission("M", origin="t")
    tid = ledger.create_task(mission.id, f"T-{len(ledger.list_tasks())}", acceptance=("a",)).id
    path: dict[TaskState, list[TaskState]] = {
        S.PROPOSED: [], S.READY: [S.READY], S.CLAIMED: [S.READY, S.CLAIMED],
        S.RUNNING: [S.READY, S.CLAIMED, S.RUNNING], S.BLOCKED: [S.READY, S.BLOCKED],
        S.NEEDS_VERIFICATION: [S.READY, S.CLAIMED, S.RUNNING, S.NEEDS_VERIFICATION],
        S.FAILED: [S.READY, S.CLAIMED, S.FAILED], S.CANCELLED: [S.CANCELLED],
        S.CLOSED: [S.READY, S.CLAIMED, S.RUNNING, S.NEEDS_VERIFICATION, S.CLOSED],
        S.WAITING: [S.READY, S.CLAIMED, S.RUNNING, S.WAITING],
    }[state]
    for step in path:
        if step is S.CLAIMED:
            ledger.claim_task(tid, "agent-a", "local")
        else:
            ledger.transition_task(tid, step, actor_id="agent-a", extra_payload=_WAITING_PAYLOAD if step is S.WAITING else None)
    assert ledger.get_task(tid).status is state  # type: ignore[union-attr]
    return tid


def test_the_transition_table_is_exactly_the_plans(tmp_path: Path) -> None:
    assert dict(ledger_mod._TASK_TRANSITIONS) == EXPECTED_TRANSITIONS


@pytest.mark.parametrize("source", [s for s in TaskState if s is not S.UNKNOWN], ids=lambda s: s.value)
def test_every_pair_is_allowed_or_refused_by_the_table(tmp_path: Path, source: TaskState) -> None:
    """Toutes les paires : une paire hors table est refusée « Invalid task transition », une paire de la table ne l'est jamais."""
    ledger = MissionLedger(tmp_path / "ledger")
    for target in (s for s in TaskState if s is not S.UNKNOWN):
        tid = _in_state(ledger, source)
        extra = _WAITING_PAYLOAD if target is S.WAITING else None
        try:
            ledger.transition_task(tid, target, actor_id="human", claim=None, extra_payload=extra)
            refusal = ""
        except GrimoireMissionError as exc:
            refusal = str(exc)
        listed = target in EXPECTED_TRANSITIONS[source]
        where = f"{source.value} → {target.value} : {refusal!r}"
        if listed:
            assert "Invalid task transition" not in refusal, where  # permise (ou refusée par une garde nommée)
        else:
            assert refusal, where  # hors table : toujours refusée (la garde du double claim passe avant le message de table)


def test_waiting_to_ready_is_the_tick_only_and_waiting_to_running_needs_the_epoch(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    tid = _in_state(ledger, S.WAITING)
    with pytest.raises(GrimoireMissionError, match="réservé au tick"):
        ledger.transition_task(tid, S.READY, actor_id="agent-a")
    with pytest.raises(GrimoireMissionError, match="même epoch"):
        ledger.transition_task(tid, S.RUNNING, actor_id="agent-a")
    with pytest.raises(GrimoireMissionError, match="périmé"):
        ledger.transition_task(tid, S.RUNNING, actor_id="agent-a", lease_epoch=9)
    ledger.transition_task(tid, S.RUNNING, actor_id="agent-a", lease_epoch=1)  # la même session, le même epoch
    assert ledger.get_task(tid).status is S.RUNNING  # type: ignore[union-attr]


def test_a_transition_to_waiting_must_describe_the_wait(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    tid = _in_state(ledger, S.RUNNING)
    with pytest.raises(GrimoireMissionError, match="aucune attente décrite"):
        ledger.transition_task(tid, S.WAITING, actor_id="agent-a")
    for bad in (
        Waiting("resource", "", None, None, ""),  # D1 : la raison « resource » est retirée
        Waiting("human", "", None, None, ""),  # une attente humaine sans question
        Waiting("human", "2030-01-01T00:00:00+00:00", Question("q", "choice", "?", (), "agent"), None, ""),  # échéance
        Waiting("ci", "", Question("q", "choice", "?", (), "agent"), None, ""),  # question hors waiting(human)
        Waiting("quota", "demain", None, None, ""),
    ):
        with pytest.raises(GrimoireMissionError, match="refusée"):
            ledger.transition_task(tid, S.WAITING, actor_id="agent-a", extra_payload={"waiting": bad.to_dict()})
    assert ledger.get_task(tid).status is S.RUNNING  # type: ignore[union-attr]


def test_waiting_keeps_its_fields_through_a_replay_and_clears_on_exit(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    question = _ask(service, tid)
    task = MissionLedger(service.ledger_root).get_task(tid)  # un autre lecteur, rejeu complet
    assert task is not None and task.status is S.WAITING and task.waiting is not None
    assert task.waiting.reason == "human" and task.waiting.until == ""
    assert task.waiting.question == question and task.waiting.question.options == ("fusionner", "attendre")
    assert MissionLedger(service.ledger_root).get_task(tid).to_dict()["waiting"]["question"]["id"] == question.id  # type: ignore[union-attr]
    service.cancel(tid, "plus utile", "human")
    assert MissionLedger(service.ledger_root).get_task(tid).waiting is None  # type: ignore[union-attr]


# ── CA : un agent ne répond pas à sa propre attente ─────────────────────────


def test_agent_cannot_answer_own_await(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    question = _ask(service, tid)
    events_before = len(service.ledger.list_events(tid))

    for origin in (ORIGIN_AGENT, "unverified", "", "HUMAN"):
        with pytest.raises(GrimoireMissionError, match="canal humain"):
            service.answer(tid, "fusionner", "agent-a", origin=origin, question_id=question.id)
    assert service.require(tid).status is S.WAITING and len(service.ledger.list_events(tid)) == events_before

    # Depuis une session d'hôte, le CLI pose `agent` : la réponse est refusée de la même façon.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S1")
    res = runner.invoke(task_app, ["answer", tid, "fusionner", "--project-root", str(tmp_path), "--actor", "agent-a"])
    assert res.exit_code == 1 and "canal humain" in res.output
    assert MissionLedger(service.ledger_root).get_task(tid).status is S.WAITING  # type: ignore[union-attr]

    # Et l'outil MCP n'offre aucune réponse : seul « ask » (poser la question) y existe.
    from grimoire.mcp import server

    assert "answer" not in server._TASK_ACTIONS and {"ask", "wait", "resume"} <= set(server._TASK_ACTIONS)


def test_a_forged_answer_event_does_not_reset_the_attempts(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    tid = _in_state(ledger, S.RUNNING)
    ledger.append_event("task.dispatched", tid, "task", "d", {"task_id": tid, "verdict": "red"})
    ledger.append_event(ledger_mod.HUMAN_ANSWERED_EVENT, tid, "task", "agent-a", {"task_id": tid, "origin": "agent"})
    ledger.append_event(ledger_mod.HUMAN_ANSWERED_EVENT, tid, "task", "agent-a", {"task_id": tid})
    assert ledger.attempts(tid) == 1


# ── CA : une réponse réveille la tâche ──────────────────────────────────────


def test_answer_wakes_task(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    question = _ask(service, tid)
    ledger_root = service.ledger_root
    assert read_lease(lease_path(ledger_root, tid)).holder  # type: ignore[union-attr]  # le bail est gardé pendant l'attente

    move = service.answer(tid, "fusionner", "guilhem", origin=ORIGIN_HUMAN, question_id=question.id)

    assert move.task.status is S.READY and move.task.waiting is None
    fresh = MissionLedger(ledger_root)
    answered = [e for e in fresh.list_events(tid) if e.event_type == "task.human_answered"]
    assert len(answered) == 1 and answered[0].payload["question_id"] == question.id
    assert answered[0].payload["answer"] == "fusionner" and answered[0].payload["origin"] == "human"
    # Le bail est rendu : l'ancien titulaire est refusé, la reprise est un nouveau claim.
    assert not fresh.check_holder(tid, 1).ok
    # La réponse rejoint la prochaine session de la tâche, comme consigne humaine.
    [directive] = fresh.get_task(tid).directives  # type: ignore[union-attr]
    assert directive.is_human and "fusionner" in directive.text and question.text in directive.text
    service.claim(tid, "agent-b", host="local")
    assert fresh.get_task(tid) is not None and MissionLedger(ledger_root).get_task(tid).claim.lease_epoch == 3  # type: ignore[union-attr]


def test_answer_refuses_a_wrong_or_missing_question_and_an_empty_answer(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    other = _running(service, "Autre", actor="agent-b", session="S2")
    with pytest.raises(GrimoireMissionError, match="n'attend aucune réponse"):
        service.answer(other, "oui", "guilhem", origin=ORIGIN_HUMAN)
    question = _ask(service, tid)
    with pytest.raises(GrimoireMissionError, match="Question q-nope inconnue"):
        service.answer(tid, "oui", "guilhem", origin=ORIGIN_HUMAN, question_id="q-nope")
    with pytest.raises(ValueError, match="réponse vide"):
        service.answer(tid, "  ", "guilhem", origin=ORIGIN_HUMAN, question_id=question.id)
    assert service.require(tid).status is S.WAITING


def test_answering_abandonner_cancels_instead_of_relaunching(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    service.ask(tid, "Reprendre ?", "agent-a", kind="resume_branch", options=("reprendre", "abandonner"))
    service.answer(tid, "Abandonner", "guilhem", origin=ORIGIN_HUMAN)
    assert service.require(tid).status is S.CANCELLED


def test_an_interrupted_wake_is_finished_by_the_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    question = _ask(service, tid)
    monkeypatch.setattr(MissionLedger, "wake", lambda *a, **k: (_ for _ in ()).throw(OSError("disque plein")))
    with pytest.raises(OSError, match="disque plein"):
        service.answer(tid, "fusionner", "guilhem", origin=ORIGIN_HUMAN, question_id=question.id)
    monkeypatch.undo()
    assert service.require(tid).status is S.WAITING and not pending_decisions(service.ledger)  # répondue : plus à décider
    [decision] = TaskService(tmp_path).wake_due()
    assert (decision.action, decision.cause, decision.written) == ("wake", "answered", True)
    assert TaskService(tmp_path).require(tid).status is S.READY


# ── CA : aucune sortie de waiting(human) au minuteur ────────────────────────


def test_no_exit_from_waiting_human_on_a_timer(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    _ask(service, tid)
    ci = _running(service, "CI", actor="agent-b", session="S2")
    service.wait(ci, "ci", "agent-b", until=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    quota = _running(service, "Quota", actor="agent-c", session="S3")
    service.wait(quota, "quota", "agent-c")  # until = unknown
    paused = _running(service, "Pause", actor="agent-d", session="S4")
    service.wait(paused, "paused", "agent-d", until=(datetime.now(UTC) + timedelta(hours=2)).isoformat())

    far = datetime.now(UTC) + timedelta(days=400)
    by_task = {d.task_id: d for d in service.wake_due(apply=False, now=far)}
    assert by_task[tid].action == "keep" and by_task[quota].action == "keep"  # humain : jamais ; unknown : une sonde, pas un minuteur
    assert by_task[ci].action == "wake" and by_task[paused].action == "wake"
    assert all(not d.written for d in by_task.values())  # aperçu

    applied = {d.task_id: d for d in service.wake_due(now=far)}
    assert applied[ci].written and applied[paused].written and not applied[tid].written
    assert service.require(tid).status is S.WAITING
    assert service.require(ci).status is S.READY and service.require(paused).status is S.READY
    assert service.require(quota).status is S.WAITING


def test_wait_refuses_human_and_unknown_reasons_and_resume_is_in_place(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    for reason in ("human", "resource", "gpu"):
        with pytest.raises(ValueError, match="raison d'attente inconnue"):
            service.wait(tid, reason, "agent-a")
    service.wait(tid, "ci", "agent-a", note="attend la CI")
    task = service.require(tid)
    assert task.waiting is not None and task.waiting.reason == "ci"
    assert task.waiting.resume_hint is not None and task.waiting.resume_hint.session_id == "S1"
    assert task.waiting.resume_hint.resume_command == "claude --resume S1"
    with pytest.raises(GrimoireMissionError, match="périmé"):
        service.resume(tid, "agent-a", epoch=7)
    resumed = service.resume(tid, "agent-a")  # même acteur, même epoch
    assert resumed.task.status is S.RUNNING and resumed.task.waiting is None
    assert read_lease(lease_path(service.ledger_root, tid)).holder  # type: ignore[union-attr]  # jamais rendu


def test_a_waiting_task_keeps_its_exclusive_files(tmp_path: Path) -> None:
    from grimoire.missions.service import TaskRefusedError

    service = TaskService(tmp_path)
    first = service.add("Première", ("a",), actor="t", ready=True).task.id
    service.claim(first, "agent-a", files=("src/a.py",))
    service.transition(first, TaskState.RUNNING, "agent-a")
    service.wait(first, "ci", "agent-a")
    second = service.add("Seconde", ("a",), actor="t", ready=True).task.id
    with pytest.raises(TaskRefusedError, match="réservé par"):
        service.claim(second, "agent-b", files=("src/a.py",))


# ── CA : la récolte différée se gare ────────────────────────────────────────


def test_reap_deferrals_park_the_task_with_their_reason(tmp_path: Path) -> None:
    class _Clock:
        now = datetime.now(UTC)

        def __call__(self) -> datetime:
            return self.now

    clock = _Clock()
    ledger = MissionLedger(tmp_path / "ledger", clock=clock)
    tid = _in_state(ledger, S.RUNNING)
    ledger.attach_session(tid, "S9", session_host="host-github-copilot", host_version="1.99.0")
    clock.now += timedelta(minutes=30)
    [decision] = ledger.reap(fenced_hosts=frozenset()).decisions
    assert (decision.action, decision.cause) == ("defer", "resume_unfenced")
    task = ledger.get_task(tid)
    assert task is not None and task.status is S.WAITING and task.waiting is not None
    assert task.waiting.reason == "human" and task.waiting.question is not None
    assert task.waiting.question.kind == "resume_unfenced" and task.waiting.question.origin == "tick"
    assert task.waiting.question.options == ("reprendre", "abandonner") and "S9" in task.waiting.question.text
    assert task.waiting.resume_hint is not None and task.waiting.resume_hint.session_id == "S9"
    # Elle sort de la récolte : la passe suivante n'a plus rien à juger.
    assert ledger.reap(fenced_hosts=frozenset()).decisions == ()


def test_reap_quota_waits_in_quota_and_a_legacy_deferred_task_is_parked(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    tid = _in_state(ledger, S.RUNNING)
    ledger.append_event("task.dispatched", tid, "task", "d", {"task_id": tid, "verdict": "rate_limit"})
    # Une ancienne différée (K2) : l'événement existe, la tâche est restée tenue.
    ledger.append_event(
        ledger_mod.REAP_DEFERRED_EVENT, tid, "task", "tick",
        {"task_id": tid, "lease_epoch_lost": 1, "cause": "quota", "attempts": 0, "lease_reason": "heartbeat"},
    )
    write = ledger_mod.write_lease
    write(lease_path(tmp_path / "ledger", tid), ledger_mod.LeaseFile(tid, 1, (datetime.now(UTC) - timedelta(hours=1)).isoformat()))
    [decision] = MissionLedger(tmp_path / "ledger").reap(fenced_hosts=frozenset({"local"})).decisions
    assert (decision.action, decision.cause) == ("defer", "quota")
    task = MissionLedger(tmp_path / "ledger").get_task(tid)
    assert task is not None and task.status is S.WAITING and task.waiting is not None
    assert (task.waiting.reason, task.waiting.until, task.waiting.question) == ("quota", "unknown", None)
    assert len([e for e in MissionLedger(tmp_path / "ledger").list_events(tid) if e.event_type == "task.reap_deferred"]) == 1


def test_an_answer_to_a_retry_exhausted_question_resets_the_attempts(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    ledger = service.ledger
    tid = _in_state(ledger, S.RUNNING)
    for _ in range(3):
        ledger.append_event("task.transitioned", tid, "task", "tick", {"task_id": tid, "to_state": "running", "from_state": "running", ledger_mod.LEASE_LOST_KEY: {}})
    assert ledger.attempts(tid) == 3
    service.ask(tid, "Réessayer ?", "tick", kind="retry_exhausted", options=("réessayer", "abandonner"), origin="tick")
    service.answer(tid, "réessayer", "guilhem", origin=ORIGIN_HUMAN)
    assert MissionLedger(service.ledger_root).attempts(tid) == 0


# ── CA : le board — waiting(human) en tête, colonne et motif ────────────────


def test_waiting_human_leads_the_board_the_list_and_the_portfolio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = TaskService(tmp_path)
    urgent = _running(service, "Urgente", actor="agent-b", session="S2")
    service.prioritize(urgent, "critical", "guilhem")
    plain = service.add("Prête", ("a",), actor="t", ready=True).task.id
    waiting = _running(service, "Attend")
    _ask(service, waiting)
    ci = _running(service, "CI", actor="agent-c", session="S3")
    service.wait(ci, "ci", "agent-c")

    board = build_board(service.ledger)
    ids = [t["task_id"] for t in board["tasks"]]
    assert ids[0] == waiting, ids
    card = board["tasks"][0]
    assert card["status"] == "blocked" and "décision humaine" in card["blockers"][0]["reason"]
    assert "Fusionner la PR #12 ?" in card["blockers"][0]["reason"]
    ci_card = next(t for t in board["tasks"] if t["task_id"] == ci)
    assert ci_card["status"] == "blocked" and "(ci)" in ci_card["blockers"][0]["reason"]
    # Le reste garde l'ordre des colonnes du cycle de vie : prête, en cours, puis bloquée/en attente.
    assert ids == [waiting, plain, urgent, ci]

    assert service.list_tasks()[0].id == waiting

    from grimoire.tools import project_registry, workspace_portfolio

    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    project_registry.register_project(tmp_path)
    view = workspace_portfolio.portfolio_tasks(tmp_path)
    assert view["tasks"][0]["id"] == waiting and view["tasks"][0]["waiting"]["question"]["text"] == "Fusionner la PR #12 ?"


def test_the_governed_verifier_accepts_a_board_with_a_waiting_task(tmp_path: Path) -> None:
    from grimoire.core.agentic_standard import setup_standard_profile

    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    service = TaskService(tmp_path)
    tid = service.add("Tâche", ("critère",), owner="guilhem", actor="t", ready=True).task.id
    service.context(tid)
    service.claim(tid, "agent-a", host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "agent-a")
    service.ask(tid, "Fusionner ?", "agent-a")  # chaque geste du service reprojette (et vérifie) le board
    board = (tmp_path / "_grimoire/standard/task-board.yaml").read_text(encoding="utf-8")
    assert tid in board and "décision humaine" in board


# ── CA : `grimoire status` liste les décisions en attente ───────────────────


def _project(root: Path) -> None:
    (root / "project-context.yaml").write_text(
        "project:\n  name: demo\n  type: webapp\nuser:\n  name: G\n", encoding="utf-8"
    )


def test_status_lists_pending_decisions(tmp_path: Path) -> None:
    from grimoire.cli.app import app

    _project(tmp_path)
    quiet = runner.invoke(app, ["status", str(tmp_path)])
    assert quiet.exit_code == 0 and "en attente" not in quiet.output

    service = TaskService(tmp_path)
    old, recent = _running(service, "Ancienne"), _running(service, "Récente", actor="agent-b", session="S2")
    _ask(service, old)
    _ask(service, recent, "Autoriser le push ?", kind="permission")
    # La plus ancienne date de 3 h.
    events = service.ledger_root / "events.jsonl"
    lines = [json.loads(x) for x in events.read_text(encoding="utf-8").splitlines()]
    for rec in lines:
        if rec["event_type"] == "task.transitioned" and rec["entity_id"] == old and rec["payload"]["to_state"] == "waiting":
            rec["payload"]["waiting"]["since"] = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    events.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")

    res = runner.invoke(app, ["status", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "2 décisions en attente, plus ancienne : 3 h" in res.output
    assert old in res.output
    as_json = runner.invoke(app, ["-o", "json", "status", str(tmp_path)])
    data = json.loads(as_json.output)
    assert data["decisions"]["count"] == 2 and data["decisions"]["oldest"]["task_id"] == old
    assert data["decisions"]["notify_failures"] == []

    # Répondre le retire de la liste ; depuis un CliRunner (pas de terminal) la réponse CLI n'est pas humaine.
    TaskService(tmp_path).answer(old, "oui", "guilhem", origin=ORIGIN_HUMAN)
    assert decisions_summary(tmp_path)["count"] == 1 and decisions_summary(tmp_path)["line"].startswith("1 décision en attente")


def test_the_cli_decisions_command_lists_questions(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    question = _ask(service, tid)
    res = runner.invoke(task_app, ["decisions", "--project-root", str(tmp_path)])
    assert res.exit_code == 0 and tid in res.output and question.id in res.output and "fusionner | attendre" in res.output
    as_json = runner.invoke(task_app, ["decisions", "--project-root", str(tmp_path)], obj={"output": "json"})
    [row] = json.loads(as_json.output)
    assert row["task_id"] == tid and row["question"]["kind"] == "merge" and row["age_seconds"] >= 0


# ── CA : notification, une fois par question, au mieux ──────────────────────


def _script(tmp_path: Path, body: str) -> str:
    script = tmp_path / "notify.py"
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}" {{title}} {{message}}'


def test_notify_once_per_question(tmp_path: Path) -> None:
    out = tmp_path / "sent.log"
    command = _script(
        tmp_path,
        "import os, sys\n"
        f"open({str(out)!r}, 'a', encoding='utf-8').write(os.environ['GRIMOIRE_NOTIFY_QUESTION_ID'] + '|' + sys.argv[2] + '\\n')\n",
    )
    service = TaskService(tmp_path)
    first, second = _running(service), _running(service, "B", actor="agent-b", session="S2")
    q1 = _ask(service, first)
    # Aperçu : rien n'est lancé ni écrit.
    assert [o.status for o in notify_pending(service.ledger, tmp_path, command=command, apply=False)] == ["pending"]
    assert not out.exists()

    assert [o.status for o in notify_pending(service.ledger, tmp_path, command=command)] == ["sent"]
    assert [o.status for o in notify_pending(service.ledger, tmp_path, command=command)] == ["skipped"]  # pas deux fois
    q2 = _ask(service, second, "Autoriser ?", kind="permission")
    statuses = {o.question_id: o.status for o in notify_pending(service.ledger, tmp_path, command=command)}
    assert statuses == {q1.id: "skipped", q2.id: "sent"}
    assert out.read_text(encoding="utf-8").splitlines() == [f"{q1.id}|{q1.text}", f"{q2.id}|{q2.text}"]
    sent = [e for e in service.ledger.list_events() if e.event_type == "task.question_notified"]
    assert [(e.payload["question_id"], e.payload["ok"]) for e in sent] == [(q1.id, True), (q2.id, True)]


def test_notify_failure_is_visible_not_blocking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli.app import app

    _project(tmp_path)
    service = TaskService(tmp_path)
    tid = _running(service)
    question = _ask(service, tid)
    failing = _script(tmp_path, "import sys\nsys.stderr.write('ntfy injoignable')\nsys.exit(3)\n")

    [outcome] = notify_pending(service.ledger, tmp_path, command=failing)
    assert outcome.status == "failed" and "code de sortie 3" in outcome.error and "ntfy injoignable" in outcome.error
    assert service.require(tid).status is S.WAITING  # rien n'est bloqué
    summary = decisions_summary(tmp_path)
    assert summary["count"] == 1 and summary["notify_failures"][0]["question_id"] == question.id
    res = runner.invoke(app, ["status", str(tmp_path)])
    assert res.exit_code == 0 and "notification échouée" in res.output and "ntfy injoignable" in res.output
    # Au moins une fois : un succès ultérieur clôt la question ; trois échecs, on renonce.
    assert [o.status for o in notify_pending(service.ledger, tmp_path, command=failing)] == ["failed"]
    assert [o.status for o in notify_pending(service.ledger, tmp_path, command=failing)] == ["failed"]
    assert [o.status for o in notify_pending(service.ledger, tmp_path, command=failing)] == ["gave_up"]
    # Commande absente, délai, illisible : jamais d'exception, un échec nommé.
    assert notify_pending(service.ledger, tmp_path, command="") == []
    for broken in ("/nonexistent/binary {title}", "'unterminated"):
        fresh = TaskService(tmp_path / f"p{len(broken)}")
        t = _running(fresh)
        _ask(fresh, t)
        [o] = notify_pending(fresh.ledger, fresh.project_root, command=broken)
        assert o.status == "failed" and o.error
    monkeypatch.setenv(NOTIFY_ENV, "true")
    assert load_command(tmp_path) == "true"


def load_command(root: Path) -> str:
    from grimoire.missions.decisions import load_notify_command

    return load_notify_command(root)


def test_notify_command_is_read_from_the_profile_and_never_through_a_shell(tmp_path: Path) -> None:
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True)
    (tmp_path / "_grimoire/standard/standard-profile.yaml").write_text(
        "profile: governed\noptions:\n  notify:\n    command: notify-send {title} {message}\n", encoding="utf-8"
    )
    assert load_command(tmp_path) == "notify-send {title} {message}"
    (tmp_path / "_grimoire/standard/standard-profile.yaml").write_text("options: [", encoding="utf-8")
    assert load_command(tmp_path) == ""  # illisible : pas de notification, pas d'exception
    # Le texte de la question n'est jamais interprété : une injection reste une chaîne.
    marker = tmp_path / "pwned"
    service = TaskService(tmp_path)
    tid = _running(service)
    service.ask(tid, f"fusion ? ; touch {marker} && $(touch {marker})", "agent-a", kind="merge")
    command = _script(tmp_path, "import sys\n")
    assert [o.status for o in notify_pending(service.ledger, tmp_path, command=command)] == ["sent"]
    assert not marker.exists()


# ── CA : lint-decisions ─────────────────────────────────────────────────────


def test_lint_flags_implicit_merge_question(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    service.comment(tid, "Je fusionne la PR maintenant, on est d'accord ?", "agent-a")  # fusion + « ? » : implicite
    service.note_progress(tid, "Tests verts ; je peux merge sur main ?", "agent-a")
    service.comment(tid, "Tests verts, la PR est prête.", "agent-a")  # pas de question
    service.comment(tid, "Peux-tu relire le style ?", "agent-a")  # une question, aucun mot de décision
    service.note_progress(tid, "Fusion faite.", "agent-a")  # un mot de décision, pas de « ? »

    findings = lint_decisions(service.ledger)
    assert [(f.task_id, f.source) for f in findings] == [(tid, "comment"), (tid, "progress")]
    assert "fusionne" in findings[0].text and "merge" in findings[1].text

    res = runner.invoke(task_app, ["lint-decisions", "--project-root", str(tmp_path)])
    assert res.exit_code == 1 and "heuristique" in res.output.lower() and tid in res.output

    # Poser la vraie question (`task ask`) associe : plus rien à signaler.
    service.ask(tid, "Fusionner la PR ?", "agent-a", kind="merge")
    assert lint_decisions(service.ledger) == []
    clean = runner.invoke(task_app, ["lint-decisions", "--project-root", str(tmp_path)])
    assert clean.exit_code == 0 and "ne prouve rien" in clean.output


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("On fusionne ?", True), ("Je merge ?", True), ("Tu m'autorises à pousser ? permission requise", True),
        ("Peux-tu valider ?", True), ("Autoriser l'accès ?", True), ("C'est fusionné.", False), ("Et le style ?", False),
    ],
)
def test_lint_vocabulary(text: str, flagged: bool) -> None:
    assert lint_decision_text(text) is flagged


# ── CA : les commandes du CLI et de l'agent ─────────────────────────────────


def test_cli_ask_wait_resume_note_and_wake(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service, actor="cli")
    base = ["--project-root", str(tmp_path)]
    res = runner.invoke(task_app, ["ask", tid, "Fusionner ?", "--kind", "merge", "-o", "oui", "-o", "non", *base, "--actor", "cli"])
    assert res.exit_code == 0, res.output
    task = TaskService(tmp_path).require(tid)
    assert task.status is S.WAITING and task.waiting is not None and task.waiting.question is not None
    assert task.waiting.question.options == ("oui", "non") and task.waiting.question.kind == "merge"
    assert runner.invoke(task_app, ["ask", tid, "Encore ?", *base, "--actor", "cli"]).exit_code == 1  # déjà garée
    bad = runner.invoke(task_app, ["ask", _running(service, "B", actor="cli", session="S2"), "?", "--kind", "bidule", *base, "--actor", "cli"])
    assert bad.exit_code == 1 and "nature de question inconnue" in bad.output

    res = runner.invoke(task_app, ["move", tid, "--to", "ready", *base])
    assert res.exit_code == 1 and "réservé au tick" in res.output
    other = _running(service, "C", actor="cli", session="S3")
    res = runner.invoke(task_app, ["move", other, "--to", "waiting", *base])
    assert res.exit_code == 1 and "aucune attente décrite" in res.output

    assert runner.invoke(task_app, ["wait", other, "--reason", "ci", "--note", "CI en cours", *base, "--actor", "cli"]).exit_code == 0
    assert TaskService(tmp_path).require(other).waiting.reason == "ci"  # type: ignore[union-attr]
    assert runner.invoke(task_app, ["wait", other, "--reason", "human", *base, "--actor", "cli"]).exit_code == 1
    assert runner.invoke(task_app, ["resume", other, *base, "--actor", "cli"]).exit_code == 0
    assert TaskService(tmp_path).require(other).status is S.RUNNING
    assert runner.invoke(task_app, ["note", other, "ça avance", *base, "--actor", "cli"]).exit_code == 0
    assert runner.invoke(task_app, ["note", other, "  ", *base]).exit_code == 1

    wake = runner.invoke(task_app, ["wake", *base])
    assert wake.exit_code == 0 and f"{tid} waiting(human) : attend encore" in wake.output and "Aperçu" in wake.output
    as_json = runner.invoke(task_app, ["wake", "--apply", *base], obj={"output": "json"})
    assert json.loads(as_json.output)["wake"][0]["action"] == "keep"
    assert runner.invoke(task_app, ["answer", tid, "oui", *base]).exit_code == 1  # CliRunner : pas de terminal, pas d'humain


def _body(result: Any) -> dict[str, Any]:
    """Le corps JSON d'un outil MCP, réussi (chaîne) ou marqué ``isError`` (CallToolResult)."""
    if isinstance(result, str):
        return dict(json.loads(result))
    return dict(json.loads("".join(getattr(block, "text", "") for block in result.content)))


def test_mcp_ask_wait_resume_but_no_answer(tmp_path: Path) -> None:
    from grimoire.mcp import server

    service = TaskService(tmp_path)
    tid = _running(service, actor="mcp-agent")
    common = {"project_path": str(tmp_path)}
    out = _body(server.task_update(tid, "ask", text="Fusionner ?", kind="merge", **common))
    assert out["status"] == "waiting" and out["waiting"]["question"]["origin"] == "agent"
    assert "error" in _body(server.task_update(tid, "answer", text="oui", **common))  # inconnue du MCP
    assert "error" in _body(server.task_update(tid, "move", to="ready", **common))
    other = _running(service, "B", actor="mcp-agent", session="S2")
    assert _body(server.task_update(other, "wait", to="quota", **common))["waiting"]["reason"] == "quota"
    assert _body(server.task_update(other, "resume", **common))["status"] == "running"
    assert "error" in _body(server.task_update(other, "ask", text="", **common))


def test_unknown_future_reason_does_not_break_the_reader(tmp_path: Path) -> None:
    """Un ledger plus récent peut écrire une raison que ce lecteur ne connaît pas : il la garde, il n'échoue pas."""
    ledger = MissionLedger(tmp_path / "ledger")
    tid = _in_state(ledger, S.RUNNING)
    events = tmp_path / "ledger" / "events.jsonl"
    record = {
        "id": "evt-x", "schema_version": "grimoire.ledger_event.v1", "event_type": "task.transitioned",
        "entity_id": tid, "entity_kind": "task", "actor_id": "future", "created_at": datetime.now(UTC).isoformat(),
        "payload": {"task_id": tid, "from_state": "running", "to_state": "waiting", "waiting": {"reason": "approval"}},
    }
    events.write_text(events.read_text(encoding="utf-8") + json.dumps(record) + "\n", encoding="utf-8")
    task = MissionLedger(tmp_path / "ledger").get_task(tid)
    assert task is not None and task.status is S.WAITING and task.waiting is not None
    assert task.waiting.reason == "approval" and "inconnue" in task.waiting.problem()
    assert pending_decisions(MissionLedger(tmp_path / "ledger")) == []
