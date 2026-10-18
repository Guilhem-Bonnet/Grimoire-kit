"""K2 (issue #767) — bail renouvelable, epoch de fencing, récolte, garde d'horloge, tentatives dérivées.

Constat sur la base (origin/main + PR #728) : ``TaskClaim`` n'a ni epoch ni
battement de cœur, ``RUNNING → READY`` n'existe pas, il n'y a ni ``renew``,
ni ``expired_claims()``, ni ``reap()`` ; aucun ``fsync`` à l'ajout d'un
événement ; aucun plafond de reprises. Un titulaire périmé (session reprise
ailleurs) peut encore écrire ses effets.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import ledger as ledger_mod
from grimoire.missions.lease import (
    BACKOFF_BASE,
    LeaseFile,
    backoff_delay,
    lease_path,
    read_lease,
    write_lease,
)
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.waiting import parse_until

FENCED = frozenset({"claude-code-cli"})


class _Clock:
    """Horloge factice : ``now`` se déplace à la main."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


def _ledger(root: Path, clock: _Clock | None = None) -> MissionLedger:
    return MissionLedger(root / "ledger", clock=clock)


def _ready(ledger: MissionLedger, title: str = "T") -> str:
    mission = ledger.list_missions()[0] if ledger.list_missions() else ledger.create_mission("M", origin="test")
    task = ledger.create_task(mission.id, title, acceptance=("a",))
    ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _running(ledger: MissionLedger, title: str = "T", *, host: str = "claude-code-cli", actor: str = "agent-a") -> str:
    tid = _ready(ledger, title)
    ledger.claim_task(tid, actor, host)
    ledger.transition_task(tid, TaskState.RUNNING, actor_id=actor)
    return tid


def _epoch(ledger: MissionLedger, tid: str) -> int:
    task = ledger.get_task(tid)
    assert task is not None and task.claim is not None
    return task.claim.lease_epoch


# ── CA : 50 claims concurrents, un seul gagnant ─────────────────────────────


def _claim_racer(root: str, task_id: str, actor: str, barrier: Any, out: Any) -> None:
    ledger = MissionLedger(Path(root))
    ledger.get_task(task_id)  # état chargé avant la barrière : la fenêtre du défaut
    barrier.wait()
    try:
        ledger.claim_task(task_id, actor, "local")
        out.put((actor, "ok"))
    except GrimoireMissionError as exc:
        out.put((actor, f"refused: {exc}"))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="processus forkés : POSIX")
def test_fifty_concurrent_claims_single_winner(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _ready(ledger)
    ctx = multiprocessing.get_context("fork")
    barrier, out = ctx.Barrier(50), ctx.Queue()
    procs = [
        ctx.Process(target=_claim_racer, args=(str(tmp_path / "ledger"), tid, f"agent-{i}", barrier, out))
        for i in range(50)
    ]
    for p in procs:
        p.start()
    results = dict(out.get(timeout=120) for _ in procs)
    for p in procs:
        p.join(timeout=60)
    winners = [a for a, r in results.items() if r == "ok"]
    assert len(winners) == 1, results
    replayed = MissionLedger(tmp_path / "ledger").get_task(tid)
    assert replayed is not None and replayed.claim is not None
    assert replayed.claim.actor_id == winners[0]
    assert replayed.claim.lease_epoch == 1
    events = (tmp_path / "ledger" / "events.jsonl").read_text(encoding="utf-8")
    assert events.count('"to_state": "claimed"') == 1


# ── CA : bail perdu après TTL sans battement de cœur ────────────────────────


def test_lease_lost_after_ttl_without_heartbeat(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)
    task = ledger.get_task(tid)
    assert task is not None
    start = clock.now

    view = ledger.lease_view(task, start + timedelta(minutes=19))
    assert view is not None and view.status == "valid"
    view = ledger.lease_view(task, start + timedelta(minutes=21))
    assert view is not None and (view.status, view.reason) == ("lost", "heartbeat")
    assert [v.task_id for v in ledger.expired_claims(start + timedelta(minutes=21))] == [tid]

    # Un battement de cœur repousse la perte ; busy_until (outil long) la repousse aussi.
    clock.now = start + timedelta(minutes=15)
    ledger.renew_lease(tid, _epoch(ledger, tid), busy_until=start + timedelta(minutes=60))
    task = ledger.get_task(tid)
    assert task is not None
    view = ledger.lease_view(task, start + timedelta(minutes=50))
    assert view is not None and view.status == "valid"
    view = ledger.lease_view(task, start + timedelta(minutes=61))
    assert view is not None and view.status == "lost"


def test_lease_without_file_falls_back_on_the_ledger_ceiling(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)
    lease_path(tmp_path / "ledger", tid).unlink()
    task = ledger.get_task(tid)
    assert task is not None
    view = ledger.lease_view(task, clock.now + timedelta(hours=1))
    assert view is not None and view.status == "valid"  # pas de fichier : le plafond de 4 h du ledger
    view = ledger.lease_view(task, clock.now + timedelta(hours=5))
    assert view is not None and (view.status, view.reason) == ("lost", "expires_at")


def test_unreadable_lease_values_count_as_lost(tmp_path: Path) -> None:
    lease = LeaseFile("T", 1, renewed_at="pas une date")
    assert lease.lost_at() is None and lease.is_lost(datetime.now(UTC))
    assert LeaseFile("T", 1, renewed_at=datetime.now(UTC).isoformat(), holder=False).is_lost(datetime.now(UTC))
    garbage = tmp_path / "x.json"
    for content in ("{", "[]", '{"$schema": "autre"}', json.dumps({"$schema": "grimoire-task-lease/v1", "epoch": "1"})):
        garbage.write_text(content, encoding="utf-8")
        assert read_lease(garbage) is None
    garbage.write_text(
        json.dumps({"$schema": "grimoire-task-lease/v1", "epoch": 2, "ttl_seconds": "x", "renewed_at": "2026-01-01T00:00:00"}),
        encoding="utf-8",
    )
    parsed = read_lease(garbage)
    assert parsed is not None and parsed.ttl_seconds == 0 and parsed.is_lost(datetime.now(UTC))
    assert read_lease(tmp_path / "absent.json") is None


def test_lease_path_never_escapes_the_leases_dir(tmp_path: Path) -> None:
    assert lease_path(tmp_path, "GAO-t-001").name == "GAO-t-001.json"
    for hostile in ("../evil", "a/b", "..", ""):
        path = lease_path(tmp_path, hostile)
        assert path.parent == tmp_path / "leases" and len(path.stem) == 64


# ── CA : RUNNING → READY réservé à la récolte ───────────────────────────────


def test_running_to_ready_only_by_tick(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)

    with pytest.raises(GrimoireMissionError, match="réservé à la récolte"):
        ledger.transition_task(tid, TaskState.READY, actor_id="agent-a")

    clock.now += timedelta(minutes=30)
    report = ledger.reap(fenced_hosts=FENCED)
    assert report.clock == "ok"
    [decision] = report.decisions
    assert (decision.action, decision.cause, decision.epoch, decision.attempts) == ("ready", "heartbeat", 1, 1)
    task = ledger.get_task(tid)
    assert task is not None and task.status is TaskState.READY
    fence = read_lease(lease_path(tmp_path / "ledger", tid))
    assert fence is not None and fence.epoch == 2 and not fence.holder
    # L'ancien titulaire est refusé partout ; le claim suivant prend l'epoch d'après la barrière.
    assert not ledger.check_holder(tid, 1).ok
    ledger.claim_task(tid, "agent-b", "claude-code-cli")
    assert _epoch(ledger, tid) == 3


def test_claimed_can_block_or_fail(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    for target in (TaskState.BLOCKED, TaskState.FAILED):
        tid = _ready(ledger, f"T-{target.value}")
        ledger.claim_task(tid, "agent-a", "local")
        assert ledger.transition_task(tid, target, actor_id="agent-a").status is target


# ── CA : effet porteur d'un epoch périmé refusé ─────────────────────────────


def test_stale_epoch_event_rejected(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)
    assert ledger.transition_task(tid, TaskState.BLOCKED, lease_epoch=1, actor_id="agent-a").status is TaskState.BLOCKED
    ledger.transition_task(tid, TaskState.READY)
    ledger.claim_task(tid, "agent-b", "claude-code-cli")
    # Le blocage du titulaire a posé la barrière (epoch 2) : le claim d'agent-b prend l'epoch 3.
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="agent-b", lease_epoch=3)

    # À l'écriture : l'ancien titulaire (epoch 1) ne passe plus.
    with pytest.raises(GrimoireMissionError, match="epoch 1 périmé"):
        ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="agent-a", lease_epoch=1)

    # Au rejeu : un événement écrit hors verrou (ou importé) avec l'epoch 1 n'est pas appliqué.
    stale = ledger.append_event(
        "task.transitioned", tid, "task", "agent-a",
        {"task_id": tid, "from_state": "running", "to_state": "needs_verification", "lease_epoch": 1},
    )
    replayed = MissionLedger(tmp_path / "ledger")
    task = replayed.get_task(tid)
    assert task is not None and task.status is TaskState.RUNNING
    assert "périmé" in replayed.rejected_events()[stale.id]


def test_effect_with_same_idem_key_applies_once(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger)
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="agent-a", lease_epoch=1, step="pr-opened")
    again = ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="agent-a", lease_epoch=1, step="pr-opened")
    assert again.status is TaskState.NEEDS_VERIFICATION
    payloads = [e.payload for e in ledger.list_events(tid) if e.event_type == "task.transitioned"]
    assert sum(1 for p in payloads if p.get("idem") == f"{tid}#1#pr-opened") == 1

    # Rejoué en double (import d'un journal), l'effet ne s'applique qu'une fois.
    dup = ledger.append_event(
        "task.transitioned", tid, "task", "agent-a",
        {"task_id": tid, "from_state": "needs_verification", "to_state": "failed", "lease_epoch": 1,
         "idem": f"{tid}#1#pr-opened"},
    )
    replayed = MissionLedger(tmp_path / "ledger")
    task = replayed.get_task(tid)
    assert task is not None and task.status is TaskState.NEEDS_VERIFICATION
    assert "doublon" in replayed.rejected_events()[dup.id]


# ── CA : fichier de bail en avance sur le ledger ────────────────────────────


def test_lease_file_ahead_of_ledger_refuses_holder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)
    clock.now += timedelta(minutes=30)

    # La récolte s'interrompt entre (1) la barrière et (2) l'événement.
    real_append = MissionLedger._append_event

    def crash(self: MissionLedger, *args: Any, **kwargs: Any) -> Any:
        raise OSError("processus tué entre la barrière et l'événement")

    monkeypatch.setattr(MissionLedger, "_append_event", crash)
    with pytest.raises(OSError, match="processus tué"):
        ledger.reap(fenced_hosts=FENCED)
    monkeypatch.setattr(MissionLedger, "_append_event", real_append)

    task = MissionLedger(tmp_path / "ledger").get_task(tid)
    assert task is not None and task.status is TaskState.RUNNING and task.claim is not None
    assert task.claim.lease_epoch == 1
    # Le ledger dit encore « epoch 1 », le fichier dit 2 : le titulaire est refusé (fail-closed).
    check = ledger.check_holder(tid, 1)
    assert (check.ok, check.source) == (False, "lease")
    with pytest.raises(GrimoireMissionError, match="récolte en cours"):
        ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="agent-a", lease_epoch=1)
    with pytest.raises(GrimoireMissionError, match="récolte en cours"):
        ledger.renew_lease(tid, 1)
    # La récolte suivante termine le geste, même sur un hôte non déclaré fencé.
    [decision] = ledger.reap().decisions
    assert (decision.action, decision.cause) == ("ready", "fence_pending")
    task = ledger.get_task(tid)
    assert task is not None and task.status is TaskState.READY


def test_holder_check_falls_back_on_the_ledger_when_the_file_is_missing(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger)
    lease_path(tmp_path / "ledger", tid).unlink()
    assert ledger.check_holder(tid, 1) == ledger_mod.HolderCheck(True, "", "ledger")
    assert not ledger.check_holder(tid, 7).ok
    assert not ledger.check_holder("inconnue", 1).ok


# ── CA : horloge qui recule ─────────────────────────────────────────────────


def test_clock_backwards_is_unknown_not_valid(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)
    clock.now = datetime.now(UTC) - timedelta(hours=1)  # l'horloge recule sous le dernier événement
    task = ledger.get_task(tid)
    assert task is not None
    assert ledger.clock_state() == "unknown"
    view = ledger.lease_view(task)
    assert view is not None and view.status == "unknown"
    assert ledger.expired_claims() == []
    before = (tmp_path / "ledger" / "events.jsonl").read_text(encoding="utf-8")
    assert ledger.reap(fenced_hosts=FENCED).to_dict() == {"clock": "unknown", "decisions": []}
    assert (tmp_path / "ledger" / "events.jsonl").read_text(encoding="utf-8") == before
    # Le contrôle d'epoch ne dépend pas de l'horloge.
    assert ledger.check_holder(tid, 1).ok
    # Dans la tolérance (5 s), un léger retard n'est pas une incohérence.
    clock.now = datetime.now(UTC) - timedelta(seconds=2)
    assert ledger.clock_state() == "ok"


# ── CA : tentatives dérivées du journal ─────────────────────────────────────


def _lose_and_reap(ledger: MissionLedger, clock: _Clock, tid: str) -> Any:
    ledger.claim_task(tid, "agent", "claude-code-cli")
    clock.now = datetime.now(UTC) + timedelta(minutes=30)
    return ledger.reap(fenced_hosts=FENCED).decisions[0]


def test_attempts_derived_from_ledger(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _ready(ledger)
    assert ledger.attempts(tid) == 0 and ledger.backoff_until(tid) is None

    for n in (1, 2, 3):
        clock.now = datetime.now(UTC)
        decision = _lose_and_reap(ledger, clock, tid)
        assert (decision.action, decision.attempts) == ("ready", n)
        # Un autre lecteur, sans aucun compteur à côté, retrouve le même nombre.
        assert MissionLedger(tmp_path / "ledger").attempts(tid) == n
        assert decision.retry_at == (clock.now + BACKOFF_BASE * 2 ** (n - 1)).isoformat()
    last_lost = ledger.backoff_until(tid)
    assert last_lost is not None

    # Au-delà de 3 (D15) : plus de relance, une différée nommée, écrite une seule fois.
    clock.now = datetime.now(UTC)
    decision = _lose_and_reap(ledger, clock, tid)
    assert (decision.action, decision.cause, decision.written) == ("defer", "retry_exhausted", True)
    assert ledger.reap(fenced_hosts=FENCED).decisions == ()  # K6 : la différée s'est garée en waiting, la récolte n'y revient pas
    task = ledger.get_task(tid)
    assert task is not None and task.status is TaskState.WAITING
    assert task.waiting is not None and task.waiting.reason == "human"
    assert task.waiting.question is not None and task.waiting.question.kind == "retry_exhausted"
    assert ledger.attempts(tid) == 3

    # Un dispatch rouge compte, un rate_limit non ; une réponse humaine remet à zéro.
    ledger.append_event("task.dispatched", tid, "task", "dispatch", {"task_id": tid, "verdict": "red"})
    ledger.append_event("task.dispatched", tid, "task", "dispatch", {"task_id": tid, "verdict": "rate_limit"})
    assert ledger.attempts(tid) == 4
    # Seul le canal humain remet le compteur à zéro : un agent qui écrit l'événement lui-même n'y gagne rien.
    ledger.append_event("task.human_answered", tid, "task", "agent-a", {"task_id": tid, "origin": "agent"})
    assert ledger.attempts(tid) == 4
    ledger.append_event("task.human_answered", tid, "task", "guilhem", {"task_id": tid, "origin": "human"})
    assert ledger.attempts(tid) == 0 and ledger.backoff_until(tid) is None
    assert backoff_delay(0) == timedelta(0)
    assert sorted(p.name for p in (tmp_path / "ledger").iterdir() if not p.name.startswith(".")) == [
        "events.jsonl", "leases",
    ]


# ── CA : récolte sous quota ─────────────────────────────────────────────────


def test_reap_under_quota_waits_not_retries(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)
    ledger.append_event(
        "task.dispatched", tid, "task", "dispatch", {"task_id": tid, "verdict": "rate_limit", "provider": "anthropic"}
    )
    clock.now = datetime.now(UTC) + timedelta(minutes=30)
    [decision] = ledger.reap(fenced_hosts=FENCED).decisions
    assert (decision.action, decision.cause, decision.attempts, decision.written) == ("defer", "quota", 0, True)
    task = ledger.get_task(tid)
    assert task is not None and task.status is TaskState.WAITING  # jamais relancée dans le mur du forfait : garée (K6)
    assert task.waiting is not None and task.waiting.reason == "quota"
    assert parse_until(task.waiting.until) is not None  # datée (K6-S1-01) : le tick la lève, aucune sonde n'est requise
    assert ledger.attempts(tid) == 0
    deferred = [e for e in ledger.list_events(tid) if e.event_type == ledger_mod.REAP_DEFERRED_EVENT]
    assert [e.payload["cause"] for e in deferred] == ["quota"]
    assert ledger.reap(fenced_hosts=FENCED).decisions == ()  # la suivante ne répète pas la même différée
    assert len([e for e in ledger.list_events(tid) if e.event_type == ledger_mod.REAP_DEFERRED_EVENT]) == 1


def test_service_reads_provider_cooldown_as_quota(tmp_path: Path) -> None:
    from grimoire.missions.service import TaskService

    service = TaskService(tmp_path)
    ledger = service.ledger
    tid = _running(ledger)
    ledger.append_event("task.dispatched", tid, "task", "d", {"task_id": tid, "verdict": "red", "provider": "anthropic"})
    state = tmp_path / "_grimoire-output" / "providers-state.json"
    state.parent.mkdir(parents=True)
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    state.write_text(
        json.dumps({"providers": {"anthropic": {"cooldown_until": until, "last_failure": "rate_limit"}}}),
        encoding="utf-8",
    )
    task = ledger.get_task(tid)
    assert task is not None and service._quota_blocked(task)
    state.write_text(json.dumps({"providers": {}}), encoding="utf-8")
    assert not service._quota_blocked(task)
    ledger.append_event("task.dispatched", tid, "task", "d", {"task_id": tid, "verdict": "rate_limit"})
    assert service._quota_blocked(task)


def test_unfenced_host_is_never_relaunched(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger, host="host-copilot")
    clock.now = datetime.now(UTC) + timedelta(minutes=30)
    preview = ledger.reap(fenced_hosts=FENCED, apply=False)
    assert preview.decisions[0].written is False
    [decision] = ledger.reap(fenced_hosts=FENCED).decisions
    assert (decision.action, decision.cause) == ("defer", "resume_unfenced")
    task = ledger.get_task(tid)
    assert task is not None and task.status is TaskState.WAITING  # jamais relancée : garée, la question est à l'humain
    assert task.waiting is not None and task.waiting.question is not None
    assert task.waiting.question.kind == "resume_unfenced"


# ── renouvellement ──────────────────────────────────────────────────────────


def test_renew_pushes_the_ceiling_only_when_half_spent(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)
    task = ledger.get_task(tid)
    assert task is not None and task.claim is not None
    first = task.claim.expires_at

    clock.now += timedelta(minutes=30)
    task = ledger.renew_lease(tid, 1)
    assert task.claim is not None and task.claim.expires_at == first  # loin du plafond : rien au journal
    clock.now += timedelta(hours=2, minutes=30)
    task = ledger.renew_lease(tid, 1)
    assert task.claim is not None and task.claim.expires_at == (clock.now + timedelta(hours=4)).isoformat()
    renewed = [e for e in ledger.list_events(tid) if e.event_type == ledger_mod.LEASE_RENEWED_EVENT]
    assert len(renewed) == 1
    replayed = MissionLedger(tmp_path / "ledger").get_task(tid)
    assert replayed is not None and replayed.claim is not None and replayed.claim.expires_at == task.claim.expires_at

    with pytest.raises(GrimoireMissionError, match="périmé"):
        ledger.renew_lease(tid, 9)
    with pytest.raises(GrimoireMissionError, match="not found"):
        ledger.renew_lease("inconnue", 1)
    ledger.transition_task(tid, TaskState.BLOCKED)
    with pytest.raises(GrimoireMissionError, match="aucun bail"):
        ledger.renew_lease(tid, 1)


def test_stale_renewal_event_is_not_replayed(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger)
    stale = ledger.append_event(
        ledger_mod.LEASE_RENEWED_EVENT, tid, "task", "x", {"task_id": tid, "lease_epoch": 5, "expires_at": "2999-01-01"}
    )
    orphan = ledger.append_event(
        ledger_mod.LEASE_RENEWED_EVENT, "nulle-part", "task", "x", {"lease_epoch": 1, "expires_at": "2999-01-01"}
    )
    task = ledger.get_task(tid)
    assert task is not None and task.claim is not None and task.claim.expires_at != "2999-01-01"
    assert set(ledger.rejected_events()) == {stale.id, orphan.id}


def test_service_renew_requires_the_holder_or_its_epoch(tmp_path: Path) -> None:
    from grimoire.missions.service import TaskService

    service = TaskService(tmp_path)
    tid = _running(service.ledger, actor="agent-a")
    assert service.renew(tid, "agent-a").claim is not None
    with pytest.raises(GrimoireMissionError, match="tenu par agent-a"):
        service.renew(tid, "agent-b")
    assert service.renew(tid, "agent-b", epoch=1).status is TaskState.RUNNING


# ── compatibilité et verrou ─────────────────────────────────────────────────


def test_claim_without_epoch_reads_as_zero_and_serializes_unchanged() -> None:
    legacy = TaskClaim.from_dict({"actor_id": "a", "host_id": "h"})
    assert legacy.lease_epoch == 0 and "lease_epoch" not in legacy.to_dict()
    for bad in ("3", -1, True, None, 2.5):
        assert TaskClaim.from_dict({"actor_id": "a", "host_id": "h", "lease_epoch": bad}).lease_epoch == 0
    fenced = TaskClaim.from_dict({"actor_id": "a", "host_id": "h", "lease_epoch": 4})
    assert fenced.to_dict()["lease_epoch"] == 4
    assert fenced.with_session("s").lease_epoch == 4


def test_epoch_is_written_by_the_ledger_not_the_caller(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _ready(ledger)
    forged = TaskClaim(actor_id="a", host_id="h", lease_epoch=99)
    ledger.transition_task(tid, TaskState.CLAIMED, claim=forged)
    assert _epoch(ledger, tid) == 1
    lease = read_lease(lease_path(tmp_path / "ledger", tid))
    assert lease is not None and lease.epoch == 1 and lease.holder


def test_any_write_without_lock_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _ledger(tmp_path)
    tid = _ready(ledger)

    def no_lock(path: Path) -> Any:
        raise OSError("flock non supporté")

    monkeypatch.setattr(ledger_mod, "locked", no_lock)
    with pytest.raises(GrimoireMissionError, match="aucun verrou"):
        ledger.transition_task(tid, TaskState.BLOCKED)
    with pytest.raises(GrimoireMissionError, match="aucun verrou"):
        ledger.append_event("x", tid, "task", "a", {})


def test_append_is_synced_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    ledger = _ledger(tmp_path)
    ledger.create_mission("M", origin="test")
    # le fichier, puis le dossier (fsync de dossier : POSIX seulement)
    assert len(synced) >= (2 if os.name == "posix" else 1)


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_renew_and_reap(tmp_path: Path) -> None:
    runner = CliRunner()
    ledger = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    tid = _running(ledger, actor="cli")
    common = ["--project-root", str(tmp_path)]

    res = runner.invoke(task_app, ["renew", tid, *common])
    assert res.exit_code == 0, res.output
    assert "epoch 1" in res.output
    res = runner.invoke(task_app, ["renew", tid, "--epoch", "7", *common])
    assert res.exit_code == 1 and "périmé" in res.output

    res = runner.invoke(task_app, ["reap", *common])
    assert res.exit_code == 0 and "Aucun bail perdu" in res.output

    path = lease_path(tmp_path / "_grimoire-runtime-output" / "ledger", tid)
    write_lease(path, LeaseFile(tid, 1, (datetime.now(UTC) - timedelta(hours=1)).isoformat()))
    # K3 : le frein n'est plus déclaré par l'opérateur, il vient de la table des hôtes
    # (Claude Code 2.1.301 prouvé) ; la version de la session est celle que le hook a lue.
    ledger.attach_session(tid, "s-cli", session_host="claude-code-cli", host_version="2.1.301")
    res = runner.invoke(task_app, ["reap", *common])
    assert res.exit_code == 0 and "relancée" in res.output and "--apply" in res.output
    assert ledger.get_task(tid).status is TaskState.RUNNING  # type: ignore[union-attr]
    res = runner.invoke(task_app, ["reap", "--apply", *common])
    assert res.exit_code == 0, res.output
    assert MissionLedger(ledger._root).get_task(tid).status is TaskState.READY  # type: ignore[union-attr]

    tid2 = _running(ledger, "U", host="host-copilot", actor="cli")
    write_lease(lease_path(ledger._root, tid2), LeaseFile(tid2, 1, (datetime.now(UTC) - timedelta(hours=1)).isoformat()))
    res = runner.invoke(task_app, ["reap", "--apply", *common])
    assert res.exit_code == 0 and "différée (resume_unfenced)" in res.output
    assert MissionLedger(ledger._root).get_task(tid2).status is TaskState.WAITING  # type: ignore[union-attr]  # K6 : garée, plus récoltée
    # Aperçu : la prochaine passe n'a plus rien à juger sur une tâche garée.
    res = runner.invoke(task_app, ["reap", *common], obj={"output": "json"})
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["decisions"] == []


def test_cli_reap_says_clock_unknown_and_lock_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    root = tmp_path / "_grimoire-runtime-output" / "ledger"
    ledger = MissionLedger(root)
    tid = _running(ledger, actor="cli")
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    ledger.append_event("task.noted", tid, "task", "x", {"task_id": tid})
    events = root / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    last["created_at"] = future  # un événement daté du futur : l'horloge locale recule par rapport au journal
    events.write_text("\n".join([*lines[:-1], json.dumps(last)]) + "\n", encoding="utf-8")
    common = ["--project-root", str(tmp_path)]
    res = runner.invoke(task_app, ["reap", "--apply", *common])
    assert res.exit_code == 0 and "Horloge incohérente" in res.output

    def no_lock(path: Path) -> Any:
        raise TimeoutError("verrou occupé")

    monkeypatch.setattr(ledger_mod, "locked", no_lock)
    res = runner.invoke(task_app, ["reap", *common])
    assert res.exit_code == 1 and "aucun verrou" in res.output


# ── CA : un bail rendu ne signe plus d'effet (K2-S1-01) ─────────────────────

#: (état atteint en rendant le bail, effet périmé tenté ensuite, acteur du rendu, tâche déjà running)
_HANDED_BACK = [
    pytest.param(TaskState.READY, TaskState.BLOCKED, "human", False, id="release-humaine"),
    pytest.param(TaskState.CANCELLED, TaskState.READY, "human", True, id="annulation"),
    pytest.param(TaskState.BLOCKED, TaskState.READY, "agent-a", True, id="blocage-du-titulaire"),
    pytest.param(TaskState.FAILED, TaskState.READY, "agent-a", True, id="echec-du-titulaire"),
]


@pytest.mark.parametrize(("handed", "stale_to", "actor", "running"), _HANDED_BACK)
def test_handed_back_lease_signs_no_effect(
    tmp_path: Path, handed: TaskState, stale_to: TaskState, actor: str, running: bool
) -> None:
    ledger = _ledger(tmp_path)
    if running:
        tid = _running(ledger)
    else:
        tid = _ready(ledger)
        ledger.claim_task(tid, "agent-a", "local")
    ledger.transition_task(tid, handed, actor_id=actor)
    root = tmp_path / "ledger"

    # À l'écriture : l'epoch 1 n'est plus celui d'un bail.
    with pytest.raises(GrimoireMissionError, match="périmé"):
        ledger.transition_task(tid, stale_to, actor_id="agent-a", lease_epoch=1, step="s1")
    with pytest.raises(GrimoireMissionError, match="Renouvellement refusé"):
        ledger.renew_lease(tid, 1)

    # Au rejeu, sur une instance neuve : un effet périmé écrit hors verrou n'est pas appliqué.
    stale = ledger.append_event(
        "task.transitioned", tid, "task", "agent-a",
        {"task_id": tid, "from_state": handed.value, "to_state": stale_to.value, "lease_epoch": 1},
    )
    replayed = MissionLedger(root)
    task = replayed.get_task(tid)
    assert task is not None and task.status is handed
    assert "périmé" in replayed.rejected_events()[stale.id]

    # Le frein d'outil : le fichier de bail seul refuse, et le ledger aussi quand le fichier manque.
    fence = read_lease(lease_path(root, tid))
    assert fence is not None and fence.epoch == 2 and not fence.holder
    assert (replayed.check_holder(tid, 1).ok, replayed.check_holder(tid, 1).source) == (False, "lease")
    lease_path(root, tid).unlink()
    assert (replayed.check_holder(tid, 1).ok, replayed.check_holder(tid, 1).source) == (False, "ledger")


def test_next_claim_starts_above_the_handback_barrier(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger)
    ledger.transition_task(tid, TaskState.BLOCKED, actor_id="agent-a", lease_epoch=1)
    ledger.transition_task(tid, TaskState.READY, actor_id="human")
    ledger.claim_task(tid, "agent-b", "local")
    assert _epoch(ledger, tid) == 3  # la barrière a pris l'epoch 2
    assert ledger.check_holder(tid, 3).ok and not ledger.check_holder(tid, 1).ok
    # Le nouveau titulaire signe ses effets ; l'ancien reste refusé.
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="agent-b", lease_epoch=3)
    with pytest.raises(GrimoireMissionError, match="périmé"):
        ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="agent-a", lease_epoch=1)


def test_holder_hands_in_proof_without_losing_the_lease(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger)
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="agent-a", lease_epoch=1)
    lease = read_lease(lease_path(tmp_path / "ledger", tid))
    assert lease is not None and lease.epoch == 1 and lease.holder  # pas de barrière sur la preuve


# ── CA : une différée se nomme une fois par (epoch, cause) (K2-S1-02) ───────


def test_reap_deferred_once_per_epoch_and_cause_even_when_the_cause_alternates(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger, host="host-copilot")
    clock.now += timedelta(minutes=30)
    for i in range(5):
        ledger.reap(quota_blocked=lambda _t, flag=(i % 2 == 0): flag)  # quota, hors quota, quota…
    deferred = [e for e in ledger.list_events(tid) if e.event_type == ledger_mod.REAP_DEFERRED_EVENT]
    # K6 : la première différée gare la tâche (waiting), la récolte n'y revient plus.
    assert sorted((e.payload["lease_epoch_lost"], e.payload["cause"]) for e in deferred) == [(1, "quota")]
    task = ledger.get_task(tid)
    assert task is not None and task.status is TaskState.WAITING


# ── CA : la sortie de needs_verification rend le bail (K2-S2-01) ────────────

#: (état atteint en sortant de needs_verification, rebond éventuel vers ready)
_PROOF_EXITS = [
    pytest.param(TaskState.FAILED, False, id="echec"),
    pytest.param(TaskState.CLOSED, False, id="cloture"),
    pytest.param(TaskState.FAILED, True, id="echec-puis-ready"),
]


@pytest.mark.parametrize(("exit_to", "relaunched"), _PROOF_EXITS)
def test_leaving_needs_verification_hands_the_lease_back(tmp_path: Path, exit_to: TaskState, relaunched: bool) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger)
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="agent-a", lease_epoch=1)
    # La remise de preuve garde le titulaire, fichier compris.
    assert ledger.check_holder(tid, 1) == ledger_mod.HolderCheck(True, "", "lease")
    ledger.transition_task(tid, exit_to, actor_id="verifier")
    if relaunched:
        ledger.transition_task(tid, TaskState.READY, actor_id="human")
    root = tmp_path / "ledger"

    # Le frein d'outil, par le fichier seul : l'epoch 1 n'est plus un bail.
    fence = read_lease(lease_path(root, tid))
    assert fence is not None and fence.epoch == 2 and not fence.holder
    check = ledger.check_holder(tid, 1)
    assert (check.ok, check.source) == (False, "lease")
    # À l'écriture, et au rejeu sur instance neuve.
    with pytest.raises(GrimoireMissionError, match="périmé"):
        ledger.transition_task(tid, TaskState.CANCELLED, actor_id="agent-a", lease_epoch=1, step="s1")
    replayed = MissionLedger(root)
    assert not replayed.check_holder(tid, 1).ok
    # Le claim suivant part au-dessus de la barrière.
    if relaunched:
        ledger.claim_task(tid, "agent-b", "local")
        assert _epoch(ledger, tid) == 3


def test_returning_from_needs_verification_to_running_keeps_the_holder(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger)
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="agent-a", lease_epoch=1)
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="verifier")  # preuve refusée : retour au travail
    lease = read_lease(lease_path(tmp_path / "ledger", tid))
    assert lease is not None and lease.epoch == 1 and lease.holder
    assert ledger.check_holder(tid, 1).ok


# ── CA : un rendu interrompu n'est pas une récolte décidée (K2-S2-02) ───────


def _fail_cancel_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le journal refuse l'écriture de l'annulation (disque plein) : la barrière de rendu reste seule."""
    real = MissionLedger._write_appended

    def boom(path: Path, record: dict[str, Any]) -> Any:
        if record.get("event_type") == "task.transitioned" and record["payload"].get("to_state") == "cancelled":
            raise OSError(28, "No space left on device")
        return real(path, record)

    monkeypatch.setattr(MissionLedger, "_write_appended", staticmethod(boom))


def test_interrupted_hand_back_is_not_relaunched_on_an_unfenced_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger, host="copilot-unfenced")
    with monkeypatch.context() as patch:
        _fail_cancel_write(patch)
        with pytest.raises(OSError, match="No space"):
            ledger.transition_task(tid, TaskState.CANCELLED, actor_id="human")
    root = tmp_path / "ledger"
    barrier = read_lease(lease_path(root, tid))
    assert barrier is not None and barrier.epoch == 2 and not barrier.holder and barrier.origin == "cancel"  # K3 : l'annulation se nomme, le frein dit « annulée »

    fresh = MissionLedger(root)
    [view] = fresh.expired_claims()
    assert view.reason == "fence_pending"
    report = fresh.reap(fenced_hosts=frozenset(), apply=True)
    [decision] = report.decisions
    assert (decision.action, decision.cause) == ("defer", "resume_unfenced")
    task = MissionLedger(root).get_task(tid)
    assert task is not None and task.status is TaskState.WAITING  # pas relancée : la décision reste à l'humain (K6)


def test_interrupted_hand_back_still_honours_quota_and_exhausted_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(tmp_path)
    tid = _running(ledger)
    with monkeypatch.context() as patch:
        _fail_cancel_write(patch)
        with pytest.raises(OSError, match="No space"):
            ledger.transition_task(tid, TaskState.CANCELLED, actor_id="human")
    # Deux fois le même état interrompu : l'un sous quota, l'autre sans.
    shutil.copytree(tmp_path / "ledger", tmp_path / "ledger2")
    # Sous quota (écrit, K6) : la tâche est garée avec une échéance, puis le tick la rend quand le quota est levé.
    [decision] = MissionLedger(tmp_path / "ledger").reap(fenced_hosts=FENCED, quota_blocked=lambda _t: True).decisions
    assert (decision.action, decision.cause) == ("defer", "quota")
    parked = MissionLedger(tmp_path / "ledger").get_task(tid)
    assert parked is not None and parked.status is TaskState.WAITING and parked.waiting is not None
    due = parse_until(parked.waiting.until)
    assert parked.waiting.reason == "quota" and due is not None
    service = TaskService(tmp_path, ledger_root=tmp_path / "ledger")
    assert service.wake_due(now=due - timedelta(seconds=1))[0].action == "keep"
    assert service.wake_due(now=due)[0].action == "wake"
    back = MissionLedger(tmp_path / "ledger").get_task(tid)
    assert back is not None and back.status is TaskState.READY  # le quota levé, la tâche revient (K6-S1-01)
    # Sans quota, sur un hôte fencé, la tâche est relancée comme pour tout bail perdu.
    [decision] = MissionLedger(tmp_path / "ledger2").reap(fenced_hosts=FENCED).decisions
    assert (decision.action, decision.cause) == ("ready", "fence_pending")


def test_reap_barrier_carries_the_reap_origin(tmp_path: Path) -> None:
    clock = _Clock()
    ledger = _ledger(tmp_path, clock)
    tid = _running(ledger)
    clock.now += timedelta(minutes=30)
    ledger.reap(fenced_hosts=FENCED)
    barrier = read_lease(lease_path(tmp_path / "ledger", tid))
    assert barrier is not None and not barrier.holder and barrier.origin == "reap"


def test_lease_file_without_origin_reads_as_empty_and_is_not_a_reap(tmp_path: Path) -> None:
    path = lease_path(tmp_path / "ledger", "T")
    write_lease(path, LeaseFile("T", 2, datetime.now(UTC).isoformat(), holder=False))
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["origin"]  # fichier écrit avant K2-S2-02
    path.write_text(json.dumps(raw), encoding="utf-8")
    lease = read_lease(path)
    assert lease is not None and lease.origin == ""


def test_cli_note_writes_a_progress_event_and_refuses_a_task_nobody_holds(tmp_path: Path) -> None:
    """K5-S1-02 : `task note` est le producteur réel de `task.progress_noted`."""
    runner = CliRunner()
    ledger = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    tid = _running(ledger, actor="cli")
    common = ["--project-root", str(tmp_path)]
    res = runner.invoke(task_app, ["note", tid, "tests écrits", *common])
    assert res.exit_code == 0, res.output
    fresh = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    assert [e.event_type for e in fresh.list_events(tid)][-1] == "task.progress_noted"
    ledger.transition_task(tid, TaskState.CANCELLED, actor_id="cli")
    res = runner.invoke(task_app, ["note", tid, "trop tard", *common])
    assert res.exit_code == 1 and "refusée" in res.output
