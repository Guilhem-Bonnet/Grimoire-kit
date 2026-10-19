"""K10 : une file triée et une admission par réservoirs déclarés (épopée #705).

Avant : ``task_list_ready`` rendait l'ordre du ledger (la priorité, vieillie ou non, n'y
comptait pas), le WIP de ``orchestration-policy.yaml`` n'était lu que par le contrôle du
standard et jamais au claim, aucune sonde de ressource n'existait, aucun quota de tokens
par jour n'était réglable, et dix PR réalignées donnaient une file CI de plus de 30 min.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import admission
from grimoire.missions.admission import (
    CAPACITY_RELEASED_EVENT,
    CI_FINISHED_EVENT,
    CI_PUSHED_EVENT,
    QUOTA_CONSUMED_EVENT,
    Admit,
    Defer,
    HostReading,
    RouteRef,
    admit,
    build_plan,
    derive_view,
    load_capacity,
    queue_order,
    read_host,
)
from grimoire.missions.schemas import MissionTask, TaskState, TaskType
from grimoire.missions.service import TaskService

ACC = "la file se vide dans l'ordre"
STANDARD = Path("_grimoire/standard")
runner = CliRunner()


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


@pytest.fixture
def mission(service: TaskService) -> str:
    return service.ledger.create_mission(title="File", origin="test").id


def _capacity(projet: Path, text: str) -> None:
    (projet / STANDARD / "capacity.yaml").write_text(text, encoding="utf-8")


def _pret(service: TaskService, mission: str, titre: str, **kw: object) -> MissionTask:
    kw.setdefault("owner", "amelia")
    task = service.ledger.create_task(mission, titre, acceptance=(ACC,), **kw)  # type: ignore[arg-type]
    service.ledger.transition_task(task.id, TaskState.READY)
    return task


def _later(hours: float) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


# ── La file ────────────────────────────────────────────────────────────────────


def test_list_ready_is_ordered_by_priority_not_by_ledger_order(service: TaskService, mission: str) -> None:
    low = _pret(service, mission, "basse", priority="low")
    critical = _pret(service, mission, "critique", priority="critical")
    medium = _pret(service, mission, "moyenne", priority="medium")
    assert [t.id for t in service.list_ready()] == [critical.id, medium.id, low.id]


def test_equal_priority_is_oldest_first(service: TaskService, mission: str) -> None:
    first = _pret(service, mission, "première", priority="high")
    second = _pret(service, mission, "seconde", priority="high")
    assert [t.id for t in service.list_ready()] == [first.id, second.id]


def test_a_task_ages_one_notch_per_24h_up_to_critical(service: TaskService, mission: str) -> None:
    old_low = _pret(service, mission, "vieille basse", priority="low")
    fresh_medium = _pret(service, mission, "moyenne", priority="medium")
    ready = [service.require(old_low.id), service.require(fresh_medium.id)]
    since = admission.ready_since(service.ledger)
    now = _later(0)
    # Avant 24 h : la moyenne passe devant la basse.
    assert [e.task.id for e in queue_order(ready, since, now=now)] == [fresh_medium.id, old_low.id]
    # La basse n'a pas vieilli seule : on avance l'horloge de 49 h, les deux vieillissent.
    aged = queue_order(ready, since, now=_later(49))
    assert {e.task.id: (e.effective, e.boosts) for e in aged} == {old_low.id: ("high", 2), fresh_medium.id: ("critical", 2)}
    # Plafond : jamais au-delà de critical, à égalité l'ancienneté départage.
    capped = queue_order(ready, since, now=_later(24 * 30))
    assert {e.effective for e in capped} == {"critical"}


def test_only_a_24h_wait_lifts_the_older_task_over_a_fresher_one(service: TaskService, mission: str) -> None:
    older = _pret(service, mission, "ancienne basse", priority="low")
    since = admission.ready_since(service.ledger)
    newer = MissionTask.from_dict({**service.require(older.id).to_dict(), "id": "T-neuf", "priority": "medium"})
    entries = queue_order([service.require(older.id), newer], {**since, "T-neuf": _later(25)}, now=_later(25))
    # La basse a attendu 25 h (+1 : medium) et précède la moyenne arrivée à l'instant.
    assert [e.task.id for e in entries] == [older.id, "T-neuf"]


def test_dependencies_and_backoff_keep_a_task_out_of_the_queue(service: TaskService, mission: str) -> None:
    a = _pret(service, mission, "A")
    b = _pret(service, mission, "B")
    service.ledger.link_tasks(b.id, a.id)
    plan = service.queue_plan(routes=False)
    assert [d.task_id for d in plan.admitted] == [a.id]
    assert plan.excluded and plan.excluded[0][0] == b.id and a.id in plan.excluded[0][1]


# ── Les réservoirs ─────────────────────────────────────────────────────────────


def test_ten_pr_tasks_with_three_runners_admit_three_and_defer_seven_on_ci(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "version: 1\npools:\n  ci:\n    runners: 3\n")
    ids = [_pret(service, mission, f"PR {i}", owner=f"role{i}").id for i in range(10)]
    plan = service.queue_plan(routes=False)
    assert [a.task_id for a in plan.admitted] == ids[:3]
    assert [d.reason for d in plan.deferred] == ["ci"] * 7
    assert [d.label for d in plan.deferred] == ["Defer(ci)"] * 7
    assert all("3/3" in d.detail for d in plan.deferred)


def test_ci_runners_default_to_three_without_a_capacity_file(service: TaskService, mission: str) -> None:
    for i in range(5):
        _pret(service, mission, f"PR {i}", owner=f"role{i}")
    plan = service.queue_plan(routes=False)
    assert (len(plan.admitted), len(plan.deferred)) == (3, 2)


def test_a_task_type_without_a_push_does_not_take_a_ci_slot(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\npools:\n  ci:\n    runners: 1\n")
    _pret(service, mission, "PR", owner="a")
    _pret(service, mission, "analyse", owner="b", type=TaskType.ANALYSIS)
    plan = service.queue_plan(routes=False)
    assert len(plan.admitted) == 2


def test_unreadable_probe_refuses_admission(projet: Path, service: TaskService, mission: str, tmp_path: Path) -> None:
    _capacity(projet, "version: 1\npools:\n  host:\n    max_load_per_cpu: 2\n")
    task = _pret(service, mission, "t")
    capacity = load_capacity(projet)
    view = derive_view(service.ledger, capacity, now=_later(0), host=None)
    decision = admit(service.require(task.id), None, capacity, view)
    assert isinstance(decision, Defer) and decision.reason == "unknown" and "sonde illisible" in decision.detail
    # La sonde réelle est muette sans /proc/meminfo (hors Linux) : None, jamais une valeur inventée.
    assert read_host(meminfo=tmp_path / "absent") is None

    def no_loadavg() -> tuple[float, float, float]:
        raise OSError("getloadavg indisponible")

    assert read_host(getloadavg=no_loadavg) is None


def test_host_pool_defers_on_load_and_memory_and_admits_otherwise(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\npools:\n  host:\n    max_load_per_cpu: 1.0\n    min_mem_available_mb: 1024\n")
    task = service.require(_pret(service, mission, "t").id)
    capacity = load_capacity(projet)

    def decide(load: float, mem: int) -> Admit | Defer:
        view = derive_view(service.ledger, capacity, now=_later(0), host=HostReading(load, mem))
        return admit(task, None, capacity, view)

    assert isinstance(decide(0.4, 4096), Admit)
    busy = decide(2.5, 4096)
    assert isinstance(busy, Defer) and busy.reason == "host" and "charge" in busy.detail
    low = decide(0.4, 200)
    assert isinstance(low, Defer) and low.reason == "host" and "mémoire" in low.detail


def test_read_host_parses_proc_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16000000 kB\nMemAvailable:    8388608 kB\n", encoding="utf-8")
    reading = read_host(meminfo=meminfo, getloadavg=lambda: (4.0, 0.0, 0.0), cpu_count=lambda: 8)
    assert reading == HostReading(0.5, 8192)


def test_an_undeclared_pool_is_not_probed(service: TaskService, mission: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_: object) -> None:
        raise AssertionError("pool host non déclaré : jamais sondé")

    monkeypatch.setattr("grimoire.missions.service.read_host", boom)
    _pret(service, mission, "t")
    assert len(service.queue_plan(routes=False).admitted) == 1


def test_llm_pool_limits_concurrent_sessions_per_provider(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\npools:\n  llm:\n    claude:\n      sessions: 1\n")
    a = service.require(_pret(service, mission, "a", owner="x").id)
    capacity = load_capacity(projet)
    route = RouteRef("route", "claude", "sonnet")
    view = derive_view(service.ledger, capacity, now=_later(0)).reserve(a, route, consumes_ci=False)
    other = service.require(_pret(service, mission, "b", owner="y").id)
    blocked = admit(other, route, capacity, view)
    assert isinstance(blocked, Defer) and blocked.reason == "llm" and blocked.route == route
    assert isinstance(admit(other, RouteRef("route", "ollama", "qwen"), capacity, view), Admit)


# ── Quota de tokens par jour ───────────────────────────────────────────────────


def _consume(service: TaskService, tokens: int) -> None:
    service.ledger.append_event(QUOTA_CONSUMED_EVENT, "project", "project", "test", {"tokens": tokens})


def test_quota_defers_with_chosen_route(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\nquota:\n  tokens_per_day: 1000\n")
    task = service.require(_pret(service, mission, "t").id)
    capacity = load_capacity(projet)
    route = RouteRef("route", "claude", "sonnet")
    _consume(service, 1500)
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    decision = admit(task, route, capacity, view)
    assert isinstance(decision, Defer) and decision.reason == "quota"
    assert decision.route == route  # la route du routeur n'est ni changée ni dégradée
    assert decision.retry_at.startswith(f"{(datetime.now(UTC) + timedelta(days=1)).date().isoformat()}T00:00:00")


def test_quota_below_the_limit_admits_and_yesterday_does_not_count(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\nquota:\n  tokens_per_day: 1000\n")
    task = service.require(_pret(service, mission, "t").id)
    capacity = load_capacity(projet)
    _consume(service, 400)
    assert isinstance(admit(task, None, capacity, derive_view(service.ledger, capacity, now=_later(0))), Admit)
    # Le lendemain, la consommation d'hier ne compte plus.
    _consume(service, 700)
    assert isinstance(admit(task, None, capacity, derive_view(service.ledger, capacity, now=_later(0))), Defer)
    assert isinstance(admit(task, None, capacity, derive_view(service.ledger, capacity, now=_later(30))), Admit)


@pytest.mark.parametrize("declaration", ["", "quota:\n  tokens_per_day: unlimited\n", "quota:\n  tokens_per_day: illimité\n"])
def test_unlimited_quota_never_defers(projet: Path, service: TaskService, mission: str, declaration: str) -> None:
    _capacity(projet, f"version: 1\n{declaration}")
    task = service.require(_pret(service, mission, "t").id)
    capacity = load_capacity(projet)
    assert capacity.unlimited and not capacity.errors
    _consume(service, 10**12)
    service.ledger.append_event("task.dispatched", task.id, "task", "test", {"verdict": "green"})
    view = derive_view(service.ledger, capacity, now=_later(0))
    for autonomous in (False, True):
        assert isinstance(admit(task, RouteRef("route", "claude", "opus"), capacity, view, autonomous=autonomous), Admit)


def test_unlimited_is_the_default_without_a_capacity_file(projet: Path) -> None:
    capacity = load_capacity(projet)
    assert capacity.unlimited and capacity.tokens_per_day == "unlimited"


@pytest.mark.parametrize("value", ["0", "-5", "'beaucoup'", "true", "1.5"])
def test_a_quota_that_is_not_unlimited_or_positive_is_refused_not_read_as_zero(
    projet: Path, service: TaskService, mission: str, value: str
) -> None:
    _capacity(projet, f"version: 1\nquota:\n  tokens_per_day: {value}\n")
    capacity = load_capacity(projet)
    assert "quota" in capacity.errors
    task = service.require(_pret(service, mission, "t").id)
    decision = admit(task, None, capacity, derive_view(service.ledger, capacity, now=_later(0)))
    assert isinstance(decision, Defer) and decision.reason == "unknown"


def test_finite_quota_with_unknown_consumption_refuses_autonomous_and_shows_interactive(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "version: 1\nquota:\n  tokens_per_day: 1000\n")
    task = service.require(_pret(service, mission, "t").id)
    service.ledger.append_event("task.dispatched", task.id, "task", "test", {"verdict": "green"})  # appel sans chiffre
    capacity = load_capacity(projet)
    view = derive_view(service.ledger, capacity, now=_later(0))
    assert view.tokens.status == "unknown" and view.tokens.tokens is None
    refused = admit(task, None, capacity, view, autonomous=True)
    assert isinstance(refused, Defer) and refused.reason == "unknown"
    assert isinstance(admit(task, None, capacity, view, autonomous=False), Admit)


def test_the_router_never_reads_the_quota() -> None:
    import grimoire.missions.router as router_module
    import grimoire.missions.routing_policy as policy_module

    for module in (router_module, policy_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "admission" not in source.replace("l'admission", "").replace("de l'admission", "")


# ── Occupation dérivée du ledger ───────────────────────────────────────────────


def test_occupancy_derived_from_ledger(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\npools:\n  ci:\n    runners: 2\n")
    a = _pret(service, mission, "a", owner="x")
    b = _pret(service, mission, "b", owner="y")
    c = _pret(service, mission, "c", owner="z")
    service.claim(a.id, "x")
    service.ledger.append_event(CI_PUSHED_EVENT, b.id, "task", "tick", {"head": "abc"})
    capacity = load_capacity(projet)
    view = derive_view(service.ledger, capacity, now=_later(0))
    assert view.ci_occupants == {a.id, b.id}
    # Un autre processus, un autre objet : même occupation, rien n'a été écrit à côté du journal.
    fresh = derive_view(TaskService(projet).ledger, capacity, now=_later(0))
    assert fresh.ci_occupants == view.ci_occupants and [h.task_id for h in fresh.held] == [a.id]
    ledger_files = sorted(p.name for p in (projet / "_grimoire-runtime-output" / "ledger").rglob("*") if p.is_file())
    assert not any("held" in name for name in ledger_files)
    assert not [p for p in projet.rglob("held") if p.exists()]
    plan = service.queue_plan(routes=False)
    assert [d.task_id for d in plan.deferred] == [c.id] and plan.deferred[0].reason == "ci"


def test_checks_finished_or_a_human_release_frees_the_slot(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\npools:\n  ci:\n    runners: 1\n")
    a = _pret(service, mission, "a", owner="x")
    b = _pret(service, mission, "b", owner="y")
    service.claim(a.id, "x")
    service.ledger.append_event(CI_PUSHED_EVENT, a.id, "task", "tick", {})
    assert [d.reason for d in service.queue_plan(routes=False).deferred] == ["ci"]
    service.ledger.append_event(CI_FINISHED_EVENT, a.id, "task", "tick", {})
    assert [x.task_id for x in service.queue_plan(routes=False).admitted] == [b.id]
    # Observation impossible : le créneau reste pris, jusqu'à ce qu'un humain le libère.
    service.ledger.append_event(CI_PUSHED_EVENT, a.id, "task", "tick", {})
    assert [d.reason for d in service.queue_plan(routes=False).deferred] == ["ci"]
    service.release_capacity(a.id, "ci", "guilhem", "checks perdus")
    released = [e for e in service.ledger.list_events(a.id) if e.event_type == CAPACITY_RELEASED_EVENT]
    assert released and released[0].payload["pool"] == "ci"
    assert [x.task_id for x in service.queue_plan(routes=False).admitted] == [b.id]


# ── Famine ─────────────────────────────────────────────────────────────────────


def test_defer_over_24h_raises_famine(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\npools:\n  ci:\n    runners: 1\n")
    _pret(service, mission, "tient le créneau", owner="x")
    waiting = _pret(service, mission, "attend", owner="y")
    service.ledger.append_event(CI_PUSHED_EVENT, service.list_ready()[0].id, "task", "tick", {})
    soon = service.queue_plan(routes=False, now=_later(3))
    assert not soon.famine
    plan = service.queue_plan(routes=False, now=_later(30))
    assert waiting.id in {f.task_id for f in plan.famine}
    famine = next(f for f in plan.famine if f.task_id == waiting.id)
    assert famine.attention.level == 2 and "Defer(ci)" in famine.reason and famine.waited_hours >= 24


def test_an_admitted_task_is_never_in_famine(service: TaskService, mission: str) -> None:
    _pret(service, mission, "t")
    assert not service.queue_plan(routes=False, now=_later(100)).famine


# ── WIP au claim ───────────────────────────────────────────────────────────────


def _wip(projet: Path, *, total: int, role: int) -> None:
    (projet / STANDARD / "orchestration-policy.yaml").write_text(
        f"wip:\n  limit_global: {total}\n  limit_per_role: {role}\n  open_delegation: forbidden\n", encoding="utf-8"
    )


def test_wip_per_role_is_applied_at_claim(projet: Path, service: TaskService, mission: str) -> None:
    _wip(projet, total=10, role=2)
    ids = [_pret(service, mission, f"t{i}", owner="amelia").id for i in range(3)]
    service.claim(ids[0], "amelia")
    service.claim(ids[1], "amelia")
    with pytest.raises(GrimoireMissionError, match="WIP du rôle amelia atteint"):
        service.claim(ids[2], "amelia")
    assert service.require(ids[2]).status is TaskState.READY
    other = _pret(service, mission, "autre rôle", owner="marcus")
    assert service.claim(other.id, "marcus").task.status is TaskState.CLAIMED


def test_global_wip_is_applied_at_claim_and_a_closed_slot_is_free_again(projet: Path, service: TaskService, mission: str) -> None:
    _wip(projet, total=2, role=2)
    ids = [_pret(service, mission, f"t{i}", owner=f"role{i}").id for i in range(3)]
    service.claim(ids[0], "a")
    service.claim(ids[1], "b")
    with pytest.raises(GrimoireMissionError, match=r"WIP global atteint \(2/2\)"):
        service.claim(ids[2], "c")
    service.ledger.transition_task(ids[0], TaskState.RUNNING)
    service.ledger.transition_task(ids[0], TaskState.NEEDS_VERIFICATION)
    assert service.claim(ids[2], "c").task.status is TaskState.CLAIMED


def test_capacity_yaml_wip_overrides_the_orchestration_policy(projet: Path, service: TaskService, mission: str) -> None:
    _wip(projet, total=1, role=1)
    _capacity(projet, "version: 1\nwip:\n  limit_global: 3\n  limit_per_role: 3\n")
    ids = [_pret(service, mission, f"t{i}").id for i in range(3)]
    for tid in ids:
        service.claim(tid, "amelia")


def test_unreadable_wip_limit_refuses_the_claim(projet: Path, service: TaskService, mission: str) -> None:
    (projet / STANDARD / "orchestration-policy.yaml").write_text("wip:\n  limit_global: beaucoup\n", encoding="utf-8")
    task = _pret(service, mission, "t")
    with pytest.raises(GrimoireMissionError, match="limite de WIP illisible"):
        service.claim(task.id, "amelia")


def test_no_declared_limit_refuses_nothing(service: TaskService, mission: str) -> None:
    ids = [_pret(service, mission, f"t{i}").id for i in range(4)]
    for tid in ids:
        service.claim(tid, "amelia")


def test_wip_is_checked_under_the_ledger_lock_for_a_second_service(projet: Path, service: TaskService, mission: str) -> None:
    _wip(projet, total=1, role=1)
    a, b = _pret(service, mission, "a", owner="x"), _pret(service, mission, "b", owner="y")
    other = TaskService(projet)
    service.claim(a.id, "x")
    with pytest.raises(GrimoireMissionError, match="WIP"):
        other.claim(b.id, "y")


# ── Gardes de configuration ────────────────────────────────────────────────────


def test_a_malformed_capacity_file_refuses_every_guard_it_covers(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\nquota: [pas, une, table\n")
    capacity = load_capacity(projet)
    assert "*" in capacity.errors
    task = service.require(_pret(service, mission, "t").id)
    decision = admit(task, None, capacity, derive_view(service.ledger, capacity, now=_later(0)))
    assert isinstance(decision, Defer) and decision.reason == "unknown"


def test_an_unknown_key_or_version_is_refused(projet: Path) -> None:
    _capacity(projet, "version: 1\nforfait: 3\n")
    assert "clé inconnue forfait" in load_capacity(projet).errors["*"]
    _capacity(projet, "version: 9\n")
    assert "version 9" in load_capacity(projet).errors["*"]


def test_pause_blocks_admission(service: TaskService, mission: str) -> None:
    _pret(service, mission, "t")
    plan = service.queue_plan(routes=False, paused=True)
    assert not plan.admitted and plan.deferred[0].reason == "pause"


def test_a_router_defer_is_carried_with_its_reopening_date(projet: Path, service: TaskService, mission: str) -> None:
    task = service.require(_pret(service, mission, "t").id)
    capacity = load_capacity(projet)
    view = derive_view(service.ledger, capacity, now=_later(0))
    decision = admit(task, RouteRef("defer", defer_until="2026-10-19T03:00:00+00:00"), capacity, view)
    assert isinstance(decision, Defer) and decision.reason == "provider" and decision.retry_at.startswith("2026-10-19")


def test_plan_is_pure_and_writes_nothing(service: TaskService, mission: str) -> None:
    _pret(service, mission, "t")
    before = [e.id for e in service.ledger.list_events()]
    build_plan(service.ledger, load_capacity(service.project_root), now=_later(0))
    service.queue_plan(routes=True)
    assert [e.id for e in service.ledger.list_events()] == before


# ── CLI ────────────────────────────────────────────────────────────────────────


@pytest.mark.covers("cli:queue plan")
def test_queue_plan_cli_json_and_famine_exit(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\npools:\n  ci:\n    runners: 1\n")
    _pret(service, mission, "a", owner="x")
    _pret(service, mission, "b", owner="y")
    service.ledger.append_event(CI_PUSHED_EVENT, service.list_ready()[0].id, "task", "tick", {})
    result = runner.invoke(app, ["queue", "plan", "--project-root", str(projet), "--no-routes", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert {d["reason"] for d in data["deferred"]} == {"ci"}
    assert data["occupancy"]["ci"] == {"busy": 1, "runners": 1}
    text = runner.invoke(app, ["queue", "plan", "--project-root", str(projet), "--no-routes"])
    assert text.exit_code == 0 and "Defer(ci)" in text.output


@pytest.mark.covers("cli:queue plan")
def test_queue_plan_cli_exits_one_on_unreadable_capacity(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\nquota:\n  tokens_per_day: 0\n")
    _pret(service, mission, "t")
    result = runner.invoke(app, ["queue", "plan", "--project-root", str(projet), "--no-routes"])
    assert result.exit_code == 1 and "capacité illisible" in result.output


@pytest.mark.covers("cli:queue release")
def test_queue_release_needs_a_human_origin(projet: Path, service: TaskService, mission: str, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _pret(service, mission, "t")
    monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    refused = runner.invoke(app, ["queue", "release", task.id, "--project-root", str(projet)])
    assert refused.exit_code == 1 and "humain" in refused.output
    assert not [e for e in service.ledger.list_events(task.id) if e.event_type == CAPACITY_RELEASED_EVENT]
    monkeypatch.delenv("GRIMOIRE_UNATTENDED")
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "human")
    done = runner.invoke(app, ["queue", "release", task.id, "--project-root", str(projet), "--reason", "PR fusionnée"])
    assert done.exit_code == 0, done.output
    assert [e for e in TaskService(projet).ledger.list_events(task.id) if e.event_type == CAPACITY_RELEASED_EVENT]


def test_task_list_ready_mcp_returns_the_queue_order(projet: Path, service: TaskService, mission: str) -> None:
    from grimoire.mcp.server import task_list_ready

    low = _pret(service, mission, "basse", priority="low")
    high = _pret(service, mission, "haute", priority="critical")
    out = json.loads(task_list_ready(project_path=str(projet)))
    assert [t["id"] for t in out["tasks"]] == [high.id, low.id]


# ── Sections de pools illisibles, créneau CI d'une tâche garée, WIP à la reprise ──


@pytest.mark.parametrize(
    "pools",
    ["  ci:\n    runners: 1\n  gpu: {}\n", "  ci: {runners: 1}\n  Ci: {runners: 1}\n"],
    ids=["clef-inconnue", "casse-fautive"],
)
def test_an_unreadable_pools_table_defers_the_pool_guards_it_carries(
    projet: Path, service: TaskService, mission: str, pools: str
) -> None:
    _capacity(projet, f"version: 1\npools:\n{pools}")
    capacity = load_capacity(projet)
    assert "pools" in capacity.errors
    for _ in range(3):
        _pret(service, mission, "PR")
    plan = service.queue_plan(probe_host=False, routes=False)
    assert not plan.admitted
    assert {d.label for d in plan.deferred} == {"Defer(unknown)"}
    assert all("pool ci illisible" in d.detail for d in plan.deferred)


def test_a_pools_typo_next_to_a_declared_host_defers_instead_of_dropping_the_host(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "version: 1\npools:\n  host:\n    min_mem_available_mb: 99999999\n  Ci: {runners: 1}\n")
    capacity = load_capacity(projet)
    task = _pret(service, mission, "analyse", type=TaskType.ANALYSIS)
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC), host=None)
    decision = admit(service.require(task.id), None, capacity, view)
    assert isinstance(decision, Defer) and decision.reason == "unknown"
    assert "pool host illisible" in decision.detail


def test_a_pools_typo_does_not_refuse_the_other_sections(projet: Path) -> None:
    _capacity(projet, "version: 1\npools:\n  gpu: {}\nwip: {limit_global: 2}\n")
    capacity = load_capacity(projet)
    assert capacity.error_for("wip") == "" and capacity.error_for("quota") == ""
    assert capacity.error_for("llm") != ""


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


@pytest.mark.parametrize("how", ["needs_verification", "waiting"])
def test_a_parked_task_keeps_its_ci_slot_until_the_checks_end_or_a_human_releases_it(
    projet: Path, service: TaskService, mission: str, how: str
) -> None:
    _capacity(projet, "version: 1\npools:\n  ci:\n    runners: 1\n")
    first, second = _pret(service, mission, "A"), _pret(service, mission, "B")
    _park(service, first.id, how)
    assert service.require(first.id).status.value == how
    capacity = load_capacity(projet)
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    assert view.ci_occupants == frozenset({first.id})
    decision = admit(service.require(second.id), None, capacity, view)
    assert isinstance(decision, Defer) and decision.reason == "ci"
    service.ledger.append_event(CI_FINISHED_EVENT, first.id, "task", "tick", {})
    freed = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    assert not freed.ci_occupants
    assert isinstance(admit(service.require(second.id), None, capacity, freed), Admit)


def test_a_closed_task_gives_its_ci_slot_back(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\npools:\n  ci:\n    runners: 1\n")
    first = _pret(service, mission, "A")
    _park(service, first.id, "needs_verification")
    service.ledger.transition_task(first.id, TaskState.CANCELLED, actor_id="agent")
    view = derive_view(service.ledger, load_capacity(projet), now=datetime.now(UTC))
    assert not view.ci_occupants


def _wait(service: TaskService, task_id: str) -> int:
    _park(service, task_id, "waiting")
    return service.require(task_id).claim.lease_epoch  # type: ignore[union-attr]


def test_a_simple_resume_cannot_exceed_the_global_wip(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\nwip:\n  limit_global: 1\n")
    first, second = _pret(service, mission, "A", owner="x"), _pret(service, mission, "B", owner="y")
    epoch = _wait(service, first.id)
    service.ledger.claim_task(second.id, "agent2", "claude")
    with pytest.raises(GrimoireMissionError, match=r"Reprise refusée.*WIP global atteint \(1/1\).*attend une place"):
        service.ledger.transition_task(first.id, TaskState.RUNNING, actor_id="agent", lease_epoch=epoch)
    assert service.require(first.id).status is TaskState.WAITING
    service.ledger.transition_task(second.id, TaskState.RUNNING, actor_id="agent2")
    service.ledger.transition_task(second.id, TaskState.NEEDS_VERIFICATION, actor_id="agent2")
    resumed = service.ledger.transition_task(first.id, TaskState.RUNNING, actor_id="agent", lease_epoch=epoch)
    assert resumed.status is TaskState.RUNNING


def test_a_resume_cannot_exceed_the_wip_of_its_role(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\nwip:\n  limit_per_role: 1\n")
    first, second = _pret(service, mission, "A", owner="amelia"), _pret(service, mission, "B", owner="amelia")
    epoch = _wait(service, first.id)
    service.ledger.claim_task(second.id, "agent2", "claude")
    with pytest.raises(GrimoireMissionError, match=r"Reprise refusée.*WIP du rôle amelia atteint"):
        service.ledger.transition_task(first.id, TaskState.RUNNING, actor_id="agent", lease_epoch=epoch)
    assert service.require(first.id).status is TaskState.WAITING


def test_an_attested_resume_cannot_exceed_the_wip(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\nwip:\n  limit_global: 1\n")
    first, second = _pret(service, mission, "A", owner="x"), _pret(service, mission, "B", owner="y")
    _wait(service, first.id)
    service.ledger.claim_task(second.id, "agent2", "claude")
    with pytest.raises(GrimoireMissionError, match=r"Reprise refusée.*attend une place"):
        service.ledger.resume_attested(first.id, actor_id="agent", reason="checkpoint décidé")
    assert service.require(first.id).status is TaskState.WAITING


def test_a_resume_with_room_is_still_granted(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "version: 1\nwip:\n  limit_global: 1\n")
    first = _pret(service, mission, "A")
    epoch = _wait(service, first.id)
    resumed = service.ledger.transition_task(first.id, TaskState.RUNNING, actor_id="agent", lease_epoch=epoch)
    assert resumed.status is TaskState.RUNNING


# ── Le retour de needs_verification vers running repasse par la garde du WIP ─────


def _verify(service: TaskService, task_id: str, actor: str) -> None:
    service.ledger.claim_task(task_id, actor, "claude")
    service.ledger.transition_task(task_id, TaskState.RUNNING, actor_id=actor)
    service.ledger.transition_task(task_id, TaskState.NEEDS_VERIFICATION, actor_id=actor)


def test_a_rework_from_needs_verification_cannot_exceed_the_global_wip(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "version: 1\nwip:\n  limit_global: 1\n")
    first, second = _pret(service, mission, "A", owner="x"), _pret(service, mission, "B", owner="y")
    _verify(service, first.id, "agent")
    service.ledger.claim_task(second.id, "agent2", "claude")
    service.ledger.transition_task(second.id, TaskState.RUNNING, actor_id="agent2")
    with pytest.raises(GrimoireMissionError, match=r"Reprise refusée.*WIP global atteint \(1/1\).*reste needs_verification"):
        service.ledger.transition_task(first.id, TaskState.RUNNING, actor_id="agent")
    assert service.require(first.id).status is TaskState.NEEDS_VERIFICATION
    held = [t.id for t in service.ledger.list_tasks() if t.status in (TaskState.CLAIMED, TaskState.RUNNING)]
    assert held == [second.id]
    service.ledger.transition_task(second.id, TaskState.NEEDS_VERIFICATION, actor_id="agent2")
    assert service.ledger.transition_task(first.id, TaskState.RUNNING, actor_id="agent").status is TaskState.RUNNING


def test_a_rework_from_needs_verification_cannot_exceed_the_wip_of_its_role_through_the_service(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "version: 1\nwip:\n  limit_per_role: 1\n")
    first, second = _pret(service, mission, "A"), _pret(service, mission, "B")
    service.claim(first.id, "agent")
    service.transition(first.id, TaskState.RUNNING, "agent")
    service.transition(first.id, TaskState.NEEDS_VERIFICATION, "agent")
    service.claim(second.id, "agent2")
    service.transition(second.id, TaskState.RUNNING, "agent2")
    with pytest.raises(GrimoireMissionError, match=r"Reprise refusée.*WIP du rôle amelia atteint"):
        service.transition(first.id, TaskState.RUNNING, "verifier", "preuve rejetée, reprise")
    assert service.require(first.id).status is TaskState.NEEDS_VERIFICATION


def test_a_rework_from_needs_verification_with_room_is_still_granted(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "version: 1\nwip:\n  limit_global: 1\n")
    first = _pret(service, mission, "A")
    _verify(service, first.id, "agent")
    assert service.ledger.transition_task(first.id, TaskState.RUNNING, actor_id="agent").status is TaskState.RUNNING


def test_task_flow_import_defers_a_rework_the_wip_refuses_instead_of_raising(projet: Path) -> None:
    from grimoire.missions.task_flow_adapter import _task_id, import_task_flow_events

    _capacity(projet, "version: 1\nwip:\n  limit_global: 1\n")
    ledger = TaskService(projet).ledger
    events = projet / "events.jsonl"
    _lines = [
        {"task": "a", "event": "task-start", "flow": "f"},
        {"task": "a", "event": "task-end", "flow": "f", "status": "ok", "exitCode": 0},
        {"task": "b", "event": "task-start", "flow": "f"},
    ]
    events.write_text("".join(json.dumps(e) + "\n" for e in _lines), encoding="utf-8")
    import_task_flow_events(ledger, events)
    assert ledger.get_task(_task_id("a")).status is TaskState.NEEDS_VERIFICATION  # type: ignore[union-attr]
    assert ledger.get_task(_task_id("b")).status is TaskState.RUNNING  # type: ignore[union-attr]
    with events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"task": "a", "event": "task-start", "flow": "f", "n": 2}) + "\n")
    report = import_task_flow_events(ledger, events)  # levait GrimoireMissionError sans le try
    assert report.events_deferred == 1 and report.events_imported == 0
    assert ledger.get_task(_task_id("a")).status is TaskState.NEEDS_VERIFICATION  # type: ignore[union-attr]
    ledger.transition_task(_task_id("b"), TaskState.NEEDS_VERIFICATION, actor_id="task-flow")
    retry = import_task_flow_events(ledger, events)  # rejoué quand une place se libère
    assert retry.events_deferred == 0 and retry.events_imported == 1
    assert ledger.get_task(_task_id("a")).status is TaskState.RUNNING  # type: ignore[union-attr]
