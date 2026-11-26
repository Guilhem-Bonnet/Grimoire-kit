"""Les règles d'usage de ``capacity.yaml`` : lecture, mesure, effets (module autonome, lecture seule pour les agents).

Une règle invalide est signalée et ignorée. ``pause`` pose la pause ``drain`` existante sans jamais en écraser une,
``hold`` retient un lancement, ``warn`` parle dans la liste d'attention. Le branchement à l'admission et au tick vient
avec la partie réservée : ici, le module et ses lectures (``queue rules``, fiche Capacité).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.pause import (
    CONTROL_HOME_ENV,
    DRAIN,
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
    QUOTA_CONSUMED_EVENT,
    apply_effects,
    consumption_today,
    evaluate,
    lift_day_pauses,
    parse_rules,
    read_rules,
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


def _statuses(service: TaskService, projet: Path, now: datetime | None = None):  # type: ignore[no-untyped-def]
    rules, _issues = read_rules(projet)
    return evaluate(rules, service.ledger.list_events(), now or datetime.now(UTC))


# ── Le fichier ─────────────────────────────────────────────────────────────────


def test_valid_rules_are_read_and_an_empty_file_has_none(projet: Path) -> None:
    assert read_rules(projet) == ((), ())
    _rules(
        projet,
        "  - {id: a, scope: day, metric: tokens, threshold: 1000, action: warn}\n"
        "  - {id: b, scope: task, metric: usd, threshold: 2.5, action: hold, enabled: false}\n",
    )
    rules, issues = read_rules(projet)
    assert [(r.id, r.scope, r.metric, r.threshold, r.action, r.enabled) for r in rules] == [
        ("a", "day", "tokens", 1000.0, "warn", True), ("b", "task", "usd", 2.5, "hold", False),
    ]
    assert issues == ()


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


@pytest.mark.parametrize("bad", [".nan", ".inf", "-.inf", "-3", "0", "-0.5"])
def test_a_non_finite_negative_or_null_threshold_is_an_invalid_rule(projet: Path, bad: str) -> None:
    _rules(projet, f"  - {{id: a, scope: day, metric: usd, threshold: {bad}, action: warn}}\n")
    rules, issues = read_rules(projet)
    assert rules == () and len(issues) == 1 and "fini strictement positif" in issues[0]


def test_an_unreadable_file_reads_as_no_rule_leaving_the_refusal_to_the_admission(projet: Path) -> None:
    (projet / STANDARD / "capacity.yaml").write_text("version: 1\nusage_rules: [pas, ferme\n", encoding="utf-8")
    assert read_rules(projet) == ((), ())
    (projet / STANDARD / "capacity.yaml").write_text("- une\n- liste\n", encoding="utf-8")
    assert read_rules(projet) == ((), ())


# ── La mesure ──────────────────────────────────────────────────────────────────


def test_metrics_by_scope_and_an_unknown_cost_is_never_zero(projet: Path, service: TaskService) -> None:
    one, two = _ready(service, "un"), _ready(service, "deux")
    _spent(service, 1000, task=one, cost=0.5, session="a")
    _spent(service, 500, task=two, cost=None, session="b")
    _launch(service, one, "a")
    _launch(service, two, "b")
    _rules(
        projet,
        "  - {id: t, scope: day, metric: tokens, threshold: 1500, action: warn}\n"
        "  - {id: u, scope: day, metric: usd, threshold: 0.5, action: warn}\n"
        "  - {id: s, scope: project, metric: sessions, threshold: 2, action: warn}\n"
        "  - {id: k, scope: task, metric: tokens, threshold: 900, action: warn}\n"
        "  - {id: off, scope: day, metric: tokens, threshold: 1, action: warn, enabled: false}\n",
    )
    by = {(s.rule.id, s.scope_key): s for s in _statuses(service, projet)}
    assert by["t", ""].value == 1500 and by["t", ""].crossed and by["t", ""].complete
    assert by["u", ""].value == 0.5 and by["u", ""].crossed and not by["u", ""].complete  # borne basse
    assert by["s", ""].value == 2 and by["s", ""].crossed
    assert by["k", one].crossed and not by["k", two].crossed and by["k", two].state == "sous le seuil"
    assert by["off", ""].state == "désactivée" and not by["off", ""].crossed
    today = consumption_today(service.ledger.list_events(), datetime.now(UTC))
    assert today["tokens"] == 1500 and today["usd"] == 0.5 and today["usd_complete"] is False and today["sessions"] == 2


def test_a_task_rule_is_read_even_without_a_task(projet: Path, service: TaskService) -> None:
    _rules(projet, "  - {id: k, scope: task, metric: tokens, threshold: 9, action: warn}\n")
    (status,) = _statuses(service, projet)
    assert status.scope_key == "" and status.state == "sous le seuil"


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


def test_a_settled_unknown_clears_the_ongoing_one_before_it_whatever_the_day(service: TaskService) -> None:
    base = {"session": "z", "status": "unknown", "tokens": None, "task_id": ""}
    service.ledger.append_event(QUOTA_CONSUMED_EVENT, "x", "task", "tick", {**base, "reason": "trop gros", "ongoing": True})
    rules, _ = parse_rules([{"id": "d", "scope": "day", "metric": "tokens", "threshold": 9, "action": "warn"}])
    later = datetime.now(UTC) + timedelta(days=3)
    assert not evaluate(rules, service.ledger.list_events(), later)[0].complete  # en cours : pèse sans limite de durée
    service.ledger.append_event(QUOTA_CONSUMED_EVENT, "x", "task", "tick", {**base, "reason": "immobile", "ongoing": False})
    assert evaluate(rules, service.ledger.list_events(), later)[0].complete  # remplacé par son dernier état


# ── Les effets ─────────────────────────────────────────────────────────────────


def test_pause_is_posed_drain_with_the_rule_as_reason_and_never_overwrites(projet: Path, service: TaskService) -> None:
    _spent(service, 9000)
    _rules(projet, "  - {id: gros, scope: project, metric: tokens, threshold: 100, action: pause}\n")
    statuses = _statuses(service, projet)
    notes: list[str] = []
    now = datetime.now(UTC)
    apply_effects(service, statuses, apply=False, now=now, attention=notes)
    assert read_pause(projet).flags == () and "serait posée" in notes[0]  # en plan : rien n'est écrit
    notes.clear()
    apply_effects(service, statuses, apply=True, now=now, attention=notes)
    flag = read_flag(project_pause_path(projet), "project")
    assert flag is not None and flag.mode == DRAIN and not flag.tasks
    assert "gros" in flag.reason and flag.actor == "usage-rule:project:gros" and flag.origin == PAUSE_ORIGIN
    assert "orchestrate resume" in notes[0] and "geste humain" in notes[0]
    # Une pause déjà posée (ici celle d'un humain) n'est jamais réécrite.
    notes.clear()
    write_pause(projet, mode="hold", actor="guilhem", reason="je tiens la main", origin="human")
    apply_effects(service, statuses, apply=True, now=now, attention=notes)
    kept = read_flag(project_pause_path(projet), "project")
    assert kept is not None and kept.mode == "hold" and kept.reason == "je tiens la main"
    assert "déjà posée" in notes[0]


def test_a_task_pause_covers_only_that_task(projet: Path, service: TaskService) -> None:
    costly = _ready(service)
    _spent(service, 9000, task=costly)
    _rules(projet, "  - {id: tache, scope: task, metric: tokens, threshold: 100, action: pause}\n")
    apply_effects(service, _statuses(service, projet), apply=True, now=datetime.now(UTC), attention=[], task_ids=[costly])
    flag = read_flag(project_pause_path(projet), "project")
    assert flag is not None and flag.tasks == (costly,) and flag.actor == "usage-rule:task:tache"


def test_hold_journals_once_per_rule_task_and_day_and_says_how_to_lift(projet: Path, service: TaskService) -> None:
    _spent(service, 9000)
    _rules(projet, "  - {id: gel, scope: project, metric: tokens, threshold: 100, action: hold}\n")
    statuses = _statuses(service, projet)
    notes: list[str] = []
    for _ in range(2):
        apply_effects(service, statuses, apply=True, now=datetime.now(UTC), attention=notes)
    events = [e for e in service.ledger.list_events() if e.event_type == HOLD_EVENT]
    assert len(events) == 1 and events[0].payload["rule"] == "gel"
    assert "enabled: false" in notes[0] and "capacity.yaml" in notes[0] and "aucun agent" in notes[0]


def test_a_task_rule_stays_silent_for_a_task_that_is_not_in_the_queue(projet: Path, service: TaskService) -> None:
    _spent(service, 9000, task="GAO-autre-001")
    _rules(projet, "  - {id: tache, scope: task, metric: tokens, threshold: 100, action: warn}\n")
    notes: list[str] = []
    apply_effects(service, _statuses(service, projet), apply=True, now=datetime.now(UTC), attention=notes, task_ids=["GAO-x-001"])
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


# ── En lecture : `queue rules` et la fiche Capacité ────────────────────────────


def test_queue_rules_shows_each_rule_the_consumption_and_the_ignored_ones(projet: Path, service: TaskService) -> None:
    _spent(service, 1234, cost=None)
    _rules(
        projet,
        "  - {id: jour, scope: day, metric: tokens, threshold: 1000, action: warn}\n  - {id: casse, scope: day}\n",
    )
    result = runner.invoke(app, ["queue", "rules", "--json", "--project-root", str(projet)])
    assert result.exit_code in (0, 1), result.output  # 1 tant que l'admission rejette la clé usage_rules (échec fermé)
    data = json.loads(result.output)
    assert data["consumption_today"]["tokens"] == 1234 and data["consumption_today"]["usd_complete"] is False
    assert data["rules"][0]["id"] == "jour" and data["rules"][0]["state"] == "franchie"
    assert len(data["issues"]) == 1
    text = runner.invoke(app, ["queue", "rules", "--project-root", str(projet)])
    assert "jour" in text.output and "franchie" in text.output and "ignorée" in text.output
    assert "Sessions suivies" in text.output


def test_queue_rules_without_rules_is_a_plain_empty_report(projet: Path, service: TaskService) -> None:
    result = runner.invoke(app, ["queue", "rules", "--project-root", str(projet)])
    assert result.exit_code == 0 and "Aucune règle d'usage valide" in result.output


def test_the_cockpit_capacity_sheet_shows_consumption_and_rules_read_only(projet: Path, service: TaskService) -> None:
    from grimoire.tools.workspace_capacity import REASON_LABELS, capacity_view

    _ready(service)
    _spent(service, 1234)
    _rules(projet, "  - {id: jour, scope: day, metric: tokens, threshold: 1000, action: hold}\n")
    view = capacity_view(projet, probe=lambda: None)
    assert view["usage"]["consumption_today"]["tokens"] == 1234
    assert [(r["id"], r["state"]) for r in view["usage"]["rules"]] == [("jour", "franchie")]
    assert REASON_LABELS["usage_rule"]
    bare = projet.parent / "sans-ledger"
    (bare / STANDARD).mkdir(parents=True)
    assert capacity_view(bare, probe=lambda: None)["usage"]["rules"] == []


def test_rules_report_is_plain_data(service: TaskService) -> None:
    rules, issues = parse_rules([{"id": "a", "scope": "day", "metric": "sessions", "threshold": 1, "action": "warn"}])
    report = rules_report(rules, issues, service.ledger.list_events(), datetime.now(UTC))
    assert report["rules"][0]["state"] == "sous le seuil" and report["issues"] == []
