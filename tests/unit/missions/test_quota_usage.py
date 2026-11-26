"""La consommation des sessions est écrite au ledger (``quota.consumed``) et le plafond du jour devient utilisable.

Avant : aucun code n'émettait ``quota.consumed`` ; sous un plafond fini la consommation était « inconnue » et
l'admission refusait tout en autonome.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.missions.admission import QUOTA_CONSUMED_EVENT, Admit, Defer, admit, derive_view, load_capacity
from grimoire.missions.quota_usage import SESSION_EVENTS, record_consumption
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

ACC = "la consommation est comptée"
STANDARD = Path("_grimoire/standard")
SESSION_A = "5a1c0001-0000-0000-0000-000000000001"
SESSION_B = "5a1c0002-0000-0000-0000-000000000002"
LATER = timedelta(hours=1)  # au-delà du délai de grâce d'une session toute neuve


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    from grimoire.missions import usage_fold
    from grimoire.tools import workspace_work as ww

    ww._TRANSCRIPT_CACHE.clear()
    usage_fold._missing_cache.clear()


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / STANDARD).mkdir(parents=True)
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


def _task(service: TaskService, titre: str = "t") -> str:
    mission = next(iter(service.ledger.list_missions()), None)
    mission_id = mission.id if mission else service.ledger.create_mission(title="Quota", origin="test").id
    task = service.ledger.create_task(mission_id, titre, acceptance=(ACC,), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _call(n: int, model: str = "claude-sonnet-5-5") -> str:
    usage = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    message = {"id": f"msg-{n}", "model": model, "content": [{"type": "text"}], "usage": usage}
    return json.dumps({"type": "assistant", "timestamp": datetime.now(UTC).isoformat(), "message": message})


def _journal(tmp_path: Path, folder: str, name: str, calls: int, model: str = "claude-sonnet-5-5") -> Path:
    path = tmp_path / "claude" / "projects" / folder / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_call(i, model) for i in range(calls)) + "\n", encoding="utf-8")
    return path


def _launch(service: TaskService, tid: str, session: str, **extra: object) -> None:
    service.ledger.append_event(
        "task.session_launched", tid, "task", "worker", {"session": "abcd1234", "session_id": session, "model": "sonnet", **extra}
    )


def _consumed(service: TaskService) -> list[dict[str, object]]:
    return [dict(e.payload) for e in service.ledger.list_events() if e.event_type == QUOTA_CONSUMED_EVENT]


def _now() -> datetime:
    return datetime.now(UTC) + LATER


def test_session_events_match_the_modules_that_write_them() -> None:
    from grimoire.missions.arbiter import ARBITRATION_LAUNCHED_EVENT
    from grimoire.missions.launcher import LAUNCHED_EVENT
    from grimoire.missions.orchestrator_session import ORCH_LAUNCHED_EVENT
    from grimoire.missions.reviewer import REVIEW_LAUNCHED_EVENT

    assert set(SESSION_EVENTS) == {LAUNCHED_EVENT, REVIEW_LAUNCHED_EVENT, ARBITRATION_LAUNCHED_EVENT, ORCH_LAUNCHED_EVENT}


def test_a_session_is_counted_once_and_a_replayed_tick_writes_nothing(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    _launch(service, tid, SESSION_A)
    _journal(tmp_path, "x", SESSION_A, 3)
    first = record_consumption(service, now=_now())
    assert [o.status for o in first] == ["recorded"]
    (event,) = _consumed(service)
    assert event["tokens"] == 4500 and event["session_total_tokens"] == 4500
    assert event["status"] == "lower_bound" and event["role"] == "worker" and event["task_id"] == tid
    assert event["session"] == SESSION_A and event["model"] == "claude-sonnet-5-5"
    assert isinstance(event["cost_usd"], float) and event["cost_status"] == "exact"
    again = record_consumption(service, now=_now())
    assert [o.status for o in again] == ["unchanged"] and len(_consumed(service)) == 1


def test_a_session_that_grows_adds_only_the_difference(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    _launch(service, tid, SESSION_A)
    _journal(tmp_path, "x", SESSION_A, 2)
    record_consumption(service, now=_now())
    _journal(tmp_path, "x", SESSION_A, 5)
    record_consumption(service, now=_now())
    assert [e["tokens"] for e in _consumed(service)] == [3000, 4500]
    capacity = load_capacity(service.home_root)
    assert derive_view(service.ledger, capacity, now=datetime.now(UTC)).tokens.tokens == 7500


def test_an_unreadable_session_says_unknown_with_its_reason_once_then_heals(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    _launch(service, tid, SESSION_A, provider="ailleurs")
    record_consumption(service, now=_now())
    record_consumption(service, now=_now())
    (event,) = _consumed(service)
    assert event["status"] == "unknown" and event["tokens"] is None and "introuvable" in str(event["reason"])
    assert "ailleurs" in str(event["reason"])
    capacity = load_capacity(service.home_root)
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    assert view.tokens.status == "unknown" and view.tokens.unknown_sessions == 1
    _journal(tmp_path, "x", SESSION_A, 1)
    record_consumption(service, now=_now())
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    assert view.tokens.tokens == 1500 and view.tokens.status == "lower_bound"


def test_a_brand_new_session_is_not_declared_unknown(service: TaskService) -> None:
    _launch(service, _task(service), SESSION_A)
    (outcome,) = record_consumption(service)
    assert outcome.status == "unchanged" and not _consumed(service)


def test_an_unknown_model_cost_is_never_counted_as_zero(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    _launch(service, tid, SESSION_A)
    _journal(tmp_path, "x", SESSION_A, 2, model="modele-inconnu-du-kit")
    record_consumption(service, now=_now())
    (event,) = _consumed(service)
    assert event["tokens"] == 3000 and event["cost_usd"] is None and event["cost_status"] == "unknown"


def test_a_role_that_keeps_only_a_digest_is_found_by_its_working_directory(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    cwd = tmp_path / "wt" / "relecteur"
    cwd.mkdir(parents=True)
    digest = hashlib.sha256(SESSION_B.encode()).hexdigest()
    service.ledger.append_event(
        "task.review_launched", tid, "task", "reviewer", {"session": "zz", "session_sha256": digest, "model": "opus", "cwd": str(cwd)}
    )
    _journal(tmp_path, re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve())), SESSION_B, 2)
    record_consumption(service, now=_now())
    (event,) = _consumed(service)
    assert event["role"] == "reviewer" and event["session"] == digest and event["tokens"] == 3000


def test_the_step_is_bounded(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    for n, session in enumerate((SESSION_A, SESSION_B)):
        _launch(service, tid, session)
        _journal(tmp_path, "x", session, n + 1)
    outcomes = record_consumption(service, now=_now(), max_sessions=1)
    assert sorted(o.status for o in outcomes) == ["recorded", "skipped"]
    record_consumption(service, now=_now(), max_sessions=1)
    events = _consumed(service)
    assert sorted(str(e["status"]) for e in events) == ["lower_bound", "lower_bound", "unknown"]  # l'attente, puis les deux


def test_an_oversized_journal_is_unknown_not_guessed(tmp_path: Path, service: TaskService) -> None:
    _launch(service, _task(service), SESSION_A)
    _journal(tmp_path, "x", SESSION_A, 3)
    record_consumption(service, now=_now(), max_bytes=10)
    (event,) = _consumed(service)
    assert event["status"] == "unknown" and "plafond" in str(event["reason"])


def test_a_known_session_that_is_old_and_unchanged_is_no_longer_examined(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    _launch(service, tid, SESSION_A)
    _journal(tmp_path, "x", SESSION_A, 1)
    record_consumption(service, now=_now())
    assert record_consumption(service, now=datetime.now(UTC) + timedelta(days=40)) == []


# ── L'admission lit enfin une consommation ────────────────────────────────────


def _admission(service: TaskService, tid: str, text: str, *, autonomous: bool = True) -> Admit | Defer:
    (service.home_root / STANDARD / "capacity.yaml").write_text(f"version: 1\nquota:\n{text}", encoding="utf-8")
    capacity = load_capacity(service.home_root)
    view = derive_view(service.ledger, capacity, now=datetime.now(UTC))
    return admit(service.require(tid), None, capacity, view, autonomous=autonomous)


def test_a_finite_quota_is_now_usable_below_and_above_the_ceiling(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    other = _task(service, "autre")
    _launch(service, other, SESSION_A)
    _journal(tmp_path, "x", SESSION_A, 3)  # 4500 tokens
    record_consumption(service, now=_now())
    assert isinstance(_admission(service, tid, "  tokens_per_day: 10000\n"), Admit)
    over = _admission(service, tid, "  tokens_per_day: 4000\n")
    assert isinstance(over, Defer) and over.reason == "quota"


def test_partly_unknown_consumption_refuses_by_default_and_count_known_relaxes_it(tmp_path: Path, service: TaskService) -> None:
    tid = _task(service)
    other = _task(service, "autre")
    _launch(service, other, SESSION_A)
    _launch(service, other, SESSION_B)
    _journal(tmp_path, "x", SESSION_A, 1)  # SESSION_B n'a pas de journal
    record_consumption(service, now=_now())
    refused = _admission(service, tid, "  tokens_per_day: 10000\n")
    assert isinstance(refused, Defer) and refused.reason == "unknown" and "partiellement" in refused.detail
    assert isinstance(_admission(service, tid, "  tokens_per_day: 10000\n", autonomous=False), Admit)
    assert isinstance(_admission(service, tid, "  tokens_per_day: 10000\n  on_unknown: count_known\n"), Admit)
    reached = _admission(service, tid, "  tokens_per_day: 1000\n  on_unknown: refuse\n")
    assert isinstance(reached, Defer) and reached.reason == "quota"  # atteint sur le connu seul


def test_on_unknown_is_closed_by_default_and_refuses_a_bad_word(service: TaskService) -> None:
    (service.home_root / STANDARD / "capacity.yaml").write_text(
        "version: 1\nquota:\n  tokens_per_day: 5\n  on_unknown: ignorer\n", encoding="utf-8"
    )
    assert "quota" in load_capacity(service.home_root).errors
    (service.home_root / STANDARD / "capacity.yaml").write_text("version: 1\nquota:\n  tokens_per_day: 5\n", encoding="utf-8")
    assert load_capacity(service.home_root).on_unknown == "refuse"


def test_the_tick_step_records_and_reports_unknown_sessions(tmp_path: Path, service: TaskService) -> None:
    from grimoire.missions.orchestrate import _consumption_stage

    tid = _task(service)
    _launch(service, tid, SESSION_A)
    _launch(service, tid, SESSION_B)  # sans journal, et assez vieille pour être déclarée inconnue
    _journal(tmp_path, "x", SESSION_A, 1)
    attention: list[str] = []
    _consumption_stage(service, "plan", attention)
    assert _consumed(service) == []  # en plan, rien ne s'écrit
    _consumption_stage(service, "apply", attention)
    assert [e["tokens"] for e in _consumed(service)] == [1500]
    assert attention == []  # la session B est neuve : pas encore inconnue
    import grimoire.missions.quota_usage as quota

    original = quota.GRACE
    quota.GRACE = timedelta(0)
    try:
        _consumption_stage(service, "apply", attention)
    finally:
        quota.GRACE = original
    assert any("inconnue" in line for line in attention)
