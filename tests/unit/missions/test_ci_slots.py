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
    migrate_orphan_slots,
    reconcile_ci,
)
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.schemas import MissionTask, TaskState
from grimoire.missions.service import TaskService

STANDARD = Path("_grimoire/standard")
runner = CliRunner()


class FakeGh:
    """Un faux ``gh pr list`` : branche → lecture ; compte les LECTURES (une par appel) et garde ce qu'on lui demande."""

    def __init__(self, **by_task: CheckReading) -> None:
        self.by_branch = {f"task/{tid}": r for tid, r in by_task.items()}
        self.calls: list[tuple[str, ...]] = []
        self.repos: list[str] = []

    def __call__(self, branches: tuple[str, ...], repo: str, _cwd: Path) -> dict[str, CheckReading]:
        self.calls.append(branches)
        self.repos.append(repo)
        return {b: self.by_branch.get(b, CheckReading(ci_slots.NO_PR, "aucune PR")) for b in branches}

    @property
    def asked(self) -> set[str]:
        return {b for call in self.calls for b in call}


def _sha(n: int = 1) -> str:
    return f"{n:040x}"


def _reading(status: str, detail: str = "", sha: int = 1) -> CheckReading:
    return CheckReading(status, detail, _sha(sha))


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
    reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.DONE, "3 check(s) terminé(s)")}))
    capacity = load_capacity(projet)
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    assert not view.ci_occupants
    assert isinstance(admit(service.require(second.id), None, capacity, view), Admit)


def test_a_parked_task_with_checks_still_running_keeps_its_slot(
    projet: Path, service: TaskService, mission: str
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.PENDING, "1/3 check(s) en cours")}))
    assert _occupants(service, projet) == {first.id}


# ── Critère 2 : le tick émet ci_pushed puis ci_finished d'après gh pr checks ───────


def test_the_tick_emits_ci_pushed_then_ci_finished_from_the_checks(
    projet: Path, service: TaskService, mission: str
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    pending = FakeGh(**{first.id: _reading(ci_slots.PENDING, "1/2 check(s) en cours")})
    done = FakeGh(**{first.id: _reading(ci_slots.DONE, "2 check(s) terminé(s)")})

    run_tick(projet, apply=True, ci_probe=pending)
    assert _types(TaskService(projet), first.id) == [CI_PUSHED_EVENT]
    assert pending.asked == {f"task/{first.id}"}
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
    reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.DONE)}))
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


def _pr(branch: str, rollup: list[dict[str, str]] | None, *, sha: int = 1, number: int = 1) -> dict[str, object]:
    return {
        "headRefName": branch,
        "headRefOid": _sha(sha),
        "number": number,
        "state": "OPEN",
        "statusCheckRollup": rollup,
    }


_RUN = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}
_BUSY = {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (_completed(0, json.dumps([_pr("task/x", [_RUN, {"state": "SUCCESS"}])])), "done"),
        (_completed(0, json.dumps([_pr("task/x", [{**_RUN, "conclusion": "FAILURE"}])])), "done"),
        (_completed(0, json.dumps([_pr("task/x", [_RUN, _BUSY])])), "pending"),
        (_completed(0, json.dumps([_pr("task/x", [{"state": "PENDING"}])])), "pending"),
        (_completed(0, json.dumps([_pr("task/x", [])])), "no_pr"),
        (_completed(0, json.dumps([_pr("task/autre", [_RUN])])), "no_pr"),
        (_completed(0, "[]"), "no_pr"),
        (_completed(4, "", "authentication required"), "unknown"),
        (_completed(0, "pas du json"), "unknown"),
        (_completed(0, json.dumps([_pr("task/x", [{"inconnu": 1}])])), "unknown"),
        (_completed(0, json.dumps([{"headRefName": "task/x", "number": 1, "statusCheckRollup": [_RUN]}])), "unknown"),
    ],
)
def test_the_default_probe_reads_gh_pr_list(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str], status: str
) -> None:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return result

    monkeypatch.undo()  # le conftest mute la sonde ; ce test lit la vraie, avec un faux subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    got = ci_slots.default_ci_probe(("task/x",), "acme/kit", Path())
    assert got["task/x"].status == status
    assert seen[0][:7] == ["gh", "pr", "list", "-R", "acme/kit", "--state", "all"]
    assert len(seen) == 1


def test_a_missing_gh_binary_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    monkeypatch.undo()
    monkeypatch.setattr(subprocess, "run", boom)
    assert ci_slots.default_ci_probe(("task/x",), "acme/kit", Path())["task/x"].status == "unknown"


# ── Critère 3 : migration unique des créneaux orphelins ──────────────────────────


def test_the_migration_frees_once_the_slots_without_pr_or_running_checks(
    projet: Path, service: TaskService, mission: str
) -> None:
    orphan, building, live = _task(service, mission, "A"), _task(service, mission, "B"), _task(service, mission, "C")
    _park(service, orphan.id, "needs_verification")
    _park(service, building.id, "waiting")
    service.ledger.claim_task(live.id, "agent", "claude")
    assert _occupants(service, projet) == {orphan.id, building.id, live.id}
    gh = FakeGh(**{building.id: _reading(ci_slots.PENDING, "1/2")})

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
        _completed(8, "[]"),
        _completed(0, "pas du json"),
    ],
)
def test_a_failing_gh_is_unknown_never_no_pr(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str]
) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: result)
    got = ci_slots.default_ci_probe(("task/x", "task/y"), "acme/kit", Path())
    assert {r.status for r in got.values()} == {"unknown"}


def test_a_full_pr_list_hides_branches_so_a_missing_one_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(ci_slots, "_PR_LIMIT", 2)
    prs = [_pr("task/a", [_RUN], number=1), _pr("autre", [_RUN], number=2)]
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _completed(0, json.dumps(prs)))
    got = ci_slots.default_ci_probe(("task/a", "task/b"), "acme/kit", Path())
    assert (got["task/a"].status, got["task/b"].status) == ("done", "unknown")


def test_the_newest_pr_of_a_branch_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    prs = [_pr("task/a", [_RUN], sha=1, number=3), _pr("task/a", [_BUSY], sha=2, number=7)]
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _completed(0, json.dumps(prs)))
    got = ci_slots.default_ci_probe(("task/a",), "acme/kit", Path())["task/a"]
    assert (got.status, got.head_sha) == ("pending", _sha(2))


def test_a_gh_failing_offline_releases_nothing_and_does_not_mark_the_migration(
    monkeypatch: pytest.MonkeyPatch, projet: Path, service: TaskService, mission: str
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    monkeypatch.undo()
    monkeypatch.setattr(ci_slots, "ci_repository", lambda _home, _project: "acme/kit")
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
    reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.PENDING, "1/2")}))
    service.ledger.transition_task(first.id, final, actor_id="agent")
    assert _occupants(service, projet) == {first.id}, "le push vu survit à l'état"
    gh = FakeGh()
    outcomes = reconcile_ci(service, probe=gh)
    assert [(o.task_id, o.action) for o in outcomes] == [(first.id, "released")]
    assert gh.calls == [], "aucun appel gh"
    assert not _occupants(service, projet)


# ── #886 (a) : ci_finished n'est émis que sur le sha de tête courant ─────────────────


def test_a_new_push_emits_a_new_ci_pushed_and_keeps_the_slot_until_the_head_sha_is_stable(
    projet: Path, service: TaskService, mission: str
) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "needs_verification")
    reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.PENDING, "1/2", sha=1)}))
    # Nouveau push (sha 2) : gh rend encore des checks terminés, ceux de l'ancien commit.
    outcomes = reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.DONE, "2 check(s)", sha=2)}))
    assert [o.action for o in outcomes] == ["pushed"], "pas de ci_finished sur un sha jamais vu en cours"
    assert _types(service, first.id) == [CI_PUSHED_EVENT, CI_PUSHED_EVENT]
    assert _occupants(service, projet) == {first.id}
    pushes = [e.payload["head_sha"] for e in service.ledger.list_events(first.id) if e.event_type == CI_PUSHED_EVENT]
    assert pushes == [_sha(1), _sha(2)]
    # Lecture suivante : même sha, checks terminés : le créneau est rendu.
    outcomes = reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.DONE, "2 check(s)", sha=2)}))
    assert [o.action for o in outcomes] == ["finished"]
    assert not _occupants(service, projet)


def test_finished_checks_of_the_pushed_sha_release_the_slot(projet: Path, service: TaskService, mission: str) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "waiting")
    reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.PENDING, "1/2", sha=4)}))
    reconcile_ci(service, probe=FakeGh(**{first.id: _reading(ci_slots.DONE, "2 check(s)", sha=4)}))
    assert _types(service, first.id) == [CI_PUSHED_EVENT, CI_FINISHED_EVENT]
    assert not _occupants(service, projet)


def test_a_reading_without_head_sha_never_finishes(projet: Path, service: TaskService, mission: str) -> None:
    first = _task(service, mission, "A")
    _park(service, first.id, "waiting")
    reconcile_ci(service, probe=FakeGh(**{first.id: CheckReading(ci_slots.DONE, "sans sha")}))
    assert _types(service, first.id) == []
    assert _occupants(service, projet) == {first.id}


# ── #886 (b) : une seule lecture gh par tick, pour tous les occupants ────────────────


def test_one_gh_read_covers_every_occupant_of_the_tick(projet: Path, service: TaskService, mission: str) -> None:
    (projet / STANDARD / "capacity.yaml").write_text("version: 1\npools:\n  ci:\n    runners: 9\n", encoding="utf-8")
    ids = [_task(service, mission, t).id for t in "ABCD"]
    for tid in ids:
        _park(service, tid, "needs_verification")
    gh = FakeGh(**{ids[0]: _reading(ci_slots.PENDING, "1/2")})
    run_tick(projet, apply=True, ci_probe=gh)
    assert len(gh.calls) == 1, "reconcile + migration : une seule lecture pour 4 occupants"
    assert gh.asked == {f"task/{t}" for t in ids}


def test_reconcile_alone_reads_once_for_all_occupants(projet: Path, service: TaskService, mission: str) -> None:
    (projet / STANDARD / "capacity.yaml").write_text("version: 1\npools:\n  ci:\n    runners: 9\n", encoding="utf-8")
    ids = [_task(service, mission, t).id for t in "AB"]
    for tid in ids:
        _park(service, tid, "waiting")
    gh = FakeGh()
    reconcile_ci(service, probe=gh)
    assert len(gh.calls) == 1


def test_a_failing_gh_read_emits_nothing_for_any_occupant(
    monkeypatch: pytest.MonkeyPatch, projet: Path, service: TaskService, mission: str
) -> None:
    first, second = _task(service, mission, "A"), _task(service, mission, "B")
    (projet / STANDARD / "capacity.yaml").write_text("version: 1\npools:\n  ci:\n    runners: 9\n", encoding="utf-8")
    _park(service, first.id, "waiting")
    _park(service, second.id, "needs_verification")
    monkeypatch.undo()
    monkeypatch.setattr(ci_slots, "ci_repository", lambda _home, _project: "acme/kit")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _completed(1, "", "rate limit"))
    run_tick(projet, apply=True)
    assert _types(TaskService(projet), first.id) == [] == _types(TaskService(projet), second.id)
    assert _occupants(service, projet) == {first.id, second.id}


# ── #886 (c) : le dépôt vient de la politique, sinon du remote origin ────────────────


def _policy(projet: Path, body: str) -> None:
    (projet / STANDARD / "orchestration-policy.yaml").write_text(body, encoding="utf-8")


def test_the_probed_repository_comes_from_the_policy(
    monkeypatch: pytest.MonkeyPatch, projet: Path, service: TaskService, mission: str
) -> None:
    monkeypatch.undo()  # la vraie résolution du dépôt
    _policy(projet, "ci:\n  repository: acme/autre-depot\n")
    first = _task(service, mission, "A")
    _park(service, first.id, "waiting")
    gh = FakeGh()
    reconcile_ci(service, probe=gh)
    assert gh.repos == ["acme/autre-depot"]


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://github.com/acme/kit.git", "acme/kit"),
        ("git@github.com:acme/kit.git", "acme/kit"),
        ("ssh://git@github.com/acme/kit", "acme/kit"),
        ("https://ghe.example.com/acme/kit.git", "ghe.example.com/acme/kit"),
    ],
)
def test_without_policy_the_repository_is_the_origin_remote(
    monkeypatch: pytest.MonkeyPatch, projet: Path, url: str, slug: str
) -> None:
    monkeypatch.undo()
    asked: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        asked.append(argv)
        return _completed(0, url + "\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ci_slots.ci_repository(projet, projet) == slug
    assert asked[0][:3] == ["git", "-C", str(projet)] and asked[0][-3:] == ["remote", "get-url", "origin"]


def test_the_policy_wins_over_the_remote(monkeypatch: pytest.MonkeyPatch, projet: Path) -> None:
    monkeypatch.undo()
    _policy(projet, "ci:\n  repository: acme/politique\n")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("git ne doit pas être lancé"))
    assert ci_slots.ci_repository(projet, projet) == "acme/politique"


def test_an_unresolvable_repository_reads_unknown_and_no_remote_reads_no_pr(
    monkeypatch: pytest.MonkeyPatch, projet: Path, service: TaskService, mission: str
) -> None:
    monkeypatch.undo()
    first = _task(service, mission, "A")
    _park(service, first.id, "waiting")
    gh = FakeGh(**{first.id: _reading(ci_slots.DONE)})
    _policy(projet, "ci:\n  repository: pas un slug\n")
    assert reconcile_ci(service, probe=gh) == [] and gh.calls == [], "politique invalide : aucune lecture, rien rendu"
    (projet / STANDARD / "orchestration-policy.yaml").unlink()
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _completed(2, "", "error: No such remote 'origin'"))
    assert migrate_orphan_slots(service, probe=gh) != [], "sans remote : aucune PR possible, l'orphelin est libéré"
    assert gh.calls == []
