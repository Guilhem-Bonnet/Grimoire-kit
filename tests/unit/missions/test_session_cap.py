"""Un plafond de sessions par jour pour le relecteur et le merger, journalisé et visible quand il refuse.

Le worker, l'arbitre et l'orchestrateur avaient le leur. Sans réglage (défaut), rien ne change : le plafond est
``None``, jamais une valeur posée à la place du projet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, make_repo
from test_merger import DELEGATED, REPO, grant, isolate_home, launch_and_work, merges, ready_judged
from test_merger import make_projet as merger_projet
from test_merger import write_policy as merger_policy
from test_reviewer import launched_and_in_review

from grimoire.missions.merger import MERGE_BOUNCED_EVENT
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.reviewer import REVIEW_LAUNCH_FAILED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.session_cap import SESSION_CAP_EVENT, cap_attention, cap_refusal, launched_today

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return isolate_home(tmp_path, monkeypatch)


@pytest.fixture
def host() -> FakeHost:
    fake = FakeHost()
    fake.repo = REPO
    return fake


def _policy(root: Path, reviewer: str = "", merger: str = "") -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      model: sonnet\n"
        "      permission_mode: acceptEdits\n    reviewer:\n      enabled: true\n      wip: 3\n"
        f"      permission_mode: acceptEdits\n{reviewer}    merger:\n      enabled: true\n{merger}"
        f"escalation:\n  merge: orchestrator\n  accept: orchestrator\nci:\n  repository: {REPO}\n",
        encoding="utf-8",
    )


# ── La politique ───────────────────────────────────────────────────────────────


def test_no_cap_by_default_so_existing_projects_do_not_change(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    _policy(root)
    policy = load_orchestrator_policy(root).policy
    assert policy is not None
    assert policy.reviewer.sessions_per_day is None and policy.merger.sessions_per_day is None
    assert "sessions_per_day" not in policy.reviewer.to_dict() and "sessions_per_day" not in policy.merger.to_dict()


def test_the_cap_is_read_for_both_roles_and_an_invalid_one_makes_the_policy_unreadable(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    _policy(root, "      sessions_per_day: 4\n", "      sessions_per_day: 7\n")
    policy = load_orchestrator_policy(root).policy
    assert policy is not None and (policy.reviewer.sessions_per_day, policy.merger.sessions_per_day) == (4, 7)
    assert policy.reviewer.to_dict()["sessions_per_day"] == 4 and policy.merger.to_dict()["sessions_per_day"] == 7
    for bad in ("0", "-1", "true", "'beaucoup'"):
        _policy(root, f"      sessions_per_day: {bad}\n")
        read = load_orchestrator_policy(root)
        assert read.policy is None and read.error


# ── La garde commune ───────────────────────────────────────────────────────────


def test_the_guard_counts_today_refuses_at_the_limit_and_journals_once(tmp_path: Path) -> None:
    service = TaskService(make_repo(tmp_path))
    ledger = service.ledger
    now = datetime.now(UTC)
    assert cap_refusal(service, "reviewer", None, ("x.event",), entity_id="t", actor="a") == ""
    ledger.append_event("x.event", "t", "task", "a", {})
    assert launched_today(service, ("x.event",), now) == 1
    assert cap_refusal(service, "reviewer", 2, ("x.event",), entity_id="t", actor="a") == ""
    ledger.append_event("x.event", "t", "task", "a", {})
    why = cap_refusal(service, "reviewer", 2, ("x.event",), entity_id="t", actor="a")
    assert "2/2" in why and "minuit UTC" in why
    cap_refusal(service, "reviewer", 2, ("x.event",), entity_id="t", actor="a")
    assert [e.payload for e in ledger.list_events() if e.event_type == SESSION_CAP_EVENT] == [
        {"role": "reviewer", "limit": 2, "count": 2, "day": now.date().isoformat()}
    ]
    ledger.append_event("y.event", "t", "task", "a", {"escalated": True})
    assert launched_today(service, ("y.event",), now, not_launch=lambda e: bool(e.payload.get("escalated"))) == 0
    assert len(cap_attention(service)) == 1 and "reviewer" in cap_attention(service)[0]
    assert cap_attention(service, now + timedelta(days=2)) == ()  # le lendemain, plus rien à dire


# ── Dans le tick ───────────────────────────────────────────────────────────────


def test_a_capped_reviewer_launches_nothing_and_says_so(tmp_path: Path, host: FakeHost) -> None:
    root = make_repo(tmp_path)
    _policy(root, "      sessions_per_day: 1\n")
    tid, _launch = launched_and_in_review(root, host)
    TaskService(root).ledger.append_event(REVIEW_LAUNCH_FAILED_EVENT, tid, "task", "reviewer", {"reason": "x"})  # compte au plafond
    report = run_tick(root, apply=True, exec_=host)
    assert [(o.action, o.status) for o in report.reviews] == [("capped", "skipped")]
    assert host.of("claude", "--bg") and len(host.of("claude", "--bg")) == 1  # le worker seulement
    assert any("plafond de sessions reviewer atteint" in line for line in report.attention)
    run_tick(root, apply=True, exec_=host)
    events = [e for e in TaskService(root).ledger.list_events() if e.event_type == SESSION_CAP_EVENT]
    assert len(events) == 1 and events[0].payload["role"] == "reviewer"


def test_a_capped_merger_does_not_resume_the_worker(tmp_path: Path, host: FakeHost) -> None:
    root = merger_projet(tmp_path)
    merger_policy(root, merger="    merger:\n      enabled: true\n      max_cycles: 5\n      sessions_per_day: 1\n", escalation=DELEGATED)
    grant(root)
    tid, wt = launch_and_work(root, host)
    ready_judged(root, host, tid, wt)
    TaskService(root).ledger.append_event(MERGE_BOUNCED_EVENT, tid, "task", "merger", {"reason": "ci_red", "cycle": 0})
    host.checks[f"task/{tid}"] = [{"name": "tests", "bucket": "fail"}]
    before = TaskService(root).require(tid).status
    report = run_tick(root, apply=True, exec_=host)
    assert merges(report) == [("bounce", "refused")]
    assert host.of("claude", "--bg", "--resume") == [] and TaskService(root).require(tid).status is before
    assert before is not TaskState.RUNNING
    assert any("plafond de sessions merger atteint" in line for line in report.attention)
