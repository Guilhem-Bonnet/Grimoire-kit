"""Les règles d'usage activables de ``capacity.yaml`` : appliquées à l'admission, jamais à une session en cours.

Une règle invalide est signalée et ignorée ; le fichier illisible reste fermé. ``pause`` pose la pause ``drain`` existante
sans jamais en écraser une, ``hold`` diffère le lancement, ``warn`` parle dans la liste d'attention.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.admission import QUOTA_CONSUMED_EVENT, Defer, build_plan, load_capacity
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.pause import (
    CONTROL_HOME_ENV,
    DRAIN,
    PauseState,
    clear_pause,
    project_pause_path,
    read_flag,
    read_pause,
    write_pause,
)
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.usage_rules import (
    HOLD_EVENT,
    PAUSE_ORIGIN,
    apply_effects,
    consumption_today,
    evaluate,
    lift_day_pauses,
    parse_rules,
    rules_report,
)

ACC = "la règle est appliquée"
STANDARD = Path("_grimoire/standard")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_HOME_ENV, str(tmp_path / "control-home"))


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / STANDARD).mkdir(parents=True)
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


def _rules(projet: Path, body: str) -> None:
    (projet / STANDARD / "capacity.yaml").write_text(f"version: 1\nusage_rules:\n{body}", encoding="utf-8")


def _ready(service: TaskService, titre: str = "t") -> str:
    mission = next(iter(service.ledger.list_missions()), None)
    mission_id = mission.id if mission else service.ledger.create_mission(title="Règles", origin="test").id
    task = service.ledger.create_task(mission_id, titre, acceptance=(ACC,), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _spent(service: TaskService, tokens: int, *, task: str = "", cost: float | None = 0.5, session: str = "s1") -> None:
    service.ledger.append_event(
        QUOTA_CONSUMED_EVENT, task or "project", "task", "tick",
        {"session": session, "task_id": task, "tokens": tokens, "cost_usd": cost, "status": "lower_bound"},
    )


def _launch(service: TaskService, tid: str, session: str) -> None:
    service.ledger.append_event("task.session_launched", tid, "task", "worker", {"session_id": session})


def _plan(service: TaskService, now: datetime | None = None) -> Any:
    capacity = load_capacity(service.home_root)
    return build_plan(service.ledger, capacity, now=now or datetime.now(UTC), pause=PauseState())


# ── Le fichier ─────────────────────────────────────────────────────────────────


def test_valid_rules_are_read_and_an_empty_file_has_none(projet: Path) -> None:
    assert load_capacity(projet).usage_rules == ()
    _rules(
        projet,
        "  - {id: a, scope: day, metric: tokens, threshold: 1000, action: warn}\n"
        "  - {id: b, scope: task, metric: usd, threshold: 2.5, action: hold, enabled: false}\n",
    )
    capacity = load_capacity(projet)
    assert [(r.id, r.scope, r.metric, r.threshold, r.action, r.enabled) for r in capacity.usage_rules] == [
        ("a", "day", "tokens", 1000.0, "warn", True), ("b", "task", "usd", 2.5, "hold", False),
    ]
    assert not capacity.errors and not capacity.rule_issues


@pytest.mark.parametrize(
    "entry",
    [
        "not-a-table",
        {"id": "a", "scope": "day", "metric": "tokens", "threshold": 1, "action": "warn", "oops": 1},
        {"id": "bad id", "scope": "day", "metric": "tokens", "threshold": 1, "action": "warn"},
        {"id": "a", "scope": "session", "metric": "tokens", "threshold": 1, "action": "warn"},
        {"id": "a", "scope": "day", "metric": "model", "threshold": 1, "action": "warn"},
        {"id": "a", "scope": "day", "metric": "tokens", "threshold": 1, "action": "ask_human"},
        {"id": "a", "scope": "day", "metric": "tokens", "threshold": 1, "action": "cheaper_model"},
        {"id": "a", "scope": "day", "metric": "tokens", "threshold": 0, "action": "warn"},
        {"id": "a", "scope": "day", "metric": "tokens", "threshold": True, "action": "warn"},
        {"id": "a", "scope": "day", "metric": "tokens", "threshold": 1.5, "action": "warn"},
        {"id": "a", "scope": "day", "metric": "tokens", "action": "warn"},
        {"id": "a", "scope": "day", "metric": "tokens", "threshold": 1, "action": "warn", "enabled": "oui"},
    ],
)
def test_an_invalid_rule_is_reported_and_ignored_while_the_others_hold(entry: object) -> None:
    good = {"id": "ok", "scope": "project", "metric": "sessions", "threshold": 3, "action": "warn"}
    rules, issues = parse_rules([entry, good])
    assert [r.id for r in rules] == ["ok"] and len(issues) == 1 and issues[0].startswith("usage_rules[1]")


def test_duplicate_ids_and_a_non_list_section_are_ignored_with_a_reason() -> None:
    rule = {"id": "x", "scope": "day", "metric": "tokens", "threshold": 1, "action": "warn"}
    rules, issues = parse_rules([rule, rule])
    assert len(rules) == 1 and "déjà pris" in issues[0]
    assert parse_rules({"id": "x"}) == ((), ("usage_rules doit être une liste de règles, pas {'id': 'x'} : toutes ignorées",))
    assert parse_rules(None) == ((), ())


def test_an_invalid_rule_never_breaks_the_capacity_but_an_unreadable_file_stays_closed(projet: Path) -> None:
    _rules(projet, "  - {id: a, scope: nope}\n")
    capacity = load_capacity(projet)
    assert capacity.usage_rules == () and len(capacity.rule_issues) == 1 and not capacity.errors
    (projet / STANDARD / "capacity.yaml").write_text("version: 1\nusage_rules: [pas, ferme\n", encoding="utf-8")
    broken = load_capacity(projet)
    assert "*" in broken.errors and broken.usage_rules == ()


# ── La mesure ──────────────────────────────────────────────────────────────────


def test_metrics_by_scope_and_an_unknown_cost_is_never_zero(service: TaskService) -> None:
    one, two = _ready(service, "un"), _ready(service, "deux")
    _spent(service, 1000, task=one, cost=0.5, session="a")
    _spent(service, 500, task=two, cost=None, session="b")
    _launch(service, one, "a")
    _launch(service, two, "b")
    old = service.ledger.list_events()[0]
    assert old  # le ledger a bien des événements
    rules, _ = parse_rules([
        {"id": "t", "scope": "day", "metric": "tokens", "threshold": 1500, "action": "warn"},
        {"id": "u", "scope": "day", "metric": "usd", "threshold": 0.5, "action": "warn"},
        {"id": "s", "scope": "project", "metric": "sessions", "threshold": 2, "action": "warn"},
        {"id": "k", "scope": "task", "metric": "tokens", "threshold": 900, "action": "warn"},
        {"id": "off", "scope": "day", "metric": "tokens", "threshold": 1, "action": "warn", "enabled": False},
    ])
    by = {(s.rule.id, s.scope_key): s for s in evaluate(rules, service.ledger.list_events(), datetime.now(UTC))}
    assert by["t", ""].value == 1500 and by["t", ""].crossed and by["t", ""].complete
    assert by["u", ""].value == 0.5 and by["u", ""].crossed and not by["u", ""].complete  # borne basse
    assert by["s", ""].value == 2 and by["s", ""].crossed
    assert by["k", one].crossed and not by["k", two].crossed and by["k", two].state == "sous le seuil"
    assert by["off", ""].state == "désactivée" and not by["off", ""].crossed
    today = consumption_today(service.ledger.list_events(), datetime.now(UTC))
    assert today["tokens"] == 1500 and today["usd"] == 0.5 and today["usd_complete"] is False and today["sessions"] == 2


def test_the_day_scope_forgets_yesterday_and_an_unknown_session_makes_the_reading_partial(service: TaskService) -> None:
    _spent(service, 5000)
    service.ledger.append_event(
        QUOTA_CONSUMED_EVENT, "x", "task", "tick", {"session": "z", "status": "unknown", "reason": "journal introuvable", "tokens": None}
    )
    rules, _ = parse_rules([{"id": "d", "scope": "day", "metric": "tokens", "threshold": 99999, "action": "warn"}])
    (now,) = evaluate(rules, service.ledger.list_events(), datetime.now(UTC))
    assert not now.crossed and not now.complete and "partiellement inconnue" in now.state
    (tomorrow,) = evaluate(rules, service.ledger.list_events(), datetime.now(UTC) + timedelta(days=2))
    assert tomorrow.value == 0


# ── À l'admission ──────────────────────────────────────────────────────────────


def test_hold_defers_launches_until_the_day_changes_and_warn_defers_nothing(projet: Path, service: TaskService) -> None:
    tid = _ready(service)
    _spent(service, 2000)
    _rules(
        projet,
        "  - {id: jour, scope: day, metric: tokens, threshold: 1500, action: hold}\n"
        "  - {id: avis, scope: day, metric: tokens, threshold: 1, action: warn}\n",
    )
    plan = _plan(service)
    (deferred,) = plan.deferred
    assert isinstance(deferred, Defer) and deferred.reason == "usage_rule" and "jour" in deferred.detail
    assert deferred.retry_at.endswith("T00:00:00+00:00") and not plan.admitted
    assert plan.to_dict()["usage_rules"][0]["state"] == "franchie"
    tomorrow = _plan(service, datetime.now(UTC) + timedelta(days=2))
    assert [a.task_id for a in tomorrow.admitted] == [tid]  # le lendemain, la règle de jour ne retient plus rien


def test_without_rules_the_plan_output_is_unchanged(projet: Path, service: TaskService) -> None:
    _ready(service)
    assert "usage_rules" not in _plan(service).to_dict()


def test_a_task_rule_holds_only_its_own_task(projet: Path, service: TaskService) -> None:
    costly, cheap = _ready(service, "chère"), _ready(service, "légère")
    _spent(service, 5000, task=costly, session="a")
    _spent(service, 10, task=cheap, session="b")
    _rules(projet, "  - {id: tache, scope: task, metric: tokens, threshold: 1000, action: pause}\n")
    plan = _plan(service)
    assert [a.task_id for a in plan.admitted] == [cheap]
    assert [d.task_id for d in plan.deferred] == [costly] and plan.deferred[0].retry_at == "unknown"


# ── Les effets ─────────────────────────────────────────────────────────────────


def test_pause_is_posed_drain_with_the_rule_as_reason_and_never_overwrites(projet: Path, service: TaskService) -> None:
    _spent(service, 9000)
    _rules(projet, "  - {id: gros, scope: project, metric: tokens, threshold: 100, action: pause}\n")
    plan = _plan(service)
    notes: list[str] = []
    now = datetime.now(UTC)
    apply_effects(service, plan.rules, apply=False, now=now, attention=notes)
    assert read_pause(projet).flags == () and "serait posée" in notes[0]  # en plan : rien n'est écrit
    notes.clear()
    apply_effects(service, plan.rules, apply=True, now=now, attention=notes)
    flag = read_flag(project_pause_path(projet), "project")
    assert flag is not None and flag.mode == DRAIN and not flag.tasks
    assert "gros" in flag.reason and flag.actor == "usage-rule:project:gros" and flag.origin == PAUSE_ORIGIN
    assert "orchestrate resume" in notes[0] and "geste humain" in notes[0]
    # Une pause déjà posée (ici la sienne, puis celle d'un humain) n'est jamais réécrite.
    notes.clear()
    write_pause(projet, mode="hold", actor="guilhem", reason="je tiens la main", origin="human")
    apply_effects(service, plan.rules, apply=True, now=now, attention=notes)
    kept = read_flag(project_pause_path(projet), "project")
    assert kept is not None and kept.mode == "hold" and kept.reason == "je tiens la main"
    assert "déjà posée" in notes[0]


def test_a_task_pause_covers_only_that_task(projet: Path, service: TaskService) -> None:
    costly = _ready(service)
    _spent(service, 9000, task=costly)
    _rules(projet, "  - {id: tache, scope: task, metric: tokens, threshold: 100, action: pause}\n")
    plan = _plan(service)
    apply_effects(service, plan.rules, apply=True, now=datetime.now(UTC), attention=[], task_ids=[costly])
    flag = read_flag(project_pause_path(projet), "project")
    assert flag is not None and flag.tasks == (costly,) and flag.actor == "usage-rule:task:tache"


def test_hold_journals_once_per_rule_task_and_day_and_says_how_to_lift(projet: Path, service: TaskService) -> None:
    _spent(service, 9000)
    _rules(projet, "  - {id: gel, scope: project, metric: tokens, threshold: 100, action: hold}\n")
    plan = _plan(service)
    notes: list[str] = []
    for _ in range(2):
        apply_effects(service, plan.rules, apply=True, now=datetime.now(UTC), attention=notes)
    events = [e for e in service.ledger.list_events() if e.event_type == HOLD_EVENT]
    assert len(events) == 1 and events[0].payload["rule"] == "gel"
    assert "enabled: false" in notes[0] and "capacity.yaml" in notes[0] and "aucun agent" in notes[0]


def test_a_task_rule_stays_silent_for_a_task_that_is_not_in_the_queue(projet: Path, service: TaskService) -> None:
    _spent(service, 9000, task="GAO-autre-001")
    _rules(projet, "  - {id: tache, scope: task, metric: tokens, threshold: 100, action: warn}\n")
    notes: list[str] = []
    apply_effects(service, _plan(service).rules, apply=True, now=datetime.now(UTC), attention=notes, task_ids=["GAO-x-001"])
    assert notes == []


def test_a_day_pause_lifts_itself_on_the_next_day_but_no_other_pause_does(projet: Path) -> None:
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    write_pause(projet, mode=DRAIN, actor="usage-rule:day:jour", reason="règle jour", origin=PAUSE_ORIGIN, now=yesterday)
    assert not lift_day_pauses(projet, yesterday)  # le même jour : elle tient
    assert lift_day_pauses(projet, now) and read_pause(projet).flags == ()
    for actor, origin in (
        ("usage-rule:project:gros", PAUSE_ORIGIN), ("usage-rule:task:t", PAUSE_ORIGIN), ("guilhem", "human"),
        ("usage-rule:day:jour", "human"),  # l'acteur imite une règle, l'origine dit qu'un humain l'a posée
        ("usage-rule:day:jour", "agent"), ("usage-rule:day:jour", ""),
    ):
        write_pause(projet, mode=DRAIN, actor=actor, reason="x", origin=origin, now=yesterday)
        assert not lift_day_pauses(projet, now)
        assert read_pause(projet).flags
        clear_pause(projet)


def test_a_day_pause_that_cannot_be_lifted_is_reported_not_fatal(projet: Path, service: TaskService, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import usage_rules

    def boom(*_a: object, **_k: object) -> bool:
        raise OSError("disque en lecture seule")

    monkeypatch.setattr(usage_rules, "lift_day_pauses", boom)
    report = run_tick(projet)
    assert report.status in ("idle", "ran") and any("levée en erreur" in line for line in report.attention)
    write_pause(projet, mode=DRAIN, actor="guilhem", reason="je tiens", origin="human")
    paused = run_tick(projet)
    assert paused.status == "paused" and any("levée en erreur" in line for line in paused.attention)


@pytest.mark.parametrize("bad", [".nan", ".inf", "-.inf", "-3", "0", "-0.5"])
def test_a_non_finite_negative_or_null_threshold_is_an_invalid_rule(projet: Path, bad: str) -> None:
    _rules(projet, f"  - {{id: a, scope: day, metric: usd, threshold: {bad}, action: warn}}\n")
    capacity = load_capacity(projet)
    assert capacity.usage_rules == () and len(capacity.rule_issues) == 1 and not capacity.errors
    assert "fini strictement positif" in capacity.rule_issues[0]
    rules, issues = parse_rules([{"id": "a", "scope": "day", "metric": "usd", "threshold": float("nan"), "action": "warn"}])
    assert rules == () and len(issues) == 1


# ── Dans le tick et en lecture ─────────────────────────────────────────────────


def test_the_tick_posed_the_pause_says_so_and_launches_nothing(projet: Path, service: TaskService) -> None:
    _ready(service)
    _spent(service, 9000)
    _rules(
        projet,
        "  - {id: gros, scope: day, metric: tokens, threshold: 100, action: pause}\n  - {id: casse, scope: nope}\n",
    )
    report = run_tick(projet)
    assert report.plan is not None and not report.plan.admitted and report.plan.deferred[0].reason == "usage_rule"
    assert any("pause drain posée" in line for line in report.attention)
    assert any("capacity.yaml : usage_rules[2]" in line for line in report.attention)
    assert read_pause(projet).flags
    assert run_tick(projet).status == "paused"  # le tick suivant s'arrête sur le drapeau


def test_the_tick_plan_mode_writes_nothing(projet: Path, service: TaskService) -> None:
    _ready(service)
    _spent(service, 9000)
    _rules(projet, "  - {id: gros, scope: day, metric: tokens, threshold: 100, action: pause}\n")
    report = run_tick(projet, apply=False)
    assert any("serait posée" in line for line in report.attention) and read_pause(projet).flags == ()


def test_an_effect_error_does_not_break_the_tick(projet: Path, service: TaskService, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import usage_rules

    _ready(service)
    _spent(service, 9000)
    _rules(projet, "  - {id: avis, scope: day, metric: tokens, threshold: 1, action: warn}\n")

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("panne")

    monkeypatch.setattr(usage_rules, "apply_effects", boom)
    report = run_tick(projet)
    assert report.status == "ran" and any("règles d'usage : étape en erreur" in line for line in report.attention)


def test_queue_rules_shows_each_rule_the_consumption_and_the_ignored_ones(projet: Path, service: TaskService) -> None:
    _spent(service, 1234, cost=None)
    _rules(
        projet,
        "  - {id: jour, scope: day, metric: tokens, threshold: 1000, action: warn}\n  - {id: casse, scope: day}\n",
    )
    result = runner.invoke(app, ["queue", "rules", "--json", "--project-root", str(projet)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["consumption_today"]["tokens"] == 1234 and data["consumption_today"]["usd_complete"] is False
    assert data["rules"][0]["id"] == "jour" and data["rules"][0]["state"] == "franchie"
    assert len(data["issues"]) == 1 and data["quota"]["on_unknown"] == "refuse"
    text = runner.invoke(app, ["queue", "rules", "--project-root", str(projet)])
    assert text.exit_code == 0 and "jour" in text.output and "franchie" in text.output and "ignorée" in text.output
    empty = runner.invoke(app, ["queue", "rules", "--project-root", str(projet / "..")])  # sans ledger ni règle
    assert empty.exit_code == 0


def test_queue_rules_exits_1_on_an_unreadable_capacity_file(projet: Path) -> None:
    (projet / STANDARD / "capacity.yaml").write_text("version: 1\nusage_rules: [pas\n", encoding="utf-8")
    result = runner.invoke(app, ["queue", "rules", "--project-root", str(projet)])
    assert result.exit_code == 1 and "illisible" in result.output


def test_the_cockpit_capacity_sheet_shows_consumption_and_rules_read_only(projet: Path, service: TaskService) -> None:
    from grimoire.tools.workspace_capacity import REASON_LABELS, capacity_view

    _ready(service)
    _spent(service, 1234)
    _rules(projet, "  - {id: jour, scope: day, metric: tokens, threshold: 1000, action: hold}\n")
    view = capacity_view(projet, probe=lambda: None)
    assert view["usage"]["consumption_today"]["tokens"] == 1234
    assert [(r["id"], r["state"]) for r in view["usage"]["rules"]] == [("jour", "franchie")]
    assert view["file"][0]["raison"] == "usage_rule" and view["file"][0]["raison_libelle"] == REASON_LABELS["usage_rule"]
    bare = tmp_project = projet.parent / "sans-ledger"
    (bare / STANDARD).mkdir(parents=True)
    assert capacity_view(tmp_project, probe=lambda: None)["usage"]["rules"] == []


def test_rules_report_is_plain_data(service: TaskService) -> None:
    rules, issues = parse_rules([{"id": "a", "scope": "day", "metric": "sessions", "threshold": 1, "action": "warn"}])
    report = rules_report(rules, issues, service.ledger.list_events(), datetime.now(UTC))
    assert report["rules"][0]["state"] == "sous le seuil" and report["issues"] == []
