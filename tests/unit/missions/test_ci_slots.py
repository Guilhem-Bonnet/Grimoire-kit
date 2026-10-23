"""Créneaux CI rendus (#876, lot 0, verrou 2) : le tick constate les checks, la migration libère les orphelins.

Aucun appel réseau : la sonde est un faux ``gh`` (fonction), ou un ``subprocess.run`` substitué.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import ci_slots
from grimoire.missions.admission import (
    CAPACITY_RELEASED_EVENT,
    CI_FINISHED_EVENT,
    CI_PUSHED_EVENT,
    Admit,
    admit,
    derive_view,
    load_capacity,
)
from grimoire.missions.ci_slots import (
    CI_MIGRATED_EVENT,
    CheckReading,
    default_ci_probe,
    migrate_orphan_slots,
    reconcile_ci,
)
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.schemas import MissionTask, TaskState
from grimoire.missions.service import TaskService

STANDARD = Path("_grimoire/standard")
runner = CliRunner()


class FakeGh:
    """Un faux ``gh pr checks`` : branche → lecture ; compte les appels."""

    def __init__(self, **by_task: CheckReading) -> None:
        self.by_branch = {f"task/{tid}": r for tid, r in by_task.items()}
        self.calls: list[str] = []

    def __call__(self, branch: str, _repo: Path) -> CheckReading:
        self.calls.append(branch)
        return self.by_branch.get(branch, CheckReading(ci_slots.NO_PR, "aucune PR"))


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    (tmp_path / STANDARD / "capacity.yaml").write_text("version: 1\npools:\n  ci:\n    runners: 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


@pytest.fixture
def mission(service: TaskService) -> str:
    return service.ledger.create_mission(title="CI", origin="test").id


def _task(service: TaskService, mission: str, title: str) -> MissionTask:
    task = service.ledger.create_task(mission, title, acceptance=("ok",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task


def _park(service: TaskService, task_id: str, how: str) -> None:
    ledger = service.ledger
    ledger.claim_task(task_id, "agent", "claude")
    ledger.transition_task(task_id, TaskState.RUNNING, actor_id="agent")
    if how == "needs_verification":
        ledger.transition_task(task_id, TaskState.NEEDS_VERIFICATION, actor_id="agent")
        return
    epoch = service.require(task_id).claim.lease_epoch  # type: ignore[union-attr]
    waiting = {"waiting": {"reason": "ci", "until": None, "question": None, "resume_hint": "checks"}}
    ledger.transition_task(task_id, TaskState.WAITING, actor_id="agent", extra_payload=waiting, lease_epoch=epoch)


def _occupants(service: TaskService, projet: Path) -> frozenset[str]:
    return derive_view(service.ledger, load_capacity(projet), now=datetime.now(UTC)).ci_occupants


def _types(service: TaskService, tid: str) -> list[str]:
    return [e.event_type for e in service.ledger.list_events(tid) if e.event_type.startswith(("task.ci_", "capacity."))]


# ── Critère 1 : checks terminés = créneau rendu, pour les tâches garées ───────────


@pytest.mark.parametrize("how", ["needs_verification", "waiting"])
def test_a_parked_task_whose_pr_checks_ended_no_longer_occupies_ci(
    projet: Path, service: TaskService, mission: str, how: str
) -> None:
    first, second = _task(service, mission, "A"), _task(service, mission, "B")
    _park(service, first.id, how)
    assert _occupants(service, projet) == {first.id}
    assert not isinstance(
        admit(
            service.require(second.id),
            None,
            load_capacity(projet),
            derive_view(service.ledger, load_capacity(projet), now=datetime.now(UTC)),
        ),
        Admit,
    )
    reconcile_ci(service, probe=FakeGh(**{first.id: CheckReading(ci_slots.DONE, "3 check(s) terminé(s)")}))
    capacity = load_capacity(projet)
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    assert not view.ci_occupants
    assert isinstance(admit(service.require(second.id), None, capacity, view), Admit)


def test_a_parked_task_with_checks_still_running_keeps_its_slot(
    projet: Path, service: TaskService, mission: str
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    reconcile_ci(service, probe=FakeGh(**{first.id: CheckReading(ci_slots.PENDING, "1/3 check(s) en cours")}))
    assert _occupants(service, projet) == {first.id}


# ── Critère 2 : le tick émet ci_pushed puis ci_finished d'après gh pr checks ───────


def test_the_tick_emits_ci_pushed_then_ci_finished_from_the_checks(
    projet: Path, service: TaskService, mission: str
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    pending = FakeGh(**{first.id: CheckReading(ci_slots.PENDING, "1/2 check(s) en cours")})
    done = FakeGh(**{first.id: CheckReading(ci_slots.DONE, "2 check(s) terminé(s)")})

    run_tick(projet, apply=True, ci_probe=pending)
    assert _types(TaskService(projet), first.id) == [CI_PUSHED_EVENT]
    assert set(pending.calls) == {f"task/{first.id}"}
    run_tick(projet, apply=True, ci_probe=pending)
    assert _types(TaskService(projet), first.id).count(CI_PUSHED_EVENT) == 1, (
        "idempotent : un push n'est écrit qu'une fois"
    )

    report = run_tick(projet, apply=True, ci_probe=done)
    assert [o.action for o in report.ci] == ["finished"]
    again = TaskService(projet)
    seen = _types(again, first.id)
    assert seen.index(CI_PUSHED_EVENT) < seen.index(CI_FINISHED_EVENT)
    assert not _occupants(again, projet)

    run_tick(projet, apply=True, ci_probe=done)
    assert _types(TaskService(projet), first.id) == seen, "un tick de plus n'écrit rien"


def test_checks_found_already_done_write_pushed_before_finished(
    projet: Path, service: TaskService, mission: str
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "waiting")
    reconcile_ci(service, probe=FakeGh(**{first.id: CheckReading(ci_slots.DONE)}))
    assert _types(service, first.id) == [CI_PUSHED_EVENT, CI_FINISHED_EVENT]


def test_a_mute_or_prless_probe_writes_nothing(projet: Path, service: TaskService, mission: str) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    reconcile_ci(service, probe=FakeGh(**{first.id: CheckReading(ci_slots.UNKNOWN, "gh muet")}))
    reconcile_ci(service, probe=FakeGh())
    assert _types(service, first.id) == []
    assert _occupants(service, projet) == {first.id}


def _completed(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, out, err)


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (_completed(0, json.dumps([{"name": "a", "bucket": "pass"}, {"name": "b", "bucket": "skipping"}])), "done"),
        (_completed(1, json.dumps([{"name": "a", "bucket": "fail"}])), "done"),
        (_completed(8, json.dumps([{"name": "a", "bucket": "pass"}, {"name": "b", "bucket": "pending"}])), "pending"),
        (_completed(0, "[]"), "no_pr"),
        (_completed(1, "", 'no pull requests found for branch "task/x"'), "no_pr"),
        (_completed(4, "", "authentication required"), "unknown"),
        (_completed(0, "pas du json"), "unknown"),
    ],
)
def test_the_default_probe_reads_gh_pr_checks(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str], status: str
) -> None:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert default_ci_probe("task/x", Path()).status == status
    assert seen[0][:4] == ["gh", "pr", "checks", "task/x"]


def test_a_missing_gh_binary_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", boom)
    assert default_ci_probe("task/x", Path()).status == "unknown"


# ── Critère 3 : migration unique des créneaux orphelins ──────────────────────────


def test_the_migration_frees_once_the_slots_without_pr_or_running_checks(
    projet: Path, service: TaskService, mission: str
) -> None:
    orphan, building, live = _task(service, mission, "A"), _task(service, mission, "B"), _task(service, mission, "C")
    _park(service, orphan.id, "needs_verification")
    _park(service, building.id, "waiting")
    service.ledger.claim_task(live.id, "agent", "claude")
    assert _occupants(service, projet) == {orphan.id, building.id, live.id}
    gh = FakeGh(**{building.id: CheckReading(ci_slots.PENDING, "1/2")})

    done = migrate_orphan_slots(service, probe=gh)
    assert [(o.task_id, o.action) for o in done] == [(orphan.id, "released")]
    assert _occupants(service, projet) == {building.id, live.id}, "checks en cours et bail vivant gardent leur créneau"
    assert CAPACITY_RELEASED_EVENT in _types(service, orphan.id)
    assert [e.event_type for e in service.ledger.list_events("ci-slots")] == [CI_MIGRATED_EVENT]

    assert migrate_orphan_slots(service, probe=FakeGh()) == [], "unique : le marqueur interdit de rejouer"
    assert _occupants(service, projet) == {building.id, live.id}


def test_the_migration_waits_when_a_probe_is_mute(projet: Path, service: TaskService, mission: str) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    mute = FakeGh(**{first.id: CheckReading(ci_slots.UNKNOWN, "gh muet")})
    assert migrate_orphan_slots(service, probe=mute) == []
    assert not service.ledger.list_events("ci-slots"), "non marquée : le tick suivant la reprend"
    assert [o.task_id for o in migrate_orphan_slots(service, probe=FakeGh())] == [first.id]


def test_a_demo_ledger_plan_admits_a_task_after_the_migration(
    projet: Path, service: TaskService, mission: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for title in ("A", "B", "C"):
        _park(service, _task(service, mission, title).id, "needs_verification")
    ready = _task(service, mission, "prête")
    # Avant : le créneau unique est pris par une tâche garée, rien n'est admis.
    assert not service.queue_plan().admitted
    monkeypatch.setattr(ci_slots, "default_ci_probe", FakeGh())

    result = runner.invoke(app, ["orchestrate", "tick", "--plan", "--json", "--project-root", str(projet)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [a["task_id"] for a in data["plan"]["admitted"]] == [ready.id]
    assert sorted(o["action"] for o in data["ci"]) == ["released"] * 3
    assert TaskService(projet).ledger.list_events("ci-slots") == [], "le plan ne touche pas au vrai ledger"


# ── Revue #880 : un gh en échec n'est pas « aucune PR » ─────────────────────────────


@pytest.mark.parametrize(
    "result",
    [
        _completed(1, "", "error connecting to api.github.com"),
        _completed(1, ""),
        _completed(0, ""),
        _completed(8, ""),
        _completed(1, "pas du json"),
        _completed(8, "[]"),
    ],
)
def test_a_failing_gh_is_unknown_never_no_pr(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str]
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: result)
    assert default_ci_probe("task/x", Path()).status == "unknown"


def test_no_pull_requests_found_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _completed(1, "", "No Pull Requests Found for branch"))
    assert default_ci_probe("task/x", Path()).status == "no_pr"


def test_a_gh_failing_offline_releases_nothing_and_does_not_mark_the_migration(
    monkeypatch: pytest.MonkeyPatch, projet: Path, service: TaskService, mission: str
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _completed(1, "", "error connecting to api.github.com"))
    assert migrate_orphan_slots(service) == []
    assert reconcile_ci(service) == []
    assert _occupants(service, projet) == {first.id}
    assert not service.ledger.list_events("ci-slots")


def test_one_unknown_reading_defers_the_marker_even_when_others_are_released(
    projet: Path, service: TaskService, mission: str
) -> None:
    orphan, mute = _task(service, mission, "A"), _task(service, mission, "B")
    _park(service, orphan.id, "needs_verification")
    _park(service, mute.id, "waiting")
    gh = FakeGh(**{mute.id: CheckReading(ci_slots.UNKNOWN, "gh muet")})
    assert [o.task_id for o in migrate_orphan_slots(service, probe=gh)] == [orphan.id]
    assert not service.ledger.list_events("ci-slots"), "retentée au tick suivant"
    assert _occupants(service, projet) == {mute.id}


@pytest.mark.parametrize("final", [TaskState.CANCELLED, TaskState.CLOSED])
def test_a_pushed_task_in_a_terminal_state_releases_its_slot_without_gh(
    projet: Path, service: TaskService, mission: str, final: TaskState
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    reconcile_ci(service, probe=FakeGh(**{first.id: CheckReading(ci_slots.PENDING, "1/2")}))
    service.ledger.transition_task(first.id, final, actor_id="agent")
    assert _occupants(service, projet) == {first.id}, "le push vu survit à l'état"
    gh = FakeGh()
    outcomes = reconcile_ci(service, probe=gh)
    assert [(o.task_id, o.action) for o in outcomes] == [(first.id, "released")]
    assert gh.calls == [], "aucun appel gh"
    assert not _occupants(service, projet)
