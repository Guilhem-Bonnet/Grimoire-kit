"""K6, revue sceptique — la tâche garée par la récolte revient, et une question ne se lève pas seule.

Constat sur 77d8d9a3 :

- K6-S1-01 : la récolte gare un quota en ``waiting(quota, until=unknown)`` ; aucune sonde ne
  lève ``unknown`` (K8/K9), donc ``wake_due`` la garde pour toujours, ``waiting → ready`` est
  réservé au tick, ``answer`` refuse (pas de question) et les fichiers exclusifs restent
  réservés. Sur la base, la récolte suivante rendait la tâche ``ready`` au quota levé.
- K6-S1-02 : ``waiting(human)`` sort par ``waiting → running`` sans réponse (garde de transition
  muette sur la raison), donc ``task_update(ask)`` puis ``task_update(resume)`` efface la question.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.lease import backoff_delay
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskRefusedError, TaskService
from grimoire.missions.waiting import parse_until
from grimoire.providers.state import record_suspension

S = TaskState
FENCED = frozenset({"claude-code-cli"})


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
        "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


def _service(tmp_path: Path, clock: _Clock) -> TaskService:
    service = TaskService(tmp_path)
    service._ledger = MissionLedger(service.ledger_root, clock=clock)
    return service


def _running(service: TaskService, title: str = "T", actor: str = "agent-a", files: tuple[str, ...] = ()) -> str:
    tid = service.add(title, ("c",), actor="t", owner="guilhem", ready=True).task.id
    service.claim(tid, actor, host="host-claude-code-cli", files=files)
    service.transition(tid, S.RUNNING, actor)
    return tid


def _dispatched_rate_limit(service: TaskService, tid: str, provider: str = "prov") -> None:
    service.ledger.append_event(
        "task.dispatched", tid, "task", "dispatch", {"task_id": tid, "verdict": "rate_limit", "provider": provider}
    )


# ── K6-S1-01 : le quota levé rend la tâche ──────────────────────────────────


def test_quota_park_carries_the_provider_reopening_date_and_the_tick_gives_the_task_back(tmp_path: Path) -> None:
    clock = _Clock()
    service = _service(tmp_path, clock)
    tid = _running(service, files=("src/x.py",))
    _dispatched_rate_limit(service, tid)
    reopens = clock.now + timedelta(hours=5)
    record_suspension(tmp_path, "prov", until=reopens, source="resets_at", now=clock.now)

    clock.now += timedelta(minutes=30)  # le bail est perdu
    [decision] = service.reap().decisions
    assert (decision.action, decision.cause) == ("defer", "quota")
    waiting = service.require(tid).waiting
    assert waiting is not None and waiting.reason == "quota"
    assert parse_until(waiting.until) == reopens  # la date du fournisseur, pas « unknown »

    # Avant la réouverture : on attend ; à la réouverture, le tick rend la tâche.
    [kept] = service.wake_due(apply=False, now=reopens - timedelta(minutes=1))
    assert kept.action == "keep"
    [woken] = service.wake_due(now=reopens)
    assert (woken.action, woken.written) == ("wake", True)
    assert service.require(tid).status is S.READY

    # Les fichiers exclusifs sont rendus : une seconde tâche les réclame.
    other = service.add("T2", ("c",), actor="t", owner="guilhem", ready=True).task.id
    service.claim(other, "agent-b", host="host-claude-code-cli", files=("src/x.py",))
    assert service.require(other).status is S.CLAIMED


def test_quota_park_without_a_provider_date_falls_back_to_a_backoff_never_to_unknown(tmp_path: Path) -> None:
    clock = _Clock()
    service = _service(tmp_path, clock)
    tid = _running(service)
    _dispatched_rate_limit(service, tid, provider="")  # aucun état de fournisseur lisible
    clock.now += timedelta(minutes=30)
    start = clock.now
    service.reap()
    waiting = service.require(tid).waiting
    assert waiting is not None and waiting.reason == "quota"
    due = parse_until(waiting.until)
    assert due == start + backoff_delay(1)  # 10 min, un cran au moins
    assert service.wake_due(now=due)[0].action == "wake"
    assert service.require(tid).status is S.READY


def test_an_expired_provider_suspension_is_not_a_reopening_date(tmp_path: Path) -> None:
    clock = _Clock()
    service = _service(tmp_path, clock)
    tid = _running(service)
    _dispatched_rate_limit(service, tid)
    record_suspension(tmp_path, "prov", until=clock.now - timedelta(hours=1), source="resets_at", now=clock.now - timedelta(hours=2))
    clock.now += timedelta(minutes=30)
    start = clock.now
    service.reap()
    waiting = service.require(tid).waiting
    assert waiting is not None and parse_until(waiting.until) == start + backoff_delay(1)


def test_ledger_reap_dates_the_quota_park_and_the_ledger_wake_rehydrates_it(tmp_path: Path) -> None:
    """Le plancher du ledger seul : un quota différé est daté, jamais ``unknown``."""
    clock = _Clock()
    ledger = MissionLedger(tmp_path / "ledger", clock=clock)
    mission = ledger.create_mission("M", origin="test")
    tid = ledger.create_task(mission.id, "T", acceptance=("a",)).id
    ledger.transition_task(tid, S.READY)
    ledger.claim_task(tid, "agent-a", "claude-code-cli")
    ledger.transition_task(tid, S.RUNNING, actor_id="agent-a")
    clock.now += timedelta(minutes=30)
    start = clock.now
    [decision] = ledger.reap(fenced_hosts=FENCED, quota_blocked=lambda _t: True).decisions
    assert (decision.action, decision.cause) == ("defer", "quota")
    task = ledger.get_task(tid)
    assert task is not None and task.waiting is not None
    assert parse_until(task.waiting.until) == start + backoff_delay(1)


def test_a_parked_task_refusal_names_what_frees_the_files_not_a_past_expiry(tmp_path: Path) -> None:
    clock = _Clock()
    service = _service(tmp_path, clock)
    tid = _running(service, files=("src/x.py",))
    service.ask(tid, "Fusionner ?", "agent-a", kind="merge")
    other = service.add("T2", ("c",), actor="t", owner="guilhem", ready=True).task.id
    with pytest.raises(TaskRefusedError) as raised:
        service.claim(other, "agent-b", host="host-claude-code-cli", files=("src/x.py",))
    [refusal] = raised.value.verdict.refusals
    assert "en attente (human)" in refusal.reason and "jusqu'à 20" not in refusal.reason
    assert "task answer" in refusal.remedy


# ── K6-S1-02 : une question ne se lève pas sans réponse humaine ─────────────


def test_ledger_refuses_resuming_a_reap_parked_human_question(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = MissionLedger(tmp_path / "ledger", clock=clock)
    mission = ledger.create_mission("M", origin="test")
    tid = ledger.create_task(mission.id, "T", acceptance=("a",)).id
    ledger.transition_task(tid, S.READY)
    ledger.claim_task(tid, "agent-a", "host-github-copilot")
    ledger.transition_task(tid, S.RUNNING, actor_id="agent-a")
    clock.now += timedelta(minutes=30)
    [decision] = ledger.reap(fenced_hosts=FENCED).decisions
    assert decision.cause == "resume_unfenced"
    task = ledger.get_task(tid)
    assert task is not None and task.status is S.WAITING and task.claim is not None
    with pytest.raises(GrimoireMissionError, match="attend la réponse de l'humain"):
        ledger.transition_task(tid, S.RUNNING, actor_id="agent-a", lease_epoch=task.claim.lease_epoch)
    after = ledger.get_task(tid)
    assert after is not None and after.status is S.WAITING and after.waiting is not None


def test_retry_exhausted_park_is_not_resumable_by_the_agent(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = MissionLedger(tmp_path / "ledger", clock=clock)
    mission = ledger.create_mission("M", origin="test")
    tid = ledger.create_task(mission.id, "T", acceptance=("a",)).id
    ledger.transition_task(tid, S.READY)
    ledger.claim_task(tid, "agent-a", "claude-code-cli")
    ledger.transition_task(tid, S.RUNNING, actor_id="agent-a")
    for _ in range(3):
        ledger.append_event(
            "task.transitioned", tid, "task", "tick",
            {"task_id": tid, "to_state": "running", "from_state": "running", "lease_lost": {}},
        )
    clock.now += timedelta(minutes=30)
    [decision] = ledger.reap(fenced_hosts=FENCED).decisions
    assert decision.cause == "retry_exhausted"
    task = ledger.get_task(tid)
    assert task is not None and task.claim is not None
    with pytest.raises(GrimoireMissionError, match="attend la réponse de l'humain"):
        ledger.transition_task(tid, S.RUNNING, actor_id="agent-a", lease_epoch=task.claim.lease_epoch)


def test_service_resume_refuses_a_pending_question_and_keeps_it(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    tid = _running(service)
    service.ask(tid, "Fusionner la PR ?", "agent-a", kind="merge")
    with pytest.raises(GrimoireMissionError, match="attend la réponse de l'humain"):
        service.resume(tid, "agent-a")
    assert service.require(tid).status is S.WAITING
    assert len(service.pending_decisions()) == 1


def _body(result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        return dict(json.loads(result))
    return dict(json.loads("".join(getattr(block, "text", "") for block in result.content)))


def test_mcp_ask_then_resume_does_not_erase_the_question(tmp_path: Path) -> None:
    from grimoire.mcp import server

    service = TaskService(tmp_path)
    tid = _running(service, actor="mcp-agent")
    common = {"project_path": str(tmp_path)}
    asked = _body(server.task_update(tid, "ask", text="Fusionner la PR ?", kind="merge", **common))
    assert asked["status"] == "waiting" and len(TaskService(tmp_path).pending_decisions()) == 1
    resumed = _body(server.task_update(tid, "resume", **common))
    assert "error" in resumed and "attend la réponse de l'humain" in resumed["error"]
    fresh = TaskService(tmp_path)
    assert fresh.require(tid).status is S.WAITING
    assert len(fresh.pending_decisions()) == 1


def test_resume_stays_open_for_ci_quota_and_paused(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    for reason in ("ci", "quota", "paused"):
        tid = _running(service, f"T-{reason}", actor=f"agent-{reason}")
        service.wait(tid, reason, f"agent-{reason}")
        assert service.resume(tid, f"agent-{reason}").task.status is S.RUNNING
